"""Anthropic API: the organisation cost report (an Admin key). API-key spend only, never a Claude
subscription. No balance API, so the balance is the user's entered credit minus the cost since."""

import urllib.parse
from datetime import timezone

from . import NotConnected, ProviderError, anchored_note, fetch_start, utc_day
from .http import refusal

ID, NAME = "anthropic", "Anthropic API"
KEY_LABEL = "Admin key"
ANCHORED = True
URL = "https://api.anthropic.com/v1/organizations/cost_report"
PAGES = 20


def costs(ctx, start):
    """[(day, model or line, usd)] from start (a date) to now, every page. Amounts come in cents."""
    headers = {"x-api-key": ctx.key, "anthropic-version": "2023-06-01"}
    rows, page = [], None
    for _ in range(PAGES):
        q = [("starting_at", start.isoformat() + "T00:00:00Z"), ("bucket_width", "1d"), ("limit", "31"),
             ("group_by[]", "description")]
        if page:
            q.append(("page", page))
        status, body = ctx.http.get(URL + "?" + urllib.parse.urlencode(q), headers)
        if status in (401, 403, 404):
            if ctx.key.startswith("sk-ant-api"):
                why = "This is a workspace API key; the cost report needs an Admin key (sk-ant-admin…)."
            else:
                why = ("The Admin API is not available to individual accounts: convert yours to a team in the "
                       "Claude Console (Settings, Organization), then create a key on the Admin keys page.")
            raise NotConnected(f"Anthropic refused the cost report: {refusal(status, body)}. {why}")
        if status != 200 or not isinstance(body, dict):
            raise ProviderError(refusal(status, body))
        for bucket in body.get("data") or []:
            if not isinstance(bucket, dict):
                continue
            day = utc_day(bucket.get("starting_at"))
            for r in bucket.get("results") or []:
                if day and isinstance(r, dict) and r.get("amount") is not None:
                    try:
                        cents = float(r["amount"])
                    except (TypeError, ValueError):
                        continue
                    label = r.get("model") or r.get("description") or r.get("cost_type") or "other"
                    rows.append((day, str(label), cents / 100.0))
        page = body.get("next_page")
        if not (body.get("has_more") and page):
            return rows
    raise ProviderError("cost report kept paging")


NOTE = "API-key spend only; a Claude subscription is not in this figure."


def run(ctx):
    rows = costs(ctx, fetch_start(ctx))
    balance, a = ctx.ledger.anchored_balance(ID, rows)
    if a is None:
        return {"rows": rows, "balance": None,
                "note": NOTE + " Anthropic has no balance API: enter the credit balance from your billing page "
                               "in Settings and it is tracked from there."}
    return {"rows": rows, "balance": balance, "note": NOTE + " " + anchored_note(a, "cost report")}


def spend_today(ctx):
    today = ctx.now.astimezone(timezone.utc).date()
    return sum(usd for day, _, usd in costs(ctx, today) if day == today.isoformat())
