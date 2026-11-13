"""The peek: a popup with one provider's details. All text is drawn as plain text (QPainter),
never as rich text: every string in the feed is untrusted network input."""

from PyQt6.QtCore import QPointF, QRectF, Qt, QTimer
from PyQt6.QtWidgets import QWidget
from PyQt6.QtGui import QColor, QFont, QFontMetrics, QImage, QPainter, QPainterPath, QPen

from . import feed as feedmod, fmt, gauge, limits as limmod, logos, place
from .config import CLAUDE_USAGE_URL  # noqa: F401
from . import links as linkmod

W = 340
PAD = 14
INNER = W - 2 * PAD
LOGO = 22
RADIUS = 8
CHART_H = 96
AXIS_BAND = 14
ROW_H = 24
MAX_NOTE_LINES_PX = 100000

THEMES = {
    "dark": dict(surface="#1f1f1f", border="#4a4a4a", ink="#f2f2f2", ink2="#b4b4b4", grid="#333333"),
    "light": dict(surface="#fbfbfb", border="#c4c4c4", ink="#1c1c1c", ink2="#5a5a5a", grid="#e3e3e3"),
}
CHART_COLOR = {"dark": "#9a9a9a", "light": "#6e6e6e"}   # one neutral series colour for every provider
STATUS = {  # status -> (label, icon colour or None for secondary ink)
    "ok": ("Live", "#2e9e6b"),
    "stale": ("Stale", "#c98500"),
    "not_connected": ("Not connected", None),
    "error": ("Error", "#d0453a"),
}
CAPTION_PX, BODY_PX, TITLE_PX = 12, 13, 14    # caption, body, title (semibold)
LEAD_PX, SECOND_PX = 20, 14                   # the figure the strip shows (bold) and the other two (semibold)
LINK_PAD_Y = 6                                # a footer link's hit rect is this much taller each side
SEMI = QFont.Weight.DemiBold


LIMITS_KEY = ("limits", "claude")
LIMITS_BAR_H = 8
LIMITS_TICK_MS = 30_000     # keeps the countdown current while the limits peek is open
LIMITS_NOTE = "Readings come from your Claude Code sessions, so they update only while one is active."


def outside_press_action(over_strip, cell_id, current_id):
    return place.outside_press(over_strip, cell_id, current_id)


class Peek(QWidget):
    def __init__(self, strip):
        super().__init__(None, Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.NoDropShadowWindowHint)
        self.strip = strip
        self.key = None
        self._p = None
        self._hover_day = None
        self._hover_link = None
        self._logo_cache = {}
        self._pressed_link = None
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setMouseTracking(True)
        self.setFixedSize(W, 100)
        self._tick = QTimer(self)
        self._tick.setInterval(LIMITS_TICK_MS)
        self._tick.timeout.connect(self._tick_limits)

    @property
    def pid(self):
        """The provider id shown, or None for the limits peek."""
        return self.key[1] if self.key and self.key[0] == "provider" else None

    @property
    def is_limits(self):
        return self.key == LIMITS_KEY

    def showEvent(self, ev):
        self._tick.start()
        super().showEvent(ev)

    def hideEvent(self, ev):
        self._tick.stop()
        super().hideEvent(ev)

    def _tick_limits(self):
        if self.is_limits and self.isVisible():
            self._build()

    # ---- model -----------------------------------------------------------
    def _lookup(self, pid):
        feed = self.strip.feed
        if feed is None:
            return None
        return next((p for p in feed.providers if p.id == pid), None)

    def set_provider(self, pid):
        p = self._lookup(pid)
        if p is None:
            return False
        self.key, self._p = ("provider", pid), p
        self._hover_day = self._hover_link = None
        self._build()
        return True

    def set_limits(self):
        """Show the Claude limits layout. Needs the limits cell to exist (show_limits on)."""
        if not self.strip.cfg.show_limits:
            return False
        self.key, self._p = LIMITS_KEY, None
        self._hover_day = self._hover_link = None
        self._build()
        return True

    def set_key(self, key):
        return self.set_limits() if key == LIMITS_KEY else self.set_provider(key[1])

    def refresh(self):
        """New data arrived: redraw from it, or close when what it shows is gone."""
        if self.is_limits:
            if not self.strip.cfg.show_limits:
                self.close()
                return
        else:
            p = self._lookup(self.pid)
            if p is None:
                self.close()
                return
            self._p = p
        self._build()
        self.reposition()

    def title(self):
        if self.is_limits:
            return "Claude subscription"
        return self._p.name if self._p else ""

    def show_provider(self, pid):
        return self.show_key(("provider", pid))

    def show_key(self, key):
        if not self.set_key(key):
            return False
        self.reposition()
        if not self.isVisible():
            self.show()
        return True

    def reposition(self):
        s = self.strip
        cell = next((c for c in s.cells() if c.key == self.key), None)
        if cell is None:
            return
        from PyQt6.QtCore import QPoint
        g = s.mapToGlobal(QPoint(0, 0))
        strip_r = place.Rect(g.x(), g.y(), g.x() + s.width(), g.y() + s.height())
        cx = g.x() + cell.x + cell.w / 2
        sg = s.screen().geometry()
        screen_r = place.Rect(sg.left(), sg.top(), sg.left() + sg.width(), sg.top() + sg.height())
        x, y = place.peek_position(cx, strip_r, (self.width(), self.height()), screen_r)
        self.move(x, y)

    # ---- theme and fonts ---------------------------------------------------
    @property
    def theme(self):
        return self.strip.theme

    def col(self, name):
        return QColor(THEMES[self.theme][name])

    def _font(self, px, weight=QFont.Weight.Normal):
        f = QFont(self.strip.font())
        f.setPixelSize(px)
        f.setWeight(weight)
        return f

    # ---- layout ------------------------------------------------------------
    def _build(self):
        if self.is_limits:
            self._build_limits()
        else:
            self._build_provider()

    def _build_provider(self):
        p = self._p
        mode = feedmod.peek_mode(p)
        L = {"mode": mode}
        cap_h = QFontMetrics(self._font(CAPTION_PX)).height()
        body_h = QFontMetrics(self._font(BODY_PX)).height()
        lead_fm = QFontMetrics(self._font(LEAD_PX, QFont.Weight.Bold))
        y = PAD
        L["header"] = QRectF(PAD, y, INNER, LOGO)
        y += LOGO + 16
        if mode != "empty":      # nothing to show: header, the reason, footer
            L["fig_cap_y"] = y
            L["fig_base"] = y + cap_h + 2 + lead_fm.ascent()
            y += cap_h + 2 + lead_fm.height() + 18
        L["rows"] = []
        if mode == "full":
            L["chart_cap_y"] = y
            y += cap_h + 6
            L["chart_top"] = y
            L["plot_top"] = y + AXIS_BAND
            L["base"] = y + CHART_H
            y += CHART_H + 3
            L["dates_y"] = y
            y += cap_h + 6
            L["peak_y"] = y
            y += body_h + 16
            rows = fmt.fold_breakdown(p.breakdown)
            L["rows"] = rows
            if rows:
                L["rows_cap_y"] = y
                y += cap_h + 4
                L["rows_y"] = y
                y += ROW_H * len(rows) + 14
        bf = QFontMetrics(self._font(BODY_PX))
        paras = [fmt.break_long_words(fmt.clean_text(t), lambda w: bf.horizontalAdvance(w) <= INNER)
                 for t in (p.note, p.error) if t]
        L["paras"] = []
        for t in paras:
            r = bf.boundingRect(QRectF(0, 0, INNER, MAX_NOTE_LINES_PX).toRect(), self._wrap_flags(), t)
            L["paras"].append((y, r.height(), t))
            y += r.height() + 6
        if paras:
            y += 8
        L["rule_y"] = y
        y += 11
        links = self._links()
        ff = QFontMetrics(self._font(CAPTION_PX))
        L["checked"] = fmt.checked_text(p.checked, self.strip.now_fn(), self.strip.tz)
        sep_w = ff.horizontalAdvance(" · ")
        widths = [ff.horizontalAdvance(t) for t, _ in links]
        total = sum(widths) + sep_w * max(0, len(links) - 1)
        L["footer_y"] = L["links_y"] = y          # always one row: the strings are short enough for 340 px
        L["checked_w"] = ff.horizontalAdvance(L["checked"])
        y += cap_h
        L["links"] = []
        x = W - PAD - total
        for (t, url), w in zip(links, widths):
            L["links"].append((QRectF(x, L["links_y"], w, cap_h), t, url))
            x += w + sep_w
        L["sep_w"] = sep_w
        L["height"] = y + PAD
        self._L = L
        self.setFixedSize(W, L["height"])
        self.update()

    def _wrap_flags(self):
        return int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop
                   | Qt.TextFlag.TextWordWrap)

    def _links(self):
        p, cfg = self._p, self.strip.cfg
        out = []
        url = cfg.links.get(p.id)
        if url:
            out.append(("Console", url))
        if cfg.details_url:
            out.append(("Details", cfg.details_url))
        return out

    def values(self):
        days = self.strip.feed.days if self.strip.feed else []
        return days, [self._p.daily.get(d, 0.0) for d in days]

    def _paint_links(self, p, L, ink, ink2):
        """Footer links: secondary ink with a persistent 1 px underline, primary ink under the pointer."""
        fm = QFontMetrics(self._font(CAPTION_PX))
        for i, (r, t, url) in enumerate(L["links"]):
            col = ink if self._hover_link == i else ink2
            self._text(p, r.left(), r.top(), r.width() + 2, r.height(), t, self._font(CAPTION_PX), col)
            p.fillRect(QRectF(r.left(), r.top() + fm.ascent() + 2, r.width(), 1), col)
            if i < len(L["links"]) - 1:
                self._text(p, r.right(), r.top(), L["sep_w"], r.height(), " · ", self._font(CAPTION_PX), ink2)

    def _text_base(self, p, x, base, w, text, font, color):
        """Text on a baseline, elided to w (so figures of different sizes share one baseline)."""
        text = QFontMetrics(font).elidedText(text, Qt.TextElideMode.ElideRight, int(w))
        p.setFont(font)
        p.setPen(color)
        p.drawText(QPointF(x, base), text)

    # ---- painting ------------------------------------------------------------
    def _logo_pixmap(self, dpr):
        ink = self.col("ink").name()
        key = (self.key, ink, dpr)
        pm = self._logo_cache.get(key)
        if pm is None:
            pm = logos.render_logo(self.key[1], self._p.name if self._p else "Claude", ink, dpr, LOGO,
                                   self.strip.user_logo_dir, self._font(BODY_PX))
            self._logo_cache[key] = pm
        return pm

    def _text(self, p, x, y, w, h, text, font, color, align=Qt.AlignmentFlag.AlignLeft, elide=None):
        p.setFont(font)
        p.setPen(color)
        if elide is not None:
            text = QFontMetrics(font).elidedText(text, elide, int(w))
        p.drawText(QRectF(x, y, w, h), int(align | Qt.AlignmentFlag.AlignVCenter), text)

    def _icon(self, p, kind, cx, cy, color):
        pen = QPen(color, 1.6)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        if kind == "ok":
            p.drawPolyline([QPointF(cx - 4.5, cy + 0.5), QPointF(cx - 1.5, cy + 3.5), QPointF(cx + 4.5, cy - 3.5)])
        elif kind == "stale":
            p.drawPolygon([QPointF(cx, cy - 5), QPointF(cx + 5.5, cy + 4.5), QPointF(cx - 5.5, cy + 4.5)])
            p.drawLine(QPointF(cx, cy - 1.5), QPointF(cx, cy + 1.5))
            p.drawPoint(QPointF(cx, cy + 3.2))
        elif kind == "not_connected":
            p.drawEllipse(QPointF(cx, cy), 4.5, 4.5)
        else:
            p.drawLine(QPointF(cx - 4, cy - 4), QPointF(cx + 4, cy + 4))
            p.drawLine(QPointF(cx - 4, cy + 4), QPointF(cx + 4, cy - 4))

    def paint_to(self, p, dpr):
        if self.is_limits:
            self._paint_limits(p, dpr)
        elif self._p is not None:
            self._paint_provider(p, dpr)

    def _paint_provider(self, p, dpr):
        L, pv = self._L, self._p
        ink, ink2, grid = self.col("ink"), self.col("ink2"), self.col("grid")
        p.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.TextAntialiasing
                         | QPainter.RenderHint.SmoothPixmapTransform)
        # surface
        p.setPen(QPen(self.col("border"), 1))
        p.setBrush(self.col("surface"))
        p.drawRoundedRect(QRectF(0.5, 0.5, W - 1, self.height() - 1), RADIUS, RADIUS)
        cap_h = QFontMetrics(self._font(CAPTION_PX)).height()
        body_h = QFontMetrics(self._font(BODY_PX)).height()
        # header
        hy = L["header"].top()
        p.drawPixmap(QPointF(PAD, hy), self._logo_pixmap(dpr))
        label, icol = STATUS[pv.status]
        chip_font = self._font(BODY_PX)
        chip_w = QFontMetrics(chip_font).horizontalAdvance(label)
        chip_x = W - PAD - chip_w
        self._text(p, chip_x, hy, chip_w + 2, LOGO, label, chip_font, ink2)
        self._icon(p, pv.status, chip_x - 5 - 7, hy + LOGO / 2, QColor(icol) if icol else ink2)
        name_x = PAD + LOGO + 8
        self._text(p, name_x, hy, chip_x - 22 - name_x, LOGO, pv.name, self._font(TITLE_PX, SEMI), ink,
                   elide=Qt.TextElideMode.ElideRight)
        # figures: the one the strip shows is the lead (20 px bold), the others 14 px semibold
        if L["mode"] != "empty":
            colw = INNER / 3
            lead = feedmod.lead_index(pv)
            figs = [("Credit left", pv.balance), ("Month to date", pv.mtd), ("Last 30 days", pv.d30)]
            for i, (cap, v) in enumerate(figs):
                x = PAD + i * colw
                self._text(p, x, L["fig_cap_y"], colw - 4, cap_h, cap, self._font(CAPTION_PX), ink2)
                if v is None:
                    self._text_base(p, x, L["fig_base"], colw - 4, fmt.DASH, self._font(SECOND_PX), ink2)
                elif i == lead:
                    self._text_base(p, x, L["fig_base"], colw - 4, fmt.money(v),
                                    self._font(LEAD_PX, QFont.Weight.Bold), ink)
                else:
                    self._text_base(p, x, L["fig_base"], colw - 4, fmt.money(v), self._font(SECOND_PX, SEMI), ink)
        if L["mode"] == "full":
            self._text(p, PAD, L["chart_cap_y"], INNER, cap_h, "Daily spend, last 30 days",
                       self._font(CAPTION_PX), ink2)
            days, vals = self.values()
            self._paint_chart(p, days, vals, grid, ink, ink2, cap_h)
            self._text(p, PAD, L["peak_y"], INNER, body_h,
                       fmt.chart_caption(days, vals, self._hover_day, pv.status != "not_connected"),
                       self._font(BODY_PX), ink, elide=Qt.TextElideMode.ElideRight)
            rows = L["rows"]
            if rows:
                self._text(p, PAD, L["rows_cap_y"], INNER, cap_h, "Last 30 days, by model or line",
                           self._font(CAPTION_PX), ink2)
                bf = self._font(BODY_PX)
                for i, (lab, usd) in enumerate(rows):
                    ry = L["rows_y"] + i * ROW_H
                    if i:
                        p.fillRect(QRectF(PAD, ry, INNER, 1), grid)
                    amt = fmt.money(usd)
                    aw = QFontMetrics(bf).horizontalAdvance(amt)
                    self._text(p, PAD, ry, INNER - aw - 12, ROW_H, lab, bf, ink,
                               elide=Qt.TextElideMode.ElideMiddle)
                    self._text(p, PAD + INNER - aw - 1, ry, aw + 1, ROW_H, amt, bf, ink,
                               align=Qt.AlignmentFlag.AlignRight)
        # note / error: when there is nothing else to show it is the answer, so primary ink
        p.setFont(self._font(BODY_PX))
        p.setPen(ink if L["mode"] == "empty" else ink2)
        for y, h, t in L["paras"]:
            p.drawText(QRectF(PAD, y, INNER, h), self._wrap_flags(), t)
        # footer
        p.fillRect(QRectF(PAD, L["rule_y"], INNER, 1), grid)
        self._text(p, PAD, L["footer_y"], INNER, cap_h, L["checked"], self._font(CAPTION_PX), ink2)
        self._paint_links(p, L, ink, ink2)

    # ---- the Claude limits layout ---------------------------------------------------
    BLOCKS = (("5-hour limit", "five_hour"), ("Weekly limit", "seven_day"))

    def _limits_views(self):
        lim, now = self.strip.limits, self.strip.now_fn()
        return [(cap, limmod.view(getattr(lim, attr, None) if lim else None, now.timestamp()))
                for cap, attr in self.BLOCKS]

    def _build_limits(self):
        L = {}
        cap_h = QFontMetrics(self._font(CAPTION_PX)).height()
        body_h = QFontMetrics(self._font(BODY_PX)).height()
        now, tz = self.strip.now_fn(), self.strip.tz
        y = PAD
        L["header"] = QRectF(PAD, y, INNER, LOGO)
        y += LOGO + 16
        L["blocks"] = []
        for cap, v in self._limits_views():
            hot = limmod.is_hot(v.used)       # at 90 % or more the reset time is the answer: body size, primary ink
            line_h = body_h if hot else cap_h
            row = dict(cap=cap, v=v, y=y, bar_y=y + body_h + 6, hot=hot, line_h=line_h,
                       line_y=y + body_h + 6 + LIMITS_BAR_H + 6, line=fmt.reset_line(v, now, tz))
            L["blocks"].append(row)
            y = row["line_y"] + line_h + 16
        bf = QFontMetrics(self._font(BODY_PX))
        note = fmt.break_long_words(LIMITS_NOTE, lambda w: bf.horizontalAdvance(w) <= INNER)
        r = bf.boundingRect(QRectF(0, 0, INNER, MAX_NOTE_LINES_PX).toRect(), self._wrap_flags(), note)
        L["note"] = (y, r.height(), note)
        y += r.height() + 14
        L["rule_y"] = y
        y += 11
        ff = QFontMetrics(self._font(CAPTION_PX))
        lim = self.strip.limits
        L["checked"] = "Updated " + (fmt.local_dt(lim.updated, tz) if lim else "never")
        L["footer_y"] = L["links_y"] = y
        text = "Usage page"
        w = ff.horizontalAdvance(text)
        L["links"] = [(QRectF(W - PAD - w, y, w, cap_h), text, self.strip.cfg.limits_link)]
        L["sep_w"] = ff.horizontalAdvance(" · ")
        L["height"] = y + cap_h + PAD
        self._L = L
        self.setFixedSize(W, L["height"])
        self.update()

    def _paint_limits(self, p, dpr):
        L = self._L
        ink, ink2, grid = self.col("ink"), self.col("ink2"), self.col("grid")
        p.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.TextAntialiasing
                         | QPainter.RenderHint.SmoothPixmapTransform)
        p.setPen(QPen(self.col("border"), 1))
        p.setBrush(self.col("surface"))
        p.drawRoundedRect(QRectF(0.5, 0.5, W - 1, self.height() - 1), RADIUS, RADIUS)
        cap_h = QFontMetrics(self._font(CAPTION_PX)).height()
        body_h = QFontMetrics(self._font(BODY_PX)).height()
        hy = L["header"].top()
        p.drawPixmap(QPointF(PAD, hy), self._logo_pixmap(dpr))
        name_x, name_w = PAD + LOGO + 8, INNER - LOGO - 8
        if self.strip.limits_failed:
            label, icol = STATUS["stale"]
            cf = self._font(BODY_PX)
            cw = QFontMetrics(cf).horizontalAdvance(label)
            cx = W - PAD - cw
            self._text(p, cx, hy, cw + 2, LOGO, label, cf, ink2)
            self._icon(p, "stale", cx - 5 - 7, hy + LOGO / 2, QColor(icol))
            name_w = cx - 22 - name_x
        self._text(p, name_x, hy, name_w, LOGO, "Claude subscription", self._font(TITLE_PX, SEMI), ink,
                   elide=Qt.TextElideMode.ElideRight)
        for b in L["blocks"]:
            v = b["v"]
            self._text(p, PAD, b["y"], INNER / 2, body_h, b["cap"], self._font(BODY_PX), ink2)
            used = "n/a" if v.used is None else f"{limmod.percent_text(v.used)} used"
            self._text(p, PAD + INNER / 2, b["y"], INNER / 2, body_h, used, self._font(BODY_PX, QFont.Weight.Bold), ink,
                       align=Qt.AlignmentFlag.AlignRight)
            track = QRectF(PAD, b["bar_y"], INNER, LIMITS_BAR_H)
            gauge.paint(p, track, LIMITS_BAR_H / 2, v.used, self.theme, ink, vertical=False, dpr=dpr)
            self._text(p, PAD, b["line_y"], INNER, b["line_h"], b["line"],
                       self._font(BODY_PX if b["hot"] else CAPTION_PX), ink if b["hot"] else ink2,
                       elide=Qt.TextElideMode.ElideRight)
        y, h, t = L["note"]
        p.setFont(self._font(BODY_PX))
        p.setPen(ink2)
        p.drawText(QRectF(PAD, y, INNER, h), self._wrap_flags(), t)
        p.fillRect(QRectF(PAD, L["rule_y"], INNER, 1), grid)
        self._text(p, PAD, L["footer_y"], INNER, cap_h, L["checked"], self._font(CAPTION_PX), ink2)
        self._paint_links(p, L, ink, ink2)

    def _slot(self, n):
        return INNER / n if n else INNER

    def _paint_chart(self, p, days, vals, grid, ink, ink2, cap_h):
        L = self._L
        base, ptop = L["base"], L["plot_top"]
        ph = base - ptop
        top = fmt.nice_top(max(vals)) if vals and max(vals) > 0 else None
        p.fillRect(QRectF(PAD, base, INNER, 1), grid)
        if days:
            self._text(p, PAD, base + 3, INNER / 2, cap_h, fmt.short_date(days[0]),
                       self._font(CAPTION_PX), ink2)
            self._text(p, PAD + INNER / 2, base + 3, INNER / 2, cap_h, fmt.short_date(days[-1]),
                       self._font(CAPTION_PX), ink2, align=Qt.AlignmentFlag.AlignRight)
        if top is None:
            return
        p.fillRect(QRectF(PAD, ptop, INNER, 1), grid)
        self._text(p, PAD, ptop - AXIS_BAND, INNER, AXIS_BAND - 1, fmt.axis_money(top),
                   self._font(CAPTION_PX), ink2)
        slot = self._slot(len(days))
        bw = max(2.0, slot * 0.62)
        color = QColor(CHART_COLOR[self.theme])
        for i, v in enumerate(vals):
            if v <= 0:
                continue
            h = max(1.0, v / top * (ph - 1))
            x = PAD + i * slot + (slot - bw) / 2
            r = min(3.0, h, bw / 2)
            t = base - h
            path = QPainterPath()
            path.moveTo(x, base)
            path.lineTo(x, t + r)
            path.arcTo(QRectF(x, t, 2 * r, 2 * r), 180, -90)
            path.lineTo(x + bw - r, t)
            path.arcTo(QRectF(x + bw - 2 * r, t, 2 * r, 2 * r), 90, -90)
            path.lineTo(x + bw, base)
            path.closeSubpath()
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(ink if self._hover_day == i else color)     # the hovered day in full primary ink
            p.drawPath(path)
        p.setOpacity(1.0)

    def paintEvent(self, _ev):
        p = QPainter(self)
        self.paint_to(p, self.devicePixelRatioF())
        p.end()

    def render_png(self, path, dpr=1.0, margin=14):
        bg = QColor({"dark": "#202020", "light": "#f3f3f3"}[self.theme])
        img = QImage(round((W + 2 * margin) * dpr), round((self.height() + 2 * margin) * dpr),
                     QImage.Format.Format_ARGB32)
        img.setDevicePixelRatio(dpr)
        img.fill(bg)
        p = QPainter(img)
        p.translate(margin, margin)
        self.paint_to(p, dpr)
        p.end()
        return img.save(str(path))

    # ---- hit testing ---------------------------------------------------------
    def day_at(self, x, y):
        L = self._L
        days = self.strip.feed.days if self.strip.feed else []
        if self.is_limits or not days or not (L["chart_top"] <= y <= L["base"] + 1) or not (PAD <= x < PAD + INNER):
            return None
        return min(len(days) - 1, int((x - PAD) / self._slot(len(days))))

    def link_at(self, x, y):
        for i, (r, _t, _u) in enumerate(self._L["links"]):
            if r.adjusted(-2, -LINK_PAD_Y, 2, LINK_PAD_Y).contains(x, y):
                return i
        return None

    def mouseMoveEvent(self, ev):
        pos = ev.position()
        day, link = self.day_at(pos.x(), pos.y()), self.link_at(pos.x(), pos.y())
        if (day, link) != (self._hover_day, self._hover_link):
            self._hover_day, self._hover_link = day, link
            self.setCursor(Qt.CursorShape.PointingHandCursor if link is not None
                           else Qt.CursorShape.ArrowCursor)
            self.update()

    def leaveEvent(self, _ev):
        if self._hover_day is not None or self._hover_link is not None:
            self._hover_day = self._hover_link = None
            self.update()

    def mousePressEvent(self, ev):
        pos = ev.position()
        if not self.rect().contains(pos.toPoint()):
            # A popup grabs the mouse; Qt hands a press outside it to this widget.
            over, cell = self.strip.cell_at_global(ev.globalPosition())
            act = outside_press_action(over, cell, self.key)
            if act.startswith("switch:"):
                self._switch(cell)
            else:
                self.close()
            return
        self._pressed_link = self.link_at(pos.x(), pos.y())

    def _switch(self, key):
        self.show_key(key)
        # If Qt closes the popup after this handler, bring it straight back.
        QTimer.singleShot(0, lambda: None if self.isVisible() else self.show_key(key))

    def mouseReleaseEvent(self, ev):
        pos = ev.position()
        i = self.link_at(pos.x(), pos.y())
        if i is not None and i == self._pressed_link:
            url = self._L["links"][i][2]
            self.close()
            linkmod.open_link(self.strip.cfg, url)
        self._pressed_link = None

    def keyPressEvent(self, ev):
        if ev.key() == Qt.Key.Key_Escape:
            self.close()
        else:
            super().keyPressEvent(ev)
