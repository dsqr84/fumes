"""OpenAI: the organisation costs API (an Admin key). OpenAI has no balance API, so the balance is the
credit the user entered (an anchor) minus the costs reported since."""

import urllib.parse
from datetime import timezone

from . import ProviderError, anchored_note, fetch_start, midnight, utc_day

ID, NAME = "openai", "OpenAI"
KEY_LABEL = "Admin key"
ANCHORED = True
URL = "https://api.openai.com/v1/organization/costs"
PAGES = 20


def costs(ctx, start_day):
    """[(day, line item, usd)] from start_day (a date) to now, every page."""
    rows, page = [], None
    for _ in range(PAGES):
        q = {"start_time": midnight(start_day), "bucket_width": "1d", "limit": 180, "group_by": "line_item"}
        if page:
            q["page"] = page
        body = ctx.http.get_json(URL + "?" + urllib.parse.urlencode(q), {"Authorization": "Bearer " + ctx.key})
        for bucket in body.get("data") or []:
            if not isinstance(bucket, dict):
                continue
            day = utc_day(bucket.get("start_time"))
            for r in bucket.get("results") or []:
                amount = r.get("amount") if isinstance(r, dict) else None
                if day and isinstance(amount, dict) and amount.get("value") is not None:
                    try:
                        rows.append((day, str(r.get("line_item") or "other"), float(amount["value"])))
                    except (TypeError, ValueError):
                        continue
        page = body.get("next_page")
        if not (body.get("has_more") and page):
            return rows
    raise ProviderError("costs API kept paging")


def run(ctx):
    rows = costs(ctx, fetch_start(ctx))
    balance, a = ctx.ledger.anchored_balance(ID, rows)
    if a is None:
        return {"rows": rows, "balance": None,
                "note": "OpenAI has no balance API. Enter the credit balance from your billing page in "
                        "Settings and it is tracked from there."}
    return {"rows": rows, "balance": balance, "note": anchored_note(a, "costs API")}


def spend_today(ctx):
    today = ctx.now.astimezone(timezone.utc).date()
    return sum(usd for day, _, usd in costs(ctx, today) if day == today.isoformat())
