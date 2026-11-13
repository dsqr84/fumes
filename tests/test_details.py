import json
from datetime import datetime, timezone

import pytest
from PyQt6.QtCore import QEvent, QPointF, Qt
from PyQt6.QtGui import QColor, QMouseEvent

from fumes import config as cfgmod
from fumes import details as dt
from fumes import feed as feedmod
from fumes import fmt, links, ui
from fumes.peek import Peek

NOW = datetime(2026, 11, 12, 12, 30, tzinfo=timezone.utc)


def window(raw, theme="dark", **kw):
    w = dt.DetailsWindow(theme, now_fn=lambda: NOW, tz=timezone.utc, feed=feedmod.parse_feed(raw), **kw)
    w.page.resize(1040, 100)
    w.page.relayout()
    return w


def texts(w):
    """Every string painted by the page."""
    from helpers import Spy
    from PyQt6.QtGui import QImage
    pg = w.page
    pg.resize(1040, 100)
    pg.relayout()
    img = QImage(1040, pg._L["height"], QImage.Format.Format_ARGB32)
    p = Spy(img)
    pg.paint_to(p)
    p.end()
    return [e["text"] for e in p.log if e.get("text")]


def test_totals_ok_and_stale_only_for_spend(raw_feed_live):
    raw_feed_live["providers"]["fal"]["status"] = "stale"
    raw_feed_live["providers"]["acme"]["status"] = "error"
    feed = feedmod.parse_feed(raw_feed_live)
    t = dt.totals(feed)
    P = raw_feed_live["providers"]
    assert t["mtd"] == pytest.approx(P["deepseek"]["mtd"] + P["openai"]["mtd"] + P["fal"]["mtd"])
    assert t["d30"] == pytest.approx(P["deepseek"]["d30"] + P["openai"]["d30"] + P["fal"]["d30"])
    # credit: any provider with a balance, including not connected and error ones
    assert t["credit"] == pytest.approx(sum(p.balance for p in feed.providers if p.balance is not None))
    assert [n for n, _ in t["credit_list"]][:2] == ["DeepSeek", "OpenAI"] and len(t["credit_list"]) == 5


def test_tiles_are_painted(qapp, raw_feed_live):
    w = window(raw_feed_live)
    out = texts(w)
    t = dt.totals(w.feed)
    assert fmt.money(t["mtd"]) in out and fmt.money(t["credit"]) in out
    assert any("DeepSeek $4.15" in r[3] for r in w.page._L["tiles"])


def test_stacked_segments_sum_and_use_provider_colours(qapp, raw_feed_live):
    feed = feedmod.parse_feed(raw_feed_live)
    segs = dt.day_segments(feed)
    assert len(segs) == 30
    for d, s in zip(feed.days, segs):
        assert sum(v for _, v in s) == pytest.approx(sum(p.daily.get(d, 0) for p in feed.providers))
    for theme, i in (("dark", 0), ("light", 1)):
        cols = dt.provider_colors(feed, theme)
        for pid in ("deepseek", "openai", "fal", "anthropic"):
            assert cols[pid] == QColor(dt.PROVIDER_COLORS[pid][i])
        assert cols["acme"] == QColor(dt.EXTRA_COLORS[0][i])
    w = window(raw_feed_live)
    w.page.grab()
    assert len(w.page._L["bars"]) == 30


def test_other_providers_take_extra_colours_in_order(raw_feed_live):
    raw_feed_live["providers"]["zed"] = dict(raw_feed_live["providers"]["acme"], name="Zed")
    cols = dt.provider_colors(feedmod.parse_feed(raw_feed_live), "dark")
    assert cols["acme"] == QColor(dt.EXTRA_COLORS[0][0]) and cols["zed"] == QColor(dt.EXTRA_COLORS[1][0])
    assert "example" not in cols          # hidden: no bar, card or column, so it takes no colour


def test_hidden_provider_has_no_card_and_balance_only_has_dashes(qapp, raw_feed_live):
    w = window(raw_feed_live)
    ids = [c["p"].id for c in w.page._L["cards"]]
    assert "example" not in ids and ids == ["deepseek", "openai", "fal", "anthropic", "acme"]
    out = texts(w)
    assert out.count(fmt.DASH) >= 2          # the Anthropic card: no mtd, no d30
    assert "$85.20" in out


def test_untrusted_text_is_literal_and_backticks_stripped(qapp, raw_feed_live):
    P = raw_feed_live["providers"]
    P["acme"]["name"] = "<b>x</b>`n`"
    P["acme"]["note"] = "run `rm -rf` <b>x</b> now"
    P["acme"]["breakdown"] = [{"label": "<i>m</i>`q`", "usd": 1.0}]
    w = window(raw_feed_live)
    out = " | ".join(texts(w))
    assert "<b>x</b>n" in out and "<b>x</b> now" in out and "<i>m</i>q" in out
    assert "`" not in out
    # the tooltip is escaped, so Qt cannot render the name as markup
    P["acme"]["daily"] = {w.feed.days[3]: 2.0}
    w2 = window(raw_feed_live)
    tip = fmt.escape_tooltip(w2.page.tooltip_text(3))
    assert "&lt;b&gt;x&lt;/b&gt;n" in tip and "<b>" not in tip and "`" not in tip


def test_table_has_30_rows_and_matching_total(qapp, raw_feed_live):
    w = window(raw_feed_live, table=True)
    header, rows = dt.table_data(w.feed)
    assert len(rows) == 30 and header[0] == "Day" and header[-1] == "Total"
    assert rows[0][0] == fmt.short_date(w.feed.days[-1]) and rows[-1][0] == fmt.short_date(w.feed.days[0])
    for d, row in zip(reversed(w.feed.days), rows):
        assert row[-1] == fmt.money(sum(p.daily.get(d, 0) for p in w.feed.providers))
    assert "Show as chart" == w.page.toggle.text() and "plot" not in w.page._L
    w.set_table(False)
    assert w.page.toggle.text() == "Show as table" and "plot" in w.page._L


def test_toggle_button_swaps_view(qapp, raw_feed_live):
    w = window(raw_feed_live)
    w.page.toggle.click()
    assert w.table
    w.page.toggle.click()
    assert not w.table


def test_hover_tooltip_lists_day_and_providers(qapp, raw_feed_live):
    w = window(raw_feed_live)
    w.page.grab()
    plot = w.page._L["plot"]
    i = w.feed.days.index("2026-10-31")
    x = plot.left() + (i + 0.5) * plot.width() / 30
    assert w.page.day_at(x, plot.bottom() - 5) == i
    tip = w.page.tooltip_text(i)
    assert tip.splitlines()[0] == "Oct 31" and "DeepSeek  $6.84" in tip and "OpenAI  $1.21" in tip
    assert w.page.day_at(x, plot.top() - 40) is None


def test_x_labels_first_last_and_weekly():
    assert dt.x_label_indices(30) == [0, 7, 14, 21, 29]
    assert dt.x_label_indices(0) == []


def test_empty_state(qapp):
    w = dt.DetailsWindow("dark", now_fn=lambda: NOW)
    w.page.resize(1040, 100)
    w.page.relayout()
    assert "No figures yet" in texts(w) and not w.page.toggle.isVisible()
    assert w.page._L["cards"] == []


def test_narrow_window_uses_two_columns(qapp, raw_feed_live):
    w = window(raw_feed_live)
    w.page.resize(800, 100)
    w.page.relayout()
    xs = {round(c["rect"].left()) for c in w.page._L["cards"]}
    assert len(xs) == 2


def test_render_png_whole_page(qapp, raw_feed_live, tmp_path):
    w = window(raw_feed_live)
    assert w.render_png(tmp_path / "d.png", 1.0)
    from PyQt6.QtGui import QImage
    img = QImage(str(tmp_path / "d.png"))
    assert img.height() > dt.WIN_H


# ---- wiring ----------------------------------------------------------------------------------
def strip_for(raw, **kw):
    kw.setdefault("show_limits", False)
    s = ui.Strip(cfgmod.Config(**kw), theme="dark")
    s.set_feed(feedmod.parse_feed(raw), now=NOW)
    return s


def click_link(pk, name):
    for r, t, _u in pk._L["links"]:
        if t == name:
            pos = QPointF(r.center())
            for typ in (QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonRelease):
                ev = QMouseEvent(typ, pos, pk.mapToGlobal(pos), Qt.MouseButton.LeftButton,
                                 Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
                (pk.mousePressEvent if typ == QEvent.Type.MouseButtonPress else pk.mouseReleaseEvent)(ev)
            return
    raise AssertionError(name)


def test_details_link_opens_window_and_never_reaches_open_link(qapp, raw_feed_live, monkeypatch):
    opened, calls = [], []
    monkeypatch.setattr(links, "open_link", lambda *a, **k: calls.append(a))
    s = strip_for(raw_feed_live)
    s.on_details = lambda: opened.append(1)
    pk = Peek(s)
    pk.set_provider("acme")
    assert pk._L["links"][-1][1:] == ("Details", "fumes:details")
    click_link(pk, "Details")
    assert opened == [1] and calls == []


def test_open_link_refuses_the_marker(qapp):
    seen = []
    assert links.open_link(cfgmod.Config(), "fumes:details", opener=lambda *a: seen.append(a),
                           fallback=lambda u: seen.append(u)) is False and seen == []


def test_details_url_still_opens_the_url(qapp, raw_feed_live, monkeypatch):
    opened, calls = [], []
    monkeypatch.setattr(links, "open_link", lambda cfg, url, *a, **k: calls.append(url))
    s = strip_for(raw_feed_live, details_url="https://d.example/")
    s.on_details = lambda: opened.append(1)
    pk = Peek(s)
    pk.set_provider("acme")
    click_link(pk, "Details")
    assert calls == ["https://d.example/"] and opened == []


def test_menu_has_details_not_the_old_item(qapp, raw_feed_live):
    s = strip_for(raw_feed_live)

    class Fake:
        strip = s
        reset_position = refresh = refresh_all = open_details_window = open_settings = _set_autostart = restart = staticmethod(lambda *a: None)
    menu = ui.build_menu(Fake)
    texts_ = [a.text() for a in menu.actions() if not a.isSeparator()]
    assert "Details…" in texts_ and "Open details page" not in texts_


def test_app_window_is_shared_and_refreshes_on_new_feed(qapp, raw_feed_live, monkeypatch, tmp_path):
    class FakeThread:
        def __init__(self, *a, **k):
            pass

        def isRunning(self):
            return False

        def start(self):
            pass

        class done:
            connect = staticmethod(lambda *a: None)
    for n in ("LimitsThread", "FetchThread", "CollectThread"):
        monkeypatch.setattr(ui, n, FakeThread)
    app = ui.App(cfgmod.Config(show_limits=False), cfgmod.State(tmp_path / "s.json"), user_logo_dir=tmp_path)
    assert app.strip.on_details == app.open_details_window
    app.strip.set_feed(feedmod.parse_feed(raw_feed_live))
    w = app.open_details_window()
    assert w is app.open_details_window() and w.isVisible()
    raw2 = json.loads(json.dumps(raw_feed_live))
    raw2["providers"]["deepseek"]["mtd"] = 99.0
    app._fetched(feedmod.parse_feed(raw2), raw2, "")
    assert w.feed.providers[0].mtd == 99.0 and dt.totals(w.feed)["mtd"] > 99
    w.close()


def test_a_hidden_provider_with_daily_figures_does_not_break_painting(qapp, raw_feed_live):
    """A remote feed is untrusted: 'hidden' (not connected, no figures) yet carrying daily spend."""
    raw_feed_live["providers"]["example"]["daily"] = {raw_feed_live["days"][-1]: 2.5}
    w = window(raw_feed_live)
    w.page.grab()                          # paints the chart; must not raise
