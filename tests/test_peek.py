from datetime import datetime, timezone

from PyQt6.QtCore import QEvent, QPoint, QPointF, Qt
from PyQt6.QtGui import QMouseEvent
from PyQt6.QtTest import QTest

from fumes import config as cfgmod
from fumes import feed as feedmod
from fumes import ui
from fumes.peek import Peek, W

NOW = datetime(2026, 11, 12, 12, 0, 0, tzinfo=timezone.utc)


def strip_with_feed(raw_feed, **kw):
    kw.setdefault("show_limits", False)
    kw.setdefault("details_url", "https://feed.example/details")
    s = ui.Strip(cfgmod.Config(**kw), theme="dark")
    s.set_feed(feedmod.parse_feed(raw_feed), now=NOW)
    return s


def test_peek_builds_for_openai_and_anthropic(qapp, raw_feed):
    s = strip_with_feed(raw_feed)
    pk = Peek(s)
    assert pk.set_provider("openai") and pk.width() == W == 340
    assert pk.title() == "OpenAI"
    h_openai = pk.height()
    assert pk.set_provider("anthropic") and pk.width() == 340
    assert pk.title() == "Anthropic API" and pk.height() != h_openai
    assert not pk.set_provider("nope") and pk.title() == "Anthropic API"
    img = pk.grab().toImage()
    assert img.width() >= 340


def test_peek_refresh_follows_feed_and_closes_when_provider_gone(qapp, raw_feed):
    s = strip_with_feed(raw_feed)
    s.show()
    assert s.open_peek("fal") and s._peek.isVisible()
    raw = dict(raw_feed)
    raw["providers"] = dict(raw_feed["providers"], fal=dict(raw_feed["providers"]["fal"], name="Renamed"))
    s.set_feed(feedmod.parse_feed(raw), now=NOW)
    assert s._peek.isVisible() and s._peek.title() == "Renamed"
    raw["providers"] = {k: v for k, v in raw_feed["providers"].items() if k != "fal"}
    s.set_feed(feedmod.parse_feed(raw), now=NOW)
    assert not s._peek.isVisible()
    s.open_peek("openai")
    s.hide()
    assert not s._peek.isVisible()


def test_peek_links_use_config_not_feed(qapp, raw_feed):
    raw_feed["providers"]["acme"]["console"] = "https://evil.example"
    s = strip_with_feed(raw_feed, details_url="https://d.example/")
    pk = Peek(s)
    pk.set_provider("acme")
    assert [u for _, _, u in pk._L["links"]] == ["https://d.example/"]
    pk.set_provider("openai")
    assert [u for _, _, u in pk._L["links"]] == [cfgmod.DEFAULT_LINKS["openai"], "https://d.example/"]


def test_untrusted_text_is_never_rich_text(qapp, raw_feed):
    raw_feed["providers"]["fal"]["name"] = "<b>x</b><img src=file:///etc/passwd>"
    raw_feed["providers"]["fal"]["note"] = "<h1>hi</h1> " + "A" * 400
    s = strip_with_feed(raw_feed)
    pk = Peek(s)
    assert pk.set_provider("fal")
    pk.grab()   # draws without error; everything goes through QPainter.drawText
    assert not [w for w in pk.findChildren(object) if w.__class__.__name__ == "QLabel"]


def test_outside_press_switches_or_closes(qapp, raw_feed):
    s = strip_with_feed(raw_feed)
    s.show()
    s.open_peek("openai")
    pk = s._peek
    cells = {c.id: c for c in s.cells()}
    fal = cells["fal"]
    g = s.mapToGlobal(QPoint(int(fal.x + fal.w / 2), 10))
    out = QPointF(-50, -50)      # a press outside the popup, delivered to its own handler
    ev = QMouseEvent(QEvent.Type.MouseButtonPress, out, QPointF(g), Qt.MouseButton.LeftButton,
                     Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    pk.mousePressEvent(ev)
    assert pk.pid == "fal" and pk.isVisible()
    pk.mousePressEvent(ev)                       # same cell again: closes
    assert not pk.isVisible()
    s.open_peek("openai")
    far = QMouseEvent(QEvent.Type.MouseButtonPress, out, QPointF(5000, 5000), Qt.MouseButton.LeftButton,
                      Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    pk.mousePressEvent(far)
    assert not pk.isVisible()
    s.hide()


# ---- strip: click, move mode, cursor ----------------------------------------------
def click_at(s, x, release_dx=0, y=10):
    QTest.mousePress(s, Qt.MouseButton.LeftButton, pos=QPoint(x, y))
    QTest.mouseMove(s, QPoint(x + release_dx, y))
    QTest.mouseRelease(s, Qt.MouseButton.LeftButton, pos=QPoint(x + release_dx, y))


def test_left_click_on_cell_opens_peek_even_after_small_movement(qapp, raw_feed):
    s = strip_with_feed(raw_feed)
    s.show()
    c = s.cells()[1]                                  # openai: the bottom row of the first column
    click_at(s, int(c.x + 5), release_dx=2, y=int(s.row_centre(c.row)))
    assert s._peek is not None and s._peek.isVisible() and s._peek.pid == c.id
    s.close_peek()
    s.hide()


def test_left_drag_outside_move_mode_does_not_move_the_strip(qapp, raw_feed):
    s = strip_with_feed(raw_feed)
    s.show()
    saved = []
    s.offsetChanged.connect(saved.append)
    off = s.offset
    c = s.cells()[0]
    QTest.mousePress(s, Qt.MouseButton.LeftButton, pos=QPoint(int(c.x + 2), 10))
    for dx in (10, 40, 90):
        QTest.mouseMove(s, QPoint(int(c.x + 2) + dx, 10))
    QTest.mouseRelease(s, Qt.MouseButton.LeftButton, pos=QPoint(int(c.x + 2) + 90, 10))
    assert s.offset == off and saved == []
    s.close_peek()
    s.hide()


def test_move_mode_drag_commits_and_a_click_there_opens_no_peek(qapp, raw_feed):
    s = strip_with_feed(raw_feed)
    s.show()
    s.offset = -200
    saved = []
    s.offsetChanged.connect(saved.append)
    s.begin_move()
    assert s.move_mode() and s.cursor().shape() == Qt.CursorShape.SizeHorCursor
    c = s.cells()[0]
    QTest.mousePress(s, Qt.MouseButton.LeftButton, pos=QPoint(int(c.x + 2), 10))
    QTest.mouseMove(s, QPoint(int(c.x + 2) - 30, 10))
    QTest.mouseRelease(s, Qt.MouseButton.LeftButton, pos=QPoint(int(c.x + 2) - 30, 10))
    assert not s.move_mode() and len(saved) == 1 and s._peek is None
    s.hide()


def test_move_mode_cancel_by_escape_and_right_click_restores_offset(qapp, raw_feed):
    s = strip_with_feed(raw_feed)
    s.show()
    saved = []
    s.offsetChanged.connect(saved.append)
    for cancel in ("esc", "right"):
        s.offset = -100
        s.begin_move()
        s.offset = -150
        if cancel == "esc":
            QTest.keyClick(s, Qt.Key.Key_Escape)
        else:
            QTest.mouseClick(s, Qt.MouseButton.RightButton, pos=QPoint(10, 10))
        assert not s.move_mode() and s.offset == -100
    assert saved == []
    s.hide()


def test_right_click_closes_peek_and_shows_the_shared_menu(qapp, raw_feed):
    s = strip_with_feed(raw_feed)
    shown = []

    class FakeMenu:
        def popup(self, pt):
            shown.append(pt)
    s.context_menu = FakeMenu()
    s.show()
    s.open_peek("fal")
    QTest.mouseClick(s, Qt.MouseButton.RightButton, pos=QPoint(10, 10))
    assert not s._peek.isVisible() and len(shown) == 1
    s.hide()


def test_cursor_pointing_hand_over_cell_arrow_elsewhere(qapp, raw_feed):
    s = strip_with_feed(raw_feed)
    s.show()
    c = s.cells()[0]
    QTest.mouseMove(s, QPoint(int(c.x + 3), 10))
    assert s.cursor().shape() == Qt.CursorShape.PointingHandCursor
    QTest.mouseMove(s, QPoint(0, 10))
    assert s.cursor().shape() == Qt.CursorShape.ArrowCursor
    s.hide()


def test_menus_share_one_builder(qapp, raw_feed):
    s = strip_with_feed(raw_feed)

    class Fake:
        strip = s
        reset_position = refresh = refresh_all = open_details = open_settings = _set_autostart = restart = staticmethod(lambda *a: None)
    menu = ui.build_menu(Fake)
    texts = [a.text() for a in menu.actions() if not a.isSeparator()]
    assert texts == ["Move", "Reset position", "Collapsed", "Settings…", "Refresh now", "Open details page", "Start with Windows", "Restart", "Quit"]
    assert [a.isSeparator() for a in menu.actions()] == [False, False, False, True, False, False, False, False, True, False, False]


# ---- redesigned peek: modes, type scale, footer, chart, gauges ------------------------------
import json  # noqa: E402
from datetime import timedelta  # noqa: E402

import pytest  # noqa: E402
from PyQt6.QtGui import QFont  # noqa: E402

from helpers import spy_peek  # noqa: E402
from fumes import limits as lim  # noqa: E402
from fumes import peek as peekmod  # noqa: E402
from conftest import HERE  # noqa: E402

EST = timezone(timedelta(hours=-5))


def live_strip(raw, **kw):
    s = strip_with_feed(raw, **kw)
    s.now_fn = lambda: NOW
    s.tz = EST
    return s


def both_fixtures():
    return [json.loads((HERE / n).read_text("utf-8")) for n in ("sample-feed.json", "sample-feed-live.json")]


def test_theme_borders_and_chart_colours():
    assert peekmod.THEMES["dark"]["border"] == "#4a4a4a" and peekmod.THEMES["light"]["border"] == "#c4c4c4"
    assert peekmod.CHART_COLOR == {"dark": "#9a9a9a", "light": "#6e6e6e"}
    assert not hasattr(peekmod, "SERIES") and not hasattr(peekmod, "series_color")


def test_type_scale_constants():
    assert (peekmod.CAPTION_PX, peekmod.BODY_PX, peekmod.TITLE_PX) == (12, 13, 14)
    assert (peekmod.LEAD_PX, peekmod.SECOND_PX) == (20, 14)


def test_footer_is_always_one_row_for_every_provider(qapp):
    for raw in both_fixtures():
        s = live_strip(raw)
        pk = Peek(s)
        for pv in s.feed.providers:
            assert pk.set_provider(pv.id)
            L = pk._L
            assert L["links_y"] == L["footer_y"]                                  # one row
            first = L["links"][0][0]
            assert PAD_LEFT + L["checked_w"] + 12 <= first.left()                 # the left text clears the links
            assert L["links"][-1][0].right() <= W - 14 + 0.5
            names = [t for _, t, _ in L["links"]]
            assert names == (["Console", "Details"] if pv.id in cfgmod.DEFAULT_LINKS else ["Details"])


PAD_LEFT = 14


def test_provider_with_no_console_link_shows_only_details(qapp, raw_feed):
    s = live_strip(raw_feed)
    pk = Peek(s)
    pk.set_provider("acme")
    assert [t for _, t, _ in pk._L["links"]] == ["Details"]
    s2 = live_strip(raw_feed, links={"acme": "https://example.com/b"})
    pk2 = Peek(s2)
    pk2.set_provider("acme")
    assert [t for _, t, _ in pk2._L["links"]] == ["Console", "Details"]


def test_limits_peek_keeps_usage_page_link(qapp, raw_feed_live):
    raw_limits = json.loads((HERE / "sample-limits.json").read_text("utf-8"))
    s = live_strip(raw_feed_live, show_limits=True)
    s.set_limits(lim.parse_limits(raw_limits), now=NOW)
    pk = Peek(s)
    pk.set_limits()
    assert [t for _, t, _ in pk._L["links"]] == ["Usage page"]


def test_checked_text_in_the_footer_is_time_only_today(qapp, raw_feed):
    s = live_strip(raw_feed)
    pk = Peek(s)
    pk.set_provider("fal")
    assert pk._L["checked"] == "Checked 06:30"
    s.now_fn = lambda: NOW + timedelta(days=1)
    pk.set_provider("fal")
    assert pk._L["checked"] == "Checked Nov 12 06:30"


def test_links_underlined_in_secondary_ink_and_primary_on_hover(qapp, raw_feed):
    s = live_strip(raw_feed)
    pk = Peek(s)
    pk.set_provider("fal")
    ink, ink2 = pk.col("ink").name(), pk.col("ink2").name()
    p, _ = spy_peek(pk)
    unders = [e for e in p.log if e["kind"] == "rect" and e["rect"].height() == 1.0 and e["rect"].top() > pk._L["rule_y"]]
    assert len(unders) == 2 and {e["color"] for e in unders} == {ink2}
    assert p.text("Console")["color"] == ink2
    pk._hover_link = 0
    p, _ = spy_peek(pk)
    unders = [e for e in p.log if e["kind"] == "rect" and e["rect"].height() == 1.0 and e["rect"].top() > pk._L["rule_y"]]
    assert sorted(e["color"] for e in unders) == sorted([ink, ink2])
    assert p.text("Console")["color"] == ink and p.text("Details")["color"] == ink2


def test_link_hit_rect_is_padded_six_px_vertically(qapp, raw_feed):
    s = live_strip(raw_feed)
    pk = Peek(s)
    pk.set_provider("fal")
    r = pk._L["links"][0][0]
    cx = r.center().x()
    assert pk.link_at(cx, r.top() - 6) == 0 and pk.link_at(cx, r.bottom() + 6) == 0
    assert pk.link_at(cx, r.top() - 7) is None and pk.link_at(cx, r.bottom() + 7) is None


def test_empty_peek_shows_header_reason_footer_only(qapp, raw_feed):
    s = live_strip(raw_feed)
    pk = Peek(s)
    assert pk.set_provider("anthropic")                       # old fixture: not connected, no balance, nothing
    L = pk._L
    assert L["mode"] == "empty" and "chart_top" not in L and "fig_cap_y" not in L and L["rows"] == []
    p, _ = spy_peek(pk)
    texts = p.texts()
    assert "Credit left" not in texts and "Daily spend, last 30 days" not in texts
    assert not any("No spend" in (t or "") for t in texts)
    note = next(e for e in p.log if e["kind"] == "text" and e["text"] and e["text"].startswith("Anthropic refused"))
    assert note["color"] == pk.col("ink").name()              # the reason is the answer: primary ink
    assert note["px"] == peekmod.BODY_PX
    assert "Checked 06:30" in texts and "Not connected" in texts
    # a normal peek keeps the note in secondary ink
    pk.set_provider("acme")
    p, _ = spy_peek(pk)
    note = next(e for e in p.log if e["kind"] == "text" and e["text"] and e["text"].startswith("Estimate"))
    assert note["color"] == pk.col("ink2").name()


def test_figures_only_peek_for_a_hand_entered_balance(qapp, raw_feed_live):
    s = live_strip(raw_feed_live)
    pk = Peek(s)
    assert pk.set_provider("anthropic")
    L = pk._L
    assert L["mode"] == "figures" and "chart_top" not in L and L["rows"] == []
    p, _ = spy_peek(pk)
    texts = p.texts()
    assert "Credit left" in texts and "$85.20" in texts and "Daily spend, last 30 days" not in texts
    assert not any("No spend" in (t or "") for t in texts)
    assert texts.count("–") == 2


def test_no_spend_caption_not_drawn_for_a_not_connected_provider(qapp, raw_feed):
    raw_feed["providers"]["anthropic"]["mtd"] = 0.0            # data present, every day zero, but not connected
    s = live_strip(raw_feed)
    pk = Peek(s)
    pk.set_provider("anthropic")
    assert pk._L["mode"] == "full"
    p, _ = spy_peek(pk)
    assert not any("No spend" in (t or "") for t in p.texts())
    raw_feed["providers"]["anthropic"]["status"] = "ok"        # connected, all zero: this is where it belongs
    s.set_feed(feedmod.parse_feed(raw_feed), now=NOW)
    pk.set_provider("anthropic")
    p, _ = spy_peek(pk)
    assert "No spend in the last 30 days" in p.texts()


def test_figure_type_scale_lead_bold_20_others_semibold_14_missing_dash_regular(qapp, raw_feed, raw_feed_live):
    pk = Peek(live_strip(raw_feed))
    pk.set_provider("deepseek")                                # balance is the lead
    p, _ = spy_peek(pk)
    lead, sec = p.text("$4.15"), p.text("$6.55")
    assert (lead["px"], lead["weight"]) == (20, int(QFont.Weight.Bold))
    assert (sec["px"], sec["weight"]) == (14, int(QFont.Weight.DemiBold))
    assert (p.text("$17.78")["px"], p.text("$17.78")["weight"]) == (14, int(QFont.Weight.DemiBold))
    pk.set_provider("openai")                                  # old fixture: no balance, month to date leads
    p, _ = spy_peek(pk)
    assert p.text("$5.44")["px"] == 20 and p.text("$6.65")["px"] == 14
    dash = p.text("–")
    assert (dash["px"], dash["weight"], dash["color"]) == (14, int(QFont.Weight.Normal), pk.col("ink2").name())
    t = p.text("Credit left")
    assert t["px"] == 12                                       # captions
    title = p.text("OpenAI")
    assert (title["px"], title["weight"]) == (14, int(QFont.Weight.DemiBold))
    assert p.text("Peak: $3.64 on Nov 6")["px"] == 13          # body


def test_lead_and_neighbours_do_not_collide_in_the_three_column_row(qapp, raw_feed_live):
    from PyQt6.QtGui import QFontMetrics
    s = live_strip(raw_feed_live)
    pk = Peek(s)
    colw = peekmod.INNER / 3
    for pv in s.feed.providers:
        pk.set_provider(pv.id)
        f = pk._font(peekmod.LEAD_PX, QFont.Weight.Bold)
        for v in (pv.balance, pv.mtd, pv.d30):
            if v is not None:
                assert QFontMetrics(f).horizontalAdvance(f"${v:.2f}") <= colw - 4


def test_chart_bars_are_neutral_and_the_hovered_bar_is_primary_ink(qapp, raw_feed):
    for theme in ("dark", "light"):
        s = live_strip(raw_feed)
        s.theme = theme
        pk = Peek(s)
        for pid in ("deepseek", "openai", "fal"):
            pk.set_provider(pid)
            p, _ = spy_peek(pk)
            assert {e["color"] for e in p.log if e["kind"] == "path"} == {peekmod.CHART_COLOR[theme]}
            assert all(e["op"] == 1.0 for e in p.log if e["kind"] == "path")           # no 55 % dimming
        pk.set_provider("deepseek")
        peak = max(range(len(s.feed.days)), key=lambda i: pk.values()[1][i])
        pk._hover_day = peak
        p, _ = spy_peek(pk)
        cols = [e["color"] for e in p.log if e["kind"] == "path"]
        assert cols.count(pk.col("ink").name()) == 1 and cols.count(peekmod.CHART_COLOR[theme]) == len(cols) - 1
        assert all(e["op"] == 1.0 for e in p.log if e["kind"] == "path")


def test_axis_top_label_whole_dollars(qapp, raw_feed):
    pk = Peek(live_strip(raw_feed))
    pk.set_provider("deepseek")
    assert "$10" in spy_peek(pk)[0].texts()
    pk.set_provider("openai")
    assert "$5" in spy_peek(pk)[0].texts()


def test_backticks_never_reach_the_peek(qapp, raw_feed):
    pk = Peek(live_strip(raw_feed))
    pk.set_provider("openai")
    assert "`" in raw_feed["providers"]["openai"]["note"]
    assert all("`" not in t for _, _, t in pk._L["paras"])
    assert not any("`" in (t or "") for t in spy_peek(pk)[0].texts())


def test_limits_peek_bars_use_the_gradient_and_the_hot_reset_line(qapp, raw_feed_live):
    raw_limits = json.loads((HERE / "sample-limits.json").read_text("utf-8"))
    s = live_strip(raw_feed_live, show_limits=True)
    s.set_limits(lim.parse_limits(raw_limits), now=NOW)
    pk = Peek(s)
    pk.set_limits()
    b5, bw = pk._L["blocks"]
    assert (b5["hot"], bw["hot"]) == (False, True)                 # 26 % and 94 %
    assert bw["line_h"] > b5["line_h"]
    p, img = spy_peek(pk)
    l5, lw = p.text(b5["line"]), p.text(bw["line"])
    assert (l5["px"], l5["color"]) == (12, pk.col("ink2").name())
    assert (lw["px"], lw["color"]) == (13, pk.col("ink").name())
    assert p.text("26% used") and p.text("94% used")
    # left to right, fixed to the track: the 94 % bar reaches red near its end, the 26 % bar stops at yellow-green
    px_ = lambda x, y: (lambda c: (c.red(), c.green(), c.blue()))(img.pixelColor(int(x), int(y)))  # noqa: E731
    y5, yw = b5["bar_y"] + 4, bw["bar_y"] + 4
    x0 = peekmod.PAD
    end5, endw = x0 + peekmod.INNER * 0.26 - 3, x0 + peekmod.INNER * 0.94 - 3
    r, g, b = px_(x0 + 5, y5)
    assert g > r                                                    # green at the left
    r, g, b = px_(endw, yw)
    assert r > g + 40                                               # red-orange near 94 %
    r5, g5, _ = px_(end5, y5)
    assert g5 > 150 and r5 < 230 and px_(x0 + peekmod.INNER - 5, y5)[0] < 110     # unfilled track beyond 26 %
    assert img.pixelColor(int(x0 + 5), int(y5)) != img.pixelColor(int(x0 + peekmod.INNER - 5), int(y5))
