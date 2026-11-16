import json
import time
from datetime import datetime, timezone

import pytest
from PyQt6.QtCore import QCoreApplication, QUrl
from PyQt6.QtGui import QValidator
from PyQt6.QtWidgets import QLabel, QLineEdit, QPlainTextEdit, QTextBrowser

from fumes import collect, keystore, settings, win
from fumes.config import Config
from fumes.collect.http import NotConnected, ProviderError
from tests.fakes import FakeHttp

SECRET = "sk-secret-0123456789abcdef"
TYPED = "sk-typed-ZZZZ9999"
DEEPSEEK_OK = (200, {"balance_infos": [{"currency": "USD", "total_balance": "4.15"}]})


@pytest.fixture
def env(qapp, tmp_path):
    store = keystore.MemoryStore()
    ledger = collect.Ledger(tmp_path / "ledger.json")
    http = FakeHttp({"https://api.deepseek.com/user/balance": DEEPSEEK_OK})
    opened, asked = [], []
    w = settings.SettingsWindow(store, ledger, http=http, folder=tmp_path, cfg=Config(),
                                opener=lambda u: opened.append(u) or True,
                                confirm=lambda t: asked.append(t) or True)
    w.show_page("deepseek")
    w.opened, w.asked = opened, asked
    yield w
    w.close()


def page(w, pid="deepseek"):
    return w.pages[pid]


def wait(cond, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        QCoreApplication.processEvents()
        if cond():
            return True
        time.sleep(0.01)
    return False


def texts(w):
    out = []
    for cls in (QLineEdit, QLabel, QTextBrowser, QPlainTextEdit):
        for c in w.findChildren(cls):
            out.append(c.text() if cls in (QLineEdit, QLabel) else c.toPlainText())
            if cls is QLineEdit:
                out.append(c.placeholderText())
    return out


def test_pages_and_title(env):
    assert env.windowTitle() == "Fumes Settings"
    assert env.ids == [m.ID for m in collect.providers()] + ["claude", "safety", "general"]
    assert env.nav.count() == len(env.ids)


def test_saved_key_never_in_any_widget(env):
    p = page(env)
    p.key.setText(SECRET)
    p.do_save()
    assert env.store.get("deepseek") == SECRET
    assert p.status.text() == "Key saved"
    for pid in env.ids:
        env.show_page(pid)
        assert all(SECRET not in t and SECRET[:8] not in t for t in texts(env))
    assert p.key.echoMode() == QLineEdit.EchoMode.Password


def test_save_clears_field(env):
    p = page(env)
    p.key.setText("  " + SECRET + " ")
    p.do_save()
    assert p.key.text() == ""
    assert env.store.get("deepseek") == SECRET


def test_invalid_key_shows_error_and_stores_nothing(env):
    p = page(env)
    p.key.setText("has spaces inside")
    p.do_save()
    assert not env.store.has("deepseek")
    assert p.key_msg.text() == "That does not look like a key."
    assert p.status.text() == "No key yet"


def test_store_error_is_shown(env):
    class Bad(keystore.MemoryStore):
        def set(self, pid, value):
            raise keystore.KeyStoreError("vault locked")
    env.store = Bad()
    p = page(env)
    p.key.setText(SECRET)
    p.do_save()
    assert p.key_msg.text() == "vault locked"


def test_remove_asks_then_deletes(env):
    env.store.set("deepseek", SECRET)
    p = page(env)
    p.refresh()
    p.do_remove()
    assert env.asked and not env.store.has("deepseek")
    assert p.status.text() == "No key yet"


def test_remove_declined_keeps_key(env):
    env.store.set("deepseek", SECRET)
    env._confirm = lambda t: False
    page(env).do_remove()
    assert env.store.has("deepseek")


def test_remove_button_only_when_saved(env):
    p = page(env)
    assert p.remove.isHidden()
    env.store.set("deepseek", SECRET)
    p.refresh()
    assert p.remove.isVisibleTo(env)


def test_key_cleared_on_page_change_and_hide(env):
    p = page(env)
    p.key.setText("abc")
    env.show_page("openai")
    assert p.key.text() == ""
    env.show()
    p.key.setText("abc")
    env.hide()
    assert p.key.text() == ""


def test_test_uses_typed_key_over_saved(env):
    env.store.set("deepseek", SECRET)
    p = page(env)
    p.key.setText(TYPED)
    p.do_test()
    assert not p.test.isEnabled()
    assert wait(lambda: p.test.isEnabled())
    auth = env.http.calls[-1][1]["Authorization"]
    assert auth == "Bearer " + TYPED
    assert p.test_msg.text() == "Working: balance $4.15, month to date $0.00"


def test_test_uses_saved_key_when_field_empty(env):
    env.store.set("deepseek", SECRET)
    p = page(env)
    p.do_test()
    assert wait(lambda: p.test.isEnabled() and env.http.calls)
    assert env.http.calls[-1][1]["Authorization"] == "Bearer " + SECRET


def test_test_runs_off_ui_thread(env):
    import threading
    seen = []

    class Spy(FakeHttp):
        def get(self, url, headers):
            seen.append(threading.current_thread())
            return super().get(url, headers)
    env.http = Spy({"https://api.deepseek.com/user/balance": DEEPSEEK_OK})
    p = page(env)
    p.key.setText(TYPED)
    p.do_test()
    assert wait(lambda: p.test.isEnabled() and seen)
    assert seen[0] is not threading.main_thread()


def test_test_no_key(env):
    p = page(env)
    p.do_test()
    assert p.test_msg.text().startswith("Not connected:")
    assert p.test.isEnabled()


@pytest.mark.parametrize("exc,expect", [
    (NotConnected("key rejected"), "Not connected: key rejected"),
    (ProviderError("HTTP 500"), "Failed: HTTP 500"),
    (RuntimeError("boom secret"), "Failed: RuntimeError"),
])
def test_test_error_texts(env, exc, expect):
    env.http = FakeHttp({"https://api.deepseek.com/user/balance": exc})
    p = page(env)
    p.key.setText(TYPED)
    p.do_test()
    assert wait(lambda: p.test.isEnabled() and p.test_msg.text() != "Testing...")
    assert p.test_msg.text() == expect


def test_test_does_not_touch_real_ledger(env):
    env.ledger.record_balance("deepseek", 9.0, datetime(2026, 11, 1, tzinfo=timezone.utc))
    before = json.dumps(env.ledger.samples)
    p = page(env)
    p.key.setText(TYPED)
    p.do_test()
    assert wait(lambda: p.test.isEnabled() and env.http.calls)
    assert json.dumps(env.ledger.samples) == before


def test_anchor_set_records_and_saves(env, tmp_path):
    refreshed = []
    env.on_refresh = lambda: refreshed.append(1)
    env.http = FakeHttp()
    p = page(env, "openai")
    p.anchor_edit.setText("$1,020.50")
    p.do_anchor()
    assert wait(lambda: p.anchor_set.isEnabled())
    a = env.ledger.anchor("openai")
    assert a["usd"] == 1020.5
    assert (tmp_path / "ledger.json").exists()
    assert collect.Ledger.load(tmp_path / "ledger.json").anchor("openai")["usd"] == 1020.5
    assert p.anchor_line.text().startswith("Entered $1,020.50 on ")
    assert refreshed == [1]


def test_anchor_warning_shown(env):
    env.store.set("openai", SECRET)
    env.http = FakeHttp({"https://api.openai.com": ProviderError("nope")})
    p = page(env, "openai")
    p.anchor_edit.setText("20")
    p.do_anchor()
    assert wait(lambda: p.anchor_set.isEnabled() and p.anchor_msg.text() != "Saving...")
    assert "Could not read today's spend" in p.anchor_msg.text()
    assert env.ledger.anchor("openai")["usd"] == 20


def test_anchor_only_for_anchored_providers(env):
    assert page(env, "deepseek").anchor_edit is None
    assert page(env, "openai").anchor_edit is not None


@pytest.mark.parametrize("bad", ["abc", "-5", "1e3", "20000000", "", "1.2.3", "nan"])
def test_credit_field_rejects_bad_input(env, bad):
    p = page(env, "openai")
    assert p.anchor_edit.validator().validate(bad, 0)[0] != QValidator.State.Acceptable
    p.anchor_edit.setText(bad)
    p.do_anchor()
    assert env.ledger.anchor("openai") is None


def test_credit_validator_accepts_dollars_and_commas(env):
    v = page(env, "openai").anchor_edit.validator()
    for good in ("20", "$20.50", "$1,234.5", "0"):
        assert v.validate(good, 0)[0] == QValidator.State.Acceptable, good
    assert v.validate("1.234", 0)[0] != QValidator.State.Acceptable


def test_parse_usd():
    assert settings.parse_usd("$1,234.567") == 1234.57
    assert settings.parse_usd("-1") is None and settings.parse_usd("1e8") is None


def test_browser_loads_no_resource(env):
    b = page(env).guide
    assert b.loadResource(2, QUrl("https://evil.example/x.png")) is None
    assert b.loadResource(1, QUrl("file:///etc/passwd")) is None
    assert not b.openLinks() and not b.openExternalLinks()


def test_browser_opens_only_http(env):
    b = page(env).guide
    for u in ("file:///etc/passwd", "javascript:alert(1)", "ftp://x/y", "fumes:settings", "relative.md"):
        b.anchorClicked.emit(QUrl(u))
    assert env.opened == []
    b.anchorClicked.emit(QUrl("https://platform.deepseek.com/api_keys"))
    assert env.opened == ["https://platform.deepseek.com/api_keys"]


def test_every_guide_loads(env):
    for pid in ("deepseek", "openai", "fal", "anthropic"):
        assert page(env, pid).guide.toPlainText().strip()
    assert page(env, "claude").guide.toPlainText().strip()


def test_statusline_command_quoting():
    c = win.statusline_command("C:\\Users\\Jo Smith\\fumes", "C:\\Py 3\\pythonw.exe")
    assert c == ('"C:/Py 3/python.exe" -c "import sys,runpy;sys.path.insert(0,\'C:/Users/Jo Smith/fumes\');'
                 'runpy.run_module(\'fumes.statusline\',run_name=\'__main__\')"')
    assert "pythonw" not in c
    for bad in ("C:/a'b", 'C:/a"b'):
        with pytest.raises(ValueError):
            win.statusline_command(bad, "C:/Py/python.exe")
        with pytest.raises(ValueError):
            win.statusline_command("C:/ok", bad + "/python.exe")


def test_claude_page_offers_no_status_line(env):
    p = page(env, "claude")
    assert not hasattr(p, "snippet") and not hasattr(p, "copy")
    said = " ".join(texts(env))
    assert "statusLine" not in said and "status line" not in said.lower()


def test_claude_last_reading(env, tmp_path):
    p = page(env, "claude")
    p.refresh()
    assert p.last.text() == "No reading yet"
    (tmp_path / "claude-limits.json").write_text(json.dumps({"updated": "2026-11-13T10:00:00Z"}))
    p.refresh()
    assert p.last.text() == "Last reading: 2026-11-13T10:00:00Z"
    (tmp_path / "claude-limits.json").write_text("not json")
    p.refresh()
    assert p.last.text() == "No reading yet"


def test_general_page(env, tmp_path):
    import fumes
    g = page(env, "general")
    assert str(tmp_path) in g.folder.text() and fumes.__version__ in g.version.text()
    assert not g.autostart.isEnabled()    # no action given


def test_general_autostart_follows_action(qapp, tmp_path):
    from PyQt6.QtGui import QAction
    act = QAction("x")
    act.setCheckable(True)
    w = settings.SettingsWindow(keystore.MemoryStore(), collect.Ledger(tmp_path / "l.json"), folder=tmp_path,
                                autostart_action=act)
    g = w.pages["general"]
    g.autostart.setChecked(True)
    assert act.isChecked()
    act.setChecked(False)
    assert not g.autostart.isChecked()


def test_menu_has_settings(qapp, tmp_path):
    from fumes import ui
    from fumes.config import State
    app = ui.App(Config(), State.load(tmp_path / "s.json"), user_logo_dir=tmp_path, store=keystore.MemoryStore(),
                 ledger=collect.Ledger(tmp_path / "l.json"), http=FakeHttp())
    acts = [a.text() for a in app.menu.actions()]
    assert "Settings…" in acts and acts.index("Settings…") < acts.index("Refresh now")
    w = app.open_settings()
    assert app.open_settings() is w and w.isVisible()
    w.close()
    app.tick_timer.stop()
    app.fetch_timer.stop()
    app.tray.hide()


def test_foreign_key_is_refused_on_save_and_test(env):
    p = env.pages["deepseek"]
    p.key.setText("sk-ant-admin01-" + "x" * 30)
    p.do_save()
    assert env.store.get("deepseek") is None and "Anthropic" in p.key_msg.text() and p.key.text() == ""
    p.key.setText("sk-ant-admin01-" + "x" * 30)
    calls = len(env.http.calls)
    p.do_test()
    assert "Anthropic" in p.test_msg.text() and len(env.http.calls) == calls and not p.test_busy()


def test_run_test_is_the_last_gate(env):
    from fumes.collect import deepseek
    out = settings.run_test(deepseek, "sk-proj-" + "y" * 30, None, env.http, env.ledger)
    assert out.startswith("Not sent:") and "OpenAI" in out


def test_admin_key_save_note_and_kind_hint(env):
    p = env.pages["openai"]
    p.key.setText("sk-admin-" + "z" * 30)
    p.do_save()
    assert "Admin keys can manage" in p.key_msg.text() and "Keeping keys safe" in p.key_msg.text()
    p.key.setText("sk-proj-" + "z" * 30)
    p.do_save()
    assert p.key_msg.text().startswith("Saved, but:") and "Admin key" in p.key_msg.text()


def test_remove_says_it_does_not_revoke(env):
    p = env.pages["deepseek"]
    env.store.set("deepseek", "sk-" + "0123456789abcdef" * 2)
    p.refresh()
    p.do_remove()
    assert "does not revoke" in env.asked[-1] and env.store.get("deepseek") is None


def test_safety_page_shows_the_guide(env):
    text = env.pages["safety"].guide.toPlainText()
    assert "Where keys leak" in text and "If a key leaks" in text


def test_general_copies_the_setup_prompt(env, tmp_path):
    from PyQt6.QtGui import QGuiApplication
    from fumes import diagnose
    g = page(env, "general")
    QGuiApplication.clipboard().setText("before")
    assert g.prompt_btn.text() == "Copy setup prompt" and g.prompt_msg.text() == ""
    g.prompt_btn.click()
    got = QGuiApplication.clipboard().text()
    assert got == diagnose.setup_prompt() and "<FOLDER" not in got and "fumes key set" in got
    assert g.prompt_msg.text() == "Copied."
    assert any("never needs your keys" in t for t in texts(env))


def test_the_real_confirmation_box_shows_text_never_html(qapp, tmp_path, monkeypatch):
    """A provider name like <img src=...> must not render: the box is plain text."""
    from PyQt6.QtCore import Qt
    from PyQt6.QtWidgets import QMessageBox
    seen = {}

    def fake_exec(box):
        seen["format"], seen["text"] = box.textFormat(), box.text()
        return QMessageBox.StandardButton.No
    monkeypatch.setattr(QMessageBox, "exec", fake_exec)
    w = settings.SettingsWindow(keystore.MemoryStore(), collect.Ledger(tmp_path / "l.json"), folder=tmp_path,
                                cfg=Config(), opener=lambda u: True)
    assert w.confirm('Remove <img src="file://evil/s/i">?') is False
    assert seen["format"] == Qt.TextFormat.PlainText and "<img" in seen["text"]
    w.close()


def test_claude_page_says_where_the_limits_come_from(qapp, tmp_path, monkeypatch):
    def win(**kw):
        return settings.SettingsWindow(keystore.MemoryStore(), collect.Ledger(tmp_path / "l.json"), folder=tmp_path,
                                       cfg=Config(**kw))
    w = win()
    assert "not found" in w.pages["claude"].source.text()                # conftest: no Claude Code in tests
    w.close()
    monkeypatch.setattr("fumes.usagepoll.find_claude", lambda *a, **k: "/opt/claude")
    w = win()
    assert "asks it every 5 minutes" in w.pages["claude"].source.text()
    w.close()
    w = win(limits_poll_minutes=0)
    assert "turned off" in w.pages["claude"].source.text()
    w.close()
