import pytest

from fakes import FakeHttp
from fumes import __main__ as m
from fumes import config as cfgmod
from fumes.collect.ledger import Ledger
from fumes.keystore import MemoryStore
from test_collect import ALL, routes

SECRET = "sk-VERYSECRETVALUE"


@pytest.fixture
def env(tmp_path, monkeypatch):
    store = MemoryStore()
    http = FakeHttp(routes())
    monkeypatch.setattr(cfgmod, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(m, "make_store", lambda: store)
    monkeypatch.setattr(m, "make_http", lambda: http)
    return store, http, tmp_path


def test_non_cli_args_fall_through():
    assert m.cli([]) is None and m.cli(["--once-png", "x.png"]) is None and m.cli(["--url", "u"]) is None


def test_key_list_never_prints_value(env, capsys):
    store, _, _ = env
    store.set("openai", SECRET)
    assert m.main(["key", "list"]) == 0
    out = capsys.readouterr().out
    assert SECRET not in out
    lines = {l.split("\t")[0]: l.split("\t") for l in out.strip().splitlines()}
    assert lines["openai"] == ["openai", "OpenAI", "Admin key", "saved"]
    assert lines["deepseek"][3] == "not saved" and lines["deepseek"][2] == "API key" and len(lines) == 4


def test_key_set_uses_getpass_and_delete(env, capsys, monkeypatch):
    store, _, _ = env
    monkeypatch.setattr("getpass.getpass", lambda prompt="": "  " + SECRET + "\n")
    assert m.main(["key", "set", "fal"]) == 0
    assert store.get("fal") == SECRET and SECRET not in capsys.readouterr().out
    assert m.main(["key", "delete", "fal"]) == 0 and store.get("fal") is None


def test_key_set_rejects_bad_input_and_unknown(env, capsys, monkeypatch):
    store, _, _ = env
    monkeypatch.setattr("getpass.getpass", lambda prompt="": "has space inside")
    assert m.main(["key", "set", "fal"]) == 1 and store.get("fal") is None
    assert "has space" not in capsys.readouterr().err
    assert m.main(["key", "set", "nope"]) == 2
    assert m.main(["key", "bogus"]) == 2 and m.main(["key"]) == 2


def test_anchor(env, capsys):
    store, _, tmp = env
    store.set("openai", "k2")
    assert m.main(["anchor", "openai", "$25.50"]) == 0
    out = capsys.readouterr().out
    assert "$25.50" in out and "$2.00" in out
    assert Ledger.load(tmp / "ledger.json").anchor("openai")["usd"] == 25.5


def test_anchor_warning_and_errors(env, capsys):
    store, http, tmp = env
    store.set("openai", "k2")
    http.add("https://api.openai.com/v1/organization/costs", (500, {}))
    assert m.main(["anchor", "openai", "10"]) == 0
    assert "warning: Could not read today's spend" in capsys.readouterr().out
    assert m.main(["anchor", "deepseek", "10"]) == 1       # reports its own balance
    assert m.main(["anchor", "openai", "abc"]) == 2 and m.main(["anchor", "openai", "-5"]) == 1
    assert m.main(["anchor", "openai"]) == 2


def test_collect_prints_line_per_provider(env, capsys):
    store, http, tmp = env
    for pid, k in ALL.items():
        store.set(pid, k)
    assert m.main(["collect"]) == 0
    out = capsys.readouterr().out.strip().splitlines()
    assert [l.split("\t")[0] for l in out] == ["deepseek", "openai", "fal", "anthropic"]
    assert out[1].split("\t")[1:] == ["ok", "balance n/a", "mtd $2.00"]
    assert "k1" not in "".join(out)
    assert Ledger.load(tmp / "ledger.json").samples["deepseek"]


def test_collect_exit_1_on_failure_with_error(env, capsys):
    store, http, _ = env
    for pid, k in ALL.items():
        store.set(pid, k)
    http.add("https://api.fal.ai/v1/account/billing", (500, {"error": {"type": "boom", "message": "LEAK"}}))
    assert m.main(["collect"]) == 1
    out = capsys.readouterr().out
    assert "fal\terror" in out and "HTTP 500 (boom)" in out and "LEAK" not in out


def test_collect_no_keys_is_not_a_failure(env, capsys):
    assert m.main(["collect"]) == 0
    assert "not_connected" in capsys.readouterr().out
    assert m.main(["collect", "extra"]) == 2


def test_config_flag_puts_the_ledger_beside_that_config(env, tmp_path):
    other = tmp_path / "elsewhere"
    other.mkdir()
    assert m.main(["anchor", "openai", "12.5", "--config", str(other / "config.toml")]) == 0
    assert Ledger.load(other / "ledger.json").anchor("openai")["usd"] == 12.5
    assert not (tmp_path / "ledger.json").exists()
    assert m.main(["anchor", "openai", "1", "--config"]) == 2
