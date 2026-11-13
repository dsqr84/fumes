import pytest

from fumes import place
from fumes.place import Rect, compute_position, is_fullscreen

SCREEN = Rect(0, 0, 1920, 1080)
TB = Rect(0, 1032, 1920, 1080)       # 48 px tall at dpr 1
TRAY = Rect(1500, 1032, 1920, 1080)


def pos(anchor="tray", w=300, h=28, margin=8, dpr=1.0, offset=0, tb=TB, tray=TRAY, screen=SCREEN):
    return compute_position(tb, tray, w, h, anchor, margin, dpr, offset, screen)


def test_tray_anchor():
    x, y, off = pos()
    assert x == 1500 - 8 - 300 and y == 1032 + (48 - 28) // 2 and off == 0


def test_left_and_right_anchor():
    assert pos("left")[0] == 8
    assert pos("right")[0] == 1920 - 8 - 300


@pytest.mark.parametrize("dpr", [1.0, 1.25, 1.5])
def test_dpi_scaling(dpr):
    tb = Rect(0, 1080 - 48 * dpr, 1920, 1080)   # physical, 48 logical tall
    tray = Rect(1500, tb.top, 1920, 1080)
    x, y, _ = compute_position(tb, tray, 300, 28, "tray", 8, dpr, 0, Rect(0, 0, 1920 / dpr, 1080 / dpr))
    assert x == round(1500 / dpr - 8 - 300)
    assert y == round(tb.top / dpr + 10)


def test_offset_applied_and_clamped():
    assert pos(offset=-100)[0] == 1192 - 100 and pos(offset=-100)[2] == -100
    x, _, off = pos(offset=-100000)
    assert x == 0 and off == -1192
    x, _, off = pos(offset=100000)
    assert x == 1920 - 300 and off == 1920 - 300 - 1192


def test_fallback_when_rects_missing():
    x, y, _ = compute_position(None, None, 300, 28, "tray", 8, 1.0, 0, SCREEN)
    assert x == 1920 - 8 - 300 and y == 1080 - 48 + 10
    x, y, _ = compute_position(None, None, 300, 28, "tray", 8, 1.5, 0, Rect(0, 0, 1280, 720))
    assert x == 1280 - 8 - 300 and y == 720 - 48 + 10


def test_tray_missing_but_taskbar_known():
    x, _, _ = compute_position(TB, None, 300, 28, "tray", 8, 1.0, 0, SCREEN)
    assert x == 1920 - 8 - 300


def test_fullscreen():
    mon = Rect(0, 0, 1920, 1080)
    assert is_fullscreen(Rect(0, 0, 1920, 1080), mon, "Chrome_WidgetWin_1")
    assert is_fullscreen(Rect(-8, -8, 1928, 1088), mon)
    assert not is_fullscreen(Rect(0, 0, 1920, 1032), mon, "x")
    for cls in ("Progman", "WorkerW", "Shell_TrayWnd"):
        assert not is_fullscreen(Rect(0, 0, 1920, 1080), mon, cls)
    assert not is_fullscreen(None, mon) and not is_fullscreen(mon, None)


from fumes.place import move_step, outside_press, peek_position  # noqa: E402


def test_outside_press_decision():
    assert outside_press(True, "fal", "openai") == "switch:fal"
    assert outside_press(True, "openai", "openai") == "close"
    assert outside_press(True, None, "openai") == "close"      # on the strip, between cells
    assert outside_press(False, None, "openai") == "close"
    assert outside_press(False, "fal", "openai") == "close"


STRIP = Rect(1000, 1040, 1400, 1068)


def test_peek_centred_above():
    assert peek_position(1200, STRIP, (340, 300), SCREEN) == (1030, 1040 - 8 - 300)


def test_peek_clamped_left_and_right():
    assert peek_position(50, STRIP, (340, 300), SCREEN)[0] == 8
    assert peek_position(1900, STRIP, (340, 300), SCREEN)[0] == 1920 - 8 - 340


def test_peek_flips_below_when_no_room_above():
    x, y = peek_position(1200, Rect(1000, 100, 1400, 128), (340, 300), SCREEN)
    assert y == 128 + 8
    x, y = peek_position(1200, Rect(1000, 316, 1400, 344), (340, 300), SCREEN)   # exactly room: 316-8-300 = 8
    assert y == 8


@pytest.mark.parametrize("mode,event,new,action", [
    (False, "press", False, "none"), (False, "move", False, "none"),
    (False, "release", False, "peek"), (False, "esc", False, "none"), (False, "rightclick", False, "none"),
    (True, "press", True, "drag"), (True, "move", True, "drag"),
    (True, "release", False, "commit"), (True, "esc", False, "cancel"), (True, "rightclick", False, "cancel"),
])
def test_move_step_transitions(mode, event, new, action):
    assert move_step(mode, event) == (new, action)


def test_left_click_peeks_only_outside_move_mode():
    assert move_step(False, "release")[1] == "peek"
    assert move_step(True, "release")[1] != "peek"


# ---- collapse: anchored edge, the wanted offset, and hit-testing ----------------------
from fumes.place import hit_test  # noqa: E402

WIDE, NARROW = 900, 300


@pytest.mark.parametrize("anchor", ["tray", "right", "left"])
@pytest.mark.parametrize("offset", [0, -50, 40, -300])
def test_collapse_keeps_the_anchored_edge(anchor, offset):
    """Unclamped offsets: the right edge (tray, right) or the left edge (left) does not move."""
    xw = pos(anchor, w=WIDE, offset=offset)[0]
    xn = pos(anchor, w=NARROW, offset=offset)[0]
    if anchor == "left":
        assert xn == xw
    else:
        assert xn + NARROW == xw + WIDE


@pytest.mark.parametrize("anchor", ["tray", "right", "left"])
def test_an_offset_valid_only_when_narrow_must_be_kept_as_wanted(anchor):
    """-1190 (tray/right) or +1100 (left) is valid for the narrow strip but clamped for the wide one.
    The wide strip's applied offset differs from what was wanted, so the strip must keep the WANTED offset
    (Strip.reposition does) to return to exactly the same place after collapse and expand."""
    wanted = 1100 if anchor == "left" else -1190
    narrow = pos(anchor, w=NARROW, offset=wanted)
    wide = pos(anchor, w=WIDE, offset=wanted)
    assert narrow[2] == wanted and wide[2] != wanted           # the wide clamp changed the offset
    assert pos(anchor, w=NARROW, offset=wide[2])[0] != narrow[0]   # writing it back would lose the place
    assert pos(anchor, w=NARROW, offset=wanted) == narrow       # keeping the wanted offset round-trips


def test_tray_right_edge_is_fixed_exactly():
    for a in ("tray", "right"):
        xw = pos(a, w=WIDE, offset=-20)[0]
        xn = pos(a, w=NARROW, offset=-20)[0]
        assert xw + WIDE == xn + NARROW
    assert pos("left", w=WIDE, offset=20)[0] == pos("left", w=NARROW, offset=20)[0]


SPANS = [(("limits", "claude"), 8, 100), (("provider", "fal"), 126, 60)]


def test_hit_test_expanded():
    btn = (200, 20)
    assert hit_test(8, SPANS, btn, 14) == ("cell", ("limits", "claude"))
    assert hit_test(1, SPANS, btn, 14) == ("cell", ("limits", "claude"))       # 7 px slack
    assert hit_test(0, SPANS, btn, 14) == (None, None)
    assert hit_test(130, SPANS, btn, 14) == ("cell", ("provider", "fal"))
    assert hit_test(112, SPANS, btn, 14) == ("cell", ("limits", "claude"))
    assert hit_test(117, SPANS, btn, 14) == (None, None)                        # between cells
    assert hit_test(200, SPANS, btn, 14) == ("button", None)
    assert hit_test(219, SPANS, btn, 14) == ("button", None)
    assert hit_test(220, SPANS, btn, 14) == (None, None)


def test_hit_test_collapsed_has_only_the_claude_cell_and_the_button():
    spans, btn = SPANS[:1], (117, 20)
    assert hit_test(130, spans, btn, 14) == ("button", None)
    assert hit_test(60, spans, btn, 14) == ("cell", ("limits", "claude"))
    assert hit_test(150, spans, btn, 14) == (None, None)


def test_hit_test_button_wins_over_a_cells_slack_and_none_without_button():
    assert hit_test(110, [(("provider", "a"), 0, 105)], (108, 20), 14) == ("button", None)
    assert hit_test(110, [(("provider", "a"), 0, 105)], None, 14) == ("cell", ("provider", "a"))


@pytest.mark.parametrize("anchor,collapsed,tip", [
    ("tray", False, 1), ("tray", True, -1),       # folds toward the right edge: expanded shows >, collapsed <
    ("right", False, 1), ("right", True, -1),
    ("left", False, -1), ("left", True, 1),       # the reverse for the left anchor
])
def test_chevron_direction_follows_the_anchor(anchor, collapsed, tip):
    assert place.chevron_tip(anchor, collapsed) == tip
