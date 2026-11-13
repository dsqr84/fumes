import copy
import json
from datetime import datetime, timedelta, timezone

import pytest

from fumes import feed


def test_good_fixture(raw_feed):
    f = feed.parse_feed(raw_feed)
    assert [p.id for p in f.providers] == ["deepseek", "openai", "fal", "anthropic", "acme"]
    ds = f.providers[0]
    assert ds.name == "DeepSeek" and ds.balance == 4.15 and ds.mtd == 6.55
    assert f.providers[1].balance is None and f.providers[1].mtd == 5.44
    assert f.providers[3].status == "not_connected" and f.providers[3].mtd is None
    assert f.generated == datetime(2026, 11, 12, 11, 30, 56, tzinfo=timezone.utc)


@pytest.mark.parametrize("bad", [None, [], "x", 3, {}, {"generated": "2026-11-12T11:30:56Z"},
                                 {"generated": "2026-11-12T11:30:56Z", "providers": []},
                                 {"generated": "nope", "providers": {}},
                                 {"providers": {}},
                                 {"generated": 5, "providers": {}},
                                 {"generated": "2026-11-12T11:30:56Z", "providers": {"a": 1}}])
def test_malformed_rejected(bad):
    with pytest.raises(feed.FeedError):
        feed.parse_feed(bad)


def test_unknown_ids_after_known_in_feed_order(raw_feed):
    raw = copy.deepcopy(raw_feed)
    raw["providers"] = {"zzz": {"name": "Z"}, "acme": raw["providers"]["acme"],
                        "aaa": {"name": "A", "balance": 1}, "deepseek": raw["providers"]["deepseek"]}
    f = feed.parse_feed(raw)
    assert [p.id for p in f.providers] == ["deepseek", "acme", "zzz", "aaa"]


def test_extra_keys_and_bad_field_types_tolerated(raw_feed):
    raw = copy.deepcopy(raw_feed)
    raw["extra"] = 1
    raw["providers"]["fal"]["balance"] = "2.86"
    raw["providers"]["fal"]["mtd"] = True
    raw["providers"]["fal"]["status"] = "weird"
    f = feed.parse_feed(raw)
    fal = next(p for p in f.providers if p.id == "fal")
    assert fal.balance is None and fal.mtd is None and fal.status == "error"


def test_select_order_and_filter(raw_feed):
    f = feed.parse_feed(raw_feed)
    assert [p.id for p in feed.select(f, ["acme", "nope", "fal"])] == ["acme", "fal"]
    assert len(feed.select(f, [])) == 4        # anthropic: not connected, no figure: hidden


def test_staleness_boundary(raw_feed):
    f = feed.parse_feed(raw_feed)
    t0 = f.generated
    assert not feed.is_stale(f, 150, t0 + timedelta(minutes=150))
    assert feed.is_stale(f, 150, t0 + timedelta(minutes=150, seconds=1))
    assert feed.age_seconds(f, t0 + timedelta(hours=3)) == 3 * 3600


def test_total_credit(raw_feed):
    f = feed.parse_feed(raw_feed)
    assert feed.total_credit(f.providers) == pytest.approx(4.15 + 2.86 + 7.254112)
    assert feed.total_credit([]) is None


def test_fetch_ok(server, raw_feed):
    server.responses["/f"] = (200, json.dumps(raw_feed).encode(), 0)
    f, raw = feed.fetch(server.base + "/f")
    assert len(f.providers) == 5 and raw["month"] == "2026-11"


def test_fetch_http_error(server):
    with pytest.raises(feed.FeedError):
        feed.fetch(server.base + "/missing")


def test_fetch_bad_json_and_shape(server):
    server.responses["/j"] = (200, b"{nope", 0)
    server.responses["/s"] = (200, b"[1,2]", 0)
    for p in ("/j", "/s"):
        with pytest.raises(feed.FeedError):
            feed.fetch(server.base + p)


def test_fetch_timeout(server):
    server.responses["/slow"] = (200, b"{}", 1.5)
    with pytest.raises(feed.FeedError):
        feed.fetch(server.base + "/slow", timeout=0.3)


def test_fetch_connection_refused():
    with pytest.raises(feed.FeedError):
        feed.fetch("http://127.0.0.1:1/x", timeout=1)


@pytest.mark.parametrize("bad", ["../x", "..", "a/b", "a\\b", "\\\\host\\share\\x", "C:x", "A", "", "a.b", "x" * 33, " a"])
def test_a_provider_id_that_could_be_a_path_is_dropped(raw_feed, bad):
    raw_feed["providers"][bad] = dict(raw_feed["providers"]["fal"])
    ids = [p.id for p in feed.parse_feed(raw_feed).providers]
    assert bad not in ids and "fal" in ids
    assert not feed.valid_id(bad)


def test_peek_data_from_fixture(raw_feed):
    f = feed.parse_feed(raw_feed)
    assert len(f.days) == 30 and f.days[0] == "2026-10-14" and f.days[-1] == "2026-11-12"
    oa = next(p for p in f.providers if p.id == "openai")
    assert oa.daily["2026-11-06"] == 3.64 and len(oa.daily) == 5
    assert oa.breakdown[0] == ("gpt-image-2.6-flare image, output", 2.842322) and len(oa.breakdown) == 9
    an = next(p for p in f.providers if p.id == "anthropic")
    assert an.daily == {} and an.breakdown == []


def test_peek_data_absent_or_wrong_type_is_empty(raw_feed):
    raw = copy.deepcopy(raw_feed)
    del raw["days"]
    for p in raw["providers"].values():
        p.pop("daily", None)
        p.pop("breakdown", None)
    f = feed.parse_feed(raw)
    assert f.days == [] and all(p.daily == {} and p.breakdown == [] for p in f.providers)
    for bad in ("x", 5, None, {"a": 1}):
        raw["days"] = bad
        raw["providers"]["fal"]["breakdown"] = bad
        raw["providers"]["fal"]["daily"] = bad if not isinstance(bad, dict) else [1]
        f = feed.parse_feed(raw)
        assert f.days == [] and f.providers[2].breakdown == [] and f.providers[2].daily == {}


def test_days_entries_must_be_dates(raw_feed):
    raw_feed["days"] = ["2026-11-01", 5, None, "2026-1-2", "2026-11-01x", "nope", "2026-13-40", "2026-11-02"]
    assert feed.parse_feed(raw_feed).days == ["2026-11-01", "2026-11-02"]


def test_daily_bad_entries_skipped(raw_feed):
    raw_feed["providers"]["fal"]["daily"] = {
        "2026-11-01": 1.5, "2026-11-02": -0.01, "2026-11-03": True, "2026-11-04": "2",
        "2026-11-05": None, "bad": 1, "2026-13-01": 1, "2026-11-06": 0, 7: 1, "2026-11-07": float("inf")}
    assert feed.parse_feed(raw_feed).providers[2].daily == {"2026-11-01": 1.5, "2026-11-06": 0.0}


def test_breakdown_bad_rows_skipped_and_capped(raw_feed):
    rows = [{"label": "ok", "usd": 1}, {"label": 5, "usd": 1}, {"usd": 1}, {"label": "neg", "usd": -1},
            {"label": "bool", "usd": True}, {"label": "str", "usd": "1"}, "x", None, {"label": "nul", "usd": None},
            {"label": "L" * 500, "usd": 2}]
    raw_feed["providers"]["fal"]["breakdown"] = rows
    bd = feed.parse_feed(raw_feed).providers[2].breakdown
    assert bd == [("ok", 1.0), ("L" * 200, 2.0)]
    raw_feed["providers"]["fal"]["breakdown"] = [{"label": f"m{i}", "usd": 1} for i in range(80)]
    bd = feed.parse_feed(raw_feed).providers[2].breakdown
    assert len(bd) == 50 and bd[0][0] == "m0" and bd[-1][0] == "m49"


def test_free_text_from_the_feed_is_capped(raw_feed):
    raw_feed["providers"]["fal"].update(name="n" * 500, note="x" * 9000, error="e" * 9000)
    p = next(p for p in feed.parse_feed(raw_feed).providers if p.id == "fal")
    assert (len(p.name), len(p.note), len(p.error)) == (80, 2000, 2000)


@pytest.mark.parametrize("bad", ["0001-01-01T00:00:00+05:00", "9999-12-31T23:59:59-05:00", "1969-12-31T00:00:00Z",
                                 "1999-12-31T23:59:59Z", "2100-01-01T00:00:00Z", "0001-01-01T00:00:00Z"])
def test_a_time_outside_2000_to_2100_is_no_time(bad, raw_feed):
    assert feed.parse_time(bad) is None
    raw_feed["generated"] = bad
    with pytest.raises(feed.FeedError):
        feed.parse_feed(raw_feed)


def test_times_at_the_edges_of_the_range_parse():
    assert feed.parse_time("2000-01-01T00:00:00Z").year == 2000
    assert feed.parse_time("2099-12-31T23:59:59Z").year == 2099


def P(**kw):
    kw.setdefault("name", "X")
    return feed.Provider(id="x", **kw)


@pytest.mark.parametrize("kw,hidden", [
    (dict(status="not_connected"), True),
    (dict(status="not_connected", balance=85.2), False),       # a hand-entered balance stays
    (dict(status="not_connected", mtd=1.0), False),
    (dict(status="not_connected", balance=0.0), False),
    (dict(status="ok"), False),                                    # a live provider with no figure is shown (dash)
    (dict(status="error"), False),
])
def test_hide_rule(kw, hidden):
    assert feed.is_hidden(P(**kw)) is hidden


def test_hidden_provider_stays_hidden_even_when_listed_in_config(raw_feed_live):
    f = feed.parse_feed(raw_feed_live)
    assert [p.id for p in f.providers] == ["deepseek", "openai", "fal", "anthropic", "acme", "example"]
    shown = [p.id for p in feed.select(f)]
    assert shown == ["deepseek", "openai", "fal", "anthropic", "acme"]             # example hidden, anthropic stays
    assert [p.id for p in feed.select(f, ["example", "acme", "fal"])] == ["acme", "fal"]   # config order, still hidden


@pytest.mark.parametrize("kw,mode", [
    (dict(status="not_connected"), "empty"),
    (dict(status="ok"), "empty"),                                   # connected but nothing known at all
    (dict(status="not_connected", balance=85.2), "figures"),      # hand-entered: figures only
    (dict(balance=1.0, mtd=2.0), "full"),
    (dict(mtd=0.0), "full"),
    (dict(d30=0.0), "full"),
    (dict(daily={"2026-11-01": 0.0}), "empty"),
    (dict(daily={"2026-11-01": 0.2}), "full"),
])
def test_peek_mode(kw, mode):
    assert feed.peek_mode(P(**kw)) == mode


def test_lead_figure_is_the_one_the_strip_shows():
    assert feed.lead_index(P(balance=0.0, mtd=3)) == 0
    assert feed.lead_index(P(mtd=3)) == 1
    assert feed.lead_index(P(d30=3)) is None and feed.lead_index(P()) is None
