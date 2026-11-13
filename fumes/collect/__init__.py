"""Collect balances and spend from each provider's own API, with the user's keys.

`collect()` returns the same payload shape as a remote feed (see feed.parse_feed), so the strip,
the peeks and the cache treat the two alike. Each provider is a module in this package with:

    ID, NAME            the id (logo file, credential name) and the display name
    KEY_LABEL           what kind of key it needs, as shown in Settings ("Admin key")
    ANCHORED            True when it has no balance API and the user enters the credit by hand
    run(ctx) -> dict    {"rows": [(day "YYYY-MM-DD", label, usd)], "balance": float | None, "note": str}
    spend_today(ctx)    (ANCHORED only) dollars spent so far today, for a new anchor's day_spend

run() raises NotConnected when there is no usable key, ProviderError when the provider cannot be
read this time. Days are UTC, because the providers' daily buckets are.
"""

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from .. import keycheck
from .http import Http, NotConnected, ProviderError  # noqa: F401 (re-exported)
from .ledger import Ledger  # noqa: F401 (re-exported)

BREAKDOWN_ROWS = 8      # per provider; the rest fold into "Other"
STAMP = "%Y-%m-%dT%H:%M:%SZ"


@dataclass
class Context:
    now: datetime
    key: str | None
    http: Http
    ledger: Ledger
    pid: str = ""


def providers():
    """The provider modules, in strip order."""
    from . import anthropic, deepseek, fal, openai
    return [deepseek, openai, fal, anthropic]


def by_id(pid, custom=()):
    return next((m for m in list(providers()) + list(custom) if m.ID == pid), None)


# ---------------------------------------------------------------- helpers --
def utc_day(ts):
    """Unix seconds or an ISO-8601 string -> 'YYYY-MM-DD' in UTC. None if unreadable."""
    try:
        if isinstance(ts, bool):
            return None
        if isinstance(ts, (int, float)):
            return datetime.fromtimestamp(ts, timezone.utc).date().isoformat()
        text = str(ts).strip()
        if len(text) == 10:
            return datetime.strptime(text, "%Y-%m-%d").date().isoformat()
        dt = datetime.fromisoformat(text[:-1] + "+00:00" if text.endswith("Z") else text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).date().isoformat()
    except (ValueError, OverflowError, OSError):
        return None


def window(now):
    """(first day fetched, first of this month, first of the last 30 days) as dates."""
    today = now.astimezone(timezone.utc).date()
    month_start = today.replace(day=1)
    d30 = today - timedelta(days=29)
    return min(month_start, d30), month_start, d30


def midnight(day):
    """Unix seconds of a date's UTC midnight."""
    return int(datetime(day.year, day.month, day.day, tzinfo=timezone.utc).timestamp())


def money(usd):
    if usd is None:
        return "n/a"
    if 0 < usd < 0.005:
        return "<$0.01"
    return f"${usd:,.2f}"


def fetch_start(ctx):
    """The first day to ask a provider for: the chart window, or the anchor's day when that is earlier."""
    start = window(ctx.now)[0]
    a = ctx.ledger.anchor(ctx.pid) if ctx.pid else None
    if a:
        start = min(start, datetime.strptime(a["ts"][:10], "%Y-%m-%d").date())
    return start


def summarise(rows, now):
    """Rows -> month-to-date, 30-day total, a sparse daily series and the 30-day breakdown."""
    start, month_start, d30 = (d.isoformat() for d in window(now))
    daily, by_label = defaultdict(float), defaultdict(float)
    for day, label, usd in rows:
        if day < start:
            continue
        daily[day] += usd
        if day >= d30:
            by_label[label] += usd
    ranked = sorted(by_label.items(), key=lambda kv: (-kv[1], kv[0]))
    breakdown = [{"label": k, "usd": round(v, 6)} for k, v in ranked[:BREAKDOWN_ROWS]]
    if len(ranked) > BREAKDOWN_ROWS:
        breakdown.append({"label": "Other", "usd": round(sum(v for _, v in ranked[BREAKDOWN_ROWS:]), 6)})
    return {"mtd": round(sum(v for d, v in daily.items() if d >= month_start), 6),
            "d30": round(sum(v for d, v in daily.items() if d >= d30), 6),
            "daily": {d: round(v, 6) for d, v in sorted(daily.items()) if d >= d30},
            "breakdown": breakdown}


EMPTY = {"mtd": None, "d30": None, "daily": {}, "breakdown": [], "balance": None, "note": ""}


def anchored_note(a, source):
    return (f"Balance = {money(a['usd'])} entered {a['ts'][:10]}, minus the {source}'s spend since. "
            "Enter it again after a top-up.")


# ---------------------------------------------------------------- collect --
def collect(now, store, ledger, http=None, previous=None, enabled=None, custom=()):
    """(payload, ids of the providers that failed this run). `enabled`: the ids to ask (default all).
    One provider failing never touches the others: it keeps its previous figures, marked stale."""
    http = http or Http()
    stamp = now.astimezone(timezone.utc).strftime(STAMP)
    out, failed = {}, []
    for mod in list(providers()) + list(custom):     # custom: the user's own (collect/custom.py)
        if enabled is not None and mod.ID not in enabled:
            continue
        ctx = Context(now=now, key=store.get(mod.ID), http=http, ledger=ledger, pid=mod.ID)
        try:
            if not ctx.key and getattr(mod, "KEY_LABEL", "x"):
                raise NotConnected("No key saved. Add one in Settings.")
            bad = keycheck.foreign(mod.ID, ctx.key) if ctx.key else None
            if bad:                  # never send another provider's key to this one
                raise NotConnected(bad)
            got = mod.run(ctx)
            bal = got.get("balance")
            entry = dict(summarise(got.get("rows") or [], now), status="ok", checked=stamp,
                         balance=None if bal is None else round(bal, 6), note=got.get("note") or "")
        except NotConnected as e:
            entry = dict(EMPTY, status="not_connected", checked=stamp, note=str(e))
            a = ledger.anchor(mod.ID) if getattr(mod, "ANCHORED", False) else None
            if a:   # nothing to subtract, so the entered balance is shown as entered
                entry["balance"] = round(a["usd"], 6)
                entry["note"] += (f" Balance = {money(a['usd'])} as entered {a['ts'][:10]}: it does not count "
                                  "down while spend cannot be read.")
        except Exception as e:     # noqa: BLE001 (one provider never takes the others down)
            failed.append(mod.ID)
            reason = str(e) if isinstance(e, ProviderError) else type(e).__name__
            old = ((previous or {}).get("providers") or {}).get(mod.ID) or {}
            if isinstance(old, dict) and old.get("status") in ("ok", "stale"):
                entry = dict(old, status="stale", error=reason)   # keeps its own `checked`
            else:
                entry = dict(EMPTY, status="error", checked=stamp, error=reason)
        out[mod.ID] = dict(entry, name=mod.NAME)
    live = [p for p in out.values() if p["status"] in ("ok", "stale")]
    d30 = window(now)[2]
    mtd_total = sum(p.get("mtd") or 0 for p in live)
    payload = {
        "generated": stamp,
        "month": now.astimezone(timezone.utc).strftime("%Y-%m"),
        "days": [(d30 + timedelta(days=i)).isoformat() for i in range(30)],
        "mtd_total": round(mtd_total, 6),
        "d30_total": round(sum(p.get("d30") or 0 for p in live), 6),
        "providers": out,
    }
    return payload, failed


def set_anchor(pid, usd, now, store, ledger, http=None, custom=()):
    """Record the credit balance the user sees on the provider's billing page right now. Today's spend so
    far is read from the provider when it can be, so it is not subtracted twice. Returns (anchor, warning)."""
    mod = by_id(pid, custom)
    if mod is None or not getattr(mod, "ANCHORED", False):
        raise ValueError(f"{pid} reports its own balance")
    day_spend, warning = 0.0, ""
    key = store.get(pid)
    if key and keycheck.foreign(pid, key):
        key, warning = None, "The saved key looks like another provider's, so today's spend was not read."
    if key:
        ctx = Context(now=now, key=key, http=http or Http(), ledger=ledger, pid=pid)
        try:
            day_spend = float(mod.spend_today(ctx))
        except (ProviderError, NotConnected) as e:
            warning = f"Could not read today's spend ({e}); assuming nothing spent yet today."
    rec = ledger.set_anchor(pid, usd, now, day_spend)
    return rec, warning
