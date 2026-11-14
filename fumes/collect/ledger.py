"""ledger.json: what Fumes must remember between runs to work out a balance.

- **Anchors**: a credit balance the user entered for a provider that has no balance API (OpenAI,
  Anthropic). The balance shown is the newest anchor minus the spend the provider reports since it.
  `day_spend` is what had already been spent on the anchor's own day when it was entered: costs
  come per whole day, so that part must not be subtracted again.
- **Balance samples**: (time, balance) readings of a provider that reports a balance but no spend
  (DeepSeek). Spend is estimated from the falls between readings; a rise is a top-up and ignored.

The file lives in the config folder and is replaced atomically. It is read as untrusted: anything
malformed is dropped, never raised.
"""

import json
import math
import os
import tempfile
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

MAX_ANCHORS = 10        # per provider; only the newest is used, the rest are history
SAMPLE_DAYS = 40        # balance samples kept, a little over the 30-day chart
MIN_SAMPLE_GAP = 60     # seconds: readings closer than this replace the previous one
MAX_USD = 1e9


def _stamp(dt):
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse(ts):
    try:
        dt = datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ")
    except (TypeError, ValueError):
        return None
    return dt.replace(tzinfo=timezone.utc)


def _usd(v):
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or abs(v) > MAX_USD:
        return None
    return float(v)


class Ledger:
    def __init__(self, path):
        self.path = Path(path)
        self.anchors = {}     # pid -> [{"usd", "ts", "day_spend"}], oldest first
        self.samples = {}     # pid -> [[ts, usd]], oldest first
        # Settings (UI thread) and collection (a worker thread) share one ledger: every change and the
        # snapshot that save() writes happen under this lock.
        self._lock = threading.RLock()

    # ---- load / save ------------------------------------------------------
    @classmethod
    def load(cls, path):
        led = cls(path)
        try:
            d = json.loads(Path(path).read_text("utf-8"))
        except (OSError, ValueError, RecursionError):
            return led
        if not isinstance(d, dict):
            return led
        anchors = d.get("anchors")
        for pid, recs in anchors.items() if isinstance(anchors, dict) else ():
            if not isinstance(pid, str) or not isinstance(recs, list):
                continue
            good = [{"usd": _usd(r["usd"]), "ts": r["ts"], "day_spend": max(_usd(r.get("day_spend")) or 0.0, 0.0)}
                    for r in recs
                    if isinstance(r, dict) and _usd(r.get("usd")) is not None and _parse(r.get("ts"))]
            if good:
                led.anchors[pid] = sorted(good, key=lambda r: r["ts"])[-MAX_ANCHORS:]
        samples = d.get("samples")
        for pid, rows in samples.items() if isinstance(samples, dict) else ():
            if not isinstance(pid, str) or not isinstance(rows, list):
                continue
            good = [[r[0], _usd(r[1])] for r in rows
                    if isinstance(r, list) and len(r) == 2 and _parse(r[0]) and _usd(r[1]) is not None]
            if good:
                led.samples[pid] = sorted(good)
        return led

    def save(self):
        with self._lock:
            text = json.dumps({"anchors": self.anchors, "samples": self.samples})
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=".ledger-", suffix=".tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    f.write(text)
                os.replace(tmp, self.path)
            except BaseException:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
                raise

    # ---- anchors ----------------------------------------------------------
    def anchor(self, pid):
        """The newest anchor {"usd", "ts", "day_spend"}, or None."""
        recs = self.anchors.get(pid)
        return dict(recs[-1]) if recs else None

    def set_anchor(self, pid, usd, now, day_spend=0.0):
        usd = _usd(usd)
        if usd is None or usd < 0:
            raise ValueError("a balance is a dollar amount, zero or more")
        rec = {"usd": round(usd, 6), "ts": _stamp(now), "day_spend": round(max(_usd(day_spend) or 0.0, 0.0), 6)}
        with self._lock:
            self.anchors[pid] = (self.anchors.get(pid, []) + [rec])[-MAX_ANCHORS:]
        return rec

    def clear_anchor(self, pid):
        with self._lock:
            self.anchors.pop(pid, None)

    def anchored_balance(self, pid, rows):
        """(balance, anchor) from the newest anchor and cost rows [(day, label, usd)]: the anchor minus what
        was spent from its day on, less what had already gone on that day when it was entered. Never more
        than the anchor. (None, None) without an anchor."""
        a = self.anchor(pid)
        if a is None:
            return None, None
        day = a["ts"][:10]
        since = sum(usd for d, _, usd in rows if d >= day) - a["day_spend"]
        return a["usd"] - max(since, 0.0), a

    # ---- balance samples --------------------------------------------------
    def record_balance(self, pid, usd, now):
        usd = _usd(usd)
        if usd is None:
            return
        with self._lock:
            rows = list(self.samples.get(pid, []))
            ts = _stamp(now)
            if rows:
                last = _parse(rows[-1][0])
                if last and (now - last).total_seconds() < MIN_SAMPLE_GAP:
                    rows.pop()
            rows.append([ts, round(usd, 6)])
            cutoff = _stamp(now - timedelta(days=SAMPLE_DAYS))
            self.samples[pid] = [r for r in rows if r[0] >= cutoff]

    def spend_from_balance(self, pid, label="spend (from balance changes)"):
        """Cost rows [(day, label, usd)] estimated from the falls between balance samples, each counted on
        the day of the later reading. A rise (a top-up, a refund) is skipped, not netted."""
        rows, prev = [], None
        for ts, usd in self.samples.get(pid, []):
            if prev is not None and usd < prev:
                rows.append((ts[:10], label, prev - usd))
            prev = usd
        return rows

    def first_sample(self, pid):
        rows = self.samples.get(pid)
        return rows[0][0] if rows else None
