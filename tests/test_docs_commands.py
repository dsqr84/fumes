"""Every `fumes <word>` command a doc or guide tells people to run must be a real subcommand."""

import re
from pathlib import Path

import pytest

from fumes import __main__ as m

ROOT = Path(__file__).parent.parent
FILES = sorted([*ROOT.glob("fumes/guides/*.md"), ROOT / "fumes/guides/setup-prompt.txt", *ROOT.glob("docs/*.md"),
                ROOT / "README.md", *ROOT.glob(".claude/skills/*/SKILL.md")])
CMD = re.compile(r"(?<![\w./-])(?:python(?:\.exe)? -m fumes|fumes(?:\.exe)?)\s+(\w[\w-]*)(?:\s+([\w<>-]+))?")
KEY_SUB = {"list", "set", "delete"}


def scan(text):
    """[(word, next word or None)] for each command written as `fumes <word> ...` or `python -m fumes <word>`:
    anywhere inside a fenced block, and at the start of a `code span` (or after python -m) in prose."""
    out, fence = [], False
    for line in text.splitlines():
        if line.lstrip().startswith("```"):
            fence = not fence
            continue
        spans = [line] if fence else re.findall(r"`([^`]*)`", line)
        for s in spans:
            for mt in CMD.finditer(s):
                out.append((mt.group(1), mt.group(2)))
        if not fence:
            for mt in re.finditer(r"python(?:\.exe)? -m fumes\s+(\w[\w-]*)(?:\s+([\w<>-]+))?", line):
                if (mt.group(1), mt.group(2)) not in out:
                    out.append((mt.group(1), mt.group(2)))
    return out


def problems(text):
    bad = []
    for word, nxt in scan(text):
        if word not in m.COMMANDS:
            bad.append(f"fumes {word}")
        elif word == "key" and nxt and not nxt.startswith("<") and nxt not in KEY_SUB:
            bad.append(f"fumes key {nxt}")
    return bad


def test_commands_list_is_the_documented_set():
    assert set(m.COMMANDS) == {"key", "anchor", "collect", "status", "doctor", "setup-prompt"}


def test_files_are_found():
    names = {f.name for f in FILES}
    assert {"README.md", "setup-prompt.txt", "SETUP-WITH-AI.md", "SKILL.md"} <= names
    assert all(f.is_file() for f in FILES)


@pytest.mark.parametrize("path", FILES, ids=lambda p: str(p.relative_to(ROOT)))
def test_docs_mention_only_real_commands(path):
    assert problems(path.read_text("utf-8")) == []


def test_the_docs_do_mention_commands():
    assert len(scan("\n".join(f.read_text("utf-8") for f in FILES))) > 15


@pytest.mark.parametrize("text,expected", [
    ("Run `fumes keys` now", ["fumes keys"]),
    ("Run `fumes key add openai`", ["fumes key add"]),
    ("```\npython -m fumes bogus\n```", ["fumes bogus"]),
    ("```\n.venv\\Scripts\\python -m fumes key set openai\n```", []),
    ("use `fumes.exe doctor` or `python -m fumes anchor <provider> 3`", []),
    ("`python -m fumes.statusline` and `fumes key <list>` and the fumes folder", []),
    ("run python -m fumes keys here", ["fumes keys"]),
])
def test_scanner_catches_bad_commands(text, expected):
    assert problems(text) == expected
