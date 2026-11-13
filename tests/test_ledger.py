import json
from datetime import datetime, timedelta, timezone

from fumes.collect.ledger import MAX_ANCHORS, MIN_SAMPLE_GAP, SAMPLE_DAYS, Ledger

T = datetime(2026, 11, 13, 12, 0, 0, tzinfo=timezone.utc)


def test_round_trip(tmp_path):
    led = Ledger(tmp_path / "sub" / "ledger.json")
    led.set_anchor("openai", 50, T, day_spend=1.5)
    led.record_balance("deepseek", 9.5, T)
    led.save()
    back = Ledger.load(tmp_path / "sub" / "ledger.json")
    assert back.anchor("openai") == {"usd": 50.0, "ts": "2026-11-13T12:00:00Z", "day_spend": 1.5}
    assert back.samples["deepseek"] == [["2026-11-13T12:00:00Z", 9.5]]
    assert not [p for p in (tmp_path / "sub").iterdir() if p.name.startswith(".ledger-")]


def test_malformed_files_dropped(tmp_path):
    p = tmp_path / "l.json"
    for text in ("", "not json", "[]", '"x"', '{"anchors": 5, "samples": []}'):
        p.write_text(text)
        led = Ledger.load(p)
        assert led.anchors == {} and led.samples == {}
    assert Ledger.load(tmp_path / "missing.json").anchors == {}


def test_malformed_entries_dropped(tmp_path):
    p = tmp_path / "l.json"
    p.write_text(json.dumps({
        "anchors": {"openai": [{"usd": 5, "ts": "2026-11-01T00:00:00Z"}, {"usd": "x", "ts": "2026-11-02T00:00:00Z"},
                               {"usd": 1, "ts": "yesterday"}, 7, {"usd": True, "ts": "2026-11-03T00:00:00Z"},
                               {"usd": float("inf"), "ts": "2026-11-03T00:00:00Z"}],
                    "bad": "str", "empty": [{"usd": "x"}]},
        "samples": {"deepseek": [["2026-11-01T00:00:00Z", 3], ["nope", 4], [1], "x", ["2026-11-02T00:00:00Z", None]],
                    "x": 3}}))
    led = Ledger.load(p)
    assert list(led.anchors) == ["openai"] and len(led.anchors["openai"]) == 1
    assert led.anchors["openai"][0]["day_spend"] == 0.0
    assert led.samples == {"deepseek": [["2026-11-01T00:00:00Z", 3.0]]}


def test_negative_day_spend_on_load_clamped(tmp_path):
    p = tmp_path / "l.json"
    p.write_text(json.dumps({"anchors": {"a": [{"usd": 5, "ts": "2026-11-01T00:00:00Z", "day_spend": -4}]}}))
    assert Ledger.load(p).anchor("a")["day_spend"] == 0.0


def test_set_anchor_validates_and_caps():
    import pytest
    led = Ledger("x")
    for bad in (-1, "a", None, float("nan"), True):
        with pytest.raises(ValueError):
            led.set_anchor("openai", bad, T)
    for i in range(MAX_ANCHORS + 3):
        led.set_anchor("openai", i, T + timedelta(seconds=i))
    assert len(led.anchors["openai"]) == MAX_ANCHORS and led.anchor("openai")["usd"] == MAX_ANCHORS + 2
    led.clear_anchor("openai")
    assert led.anchor("openai") is None


def test_anchored_balance():
    led = Ledger("x")
    assert led.anchored_balance("openai", []) == (None, None)
    led.set_anchor("openai", 50, T, day_spend=2.0)
    rows = [("2026-11-12", "a", 100.0), ("2026-11-13", "a", 3.0), ("2026-11-14", "b", 1.0)]
    bal, a = led.anchored_balance("openai", rows)
    assert bal == 50 - (3.0 + 1.0 - 2.0) and a["usd"] == 50
    # without day_spend, spend on the anchor day counts in full
    led.set_anchor("fal", 10, T)
    assert led.anchored_balance("fal", rows)[0] == 10 - 4.0
    # day_spend larger than the day's rows: never above the anchor
    led.set_anchor("big", 10, T, day_spend=99)
    assert led.anchored_balance("big", rows)[0] == 10.0
    assert led.anchored_balance("big", [])[0] == 10.0


def test_record_balance_replaces_close_readings_and_trims():
    led = Ledger("x")
    led.record_balance("d", 10, T)
    led.record_balance("d", 9, T + timedelta(seconds=MIN_SAMPLE_GAP - 1))
    assert led.samples["d"] == [["2026-11-13T12:00:59Z", 9.0]]
    led.record_balance("d", 8, T + timedelta(seconds=MIN_SAMPLE_GAP - 1 + MIN_SAMPLE_GAP))
    assert len(led.samples["d"]) == 2
    old = T - timedelta(days=SAMPLE_DAYS + 1)
    led2 = Ledger("x")
    led2.record_balance("d", 5, old)
    led2.record_balance("d", 4, T)
    assert led2.samples["d"] == [["2026-11-13T12:00:00Z", 4.0]]
    led2.record_balance("d", float("nan"), T + timedelta(hours=1))
    led2.record_balance("d", "x", T + timedelta(hours=1))
    assert len(led2.samples["d"]) == 1


def test_spend_from_balance_counts_falls_ignores_rises():
    led = Ledger("x")
    for i, bal in enumerate([10, 9, 12, 11.5, 11.5]):
        led.record_balance("d", bal, T + timedelta(hours=i))
    rows = led.spend_from_balance("d")
    assert [round(r[2], 6) for r in rows] == [1.0, 0.5]
    assert all(r[0] == "2026-11-13" for r in rows)
    assert led.spend_from_balance("none") == [] and led.first_sample("none") is None
    assert led.first_sample("d") == "2026-11-13T12:00:00Z"


def test_concurrent_changes_and_saves_never_raise(tmp_path):
    """Settings (UI thread) and collection (a worker) share one ledger."""
    import threading
    from datetime import datetime, timedelta, timezone
    led = Ledger(tmp_path / "l.json")
    t0 = datetime(2026, 11, 1, tzinfo=timezone.utc)
    errors = []

    def writer():
        try:
            for i in range(400):
                led.record_balance("deepseek", 100 - i * 0.1, t0 + timedelta(minutes=2 * i))
                led.set_anchor("openai", i, t0 + timedelta(minutes=i))
        except Exception as e:      # noqa: BLE001
            errors.append(e)

    def saver():
        try:
            for _ in range(200):
                led.save()
        except Exception as e:      # noqa: BLE001
            errors.append(e)
    threads = [threading.Thread(target=writer), threading.Thread(target=saver)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert errors == []
    assert Ledger.load(tmp_path / "l.json").anchor("openai") is not None
