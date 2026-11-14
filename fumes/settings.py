"""The Settings window: API keys, credit balances, the Claude limits status line, general options.

Rules this module keeps:
- A saved key is never shown, not even in part: the key field is write-only and cleared on save, on
  page change and when the window hides.
- Anything that can touch the network (Test, Set balance) runs on a QThread, never the UI thread.
- Instruction pages are bundled Markdown shown in a browser that never loads a resource and opens only
  http(s) links, through links.open_link.
"""

import json
import re
import shlex
import tempfile
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

from PyQt6.QtCore import QSize, Qt, QThread, QUrl, pyqtSignal
from PyQt6.QtGui import QColor, QDoubleValidator, QFont, QGuiApplication, QIcon, QPalette, QValidator
from PyQt6.QtWidgets import (QCheckBox, QComboBox, QDialog, QFileDialog, QFormLayout, QHBoxLayout, QLabel,
                             QLineEdit, QListWidget, QListWidgetItem, QMessageBox, QPushButton, QSpinBox,
                             QStackedWidget, QTextBrowser, QPlainTextEdit, QVBoxLayout, QWidget)

from . import collect, diagnose, fonts, keycheck, keystore, links, logos, statusline, win
from . import __version__
from .collect import custom as customlib

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
    bad = keycheck.foreign(mod.ID, key) if key else None
    if bad:                      # the page checks too; this is the last gate before anything is sent
        return f"Not sent: {bad}"
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


def quote_arg(a):
    """One argument for display: in double quotes, a backslash stays a backslash (a path reads as typed)."""
    return '"' + a.replace('"', '\\"') + '"'


def describe_command(argv, timeout):
    """Read-only text for a command provider: the program, then each argument on its own line."""
    lines = [f"Program: {argv[0]}"]
    if len(argv) > 1:
        lines.append("Arguments:")
        lines += [f"    {quote_arg(a)}" for a in argv[1:]]
    else:
        lines.append("Arguments: none")
    lines.append(f"Timeout: {timeout} s")
    return "\n".join(lines)


def split_args(text):
    """The Arguments field -> a list, the way Windows splits a command line: shlex with posix=False (a
    backslash is just a backslash), then one pair of surrounding quotes taken off each piece. Raises
    ValueError('...') for an unbalanced quote."""
    out = []
    for tok in shlex.split(text or "", posix=False):
        if len(tok) >= 2 and tok[0] == tok[-1] and tok[0] in "\"'":
            tok = tok[1:-1]
        out.append(tok)
    return out


def slug(name):
    return re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")[:32]


class AddProviderDialog(QDialog):
    """Name, id and kind of a new provider; validated with customlib.validate before anything is saved."""

    def __init__(self, win_):
        super().__init__(win_)
        self.w = win_
        self.setWindowTitle("Add provider")
        self.setModal(True)
        self.setMinimumWidth(480)
        self._id_touched = False
        lay = QVBoxLayout(self)
        lay.setSpacing(10)
        form = QFormLayout()
        form.setSpacing(8)
        self.name = QLineEdit()
        self.name.setMaxLength(customlib.MAX_NAME)
        self.name.setPlaceholderText("Shown on the strip")
        self.pid = QLineEdit()
        self.pid.setMaxLength(32)
        self.kind = QComboBox()
        self.kind.addItems(["Manual", "Command"])
        form.addRow("Name", self.name)
        form.addRow("Id", self.pid)
        form.addRow("Kind", self.kind)
        lay.addLayout(form)

        self.manual_box = QWidget()
        mf = QFormLayout(self.manual_box)
        mf.setContentsMargins(0, 0, 0, 0)
        self.burn = QLineEdit()
        self.burn.setPlaceholderText("optional, e.g. $1.50")
        self.burn.setValidator(UsdValidator(self.burn))
        mf.addRow("Daily burn", self.burn)
        lay.addWidget(self.manual_box)

        self.command_box = QWidget()
        cf = QFormLayout(self.command_box)
        cf.setContentsMargins(0, 0, 0, 0)
        prow = QHBoxLayout()
        self.program = QLineEdit()
        self.program.setPlaceholderText(r"C:\Tools\balance.exe")
        self.browse_btn = QPushButton("Browse...")
        prow.addWidget(self.program, 1)
        prow.addWidget(self.browse_btn)
        cf.addRow("Program", prow)
        self.args = QLineEdit()
        self.args.setPlaceholderText('--json "a value with spaces"')
        cf.addRow("Arguments", self.args)
        self.preview = QLabel("")
        self.preview.setObjectName("hint")
        self.preview.setWordWrap(True)
        self.preview.setTextFormat(Qt.TextFormat.PlainText)
        self.preview.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        cf.addRow("Will run", self.preview)
        self.needs_key = QCheckBox("Needs a key (passed to the program as FUMES_KEY)")
        cf.addRow("", self.needs_key)
        self.timeout = QSpinBox()
        self.timeout.setRange(1, customlib.MAX_TIMEOUT)
        self.timeout.setValue(customlib.TIMEOUT)
        self.timeout.setSuffix(" s")
        cf.addRow("Timeout", self.timeout)
        lay.addWidget(self.command_box)

        self.msg = QLabel("")
        self.msg.setObjectName("msg")
        self.msg.setWordWrap(True)
        self.msg.setTextFormat(Qt.TextFormat.PlainText)
        lay.addWidget(self.msg)
        row = QHBoxLayout()
        row.addStretch(1)
        self.cancel_btn = QPushButton("Cancel")
        self.add_btn = QPushButton("Add")
        self.add_btn.setDefault(True)
        row.addWidget(self.cancel_btn)
        row.addWidget(self.add_btn)
        lay.addLayout(row)

        self.name.textChanged.connect(self._name_changed)
        self.pid.textEdited.connect(lambda _t: setattr(self, "_id_touched", True))
        self.kind.currentIndexChanged.connect(self._kind_changed)
        self.program.textChanged.connect(self._preview)
        self.args.textChanged.connect(self._preview)
        self.browse_btn.clicked.connect(self.browse)
        self.cancel_btn.clicked.connect(self.reject)
        self.add_btn.clicked.connect(self.accept)
        self._kind_changed()

    def _name_changed(self, text):
        if not self._id_touched:
            self.pid.setText(slug(text))

    def _kind_changed(self, _i=None):
        manual = self.kind.currentIndex() == 0
        self.manual_box.setVisible(manual)
        self.command_box.setVisible(not manual)
        self._preview()
        self.adjustSize()

    def browse(self):
        path, _ = QFileDialog.getOpenFileName(self, "Choose the program")
        if path:
            self.program.setText(path)

    def argv(self):
        """[program, *arguments], or raises ValueError (an unbalanced quote)."""
        prog = self.program.text().strip()
        return ([prog] if prog else []) + split_args(self.args.text())

    def _preview(self, *_):
        try:
            argv = self.argv()
        except ValueError:
            self.preview.setText("A quote is not closed.")
            return
        self.preview.setText("[" + ", ".join(quote_arg(a) for a in argv) + "]" if argv else "(nothing yet)")

    def spec(self):
        spec = {"id": self.pid.text().strip(), "name": self.name.text().strip(),
                "kind": "manual" if self.kind.currentIndex() == 0 else "command"}
        if spec["kind"] == "manual":
            if self.burn.text().strip():
                burn = parse_usd(self.burn.text())
                spec["burn_per_day"] = burn if burn else -1      # validate() rejects it with its own message
        else:
            spec["argv"] = self.argv()
            spec["timeout"] = self.timeout.value()
            spec["key"] = self.needs_key.isChecked()
        return spec

    def accept(self):
        try:
            self.w.add_provider(self.spec())
        except customlib.ProviderSpecError as e:
            self._show(str(e), bad=True)
            return
        except ValueError:
            self._show("A quote in the arguments is not closed.", bad=True)
            return
        except OSError:
            self._show("Could not save the provider list.", bad=True)
            return
        super().accept()

    def _show(self, text, bad):
        self.msg.setText(text)
        self.msg.setProperty("bad", bad)
        self.msg.style().unpolish(self.msg)
        self.msg.style().polish(self.msg)


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
        self.custom = isinstance(mod, customlib.Custom)      # a provider the user added
        self.needs_key = bool(mod.KEY_LABEL)
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
        self.key_box = QWidget()
        kl = QVBoxLayout(self.key_box)
        kl.setContentsMargins(0, 0, 0, 0)
        kl.addLayout(row)
        self.key_msg = self.label("", "msg")
        kl.addWidget(self.key_msg)
        self.key_box.setVisible(self.needs_key)
        lay.addWidget(self.key_box)
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

        self.cmd_view = self.burn_line = self.remove_provider = None
        if self.custom:
            self._custom_blocks(lay)
        self.guide = SafeBrowser(win_.open_link, link=win_.colors['link'])
        self.guide.setMarkdown(load_guide(mod.kind if self.custom else mod.ID))
        lay.addWidget(self.guide, 1)
        if self.custom:
            self.remove_provider = QPushButton("Remove provider")
            self.remove_provider.setObjectName("remove_provider")
            self.remove_provider.clicked.connect(self.do_remove_provider)
            rrow = QHBoxLayout()
            rrow.addWidget(self.remove_provider)
            rrow.addStretch(1)
            lay.addLayout(rrow)
        self.refresh()

    def _custom_blocks(self, lay):
        mod = self.mod
        if mod.kind == "manual":
            self.burn_line = self.label(
                f"Daily burn: {collect.money(mod.burn)} (counted down from the balance, shown as an estimate)"
                if mod.burn else "No daily burn: the balance stays as entered.", "hint")
            lay.insertWidget(1, self.burn_line)
        else:
            self.cmd_view = self.label(describe_command(mod.argv, mod.timeout), "cmdview")
            self.cmd_view.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            lay.insertWidget(1, self.cmd_view)

    # ---- state ------------------------------------------------------------
    def refresh(self):
        has = self.w.store.has(self.mod.ID)
        if self.custom:
            kind = self.mod.kind.capitalize()
            self.status.setText(f"{kind}, {'key saved' if has else 'no key yet'}" if self.needs_key else kind)
        else:
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
        bad = keycheck.foreign(self.mod.ID, value)
        if bad:
            self.key.setText("")
            self._error(self.key_msg, bad)
            return
        try:
            self.w.store.set(self.mod.ID, value)
        except keystore.KeyStoreError as e:
            self._error(self.key_msg, str(e))
            return
        self.key.setText("")
        hint = keycheck.kind_hint(self.mod.ID, value)
        if hint:
            self._error(self.key_msg, "Saved, but: " + hint)
        elif "admin" in self.mod.KEY_LABEL.lower():
            self._note(self.key_msg, "Saved. Admin keys can manage your whole organisation: use one made only "
                                     "for Fumes. See Keeping keys safe.")
        else:
            self._note(self.key_msg, "Saved.")
        self.refresh()

    def do_remove(self):
        ask = self.w.confirm(f"Remove the saved {self.mod.NAME} key from this computer?\n\n"
                             f"This does not revoke it. If you are finished with it, also delete it on "
                             f"{self.mod.NAME}'s website, so it stops working everywhere.")
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
        typed = self.key.text() if self.needs_key else ""
        if not self.needs_key:
            key = None
        elif typed.strip():
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
        if self.needs_key:
            if not key:
                self._error(self.test_msg, "Not connected: No key saved. Add one above.")
                return
            bad = keycheck.foreign(self.mod.ID, key)
            if bad:
                self._error(self.test_msg, bad)
                return
            hint = keycheck.kind_hint(self.mod.ID, key)
            if hint:
                self._error(self.test_msg, hint)
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
        mine = (self.mod,) if self.custom else ()
        self._start("anchor", lambda: collect.set_anchor(pid, usd, now, store, ledger, http, custom=mine),
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

    # ---- removing a provider you added ------------------------------------
    def do_remove_provider(self):
        if not self.w.confirm(f"Remove the provider {self.mod.NAME}?\n\nIt leaves the strip and Settings. "
                              "Nothing on the service itself changes."):
            return
        pid = self.mod.ID
        key = self.needs_key and self.w.store.has(pid) and self.w.confirm(
            f"Also delete the key saved for {self.mod.NAME} from this computer?")
        anchor = self.w.ledger.anchor(pid) is not None and self.w.confirm(
            f"Also delete the balance you entered for {self.mod.NAME}?")
        self.w.remove_provider(pid, delete_key=key, delete_anchor=anchor)

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


class SafetyPage(Page):
    """Keeping keys safe: the guide, read-only."""

    def __init__(self, win_):
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setSpacing(10)
        lay.addWidget(self.label("Keeping keys safe", "title", wrap=False))
        self.guide = SafeBrowser(win_.open_link, link=win_.colors['link'])
        self.guide.setMarkdown(load_guide("key-safety"))
        lay.addWidget(self.guide, 1)

    def refresh(self):
        pass


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
        self.prompt_btn = QPushButton("Copy setup prompt")
        self.prompt_btn.setObjectName("copy_setup_prompt")
        self.prompt_msg = self.label("", "msg", wrap=False)
        self.prompt_btn.clicked.connect(self.copy_prompt)
        row = QHBoxLayout()
        row.addWidget(self.prompt_btn)
        row.addWidget(self.prompt_msg, 1)
        lay.addLayout(row)
        lay.addWidget(self.label("Paste it into Claude Code or another assistant that can run commands. "
                                 "It never needs your keys.", "hint"))
        self.folder = self.label(f"Config folder: {win_.folder}", "msg")
        self.version = self.label(f"Version: {__version__}", "msg")
        for lb in (self.folder, self.version):
            lb.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            lay.addWidget(lb)
        lay.addStretch(1)

    def copy_prompt(self):
        QGuiApplication.clipboard().setText(diagnose.setup_prompt())
        self.prompt_msg.setText("Copied.")

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
        self.nav.setIconSize(QSize(20, 20))
        navcol = QWidget()
        navcol.setObjectName("navcol")
        navcol.setFixedWidth(210)
        nl = QVBoxLayout(navcol)
        nl.setContentsMargins(0, 0, 0, 0)
        nl.setSpacing(0)
        self.add_btn = QPushButton("Add provider...")
        self.add_btn.setObjectName("add_provider")
        self.add_btn.clicked.connect(self.open_add_dialog)
        nl.addWidget(self.nav, 1)
        nl.addWidget(self.add_btn)
        self.stack = QStackedWidget()
        body = QWidget()
        bl = QVBoxLayout(body)
        bl.setContentsMargins(22, 20, 22, 20)
        bl.addWidget(self.stack)
        outer.addWidget(navcol)
        outer.addWidget(body, 1)

        self.ids = []
        self.pages = {}
        for mod in collect.providers():
            self._add(mod.ID, mod.NAME, ProviderPage(self, mod))
        self.n_builtin = len(self.ids)
        self.custom = []                 # [Custom] from providers.json; their pages sit after the built-ins
        self.reload_custom()
        self._add("claude", "Claude limits", ClaudePage(self))
        self._add("safety", "Keeping keys safe", SafetyPage(self))
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
        QLabel#cmdview {{ background: {c['panel']}; border: 1px solid {c['line']}; border-radius: 6px;
                         padding: 6px; font-family: monospace; }}
        QWidget#navcol {{ background: {c['panel']}; }}
        QPushButton#add_provider {{ margin: 8px 10px; }}
        QComboBox, QSpinBox {{ background: {c['panel']}; border: 1px solid {c['line']}; border-radius: 6px;
                              padding: 5px 8px; }}
        QDialog {{ background: {c['bg']}; }}
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

    def _add(self, pid, name, page, at=None):
        at = len(self.ids) if at is None else at
        self.ids.insert(at, pid)
        self.pages[pid] = page
        self.stack.insertWidget(at, page)
        item = QListWidgetItem(name)
        if pid not in ("general", "safety"):
            item.setIcon(QIcon(self.logo(pid, name, 20)))
        self.nav.insertItem(at, item)

    # ---- providers the user added -------------------------------------------
    def reload_custom(self, select=None):
        """Rebuild the custom pages from providers.json (after Settings changed it)."""
        keep = select or (self.ids[self.nav.currentRow()] if self.nav.currentRow() >= 0 else None)
        self.nav.blockSignals(True)
        for mod in self.custom:
            row = self.ids.index(mod.ID)
            self.ids.pop(row)
            page = self.pages.pop(mod.ID)
            self.stack.removeWidget(page)
            page.deleteLater()
            self.nav.takeItem(row)
        self.custom = customlib.load(self.folder)
        for i, mod in enumerate(self.custom):
            self._add(mod.ID, mod.NAME, ProviderPage(self, mod), at=self.n_builtin + i)
        self.nav.blockSignals(False)
        row = self.ids.index(keep) if keep in self.ids else 0
        self.nav.setCurrentRow(row)
        self._changed(row)

    def open_add_dialog(self):
        dlg = AddProviderDialog(self)
        dlg.open()
        self.add_dialog = dlg
        return dlg

    def add_provider(self, spec):
        """Validate, save to providers.json and show the new page. Raises ProviderSpecError / OSError."""
        clean = customlib.validate(spec, taken={m.ID for m in self.custom})
        customlib.save(self.folder, [m.spec for m in self.custom] + [clean])
        self.reload_custom(select=clean["id"])
        self.refresh_app()

    def remove_provider(self, pid, delete_key=False, delete_anchor=False):
        specs = [m.spec for m in self.custom if m.ID != pid]
        customlib.save(self.folder, specs)
        notes = []
        if delete_key:
            try:
                self.store.delete(pid)
            except keystore.KeyStoreError as e:
                notes.append(str(e))
        if delete_anchor:
            self.ledger.clear_anchor(pid)
            try:
                self.ledger.save()
            except OSError:
                notes.append("Could not save the ledger.")
        self.reload_custom(select="general")
        self.refresh_app()
        return notes

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
        box.setTextFormat(Qt.TextFormat.PlainText)     # the text can carry a user-chosen provider name
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
        return self._grab(self, path, dpr)

    def render_add_dialog(self, path, dpr=1.0):
        """The Add provider dialog, filled with an example command provider, to a PNG."""
        dlg = AddProviderDialog(self)
        dlg.kind.setCurrentIndex(1)
        dlg.name.setText("Acme")
        dlg.program.setText("python")
        dlg.args.setText(r'C:\Tools\acme-balance.py --json "two words"')
        dlg.needs_key.setChecked(True)
        dlg.resize(dlg.sizeHint().expandedTo(dlg.minimumSize()))
        return self._grab(dlg, path, dpr)

    @staticmethod
    def _grab(widget, path, dpr):
        from PyQt6.QtGui import QImage, QPainter
        widget.ensurePolished()
        img = QImage(round(widget.width() * dpr), round(widget.height() * dpr), QImage.Format.Format_ARGB32)
        img.setDevicePixelRatio(dpr)
        img.fill(0)
        p = QPainter(img)
        widget.render(p)
        p.end()
        return img.save(str(path))
