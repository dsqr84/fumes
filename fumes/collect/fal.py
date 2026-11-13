"""fal.ai: the billing API (balance) and the usage API (cost per endpoint per day). A key on fal's BILLING
preset (billing:usage:read, auth:keys:read, account:users:read): read-only, cannot call models."""

import urllib.parse

from . import ProviderError, utc_day, window

ID, NAME = "fal", "fal.ai"
KEY_LABEL = "BILLING key"
ANCHORED = False
BILLING = "https://api.fal.ai/v1/account/billing?expand=credits"
USAGE = "https://api.fal.ai/v1/models/usage"
PAGES = 20


def run(ctx):
    headers = {"Authorization": "Key " + ctx.key}
    credits = ctx.http.get_json(BILLING, headers).get("credits")
    try:
        balance = float(credits["current_balance"])
    except (TypeError, KeyError, ValueError):
        raise ProviderError("billing answer has no balance") from None
    rows, cursor = [], None
    for _ in range(PAGES):
        q = {"start": window(ctx.now)[0].isoformat(), "timeframe": "day", "expand": "time_series"}
        if cursor:
            q["cursor"] = cursor
        body = ctx.http.get_json(USAGE + "?" + urllib.parse.urlencode(q), headers)
        for bucket in body.get("time_series") or []:
            if not isinstance(bucket, dict):
                continue
            day = utc_day(bucket.get("bucket"))
            for r in bucket.get("results") or []:
                if not isinstance(r, dict):
                    continue
                cost = r.get("cost_total", r.get("cost"))
                if day and cost is not None:
                    try:
                        rows.append((day, str(r.get("endpoint_id") or "other"), float(cost)))
                    except (TypeError, ValueError):
                        continue
        cursor = body.get("next_cursor")
        if not (body.get("has_more") and cursor):
            return {"rows": rows, "balance": balance, "note": ""}
    raise ProviderError("usage API kept paging")
