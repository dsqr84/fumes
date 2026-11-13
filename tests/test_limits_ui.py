"""Offscreen UI tests for the Claude limits cell, its peek, and the collapse button."""
import json
from datetime import datetime, timedelta, timezone

import pytest
from PyQt6.QtCore import QEvent, QPoint, QPointF, Qt
from PyQt6.QtGui import QMouseEvent
from PyQt6.QtTest import QTest

from fumes import config as cfgmod
from fumes import feed as feedmod
from fumes import limits as lim
from fumes import ui
from fumes.peek import LIMITS_KEY, Peek

from conftest import HERE

NOW = datetime(2026, 11, 12, 12, 20, 0, tzinfo=timezone.utc)
EST = timezone(timedelta(hours=-5))
PROVIDER_TEXTS = ["$4.15", "$14.37", "$2.86", "$85.20", "$7.25"]      # the live fixture


@pytest.fixture
def raw_limits():
    return json.loads((HERE / "sample-limits.json").read_text("utf-8"))


def make(raw_feed=None, raw_limits=None, **kw):
    s = ui.Strip(cfgmod.Config(**kw), theme="dark")
    s.now_fn = lambda: NOW
    s.tz = EST
    if raw_feed is not None:
        s.set_feed(feedmod.parse_feed(raw_feed), now=NOW)
    if raw_limits is not None:
        s.set_limits(lim.parse_limits(raw_limits), now=NOW)
    return s


def click(s, x, y=10):
    QTest.mouseClick(s, Qt.MouseButton.LeftButton, pos=QPoint(int(x), y))


def hover(s, x, y=10):
    """Deliver a mouse move to the strip directly (QTest.mouseMove sends nothing when the pointer is already there)."""
    ev = QMouseEvent(QEvent.Type.MouseMove, QPointF(x, y), QPointF(s.mapToGlobal(QPoint(int(x), y))),
                     Qt.MouseButton.NoButton, Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier)
    s.mouseMoveEvent(ev)


def press_outside_on(s, key_cell, pk):
    g = s.mapToGlobal(QPoint(int(key_cell.x + key_cell.w / 2), 10))
    ev = QMouseEvent(QEvent.Type.MouseButtonPress, QPointF(-50, -50), QPointF(g), Qt.MouseButton.LeftButton,
                     Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    pk.mousePressEvent(ev)


# ---- the cell ----------------------------------------------------------------------
def test_claude_cell_is_first_and_provider_cells_keep_order_and_texts(qapp, raw_feed_live, raw_limits):
    s = make(raw_feed_live, raw_limits)
    cells = s.cells()
    assert [c.kind for c in cells] == ["limits"] + ["provider"] * 5          # "example" is hidden
    assert cells[0].id == "claude" and cells[0].key == LIMITS_KEY
    assert cells[0].text == "" and [m["label"] for m in cells[0].meters] == ["5h", "wk"]   # no numbers on the strip
    assert [m["used"] for m in cells[0].meters] == [26.0, 94.0]
    assert [c.text for c in s.provider_cells()] == PROVIDER_TEXTS
    assert [c.id for c in s.provider_cells()] == ["deepseek", "openai", "fal", "anthropic", "acme"]
    xs = [c.x for c in cells]
    assert xs == sorted(xs)
    # providers fill columns of two, top row first; a column's cells share x and width
    pc = s.provider_cells()
    assert [c.row for c in pc] == [0, 1, 0, 1, 0] and cells[0].row is None
    assert (pc[0].x, pc[0].w) == (pc[1].x, pc[1].w) and (pc[2].x, pc[2].w) == (pc[3].x, pc[3].w)
    assert pc[4].x > pc[2].x > pc[0].x
    # the hairline replaces the normal gap after the Claude cell: 8 clear, 1 wide, 8 clear
    end = cells[0].x + cells[0].w
    assert s._sep_x == end + ui.SEP_CLEAR and pc[0].x - end == 2 * ui.SEP_CLEAR + ui.SEP_W
    assert pc[2].x - (pc[0].x + pc[0].w) == ui.GAP_CELL == 12      # normal gaps between columns


def test_claude_cell_geometry_labels_and_gauges(qapp, raw_feed_live, raw_limits):
    s = make(raw_feed_live, raw_limits)
    c = s.cells()[0]
    m5, mwk = c.meters
    assert c.label_w == max(m5["lw"], mwk["lw"]) and ui.GAP_LABEL == 4
    assert c.w == ui.LOGO + ui.GAP_LOGO + c.label_w + ui.GAP_LABEL + ui.BAR_W and (ui.BAR_W, ui.BAR_H) == (28, 5)


def test_placeholder_before_first_limits_fetch(qapp, raw_feed):
    s = make(raw_feed)
    c = s.cells()[0]
    assert c.text == "" and not c.dim and [(m["label"], m["used"]) for m in c.meters] == [("5h", None), ("wk", None)]


def test_absent_and_reset_windows_in_the_cell(qapp, raw_feed, raw_limits):
    raw_limits["seven_day"] = None
    raw_limits["five_hour"]["resets_at"] = int(NOW.timestamp())          # exactly now: reset
    s = make(raw_feed, raw_limits)
    m5, mwk = s.cells()[0].meters
    assert m5["used"] == 0.0 and mwk["used"] is None                     # reset reads 0; absent has no reading


def test_no_remote_urls_means_no_fetch(qapp, monkeypatch, tmp_path):
    started = []

    class FakeThread:
        class done:
            connect = staticmethod(lambda f: None)

        def __init__(self, url, parent=None):
            started.append(url)

        def start(self): pass
        def isRunning(self): return False

    class FakeCollect(FakeThread):
        def __init__(self, *a, **k):
            started.append("local collection")

    monkeypatch.setattr(ui, "LimitsThread", FakeThread)
    monkeypatch.setattr(ui, "FetchThread", FakeThread)
    monkeypatch.setattr(ui, "CollectThread", FakeCollect)
    app = ui.App(cfgmod.Config(), cfgmod.State(tmp_path / "s.json"))
    app.refresh_all()
    assert "local collection" in started and all(u == "local collection" for u in started)   # no remote fetch


def test_show_limits_false_means_no_cell_no_button_no_fetch(qapp, raw_feed_live, monkeypatch, tmp_path):
    s = make(raw_feed_live, show_limits=False)
    assert [c.kind for c in s.cells()] == ["provider"] * 5 and s._btn is None
    s.collapsed = True                                        # a stored collapsed state is ignored
    s._relayout(NOW)
    assert not s.is_collapsed() and len(s.cells()) == 5
    started = []

    class FakeThread:
        class done:
            connect = staticmethod(lambda f: None)

        def __init__(self, url, parent=None):
            started.append(url)

        def start(self): pass
        def isRunning(self): return False

    monkeypatch.setattr(ui, "LimitsThread", FakeThread)
    monkeypatch.setattr(ui, "FetchThread", FakeThread)
    cfg = cfgmod.Config(show_limits=False, url="http://x/f")
    app = ui.App(cfg, cfgmod.State(tmp_path / "s.json"))
    assert started == [cfg.url]                                # the provider fetch at start, no limits fetch
    started.clear()
    app.refresh_limits()
    assert started == [] and not app.limits_timer.isActive()
    assert [c.kind for c in app.strip.cells()] == ["provider"] * 5
    app.strip.hide()


def test_app_starts_a_limits_fetch_with_its_own_timer(qapp, monkeypatch, tmp_path, raw_limits):
    started = []

    class FakeThread:
        class done:
            connect = staticmethod(lambda f: None)

        def __init__(self, url, parent=None):
            started.append(url)

        def start(self): pass
        def isRunning(self): return False

    monkeypatch.setattr(ui, "LimitsThread", FakeThread)
    monkeypatch.setattr(ui, "FetchThread", FakeThread)
    cfg = cfgmod.Config(url="http://x/f", limits_url="http://x/l", limits_refresh_minutes=3)
    st = cfgmod.State(tmp_path / "s.json")
    raw_limits["five_hour"]["resets_at"] = 4_000_000_000          # the App reads the real clock: not yet reset
    st.limits_cache = raw_limits                                  # a restart while offline still shows it
    st.collapsed = True
    app = ui.App(cfg, st)
    assert started.count("http://x/l") == 1 and cfg.url in started
    assert app.limits_timer.interval() == 3 * 60_000 and app.limits_timer.isActive()
    assert app.strip.limits is not None and app.strip.cells()[0].meters[0]["used"] == 26.0
    assert app.strip.is_collapsed() and app.collapse_action.isChecked()
    app.strip.hide()


def test_failed_limits_fetch_keeps_figures_and_dims_only_the_claude_cell(qapp, raw_feed, raw_limits, tmp_path):
    s = make(raw_feed, raw_limits)
    texts, provider_dim = s.cell_texts(), [c.dim for c in s.provider_cells()]
    s.set_limits_failed(True, now=NOW)
    assert s.cell_texts() == texts
    assert s.cells()[0].dim is True
    assert [c.dim for c in s.provider_cells()] == provider_dim and not s._dim_all and s.age_label() == ""
    s.set_limits(lim.parse_limits(raw_limits), failed=False, now=NOW)
    assert s.cells()[0].dim is False


def test_provider_failure_does_not_dim_the_claude_cell(qapp, raw_feed, raw_limits):
    s = make(raw_feed, raw_limits)
    s.set_failed(True, now=NOW)
    assert s._dim_all and s.cells()[0].dim is False


def test_app_limits_result_handlers(qapp, monkeypatch, tmp_path, raw_limits):
    class Inert:
        class done:
            connect = staticmethod(lambda f: None)

        def __init__(self, *a, **k): pass
        def start(self): pass
        def isRunning(self): return False

    monkeypatch.setattr(ui, "LimitsThread", Inert)
    monkeypatch.setattr(ui, "FetchThread", Inert)
    app = ui.App(cfgmod.Config(), cfgmod.State(tmp_path / "s.json"))
    app._limits_fetched(lim.parse_limits(raw_limits), raw_limits, "")
    assert cfgmod.State.load(tmp_path / "s.json").limits_cache == raw_limits
    good = app.strip.cell_texts()
    app._limits_fetched(None, None, "boom")
    assert app.strip.cell_texts() == good and app.strip.cells()[0].dim
    app.strip.hide()


def test_limits_tooltip_text_is_escaped_rich_text(qapp, raw_feed, raw_limits):
    s = make(raw_feed, raw_limits)
    s.set_feed(feedmod.parse_feed(raw_feed), now=NOW)
    t = fmt_tooltip(s)
    assert t.startswith("Claude subscription")
    assert "5-hour limit: 26% used, resets 10:40 (in 3h 20m)" in t
    assert "Weekly limit: 94% used, resets Fri 02:00 (in 18h 40m)" in t


def fmt_tooltip(s):
    from fumes import fmt
    ts = s.now_fn().timestamp()
    return fmt.limits_tooltip([("5-hour limit", lim.view(s.limits.five_hour, ts)),
                               ("Weekly limit", lim.view(s.limits.seven_day, ts))],
                              s.limits.updated, s.now_fn(), s.tz)


# ---- clicks and the peek ------------------------------------------------------------
def test_click_claude_cell_opens_limits_peek_then_a_provider_cell_switches(qapp, raw_feed, raw_limits):
    s = make(raw_feed, raw_limits)
    s.show()
    click(s, s.cells()[0].x + 5)
    pk = s._peek
    assert pk is not None and pk.isVisible() and pk.is_limits and pk.pid is None
    assert pk.title() == "Claude subscription" and pk.width() == 340
    fal = next(c for c in s.cells() if c.id == "fal")
    press_outside_on(s, fal, pk)
    assert pk.key == ("provider", "fal") and pk.isVisible() and not pk.is_limits
    press_outside_on(s, s.cells()[0], pk)                        # and back to the limits peek
    assert pk.is_limits and pk.isVisible()
    s.close_peek()
    s.hide()


def test_cursor_is_a_pointing_hand_over_the_claude_cell(qapp, raw_feed, raw_limits):
    s = make(raw_feed, raw_limits)
    s.show()
    hover(s, s.cells()[0].x + 3)
    assert s.cursor().shape() == Qt.CursorShape.PointingHandCursor
    s.hide()


def test_a_provider_named_claude_gets_its_own_cell_and_peek(qapp, raw_feed, raw_limits):
    raw_feed["providers"]["claude"] = {"name": "Claude API", "status": "ok", "balance": 4.5}
    s = make(raw_feed, raw_limits, providers=["claude", "fal"])
    s.show()
    assert [(c.kind, c.id) for c in s.cells()] == [("limits", "claude"), ("provider", "claude"), ("provider", "fal")]
    assert s.cells()[0].key != s.cells()[1].key
    assert s.cell_texts()[1:] == ["$4.50", "$2.86"]
    click(s, s.cells()[1].x + 5)
    assert s._peek.key == ("provider", "claude") and s._peek.title() == "Claude API"
    # the provider peek's console link is not the limits page
    assert all(link[2] != cfgmod.CLAUDE_USAGE_URL for link in s._peek._L["links"])
    click(s, s.cells()[0].x + 5)           # the limits cell, with a provider peek open
    assert s._peek.is_limits
    s.close_peek()
    s.hide()


def test_limits_peek_content_and_footer_link(qapp, raw_feed, raw_limits):
    s = make(raw_feed, raw_limits, links={"claude": "x"})
    pk = Peek(s)
    assert pk.set_limits()
    lines = [b["line"] for b in pk._L["blocks"]]
    assert lines == ["Resets 10:40 (in 3h 20m)", "Resets Fri 02:00 (in 18h 40m)"]
    assert pk._L["checked"] == "Updated 2026-11-12 07:16"
    assert [(t, u) for _, t, u in pk._L["links"]] == [("Usage page", "https://claude.ai/settings/usage")]
    s2 = make(raw_feed, raw_limits)
    s2.cfg.limits_link = "https://example.com/u"
    pk2 = Peek(s2)
    pk2.set_limits()
    assert pk2._L["links"][0][2] == "https://example.com/u"
    pk.grab()


def test_limits_peek_states_reset_absent_and_failed(qapp, raw_feed, raw_limits):
    raw_limits["five_hour"]["resets_at"] = int(NOW.timestamp()) - 1
    del raw_limits["seven_day"]
    s = make(raw_feed, raw_limits)
    s.set_limits_failed(True, now=NOW)
    pk = Peek(s)
    pk.set_limits()
    assert [b["line"] for b in pk._L["blocks"]] == ["Reset: a new window starts with your next message",
                                                    "No reading yet"]
    pk.grab()                                                    # the Stale chip path draws without error
    s2 = make(raw_feed)
    pk2 = Peek(s2)
    assert pk2.set_limits() and pk2._L["checked"] == "Updated never"


def test_limits_peek_countdown_follows_the_clock_and_a_timer_runs_only_while_visible(qapp, raw_feed, raw_limits):
    s = make(raw_feed, raw_limits)
    s.show()
    pk = Peek(s)
    assert pk.show_key(LIMITS_KEY) and pk._tick.isActive() and pk._tick.interval() == 30_000
    assert pk._L["blocks"][0]["line"].endswith("(in 3h 20m)")
    s.now_fn = lambda: NOW + timedelta(minutes=30)
    pk._tick_limits()
    assert pk._L["blocks"][0]["line"].endswith("(in 2h 50m)")
    pk.close()
    assert not pk._tick.isActive()
    s.hide()


def test_new_limits_refresh_an_open_limits_peek(qapp, raw_feed, raw_limits):
    s = make(raw_feed, raw_limits)
    s.show()
    s.open_limits_peek()
    raw_limits["five_hour"]["used_percentage"] = 80.0
    s.set_limits(lim.parse_limits(raw_limits), now=NOW)
    assert s._peek.isVisible() and s._peek._L["blocks"][0]["v"].used == 80.0
    s.close_peek()
    s.hide()


# ---- digit size, height ------------------------------------------------------------
def test_figure_font_digits_are_clock_sized(qapp, raw_feed, raw_limits):
    s = make(raw_feed, raw_limits)
    cap = ui.cap_height(s._fig_font())
    assert cap <= ui.FIG_CAP + 0.5 and cap > ui.FIG_CAP - 2.5    # whole-pixel sizes: close, never over by > 0.5
    assert s.content_height() == ui.STRIP_H == 40
    assert (s.row_centre(0), s.row_centre(1), s.row_centre(None)) == (11, 29, 20)


def test_font_size_override_wins_and_small_font_stays(qapp, raw_feed):
    s = make(raw_feed, font_size=14)
    assert s._fig_font().pointSizeF() == 14 and s._font().pointSizeF() == ui.SMALL_PT


# ---- collapse ------------------------------------------------------------------------
def test_collapse_leaves_only_the_claude_cell_and_expand_restores(qapp, raw_feed, raw_limits):
    s = make(raw_feed, raw_limits)
    before_texts, before_w = s.cell_texts(), s.width()
    seen = []
    s.collapsedChanged.connect(seen.append)
    s.set_collapsed(True)
    assert [c.key for c in s.cells()] == [LIMITS_KEY] and s.width() < before_w and s.age_label() == ""
    s.set_collapsed(True)                                        # no change, no signal
    s.set_collapsed(False)
    assert s.cell_texts() == before_texts and s.width() == before_w and seen == [True, False]


def test_collapsed_with_a_stale_feed_shows_no_age_label(qapp, raw_feed, raw_limits):
    s = make(raw_feed, raw_limits)
    s.set_failed(True, now=NOW)
    assert s.age_label() != ""
    s.set_collapsed(True)
    assert s.age_label() == ""


def test_button_is_at_the_right_end_and_click_toggles_without_a_peek(qapp, raw_feed, raw_limits):
    s = make(raw_feed, raw_limits)
    s.show()
    bx, bw = s._btn
    last = s.cells()[-1]
    assert bw == 20 and bx >= last.x + last.w and bx + bw <= s.width()
    click(s, bx + 10)
    assert s.is_collapsed() and s._peek is None
    bx2 = s._btn[0]
    click(s, bx2 + 10)
    assert not s.is_collapsed() and s._peek is None
    hover(s, s._btn[0] + 5)
    assert s.cursor().shape() == Qt.CursorShape.PointingHandCursor and s._hover_btn
    hover(s, 0)
    assert not s._hover_btn
    s.hide()


def test_button_does_nothing_in_move_mode(qapp, raw_feed, raw_limits):
    s = make(raw_feed, raw_limits)
    s.show()
    s.begin_move()
    click(s, s._btn[0] + 10)
    assert not s.collapsed and s._peek is None
    s.hide()


def test_collapse_closes_a_provider_peek_but_not_the_limits_peek(qapp, raw_feed, raw_limits):
    s = make(raw_feed, raw_limits)
    s.show()
    s.open_peek("fal")
    assert s._peek.isVisible()
    s.set_collapsed(True)
    assert not s._peek.isVisible()
    s.open_limits_peek()
    s.set_collapsed(False)
    assert s._peek.isVisible() and s._peek.is_limits
    s.set_collapsed(True)
    assert s._peek.isVisible() and s._peek.is_limits
    s.close_peek()
    s.hide()


def test_collapse_round_trip_keeps_the_wanted_offset_and_position(qapp, raw_feed, raw_limits):
    s = make(raw_feed, raw_limits, providers=["fal", "acme"])
    s.show()
    s.set_collapsed(True)
    s.offset = -(s.screen().geometry().width() - s.cfg.margin - s.width()) + 10    # only valid when narrow
    s.reposition()
    x_narrow = s.x()
    assert x_narrow > 0
    s.set_collapsed(False)
    assert s.x() == 0                                            # the wide strip is clamped at the screen edge
    s.reposition()                                               # what the 2 s tick does
    s.set_collapsed(True)
    assert s.offset == -(s.screen().geometry().width() - s.cfg.margin - s.width()) + 10 and s.x() == x_narrow
    s.hide()


def test_tray_anchor_keeps_the_right_edge_when_collapsing(qapp, raw_feed, raw_limits):
    s = make(raw_feed, raw_limits, providers=["fal"])        # narrow enough to fit the offscreen screen
    s.show()
    s.reposition()
    assert s.x() > 0
    right = s.x() + s.width()
    s.set_collapsed(True)
    assert s.x() + s.width() == right
    s.hide()
    s2 = make(raw_feed, raw_limits, anchor="left", providers=["fal"])
    s2.show()
    s2.reposition()
    left = s2.x()
    s2.set_collapsed(True)
    assert s2.x() == left
    s2.hide()


def test_menu_item_mirrors_collapsed_state(qapp, raw_feed, raw_limits):
    s = make(raw_feed, raw_limits)

    class Fake:
        strip = s
        reset_position = refresh = refresh_all = open_details_window = open_settings = _set_autostart = restart = staticmethod(lambda *a: None)
    menu = ui.build_menu(Fake)  # noqa: F841 (the actions die with the menu)
    act = Fake.collapse_action
    assert act.isCheckable() and act.text() == "Collapsed" and not act.isChecked() and act.isEnabled()
    act.setChecked(True)
    assert s.is_collapsed()
    s.set_collapsed(False)
    assert not act.isChecked()
    s.set_collapsed(True)
    assert act.isChecked()


def test_menu_item_disabled_without_the_limits_cell(qapp, raw_feed):
    s = make(raw_feed, show_limits=False)

    class Fake:
        strip = s
        reset_position = refresh = refresh_all = open_details_window = open_settings = _set_autostart = restart = staticmethod(lambda *a: None)
    menu = ui.build_menu(Fake)  # noqa: F841 (the actions die with the menu)
    assert not Fake.collapse_action.isEnabled()


# ---- renders --------------------------------------------------------------------------
def test_cli_renders_limits_strip_and_peek(tmp_path, raw_feed, raw_limits):
    from fumes import __main__ as m
    f, l = tmp_path / "f.json", tmp_path / "l.json"
    f.write_text(json.dumps(raw_feed))
    l.write_text(json.dumps(raw_limits))
    base = ["--config", str(tmp_path / "c.toml"), "--feed-file", str(f), "--limits-file", str(l),
            "--now", "2026-11-12T12:20:00Z"]
    for name, extra in (("a", []), ("b", ["--collapsed"]), ("c", ["--peek", "claude"]),
                        ("d", ["--peek", "provider:fal"])):
        out = tmp_path / f"{name}.png"
        assert m.main(base + extra + ["--once-png", str(out)]) == 0 and out.stat().st_size > 300
    wide, narrow = (ui_png(tmp_path / "a.png"), ui_png(tmp_path / "b.png"))
    assert narrow[0] < wide[0]
    assert m.main(base + ["--peek", "provider:claude", "--once-png", str(tmp_path / "e.png")]) == 1


def ui_png(path):
    from PyQt6.QtGui import QImage
    img = QImage(str(path))
    return img.width(), img.height()


# ---- strip painting: gauges, opacity, separator, hover, chevron -------------------------------
from helpers import spy_strip  # noqa: E402
from PyQt6.QtGui import QColor  # noqa: E402


def px(img, x, y, dpr=1):
    c = img.pixelColor(int(x * dpr), int(y * dpr))
    return c.red(), c.green(), c.blue()


def bars(s, c):
    """(x left, y centre) of each limit bar of the Claude cell, one per row, at the layout's own coordinates."""
    x = c.x + ui.LOGO + ui.GAP_LOGO + c.label_w + ui.GAP_LABEL
    return [(x, s.row_centre(r)) for r in range(len(c.meters))]


def set_windows(raw_limits, five, week):
    for key, v in (("five_hour", five), ("seven_day", week)):
        if v is None:
            raw_limits.pop(key, None)
        else:
            raw_limits[key] = {"used_percentage": v, "resets_at": 1794553200}
    return raw_limits


def test_bars_sit_on_their_row_centres(qapp, raw_feed_live, raw_limits):
    s = make(raw_feed_live, raw_limits)
    _, img = spy_strip(s)
    bg = 0x20
    for x, cy in bars(s, s.cells()[0]):
        assert px(img, x + ui.BAR_W - 3, cy)[0] > bg                    # the track (or fill) is there
        assert px(img, x + 6, cy - 4)[0] == bg and px(img, x + 6, cy + 3)[0] == bg   # 5 px tall, centred


@pytest.mark.parametrize("dpr", [1, 2])
def test_bar_fill_grows_from_the_left_with_a_two_px_minimum(qapp, raw_feed_live, raw_limits, dpr):
    s = make(raw_feed_live, set_windows(raw_limits, 0.4, 100.0))
    s.theme = "dark"
    c = s.cells()[0]
    from helpers import new_image, Spy
    img = new_image(s.width(), s.height(), dpr)
    p = Spy(img)
    s.paint_to(p, dpr, bg=QColor("#202020"))
    p.end()
    (x5, y5), (xw, yw) = bars(s, c)
    def is_fill(rgb):
        return rgb[1] > rgb[0] + 25 or rgb[0] > rgb[1] + 25 or rgb[0] > 150        # coloured, not the grey track
    from fumes import gauge
    x5, xw = gauge.snap(x5, dpr), gauge.snap(xw, dpr)            # the bar is drawn on whole device pixels
    cols5 = [x5 + k for k in range(ui.BAR_W) if is_fill(px(img, x5 + k + 0.5, y5, dpr))]
    assert len(cols5) == 2 and cols5[0] == x5                      # 0.4 %: shown as 2 px, at the left
    colsw = [k for k in range(ui.BAR_W) if is_fill(px(img, xw + k + 0.5, yw, dpr))]
    assert len(colsw) == ui.BAR_W                                  # 100 %: the whole track
    # the gradient is fixed to the track: a full bar is green at the left, red at the right
    g_l, g_r = px(img, xw + 1.5, yw, dpr), px(img, xw + ui.BAR_W - 1.5, yw, dpr)
    assert g_l[1] > g_l[0] and g_r[0] > g_r[1] + 80
    low = px(img, x5 + 1.5, y5, dpr)                               # a low reading is only the green end
    assert low[1] > low[0]


def test_bar_edges_are_crisp_on_whole_pixels(qapp, raw_feed_live, raw_limits):
    s = make(raw_feed_live, raw_limits)
    from fumes import gauge
    for dpr in (1, 2):
        for (x, cy) in bars(s, s.cells()[0]):
            left, top = gauge.snap(x, dpr), gauge.snap(cy - ui.BAR_H / 2, dpr)
            assert left * dpr == int(left * dpr) and top * dpr == int(top * dpr)


def test_bar_track_opacities(qapp, raw_feed_live, raw_limits):
    # five-hour absent (no reading): 0.10; weekly reset (reads 0): the normal 0.18
    raw_limits.pop("five_hour")
    raw_limits["seven_day"]["resets_at"] = int(NOW.timestamp()) - 5
    s = make(raw_feed_live, raw_limits)
    _, img = spy_strip(s)
    (x5, y5), (xw, yw) = bars(s, s.cells()[0])
    bg, ink = 0x20, 0xf2
    assert px(img, x5 + 14, y5)[0] == pytest.approx(bg + (ink - bg) * 0.10, abs=2)
    assert px(img, xw + 14, yw)[0] == pytest.approx(bg + (ink - bg) * 0.18, abs=2)
    # before the first fetch: two empty tracks at 0.10
    s2 = make(raw_feed_live)
    _, img2 = spy_strip(s2)
    for (x, cy) in bars(s2, s2.cells()[0]):
        assert px(img2, x + 14, cy)[0] == pytest.approx(bg + (ink - bg) * 0.10, abs=2)


def test_cell_opacity_has_a_floor_of_0_45_and_never_multiplies():
    for role in ("logo", "label", "figure"):
        for dash in (False, True):
            assert ui.cell_opacity(role, True, dash) == 0.45          # not live: one flat 0.45
            assert ui.cell_opacity(role, False, dash) >= 0.62
    assert ui.cell_opacity("logo", False) == ui.cell_opacity("figure", False) == 1.0
    assert ui.cell_opacity("label", False) == ui.cell_opacity("figure", False, dash=True) == 0.62


def test_painted_opacities_on_the_strip(qapp, raw_feed, raw_limits):
    raw_feed["providers"]["anthropic"]["status"] = "ok"          # a live dash cell
    raw_feed["providers"]["fal"]["status"] = "stale"             # a not-live cell
    s = make(raw_feed, raw_limits)
    p, _ = spy_strip(s)
    seen = [e["op"] for e in p.log if e["kind"] in ("text", "pixmap")]
    assert min(seen) >= 0.45                                       # nothing below the floor
    assert p.text("$4.15")["op"] == 1.0 and p.text("$5.44")["op"] == 1.0       # balance and mtd: full
    assert p.text("mtd")["op"] == 0.62 and p.text("5h")["op"] == 0.62
    assert p.text("–")["op"] == 0.62                                       # the dash
    assert p.text("$2.86")["op"] == 0.45                                        # fal: not live
    # the whole strip stale: every cell at 0.45, labels and the dash included (never 0.62 * 0.45)
    s.set_failed(True, now=NOW)
    p, _ = spy_strip(s)
    provider_ops = {e["op"] for e in p.log if e["kind"] in ("text", "pixmap") and e.get("text") not in ("5h", "wk")}
    assert min(e["op"] for e in p.log if e["kind"] in ("text", "pixmap")) >= 0.45
    assert p.text("mtd")["op"] == 0.45 and p.text("–")["op"] == 0.45 and p.text("$4.15")["op"] == 0.45
    assert 0.45 in provider_ops


def test_failed_limits_fetch_dims_the_claude_cell_flat(qapp, raw_feed_live, raw_limits):
    s = make(raw_feed_live, raw_limits)
    s.set_limits_failed(True, now=NOW)
    p, _ = spy_strip(s)
    assert p.text("5h")["op"] == 0.45 and p.text("wk")["op"] == 0.45
    first_logo = next(e for e in p.log if e["kind"] == "pixmap")
    assert first_logo["op"] == 0.45
    assert p.text("$4.15")["op"] == 1.0


def test_mtd_label_is_part_of_the_cell(qapp, raw_feed, raw_limits):
    s = make(raw_feed, raw_limits)
    mtd = next(c for c in s.provider_cells() if c.fig == "mtd")
    other = next(c for c in s.provider_cells() if c.fig == "balance")
    assert mtd.label == "mtd" and mtd.text == "$5.44"
    from PyQt6.QtGui import QFontMetricsF
    lw = QFontMetricsF(s._font()).horizontalAdvance("mtd") + ui.GAP_LABEL
    assert mtd.label_w == pytest.approx(lw)
    big = QFontMetricsF(s._fig_font())
    # the figure starts beside the logo; a column is as wide as its wider cell
    assert mtd.text_x == other.text_x - other.x + mtd.x == mtd.x + ui.LOGO + ui.GAP_LOGO
    mate = next(c for c in s.provider_cells() if c.x == mtd.x and c is not mtd)
    widths = [(lw if c is mtd else 0) + big.horizontalAdvance(c.text) for c in (mtd, mate)]
    assert mtd.w == pytest.approx(ui.LOGO + ui.GAP_LOGO + max(widths))


def test_separator_only_with_the_claude_cell_expanded_and_providers(qapp, raw_feed_live, raw_limits):
    s = make(raw_feed_live, raw_limits)
    assert s._sep_x is not None
    s.set_collapsed(True)
    assert s._sep_x is None
    s.set_collapsed(False)
    s2 = make(raw_feed_live, raw_limits, providers=["example"])          # only a hidden provider: no cells after Claude
    assert [c.kind for c in s2.cells()] == ["limits"] and s2._sep_x is None
    _, img = spy_strip(s)
    bg, ink = 0x20, 0xf2
    mid = s.height() // 2
    sx = round(s._sep_x)
    assert px(img, sx, mid)[0] == pytest.approx(bg + (ink - bg) * 0.20, abs=3)
    assert px(img, sx, mid - 15 - 1)[0] == bg and px(img, sx, mid + 15)[0] == bg    # 30 px tall, centred
    assert px(img, sx, mid - 15)[0] > bg and px(img, sx - 1, mid)[0] == bg and px(img, sx + 1, mid)[0] == bg


def test_hover_plate_follows_the_pointer_and_repaints_only_on_change(qapp, raw_feed_live, raw_limits):
    s = make(raw_feed_live, raw_limits)
    s.show()
    updates = []
    real = s.update
    s.update = lambda *a: (updates.append(1), real(*a))[1]
    c = s.provider_cells()[1]               # openai: bottom row of the first column
    bottom, top = int(s.row_centre(1)), int(s.row_centre(0))
    hover(s, c.x + 5, bottom)
    assert s.hovered() == c.key and len(updates) == 1
    hover(s, c.x + 9, bottom + 2)           # same cell, moved: no repaint
    assert len(updates) == 1
    hover(s, c.x + 9, top)                  # same column, top row: the other cell
    assert s.hovered() == s.provider_cells()[0].key and len(updates) == 2
    hover(s, s._btn[0] + 3)
    assert s.hovered() == ui.BUTTON and len(updates) == 3
    hover(s, 0)                             # over nothing
    assert s.hovered() is None and len(updates) == 4
    hover(s, 0)
    assert len(updates) == 4
    s.hide()


def test_hover_plate_geometry_and_move_mode(qapp, raw_feed_live, raw_limits):
    s = make(raw_feed_live, raw_limits)
    s.show()
    c = s.provider_cells()[1]
    s._hover = c.key
    _, img = spy_strip(s)
    bg = 0x20
    plate = bg + (0xf2 - bg) * 0.08
    cy = s.row_centre(c.row)                                                 # bottom row: 29
    # x-5 .. x+w+5, cy-8.5 .. cy+8.5 (17 tall)
    assert px(img, c.x - 3, cy - 7)[0] == pytest.approx(plate, abs=3)
    assert px(img, c.x + c.w + 3, cy + 7)[0] == pytest.approx(plate, abs=3)
    assert px(img, c.x - 7, cy)[0] == bg and px(img, c.x - 3, cy - 10)[0] == bg and px(img, c.x - 3, cy + 9)[0] == bg
    assert px(img, c.x + c.w + 7, cy)[0] == bg
    # the next column is untouched
    nxt = s.provider_cells()[2]
    assert px(img, nxt.x - 3, cy)[0] == bg
    # the chevron's plate is a 20 x 20 square centred on it
    s._hover = ui.BUTTON
    _, img = spy_strip(s)
    cx, cy = s._btn[0] + 10, s.height() / 2
    assert px(img, cx - 8, cy - 7)[0] == pytest.approx(plate, abs=3) and px(img, cx - 11, cy)[0] == bg
    # no plate in move mode
    s.begin_move()
    assert s.hovered() is None
    s._hover = c.key
    _, img = spy_strip(s)
    assert px(img, c.x - 5, 3)[0] == bg
    s._end_move()
    s.hide()


def test_hover_cli_flag(tmp_path, raw_feed_live, raw_limits):
    from fumes import __main__ as m
    f, l = tmp_path / "f.json", tmp_path / "l.json"
    f.write_text(json.dumps(raw_feed_live))
    l.write_text(json.dumps(raw_limits))
    sizes = set()
    for target in ("claude", "openai", "button", None):
        out = tmp_path / f"h-{target}.png"
        extra = ["--hover", target] if target else []
        assert m.main(["--config", str(tmp_path / "c.toml"), "--feed-file", str(f), "--limits-file", str(l),
                       "--now", "2026-11-12T12:20:00Z", "--once-png", str(out)] + extra) == 0
        sizes.add(out.read_bytes())
    assert len(sizes) == 4                                           # every target renders a different picture


def ink_cols(img, y, x0, x1, bg=0x20):
    xs = [x for x in range(int(x0), int(x1)) if img.pixelColor(x, y).red() > bg + 40]
    return (min(xs), max(xs)) if xs else None


@pytest.mark.parametrize("anchor,collapsed,tip_right", [
    ("tray", False, True), ("tray", True, False), ("right", False, True), ("right", True, False),
    ("left", False, False), ("left", True, True)])
def test_chevron_points_the_way_the_anchor_dictates(qapp, raw_feed_live, raw_limits, anchor, collapsed, tip_right):
    s = make(raw_feed_live, raw_limits, anchor=anchor)
    s.set_collapsed(collapsed)
    s._hover = ui.BUTTON                                           # full opacity: easy to find
    _, img = spy_strip(s)
    bx = s._btn[0]
    mid, top = s.height() // 2, s.height() // 2 - 4
    tip, edge = ink_cols(img, mid, bx, bx + 20), ink_cols(img, top, bx, bx + 20)
    assert (tip[1] > edge[1]) is tip_right and (tip[0] < edge[0]) is (not tip_right)


def test_chevron_size_and_gap(qapp, raw_feed_live, raw_limits):
    assert (ui.CHEV_DEPTH, ui.CHEV_H, ui.CHEV_STROKE, ui.BTN_W, ui.BTN_GAP, ui.BTN_OPACITY) == (5, 10, 1.5, 20, 12, 0.45)
    s = make(raw_feed_live, raw_limits)
    last = s.cells()[-1]
    assert s._btn[0] == last.x + last.w + 12
    _, img = spy_strip(s)
    bx = int(s._btn[0])
    rows = [y for y in range(s.height()) if ink_cols(img, y, bx, bx + 20, bg=0x20 + 10)]
    assert 10 <= len(rows) <= 12                                    # 10 tall plus the stroke's width
    s._hover = ui.BUTTON
    _, img_h = spy_strip(s)
    assert max(img_h.pixelColor(x, s.height() // 2).red() for x in range(bx, bx + 20)) > \
        max(img.pixelColor(x, s.height() // 2).red() for x in range(bx, bx + 20))    # 1.0 on hover, 0.45 at rest


def test_logical_widths_are_reasonable(qapp, raw_feed_live, raw_limits):
    s = make(raw_feed_live, raw_limits)
    wide = s.width()
    s.set_collapsed(True)
    assert s.width() < wide < 800
