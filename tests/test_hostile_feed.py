"""A hostile or broken remote feed must never crash, hang or mislead the app (SPEND-6)."""
import json
from pathlib import Path

import pytest

from fumes import config as cfgmod
from fumes import feed as feedmod
from fumes import fmt, ui

HERE = Path(__file__).parent


@pytest.mark.parametrize("v", [float("nan"), float("inf"), -float("inf"), 1e308, 1e10, -1e10, True, "5", None])
def test_non_finite_absurd_or_wrong_typed_numbers_are_missing(v):
    assert feedmod._num(v) is None and feedmod._usd(v) is None


def test_ordinary_numbers_still_parse():
    assert feedmod._num(-1.5) == -1.5 and feedmod._usd(3) == 3.0 and feedmod._num(1e9) == 1e9


@pytest.mark.parametrize("v", [float("inf"), 1e300, float("nan"), -5, 0])
def test_nice_top_never_raises(v):
    top = fmt.nice_top(v)
    assert top >= 1.0 and top == top


def test_huge_daily_values_cannot_overflow_the_chart(qapp):
    raw = json.loads((HERE / "sample-feed-live.json").read_text("utf-8"))
    for p in raw["providers"].values():
        p["daily"] = {d: 1e308 for d in raw["days"]}
        p["balance"] = float("inf")
    f = feedmod.parse_feed(raw)
    assert all(p.balance is None and not p.daily for p in f.providers)


def test_a_paint_that_raises_draws_nothing_instead_of_aborting(qapp, monkeypatch, capsys):
    s = ui.Strip(cfgmod.Config(), theme="dark")
    monkeypatch.setattr(s, "paint_to", lambda *a: 1 / 0)
    s.grab()                                     # paintEvent: under PyQt6 an escaping raise aborts the process
    assert "could not be drawn" in capsys.readouterr().err


def test_fetch_refuses_other_schemes_and_oversized_feeds(server):
    with pytest.raises(feedmod.FeedError, match="only http and https"):
        feedmod.fetch_raw("file:///etc/passwd")
    big = b'{"x": "' + b"a" * (feedmod.MAX_BYTES + 10) + b'"}'
    server.responses["/big"] = (200, big, 0)
    with pytest.raises(feedmod.FeedError, match="too large"):
        feedmod.fetch_raw(server.base + "/big")


def test_fetch_errors_do_not_echo_server_text(server):
    server.responses["/bad"] = (500, b"secret page text", 0)
    with pytest.raises(feedmod.FeedError) as e:
        feedmod.fetch_raw(server.base + "/bad")
    assert "secret" not in str(e.value)


def test_limits_survive_huge_integers_and_deep_nesting(qapp, tmp_path, monkeypatch):
    from fumes import limits as lim
    w = lim._window({"used_percentage": 10 ** 400, "resets_at": 1})
    assert w is None or w.used == lim.MAX_USED                       # never OverflowError
    assert lim._window({"used_percentage": float("nan"), "resets_at": 1}) is None
    import test_local_limits as tl
    (tmp_path / "claude-limits.json").write_text("[" * 5000 + "]" * 5000)
    app = tl.make_app(tmp_path, monkeypatch)
    app.refresh_limits()                                              # unreadable, not a crash
    assert app.strip.limits_failed


def test_an_unwritable_cached_document_is_dropped_not_raised(tmp_path):
    st = cfgmod.State(tmp_path / "s.json")
    st.cache, st.offset = {"x": {1, 2}}, 7          # not JSON: json.dumps raises (as deep nesting can)
    st.save()
    back = cfgmod.State.load(tmp_path / "s.json")
    assert back.offset == 7 and back.cache is None


def test_hovering_a_peek_without_a_chart_does_not_raise(qapp):
    raw = json.loads((HERE / "sample-feed-live.json").read_text("utf-8"))
    s = ui.Strip(cfgmod.Config(), theme="dark")
    s.set_feed(feedmod.parse_feed(raw))
    from fumes.peek import Peek
    pk = Peek(s)
    assert pk.set_provider("anthropic")                    # not connected: no chart
    assert pk.day_at(40, 200) is None


def test_a_reflected_key_never_reaches_the_payload(tmp_path):
    """A provider echoing the key it was sent, in a label, a note or an error, is scrubbed by collect()."""
    from datetime import datetime, timezone
    from fakes import FakeHttp
    from fumes import collect, keystore
    from fumes.collect.ledger import Ledger
    key = "sk-admin-REFLECTEDKEY0123456789"
    now = datetime(2026, 11, 13, 15, tzinfo=timezone.utc)
    http = FakeHttp({"https://api.openai.com/v1/organization/costs": (200, {"data": [{"start_time": 1794960000,
                     "results": [{"line_item": "model " + key, "amount": {"value": 1}}]}]})})
    pay, _ = collect.collect(now, keystore.MemoryStore({"openai": key}), Ledger(tmp_path / "l.json"), http,
                             enabled={"openai"})
    assert "REFLECTEDKEY" not in json.dumps(pay) and "[redacted]" in json.dumps(pay)


def test_batch_files_are_refused_as_command_programs(tmp_path):
    from fumes.collect import custom
    for prog in ("C:/tools/run.cmd", "wrapper.BAT", "x.cmd. "):
        with pytest.raises(custom.ProviderSpecError, match="batch file"):
            custom.validate({"id": "x", "name": "X", "kind": "command", "argv": [prog, "a&calc.exe"]})


def test_status_line_command_refuses_shell_metacharacters():
    from fumes import win
    for bad in ("C:/Fumes$(calc)", "C:/a`b`", "C:/x!y"):
        with pytest.raises(ValueError):
            win.statusline_command(root=bad, exe="C:/py/python.exe")
    assert "fumes.statusline" in win.statusline_command(root="C:/Users/Alex Kim/fumes", exe="C:/py/python.exe")


def test_a_slow_dripping_server_hits_the_deadline(monkeypatch):
    from fumes.collect import http as h

    class Drip:
        status = 200

        def read(self, n=-1):
            return b" "                     # one byte at a time, forever

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    class Opener:
        def open(self, req, timeout=None):
            return Drip()
    clock = iter(range(0, 10_000, 5))
    monkeypatch.setattr(h.time, "monotonic", lambda: next(clock))
    with pytest.raises(h.ProviderError, match="TimeoutError"):
        h.Http(opener=Opener()).get("https://x.test/a", {})


def test_an_error_in_a_qt_handler_does_not_abort_the_app():
    """Run in a child process: with the hook installed, a raising mouse handler is reported and the
    process exits normally; PyQt6's default would abort it."""
    import os
    import subprocess
    import sys
    code = r'''
import sys
from fumes import guard
guard.install_excepthook()
from PyQt6.QtWidgets import QApplication, QWidget
from PyQt6.QtCore import QTimer
app = QApplication([])
class W(QWidget):
    def resizeEvent(self, ev):
        raise ValueError("boom")
w = W(); w.resize(10, 10); w.show(); w.resize(20, 20)
QTimer.singleShot(50, app.quit)
app.exec()
print("still alive")
'''
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen", PYTHONPATH=str(HERE.parent))
    got = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, timeout=60)
    assert got.returncode == 0 and "still alive" in got.stdout and "boom" in got.stderr
