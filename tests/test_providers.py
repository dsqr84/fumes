from datetime import datetime, timezone
from urllib.parse import parse_qs, urlsplit

import pytest

from fakes import FakeHttp
from fumes import collect as col
from fumes.collect import NotConnected, ProviderError, anthropic, deepseek, fal, openai
from fumes.collect.ledger import Ledger

NOW = datetime(2026, 11, 13, 15, 0, 0, tzinfo=timezone.utc)
KEY = "sk-SECRETKEYVALUE123"
LEAK = "LEAKED-MESSAGE-TEXT"


def ctx(pid, http, tmp_path, key=KEY, ledger=None):
    return col.Context(now=NOW, key=key, http=http, ledger=ledger or Ledger(tmp_path / "l.json"), pid=pid)


def assert_clean(text):
    assert KEY not in text and LEAK not in text


# ------------------------------------------------------------- deepseek ----
DS = "https://api.deepseek.com/user/balance"


def test_deepseek_happy(tmp_path):
    http = FakeHttp({DS: (200, {"balance_infos": [{"currency": "CNY", "total_balance": "1"},
                                                  {"currency": "USD", "total_balance": "12.34"}]})})
    c = ctx("deepseek", http, tmp_path)
    got = deepseek.run(c)
    assert got["balance"] == 12.34 and got["rows"] == [] and "estimated" in got["note"]
    assert http.calls[0][1]["Authorization"] == "Bearer " + KEY
    c.now = datetime(2026, 11, 13, 18, 0, tzinfo=timezone.utc)
    http.add(DS, (200, {"balance_infos": [{"currency": "USD", "total_balance": "11.84"}]}))
    got = deepseek.run(c)
    assert got["rows"] == [("2026-11-13", "spend (from balance changes)", pytest.approx(0.5))]


@pytest.mark.parametrize("body", [{}, {"balance_infos": []}, {"balance_infos": [{"currency": "CNY", "total_balance": "1"}]},
                                  {"balance_infos": "x"}, {"balance_infos": [{"currency": "USD"}]},
                                  {"balance_infos": [{"currency": "USD", "total_balance": "abc"}]}])
def test_deepseek_missing_balance(tmp_path, body):
    with pytest.raises(ProviderError):
        deepseek.run(ctx("deepseek", FakeHttp({DS: (200, body)}), tmp_path))


def test_deepseek_error_clean(tmp_path):
    http = FakeHttp({DS: (401, {"error": {"type": "auth", "message": LEAK + KEY}})})
    with pytest.raises(ProviderError) as e:
        deepseek.run(ctx("deepseek", http, tmp_path))
    assert_clean(str(e.value))


# --------------------------------------------------------------- openai ----
OA = "https://api.openai.com/v1/organization/costs"


def oa_page(day_ts, items, more=False, nxt=None):
    return (200, {"data": [{"start_time": day_ts, "results": [{"line_item": n, "amount": {"value": v}}
                                                              for n, v in items]}],
                  "has_more": more, "next_page": nxt})


DAY = 1_794_528_000   # 2026-11-13 00:00 UTC


def test_openai_happy_with_anchor(tmp_path):
    led = Ledger(tmp_path / "l.json")
    led.set_anchor("openai", 20, datetime(2026, 11, 12, 9, tzinfo=timezone.utc), day_spend=0.5)
    http = FakeHttp({OA: oa_page(DAY - 86400, [("gpt", 1.0), ("img", 0.5)])})
    got = openai.run(ctx("openai", http, tmp_path, ledger=led))
    assert got["rows"] == [("2026-11-12", "gpt", 1.0), ("2026-11-12", "img", 0.5)]
    assert got["balance"] == pytest.approx(20 - 1.0)          # 1.5 spent that day, 0.5 already in the anchor
    assert "entered 2026-11-12" in got["note"]
    assert http.calls[0][1]["Authorization"] == "Bearer " + KEY
    q = parse_qs(urlsplit(http.calls[0][0]).query)
    assert q["bucket_width"] == ["1d"] and q["group_by"] == ["line_item"]
    assert int(q["start_time"][0]) <= DAY - 86400      # fetch reaches back to the anchor's day


def test_openai_no_anchor_note(tmp_path):
    got = openai.run(ctx("openai", FakeHttp({OA: oa_page(DAY, [("a", 1)])}), tmp_path))
    assert got["balance"] is None and "Enter the credit balance" in got["note"]


def test_openai_paging(tmp_path):
    http = FakeHttp({OA: [oa_page(DAY, [("a", 1)], True, "p2"), oa_page(DAY, [("b", 2)])]})
    got = openai.run(ctx("openai", http, tmp_path))
    assert [r[1] for r in got["rows"]] == ["a", "b"]
    assert "page=p2" in http.calls[1][0] and "page=" not in http.calls[0][0]


def test_openai_page_guard(tmp_path):
    http = FakeHttp({OA: oa_page(DAY, [("a", 1)], True, "again")})
    with pytest.raises(ProviderError, match="kept paging"):
        openai.run(ctx("openai", http, tmp_path))
    assert len(http.calls) == openai.PAGES


def test_openai_malformed_buckets_skipped(tmp_path):
    body = {"data": ["x", {"start_time": "garbage", "results": [{"line_item": "a", "amount": {"value": 1}}]},
                     {"start_time": DAY, "results": ["y", {"amount": "z"}, {"amount": {"value": "abc"}},
                                                     {"line_item": "ok", "amount": {"value": 2}},
                                                     {"line_item": "none", "amount": {}}]}]}
    got = openai.run(ctx("openai", FakeHttp({OA: (200, body)}), tmp_path))
    assert got["rows"] == [("2026-11-13", "ok", 2.0)]


def test_openai_spend_today(tmp_path):
    http = FakeHttp({OA: oa_page(DAY, [("a", 1.25), ("b", 0.25)])})
    assert openai.spend_today(ctx("openai", http, tmp_path)) == 1.5


def test_openai_error_clean(tmp_path):
    http = FakeHttp({OA: (403, {"error": {"code": "forbidden", "message": LEAK + KEY}})})
    with pytest.raises(ProviderError) as e:
        openai.run(ctx("openai", http, tmp_path))
    assert str(e.value) == "HTTP 403 (forbidden)"


# ------------------------------------------------------------------ fal ----
FB = "https://api.fal.ai/v1/account/billing"
FU = "https://api.fal.ai/v1/models/usage"


def fal_http(usage):
    return FakeHttp({FB: (200, {"credits": {"current_balance": 7.5}}), FU: usage})


def test_fal_happy(tmp_path):
    usage = (200, {"time_series": [{"bucket": "2026-11-12T00:00:00Z",
                                    "results": [{"endpoint_id": "fal-ai/flux", "cost_total": 1.0},
                                                {"endpoint_id": "x", "cost": 0.5}]}], "has_more": False})
    http = fal_http(usage)
    got = fal.run(ctx("fal", http, tmp_path))
    assert got["balance"] == 7.5 and got["rows"] == [("2026-11-12", "fal-ai/flux", 1.0), ("2026-11-12", "x", 0.5)]
    assert all(h["Authorization"] == "Key " + KEY for _, h in http.calls)


def test_fal_paging_and_guard(tmp_path):
    p1 = (200, {"time_series": [{"bucket": "2026-11-12", "results": [{"endpoint_id": "a", "cost": 1}]}],
                "has_more": True, "next_cursor": "c2"})
    p2 = (200, {"time_series": [], "has_more": False})
    http = fal_http([p1, p2])
    fal.run(ctx("fal", http, tmp_path))
    assert "cursor=c2" in http.calls[-1][0]
    with pytest.raises(ProviderError, match="kept paging"):
        fal.run(ctx("fal", fal_http(p1), tmp_path))


def test_fal_malformed_and_missing_balance(tmp_path):
    usage = (200, {"time_series": ["x", {"bucket": "bad", "results": [{"cost": 1}]},
                                   {"bucket": "2026-11-12", "results": ["y", {"cost": "abc"}, {"endpoint_id": "n"},
                                                                        {"endpoint_id": "ok", "cost": 2}]}]})
    assert fal.run(ctx("fal", fal_http(usage), tmp_path))["rows"] == [("2026-11-12", "ok", 2.0)]
    for credits in ({}, {"credits": None}, {"credits": {"current_balance": "x"}}, {"credits": {}}):
        http = FakeHttp({FB: (200, credits), FU: (200, {})})
        with pytest.raises(ProviderError):
            fal.run(ctx("fal", http, tmp_path))


def test_fal_error_clean(tmp_path):
    http = FakeHttp({FB: (401, {"error": {"type": "unauthorized", "message": LEAK + KEY}})})
    with pytest.raises(ProviderError) as e:
        fal.run(ctx("fal", http, tmp_path))
    assert_clean(str(e.value))


# ------------------------------------------------------------ anthropic ----
AN = "https://api.anthropic.com/v1/organizations/cost_report"


def an_page(items, more=False, nxt=None):
    return (200, {"data": [{"starting_at": "2026-11-12T00:00:00Z", "results": items}],
                  "has_more": more, "next_page": nxt})


def test_anthropic_happy_with_anchor(tmp_path):
    led = Ledger(tmp_path / "l.json")
    led.set_anchor("anthropic", 30, datetime(2026, 11, 12, 9, tzinfo=timezone.utc))
    http = FakeHttp({AN: an_page([{"amount": "250", "model": "claude-x"}, {"amount": 50, "description": "web"}])})
    got = anthropic.run(ctx("anthropic", http, tmp_path, ledger=led))
    assert got["rows"] == [("2026-11-12", "claude-x", 2.5), ("2026-11-12", "web", 0.5)]
    assert got["balance"] == pytest.approx(27.0) and "subscription" in got["note"]
    h = http.calls[0][1]
    assert h["x-api-key"] == KEY and h["anthropic-version"] and "Authorization" not in h


def test_anthropic_no_anchor(tmp_path):
    got = anthropic.run(ctx("anthropic", FakeHttp({AN: an_page([{"amount": 100}])}), tmp_path))
    assert got["balance"] is None and got["rows"] == [("2026-11-12", "other", 1.0)]


def test_anthropic_paging_guard_malformed(tmp_path):
    http = FakeHttp({AN: [an_page([{"amount": 100, "model": "a"}], True, "n2"), an_page([{"amount": 200, "model": "b"}])]})
    assert len(anthropic.run(ctx("anthropic", http, tmp_path))["rows"]) == 2
    assert "page=n2" in http.calls[1][0]
    with pytest.raises(ProviderError, match="kept paging"):
        anthropic.run(ctx("anthropic", FakeHttp({AN: an_page([], True, "x")}), tmp_path))
    body = {"data": ["x", {"starting_at": "bad", "results": [{"amount": 1}]},
                     {"starting_at": "2026-11-12", "results": ["y", {"amount": "z"}, {"model": "m"}, {"amount": 100, "model": "ok"}]}]}
    assert anthropic.run(ctx("anthropic", FakeHttp({AN: (200, body)}), tmp_path))["rows"] == [("2026-11-12", "ok", 1.0)]


@pytest.mark.parametrize("status", [401, 403, 404])
def test_anthropic_auth_refusals_not_connected(tmp_path, status):
    body = {"error": {"type": "authentication_error", "message": LEAK + KEY}}
    with pytest.raises(NotConnected) as e:
        anthropic.run(ctx("anthropic", FakeHttp({AN: (status, body)}), tmp_path, key="sk-ant-api03-abcdef"))
    msg = str(e.value)
    assert "Admin key" in msg and "workspace API key" in msg and f"HTTP {status}" in msg
    assert LEAK not in msg and "abcdef" not in msg
    with pytest.raises(NotConnected) as e:
        anthropic.run(ctx("anthropic", FakeHttp({AN: (status, body)}), tmp_path, key="sk-ant-admin01-zzz"))
    msg = str(e.value)
    assert "workspace API key" not in msg and "individual accounts" in msg and "convert yours to a team" in msg


def test_anthropic_other_error_clean(tmp_path):
    with pytest.raises(ProviderError) as e:
        anthropic.run(ctx("anthropic", FakeHttp({AN: (500, {"error": {"type": "api_error", "message": LEAK + KEY}})}), tmp_path))
    assert str(e.value) == "HTTP 500 (api_error)"


def test_anthropic_spend_today(tmp_path):
    http = FakeHttp({AN: (200, {"data": [{"starting_at": "2026-11-13T00:00:00Z", "results": [{"amount": 150}]},
                                         {"starting_at": "2026-11-12T00:00:00Z", "results": [{"amount": 900}]}]})})
    assert anthropic.spend_today(ctx("anthropic", http, tmp_path)) == 1.5


def test_provider_metadata():
    assert [m.ID for m in col.providers()] == ["deepseek", "openai", "fal", "anthropic"]
    assert {m.ID for m in col.providers() if m.ANCHORED} == {"openai", "anthropic"}
    assert all(m.KEY_LABEL and m.NAME for m in col.providers())
