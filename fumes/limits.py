"""The Claude subscription-limits document: fetch, validate, and the pure maths behind the meters. No Qt.

The document is untrusted network input (same as the main feed): anything off-shape is a failed
fetch or an absent window, never an exception for the UI thread."""

import math
from dataclasses import dataclass

from . import feed as feedmod
from .feed import FeedError

MAX_USED = 999.0                 # a window can exceed 100 %; the display stops somewhere
MAX_RESETS_AT = 4102444800       # 2100-01-01: keeps datetime conversion safe
MIN_FILL_PX = 2.0
GAUGE_W, GAUGE_H = 12, 18        # the strip gauge track in logical px: 12 wide (8 read as too slight), 18 tall, the logo's height (20 stuck out 1 px each side)
# Gradient stops, bottom/left (0.0) to top/right (1.0), fixed to the track: a low reading shows only the
# green end, a high one reaches into red. (position, colour) per theme.
GRADIENT = {
    "dark": ((0.0, "#7fd08e"), (0.5, "#e6c34a"), (0.75, "#e8963c"), (1.0, "#e0453a")),
    "light": ((0.0, "#4fb86a"), (0.5, "#d4a82a"), (0.75, "#dd7f1f"), (1.0, "#cf3a2f")),
}
HOT_AT = 90.0                    # the peek gives the reset line more weight from here


def gradient_stops(theme):
    return GRADIENT["dark" if theme == "dark" else "light"]


@dataclass(frozen=True)
class Window:
    used: float       # percent, 0..999
    resets_at: int    # Unix epoch seconds


@dataclass(frozen=True)
class Limits:
    five_hour: "Window | None"
    seven_day: "Window | None"
    updated: object   # aware UTC datetime


@dataclass(frozen=True)
class View:
    """A window as the user should see it at a given moment."""
    used: "float | None"          # None: absent (no reading)
    resets_at: "int | None"       # None once reset, or absent
    reset: bool = False           # the window has reset: a fresh one nobody has used yet


ABSENT = View(None, None, False)


def _window(v):
    if not isinstance(v, dict):
        return None
    used, at = v.get("used_percentage"), v.get("resets_at")
    for n in (used, at):
        if isinstance(n, bool) or not isinstance(n, (int, float)):
            return None
    if used != used or used < 0 or at != at:
        return None
    try:
        at = int(at)
    except (OverflowError, ValueError):
        return None
    return Window(used=min(float(used), MAX_USED), resets_at=min(at, MAX_RESETS_AT))


def parse_limits(obj) -> Limits:
    """Validate a decoded document. Raises FeedError; never anything else."""
    if not isinstance(obj, dict):
        raise FeedError("limits document is not an object")
    updated = feedmod.parse_time(obj.get("updated"))
    if updated is None:
        raise FeedError("missing or invalid 'updated'")
    return Limits(_window(obj.get("five_hour")), _window(obj.get("seven_day")), updated)


def fetch(url, timeout=10):
    """Returns (Limits, raw_obj). Raises FeedError."""
    raw = feedmod.fetch_raw(url, timeout)
    return parse_limits(raw), raw


def view(window, now_ts) -> View:
    """A window at time now_ts (epoch seconds). At or past resets_at it has reset: 0 % used, no time."""
    if window is None:
        return ABSENT
    if window.resets_at <= now_ts:
        return View(0.0, None, True)
    return View(window.used, window.resets_at, False)


def fill_px(used, track_px) -> float:
    """Length of a fill (gauge height or bar width): min(used, 100) % of the track; a nonzero value shows at least 2 px."""
    if used is None or used <= 0:
        return 0.0
    w = track_px * min(used, 100.0) / 100.0
    return min(float(track_px), max(MIN_FILL_PX, w))


def percent_text(used) -> str:
    return "n/a" if used is None else f"{int(math.floor(used + 0.5))}%"


def is_hot(used) -> bool:
    return used is not None and used >= HOT_AT
