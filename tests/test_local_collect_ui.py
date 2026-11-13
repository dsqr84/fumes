"""The App's local collection: cfg.url empty -> collect on a QThread with injected store, ledger and Http."""
import json
import time

from fakes import FakeHttp
from fumes import config as cfgmod
from fumes import ui
from fumes.collect.ledger import Ledger
from fumes.keystore import MemoryStore
from test_collect import ALL, routes


def wait_for(qapp, app, timeout=10):
    end = time.time() + timeout
    while time.time() < end:
        qapp.processEvents()
        if app.thread is not None and app.thread.isFinished():
            qapp.processEvents()
            return True
        time.sleep(0.01)
    return False


def make_app(tmp_path, http, keys, url="", providers=None):
    cfg = cfgmod.Config(url=url, show_limits=False, providers=providers or [])
    state = cfgmod.State(tmp_path / "state.json")
    ledger = Ledger(tmp_path / "ledger.json")
    return ui.App(cfg, state, user_logo_dir=tmp_path, store=MemoryStore(keys), ledger=ledger, http=http), state, ledger


def test_empty_url_collects_locally_off_the_ui_thread(qapp, tmp_path):
    http = FakeHttp(routes())
    app, state, ledger = make_app(tmp_path, http, ALL)
    try:
        assert isinstance(app.thread, ui.CollectThread)
        assert wait_for(qapp, app)
        assert state.cache and state.cache["providers"]["fal"]["balance"] == 3
        assert json.loads((tmp_path / "state.json").read_text())["cache"]["providers"]["openai"]["mtd"] == 2.0
        ids = [c.provider.id for c in app.strip.cells() if c.kind == "provider"]
        assert ids == ["deepseek", "openai", "fal", "anthropic"] and not app.strip.fetch_failed
        assert (tmp_path / "ledger.json").is_file()                 # DeepSeek's balance sample was saved
        assert Ledger.load(tmp_path / "ledger.json").samples["deepseek"]
        assert all(h.get("Authorization", "").endswith(("k1", "k2", "k3")) or "x-api-key" in h
                   for _, h in http.calls)
    finally:
        app.strip.hide()


def test_failed_provider_goes_stale_through_the_same_path(qapp, tmp_path):
    http = FakeHttp(routes())
    app, state, _ = make_app(tmp_path, http, ALL)
    assert wait_for(qapp, app)
    http.add("https://api.fal.ai/v1/account/billing", (500, {"error": {"type": "boom"}}))
    app.refresh()
    assert wait_for(qapp, app)
    assert state.cache["providers"]["fal"]["status"] == "stale"
    assert state.cache["providers"]["fal"]["balance"] == 3
    app.strip.hide()


def test_providers_config_limits_what_is_asked(qapp, tmp_path):
    http = FakeHttp(routes())
    app, state, _ = make_app(tmp_path, http, ALL, providers=["fal"])
    assert wait_for(qapp, app)
    assert list(state.cache["providers"]) == ["fal"]
    app.strip.hide()


def test_no_keys_writes_no_ledger_and_shows_not_connected(qapp, tmp_path):
    http = FakeHttp(routes())
    app, state, _ = make_app(tmp_path, http, {})
    assert wait_for(qapp, app)
    assert http.calls == [] and not (tmp_path / "ledger.json").exists()
    assert all(p["status"] == "not_connected" for p in state.cache["providers"].values())
    app.strip.hide()


def test_collect_crash_is_a_failed_fetch_not_a_ui_crash(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(ui.collect, "collect", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("secret k1")))
    app, state, _ = make_app(tmp_path, FakeHttp(), ALL)
    assert wait_for(qapp, app)
    assert state.cache is None and app.strip.fetch_failed
    app.strip.hide()


def test_url_set_keeps_the_remote_feed_path(qapp, tmp_path, monkeypatch):
    started = []

    class FakeThread:
        class done:
            connect = staticmethod(lambda f: None)

        def __init__(self, *a, **k):
            started.append(a)

        def start(self): pass
        def isRunning(self): return False

    monkeypatch.setattr(ui, "FetchThread", FakeThread)
    monkeypatch.setattr(ui, "CollectThread", FakeThread)
    make_app(tmp_path, FakeHttp(), ALL, url="http://x/feed")[0].strip.hide()
    assert started == [("http://x/feed",)]
