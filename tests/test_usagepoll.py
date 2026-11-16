"""SPEND-22: the `claude -p /usage` poll: what is run, what is believed, and what is written."""

import io
import json
import os
import subprocess

import pytest

from fumes import config as cfgmod
from fumes import keystore, limits, statusline, ui, usagepoll

NOW = 1_800_000_000
REPORT = {"rate_limits": {"limits": [
    {"kind": "session", "percent": 24, "resets_at": "2027-01-15T10:00:00.4+00:00"},
    {"kind": "weekly_all", "percent": 14, "resets_at": "2027-01-20T07:00:00+00:00"},
    {"kind": "weekly_scoped", "percent": 11, "resets_at": "2027-01-20T07:00:00+00:00",
     "scope": {"model": {"display_name": "Fable"}, "surface": None}},
    {"kind": "weekly_scoped", "percent": 3, "resets_at": "2027-01-20T07:00:00+00:00",
     "scope": {"model": None, "surface": {"display_name": "Claude Code"}}}]}}


class FakeProc:
    def __init__(self, out):
        self.stdout = io.BytesIO(out)

    def wait(self, timeout=None):
        return 0

    def kill(self):
        pass


def fake_popen(report=REPORT, calls=None, raw=None):
    def popen(argv, **kw):
        if calls is not None:
            calls.append((argv, kw))
        lines = ["not json", json.dumps({"type": "system", "tools": []}),
                 json.dumps({"type": "assistant", "message": {"usage_report": report}})]
        return FakeProc(raw if raw is not None else "\n".join(lines).encode())
    return popen


def stored(tmp_path):
    return json.loads((tmp_path / statusline.FILE).read_text("utf-8"))


def test_it_runs_one_fixed_command_without_a_shell(tmp_path):
    calls = []
    assert usagepoll.poll(tmp_path, "/opt/claude", NOW, fake_popen(calls=calls)) is True
    (argv, kw), = calls
    assert argv == ["/opt/claude", "-p", "/usage", "--output-format", "stream-json", "--verbose",
                    "--no-session-persistence", "--strict-mcp-config", "--mcp-config", '{"mcpServers": {}}']
    assert kw["shell"] is False and kw["stdin"] == subprocess.DEVNULL and kw["cwd"] == str(tmp_path)


def test_the_answer_is_written_with_the_model_limit(tmp_path):
    usagepoll.poll(tmp_path, "/opt/claude", NOW, fake_popen())
    doc = stored(tmp_path)
    assert doc["five_hour"] == {"used_percentage": 24.0, "resets_at": 1800007200}
    assert doc["seven_day"]["used_percentage"] == 14.0 and doc["updated"] == "2027-01-15T08:00:00Z"
    assert doc["seven_day_models"] == {"Fable": {"used_percentage": 11.0, "resets_at": 1800428400}}   # not the surface row
    lim = limits.parse_limits(doc)                                    # and the strip can read it back
    assert lim.models[0][0] == "Fable" and lim.five_hour.used == 24.0


def test_a_lower_live_answer_replaces_a_frozen_reading(tmp_path):
    """SPEND-21: the weekly limit reset early; the status line's rule would keep the old 91 %."""
    f = tmp_path / statusline.FILE
    f.write_text(json.dumps({"seven_day": {"used_percentage": 91.0, "resets_at": 1800428400},
                             "seven_day_models": {"Fable": {"used_percentage": 100.0, "resets_at": 1800428400},
                                                  "Gone": {"used_percentage": 5.0, "resets_at": 1800428400}}}))
    assert usagepoll.poll(tmp_path, "/opt/claude", NOW, fake_popen()) is True
    doc = stored(tmp_path)
    assert doc["seven_day"]["used_percentage"] == 14.0 and list(doc["seven_day_models"]) == ["Fable"]
    assert usagepoll.poll(tmp_path, "/opt/claude", NOW + 60, fake_popen()) is False     # same answer: no rewrite
    assert stored(tmp_path)["updated"] == "2027-01-15T08:00:00Z"


@pytest.mark.parametrize("bad", [{}, {"rate_limits": None}, {"rate_limits": {"limits": "x"}},
                                 {"rate_limits": {"limits": [{"kind": "session"}]}},
                                 {"rate_limits": {"limits": [{"kind": "session", "percent": True,
                                                              "resets_at": "2027-01-15T10:00:00+00:00"}]}},
                                 {"rate_limits": {"limits": [{"kind": "session", "percent": float("nan"),
                                                              "resets_at": "2027-01-15T10:00:00+00:00"}]}},
                                 {"rate_limits": {"limits": [{"kind": "session", "percent": 5, "resets_at": "x" * 500}]}},
                                 {"rate_limits": {"limits": [{"kind": "session", "percent": 5,
                                                              "resets_at": "9999-01-01T00:00:00+00:00"}]}}])
def test_an_answer_without_usable_limits_leaves_the_file(tmp_path, bad):
    usagepoll.poll(tmp_path, "/opt/claude", NOW, fake_popen())
    before = (tmp_path / statusline.FILE).read_bytes()
    with pytest.raises(usagepoll.PollError):
        usagepoll.poll(tmp_path, "/opt/claude", NOW + 60, fake_popen(bad))
    assert (tmp_path / statusline.FILE).read_bytes() == before


@pytest.mark.parametrize("raw", [b"", b"\xff\xfe garbage", b"plain text, no report\n",
                                 b'{"usage_report": "not an object"}\n', b"x" * ((1 << 20) + 10)])
def test_output_that_is_not_a_report_is_an_error_not_a_crash(tmp_path, raw):
    with pytest.raises(usagepoll.PollError) as e:
        usagepoll.poll(tmp_path, "/opt/claude", NOW, fake_popen(raw=raw))
    assert not (tmp_path / statusline.FILE).exists() and "garbage" not in str(e.value)


def test_untrusted_values_are_bounded(tmp_path):
    rep = {"rate_limits": {"limits": [
        {"kind": "session", "percent": 10 ** 9, "resets_at": "2027-01-15T10:00:00+00:00"},
        {"kind": "weekly_all", "percent": -5, "resets_at": "2027-01-20T07:00:00+00:00"}] + [
        {"kind": "weekly_scoped", "percent": 1, "resets_at": "2027-01-20T07:00:00+00:00",
         "scope": {"model": {"display_name": f"M{i}" + "x" * 80}}} for i in range(9)] + [
        {"kind": "weekly_scoped", "percent": 1, "resets_at": "2027-01-20T07:00:00+00:00",
         "scope": {"model": {"display_name": "bad\nname"}}}, "junk", None]}}
    windows, models = usagepoll.reading(rep)
    assert windows["five_hour"]["used_percentage"] == 999.0 and windows["seven_day"]["used_percentage"] == 0.0
    assert len(models) == usagepoll.MAX_MODELS and all(len(n) <= usagepoll.MAX_MODEL_NAME for n in models)


def test_a_program_that_cannot_start_or_is_missing(tmp_path):
    def boom(argv, **kw):
        raise FileNotFoundError(argv[0])
    with pytest.raises(usagepoll.PollError, match="could not be started"):
        usagepoll.poll(tmp_path, "/nope/claude", NOW, boom)
    with pytest.raises(usagepoll.PollError, match="not signed in or not installed"):
        usagepoll.poll(tmp_path, None, NOW, fake_popen())            # conftest: nothing is installed in tests


def test_find_claude_never_looks_in_the_current_folder(tmp_path, monkeypatch):
    monkeypatch.undo()                                               # the real find_claude, not conftest's stub
    home, bindir, cwd = tmp_path / "home", tmp_path / "bin", tmp_path / "cwd"
    for d in (home / ".local" / "bin", bindir, cwd):
        d.mkdir(parents=True)
    trap = cwd / "claude"
    trap.write_text("#!/bin/sh\n")
    trap.chmod(0o755)
    monkeypatch.chdir(cwd)
    env = {"PATH": os.pathsep.join(["", ".", "relative/dir", str(bindir)])}
    assert usagepoll.find_claude(env, home, "linux") is None
    real = bindir / "claude"
    real.write_text("#!/bin/sh\n")
    if os.name != "nt":                                              # Windows has no executable bit to lack
        assert usagepoll.find_claude(env, home, "linux") is None    # not executable
    real.chmod(0o755)
    assert usagepoll.find_claude(env, home, "linux") == str(real)
    own = home / ".local" / "bin" / "claude"
    own.write_text("#!/bin/sh\n")
    own.chmod(0o755)
    assert usagepoll.find_claude(env, home, "linux") == str(own)    # the user's own install wins
    # Windows: only a real claude.exe, never the npm .cmd shim (it would run through cmd.exe)
    (bindir / "claude.cmd").write_text("@echo off\n")
    assert usagepoll.find_claude({"PATH": str(bindir)}, tmp_path / "nohome", "win32") is None
    (bindir / "claude.exe").write_bytes(b"MZ")
    assert usagepoll.find_claude({"PATH": f'"{bindir}"'}, tmp_path / "nohome", "win32") == str(bindir / "claude.exe")


def app(tmp_path, **kw):
    return ui.App(cfgmod.Config(**kw), cfgmod.State(tmp_path / "s.json"), user_logo_dir=tmp_path,
                  store=keystore.MemoryStore())


def test_the_app_polls_only_for_local_limits(qapp, tmp_path, monkeypatch):
    started = []

    class FakeThread:
        def __init__(self, folder, use_signin=True):
            started.append((folder, use_signin))
            self.done = type("S", (), {"connect": lambda self, fn: None})()

        def isRunning(self):  # noqa: N802
            return True

        def start(self):
            pass

    monkeypatch.setattr(ui, "UsagePollThread", FakeThread)
    a = app(tmp_path)
    a.poll_usage()
    assert not started and "not signed in or not installed" in a.poll_error   # nothing here: quiet, no thread
    monkeypatch.setattr(usagepoll, "find_claude", lambda *x, **k: "/opt/claude")
    a.poll_usage()
    a.poll_usage()                                                   # one at a time
    assert started == [(tmp_path, True)]
    assert a.poll_timer.isActive() and a.poll_timer.interval() == 5 * 60_000
    for kw in ({"limits_url": "http://x.example/l.json"}, {"limits_poll_minutes": 0}, {"show_limits": False}):
        started.clear()
        b = app(tmp_path, **kw)
        b.poll_usage()
        assert not started, kw
    started.clear()
    app(tmp_path, limits_use_signin=False).poll_usage()
    assert started == [(tmp_path, False)]


def test_a_poll_answer_reaches_the_strip(qapp, tmp_path):
    a = app(tmp_path)
    usagepoll.poll(tmp_path, "/opt/claude", NOW - 86400 * 60, fake_popen())
    a._usage_polled(True, "")
    assert a.strip.limits.models[0][0] == "Fable" and a.poll_error == ""
    a._usage_polled(False, "Claude Code took longer than 120 s")
    assert a.poll_error.startswith("Claude Code took") and a.strip.limits is not None   # the reading stays


def test_config_accepts_zero_and_sane_intervals():
    assert cfgmod.parse_config({"limits_poll_minutes": 0})[0].limits_poll_minutes == 0
    assert cfgmod.parse_config({"limits_poll_minutes": 15})[0].limits_poll_minutes == 15
    for bad in (-1, 0.2, 5000, "5", True):
        cfg, warns = cfgmod.parse_config({"limits_poll_minutes": bad})
        assert cfg.limits_poll_minutes == 5 and warns, bad


# ---- the direct way: Claude Code's sign-in and the usage endpoint -----------------------------------
TOKEN = "sk-ant-oat01-" + "T" * 60
BODY = {"five_hour": {"utilization": 30.0}, "limits": REPORT["rate_limits"]["limits"], "extra": {"deep": [1, 2]}}


class FakeHttp:
    def __init__(self, status=200, body=None, error=None):
        self.status, self.body, self.error, self.calls = status, BODY if body is None else body, error, []

    def get(self, url, headers):
        self.calls.append((url, dict(headers)))
        if self.error:
            raise self.error
        return self.status, self.body


def signin(tmp_path, monkeypatch, token=TOKEN, expires_in=3600, now=NOW, **extra):
    f = tmp_path / "creds" / ".credentials.json"
    f.parent.mkdir(exist_ok=True)
    f.write_text(json.dumps({"mcpOAuth": {"x": "y"}, "claudeAiOauth": dict(
        {"accessToken": token, "refreshToken": "sk-ant-ort01-SECRETREFRESH", "expiresAt": (now + expires_in) * 1000},
        **extra)}), "utf-8")
    monkeypatch.setattr(usagepoll, "credentials_path", lambda *a, **k: f)
    monkeypatch.setattr(usagepoll.time, "time", lambda: now)
    return f


def test_direct_sends_the_token_to_anthropic_only_and_records_the_answer(tmp_path, monkeypatch):
    signin(tmp_path, monkeypatch)
    http, started = FakeHttp(), []
    assert usagepoll.poll(tmp_path, None, NOW, lambda *a, **k: started.append(a), http=http) is True
    (url, headers), = http.calls
    assert url == "https://api.anthropic.com/api/oauth/usage" and not started          # Claude Code is not run
    assert headers == {"Authorization": "Bearer " + TOKEN, "anthropic-beta": "oauth-2025-04-20"}
    doc = stored(tmp_path)
    assert doc["five_hour"]["used_percentage"] == 24.0 and list(doc["seven_day_models"]) == ["Fable"]
    assert TOKEN not in json.dumps(doc) and "SECRETREFRESH" not in json.dumps(doc)       # nothing of it is kept
    assert sorted(p.name for p in tmp_path.iterdir()) == ["claude-limits.json", "creds"]


@pytest.mark.parametrize("http", [FakeHttp(401, {"error": {"message": "token " + TOKEN}}), FakeHttp(403, "no"),
                                  FakeHttp(500, "oops " + TOKEN), FakeHttp(200, "not json"), FakeHttp(200, {"limits": []}),
                                  FakeHttp(200, {"limits": "x"}), FakeHttp(302, "")])
def test_a_refused_or_odd_answer_falls_back_to_asking_claude_code(tmp_path, monkeypatch, http):
    signin(tmp_path, monkeypatch)
    calls = []
    assert usagepoll.poll(tmp_path, "/opt/claude", NOW, fake_popen(calls=calls), http=http) is True
    assert len(calls) == 1 and stored(tmp_path)["seven_day"]["used_percentage"] == 14.0
    assert all(TOKEN not in a for argv, kw in calls for a in argv) and "env" not in calls[0][1]


def test_with_no_other_way_the_error_never_carries_the_token_or_the_servers_words(tmp_path, monkeypatch):
    from fumes.collect.http import ProviderError
    signin(tmp_path, monkeypatch)
    for http in (FakeHttp(401, {"error": TOKEN}), FakeHttp(500, "leak " + TOKEN),
                 FakeHttp(error=ProviderError("api.anthropic.com: " + TOKEN))):
        with pytest.raises(usagepoll.PollError) as e:
            usagepoll.poll(tmp_path, None, NOW, fake_popen(), http=http)
        assert TOKEN not in str(e.value) and "leak" not in str(e.value)
    assert not (tmp_path / statusline.FILE).exists()


def test_an_expired_missing_or_malformed_signin_is_not_used(tmp_path, monkeypatch):
    http = FakeHttp()
    for kw in ({"expires_in": -10}, {"expires_in": 60}, {"token": "short"}, {"token": "a" * 5000},
               {"token": "sk-ant-oat01-with space" + "x" * 30}, {"token": "sk-ant-oat01-line\r\nX-Evil: 1" + "x" * 30},
               {"token": 12345}, {"token": "sk-ant-oat01-" + "é" * 30}):
        f = signin(tmp_path, monkeypatch, **kw)
        assert usagepoll.signin_token() is None, kw
        assert usagepoll.available() is None
    for body in ("", "not json", "[]", '{"claudeAiOauth": "x"}', '{"claudeAiOauth": {"accessToken": "%s"}}' % TOKEN,
                 '{"claudeAiOauth": {"accessToken": "%s", "expiresAt": true}}' % TOKEN):
        f.write_text(body, "utf-8")
        assert usagepoll.signin_token() is None, body
    f.unlink()
    assert usagepoll.signin_token() is None
    with pytest.raises(usagepoll.PollError):
        usagepoll.poll(tmp_path, None, NOW, fake_popen(), http=http)
    assert not http.calls                                              # no token: no request at all
    signin(tmp_path, monkeypatch)
    assert usagepoll.signin_token() == TOKEN and usagepoll.available() == "signin"


def test_the_signin_can_be_turned_off(tmp_path, monkeypatch):
    signin(tmp_path, monkeypatch)
    http, calls = FakeHttp(), []
    assert usagepoll.available(use_signin=False) is None
    usagepoll.poll(tmp_path, "/opt/claude", NOW, fake_popen(calls=calls), use_signin=False, http=http)
    assert not http.calls and len(calls) == 1
    assert cfgmod.parse_config({"limits_use_signin": False})[0].limits_use_signin is False
    assert cfgmod.parse_config({"limits_use_signin": "no"})[0].limits_use_signin is True


def test_credentials_path_follows_claude_code(tmp_path, monkeypatch):
    monkeypatch.undo()
    assert usagepoll.credentials_path({}, tmp_path) == tmp_path / ".claude" / ".credentials.json"
    assert usagepoll.credentials_path({"CLAUDE_CONFIG_DIR": str(tmp_path / "cc")}, tmp_path) == \
        tmp_path / "cc" / ".credentials.json"
    assert usagepoll.credentials_path({"CLAUDE_CONFIG_DIR": "relative/dir"}, tmp_path) == \
        tmp_path / ".claude" / ".credentials.json"


def test_the_real_http_client_refuses_redirects_and_plain_http():
    """The token rides on collect.http: HTTPS only and no redirects, so it cannot be sent anywhere else."""
    from fumes.collect import http as httpmod
    assert usagepoll.USAGE_URL.startswith("https://api.anthropic.com/")
    with pytest.raises(httpmod.ProviderError):
        httpmod.Http().get("http://api.anthropic.com/api/oauth/usage", {})
    assert httpmod._NoRedirect().redirect_request(None, None, 302, "", {}, "https://evil.example/") is None
