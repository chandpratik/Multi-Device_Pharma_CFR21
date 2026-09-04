# gui/device_panel.py
# DevicePanel — self-contained widget for one camera device.
#
# Changes from original:
#   - Device header: camera connect button (two-line, label + IP) replaces text label
#   - KPI strip: equally spaced with no addStretch — PASS/FAIL indicator included
#   - Info strip: Operator ID removed — Batch ID + Product Name only
#   - No sidebar connect buttons needed — device header handles connect/disconnect

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QFrame, QTableWidget, QTableWidgetItem, QHeaderView,
)
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor, QBrush, QFont

try:
    from PyQt6.QtWidgets import QScroller
    _SCROLLER_OK = True
except ImportError:
    _SCROLLER_OK = False

from core.models import ReadRecord
from gui.ui_constants import UI


def _enable_touch_scroll(widget):
    if not _SCROLLER_OK:
        return
    QScroller.grabGesture(
        widget.viewport(),
        QScroller.ScrollerGestureType.TouchGesture,
    )


class DevicePanel(QWidget):

    # ── signals emitted to MainWindow ─────────────────────────────────────────
    sig_connect_requested    = pyqtSignal(int)
    sig_disconnect_requested = pyqtSignal(int)
    sig_arm_teach            = pyqtSignal(int)
    sig_clear_master         = pyqtSignal(int)

    # ── signals received from AppController ───────────────────────────────────
    sig_read_logged       = pyqtSignal(object)
    sig_live_sample       = pyqtSignal(object, bool, object)
    sig_teach_done        = pyqtSignal(str)
    sig_consec_fail       = pyqtSignal(int)
    sig_camera_disconnect = pyqtSignal()

    def __init__(self, device_id: int, camera_ip: str = "", parent=None):
        super().__init__(parent)
        self.device_id  = device_id
        self._camera_ip = camera_ip
        self.setObjectName("device_panel")

        self._pass_count  = 0
        self._fail_count  = 0
        self._total_count = 0
        self._connected   = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        layout.addWidget(self._build_device_header())
        layout.addWidget(self._hsep())
        layout.addWidget(self._build_master_bar())
        layout.addWidget(self._hsep())
        layout.addWidget(self._build_kpi_strip())
        layout.addWidget(self._hsep())
        layout.addWidget(self._build_table(), 1)

        self.sig_read_logged.connect(self._on_read_logged)
        self.sig_teach_done.connect(self._on_teach_done)
        self.sig_consec_fail.connect(self._on_consec_fail)
        self.sig_camera_disconnect.connect(self._on_camera_disconnect)

    # ── builders ──────────────────────────────────────────────────────────────

    def _hsep(self) -> QFrame:
        f = QFrame()
        f.setFrameShape(QFrame.Shape.HLine)
        f.setStyleSheet("background: #d0d4da; max-height:1px;")
        return f

    def _header_text(self, connected: bool) -> str:
        ip = self._camera_ip or "─ ─ ─"
        if connected:
            ip_html = (f'<span style="color:#4cdf7c; '
                       f'font-family:\'IBM Plex Mono\',monospace; font-size:12px;">'
                       f'● {ip}</span>')
        else:
            ip_html = (f'<span style="color:rgba(255,255,255,0.70); '
                       f'font-family:\'IBM Plex Mono\',monospace; font-size:12px;">'
                       f'{ip}</span>')
        return (f'<span style="font-size:13px; font-weight:600; color:#ffffff;">'
                f'CAMERA {self.device_id}</span>'
                f'&nbsp;&nbsp;&nbsp;{ip_html}')

    def _build_device_header(self) -> QWidget:
        """
        Device header: device label + camera IP on the left,
        compact Connect/Disconnect button on the right.
        Clean and professional — no full-width banner button.
        """
        bar = QWidget()
        bar.setObjectName("device_header")
        bar.setFixedHeight(UI.DEVICE_HEADER_H)
        h = QHBoxLayout(bar)
        h.setContentsMargins(12, 0, 16, 0)
        h.setSpacing(8)

        # Left — device label + IP on same line (HTML for mixed styling)
        self._lbl_cam_ip = QLabel()
        self._lbl_cam_ip.setObjectName("device_header_label")
        self._lbl_cam_ip.setText(self._header_text(False))
        h.addWidget(self._lbl_cam_ip, 1)

        # Right — connect button, full height of header
        self.btn_cam = QPushButton("Connect")
        self.btn_cam.setObjectName("btn_cam_disconnected")
        self.btn_cam.setFixedSize(90, 30)
        self.btn_cam.clicked.connect(self._on_connect_clicked)
        h.addWidget(self.btn_cam)

        return bar

    def _build_master_bar(self) -> QWidget:
        bar = QWidget()
        bar.setObjectName("master_bar")
        bar.setFixedHeight(44)
        h = QHBoxLayout(bar)
        h.setContentsMargins(10, 0, 8, 0)
        h.setAlignment(Qt.AlignmentFlag.AlignVCenter)
        h.setSpacing(8)

        lbl = QLabel("MASTER CODE")
        lbl.setObjectName("master_bar_label")
        h.addWidget(lbl)

        self.lbl_master_code = QLabel("– (not set)")
        self.lbl_master_code.setObjectName("master_code_value")
        h.addWidget(self.lbl_master_code)

        h.addStretch()

        self.btn_teach = QPushButton("▶  Teach")
        self.btn_teach.setObjectName("btn_teach")
        self.btn_teach.setFixedWidth(UI.TEACH_BTN_W)
        self.btn_teach.setFixedHeight(24)
        self.btn_teach.setEnabled(False)
        self.btn_teach.clicked.connect(
            lambda: self.sig_arm_teach.emit(self.device_id))
        h.addWidget(self.btn_teach)

        self.btn_clear = QPushButton("✕  Clear")
        self.btn_clear.setObjectName("btn_clear")
        self.btn_clear.setFixedWidth(UI.CLEAR_BTN_W)
        self.btn_clear.setFixedHeight(24)
        self.btn_clear.setEnabled(False)
        self.btn_clear.clicked.connect(
            lambda: self.sig_clear_master.emit(self.device_id))
        h.addWidget(self.btn_clear)


        return bar


    def _build_kpi_strip(self) -> QWidget:
        """
        Equally spaced KPI cells + PASS/FAIL indicator.
        No addStretch — all cells share equal width via stretch factor 1.
        """
        strip = QWidget()
        strip.setObjectName("live_strip")
        strip.setFixedHeight(UI.LIVE_STRIP_H)
        h = QHBoxLayout(strip)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(0)

        def kpi_cell(label, obj_name, sub=""):
            cell = QWidget()
            cell.setObjectName("kpi_strip")
            cv = QVBoxLayout(cell)
            cv.setContentsMargins(8, 6, 8, 6)
            cv.setSpacing(0)
            lbl = QLabel(label.upper())
            lbl.setObjectName("kpi_label")
            val = QLabel("0")
            val.setObjectName(obj_name)
            s = QLabel(sub)
            s.setObjectName("kpi_sub")
            cv.addWidget(lbl)
            cv.addWidget(val)
            cv.addWidget(s)
            return cell, val, s

        def vsep():
            f = QFrame()
            f.setObjectName("kpi_sep")
            f.setFrameShape(QFrame.Shape.VLine)
            return f

        c1, self.lbl_pass,  self.lbl_pass_pct = kpi_cell(
            "Pass",           "kpi_value_pass",  "0.0 %")
        c2, self.lbl_fail,  self.lbl_fail_pct = kpi_cell(
            "Fail / No Read", "kpi_value_fail",  "0.0 %")
        c3, self.lbl_total, _                 = kpi_cell(
            "Total Reads",    "kpi_value_total", "This batch")

        # Live PASS/FAIL status box
        status_cell = QWidget()
        status_cell.setObjectName("live_status_pass")
        sv = QVBoxLayout(status_cell)
        sv.setContentsMargins(0, 0, 0, 0)
        self.lbl_live_status = QLabel("–")
        self.lbl_live_status.setObjectName("live_status_text_pass")
        self.lbl_live_status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        sv.addWidget(self.lbl_live_status)
        self.live_status_widget = status_cell

        # All 4 cells equal stretch — no addStretch at end
        for i, c in enumerate([c1, c2, c3, status_cell]):
            h.addWidget(c, 1)
            if i < 3:
                h.addWidget(vsep())

        return strip

    def _build_table(self) -> QTableWidget:
        self.table = QTableWidget()
        self.table.setColumnCount(4)
        self.table.setHorizontalHeaderLabels(
            ["#", "Time", "Pharma Code", "Status"])
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.verticalHeader().setVisible(False)
        hdr = self.table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)
        self.table.setColumnWidth(3, 75)
        self.table.setFont(QFont("IBM Plex Mono", 10))
        _enable_touch_scroll(self.table)
        return self.table

    # ── public API ────────────────────────────────────────────────────────────

    def update_camera_ip(self, ip: str):
        self._camera_ip = ip
        self._lbl_cam_ip.setText(self._header_text(self._connected))

    def reset_counters(self):
        self._pass_count = self._fail_count = self._total_count = 0
        self._update_kpi()

    def restore_counters(self, passes: int, fails: int):
        """
        Restore counters from a previous session's WAL after crash/restart.
        Sets counts to the replayed values so the display continues
        from where the last session left off rather than starting at zero.
        """
        self._pass_count  = passes
        self._fail_count  = fails
        self._total_count = passes + fails
        self._update_kpi()

    def _update_kpi(self):
        """Refresh the KPI label display from current counter values."""
        total = self._total_count
        pct   = f"{self._pass_count / total * 100:.1f}" if total else "0.0"
        fpct  = f"{self._fail_count / total * 100:.1f}" if total else "0.0"
        self.lbl_total.setText(str(total))
        self.lbl_pass.setText(str(self._pass_count))
        self.lbl_fail.setText(str(self._fail_count))
        self.lbl_pass_pct.setText(f"{pct} %")
        self.lbl_fail_pct.setText(f"{fpct} %")

    def _reset_display(self):
        """Full visual reset — clears counters, live status and table."""
        self.lbl_live_status.setText("–")
        self.lbl_live_status.setObjectName("live_status_text_pass")
        self.live_status_widget.setObjectName("live_status_pass")
        self.table.setRowCount(0)

    def set_logging_active(self, active: bool):
        self.btn_teach.setEnabled(active)
        self.btn_clear.setEnabled(active)


    def set_connected(self, connected: bool):
        self._connected = connected
        self._lbl_cam_ip.setText(self._header_text(connected))
        self._lbl_cam_ip.setStyleSheet("")  # HTML handles color
        if connected:
            self.btn_cam.setText("Disconnect")
            self.btn_cam.setObjectName("btn_cam_connected")
        else:
            self.btn_cam.setText("Connect")
            self.btn_cam.setObjectName("btn_cam_disconnected")
        self.btn_cam.style().unpolish(self.btn_cam)
        self.btn_cam.style().polish(self.btn_cam)

    # ── slot handlers ─────────────────────────────────────────────────────────

    def _on_connect_clicked(self):
        if self._connected:
            self.sig_disconnect_requested.emit(self.device_id)
        else:
            self.sig_connect_requested.emit(self.device_id)

    def _on_read_logged(self, rec: ReadRecord):
        is_pass = rec.status == "PASS"
        self._total_count += 1
        if is_pass:
            self._pass_count += 1
        else:
            self._fail_count += 1

        self._update_kpi()

        self.lbl_live_status.setText("✓  PASS" if is_pass else "✕  FAIL")
        self.lbl_live_status.setObjectName(
            "live_status_text_pass" if is_pass else "live_status_text_fail")
        self.live_status_widget.setObjectName(
            "live_status_pass" if is_pass else "live_status_fail")
        self.live_status_widget.style().unpolish(self.live_status_widget)
        self.live_status_widget.style().polish(self.live_status_widget)
        self.lbl_live_status.style().unpolish(self.lbl_live_status)
        self.lbl_live_status.style().polish(self.lbl_live_status)

        row = self.table.rowCount()
        self.table.insertRow(row)
        bg = QColor("#e8f5ec") if is_pass else QColor("#fdf0ef")
        for col, text in enumerate([
            str(rec.read_id),
            rec.timestamp.strftime("%H:%M:%S"),
            rec.raw_data,
            rec.status,
        ]):
            item = QTableWidgetItem(text)
            item.setBackground(QBrush(bg))
            if col == 2:
                item.setFont(QFont("IBM Plex Mono", 10))
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            if col == 3:
                item.setForeground(QBrush(
                    QColor("#1a7a3a") if is_pass else QColor("#c0392b")))
                item.setFont(QFont("IBM Plex Sans", 10, QFont.Weight.Bold))
            self.table.setItem(row, col, item)

        self.table.verticalScrollBar().setValue(self.table.verticalScrollBar().maximum())

    def _on_teach_done(self, master_code: str):
        self.lbl_master_code.setText(master_code)
        self.lbl_master_code.setObjectName("master_code_value")
        self.lbl_master_code.style().unpolish(self.lbl_master_code)
        self.lbl_master_code.style().polish(self.lbl_master_code)
        self.btn_teach.setEnabled(True)

    def _on_consec_fail(self, count: int):
        from PyQt6.QtWidgets import QMessageBox
        QMessageBox.critical(
            self,
            f"⚠  Device {self.device_id} — Consecutive Fail Alarm",
            f"{count} consecutive FAILs on Device {self.device_id}!\n\n"
            f"Check the product and camera, then acknowledge to continue.")

    def _on_camera_disconnect(self):
        self.set_connected(False)
        self.btn_teach.setEnabled(False)
        self.btn_clear.setEnabled(False)
        from PyQt6.QtWidgets import QMessageBox
        QMessageBox.critical(
            self,
            f"⚠  Device {self.device_id} — Camera Disconnected",
            f"Camera {self.device_id} connection was lost during logging.\n\n"
            f"Logging has been suspended for this device.\n\n"
            f"Recovery steps:\n"
            f"  1. Check the camera cable and network\n"
            f"  2. Click Stop Logging to close the batch\n"
            f"  3. Click the CAM {self.device_id} button to reconnect\n"
            f"  4. Click Start Logging to resume")

    def arm_teach_visual(self):
        self.lbl_master_code.setText("⏳ Scan to teach…")
        self.lbl_master_code.setObjectName("master_code_warn")
        self.lbl_master_code.style().unpolish(self.lbl_master_code)
        self.lbl_master_code.style().polish(self.lbl_master_code)
        self.btn_teach.setEnabled(False)

    def clear_master_visual(self):
        self.lbl_master_code.setText("– (not set)")
        self.lbl_master_code.setObjectName("master_code_value")
        self.lbl_master_code.style().unpolish(self.lbl_master_code)
        self.lbl_master_code.style().polish(self.lbl_master_code)
        self.btn_teach.setEnabled(True)