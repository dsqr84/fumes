"""The read-only commands behind `fumes setup-prompt`, `fumes status` and `fumes doctor`. No Qt.

Nothing here ever returns, prints or logs a key value or any part of one: keys are read from the store
only to be classified (saved, wrong provider, wrong kind) and the classification is all that leaves.
"""

import json
import os
import sys
import tomllib
from datetime import datetime, timezone
from pathlib import Path

from . import collect as col
from . import config as cfgmod
from . import keycheck, statusline
from . import limits as limmod
from .collect import custom

PLACEHOLDER = '<FOLDER, or "the folder you are in">'
GUIDES = Path(__file__).parent / "guides"
MAX_SETTINGS = 1 << 20


def install_root():
    """The folder this copy of Fumes runs from (the same one win.statusline_command uses)."""
    from . import win
    if win.frozen():                       # the release build: the folder Fumes.exe was unzipped to
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def setup_prompt(root=None):
    text = (GUIDES / "setup-prompt.txt").read_text("utf-8")
    return text.replace(PLACEHOLDER, root or install_root())


# ------------------------------------------------------------ gathering --
def _raw_specs(folder):
    try:
        doc = json.loads((Path(folder) / custom.FILE).read_text("utf-8"))
    except (OSError, ValueError):
        return []
    specs = doc.get("providers") if isinstance(doc, dict) else None
    return specs if isinstance(specs, list) else []


def custom_errors(folder):
    """One message per providers.json entry that custom.validate rejects (load() skips them silently)."""
    out, taken = [], set()
    for i, spec in enumerate(_raw_specs(folder), 1):
        label = spec.get("id") if isinstance(spec, dict) and isinstance(spec.get("id"), str) else f"#{i}"
        try:
            clean = custom.validate(spec, taken)
        except custom.ProviderSpecError as e:
            out.append(f"providers.json entry {label}: {e}")
            continue
        taken.add(clean["id"])
    return out


def claude_settings_path():
    return Path.home() / ".claude" / "settings.json"


def statusline_state(settings_path=None):
    """'configured' | 'other' | 'missing' from the user's Claude Code settings, read as untrusted JSON."""
    path = Path(settings_path) if settings_path else claude_settings_path()
    try:
        with open(path, "rb") as f:
            doc = json.loads(f.read(MAX_SETTINGS + 1).decode("utf-8"))
    except (OSError, ValueError):
        return "missing"
    sl = doc.get("statusLine") if isinstance(doc, dict) else None
    if not isinstance(sl, dict) or not sl:
        return "missing"
    cmd = sl.get("command")
    if isinstance(cmd, str) and "fumes.statusline" in cmd:
        return "configured"
    return "other"


def limits_reading(folder, state):
    """{"five_hour_used", "seven_day_used", "updated"} from claude-limits.json or the cached feed, else None."""
    docs = []
    try:
        with open(Path(folder) / statusline.FILE, "rb") as f:
            docs.append(json.loads(f.read(65536).decode("utf-8")))
    except (OSError, ValueError):
        pass
    if state is not None and state.limits_cache:
        docs.append(state.limits_cache)
    best = None
    for d in docs:
        try:
            lim = limmod.parse_limits(d)
        except limmod.FeedError:
            continue
        if best is None or lim.updated > best.updated:
            best = lim
    if best is None:
        return None
    return {"five_hour_used": best.five_hour.used if best.five_hour else None,
            "seven_day_used": best.seven_day.used if best.seven_day else None,
            "updated": best.updated.strftime(col.STAMP)}


def _kind(mod):
    return getattr(mod, "kind", "builtin")


def _key_state(mod, store):
    """(state, problem) for a provider's key; the key itself never leaves this function."""
    if not mod.KEY_LABEL:
        return "not_needed", None
    key = store.get(mod.ID)
    if not key:
        return "missing", None
    return "saved", keycheck.foreign(mod.ID, key) or keycheck.kind_hint(mod.ID, key)


def gather(folder, store, cfg, state, ledger, settings_path=None):
    """The whole status document (see `fumes status --json`)."""
    from . import __version__
    mine = custom.load(folder)
    cached = ((state.cache or {}).get("providers") if state else None) or {}
    provs = []
    for m in list(col.providers()) + list(mine):
        key, problem = _key_state(m, store)
        credit = None
        if getattr(m, "ANCHORED", False):
            a = ledger.anchor(m.ID)
            if a:
                credit = {"usd": a["usd"], "date": a["ts"][:10]}
        c = cached.get(m.ID)
        last = None
        if isinstance(c, dict):
            last = {k: c.get(k) for k in ("status", "balance", "mtd", "checked", "error")}
        provs.append({"id": m.ID, "name": m.NAME, "kind": _kind(m), "key": key, "key_problem": problem,
                      "credit_entered": credit, "last": last})
    return {
        "version": __version__,
        "config_folder": str(folder),
        "mode": "remote" if cfg.url else "local",
        "providers": provs,
        "custom_errors": custom_errors(folder),
        "limits": {"reading": limits_reading(folder, state), "statusline": statusline_state(settings_path)},
    }


def _money(v):
    return "-" if v is None else col.money(v)


def render_status(doc):
    rows = [("provider", "kind", "key", "credit", "last")]
    for p in doc["providers"]:
        credit = f"{col.money(p['credit_entered']['usd'])} ({p['credit_entered']['date']})" \
            if p["credit_entered"] else "-"
        last = p["last"]
        lastt = "-" if not last else f"{last.get('status')} {_money(last.get('balance'))}"
        key = p["key"] + (" (problem)" if p["key_problem"] else "")
        rows.append((p["id"], p["kind"], key, credit, lastt))
    widths = [max(len(r[i]) for r in rows) for i in range(5)]
    lines = [f"Fumes {doc['version']}, {doc['mode']} mode, config folder {doc['config_folder']}"]
    for r in rows:
        lines.append("  ".join(c.ljust(w) for c, w in zip(r, widths)).rstrip())
    for e in doc["custom_errors"]:
        lines.append(f"rejected: {e}")
    lim = doc["limits"]
    rd = lim["reading"]
    lines.append(f"Claude limits: status line {lim['statusline']}"
                 + (f", last reading {rd['updated']}" if rd else ", no reading yet"))
    return "\n".join(lines)


# ---------------------------------------------------------------- doctor --
def _age(updated, now):
    try:
        t = datetime.strptime(updated, col.STAMP).replace(tzinfo=timezone.utc)
    except ValueError:
        return updated
    s = max(int((now - t).total_seconds()), 0)
    if s < 90:
        return "just now"
    if s < 5400:
        return f"{round(s / 60)} min ago"
    if s < 172800:
        return f"{round(s / 3600)} h ago"
    return f"{round(s / 86400)} days ago"


def snippet():
    """The statusLine entry to add to Claude Code's settings, or None if no command can be built."""
    from . import win
    try:
        cmd = win.statusline_command()
    except ValueError:
        return None
    return {"statusLine": {"type": "command", "command": cmd}}


def run_doctor(folder, store, now, settings_path=None, config_warnings=None, config_path=None):
    """(checks, snippet or None). checks: [{"check", "state": ok|fix|info, "message"}]."""
    from . import win
    checks = []

    def add(check, state, message):
        checks.append({"check": check, "state": state, "message": message})

    path = Path(config_path) if config_path else Path(folder) / "config.toml"
    warnings = list(config_warnings or [])
    if not path.is_file():
        add("config", "info", f"no config.toml in {folder}; the defaults are used (that is fine)")
    elif warnings:
        add("config", "fix", f"config.toml has problems: {'; '.join(warnings)}")
    else:
        add("config", "ok", f"config.toml loads ({path})")
    cfg = cfgmod.load_config(path, warn=lambda m: None)
    remote = bool(cfg.url)
    state = cfgmod.State.load(Path(folder) / "state.json")
    ledger = col.Ledger.load(Path(folder) / "ledger.json")
    doc = gather(folder, store, cfg, state, ledger, settings_path)

    if remote:
        add("mode", "info", "remote feed mode: the strip reads the feed at the configured url, so the commands "
            "and keys below are not used by it")
    else:
        add("mode", "ok", "local mode: Fumes asks each provider itself with the keys saved on this computer")

    for p in doc["providers"]:
        name = f"{p['name']} ({p['id']})"
        hint = f"`fumes key set {p['id']}`"
        if p["kind"] == "manual":
            if p["credit_entered"]:
                add(p["id"], "ok", f"{name}: manual provider, balance entered "
                    f"{col.money(p['credit_entered']['usd'])} on {p['credit_entered']['date']}")
            else:
                add(p["id"], "fix", f"{name}: manual provider with no balance; enter one in Settings")
        elif p["key"] == "missing" and p["kind"] == "command":
            add(p["id"], "fix", f"{name}: this command provider needs a key and none is saved; run {hint}")
        elif p["key"] == "missing":
            add(p["id"], "info", f"{name}: no key saved (fine if you do not use it; otherwise run {hint})")
        elif p["key_problem"]:
            add(p["id"], "fix", f"{name}: {p['key_problem']}")
        elif p["kind"] == "builtin" and getattr(col.by_id(p["id"]), "ANCHORED", False) and not p["credit_entered"]:
            add(p["id"], "fix", f"{name}: key saved but no credit entered; run `fumes anchor {p['id']} <usd>` "
                "with the balance your billing page shows")
        else:
            bits = ["key saved"] if p["key"] == "saved" else ["command provider"]
            if p["credit_entered"]:
                bits.append(f"credit {col.money(p['credit_entered']['usd'])} entered {p['credit_entered']['date']}")
            add(p["id"], "ok", f"{name}: {', '.join(bits)}")
    for e in doc["custom_errors"]:
        add("providers.json", "fix", f"{e} (this provider is ignored)")

    sl = doc["limits"]["statusline"]
    snip = None
    if sl == "configured":
        rd = doc["limits"]["reading"]
        msg = "Claude Code's status line records the limits"
        msg += f"; last reading {_age(rd['updated'], now)}" if rd else \
            "; no reading yet (it appears after your next Claude Code answer)"
        add("claude-limits", "ok", msg)
    elif sl == "other":
        snip = snippet()
        add("claude-limits", "fix", "Claude Code already has a different status line. Fumes's, below, would "
            "replace it: decide first, and keep the other settings in the file")
    else:
        snip = snippet()
        add("claude-limits", "info", "Claude Code's status line is not set up for Fumes. Add this to "
            "~/.claude/settings.json (keep the other settings):" if snip else
            "Claude Code's status line is not set up for Fumes, and no command can be built for this folder")

    add("autostart", "ok" if win.autostart_enabled() else "info",
        "Fumes starts with Windows" if win.autostart_enabled() else "Fumes does not start with Windows")
    return checks, snip


PREFIX = {"ok": "ok", "fix": "!!", "info": "--"}


def render_doctor(checks, snip):
    lines = []
    for c in checks:
        lines.append(f"{PREFIX[c['state']]} {c['check']}: {c['message']}")
        if snip and c["check"] == "claude-limits":
            lines.append(json.dumps(snip, indent=2))
    bad = sum(1 for c in checks if c["state"] == "fix")
    lines.append(f"{bad} problem{'s' if bad != 1 else ''} to fix." if bad else "Nothing needs fixing.")
    return "\n".join(lines)
