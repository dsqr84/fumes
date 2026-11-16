"""The Claude subscription limits for this computer, with nothing for the user to set up. No Qt.

Two ways, tried in this order by poll():

1. Directly (`direct`): one HTTPS GET of the usage endpoint Claude Code itself calls, with the sign-in
   Claude Code keeps on this computer (~/.claude/.credentials.json). Fast (a fraction of a second) and it
   works with any Claude Code install. The token is read for that one request and nothing else: never
   written, logged, refreshed or sent to any host but api.anthropic.com, over HTTPS with no redirects
   (collect.http). An expired or missing token is not an error, just a reason to use the second way.
   `limits_use_signin = false` turns this off.
2. By asking Claude Code (`ask`): run `claude -p /usage` and read its report, described below. It needs
   no token here, and running it renews Claude Code's sign-in, so the direct way works again afterwards.

Both give the same rows, so reading() and record() serve both. The endpoint is undocumented and the
report's shape is marked experimental: either may change, and then the limits file is left as it was.

Asking Claude Code:

The status line (statusline.py) only reports the 5-hour and weekly windows, only while a Claude Code
session is drawing its status row, and never lowers a reading. `/usage` sent as a prompt is a local
command (no model call, no cost, no session file) whose structured report also carries the per-model
weekly limits (e.g. Fable), and it is the account's live answer. So when Claude Code is installed on
this computer, Fumes asks it on a timer and writes the answer over the readings in claude-limits.json:
downwards too, because a window can be reset early.

What is run: the `claude` program found in the user's own install folder or on PATH (never the current
folder), with a fixed argument list, no shell, no console window, a time limit and a cap on its output.
On Windows only a real `claude.exe` is run: a `.cmd` shim would go through cmd.exe. Its output is
untrusted: every number is checked here and again by limits.parse_limits when the file is read. The
report's shape is marked experimental, so an answer without usable limits leaves the file as it was.
"""

import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

from . import statusline
from .collect.custom import MAX_OUTPUT, _read_bounded

USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
OAUTH_BETA = "oauth-2025-04-20"
MAX_CREDENTIALS = 4 * 1024 * 1024     # the file also holds other sign-ins (MCP servers); a few hundred KB
MAX_TOKEN = 4096
EXPIRY_MARGIN = 120                    # seconds: a token about to expire is left for Claude Code to renew

ARGS = ("-p", "/usage", "--output-format", "stream-json", "--verbose", "--no-session-persistence",
        "--strict-mcp-config", "--mcp-config", '{"mcpServers": {}}')
TIMEOUT = 120
KINDS = {"session": "five_hour", "weekly_all": "seven_day"}
MAX_MODELS, MAX_MODEL_NAME, MAX_LINES = 4, 24, 2000
MAX_RESETS_AT = 4102444800      # 2100-01-01, as in limits.py


class PollError(Exception):
    """The poll did not produce a reading. The message is short and never holds program output."""


def find_claude(env=None, home=None, platform=None):
    """The absolute path of the Claude Code program, or None. Looked for in ~/.local/bin (the native
    installer's place) and then in each absolute PATH folder; the current folder is never searched."""
    env = os.environ if env is None else env
    platform = platform or sys.platform
    names = ("claude.exe",) if platform == "win32" else ("claude",)
    folders = [Path(home) if home else Path.home()]
    folders[0] = folders[0] / ".local" / "bin"
    for raw in (env.get("PATH") or "").split(os.pathsep):
        raw = raw.strip().strip('"')
        if raw and os.path.isabs(raw):
            folders.append(Path(raw))
    for folder in folders:
        for name in names:
            f = folder / name
            try:
                if f.is_file() and (platform == "win32" or os.access(f, os.X_OK)):
                    return str(f)
            except OSError:
                continue
    return None


def credentials_path(env=None, home=None):
    """Where Claude Code keeps its sign-in on Windows and Linux (macOS uses the Keychain: not read)."""
    env = os.environ if env is None else env
    base = env.get("CLAUDE_CONFIG_DIR")
    folder = Path(base) if base and os.path.isabs(base) else (Path(home) if home else Path.home()) / ".claude"
    return folder / ".credentials.json"


def signin_token(path=None, now=None):
    """Claude Code's current access token, or None when there is none, it is unreadable, or it has (nearly)
    expired. Read as untrusted data. The token is returned to the caller for one request and kept nowhere."""
    path = Path(path) if path else credentials_path()
    try:
        with open(path, "rb") as f:
            data = f.read(MAX_CREDENTIALS + 1)
        if len(data) > MAX_CREDENTIALS:
            return None
        doc = json.loads(data.decode("utf-8"))
    except (OSError, ValueError, RecursionError):
        return None
    oauth = doc.get("claudeAiOauth") if isinstance(doc, dict) else None
    if not isinstance(oauth, dict):
        return None
    token, exp = oauth.get("accessToken"), oauth.get("expiresAt")
    if not isinstance(token, str) or not 20 <= len(token) <= MAX_TOKEN:
        return None
    if not token.isascii() or not token.isprintable() or any(c.isspace() for c in token):
        return None                                   # it goes into a header: nothing that could split one
    if isinstance(exp, bool) or not isinstance(exp, (int, float)):
        return None
    now = time.time() if now is None else now
    return token if exp / 1000.0 - EXPIRY_MARGIN > now else None


def direct(token, http=None):
    """The usage endpoint's answer as a report reading() understands. Raises PollError; the message never
    holds the token or anything the server said."""
    from .collect.http import Http, ProviderError
    try:
        status, body = (http or Http()).get(USAGE_URL, {"Authorization": "Bearer " + token,
                                                        "anthropic-beta": OAUTH_BETA})
    except ProviderError as e:
        raise PollError("the usage endpoint could not be reached") from e
    if status in (401, 403):
        raise PollError("the usage endpoint refused Claude Code's sign-in")
    if status != 200 or not isinstance(body, dict):
        raise PollError(f"the usage endpoint answered {status if isinstance(status, int) else 'oddly'}")
    return {"rate_limits": {"limits": body.get("limits")}}


def _epoch(ts):
    if not isinstance(ts, str) or len(ts) > 64:
        return None
    try:
        v = int(datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp())
    except (ValueError, OverflowError, OSError):
        return None
    return v if 0 < v <= MAX_RESETS_AT else None


def _row(r):
    """(used percent, resets_at) from one limits row, or None."""
    pct, at = r.get("percent"), _epoch(r.get("resets_at"))
    if isinstance(pct, bool) or not isinstance(pct, (int, float)) or at is None:
        return None
    if pct != pct or pct in (float("inf"), float("-inf")):
        return None
    return {"used_percentage": round(max(0.0, min(float(pct), 999.0)), 1), "resets_at": at}


def reading(report):
    """({'five_hour': w, 'seven_day': w}, {model name: w}) from a usage_report; both empty if it has none."""
    rate = report.get("rate_limits") if isinstance(report, dict) else None
    rows = rate.get("limits") if isinstance(rate, dict) else None
    windows, models = {}, {}
    for r in rows if isinstance(rows, list) else []:
        if not isinstance(r, dict):
            continue
        kind, w = r.get("kind"), _row(r)
        if w is None:
            continue
        if kind in KINDS:
            windows[KINDS[kind]] = w
        elif kind == "weekly_scoped" and len(models) < MAX_MODELS:
            scope = r.get("scope")
            model = scope.get("model") if isinstance(scope, dict) else None
            name = model.get("display_name") if isinstance(model, dict) else None
            if isinstance(name, str) and name.strip() and name.isprintable():
                models[name.strip()[:MAX_MODEL_NAME]] = w
    return windows, models


def report_from_output(data):
    """The usage_report in the program's stream-json output (bytes), or None."""
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return None
    for line in text.splitlines()[:MAX_LINES]:
        if "usage_report" not in line:
            continue
        try:
            msg = json.loads(line)
        except (ValueError, RecursionError):
            continue
        if not isinstance(msg, dict):
            continue
        inner = msg.get("message")
        rep = msg.get("usage_report") or (inner.get("usage_report") if isinstance(inner, dict) else None)
        if isinstance(rep, dict):
            return rep
    return None


def ask(claude, folder, popen=subprocess.Popen, timeout=TIMEOUT):
    """Run `claude -p /usage` and return its usage_report. Raises PollError."""
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0
    try:
        proc = popen([claude, *ARGS], cwd=str(folder), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                     stderr=subprocess.DEVNULL, creationflags=flags, shell=False)
    except (OSError, ValueError) as e:
        raise PollError(f"Claude Code could not be started ({type(e).__name__})") from e
    out = _read_bounded(proc, timeout)
    if out is None:
        raise PollError(f"Claude Code took longer than {timeout} s")
    if len(out) > MAX_OUTPUT:
        raise PollError("Claude Code printed too much")
    rep = report_from_output(out)
    if rep is None:
        raise PollError("no usage report in Claude Code's answer")
    return rep


def record(path, windows, models, now):
    """Write the reading over claude-limits.json. Returns True if the file changed. The live answer
    replaces what is stored; a per-model window the report no longer has is dropped."""
    doc = statusline.load(path)
    doc = doc if isinstance(doc, dict) else {}
    before = json.dumps(doc, sort_keys=True)
    for key, _ in statusline.WINDOWS:
        if key in windows:
            doc[key] = windows[key]
    if models:
        doc["seven_day_models"] = models
    else:
        doc.pop("seven_day_models", None)
    if json.dumps(doc, sort_keys=True) == before:
        return False
    doc["updated"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now))
    statusline.save(str(path), doc)
    return True


def available(use_signin=True):
    """How the limits can be had on this computer right now: "signin", "claude" or None."""
    if use_signin and signin_token():
        return "signin"
    return "claude" if find_claude() else None


def poll(folder, claude=None, now=None, popen=subprocess.Popen, use_signin=True, http=None):
    """One poll: get the limits (directly, else by asking Claude Code), check them, record them in
    <folder>/claude-limits.json. Returns True if the file changed. Raises PollError, with nothing written,
    when neither way produced limits."""
    windows, models, why = {}, {}, None
    token = signin_token() if use_signin else None
    if token:
        try:
            windows, models = reading(direct(token, http))
            if not windows:
                why = PollError("no limits in the usage endpoint's answer")
        except PollError as e:
            why = e
        finally:
            token = None                              # not kept past the request
    if not windows:
        claude = claude or find_claude()
        if not claude:
            raise why or PollError("Claude Code is not signed in or not installed on this computer")
        windows, models = reading(ask(claude, folder, popen))
        if not windows:
            raise PollError("no limits in the usage report (not a Claude subscription, or the report changed)")
    try:
        return record(Path(folder) / statusline.FILE, windows, models, time.time() if now is None else now)
    except OSError as e:
        raise PollError(f"the limits file could not be written ({type(e).__name__})") from e
