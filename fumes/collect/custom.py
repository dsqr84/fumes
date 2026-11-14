"""Providers the user adds: manual (an entered balance) and command (a local program that prints JSON).

They are defined in `providers.json` in the config folder, which Settings writes. Nothing here ever
comes from a remote feed: a feed can only carry figures, never a command.

Manual: the balance is the newest anchor (Settings, "Credit balance"), optionally counted down by a
fixed `burn_per_day`, which also draws an estimated daily spend.

Command: `argv` is run as a list (no shell, so nothing in it is interpreted), from the config folder,
with a timeout and a cap on its output, and without a console window on Windows. If the user saved a
key for this provider (`fumes key set <id>`), it is passed in the environment as FUMES_KEY, never on
the command line, where other programs and logs can see it. The program prints one JSON object:

    {"balance": 12.40,                                  number or null
     "rows": [["2026-11-12", "model-x", 0.31], ...],    optional: day (UTC), label, dollars
     "note": "optional text shown in the peek"}

Its stderr is never shown (it could echo the key); a failure is reported by exit code or by what was
wrong with the output.
"""

import json
import math
import os
import re
import subprocess
import sys
import tempfile
from datetime import date, datetime, timezone
from pathlib import Path

from .http import NotConnected, ProviderError

FILE = "providers.json"
KINDS = ("manual", "command")
TIMEOUT, MAX_TIMEOUT = 20, 120          # seconds
MAX_OUTPUT = 1 << 20                    # bytes of stdout read
MAX_ROWS, MAX_LABEL, MAX_NOTE, MAX_NAME = 5000, 120, 600, 40
MAX_ARGS, MAX_ARG = 32, 2048
MAX_USD = 1e9
_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")
_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class ProviderSpecError(ValueError):
    """A provider definition that cannot be used (the message is safe to show)."""


def _usd(v):
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or abs(v) > MAX_USD:
        return None
    return float(v)


def _text(v, cap):
    return "".join(ch for ch in v if ch.isprintable())[:cap] if isinstance(v, str) else ""


def reserved_ids():
    from . import providers
    return {m.ID for m in providers()} | {"claude"}


class Custom:
    """Quacks like a provider module (ID, NAME, KEY_LABEL, ANCHORED, run)."""

    def __init__(self, spec):
        self.spec = spec
        self.ID, self.NAME, self.kind = spec["id"], spec["name"], spec["kind"]
        self.ANCHORED = self.kind == "manual"
        self.KEY_LABEL = "key" if spec.get("key") else ""
        self.argv = list(spec.get("argv") or [])
        self.timeout = spec.get("timeout", TIMEOUT)
        self.burn = spec.get("burn_per_day")
        self.folder = None                  # set by load(): where the command runs

    def run(self, ctx):
        return self._manual(ctx) if self.kind == "manual" else self._command(ctx)

    def spend_today(self, ctx):
        return 0.0                          # a manual balance has no provider to ask

    # ---- manual -----------------------------------------------------------
    def _manual(self, ctx):
        a = ctx.ledger.anchor(self.ID)
        if a is None:
            raise NotConnected("No balance entered yet. Enter it in Settings.")
        rows, balance = [], a["usd"]
        if self.burn:
            start = datetime.strptime(a["ts"][:10], "%Y-%m-%d").date()
            today = ctx.now.astimezone(timezone.utc).date()
            days = (today - start).days
            balance = a["usd"] - self.burn * max(days, 0)
            rows = [(date.fromordinal(start.toordinal() + i).isoformat(), "estimate", self.burn)
                    for i in range(1, days + 1)]
            note = (f"Estimate: {_money(a['usd'])} entered {a['ts'][:10]}, minus {_money(self.burn)} a day. "
                    "Enter it again after a top-up.")
        else:
            note = f"Balance as entered {a['ts'][:10]}. Enter it again when it changes."
        return {"rows": rows, "balance": balance, "note": note}

    # ---- command ----------------------------------------------------------
    def _command(self, ctx):
        # every other key Fumes can see (FUMES_KEY_<ID> off Windows) stays out of the program's environment
        env = {k: v for k, v in os.environ.items() if not k.upper().startswith("FUMES_KEY")}
        if self.spec.get("key"):
            if not ctx.key:
                raise NotConnected(f"No key saved. Run: fumes key set {self.ID}")
            env["FUMES_KEY"] = ctx.key
        if _is_batch(self.argv[0]):
            raise ProviderError("batch files (.bat, .cmd) cannot be run: Windows passes their arguments "
                                "through cmd.exe. Run the program the batch file starts instead.")
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0
        try:
            proc = subprocess.Popen(self.argv, cwd=str(self.folder) if self.folder else None, env=env,
                                    stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                    creationflags=flags, shell=False)
        except FileNotFoundError:
            raise ProviderError("the command's program was not found") from None
        except OSError as e:
            raise ProviderError(f"the command could not start ({type(e).__name__})") from None
        out = _read_bounded(proc, self.timeout)
        if out is None:
            raise ProviderError(f"the command took longer than {self.timeout} s")
        if len(out) > MAX_OUTPUT:
            raise ProviderError("the command printed too much")
        if proc.returncode != 0:
            raise ProviderError(f"the command exited with code {proc.returncode}")
        return parse_output(out)


def _read_bounded(proc, timeout):
    """Read at most MAX_OUTPUT + 1 bytes of stdout within `timeout`, then reap the process. The program is
    killed if it runs out of time or prints past the cap, so neither its run time nor its output can grow
    without bound. Returns the bytes read, or None on timeout."""
    import threading
    import time
    got = []
    reader = threading.Thread(target=lambda: got.append(proc.stdout.read(MAX_OUTPUT + 1)), daemon=True)
    deadline = time.monotonic() + timeout
    reader.start()
    reader.join(timeout)
    timed_out = reader.is_alive()
    if timed_out or (got and len(got[0]) > MAX_OUTPUT):
        proc.kill()
    try:
        proc.wait(timeout=max(1.0, deadline - time.monotonic()))
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
    reader.join(1.0)
    if not reader.is_alive():   # a child the program started can still hold the pipe; then leave it to the daemon
        try:
            proc.stdout.close()
        except OSError:
            pass
    if timed_out:
        return None
    return got[0] if got else b""


def _is_batch(program):
    """A .bat or .cmd file, named or found on PATH (a bare `run` can resolve to run.cmd on Windows)."""
    import shutil
    found = shutil.which(program) or program
    return str(found).lower().rstrip(". ").endswith((".bat", ".cmd"))


def _money(v):
    from . import money
    return money(v)


def parse_output(raw):
    """A command's stdout -> {"rows", "balance", "note"}. Untrusted: bad rows are dropped, a bad document
    raises ProviderError with what was wrong (never the output itself)."""
    try:
        doc = json.loads(raw.decode("utf-8") if isinstance(raw, bytes) else raw)
    except (ValueError, UnicodeDecodeError, RecursionError):
        raise ProviderError("the command did not print JSON") from None
    if not isinstance(doc, dict):
        raise ProviderError("the command's JSON is not an object")
    balance = doc.get("balance")
    if balance is not None:
        balance = _usd(balance)
        if balance is None:
            raise ProviderError("the command's balance is not a number")
    rows = []
    raw_rows = doc.get("rows")
    if isinstance(raw_rows, list):
        for r in raw_rows[:MAX_ROWS]:
            if (isinstance(r, list) and len(r) == 3 and isinstance(r[0], str) and _DAY.match(r[0])
                    and _usd(r[2]) is not None and _usd(r[2]) >= 0):
                try:
                    datetime.strptime(r[0], "%Y-%m-%d")
                except ValueError:
                    continue
                rows.append((r[0], _text(r[1], MAX_LABEL) or "other", _usd(r[2])))
    if balance is None and not rows:
        raise ProviderError("the command printed neither a balance nor any rows")
    return {"rows": rows, "balance": balance, "note": _text(doc.get("note"), MAX_NOTE)}


# ---- providers.json --------------------------------------------------------
def validate(spec, taken=()):
    """A cleaned provider definition, or ProviderSpecError."""
    if not isinstance(spec, dict):
        raise ProviderSpecError("a provider must be an object")
    pid = spec.get("id")
    if not isinstance(pid, str) or not _ID.match(pid):
        raise ProviderSpecError("the id must be lowercase letters, digits, - or _ (up to 32)")
    if pid in reserved_ids() or pid in taken:
        raise ProviderSpecError(f"the id {pid!r} is already used")
    name = _text(spec.get("name"), MAX_NAME).strip()
    if not name:
        raise ProviderSpecError("a provider needs a name")
    kind = spec.get("kind")
    if kind not in KINDS:
        raise ProviderSpecError("kind must be manual or command")
    out = {"id": pid, "name": name, "kind": kind}
    if kind == "manual":
        if spec.get("burn_per_day") is not None:
            burn = _usd(spec["burn_per_day"])
            if burn is None or burn <= 0:
                raise ProviderSpecError("the daily burn must be a dollar amount above zero")
            out["burn_per_day"] = burn
    else:
        argv = spec.get("argv")
        if (not isinstance(argv, list) or not argv or len(argv) > MAX_ARGS
                or not all(isinstance(a, str) and a and len(a) <= MAX_ARG and "\x00" not in a for a in argv)):
            raise ProviderSpecError("the command must be a program and its arguments, as a list of strings")
        if _is_batch(argv[0]):
            raise ProviderSpecError("a batch file (.bat, .cmd) cannot be the program: Windows passes its "
                                    "arguments through cmd.exe. Choose the program it starts instead")
        out["argv"] = list(argv)
        t = spec.get("timeout", TIMEOUT)
        if isinstance(t, bool) or not isinstance(t, int) or not 1 <= t <= MAX_TIMEOUT:
            raise ProviderSpecError(f"the timeout must be 1 to {MAX_TIMEOUT} seconds")
        out["timeout"] = t
        out["key"] = spec.get("key") is True
    return out


def load(folder):
    """[Custom] from <folder>/providers.json. A bad entry is skipped, never raised: the strip must start."""
    folder = Path(folder)
    try:
        doc = json.loads((folder / FILE).read_text("utf-8"))
    except (OSError, ValueError, RecursionError):
        return []
    out, taken = [], set()
    specs = doc.get("providers") if isinstance(doc, dict) else None
    for spec in specs if isinstance(specs, list) else []:
        try:
            clean = validate(spec, taken)
        except ProviderSpecError:
            continue
        taken.add(clean["id"])
        c = Custom(clean)
        c.folder = folder
        out.append(c)
    return out


def save(folder, specs):
    """Write providers.json atomically from cleaned definitions (Settings validates before calling)."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    text = json.dumps({"providers": [dict(s) for s in specs]}, indent=2)
    fd, tmp = tempfile.mkstemp(dir=folder, prefix=".providers-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, folder / FILE)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
