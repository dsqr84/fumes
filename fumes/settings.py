"""The Settings window: API keys, credit balances, the Claude limits status line, general options.

Rules this module keeps:
- A saved key is never shown, not even in part: the key field is write-only and cleared on save, on
  page change and when the window hides.
- Anything that can touch the network (Test, Set balance) runs on a QThread, never the UI thread.
- Instruction pages are bundled Markdown shown in a browser that never loads a resource and opens only
  http(s) links, through links.open_link.
"""

import json
import tempfile
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

from PyQt6.QtCore import QSize, Qt, QThread, QUrl, pyqtSignal
from PyQt6.QtGui import QColor, QDoubleValidator, QFont, QGuiApplication, QIcon, QPalette, QValidator
from PyQt6.QtWidgets import (QCheckBox, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
                             QMessageBox, QPushButton, QStackedWidget, QTextBrowser, QPlainTextEdit,
                             QVBoxLayout, QWidget)

from . import collect, fonts, keystore, links, logos, statusline, win
from . import __version__

GUIDES = Path(__file__).parent / "guides"
MAX_USD = 1e7
DARK = {"bg": "#202020", "panel": "#2a2a2a", "ink": "#f2f2f2", "ink2": "#a8a8a8", "line": "#3c3c3c",
        "accent": "#2f7d5b", "link": "#6fc2a0", "bad": "#e07a6b", "sel": "#34403a"}
LIGHT = {"bg": "#f3f3f3", "panel": "#ffffff", "ink": "#1c1c1c", "ink2": "#5c5c5c", "line": "#d4d4d4",
         "accent": "#2f7d5b", "link": "#1f6b4a", "bad": "#b3402f", "sel": "#dcebe3"}
_LIVE = set()      # running workers: a QThread must outlive a closed window


class Worker(QThread):
    """Runs fn() off the UI thread; emits ("ok", result) or ("err", exception)."""
    finished_with = pyqtSignal(object)

    def __init__(self, fn):
        super().__init__()
        self.fn = fn
        _LIVE.add(self)
        self.finished.connect(lambda: _LIVE.discard(self))

    def run(self):
        try:
            self.finished_with.emit(("ok", self.fn()))
        except Exception as e:  # noqa: BLE001 (nothing may escape the thread)
            self.finished_with.emit(("err", e))


class SafeBrowser(QTextBrowser):
    """Shows a bundled Markdown guide. Never loads a resource (no remote images or files); a clicked
    link opens only when it is http(s), through `opener`."""

    def __init__(self, opener, parent=None, link="#6fc2a0"):
        super().__init__(parent)
        self.opener = opener
        self.document().setDefaultStyleSheet(f"a {{ color: {link}; }}")
        pal = self.palette()       # Qt's Markdown links take the palette's colour, not the style sheet's
        pal.setColor(QPalette.ColorRole.Link, QColor(link))
        pal.setColor(QPalette.ColorRole.LinkVisited, QColor(link))
        self.setPalette(pal)
        self.setOpenLinks(False)
        self.setOpenExternalLinks(False)
        self.setReadOnly(True)
        self.anchorClicked.connect(self._clicked)

    def loadResource(self, type_, name):    # noqa: N802, ARG002
        return None

    def setMarkdown(self, text):        # noqa: N802
        """Markdown links come in with Qt's own blue baked into each fragment, so recolour them after."""
        super().setMarkdown(text)
        from PyQt6.QtGui import QTextCharFormat, QTextCursor
        fmt = QTextCharFormat()
        fmt.setForeground(self.palette().color(QPalette.ColorRole.Link))
        block = self.document().begin()
        while block.isValid():
            it = block.begin()
            while not it.atEnd():
                frag = it.fragment()
                if frag.isValid() and frag.charFormat().isAnchor():
                    cur = QTextCursor(self.document())
                    cur.setPosition(frag.position())
                    cur.setPosition(frag.position() + frag.length(), QTextCursor.MoveMode.KeepAnchor)
                    cur.mergeCharFormat(fmt)
                it += 1
            block = block.next()

    def _clicked(self, url):
        text = url.toString()
        if text.startswith(("http://", "https://")):
            self.opener(text)


def parse_usd(text):
    """'$1,234.50' -> 1234.5; None when it is not a dollar amount from 0 to 10,000,000."""
    t = (text or "").strip().lstrip("$").replace(",", "").strip()
    if not t:
        return None
    try:
        v = float(t)
    except ValueError:
        return None
    if not (0 <= v <= MAX_USD) or v != v:
        return None
    return round(v, 2)


class UsdValidator(QDoubleValidator):
    """A dollar amount while typing: a leading $ and commas are allowed."""

    def __init__(self, parent=None):
        super().__init__(0.0, MAX_USD, 2, parent)
        self.setNotation(QDoubleValidator.Notation.StandardNotation)

    def validate(self, text, pos):
        cleaned = text.strip().lstrip("$").replace(",", "")
        if cleaned == "":
            return QValidator.State.Intermediate, text, pos
        state, _, _ = super().validate(cleaned, len(cleaned))
        if state == QValidator.State.Acceptable and parse_usd(text) is None:
            state = QValidator.State.Invalid
        return state, text, pos


def load_guide(pid):
    """The guide's Markdown without its own title: the page header already names the provider."""
    try:
        text = (GUIDES / f"{pid}.md").read_text("utf-8")
    except OSError:
        return "No instructions for this page."
    lines = text.lstrip().splitlines()
    if lines and lines[0].startswith("# "):
        lines = lines[1:]
    return "\n".join(lines).lstrip()


def run_test(mod, key, now, http, ledger):
    """One run of a provider with `key`; returns the text to show. Runs on a worker thread."""
    try:
        ctx = collect.Context(now=now, key=key, http=http, ledger=ledger, pid=mod.ID)
        got = mod.run(ctx)
        parts = []
        if got.get("balance") is not None:
            parts.append(f"balance {collect.money(got['balance'])}")
        mtd = collect.summarise(got.get("rows") or [], now)["mtd"]
        parts.append(f"month to date {collect.money(mtd)}")
        return "Working: " + ", ".join(parts)
    except collect.NotConnected as e:
        return f"Not connected: {e}"
    except collect.ProviderError as e:
        return f"Failed: {e}"
    except Exception as e:  # noqa: BLE001
        return f"Failed: {type(e).__name__}"


class Page(QWidget):
    def label(self, text="", name=None, wrap=True):
        lb = QLabel(text)
        lb.setWordWrap(wrap)
        lb.setTextFormat(Qt.TextFormat.PlainText)
        if name:
            lb.setObjectName(name)
        return lb


class ProviderPage(Page):
    def __init__(self, win_, mod):
        super().__init__()
        self.w, self.mod = win_, mod
        self._busy = {}
        lay = QVBoxLayout(self)
        lay.setSpacing(10)

        head = QHBoxLayout()
        self.logo = QLabel()
        self.logo.setFixedSize(32, 32)
        self.logo.setPixmap(win_.logo(mod.ID, mod.NAME, 32))
        head.addWidget(self.logo)
        col = QVBoxLayout()
        col.setSpacing(0)
        self.title = self.label(mod.NAME, "title", wrap=False)
        self.status = self.label("", "status", wrap=False)
        col.addWidget(self.title)
        col.addWidget(self.status)
        head.addLayout(col, 1)
        lay.addLayout(head)

        row = QHBoxLayout()
        self.key = QLineEdit()
        self.key.setEchoMode(QLineEdit.EchoMode.Password)
        self.key.setPlaceholderText(f"Paste your {mod.KEY_LABEL}")
        self.key.setAutoFillBackground(False)
        self.save = QPushButton("Save")
        self.remove = QPushButton("Remove")
        row.addWidget(self.key, 1)
        row.addWidget(self.save)
        row.addWidget(self.remove)
        lay.addLayout(row)
        self.key_msg = self.label("", "msg")
        lay.addWidget(self.key_msg)
        self.save.clicked.connect(self.do_save)
        self.key.returnPressed.connect(self.do_save)
        self.remove.clicked.connect(self.do_remove)

        trow = QHBoxLayout()
        self.test = QPushButton("Test")
        self.test_msg = self.label("", "msg")
        trow.addWidget(self.test)
        trow.addWidget(self.test_msg, 1)
        lay.addLayout(trow)
        self.test.clicked.connect(self.do_test)

        self.anchor_box = QWidget()
        self.anchor_edit = self.anchor_set = self.anchor_line = self.anchor_msg = None
        if getattr(mod, "ANCHORED", False):
            al = QVBoxLayout(self.anchor_box)
            al.setContentsMargins(0, 0, 0, 0)
            al.addWidget(self.label("Credit balance: the amount your billing page shows now.", "hint"))
            arow = QHBoxLayout()
            self.anchor_edit = QLineEdit()
            self.anchor_edit.setPlaceholderText("$0.00")
            self.anchor_edit.setValidator(UsdValidator(self.anchor_edit))
            self.anchor_set = QPushButton("Set")
            arow.addWidget(self.anchor_edit, 1)
            arow.addWidget(self.anchor_set)
            al.addLayout(arow)
            self.anchor_line = self.label("", "msg")
            al.addWidget(self.anchor_line)
            self.anchor_msg = self.label("", "msg")
            al.addWidget(self.anchor_msg)
            self.anchor_set.clicked.connect(self.do_anchor)
            self.anchor_edit.returnPressed.connect(self.do_anchor)
            lay.addWidget(self.anchor_box)

        self.guide = SafeBrowser(win_.open_link, link=win_.colors['link'])
        self.guide.setMarkdown(load_guide(mod.ID))
        lay.addWidget(self.guide, 1)
        self.refresh()

    # ---- state ------------------------------------------------------------
    def refresh(self):
        has = self.w.store.has(self.mod.ID)
        self.status.setText("Key saved" if has else "No key yet")
        self.remove.setVisible(has)
        if self.anchor_line is not None:
            a = self.w.ledger.anchor(self.mod.ID)
            self.anchor_line.setText(
                f"Entered {collect.money(a['usd'])} on {a['ts'][:10]}" if a else "Not entered yet")

    def clear_key(self):
        self.key.setText("")

    def _error(self, label, text):
        label.setText(text)
        label.setProperty("bad", True)
        label.style().unpolish(label)
        label.style().polish(label)

    def _note(self, label, text):
        label.setText(text)
        label.setProperty("bad", False)
        label.style().unpolish(label)
        label.style().polish(label)

    # ---- key --------------------------------------------------------------
    def do_save(self):
        value = keystore.valid_key(self.key.text())
        if value is None:
            self._error(self.key_msg, "That does not look like a key.")
            return
        try:
            self.w.store.set(self.mod.ID, value)
        except keystore.KeyStoreError as e:
            self._error(self.key_msg, str(e))
            return
        self.key.setText("")
        self._note(self.key_msg, "Saved.")
        self.refresh()

    def do_remove(self):
        ask = self.w.confirm(f"Remove the saved {self.mod.NAME} key?")
        if not ask:
            return
        try:
            self.w.store.delete(self.mod.ID)
        except keystore.KeyStoreError as e:
            self._error(self.key_msg, str(e))
            return
        self._note(self.key_msg, "Removed.")
        self.refresh()

    # ---- test -------------------------------------------------------------
    def do_test(self):
        if self.test_busy():
            return
        typed = self.key.text()
        if typed.strip():
            key = keystore.valid_key(typed)
            if key is None:
                self._error(self.test_msg, "That does not look like a key.")
                return
        else:
            try:
                key = self.w.store.get(self.mod.ID)
            except keystore.KeyStoreError as e:
                self._error(self.test_msg, str(e))
                return
        if not key:
            self._error(self.test_msg, "Not connected: No key saved. Add one above.")
            return
        # a throwaway copy of the ledger: a test run must not record balance samples in the real one
        scratch = collect.Ledger(Path(tempfile.gettempdir()) / "fumes-test-ledger.json")
        scratch.anchors = deepcopy(self.w.ledger.anchors)
        scratch.samples = deepcopy(self.w.ledger.samples)
        http = self.w.http or collect.Http()
        now = datetime.now(timezone.utc)
        mod = self.mod
        self.test.setEnabled(False)
        self._note(self.test_msg, "Testing...")
        self._start("test", lambda: run_test(mod, key, now, http, scratch), self._test_done)

    def test_busy(self):
        return "test" in self._busy

    def _test_done(self, result):
        self.test.setEnabled(True)
        kind, val = result
        self._note(self.test_msg, val if kind == "ok" else f"Failed: {type(val).__name__}")

    # ---- credit balance ---------------------------------------------------
    def do_anchor(self):
        if self.anchor_edit is None or "anchor" in self._busy:
            return
        usd = parse_usd(self.anchor_edit.text())
        if usd is None:
            self._error(self.anchor_msg, "Enter a dollar amount, from $0 to $10,000,000.")
            return
        self.anchor_set.setEnabled(False)
        self._note(self.anchor_msg, "Saving...")
        pid, store, ledger, http = self.mod.ID, self.w.store, self.w.ledger, self.w.http or collect.Http()
        now = datetime.now(timezone.utc)
        self._start("anchor", lambda: collect.set_anchor(pid, usd, now, store, ledger, http),
                    self._anchor_done)

    def _anchor_done(self, result):
        self.anchor_set.setEnabled(True)
        kind, val = result
        if kind == "err":
            self._error(self.anchor_msg, str(val) if isinstance(val, ValueError) else f"Failed: {type(val).__name__}")
            return
        _, warning = val
        try:
            self.w.ledger.save()
        except OSError:
            self._error(self.anchor_msg, "Could not save the balance.")
            return
        self.anchor_edit.setText("")
        self._note(self.anchor_msg, warning or "Saved.")
        self.refresh()
        self.w.refresh_app()

    # ---- threads ----------------------------------------------------------
    def _start(self, name, fn, done):
        t = Worker(fn)
        self._busy[name] = t

        def finish(result, name=name):
            self._busy.pop(name, None)
            done(result)

        t.finished_with.connect(finish)
        t.start()


class ClaudePage(Page):
    NOTE = "If you already have a status line, this replaces it."

    def __init__(self, win_):
        super().__init__()
        self.w = win_
        lay = QVBoxLayout(self)
        lay.setSpacing(10)
        head = QHBoxLayout()
        logo = QLabel()
        logo.setFixedSize(32, 32)
        logo.setPixmap(win_.logo("claude", "Claude", 32))
        head.addWidget(logo)
        head.addWidget(self.label("Claude limits", "title", wrap=False), 1)
        lay.addLayout(head)
        self.guide = SafeBrowser(win_.open_link, link=win_.colors['link'])
        self.guide.setMarkdown(load_guide("claude"))
        lay.addWidget(self.guide, 1)

        self.snippet = QPlainTextEdit()
        self.snippet.setReadOnly(True)
        self.snippet.setObjectName("snippet")
        mono = QFont("monospace")
        mono.setStyleHint(QFont.StyleHint.Monospace)
        self.snippet.setFont(mono)
        self.snippet.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        self.snippet.setFixedHeight(150)
        self.copy = QPushButton("Copy")
        self.copy_msg = self.label("", "msg", wrap=False)
        self.snippet_text = self.build_snippet()
        self.snippet.setPlainText(self.snippet_text)
        lay.addWidget(self.snippet)
        row = QHBoxLayout()
        row.addWidget(self.copy)
        row.addWidget(self.copy_msg, 1)
        lay.addLayout(row)
        lay.addWidget(self.label(self.NOTE, "hint"))
        self.last = self.label("", "msg")
        lay.addWidget(self.last)
        self.copy.clicked.connect(self.do_copy)
        self.refresh()

    def build_snippet(self):
        try:
            cmd = win.statusline_command()
        except ValueError as e:
            return f"Cannot build the command: {e}"
        return json.dumps({"statusLine": {"type": "command", "command": cmd}}, indent=2)

    def do_copy(self):
        QGuiApplication.clipboard().setText(self.snippet_text)
        self.copy_msg.setText("Copied.")

    def last_reading(self):
        try:
            with open(self.w.folder / statusline.FILE, "rb") as f:
                doc = json.loads(f.read(65536).decode("utf-8"))
            upd = doc.get("updated") if isinstance(doc, dict) else None
            return str(upd) if isinstance(upd, str) and upd else None
        except (OSError, ValueError):
            return None

    def refresh(self):
        upd = self.last_reading()
        self.last.setText(f"Last reading: {upd}" if upd else "No reading yet")


class GeneralPage(Page):
    def __init__(self, win_):
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setSpacing(10)
        lay.addWidget(self.label("General", "title", wrap=False))
        self.autostart = QCheckBox("Start with Windows")
        act = win_.autostart_action
        if act is None:
            self.autostart.setEnabled(False)
        else:
            self.autostart.setEnabled(act.isEnabled())
            self.autostart.setChecked(act.isChecked())
            self.autostart.toggled.connect(lambda on: act.setChecked(on) if act.isChecked() != on else None)
            act.toggled.connect(self._sync)
        lay.addWidget(self.autostart)
        self.folder = self.label(f"Config folder: {win_.folder}", "msg")
        self.version = self.label(f"Version: {__version__}", "msg")
        for lb in (self.folder, self.version):
            lb.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            lay.addWidget(lb)
        lay.addStretch(1)

    def _sync(self, on):
        if self.autostart.isChecked() != on:
            self.autostart.blockSignals(True)
            self.autostart.setChecked(on)
            self.autostart.blockSignals(False)


class SettingsWindow(QWidget):
    def __init__(self, store, ledger, http=None, folder=None, cfg=None, autostart_action=None,
                 on_refresh=None, theme="dark", opener=None, confirm=None, dpr=None):
        super().__init__()
        self.store, self.ledger, self.http = store, ledger, http
        self.folder = Path(folder) if folder else Path(tempfile.gettempdir())
        self.cfg, self.autostart_action, self.on_refresh = cfg, autostart_action, on_refresh
        self.theme = theme if theme in ("dark", "light") else "dark"
        self.colors = DARK if self.theme == "dark" else LIGHT
        self._opener, self._confirm, self.dpr = opener, confirm, dpr
        self.setWindowTitle("Fumes Settings")
        self.resize(860, 600)
        fonts.apply(self)
        self.setStyleSheet(self._css())

        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        self.nav = QListWidget()
        self.nav.setObjectName("nav")
        self.nav.setFixedWidth(210)
        self.nav.setIconSize(QSize(20, 20))
        self.stack = QStackedWidget()
        body = QWidget()
        bl = QVBoxLayout(body)
        bl.setContentsMargins(22, 20, 22, 20)
        bl.addWidget(self.stack)
        outer.addWidget(self.nav)
        outer.addWidget(body, 1)

        self.ids = []
        self.pages = {}
        for mod in collect.providers():
            self._add(mod.ID, mod.NAME, ProviderPage(self, mod))
        self._add("claude", "Claude limits", ClaudePage(self))
        self._add("general", "General", GeneralPage(self))
        self.nav.currentRowChanged.connect(self._changed)
        self.nav.setCurrentRow(0)

    # ---- plumbing ---------------------------------------------------------
    def _css(self):
        c = self.colors
        return f"""
        QWidget {{ background: {c['bg']}; color: {c['ink']}; font-size: 13px; }}
        QListWidget#nav {{ background: {c['panel']}; border: none; border-right: 1px solid {c['line']};
                          padding: 8px 6px; outline: 0; }}
        QListWidget#nav::item {{ padding: 8px 10px; border-radius: 6px; }}
        QListWidget#nav::item:selected {{ background: {c['sel']}; color: {c['ink']}; }}
        QLabel#title {{ font-size: 18px; font-weight: 600; }}
        QLabel#status, QLabel#hint {{ color: {c['ink2']}; }}
        QLabel#msg {{ color: {c['ink2']}; }}
        QLabel[bad="true"] {{ color: {c['bad']}; }}
        QLineEdit, QTextBrowser, QPlainTextEdit {{ background: {c['panel']}; border: 1px solid {c['line']};
                                                  border-radius: 6px; padding: 6px; }}
        QPushButton {{ background: {c['panel']}; border: 1px solid {c['line']}; border-radius: 6px;
                      padding: 6px 16px; }}
        QPushButton:hover {{ border-color: {c['accent']}; }}
        QPushButton:disabled {{ color: {c['ink2']}; }}
        QCheckBox {{ spacing: 8px; }}
        """

    def _add(self, pid, name, page):
        self.ids.append(pid)
        self.pages[pid] = page
        self.stack.addWidget(page)
        item = QListWidgetItem(name)
        if pid != "general":
            item.setIcon(QIcon(self.logo(pid, name, 20)))
        self.nav.addItem(item)

    def logo(self, pid, name, size):
        dpr = self.dpr or self.devicePixelRatioF()
        return logos.render_logo(pid, name, self.colors["ink"], dpr, size, self.folder, self.font())

    def open_link(self, url):
        if self._opener is not None:
            return self._opener(url)
        if self.cfg is None:
            return False
        return links.open_link(self.cfg, url)

    def confirm(self, text):
        if self._confirm is not None:
            return self._confirm(text)
        box = QMessageBox(self)
        box.setWindowTitle("Fumes")
        box.setText(text)
        box.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        box.setDefaultButton(QMessageBox.StandardButton.No)
        return box.exec() == QMessageBox.StandardButton.Yes

    def refresh_app(self):
        if self.on_refresh:
            self.on_refresh()

    def clear_keys(self):
        for page in self.pages.values():
            if isinstance(page, ProviderPage):
                page.clear_key()

    def _changed(self, row):
        self.clear_keys()
        if 0 <= row < self.stack.count():
            self.stack.setCurrentIndex(row)
            page = self.stack.currentWidget()
            if hasattr(page, "refresh"):
                page.refresh()

    def show_page(self, pid):
        if pid in self.ids:
            self.nav.setCurrentRow(self.ids.index(pid))

    def hideEvent(self, ev):    # noqa: N802
        self.clear_keys()
        super().hideEvent(ev)

    def closeEvent(self, ev):   # noqa: N802
        self.clear_keys()
        super().closeEvent(ev)

    def render_png(self, path, dpr=1.0):
        """Render the whole window to a PNG (off-screen). Build the window with dpr= for sharp logos."""
        from PyQt6.QtGui import QImage, QPainter
        self.ensurePolished()
        img = QImage(round(self.width() * dpr), round(self.height() * dpr), QImage.Format.Format_ARGB32)
        img.setDevicePixelRatio(dpr)
        img.fill(0)
        p = QPainter(img)
        self.render(p)
        p.end()
        return img.save(str(path))
