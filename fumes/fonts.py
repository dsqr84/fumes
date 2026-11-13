"""The bundled UI font: Inter 4.1 (SIL OFL 1.1, fumes/fonts/LICENSE-Inter.txt), static Regular, Medium,
SemiBold and Bold from the rsms/inter release. Bundled so the strip looks the same on every machine and in
--once-png renders; if it cannot be loaded, the widgets keep the system font."""

from pathlib import Path

from PyQt6.QtGui import QFont, QFontDatabase

FONT_DIR = Path(__file__).parent / "fonts"
FAMILY = "Inter"
_loaded = None


def family():
    """Load the bundled files once (needs a QGuiApplication) and return the family, or None."""
    global _loaded
    if _loaded is None:
        fams = set()
        for f in sorted(FONT_DIR.glob("*.ttf")):
            i = QFontDatabase.addApplicationFont(str(f))
            if i >= 0:
                fams.update(QFontDatabase.applicationFontFamilies(i))
        _loaded = FAMILY if FAMILY in fams else ""
    return _loaded or None


def apply(widget):
    """Give a widget the bundled family, keeping its size."""
    fam = family()
    if fam:
        f = QFont(widget.font())
        f.setFamily(fam)
        widget.setFont(f)


def tabular(font):
    """Digits all one width, so figures do not shift as they change (Qt 6.7+; ignored before)."""
    try:
        font.setFeature(QFont.Tag("tnum"), 1)
    except AttributeError:
        pass
    return font
