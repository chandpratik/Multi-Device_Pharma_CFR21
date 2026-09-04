# gui/ui_constants.py
# All hardcoded UI sizing values in one place.
# Target hardware: 10.4" touchscreen, 1024x728 resolution.

class UI:

    # ── Window ────────────────────────────────────────────────────────────────
    WINDOW_W        = 1024
    WINDOW_H        = 728
    WINDOW_MIN_W    = 900
    WINDOW_MIN_H    = 600

    # ── Topbar ────────────────────────────────────────────────────────────────
    TOPBAR_H        = 48
    TOPBAR_BTN_SIZE = 28
    BTN_ICON_SIZE   = 28

    # ── Bottom bar (replaces sidebar) ─────────────────────────────────────────
    BOTTOM_BAR_H    = 52

    # ── Touch targets ─────────────────────────────────────────────────────────
    TOUCH_H         = 44
    SBTN_H          = 44

    # ── Device panel ──────────────────────────────────────────────────────────
    DEVICE_HEADER_H = 48        # taller — camera button lives here
    MASTER_BAR_H    = 36
    LIVE_STRIP_H    = 68
    INFO_STRIP_H    = 48

    # ── Camera connect button inside device header ────────────────────────────
    CAM_BTN_W       = 160
    CAM_BTN_H       = 38

    # ── Input fields ──────────────────────────────────────────────────────────
    INPUT_H         = 44
    INPUT_W_XS      = 80
    INPUT_W_SM      = 100
    INPUT_W_MD      = 160
    INPUT_W_LG      = 200

    # ── Buttons ───────────────────────────────────────────────────────────────
    BTN_SM_W        = 60
    BTN_MD_W        = 80
    BTN_SET_W       = 55
    BTN_PW_W        = 120
    BTN_SAVE_W      = 200

    # ── Live status indicator ─────────────────────────────────────────────────
    LIVE_STATUS_W   = 130

    # ── Teach buttons ─────────────────────────────────────────────────────────
    TEACH_BTN_W     = 80
    CLEAR_BTN_W     = 70

    # ── Camera Views ──────────────────────────────────────────────────────────
    CAM_BANNER_H    = 56

    # ── Chip ──────────────────────────────────────────────────────────────────
    CHIP_PADDING_H  = 3
    CHIP_PADDING_W  = 8
