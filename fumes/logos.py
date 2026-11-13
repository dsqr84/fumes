"""Provider logos: find a file, render it at a size in the ink colour, or draw a badge. Qt, no widgets."""

from pathlib import Path

from PyQt6.QtCore import QRectF, Qt
from PyQt6.QtGui import QColor, QFont, QPainter, QPixmap
from PyQt6.QtSvg import QSvgRenderer

from . import feed as feedmod
from . import fmt

LOGO_DIR = Path(__file__).parent / "logos"
BADGE_COLORS = {"acme": "#d55181"}
BADGE_PALETTE = ["#5b8def", "#e0a030", "#3fa37a", "#9b6bd6", "#d0654a", "#4aa3b8"]


def badge_color(pid):
    if pid in BADGE_COLORS:
        return BADGE_COLORS[pid]
    return BADGE_PALETTE[sum(map(ord, pid)) % len(BADGE_PALETTE)]


def find_logo(pid, user_logo_dir=None):
    if not feedmod.valid_id(pid):   # never build a path from anything but a plain slug
        return None
    dirs = ([user_logo_dir] if user_logo_dir else []) + [LOGO_DIR]
    for d in dirs:
        for ext in ("svg", "png"):
            f = d / "logos" / f"{pid}.{ext}" if d is user_logo_dir else d / f"{pid}.{ext}"
            if f.is_file():
                return f
    return None


def render_logo(pid, name, ink, dpr, size, user_logo_dir, font):
    """A size x size (logical px) pixmap at device ratio dpr."""
    px = round(size * dpr)
    pm = QPixmap(px, px)
    pm.fill(Qt.GlobalColor.transparent)
    pm.setDevicePixelRatio(dpr)
    p = QPainter(pm)
    p.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform)
    f = find_logo(pid, user_logo_dir)
    target = QRectF(0, 0, size, size)
    drawn = False
    if f is not None and f.suffix == ".svg":
        try:
            text = fmt.recolor_svg(f.read_text("utf-8"), ink)
            r = QSvgRenderer(text.encode("utf-8"))
            if r.isValid():
                r.render(p, target)
                drawn = True
        except OSError:
            pass
    elif f is not None:
        src = QPixmap(str(f))
        if not src.isNull():
            p.drawPixmap(target.toRect(), src.scaled(
                px, px, Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation))
            drawn = True
    if not drawn:
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(badge_color(pid)))
        p.drawRoundedRect(target, size * 0.22, size * 0.22)
        p.setPen(QColor("white"))
        bf = QFont(font)
        bf.setPixelSize(round(size * 0.61))
        bf.setBold(True)
        p.setFont(bf)
        p.drawText(target, Qt.AlignmentFlag.AlignCenter, (name or pid)[:1].upper())
    p.end()
    return pm
