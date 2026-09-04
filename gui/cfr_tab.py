# gui/cfr_tab.py
# CFR21 Compliance Tab — embedded in MainWindow's QTabWidget.
#
# Three sections accessible via a sidebar:
#   1. Audit Trail Viewer  — filter, view, export all audit records
#   2. User Management     — create/deactivate/reset users (admin only)
#   3. File Integrity      — verify SHA-256 checksums on batch files
#
# The tab is always visible but section content is gated by role:
#   - Audit Viewer: supervisor, administrator, qa
#   - User Management: administrator only
#   - File Integrity: supervisor, administrator, qa

import os
from datetime import datetime, timezone, timedelta
from typing import Optional

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QFrame, QTableWidget, QTableWidgetItem, QHeaderView,
    QAbstractItemView, QComboBox, QLineEdit, QDateEdit,
    QMessageBox, QFileDialog, QStackedWidget, QSizePolicy,
    QScrollArea, QCheckBox, QSpinBox, QInputDialog,
)
from PyQt6.QtGui import QColor, QFont
from PyQt6.QtCore import Qt, QDate, QTimer

import cfr21.audit_trail as audit
from cfr21.session_manager import SessionManager
from cfr21.user_manager import (
    ROLE_ADMINISTRATOR, ROLE_QA, ROLE_SUPERVISOR,
    get_all_users,
    ALL_ROLES, ROLE_DISPLAY,
)
from cfr21.user_admin_service import (
    create_account,
    deactivate_account,
    reactivate_account,
    reset_password,
)
from cfr21.record_integrity import get_integrity_records, verify_batch_files
from cfr21.device_registry_service import DeviceRegistryService, DeviceRegistryError
from cfr21.reauthentication_service import issue_grant, ReauthenticationError
import cfr21.report_export as report_export

# ── Style constants ───────────────────────────────────────────────────────────

_BLUE   = "#0062a3"
_DARK   = "#1a1e24"
_GREEN  = "#1a7a3a"
_RED    = "#c0392b"
_GREY   = "#f4f5f7"
_BORDER = "#d0d4da"
_TEXT2  = "#8a93a0"

_S_SECTION_TITLE = (
    "font-size: 13px; font-weight: 700; color: #1a1e24; padding-bottom: 2px;"
)
_S_FIELD_LABEL = (
    "font-size: 10px; font-weight: 600; color: #8a93a0; "
    "letter-spacing: 0.8px;"
)
_S_NAV_BTN = (
    "QPushButton { background: transparent; color: #4a5260; "
    "border: none; border-radius: 4px; text-align: left; "
    "padding: 10px 14px; font-size: 12px; font-weight: 500; } "
    "QPushButton:hover { background: #e8edf3; color: #0062a3; } "
    "QPushButton:checked { background: #dbeafe; color: #0062a3; "
    "font-weight: 700; border-left: 3px solid #0062a3; }"
)
_S_ACTION_BTN = (
    "QPushButton { background: #0062a3; color: white; border: none; "
    "border-radius: 4px; padding: 7px 16px; font-size: 12px; "
    "font-weight: 600; } "
    "QPushButton:hover { background: #004f87; } "
    "QPushButton:disabled { background: #b0c4d8; }"
)
_S_SM_BTN = (
    "QPushButton { background: #f4f5f7; color: #4a5260; "
    "border: 1px solid #d0d4da; border-radius: 4px; "
    "padding: 5px 12px; font-size: 11px; } "
    "QPushButton:hover { background: #e8eaed; }"
)
_S_DANGER_BTN = (
    "QPushButton { background: #fef2f2; color: #c0392b; "
    "border: 1px solid #fca5a5; border-radius: 4px; "
    "padding: 5px 12px; font-size: 11px; font-weight: 600; } "
    "QPushButton:hover { background: #fee2e2; }"
)
_S_SUCCESS_BTN = (
    "QPushButton { background: #f0fdf4; color: #1a7a3a; "
    "border: 1px solid #86efac; border-radius: 4px; "
    "padding: 5px 12px; font-size: 11px; font-weight: 600; } "
    "QPushButton:hover { background: #dcfce7; }"
)
_S_INPUT = (
    "QLineEdit, QComboBox, QDateEdit { "
    "border: 1px solid #d0d4da; border-radius: 4px; "
    "padding: 5px 8px; font-size: 11px; background: #fff; } "
    "QLineEdit:focus, QComboBox:focus { border-color: #0062a3; }"
)
_S_TABLE = (
    "QTableWidget { font-size: 11px; gridline-color: #e0e4ea; "
    "border: 1px solid #d0d4da; } "
    "QHeaderView::section { background: #1a1e24; color: white; "
    "font-weight: 600; font-size: 10px; padding: 6px 4px; "
    "border: none; letter-spacing: 0.5px; } "
    "QTableWidget::item { padding: 4px; } "
    "QTableWidget::item:selected { background: #dbeafe; color: #1a1e24; }"
)


def _divider(vertical=False):
    f = QFrame()
    f.setFrameShape(
        QFrame.Shape.VLine if vertical else QFrame.Shape.HLine
    )
    f.setStyleSheet(
        "background: #e0e4ea; "
        + ("max-width: 1px;" if vertical else "max-height: 1px;")
    )
    return f


def _lbl(text, style="font-size: 11px; color: #4a5260;"):
    w = QLabel(text)
    w.setStyleSheet(style)
    w.setWordWrap(True)
    return w


def _inp(placeholder="", width=None):
    w = QLineEdit()
    w.setPlaceholderText(placeholder)
    w.setStyleSheet(_S_INPUT)
    w.setFixedHeight(32)
    if width:
        w.setFixedWidth(width)
    return w


# ══════════════════════════════════════════════════════════════════════════════
#  MAIN CFR21 TAB WIDGET
# ══════════════════════════════════════════════════════════════════════════════

class CFR21Tab(QWidget):
    """
    Top-level widget for the CFR21 Compliance tab.
    Left sidebar with nav buttons, right stacked content area.
    """

    def __init__(self, session_mgr: SessionManager, parent=None,
                 config=None):
        super().__init__(parent)
        self._sm     = session_mgr
        self._config = config   # AppConfig — used for company info in PDF exports
        self._build_ui()

    def _build_ui(self):
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── Left sidebar ──────────────────────────────────────────────────────
        sidebar = QWidget()
        sidebar.setFixedWidth(180)
        sidebar.setStyleSheet(
            "background: #f8f9fb; border-right: 1px solid #e0e4ea;"
        )
        sv = QVBoxLayout(sidebar)
        sv.setContentsMargins(8, 16, 8, 16)
        sv.setSpacing(2)

        # Sidebar header
        hdr = QLabel("CFR21\nCompliance")
        hdr.setStyleSheet(
            "font-size: 11px; font-weight: 700; color: #0062a3; "
            "letter-spacing: 1px; padding: 0 6px 12px 6px;"
        )
        sv.addWidget(hdr)
        sv.addWidget(_divider())
        sv.addSpacing(8)

        # Nav buttons
        self._nav_audit   = self._nav_btn("📋  Audit Trail")
        self._nav_users   = self._nav_btn("👥  User Management")
        self._nav_devices = self._nav_btn("Device Management")
        self._nav_integrity = self._nav_btn("🔒  File Integrity")

        for btn in [self._nav_audit, self._nav_users, self._nav_devices, self._nav_integrity]:
            btn.setCheckable(True)
            sv.addWidget(btn)

        sv.addStretch()

        # Sidebar footer — compliance badge
        badge = QLabel("21 CFR Part 11\nCompliant")
        badge.setStyleSheet(
            "font-size: 9px; color: #8a93a0; text-align: center; "
            "padding: 8px 6px; border-top: 1px solid #e0e4ea;"
        )
        badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        sv.addWidget(badge)

        root.addWidget(sidebar)

        # ── Right content stack ───────────────────────────────────────────────
        self._stack = QStackedWidget()
        self._stack.setStyleSheet("background: #ffffff;")

        self._page_audit     = AuditTrailPage(self._sm, config=self._config)
        self._page_users     = UserManagementPage(self._sm)
        self._page_devices   = DeviceManagementPage(self._sm)
        self._page_integrity = FileIntegrityPage(self._sm, config=self._config)

        self._stack.addWidget(self._page_audit)      # index 0
        self._stack.addWidget(self._page_users)      # index 1
        self._stack.addWidget(self._page_devices)    # index 2
        self._stack.addWidget(self._page_integrity)  # index 3

        root.addWidget(self._stack, 1)

        # Wire nav buttons
        self._nav_audit.clicked.connect(lambda: self._switch(0))
        self._nav_users.clicked.connect(lambda: self._switch(1))
        self._nav_devices.clicked.connect(lambda: self._switch(2))
        self._nav_integrity.clicked.connect(lambda: self._switch(3))

        # Start on audit trail
        self._switch(0)

    def _nav_btn(self, text: str) -> QPushButton:
        btn = QPushButton(text)
        btn.setStyleSheet(_S_NAV_BTN)
        btn.setFixedHeight(40)
        return btn

    def _switch(self, index: int):
        self._stack.setCurrentIndex(index)
        for i, btn in enumerate([
            self._nav_audit, self._nav_users, self._nav_devices, self._nav_integrity
        ]):
            btn.setChecked(i == index)

        # Refresh content when switching to it
        if index == 0:
            self._page_audit.refresh()
        elif index == 1:
            self._page_users.refresh()
        elif index == 2:
            self._page_devices.refresh()
        elif index == 3:
            self._page_integrity.refresh()

    def refresh(self):
        """Called when the tab becomes visible."""
        self._page_audit.refresh()


# ══════════════════════════════════════════════════════════════════════════════
#  PAGE 1: AUDIT TRAIL VIEWER
# ══════════════════════════════════════════════════════════════════════════════

class AuditTrailPage(QWidget):
    """
    Filterable, exportable audit trail viewer.
    Shows all records from audit_trail table, most recent first.
    """

    def __init__(self, session_mgr: SessionManager, parent=None, config=None):
        super().__init__(parent)
        self._sm = session_mgr
        self._config = config
        self._records: list[dict] = []
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(10)

        # ── Header ────────────────────────────────────────────────────────────
        hdr = QHBoxLayout()
        hdr.addWidget(_lbl("Audit Trail", _S_SECTION_TITLE))
        hdr.addStretch()

        self._lbl_count = _lbl("", "font-size: 11px; color: #8a93a0;")
        hdr.addWidget(self._lbl_count)

        btn_refresh = QPushButton("↻  Refresh")
        btn_refresh.setStyleSheet(_S_SM_BTN)
        btn_refresh.setFixedHeight(30)
        btn_refresh.clicked.connect(self.refresh)
        hdr.addWidget(btn_refresh)

        btn_verify = QPushButton("🛡  Verify Integrity")
        btn_verify.setStyleSheet(_S_SM_BTN)
        btn_verify.setFixedHeight(30)
        btn_verify.setToolTip(
            "Verify the audit trail hash chain — detects any record that "
            "was modified, deleted, or inserted outside the application")
        btn_verify.clicked.connect(self._verify_chain)
        hdr.addWidget(btn_verify)

        btn_export = QPushButton("⬇  Export PDF")
        btn_export.setStyleSheet(_S_ACTION_BTN)
        btn_export.setFixedHeight(30)
        btn_export.clicked.connect(self._export_pdf)
        hdr.addWidget(btn_export)

        layout.addLayout(hdr)
        layout.addWidget(_divider())

        # ── Filter bar ────────────────────────────────────────────────────────
        filter_bar = QHBoxLayout()
        filter_bar.setSpacing(8)

        filter_bar.addWidget(_lbl("USER", _S_FIELD_LABEL))
        self._filter_user = _inp("All users", width=130)
        self._filter_user.returnPressed.connect(self.refresh)
        filter_bar.addWidget(self._filter_user)

        filter_bar.addWidget(_lbl("ACTION", _S_FIELD_LABEL))
        self._filter_action = QComboBox()
        self._filter_action.setStyleSheet(_S_INPUT)
        self._filter_action.setFixedHeight(32)
        self._filter_action.setFixedWidth(160)
        self._filter_action.addItem("All actions", "")
        for code in sorted([
            audit.ACTION_LOGIN, audit.ACTION_LOGOUT,
            audit.ACTION_LOGIN_FAILED, audit.ACTION_ACCOUNT_LOCKED,
            audit.ACTION_PASSWORD_CHANGED, audit.ACTION_PASSWORD_RESET,
            audit.ACTION_SESSION_TIMEOUT,
            audit.ACTION_SCREEN_LOCKED, audit.ACTION_SCREEN_UNLOCKED,
            audit.ACTION_USER_CREATED, audit.ACTION_USER_DEACTIVATED,
            audit.ACTION_USER_REACTIVATED,
            audit.ACTION_BATCH_STARTED, audit.ACTION_BATCH_STOPPED,
            audit.ACTION_MASTER_SET, audit.ACTION_MASTER_CLEARED,
            audit.ACTION_CAMERA_CONNECTED, audit.ACTION_CAMERA_DISCONNECTED,
            audit.ACTION_CAMERA_LOST,
            audit.ACTION_PLC_CONNECTED, audit.ACTION_PLC_DISCONNECTED,
            audit.ACTION_CONSEC_FAIL_ALARM,
            audit.ACTION_SETTINGS_CHANGED, audit.ACTION_SESSION_SETUP,
            audit.ACTION_REPORT_EXPORTED,
            audit.ACTION_APP_STARTED, audit.ACTION_APP_CLOSED,
            audit.ACTION_CRASH_DETECTED,
        ]):
            self._filter_action.addItem(code, code)
        filter_bar.addWidget(self._filter_action)

        filter_bar.addWidget(_lbl("FROM", _S_FIELD_LABEL))
        self._filter_from = QDateEdit()
        self._filter_from.setStyleSheet(_S_INPUT)
        self._filter_from.setFixedHeight(32)
        self._filter_from.setFixedWidth(110)
        self._filter_from.setCalendarPopup(True)
        self._filter_from.setDate(
            QDate.currentDate().addDays(-30)
        )
        filter_bar.addWidget(self._filter_from)

        filter_bar.addWidget(_lbl("TO", _S_FIELD_LABEL))
        self._filter_to = QDateEdit()
        self._filter_to.setStyleSheet(_S_INPUT)
        self._filter_to.setFixedHeight(32)
        self._filter_to.setFixedWidth(110)
        self._filter_to.setCalendarPopup(True)
        self._filter_to.setDate(QDate.currentDate())
        filter_bar.addWidget(self._filter_to)

        self._chk_use_dates = QCheckBox("Date filter")
        self._chk_use_dates.setStyleSheet("font-size: 11px; color: #4a5260;")
        filter_bar.addWidget(self._chk_use_dates)

        btn_apply = QPushButton("Apply")
        btn_apply.setStyleSheet(_S_ACTION_BTN)
        btn_apply.setFixedHeight(32)
        btn_apply.setFixedWidth(70)
        btn_apply.clicked.connect(self.refresh)
        filter_bar.addWidget(btn_apply)

        filter_bar.addStretch()
        layout.addLayout(filter_bar)

        # ── Table ─────────────────────────────────────────────────────────────
        self._table = QTableWidget()
        self._table.setStyleSheet(_S_TABLE)
        self._table.setColumnCount(7)
        self._table.setHorizontalHeaderLabels([
            "#", "Timestamp (UTC)", "User", "Role",
            "Action", "Detail", "Reason",
        ])
        hh = self._table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        hh.setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        hh.setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        hh.setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)
        hh.setSectionResizeMode(4, QHeaderView.ResizeMode.Fixed)
        hh.setSectionResizeMode(5, QHeaderView.ResizeMode.Stretch)
        hh.setSectionResizeMode(6, QHeaderView.ResizeMode.Fixed)
        self._table.setColumnWidth(0, 40)
        self._table.setColumnWidth(1, 155)
        self._table.setColumnWidth(2, 100)
        self._table.setColumnWidth(3, 90)
        self._table.setColumnWidth(4, 145)
        self._table.setColumnWidth(6, 130)
        self._table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setAlternatingRowColors(True)
        self._table.verticalHeader().setDefaultSectionSize(24)
        self._table.verticalHeader().hide()
        layout.addWidget(self._table, 1)

        # ── Footer note ───────────────────────────────────────────────────────
        layout.addWidget(_lbl(
            "Audit trail records are immutable — they cannot be edited or deleted. "
            "Showing most recent 500 records. Use filters to narrow results.",
            "font-size: 10px; color: #b0b8c4;"
        ))

    def refresh(self):
        """Load records from DB applying current filters."""
        if not self._sm or not self._sm.can("view_audit_trail"):
            self._table.setRowCount(0)
            self._lbl_count.setText("⚠ Insufficient permissions")
            return

        username = self._filter_user.text().strip() or None
        action   = self._filter_action.currentData() or None

        date_from = date_to = None
        if self._chk_use_dates.isChecked():
            qdf = self._filter_from.date()
            qdt = self._filter_to.date()
            date_from = datetime(
                qdf.year(), qdf.month(), qdf.day(), tzinfo=timezone.utc
            )
            date_to = datetime(
                qdt.year(), qdt.month(), qdt.day(),
                23, 59, 59, tzinfo=timezone.utc
            )

        self._records = audit.get_records(
            limit          = 500,
            username_filter= username,
            action_filter  = action,
            date_from      = date_from,
            date_to        = date_to,
        )

        self._lbl_count.setText(f"{len(self._records)} records")
        self._populate_table()

        # Log that audit trail was viewed
        audit.log(
            user       = self._sm.current_user,
            action     = audit.ACTION_AUDIT_VIEWED,
            detail     = (
                f"Audit trail viewed. Filters: user={username or 'all'}, "
                f"action={action or 'all'}, "
                f"date_range={self._chk_use_dates.isChecked()}"
            ),
            session_id = self._sm.session_id,
        )

    def _populate_table(self):
        self._table.setRowCount(len(self._records))
        action_colors = {
            "FAIL": _RED, "LOGIN_FAILED": _RED, "ACCOUNT_LOCKED": _RED,
            "CAMERA_LOST": _RED, "CRASH_DETECTED": _RED,
            "LOGIN": _GREEN, "BATCH_STARTED": _GREEN,
            "MASTER_CODE_SET": _GREEN,
        }
        for row, rec in enumerate(self._records):
            action = rec.get("action", "")
            ts_raw = rec.get("timestamp", "")
            # Format timestamp for readability
            try:
                dt = datetime.fromisoformat(ts_raw)
                ts_str = dt.strftime("%Y-%m-%d %H:%M:%S")
            except Exception:
                ts_str = ts_raw[:19]

            cells = [
                str(rec.get("id", "")),
                ts_str,
                rec.get("username", ""),
                rec.get("role", "").title(),
                action,
                rec.get("detail", ""),
                rec.get("reason") or "–",
            ]
            for col, text in enumerate(cells):
                item = QTableWidgetItem(text)
                item.setTextAlignment(Qt.AlignmentFlag.AlignVCenter)
                if col == 4:  # action column — colour code
                    color = action_colors.get(action)
                    if color:
                        item.setForeground(QColor(color))
                        f = item.font()
                        f.setBold(True)
                        item.setFont(f)
                self._table.setItem(row, col, item)

    def _verify_chain(self):
        """
        Item 1: walk the audit trail hash chain and report integrity.
        Detects any record modified, deleted, or inserted directly in the
        database file outside the application.
        """
        ok, message, checked = audit.verify_chain()

        # Audit the verification act itself
        audit.log(
            user       = self._sm.current_user,
            action     = audit.ACTION_AUDIT_VERIFIED,
            detail     = (
                f"Audit chain verification run: "
                f"{'PASS' if ok else 'FAIL'} — {message}"
            ),
            session_id = self._sm.session_id,
        )

        if ok:
            QMessageBox.information(
                self, "✓ Audit Trail Verified",
                f"{message}\n\n"
                "Every record's cryptographic hash chain is intact.\n"
                "No tampering detected."
            )
        else:
            QMessageBox.critical(
                self, "⚠ AUDIT TRAIL INTEGRITY FAILURE",
                f"{message}\n\n"
                f"Records verified before failure: {checked}\n\n"
                "The audit trail has been modified outside the application.\n"
                "This is a serious compliance event:\n"
                "  1. Preserve the current database file immediately\n"
                "  2. Retrieve the most recent verified backup\n"
                "  3. Notify your Quality Assurance department\n"
                "  4. Investigate who had file access to this workstation"
            )
        self.refresh()

    def _export_pdf(self):
        if not self._sm or not self._sm.can("export_reports"):
            QMessageBox.warning(self, "Permission Denied",
                                "You do not have permission to export reports.")
            return

        if not report_export.REPORTLAB_OK:
            QMessageBox.critical(
                self, "Missing Dependency",
                "reportlab is not installed.\n\nRun:\n  pip install reportlab"
            )
            return

        path, _ = QFileDialog.getSaveFileName(
            self, "Export Audit Trail PDF",
            f"AuditTrail_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf",
            "PDF Files (*.pdf)"
        )
        if not path:
            return

        # Build filters from current UI state
        username = self._filter_user.text().strip() or None
        action   = self._filter_action.currentData() or None
        date_from = date_to = None
        if self._chk_use_dates.isChecked():
            qdf = self._filter_from.date()
            qdt = self._filter_to.date()
            date_from = datetime(
                qdf.year(), qdf.month(), qdf.day(), tzinfo=timezone.utc
            )
            date_to = datetime(
                qdt.year(), qdt.month(), qdt.day(),
                23, 59, 59, tzinfo=timezone.utc
            )

        ok, msg = report_export.export_audit_trail(
            output_path    = path,
            generated_by   = self._sm.current_user,
            session_id     = self._sm.session_id,
            username_filter= username,
            action_filter  = action,
            date_from      = date_from,
            date_to        = date_to,
            company_name    = self._config.company.name    if self._config else "",
            company_address = self._config.company.address if self._config else "",
        )
        if ok:
            QMessageBox.information(
                self, "Export Complete",
                f"Audit trail exported to:\n{path}"
            )
        else:
            QMessageBox.critical(self, "Export Failed", msg)


# ══════════════════════════════════════════════════════════════════════════════
#  PAGE 2: USER MANAGEMENT
# ══════════════════════════════════════════════════════════════════════════════

class UserManagementPage(QWidget):
    """User account management — administrator only."""

    def __init__(self, session_mgr: SessionManager, parent=None):
        super().__init__(parent)
        self._sm    = session_mgr
        self._users = []
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(10)

        # ── Header ────────────────────────────────────────────────────────────
        hdr = QHBoxLayout()
        hdr.addWidget(_lbl("User Management", _S_SECTION_TITLE))
        hdr.addStretch()
        btn_refresh = QPushButton("↻  Refresh")
        btn_refresh.setStyleSheet(_S_SM_BTN)
        btn_refresh.setFixedHeight(30)
        btn_refresh.clicked.connect(self.refresh)
        hdr.addWidget(btn_refresh)
        layout.addLayout(hdr)

        layout.addWidget(_lbl(
            "Manage user accounts. Only Administrators can create, "
            "deactivate, or reset passwords.",
            "font-size: 11px; color: #8a93a0;"
        ))
        layout.addWidget(_divider())

        # ── Access gate ───────────────────────────────────────────────────────
        self._gate_lbl = QLabel(
            "⚠  Administrator access required to manage users."
        )
        self._gate_lbl.setStyleSheet(
            "font-size: 12px; color: #92400e; background: #fffbeb; "
            "border: 1px solid #f59e0b; border-radius: 4px; padding: 10px;"
        )
        self._gate_lbl.hide()
        layout.addWidget(self._gate_lbl)

        # ── Users table ───────────────────────────────────────────────────────
        self._table = QTableWidget()
        self._table.setStyleSheet(_S_TABLE)
        self._table.setColumnCount(7)
        self._table.setHorizontalHeaderLabels([
            "Username", "Role", "Status", "Failed\nAttempts",
            "Password Changed", "Created By", "Created At",
        ])
        hh = self._table.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self._table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setAlternatingRowColors(True)
        self._table.verticalHeader().hide()
        layout.addWidget(self._table, 1)

        # ── Action buttons ────────────────────────────────────────────────────
        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)

        self._btn_deactivate = QPushButton("Deactivate Selected")
        self._btn_deactivate.setStyleSheet(_S_DANGER_BTN)
        self._btn_deactivate.clicked.connect(self._deactivate)
        btn_row.addWidget(self._btn_deactivate)

        self._btn_reactivate = QPushButton("Reactivate Selected")
        self._btn_reactivate.setStyleSheet(_S_SUCCESS_BTN)
        self._btn_reactivate.clicked.connect(self._reactivate)
        btn_row.addWidget(self._btn_reactivate)

        self._btn_reset_pw = QPushButton("Reset Password")
        self._btn_reset_pw.setStyleSheet(_S_SM_BTN)
        self._btn_reset_pw.clicked.connect(self._reset_pw)
        btn_row.addWidget(self._btn_reset_pw)

        self._btn_import_legacy = QPushButton("Import Legacy WAL")
        self._btn_import_legacy.setStyleSheet(_S_SM_BTN)
        self._btn_import_legacy.clicked.connect(self._import_legacy_wal)
        btn_row.addWidget(self._btn_import_legacy)

        btn_row.addStretch()
        layout.addLayout(btn_row)

        layout.addWidget(_divider())

        # ── Create user form ──────────────────────────────────────────────────
        layout.addWidget(_lbl("Create New Account", _S_SECTION_TITLE))
        layout.addSpacing(4)

        form = QHBoxLayout()
        form.setSpacing(10)

        form.addWidget(_lbl("Username", _S_FIELD_LABEL))
        self._new_user = _inp("Username", width=140)
        form.addWidget(self._new_user)

        form.addWidget(_lbl("Temp Password", _S_FIELD_LABEL))
        self._new_pw = QLineEdit()
        self._new_pw.setEchoMode(QLineEdit.EchoMode.Password)
        self._new_pw.setPlaceholderText("Temporary password")
        self._new_pw.setStyleSheet(_S_INPUT)
        self._new_pw.setFixedHeight(32)
        self._new_pw.setFixedWidth(180)
        form.addWidget(self._new_pw)

        form.addWidget(_lbl("Role", _S_FIELD_LABEL))
        self._new_role = QComboBox()
        self._new_role.setStyleSheet(_S_INPUT)
        self._new_role.setFixedHeight(32)
        self._new_role.setFixedWidth(140)
        for role in ALL_ROLES:
            self._new_role.addItem(ROLE_DISPLAY[role], role)
        form.addWidget(self._new_role)

        btn_create = QPushButton("Create Account")
        btn_create.setStyleSheet(_S_ACTION_BTN)
        btn_create.setFixedHeight(32)
        btn_create.clicked.connect(self._create)
        form.addWidget(btn_create)

        form.addStretch()
        layout.addLayout(form)

        self._lbl_create_err = QLabel("")
        self._lbl_create_err.setStyleSheet(
            "color: #c0392b; font-size: 11px;"
        )
        layout.addWidget(self._lbl_create_err)

    def refresh(self):
        is_admin = self._sm and self._sm.can("manage_users")
        self._gate_lbl.setVisible(not is_admin)
        self._table.setEnabled(is_admin)
        self._btn_deactivate.setEnabled(is_admin)
        self._btn_reactivate.setEnabled(is_admin)
        self._btn_reset_pw.setEnabled(is_admin)
        self._btn_import_legacy.setEnabled(is_admin)
        self._new_user.setEnabled(is_admin)
        self._new_pw.setEnabled(is_admin)
        self._new_role.setEnabled(is_admin)

        self._users = get_all_users()
        self._table.setRowCount(len(self._users))

        for row, u in enumerate(self._users):
            status = "Active" if u.is_active else "Inactive"
            if u.is_locked():
                status += " (Locked)"
            cells = [
                u.username,
                u.role_display,
                status,
                str(u.failed_attempts),
                u.password_changed_at.strftime("%Y-%m-%d"),
                u.created_by,
                u.created_at.strftime("%Y-%m-%d"),
            ]
            for col, text in enumerate(cells):
                item = QTableWidgetItem(text)
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                if not u.is_active:
                    item.setForeground(QColor("#b0b8c4"))
                elif col == 2 and "Locked" in text:
                    item.setForeground(QColor(_RED))
                self._table.setItem(row, col, item)

    def _selected_user(self):
        row = self._table.currentRow()
        if row < 0 or row >= len(self._users):
            QMessageBox.warning(self, "No Selection",
                                "Please select a user from the table first.")
            return None
        return self._users[row]

    def _deactivate(self):
        u = self._selected_user()
        if not u:
            return
        if not u.is_active:
            QMessageBox.information(self, "Already Inactive",
                                    "That account is already inactive.")
            return
        reply = QMessageBox.question(
            self, "Confirm Deactivation",
            f"Deactivate account '{u.username}'?\n"
            "The user will no longer be able to log in.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        ok, msg = deactivate_account(
            self._sm.current_user, self._sm.session_id, u.username)
        if ok:
            self.refresh()
            QMessageBox.information(self, "Done",
                                    f"Account '{u.username}' deactivated.")
        else:
            QMessageBox.critical(self, "Error", msg)

    def _reactivate(self):
        u = self._selected_user()
        if not u:
            return
        if u.is_active:
            QMessageBox.information(self, "Already Active",
                                    "That account is already active.")
            return
        ok, msg = reactivate_account(
            self._sm.current_user, self._sm.session_id, u.username)
        if ok:
            self.refresh()
            QMessageBox.information(self, "Done",
                                    f"Account '{u.username}' reactivated.")
        else:
            QMessageBox.critical(self, "Error", msg)

    def _reset_pw(self):
        u = self._selected_user()
        if not u:
            return

        from gui.cfr_dialogs import _ResetPasswordDialog
        dlg = _ResetPasswordDialog(u.username, parent=self)
        if dlg.exec() != dlg.DialogCode.Accepted:
            return

        from gui.cfr_dialogs import ReauthDialog
        from cfr21.reauthentication_service import ReauthenticationError, issue_grant
        reauth = ReauthDialog(self._sm, "reset this password", self)
        if reauth.exec() != ReauthDialog.DialogCode.Accepted:
            return
        try:
            grant = issue_grant(
                self._sm.current_user, self._sm.session_id, reauth.verified_password,
                "manage_users", f"user:{u.username}")
        except ReauthenticationError as exc:
            QMessageBox.critical(self, "Reauthentication Failed", str(exc))
            return

        ok, msg = reset_password(
            self._sm.current_user, self._sm.session_id, u.username, dlg.new_password, grant
        )
        if ok:
            QMessageBox.information(
                self, "Password Reset",
                f"Password for '{u.username}' reset. "
                "They will be prompted to change it on next login."
            )
        else:
            QMessageBox.critical(self, "Error", msg)

    def _create(self):
        self._lbl_create_err.setText("")
        username = self._new_user.text().strip()
        password = self._new_pw.text()
        role     = self._new_role.currentData()

        ok, msg = create_account(
            self._sm.current_user, self._sm.session_id, username, password, role
        )
        if ok:
            self._new_user.clear()
            self._new_pw.clear()
            self.refresh()
            QMessageBox.information(
                self, "Account Created",
                f"Account '{username}' created.\n"
                "User must change their password on first login."
            )
        else:
            self._lbl_create_err.setText(msg)

    def _import_legacy_wal(self):
        """Administrator-only, explicitly confirmed legacy evidence import."""
        if not self._sm or not self._sm.can("import_legacy_wal"):
            QMessageBox.warning(self, "Permission Denied",
                                "Administrator authority is required for legacy import.")
            return
        source, _ = QFileDialog.getOpenFileName(self, "Select Legacy WAL", "",
                                                  "CSV Files (*.csv)")
        if not source:
            return
        evidence_dir = QFileDialog.getExistingDirectory(
            self, "Select Controlled Evidence Directory")
        if not evidence_dir:
            return
        reply = QMessageBox.question(
            self, "Confirm Legacy Import",
            "This creates immutable records classified as legacy/non-retrospectively-compliant. "
            "The original file will be preserved and hashed. Continue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        try:
            from cfr21.legacy_wal_import import import_legacy_wal
            result = import_legacy_wal(self._sm.current_user, source, evidence_dir,
                                       self._sm.session_id)
        except Exception as exc:
            QMessageBox.critical(self, "Legacy Import Failed", str(exc))
            return
        QMessageBox.information(
            self, "Legacy Import Complete",
            f"Imported rows: {result['row_count']}\n"
            f"Source SHA-256: {result['source_sha256']}\n"
            f"Reconciliation report: {result['report_path']}\n\n"
            "Imported records are marked legacy/non-retrospectively-compliant.")


# ══════════════════════════════════════════════════════════════════════════════
#  PAGE 3: FILE INTEGRITY
# ══════════════════════════════════════════════════════════════════════════════

class DeviceManagementPage(QWidget):
    """Administrator-only device registry operations backed by live sessions."""

    def __init__(self, session_mgr: SessionManager, parent=None):
        super().__init__(parent)
        self._sm = session_mgr
        self._service = DeviceRegistryService()
        self._rows: list[dict] = []
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 16)
        header = QHBoxLayout()
        header.addWidget(_lbl("Device Management", _S_SECTION_TITLE))
        header.addStretch()
        refresh = QPushButton("Refresh")
        refresh.setStyleSheet(_S_SM_BTN)
        refresh.clicked.connect(self.refresh)
        header.addWidget(refresh)
        layout.addLayout(header)
        self._table = QTableWidget(0, 6)
        self._table.setHorizontalHeaderLabels(["Number", "Name", "Source", "Approval", "Enabled", "Created By"])
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setStyleSheet(_S_TABLE)
        self._table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self._table, 1)

        form = QHBoxLayout()
        self._number = QSpinBox(); self._number.setRange(1, 9999); self._number.setStyleSheet(_S_INPUT)
        self._source = _inp("Source identity", 180)
        self._name = _inp("Display name", 150)
        self._reason = _inp("Reason", 180)
        register = QPushButton("Register")
        register.setStyleSheet(_S_ACTION_BTN)
        register.clicked.connect(self._register)
        form.addWidget(self._number); form.addWidget(self._source); form.addWidget(self._name)
        form.addWidget(self._reason); form.addWidget(register); form.addStretch()
        layout.addLayout(form)

        actions = QHBoxLayout()
        approve = QPushButton("Approve Selected")
        approve.setStyleSheet(_S_SUCCESS_BTN)
        approve.clicked.connect(self._approve)
        deactivate = QPushButton("Deactivate Selected")
        deactivate.setStyleSheet(_S_DANGER_BTN)
        deactivate.clicked.connect(self._deactivate)
        replace = QPushButton("Replace Selected")
        replace.setStyleSheet(_S_DANGER_BTN)
        replace.clicked.connect(self._replace)
        assign = QPushButton("Assign Selected")
        assign.setStyleSheet(_S_SM_BTN)
        assign.clicked.connect(self._assign)
        actions.addWidget(approve); actions.addWidget(deactivate); actions.addWidget(replace); actions.addWidget(assign); actions.addStretch()
        layout.addLayout(actions)

    def _actor(self):
        if not self._sm.current_user or not self._sm.can("manage_devices"):
            QMessageBox.warning(self, "Permission Denied", "Administrator device authority is required.")
            return None
        return self._sm.current_user

    def _selected(self):
        row = self._table.currentRow()
        return self._rows[row] if 0 <= row < len(self._rows) else None

    def refresh(self):
        actor = self._actor()
        if not actor:
            return
        try:
            self._rows = self._service.list_devices(actor, self._sm.session_id)
        except Exception as exc:
            QMessageBox.critical(self, "Device Registry", str(exc)); return
        self._table.setRowCount(len(self._rows))
        for index, row in enumerate(self._rows):
            values = [row["device_number"], row["display_name"], row["source_identifier"],
                      row["approval_status"], "Yes" if row["enabled"] else "No", row["created_by"]]
            for column, value in enumerate(values):
                self._table.setItem(index, column, QTableWidgetItem(str(value)))

    def _register(self):
        actor = self._actor()
        if not actor:
            return
        try:
            self._service.register_device(actor, self._sm.session_id, self._number.value(),
                                          self._source.text(), self._name.text(), self._reason.text())
            self._source.clear(); self._name.clear(); self._reason.clear(); self.refresh()
        except DeviceRegistryError as exc:
            QMessageBox.critical(self, "Registration Blocked", str(exc))

    def _grant(self, device_id: str):
        from gui.cfr_dialogs import ReauthDialog
        dialog = ReauthDialog(self._sm, "change this device", self)
        if dialog.exec() != dialog.DialogCode.Accepted:
            return ""
        return issue_grant(self._sm.current_user, self._sm.session_id,
                           dialog.verified_password, "manage_devices", f"device:{device_id}")

    def _approve(self):
        row = self._selected()
        if not row:
            return
        try:
            grant = self._grant(row["id"])
            if grant:
                self._service.approve_device(self._sm.current_user, self._sm.session_id,
                                             row["id"], self._reason.text() or "device approval", grant)
                self.refresh()
        except (DeviceRegistryError, ReauthenticationError) as exc:
            QMessageBox.critical(self, "Approval Blocked", str(exc))

    def _deactivate(self):
        row = self._selected()
        if not row:
            return
        try:
            grant = self._grant(row["id"])
            if grant:
                self._service.deactivate_device(self._sm.current_user, self._sm.session_id,
                                                row["id"], self._reason.text() or "device deactivation", grant)
                self.refresh()
        except (DeviceRegistryError, ReauthenticationError) as exc:
            QMessageBox.critical(self, "Deactivation Blocked", str(exc))

    def _replace(self):
        row = self._selected()
        if not row:
            return
        try:
            grant = self._grant(row["id"])
            if grant:
                self._service.replace_device(
                    self._sm.current_user, self._sm.session_id, row["id"],
                    self._number.value(), self._source.text(), self._name.text(),
                    self._reason.text() or "device replacement", grant)
                self._source.clear(); self._name.clear(); self._reason.clear(); self.refresh()
        except (DeviceRegistryError, ReauthenticationError) as exc:
            QMessageBox.critical(self, "Replacement Blocked", str(exc))

    def _assign(self):
        row = self._selected()
        actor = self._actor()
        if not row or not actor:
            return
        from cfr21.db import get_conn
        conn = get_conn()
        try:
            batches = conn.execute("""
                SELECT id, external_batch_id FROM regulated_batches
                WHERE state IN ('draft', 'configured') ORDER BY created_at DESC
            """).fetchall()
        finally:
            conn.close()
        if not batches:
            QMessageBox.information(self, "No Eligible Batches",
                                    "No draft or configured batch is available for assignment.")
            return
        labels = [f"{batch['external_batch_id']}  {batch['id']}" for batch in batches]
        selected, ok = QInputDialog.getItem(self, "Assign Device", "Batch", labels, 0, False)
        if not ok:
            return
        try:
            self._service.assign_device(actor, self._sm.session_id,
                                        batches[labels.index(selected)]["id"], row["id"],
                                        self._reason.text() or "device assignment")
        except DeviceRegistryError as exc:
            QMessageBox.critical(self, "Assignment Blocked", str(exc))


class FileIntegrityPage(QWidget):
    """
    View and verify SHA-256 checksums stored at batch close.
    Lets QA/admin verify that no log file has been tampered with.
    """

    def __init__(self, session_mgr: SessionManager, parent=None, config=None):
        super().__init__(parent)
        self._sm = session_mgr
        self._config = config
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(10)

        # ── Header ────────────────────────────────────────────────────────────
        hdr = QHBoxLayout()
        hdr.addWidget(_lbl("File Integrity", _S_SECTION_TITLE))
        hdr.addStretch()
        btn_refresh = QPushButton("↻  Refresh")
        btn_refresh.setStyleSheet(_S_SM_BTN)
        btn_refresh.setFixedHeight(30)
        btn_refresh.clicked.connect(self.refresh)
        hdr.addWidget(btn_refresh)
        layout.addLayout(hdr)

        layout.addWidget(_lbl(
            "SHA-256 checksums are recorded every time a batch is stopped. "
            "Verify below to confirm no log file has been altered since sealing.",
            "font-size: 11px; color: #8a93a0;"
        ))
        layout.addWidget(_divider())

        # ── Filter by batch ───────────────────────────────────────────────────
        filter_row = QHBoxLayout()
        filter_row.setSpacing(8)
        filter_row.addWidget(_lbl("BATCH ID FILTER", _S_FIELD_LABEL))
        self._filter_batch = _inp("Leave blank for all batches", width=220)
        filter_row.addWidget(self._filter_batch)

        btn_apply = QPushButton("Apply")
        btn_apply.setStyleSheet(_S_ACTION_BTN)
        btn_apply.setFixedHeight(32)
        btn_apply.setFixedWidth(70)
        btn_apply.clicked.connect(self.refresh)
        filter_row.addWidget(btn_apply)

        btn_verify = QPushButton("Verify Selected Batch")
        btn_verify.setStyleSheet(_S_ACTION_BTN)
        btn_verify.setFixedHeight(32)
        btn_verify.clicked.connect(self._verify_selected)
        filter_row.addWidget(btn_verify)

        btn_pdf = QPushButton("⬇  Export Batch PDF")
        btn_pdf.setStyleSheet(_S_ACTION_BTN)
        btn_pdf.setFixedHeight(32)
        btn_pdf.clicked.connect(self._export_batch_pdf)
        filter_row.addWidget(btn_pdf)

        filter_row.addStretch()
        layout.addLayout(filter_row)

        # ── Records table ─────────────────────────────────────────────────────
        self._table = QTableWidget()
        self._table.setStyleSheet(_S_TABLE)
        self._table.setColumnCount(6)
        self._table.setHorizontalHeaderLabels([
            "Batch ID", "Device", "File Type",
            "File Name", "Sealed By", "Sealed At",
        ])
        hh = self._table.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self._table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setAlternatingRowColors(True)
        self._table.verticalHeader().hide()
        layout.addWidget(self._table, 1)

        # ── Verification results panel ────────────────────────────────────────
        self._result_panel = QLabel("")
        self._result_panel.setWordWrap(True)
        self._result_panel.setStyleSheet(
            "font-size: 11px; color: #1a1e24; padding: 8px; "
            "border: 1px solid #d0d4da; border-radius: 4px; "
            "background: #f8f9fb; min-height: 40px;"
        )
        layout.addWidget(self._result_panel)

        self._records = []

    def refresh(self):
        batch_filter = self._filter_batch.text().strip() or None
        self._records = get_integrity_records(
            batch_id=batch_filter, limit=200
        )
        self._table.setRowCount(len(self._records))

        for row, rec in enumerate(self._records):
            ts_raw = rec.get("timestamp", "")
            try:
                dt = datetime.fromisoformat(ts_raw)
                ts_str = dt.strftime("%Y-%m-%d %H:%M:%S")
            except Exception:
                ts_str = ts_raw[:19]

            cells = [
                rec.get("batch_id", ""),
                f"Device {rec.get('device_id', '')}",
                rec.get("file_type", "").upper(),
                os.path.basename(rec.get("file_path", "")),
                rec.get("username", ""),
                ts_str,
            ]
            for col, text in enumerate(cells):
                item = QTableWidgetItem(text)
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self._table.setItem(row, col, item)

        self._result_panel.setText(
            f"{len(self._records)} integrity record(s) loaded. "
            "Select records and click 'Verify Selected Batch' to re-hash and compare."
        )

    def _verify_selected(self):
        row = self._table.currentRow()
        if row < 0 or row >= len(self._records):
            QMessageBox.warning(self, "No Selection",
                                "Please select a record to verify.")
            return

        rec       = self._records[row]
        batch_id  = rec.get("batch_id", "")
        device_id = rec.get("device_id")

        results = verify_batch_files(batch_id, device_id)

        if not results:
            self._result_panel.setText(
                f"No integrity records found for batch '{batch_id}'."
            )
            return

        lines = [f"Verification for batch '{batch_id}' — Device {device_id}:\n"]
        all_ok = True
        for r in results:
            ftype = r.get("file_type", "").upper()
            fname = os.path.basename(r.get("file_path", ""))
            if r.get("error"):
                status = f"❌  ERROR: {r['error']}"
                all_ok = False
            elif r.get("match"):
                status = "✅  INTACT — hash matches"
            else:
                status = "❌  TAMPERED — hash mismatch"
                all_ok = False
            lines.append(f"  {ftype}  {fname}:  {status}")

        summary = "✅  ALL FILES INTACT" if all_ok else "❌  INTEGRITY FAILURE — contact QA"
        lines.append(f"\n{summary}")

        self._result_panel.setText("\n".join(lines))
        self._result_panel.setStyleSheet(
            "font-size: 11px; padding: 8px; border-radius: 4px; "
            "border: 1px solid {}; background: {}; color: #1a1e24;".format(
                "#86efac" if all_ok else "#fca5a5",
                "#f0fdf4" if all_ok else "#fef2f2",
            )
        )

    def _export_batch_pdf(self):
        """Issue 4: Export a full Batch Record PDF for the selected batch."""
        if not self._sm or not self._sm.can("export_reports"):
            QMessageBox.warning(self, "Permission Denied",
                                "You do not have permission to export reports.")
            return

        if not report_export.REPORTLAB_OK:
            QMessageBox.critical(
                self, "Missing Dependency",
                "reportlab is not installed.\n\nRun:\n  pip install reportlab"
            )
            return

        row = self._table.currentRow()
        if row < 0 or row >= len(self._records):
            QMessageBox.warning(self, "No Selection",
                                "Please select a batch record from the table first.")
            return

        rec       = self._records[row]
        batch_id  = rec.get("batch_id", "")
        device_id = rec.get("device_id", 1)

        path, _ = QFileDialog.getSaveFileName(
            self, "Export Batch Record PDF",
            f"BatchRecord_{batch_id}_Device{device_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf",
            "PDF Files (*.pdf)"
        )
        if not path:
            return

        ok, msg = report_export.export_batch_record(
            output_path  = path,
            generated_by = self._sm.current_user,
            batch_id     = batch_id,
            device_id    = device_id,
            session_id   = self._sm.session_id,
            company_name    = self._config.company.name    if self._config else "",
            company_address = self._config.company.address if self._config else "",
        )

        if ok:
            QMessageBox.information(
                self, "Export Complete",
                f"Batch record exported to:\n{path}"
            )
        else:
            QMessageBox.critical(self, "Export Failed", msg)
