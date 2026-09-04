# gui/widgets.py
# Reusable GUI helper functions and small widget factories.

import os
import sys
from PyQt6.QtWidgets import QPushButton, QLabel, QFrame
from PyQt6.QtGui import QFontDatabase

_HERE = os.path.dirname(os.path.abspath(__file__))

if getattr(sys, 'frozen', False):
    _DATA_ROOT   = os.path.dirname(sys.executable)
    _BUNDLE_ROOT = sys._MEIPASS
else:
    _DATA_ROOT   = os.path.dirname(_HERE)
    _BUNDLE_ROOT = os.path.dirname(_HERE)

BATCH_FILE    = os.path.join(_DATA_ROOT, "batch_ids.json")
OPERATOR_FILE = os.path.join(_DATA_ROOT, "operator_ids.json")
PRODUCT_FILE  = os.path.join(_DATA_ROOT, "product_names.json")
FONTS_DIR     = os.path.join(_BUNDLE_ROOT, "fonts")

IBM_PLEX_SANS_FILES = [
    "IBMPlexSans-Light.ttf",
    "IBMPlexSans-Regular.ttf",
    "IBMPlexSans-Medium.ttf",
    "IBMPlexSans-SemiBold.ttf",
]
IBM_PLEX_MONO_FILES = [
    "IBMPlexMono-Regular.ttf",
    "IBMPlexMono-Medium.ttf",
]


def load_bundled_fonts() -> str:
    loaded_families = []
    missing = []
    for fname in IBM_PLEX_SANS_FILES + IBM_PLEX_MONO_FILES:
        path = os.path.join(FONTS_DIR, fname)
        if os.path.exists(path):
            fid = QFontDatabase.addApplicationFont(path)
            if fid != -1:
                loaded_families.extend(QFontDatabase.applicationFontFamilies(fid))
            else:
                missing.append(fname)
        else:
            missing.append(fname)
    if missing:
        print(f"[Font] Missing: {missing}")
    for fam in loaded_families:
        if "IBM Plex Sans" in fam:
            return "IBM Plex Sans"
    for fb in ["Segoe UI", "Arial"]:
        if fb in QFontDatabase.families():
            return fb
    return "sans-serif"


def make_sbtn(text: str, style: str = "normal") -> QPushButton:
    btn = QPushButton(text)
    obj = {"normal": "sbtn", "danger": "sbtn_danger", "warn": "sbtn_warn"}.get(
        style, "sbtn")
    btn.setObjectName(obj)
    btn.setMinimumHeight(44)
    return btn


def sidebar_sep() -> QFrame:
    f = QFrame()
    f.setObjectName("sidebar_sep")
    f.setFrameShape(QFrame.Shape.HLine)
    return f


def sidebar_label(text: str) -> QLabel:
    lbl = QLabel(text.upper())
    lbl.setObjectName("sidebar_section_label")
    return lbl


def make_bbtn(text: str, style: str = "normal") -> QPushButton:
    """Create a styled bottom bar button (horizontal layout)."""
    btn = QPushButton(text)
    obj = {"normal": "sbtn", "danger": "sbtn_danger", "warn": "sbtn_warn"}.get(
        style, "sbtn")
    btn.setObjectName(obj)
    btn.setMinimumHeight(44)
    return btn


def bottom_sep() -> QFrame:
    """Thin vertical separator for bottom bar sections."""
    f = QFrame()
    f.setObjectName("sidebar_sep")
    f.setFrameShape(QFrame.Shape.VLine)
    return f
