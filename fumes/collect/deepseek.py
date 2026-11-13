"""DeepSeek: the balance API. DeepSeek reports no usage history, so spend is estimated from the
falls in the balance between Fumes's own readings (a rise is a top-up and ignored)."""

from . import ProviderError

ID, NAME = "deepseek", "DeepSeek"
KEY_LABEL = "API key"
ANCHORED = False
URL = "https://api.deepseek.com/user/balance"


def run(ctx):
    body = ctx.http.get_json(URL, {"Authorization": "Bearer " + ctx.key})
    infos = body.get("balance_infos")
    usd = next((i for i in infos if isinstance(i, dict) and i.get("currency") == "USD"), None) \
        if isinstance(infos, list) else None
    if usd is None:
        raise ProviderError("balance answer has no USD entry")
    try:
        balance = float(usd.get("total_balance"))
    except (TypeError, ValueError):
        raise ProviderError("balance answer has no total") from None
    ctx.ledger.record_balance(ID, balance, ctx.now)
    first = ctx.ledger.first_sample(ID)
    return {"rows": ctx.ledger.spend_from_balance(ID), "balance": balance,
            "note": "DeepSeek reports no usage history: spend is estimated from the falls in the balance "
                    f"between checks (since {first[:10] if first else 'today'}). Top-ups are not counted."}
