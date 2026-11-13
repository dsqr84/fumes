"""The limit gauge: a rounded track with a gradient fill, shared by the strip (vertical) and the peek (horizontal).
The gradient is fixed to the whole track and the fill only reveals the part of it up to the reading, so a low
reading is all green and a high one reaches into red."""

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QBrush, QColor, QLinearGradient, QPainterPath

from . import limits as limmod

TRACK_OPACITY = 0.18         # a window with a reading (a reset window reads 0, with this track)
TRACK_OPACITY_EMPTY = 0.10   # no reading


def snap(v, dpr):
    """Round a logical coordinate to a whole device pixel, so edges are crisp at any scale."""
    return round(v * dpr) / dpr


def _gradient(theme, a, b):
    g = QLinearGradient(a, b)
    for pos, col in limmod.gradient_stops(theme):
        g.setColorAt(pos, QColor(col))
    return g


def paint(p, track, radius, used, theme, ink, vertical, dim=1.0, dpr=1.0):
    """Draw the track (and the fill for `used`, a percent or None) into the QRectF `track`.
    dim: opacity of the fill (1.0, or 0.45 when the reading is not live)."""
    p.setPen(Qt.PenStyle.NoPen)
    p.setOpacity(TRACK_OPACITY if used is not None else TRACK_OPACITY_EMPTY)
    p.setBrush(ink)
    p.drawRoundedRect(track, radius, radius)
    n = limmod.fill_px(used, track.height() if vertical else track.width())
    if n <= 0:
        p.setOpacity(1.0)
        return
    n = snap(n, dpr)
    if vertical:
        fill = QRectF(track.left(), track.bottom() - n, track.width(), n)
        grad = _gradient(theme, QPointF(0, track.bottom()), QPointF(0, track.top()))
    else:
        fill = QRectF(track.left(), track.top(), n, track.height())
        grad = _gradient(theme, QPointF(track.left(), 0), QPointF(track.right(), 0))
    shape = QPainterPath()
    shape.addRoundedRect(track, radius, radius)
    clip = QPainterPath()
    clip.addRect(fill)
    p.setOpacity(dim)
    p.setBrush(QBrush(grad))
    p.drawPath(shape.intersected(clip))     # a path, not a clip region: Qt's clip edges are not antialiased
    p.setOpacity(1.0)
