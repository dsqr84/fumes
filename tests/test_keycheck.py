"""A key that is clearly another provider's is never saved or sent: Settings, the CLI, collection, anchors."""
import pytest

from fakes import FakeHttp
from fumes import __main__ as m
from fumes import collect, keycheck, keystore
from fumes.collect.ledger import Ledger
from test_collect import NOW, routes

ANT = "sk-ant-admin01-" + "a" * 40
ANT_WS = "sk-ant-api03-" + "b" * 40
OA_ADMIN = "sk-admin-" + "c" * 40
OA_PROJ = "sk-proj-" + "d" * 40
OA_LEGACY = "sk-" + "e" * 20 + "T3BlbkFJ" + "f" * 20
DS = "sk-" + "0123456789abcdef" * 2


@pytest.mark.parametrize("key,who", [(ANT, "anthropic"), (ANT_WS, "anthropic"), (OA_ADMIN, "openai"),
                                     (OA_PROJ, "openai"), (OA_LEGACY, "openai"), (DS, "deepseek"),
                                     ("12345678-aaaa-bbbb-cccc-1234567890ab:secret", None), ("sk-short", None),
                                     ("", None), (None, None)])
def test_owner_only_for_unmistakable_formats(key, who):
    assert keycheck.owner(key) == who


def test_foreign_message_names_both_and_never_the_key():
    msg = keycheck.foreign("deepseek", ANT)
    assert "Anthropic" in msg and "DeepSeek" in msg and ANT not in msg and "not saved or sent" in msg
    assert keycheck.foreign("anthropic", ANT) is None and keycheck.foreign("fal", "unknown-format") is None


def test_kind_hints():
    assert "Admin key" in keycheck.kind_hint("openai", OA_PROJ)
    assert "Admin key" in keycheck.kind_hint("openai", OA_LEGACY)
    assert "sk-ant-admin" in keycheck.kind_hint("anthropic", ANT_WS)
    assert keycheck.kind_hint("openai", OA_ADMIN) is None and keycheck.kind_hint("anthropic", ANT) is None


def test_collect_never_sends_a_foreign_key(tmp_path):
    http = FakeHttp(routes())
    store = keystore.MemoryStore({"deepseek": ANT, "openai": OA_ADMIN})
    pay, failed = collect.collect(NOW, store, Ledger(tmp_path / "l.json"), http, enabled={"deepseek", "openai"})
    sent_to_deepseek = [h for u, h in http.calls if "deepseek" in u]
    assert sent_to_deepseek == [] and failed == []
    assert pay["providers"]["deepseek"]["status"] == "not_connected"
    assert "Anthropic" in pay["providers"]["deepseek"]["note"] and ANT not in str(pay)
    assert pay["providers"]["openai"]["status"] == "ok"          # the right key still goes to its own provider
    assert all(ANT not in str(h) for _, h in http.calls)


def test_set_anchor_does_not_send_a_foreign_key(tmp_path):
    http = FakeHttp(routes())
    store = keystore.MemoryStore({"openai": ANT})
    rec, warning = collect.set_anchor("openai", 20, NOW, store, Ledger(tmp_path / "l.json"), http)
    assert http.calls == [] and rec["usd"] == 20 and "another provider" in warning


def test_cli_refuses_a_foreign_key(tmp_path, monkeypatch, capsys):
    store = keystore.MemoryStore()
    monkeypatch.setattr(m, "make_store", lambda: store)
    monkeypatch.setattr("fumes.config.config_dir", lambda: tmp_path)
    monkeypatch.setattr("getpass.getpass", lambda prompt="": OA_ADMIN)
    assert m.main(["key", "set", "anthropic"]) == 2 and store.get("anthropic") is None
    err = capsys.readouterr().err
    assert "OpenAI" in err and OA_ADMIN not in err
    monkeypatch.setattr("getpass.getpass", lambda prompt="": OA_PROJ)
    assert m.main(["key", "set", "openai"]) == 0 and store.get("openai") == OA_PROJ
    assert "Admin key" in capsys.readouterr().err                   # saved, with the kind hint
