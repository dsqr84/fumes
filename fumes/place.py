"""Pure geometry. Win32 rects are physical pixels; strip size and results are Qt logical."""

from typing import NamedTuple

FALLBACK_TASKBAR = 48  # logical px


class Rect(NamedTuple):
    left: float
    top: float
    right: float
    bottom: float

    @property
    def width(self):
        return self.right - self.left

    @property
    def height(self):
        return self.bottom - self.top


def to_logical(r, dpr):
    return Rect(r.left / dpr, r.top / dpr, r.right / dpr, r.bottom / dpr)


def clamp(v, lo, hi):
    if hi < lo:
        return lo
    return max(lo, min(hi, v))


def taskbar_logical(taskbar, screen, dpr):
    """Taskbar rect in logical px; falls back to a 48px band at the bottom of `screen`
    (a logical Rect) when the Win32 lookup gave nothing."""
    if taskbar is not None:
        return to_logical(taskbar, dpr)
    return Rect(screen.left, screen.bottom - FALLBACK_TASKBAR, screen.right, screen.bottom)


def base_x(tb, tray, strip_w, anchor, margin, dpr):
    """Default x (logical) before any saved offset."""
    if anchor == "left":
        return tb.left + margin
    if anchor == "right":
        return tb.right - margin - strip_w
    # tray: right edge `margin` left of the notification area
    right = to_logical(tray, dpr).left if tray is not None else tb.right
    return right - margin - strip_w


def compute_position(taskbar, tray, strip_w, strip_h, anchor, margin, dpr, offset, screen):
    """-> (x, y, applied_offset) in logical px.

    The saved offset is clamped so the strip stays inside the taskbar band.
    """
    tb = taskbar_logical(taskbar, screen, dpr)
    x0 = base_x(tb, tray, strip_w, anchor, margin, dpr)
    lo, hi = tb.left, tb.right - strip_w
    x = clamp(x0 + offset, lo, hi)
    y = tb.top + (tb.height - strip_h) / 2
    return round(x), round(y), round(x - x0)


def is_fullscreen(win, monitor, cls=""):
    """Does the foreground window cover its whole monitor (and is not the shell)?"""
    if cls in ("Progman", "WorkerW", "Shell_TrayWnd"):
        return False
    if win is None or monitor is None:
        return False
    return (win.left <= monitor.left and win.top <= monitor.top
            and win.right >= monitor.right and win.bottom >= monitor.bottom)


def hit_test(x, spans, button=None, gap=0):
    """What is at logical x on the strip. spans: [(key, x, width)] of the laid-out cells; button: (x, width)
    or None. -> ('button', None) | ('cell', key) | (None, None). The button wins where a cell's slack reaches it."""
    if button is not None and button[0] <= x < button[0] + button[1]:
        return "button", None
    for key, cx, w in spans:
        if cx - gap / 2 <= x <= cx + w + gap / 2:
            return "cell", key
    return None, None


def outside_press(over_strip, cell_id, current_id):
    """What a press outside the open peek does: 'switch:<id>' or 'close'."""
    if over_strip and cell_id is not None and cell_id != current_id:
        return f"switch:{cell_id}"   # the caller already holds the key; the text only says "switch"
    return "close"


PEEK_GAP = 8
PEEK_MARGIN = 8


def peek_position(cell_cx, strip, size, screen, gap=PEEK_GAP, margin=PEEK_MARGIN):
    """Top-left (x, y) of the peek, logical px. Centred on the cell, clamped inside `screen`
    (a Rect) with `margin`; above the strip, or below it when there is no room above.
    strip is a Rect (the strip's global geometry), size is (w, h)."""
    w, h = size
    x = clamp(cell_cx - w / 2, screen.left + margin, screen.right - margin - w)
    y = strip.top - gap - h
    if y < screen.top + margin:
        y = strip.bottom + gap
    return round(x), round(y)


def move_step(mode, event):
    """Move-mode state machine. mode: bool; event: press | move | release | esc | rightclick
    -> (new_mode, action) with action in none | drag | commit | cancel | peek.

    Outside move mode a left release is a click: the caller checks it ended over the cell it
    started on, then opens the peek; presses and moves do nothing (a drag never moves the strip).
    In move mode press/move follow the pointer, a release saves, Esc and right-click cancel."""
    if not mode:
        return False, ("peek" if event == "release" else "none")
    if event in ("press", "move"):
        return True, "drag"
    if event == "release":
        return False, "commit"
    if event in ("esc", "rightclick"):
        return False, "cancel"
    return True, "none"


def chevron_tip(anchor, collapsed):
    """Which way the collapse chevron points: +1 right (`>`), -1 left (`<`). The strip folds toward the edge
    it is anchored to: tray and right anchors keep the right edge (it folds right), left keeps the left."""
    toward_right = anchor != "left"
    # expanded: point the way it will fold; collapsed: point back the way it will grow
    return 1 if toward_right != bool(collapsed) else -1
