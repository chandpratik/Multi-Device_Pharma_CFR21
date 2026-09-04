# gui/main_window.py
# MainWindow — UI assembly only.
# Hardware operations go through AppController.
# Business logic lives in Datalogger / AppController.
#
# ── Thread ownership reminder ─────────────────────────────────────────────────
#   GUI thread    — all Qt widgets, all methods in this file
#   scan threads  — one per device, inside Datalogger._run_loop
#   plc threads   — one per device, inside Datalogger._plc_worker
#   Cross-thread callbacks → pyqtSignal.emit (Qt handles dispatch)
# ─────────────────────────────────────────────────────────────────────────────

import os
import sys
import json
import subprocess
from datetime import datetime

from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QFrame, QTabWidget, QScrollArea, QMessageBox,
    QStackedWidget,QLineEdit, QInputDialog,
)
from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QBrush, QFont, QKeySequence, QShortcut

try:
    import pyqtgraph as pg
    from PyQt6.QtGui import QPen
    PG_OK = True
except ImportError:
    PG_OK = False

try:
    from PyQt6.QtWebEngineWidgets import QWebEngineView
    from PyQt6.QtCore import QUrl
    WEB_OK = True
except ImportError:
    WEB_OK = False

from version import __version__, __app_name__, __company__
from config.settings import AppConfig
from core.models import SessionInfo, ReadRecord
from core.app_controller import AppController
import license_check

from gui.styles import QSS
from gui.ui_constants import UI
from gui.widgets import (
    make_sbtn, make_bbtn, sidebar_sep, sidebar_label,
    bottom_sep, load_bundled_fonts,
    BATCH_FILE, OPERATOR_FILE, PRODUCT_FILE
)
from gui.device_panel import DevicePanel
from gui.dialogs import SessionSetupDialog, AdvancedSettingsPage
# CFR21 imports
import cfr21.audit_trail as audit
from cfr21.db import get_conn
from gui.cfr_dialogs import (
    ReauthDialog, ReasonDialog, ChangePasswordDialog,
    LoginDialog,
)
from gui.cfr_tab import CFR21Tab


class MainWindow(QMainWindow):

    # ── cross-thread signals (wired to datalogger callbacks) ──────────────────
    # Per device — device_id is embedded in the lambda wiring below
    sig_status_d1        = pyqtSignal(str)
    sig_status_d2        = pyqtSignal(str)
    sig_read_logged_d1   = pyqtSignal(object)
    sig_read_logged_d2   = pyqtSignal(object)
    sig_live_sample_d1   = pyqtSignal(object, bool, object)
    sig_live_sample_d2   = pyqtSignal(object, bool, object)
    sig_teach_done_d1    = pyqtSignal(str)
    sig_teach_done_d2    = pyqtSignal(str)
    sig_consec_fail_d1   = pyqtSignal(int)
    sig_consec_fail_d2   = pyqtSignal(int)
    sig_cam_disconnect_d1 = pyqtSignal()
    sig_cam_disconnect_d2 = pyqtSignal()
    sig_wal_error_d1     = pyqtSignal(str)
    sig_wal_error_d2     = pyqtSignal(str)
    sig_plc_error_d1     = pyqtSignal(int)
    sig_plc_error_d2     = pyqtSignal(int)

    def __init__(self, config: AppConfig, session_mgr=None):
        super().__init__()
        self._config     = config
        self._controller = AppController(config)
        self._session    = SessionInfo()
        self._sm         = session_mgr  # CFR21 SessionManager (may be None in tests)

        # CFR21: push logged-in user into WAL loggers for integrity sealing
        if self._sm and self._sm.current_user:
            self._controller.set_cfr_session(
                self._sm.current_user, self._sm.session_id)

        self.setWindowTitle(
            f"{__company__} – {__app_name__}  v{__version__}")
        self.resize(UI.WINDOW_W, UI.WINDOW_H)
        self.setMinimumSize(UI.WINDOW_MIN_W, UI.WINDOW_MIN_H)
        self.setStyleSheet(QSS)

        # Persistence
        self.batch_ids     = self._load_list(BATCH_FILE)
        self.operator_ids  = self._load_list(OPERATOR_FILE)
        self.product_names = self._load_list(PRODUCT_FILE)

        self._build_ui()
        self._wire_controller_callbacks()
        self._wire_panel_signals()
        self._connect_signals()

        # Sync chips to loaded config
        self._refresh_chips()

        self._date_timer = QTimer(self)
        self._date_timer.timeout.connect(self._update_date)
        self._date_timer.start(60_000)
        self._update_date()

        # CFR21: inactivity timeout check every 60 seconds
        self._inactivity_timer = QTimer(self)
        self._inactivity_timer.timeout.connect(self._check_inactivity)
        self._inactivity_timer.start(60_000)

        # CFR21: wire screen lock callback (replaces old on_session_timeout)
        if self._sm:
            self._sm.on_screen_lock = self._show_lock_screen

        # CFR21 Fix 6: apply role-based UI gating on startup
        self._apply_role_ui()

        # Build lock screen overlay (hidden until needed)
        self._lock_overlay = self._build_lock_overlay()

        # Item 7: detect WALs from crashed batches that were never sealed

        # CFR21 Fix 9: only auto-open session setup for roles that can start logging
        if not self._sm or self._sm.can("start_logging"):
            QTimer.singleShot(300, self.open_session_setup)

    def _check_orphaned_wals(self):
        """
        Item 7: on startup, find WAL files that were never sealed —
        batches interrupted by a crash. Offer to seal them now so the
        crash story is complete and the records are protected.
        """
        from cfr21.record_integrity import find_orphaned_wals, seal_orphaned_wal

        wal_dirs = {
            1: self._config.device_wal_dir(1),
            2: self._config.device_wal_dir(2),
        }
        orphans = find_orphaned_wals(wal_dirs)
        if not orphans:
            return

        names = "\n".join(
            f"  • Device {o['device_id']}: {o['batch_hint']}"
            for o in orphans
        )
        resp = QMessageBox.question(
            self, "Unsealed Batch Records Found",
            f"{len(orphans)} batch record(s) from a previous session were "
            f"never sealed — likely due to a crash or power loss:\n\n"
            f"{names}\n\n"
            "Seal them now? (Recommended — protects the records with "
            "SHA-256 checksums. The batches remain re-openable by "
            "entering the same Batch ID.)",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if resp != QMessageBox.StandardButton.Yes:
            return

        sealed, failed = 0, 0
        for o in orphans:
            ok, info = seal_orphaned_wal(
                self._sm.current_user if self._sm else None,
                o["device_id"], o["wal_path"]
            )
            if ok:
                sealed += 1
                if self._sm:
                    audit.log(
                        user       = self._sm.current_user,
                        action     = audit.ACTION_ORPHAN_WAL_SEALED,
                        detail     = (
                            f"Orphaned WAL sealed after crash recovery. "
                            f"Device {o['device_id']}, batch '{info}', "
                            f"file: {o['batch_hint']}"
                        ),
                        session_id = self._sm.session_id,
                    )
            else:
                failed += 1

        QMessageBox.information(
            self, "Sealing Complete",
            f"Sealed: {sealed}\nFailed: {failed}" +
            ("\n\nCheck the log for failure details." if failed else "")
        )

    # ══════════════════════════════════════════════════════════════════════════
    #  UI CONSTRUCTION
    # ══════════════════════════════════════════════════════════════════════════

    def _build_ui(self):
        central = QWidget()
        central.setObjectName("central")
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._build_topbar())
        root.addWidget(self._build_main(), 1)
        root.addWidget(self._build_bottom_bar())

    # ── topbar ────────────────────────────────────────────────────────────────

    def _build_topbar(self) -> QWidget:
        bar = QWidget()
        bar.setObjectName("topbar")
        bar.setFixedHeight(UI.TOPBAR_H)
        h = QHBoxLayout(bar)
        h.setContentsMargins(16, 0, 16, 0)
        h.setSpacing(10)

        title = QLabel(__company__)
        title.setObjectName("topbar_title")
        sub = QLabel(f" / {__app_name__}")
        sub.setObjectName("topbar_sub")
        h.addWidget(title)
        h.addWidget(sub)
        h.addSpacing(16)

        self._chip_plc = self._make_plc_chip(self._config.plc.ip)
        h.addWidget(self._chip_plc)
        h.addStretch()


        self.lbl_date = QLabel("")
        self.lbl_date.setStyleSheet(
            "color: rgba(255,255,255,0.75); font-size: 11px;")
        h.addWidget(self.lbl_date)

        # CFR21 Fix 4: user label is now a clickable button
        # Any role can click it to change their own password
        self._btn_user = QPushButton("")
        self._btn_user.setStyleSheet(
            "QPushButton { background: rgba(255,255,255,0.10); "
            "color: rgba(255,255,255,0.90); font-size: 11px; "
            "font-weight: 600; padding: 4px 10px; border-radius: 4px; "
            "border: 1px solid rgba(255,255,255,0.20); } "
            "QPushButton:hover { background: rgba(255,255,255,0.20); } "
            "QPushButton:pressed { background: rgba(255,255,255,0.05); }"
        )
        self._btn_user.setToolTip("Click to change your password")
        self._btn_user.clicked.connect(self._open_change_password)
        h.addWidget(self._btn_user)

        # CFR21: Logout button — explicitly ends the session and stops logging
        self._btn_logout = QPushButton("⏻  Sign Out")
        self._btn_logout.setStyleSheet(
            "QPushButton { background: rgba(220,50,50,0.20); "
            "color: rgba(255,200,200,0.95); font-size: 11px; "
            "font-weight: 600; padding: 4px 10px; border-radius: 4px; "
            "border: 1px solid rgba(220,50,50,0.40); } "
            "QPushButton:hover { background: rgba(220,50,50,0.40); } "
            "QPushButton:pressed { background: rgba(220,50,50,0.15); }"
        )
        self._btn_logout.setToolTip("Sign out and lock the application")
        self._btn_logout.clicked.connect(self._on_logout_clicked)
        h.addWidget(self._btn_logout)

        self._update_user_label()

        self.lbl_logging_badge = QLabel("● SCANNING")
        self.lbl_logging_badge.setObjectName("logging_badge")
        self.lbl_logging_badge.hide()
        h.addWidget(self.lbl_logging_badge)

        self.btn_fullscreen = QPushButton("⛶")
        self.btn_fullscreen.setObjectName("topbar_btn")
        self.btn_fullscreen.setToolTip("Toggle Fullscreen  (F11)")
        self.btn_fullscreen.setFixedSize(UI.TOPBAR_BTN_SIZE, UI.TOPBAR_BTN_SIZE)
        self.btn_fullscreen.clicked.connect(self.toggle_fullscreen)
        h.addWidget(self.btn_fullscreen)

        QShortcut(QKeySequence("F11"), self).activated.connect(
            self.toggle_fullscreen)

        return bar

    def _make_plc_chip(self, ip: str) -> QWidget:
        """
        PLC status chip with embedded connect/disconnect toggle.
        Left side: label + IP.  Right side: small toggle button.
        """
        chip = QWidget()
        chip.setObjectName("status_chip")
        h = QHBoxLayout(chip)
        h.setContentsMargins(8, 3, 6, 3)
        h.setSpacing(8)

        # Left — label stack
        text_w = QWidget()
        text_w.setStyleSheet("background: transparent;")
        tv = QVBoxLayout(text_w)
        tv.setContentsMargins(0, 0, 0, 0)
        tv.setSpacing(1)
        lbl = QLabel("PLC")
        lbl.setObjectName("chip_label")
        self._plc_val_lbl = QLabel(ip)
        self._plc_val_lbl.setObjectName("chip_val_PLC")
        self._plc_val_lbl.setStyleSheet(
            "color: rgba(255,255,255,0.85); font-size:11px; font-weight:500; "
            "font-family:'IBM Plex Mono','Consolas','Courier New',monospace;")
        tv.addWidget(lbl)
        tv.addWidget(self._plc_val_lbl)
        h.addWidget(text_w)

        # Right — toggle button
        self.btn_plc_toggle = QPushButton("Connect")
        self.btn_plc_toggle.setObjectName("chip_btn_connect")
        self.btn_plc_toggle.setFixedHeight(30)
        self.btn_plc_toggle.setFixedWidth(90)
        self.btn_plc_toggle.clicked.connect(self._on_plc_toggle)
        h.addWidget(self.btn_plc_toggle)

        return chip

    def _on_plc_toggle(self):
        if self._controller.plc_is_open():
            self.disconnect_plc()
        else:
            self.connect_plc()

    def _update_plc_chip(self, ip_port: str, connected: bool):
        self._plc_val_lbl.setText(ip_port)
        color = "#4cdf7c" if connected else "rgba(255,255,255,0.85)"
        self._plc_val_lbl.setStyleSheet(
            f"color: {color}; font-size:11px; font-weight:500; "
            "font-family:'IBM Plex Mono','Consolas','Courier New',monospace;")
        self.btn_plc_toggle.setText("Disconnect" if connected else "Connect")
        self.btn_plc_toggle.setObjectName(
            "chip_btn_disconnect" if connected else "chip_btn_connect")
        self.btn_plc_toggle.style().unpolish(self.btn_plc_toggle)
        self.btn_plc_toggle.style().polish(self.btn_plc_toggle)


    def _update_chip(self, label: str, value: str, connected: bool):
        """Generic chip updater — routes PLC to dedicated method."""
        if label == "PLC":
            self._update_plc_chip(value, connected)
            return

    # ── bottom bar ────────────────────────────────────────────────────────────

    def _build_bottom_bar(self) -> QWidget:
        bar = QWidget()
        bar.setObjectName("bottom_bar")
        bar.setFixedHeight(UI.BOTTOM_BAR_H)
        h = QHBoxLayout(bar)
        h.setContentsMargins(8, 0, 8, 0)
        h.setSpacing(0)
        h.setAlignment(Qt.AlignmentFlag.AlignVCenter)

        self.btn_session   = make_bbtn("  ☰  Session Setup", "warn")
        self.btn_session.clicked.connect(self.open_session_setup)
        self.btn_start     = make_bbtn("  ▶  Start Logging")
        self.btn_stop      = make_bbtn("  ■  Stop Logging", "danger")
        self.btn_logs      = make_bbtn("  📁  Open Logs Folder")

        self.btn_start.setEnabled(False)
        self.btn_stop.setEnabled(False)

        for btn in [self.btn_session, self.btn_start,
                    self.btn_stop, self.btn_logs]:
            h.addWidget(btn)

        h.addStretch()

        footer = QLabel(f"{__company__} · Pharma Code Logger  v{__version__}")
        footer.setStyleSheet(
            "color:#8a93a0; font-size:10px; font-weight:300; padding:0px 12px;")
        h.addWidget(footer)
        return bar

    # ── main content ──────────────────────────────────────────────────────────

    def _build_main(self) -> QWidget:
        container = QWidget()
        container.setObjectName("central")
        v = QVBoxLayout(container)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self._build_live_tab()
        self._build_camera_views_tab()
        self._build_viz_tab()
        self._build_batch_tab()
        self._build_adv_tab()
        self._build_cfr21_tab()   # CFR21: compliance tab
        self._build_help_tab()
        v.addWidget(self.tabs)
        return container

    # ── live monitoring tab ───────────────────────────────────────────────────

    def _build_live_tab(self):
        tab = QWidget()
        tab.setObjectName("central")
        v = QVBoxLayout(tab)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)

        # ── Session info strip (full width, above both panels) ────────────────
        self._session_strip = self._build_session_strip()
        v.addWidget(self._session_strip)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet("background: #d0d4da; max-height:1px;")
        v.addWidget(sep)

        # ── Device panels side by side ────────────────────────────────────────
        panels = QWidget()
        panels.setObjectName("central")
        h = QHBoxLayout(panels)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(0)

        self.panel_d1 = DevicePanel(
            device_id=1, camera_ip=self._config.device1.camera_ip)
        self.panel_d2 = DevicePanel(
            device_id=2, camera_ip=self._config.device2.camera_ip)

        vsep = QFrame()
        vsep.setObjectName("device_vsep")
        vsep.setFrameShape(QFrame.Shape.VLine)

        h.addWidget(self.panel_d1, 1)
        h.addWidget(vsep)
        h.addWidget(self.panel_d2, 1)
        v.addWidget(panels, 1)

        self.tabs.addTab(tab, "Live Monitoring")

    def _build_session_strip(self) -> QWidget:
        """Full-width session info row — Batch ID, Operator ID, Product Name."""
        strip = QWidget()
        strip.setObjectName("session_strip")
        strip.setFixedHeight(46)
        h = QHBoxLayout(strip)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(0)

        def cell(label_text):
            w = QWidget()
            w.setObjectName("session_cell")
            cv = QHBoxLayout(w)
            cv.setContentsMargins(16, 0, 16, 0)
            cv.setSpacing(10)
            lbl = QLabel(label_text.upper())
            lbl.setObjectName("session_cell_label")
            val = QLabel("–")
            val.setObjectName("session_cell_value")
            cv.addWidget(lbl)
            cv.addWidget(val, 1)
            return w, val

        c1, self.lbl_session_batch    = cell("Batch ID")
        c2, self.lbl_session_operator = cell("Operator ID")
        c3, self.lbl_session_product  = cell("Product Name")

        for i, c in enumerate([c1, c2, c3]):
            h.addWidget(c, 1)
            if i < 2:
                sep = QFrame()
                sep.setObjectName("kpi_sep")
                sep.setFrameShape(QFrame.Shape.VLine)
                h.addWidget(sep)

        return strip

    # ── camera views tab ──────────────────────────────────────────────────────

    def _build_camera_views_tab(self):
        tab = QWidget()
        tab.setObjectName("central")
        h = QHBoxLayout(tab)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(0)

        h.addWidget(self._build_camera_pane(1), 1)

        vsep = QFrame()
        vsep.setObjectName("device_vsep")
        vsep.setFrameShape(QFrame.Shape.VLine)
        h.addWidget(vsep)

        h.addWidget(self._build_camera_pane(2), 1)

        self.tabs.addTab(tab, "Camera Views")

    def _build_camera_pane(self, device_id: int) -> QWidget:
        pane = QWidget()
        pane.setObjectName("central")
        v = QVBoxLayout(pane)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)

        # Label
        lbl = QLabel(f"  DEVICE {device_id}  —  CAMERA VIEW")
        lbl.setObjectName("cam_label")
        v.addWidget(lbl)

        # Web view or fallback
        cfg = self._config.device1 if device_id == 1 else self._config.device2
        if WEB_OK:
            browser = QWebEngineView()
            browser.setUrl(QUrl(
                f"http://{cfg.camera_ip}/api/dataman/images/image-viewer/"))
            v.addWidget(browser, 1)
            if device_id == 1:
                self._cam_browser_d1 = browser
            else:
                self._cam_browser_d2 = browser
        else:
            fallback = QLabel(
                f"PyQtWebEngine not installed.\n\n"
                f"Open manually:\n"
                f"http://{cfg.camera_ip}/api/dataman/images/image-viewer/")
            fallback.setAlignment(Qt.AlignmentFlag.AlignCenter)
            fallback.setStyleSheet(
                "color:#4a5260; font-size:12px; background:#eceef1;")
            v.addWidget(fallback, 1)

        # PASS/FAIL banner
        banner = QLabel("–")
        banner.setObjectName("cam_banner_idle")
        banner.setAlignment(Qt.AlignmentFlag.AlignCenter)
        v.addWidget(banner)

        if device_id == 1:
            self._cam_banner_d1 = banner
        else:
            self._cam_banner_d2 = banner

        return pane

    def _update_cam_banner(self, device_id: int, is_pass: bool):
        banner = self._cam_banner_d1 if device_id == 1 else self._cam_banner_d2
        if is_pass:
            banner.setText("✓   PASS")
            banner.setObjectName("cam_banner_pass")
        else:
            banner.setText("✕   FAIL")
            banner.setObjectName("cam_banner_fail")
        banner.style().unpolish(banner)
        banner.style().polish(banner)

    # ── visualization tab ─────────────────────────────────────────────────────

    def _build_viz_tab(self):
        tab = QWidget()
        tab.setObjectName("central")
        h = QHBoxLayout(tab)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(0)

        self._viz_d1 = self._build_viz_pane(1)
        self._viz_d2 = self._build_viz_pane(2)

        vsep = QFrame()
        vsep.setObjectName("device_vsep")
        vsep.setFrameShape(QFrame.Shape.VLine)

        h.addWidget(self._viz_d1["widget"], 1)
        h.addWidget(vsep)
        h.addWidget(self._viz_d2["widget"], 1)

        self.tabs.addTab(tab, "Visualization")

    def _build_viz_pane(self, device_id: int) -> dict:
        pane = QWidget()
        pane.setObjectName("central")
        v = QVBoxLayout(pane)
        v.setContentsMargins(12, 10, 12, 10)
        v.setSpacing(8)

        # Title
        title_lbl = QLabel(f"DEVICE {device_id}")
        title_lbl.setStyleSheet(
            "color:#0062a3; font-size:10px; font-weight:700; "
            "letter-spacing:1.2px; border-bottom:1px solid #d0d4da; "
            "padding-bottom:6px;")
        v.addWidget(title_lbl)

        # KPI cards row
        kpi_row = QHBoxLayout()
        kpi_row.setSpacing(8)

        refs = {}

        def viz_kpi(label, key, color):
            card = QWidget()
            card.setObjectName("viz_card")
            card.setStyleSheet("background:#ffffff;")
            cv = QVBoxLayout(card)
            cv.setContentsMargins(12, 8, 12, 8)
            cv.setSpacing(2)
            lbl = QLabel(label.upper())
            lbl.setStyleSheet(
                "color:#8a93a0; font-size:9px; font-weight:600; letter-spacing:1px;")
            val = QLabel("0")
            val.setStyleSheet(
                f"color:{color}; font-size:28px; font-weight:600;")
            sub = QLabel("")
            sub.setStyleSheet("color:#8a93a0; font-size:10px;")
            cv.addWidget(lbl)
            cv.addWidget(val)
            cv.addWidget(sub)
            refs[key]         = val
            refs[key + "_sub"] = sub
            return card

        kpi_row.addWidget(viz_kpi("Total",     "total", "#1a1e24"))
        kpi_row.addWidget(viz_kpi("Pass",      "pass",  "#1a7a3a"))
        kpi_row.addWidget(viz_kpi("Fail",      "fail",  "#c0392b"))
        kpi_row.addWidget(viz_kpi("Pass Rate", "rate",  "#0062a3"))
        v.addLayout(kpi_row)

        # Pie chart only
        if PG_OK:
            pie_card = QWidget()
            pie_card.setStyleSheet("background:#ffffff;")
            pc = QVBoxLayout(pie_card)
            pc.setContentsMargins(8, 8, 8, 8)
            pie_title = QLabel("PASS / FAIL DISTRIBUTION")
            pie_title.setStyleSheet(
                "color:#8a93a0; font-size:9px; font-weight:600; "
                "letter-spacing:1px; border-bottom:1px solid #d0d4da; "
                "padding-bottom:6px;")
            pc.addWidget(pie_title)
            pie_w = pg.PlotWidget(background="#ffffff")
            pie_w.setAspectLocked(True)
            pie_w.hideAxis("bottom")
            pie_w.hideAxis("left")
            pie_w.setMinimumHeight(180)
            pc.addWidget(pie_w, 1)
            refs["pie"] = pie_w
            v.addWidget(pie_card, 1)
        else:
            fallback = QLabel("Install pyqtgraph for charts:\npip install pyqtgraph")
            fallback.setAlignment(Qt.AlignmentFlag.AlignCenter)
            fallback.setStyleSheet(
                "color:#8a93a0; font-size:12px; background:#ffffff;")
            v.addWidget(fallback, 1)

        refs["widget"] = pane
        refs["pass_count"] = 0
        refs["fail_count"] = 0
        return refs

    def _refresh_viz(self, device_id: int):
        refs = self._viz_d1 if device_id == 1 else self._viz_d2
        p = refs["pass_count"]
        f = refs["fail_count"]
        total = p + f
        pct   = f"{p / total * 100:.1f}" if total else "0.0"
        fpct  = f"{f / total * 100:.1f}" if total else "0.0"

        refs["total"].setText(str(total))
        refs["pass"].setText(str(p))
        refs["fail"].setText(str(f))
        refs["rate"].setText(f"{pct}%")
        refs["pass_sub"].setText(f"{pct} %")
        refs["fail_sub"].setText(f"{fpct} %")

        if PG_OK:
            self._draw_pie(refs["pie"], p, f)

    def _draw_pie(self, pie_w, p: int, f: int):
        pie_w.clear()
        total = p + f
        if total == 0:
            circle = pg.QtWidgets.QGraphicsEllipseItem(-1, -1, 2, 2)
            circle.setBrush(QBrush(QColor("#eceef1")))
            circle.setPen(QPen(Qt.PenStyle.NoPen))
            pie_w.addItem(circle)
            return
        start = 90
        for angle, color in [
            ((p / total) * 360, "#1a7a3a"),
            (((f / total) * 360), "#c0392b"),
        ]:
            if angle <= 0:
                continue
            arc = pg.QtWidgets.QGraphicsEllipseItem(-1, -1, 2, 2)
            arc.setStartAngle(int(start * 16))
            arc.setSpanAngle(int(-angle * 16))
            arc.setBrush(QBrush(QColor(color)))
            arc.setPen(QPen(QColor("#ffffff"), 0.04))
            pie_w.addItem(arc)
            start -= angle
        hole = pg.QtWidgets.QGraphicsEllipseItem(-0.55, -0.55, 1.1, 1.1)
        hole.setBrush(QBrush(QColor("#ffffff")))
        hole.setPen(QPen(Qt.PenStyle.NoPen))
        pie_w.addItem(hole)
        pct = f"{p/total*100:.1f}%" if total else "–"
        txt = pg.TextItem(text=f"{pct}\nPASS", anchor=(0.5, 0.5), color="#1a1e24")
        txt.setFont(QFont("IBM Plex Sans", 9, QFont.Weight.Bold))
        txt.setPos(0, 0)
        pie_w.addItem(txt)


    def _build_batch_tab(self):
        tab = QWidget()
        tab.setObjectName("central")
        h = QHBoxLayout(tab)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(0)

        self._bi_d1 = self._build_batch_pane(1)
        self._bi_d2 = self._build_batch_pane(2)

        vsep = QFrame()
        vsep.setObjectName("device_vsep")
        vsep.setFrameShape(QFrame.Shape.VLine)

        h.addWidget(self._bi_d1["widget"], 1)
        h.addWidget(vsep)
        h.addWidget(self._bi_d2["widget"], 1)

        self.tabs.addTab(tab, "Batch Info")

    def _build_batch_pane(self, device_id: int) -> dict:
        pane = QWidget()
        pane.setObjectName("central")
        v = QVBoxLayout(pane)
        v.setContentsMargins(20, 16, 20, 16)
        v.setSpacing(8)

        title = QLabel(f"DEVICE {device_id}")
        title.setStyleSheet(
            "color:#0062a3; font-size:10px; font-weight:700; "
            "letter-spacing:1.2px; border-bottom:1px solid #d0d4da; "
            "padding-bottom:6px; margin-bottom:4px;")
        v.addWidget(title)

        refs = {"widget": pane}

        def info_card(label, key):
            card = QWidget()
            card.setStyleSheet("background:#ffffff;")
            cv = QVBoxLayout(card)
            cv.setContentsMargins(14, 8, 14, 8)
            lbl = QLabel(label.upper())
            lbl.setObjectName("bi_field_label")
            val = QLabel("–")
            val.setObjectName("bi_field_value")
            cv.addWidget(lbl)
            cv.addWidget(val)
            refs[key] = val
            return card

        v.addWidget(info_card("Batch ID",        "batch"))
        v.addWidget(info_card("Excel Log File",   "file"))
        v.addWidget(info_card("Logging Started",  "started"))
        v.addWidget(info_card("WAL File",         "wal"))
        v.addStretch()
        return refs

    # ── advanced settings tab ─────────────────────────────────────────────────

    def _build_adv_tab(self):
        self._adv_page = AdvancedSettingsPage(
            self._config, self, session_mgr=self._sm
        )
        self.tabs.addTab(self._adv_page, "Advanced Settings")
        # CFR21 Fix 10: use only currentChanged — avoids double-trigger
        # _on_tab_clicked handled inside _on_tab_changed now

    def _build_cfr21_tab(self):
        """CFR21: Audit Trail + User Management + File Integrity tab."""
        self._cfr21_tab = CFR21Tab(self._sm, parent=self, config=self._config)
        self.tabs.addTab(self._cfr21_tab, "CFR21 Compliance")
        self.tabs.currentChanged.connect(self._on_tab_changed)

    def _on_tab_changed(self, index: int):
        tab_name = self.tabs.tabText(index)
        # CFR21 Fix 10: handle Advanced Settings gate here (was tabBarClicked)
        if tab_name == "Advanced Settings":
            self._adv_page._unlock()
        elif tab_name == "CFR21 Compliance":
            self._cfr21_tab.refresh()
            if self._sm:
                self._sm.ping()

    # ── help tab ──────────────────────────────────────────────────────────────

    def _build_help_tab(self):
        from PyQt6.QtWidgets import QScroller
        tab = QScrollArea()
        tab.setWidgetResizable(True)
        QScroller.grabGesture(
            tab.viewport(),
            QScroller.ScrollerGestureType.TouchGesture
        )
        content = QWidget()
        content.setObjectName("central")
        v = QVBoxLayout(content)
        v.setContentsMargins(32, 28, 32, 40)
        v.setSpacing(0)

        S_TITLE = "color:#1a1e24; font-size:22px; font-weight:700; padding-bottom:2px;"
        S_SUBTITLE = "color:#6b7280; font-size:12px; padding-bottom:20px;"
        S_HEAD = ("color:#0062a3; font-size:10px; font-weight:700; "
                  "letter-spacing:1.2px; padding-top:24px; padding-bottom:6px;")
        S_SUBHEAD = ("color:#1a1e24; font-size:13px; font-weight:600; "
                     "padding-top:14px; padding-bottom:3px;")
        S_BODY = "color:#4a5260; font-size:12px; line-height:1.6; padding-bottom:4px;"
        S_NOTE = ("color:#92400e; background:#fffbeb; font-size:11px; "
                  "border-left:3px solid #f59e0b; padding:8px 12px; margin-top:6px;")
        S_WARN = ("color:#991b1b; background:#fef2f2; font-size:11px; "
                  "border-left:3px solid #ef4444; padding:8px 12px; margin-top:6px;")
        S_STEP = ("color:#1a1e24; font-size:12px; padding:3px 0px 3px 16px; "
                  "border-left:2px solid #d0d4da;")
        S_RULE = "background:#e5e7eb; max-height:1px; margin-top:10px; margin-bottom:4px;"

        def lbl(text, style, wrap=True):
            w = QLabel(text)
            w.setStyleSheet(style)
            w.setWordWrap(wrap)
            return w

        def rule():
            f = QFrame()
            f.setFrameShape(QFrame.Shape.HLine)
            f.setStyleSheet(S_RULE)
            return f

        # ── Header ────────────────────────────────────────────────────────────
        v.addWidget(lbl("Pharma Code Datalogger - Multi Device", S_TITLE))
        v.addWidget(lbl(f"Operator Manual  ·  {__company__} ", S_SUBTITLE))
        v.addWidget(rule())

        # ── 1. Overview ───────────────────────────────────────────────────────
        v.addWidget(lbl("1.  OVERVIEW", S_HEAD))
        v.addWidget(lbl(
            "This application connects to two Cognex DataMan 290 barcode scanners "
            "and reads the pharma code printed on each product as it passes under each camera. "
            "Every scan is compared against a master (reference) code set per device. "
            "If the code matches, the product is recorded as PASS. "
            "If it does not match, or if no code is read, it is recorded as FAIL. "
            "On FAIL, a signal is sent to the PLC which triggers the rejection mechanism on the line.",
            S_BODY))
        v.addWidget(lbl(
            "Both devices operate independently with their own master codes, counters, "
            "and log files. Each device logs to its own subfolder. "
            "Session details (Batch ID, Operator ID, Product Name) are shared across both devices.",
            S_BODY))

        # ── 2. Startup ────────────────────────────────────────────────────────
        v.addWidget(lbl("2.  STARTUP PROCEDURE", S_HEAD))
        v.addWidget(lbl(
            "Follow these steps every time you start the application or begin a new batch.",
            S_BODY))

        for step, text in [
            ("Step 1 — Session Setup",
             "When the application opens, the Session Setup window appears automatically. "
             "Select or type the Batch ID, Operator ID, and Product Name. "
             "Press Confirm. You can reopen this window at any time from the bottom bar."),
            ("Step 2 — Connect Cameras",
             "Click Connect on the Camera 1 header bar, then on the Camera 2 header bar. "
             "Each header turns green with a ● indicator when connected successfully. "
             "You can connect one or both cameras — logging works with either or both active."),
            ("Step 3 — Connect PLC",
             "Click Connect in the PLC chip in the topbar. "
             "The chip turns green when connected. "
             "PLC connection is optional — logging works without it but rejection signalling is disabled."),
            ("Step 4 — Start Logging",
             "Click Start Logging in the bottom bar. "
             "The SCANNING badge appears in the topbar. "
             "The Teach and Clear buttons become active on connected devices. "
             "Do not allow production products onto the line yet."),
            ("Step 5 — Set Master Codes",
             "Click Teach on Device 1's master bar. "
             "Present a known-good product to Camera 1 — the code is captured and shown. "
             "Repeat for Device 2. Each device has its own independent master code. "
             "This teach scan is not counted in the batch record."),
            ("Step 6 — Begin Production",
             "With master codes set on all active devices, allow production products onto the line. "
             "PASS products continue, FAIL products receive a reject signal from the PLC."),
        ]:
            v.addWidget(lbl(step, S_SUBHEAD))
            v.addWidget(lbl(text, S_STEP))

        v.addWidget(lbl(
            "⚠  Do not allow production products onto the line until master codes have been "
            "set on all active devices. Without a master, every scan is recorded as FAIL "
            "and every product receives a reject signal.",
            S_WARN))

        # ── 3. Live Monitoring ────────────────────────────────────────────────
        v.addWidget(lbl("3.  LIVE MONITORING TAB", S_HEAD))
        v.addWidget(lbl(
            "The main working screen during production. Both devices are shown side by side. "
            "Each device panel is independent.",
            S_BODY))

        v.addWidget(lbl("Session Info Strip", S_SUBHEAD))
        v.addWidget(lbl(
            "The strip above both device panels shows the current Batch ID, Operator ID, "
            "and Product Name. This is shared across both devices.",
            S_STEP))

        v.addWidget(lbl("Master Code Bar", S_SUBHEAD))
        v.addWidget(lbl(
            "Shows the master code currently set for that device. "
            "If it reads '– (not set)', teach has not been done for this device. "
            "Do not allow products to run until this shows a valid code.",
            S_STEP))

        v.addWidget(lbl("Pass / Fail Counters", S_SUBHEAD))
        v.addWidget(lbl(
            "Three counters show the running totals per device: "
            "Pass, Fail / No Read, and Total Reads with percentage rates. "
            "These counters reset to zero each time Start Logging is clicked.",
            S_STEP))

        v.addWidget(lbl("PASS / FAIL Indicator", S_SUBHEAD))
        v.addWidget(lbl(
            "The large coloured box on the right of each device panel shows the result "
            "of the most recent scan for that device. "
            "Green ✓ PASS — last product matched master. "
            "Red ✕ FAIL — last product did not match, or no code was read.",
            S_STEP))

        v.addWidget(lbl("Scan Table", S_SUBHEAD))
        v.addWidget(lbl(
            "Every scan is added to the table as it happens, most recent at the top. "
            "Each row shows scan number, time, barcode read, and result. "
            "All records are saved to log files regardless of what is visible in the table.",
            S_STEP))

        v.addWidget(lbl(
            "from receiving the barcode line to completing the PASS/FAIL decision. "
            "Typical values are sub-millisecond.",
            S_STEP))

        # ── 4. Teach Mode ─────────────────────────────────────────────────────
        v.addWidget(lbl("4.  TEACH MODE", S_HEAD))
        v.addWidget(lbl(
            "Each device has independent Teach and Clear buttons. "
            "Teach and Clear are only active when logging is running.",
            S_BODY))

        v.addWidget(lbl("How to set the master code", S_SUBHEAD))
        for text in [
            "Click Teach on the device's master bar.",
            "The master bar shows '⏳ Scan to teach…'",
            "Present a known-good, correctly labelled product to that camera.",
            "The barcode is read and set as the master code — it appears in the master bar.",
            "Repeat for the second device if both are active.",
        ]:
            v.addWidget(lbl(f"  •  {text}", S_STEP))

        v.addWidget(lbl("Clearing the master code", S_SUBHEAD))
        v.addWidget(lbl(
            "Click Clear to remove the current master for that device. "
            "All subsequent scans on that device will FAIL until re-taught. "
            "Clearing mid-batch should only be done if re-teaching immediately.",
            S_STEP))

        v.addWidget(lbl(
            "⚠  If the camera returns No Read during teach, the master is not set "
            "and the system remains armed. Check product positioning and try again.",
            S_WARN))
        v.addWidget(lbl(
            "ℹ  The master code is held in memory only. "
            "If the application is closed or the batch is stopped and restarted, "
            "you must re-teach the master before logging begins.",
            S_NOTE))

        # ── 5. Pass/Fail Logic ────────────────────────────────────────────────
        v.addWidget(lbl("5.  PASS / FAIL LOGIC", S_HEAD))
        v.addWidget(lbl(
            "The result of every scan is determined by a simple exact-match rule.",
            S_BODY))

        for condition, result in [
            ("Scanned code matches master code exactly", "PASS"),
            ("Scanned code does not match master code", "FAIL"),
            ("Camera could not read a code (No Read)", "FAIL"),
            ("No master code has been set", "FAIL"),
        ]:
            row = QWidget()
            rl = QHBoxLayout(row)
            rl.setContentsMargins(16, 2, 0, 2)
            rl.setSpacing(12)
            cond_lbl = QLabel(condition)
            cond_lbl.setStyleSheet("color:#4a5260; font-size:12px;")
            cond_lbl.setWordWrap(True)
            res_lbl = QLabel(result)
            res_color = "#1a7a3a" if result == "PASS" else "#c0392b"
            res_lbl.setStyleSheet(
                f"color:{res_color}; font-size:12px; font-weight:700; min-width:50px;")
            rl.addWidget(cond_lbl, 1)
            rl.addWidget(res_lbl)
            v.addWidget(row)

        v.addWidget(lbl("Consecutive Fail Alarm", S_SUBHEAD))
        v.addWidget(lbl(
            "If three consecutive scans on a device result in FAIL, an alarm dialog appears. "
            "Acknowledge the alarm and investigate the product, camera alignment, "
            "and master code before continuing.",
            S_STEP))

        # ── 6. PLC Rejection ──────────────────────────────────────────────────
        v.addWidget(lbl("6.  PLC REJECTION SIGNALLING", S_HEAD))
        v.addWidget(lbl(
            "When the PLC is connected, every FAIL scan triggers a pulse to the PLC "
            "which activates the rejection mechanism. Each device has its own register address. "
            "PASS scans do not send any signal.",
            S_BODY))
        v.addWidget(lbl(
            "The signal is a short pulse to a Modbus holding register: "
            "FAIL value written → 100ms wait → PASS value written (rising edge). "
            "Register addresses and values are configured in Advanced Settings.",
            S_BODY))
        v.addWidget(lbl(
            "ℹ  The PLC's own safety systems are the primary safety layer. "
            "This application provides the reject signal — the PLC decides what to do with it.",
            S_NOTE))

        # ── 7. Stop / End of Batch ────────────────────────────────────────────
        v.addWidget(lbl("7.  STOPPING AND END OF BATCH", S_HEAD))

        for step, text in [
            ("Step 1 — Stop the line",
             "Allow the last product to pass through all cameras before stopping."),
            ("Step 2 — Click Stop Logging",
             "Click Stop Logging in the bottom bar. "
             "The SCANNING badge disappears. "
             "Excel log files for both devices are finalised and saved."),
            ("Step 3 — Verify log files",
             "Click Open Logs Folder to verify the Excel files have been created "
             "in logs/Device1/ and logs/Device2/."),
            ("Step 4 — Disconnect devices",
             "Click Disconnect on each camera header and the PLC chip when finished. "
             "Cameras and PLC cannot be disconnected while logging is active."),
        ]:
            v.addWidget(lbl(step, S_SUBHEAD))
            v.addWidget(lbl(text, S_STEP))

        # ── 8. Log Files ──────────────────────────────────────────────────────
        v.addWidget(lbl("8.  LOG FILES", S_HEAD))
        v.addWidget(lbl(
            "Each device saves two files per batch in its own subfolder.",
            S_BODY))

        v.addWidget(lbl("Excel Log File (.xlsx)", S_SUBHEAD))
        v.addWidget(lbl(
            "The main production record. One row per scan with scan number, time, "
            "barcode read, and PASS/FAIL result. Also records Batch ID, Operator ID, "
            "Product Name, and the start time. "
            "Updated every 5 scans and when Stop Logging is clicked.",
            S_STEP))

        v.addWidget(lbl("WAL Backup File (.csv)", S_SUBHEAD))
        v.addWidget(lbl(
            "A backup CSV written to the /wal subfolder on every scan immediately. "
            "If the application crashes mid-batch, no data is lost — "
            "the CSV contains the complete record and is used to rebuild the Excel on next start.",
            S_STEP))

        v.addWidget(lbl(
            "ℹ  File naming format: ProductionLog_<BatchID>_<YYYYMMDD>_<HHMMSS>.xlsx  "
            "stored in logs/Device1/ and logs/Device2/",
            S_NOTE))

        # ── 9. Visualization ──────────────────────────────────────────────────
        v.addWidget(lbl("9.  VISUALIZATION TAB", S_HEAD))
        v.addWidget(lbl(
            "Shows live charts and summary statistics for both devices in the current batch. "
            "Four KPI cards show Total, Pass, Fail, and Pass Rate per device. "
            "A pie chart shows the pass/fail split. "
            "All values reset when a new batch is started.",
            S_BODY))

        # ── 10. Batch Info ────────────────────────────────────────────────────
        v.addWidget(lbl("10.  BATCH INFO TAB", S_HEAD))
        v.addWidget(lbl(
            "Shows the active batch details per device: Batch ID, Excel log file path, "
            "logging start time, and WAL backup file path. "
            "Use this tab to confirm logging is writing to the correct files and folder.",
            S_BODY))

        # ── 11. Camera Disconnect ─────────────────────────────────────────────
        v.addWidget(lbl("11.  CAMERA DISCONNECT DURING LOGGING", S_HEAD))
        v.addWidget(lbl(
            "If a camera connection is lost while logging is active, "
            "an alarm dialog appears immediately. "
            "Logging is suspended for that device — no scans are recorded during the disconnect.",
            S_BODY))
        v.addWidget(lbl(
            "⚠  Products that pass in front of the camera while it is disconnected "
            "are NOT scanned, NOT recorded, and do NOT receive a reject signal. "
            "Check the gap in timestamps in the log file to determine how many products "
            "may have been missed.",
            S_WARN))

        v.addWidget(lbl("Recovery steps:", S_SUBHEAD))
        for text in [
            "Check the camera cable and network connection.",
            "Click Stop Logging to close and save the current batch.",
            "Click Connect on the affected device's header bar to reconnect.",
            "Assess whether the batch is valid given the gap in scanning.",
            "If continuing, click Start Logging to begin a new session.",
        ]:
            v.addWidget(lbl(f"  {text}", S_STEP))

        # ── 12. Advanced Settings ─────────────────────────────────────────────
        v.addWidget(lbl("12.  ADVANCED SETTINGS", S_HEAD))
        v.addWidget(lbl(
            "Password-protected technician settings. Default password: admin@123. "
            "Do not change these settings during active logging or regular operations. "
            "Changes apply on the next Start Logging.",
            S_BODY))

        for setting, desc in [
            ("Camera IP / Port",
             "Network address of each Cognex DataMan camera. Default: Port 23."),
            ("Read Timeout (s)",
             "How long the app waits for a scan before cycling. "
             "Recommended: 2.0 seconds."),
            ("PLC IP / Port",
             "Network address of the PLC for Modbus/TCP. Default: Port 502."),
            ("Register — Device 1 / Device 2",
             "Modbus holding register addresses for each device's reject signal. "
             "Must match the PLC program configuration."),
            ("PASS Value / FAIL Value",
             "Numeric values written to the register on PASS and FAIL. "
             "Default: 0 for PASS, 1 for FAIL."),
            ("Save Logs To",
             "Folder where Excel and WAL files are saved. "
             "Ensure the drive has sufficient free space for long production runs."),
            ("New Password",
             "Change the technician password for Advanced Settings access."),
            ("Reset Lists",
             "Clears all saved Batch ID, Operator ID, and Product Name dropdown entries."),
        ]:
            v.addWidget(lbl(setting, S_SUBHEAD))
            v.addWidget(lbl(desc, S_STEP))

        # ── 13. Troubleshooting ───────────────────────────────────────────────
        v.addWidget(lbl("13.  TROUBLESHOOTING", S_HEAD))

        for problem, solution in [
            ("Camera will not connect",
             "Check the network cable. Verify the camera IP in Advanced Settings. "
             "The DataMan 290 only allows one TCP connection at a time — "
             "ensure no other application (e.g. DataMan Setup Tool) is connected."),
            ("Start Logging is greyed out",
             "At least one camera must be connected before logging can start. "
             "Click Connect on a device header first."),
            ("Every scan shows FAIL",
             "The master code has not been set, or was cleared. "
             "Click Teach and scan a known-good product to set the master."),
            ("No Read on every scan",
             "Check that products are correctly positioned under the camera. "
             "Check the camera lens for dirt or obstruction. "
             "Verify the camera reading mode matches the barcode type on the product."),
            ("PLC connected but rejection not working",
             "Verify Register Address and FAIL Value in Advanced Settings match "
             "the PLC program. Contact the PLC technician to confirm register mapping."),
            ("Log files not being created",
             "Check the Log Folder path in Advanced Settings. "
             "Ensure the application has write permission to that folder "
             "and that the drive is not full."),
            ("Camera Disconnected alarm during logging",
             "Follow the recovery steps in Section 11. "
             "Do not ignore this alarm — products may have passed unscanned."),
            ("Consecutive Fail alarm appeared",
             "Three or more consecutive products failed on one device. "
             "Stop the line, check the products in the rejection area, "
             "and verify the master code is correct before resuming."),
        ]:
            v.addWidget(lbl(problem, S_SUBHEAD))
            v.addWidget(lbl(solution, S_STEP))

        # ── Support ───────────────────────────────────────────────────────────
        v.addWidget(lbl("SUPPORT", S_HEAD))
        v.addWidget(lbl(
            "For technical support and configuration changes, "
            "contact support. "
            "Please share the crash_log.txt file (located next to the exe) "
            "if reporting a crash or unexpected error.",
            S_BODY))

        # ── Footer ────────────────────────────────────────────────────────────
        v.addSpacing(32)
        v.addWidget(rule())
        v.addWidget(lbl(
            f"{__company__}  ·  {__app_name__}  ·  v{__version__}",
            "color:#9ca3af; font-size:10px; padding-top:10px;"))

        v.addStretch()
        tab.setWidget(content)
        self.tabs.addTab(tab, "Help")

    # ══════════════════════════════════════════════════════════════════════════
    #  SIGNAL WIRING
    # ══════════════════════════════════════════════════════════════════════════

    def _wire_controller_callbacks(self):
        """Wire AppController datalogger callbacks → pyqtSignals."""
        for device_id in [1, 2]:
            lg = self._controller.logger(device_id)
            sig_read   = self.sig_read_logged_d1   if device_id == 1 \
                         else self.sig_read_logged_d2
            sig_live   = self.sig_live_sample_d1   if device_id == 1 \
                         else self.sig_live_sample_d2
            sig_teach  = self.sig_teach_done_d1    if device_id == 1 \
                         else self.sig_teach_done_d2
            sig_consec = self.sig_consec_fail_d1   if device_id == 1 \
                         else self.sig_consec_fail_d2
            sig_disc   = self.sig_cam_disconnect_d1 if device_id == 1 \
                         else self.sig_cam_disconnect_d2
            sig_status = self.sig_status_d1 if device_id == 1 \
                         else self.sig_status_d2
            sig_wal    = self.sig_wal_error_d1 if device_id == 1 \
                         else self.sig_wal_error_d2
            sig_plc    = self.sig_plc_error_d1 if device_id == 1 \
                         else self.sig_plc_error_d2

            lg.on_read_logged        = sig_read.emit
            lg.on_live_sample        = sig_live.emit
            lg.on_teach_done         = sig_teach.emit
            lg.on_consec_fail        = sig_consec.emit
            lg.on_camera_disconnect  = sig_disc.emit
            lg.on_status             = sig_status.emit
            lg.on_wal_error          = sig_wal.emit
            lg.on_plc_error          = sig_plc.emit

    def _wire_panel_signals(self):
        """Wire DevicePanel outgoing signals → controller actions."""
        self.panel_d1.sig_connect_requested.connect(self._on_connect_requested)
        self.panel_d2.sig_connect_requested.connect(self._on_connect_requested)
        self.panel_d1.sig_disconnect_requested.connect(self._on_disconnect_requested)
        self.panel_d2.sig_disconnect_requested.connect(self._on_disconnect_requested)
        self.panel_d1.sig_arm_teach.connect(self._on_arm_teach)
        self.panel_d2.sig_arm_teach.connect(self._on_arm_teach)
        self.panel_d1.sig_clear_master.connect(self._on_clear_master)
        self.panel_d2.sig_clear_master.connect(self._on_clear_master)

        # Wire controller → panel (read logged, teach done, etc.)
        self.sig_read_logged_d1.connect(self.panel_d1.sig_read_logged)
        self.sig_read_logged_d2.connect(self.panel_d2.sig_read_logged)
        self.sig_teach_done_d1.connect(self.panel_d1.sig_teach_done)
        self.sig_teach_done_d2.connect(self.panel_d2.sig_teach_done)
        self.sig_consec_fail_d1.connect(self.panel_d1.sig_consec_fail)
        self.sig_consec_fail_d2.connect(self.panel_d2.sig_consec_fail)
        self.sig_cam_disconnect_d1.connect(self.panel_d1.sig_camera_disconnect)
        self.sig_cam_disconnect_d2.connect(self.panel_d2.sig_camera_disconnect)

        # CFR21 Fix 2&5: audit actual master code capture (fires only on confirmed read)
        self.sig_teach_done_d1.connect(
            lambda code: self._on_teach_captured(1, code))
        self.sig_teach_done_d2.connect(
            lambda code: self._on_teach_captured(2, code))

        # CFR21: audit camera lost and consecutive fail
        self.sig_cam_disconnect_d1.connect(
            lambda: self._on_camera_lost(1))
        self.sig_cam_disconnect_d2.connect(
            lambda: self._on_camera_lost(2))
        self.sig_consec_fail_d1.connect(
            lambda count: self._on_consec_fail_audit(1, count))
        self.sig_consec_fail_d2.connect(
            lambda count: self._on_consec_fail_audit(2, count))

        # Item 3: WAL write failure — alarm + stop
        self.sig_wal_error_d1.connect(
            lambda msg: self._on_wal_error(1, msg))
        self.sig_wal_error_d2.connect(
            lambda msg: self._on_wal_error(2, msg))

        # Item 4: PLC reject write failure — alarm
        self.sig_plc_error_d1.connect(
            lambda count: self._on_plc_error(1, count))
        self.sig_plc_error_d2.connect(
            lambda count: self._on_plc_error(2, count))

        # Live sample → latency + camera banner
        self.sig_live_sample_d1.connect(
            lambda raw, ok, lat: self._on_live_sample(1, raw, ok, lat))
        self.sig_live_sample_d2.connect(
            lambda raw, ok, lat: self._on_live_sample(2, raw, ok, lat))

        # Read logged → visualization counters
        self.sig_read_logged_d1.connect(
            lambda rec: self._update_viz_on_read(1, rec.status == "PASS"))
        self.sig_read_logged_d2.connect(
            lambda rec: self._update_viz_on_read(2, rec.status == "PASS"))

        # Status → batch info
        self.sig_status_d1.connect(lambda msg: self._on_status(1, msg))
        self.sig_status_d2.connect(lambda msg: self._on_status(2, msg))

    def _connect_signals(self):
        self.btn_start.clicked.connect(self.start_logging)
        self.btn_stop.clicked.connect(self.stop_logging)
        self.btn_logs.clicked.connect(self.open_logs)
        self._adv_page.settings_saved.connect(self._on_settings_saved)
        self._adv_page.session_lists_reset.connect(self._on_session_lists_reset)

    # ══════════════════════════════════════════════════════════════════════════
    #  DEVICE CONNECT / DISCONNECT
    # ══════════════════════════════════════════════════════════════════════════

    def _on_connect_requested(self, device_id: int):
        if self._controller.is_running():
            QMessageBox.warning(self, "Logging Active",
                                "Stop logging before connecting a camera.")
            return

        try:
            self._controller.connect_camera(device_id)
        except Exception as e:
            QMessageBox.critical(self, f"Camera {device_id} Failed",
                                 f"Could not connect:\n{e}")
            return

        # License check
        try:
            ok, result = license_check.verify_socket(
                self._controller.camera_socket(device_id))
            if not ok:
                self._controller.disconnect_camera(device_id)
                QMessageBox.critical(
                    self, "Unlicensed Device",
                    f"Camera {device_id} is not authorised.\n\n{result}\n\n"
                    "Contact Machin Tek Engineers to add this device.")
                return
        except Exception as e:
            self._controller.disconnect_camera(device_id)
            QMessageBox.critical(self, "License Check Failed",
                                 f"Could not verify device licence:\n{e}")
            return

        panel = self.panel_d1 if device_id == 1 else self.panel_d2
        panel.set_connected(True)
        self.btn_start.setEnabled(
            self._sm.can("start_logging") if self._sm else True
        )
        # Update PLC camera status register
        self._controller.set_camera_connected(device_id, True)

        # CFR21: audit camera connect (Fix 3)
        if self._sm:
            cfg = self._config.device1 if device_id == 1 else self._config.device2
            audit.log(
                user       = self._sm.current_user,
                action     = audit.ACTION_CAMERA_CONNECTED,
                detail     = (f"Camera Device {device_id} connected "
                              f"({cfg.camera_ip}:{cfg.camera_port})."),
                session_id = self._sm.session_id,
            )
            self._sm.ping()

    def _on_disconnect_requested(self, device_id: int):
        if self._controller.is_running():
            # CFR21: require reason when disconnecting camera during active batch
            dlg = ReasonDialog(
                title  = f"Disconnect Camera — Device {device_id}",
                prompt = (
                    f"Logging is active. Disconnecting Camera Device {device_id} "
                    f"will halt inspection for that device.\n\n"
                    f"State the reason for disconnecting during an active batch:"
                ),
                parent = self,
            )
            if dlg.exec() != QDialog.DialogCode.Accepted:
                return
            self.stop_logging()
            if self._sm:
                audit.log(
                    user       = self._sm.current_user,
                    action     = audit.ACTION_CAMERA_DISCONNECTED,
                    detail     = f"Camera Device {device_id} disconnected DURING ACTIVE BATCH. Logging stopped.",
                    session_id = self._sm.session_id,
                    reason     = dlg.reason,
                )
                self._sm.ping()
            try:
                self._controller.disconnect_camera(device_id)
            except RuntimeError as e:
                QMessageBox.warning(self, "Warning", str(e))
                return
            panel = self.panel_d1 if device_id == 1 else self.panel_d2
            panel.set_connected(False)
            self._controller.set_camera_connected(device_id, False)
            if not (self._controller.camera_is_open(1) or self._controller.camera_is_open(2)):
                self.btn_start.setEnabled(False)
            return

        try:
            self._controller.disconnect_camera(device_id)
        except RuntimeError as e:
            QMessageBox.warning(self, "Warning", str(e))
            return

        panel = self.panel_d1 if device_id == 1 else self.panel_d2
        panel.set_connected(False)
        self._controller.set_camera_connected(device_id, False)
        if not (self._controller.camera_is_open(1) or
                self._controller.camera_is_open(2)):
            self.btn_start.setEnabled(False)

        # CFR21: audit camera disconnect (Fix 3)
        if self._sm:
            audit.log(
                user       = self._sm.current_user,
                action     = audit.ACTION_CAMERA_DISCONNECTED,
                detail     = f"Camera Device {device_id} disconnected by user.",
                session_id = self._sm.session_id,
            )
            self._sm.ping()

    def connect_plc(self):
        try:
            self._controller.connect_plc()
            plc = self._config.plc
            self._update_plc_chip(f"{plc.ip}:{plc.port}", True)
            # CFR21: audit PLC connect (Fix 3)
            if self._sm:
                audit.log(
                    user       = self._sm.current_user,
                    action     = audit.ACTION_PLC_CONNECTED,
                    detail     = f"PLC connected ({plc.ip}:{plc.port}).",
                    session_id = self._sm.session_id,
                )
                self._sm.ping()
        except Exception as e:
            QMessageBox.critical(self, "PLC Failed",
                                 f"Modbus/TCP connect failed:\n{e}")

    def disconnect_plc(self):
        reason = None
        if self._controller.is_running():
            # CFR21: require reason when disconnecting PLC during active batch
            dlg = ReasonDialog(
                title  = "Disconnect PLC",
                prompt = (
                    "Logging is active. Disconnecting the PLC disables the reject signal —\n"
                    "bad products will NOT be physically ejected.\n\n"
                    "State the reason for disconnecting during an active batch:"
                ),
                parent = self,
            )
            if dlg.exec() != QDialog.DialogCode.Accepted:
                return
            reason = dlg.reason
        try:
            self._controller.disconnect_plc()
            self._update_plc_chip(
                f"{self._config.plc.ip}:{self._config.plc.port}", False)
            if self._sm:
                detail = (
                    "PLC disconnected DURING ACTIVE BATCH — reject signal disabled."
                    if reason else "PLC disconnected by user."
                )
                audit.log(
                    user       = self._sm.current_user,
                    action     = audit.ACTION_PLC_DISCONNECTED,
                    detail     = detail,
                    session_id = self._sm.session_id,
                    reason     = reason,
                )
                self._sm.ping()
        except RuntimeError as e:
            QMessageBox.warning(self, "Warning", str(e))

    # ══════════════════════════════════════════════════════════════════════════
    #  TEACH  — implementation is in CFR21 section below (with audit logging)
    # ══════════════════════════════════════════════════════════════════════════

    # ══════════════════════════════════════════════════════════════════════════
    #  SESSION
    # ══════════════════════════════════════════════════════════════════════════

    def _update_session_strip(self):
        self.lbl_session_batch.setText(self._session.batch_id or "–")
        self.lbl_session_operator.setText(self._session.operator_id or "–")
        self.lbl_session_product.setText(self._session.product_name or "–")

    def open_session_setup(self):
        # CFR21 Fix 5: operator_id is always the logged-in username — not editable
        # Pre-fill and lock the operator combo to the current user
        logged_in_username = (
            self._sm.current_user.username
            if self._sm and self._sm.current_user
            else ""
        )

        dlg = SessionSetupDialog(
            self,
            batch_ids     = self.batch_ids,
            operator_ids  = self.operator_ids,
            product_names = self.product_names,
        )

        # Pre-fill current session values so reopening preserves them
        if self._session.batch_id:
            dlg.batch_edit.setText(self._session.batch_id)
        if self._session.product_name:
            dlg.product_edit.setText(self._session.product_name)

        # CFR21 Fix 5: lock operator field to logged-in user
        if logged_in_username:
            dlg.operator_combo.setCurrentText(logged_in_username)
            dlg.operator_combo.setEnabled(False)

        if dlg.exec() == dlg.DialogCode.Accepted:
            self._session = dlg.values()
            # CFR21 Fix 5: always override operator_id with logged-in user
            if logged_in_username:
                self._session.operator_id = logged_in_username
            self._controller.set_session(self._session)
            self._update_session_strip()

            # CFR21 Fix 7: log session setup to audit trail
            if self._sm:
                audit.log(
                    user       = self._sm.current_user,
                    action     = audit.ACTION_SESSION_SETUP,
                    detail     = (
                        f"Session configured — Batch: '{self._session.batch_id}', "
                        f"Product: '{self._session.product_name}', "
                        f"Operator: '{self._session.operator_id}'."
                    ),
                    session_id = self._sm.session_id,
                )
                self._sm.ping()

    # ══════════════════════════════════════════════════════════════════════════
    #  LOGGING
    # ══════════════════════════════════════════════════════════════════════════

    def start_logging(self):
        if self._controller.is_running():
            return

        # CFR21 Fix 6: check role permission before allowing start
        if self._sm and not self._sm.can("start_logging"):
            QMessageBox.warning(
                self, "Permission Denied",
                f"Your role ({self._sm.current_user.role_display}) "
                "does not have permission to start logging."
            )
            return

        if not self._session.is_valid():
            QMessageBox.warning(self, "No Session",
                                "Open Session Setup and enter a Batch ID first.")
            self.open_session_setup()
            return

        # Interrupted batches are never silently resumed from compatibility
        # files.  Recovery requires a permitted authenticated user and a reason;
        # the controller records reconciliation evidence before acquisition.
        pending = [item for item in self._controller.pending_reconciliations()
                   if item["external_batch_id"] == self._session.batch_id]
        recovered = False
        if pending:
            if not self._sm or not self._sm.can("recover_batches"):
                QMessageBox.warning(self, "Recovery Authorization Required",
                                    "This batch was interrupted and must be reconciled by an Administrator or Supervisor.")
                return
            reason_dlg = ReasonDialog("Recover Interrupted Batch",
                                      "State the reason for resuming this interrupted batch.", self)
            if reason_dlg.exec() != ReasonDialog.DialogCode.Accepted:
                return
            try:
                self._controller.recover_interrupted_batch(reason_dlg.reason)
                recovered = True
            except Exception as exc:
                QMessageBox.critical(self, "Recovery Blocked", str(exc))
                return

        for panel in [self.panel_d1, self.panel_d2]:
            panel.reset_counters()
            panel.set_logging_active(panel._connected)

        self._update_session_strip()

        # ── Counter restore — check for existing WAL from previous run ────────
        wal_counts = self._controller.get_wal_counts_for_batch(
            self._session.batch_id
        )
        restored = False
        for device_id, (passes, fails) in wal_counts.items():
            if passes > 0 or fails > 0:
                panel = self.panel_d1 if device_id == 1 else self.panel_d2
                panel.restore_counters(passes, fails)
                # Sync viz counters too
                viz = self._viz_d1 if device_id == 1 else self._viz_d2
                viz["pass_count"] = passes
                viz["fail_count"] = fails
                self._refresh_viz(device_id)
                restored = True

        if restored:
            total_p = sum(v[0] for v in wal_counts.values())
            total_f = sum(v[1] for v in wal_counts.values())
            QMessageBox.information(
                self, "Counters Restored",
                f"Existing records found for Batch '{self._session.batch_id}'.\n\n"
                f"Counters restored from previous session:\n"
                f"  PASS: {total_p}\n"
                f"  FAIL: {total_f}\n\n"
                f"Logging will continue from where it left off."
            )
        else:
            # Fresh batch — reset viz counters to zero
            for refs in [self._viz_d1, self._viz_d2]:
                refs["pass_count"] = 0
                refs["fail_count"] = 0
            self._refresh_viz(1)
            self._refresh_viz(2)

        if not recovered:
            try:
                if not self._prepare_controlled_batch():
                    return
                self._controller.start_logging()
            except Exception as exc:
                QMessageBox.critical(self, "Logging Not Started", str(exc))
                return

        # CFR21: audit log batch start
        if self._sm:
            audit.log(
                user       = self._sm.current_user,
                action     = audit.ACTION_BATCH_STARTED,
                detail     = (
                    f"Batch '{self._session.batch_id}' started. "
                    f"Product: {self._session.product_name}. "
                    f"Operator: {self._session.operator_id}."
                ),
                session_id = self._sm.session_id,
            )
            self._sm.ping()

        self.btn_start.setEnabled(False)
        self.btn_stop.setEnabled(True)
        self.lbl_logging_badge.show()

    def _prepare_controlled_batch(self) -> bool:
        """Collect approved setup records before invoking the guarded start path."""
        conn = get_conn()
        try:
            configurations = conn.execute("""
                SELECT id, version_number FROM configuration_versions
                WHERE approval_status = 'approved' ORDER BY effective_at DESC
            """).fetchall()
            recipes = conn.execute("""
                SELECT id, version_number FROM recipe_versions
                WHERE approval_status = 'approved' ORDER BY effective_at DESC
            """).fetchall()
            devices = []
            for device_number in (1, 2):
                logger = self._controller.logger(device_number)
                row = conn.execute("""
                    SELECT id FROM devices WHERE device_number = ? AND source_identifier = ?
                      AND approval_status = 'approved' AND enabled = 1
                """, (device_number, logger.regulated_device_source)).fetchone()
                if row is None:
                    QMessageBox.warning(
                        self, "Device Setup Required",
                        f"No approved device matches configured Device {device_number}.")
                    return False
                devices.append(row["id"])
        finally:
            conn.close()
        if not configurations or not recipes:
            QMessageBox.warning(self, "Controlled Setup Required",
                                "An approved configuration and recipe are required.")
            return False
        config_labels = [f"v{row['version_number']}  {row['id']}" for row in configurations]
        config_label, ok = QInputDialog.getItem(self, "Configuration Version",
                                                 "Approved configuration", config_labels, 0, False)
        if not ok:
            return False
        recipe_labels = [f"v{row['version_number']}  {row['id']}" for row in recipes]
        recipe_label, ok = QInputDialog.getItem(self, "Recipe Version",
                                                 "Approved recipe", recipe_labels, 0, False)
        if not ok:
            return False
        reason = ReasonDialog("Controlled Batch Setup", "State the batch setup reason.", self)
        if reason.exec() != ReasonDialog.DialogCode.Accepted:
            return False
        configuration_id = configurations[config_labels.index(config_label)]["id"]
        recipe_id = recipes[recipe_labels.index(recipe_label)]["id"]
        self._controller.prepare_controlled_batch(
            configuration_id, recipe_id, devices, reason.reason)
        return True

    def stop_logging(self):
        if not self._controller.is_running():
            return

        # Stop scan threads — WAL is flushed and closed inside stop_logging()
        self._controller.stop_logging()
        for panel in [self.panel_d1, self.panel_d2]:
            panel.set_logging_active(False)

        # CFR21: audit batch stop
        if self._sm:
            audit.log(
                user       = self._sm.current_user,
                action     = audit.ACTION_BATCH_STOPPED,
                detail     = f"Batch '{self._session.batch_id}' stopped.",
                session_id = self._sm.session_id,
            )
            self._sm.ping()

        # ── Progress dialog while Excel is built and files are sealed ─────────
        from PyQt6.QtWidgets import QProgressDialog
        from PyQt6.QtCore import QThread, pyqtSignal as _Signal

        class _CloseBatchWorker(QThread):
            progress = _Signal(int, int)   # current, total
            finished = _Signal()

            def __init__(self, controller):
                super().__init__()
                self._ctrl = controller

            def run(self):
                def _cb(current, total):
                    self.progress.emit(current, total)
                self._ctrl.close_batch(progress_callback=_cb)
                self.finished.emit()

        prog = QProgressDialog(
            "Building Excel report and sealing files…",
            None,   # no cancel button — must complete
            0, 100,
            self,
        )
        prog.setWindowTitle("Closing Batch")
        prog.setWindowModality(Qt.WindowModality.WindowModal)
        prog.setMinimumDuration(0)
        prog.setAutoClose(True)
        prog.setValue(0)

        worker = _CloseBatchWorker(self._controller)

        def _on_progress(current, total):
            if total > 0:
                prog.setValue(int(current / total * 100))

        def _on_finished():
            prog.setValue(100)
            self.btn_start.setEnabled(True)
            self.btn_stop.setEnabled(False)
            self.lbl_logging_badge.hide()

        worker.progress.connect(_on_progress)
        worker.finished.connect(_on_finished)
        worker.start()
        prog.exec()   # blocks GUI thread until finished signal closes it

    # ══════════════════════════════════════════════════════════════════════════
    #  CALLBACKS FROM CONTROLLER
    # ══════════════════════════════════════════════════════════════════════════

    def _on_live_sample(self, device_id: int, raw, is_pass: bool, latency):
        self._update_cam_banner(device_id, is_pass)

    def _on_status(self, device_id: int, msg: str):
        refs = self._bi_d1 if device_id == 1 else self._bi_d2
        if "Excel:" in msg:
            parts = msg.split("Excel:")
            if len(parts) > 1:
                refs["file"].setText(parts[1].strip().split(" |")[0])
        if "WAL:" in msg:
            parts = msg.split("WAL:")
            if len(parts) > 1:
                refs["wal"].setText(parts[1].strip())
        if "Batch started" in msg:
            lg = self._controller.logger(device_id)
            if lg.batch_started:
                refs["batch"].setText(self._session.batch_id)
                refs["started"].setText(
                    lg.batch_started.strftime("%Y-%m-%d %H:%M:%S"))

        # Also update viz on every read
        if "read_id" in msg.lower():
            pass    # viz updated via sig_read_logged

    def _on_settings_saved(self, new_config: AppConfig):
        # CFR21: audit exactly what changed before applying (§11.10e)
        if self._sm:
            old = self._config
            changes = []
            checks = [
                (old.device1.camera_ip,  new_config.device1.camera_ip,  "Device1 Camera IP"),
                (old.device2.camera_ip,  new_config.device2.camera_ip,  "Device2 Camera IP"),
                (old.plc.ip,             new_config.plc.ip,             "PLC IP"),
                (old.plc.hreg_device1,   new_config.plc.hreg_device1,   "PLC Reg D1"),
                (old.plc.hreg_device2,   new_config.plc.hreg_device2,   "PLC Reg D2"),
                (old.plc.fail_val,       new_config.plc.fail_val,       "PLC Fail Value"),
                (old.plc.d1_reject_hreg,  new_config.plc.d1_reject_hreg,  "D1 Reject Hreg"),
                (old.plc.d1_reject_val,   new_config.plc.d1_reject_val,   "D1 Reject Val"),
                (old.plc.d1_trigger_hreg, new_config.plc.d1_trigger_hreg, "D1 Trigger Hreg"),
                (old.plc.d1_trigger_val,  new_config.plc.d1_trigger_val,  "D1 Trigger Val"),
                (old.plc.cyl_timing_hreg, new_config.plc.cyl_timing_hreg, "Cyl Timing Hreg"),
                (old.plc.cyl_timing_val,  new_config.plc.cyl_timing_val,  "Cyl Timing Val"),
                (old.plc.cam1_status_hreg,new_config.plc.cam1_status_hreg,"Cam1 Status Hreg"),
                (old.plc.spare1_hreg,    new_config.plc.spare1_hreg,    "Spare1 Hreg"),
                (old.plc.spare2_hreg,    new_config.plc.spare2_hreg,    "Spare2 Hreg"),
                (old.plc.spare3_hreg,    new_config.plc.spare3_hreg,    "Spare3 Hreg"),
                (old.plc.spare4_hreg,    new_config.plc.spare4_hreg,    "Spare4 Hreg"),
                (old.general.log_dir,    new_config.general.log_dir,    "Log Directory"),
                (old.general.consecutive_fail_limit, new_config.general.consecutive_fail_limit, "Consec Fail Limit"),
                (old.general.backup_destination,     new_config.general.backup_destination,     "Backup Destination"),
                (old.policy.timeout_minutes,         new_config.policy.timeout_minutes,         "Lock Timeout (min)"),
                (old.policy.password_expiry_days,    new_config.policy.password_expiry_days,    "PW Expiry (days)"),
                (old.policy.max_login_attempts,      new_config.policy.max_login_attempts,      "Max Login Attempts"),
                (old.policy.lockout_minutes,         new_config.policy.lockout_minutes,         "Lockout Duration (min)"),
                (old.policy.password_history_count,  new_config.policy.password_history_count,  "PW History Count"),
                (old.company.name,       new_config.company.name,       "Company Name"),
                (old.company.address,    new_config.company.address,    "Company Address"),
            ]
            for old_val, new_val, field in checks:
                if old_val != new_val:
                    changes.append(f"{field}: {old_val!r} → {new_val!r}")
            detail = ("Settings saved. Changes: " + "; ".join(changes)
                      if changes else "Settings saved. No values changed.")
            audit.log(
                user       = self._sm.current_user,
                action     = audit.ACTION_SETTINGS_CHANGED,
                detail     = detail,
                session_id = self._sm.session_id,
            )
            self._sm.ping()
        self._config = new_config
        self._controller.apply_new_config(new_config)
        self._refresh_chips()

    def _on_session_lists_reset(self):
        """Clear in-memory session lists after reset from Advanced Settings."""
        self.batch_ids.clear()
        self.operator_ids.clear()
        self.product_names.clear()

    def _refresh_chips(self):
        plc_conn = self._controller.plc_is_open()
        self._update_plc_chip(
            f"{self._config.plc.ip}:{self._config.plc.port}", plc_conn)
        self.panel_d1.update_camera_ip(self._config.device1.camera_ip)
        self.panel_d2.update_camera_ip(self._config.device2.camera_ip)

    # ══════════════════════════════════════════════════════════════════════════
    #  MISC
    # ══════════════════════════════════════════════════════════════════════════

    def open_logs(self):
        log_dir = self._config.general.log_dir
        os.makedirs(log_dir, exist_ok=True)
        if sys.platform == "win32":
            os.startfile(os.path.abspath(log_dir))
        else:
            subprocess.Popen(["xdg-open", os.path.abspath(log_dir)])

    def toggle_fullscreen(self):
        if self.isFullScreen():
            self.showNormal()
            self.btn_fullscreen.setText("⛶")
        else:
            self.showFullScreen()
            self.btn_fullscreen.setText("⊠")

    def _update_date(self):
        self.lbl_date.setText(datetime.now().strftime("%a %d %b %Y"))

    # ── persistence ───────────────────────────────────────────────────────────

    def _load_list(self, filepath: str) -> list:
        if os.path.exists(filepath):
            try:
                with open(filepath) as f:
                    data = json.load(f)
                if isinstance(data, list):
                    return [str(x) for x in data]
            except Exception as e:
                import logging
                logging.getLogger("pharma.main_window").warning(
                    "Could not load list %s: %s", filepath, e)
        return []

    def _save_list(self, filepath: str, items: list):
        try:
            with open(filepath, "w") as f:
                json.dump(items, f, indent=2)
        except Exception as e:
            import logging
            logging.getLogger("pharma.main_window").warning(
                "Could not save list %s: %s", filepath, e)

    def _update_viz_on_read(self, device_id: int, is_pass: bool):
        refs = self._viz_d1 if device_id == 1 else self._viz_d2
        if is_pass:
            refs["pass_count"] += 1
        else:
            refs["fail_count"] += 1
        self._refresh_viz(device_id)

    # ══════════════════════════════════════════════════════════════════════════
    #  CFR21 — SESSION, INACTIVITY, AUDIT HELPERS
    # ══════════════════════════════════════════════════════════════════════════

    def _apply_role_ui(self):
        """
        Apply role-based UI gating. Called at startup and after re-login.

        For roles WITHOUT start_logging permission (QA):
          - btn_start and btn_stop are permanently disabled
          - btn_session is hidden (no point setting up a batch they can't start)

        For roles WITH start_logging permission (Admin/Supervisor/Operator):
          - btn_start enable state is left to the camera connect flow
            (enabled by _on_connect_requested, disabled by _on_disconnect_requested)
          - btn_session is visible
          - Tooltips are cleared
        """
        can_start = self._sm.can("start_logging") if self._sm else True

        if not can_start:
            # QA or unknown role — lock out start/stop/load permanently
            self.btn_start.setEnabled(False)
            self.btn_stop.setEnabled(False)
            role_name = (
                self._sm.current_user.role_display
                if self._sm and self._sm.current_user else "Unknown"
            )
            tip = f"Not available for role: {role_name}"
            self.btn_start.setToolTip(tip)
            self.btn_stop.setToolTip(tip)
            self.btn_session.setVisible(False)
        else:
            # Permitted role — clear any leftover tooltip from a previous QA session
            # Do NOT touch btn_start.setEnabled here — camera connect flow owns that
            self.btn_start.setToolTip("")
            self.btn_stop.setToolTip("")
            self.btn_session.setVisible(True)

    def _update_user_label(self):
        """Update the topbar user button and logout button from current session."""
        if self._sm and self._sm.current_user:
            u = self._sm.current_user
            self._btn_user.setText(f"👤  {u.username}  ·  {u.role_display}  ▾")
            self._btn_user.setVisible(True)
            self._btn_logout.setVisible(True)
        else:
            self._btn_user.setText("")
            self._btn_user.setVisible(False)
            self._btn_logout.setVisible(False)

    def _open_change_password(self):
        """CFR21 Fix 4: open Change Password dialog for the current user."""
        if not self._sm or not self._sm.current_user:
            return
        dlg = ChangePasswordDialog(
            user        = self._sm.current_user,
            session_mgr = self._sm,
            forced      = False,
            parent      = self,
        )
        dlg.exec()

    # ══════════════════════════════════════════════════════════════════════════
    #  CFR21 — LOCK SCREEN OVERLAY
    # ══════════════════════════════════════════════════════════════════════════

    def _build_lock_overlay(self) -> QWidget:
        """
        Builds a full-window overlay widget that sits on top of everything.
        Hidden by default. Shown by _show_lock_screen() on inactivity timeout.
        Logging continues behind it — only the UI is blocked.
        """
        overlay = QWidget(self)
        overlay.setObjectName("lock_overlay")
        overlay.setStyleSheet(
            "QWidget#lock_overlay { background: rgba(10, 12, 16, 0.92); }"
        )
        overlay.hide()
        overlay.resize(self.size())

        v = QVBoxLayout(overlay)
        v.setAlignment(Qt.AlignmentFlag.AlignCenter)
        v.setSpacing(12)

        icon = QLabel("🔒")
        icon.setStyleSheet("font-size: 48px; background: transparent;")
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)

        title = QLabel("Screen Locked")
        title.setStyleSheet(
            "color: #ffffff; font-size: 22px; font-weight: 700; "
            "background: transparent;"
        )
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._lock_user_lbl = QLabel("")
        self._lock_user_lbl.setStyleSheet(
            "color: rgba(255,255,255,0.60); font-size: 13px; "
            "background: transparent;"
        )
        self._lock_user_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)

        sub = QLabel("Production is running. Enter your password to resume.")
        sub.setStyleSheet(
            "color: rgba(255,255,255,0.50); font-size: 11px; "
            "background: transparent;"
        )
        sub.setAlignment(Qt.AlignmentFlag.AlignCenter)

        # Running badge — shows logging is still active
        self._lock_running_lbl = QLabel("● LOGGING IN PROGRESS")
        self._lock_running_lbl.setStyleSheet(
            "color: #4cdf7c; font-size: 11px; font-weight: 700; "
            "background: transparent; letter-spacing: 1px;"
        )
        self._lock_running_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._lock_running_lbl.hide()

        # Password input
        self._lock_pw_input = QLineEdit()
        self._lock_pw_input.setEchoMode(QLineEdit.EchoMode.Password)
        self._lock_pw_input.setPlaceholderText("Enter your password to unlock")
        self._lock_pw_input.setFixedWidth(300)
        self._lock_pw_input.setFixedHeight(40)
        self._lock_pw_input.setStyleSheet(
            "QLineEdit { background: rgba(255,255,255,0.10); "
            "border: 1px solid rgba(255,255,255,0.25); border-radius: 6px; "
            "color: #ffffff; font-size: 13px; padding: 0 12px; } "
            "QLineEdit:focus { border-color: rgba(255,255,255,0.60); }"
        )
        self._lock_pw_input.returnPressed.connect(self._on_unlock_attempt)

        # Error label
        self._lock_err_lbl = QLabel("")
        self._lock_err_lbl.setStyleSheet(
            "color: #ff6b6b; font-size: 11px; background: transparent;"
        )
        self._lock_err_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._lock_err_lbl.hide()

        # Unlock button
        unlock_btn = QPushButton("Unlock")
        unlock_btn.setFixedWidth(160)
        unlock_btn.setFixedHeight(40)
        unlock_btn.setStyleSheet(
            "QPushButton { background: #0062a3; color: #ffffff; border: none; "
            "border-radius: 6px; font-size: 13px; font-weight: 700; } "
            "QPushButton:hover { background: #004f87; } "
            "QPushButton:pressed { background: #003d6b; }"
        )
        unlock_btn.clicked.connect(self._on_unlock_attempt)

        # Sign out link — if a different person needs to log in
        signout_btn = QPushButton("Sign out and switch user →")
        signout_btn.setStyleSheet(
            "QPushButton { background: transparent; color: rgba(255,255,255,0.40); "
            "border: none; font-size: 10px; text-decoration: underline; } "
            "QPushButton:hover { color: rgba(255,255,255,0.70); }"
        )
        signout_btn.clicked.connect(self._on_signout_from_lock)

        for w in [icon, title, self._lock_user_lbl, sub,
                  self._lock_running_lbl,
                  self._lock_pw_input,
                  self._lock_err_lbl, unlock_btn, signout_btn]:
            v.addWidget(w, alignment=Qt.AlignmentFlag.AlignCenter)

        return overlay

    def resizeEvent(self, event):
        """Keep lock overlay covering the full window when resized."""
        super().resizeEvent(event)
        if hasattr(self, "_lock_overlay"):
            self._lock_overlay.resize(self.size())

    def _show_lock_screen(self):
        """
        Called by SessionManager.on_screen_lock when inactivity timeout fires.
        Shows the overlay. Logging keeps running behind it.
        """
        if not self._sm or not self._sm.current_user:
            return

        u = self._sm.current_user
        self._lock_user_lbl.setText(
            f"{u.username}  ·  {u.role_display}"
        )
        # Show whether logging is active so operator knows
        if self._controller.is_running():
            self._lock_running_lbl.show()
        else:
            self._lock_running_lbl.hide()

        self._lock_pw_input.clear()
        self._lock_err_lbl.hide()
        self._lock_overlay.resize(self.size())
        self._lock_overlay.show()
        self._lock_overlay.raise_()
        QTimer.singleShot(100, self._lock_pw_input.setFocus)

    def _on_unlock_attempt(self):
        """User entered password on lock screen — verify and unlock."""
        pw = self._lock_pw_input.text()
        if not pw:
            return

        ok, msg = self._sm.unlock_screen(pw)
        self._lock_pw_input.clear()

        if ok:
            self._lock_overlay.hide()
            self._lock_err_lbl.hide()
        else:
            self._lock_err_lbl.setText(msg or "Incorrect password.")
            self._lock_err_lbl.show()

    def _on_signout_from_lock(self):
        """
        Switch user from the lock screen — for shift change.

        KEY BEHAVIOUR: logging is NOT stopped.
        The new user takes over the running batch seamlessly.
        The operator_id in the session is updated to the new user.
        A SESSION_SETUP audit entry records the handover.

        This is different from the Sign Out button which always stops logging.
        """
        if not self._sm or not self._sm.current_user:
            self._lock_overlay.hide()
            return

        previous_user     = self._sm.current_user
        previous_username = previous_user.username
        logging_was_active = self._controller.is_running()

        # End the current session — but do NOT touch logging
        self._sm.logout(reason="Shift change — switched user from lock screen")
        self._update_user_label()
        self._lock_overlay.hide()

        # Show login dialog for next user
        login_dlg = LoginDialog(self._sm, parent=self)
        if login_dlg.exec() == LoginDialog.DialogCode.Accepted:
            self._update_user_label()
            if self._sm.current_user:
                self._controller.set_cfr_session(
                    self._sm.current_user, self._sm.session_id)
            self._apply_role_ui()

            new_user = self._sm.current_user
            new_username = new_user.username if new_user else ""

            if new_username.lower() != previous_username.lower():
                # Different user logged in — update operator_id in the
                # running session so all subsequent scans carry the new name
                if logging_was_active and new_user:
                    self._session.operator_id = new_username
                    self._controller.set_session(self._session)
                    self._update_session_strip()

                    # Audit the handover
                    audit.log(
                        user       = new_user,
                        action     = audit.ACTION_SESSION_SETUP,
                        detail     = (
                            f"Shift change — '{previous_username}' handed over "
                            f"to '{new_username}'. "
                            f"Batch '{self._session.batch_id}' continues. "
                            f"Logging was not interrupted."
                        ),
                        session_id = self._sm.session_id,
                    )
                else:
                    # Not logging — full session reset for new user
                    self._session = SessionInfo()
                    self._controller.set_session(self._session)
                    self._update_session_strip()
                    if self._sm.can("start_logging"):
                        QTimer.singleShot(300, self.open_session_setup)
        else:
            # New user cancelled login — if logging was active we need
            # someone to be logged in. Show lock screen again.
            if logging_was_active:
                QMessageBox.warning(
                    self, "Login Required",
                    "Logging is active. You must log in to continue.\n"
                    "The application will re-lock."
                )
                # Re-login loop — keep showing login until someone logs in
                login_dlg2 = LoginDialog(self._sm, parent=self)
                if login_dlg2.exec() == LoginDialog.DialogCode.Accepted:
                    self._update_user_label()
                    if self._sm.current_user:
                        self._controller.set_cfr_session(
                            self._sm.current_user, self._sm.session_id)
                    self._apply_role_ui()
                else:
                    # Nobody will log in — stop logging and close
                    self._controller.stop_logging()
                    self.close()
            else:
                self.close()

    def _on_logout_clicked(self, reason: str = "User clicked Sign Out"):
        """
        Explicit logout — ends session, stops logging, shows login dialog.
        This is the only path that actually terminates an active session.
        """
        if not self._sm or not self._sm.is_logged_in:
            return

        # Warn if logging is active
        if self._controller.is_running():
            reply = QMessageBox.question(
                self, "Logging Active",
                "Logging is currently active.\n\n"
                "Signing out will stop logging and finalise the current batch.\n\n"
                "Are you sure you want to sign out?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return

            # Stop logging before logout
            self._controller.stop_logging()
            for panel in [self.panel_d1, self.panel_d2]:
                panel.set_logging_active(False)
            self.btn_start.setEnabled(True)
            self.btn_stop.setEnabled(False)
            self.lbl_logging_badge.hide()

            if self._sm:
                audit.log(
                    user       = self._sm.current_user,
                    action     = audit.ACTION_BATCH_STOPPED,
                    detail     = (
                        f"Batch '{self._session.batch_id}' stopped "
                        f"due to user sign-out."
                    ),
                    session_id = self._sm.session_id,
                    reason     = reason,
                )

        # Remember previous username for session reset check
        previous_username = (
            self._sm.current_user.username
            if self._sm.current_user else ""
        )

        # End the session
        self._sm.logout(reason=reason)
        self._update_user_label()

        # Show login dialog for next user
        login_dlg = LoginDialog(self._sm, parent=self)
        if login_dlg.exec() == LoginDialog.DialogCode.Accepted:
            self._update_user_label()
            if self._sm.current_user:
                self._controller.set_cfr_session(
                    self._sm.current_user, self._sm.session_id)
            self._apply_role_ui()

            # Reset session if different user logged in
            new_username = (
                self._sm.current_user.username
                if self._sm.current_user else ""
            )
            if new_username.lower() != previous_username.lower():
                self._session = SessionInfo()
                self._controller.set_session(self._session)
                self._update_session_strip()
                if self._sm.can("start_logging"):
                    QTimer.singleShot(300, self.open_session_setup)
        else:
            self.close()

    def _check_inactivity(self):
        """Called by QTimer every 60s — lock screen if timed out."""
        if self._sm and self._sm.is_timed_out():
            self._sm.lock_screen()


    def mousePressEvent(self, event):
        """Ping session on any mouse click to reset inactivity timer."""
        if self._sm:
            self._sm.ping()
        super().mousePressEvent(event)

    def keyPressEvent(self, event):
        """Ping session on any keypress to reset inactivity timer."""
        if self._sm:
            self._sm.ping()
        super().keyPressEvent(event)

    def _on_arm_teach(self, device_id: int):
        if not self._controller.logger(device_id).is_running():
            QMessageBox.warning(self, "Not Running",
                                "Start Logging before arming Teach mode.")
            return
        self._controller.arm_teach(device_id)
        panel = self.panel_d1 if device_id == 1 else self.panel_d2
        panel.arm_teach_visual()
        # CFR21: log teach ARMED — pending capture, not yet confirmed
        # The actual master code value is logged in _on_teach_captured
        # which fires only when the camera confirms a successful read
        if self._sm:
            audit.log(
                user       = self._sm.current_user,
                action     = audit.ACTION_MASTER_SET,
                detail     = (
                    f"Teach mode armed for Device {device_id}. "
                    f"Awaiting camera read to confirm master code."
                ),
                session_id = self._sm.session_id,
            )
            self._sm.ping()

    def _on_teach_captured(self, device_id: int, code: str):
        """
        CFR21 Fix 2 & 5: fires when datalogger confirms a successful teach capture.
        Logs the actual master code value — this is the authoritative audit entry.
        Fires exactly once per teach operation, only on confirmed camera read.
        """
        if self._sm:
            audit.log(
                user       = self._sm.current_user,
                action     = audit.ACTION_MASTER_SET,
                detail     = (
                    f"Master code confirmed and set for Device {device_id}. "
                    f"Captured code: '{code}'. "
                    f"Batch: '{self._session.batch_id}'."
                ),
                session_id = self._sm.session_id,
            )
            self._sm.ping()

    def _on_clear_master(self, device_id: int):
        self._controller.clear_master(device_id)
        panel = self.panel_d1 if device_id == 1 else self.panel_d2
        panel.clear_master_visual()
        # CFR21: log master clear
        if self._sm:
            audit.log(
                user       = self._sm.current_user,
                action     = audit.ACTION_MASTER_CLEARED,
                detail     = (
                    f"Master code cleared for Device {device_id}. "
                    f"Batch: '{self._session.batch_id}'."
                ),
                session_id = self._sm.session_id,
            )
            self._sm.ping()

    def _on_wal_error(self, device_id: int, msg: str):
        """
        Item 3: WAL write failed — the electronic record cannot be written.
        The scan loop has already halted itself. Stop everything, alarm
        loudly, audit the event. A record system that cannot record must
        not appear to be recording.
        """
        # Stop the other device too — batch integrity is per-session
        self._controller.stop_logging()
        for panel in [self.panel_d1, self.panel_d2]:
            panel.set_logging_active(False)
        self.btn_start.setEnabled(True)
        self.btn_stop.setEnabled(False)
        self.lbl_logging_badge.hide()

        if self._sm:
            audit.log(
                user       = self._sm.current_user,
                action     = audit.ACTION_WAL_WRITE_FAILED,
                detail     = (
                    f"RECORD WRITE FAILURE on Device {device_id} during batch "
                    f"'{self._session.batch_id}': {msg}. "
                    f"Logging stopped automatically on all devices."
                ),
                session_id = self._sm.session_id,
            )

        QMessageBox.critical(
            self, "⚠ RECORD WRITE FAILURE",
            f"Device {device_id} could not write to the batch record "
            f"(WAL file):\n\n{msg}\n\n"
            "LOGGING HAS BEEN STOPPED on all devices.\n\n"
            "Products scanned after this failure were NOT recorded.\n"
            "Check disk space and folder permissions, then restart the batch.\n"
            "Contact your administrator before resuming production."
        )

    def _on_plc_error(self, device_id: int, consec_count: int):
        """
        Item 4: PLC reject writes failing repeatedly — the ejector may not
        be firing. Failed products could be passing through un-rejected.
        Logging continues (records are still valid) but the operator must
        be told immediately.
        """
        if self._sm:
            audit.log(
                user       = self._sm.current_user,
                action     = audit.ACTION_PLC_WRITE_FAILED,
                detail     = (
                    f"PLC reject write FAILED {consec_count} consecutive "
                    f"times on Device {device_id} during batch "
                    f"'{self._session.batch_id}'. Failed products may NOT "
                    f"be physically rejected. Recording continues."
                ),
                session_id = self._sm.session_id,
            )

        QMessageBox.critical(
            self, "⚠ PLC REJECT FAILURE",
            f"The PLC reject signal has failed {consec_count} times in a "
            f"row on Device {device_id}.\n\n"
            "FAILED PRODUCTS MAY NOT BE PHYSICALLY EJECTED.\n\n"
            "Recording continues, but check the PLC connection and the "
            "ejector mechanism immediately. Quarantine product produced "
            "since the last verified rejection."
        )

    def _on_camera_lost(self, device_id: int):
        """CFR21: audit camera unexpected disconnect (Fix 4)."""
        if self._sm:
            audit.log(
                user       = self._sm.current_user,
                action     = audit.ACTION_CAMERA_LOST,
                detail     = (
                    f"Camera Device {device_id} lost connection unexpectedly "
                    f"during batch '{self._session.batch_id}'. "
                    f"Logging suspended for this device."
                ),
                session_id = self._sm.session_id,
            )

    def _on_consec_fail_audit(self, device_id: int, count: int):
        """CFR21: audit consecutive fail alarm (Fix 4)."""
        if self._sm:
            audit.log(
                user       = self._sm.current_user,
                action     = audit.ACTION_CONSEC_FAIL_ALARM,
                detail     = (
                    f"Consecutive fail alarm triggered on Device {device_id}. "
                    f"{count} consecutive failures in batch "
                    f"'{self._session.batch_id}'."
                ),
                session_id = self._sm.session_id,
            )
