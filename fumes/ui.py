"""The strip widget, tray icon and fetch thread."""

import json
import math
from datetime import datetime, timezone
from pathlib import Path

from PyQt6.QtCore import QPointF, QRect, QRectF, QSize, Qt, QThread, QTimer, QUrl, pyqtSignal
from PyQt6.QtGui import (QAction, QColor, QDesktopServices, QFont, QFontMetrics, QFontMetricsF,
                         QGuiApplication, QIcon, QImage, QPainter, QPen, QPixmap)
from PyQt6.QtSvg import QSvgRenderer
from PyQt6.QtWidgets import QApplication, QMenu, QSystemTrayIcon, QToolTip, QWidget

from . import collect, keystore
from . import feed as feedmod
from . import fmt, fonts, gauge, limits as limmod, links, logos, place, win
from .logos import LOGO_DIR, badge_color  # noqa: F401 (re-exported)
from . import config as cfgmod
from .config import Config, State

LOGO = 14                     # two rows, like the taskbar clock: small logos and clock-sized figures
FIG_CAP = 9                   # digit cap height in px (the clock's time is about 9)
ROWS = 2
ROW_PITCH = 18                # centre to centre of the two rows
STRIP_H = 40
PAD_X = 6
GAP_LOGO = 4
GAP_CELL = 12                 # between columns
HIT_GAP = 10                  # a cell is clickable this much wider than it is drawn (5 px each side, under PAD_X)
SMALL_PT = 7.5                # gauge labels, the `mtd` label and the age label
BAR_W, BAR_H = 28, 5          # the Claude cell's horizontal limit bars, one per row
FIG_WEIGHT = QFont.Weight.Normal
BTN_W, CHEV_DEPTH, CHEV_H, CHEV_STROKE = 20, 5, 10, 1.5   # collapse button: hit area, drawn chevron
BTN_GAP = 12                  # clear space before the button's hit area
BTN_OPACITY = 0.45
GAP_LABEL = 4                 # a small label to what it labels (gauge, or the mtd figure)
SEP_W, SEP_H, SEP_CLEAR = 1, 30, 8   # hairline after the Claude cell and the clear space each side
SEP_OPACITY = 0.20
PLATE_OPACITY, PLATE_RADIUS, PLATE_PAD, PLATE_H, BTN_PLATE = 0.08, 4, 5, 17, 20
LABEL_OPACITY = 0.62          # small labels, and the dash for "no figure"
DIM_OPACITY = 0.45            # a figure that is not live; the one and only dimming, never multiplied
_FIG_PX_CACHE = {}


def cap_height(font):
    """Digit height of a font in px: capHeight, or the tight height of '0' when the font reports none."""
    fm = QFontMetricsF(font)
    c = fm.capHeight()
    return c if c > 0 else fm.tightBoundingRect("0").height()


def figure_pixel_size(base_font, dpr, weight=FIG_WEIGHT, target=LOGO):
    """Pixel size at which this family's digits are as tall as the logos. Cached per family/weight/dpr."""
    key = (base_font.family(), int(weight), dpr)
    px = _FIG_PX_CACHE.get(key)
    if px is None:
        def cap(n):
            f = QFont(base_font)
            f.setPixelSize(n)
            f.setWeight(weight)
            return cap_height(f)
        px = _FIG_PX_CACHE[key] = fmt.fit_pixel_size(cap, target)
    return px
DARK_INK, LIGHT_INK = "#f2f2f2", "#1c1c1c"
PNG_BG = {"dark": "#202020", "light": "#f3f3f3"}


def cell_opacity(role, dim, dash=False):
    """Opacity of one part of a cell. role: 'logo' | 'label' | 'figure'. Opacity means data trust only:
    a live cell is full (labels and the dash for no figure are the quiet 0.62), a cell that is not live is
    0.45 on every part, and the two never multiply, so nothing goes below 0.45."""
    if dim:
        return DIM_OPACITY
    if role == "label" or (role == "figure" and dash):
        return LABEL_OPACITY
    return 1.0


def resolve_theme(setting):
    """'dark' | 'light' -> the ink choice; 'auto' asks Windows (default dark taskbar)."""
    if setting in ("dark", "light"):
        return setting
    light = win.uses_light_theme()
    return "light" if light else "dark"


class Cell:
    """kind 'provider' | 'limits'; fig is the figure kind (balance | mtd | na) of a provider cell; label is
    the small `mtd` before a month-to-date figure.
    key = (kind, id) is what clicks and peeks use, so a provider called 'claude' cannot meet the limits cell."""

    def __init__(self, kind, id, text, x, w, provider=None, fig="balance", dim=False, meters=(), label="",
                 label_w=0.0, y=0.0, h=None, row=None, text_x=None):
        self.kind, self.id, self.text, self.x, self.w = kind, id, text, x, w
        self.y, self.h, self.row = y, (STRIP_H if h is None else h), row   # row None: spans the height
        self.text_x = text_x          # where the figure (or its mtd label) starts, beside the logo
        self.provider, self.fig, self.dim, self.meters = provider, fig, dim, list(meters)
        self.label, self.label_w = label, label_w

    @property
    def key(self):
        return (self.kind, self.id)


LIMITS_KEY = ("limits", "claude")
BUTTON = ("button", None)


class Strip(QWidget):
    offsetChanged = pyqtSignal(int)
    collapsedChanged = pyqtSignal(bool)

    def __init__(self, cfg: Config, user_logo_dir=None, theme=None):
        super().__init__()
        self.cfg = cfg
        self.user_logo_dir = Path(user_logo_dir) if user_logo_dir else None
        self.theme = theme or resolve_theme(cfg.theme)
        self.feed = None
        self.fetch_failed = False
        self.limits = None
        self.limits_failed = False
        self.now_fn = lambda: datetime.now(timezone.utc)   # tests and --once-png pin it
        self.tz = None                                     # tzinfo for local times; None = this machine
        self._h = 28
        self.collapsed = False
        self._btn = None            # (x, width) of the collapse button, or None
        self._hover = None          # key of the cell, BUTTON, or None: what the pointer is over
        self._sep_x = None          # x of the hairline after the Claude cell, or None
        self._cap = 18.0
        self.offset = 0
        self._cells = []
        self._age = ""
        self._age_x = 0
        self._logo_cache = {}
        self._drag = None
        self._move = False
        self._move_origin = 0
        self._press_cell = None
        self._rpress = False
        self._peek = None
        self.context_menu = None
        self.setWindowFlags(Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint
                            | Qt.WindowType.WindowStaysOnTopHint
                            | Qt.WindowType.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setMouseTracking(True)
        fonts.apply(self)
        self._relayout()

    # ---- model -----------------------------------------------------------
    def set_feed(self, feed, failed=False, now=None):
        self.feed = feed
        self.fetch_failed = failed
        self._relayout(now)
        self._refresh_peek()

    def set_failed(self, failed=True, now=None):
        self.fetch_failed = failed
        self._relayout(now)

    def set_limits(self, limits, failed=False, now=None):
        self.limits = limits
        self.limits_failed = failed
        self._relayout(now)
        self._refresh_peek()

    def set_limits_failed(self, failed=True, now=None):
        self.limits_failed = failed
        self._relayout(now)
        self._refresh_peek()

    def _refresh_peek(self):
        if self._peek is not None and self._peek.isVisible():
            self._peek.refresh()

    def provider_cells(self):
        return [c for c in self._cells if c.kind == "provider"]

    def is_stale(self, now=None):
        if self.fetch_failed or self.feed is None:
            return self.fetch_failed
        return feedmod.is_stale(self.feed, self.cfg.stale_after_minutes, now)

    def cell_texts(self):
        return [c.text for c in self._cells]

    def cells(self):
        return list(self._cells)

    def age_label(self):
        return self._age

    # ---- layout ----------------------------------------------------------
    def _font(self):
        """The small font: meter labels and the age label."""
        f = QFont(self.font())
        f.setPointSizeF(SMALL_PT)
        return f

    def _fig_font(self):
        """The figure font: digits as tall as the logos, or the font_size override (points)."""
        f = QFont(self.font())
        if self.cfg.font_size:
            f.setPointSizeF(self.cfg.font_size)
            f.setWeight(FIG_WEIGHT)
        else:
            f.setPixelSize(figure_pixel_size(self.font(), self.devicePixelRatioF(), target=FIG_CAP))
            f.setWeight(FIG_WEIGHT)
        return fonts.tabular(f)

    def _relayout(self, now=None):
        now = now or self.now_fn()
        small, big = QFontMetricsF(self._font()), QFontMetricsF(self._fig_font())
        self._cap = cap_height(self._fig_font())
        self._h = STRIP_H
        stale = self.is_stale(now)
        x = PAD_X
        self._cells = []
        self._sep_x = None
        if self.cfg.show_limits:
            x = self._layout_limits(x, now, small)
        if self.feed is None:
            ids = self.cfg.providers or feedmod.KNOWN_ORDER
            items = [(feedmod.Provider(id=i, name=i), "…", "balance", False) for i in ids]
        else:
            items = []
            for p in feedmod.select(self.feed, self.cfg.providers):
                text, kind = fmt.figure(p)
                items.append((p, text, kind, p.status != "ok"))
        if self.is_collapsed():
            items = []
        for col in range(0, len(items), ROWS):        # columns of two, filled top to bottom
            group = items[col:col + ROWS]
            if self._cells:
                if self._cells[-1].kind == "limits":     # the hairline replaces the normal gap here
                    self._sep_x = x + SEP_CLEAR
                    x += 2 * SEP_CLEAR + SEP_W
                else:
                    x += GAP_CELL
            parts = []
            for p, text, kind, dim in group:
                label = "mtd" if kind == "mtd" else ""
                label_w = small.horizontalAdvance(label) + GAP_LABEL if label else 0.0
                parts.append((p, text, kind, dim, label, label_w, label_w + big.horizontalAdvance(text)))
            col_w = LOGO + GAP_LOGO + max(fw for *_, fw in parts)
            for r, (p, text, kind, dim, label, label_w, fw) in enumerate(parts):
                y, h = self.row_top(r), ROW_PITCH
                self._cells.append(Cell("provider", p.id, text, x, col_w, provider=p, fig=kind, dim=dim,
                                        label=label, label_w=label_w, y=y, h=h, row=r,
                                        text_x=x + LOGO + GAP_LOGO))
            x += col_w
        self._age = ""
        if self.is_collapsed():
            pass
        elif self.feed is not None and stale:
            self._age = fmt.age_text(feedmod.age_seconds(self.feed, now))
        elif self.feed is None and self.fetch_failed:
            self._age = "offline"
        if self._age:
            x += GAP_CELL
            self._age_x = x
            x += small.horizontalAdvance(self._age)
        self._dim_all = stale or (self.feed is None and self.fetch_failed)
        if self.cfg.show_limits:
            x += BTN_GAP
            self._btn = (x, BTN_W)
            self._width = x + BTN_W + 4
        else:
            self._btn = None
            self._width = x + PAD_X
        self._width = math.ceil(self._width)
        self.setFixedSize(QSize(self._width, self.content_height()))
        self.update()

    def _layout_limits(self, x, now, small):
        """The Claude cell, leftmost: logo, then a label and a vertical gauge per window. Returns the x after it."""
        meters = []
        ts = now.timestamp()
        for label, win_ in (("5h", self.limits.five_hour if self.limits else None),
                            ("wk", self.limits.seven_day if self.limits else None)):
            used = limmod.view(win_, ts).used          # None: no reading (also before the first fetch)
            lw = small.horizontalAdvance(label)
            meters.append(dict(label=label, used=used, lw=lw))
        label_w = max(m["lw"] for m in meters)
        w = LOGO + GAP_LOGO + label_w + GAP_LABEL + BAR_W
        self._cells.append(Cell("limits", "claude", "", x, w, dim=self.limits_failed, meters=meters,
                                label_w=label_w))
        return x + w

    def content_height(self):
        return self._h

    @staticmethod
    def row_centre(r):
        """Centre y of row r (0 top, 1 bottom); None is the middle of the strip."""
        if r is None:
            return STRIP_H / 2.0
        return STRIP_H / 2.0 + (r - (ROWS - 1) / 2.0) * ROW_PITCH

    @classmethod
    def row_top(cls, r):
        return cls.row_centre(r) - ROW_PITCH / 2.0

    def is_collapsed(self):
        """Collapsed to the Claude cell: only meaningful while the Claude cell exists."""
        return self.collapsed and self.cfg.show_limits

    def set_collapsed(self, on):
        on = bool(on)
        if on == self.collapsed:
            return
        self.collapsed = on
        if self.is_collapsed() and self._peek is not None and self._peek.isVisible() \
                and self._peek.key[0] == "provider":
            self._peek.close()
        self._relayout()
        self.reposition()   # same anchored edge, same wanted offset: see reposition()
        if self._peek is not None and self._peek.isVisible():
            self._peek.reposition()
        self.collapsedChanged.emit(on)

    def toggle_collapsed(self):
        self.set_collapsed(not self.collapsed)

    def hit(self, x, y=None):
        """What is at logical (x, y): ('button', None), ('cell', key) or (None, None). Without y, the first
        cell whose column holds x (the top row)."""
        if y is not None:
            # each row owns its half of the strip, so the gap between the rows still belongs to one of them
            spans = [(c.key, c.x, c.w) for c in self._cells
                     if c.row is None or self._row_of(y) == c.row]
        else:
            spans = [(c.key, c.x, c.w) for c in self._cells]
        return place.hit_test(x, spans, self._btn, HIT_GAP)

    def _row_of(self, y):
        return 0 if y < STRIP_H / 2.0 else ROWS - 1

    # ---- painting --------------------------------------------------------
    def ink(self):
        return QColor(DARK_INK if self.theme == "dark" else LIGHT_INK)

    def _logo_pixmap(self, pid, name, dpr):
        ink = self.ink().name()
        key = (pid, ink, dpr)
        pm = self._logo_cache.get(key)
        if pm is None:
            pm = self._render_logo(pid, name, ink, dpr)
            self._logo_cache[key] = pm
        return pm

    def _find_logo(self, pid):
        return logos.find_logo(pid, self.user_logo_dir)

    def _render_logo(self, pid, name, ink, dpr):
        return logos.render_logo(pid, name, ink, dpr, LOGO, self.user_logo_dir, self._font())

    def hovered(self):
        """What the pointer is over: a cell key, BUTTON, or None."""
        return self._hover

    @property
    def _hover_btn(self):
        return self._hover == BUTTON

    def _plate(self, p, rect):
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(self.ink())
        p.setOpacity(PLATE_OPACITY)
        p.drawRoundedRect(rect, PLATE_RADIUS, PLATE_RADIUS)
        p.setOpacity(1.0)

    def paint_to(self, p, dpr, bg=None):
        """Draw the strip at logical coordinates onto painter p (used by paintEvent and PNG)."""
        h = self.content_height()
        p.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.TextAntialiasing
                         | QPainter.RenderHint.SmoothPixmapTransform)
        if bg is not None:
            p.fillRect(QRect(0, 0, self._width, h), bg)
        else:
            # near-invisible fill: fully transparent pixels are click-through on Windows
            p.fillRect(QRect(0, 0, self._width, h), QColor(0, 0, 0, 1))
        small, big = self._font(), self._fig_font()
        base = (h + self._cap) / 2.0      # the age label: centred on the strip
        if self._hover is not None and not self._move:       # the plate sits behind everything else
            if self._hover == BUTTON and self._btn is not None:
                cx = self._btn[0] + BTN_W / 2.0
                self._plate(p, QRectF(cx - BTN_PLATE / 2.0, (h - BTN_PLATE) / 2.0, BTN_PLATE, BTN_PLATE))
            else:
                hc = next((c for c in self._cells if c.key == self._hover), None)
                if hc is not None:
                    ph = h - 4 if hc.row is None else PLATE_H
                    cy = self.row_centre(hc.row)
                    self._plate(p, QRectF(hc.x - PLATE_PAD, cy - ph / 2.0, hc.w + 2 * PLATE_PAD, ph))
        for c in self._cells:
            if c.kind == "limits":
                self._paint_limits(p, c, dpr, small, base, h)
                continue
            dim = c.dim or self._dim_all
            dash = c.fig == "na"
            cy = self.row_centre(c.row)
            rbase = cy + self._cap / 2.0
            p.setOpacity(cell_opacity("logo", dim))
            p.drawPixmap(QPointF(c.x, gauge.snap(cy - LOGO / 2.0, dpr)), self._logo_pixmap(c.id, c.provider.name, dpr))
            x = c.text_x if c.text_x is not None else c.x + LOGO + GAP_LOGO
            p.setPen(self.ink())
            if c.label:
                p.setOpacity(cell_opacity("label", dim))
                p.setFont(small)
                p.drawText(QPointF(x, rbase), c.label)
                x += c.label_w
            p.setOpacity(cell_opacity("figure", dim, dash))
            p.setFont(big)
            p.drawText(QPointF(x, rbase), c.text)
        p.setOpacity(1.0)
        if self._sep_x is not None:
            p.setOpacity(SEP_OPACITY)
            p.fillRect(QRectF(gauge.snap(self._sep_x, dpr), (h - SEP_H) / 2.0, SEP_W, SEP_H), self.ink())
            p.setOpacity(1.0)
        if self._age:
            p.setOpacity(0.7)
            p.setPen(self.ink())
            p.setFont(small)
            p.drawText(QPointF(self._age_x, base), self._age)
        p.setOpacity(1.0)
        if self._btn is not None:
            self._paint_button(p, h)
        if self._move:
            p.setOpacity(0.5)
            p.setPen(self.ink())
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRect(QRectF(0.5, 0.5, self._width - 1, h - 1))
            p.setOpacity(1.0)

    def _paint_limits(self, p, c, dpr, small, base, h):
        """The Claude cell: the logo, then `label bar` per window, one window per row. Numbers live in the
        tooltip and the peek."""
        dim = c.dim
        p.setOpacity(cell_opacity("logo", dim))
        p.drawPixmap(QPointF(c.x, gauge.snap((h - LOGO) / 2.0, dpr)), self._logo_pixmap("claude", "Claude", dpr))
        x0 = c.x + LOGO + GAP_LOGO
        for r, m in enumerate(c.meters):
            cy = self.row_centre(r)
            p.setOpacity(cell_opacity("label", dim))
            p.setFont(small)
            p.setPen(self.ink())
            p.drawText(QPointF(x0, cy + cap_height(small) / 2.0), m["label"])
            bx = x0 + c.label_w + GAP_LABEL
            gauge.paint(p, QRectF(gauge.snap(bx, dpr), gauge.snap(cy - BAR_H / 2.0, dpr), BAR_W, BAR_H),
                        BAR_H / 2.0, m["used"], self.theme, self.ink(), vertical=False,
                        dim=cell_opacity("figure", dim), dpr=dpr)
        p.setOpacity(1.0)

    def _paint_button(self, p, h):
        """A chevron drawn in code. It points the way the strip will fold when expanded, back when collapsed."""
        bx = self._btn[0]
        cx, cy = bx + BTN_W / 2.0, h / 2.0
        t = place.chevron_tip(self.cfg.anchor, self.is_collapsed())     # +1: tip on the right
        pen = QPen(self.ink(), CHEV_STROKE)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        p.setOpacity(1.0 if self._hover_btn else BTN_OPACITY)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        hw, hh = CHEV_DEPTH / 2.0, CHEV_H / 2.0
        p.drawPolyline([QPointF(cx - t * hw, cy - hh), QPointF(cx + t * hw, cy), QPointF(cx - t * hw, cy + hh)])
        p.setOpacity(1.0)

    def paintEvent(self, _ev):
        p = QPainter(self)
        self.paint_to(p, self.devicePixelRatioF())
        p.end()

    def render_png(self, path, dpr=1.0):
        """Render onto a theme-coloured background (for --once-png)."""
        w, h = self._width, self.content_height()
        img = QImage(round(w * dpr), round(h * dpr), QImage.Format.Format_ARGB32)
        img.setDevicePixelRatio(dpr)
        img.fill(QColor(PNG_BG[self.theme]))
        p = QPainter(img)
        self.paint_to(p, dpr, bg=QColor(PNG_BG[self.theme]))
        p.end()
        return img.save(str(path))

    # ---- tooltip + dragging ----------------------------------------------
    def _cell_at(self, x, y=None):
        what, key = self.hit(x, y)
        return next((c for c in self._cells if c.key == key), None) if what == "cell" else None

    def event(self, ev):
        if ev.type() == ev.Type.ToolTip:
            what, key = self.hit(ev.pos().x(), ev.pos().y())
            c = self._cell_at(ev.pos().x(), ev.pos().y())
            if what == "button":
                QToolTip.showText(ev.globalPos(), "Expand" if self.is_collapsed() else "Collapse", self)
            elif c is not None and c.kind == "limits" and self.limits is not None:
                QToolTip.showText(ev.globalPos(), fmt.escape_tooltip(fmt.limits_tooltip(
                    [("5-hour limit", limmod.view(self.limits.five_hour, self.now_fn().timestamp())),
                     ("Weekly limit", limmod.view(self.limits.seven_day, self.now_fn().timestamp()))],
                    self.limits.updated, self.now_fn(), self.tz)), self)
            elif c is not None and c.kind == "provider" and self.feed is not None:
                QToolTip.showText(ev.globalPos(), fmt.tooltip_html(c.provider), self)
            else:
                QToolTip.hideText()
            return True
        return super().event(ev)

    def cell_at_global(self, gpos):
        """(over the strip?, key of the cell under that global point or None)."""
        pt = self.mapFromGlobal(gpos.toPoint())
        over = self.rect().contains(pt)
        c = self._cell_at(pt.x(), pt.y()) if over else None
        return over, (c.key if c is not None else None)

    def move_mode(self):
        return self._move

    def begin_move(self):
        self.close_peek()
        self._move, self._move_origin, self._drag = True, self.offset, None
        self._hover = None                        # no hover plate in move mode
        self.setCursor(Qt.CursorShape.SizeHorCursor)
        self.grabKeyboard()   # the strip takes no focus, so Esc needs the grab (untested on Windows)
        self.update()

    def _end_move(self):
        self._move, self._drag = False, None
        self.releaseKeyboard()
        self.unsetCursor()
        self.update()

    def _move_event(self, event):
        """Run one event through place.move_step; returns the action."""
        self._move, action = place.move_step(self._move, event)
        return action

    def cancel_move(self):
        if self._move:
            self._move_event("esc")
            self._end_move()
            self.offset = self._move_origin
            self.reposition(commit=True)

    def keyPressEvent(self, ev):
        if ev.key() == Qt.Key.Key_Escape and self._move:
            self.cancel_move()
        else:
            super().keyPressEvent(ev)

    def mousePressEvent(self, ev):
        if ev.button() == Qt.MouseButton.RightButton:
            self.close_peek()
            if self._move:
                self.cancel_move()
                self._rpress = False
            else:
                self._rpress = True
            return
        if ev.button() != Qt.MouseButton.LeftButton:
            return
        if self._move:
            if self._move_event("press") == "drag":
                self._drag = (ev.globalPosition().x(), self.offset)
                self._move = True
        else:
            self._move_event("press")
            what, key = self.hit(ev.position().x(), ev.position().y())
            self._press_cell = BUTTON if what == "button" else (key if what == "cell" else None)

    def mouseMoveEvent(self, ev):
        if self._move:
            if self._drag is not None and self._move_event("move") == "drag":
                gx, off0 = self._drag
                self.offset = round(off0 + ev.globalPosition().x() - gx)
                self.reposition(commit=True)
            return
        what, key = self.hit(ev.position().x(), ev.position().y())
        target = BUTTON if what == "button" else (key if what == "cell" else None)
        if target != self._hover:                 # repaint only when the hovered target changes
            self._hover = target
            self.update()
        self.setCursor(Qt.CursorShape.PointingHandCursor) if what else self.unsetCursor()

    def leaveEvent(self, ev):
        if self._hover is not None:
            self._hover = None
            self.update()
        super().leaveEvent(ev)

    def mouseReleaseEvent(self, ev):
        if ev.button() == Qt.MouseButton.RightButton:
            if self._rpress:
                self._rpress = False
                if self.context_menu is not None:
                    self.context_menu.popup(ev.globalPosition().toPoint())
            return
        if ev.button() != Qt.MouseButton.LeftButton:
            return
        if self._move:
            if self._drag is not None and self._move_event("release") == "commit":
                self._end_move()
                self.offsetChanged.emit(self.offset)
            return
        action = self._move_event("release")
        pid, self._press_cell = self._press_cell, None
        what, key = self.hit(ev.position().x(), ev.position().y())
        if action != "peek" or pid is None:
            return
        if pid == BUTTON:
            if what == "button":
                self.toggle_collapsed()
        elif what == "cell" and key == pid:
            self.open_key(pid)

    def hideEvent(self, ev):
        self.close_peek()
        self.cancel_move()
        super().hideEvent(ev)

    # ---- peek ----------------------------------------------------------------
    def open_peek(self, pid):
        """Open the peek of provider `pid` (a plain provider id)."""
        return self.open_key(("provider", pid))

    def open_limits_peek(self):
        return self.open_key(LIMITS_KEY)

    def open_key(self, key):
        from .peek import Peek
        if self._peek is None:
            self._peek = Peek(self)
        return self._peek.show_key(key)

    def close_peek(self):
        if self._peek is not None and self._peek.isVisible():
            self._peek.close()

    # ---- position --------------------------------------------------------
    def reposition(self, commit=False):
        """Place the strip. `offset` is the wanted offset from the anchor and is only changed by a drag
        (commit=True stores what the clamp allowed). Collapse and the 2 s tick must not rewrite it, or an
        offset that fits only the narrow strip would be lost on expanding."""
        screen = QGuiApplication.primaryScreen()
        dpr = screen.devicePixelRatio()
        g = screen.geometry()
        found = win.taskbar_rects()
        taskbar, tray = found if found else (None, None)
        screen_r = place.Rect(g.left(), g.top(), g.left() + g.width(), g.top() + g.height())
        x, y, applied = place.compute_position(taskbar, tray, self.width(), self.height(),
                                               self.cfg.anchor, self.cfg.margin, dpr,
                                               self.offset, screen_r)
        if commit:
            self.offset = applied
        if (x, y) != (self.x(), self.y()):
            self.move(x, y)

    def hwnd(self):
        return int(self.winId()) if win.ON_WINDOWS else 0


class LimitsThread(QThread):
    """Its own thread, so a limits failure never touches the provider fetch and vice versa."""
    done = pyqtSignal(object, object, str)  # Limits or None, raw or None, error text

    def __init__(self, url, parent=None):
        super().__init__(parent)
        self.url = url

    def run(self):
        try:
            lim, raw = limmod.fetch(self.url, timeout=10)
            self.done.emit(lim, raw, "")
        except Exception as e:
            self.done.emit(None, None, str(e))


class FetchThread(QThread):
    done = pyqtSignal(object, object, str)  # feed or None, raw or None, error text

    def __init__(self, url, parent=None):
        super().__init__(parent)
        self.url = url

    def run(self):
        try:
            f, raw = feedmod.fetch(self.url, timeout=10)
            self.done.emit(f, raw, "")
        except Exception as e:  # FeedError and anything unexpected: never reach the UI thread
            self.done.emit(None, None, str(e))


class CollectThread(QThread):
    """Local collection: asks each provider's own API with the saved keys. Same signal as FetchThread."""
    done = pyqtSignal(object, object, str)  # feed or None, raw payload or None, error text

    def __init__(self, store, ledger, http, previous, enabled, parent=None):
        super().__init__(parent)
        self.store, self.ledger, self.http = store, ledger, http
        self.previous, self.enabled = previous, enabled

    def run(self):
        try:
            payload, failed = collect.collect(datetime.now(timezone.utc), self.store, self.ledger,
                                              http=self.http, previous=self.previous, enabled=self.enabled)
            if self.ledger.anchors or self.ledger.samples:   # nothing to remember: write nothing
                self.ledger.save()
            self.done.emit(feedmod.parse_feed(payload), payload,
                           "failed: " + ", ".join(failed) if failed else "")
        except Exception as e:  # noqa: BLE001 (never reach the UI thread)
            self.done.emit(None, None, type(e).__name__)


def tray_icon():
    """A $ glyph on a rounded square, drawn in code."""
    out = QIcon()
    for size in (16, 24, 32, 48, 64):
        pm = QPixmap(size, size)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#2f7d5b"))
        p.drawRoundedRect(QRectF(0, 0, size, size), size * 0.22, size * 0.22)
        f = QFont()
        f.setBold(True)
        f.setPixelSize(int(size * 0.7))
        p.setFont(f)
        p.setPen(QColor("white"))
        p.drawText(QRectF(0, 0, size, size), Qt.AlignmentFlag.AlignCenter, "$")
        p.end()
        out.addPixmap(pm)
    return out


def build_menu(app):
    """The one menu behind both the tray icon and a right-click on the strip."""
    menu = QMenu()
    menu.addAction("Move", app.strip.begin_move)
    menu.addAction("Reset position", app.reset_position)
    app.collapse_action = QAction("Collapsed", menu)
    app.collapse_action.setCheckable(True)
    app.collapse_action.setEnabled(app.strip.cfg.show_limits)
    app.collapse_action.setChecked(app.strip.is_collapsed())
    app.collapse_action.toggled.connect(app.strip.set_collapsed)
    app.strip.collapsedChanged.connect(app.collapse_action.setChecked)
    menu.addAction(app.collapse_action)
    menu.addSeparator()
    menu.addAction("Settings…", app.open_settings)
    menu.addAction("Refresh now", app.refresh_all)
    menu.addAction("Open details page", app.open_details)
    app.autostart = QAction("Start with Windows", menu)
    app.autostart.setCheckable(True)
    app.autostart.setEnabled(win.ON_WINDOWS)
    app.autostart.setChecked(win.autostart_enabled())
    app.autostart.toggled.connect(app._set_autostart)
    menu.addAction(app.autostart)
    menu.addSeparator()
    menu.addAction("Restart", app.restart)
    menu.addAction("Quit", QApplication.quit)
    return menu


class App:
    """Owns the strip, tray, timers and the fetch thread."""

    def __init__(self, cfg: Config, state: State, user_logo_dir=None, store=None, ledger=None, http=None):
        self.cfg, self.state = cfg, state
        self.folder = Path(user_logo_dir or cfgmod.config_dir())
        # used only when cfg.url is empty (local collection); injectable for tests
        self.store = store if store is not None else keystore.default_store()
        self.ledger = ledger if ledger is not None else collect.Ledger.load(self.folder / "ledger.json")
        self.http = http
        self.strip = Strip(cfg, user_logo_dir)
        self.strip.offset = state.offset
        self.strip.collapsed = state.collapsed
        self.strip.collapsedChanged.connect(self._save_collapsed)
        self.strip.offsetChanged.connect(self._save_offset)
        self.user_hidden = False
        self.fullscreen_hidden = False
        self.thread = None
        self.limits_thread = None

        if state.cache:
            try:
                self.strip.set_feed(feedmod.parse_feed(state.cache))
            except feedmod.FeedError:
                pass
        if cfg.show_limits and state.limits_cache:
            try:
                self.strip.set_limits(limmod.parse_limits(state.limits_cache))
            except feedmod.FeedError:
                pass

        self.tray = QSystemTrayIcon(tray_icon())
        menu = build_menu(self)
        self.menu = menu
        self.strip.context_menu = menu
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._tray_activated)
        self.tray.show()

        self.fetch_timer = QTimer()
        self.fetch_timer.timeout.connect(self.refresh)
        self.fetch_timer.start(int(cfg.refresh_minutes * 60_000))
        self.limits_timer = QTimer()
        if cfg.show_limits:
            self.limits_timer.timeout.connect(self.refresh_limits)
            self.limits_timer.start(int(cfg.limits_refresh_minutes * 60_000))
        self.tick_timer = QTimer()
        self.tick_timer.timeout.connect(self.tick)
        self.tick_timer.start(2000)
        QGuiApplication.primaryScreen().geometryChanged.connect(lambda *_: self.strip.reposition())

        self.strip.reposition()
        self.strip.show()
        self._update_tray_tip()
        self.refresh()
        self.refresh_limits()

    def restart(self):
        """Start a fresh copy, then quit this one. If the new one cannot start, keep running."""
        if win.relaunch():
            QApplication.quit()

    def open_settings(self, page=None):
        """The one shared Settings window: created on first use, raised when already open."""
        from .settings import SettingsWindow
        if getattr(self, "settings_window", None) is None:
            self.settings_window = SettingsWindow(
                self.store, self.ledger, http=self.http, folder=self.folder, cfg=self.cfg,
                autostart_action=self.autostart, on_refresh=self.refresh_all, theme=self.strip.theme)
        w = self.settings_window
        if page:
            w.show_page(page)
        w.show()
        w.raise_()
        w.activateWindow()
        return w

    def open_details(self):
        links.open_link(self.cfg, self.cfg.details_url)

    def _set_autostart(self, on):
        win.set_autostart(on)
        self.autostart.blockSignals(True)      # show what the registry says, not what was clicked
        self.autostart.setChecked(win.autostart_enabled())
        self.autostart.blockSignals(False)

    def _save_offset(self, off):
        self.state.offset = off
        self.state.save()

    def _save_collapsed(self, on):
        self.state.collapsed = on
        try:
            self.state.save()
        except OSError:
            pass

    def reset_position(self):
        self.strip.offset = 0
        self.strip.reposition()
        self._save_offset(0)

    def _tray_activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self.user_hidden = not self.user_hidden
            self._apply_visibility()

    def _apply_visibility(self):
        self.strip.setVisible(not (self.user_hidden or self.fullscreen_hidden))

    def tick(self):
        self.strip.reposition()
        win.set_topmost(self.strip.hwnd())
        fs = win.foreground_fullscreen(self.strip.hwnd())
        if fs != self.fullscreen_hidden:
            self.fullscreen_hidden = fs
            self._apply_visibility()
        self.strip._relayout()  # keeps the age label current
        self._update_tray_tip()

    def refresh(self):
        if self.thread is not None and self.thread.isRunning():
            return
        if self.cfg.url:
            self.thread = FetchThread(self.cfg.url)
        else:
            prev = self.state.cache if isinstance(self.state.cache, dict) else None
            self.thread = CollectThread(self.store, self.ledger, self.http, prev, self.cfg.providers or None)
        self.thread.done.connect(self._fetched)
        self.thread.start()

    def refresh_all(self):
        self.refresh()
        self.refresh_limits()

    def refresh_limits(self):
        """Fetch the Claude limits on their own thread, or read the status line's local file when no
        limits_url is set. Does nothing when show_limits is off."""
        if not self.cfg.show_limits:
            return
        if not self.cfg.limits_url:
            self._read_local_limits()
            return
        if self.limits_thread is not None and self.limits_thread.isRunning():
            return
        self.limits_thread = LimitsThread(self.cfg.limits_url)
        self.limits_thread.done.connect(self._limits_fetched)
        self.limits_thread.start()

    LOCAL_LIMITS_MAX = 64 * 1024

    def _read_local_limits(self):
        """claude-limits.json, written by `python -m fumes.statusline` in this folder. A few hundred bytes,
        so it is read on the UI thread. No file yet means no reading, not a failure."""
        from . import statusline
        path = self.folder / statusline.FILE
        try:
            with open(path, "rb") as f:
                data = f.read(self.LOCAL_LIMITS_MAX + 1)
        except FileNotFoundError:
            return
        except OSError:
            self._limits_fetched(None, None, "unreadable")
            return
        try:
            if len(data) > self.LOCAL_LIMITS_MAX:
                raise ValueError("too large")
            raw = json.loads(data.decode("utf-8"))
            lim = limmod.parse_limits(raw)
        except (ValueError, UnicodeDecodeError, feedmod.FeedError):
            self._limits_fetched(None, None, "unreadable")
            return
        self._limits_fetched(lim, raw, "")

    def _limits_fetched(self, lim, raw, error):
        if lim is None:
            self.strip.set_limits_failed(True)    # keeps the figures; dims only the Claude cell
            return
        self.strip.set_limits(lim, failed=False)
        self.state.limits_cache = raw
        try:
            self.state.save()
        except OSError:
            pass

    def _fetched(self, feed, raw, error):
        if feed is None:
            self.strip.set_failed(True)
        else:
            self.strip.set_feed(feed, failed=False)
            self.state.cache = raw
            try:
                self.state.save()
            except OSError:
                pass
        self._update_tray_tip()

    def _update_tray_tip(self):
        f = self.strip.feed
        if f is None:
            self.tray.setToolTip("Fumes: no data yet")
            return
        total = feedmod.total_credit(f.providers)
        credit = f"{fmt.money(total)} credit" if total is not None else "no balances"
        age = fmt.age_text(feedmod.age_seconds(f))
        self.tray.setToolTip(f"Fumes: {credit}, {age}" + (" (offline)" if self.strip.fetch_failed else ""))
