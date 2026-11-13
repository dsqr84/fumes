"""Fetch, validate and normalise the ai-usage JSON feed. No Qt."""

import re
import json
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone

KNOWN_ORDER = ["deepseek", "openai", "fal", "anthropic"]
STATUSES = ("ok", "stale", "not_connected", "error")


class FeedError(Exception):
    """The payload is unusable (wrong shape or the fetch failed)."""


@dataclass
class Provider:
    id: str
    name: str
    status: str = "ok"
    balance: float | None = None
    mtd: float | None = None
    d30: float | None = None
    checked: str = ""
    note: str = ""
    error: str = ""
    daily: dict = field(default_factory=dict)       # {"YYYY-MM-DD": usd}, sparse (missing day = 0)
    breakdown: list = field(default_factory=list)   # [(label, usd)], largest first


@dataclass
class Feed:
    generated: datetime
    providers: list  # list[Provider], known ids first then unknown in feed order
    days: list = field(default_factory=list)  # chart x axis, oldest first, "YYYY-MM-DD"


MAX_BREAKDOWN = 50
MAX_LABEL = 200
MAX_NAME = 80      # free text from the network is capped before anything measures or draws it
MAX_TEXT = 2000
DAY_RE = re.compile(r"\d{4}-\d{2}-\d{2}")


def _day(v):
    """A real calendar date in YYYY-MM-DD form, or None."""
    if not isinstance(v, str) or DAY_RE.fullmatch(v) is None:
        return None
    try:
        datetime.strptime(v, "%Y-%m-%d")
    except ValueError:
        return None
    return v


def _usd(v):
    """A non-negative, finite number (not a bool), else None."""
    n = _num(v)
    if n is None or n != n or n < 0 or n == float("inf"):
        return None
    return n


def _daily(v):
    if not isinstance(v, dict):
        return {}
    out = {}
    for k, amount in v.items():
        d, n = _day(k), _usd(amount)
        if d is not None and n is not None:
            out[d] = n
    return out


def _breakdown(v):
    if not isinstance(v, list):
        return []
    out = []
    for row in v:
        if not isinstance(row, dict) or not isinstance(row.get("label"), str):
            continue
        n = _usd(row.get("usd"))
        if n is None:
            continue
        out.append((row["label"][:MAX_LABEL], n))
        if len(out) >= MAX_BREAKDOWN:
            break
    return out


def _days(v):
    if not isinstance(v, list):
        return []
    return [d for d in (_day(x) for x in v) if d is not None]


def _num(v):
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    return float(v)


def _str(v):
    return v if isinstance(v, str) else ""


def parse_time(s):
    """ISO string (Z or offset) -> aware UTC datetime, or None."""
    if not isinstance(s, str) or not s:
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        dt = dt.astimezone(timezone.utc)
    except (ValueError, OverflowError):
        return None
    # A time from the network outside this range is nonsense, and converting it to local time can
    # raise (Windows cannot convert dates before 1970): treat it as no time at all.
    if not 2000 <= dt.year < 2100:
        return None
    return dt


ID_RE = re.compile(r"[a-z0-9][a-z0-9_-]{0,31}")


def valid_id(pid):
    """Provider ids come from the network and name logo files: plain lowercase slugs only."""
    return isinstance(pid, str) and ID_RE.fullmatch(pid) is not None


def parse_feed(obj) -> Feed:
    """Validate a decoded payload. Raises FeedError; never anything else."""
    if not isinstance(obj, dict):
        raise FeedError("payload is not an object")
    generated = parse_time(obj.get("generated"))
    if generated is None:
        raise FeedError("missing or invalid 'generated'")
    raw = obj.get("providers")
    if not isinstance(raw, dict):
        raise FeedError("missing or invalid 'providers'")
    parsed = {}
    for pid, p in raw.items():
        if not isinstance(pid, str) or not isinstance(p, dict):
            raise FeedError(f"provider {pid!r} is not an object")
        if not valid_id(pid):
            continue   # an id becomes a logo file name: one that could be a path is dropped
        status = p.get("status")
        if status not in STATUSES:
            status = "error"
        parsed[pid] = Provider(
            id=pid,
            name=_str(p.get("name"))[:MAX_NAME] or pid,
            status=status,
            balance=_num(p.get("balance")),
            mtd=_num(p.get("mtd")),
            d30=_num(p.get("d30")),
            checked=_str(p.get("checked")),
            note=_str(p.get("note"))[:MAX_TEXT],
            error=_str(p.get("error"))[:MAX_TEXT],
            daily=_daily(p.get("daily")),
            breakdown=_breakdown(p.get("breakdown")),
        )
    ordered = [parsed[i] for i in KNOWN_ORDER if i in parsed]
    ordered += [p for i, p in parsed.items() if i not in KNOWN_ORDER]
    return Feed(generated=generated, providers=ordered, days=_days(obj.get("days")))


def is_hidden(p: Provider):
    """A provider that is not connected and has neither a balance nor a month-to-date figure has nothing to
    show on the strip. One that still has a balance (entered by hand) stays."""
    return p.status == "not_connected" and p.balance is None and p.mtd is None


def has_spend_data(p: Provider):
    return p.mtd is not None or p.d30 is not None or any(v > 0 for v in p.daily.values())


def peek_mode(p: Provider):
    """What the peek shows: 'empty' (header, note, footer only), 'figures' (a balance but no spend data:
    the figures row, no chart or breakdown) or 'full'."""
    if has_spend_data(p):
        return "full"
    return "figures" if p.balance is not None else "empty"


def lead_index(p: Provider):
    """Which of the peek's three figures (balance, month to date, last 30 days) the strip shows."""
    if p.balance is not None:
        return 0
    return 1 if p.mtd is not None else None


def select(feed: Feed, wanted=None):
    """Providers to show on the strip. wanted=None: all. Otherwise the listed ids in that order
    (ids missing from the feed are skipped). A hidden provider (is_hidden) is never returned."""
    if not wanted:
        pool = list(feed.providers)
    else:
        by_id = {p.id: p for p in feed.providers}
        pool = [by_id[i] for i in wanted if i in by_id]
    return [p for p in pool if not is_hidden(p)]


def fetch_raw(url, timeout=10):
    """GET and decode JSON. Returns the decoded object; raises FeedError."""
    req = urllib.request.Request(url, headers={"User-Agent": "fumes"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = r.read()
    except Exception as e:  # URLError, HTTPError, timeout, ValueError, OSError
        raise FeedError(f"fetch failed: {e}") from e
    try:
        return json.loads(data.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as e:
        raise FeedError(f"bad JSON: {e}") from e


def fetch(url, timeout=10):
    """Returns (Feed, raw_obj). Raises FeedError."""
    raw = fetch_raw(url, timeout)
    return parse_feed(raw), raw


def age_seconds(feed: Feed, now=None):
    now = now or datetime.now(timezone.utc)
    return (now - feed.generated).total_seconds()


def is_stale(feed: Feed, stale_after_minutes, now=None):
    """Older than the limit (strictly) by the feed's own 'generated'."""
    return age_seconds(feed, now) > stale_after_minutes * 60


def total_credit(providers):
    vals = [p.balance for p in providers if p.balance is not None]
    return sum(vals) if vals else None
