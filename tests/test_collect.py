from datetime import datetime, timezone

import pytest

from fakes import FakeHttp
from fumes import collect as col
from fumes import feed
from fumes.collect import ProviderError
from fumes.collect.ledger import Ledger
from fumes.keystore import MemoryStore

NOW = datetime(2026, 11, 13, 15, 0, 0, tzinfo=timezone.utc)
DS = "https://api.deepseek.com/user/balance"
OA = "https://api.openai.com/v1/organization/costs"
FB = "https://api.fal.ai/v1/account/billing"
FU = "https://api.fal.ai/v1/models/usage"
AN = "https://api.anthropic.com/v1/organizations/cost_report"
DAY = 1_794_528_000


def routes():
    return {DS: (200, {"balance_infos": [{"currency": "USD", "total_balance": "5"}]}),
            OA: (200, {"data": [{"start_time": DAY, "results": [{"line_item": "gpt", "amount": {"value": 2}}]}]}),
            FB: (200, {"credits": {"current_balance": 3}}),
            FU: (200, {"time_series": [{"bucket": "2026-11-12", "results": [{"endpoint_id": "e", "cost": 1}]}]}),
            AN: (200, {"data": [{"starting_at": "2026-11-13T00:00:00Z", "results": [{"amount": 300, "model": "m"}]}]})}


ALL = {"deepseek": "k1", "openai": "k2", "fal": "k3", "anthropic": "sk-ant-admin-k4"}


def led(tmp_path):
    return Ledger(tmp_path / "l.json")


def test_all_providers_payload_parses(tmp_path):
    payload, failed = col.collect(NOW, MemoryStore(ALL), led(tmp_path), FakeHttp(routes()))
    assert failed == []
    f = feed.parse_feed(payload)
    assert [p.id for p in f.providers] == ["deepseek", "openai", "fal", "anthropic"]
    assert payload["month"] == "2026-11" and len(payload["days"]) == 30 and payload["days"][-1] == "2026-11-13"
    assert payload["providers"]["openai"]["mtd"] == 2.0 and payload["providers"]["fal"]["balance"] == 3
    assert payload["mtd_total"] == pytest.approx(2 + 1 + 3)
    assert all(p["status"] == "ok" for p in payload["providers"].values())


def test_no_key_not_connected(tmp_path):
    http = FakeHttp(routes())
    payload, failed = col.collect(NOW, MemoryStore(), led(tmp_path), http)
    assert failed == [] and http.calls == []
    p = payload["providers"]["deepseek"]
    assert p["status"] == "not_connected" and p["balance"] is None and "No key" in p["note"]
    feed.parse_feed(payload)


def test_anchor_without_key_shows_entered_balance(tmp_path):
    l = led(tmp_path)
    l.set_anchor("openai", 42.5, datetime(2026, 11, 10, tzinfo=timezone.utc))
    l.set_anchor("deepseek", 99, NOW)     # not anchored provider: ignored
    payload, _ = col.collect(NOW, MemoryStore(), l, FakeHttp())
    p = payload["providers"]["openai"]
    assert p["status"] == "not_connected" and p["balance"] == 42.5 and "as entered 2026-11-10" in p["note"]
    assert payload["providers"]["deepseek"]["balance"] is None
    assert payload["providers"]["anthropic"]["balance"] is None


def test_raising_provider_keeps_previous_stale(tmp_path):
    http = FakeHttp(routes())
    store = MemoryStore(ALL)
    first, _ = col.collect(NOW, store, led(tmp_path), http)
    http.add(FB, (500, {"error": {"type": "boom", "message": "LEAK"}}))
    later = datetime(2026, 11, 13, 16, tzinfo=timezone.utc)
    second, failed = col.collect(later, store, led(tmp_path), http, previous=first)
    assert failed == ["fal"]
    p = second["providers"]["fal"]
    assert p["status"] == "stale" and p["error"] == "HTTP 500 (boom)" and p["balance"] == 3
    assert p["checked"] == first["providers"]["fal"]["checked"] and "LEAK" not in str(second)
    assert second["providers"]["openai"]["status"] == "ok"
    third, _ = col.collect(later, store, led(tmp_path), http, previous=second)    # stale stays stale
    assert third["providers"]["fal"]["status"] == "stale"
    feed.parse_feed(second)


def test_raising_without_previous_is_error(tmp_path):
    http = FakeHttp(routes())
    http.add(DS, ProviderError("api.deepseek.com: TimeoutError"))
    payload, failed = col.collect(NOW, MemoryStore(ALL), led(tmp_path), http)
    p = payload["providers"]["deepseek"]
    assert failed == ["deepseek"] and p["status"] == "error" and p["error"] == "api.deepseek.com: TimeoutError"
    http.add(DS, ValueError("secret detail k1"))
    payload, _ = col.collect(NOW, MemoryStore(ALL), led(tmp_path), http)
    assert payload["providers"]["deepseek"]["error"] == "ValueError"      # unexpected: type name only
    feed.parse_feed(payload)


def test_enabled_limits_providers(tmp_path):
    http = FakeHttp(routes())
    payload, _ = col.collect(NOW, MemoryStore(ALL), led(tmp_path), http, enabled=["fal"])
    assert list(payload["providers"]) == ["fal"]
    assert all(u.startswith("https://api.fal.ai") for u, _ in http.calls)


def test_one_failure_does_not_stop_others(tmp_path):
    http = FakeHttp(routes())
    http.add(OA, (401, {}))
    payload, failed = col.collect(NOW, MemoryStore(ALL), led(tmp_path), http)
    assert failed == ["openai"] and payload["providers"]["anthropic"]["status"] == "ok"


def test_set_anchor_records_day_spend(tmp_path):
    l = led(tmp_path)
    http = FakeHttp(routes())
    rec, warn = col.set_anchor("openai", 25, NOW, MemoryStore(ALL), l, http)
    assert rec["usd"] == 25 and rec["day_spend"] == 2.0 and warn == ""
    assert l.anchor("openai")["day_spend"] == 2.0


def test_set_anchor_warns_when_spend_unreadable(tmp_path):
    l = led(tmp_path)
    http = FakeHttp(routes())
    http.add(OA, (500, {"error": {"type": "x", "message": "LEAK"}}))
    rec, warn = col.set_anchor("openai", 25, NOW, MemoryStore(ALL), l, http)
    assert rec["day_spend"] == 0.0 and "Could not read today's spend" in warn and "LEAK" not in warn
    rec, warn = col.set_anchor("anthropic", 5, NOW, MemoryStore(), l, http)      # no key: no read, no warning
    assert rec["day_spend"] == 0.0 and warn == ""


def test_set_anchor_rejects_unanchored_and_bad_amount(tmp_path):
    with pytest.raises(ValueError):
        col.set_anchor("deepseek", 5, NOW, MemoryStore(), led(tmp_path))
    with pytest.raises(ValueError):
        col.set_anchor("nope", 5, NOW, MemoryStore(), led(tmp_path))
    with pytest.raises(ValueError):
        col.set_anchor("openai", -1, NOW, MemoryStore(), led(tmp_path))


def test_helpers():
    assert col.utc_day(DAY) == "2026-11-13" and col.utc_day("2026-11-13T23:30:00-05:00") == "2026-11-14"
    assert col.utc_day("2026-11-13") == "2026-11-13" and col.utc_day("garbage") is None
    assert col.utc_day(True) is None and col.utc_day(None) is None
    assert col.money(None) == "n/a" and col.money(0.001) == "<$0.01" and col.money(1234.5) == "$1,234.50"
    assert col.money(0) == "$0.00"
    start, ms, d30 = col.window(NOW)
    assert ms.isoformat() == "2026-11-01" and d30.isoformat() == "2026-10-15" and start == d30


def test_summarise_breakdown_other_and_window():
    rows = [("2026-11-13", f"m{i}", 10 - i) for i in range(10)] + [("2026-09-01", "old", 99), ("2026-10-20", "m0", 1)]
    s = col.summarise(rows, NOW)
    assert len(s["breakdown"]) == col.BREAKDOWN_ROWS + 1 and s["breakdown"][-1]["label"] == "Other"
    assert s["breakdown"][0] == {"label": "m0", "usd": 11.0}
    assert s["mtd"] == 55.0 and s["d30"] == 56.0 and list(s["daily"]) == ["2026-10-20", "2026-11-13"]
