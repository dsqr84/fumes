import copy
import json
import math
from datetime import datetime, timezone

import pytest

from fumes import config as cfgmod
from fumes import limits as lim
from fumes.feed import FeedError

from conftest import HERE


@pytest.fixture
def raw_limits():
    return json.loads((HERE / "sample-limits.json").read_text("utf-8"))


def test_good_fixture(raw_limits):
    L = lim.parse_limits(raw_limits)
    assert L.five_hour == lim.Window(26.0, 1794498000)
    assert L.seven_day == lim.Window(94.0, 1794553200)
    assert L.updated == datetime(2026, 11, 12, 12, 16, 11, tzinfo=timezone.utc)


@pytest.mark.parametrize("bad", [None, [], "x", 3, {}, {"updated": "nope"}, {"updated": 5},
                                 {"five_hour": {}, "updated": ""}])
def test_failed_document(bad):
    with pytest.raises(FeedError):
        lim.parse_limits(bad)


@pytest.mark.parametrize("w", [
    None, 5, [], {}, {"used_percentage": 5}, {"resets_at": 5},
    {"used_percentage": "5", "resets_at": 5}, {"used_percentage": 5, "resets_at": "5"},
    {"used_percentage": True, "resets_at": 5}, {"used_percentage": 5, "resets_at": False},
    {"used_percentage": -0.1, "resets_at": 5}, {"used_percentage": float("nan"), "resets_at": 5},
    {"used_percentage": 5, "resets_at": float("nan")}, {"used_percentage": 5, "resets_at": float("inf")},
    {"used_percentage": None, "resets_at": 5},
])
def test_bad_window_is_absent_not_a_failure(raw_limits, w):
    raw_limits["five_hour"] = w
    L = lim.parse_limits(raw_limits)
    assert L.five_hour is None and L.seven_day is not None


def test_absent_windows(raw_limits):
    del raw_limits["five_hour"], raw_limits["seven_day"]
    L = lim.parse_limits(raw_limits)
    assert L.five_hour is None and L.seven_day is None


def test_clamp_and_huge_reset(raw_limits):
    raw_limits["five_hour"] = {"used_percentage": 5000, "resets_at": 10 ** 15}
    raw_limits["seven_day"] = {"used_percentage": float("inf"), "resets_at": -5}
    L = lim.parse_limits(raw_limits)
    assert L.five_hour.used == 999.0 and L.five_hour.resets_at == lim.MAX_RESETS_AT
    assert L.seven_day.used == 999.0 and L.seven_day.resets_at == -5
    assert lim.parse_limits(dict(raw_limits, five_hour={"used_percentage": 140, "resets_at": 5})).five_hour.used == 140


def test_reset_rule_at_exactly_resets_at():
    w = lim.Window(80.0, 1000)
    assert lim.view(w, 999) == lim.View(80.0, 1000, False)
    assert lim.view(w, 1000) == lim.View(0.0, None, True)      # at the instant: reset
    assert lim.view(w, 1001) == lim.View(0.0, None, True)
    assert lim.view(None, 5) == lim.ABSENT and lim.ABSENT.used is None


@pytest.mark.parametrize("used,px", [(0, 0), (None, 0), (0.01, 2), (1, 2), (50, 17), (74.9, 34 * 0.749),
                                     (100, 34), (140, 34), (999, 34)])
def test_fill_px(used, px):
    assert lim.fill_px(used, 34) == pytest.approx(px)


def test_fill_never_exceeds_a_narrow_track():
    assert lim.fill_px(0.5, 1) == 1


def test_percent_text_rounds_half_up():
    assert [lim.percent_text(v) for v in (26.0, 25.5, 25.4, 0.4, 0.5, 100, 999, None)] == \
        ["26%", "26%", "25%", "0%", "1%", "100%", "999%", "n/a"]


def test_gauge_fill_height_minimum_two_px_and_cap():
    h = lim.GAUGE_H
    assert lim.fill_px(0, 20) == 0 and lim.fill_px(None, 20) == 0
    assert lim.fill_px(0.4, 20) == lim.MIN_FILL_PX == 2.0            # tiny but nonzero: still visible
    assert lim.fill_px(50, 20) == pytest.approx(10)
    assert lim.fill_px(100, 20) == 20 and lim.fill_px(140, 20) == 20
    assert lim.fill_px(50, h) == pytest.approx(h / 2) and lim.fill_px(999, h) == h


def test_gradient_stops_per_theme_bottom_to_top():
    assert lim.gradient_stops("dark") == ((0.0, "#7fd08e"), (0.5, "#e6c34a"), (0.75, "#e8963c"), (1.0, "#e0453a"))
    assert lim.gradient_stops("light") == ((0.0, "#4fb86a"), (0.5, "#d4a82a"), (0.75, "#dd7f1f"), (1.0, "#cf3a2f"))
    for theme in ("dark", "light"):
        pos = [p for p, _ in lim.gradient_stops(theme)]
        assert pos == sorted(pos) and pos[0] == 0.0 and pos[-1] == 1.0


@pytest.mark.parametrize("used,hot", [(None, False), (0, False), (89.9, False), (90, True), (140, True)])
def test_hot_from_ninety_percent(used, hot):
    assert lim.is_hot(used) is hot


def test_old_state_colours_are_gone():
    assert not hasattr(lim, "state_of") and not hasattr(lim, "state_color")


def test_fetch_good_and_errors(server, raw_limits):
    server.responses["/l"] = (200, json.dumps(raw_limits).encode(), 0)
    server.responses["/bad"] = (200, b"[1]", 0)
    server.responses["/json"] = (200, b"{nope", 0)
    L, raw = lim.fetch(server.base + "/l")
    assert L.five_hour.used == 26.0 and raw == raw_limits
    for path in ("/bad", "/json", "/missing"):       # wrong shape, bad JSON, HTTP 404
        with pytest.raises(FeedError):
            lim.fetch(server.base + path)
    with pytest.raises(FeedError):
        lim.fetch("http://127.0.0.1:1/x", timeout=1)


def test_state_round_trip_of_cached_limits(tmp_path, raw_limits):
    st = cfgmod.State(tmp_path / "state.json")
    st.limits_cache = raw_limits
    st.save()
    assert cfgmod.State.load(tmp_path / "state.json").limits_cache == raw_limits
    (tmp_path / "state.json").write_text(json.dumps({"limits_cache": [1], "collapsed": "yes"}))
    st = cfgmod.State.load(tmp_path / "state.json")
    assert st.limits_cache is None and st.collapsed is False


def test_state_round_trip_of_collapsed(tmp_path):
    st = cfgmod.State(tmp_path / "s.json")
    assert st.collapsed is False
    st.collapsed = True
    st.save()
    assert cfgmod.State.load(tmp_path / "s.json").collapsed is True


def test_limits_module_is_qt_free():
    import subprocess, sys
    out = subprocess.run([sys.executable, "-c", "import fumes.limits, sys; print('PyQt6' in sys.modules)"],
                         capture_output=True, text=True, cwd=str(HERE.parent))
    assert out.stdout.strip() == "False"
