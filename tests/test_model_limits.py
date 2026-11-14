"""Per-model weekly limits (e.g. Fable): parsed as untrusted, drawn as a blue bar under the weekly one."""
import json
from pathlib import Path

from fumes import config as cfgmod
from fumes import feed as feedmod
from fumes import fmt, limits as lim, ui
from helpers import spy_strip

HERE = Path(__file__).parent
NOW = __import__("test_limits_ui").NOW


def doc(models):
    d = json.loads((HERE / "sample-limits.json").read_text("utf-8"))
    d["seven_day_models"] = models
    return d


def fable(used=11.0):
    return {"Fable": {"used_percentage": used, "resets_at": 1795158000}}


def test_models_parse_and_bad_entries_are_dropped():
    raw = {"Fable": {"used_percentage": 11, "resets_at": 1795158000}, "": {"used_percentage": 1, "resets_at": 1},
           "Bad": {"used_percentage": "x", "resets_at": 1}, "X\u0000‮Y" + "z" * 40: {"used_percentage": 2, "resets_at": 1},
           "A": {}, "B": 5}
    got = lim.parse_limits(doc(raw)).models
    assert [n for n, _ in got] == ["Fable", "XYzzzzzzzzzzzzzzzzzzzzzz"]
    assert got[0][1].used == 11.0 and len(got[1][0]) == lim.MAX_MODEL_NAME
    assert lim.parse_limits(doc({f"m{i}": {"used_percentage": i, "resets_at": 1} for i in range(9)})).models[-1][0] == "m3"
    for junk in (None, [], "x", 5):
        assert lim.parse_limits(doc(junk)).models == ()


def test_windows_lists_the_model_after_the_plan():
    caps = [(c, k) for c, _, k in lim.windows(lim.parse_limits(doc(fable())))]
    assert caps == [("5-hour limit", "all"), ("Weekly limit", "all"), ("Weekly limit, Fable", "model")]
    assert [c for c, _, _ in lim.windows(None)] == ["5-hour limit", "Weekly limit"]


def make(models):
    raw_feed = json.loads((HERE / "sample-feed-live.json").read_text("utf-8"))
    s = ui.Strip(cfgmod.Config(), theme="dark")
    s.now_fn = lambda: NOW
    s.set_feed(feedmod.parse_feed(raw_feed), now=NOW)
    s.set_limits(lim.parse_limits(doc(models)), now=NOW)
    return s


def test_wk_row_gets_a_second_blue_bar(qapp):
    s = make(fable(80.0))
    c = s.cells()[0]
    assert c.meters[1]["model"] == ("Fable", 80.0) and c.meters[0]["model"] is None
    _, img = spy_strip(s)
    x = c.x + ui.LOGO + ui.GAP_LOGO + c.label_w + ui.GAP_LABEL + 6          # inside both fills
    cy = s.row_centre(1)
    upper = img.pixelColor(int(x), int(cy - ui.PAIR_GAP / 2 - ui.PAIR_H / 2))
    lower = img.pixelColor(int(x), int(cy + ui.PAIR_GAP / 2 + ui.PAIR_H / 2))
    assert lower.blue() > lower.red() + 60                                     # the model bar is blue
    assert upper.green() > upper.blue()                                        # the weekly bar keeps its palette


def test_no_models_keeps_one_bar_per_row(qapp):
    s = make(None)
    assert all(m["model"] is None for m in s.cells()[0].meters)


def test_tooltip_and_peek_include_the_model(qapp):
    s = make(fable())
    ts = NOW.timestamp()
    text = fmt.limits_tooltip([(cap, lim.view(w, ts)) for cap, w, _ in lim.windows(s.limits)],
                              s.limits.updated, NOW, None)
    assert "Weekly limit, Fable: 11% used" in text
    from fumes.peek import Peek
    pk = Peek(s)
    assert pk.set_limits()
    kinds = [(b["cap"], b["kind"]) for b in pk._L["blocks"]]
    assert kinds[-1] == ("Weekly limit, Fable", "model") and len(kinds) == 3
