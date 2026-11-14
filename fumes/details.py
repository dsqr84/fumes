"""The Details window: totals, a 30-day stacked spend chart (or its table) and one card per provider.

Everything that comes from the feed (names, labels, notes, errors) is drawn as plain text with
QPainter.drawText; there is no rich text and no link anywhere. The chart tooltip is escaped."""

from datetime import datetime, timezone

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QColor, QFont, QFontMetrics, QImage, QPainter, QPen
from PyQt6.QtWidgets import QPushButton, QScrollArea, QToolTip, QWidget

from . import feed as feedmod, fmt, fonts, guard
from .peek import STATUS, THEMES

WIN_W, WIN_H = 1040, 760
MARGIN = 28
GAP = 16
NARROW = 900                  # content width under which the cards go two to a row
CARD_PAD = 16
CHART_H = 200
AXIS_W = 46
ROW_H = 22
TABLE_ROW_H = 24
MAX_ROWS = 6                  # breakdown rows on a card (the rest fold into "Other")
BG = {"dark": "#161616", "light": "#f3f3f3"}
SEMI = QFont.Weight.DemiBold

# provider id -> (dark theme, light theme); every other id takes EXTRA_COLORS in feed order
PROVIDER_COLORS = {
    "deepseek": ("#6CA0F5", "#2F6FD6"),
    "openai": ("#F08A5D", "#C9532A"),
    "fal": ("#3CC48C", "#168A5A"),
    "anthropic": ("#E9B949", "#A8780E"),
}
EXTRA_COLORS = [("#B48EF0", "#7B4FC4"), ("#E07AA6", "#B0426F"), ("#5FC6D2", "#23808C")]


# ---- model (no Qt painting) ---------------------------------------------------------------
def provider_colors(feed, theme):
    """{provider id: QColor} for every provider in the feed."""
    i = 0 if theme == "dark" else 1
    out, extra = {}, 0
    for p in feed.providers:
        if feedmod.is_hidden(p):
            continue                 # a hidden provider has no bar, card or column: it takes no colour
        if p.id in PROVIDER_COLORS:
            out[p.id] = QColor(PROVIDER_COLORS[p.id][i])
        else:
            out[p.id] = QColor(EXTRA_COLORS[extra % len(EXTRA_COLORS)][i])
            extra += 1
    return out


def totals(feed):
    """Month to date and last 30 days (ok or stale providers only), credit (any balance)."""
    spend = [p for p in feed.providers if p.status in ("ok", "stale")]
    mtd = [p.mtd for p in spend if p.mtd is not None]
    d30 = [p.d30 for p in spend if p.d30 is not None]
    held = [p for p in feed.providers if p.balance is not None]
    return dict(mtd=sum(mtd) if mtd else None, d30=sum(d30) if d30 else None,
                credit=sum(p.balance for p in held) if held else None,
                credit_list=[(fmt.clean_text(p.name), p.balance) for p in held])


def day_segments(feed):
    """One list per day of [(provider id, usd)], provider order, only amounts above zero."""
    return [[(p.id, p.daily.get(d, 0.0)) for p in feed.providers if p.daily.get(d, 0.0) > 0] for d in feed.days]


def spending_providers(feed):
    return [p for p in feed.providers if any(v > 0 for v in p.daily.values())]


def table_data(feed):
    """(header, rows): rows newest day first; provider columns then Total; text only."""
    cols = spending_providers(feed)
    header = ["Day"] + [fmt.clean_text(p.name) for p in cols] + ["Total"]
    rows = []
    for d in reversed(feed.days):
        vals = [p.daily.get(d, 0.0) for p in cols]
        total = sum(p.daily.get(d, 0.0) for p in feed.providers)
        rows.append([fmt.short_date(d)] + [fmt.money(v) if v > 0 else fmt.DASH for v in vals] + [fmt.money(total)])
    return header, rows


def updated_text(dt, now, tz=None):
    return fmt.checked_text(dt.isoformat(), now, tz).replace("Checked", "Updated", 1)


def x_label_indices(n):
    """The first and last day plus about every 7th; one too near the end is dropped."""
    if n == 0:
        return []
    idx = [i for i in range(0, n - 3, 7)]
    return idx + [n - 1] if (n - 1) not in idx else idx


# ---- the page -------------------------------------------------------------------------------
class Page(QWidget):
    def __init__(self, owner):
        super().__init__()
        self.owner = owner
        self.setMouseTracking(True)
        self._L = None
        self._hover = None
        self.toggle = QPushButton("Show as table", self)
        self.toggle.setCursor(Qt.CursorShape.PointingHandCursor)
        self.toggle.clicked.connect(lambda: owner.set_table(not owner.table))
        self.toggle.hide()

    # -- fonts and colours
    def font_px(self, px, weight=QFont.Weight.Normal):
        f = QFont()
        fam = fonts.family()
        if fam:
            f.setFamily(fam)
        f.setPixelSize(px)
        f.setWeight(weight)
        return f

    def col(self, name):
        return QColor(THEMES[self.owner.theme][name])

    def fm(self, px, weight=QFont.Weight.Normal):
        return QFontMetrics(self.font_px(px, weight))

    def wrap(self, text, px, width):
        """(wrapped text, height) of untrusted text broken to fit `width`."""
        fm = self.fm(px)
        text = fmt.break_long_words(fmt.clean_text(text), lambda w: fm.horizontalAdvance(w) <= width)
        r = fm.boundingRect(QRectF(0, 0, width, 100000).toRect(), self._wrap_flags(), text)
        return text, r.height()

    @staticmethod
    def _wrap_flags():
        return int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop | Qt.TextFlag.TextWordWrap)

    # -- layout
    def relayout(self):
        o = self.owner
        feed = o.feed
        W = max(self.width(), 480)
        CW = W - 2 * MARGIN
        L = dict(W=W, CW=CW, bars=[], cards=[], tiles=[])
        y = 24
        L["title_y"] = y
        y += 30
        L["updated_y"] = y
        y += self.fm(12).height() + 22
        if feed is None:
            L["empty_y"] = y
            L["height"] = y + 80
            self._L = L
            self.toggle.hide()
            self.setMinimumHeight(L["height"])
            return
        # tiles
        t = totals(feed)
        tw = (CW - 2 * GAP) / 3
        small_h = self.fm(12).height()
        tiles = [("Month to date", t["mtd"], ""), ("Last 30 days", t["d30"], ""),
                 ("Credit left", t["credit"], " · ".join(f"{n} {fmt.money(b)}" for n, b in t["credit_list"]))]
        extra_h = 0
        for cap, v, small in tiles:
            if small:
                extra_h = max(extra_h, self.wrap(small, 12, tw - 2 * CARD_PAD)[1])
        th = CARD_PAD + self.fm(12).height() + 6 + self.fm(28, QFont.Weight.Bold).height() + 6 \
            + max(small_h, extra_h) + CARD_PAD
        for i, (cap, v, small) in enumerate(tiles):
            wrapped = self.wrap(small, 12, tw - 2 * CARD_PAD)[0] if small else ""
            L["tiles"].append((QRectF(MARGIN + i * (tw + GAP), y, tw, th), cap, v, wrapped))
        y += th + 28
        # chart section
        L["chart_head_y"] = y
        L["toggle_rect"] = QRectF(MARGIN + CW - 124, y - 4, 124, 30)
        y += 34
        cols = spending_providers(feed)
        L["legend"] = []
        lx, ly, lfm = MARGIN + CARD_PAD, y + CARD_PAD, self.fm(12)
        for p in cols:
            name = fmt.clean_text(p.name)
            w = 16 + lfm.horizontalAdvance(name)
            if lx + w > MARGIN + CW - CARD_PAD and lx > MARGIN + CARD_PAD:
                lx, ly = MARGIN + CARD_PAD, ly + lfm.height() + 6
            L["legend"].append((p.id, name, lx, ly))
            lx += w + 18
        legend_h = (ly + lfm.height() - (y + CARD_PAD)) if cols else 0
        cy = y + CARD_PAD + legend_h + (16 if cols else 0)
        if o.table:
            header, rows = table_data(feed)
            L["table"] = (header, rows, cy)
            ch = TABLE_ROW_H * (len(rows) + 1)
            L["chart_card"] = QRectF(MARGIN, y, CW, cy - y + ch + CARD_PAD)
            y = cy + ch + CARD_PAD
        else:
            segs = day_segments(feed)
            totals_ = [sum(v for _, v in s) for s in segs]
            top = fmt.nice_top(max(totals_)) if totals_ and max(totals_) > 0 else None
            L["plot"] = QRectF(MARGIN + CARD_PAD + AXIS_W, cy + 8, CW - 2 * CARD_PAD - AXIS_W, CHART_H)
            L["top"] = top
            L["segs"], L["day_totals"] = segs, totals_
            ch = 8 + CHART_H + 6 + self.fm(12).height()
            L["chart_card"] = QRectF(MARGIN, y, CW, cy - y + ch + CARD_PAD)
            y = cy + ch + CARD_PAD
        self.toggle.setText("Show as chart" if o.table else "Show as table")
        self.toggle.setGeometry(L["toggle_rect"].toRect())
        self.toggle.show()
        y += 28
        # provider cards
        L["prov_head_y"] = y
        y += 34
        shown = [p for p in feed.providers if not feedmod.is_hidden(p)]
        ncol = 3 if CW >= NARROW else 2
        cw = (CW - (ncol - 1) * GAP) / ncol
        inner = cw - 2 * CARD_PAD
        row_start, i = y, 0
        while i < len(shown):
            batch = shown[i:i + ncol]
            infos = [self._card_info(p, inner) for p in batch]
            h = max(c["h"] for c in infos)
            for j, c in enumerate(infos):
                c["rect"] = QRectF(MARGIN + j * (cw + GAP), row_start, cw, h)
                L["cards"].append(c)
            row_start += h + GAP
            i += ncol
        y = row_start if shown else y
        L["height"] = int(y + 12)
        self._L = L
        self.setMinimumHeight(L["height"])

    def _card_info(self, p, inner):
        c = dict(p=p, rows=fmt.fold_breakdown(p.breakdown, MAX_ROWS) if p.breakdown else [])
        y = CARD_PAD
        c["head_y"] = y
        y += 22 + 12
        c["fig_y"] = y
        y += self.fm(12).height() + 2 + self.fm(18, SEMI).height() + 14
        c["rule1"] = y
        y += 12
        if c["rows"]:
            c["rows_cap_y"] = y
            y += self.fm(12).height() + 4
            c["rows_y"] = y
            y += ROW_H * len(c["rows"]) + 8
        c["paras"] = []
        for text in (p.note, p.error):
            if text:
                wrapped, h = self.wrap(text, 13, inner)
                c["paras"].append((y, h, wrapped))
                y += h + 6
        y += 6
        c["rule2"] = y
        y += 10
        c["checked_y"] = y
        y += self.fm(12).height()
        c["h"] = y + CARD_PAD
        return c

    # -- painting
    def _t(self, p, x, y, w, h, text, font, color, align=Qt.AlignmentFlag.AlignLeft, elide=Qt.TextElideMode.ElideRight):
        text = QFontMetrics(font).elidedText(text, elide, int(w))
        p.setFont(font)
        p.setPen(color)
        p.drawText(QRectF(x, y, w, h), int(align | Qt.AlignmentFlag.AlignVCenter), text)

    def _box(self, p, rect):
        p.setPen(QPen(self.col("border"), 1))
        p.setBrush(self.col("surface"))
        p.drawRoundedRect(rect.adjusted(0.5, 0.5, -0.5, -0.5), 8, 8)

    def paintEvent(self, _ev):
        p = QPainter(self)
        try:
            if self._L is None:
                self.relayout()
            self.paint_to(p)
        except Exception:               # noqa: BLE001 — under PyQt6 a raise here aborts the whole app
            guard.paint_failed(self)
        finally:
            p.end()

    def paint_to(self, p):
        o, L = self.owner, self._L
        feed = o.feed
        ink, ink2, grid = self.col("ink"), self.col("ink2"), self.col("grid")
        p.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.TextAntialiasing)
        p.fillRect(QRectF(0, 0, L["W"], max(self.height(), L["height"])), QColor(BG[o.theme]))
        self._t(p, MARGIN, L["title_y"], L["CW"], 30, "AI spend", self.font_px(22, QFont.Weight.Bold), ink)
        if feed is None:
            self._t(p, MARGIN, L["empty_y"], L["CW"], 30, "No figures yet", self.font_px(14), ink2)
            return
        self._t(p, MARGIN, L["updated_y"], L["CW"], self.fm(12).height(),
                updated_text(feed.generated, o.now_fn(), o.tz), self.font_px(12), ink2)
        for rect, cap, v, small in L["tiles"]:
            self._box(p, rect)
            x, w = rect.left() + CARD_PAD, rect.width() - 2 * CARD_PAD
            y = rect.top() + CARD_PAD
            self._t(p, x, y, w, self.fm(12).height(), cap, self.font_px(12), ink2)
            y += self.fm(12).height() + 6
            self._t(p, x, y, w, self.fm(28, QFont.Weight.Bold).height(), fmt.DASH if v is None else fmt.money(v),
                    self.font_px(28, QFont.Weight.Bold), ink if v is not None else ink2)
            y += self.fm(28, QFont.Weight.Bold).height() + 6
            if small:
                p.setFont(self.font_px(12))
                p.setPen(ink2)
                p.drawText(QRectF(x, y, w, rect.bottom() - y), self._wrap_flags(), small)
        self._t(p, MARGIN, L["chart_head_y"], L["CW"] - 140, 22, "Daily spend, last 30 days",
                self.font_px(14, SEMI), ink)
        self._box(p, L["chart_card"])
        colors = provider_colors(feed, o.theme)
        for pid, name, x, y in L["legend"]:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(colors.get(pid, ink2))
            p.drawRoundedRect(QRectF(x, y + 2, 10, 10), 2, 2)
            self._t(p, x + 16, y, 400, self.fm(12).height(), name, self.font_px(12), ink2)
        if o.table:
            self._paint_table(p, L, ink, ink2, grid)
        else:
            self._paint_chart(p, L, colors, ink, ink2, grid)
        self._t(p, MARGIN, L["prov_head_y"], L["CW"], 22, "Providers", self.font_px(14, SEMI), ink)
        for c in L["cards"]:
            self._paint_card(p, c, ink, ink2, grid)

    def _paint_chart(self, p, L, colors, ink, ink2, grid):
        feed, plot, top = self.owner.feed, L["plot"], L["top"]
        cap = self.fm(12).height()
        base = plot.bottom()
        p.fillRect(QRectF(plot.left(), base, plot.width(), 1), grid)
        if top is not None:
            for frac in (0.5, 1.0):
                gy = base - frac * (plot.height() - 1)
                p.fillRect(QRectF(plot.left(), gy, plot.width(), 1), grid)
                self._t(p, MARGIN + CARD_PAD, gy - cap / 2, AXIS_W - 6, cap, fmt.axis_money(top * frac),
                        self.font_px(12), ink2, align=Qt.AlignmentFlag.AlignRight)
        n = len(feed.days)
        slot = plot.width() / n if n else plot.width()
        bw = max(2.0, slot * 0.62)
        L["bars"] = []
        for i, segs in enumerate(L["segs"]):
            x = plot.left() + i * slot + (slot - bw) / 2
            L["bars"].append((x, bw))
            if self._hover == i:
                p.fillRect(QRectF(plot.left() + i * slot, plot.top(), slot, plot.height()), grid)
            if top is None:
                continue
            y = base
            for pid, v in segs:
                h = max(1.0, v / top * (plot.height() - 1))
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(colors.get(pid, ink2))
                p.drawRect(QRectF(x, y - h, bw, max(0.5, h - 1)))
                y -= h
        for i in x_label_indices(n):
            text = fmt.short_date(feed.days[i])
            w = self.fm(12).horizontalAdvance(text)
            cx = plot.left() + (i + 0.5) * slot
            lx = min(max(cx - w / 2, plot.left()), plot.right() - w)
            self._t(p, lx, base + 4, w + 2, cap, text, self.font_px(12), ink2)

    def _paint_table(self, p, L, ink, ink2, grid):
        header, rows, y0 = L["table"]
        x0, w = MARGIN + CARD_PAD, L["CW"] - 2 * CARD_PAD
        n = len(header)
        first = 100
        cw = (w - first) / max(1, n - 1)
        for r, row in enumerate([header] + rows):
            y = y0 + r * TABLE_ROW_H
            if r:
                p.fillRect(QRectF(x0, y, w, 1), grid)
            for c, text in enumerate(row):
                head = r == 0
                font = self.font_px(12 if head else 13, SEMI if (head or c == n - 1) else QFont.Weight.Normal)
                color = ink2 if head or text == fmt.DASH else ink
                if c == 0:
                    self._t(p, x0, y, first - 8, TABLE_ROW_H, text, font, color)
                else:
                    self._t(p, x0 + first + (c - 1) * cw, y, cw - 8, TABLE_ROW_H, text, font, color,
                            align=Qt.AlignmentFlag.AlignRight)

    def _paint_card(self, p, c, ink, ink2, grid):
        pv, rect = c["p"], c["rect"]
        self._box(p, rect)
        x, w = rect.left() + CARD_PAD, rect.width() - 2 * CARD_PAD
        top = rect.top()
        label, icol = STATUS[pv.status]
        sfont = self.font_px(13)
        sw = QFontMetrics(sfont).horizontalAdvance(label)
        self._t(p, x + w - sw, top + c["head_y"], sw + 2, 22, label, sfont, ink2)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(icol) if icol else ink2)
        p.drawEllipse(QPointF(x + w - sw - 10, top + c["head_y"] + 11), 4, 4)
        self._t(p, x, top + c["head_y"], w - sw - 26, 22, fmt.clean_text(pv.name), self.font_px(15, SEMI), ink)
        colw = w / 3
        figs = [("Month to date", pv.mtd), ("Last 30 days", pv.d30), ("Credit left", pv.balance)]
        for i, (cap, v) in enumerate(figs):
            fx = x + i * colw
            self._t(p, fx, top + c["fig_y"], colw - 4, self.fm(12).height(), cap, self.font_px(12), ink2)
            self._t(p, fx, top + c["fig_y"] + self.fm(12).height() + 2, colw - 4, self.fm(18, SEMI).height(),
                    fmt.DASH if v is None else fmt.money(v), self.font_px(18, SEMI), ink if v is not None else ink2)
        p.fillRect(QRectF(x, top + c["rule1"], w, 1), grid)
        if c["rows"]:
            self._t(p, x, top + c["rows_cap_y"], w, self.fm(12).height(), "Last 30 days, by model or line",
                    self.font_px(12), ink2)
            bf = self.font_px(13)
            for i, (lab, usd) in enumerate(c["rows"]):
                ry = top + c["rows_y"] + i * ROW_H
                if i:
                    p.fillRect(QRectF(x, ry, w, 1), grid)
                amt = fmt.money(usd)
                aw = QFontMetrics(bf).horizontalAdvance(amt)
                self._t(p, x, ry, w - aw - 12, ROW_H, fmt.clean_text(lab), bf, ink,
                        elide=Qt.TextElideMode.ElideMiddle)
                self._t(p, x + w - aw - 1, ry, aw + 1, ROW_H, amt, bf, ink, align=Qt.AlignmentFlag.AlignRight)
        p.setFont(self.font_px(13))
        p.setPen(ink2)
        for y, h, text in c["paras"]:
            p.drawText(QRectF(x, top + y, w, h), self._wrap_flags(), text)
        p.fillRect(QRectF(x, top + c["rule2"], w, 1), grid)
        self._t(p, x, top + c["checked_y"], w, self.fm(12).height(),
                fmt.checked_text(pv.checked, self.owner.now_fn(), self.owner.tz), self.font_px(12), ink2)

    # -- hover
    def day_at(self, x, y):
        L = self._L
        o = self.owner
        if o.feed is None or o.table or not L or "plot" not in L or not o.feed.days:
            return None
        plot = L["plot"]
        if not (plot.left() <= x < plot.right() and plot.top() <= y <= plot.bottom() + 1):
            return None
        return min(len(o.feed.days) - 1, int((x - plot.left()) / (plot.width() / len(o.feed.days))))

    def tooltip_text(self, i):
        """Plain text for day index i: the day, then each provider's amount that day."""
        feed = self.owner.feed
        lines = [fmt.short_date(feed.days[i])]
        for p in feed.providers:
            v = p.daily.get(feed.days[i], 0.0)
            if v > 0:
                lines.append(f"{fmt.clean_text(p.name)}  {fmt.money(v)}")
        if len(lines) == 1:
            lines.append("No spend")
        return "\n".join(lines)

    def mouseMoveEvent(self, ev):
        pos = ev.position()
        i = self.day_at(pos.x(), pos.y())
        if i != self._hover:
            self._hover = i
            self.update()
        if i is None:
            QToolTip.hideText()
        else:
            QToolTip.showText(ev.globalPosition().toPoint(), fmt.escape_tooltip(self.tooltip_text(i)), self)

    def leaveEvent(self, _ev):
        if self._hover is not None:
            self._hover = None
            self.update()

    def resizeEvent(self, ev):
        self.relayout()
        super().resizeEvent(ev)


class DetailsWindow(QScrollArea):
    """One shared window; set_feed() redraws it when a new feed arrives."""

    def __init__(self, theme="dark", now_fn=None, tz=None, feed=None, table=False):
        super().__init__()
        self.theme, self.tz, self.feed, self.table = theme, tz, feed, table
        self.now_fn = now_fn or (lambda: datetime.now(timezone.utc))
        self.setWindowTitle("Fumes Details")
        self.resize(WIN_W, WIN_H)
        self.setWidgetResizable(True)
        self.setFrameShape(QScrollArea.Shape.NoFrame)
        self.page = Page(self)
        self.setWidget(self.page)
        self._style()
        self.page.relayout()

    def _style(self):
        bg = BG[self.theme]
        self.setStyleSheet(f"QScrollArea {{ background: {bg}; border: 0; }}")
        ink, border, surf = (THEMES[self.theme][k] for k in ("ink", "border", "surface"))
        self.page.toggle.setStyleSheet(
            f"QPushButton {{ background: {surf}; color: {ink}; border: 1px solid {border}; border-radius: 6px; "
            f"padding: 4px 12px; }} QPushButton:hover {{ border-color: {ink}; }}")
        f = QFont()
        fam = fonts.family()
        if fam:
            f.setFamily(fam)
        f.setPixelSize(13)
        self.page.toggle.setFont(f)

    def set_feed(self, feed):
        self.feed = feed
        self.page._hover = None
        self.page.relayout()
        self.page.update()

    def set_table(self, on):
        self.table = bool(on)
        self.page.relayout()
        self.page.update()

    def render_png(self, path, dpr=1.0):
        """The whole page (not just the viewport) to a PNG, off-screen."""
        pg = self.page
        w = self.width() - (self.verticalScrollBar().sizeHint().width() if self.feed else 0)
        pg.resize(w, 100)
        pg.relayout()
        pg.resize(w, pg._L["height"])
        pg.relayout()
        img = QImage(round(w * dpr), round(pg._L["height"] * dpr), QImage.Format.Format_ARGB32)
        img.setDevicePixelRatio(dpr)
        img.fill(0)
        p = QPainter(img)
        pg.render(p)
        p.end()
        return img.save(str(path))
