# cfr21/report_export.py
# PDF report generation — 21 CFR Part 11 §11.10(b).
#
# Generates two types of PDF reports:
#
#   1. Audit Trail Report
#      - Filter by date range, user, or action type
#      - Shows WHO / WHEN / WHAT / WHY in tabular format
#      - Footer shows generation timestamp and generating user
#
#   2. Batch Record Report
#      - Per-batch summary: product, operator, start/end time, counts
#      - Full scan table from WAL CSV
#      - File integrity verification results
#
# ── Dependency ────────────────────────────────────────────────────────────────
#   Uses reportlab — a pure-Python PDF library.
#   pip install reportlab
#   It is lightweight (~1MB), no external DLLs, works in frozen PyInstaller.
# ─────────────────────────────────────────────────────────────────────────────

import logging
import os
from datetime import datetime, timezone
from typing import Optional
import functools

from cfr21.authorization import AuthorizationError, SessionContext, authorize_session
from cfr21.user_manager import User
from cfr21.regulated_records import RegulatedRecordService
import cfr21.audit_trail as audit
import cfr21.record_integrity as integrity

log = logging.getLogger("pharma.cfr21.report_export")


# ── ReportLab imports (guarded) ───────────────────────────────────────────────

try:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import (
        SimpleDocTemplate, Table, TableStyle, Paragraph,
        Spacer, HRFlowable, PageBreak,
    )
    from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_RIGHT
    REPORTLAB_OK = True
except ImportError:
    REPORTLAB_OK = False
    log.warning(
        "reportlab not installed — PDF export disabled. "
        "Run: pip install reportlab"
    )


# ── Colour palette (matches app theme) ───────────────────────────────────────

if REPORTLAB_OK:
    _BLUE       = colors.HexColor("#0062a3")
    _DARK       = colors.HexColor("#1a1e24")
    _LIGHT_GREY = colors.HexColor("#f4f5f7")
    _MID_GREY   = colors.HexColor("#d0d4da")
    _RED        = colors.HexColor("#c0392b")
    _GREEN      = colors.HexColor("#1a7a3a")
    _AMBER      = colors.HexColor("#e67e22")
    _WHITE      = colors.white
else:
    # Keep module importable when optional PDF support is absent; decorated
    # export functions return a clear dependency error before using these.
    _BLUE = _DARK = _LIGHT_GREY = _MID_GREY = _RED = _GREEN = _AMBER = _WHITE = None


# ── Guard decorator ───────────────────────────────────────────────────────────

def _require_reportlab(fn):
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        if not REPORTLAB_OK:
            return False, "reportlab is not installed. Run: pip install reportlab"
        return fn(*args, **kwargs)
    return wrapper


# ── Shared page template ──────────────────────────────────────────────────────

def _build_header_footer(canvas, doc, title: str, generated_by: str,
                         company_name: str = ""):
    """
    Draws the page header and footer on every page.
    Called by SimpleDocTemplate as an onPage/onLaterPages callback.
    company_name is shown in the header if provided (from CompanyConfig).
    """
    canvas.saveState()
    width, height = A4

    # ── Header bar ────────────────────────────────────────────────────────────
    canvas.setFillColor(_BLUE)
    canvas.rect(0, height - 28*mm, width, 28*mm, fill=1, stroke=0)

    canvas.setFillColor(_WHITE)
    canvas.setFont("Helvetica-Bold", 14)
    app_label = f"{company_name}  —  Pharma Code Datalogger" if company_name \
                else "Pharma Code Datalogger"
    canvas.drawString(15*mm, height - 13*mm, app_label)

    canvas.setFont("Helvetica", 9)
    canvas.drawRightString(width - 15*mm, height - 13*mm, title)

    canvas.setFont("Helvetica", 8)
    canvas.setFillColor(colors.HexColor("#c8ddef"))
    canvas.drawString(15*mm, height - 20*mm,
                      "21 CFR Part 11 Compliant Electronic Record")

    # ── Footer bar ────────────────────────────────────────────────────────────
    canvas.setFillColor(_LIGHT_GREY)
    canvas.rect(0, 0, width, 14*mm, fill=1, stroke=0)

    canvas.setFillColor(_MID_GREY)
    canvas.rect(0, 14*mm, width, 0.3*mm, fill=1, stroke=0)

    canvas.setFillColor(_DARK)
    canvas.setFont("Helvetica", 7.5)
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    canvas.drawString(15*mm, 5*mm,
                      f"Generated: {now_str}  |  By: {generated_by}  |  CONFIDENTIAL")
    canvas.drawRightString(
        width - 15*mm, 5*mm,
        f"Page {doc.page}"
    )

    canvas.restoreState()


def _make_callback(title: str, generated_by: str, company_name: str = ""):
    """Return a closure compatible with SimpleDocTemplate's onPage."""
    def cb(canvas, doc):
        _build_header_footer(canvas, doc, title, generated_by, company_name)
    return cb


# ── Styles ────────────────────────────────────────────────────────────────────

def _get_styles():
    styles = getSampleStyleSheet()

    styles.add(ParagraphStyle(
        "SectionHead",
        parent     = styles["Normal"],
        fontSize   = 10,
        textColor  = _BLUE,
        fontName   = "Helvetica-Bold",
        spaceAfter = 4,
        spaceBefore= 12,
    ))
    styles.add(ParagraphStyle(
        "FieldLabel",
        parent    = styles["Normal"],
        fontSize  = 8,
        textColor = colors.HexColor("#8a93a0"),
        fontName  = "Helvetica",
    ))
    styles.add(ParagraphStyle(
        "FieldValue",
        parent    = styles["Normal"],
        fontSize  = 9,
        textColor = _DARK,
        fontName  = "Helvetica-Bold",
    ))
    styles.add(ParagraphStyle(
        "SmallNote",
        parent    = styles["Normal"],
        fontSize  = 7,
        textColor = colors.HexColor("#8a93a0"),
        fontName  = "Helvetica-Oblique",
    ))
    return styles


# ── 1. Audit Trail Report ─────────────────────────────────────────────────────

@_require_reportlab
def export_audit_trail(output_path: str,
                       generated_by: User,
                       session_id: str = "",
                       username_filter: Optional[str] = None,
                       action_filter:   Optional[str] = None,
                       date_from:       Optional[datetime] = None,
                       date_to:         Optional[datetime] = None,
                       limit: int = 5000,
                       company_name: str = "",
                       company_address: str = "") -> tuple[bool, str]:
    """
    Export the audit trail to a PDF file.

    Parameters
    ----------
    output_path     : Full path including filename, e.g. /logs/audit_trail.pdf
    generated_by    : User requesting the export (logged to audit trail).
    username_filter : Only include records for this user (optional).
    action_filter   : Only include this action code (optional).
    date_from/to    : Date range filter (optional).
    limit           : Maximum records to include (default 5000).

    Returns (True, "") on success, (False, error_message) on failure.
    """
    try:
        try:
            generated_by = authorize_session(
                SessionContext.from_user(generated_by, session_id),
                "export_reports",
                target="audit_trail",
            )
        except AuthorizationError:
            return False, "You are not authorized to export audit records."

        records = audit.get_records(
            limit          = limit,
            username_filter= username_filter,
            action_filter  = action_filter,
            date_from      = date_from,
            date_to        = date_to,
        )

        styles = _get_styles()
        story  = []

        # ── Report title block ────────────────────────────────────────────────
        story.append(Spacer(1, 6*mm))
        story.append(Paragraph("AUDIT TRAIL REPORT", styles["SectionHead"]))
        story.append(HRFlowable(width="100%", thickness=1, color=_MID_GREY))
        story.append(Spacer(1, 3*mm))

        # Metadata grid
        meta_rows = [
            ["Generated By",  generated_by.username,
             "Role",          generated_by.role_display],
            ["Generated At",  datetime.now(timezone.utc).strftime(
                                  "%Y-%m-%d %H:%M:%S UTC"),
             "Record Count",  str(len(records))],
            ["Filter – User", username_filter or "All",
             "Filter – Action", action_filter or "All"],
            ["Date From",     date_from.strftime("%Y-%m-%d") if date_from else "–",
             "Date To",       date_to.strftime("%Y-%m-%d") if date_to else "–"],
        ]

        meta_table = Table(meta_rows, colWidths=[35*mm, 55*mm, 35*mm, 55*mm])
        meta_table.setStyle(TableStyle([
            ("FONTNAME",      (0, 0), (-1, -1), "Helvetica"),
            ("FONTSIZE",      (0, 0), (-1, -1), 8),
            ("FONTNAME",      (0, 0), (0, -1), "Helvetica-Bold"),
            ("FONTNAME",      (2, 0), (2, -1), "Helvetica-Bold"),
            ("TEXTCOLOR",     (0, 0), (0, -1), _BLUE),
            ("TEXTCOLOR",     (2, 0), (2, -1), _BLUE),
            ("ROWBACKGROUNDS",(0, 0), (-1, -1), [_LIGHT_GREY, _WHITE]),
            ("TOPPADDING",    (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ("LEFTPADDING",   (0, 0), (-1, -1), 4),
        ]))
        story.append(meta_table)
        story.append(Spacer(1, 6*mm))

        # ── Records table ─────────────────────────────────────────────────────
        if not records:
            story.append(Paragraph(
                "No audit trail records found for the selected filters.",
                styles["SmallNote"]
            ))
        else:
            story.append(Paragraph("Audit Records", styles["SectionHead"]))
            story.append(Spacer(1, 2*mm))

            # Header row
            headers = ["#", "Timestamp (UTC)", "User", "Role",
                       "Action", "Detail", "Reason"]
            col_w   = [8*mm, 38*mm, 22*mm, 20*mm, 28*mm, 50*mm, 30*mm]

            table_data = [headers]
            for i, rec in enumerate(records, start=1):
                # Truncate long detail strings for readability
                detail = rec.get("detail", "")
                if len(detail) > 120:
                    detail = detail[:117] + "…"
                reason = rec.get("reason") or "–"

                table_data.append([
                    str(i),
                    _fmt_ts(rec.get("timestamp", "")),
                    rec.get("username", ""),
                    rec.get("role", "").title(),
                    rec.get("action", ""),
                    detail,
                    reason,
                ])

            tbl = Table(table_data, colWidths=col_w, repeatRows=1)
            tbl.setStyle(TableStyle([
                # Header
                ("BACKGROUND",    (0, 0), (-1, 0), _BLUE),
                ("TEXTCOLOR",     (0, 0), (-1, 0), _WHITE),
                ("FONTNAME",      (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE",      (0, 0), (-1, 0), 7.5),
                ("ALIGN",         (0, 0), (-1, 0), "CENTER"),
                # Body
                ("FONTNAME",      (0, 1), (-1, -1), "Helvetica"),
                ("FONTSIZE",      (0, 1), (-1, -1), 7),
                ("ROWBACKGROUNDS",(0, 1), (-1, -1), [_WHITE, _LIGHT_GREY]),
                ("VALIGN",        (0, 0), (-1, -1), "TOP"),
                ("TOPPADDING",    (0, 0), (-1, -1), 2),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
                ("LEFTPADDING",   (0, 0), (-1, -1), 3),
                ("GRID",          (0, 0), (-1, -1), 0.3, _MID_GREY),
            ]))
            story.append(tbl)

        # ── Build PDF ─────────────────────────────────────────────────────────
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        cb = _make_callback("Audit Trail Report", generated_by.username,
                            company_name)

        doc = SimpleDocTemplate(
            output_path,
            pagesize    = A4,
            topMargin   = 32*mm,
            bottomMargin= 18*mm,
            leftMargin  = 15*mm,
            rightMargin = 15*mm,
        )
        doc.build(story, onFirstPage=cb, onLaterPages=cb)

        # Write export event to audit trail
        audit.log(
            user   = generated_by,
            action = audit.ACTION_REPORT_EXPORTED,
            detail = f"Audit Trail PDF exported: {os.path.basename(output_path)} "
                     f"({len(records)} records)",
            session_id = session_id,
        )

        log.info("Audit trail PDF exported: %s (%s records)",
                 output_path, len(records))
        return True, ""

    except Exception as e:
        log.error("export_audit_trail() failed: %s", e)
        return False, str(e)


# ── 2. Batch Record Report ────────────────────────────────────────────────────

@_require_reportlab
def export_batch_record(output_path: str,
                        generated_by: User,
                        batch_id: str,
                        device_id: int,
                        session_id: str = "",
                        wal_path: str = "",
                        product_name: str = "",
                        operator_id: str  = "",
                        started_at: Optional[datetime] = None,
                        stopped_at: Optional[datetime] = None,
                        company_name: str = "",
                        company_address: str = "") -> tuple[bool, str]:
    """
    Export a complete batch record to PDF.

    Includes:
      - Batch metadata (ID, product, operator, start/stop times)
      - Full scan table read from the WAL CSV
      - Pass/Fail summary counts
      - File integrity verification results

    Returns (True, "") on success, (False, error_message) on failure.
    """
    try:
        try:
            generated_by = authorize_session(
                SessionContext.from_user(generated_by, session_id),
                "export_reports",
                target=f"batch:{batch_id}:device:{device_id}",
            )
        except AuthorizationError:
            return False, "You are not authorized to export batch records."

        authoritative_batch, scan_rows = RegulatedRecordService().get_batch_record(
            batch_id, device_id)
        product_name = authoritative_batch["product_name"]
        operator_id = authoritative_batch["operator_id"]
        started_at = datetime.fromisoformat(authoritative_batch["started_at"])
        stopped_at = (datetime.fromisoformat(authoritative_batch["stopped_at"])
                      if authoritative_batch.get("stopped_at") else None)
        styles = _get_styles()
        story  = []

        story.append(Spacer(1, 6*mm))
        story.append(Paragraph("BATCH PRODUCTION RECORD", styles["SectionHead"]))
        story.append(HRFlowable(width="100%", thickness=1, color=_MID_GREY))
        story.append(Spacer(1, 3*mm))

        # ── Batch metadata ────────────────────────────────────────────────────
        meta = [
            ["Batch ID",       batch_id,
             "Device",         f"Device {device_id}"],
            ["Product Name",   product_name or "–",
             "Operator ID",    operator_id or "–"],
            ["Started At",     started_at.strftime("%Y-%m-%d %H:%M:%S") if started_at else "–",
             "Stopped At",     stopped_at.strftime("%Y-%m-%d %H:%M:%S") if stopped_at else "–"],
        ]
        if company_name:
            meta.append(["Company", company_name,
                         "Address", company_address or "–"])
        meta_tbl = Table(meta, colWidths=[30*mm, 60*mm, 30*mm, 60*mm])
        meta_tbl.setStyle(TableStyle([
            ("FONTNAME",      (0, 0), (-1, -1), "Helvetica"),
            ("FONTSIZE",      (0, 0), (-1, -1), 8),
            ("FONTNAME",      (0, 0), (0, -1), "Helvetica-Bold"),
            ("FONTNAME",      (2, 0), (2, -1), "Helvetica-Bold"),
            ("TEXTCOLOR",     (0, 0), (0, -1), _BLUE),
            ("TEXTCOLOR",     (2, 0), (2, -1), _BLUE),
            ("ROWBACKGROUNDS",(0, 0), (-1, -1), [_LIGHT_GREY, _WHITE]),
            ("TOPPADDING",    (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ("LEFTPADDING",   (0, 0), (-1, -1), 4),
        ]))
        story.append(meta_tbl)
        story.append(Spacer(1, 5*mm))

        # ── Read WAL CSV ──────────────────────────────────────────────────────
        # Official report data is the authoritative database record.  The
        # legacy WAL argument is retained only for caller compatibility.
        pass_count = sum(1 for r in scan_rows if r.get("status") == "PASS")
        fail_count = sum(1 for r in scan_rows if r.get("status") == "FAIL")
        total      = len(scan_rows)
        pass_rate  = f"{pass_count/total*100:.1f}%" if total else "–"

        # ── Summary KPI row ───────────────────────────────────────────────────
        story.append(Paragraph("Summary", styles["SectionHead"]))
        kpi_data = [
            ["Total Scans", "PASS", "FAIL", "Pass Rate"],
            [str(total), str(pass_count), str(fail_count), pass_rate],
        ]
        kpi_tbl = Table(kpi_data, colWidths=[45*mm, 45*mm, 45*mm, 45*mm])
        kpi_tbl.setStyle(TableStyle([
            ("BACKGROUND",  (0, 0), (-1, 0), _DARK),
            ("TEXTCOLOR",   (0, 0), (-1, 0), _WHITE),
            ("FONTNAME",    (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE",    (0, 0), (-1, -1), 10),
            ("ALIGN",       (0, 0), (-1, -1), "CENTER"),
            ("FONTNAME",    (0, 1), (-1, 1), "Helvetica-Bold"),
            ("FONTSIZE",    (0, 1), (-1, 1), 16),
            ("TEXTCOLOR",   (1, 1), (1, 1), _GREEN),
            ("TEXTCOLOR",   (2, 1), (2, 1), _RED),
            ("TOPPADDING",  (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING",(0, 0), (-1, -1), 5),
            ("GRID",        (0, 0), (-1, -1), 0.5, _MID_GREY),
        ]))
        story.append(kpi_tbl)
        story.append(Spacer(1, 5*mm))

        # ── Scan table ────────────────────────────────────────────────────────
        if scan_rows:
            story.append(Paragraph(
                f"Scan Records  ({total} total)", styles["SectionHead"]))
            story.append(Spacer(1, 2*mm))

            headers   = ["#", "Time", "Pharma Code", "Master Code", "Status"]
            col_widths = [10*mm, 35*mm, 50*mm, 50*mm, 18*mm]
            tbl_data  = [headers]

            for r in scan_rows:
                status = r.get("status", "")
                tbl_data.append([
                    r.get("sequence_no", ""),
                    r.get("recorded_at", ""),
                    r.get("raw_data",  ""),
                    r.get("master_data", ""),
                    status,
                ])

            scan_tbl = Table(tbl_data, colWidths=col_widths, repeatRows=1)

            # Build per-row status colours
            style_cmds = [
                ("BACKGROUND",    (0, 0), (-1, 0), _BLUE),
                ("TEXTCOLOR",     (0, 0), (-1, 0), _WHITE),
                ("FONTNAME",      (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE",      (0, 0), (-1, 0), 7.5),
                ("FONTNAME",      (0, 1), (-1, -1), "Helvetica"),
                ("FONTSIZE",      (0, 1), (-1, -1), 7),
                ("ROWBACKGROUNDS",(0, 1), (-1, -1), [_WHITE, _LIGHT_GREY]),
                ("TOPPADDING",    (0, 0), (-1, -1), 2),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
                ("LEFTPADDING",   (0, 0), (-1, -1), 3),
                ("GRID",          (0, 0), (-1, -1), 0.3, _MID_GREY),
                ("ALIGN",         (4, 0), (4, -1), "CENTER"),
            ]

            for row_idx, r in enumerate(scan_rows, start=1):
                color = _GREEN if r.get("status") == "PASS" else _RED
                style_cmds.append(
                    ("TEXTCOLOR", (4, row_idx), (4, row_idx), color)
                )
                style_cmds.append(
                    ("FONTNAME",  (4, row_idx), (4, row_idx), "Helvetica-Bold")
                )

            scan_tbl.setStyle(TableStyle(style_cmds))
            story.append(scan_tbl)
        else:
            story.append(Paragraph(
                "No scan records found (WAL file missing or empty).",
                styles["SmallNote"]
            ))

        # ── File integrity section ────────────────────────────────────────────
        story.append(PageBreak())
        story.append(Spacer(1, 6*mm))
        story.append(Paragraph("File Integrity Verification", styles["SectionHead"]))
        story.append(HRFlowable(width="100%", thickness=1, color=_MID_GREY))
        story.append(Spacer(1, 3*mm))

        integrity_results = integrity.verify_batch_files(batch_id, device_id)

        if not integrity_results:
            story.append(Paragraph(
                "No integrity records found for this batch. "
                "Checksums are recorded when a batch is stopped.",
                styles["SmallNote"]
            ))
        else:
            int_headers = ["File Type", "File Name", "Sealed By",
                           "Sealed At", "Status"]
            int_widths  = [20*mm, 65*mm, 28*mm, 38*mm, 22*mm]
            int_data    = [int_headers]

            for res in integrity_results:
                if res.get("error"):
                    status_str = f"ERROR: {res['error']}"
                    ok = False
                elif res.get("match"):
                    status_str = "✓ INTACT"
                    ok = True
                else:
                    status_str = "✗ MODIFIED"
                    ok = False

                int_data.append([
                    res.get("file_type", "").upper(),
                    os.path.basename(res.get("file_path", "")),
                    res.get("sealed_by", ""),
                    _fmt_ts(res.get("sealed_at", "")),
                    status_str,
                ])

            int_tbl = Table(int_data, colWidths=int_widths, repeatRows=1)
            int_style = [
                ("BACKGROUND",   (0, 0), (-1, 0), _DARK),
                ("TEXTCOLOR",    (0, 0), (-1, 0), _WHITE),
                ("FONTNAME",     (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE",     (0, 0), (-1, 0), 7.5),
                ("FONTNAME",     (0, 1), (-1, -1), "Helvetica"),
                ("FONTSIZE",     (0, 1), (-1, -1), 7),
                ("ROWBACKGROUNDS",(0, 1), (-1, -1), [_WHITE, _LIGHT_GREY]),
                ("TOPPADDING",   (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING",(0, 0), (-1, -1), 3),
                ("LEFTPADDING",  (0, 0), (-1, -1), 3),
                ("GRID",         (0, 0), (-1, -1), 0.3, _MID_GREY),
            ]
            for row_idx, res in enumerate(integrity_results, start=1):
                ok = res.get("match", False) and not res.get("error")
                color = _GREEN if ok else _RED
                int_style.append(
                    ("TEXTCOLOR", (4, row_idx), (4, row_idx), color)
                )
                int_style.append(
                    ("FONTNAME",  (4, row_idx), (4, row_idx), "Helvetica-Bold")
                )
            int_tbl.setStyle(TableStyle(int_style))
            story.append(int_tbl)

            # Show full hashes for each file
            story.append(Spacer(1, 4*mm))
            story.append(Paragraph(
                "SHA-256 Checksums", styles["SectionHead"]))
            for res in integrity_results:
                ftype = res.get("file_type", "").upper()
                stored = res.get("stored_hash", "–")
                actual = res.get("actual_hash", "–")
                match  = res.get("match", False)
                story.append(Paragraph(
                    f"<b>{ftype}</b>  "
                    f"{os.path.basename(res.get('file_path', ''))}",
                    styles["FieldLabel"]
                ))
                story.append(Paragraph(f"Stored : {stored}", styles["SmallNote"]))
                story.append(Paragraph(f"Actual : {actual}", styles["SmallNote"]))
                story.append(Paragraph(
                    "Match: YES ✓" if match else "Match: NO  ✗ — FILE MAY HAVE BEEN MODIFIED",
                    ParagraphStyle(
                        "MatchStatus",
                        parent    = styles["SmallNote"],
                        textColor = _GREEN if match else _RED,
                        fontName  = "Helvetica-Bold",
                    )
                ))
                story.append(Spacer(1, 3*mm))

        # ── Build PDF ─────────────────────────────────────────────────────────
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        cb = _make_callback(f"Batch Record — {batch_id}", generated_by.username,
                            company_name)

        doc = SimpleDocTemplate(
            output_path,
            pagesize    = A4,
            topMargin   = 32*mm,
            bottomMargin= 18*mm,
            leftMargin  = 15*mm,
            rightMargin = 15*mm,
        )
        doc.build(story, onFirstPage=cb, onLaterPages=cb)

        audit.log(
            user   = generated_by,
            action = audit.ACTION_REPORT_EXPORTED,
            detail = (f"Batch Record PDF exported for batch '{batch_id}' "
                      f"Device {device_id}: {os.path.basename(output_path)}"),
            session_id = session_id,
        )

        log.info("Batch record PDF exported: %s", output_path)
        return True, ""

    except Exception as e:
        log.error("export_batch_record() failed: %s", e)
        return False, str(e)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _fmt_ts(iso_str: str) -> str:
    """Format an ISO timestamp string for display in PDF tables."""
    if not iso_str:
        return "–"
    try:
        dt = datetime.fromisoformat(iso_str)
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return iso_str[:19]  # best-effort truncation
