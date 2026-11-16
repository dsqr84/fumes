import json
import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
HERE = Path(__file__).parent


@pytest.fixture
def raw_feed():
    return json.loads((HERE / "sample-feed.json").read_text("utf-8"))


@pytest.fixture
def raw_feed_live():
    """The newer sample: balances entered for OpenAI and Anthropic, plus a hidden not-connected provider."""
    return json.loads((HERE / "sample-feed-live.json").read_text("utf-8"))


@pytest.fixture
def server():
    """Localhost fake server. Set server.responses[path] = (status, body bytes, delay)."""
    responses = {}

    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            status, body, delay = responses.get(self.path, (404, b"", 0))
            if delay:
                import time
                time.sleep(delay)
            try:
                self.send_response(status)
                self.end_headers()
                self.wfile.write(body)
            except OSError:
                pass

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), H)
    srv.responses = responses
    srv.base = f"http://127.0.0.1:{srv.server_port}"
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield srv
    srv.shutdown()
    srv.server_close()


@pytest.fixture(scope="session")
def qapp():
    from PyQt6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def no_real_claude(monkeypatch):
    """No test may start the Claude Code installed on this machine: the poll finds nothing unless a test
    hands it a program."""
    from fumes import usagepoll
    monkeypatch.setattr(usagepoll, "find_claude", lambda *a, **k: None)


@pytest.fixture(autouse=True)
def no_real_claude_settings(monkeypatch, tmp_path):
    """Tests never read this machine's ~/.claude/settings.json (it may hold a status line of its own)."""
    from fumes import diagnose
    monkeypatch.setattr(diagnose, "claude_settings_path", lambda: tmp_path / "no-claude" / "settings.json")
