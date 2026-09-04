# gui/dialogs.py
# SessionSetupDialog  — Batch ID, Operator ID, Product Name
# AdvancedSettingsPage — password-locked settings for Device 1, Device 2, PLC, General

from PyQt6.QtWidgets import (
    QDialog, QDialogButtonBox, QWidget, QVBoxLayout, QHBoxLayout,
    QGridLayout, QGroupBox, QLabel, QLineEdit, QPushButton,
    QComboBox, QFrame, QScrollArea, QStackedWidget, QMessageBox, QFileDialog,
)
from PyQt6.QtCore import Qt, pyqtSignal

try:
    from PyQt6.QtWidgets import QScroller
    _SCROLLER_OK = True
except ImportError:
    _SCROLLER_OK = False


def _enable_touch_scroll(widget):
    if not _SCROLLER_OK:
        return
    QScroller.grabGesture(
        widget.viewport(),
        QScroller.ScrollerGestureType.LeftMouseButtonGesture,
    )


from config.settings import AppConfig
from core.models import SessionInfo
from gui.ui_constants import UI


# ══════════════════════════════════════════════════════════════════════════════
#  SESSION SETUP DIALOG
# ══════════════════════════════════════════════════════════════════════════════

class SessionSetupDialog(QDialog):

    def __init__(self, parent, batch_ids: list,
                 operator_ids: list, product_names: list):
        super().__init__(parent)
        self.setWindowTitle("Session Setup")
        self.setModal(True)
        self.setFixedWidth(540)
        self.setMinimumHeight(320)
        self.setWindowFlags(
            self.windowFlags() & ~Qt.WindowType.WindowContextHelpButtonHint)

        layout = QVBoxLayout(self)
        layout.setSpacing(0)
        layout.setContentsMargins(24, 20, 24, 20)

        title = QLabel("Session Setup")
        title.setStyleSheet("font-size: 14px; font-weight: 700; color: #1a1e24;")
        layout.addWidget(title)

        layout.addSpacing(4)

        sub = QLabel("Set session details before starting logging.")
        sub.setStyleSheet("font-size: 11px; color: #8a93a0;")
        layout.addWidget(sub)

        layout.addSpacing(12)

        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setStyleSheet("background: #e0e4ea; max-height: 1px;")
        layout.addWidget(line)

        layout.addSpacing(16)

        grid = QGridLayout()
        grid.setVerticalSpacing(12)
        grid.setHorizontalSpacing(10)
        grid.setColumnMinimumWidth(0, 90)
        grid.setColumnStretch(1, 1)

        def _lbl(text):
            l = QLabel(text)
            l.setStyleSheet("font-size: 11px; font-weight: 600; color: #4a5260;")
            return l

        def _line_edit(placeholder):
            le = QLineEdit()
            le.setPlaceholderText(placeholder)
            le.setFixedHeight(32)
            le.setStyleSheet(
                "QLineEdit { border: 1px solid #d0d4da; border-radius: 4px; "
                "padding: 0 8px; font-size: 12px; background: #fff; }"
                "QLineEdit:focus { border-color: #0062a3; }"
            )
            return le

        # Batch ID — plain text input (no dropdown, no Add button)
        self.batch_edit = _line_edit("e.g. 12200")

        # Operator ID — locked to logged-in user; shown as read-only combo
        self.operator_combo = QComboBox()
        self.operator_combo.setObjectName("batch_combo")
        self.operator_combo.setEditable(True)
        self.operator_combo.addItems(operator_ids)
        self.operator_combo.lineEdit().setPlaceholderText("e.g. OP-01")
        self.operator_combo.setFixedHeight(32)
        self.operator_combo.setEnabled(False)   # locked to logged-in user

        # Product Name — plain text input (no dropdown, no Add button)
        self.product_edit = _line_edit("e.g. Aspirin 500mg")

        rows = [
            ("Batch ID",     self.batch_edit),
            ("Operator ID",  self.operator_combo),
            ("Product Name", self.product_edit),
        ]
        for row_idx, (lbl_text, widget) in enumerate(rows):
            grid.addWidget(_lbl(lbl_text), row_idx, 0,
                           Qt.AlignmentFlag.AlignVCenter)
            grid.addWidget(widget,         row_idx, 1)

        layout.addLayout(grid)
        layout.addSpacing(16)

        # Buttons row — equal width Confirm + Cancel
        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)

        self.btn_cancel = QPushButton("Cancel")
        self.btn_cancel.setFixedHeight(34)
        self.btn_cancel.setStyleSheet(
            "QPushButton { background: #f4f5f7; border: 1px solid #d0d4da; "
            "border-radius: 4px; color: #4a5260; font-size: 13px; font-weight: 500; }"
            "QPushButton:hover { background: #e8eaed; }"
            "QPushButton:pressed { background: #d8dce2; }")
        self.btn_cancel.clicked.connect(self.reject)

        self.btn_confirm = QPushButton("Confirm")
        self.btn_confirm.setFixedHeight(34)
        self.btn_confirm.setStyleSheet(
            "QPushButton { background: #0062a3; border: none; border-radius: 4px; "
            "color: #ffffff; font-size: 13px; font-weight: 600; }"
            "QPushButton:hover { background: #004f87; }"
            "QPushButton:pressed { background: #003d6b; }")
        self.btn_confirm.clicked.connect(self.accept)

        btn_row.addWidget(self.btn_cancel, 1)
        btn_row.addWidget(self.btn_confirm, 1)
        layout.addLayout(btn_row)

    def values(self) -> SessionInfo:
        return SessionInfo(
            batch_id     = self.batch_edit.text().strip(),
            operator_id  = self.operator_combo.currentText().strip(),
            product_name = self.product_edit.text().strip(),
        )


# ══════════════════════════════════════════════════════════════════════════════
#  ADVANCED SETTINGS PAGE
# ══════════════════════════════════════════════════════════════════════════════

class AdvancedSettingsPage(QStackedWidget):
    """
    Two-page stack: page 0 = lock screen, page 1 = settings content.
    Reads from and writes to AppConfig directly.
    Emits settings_saved when Save is clicked and validated.
    """

    settings_saved = pyqtSignal(object)   # emits updated AppConfig
    session_lists_reset = pyqtSignal()    # emits when session lists are cleared

    def __init__(self, config: AppConfig, parent=None, session_mgr=None):
        super().__init__(parent)
        self._config = config
        self._sm     = session_mgr  # CFR21: SessionManager — owns all auth
        # CFR21 Fix 10: _current_password removed — no password in settings.json
        # Fallback dev mode uses a hardcoded check only, never stored

        self._inputs: dict[str, QLineEdit] = {}

        self.addWidget(self._build_lock_page())

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        content.setObjectName("central")
        cv = QVBoxLayout(content)
        cv.setContentsMargins(20, 16, 20, 16)
        cv.setSpacing(12)
        self._build_settings_content(cv)
        scroll.setWidget(content)
        _enable_touch_scroll(scroll)
        self.addWidget(scroll)

        self._populate_from_config()

    # ── lock page ─────────────────────────────────────────────────────────────

    def _build_lock_page(self) -> QWidget:
        """
        CFR21: lock page no longer uses a password input.
        Access is granted purely by role — Administrator only.
        When the tab is clicked, _unlock() is called automatically.
        The lock page is only shown if the role check fails.
        """
        page = QWidget()
        page.setObjectName("lock_widget")
        lv = QVBoxLayout(page)
        lv.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lv.setSpacing(10)

        self._lock_icon  = self._make_label("🔒", "lock_icon")
        self._lock_title = self._make_label("Advanced Settings", "lock_title")
        self._lock_sub   = self._make_label(
            "Administrator access required.", "lock_sub"
        )

        for w in [self._lock_icon, self._lock_title, self._lock_sub]:
            lv.addWidget(w, alignment=Qt.AlignmentFlag.AlignHCenter)

        return page

    @staticmethod
    def _make_label(text, obj_name) -> QLabel:
        lbl = QLabel(text)
        lbl.setObjectName(obj_name)
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        return lbl

    def _unlock(self):
        """
        CFR21: check role instead of comparing a password.

        - If SessionManager is present and user has 'change_settings'
          permission (Administrator only) → go straight to settings page.
        - If no session manager (running in test/dev without CFR21) →
          fall back to the old plaintext password check on settings.json.
        - Any other role → show the lock page with an access denied message.
        """
        if self._sm:
            if self._sm.can("change_settings"):
                # Administrator — require re-authentication (§11.200)
                from gui.cfr_dialogs import ReauthDialog
                dlg = ReauthDialog(
                    self._sm,
                    action_description="access Advanced Settings",
                    parent=self.parentWidget(),
                )
                if dlg.exec() == QDialog.DialogCode.Accepted:
                    self.setCurrentIndex(1)
                else:
                    self._lock_sub.setText(
                        "Identity verification cancelled.\n"
                        "Re-authentication is required to access settings."
                    )
                    self._lock_icon.setText("🔒")
                    self.setCurrentIndex(0)
            else:
                # Any other role — show lock page with clear message
                role = (
                    self._sm.current_user.role_display
                    if self._sm.current_user else "Unknown"
                )
                self._lock_sub.setText(
                    f"Access denied — Administrator role required.\n"
                    f"Your current role: {role}"
                )
                self._lock_icon.setText("🚫")
                self.setCurrentIndex(0)
        else:
            # Fallback: no CFR21 session manager — development mode only.
            # In production the session_mgr is always present.
            # Use a hardcoded dev password rather than settings.json
            from PyQt6.QtWidgets import QInputDialog
            _DEV_FALLBACK_PW = "admin@123"
            pw, ok = QInputDialog.getText(
                self.parentWidget(), "Advanced Settings",
                "Enter password:", QLineEdit.EchoMode.Password
            )
            if ok and pw == _DEV_FALLBACK_PW:
                self.setCurrentIndex(1)
            elif ok:
                QMessageBox.warning(self.parentWidget(), "Wrong Password",
                                    "Incorrect password.")
                self.setCurrentIndex(0)

    # ── settings content ──────────────────────────────────────────────────────

    def _inp(self, key: str, default: str,
             width: int = UI.INPUT_W_MD) -> QLineEdit:
        inp = QLineEdit(default)
        inp.setObjectName("settings_input")
        inp.setFixedWidth(width)
        inp.setMinimumHeight(UI.INPUT_H)
        self._inputs[key] = inp
        return inp

    def _lbl(self, text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setObjectName("settings_label")
        return lbl

    def _build_settings_content(self, layout: QVBoxLayout):

        # ── Device 1 ──────────────────────────────────────────────────────────
        d1_grp = QGroupBox("Device 1 — Camera Connection")
        g = QGridLayout(d1_grp)
        g.setSpacing(8)
        g.setColumnMinimumWidth(0, 180)
        for r, (lbl, key, w) in enumerate([
            ("Camera IP",          "d1_camera_ip",   UI.INPUT_W_MD),
            ("Camera Port",        "d1_camera_port",  UI.INPUT_W_SM),
            ("Read Timeout (s)",   "d1_poll",         UI.INPUT_W_SM),
        ]):
            g.addWidget(self._lbl(lbl), r, 0)
            g.addWidget(self._inp(key, "", w), r, 1)
        layout.addWidget(d1_grp)

        # ── Device 2 ──────────────────────────────────────────────────────────
        d2_grp = QGroupBox("Device 2 — Camera Connection")
        g2 = QGridLayout(d2_grp)
        g2.setSpacing(8)
        g2.setColumnMinimumWidth(0, 180)
        for r, (lbl, key, w) in enumerate([
            ("Camera IP",          "d2_camera_ip",   UI.INPUT_W_MD),
            ("Camera Port",        "d2_camera_port",  UI.INPUT_W_SM),
            ("Read Timeout (s)",   "d2_poll",         UI.INPUT_W_SM),
        ]):
            g2.addWidget(self._lbl(lbl), r, 0)
            g2.addWidget(self._inp(key, "", w), r, 1)
        layout.addWidget(d2_grp)

        # ── PLC ───────────────────────────────────────────────────────────────
        plc_grp = QGroupBox("PLC Network  (Modbus/TCP)")
        pg = QGridLayout(plc_grp)
        pg.setSpacing(8)
        pg.setColumnMinimumWidth(0, 180)

        pg.addWidget(self._lbl("PLC IP"),   0, 0)
        pg.addWidget(self._inp("plc_ip",   "", UI.INPUT_W_MD), 0, 1)
        pg.addWidget(self._lbl("PLC Port"), 1, 0)
        pg.addWidget(self._inp("plc_port", "", UI.INPUT_W_SM), 1, 1)

        div = QFrame()
        div.setFrameShape(QFrame.Shape.HLine)
        div.setStyleSheet("color: #e0e4ea; margin: 4px 0;")
        pg.addWidget(div, 2, 0, 1, 3)

        # Register — Device 1
        pg.addWidget(self._lbl("Register — Device 1"), 3, 0)
        pg.addWidget(self._inp("plc_hreg_d1", "", UI.INPUT_W_SM), 3, 1)
        btn_set1 = QPushButton("Set")
        btn_set1.setObjectName("btn_sm")
        btn_set1.setFixedWidth(UI.BTN_SET_W)
        btn_set1.setMinimumHeight(UI.INPUT_H)
        btn_set1.clicked.connect(lambda: self._on_hreg_set(1))
        pg.addWidget(btn_set1, 3, 2)

        # Register — Device 2
        pg.addWidget(self._lbl("Register — Device 2"), 4, 0)
        pg.addWidget(self._inp("plc_hreg_d2", "", UI.INPUT_W_SM), 4, 1)
        btn_set2 = QPushButton("Set")
        btn_set2.setObjectName("btn_sm")
        btn_set2.setFixedWidth(UI.BTN_SET_W)
        btn_set2.setMinimumHeight(UI.INPUT_H)
        btn_set2.clicked.connect(lambda: self._on_hreg_set(2))
        pg.addWidget(btn_set2, 4, 2)

        pg.addWidget(self._lbl("PASS Value"), 5, 0)
        pg.addWidget(self._inp("plc_pass_val", "", UI.INPUT_W_SM), 5, 1)
        pg.addWidget(self._lbl("FAIL Value"),  6, 0)
        pg.addWidget(self._inp("plc_fail_val", "", UI.INPUT_W_SM), 6, 1)

        layout.addWidget(plc_grp)

        # ── PLC Delay Registers ───────────────────────────────────────────────
        delay_grp = QGroupBox("PLC — Delay Registers  (-1 = not used)")
        dg = QGridLayout(delay_grp)
        dg.setSpacing(8)
        dg.setColumnMinimumWidth(0, 180)

        for row, (lbl_text, hreg_key, val_key) in enumerate([
            ("D1 Reject Delay Register",  "plc_d1_reject_hreg",  "plc_d1_reject_val"),
            ("D1 Trigger Delay Register", "plc_d1_trigger_hreg", "plc_d1_trigger_val"),
        ]):
            dg.addWidget(self._lbl(lbl_text), row, 0)
            dg.addWidget(self._inp(hreg_key, "", UI.INPUT_W_SM), row, 1)
            dg.addWidget(self._lbl("Value"), row, 2)
            dg.addWidget(self._inp(val_key, "", UI.INPUT_W_SM), row, 3)

        layout.addWidget(delay_grp)

        # ── PLC Timing & Status Registers ─────────────────────────────────────
        timing_grp = QGroupBox("PLC — Timing & Status Registers  (-1 = not used)")
        tg = QGridLayout(timing_grp)
        tg.setSpacing(8)
        tg.setColumnMinimumWidth(0, 180)

        tg.addWidget(self._lbl("Cylinder Timing Register"), 0, 0)
        tg.addWidget(self._inp("plc_cyl_timing_hreg", "", UI.INPUT_W_SM), 0, 1)
        tg.addWidget(self._lbl("Value"), 0, 2)
        tg.addWidget(self._inp("plc_cyl_timing_val", "", UI.INPUT_W_SM), 0, 3)

        tg.addWidget(self._lbl("Camera Status Register"), 1, 0)
        tg.addWidget(self._inp("plc_cam1_status_hreg", "", UI.INPUT_W_SM), 1, 1)
        status_note = QLabel("1=connected  0=disconnected  (auto-written)")
        status_note.setStyleSheet("color: #8a93a0; font-size: 11px;")
        tg.addWidget(status_note, 1, 2, 1, 2)

        layout.addWidget(timing_grp)

        # ── PLC Spare Registers ───────────────────────────────────────────────
        spare_grp = QGroupBox("PLC — Spare Registers  (-1 = not used)")
        sg = QGridLayout(spare_grp)
        sg.setSpacing(8)
        sg.setColumnMinimumWidth(0, 180)

        for row, n in enumerate([1, 2, 3, 4]):
            sg.addWidget(self._lbl(f"Spare {n} Register"), row, 0)
            sg.addWidget(self._inp(f"plc_spare{n}_hreg", "", UI.INPUT_W_SM), row, 1)
            sg.addWidget(self._lbl("Value"), row, 2)
            sg.addWidget(self._inp(f"plc_spare{n}_val", "", UI.INPUT_W_SM), row, 3)

        layout.addWidget(spare_grp)

        # ── General ───────────────────────────────────────────────────────────
        gen_grp = QGroupBox("General")
        gg = QGridLayout(gen_grp)
        gg.setSpacing(8)
        gg.setColumnMinimumWidth(0, 180)

        gg.addWidget(self._lbl("Save Logs To"), 0, 0)
        log_inp = self._inp("log_dir", "", UI.INPUT_W_LG)
        gg.addWidget(log_inp, 0, 1)
        browse_btn = QPushButton("Browse…")
        browse_btn.setObjectName("btn_sm")
        browse_btn.setFixedWidth(UI.BTN_MD_W)
        browse_btn.setMinimumHeight(UI.INPUT_H)
        browse_btn.clicked.connect(self._browse_log_dir)
        gg.addWidget(browse_btn, 0, 2)

        # CFR21: password management moved to CFR21 Compliance → User Management
        pw_note = QLabel(
            "Password management is in  CFR21 Compliance → User Management"
        )
        pw_note.setStyleSheet("color: #8a93a0; font-size: 11px; font-style: italic;")
        gg.addWidget(pw_note, 1, 0, 1, 3)

        gg.addWidget(self._lbl("Session Lists"), 2, 0)
        lbl_info = QLabel("Batch IDs, Operator IDs, Product Names")
        lbl_info.setStyleSheet("color: #8a93a0; font-size: 11px;")
        gg.addWidget(lbl_info, 2, 1)
        reset_btn = QPushButton("Reset Lists")
        reset_btn.setObjectName("btn_sm")
        reset_btn.setFixedWidth(UI.BTN_PW_W)
        reset_btn.setMinimumHeight(UI.INPUT_H)
        reset_btn.clicked.connect(self._reset_session_lists)
        gg.addWidget(reset_btn, 2, 2)

        layout.addWidget(gen_grp)

        # ── Security Policy (CFR21 Issue 5) ───────────────────────────────────
        pol_grp = QGroupBox("Security Policy  (21 CFR Part 11)")
        pg2 = QGridLayout(pol_grp)
        pg2.setSpacing(8)
        pg2.setColumnMinimumWidth(0, 220)

        for r, (lbl_text, key, unit) in enumerate([
            ("Screen Lock Timeout (minutes)", "pol_timeout",  "min"),
            ("Password Expiry (days, 0=never)", "pol_expiry", "days"),
            ("Max Failed Login Attempts",     "pol_attempts", ""),
            ("Account Lockout Duration (min)", "pol_lockout", "min"),
            ("Password History Count",        "pol_history",  ""),
            ("Consecutive Fail Alarm Limit",  "consec_fail_limit", "scans"),
        ]):
            pg2.addWidget(self._lbl(lbl_text), r, 0)
            pg2.addWidget(self._inp(key, "", UI.INPUT_W_SM), r, 1)
            if unit:
                pg2.addWidget(self._lbl(unit), r, 2)

        layout.addWidget(pol_grp)

        # ── Compliance DB Backup (CFR21 Issue 6) ──────────────────────────────
        bkp_grp = QGroupBox("Compliance Database Backup  (21 CFR Part 11)")
        bg = QGridLayout(bkp_grp)
        bg.setSpacing(8)
        bg.setColumnMinimumWidth(0, 220)

        bg.addWidget(self._lbl("Backup Location"), 0, 0)
        bkp_note = QLabel("Default: <log_dir>/db_backups/  (last 10 kept)")
        bkp_note.setStyleSheet("color: #8a93a0; font-size: 11px;")
        bg.addWidget(bkp_note, 0, 1)

        bg.addWidget(self._lbl("Custom Destination"), 1, 0)
        bkp_dest_row = QWidget()
        bkp_dest_h = QHBoxLayout(bkp_dest_row)
        bkp_dest_h.setContentsMargins(0, 0, 0, 0)
        bkp_dest_h.setSpacing(6)
        bkp_dest_inp = self._inp("backup_destination", "", UI.INPUT_W_LG)
        bkp_dest_inp.setPlaceholderText("e.g. D:\\Backups  or  \\\\server\\share  (blank = default)")
        bkp_dest_h.addWidget(bkp_dest_inp, 1)
        bkp_browse2 = QPushButton("Browse…")
        bkp_browse2.setObjectName("btn_sm")
        bkp_browse2.setFixedWidth(UI.BTN_MD_W)
        bkp_browse2.setMinimumHeight(UI.INPUT_H)
        bkp_browse2.clicked.connect(self._browse_backup_dest)
        bkp_dest_h.addWidget(bkp_browse2)
        bg.addWidget(bkp_dest_row, 1, 1)

        self._lbl_last_backup = QLabel("Last backup: checking…")
        self._lbl_last_backup.setStyleSheet("color: #4a5260; font-size: 11px;")
        bg.addWidget(self._lbl_last_backup, 2, 0, 1, 2)

        bkp_btn = QPushButton("Backup Now")
        bkp_btn.setObjectName("btn_sm")
        bkp_btn.setFixedWidth(UI.BTN_PW_W)
        bkp_btn.setMinimumHeight(UI.INPUT_H)
        bkp_btn.clicked.connect(self._run_backup_now)
        bg.addWidget(bkp_btn, 3, 1)

        layout.addWidget(bkp_grp)

        # ── Company Settings ───────────────────────────────────────────────────
        co_grp = QGroupBox("Company Settings  (printed on all PDF reports)")
        cg = QGridLayout(co_grp)
        cg.setSpacing(8)
        cg.setColumnMinimumWidth(0, 220)

        cg.addWidget(self._lbl("Company Name"), 0, 0)
        cg.addWidget(self._inp("company_name", "", UI.INPUT_W_LG), 0, 1)
        cg.addWidget(self._lbl("Company Address"), 1, 0)
        addr_inp = self._inp("company_address", "", UI.INPUT_W_LG)
        addr_inp.setPlaceholderText("e.g. Plot 5, MIDC, Pune – 411 019")
        cg.addWidget(addr_inp, 1, 1)

        layout.addWidget(co_grp)

        # ── Save ──────────────────────────────────────────────────────────────
        save_btn = QPushButton("Save Settings")
        save_btn.setObjectName("save_btn")
        save_btn.setFixedWidth(UI.BTN_SAVE_W)
        save_btn.clicked.connect(self._save_and_lock)
        layout.addWidget(save_btn)
        layout.addStretch()

    # ── populate / extract ────────────────────────────────────────────────────

    def _populate_from_config(self):
        cfg = self._config
        d   = {
            "d1_camera_ip":   cfg.device1.camera_ip,
            "d1_camera_port": str(cfg.device1.camera_port),
            "d1_poll":        str(cfg.device1.poll_interval_s),
            "d2_camera_ip":   cfg.device2.camera_ip,
            "d2_camera_port": str(cfg.device2.camera_port),
            "d2_poll":        str(cfg.device2.poll_interval_s),
            "plc_ip":         cfg.plc.ip,
            "plc_port":       str(cfg.plc.port),
            "plc_hreg_d1":    str(cfg.plc.hreg_device1),
            "plc_hreg_d2":    str(cfg.plc.hreg_device2),
            "plc_pass_val":   str(cfg.plc.pass_val),
            "plc_fail_val":   str(cfg.plc.fail_val),
            # Delay
            "plc_d1_reject_hreg":  str(cfg.plc.d1_reject_hreg),
            "plc_d1_reject_val":   str(cfg.plc.d1_reject_val),
            "plc_d1_trigger_hreg": str(cfg.plc.d1_trigger_hreg),
            "plc_d1_trigger_val":  str(cfg.plc.d1_trigger_val),
            # Timing & status
            "plc_cyl_timing_hreg":  str(cfg.plc.cyl_timing_hreg),
            "plc_cyl_timing_val":   str(cfg.plc.cyl_timing_val),
            "plc_cam1_status_hreg": str(cfg.plc.cam1_status_hreg),
            # Spare
            "plc_spare1_hreg": str(cfg.plc.spare1_hreg), "plc_spare1_val": str(cfg.plc.spare1_val),
            "plc_spare2_hreg": str(cfg.plc.spare2_hreg), "plc_spare2_val": str(cfg.plc.spare2_val),
            "plc_spare3_hreg": str(cfg.plc.spare3_hreg), "plc_spare3_val": str(cfg.plc.spare3_val),
            "plc_spare4_hreg": str(cfg.plc.spare4_hreg), "plc_spare4_val": str(cfg.plc.spare4_val),
            "log_dir":        cfg.general.log_dir,
            "backup_destination": cfg.general.backup_destination,
            "pol_timeout":    str(cfg.policy.timeout_minutes),
            "pol_expiry":     str(cfg.policy.password_expiry_days),
            "pol_attempts":   str(cfg.policy.max_login_attempts),
            "pol_lockout":    str(cfg.policy.lockout_minutes),
            "pol_history":    str(cfg.policy.password_history_count),
            "consec_fail_limit": str(cfg.general.consecutive_fail_limit),
            "company_name":    cfg.company.name,
            "company_address": cfg.company.address,
        }
        for key, val in d.items():
            if key in self._inputs:
                self._inputs[key].setText(val)

    def _extract_to_config(self) -> AppConfig:
        """Read all input fields and return a new AppConfig."""
        from config.settings import DeviceConfig, PLCConfig, GeneralConfig, PolicyConfig, CompanyConfig
        cfg = AppConfig(
            device1 = DeviceConfig(
                camera_ip       = self._inputs["d1_camera_ip"].text().strip(),
                camera_port     = int(self._inputs["d1_camera_port"].text()),
                poll_interval_s = float(self._inputs["d1_poll"].text()),
            ),
            device2 = DeviceConfig(
                camera_ip       = self._inputs["d2_camera_ip"].text().strip(),
                camera_port     = int(self._inputs["d2_camera_port"].text()),
                poll_interval_s = float(self._inputs["d2_poll"].text()),
            ),
            plc = PLCConfig(
                ip           = self._inputs["plc_ip"].text().strip(),
                port         = int(self._inputs["plc_port"].text()),
                hreg_device1 = int(self._inputs["plc_hreg_d1"].text()),
                hreg_device2 = int(self._inputs["plc_hreg_d2"].text()),
                pass_val     = int(self._inputs["plc_pass_val"].text()),
                fail_val     = int(self._inputs["plc_fail_val"].text()),
                # Delay
                d1_reject_hreg  = int(self._inputs["plc_d1_reject_hreg"].text()),
                d1_reject_val   = int(self._inputs["plc_d1_reject_val"].text()),
                d1_trigger_hreg = int(self._inputs["plc_d1_trigger_hreg"].text()),
                d1_trigger_val  = int(self._inputs["plc_d1_trigger_val"].text()),
                # Timing & status
                cyl_timing_hreg  = int(self._inputs["plc_cyl_timing_hreg"].text()),
                cyl_timing_val   = int(self._inputs["plc_cyl_timing_val"].text()),
                cam1_status_hreg = int(self._inputs["plc_cam1_status_hreg"].text()),
                # Spare
                spare1_hreg = int(self._inputs["plc_spare1_hreg"].text()),
                spare1_val  = int(self._inputs["plc_spare1_val"].text()),
                spare2_hreg = int(self._inputs["plc_spare2_hreg"].text()),
                spare2_val  = int(self._inputs["plc_spare2_val"].text()),
                spare3_hreg = int(self._inputs["plc_spare3_hreg"].text()),
                spare3_val  = int(self._inputs["plc_spare3_val"].text()),
                spare4_hreg = int(self._inputs["plc_spare4_hreg"].text()),
                spare4_val  = int(self._inputs["plc_spare4_val"].text()),
            ),
            general = GeneralConfig(
                log_dir               = self._inputs["log_dir"].text().strip(),
                backup_destination    = self._inputs["backup_destination"].text().strip(),
                consecutive_fail_limit= max(1, int(self._inputs["consec_fail_limit"].text())),
            ),
            policy = PolicyConfig(
                # Enforce sane bounds: timeout 1-480 min, attempts 1-10, history 0-24
                timeout_minutes        = max(1, min(480, int(self._inputs["pol_timeout"].text()))),
                password_expiry_days   = max(0, int(self._inputs["pol_expiry"].text())),
                max_login_attempts     = max(1, min(10, int(self._inputs["pol_attempts"].text()))),
                lockout_minutes        = max(1, min(1440, int(self._inputs["pol_lockout"].text()))),
                password_history_count = max(0, min(24, int(self._inputs["pol_history"].text()))),
            ),
            company = CompanyConfig(
                name    = self._inputs["company_name"].text().strip(),
                address = self._inputs["company_address"].text().strip(),
            ),
        )
        return cfg

    # ── slot handlers ─────────────────────────────────────────────────────────

    def _browse_log_dir(self):
        folder = QFileDialog.getExistingDirectory(
            self, "Select Log Folder",
            self._inputs["log_dir"].text())
        if folder:
            self._inputs["log_dir"].setText(folder)
        self._refresh_backup_label()

    def _browse_backup_dest(self):
        folder = QFileDialog.getExistingDirectory(
            self, "Select Backup Destination",
            self._inputs["backup_destination"].text() or
            self._inputs["log_dir"].text())
        if folder:
            self._inputs["backup_destination"].setText(folder)
        self._refresh_backup_label()

    def _refresh_backup_label(self):
        """Update last-backup label from current log_dir."""
        try:
            from cfr21.db_backup import get_last_backup_time
            log_dir = self._inputs.get("log_dir")
            if log_dir:
                last = get_last_backup_time(log_dir.text().strip())
                self._lbl_last_backup.setText(
                    f"Last backup: {last}" if last else "Last backup: None yet"
                )
        except Exception:
            pass

    def _run_backup_now(self):
        """Manually trigger a compliance DB backup."""
        from cfr21.db_backup import run_backup
        log_dir = self._inputs.get("log_dir", None)
        if not log_dir:
            QMessageBox.warning(self.parentWidget(), "No Log Directory",
                                "Please set the log directory first.")
            return
        custom_dest = self._inputs.get("backup_destination", None)
        ok, result = run_backup(
            log_dir.text().strip(),
            custom_dest.text().strip() if custom_dest else "",
        )
        if ok:
            QMessageBox.information(
                self.parentWidget(), "Backup Complete",
                f"Compliance database backed up to:\n{result}"
            )
            self._refresh_backup_label()
        else:
            QMessageBox.critical(
                self.parentWidget(), "Backup Failed", result
            )

    def _on_hreg_set(self, device_id: int):
        key = f"plc_hreg_d{device_id}"
        val = self._inputs[key].text().strip()
        try:
            addr = int(val)
            if addr < 0:
                raise ValueError
            QMessageBox.information(
                self.parentWidget(), "Register Address Set",
                f"Device {device_id} register address set to {addr}.\n"
                f"Applies on next Start Logging.")
        except ValueError:
            QMessageBox.warning(
                self.parentWidget(), "Invalid Address",
                "Register address must be a non-negative integer.")

    def _reset_session_lists(self):
        reply = QMessageBox.question(
            self.parentWidget(),
            "Reset Session Lists",
            "This will permanently clear all saved Batch IDs, Operator IDs "
            "and Product Names from the dropdown lists.\n\nAre you sure?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            from gui.widgets import BATCH_FILE, OPERATOR_FILE, PRODUCT_FILE
            import os, json
            for path in [BATCH_FILE, OPERATOR_FILE, PRODUCT_FILE]:
                try:
                    with open(path, "w") as f:
                        json.dump([], f)
                except Exception:
                    pass
            self.session_lists_reset.emit()
            QMessageBox.information(
                self.parentWidget(), "Done",
                "Session lists cleared successfully.")

    def _save_and_lock(self):
        try:
            new_cfg = self._extract_to_config()
        except ValueError as e:
            QMessageBox.critical(self.parentWidget(), "Error",
                                 f"Invalid value in settings:\n{e}")
            return

        # CFR21: re-authenticate before saving settings (§11.200)
        if self._sm and self._sm.is_logged_in:
            from gui.cfr_dialogs import ReauthDialog, ReasonDialog
            import cfr21.audit_trail as audit

            reauth = ReauthDialog(
                self._sm,
                action_description="save system settings",
                parent=self.parentWidget(),
            )
            if reauth.exec() != ReauthDialog.DialogCode.Accepted:
                return

            reason_dlg = ReasonDialog(
                title  = "Settings Change Reason",
                prompt = "State the reason for changing system settings:",
                parent = self.parentWidget(),
            )
            if reason_dlg.exec() != ReasonDialog.DialogCode.Accepted:
                return
            change_reason = reason_dlg.reason
        else:
            change_reason = None

        if not new_cfg.save():
            QMessageBox.warning(self.parentWidget(), "Save Failed",
                                "Could not write settings file.")
            return

        # CFR21: audit the settings change
        if self._sm and self._sm.is_logged_in:
            audit.log(
                user       = self._sm.current_user,
                action     = audit.ACTION_SETTINGS_CHANGED,
                detail     = "System settings saved via Advanced Settings page.",
                session_id = self._sm.session_id,
                reason     = change_reason,
            )
            self._sm.ping()

        QMessageBox.information(
            self.parentWidget(), "Saved",
            "Settings saved. Changes apply on next Start Logging.")
        self._config = new_cfg
        self.settings_saved.emit(new_cfg)
        self.setCurrentIndex(0)