"""With no limits_url, the Claude cell reads the status line's file in the config folder."""
import json

from fumes import config as cfgmod
from fumes import ui
from fumes.collect.ledger import Ledger
from fumes.keystore import MemoryStore


def make_app(tmp_path, monkeypatch, **kw):
    class Idle:
        class done:
            connect = staticmethod(lambda f: None)

        def __init__(self, *a, **k): pass
        def start(self): pass
        def isRunning(self): return False
    monkeypatch.setattr(ui, "CollectThread", Idle)
    monkeypatch.setattr(ui, "LimitsThread", Idle)
    return ui.App(cfgmod.Config(**kw), cfgmod.State(tmp_path / "s.json"), user_logo_dir=tmp_path,
                  store=MemoryStore(), ledger=Ledger(tmp_path / "ledger.json"))


def doc(five=30.0):
    return {"five_hour": {"used_percentage": five, "resets_at": 4_000_000_000},
            "seven_day": {"used_percentage": 60.0, "resets_at": 4_000_000_000}, "updated": "2026-11-12T12:00:00Z"}


def test_no_file_yet_is_no_reading_not_a_failure(qapp, tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch)
    app.refresh_limits()
    assert app.strip.limits is None and not app.strip.limits_failed


def test_reads_the_status_line_file(qapp, tmp_path, monkeypatch):
    (tmp_path / "claude-limits.json").write_text(json.dumps(doc(30.0)))
    app = make_app(tmp_path, monkeypatch)
    app.refresh_limits()
    assert app.strip.limits.five_hour.used == 30.0 and not app.strip.limits_failed
    assert app.state.limits_cache["five_hour"]["used_percentage"] == 30.0


def test_bad_or_huge_file_dims_the_cell(qapp, tmp_path, monkeypatch):
    f = tmp_path / "claude-limits.json"
    for content in ("not json", json.dumps([1, 2]), " " * (ui.App.LOCAL_LIMITS_MAX + 10) + "{}"):
        f.write_text(content)
        app = make_app(tmp_path, monkeypatch)
        app.refresh_limits()
        assert app.strip.limits_failed


def test_a_limits_url_still_fetches_remotely(qapp, tmp_path, monkeypatch):
    (tmp_path / "claude-limits.json").write_text(json.dumps(doc(30.0)))
    started = []

    class Fake:
        class done:
            connect = staticmethod(lambda f: None)

        def __init__(self, url, parent=None):
            started.append(url)
        def start(self): pass
        def isRunning(self): return False
    app = make_app(tmp_path, monkeypatch, limits_url="https://example.com/l.json")
    monkeypatch.setattr(ui, "LimitsThread", Fake)
    app.refresh_limits()
    assert started == ["https://example.com/l.json"] and app.strip.limits is None
