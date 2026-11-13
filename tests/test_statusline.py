"""Tests for fumes.statusline (the Claude Code status line): offline, temp files only."""

import io
import json
import os
import subprocess
import sys
import tempfile
import unittest

from fumes import statusline as limits

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

NOW = 1_800_000_000
FIVE = {"used_percentage": 23.5, "resets_at": NOW + 3600}
WEEK = {"used_percentage": 41.2, "resets_at": NOW + 400_000}


def session(five=FIVE, week=WEEK, ctx=12.4):
    data = {"context_window": {"used_percentage": ctx}, "rate_limits": {}}
    if five is not None:
        data["rate_limits"]["five_hour"] = five
    if week is not None:
        data["rate_limits"]["seven_day"] = week
    return data


class Base(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.out = os.path.join(tmp.name, "sub", "claude-limits.json")

    def run_main(self, data, now=NOW):
        text = data if isinstance(data, str) else json.dumps(data)
        return limits.main(stdin=io.StringIO(text), out=self.out, now=now)

    def stored(self):
        with open(self.out, encoding="utf-8") as f:
            return json.load(f)


class WriteTest(Base):
    def test_first_reading_is_written_and_shown(self):
        shown = self.run_main(session())
        doc = self.stored()
        self.assertEqual(doc["five_hour"], {"used_percentage": 23.5, "resets_at": NOW + 3600})
        self.assertEqual(doc["seven_day"], {"used_percentage": 41.2, "resets_at": NOW + 400_000})
        self.assertEqual(doc["updated"], "2027-01-15T08:00:00Z")
        self.assertTrue(shown.startswith("ctx 12% · 5h 24% → "))
        self.assertTrue(shown.endswith(" · week 41%"))

    def test_a_session_without_limits_keeps_what_is_stored_and_still_shows_it(self):
        self.run_main(session())
        before = self.stored()
        shown = self.run_main({"context_window": {"used_percentage": 3}}, now=NOW + 60)
        self.assertEqual(self.stored(), before)
        self.assertIn("5h 24%", shown)
        self.assertIn("ctx 3%", shown)

    def test_an_older_reading_of_the_same_window_never_wins(self):
        self.run_main(session())
        idle = session(five={"used_percentage": 10.0, "resets_at": NOW + 3600 + 30},   # same window, less used
                       week={"used_percentage": 41.2, "resets_at": NOW + 400_000})
        self.run_main(idle, now=NOW + 60)
        self.assertEqual(self.stored()["five_hour"]["used_percentage"], 23.5)
        self.assertEqual(self.stored()["updated"], "2027-01-15T08:00:00Z")           # nothing changed

    def test_more_use_in_the_same_window_wins(self):
        self.run_main(session())
        self.run_main(session(five={"used_percentage": 30.0, "resets_at": NOW + 3600}), now=NOW + 60)
        self.assertEqual(self.stored()["five_hour"]["used_percentage"], 30.0)
        self.assertEqual(self.stored()["updated"], "2027-01-15T08:01:00Z")

    def test_a_later_window_replaces_a_fuller_earlier_one(self):
        self.run_main(session(five={"used_percentage": 95.0, "resets_at": NOW + 3600}))
        self.run_main(session(five={"used_percentage": 2.0, "resets_at": NOW + 3600 + 18_000}), now=NOW + 4000)
        self.assertEqual(self.stored()["five_hour"], {"used_percentage": 2.0, "resets_at": NOW + 21_600})

    def test_an_earlier_window_never_replaces_a_later_one(self):
        self.run_main(session(five={"used_percentage": 2.0, "resets_at": NOW + 21_600}))
        self.run_main(session(five={"used_percentage": 95.0, "resets_at": NOW + 3600}), now=NOW + 10)
        self.assertEqual(self.stored()["five_hour"]["used_percentage"], 2.0)

    def test_one_window_can_be_absent(self):
        self.run_main(session(week=None))
        self.assertNotIn("seven_day", self.stored())
        self.run_main(session(five=None, week=WEEK), now=NOW + 5)
        self.assertEqual(sorted(k for k in self.stored() if k != "updated"), ["five_hour", "seven_day"])

    def test_bad_input_writes_nothing_and_prints_nothing(self):
        for bad in ("", "not json", "[]", "null", json.dumps({"rate_limits": "x"}),
                    json.dumps({"rate_limits": {"five_hour": {"used_percentage": "23", "resets_at": NOW + 9}}}),
                    json.dumps({"rate_limits": {"five_hour": {"used_percentage": True, "resets_at": NOW + 9}}}),
                    json.dumps({"rate_limits": {"five_hour": {"used_percentage": 5}}})):
            self.assertEqual(self.run_main(bad), "", bad)
            self.assertFalse(os.path.exists(self.out), bad)

    def test_a_corrupt_stored_file_is_replaced(self):
        os.makedirs(os.path.dirname(self.out))
        with open(self.out, "w") as f:
            f.write("{broken")
        self.run_main(session())
        self.assertEqual(self.stored()["five_hour"]["used_percentage"], 23.5)


class LineTest(Base):
    def test_a_window_past_its_reset_is_not_shown(self):
        self.run_main(session())
        shown = self.run_main({"context_window": {"used_percentage": 50}}, now=NOW + 3600)
        self.assertEqual(shown, "ctx 50% · week 41%")

    def test_no_context_figure_yet(self):
        shown = self.run_main(session(ctx=None))
        self.assertTrue(shown.startswith("5h 24% → "))


class ScriptTest(Base):
    def test_run_as_claude_code_runs_it(self):
        env = dict(os.environ, FUMES_LIMITS_OUT=self.out, PYTHONPATH=ROOT)
        future = session(five={"used_percentage": 7, "resets_at": 4_000_000_000},
                         week={"used_percentage": 9, "resets_at": 4_000_000_000})
        got = subprocess.run([sys.executable, "-m", "fumes.statusline"], input=json.dumps(future), env=env,
                             capture_output=True, text=True, timeout=30)
        self.assertEqual((got.returncode, got.stderr), (0, ""))
        self.assertIn("5h 7%", got.stdout)
        self.assertEqual(self.stored()["seven_day"]["used_percentage"], 9.0)
        got = subprocess.run([sys.executable, "-m", "fumes.statusline"], input="garbage", env=env,
                             capture_output=True, text=True, timeout=30)
        self.assertEqual((got.returncode, got.stderr), (0, ""))

    def test_it_never_imports_qt(self):
        """Claude Code runs it after every answer: it must start fast, so no Qt."""
        code = "import sys, fumes.statusline; print(any(m.startswith('PyQt6') for m in sys.modules))"
        got = subprocess.run([sys.executable, "-c", code], env=dict(os.environ, PYTHONPATH=ROOT),
                             capture_output=True, text=True, timeout=30)
        self.assertEqual(got.stdout.strip(), "False")


if __name__ == "__main__":
    unittest.main()
