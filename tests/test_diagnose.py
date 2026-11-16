import json
import re
from datetime import datetime, timezone
from pathlib import Path

import pytest

from fumes import __main__ as m
from fumes import config as cfgmod
from fumes import diagnose, keystore, statusline
from fumes.collect.ledger import Ledger

ROOT = Path(__file__).parent.parent
DEEPSEEK = "sk-" + "ab12" * 8                    # a real-looking DeepSeek key
ANTHROPIC_KEY = "sk-ant-api03-DISTINCTIVEANTHROPICVALUE"   # not an Admin key, and foreign to OpenAI
OPENAI_PROJ = "sk-proj-DISTINCTIVEOPENAIPROJECTVALUE"
FAL = "fal-DISTINCTIVEFALVALUE-0123"
CMDKEY = "cmd-DISTINCTIVECOMMANDVALUE"
SECRETS = (DEEPSEEK, ANTHROPIC_KEY, OPENAI_PROJ, FAL, CMDKEY, "DISTINCTIVE")
NOW = datetime(2026, 11, 13, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def env(tmp_path, monkeypatch):
    store = keystore.MemoryStore()
    home = tmp_path / "home"
    home.mkdir()
    cfg = tmp_path / "cfg"
    cfg.mkdir()
    monkeypatch.setattr(cfgmod, "config_dir", lambda: cfg)
    monkeypatch.setattr(m, "make_store", lambda: store)
    monkeypatch.setattr(m, "_now", lambda: NOW)
    monkeypatch.setattr(diagnose, "claude_settings_path", lambda: home / "settings.json")
    monkeypatch.setattr("fumes.win.autostart_enabled", lambda: False)
    store.cfg, store.home = cfg, home
    return store


def run(capsys, *argv):
    code = m.main(list(argv))
    cap = capsys.readouterr()
    return code, cap.out, cap.err


def checks(capsys, *argv):
    code, out, err = run(capsys, "doctor", "--json", *argv)
    return code, {c["check"]: c for c in json.loads(out)["checks"]}, json.loads(out)


def put_providers(env, specs):
    (env.cfg / "providers.json").write_text(json.dumps({"providers": specs}))


# ------------------------------------------------------------- setup-prompt --
def test_prompt_file_equals_doc_block():
    doc = (ROOT / "docs" / "SETUP-WITH-AI.md").read_text("utf-8")
    block = re.search(r"## The prompt\n\n```text\n(.*?)\n```", doc, re.S).group(1)
    assert (ROOT / "fumes/guides/setup-prompt.txt").read_text("utf-8") == block + "\n"


def test_setup_prompt_substitutes_the_install_folder(env, capsys):
    code, out, _ = run(capsys, "setup-prompt")
    assert code == 0
    assert diagnose.PLACEHOLDER not in out and "<FOLDER" not in out
    assert f"It is installed at: {diagnose.install_root()}." in out
    assert Path(diagnose.install_root()) == ROOT
    assert out == diagnose.setup_prompt()


def test_setup_prompt_rejects_arguments(env, capsys):
    assert run(capsys, "setup-prompt", "--json")[0] == 2
    assert run(capsys, "status", "extra")[0] == 2


# ------------------------------------------------------------------ status --
def seed(env):
    env.set("deepseek", DEEPSEEK)
    env.set("openai", ANTHROPIC_KEY)          # foreign
    env.set("anthropic", "sk-ant-api03-WORKSPACEKEYDISTINCTIVE")   # right provider, wrong kind
    env.set("fal", FAL)
    put_providers(env, [
        {"id": "prepaid", "name": "Prepaid", "kind": "manual"},
        {"id": "acme", "name": "Acme", "kind": "command", "argv": ["x"], "key": True},
        {"id": "free", "name": "Free", "kind": "command", "argv": ["x"]},
        {"id": "deepseek", "name": "Dup", "kind": "manual"},
        {"id": "Bad Id", "name": "Bad", "kind": "manual"},
    ])


def test_status_json_shape(env, capsys):
    seed(env)
    env.set("acme", CMDKEY)
    led = Ledger(env.cfg / "ledger.json")
    led.set_anchor("prepaid", 12.5, datetime(2026, 11, 1, tzinfo=timezone.utc))
    led.save()
    cache = {"providers": {"deepseek": {"status": "ok", "balance": 4.15, "mtd": 1.0, "checked": "2026-11-13T10:00:00Z",
                                        "error": None, "note": "ignored"}}}
    (env.cfg / "state.json").write_text(json.dumps({"cache": cache}))
    (env.cfg / "claude-limits.json").write_text(json.dumps({
        "updated": "2026-11-13T11:00:00Z", "five_hour": {"used_percentage": 23, "resets_at": 1900000000}}))
    code, out, err = run(capsys, "status", "--json")
    assert code == 0
    doc = json.loads(out)
    assert set(doc) == {"version", "config_folder", "mode", "providers", "custom_errors", "limits"}
    assert doc["mode"] == "local" and doc["config_folder"] == str(env.cfg)
    by = {p["id"]: p for p in doc["providers"]}
    assert list(by) == ["deepseek", "openai", "fal", "anthropic", "prepaid", "acme", "free"]
    assert all(set(p) == {"id", "name", "kind", "key", "key_problem", "credit_entered", "last"} for p in by.values())
    assert by["deepseek"]["kind"] == "builtin" and by["deepseek"]["key"] == "saved"
    assert by["deepseek"]["last"] == {"status": "ok", "balance": 4.15, "mtd": 1.0, "checked": "2026-11-13T10:00:00Z",
                                      "error": None}
    assert "foreign" not in str(by["openai"]) and "Anthropic key" in by["openai"]["key_problem"]
    assert "Admin key" in by["anthropic"]["key_problem"]
    assert by["fal"]["key_problem"] is None and by["fal"]["credit_entered"] is None
    assert by["prepaid"]["kind"] == "manual" and by["prepaid"]["key"] == "not_needed"
    assert by["prepaid"]["credit_entered"] == {"usd": 12.5, "date": "2026-11-01"}
    assert by["acme"]["kind"] == "command" and by["acme"]["key"] == "saved"
    assert by["free"]["key"] == "not_needed" and by["free"]["last"] is None
    assert len(doc["custom_errors"]) == 2 and any("already used" in e for e in doc["custom_errors"])
    assert doc["limits"]["statusline"] == "missing"
    assert doc["limits"]["reading"] == {"five_hour_used": 23, "seven_day_used": None, "updated": "2026-11-13T11:00:00Z"}


def test_status_remote_mode_and_limits_cache(env, capsys):
    (env.cfg / "config.toml").write_text('url = "https://example.com/feed.json"\n')
    (env.cfg / "state.json").write_text(json.dumps({"limits_cache": {
        "updated": "2026-11-13T09:00:00Z", "seven_day": {"used_percentage": 41, "resets_at": 1900000000}}}))
    doc = json.loads(run(capsys, "status", "--json")[1])
    assert doc["mode"] == "remote"
    assert doc["limits"]["reading"]["seven_day_used"] == 41


def test_status_plain_is_a_table(env, capsys):
    seed(env)
    code, out, _ = run(capsys, "status")
    assert code == 0 and out.splitlines()[1].split()[:3] == ["provider", "kind", "key"]
    assert "deepseek" in out and "rejected:" in out


@pytest.mark.parametrize("argv", [("status",), ("status", "--json"), ("doctor",), ("doctor", "--json"),
                                  ("setup-prompt",)])
def test_no_command_ever_prints_a_key(env, capsys, argv):
    seed(env)
    env.set("acme", CMDKEY)
    (env.home / "settings.json").write_text(json.dumps({"statusLine": {"command": "echo " + DEEPSEEK}}))
    code, out, err = run(capsys, *argv)
    for s in SECRETS:
        assert s not in out and s not in err


def test_nothing_of_a_key_leaks_even_in_pieces(env, capsys):
    seed(env)
    for argv in (("status", "--json"), ("doctor", "--json")):
        _, out, err = run(capsys, *argv)
        assert "ab12ab12" not in out + err and "DISTINCTIVE" not in out + err


# ------------------------------------------------------------------ doctor --
def test_doctor_sample_states_and_exit(env, capsys):
    env.set("deepseek", DEEPSEEK)
    env.set("openai", ANTHROPIC_KEY)
    code, out, _ = run(capsys, "doctor")
    lines = out.splitlines()
    assert code == 1
    assert [l[:2] for l in lines if " deepseek:" in l] == ["ok"]
    assert [l[:2] for l in lines if " openai:" in l] == ["!!"]
    assert [l[:2] for l in lines if " fal:" in l] == ["--"]
    assert lines[-1] == "1 problem to fix."


def test_doctor_all_clear_exits_zero(env, capsys):
    env.set("deepseek", DEEPSEEK)
    (env.home / "settings.json").write_text(json.dumps({"statusLine": {"command": "py -c fumes.statusline"}}))
    code, out, _ = run(capsys, "doctor")
    assert code == 0 and out.splitlines()[-1] == "Nothing needs fixing."


def test_doctor_missing_key_is_info_and_command_key_is_fix(env, capsys):
    put_providers(env, [{"id": "acme", "name": "Acme", "kind": "command", "argv": ["x"], "key": True}])
    code, c, _ = checks(capsys)
    assert c["fal"]["state"] == "info" and c["fal"]["message"].startswith("fal.ai (fal): no key saved")
    assert c["acme"]["state"] == "fix" and "fumes key set acme" in c["acme"]["message"]
    assert code == 1
    env.set("acme", CMDKEY)
    assert checks(capsys)[1]["acme"]["state"] == "ok"


def test_doctor_foreign_and_wrong_kind(env, capsys):
    env.set("openai", ANTHROPIC_KEY)
    env.set("anthropic", "sk-ant-api03-WORKSPACE")
    _, c, _ = checks(capsys)
    assert c["openai"]["state"] == "fix" and "Anthropic key" in c["openai"]["message"]
    assert c["anthropic"]["state"] == "fix" and "Admin key" in c["anthropic"]["message"]
    env.set("openai", OPENAI_PROJ)
    assert "project key" in checks(capsys)[1]["openai"]["message"]


def test_doctor_anchored_without_credit(env, capsys):
    env.set("openai", "sk-admin-GOODADMINKEY")
    _, c, _ = checks(capsys)
    assert c["openai"]["state"] == "fix" and "`fumes anchor openai <usd>`" in c["openai"]["message"]
    led = Ledger(env.cfg / "ledger.json")
    led.set_anchor("openai", 20, NOW)
    led.save()
    c = checks(capsys)[1]
    assert c["openai"]["state"] == "ok" and "$20.00" in c["openai"]["message"]


def test_doctor_manual_provider_needs_a_balance(env, capsys):
    put_providers(env, [{"id": "prepaid", "name": "Prepaid", "kind": "manual"}])
    assert checks(capsys)[1]["prepaid"]["state"] == "fix"
    led = Ledger(env.cfg / "ledger.json")
    led.set_anchor("prepaid", 5, NOW)
    led.save()
    assert checks(capsys)[1]["prepaid"]["state"] == "ok"


def test_doctor_bad_providers_json_entry(env, capsys):
    put_providers(env, [{"id": "ok1", "name": "Fine", "kind": "manual"}, {"id": "bad", "name": "X", "kind": "nope"},
                        "junk", {"id": "ok1", "name": "Again", "kind": "manual"}])
    code, c, doc = checks(capsys)
    errs = [x for x in doc["checks"] if x["check"] == "providers.json"]
    assert len(errs) == 3 and all(x["state"] == "fix" for x in errs)
    assert any("kind must be manual or command" in x["message"] for x in errs)
    assert any("already used" in x["message"] for x in errs)
    assert code == 1


def test_doctor_claude_limits_never_offers_a_status_line(env, capsys, monkeypatch):
    s = env.home / "settings.json"
    code, c, doc = checks(capsys)
    assert c["claude-limits"]["state"] == "info" and "was not found" in c["claude-limits"]["message"]
    assert doc["snippet"] is None and code == 0
    _, out, _ = run(capsys, "doctor")
    assert "statusLine" not in out and "{" not in out
    s.write_text(json.dumps({"statusLine": {"type": "command", "command": "other-tool"}}))     # the user's own
    _, c, doc = checks(capsys)
    assert c["claude-limits"]["state"] == "info" and doc["snippet"] is None
    monkeypatch.setattr("fumes.usagepoll.find_claude", lambda *a, **k: "/opt/claude")
    (env.cfg / statusline.FILE).write_text(json.dumps({
        "updated": "2026-11-13T09:00:00Z", "five_hour": {"used_percentage": 5, "resets_at": 1900000000}}))
    _, c, doc = checks(capsys)
    assert c["claude-limits"]["state"] == "ok" and "asks Claude Code" in c["claude-limits"]["message"]
    assert "3 h ago" in c["claude-limits"]["message"] and doc["snippet"] is None
    monkeypatch.setattr("fumes.usagepoll.find_claude", lambda *a, **k: None)
    s.write_text(json.dumps({"statusLine": {"type": "command", "command": "py -m fumes.statusline"}}))
    _, c, _ = checks(capsys)                                       # one already set up keeps working, and is reported
    assert c["claude-limits"]["state"] == "ok" and "status line records" in c["claude-limits"]["message"]
    (env.cfg / "config.toml").write_text('limits_url = "http://z.example/l.json"\n')
    _, c, _ = checks(capsys)
    assert c["claude-limits"]["state"] == "ok" and "limits_url" in c["claude-limits"]["message"]


def test_statusline_state_reads_untrusted_json(tmp_path):
    p = tmp_path / "s.json"
    for body, want in [('{"statusLine": "str"}', "missing"), ("[1]", "missing"), ('{"statusLine": {}}', "missing"),
                       ('{"statusLine": {"command": 5}}', "other"),
                       ('{"statusLine": {"command": "my-fumes-tool --x"}}', "other"),
                       ('{"statusLine": {"command": "py -m fumes.statusline"}}', "configured"),
                       ('{"statusLine": {"command": "\\"C:\\\\Apps\\\\Fumes\\\\fumes-cli.exe\\" statusline"}}', "configured"),
                       ('{"statusLine": {"command": "fumes-cli-helper --other"}}', "other"), ('{"x": 1}', "missing")]:
        p.write_text(body)
        assert diagnose.statusline_state(p) == want
    assert diagnose.statusline_state(tmp_path / "absent.json") == "missing"


def test_doctor_remote_mode(env, capsys):
    (env.cfg / "config.toml").write_text('url = "https://example.com/feed.json"\n')
    _, c, _ = checks(capsys)
    assert c["mode"]["state"] == "info" and "not used" in c["mode"]["message"]
    assert c["config"]["state"] == "ok"


def test_doctor_bad_config_is_fix(env, capsys):
    (env.cfg / "config.toml").write_text("this is [not toml")
    code, c, _ = checks(capsys)
    assert c["config"]["state"] == "fix" and code == 1
    (env.cfg / "config.toml").write_text("refresh_minutes = -3\n")
    assert checks(capsys)[1]["config"]["state"] == "fix"


def test_doctor_json_lists_every_check_with_valid_states(env, capsys):
    _, _, doc = checks(capsys)
    assert {c["state"] for c in doc["checks"]} <= {"ok", "fix", "info"}
    assert all(set(c) == {"check", "state", "message"} for c in doc["checks"])


def test_doctor_config_flag_uses_that_folder(env, capsys, tmp_path):
    other = tmp_path / "other"
    other.mkdir()
    (other / "providers.json").write_text(json.dumps({"providers": [{"id": "zed", "name": "Zed", "kind": "manual"}]}))
    _, c, _ = checks(capsys, "--config", str(other / "config.toml"))
    assert "zed" in c and "prepaid" not in c
