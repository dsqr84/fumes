"""Text for the strip, tooltips and tray. No Qt."""

import html
import math
import textwrap
from datetime import timezone

from .feed import parse_time


DASH = "\u2013"


def money(v):
    """$12.34, <$0.01 (above zero, under a cent), -$1.20."""
    if v < 0:
        return "-" + money(-v)
    if 0 < v < 0.005:
        return "<$0.01"
    return f"${v:.2f}"


def figure(p):
    """(text, kind) for a provider. kind: 'balance' | 'mtd' | 'na'. A month-to-date figure is just the
    amount (the strip adds its own `mtd` label); no figure is an en dash."""
    if p.balance is not None:
        return money(p.balance), "balance"
    if p.mtd is not None:
        return money(p.mtd), "mtd"
    return DASH, "na"


def age_text(seconds):
    """'12m old', '3h old', '2d old'; under a minute is 'now'."""
    s = max(0, int(seconds))
    if s < 60:
        return "now"
    if s < 3600:
        return f"{s // 60}m old"
    if s < 86400:
        return f"{s // 3600}h old"
    return f"{s // 86400}d old"


def local_time(s):
    dt = parse_time(s)
    if dt is None:
        return s or "unknown"
    return local_dt(dt)


def _opt(v):
    return money(v) if v is not None else "n/a"


def clean_text(t):
    """Feed notes quote shell commands in backticks; drawn as plain text they are just noise."""
    return (t or "").replace("`", "")


def tooltip(p, width=48):
    lines = [
        p.name,
        f"Balance: {_opt(p.balance)}",
        f"Month to date: {_opt(p.mtd)}",
        f"Last 30 days: {_opt(p.d30)}",
        f"Status: {p.status}",
        f"Checked: {local_time(p.checked)}",
    ]
    for text in (p.note, p.error):
        if text:
            lines.append("")
            lines.extend(textwrap.wrap(clean_text(text), width) or [""])
    return "\n".join(lines)


def escape_tooltip(text):
    """Plain text as a tooltip's rich text, escaped: Qt would otherwise render markup in network strings."""
    return "<p style='white-space:pre'>" + html.escape(text) + "</p>"


def tooltip_html(p, width=48):
    return escape_tooltip(tooltip(p, width))


def recolor_svg(text, hex_color):
    """QtSvg paints currentColor black; substitute the ink colour."""
    return text.replace("currentColor", hex_color)


# ---- peek window helpers (pure) ---------------------------------------------
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
BREAKDOWN_ROWS = 8


def short_date(day):
    """'2026-11-06' -> 'Nov 6' (no strftime: %-d does not exist on Windows)."""
    try:
        y, m, d = day.split("-")
        return f"{MONTHS[int(m) - 1]} {int(d)}"
    except (ValueError, IndexError, AttributeError):
        return str(day)


def nice_top(v):
    """Round an axis top up to 1, 2 or 5 times a power of ten (never below v; 1 for v <= 0)."""
    if not v or v != v or v <= 0:
        return 1.0
    if not math.isfinite(v) or v > 1e12:
        return 1e12                       # never raises: math.floor(log10(inf)) would, inside a paint event
    exp = math.floor(math.log10(v))
    for k in (1, 2, 5, 10):
        top = k * 10.0 ** exp
        if top >= v * (1 - 1e-12):
            return top
    return 10.0 ** (exp + 1)


def fold_breakdown(rows, limit=BREAKDOWN_ROWS):
    """[(label, usd)] -> at most `limit` rows. Over the limit: limit-1 rows plus one 'Other'
    holding the rest; an 'Other' already in the feed is merged into it, never shown twice."""
    rows = list(rows)
    if len(rows) <= limit:
        return rows
    others = [usd for label, usd in rows if label == "Other"]
    rest = [(label, usd) for label, usd in rows if label != "Other"]
    if len(rest) + (1 if others else 0) <= limit:
        return rest + [("Other", sum(others))]
    return rest[:limit - 1] + [("Other", sum(u for _, u in rest[limit - 1:]) + sum(others))]


def peak_index(values):
    """Index of the largest value (first on a tie), or None when every value is 0."""
    best = None
    for i, v in enumerate(values):
        if v > 0 and (best is None or v > values[best]):
            best = i
    return best


def chart_caption(days, values, hover=None, connected=True):
    """The line under the chart. hover = index of the day under the pointer, else None."""
    if hover is not None and 0 <= hover < len(days):
        return f"{short_date(days[hover])}: {money(values[hover])}"
    i = peak_index(values)
    if i is None:
        # only a connected provider has "no spend"; for one with no data the caption would be a lie
        return "No spend in the last 30 days" if connected else ""
    return f"Peak: {money(values[i])} on {short_date(days[i])}"


def axis_money(v):
    """Axis-top label: whole dollars without cents ($10), otherwise money ($0.50)."""
    return f"${int(v)}" if v == int(v) else money(v)


def checked_text(checked, now, tz=None):
    """'Checked 06:30' when the time is today in tz (None: local), else 'Checked Nov 11 06:30'."""
    dt = parse_time(checked)
    if dt is None:
        return "Checked " + (checked if isinstance(checked, str) and checked else "unknown")
    try:
        at, today = dt.astimezone(tz), now.astimezone(tz)
    except (OverflowError, OSError, ValueError):
        return "Checked " + dt.strftime("%Y-%m-%d %H:%M") + " UTC"
    clock = f"{at.hour:02d}:{at.minute:02d}"
    if at.date() == today.date():
        return f"Checked {clock}"
    return f"Checked {MONTHS[at.month - 1]} {at.day} {clock}"


def break_long_words(text, fits):
    """Split any unbroken run that `fits(chunk)` rejects (too wide for the panel) into chunks that
    fit, so word wrapping cannot overflow. fits is a Qt-free callable here (width test injected)."""
    out = []
    for w in text.split(" "):
        while len(w) > 1 and not fits(w):   # one character cannot be split further
            # Longest prefix that fits, by bisection (width grows with length); always at least one
            # character, so a run of any length is consumed in O(len * log len) width tests.
            lo, hi = 1, len(w) - 1
            while lo < hi:
                mid = (lo + hi + 1) // 2
                if fits(w[:mid]):
                    lo = mid
                else:
                    hi = mid - 1
            out.append(w[:lo])
            w = w[lo:]
        out.append(w)
    return " ".join(out)


# ---- Claude limits (pure; time and zone are injected) ------------------------------
WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def countdown(seconds):
    """'in 2d 5h', 'in 3h 24m', 'in 44m', 'in under a minute' (rounded down)."""
    s = max(0, int(seconds))
    if s < 60:
        return "in under a minute"
    if s < 3600:
        return f"in {s // 60}m"
    if s < 86400:
        return f"in {s // 3600}h {s % 3600 // 60}m"
    return f"in {s // 86400}d {s % 86400 // 3600}h"


def local_dt(dt, tz=None):
    """'2026-11-12 07:16' in tz (None: this machine's zone)."""
    try:
        return dt.astimezone(tz).strftime("%Y-%m-%d %H:%M")
    except (OverflowError, OSError, ValueError):   # the platform cannot convert it: say it in UTC
        return dt.strftime("%Y-%m-%d %H:%M") + " UTC"


def _reset_when(v, now, tz=None):
    """('10:40', 'in 3h 20m') or ('Fri 02:00', 'in 18h 40m') for a window with a reset time."""
    from datetime import datetime
    at = datetime.fromtimestamp(v.resets_at, tz)
    clock = f"{at.hour:02d}:{at.minute:02d}"
    day = "" if at.date() == now.astimezone(tz).date() else WEEKDAYS[at.weekday()] + " "
    return day + clock, countdown(v.resets_at - now.timestamp())


def reset_line(v, now, tz=None):
    """The peek's reset line for a limits.View. now is an aware datetime; tz a tzinfo or None (local)."""
    if v.used is None:
        return "No reading yet"
    if v.reset:
        return "Reset: a new window starts with your next message"
    when, left = _reset_when(v, now, tz)
    return f"Resets {when} ({left})"


def limits_tooltip(views, updated, now, tz=None):
    """views: [(caption, limits.View)]. The strip shows no numbers, so they are all here."""
    from .limits import percent_text
    lines = ["Claude subscription"]
    for cap, v in views:
        if v.used is None:
            lines.append(f"{cap}: no reading yet")
        elif v.reset:
            lines.append(f"{cap}: {percent_text(v.used)} used (reset)")
        else:
            when, left = _reset_when(v, now, tz)
            lines.append(f"{cap}: {percent_text(v.used)} used, resets {when} ({left})")
    lines.append(f"Updated: {local_dt(updated, tz)}")
    return "\n".join(lines)


def fit_pixel_size(cap_height, target, tol=0.5, lo=6, hi=72):
    """Whole pixel size whose cap height is closest to `target` without exceeding it by more than
    `tol`. cap_height(px) -> float is injected (a Qt metric in the app, a fake in tests)."""
    best = None
    for px in range(lo, hi + 1):
        c = cap_height(px)
        if c > target + tol:
            break
        if best is None or abs(c - target) < abs(best[1] - target):
            best = (px, c)
    return best[0] if best else lo
