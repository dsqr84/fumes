"""Claude Code status line that also records the subscription's 5-hour and weekly limits for Fumes.

Claude Code runs the status line command after each assistant message with the session's JSON on
stdin. For a Claude subscriber that JSON carries `rate_limits.five_hour` and `rate_limits.seven_day`
(`used_percentage` 0-100, `resets_at` in Unix seconds) once the session has had its first answer.
This script:

  - prints one short line for the status row:   ctx 12% · 5h 23% → 14:00 · week 41%
  - merges the limits into claude-limits.json in the Fumes config folder, which the strip reads.

Run it as `python -m fumes.statusline` (Settings shows the full command). Every Claude Code session
runs it, so a write never moves the figures backwards: a window is replaced only by a later window,
or by the same window with at least as much used. It never fails or prints an error into the status
row, and it imports nothing from Qt, so it starts fast. Nothing is sent anywhere.
"""

import json
import os
import sys
import tempfile
import time
from datetime import datetime, timezone

FILE = "claude-limits.json"


def default_out():
    """claude-limits.json in the Fumes config folder (FUMES_LIMITS_OUT overrides, for tests)."""
    from .config import config_dir
    return os.environ.get("FUMES_LIMITS_OUT") or str(config_dir() / FILE)


WINDOWS = (("five_hour", "5h"), ("seven_day", "week"))
SAME_WINDOW = 120   # seconds: resets_at values this close are the same window
MAX_INPUT = 1 << 20  # Claude Code's status JSON is a few KB; never read more than this


def window(raw):
    """{'used_percentage': float, 'resets_at': int} from one rate-limit window, or None."""
    if not isinstance(raw, dict):
        return None
    used, resets = raw.get("used_percentage"), raw.get("resets_at")
    if isinstance(used, bool) or not isinstance(used, (int, float)):
        return None
    if isinstance(resets, bool) or not isinstance(resets, (int, float)):
        return None
    return {"used_percentage": round(max(0.0, float(used)), 1), "resets_at": int(resets)}


def newer(new, old, now):
    """Should `new` replace `old`? Never let an idle session's older reading win."""
    if new is None:
        return False
    if old is None:
        return True
    if old["resets_at"] <= now < new["resets_at"]:
        return True                                   # the stored window is over, this one is live
    if new["resets_at"] > old["resets_at"] + SAME_WINDOW:
        return True                                   # a later window
    if abs(new["resets_at"] - old["resets_at"]) <= SAME_WINDOW:
        return new["used_percentage"] >= old["used_percentage"]   # same window: use only goes up
    return False


def merge(stored, limits, now):
    """(merged document, changed?) from the stored document and this session's rate_limits."""
    doc = stored if isinstance(stored, dict) else {}
    changed = False
    for key, _ in WINDOWS:
        new, old = window((limits or {}).get(key)), window(doc.get(key))
        if newer(new, old, now) and new != old:
            doc[key] = new
            changed = True
        elif old is None and key in doc:
            del doc[key]                              # unreadable leftovers
            changed = True
    if changed:
        doc["updated"] = datetime.fromtimestamp(now, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return doc, changed


def load(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def save(path, doc):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".claude-limits.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(doc, f)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def line(data, doc, now):
    """The status row's text: context use, then each limit still inside its window."""
    parts = []
    ctx = (data.get("context_window") or {}).get("used_percentage") if isinstance(data, dict) else None
    if isinstance(ctx, (int, float)) and not isinstance(ctx, bool):
        parts.append(f"ctx {ctx:.0f}%")
    for key, label in WINDOWS:
        w = window(doc.get(key))
        if w is None or w["resets_at"] <= now:
            continue
        text = f"{label} {w['used_percentage']:.0f}%"
        if key == "five_hour":
            text += " → " + datetime.fromtimestamp(w["resets_at"]).strftime("%H:%M")
        parts.append(text)
    return " · ".join(parts)


def main(stdin=None, out=None, now=None):
    out = out or default_out()
    now = time.time() if now is None else now
    try:
        data = json.loads((stdin or sys.stdin).read(MAX_INPUT))
    except ValueError:
        data = {}
    limits = data.get("rate_limits") if isinstance(data, dict) else None
    doc, changed = merge(load(out), limits if isinstance(limits, dict) else None, now)
    if changed:
        try:
            save(out, doc)
        except OSError:
            pass
    return line(data, doc, now)


def run():
    try:
        print(main())
    except Exception:   # the status row must never show a traceback
        pass


if __name__ == "__main__":
    run()
