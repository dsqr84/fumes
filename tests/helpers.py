"""A QPainter that records what is drawn, so tests can assert opacity, ink, font and text without pixel guessing."""
from PyQt6.QtCore import QPointF, QRectF
from PyQt6.QtGui import QColor, QImage, QPainter


class Spy(QPainter):
    def __init__(self, dev):
        super().__init__(dev)
        self.log = []          # dicts: kind, op, text, color, px, weight, rect
        self._op = 1.0

    def setOpacity(self, o):
        self._op = o
        super().setOpacity(o)

    def _font(self):
        f = self.font()
        return f.pixelSize(), int(f.weight())

    def drawText(self, *a):
        text = a[-1] if isinstance(a[-1], str) else None
        px, wt = self._font()
        rect = a[0] if isinstance(a[0], QRectF) else None
        self.log.append(dict(kind="text", op=self._op, text=text, color=self.pen().color().name(), px=px,
                             weight=wt, rect=rect, pos=a[0] if isinstance(a[0], QPointF) else None))
        super().drawText(*a)

    def drawPixmap(self, *a):
        self.log.append(dict(kind="pixmap", op=self._op))
        super().drawPixmap(*a)

    def fillRect(self, *a):
        col = a[1] if len(a) > 1 and isinstance(a[1], QColor) else None
        self.log.append(dict(kind="rect", op=self._op, rect=a[0], color=col.name() if col else None))
        super().fillRect(*a)

    def drawPath(self, path):
        self.log.append(dict(kind="path", op=self._op, color=self.brush().color().name()))
        super().drawPath(path)

    def texts(self):
        return [e["text"] for e in self.log if e["kind"] == "text"]

    def text(self, t):
        return next(e for e in self.log if e["kind"] == "text" and e["text"] == t)


def new_image(w, h, dpr=1.0, bg="#202020"):
    img = QImage(round(w * dpr), round(h * dpr), QImage.Format.Format_ARGB32)
    img.setDevicePixelRatio(dpr)
    img.fill(QColor(bg))
    return img


def spy_strip(strip, dpr=1.0):
    img = new_image(strip.width(), strip.height(), dpr)
    p = Spy(img)
    strip.paint_to(p, dpr, bg=QColor("#202020"))
    p.end()
    return p, img


def spy_peek(peek, dpr=1.0):
    img = new_image(peek.width(), peek.height(), dpr)
    p = Spy(img)
    peek.paint_to(p, dpr)
    p.end()
    return p, img
