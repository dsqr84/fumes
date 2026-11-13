"""User-added providers (collect/custom.py) and how App, CLI and Settings use them."""
import json
import math
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest
from PyQt6.QtCore import QCoreApplication
from PyQt6.QtWidgets import QLabel, QLineEdit, QPlainTextEdit, QTextBrowser

from fakes import FakeHttp
from fumes import __main__ as m
from fumes import collect as col
from fumes import config as cfgmod
from fumes import feed as feedmod
from fumes import keystore, settings, ui
from fumes.collect import NotConnected, ProviderError, custom
from fumes.collect.ledger import Ledger
from fumes.keystore import MemoryStore

NOW = datetime(2026, 11, 13, 15, 0, 0, tzinfo=timezone.utc)
SECRET = "KEYVALUE-0123456789"
LEAK = "STDERR-LEAK-TEXT"


# ------------------------------------------------------------------ helpers --
def ctx(pid, tmp_path, key=None, ledger=None):
    return col.Context(now=NOW, key=key, http=FakeHttp(), ledger=ledger or Ledger(tmp_path / "l.json"), pid=pid)


def script(tmp_path, body, name="prog.py"):
    p = tmp_path / name
    p.write_text("import json, os, sys, time\n" + body, "utf-8")
    return str(p)


def command(tmp_path, body, key=False, timeout=20, pid="acme", extra=()):
    spec = custom.validate({"id": pid, "name": "Acme", "kind": "command", "key": key, "timeout": timeout,
                            "argv": [sys.executable, script(tmp_path, body, f"{pid}.py"), *extra]})
    c = custom.Custom(spec)
    c.folder = tmp_path
    return c


def manual(burn=None, pid="prepaid"):
    spec = {"id": pid, "name": "Prepaid card", "kind": "manual"}
    if burn is not None:
        spec["burn_per_day"] = burn
    return custom.Custom(custom.validate(spec))


def printer(doc):
    return f"print(json.dumps({doc!r}))\n"


# ----------------------------------------------------------------- validate --
GOOD_CMD = {"id": "acme", "name": "Acme", "kind": "command", "argv": ["prog", "--x"]}


def bad(spec, taken=()):
    with pytest.raises(custom.ProviderSpecError) as e:
        custom.validate(spec, taken)
    return str(e.value)


def test_validate_accepts_good_specs():
    assert custom.validate({"id": "prepaid", "name": " Prepaid card ", "kind": "manual"}) == {
        "id": "prepaid", "name": "Prepaid card", "kind": "manual"}
    out = custom.validate(dict(GOOD_CMD, key=True, timeout=30))
    assert out == {"id": "acme", "name": "Acme", "kind": "command", "argv": ["prog", "--x"], "timeout": 30,
                   "key": True}
    assert custom.validate(GOOD_CMD)["timeout"] == custom.TIMEOUT and custom.validate(GOOD_CMD)["key"] is False


@pytest.mark.parametrize("pid", ["", "Acme", "has space", "-lead", "a" * 33, "é", "a/b", "a.b", None, 5])
def test_validate_rejects_bad_ids(pid):
    assert "id" in bad(dict(GOOD_CMD, id=pid))


@pytest.mark.parametrize("pid", ["deepseek", "openai", "fal", "anthropic", "claude"])
def test_validate_reserves_builtin_ids_and_claude(pid):
    assert "already used" in bad(dict(GOOD_CMD, id=pid))


def test_validate_rejects_duplicates():
    assert "already used" in bad(GOOD_CMD, taken={"acme"})


@pytest.mark.parametrize("name", ["", "   ", None, 5, "\x00\x01"])
def test_validate_needs_a_name(name):
    assert "name" in bad(dict(GOOD_CMD, name=name))


def test_validate_name_is_cleaned_and_capped():
    out = custom.validate(dict(GOOD_CMD, name="A\x00B\n" + "x" * 100))
    assert out["name"].startswith("AB") and len(out["name"]) == custom.MAX_NAME


@pytest.mark.parametrize("kind", ["", "shell", None, 1])
def test_validate_kinds(kind):
    assert "kind" in bad(dict(GOOD_CMD, kind=kind))


@pytest.mark.parametrize("argv", [None, "prog", [], [""], [1], ["a", None], ["a\x00b"], ["a"] * 33, ["x" * 2049],
                                  {"a": 1}])
def test_validate_argv_shape(argv):
    assert "command" in bad(dict(GOOD_CMD, argv=argv))


def test_validate_argv_missing():
    assert "command" in bad({"id": "acme", "name": "Acme", "kind": "command"})


@pytest.mark.parametrize("t", [0, -1, 121, 1.5, "20", True, None])
def test_validate_timeout_range(t):
    assert "timeout" in bad(dict(GOOD_CMD, timeout=t))


@pytest.mark.parametrize("t", [1, 120])
def test_validate_timeout_edges_ok(t):
    assert custom.validate(dict(GOOD_CMD, timeout=t))["timeout"] == t


@pytest.mark.parametrize("burn", [0, -1, math.nan, math.inf, "1", True, 2e9])
def test_validate_burn(burn):
    assert "burn" in bad({"id": "p", "name": "P", "kind": "manual", "burn_per_day": burn})


def test_validate_burn_ok_and_not_for_commands():
    assert custom.validate({"id": "p", "name": "P", "kind": "manual", "burn_per_day": 1.5})["burn_per_day"] == 1.5
    assert "burn_per_day" not in custom.validate(dict(GOOD_CMD, burn_per_day=3))


def test_validate_not_a_dict():
    assert "object" in bad(["acme"])


# --------------------------------------------------------------- load / save --
def test_save_round_trip(tmp_path):
    specs = [custom.validate({"id": "p", "name": "P", "kind": "manual", "burn_per_day": 2}),
             custom.validate(dict(GOOD_CMD, key=True))]
    custom.save(tmp_path / "new", specs)
    loaded = custom.load(tmp_path / "new")
    assert [c.spec for c in loaded] == specs
    assert [c.ID for c in loaded] == ["p", "acme"] and loaded[0].ANCHORED and not loaded[1].ANCHORED
    assert loaded[0].KEY_LABEL == "" and loaded[1].KEY_LABEL == "key" and loaded[1].folder == tmp_path / "new"
    assert [f.name for f in (tmp_path / "new").iterdir()] == ["providers.json"]      # no temp file left


def test_save_is_atomic_when_the_replace_fails(tmp_path, monkeypatch):
    custom.save(tmp_path, [custom.validate(GOOD_CMD)])
    before = (tmp_path / "providers.json").read_text()
    monkeypatch.setattr(custom.os, "replace", lambda *a: (_ for _ in ()).throw(OSError("disk")))
    with pytest.raises(OSError):
        custom.save(tmp_path, [])
    assert (tmp_path / "providers.json").read_text() == before
    assert [f.name for f in tmp_path.iterdir()] == ["providers.json"]


def test_load_skips_bad_entries_and_duplicates(tmp_path):
    doc = {"providers": [GOOD_CMD, {"id": "deepseek", "name": "X", "kind": "manual"}, "junk", 5, None,
                         {"id": "acme", "name": "Again", "kind": "manual"},
                         {"id": "ok2", "name": "Second", "kind": "manual"},
                         {"id": "bad id", "name": "Z", "kind": "manual"}]}
    (tmp_path / "providers.json").write_text(json.dumps(doc))
    assert [(c.ID, c.NAME) for c in custom.load(tmp_path)] == [("acme", "Acme"), ("ok2", "Second")]


@pytest.mark.parametrize("text", ["{not json", "", "[]", "null", '{"providers": 5}', '{"providers": null}',
                                  "\x00\x01", '{"providers": {"a": 1}}'])
def test_load_survives_a_corrupt_file(tmp_path, text):
    (tmp_path / "providers.json").write_text(text)
    assert custom.load(tmp_path) == []


def test_load_with_no_file(tmp_path):
    assert custom.load(tmp_path / "nowhere") == []


# ------------------------------------------------------------------- manual --
def test_manual_without_an_anchor_is_not_connected(tmp_path):
    with pytest.raises(NotConnected):
        manual().run(ctx("prepaid", tmp_path))


def test_manual_without_burn_shows_the_entered_balance(tmp_path):
    led = Ledger(tmp_path / "l.json")
    led.set_anchor("prepaid", 40, datetime(2026, 11, 1, 9, 0, tzinfo=timezone.utc))
    got = manual().run(ctx("prepaid", tmp_path, ledger=led))
    assert got["balance"] == 40 and got["rows"] == [] and "2026-11-01" in got["note"]


def test_manual_with_burn_counts_down_and_draws_estimates(tmp_path):
    led = Ledger(tmp_path / "l.json")
    led.set_anchor("prepaid", 40, datetime(2026, 11, 10, 9, 0, tzinfo=timezone.utc))
    got = manual(burn=2.5).run(ctx("prepaid", tmp_path, ledger=led))
    assert got["balance"] == pytest.approx(40 - 2.5 * 3)
    assert got["rows"] == [("2026-11-11", "estimate", 2.5), ("2026-11-12", "estimate", 2.5),
                           ("2026-11-13", "estimate", 2.5)]
    assert "Estimate" in got["note"] and "$2.50" in got["note"]


def test_manual_same_day_has_no_estimate_rows(tmp_path):
    led = Ledger(tmp_path / "l.json")
    led.set_anchor("prepaid", 40, NOW)
    got = manual(burn=2.5).run(ctx("prepaid", tmp_path, ledger=led))
    assert got["balance"] == 40 and got["rows"] == []


# ------------------------------------------------------------------ command --
def test_command_ok(tmp_path):
    c = command(tmp_path, printer({"balance": 12.4, "rows": [["2026-11-12", "m", 0.31]], "note": "hi"}))
    assert c.run(ctx("acme", tmp_path)) == {"rows": [("2026-11-12", "m", 0.31)], "balance": 12.4, "note": "hi"}


def test_command_runs_in_the_config_folder(tmp_path):
    c = command(tmp_path, 'print(json.dumps({"balance": 1, "note": os.getcwd()}))\n')
    assert Path(c.run(ctx("acme", tmp_path))["note"]).resolve() == tmp_path.resolve()


def test_command_nonzero_exit(tmp_path):
    c = command(tmp_path, "sys.exit(3)\n")
    with pytest.raises(ProviderError, match="code 3"):
        c.run(ctx("acme", tmp_path))


def test_command_timeout(tmp_path):
    c = command(tmp_path, "time.sleep(10)\n", timeout=1)
    t = time.time()
    with pytest.raises(ProviderError, match="longer than 1 s"):
        c.run(ctx("acme", tmp_path))
    assert time.time() - t < 8


def test_command_program_not_found(tmp_path):
    c = custom.Custom(custom.validate({"id": "acme", "name": "A", "kind": "command",
                                       "argv": [str(tmp_path / "no-such-program")]}))
    with pytest.raises(ProviderError, match="not found"):
        c.run(ctx("acme", tmp_path))


@pytest.mark.parametrize("body,match", [
    ("print('not json at all')\n", "did not print JSON"),
    ("print('')\n", "did not print JSON"),
    ("sys.stdout.buffer.write(b'\\xff\\xfe')\n", "did not print JSON"),
    ("print('[1, 2]')\n", "not an object"),
    ("print('\"text\"')\n", "not an object"),
    (printer({"balance": "12"}), "not a number"),
    (printer({"balance": True}), "not a number"),
    (printer({"balance": 1e30}), "not a number"),
    ("print('{\"balance\": NaN}')\n", "not a number"),
    (printer({"note": "no figures"}), "neither a balance nor any rows"),
    (printer({"balance": None, "rows": []}), "neither a balance nor any rows"),
])
def test_command_bad_output(tmp_path, body, match):
    with pytest.raises(ProviderError, match=match):
        command(tmp_path, body).run(ctx("acme", tmp_path))


def test_command_rows_with_bad_values_are_dropped(tmp_path):
    rows = [["2026-11-12", "good", 1.0], ["2026-13-45", "bad day", 1], ["12/11/2026", "fmt", 1],
            ["2026-11-12", "neg", -1], ["2026-11-12", "huge", 1e12], ["2026-11-12", "nan", "x"],
            ["2026-11-12", "bool", True], ["2026-11-12", 5, 2], ["2026-11-12", "short"], "row", None,
            ["2026-11-13", "", 3.5]]
    got = command(tmp_path, printer({"rows": rows})).run(ctx("acme", tmp_path))
    assert got["rows"] == [("2026-11-12", "good", 1.0), ("2026-11-12", "other", 2.0), ("2026-11-13", "other", 3.5)]
    assert got["balance"] is None


def test_command_oversized_output(tmp_path):
    c = command(tmp_path, "sys.stdout.write('x' * (2 << 20))\n")
    with pytest.raises(ProviderError, match="too much"):
        c.run(ctx("acme", tmp_path))


def test_command_note_and_labels_are_cleaned_and_capped():
    out = custom.parse_output(json.dumps({"balance": 1, "note": "a\x00b" + "n" * 1000,
                                          "rows": [["2026-11-12", "l\x07" + "x" * 500, 1]]}))
    assert out["note"].startswith("ab") and len(out["note"]) == custom.MAX_NOTE
    assert len(out["rows"][0][1]) == custom.MAX_LABEL and "\x07" not in out["rows"][0][1]


def test_command_gets_the_key_only_in_fumes_key(tmp_path, monkeypatch):
    monkeypatch.setenv("FUMES_KEY_OTHER", "other-provider-secret")
    monkeypatch.setenv("FUMES_KEY_ACME", "acme-from-env")
    monkeypatch.setenv("FUMES_KEY", "ambient")
    body = ('print(json.dumps({"balance": 1, "note": os.environ.get("FUMES_KEY", "") + "|" + '
            '",".join(sorted(k for k in os.environ if k.startswith("FUMES_KEY"))) + "|" + " ".join(sys.argv)}))\n')
    got = command(tmp_path, body, key=True).run(ctx("acme", tmp_path, key=SECRET))
    key, names, argv = got["note"].split("|")
    assert key == SECRET and names == "FUMES_KEY" and SECRET not in argv
    assert "other-provider-secret" not in got["note"] and "acme-from-env" not in got["note"]


def test_command_without_key_sees_no_fumes_key_at_all(tmp_path, monkeypatch):
    monkeypatch.setenv("FUMES_KEY_OTHER", "zzz")
    monkeypatch.setenv("FUMES_KEY", "ambient")
    body = 'print(json.dumps({"balance": 1, "note": ",".join(k for k in os.environ if k.startswith("FUMES_KEY"))}))\n'
    assert command(tmp_path, body).run(ctx("acme", tmp_path, key=SECRET))["note"] == ""


def test_command_needing_a_key_without_one_is_not_connected(tmp_path):
    with pytest.raises(NotConnected, match="fumes key set acme"):
        command(tmp_path, printer({"balance": 1}), key=True).run(ctx("acme", tmp_path, key=None))


@pytest.mark.parametrize("body", [
    f"sys.stderr.write('{LEAK} ' + os.environ.get('FUMES_KEY', ''))\nsys.exit(4)\n",
    f"sys.stderr.write('{LEAK} ' + os.environ.get('FUMES_KEY', ''))\nprint('not json')\n",
    f"sys.stderr.write('{LEAK} ' + os.environ.get('FUMES_KEY', ''))\nprint('[]')\n",
    f"sys.stderr.write('{LEAK} ' + os.environ.get('FUMES_KEY', ''))\nprint(json.dumps({{'balance': 'x'}}))\n",
])
def test_stderr_and_key_never_appear_in_an_error(tmp_path, body):
    with pytest.raises(ProviderError) as e:
        command(tmp_path, body, key=True).run(ctx("acme", tmp_path, key=SECRET))
    assert LEAK not in str(e.value) and SECRET not in str(e.value)


def test_stderr_never_reaches_a_collect_payload(tmp_path):
    c = command(tmp_path, f"sys.stderr.write('{LEAK}')\nsys.exit(2)\n")
    payload, failed = col.collect(NOW, MemoryStore(), Ledger(tmp_path / "l.json"), FakeHttp(), custom=[c],
                                  enabled=["acme"])
    assert failed == ["acme"] and LEAK not in json.dumps(payload)


def test_command_uses_no_shell(tmp_path):
    body = 'print(json.dumps({"balance": 1, "note": json.dumps(sys.argv[1:])}))\n'
    c = command(tmp_path, body, extra=("a", "&&", "evil", ";", "$HOME", "`x`", "|"))
    assert json.loads(c.run(ctx("acme", tmp_path))["note"]) == ["a", "&&", "evil", ";", "$HOME", "`x`", "|"]


def test_command_passes_shell_false_and_no_stdin(tmp_path, monkeypatch):
    seen = {}

    real = subprocess.Popen

    def fake(argv, **kw):
        seen.update(kw, argv=argv)
        return real([sys.executable, "-c", "import json; print(json.dumps({'balance': 1}))"], **kw)
    monkeypatch.setattr(custom.subprocess, "Popen", fake)
    assert command(tmp_path, "pass\n").run(ctx("acme", tmp_path))["balance"] == 1
    assert seen["shell"] is False and seen["stdin"] == subprocess.DEVNULL and seen["stderr"] == subprocess.DEVNULL
    assert isinstance(seen["argv"], list)


# ------------------------------------------------------------------ collect --
def test_collect_runs_custom_providers(tmp_path):
    led = Ledger(tmp_path / "l.json")
    led.set_anchor("prepaid", 30, NOW)
    cmd = command(tmp_path, printer({"balance": 7, "rows": [["2026-11-13", "x", 2]]}))
    payload, failed = col.collect(NOW, MemoryStore(), led, FakeHttp(), custom=[manual(), cmd],
                                  enabled=["prepaid", "acme"])
    assert failed == [] and list(payload["providers"]) == ["prepaid", "acme"]
    assert payload["providers"]["prepaid"]["balance"] == 30 and payload["providers"]["prepaid"]["name"] == "Prepaid card"
    assert payload["providers"]["acme"]["balance"] == 7 and payload["providers"]["acme"]["mtd"] == 2
    assert {p.id for p in feedmod.parse_feed(payload).providers} == {"acme", "prepaid"}


def test_collect_manual_without_anchor_and_keyless_command_need_no_key(tmp_path):
    payload, failed = col.collect(NOW, MemoryStore(), Ledger(tmp_path / "l.json"), FakeHttp(),
                                  custom=[manual(), command(tmp_path, printer({"balance": 1}))],
                                  enabled=["prepaid", "acme"])
    assert failed == [] and payload["providers"]["prepaid"]["status"] == "not_connected"
    assert payload["providers"]["prepaid"]["note"].startswith("No balance entered")
    assert payload["providers"]["acme"]["status"] == "ok"


def test_collect_failing_command_goes_stale_and_others_are_untouched(tmp_path):
    good = command(tmp_path, printer({"balance": 5}), pid="good")
    flaky = command(tmp_path, "sys.exit(1)\n", pid="flaky")
    prev, _ = col.collect(NOW, MemoryStore(), Ledger(tmp_path / "l.json"), FakeHttp(), custom=[good],
                          enabled=["good"])
    prev["providers"]["flaky"] = dict(prev["providers"]["good"], balance=9)
    payload, failed = col.collect(NOW, MemoryStore(), Ledger(tmp_path / "l.json"), FakeHttp(), previous=prev,
                                  custom=[good, flaky], enabled=["good", "flaky"])
    assert failed == ["flaky"] and payload["providers"]["flaky"]["status"] == "stale"
    assert payload["providers"]["flaky"]["balance"] == 9 and payload["providers"]["good"]["status"] == "ok"


def test_collect_key_command_not_connected_without_a_key(tmp_path):
    payload, _ = col.collect(NOW, MemoryStore(), Ledger(tmp_path / "l.json"), FakeHttp(),
                             custom=[command(tmp_path, printer({"balance": 1}), key=True)], enabled=["acme"])
    assert payload["providers"]["acme"]["status"] == "not_connected"


def test_a_custom_provider_may_use_any_providers_key(tmp_path):
    """Its key goes to the user's own local program, not to a company, so the wrong-provider check is for
    built-ins only: a script tracking an Anthropic workspace legitimately takes an Anthropic key."""
    marker = tmp_path / "ran"
    c = command(tmp_path, f"open({str(marker)!r}, 'w').close()\n" + printer({"balance": 1}), key=True)
    payload, _ = col.collect(NOW, MemoryStore({"acme": "sk-ant-admin-zzz"}), Ledger(tmp_path / "l.json"),
                             FakeHttp(), custom=[c], enabled=["acme"])
    assert payload["providers"]["acme"]["status"] == "ok" and marker.exists()
    assert "sk-ant-admin-zzz" not in json.dumps(payload)


def test_collect_without_custom_is_unchanged(tmp_path):
    payload, _ = col.collect(NOW, MemoryStore(), Ledger(tmp_path / "l.json"), FakeHttp())
    assert list(payload["providers"]) == ["deepseek", "openai", "fal", "anthropic"]


def test_set_anchor_and_by_id_know_custom_providers(tmp_path):
    led = Ledger(tmp_path / "l.json")
    mod = manual()
    assert col.by_id("prepaid") is None and col.by_id("prepaid", [mod]) is mod
    rec, warning = col.set_anchor("prepaid", 12.5, NOW, MemoryStore(), led, custom=[mod])
    assert rec["usd"] == 12.5 and warning == "" and led.anchor("prepaid")["usd"] == 12.5
    with pytest.raises(ValueError):
        col.set_anchor("prepaid", 1, NOW, MemoryStore(), led)               # unknown without the custom list
    with pytest.raises(ValueError):
        col.set_anchor("acme", 1, NOW, MemoryStore(), led, custom=[command(tmp_path, "pass\n")])


# ---------------------------------------------------------------------- App --
def wait_thread(qapp, app, timeout=10):
    end = time.time() + timeout
    while time.time() < end:
        qapp.processEvents()
        if app.thread is not None and app.thread.isFinished():
            qapp.processEvents()
            return True
        time.sleep(0.01)
    return False


def make_app(tmp_path, url="", providers=None, keys=None):
    cfg = cfgmod.Config(url=url, show_limits=False, providers=providers or [])
    return ui.App(cfg, cfgmod.State(tmp_path / "state.json"), user_logo_dir=tmp_path, store=MemoryStore(keys or {}),
                  ledger=Ledger(tmp_path / "ledger.json"), http=FakeHttp())


def test_app_loads_providers_json_at_every_refresh(qapp, tmp_path):
    custom.save(tmp_path, [custom.validate({"id": "prepaid", "name": "Prepaid card", "kind": "manual"})])
    led = Ledger(tmp_path / "ledger.json")
    led.set_anchor("prepaid", 25, datetime.now(timezone.utc))
    led.save()
    app = make_app(tmp_path)
    app.ledger = Ledger.load(tmp_path / "ledger.json")
    try:
        assert wait_thread(qapp, app)
        app.refresh()
        assert wait_thread(qapp, app)
        ids = [c.provider.id for c in app.strip.cells() if c.kind == "provider"]
        assert "prepaid" in ids and app.state.cache["providers"]["prepaid"]["balance"] == 25
        # an edit made in Settings applies at the next refresh, with no restart
        custom.save(tmp_path, [custom.validate({"id": "second", "name": "Second", "kind": "manual"}),
                               custom.validate({"id": "prepaid", "name": "Prepaid card", "kind": "manual"})])
        app.refresh()
        assert wait_thread(qapp, app)
        assert set(app.state.cache["providers"]) >= {"second", "prepaid"}
    finally:
        app.strip.hide()


def test_app_providers_filter_applies_to_custom_providers(qapp, tmp_path):
    custom.save(tmp_path, [custom.validate({"id": "prepaid", "name": "Prepaid card", "kind": "manual"})])
    app = make_app(tmp_path, providers=["prepaid"])
    try:
        assert wait_thread(qapp, app)
        assert list(app.state.cache["providers"]) == ["prepaid"]
        app2 = make_app(tmp_path, providers=["deepseek"])
        assert wait_thread(qapp, app2)
        assert list(app2.state.cache["providers"]) == ["deepseek"]
        app2.strip.hide()
    finally:
        app.strip.hide()


def test_app_runs_a_custom_command_and_shows_it_in_the_strip(qapp, tmp_path):
    custom.save(tmp_path, [custom.validate({"id": "acme", "name": "Acme", "kind": "command",
                                            "argv": [sys.executable, script(tmp_path, printer({"balance": 8.5}))]})])
    app = make_app(tmp_path)
    try:
        assert wait_thread(qapp, app)
        assert app.state.cache["providers"]["acme"]["balance"] == 8.5
        assert "acme" in [c.provider.id for c in app.strip.cells() if c.kind == "provider"]
    finally:
        app.strip.hide()


def test_a_remote_feed_never_runs_commands(qapp, tmp_path, monkeypatch, raw_feed):
    marker = tmp_path / "ran"
    custom.save(tmp_path, [custom.validate({"id": "acme", "name": "Acme", "kind": "command", "argv": [
        sys.executable, script(tmp_path, f"open({str(marker)!r}, 'w').close()\n" + printer({"balance": 1}))]})])
    raw = json.loads(json.dumps(raw_feed))
    raw["providers"]["acme"] = {"status": "ok", "balance": 1, "name": "Acme", "command": ["evil"],
                                "argv": ["evil"]}

    def boom(*a, **k):
        raise AssertionError("a remote feed must never start a process")
    monkeypatch.setattr(subprocess, "run", boom)
    monkeypatch.setattr(subprocess, "Popen", boom)
    monkeypatch.setattr(ui.feedmod, "fetch", lambda url, timeout=10: (feedmod.parse_feed(raw), raw))
    app = make_app(tmp_path, url="http://feed.invalid/feed.json")
    try:
        assert isinstance(app.thread, ui.FetchThread)
        assert wait_thread(qapp, app)
        assert not marker.exists()
        assert "acme" in [c.provider.id for c in app.strip.cells() if c.kind == "provider"]    # drawn as a figure
    finally:
        app.strip.hide()


# ---------------------------------------------------------------------- CLI --
@pytest.fixture
def cli(tmp_path, monkeypatch):
    store = MemoryStore()
    monkeypatch.setattr(cfgmod, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(m, "make_store", lambda: store)
    monkeypatch.setattr(m, "make_http", lambda: FakeHttp())
    custom.save(tmp_path, [
        custom.validate({"id": "prepaid", "name": "Prepaid card", "kind": "manual"}),
        custom.validate({"id": "acme", "name": "Acme", "kind": "command", "key": True,
                         "argv": [sys.executable, script(tmp_path, 'print(json.dumps({"balance": float(len(os.environ.get("FUMES_KEY", "")))}))\n')]}),
        custom.validate({"id": "keyless", "name": "Keyless", "kind": "command",
                         "argv": [sys.executable, script(tmp_path, printer({"balance": 2}), "k.py")]}),
    ])
    return store, tmp_path


def test_cli_key_list_includes_custom_providers_that_take_a_key(cli, capsys):
    store, _ = cli
    store.set("acme", SECRET)
    assert m.main(["key", "list"]) == 0
    out = capsys.readouterr().out
    lines = {l.split("\t")[0]: l.split("\t") for l in out.strip().splitlines()}
    assert lines["acme"] == ["acme", "Acme", "key", "saved"] and SECRET not in out
    assert "prepaid" not in lines and "keyless" not in lines and "deepseek" in lines


def test_cli_key_set_and_delete_for_a_custom_id(cli, capsys, monkeypatch):
    store, _ = cli
    monkeypatch.setattr("getpass.getpass", lambda prompt="": SECRET)
    assert m.main(["key", "set", "acme"]) == 0 and store.get("acme") == SECRET
    assert SECRET not in capsys.readouterr().out
    assert m.main(["key", "delete", "acme"]) == 0 and store.get("acme") is None


def test_cli_key_set_refuses_a_provider_without_a_key_and_unknown_ids(cli, capsys, monkeypatch):
    store, _ = cli
    monkeypatch.setattr("getpass.getpass", lambda prompt="": SECRET)
    assert m.main(["key", "set", "prepaid"]) == 2 and m.main(["key", "set", "keyless"]) == 2
    assert m.main(["key", "set", "nope"]) == 2 and store.get("prepaid") is None
    assert "does not use a key" in capsys.readouterr().err


def test_cli_custom_ids_take_any_key_but_built_ins_do_not(cli, capsys, monkeypatch):
    store, _ = cli
    monkeypatch.setattr("getpass.getpass", lambda prompt="": "sk-ant-admin-ABCDEFG")
    assert m.main(["key", "set", "acme"]) == 0 and store.get("acme") == "sk-ant-admin-ABCDEFG"
    assert m.main(["key", "set", "openai"]) == 2 and store.get("openai") is None
    assert "sk-ant-admin-ABCDEFG" not in capsys.readouterr().err


def test_cli_anchor_for_a_custom_manual_provider(cli, capsys):
    _, tmp = cli
    assert m.main(["anchor", "prepaid", "$12.50"]) == 0
    assert "$12.50" in capsys.readouterr().out
    assert Ledger.load(tmp / "ledger.json").anchor("prepaid")["usd"] == 12.5
    assert m.main(["anchor", "acme", "5"]) == 1             # a command reports its own balance


def test_cli_collect_includes_custom_providers(cli, capsys):
    store, tmp = cli
    store.set("acme", "12345")
    Ledger.load(tmp / "ledger.json")
    assert m.main(["anchor", "prepaid", "9"]) == 0
    capsys.readouterr()
    assert m.main(["collect"]) == 0
    rows = {l.split("\t")[0]: l.split("\t") for l in capsys.readouterr().out.strip().splitlines()}
    assert rows["prepaid"][1] == "ok" and "$9.00" in rows["prepaid"][2]
    assert rows["acme"][1] == "ok" and "$5.00" in rows["acme"][2]          # the key reached the program
    assert rows["keyless"][1] == "ok" and "$2.00" in rows["keyless"][2]


def test_cli_uses_the_folder_of_config(cli, tmp_path, capsys):
    other = tmp_path / "other"
    custom.save(other, [custom.validate({"id": "elsewhere", "name": "E", "kind": "manual"})])
    assert m.main(["anchor", "elsewhere", "3", "--config", str(other / "config.toml")]) == 0
    assert Ledger.load(other / "ledger.json").anchor("elsewhere")["usd"] == 3
    assert m.main(["anchor", "elsewhere", "3"]) == 1       # not defined in the default folder


# ----------------------------------------------------------------- Settings --
def wait(cond, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        QCoreApplication.processEvents()
        if cond():
            return True
        time.sleep(0.01)
    return False


@pytest.fixture
def env(qapp, tmp_path):
    store = MemoryStore()
    ledger = Ledger(tmp_path / "ledger.json")
    asked, refreshed = [], []
    answers = {"all": True}
    w = settings.SettingsWindow(store, ledger, http=FakeHttp(), folder=tmp_path, cfg=cfgmod.Config(),
                                opener=lambda u: True, on_refresh=lambda: refreshed.append(1),
                                confirm=lambda t: asked.append(t) or answers["all"])
    w.asked, w.refreshed, w.answers = asked, refreshed, answers
    yield w
    w.close()


def fill_manual(dlg, name="Prepaid card", burn=""):
    dlg.name.setText(name)
    dlg.kind.setCurrentIndex(0)
    dlg.burn.setText(burn)


def fill_command(dlg, name="Acme", program=sys.executable, args="", key=False, timeout=20):
    dlg.name.setText(name)
    dlg.kind.setCurrentIndex(1)
    dlg.program.setText(program)
    dlg.args.setText(args)
    dlg.needs_key.setChecked(key)
    dlg.timeout.setValue(timeout)


def test_settings_lists_custom_providers_after_the_builtins(env, tmp_path):
    assert env.ids[:4] == ["deepseek", "openai", "fal", "anthropic"] and "prepaid" not in env.ids
    custom.save(tmp_path, [custom.validate({"id": "prepaid", "name": "Prepaid card", "kind": "manual"})])
    env.reload_custom()
    assert env.ids[:5] == ["deepseek", "openai", "fal", "anthropic", "prepaid"] and env.ids[5] == "claude"
    assert env.nav.item(4).text() == "Prepaid card" and not env.nav.item(4).icon().isNull()
    assert env.stack.widget(4) is env.pages["prepaid"] and env.nav.count() == env.stack.count() == len(env.ids)


def test_add_dialog_prefills_the_id_until_it_is_edited(env):
    dlg = env.open_add_dialog()
    dlg.name.setText("My Prepaid Card!")
    assert dlg.pid.text() == "my-prepaid-card"
    dlg.pid.textEdited.emit("mine")
    dlg.pid.setText("mine")
    dlg.name.setText("Something else")
    assert dlg.pid.text() == "mine"
    dlg.reject()


def test_add_dialog_shows_the_kind_specific_fields(env):
    dlg = env.open_add_dialog()
    dlg.show()
    assert dlg.manual_box.isVisible() and not dlg.command_box.isVisible()
    dlg.kind.setCurrentIndex(1)
    assert dlg.command_box.isVisible() and not dlg.manual_box.isVisible()
    assert dlg.timeout.value() == 20 and not dlg.needs_key.isChecked()
    dlg.reject()


def test_add_manual_provider_saves_and_shows_its_page(env, tmp_path):
    dlg = env.open_add_dialog()
    fill_manual(dlg, burn="$1.50")
    dlg.accept()
    assert dlg.result() == 1 and "prepaid-card" in env.ids and env.refreshed
    assert [c.spec for c in custom.load(tmp_path)] == [
        {"id": "prepaid-card", "name": "Prepaid card", "kind": "manual", "burn_per_day": 1.5}]
    assert env.ids[env.nav.currentRow()] == "prepaid-card"
    page = env.pages["prepaid-card"]
    assert page.anchor_edit is not None and "$1.50" in page.burn_line.text() and not page.key_box.isVisible()
    assert page.status.text() == "Manual"


def test_add_command_provider_saves_argv_and_needs_key(env, tmp_path):
    dlg = env.open_add_dialog()
    fill_command(dlg, program=r"C:\Tools\balance.exe", args=r'--json "two words" C:\x\y', key=True, timeout=45)
    dlg.accept()
    c = custom.load(tmp_path)[0]
    assert c.argv == [r"C:\Tools\balance.exe", "--json", "two words", r"C:\x\y"]
    assert c.timeout == 45 and c.KEY_LABEL == "key" and c.kind == "command"


def test_arguments_preview_shows_exactly_what_will_run(env):
    dlg = env.open_add_dialog()
    fill_command(dlg, program="python", args=r'a.py --name "two words" C:\x\y')
    assert dlg.preview.text() == r'["python", "a.py", "--name", "two words", "C:\x\y"]'
    dlg.args.setText("")
    assert dlg.preview.text() == '["python"]'
    dlg.args.setText('"never closed')
    assert "not closed" in dlg.preview.text()
    dlg.accept()
    assert "quote" in dlg.msg.text() and dlg.result() == 0
    dlg.reject()


def test_split_args_windows_semantics():
    assert settings.split_args(r'C:\a\b "x y" --k=v ""') == [r"C:\a\b", "x y", "--k=v", ""]
    assert settings.split_args("") == [] and settings.split_args("   ") == []
    with pytest.raises(ValueError):
        settings.split_args("'open")


def test_add_dialog_shows_validation_messages_and_saves_nothing(env, tmp_path):
    cases = [(lambda d: (fill_manual(d, name="   "), d.pid.setText("x")), "name"),
             (lambda d: (fill_manual(d, "X"), d.pid.setText("Bad Id")), "id"),
             (lambda d: (fill_manual(d, "X"), d.pid.setText("openai")), "already used"),
             (lambda d: (fill_manual(d, "X"), d.pid.setText("claude")), "already used"),
             (lambda d: fill_manual(d, "X", burn="0"), "burn"),
             (lambda d: fill_command(d, program=""), "command")]
    for setup, expect in cases:
        dlg = env.open_add_dialog()
        setup(dlg)
        dlg.accept()
        assert expect in dlg.msg.text() and dlg.result() == 0
        assert not (tmp_path / "providers.json").exists() and not env.refreshed
        dlg.reject()


def test_add_dialog_rejects_a_duplicate_of_a_custom_id(env):
    d1 = env.open_add_dialog()
    fill_manual(d1, "Prepaid")
    d1.accept()
    d2 = env.open_add_dialog()
    fill_manual(d2, "Prepaid")
    d2.accept()
    assert "already used" in d2.msg.text() and len(env.custom) == 1
    d2.reject()


def test_add_dialog_reports_a_save_failure(env, monkeypatch):
    monkeypatch.setattr(settings.customlib, "save", lambda *a: (_ for _ in ()).throw(OSError("disk")))
    dlg = env.open_add_dialog()
    fill_manual(dlg)
    dlg.accept()
    assert "Could not save" in dlg.msg.text() and "prepaid-card" not in env.ids
    dlg.reject()


def test_browse_fills_the_program(env, monkeypatch):
    monkeypatch.setattr(settings.QFileDialog, "getOpenFileName", lambda *a, **k: (r"C:\Tools\x.exe", ""))
    dlg = env.open_add_dialog()
    dlg.browse_btn.click()
    assert dlg.program.text() == r"C:\Tools\x.exe"
    monkeypatch.setattr(settings.QFileDialog, "getOpenFileName", lambda *a, **k: ("", ""))
    dlg.browse_btn.click()
    assert dlg.program.text() == r"C:\Tools\x.exe"
    dlg.reject()


def add(env, **spec):
    env.add_provider(dict({"id": "prepaid", "name": "Prepaid card", "kind": "manual"}, **spec))
    return env.pages[spec.get("id", "prepaid")]


def test_manual_page_sets_the_anchor_through_custom_collect(env):
    page = add(env)
    page.anchor_edit.setText("$40")
    page.anchor_set.click()
    assert wait(lambda: env.ledger.anchor("prepaid") is not None)
    assert env.ledger.anchor("prepaid")["usd"] == 40 and wait(lambda: page.anchor_set.isEnabled())
    assert "Entered $40.00" in page.anchor_line.text() and env.refreshed


def test_manual_page_test_button_runs_without_a_key(env):
    page = add(env)
    page.test.click()
    assert wait(lambda: page.test.isEnabled() and "Not connected" in page.test_msg.text())
    page.anchor_edit.setText("5")
    page.anchor_set.click()
    assert wait(lambda: env.ledger.anchor("prepaid") and page.anchor_set.isEnabled())
    page.test.click()
    assert wait(lambda: "Working" in page.test_msg.text()) and "$5.00" in page.test_msg.text()


def command_page(env, tmp_path, body, key=False, pid="acme"):
    env.add_provider({"id": pid, "name": "Acme", "kind": "command", "key": key,
                      "argv": [sys.executable, script(tmp_path, body)]})
    return env.pages[pid]


def test_command_page_shows_program_and_arguments_read_only(env, tmp_path):
    env.add_provider({"id": "acme", "name": "Acme", "kind": "command", "argv": ["prog", r"C:\a b\c", "--x"]})
    page = env.pages["acme"]
    assert page.cmd_view.text() == 'Program: prog\nArguments:\n    "C:\\a b\\c"\n    "--x"\nTimeout: 20 s'
    assert page.status.text() == "Command" and not page.key_box.isVisible()
    assert "contract" in page.guide.toPlainText().lower()


def test_command_page_test_runs_the_command_off_the_ui_thread(env, tmp_path):
    page = command_page(env, tmp_path, "time.sleep(0.3)\n" + printer({"balance": 3.5}))
    page.test.click()
    assert not page.test.isEnabled() and "Testing" in page.test_msg.text()
    assert wait(lambda: page.test.isEnabled())
    assert page.test_msg.text() == "Working: balance $3.50, month to date $0.00"


def test_command_page_test_reports_failures_safely(env, tmp_path):
    page = command_page(env, tmp_path, f"sys.stderr.write('{LEAK}')\nsys.exit(2)\n")
    page.test.click()
    assert wait(lambda: page.test_msg.text().startswith("Failed"))
    assert "code 2" in page.test_msg.text() and LEAK not in page.test_msg.text()


def test_command_page_with_a_key_follows_the_builtin_key_rules(env, tmp_path):
    body = 'print(json.dumps({"balance": float(len(os.environ.get("FUMES_KEY", "")))}))\n'
    page = command_page(env, tmp_path, body, key=True)
    assert page.key.echoMode() == page.key.EchoMode.Password and page.status.text() == "Command, no key yet"
    page.test.click()
    assert "No key saved" in page.test_msg.text()
    page.key.setText(SECRET)
    page.save.click()
    assert env.store.get("acme") == SECRET and page.key.text() == "" and page.status.text() == "Command, key saved"
    for t in (lbl.text() for lbl in env.findChildren(QLabel)):
        assert SECRET not in t
    page.test.click()
    assert wait(lambda: "Working" in page.test_msg.text())
    assert f"${float(len(SECRET)):,.2f}" in page.test_msg.text()


def test_custom_page_saves_any_providers_key_and_never_shows_it(env, tmp_path):
    page = command_page(env, tmp_path, printer({"balance": 1}), key=True)
    page.key.setText("sk-ant-admin-ABCDEFG")
    page.save.click()
    assert env.store.get("acme") == "sk-ant-admin-ABCDEFG" and page.key.text() == ""
    assert "ABCDEFG" not in page.key_msg.text()


def test_remove_provider_confirms_then_offers_key_and_anchor(env, tmp_path):
    page = command_page(env, tmp_path, printer({"balance": 1}), key=True)
    env.store.set("acme", SECRET)
    env.ledger.set_anchor("acme", 3, datetime.now(timezone.utc))
    page.remove_provider.click()
    assert len(env.asked) == 3 and "Remove the provider Acme" in env.asked[0]
    assert "key" in env.asked[1] and "balance" in env.asked[2]
    assert "acme" not in env.ids and "acme" not in env.pages and custom.load(tmp_path) == []
    assert env.store.get("acme") is None and env.ledger.anchor("acme") is None and env.refreshed
    assert env.nav.count() == env.stack.count() == len(env.ids)


def test_remove_provider_can_keep_the_key_and_the_anchor(env, tmp_path):
    page = command_page(env, tmp_path, printer({"balance": 1}), key=True)
    env.store.set("acme", SECRET)
    env.ledger.set_anchor("acme", 3, datetime.now(timezone.utc))
    answers = iter([True, False, False])
    env._confirm = lambda t: next(answers)
    page.remove_provider.click()
    assert "acme" not in env.ids and env.store.get("acme") == SECRET and env.ledger.anchor("acme") is not None


def test_remove_provider_declined_changes_nothing(env, tmp_path):
    page = add(env)
    env.answers["all"] = False
    page.remove_provider.click()
    assert len(env.asked) == 1 and "prepaid" in env.ids and len(custom.load(tmp_path)) == 1


def test_remove_only_asks_about_what_exists(env):
    page = add(env)
    page.remove_provider.click()
    assert len(env.asked) == 1 and "prepaid" not in env.ids


def test_removing_one_keeps_the_others(env, tmp_path):
    add(env)
    add(env, id="second", name="Second")
    env.pages["prepaid"].remove_provider.click()
    assert env.ids[4] == "second" and [c.ID for c in custom.load(tmp_path)] == ["second"]
    assert env.ids[env.nav.currentRow()] == "general"


def test_no_secret_in_any_widget_after_adding_a_keyed_provider(env, tmp_path):
    page = command_page(env, tmp_path, printer({"balance": 1}), key=True)
    env.store.set("acme", SECRET)
    page.refresh()
    for cls in (QLineEdit, QLabel, QTextBrowser, QPlainTextEdit):
        for c in env.findChildren(cls):
            assert SECRET not in (c.text() if cls in (QLineEdit, QLabel) else c.toPlainText())


def test_settings_picks_up_providers_json_written_before_it_opened(qapp, tmp_path):
    custom.save(tmp_path, [custom.validate({"id": "prepaid", "name": "Prepaid card", "kind": "manual"})])
    w = settings.SettingsWindow(MemoryStore(), Ledger(tmp_path / "l.json"), folder=tmp_path, cfg=cfgmod.Config(),
                                opener=lambda u: True)
    try:
        assert "prepaid" in w.ids and w.ids.index("prepaid") == 4
    finally:
        w.close()


def test_render_png_for_the_demo_pages(qapp, tmp_path):
    out = tmp_path / "x.png"
    assert m.main(["--once-png", str(out), "--settings", "custom-command", "--config", str(tmp_path / "c.toml")]) == 0
    assert out.stat().st_size > 1000
    assert m.main(["--once-png", str(out), "--settings", "add-provider", "--config", str(tmp_path / "c.toml")]) == 0
    assert m.main(["--once-png", str(out), "--settings", "custom-manual", "--config", str(tmp_path / "c.toml")]) == 0


def test_a_flooding_command_is_killed_at_the_cap_not_read_in_full(tmp_path):
    """MAX_OUTPUT bounds memory and time: reading stops at the cap and the program is killed."""
    import time
    c = command(tmp_path, "import sys\nwhile True:\n    sys.stdout.write('x' * 65536)\n")
    t0 = time.monotonic()
    with pytest.raises(ProviderError, match="printed too much"):
        c.run(ctx("acme", tmp_path))
    assert time.monotonic() - t0 < c.timeout                 # did not wait for the timeout


def test_a_silent_hanging_command_times_out_and_is_reaped(tmp_path):
    c = command(tmp_path, "import time\ntime.sleep(60)\n")
    c.timeout = 1
    with pytest.raises(ProviderError, match="longer than 1 s"):
        c.run(ctx("acme", tmp_path))
