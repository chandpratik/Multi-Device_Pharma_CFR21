# gui/styles.py
# Application-wide Qt stylesheet (QSS).
# Built with Python variables — change a color or font once, updates everywhere.

# ── Design tokens ─────────────────────────────────────────────────────────────

# Colors
C_PRIMARY       = "#0062a3"
C_PRIMARY_DARK  = "#004f87"
C_PRIMARY_XDARK = "#003d6b"
C_PRIMARY_BG    = "#e8f0f8"
C_PRIMARY_BG2   = "#cde0f5"

C_PASS          = "#1a7a3a"
C_PASS_BG       = "#e8f5ec"
C_PASS_BORDER   = "#a8d8b8"
C_PASS_BG_LIGHT = "#C6EFCE"

C_FAIL          = "#c0392b"
C_FAIL_BG       = "#fdf0ef"
C_FAIL_DARK     = "#922b21"

C_WARN          = "#b05a00"
C_WARN_BG       = "#fef5e8"
C_WARN_BORDER   = "#e8c88a"
C_WARN_BG2      = "#fde4bf"
C_WARN_DARK     = "#7a3e00"

C_BG            = "#eceef1"
C_SURFACE       = "#ffffff"
C_BORDER        = "#d0d4da"
C_BORDER_INPUT  = "#b0b6bf"
C_DIVIDER       = "#e5e7eb"

C_TEXT_PRIMARY  = "#1a1e24"
C_TEXT_BODY     = "#4a5260"
C_TEXT_MUTED    = "#8a93a0"
C_TEXT_WHITE    = "#ffffff"

C_TOPBAR        = "#0062a3"
C_TOPBAR_ALPHA1 = "rgba(0,0,0,0.18)"
C_TOPBAR_ALPHA2 = "rgba(0,0,0,0.35)"
C_TOPBAR_ALPHA3 = "rgba(0,0,0,0.50)"

C_TABLE_ALT     = "#fafbfc"
C_TABLE_BORDER  = "#eef0f3"
C_TABLE_HEADER  = "#f4f5f7"
C_SELECT_BG     = "#e8f0f8"

C_SCROLLBAR     = "#f4f5f7"
C_SCROLLHANDLE  = "#b0b6bf"
C_SCROLLHOVER   = "#8a93a0"

C_DEVICE_HEADER = "#f4f5f7"
C_DEVICE_BORDER = "#d0d4da"

# Fonts
F_SANS = "'IBM Plex Sans', 'Segoe UI', Arial, sans-serif"
F_MONO = "'IBM Plex Mono', 'Consolas', 'Courier New', monospace"

# ── Build QSS ─────────────────────────────────────────────────────────────────

QSS = f"""
* {{
    font-family: {F_SANS};
}}

QMainWindow, QWidget#central {{
    background: {C_BG};
}}

/* ── topbar ── */
QWidget#topbar {{
    background: {C_TOPBAR};
    min-height: 48px;
    max-height: 48px;
}}

QLabel#topbar_title {{
    color: {C_TEXT_WHITE};
    font-size: 14px;
    font-weight: 600;
}}

QLabel#topbar_sub {{
    color: rgba(255,255,255,0.75);
    font-size: 14px;
    font-weight: 300;
}}

QWidget#status_chip {{
    background: {C_TOPBAR_ALPHA1};
    border-radius: 3px;
    padding: 2px 8px;
}}

QLabel#chip_label {{
    color: rgba(255,255,255,0.65);
    font-size: 10px;
    font-weight: 600;
    letter-spacing: 1px;
}}

QLabel#latency_label {{
    color: {C_TEXT_WHITE};
    font-family: {F_MONO};
    font-size: 11px;
    background: {C_TOPBAR_ALPHA1};
    border-radius: 3px;
    padding: 3px 9px;
}}

QLabel#logging_badge {{
    color: {C_TEXT_WHITE};
    font-size: 11px;
    font-weight: 600;
    background: {C_TOPBAR_ALPHA1};
    border-radius: 3px;
    padding: 3px 10px;
    letter-spacing: 0.5px;
}}

/* ── topbar icon buttons (fullscreen) ── */
QPushButton#topbar_btn {{
    background: {C_TOPBAR_ALPHA1};
    border: none;
    border-radius: 3px;
    color: {C_TEXT_WHITE};
    font-size: 16px;
}}
QPushButton#topbar_btn:hover    {{ background: {C_TOPBAR_ALPHA2}; }}
QPushButton#topbar_btn:pressed  {{ background: {C_TOPBAR_ALPHA3}; }}

/* ── bottom bar (replaces sidebar) ── */
QWidget#bottom_bar {{
    background: {C_SURFACE};
    border-top: 1px solid {C_BORDER};
    min-height: 52px;
    max-height: 52px;
}}

QLabel#sidebar_section_label {{
    color: {C_TEXT_MUTED};
    font-size: 10px;
    font-weight: 600;
    letter-spacing: 1.2px;
    padding: 0px 6px;
}}

QFrame#bottom_sep {{
    background: {C_BORDER};
    max-width: 1px;
    min-width: 1px;
    margin: 8px 0px;
}}

/* ── bottom bar buttons ── */
QPushButton#sbtn {{
    background: transparent;
    border: none;
    border-radius: 4px;
    color: {C_TEXT_BODY};
    font-size: 12px;
    font-weight: 500;
    text-align: center;
    padding: 0px 16px;
    min-height: 36px;
    min-width: 110px;
}}
QPushButton#sbtn:hover {{
    background: {C_PRIMARY_BG};
    color: {C_PRIMARY};
}}
QPushButton#sbtn:pressed {{
    background: {C_PRIMARY_BG2};
    color: {C_PRIMARY_DARK};
}}
QPushButton#sbtn:disabled {{
    color: #c0c8d4;
}}

QPushButton#sbtn_active {{
    background: {C_PRIMARY_BG};
    border: none;
    border-radius: 4px;
    color: {C_PRIMARY};
    font-size: 12px;
    font-weight: 600;
    text-align: center;
    padding: 0px 16px;
    min-height: 36px;
    min-width: 110px;
}}

QPushButton#sbtn_danger {{
    background: transparent;
    border: none;
    border-radius: 4px;
    color: {C_FAIL};
    font-size: 12px;
    font-weight: 500;
    text-align: center;
    padding: 0px 16px;
    min-height: 36px;
    min-width: 110px;
}}
QPushButton#sbtn_danger:hover {{
    background: {C_FAIL_BG};
}}
QPushButton#sbtn_danger:pressed {{
    background: #f9d6d3;
    color: {C_FAIL_DARK};
}}
QPushButton#sbtn_danger:disabled {{
    color: #c0c8d4;
}}

QPushButton#sbtn_warn {{
    background: transparent;
    border: none;
    border-radius: 4px;
    color: {C_WARN};
    font-size: 12px;
    font-weight: 500;
    text-align: center;
    padding: 0px 16px;
    min-height: 36px;
    min-width: 110px;
}}
QPushButton#sbtn_warn:hover {{
    background: {C_WARN_BG};
}}
QPushButton#sbtn_warn:pressed {{
    background: {C_WARN_BG2};
    color: {C_WARN_DARK};
}}
QPushButton#sbtn_warn:disabled {{
    color: #c0c8d4;
}}

QFrame#sidebar_sep {{
    background: {C_BORDER};
    max-width: 1px;
    min-width: 1px;
    margin: 0px 2px;
}}

/* ── tab bar ── */
QTabWidget::pane {{
    border: none;
    background: {C_BG};
}}
QTabBar::tab {{
    background: {C_SURFACE};
    color: {C_TEXT_MUTED};
    font-size: 13px;
    font-weight: 400;
    padding: 10px 16px;
    border: none;
    border-bottom: 2px solid transparent;
}}
QTabBar::tab:selected {{
    color: {C_PRIMARY};
    border-bottom: 2px solid {C_PRIMARY};
    font-weight: 500;
}}
QTabBar::tab:hover:!selected {{
    color: {C_TEXT_BODY};
    background: #f4f5f7;
}}

/* ── PLC chip toggle button ── */
QPushButton#chip_btn_connect {{
    background: rgba(255,255,255,0.20);
    border: 1px solid rgba(255,255,255,0.45);
    border-radius: 4px;
    color: {C_TEXT_WHITE};
    font-size: 12px;
    font-weight: 700;
    letter-spacing: 0.3px;
    min-width: 90px;
    min-height: 28px;
}}
QPushButton#chip_btn_connect:hover {{
    background: rgba(255,255,255,0.32);
    border-color: rgba(255,255,255,0.70);
}}
QPushButton#chip_btn_connect:pressed {{
    background: rgba(255,255,255,0.45);
}}
QPushButton#chip_btn_disconnect {{
    background: rgba(26,122,58,0.45);
    border: 1px solid rgba(76,223,124,0.65);
    border-radius: 4px;
    color: #7effa8;
    font-size: 12px;
    font-weight: 700;
    letter-spacing: 0.3px;
    min-width: 90px;
    min-height: 28px;
}}
QPushButton#chip_btn_disconnect:hover {{
    background: rgba(26,122,58,0.65);
    border-color: rgba(76,223,124,0.9);
}}

/* ── device panel container ── */
QWidget#device_panel {{
    background: {C_BG};
    border: none;
}}

QFrame#device_vsep {{
    background: {C_BORDER};
    max-width: 1px;
    min-width: 1px;
}}

/* ── device header ── */
QWidget#device_header {{
    background: {C_TOPBAR};
    border-bottom: none;
    min-height: 48px;
    max-height: 48px;
}}

QLabel#device_header_label {{
    color: {C_TEXT_WHITE};
    font-size: 13px;
    font-weight: 600;
    letter-spacing: 0.3px;
}}

/* ── camera connect/disconnect button (90x30, on blue header) ── */
QPushButton#btn_cam_disconnected {{
    background: rgba(255,255,255,0.20);
    border: 1px solid rgba(255,255,255,0.45);
    border-radius: 4px;
    color: {C_TEXT_WHITE};
    font-size: 12px;
    font-weight: 700;
    letter-spacing: 0.3px;
    min-width: 90px;
    min-height: 30px;
}}
QPushButton#btn_cam_disconnected:hover {{
    background: rgba(255,255,255,0.32);
    border-color: rgba(255,255,255,0.70);
}}
QPushButton#btn_cam_disconnected:pressed {{
    background: rgba(255,255,255,0.45);
}}

QPushButton#btn_cam_connected {{
    background: rgba(26,122,58,0.45);
    border: 1px solid rgba(76,223,124,0.65);
    border-radius: 4px;
    color: #7effa8;
    font-size: 12px;
    font-weight: 700;
    letter-spacing: 0.3px;
    min-width: 90px;
    min-height: 30px;
}}
QPushButton#btn_cam_connected:hover {{
    background: rgba(26,122,58,0.65);
    border-color: rgba(76,223,124,0.9);
}}
QPushButton#btn_cam_connected:pressed {{
    background: rgba(26,122,58,0.80);
}}

/* ── master bar ── */
QWidget#master_bar {{
    background: {C_WARN_BG};
    border-bottom: 1px solid {C_WARN_BORDER};
    min-height: 44px;
    max-height: 44px;
}}
QLabel#master_bar_label {{
    color: {C_WARN};
    font-size: 10px;
    font-weight: 600;
    letter-spacing: 1px;
}}
QLabel#master_code_value {{
    color: {C_PASS};
    font-family: {F_MONO};
    font-size: 12px;
    font-weight: 500;
    background: {C_PASS_BG};
    border: 1px solid {C_PASS_BORDER};
    border-radius: 2px;
    padding: 1px 6px;
}}
QLabel#master_code_warn {{
    color: {C_WARN};
    font-family: {F_MONO};
    font-size: 12px;
    font-weight: 500;
    background: {C_WARN_BG};
    border: 1px solid {C_WARN_BORDER};
    border-radius: 2px;
    padding: 1px 6px;
}}

/* ── teach buttons inside master bar ── */
QPushButton#btn_teach {{
    background: {C_WARN_BG};
    border: 1px solid {C_WARN_BORDER};
    border-radius: 2px;
    color: {C_WARN};
    font-size: 11px;
    font-weight: 600;
    padding: 0px 8px;
    min-height: 24px;
    max-height: 24px;
    min-width: 70px;
}}
QPushButton#btn_teach:hover {{
    background: {C_WARN_BG2};
    border-color: {C_WARN};
}}
QPushButton#btn_teach:disabled {{
    color: #c0c8d4;
    border-color: #d8dce2;
    background: #f4f5f7;
}}

QPushButton#btn_clear {{
    background: {C_SURFACE};
    border: 1px solid {C_BORDER_INPUT};
    border-radius: 2px;
    color: {C_TEXT_BODY};
    font-size: 11px;
    font-weight: 500;
    padding: 0px 8px;
    min-height: 24px;
    max-height: 24px;
    min-width: 60px;
}}
QPushButton#btn_clear:hover {{
    background: {C_FAIL_BG};
    border-color: {C_FAIL};
    color: {C_FAIL};
}}
QPushButton#btn_clear:disabled {{
    color: #c0c8d4;
    border-color: #d8dce2;
}}

/* ── KPI strip ── */
QWidget#kpi_strip, QWidget#live_strip, QWidget#info_strip {{
    background: {C_SURFACE};
}}
QFrame#kpi_sep {{
    background: {C_BORDER};
    max-width: 1px;
    min-width: 1px;
}}
QLabel#kpi_label {{
    color: {C_TEXT_MUTED};
    font-size: 9px;
    font-weight: 600;
    letter-spacing: 1px;
}}
QLabel#kpi_value_total {{ color: {C_TEXT_PRIMARY}; font-size: 24px; font-weight: 600; }}
QLabel#kpi_value_pass  {{ color: {C_PASS};         font-size: 24px; font-weight: 600; }}
QLabel#kpi_value_fail  {{ color: {C_FAIL};         font-size: 24px; font-weight: 600; }}
QLabel#kpi_sub         {{ color: {C_TEXT_MUTED};   font-size: 10px; }}

/* ── live status indicator ── */
QWidget#live_status_pass {{
    background: {C_PASS_BG};
    border-left: 4px solid {C_PASS};
    min-width: 160px;
    max-width: 160px;
}}
QWidget#live_status_fail {{
    background: {C_FAIL_BG};
    border-left: 4px solid {C_FAIL};
    min-width: 160px;
    max-width: 160px;
}}
QLabel#live_status_text_pass {{
    color: {C_PASS};
    font-size: 17px;
    font-weight: 600;
    letter-spacing: 2px;
}}
QLabel#live_status_text_fail {{
    color: {C_FAIL};
    font-size: 17px;
    font-weight: 600;
    letter-spacing: 2px;
}}

/* ── session info strip (full-width, above device panels) ── */
QWidget#session_strip {{
    background: {C_SURFACE};
    border-bottom: 1px solid {C_BORDER};
    min-height: 46px;
    max-height: 46px;
}}
QWidget#session_cell {{
    background: transparent;
}}
QLabel#session_cell_label {{
    color: {C_TEXT_MUTED};
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 1.2px;
}}
QLabel#session_cell_value {{
    color: {C_PRIMARY};
    font-family: {F_MONO};
    font-size: 15px;
    font-weight: 500;
}}

/* ── table ── */
QTableWidget {{
    background: {C_SURFACE};
    alternate-background-color: {C_TABLE_ALT};
    border: none;
    gridline-color: {C_TABLE_BORDER};
    font-size: 12px;
    color: {C_TEXT_BODY};
    selection-background-color: {C_SELECT_BG};
    selection-color: {C_TEXT_PRIMARY};
}}
QTableWidget::item {{
    padding: 4px 8px;
    border-bottom: 1px solid {C_TABLE_BORDER};
}}
QTableWidget::item:hover {{ background: {C_SELECT_BG}; }}
QHeaderView::section {{
    background: {C_TABLE_HEADER};
    color: {C_TEXT_MUTED};
    font-size: 9px;
    font-weight: 600;
    letter-spacing: 1px;
    padding: 5px 8px;
    border: none;
    border-bottom: 1px solid {C_BORDER};
    border-right: 1px solid {C_BORDER};
    text-transform: uppercase;
}}
QHeaderView::section:last {{ border-right: none; }}

/* ── scrollbar ── */
QScrollBar:vertical {{
    background: {C_SCROLLBAR};
    width: 7px;
    border: none;
}}
QScrollBar::handle:vertical {{
    background: {C_SCROLLHANDLE};
    border-radius: 3px;
    min-height: 20px;
}}
QScrollBar::handle:vertical:hover {{ background: {C_SCROLLHOVER}; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0px; }}

/* ── camera views tab ── */
QLabel#cam_label {{
    color: {C_TEXT_MUTED};
    font-size: 10px;
    font-weight: 600;
    letter-spacing: 1px;
    padding: 4px 10px;
    background: {C_DEVICE_HEADER};
    border-bottom: 1px solid {C_DEVICE_BORDER};
}}
QLabel#cam_banner_pass {{
    background: {C_PASS};
    color: {C_TEXT_WHITE};
    font-size: 18px;
    font-weight: 700;
    letter-spacing: 2px;
    min-height: 56px;
    max-height: 56px;
}}
QLabel#cam_banner_fail {{
    background: {C_FAIL};
    color: {C_TEXT_WHITE};
    font-size: 18px;
    font-weight: 700;
    letter-spacing: 2px;
    min-height: 56px;
    max-height: 56px;
}}
QLabel#cam_banner_idle {{
    background: {C_TEXT_BODY};
    color: {C_TEXT_WHITE};
    font-size: 18px;
    font-weight: 700;
    letter-spacing: 2px;
    min-height: 56px;
    max-height: 56px;
}}

/* ── batch info tab ── */
QLabel#bi_field_label {{
    color: {C_TEXT_MUTED};
    font-size: 9px;
    font-weight: 600;
    letter-spacing: 1px;
}}
QLabel#bi_field_value {{
    color: {C_TEXT_PRIMARY};
    font-family: {F_MONO};
    font-size: 12px;
}}

/* ── settings / lock ── */
QWidget#lock_widget {{ background: {C_BG}; }}
QLabel#lock_icon    {{ font-size: 36px; color: {C_TEXT_MUTED}; }}
QLabel#lock_title   {{ color: {C_TEXT_BODY}; font-size: 16px; font-weight: 600; }}
QLabel#lock_sub     {{ color: {C_TEXT_MUTED}; font-size: 13px; }}

QLineEdit#lock_input {{
    background: {C_SURFACE};
    border: 1px solid {C_BORDER_INPUT};
    border-radius: 2px;
    color: {C_TEXT_PRIMARY};
    font-size: 13px;
    padding: 6px 12px;
    min-width: 200px;
    max-width: 200px;
}}
QLineEdit#lock_input:focus {{ border-color: {C_PRIMARY}; }}

QPushButton#lock_btn {{
    background: {C_PRIMARY};
    border: none;
    border-radius: 2px;
    color: {C_TEXT_WHITE};
    font-size: 13px;
    font-weight: 500;
    padding: 7px 28px;
    min-width: 140px;
    max-width: 140px;
    min-height: 44px;
}}
QPushButton#lock_btn:hover   {{ background: {C_PRIMARY_DARK}; }}
QPushButton#lock_btn:pressed {{ background: {C_PRIMARY_XDARK}; }}

QGroupBox {{
    background: {C_SURFACE};
    border: 1px solid {C_BORDER};
    border-radius: 3px;
    font-size: 10px;
    font-weight: 600;
    color: {C_TEXT_MUTED};
    letter-spacing: 1px;
    margin-top: 10px;
    padding-top: 8px;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    subcontrol-position: top left;
    padding: 0 6px;
    left: 10px;
    text-transform: uppercase;
}}

QLineEdit#settings_input {{
    background: {C_SURFACE};
    border: 1px solid {C_BORDER_INPUT};
    border-radius: 2px;
    color: {C_TEXT_PRIMARY};
    font-family: {F_MONO};
    font-size: 13px;
    padding: 4px 8px;
    min-height: 44px;
}}
QLineEdit#settings_input:focus {{ border-color: {C_PRIMARY}; }}

QLabel#settings_label {{
    color: {C_TEXT_BODY};
    font-size: 13px;
    font-weight: 500;
}}

QPushButton#btn_sm {{
    background: {C_SURFACE};
    border: 1px solid {C_BORDER_INPUT};
    border-radius: 2px;
    color: {C_TEXT_BODY};
    font-size: 13px;
    font-weight: 500;
    padding: 0px 12px;
    min-height: 32px;
    max-height: 32px;
}}
QPushButton#btn_sm:hover {{
    background: {C_PRIMARY_BG};
    border-color: {C_PRIMARY};
    color: {C_PRIMARY};
}}
QPushButton#btn_sm:pressed {{
    background: {C_PRIMARY_BG2};
    border-color: {C_PRIMARY_DARK};
    color: {C_PRIMARY_DARK};
}}

QPushButton#save_btn {{
    background: {C_PRIMARY};
    border: none;
    border-radius: 2px;
    color: {C_TEXT_WHITE};
    font-size: 13px;
    font-weight: 500;
    padding: 6px 20px;
    min-height: 44px;
}}
QPushButton#save_btn:hover   {{ background: {C_PRIMARY_DARK}; }}
QPushButton#save_btn:pressed {{ background: {C_PRIMARY_XDARK}; }}

/* ── session dialog ── */
QComboBox#batch_combo {{
    background: {C_SURFACE};
    border: 1px solid {C_BORDER_INPUT};
    border-radius: 2px;
    color: {C_TEXT_PRIMARY};
    font-family: {F_MONO};
    font-size: 13px;
    padding: 0px 8px;
    min-width: 180px;
    min-height: 32px;
    max-height: 32px;
}}
QComboBox#batch_combo:focus {{ border-color: {C_PRIMARY}; }}

QPushButton#btn_primary {{
    background: {C_PRIMARY};
    border: none;
    border-radius: 2px;
    color: {C_TEXT_WHITE};
    font-size: 13px;
    font-weight: 500;
    padding: 6px 20px;
    min-height: 44px;
}}
QPushButton#btn_primary:hover   {{ background: {C_PRIMARY_DARK}; }}
QPushButton#btn_primary:pressed {{ background: {C_PRIMARY_XDARK}; }}

/* ── visualization tab cards ── */
QWidget#viz_card {{
    background: {C_SURFACE};
    border-radius: 2px;
}}

/* ── help tab ── */
QScrollArea {{ border: none; background: {C_BG}; }}
"""