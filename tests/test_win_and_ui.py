import json
from datetime import datetime, timezone

import pytest

from fumes import config as cfgmod
from fumes import feed as feedmod
from fumes import win

NOW = datetime(2026, 11, 12, 12, 0, 0, tzinfo=timezone.utc)


@pytest.mark.skipif(win.ON_WINDOWS, reason="the no-op fallbacks are for other platforms")
def test_win_noop_on_linux():
    assert win.ON_WINDOWS is False
    assert win.taskbar_rects() is None
    assert win.uses_light_theme() is None
    assert win.foreground_fullscreen(0) is False
    assert win.set_autostart(True) is False
    assert win.autostart_enabled() is False
    win.set_topmost(123)


def make_strip(qapp, **kw):
    from fumes import ui
    kw.setdefault("show_limits", False)    # the Claude cell has its own tests (test_limits_ui.py)
    return ui.Strip(cfgmod.Config(**kw), theme="dark")


def test_placeholder_before_first_fetch(qapp):
    s = make_strip(qapp)
    assert s.cell_texts() == ["…"] * len(feedmod.KNOWN_ORDER)          # one placeholder per built-in provider
    assert s.age_label() == ""


def test_smoke_cells_from_old_fixture(qapp, raw_feed):
    s = make_strip(qapp)
    s.set_feed(feedmod.parse_feed(raw_feed), now=NOW)
    assert [c.id for c in s.cells()] == ["deepseek", "openai", "fal", "acme"]       # anthropic: hidden
    assert s.cell_texts() == ["$4.15", "$5.44", "$2.86", "$7.25"]
    assert [c.fig for c in s.cells()] == ["balance", "mtd", "balance", "balance"]
    assert [c.label for c in s.cells()] == ["", "mtd", "", ""]
    assert [c.dim for c in s.cells()] == [False] * 4
    assert not s.is_stale(NOW) and s.age_label() == ""
    xs = [c.x for c in s.cells()]
    assert xs == sorted(xs) and s.width() > xs[-1]


def test_live_fixture_hides_the_unconnected_and_dims_the_not_live(qapp, raw_feed_live):
    s = make_strip(qapp)
    s.set_feed(feedmod.parse_feed(raw_feed_live), now=NOW)
    assert [c.id for c in s.cells()] == ["deepseek", "openai", "fal", "anthropic", "acme"]
    assert s.cell_texts() == ["$4.15", "$14.37", "$2.86", "$85.20", "$7.25"]
    assert [c.dim for c in s.cells()] == [False, False, False, True, False]      # anthropic: balance kept, dimmed


def test_no_figure_is_a_dash_and_a_live_one_is_not_dimmed(qapp, raw_feed):
    raw_feed["providers"]["anthropic"]["status"] = "error"        # shown (not "not_connected"), no figure
    s = make_strip(qapp)
    s.set_feed(feedmod.parse_feed(raw_feed), now=NOW)
    c = next(c for c in s.cells() if c.id == "anthropic")
    assert (c.text, c.fig, c.dim) == ("\u2013", "na", True)
    raw_feed["providers"]["anthropic"]["status"] = "ok"
    s.set_feed(feedmod.parse_feed(raw_feed), now=NOW)
    c = next(c for c in s.cells() if c.id == "anthropic")
    assert (c.text, c.fig, c.dim) == ("\u2013", "na", False)


def test_failed_fetch_keeps_figures_and_marks_stale(qapp, raw_feed):
    s = make_strip(qapp)
    s.set_feed(feedmod.parse_feed(raw_feed), now=NOW)
    before = s.cell_texts()
    s.set_failed(True, now=NOW)
    assert s.cell_texts() == before
    assert s.is_stale(NOW) and s.age_label() == "29m old"


def test_old_data_is_stale_with_age_label(qapp, raw_feed):
    s = make_strip(qapp)
    later = datetime(2026, 11, 12, 15, 0, 0, tzinfo=timezone.utc)
    s.set_feed(feedmod.parse_feed(raw_feed), now=later)
    assert s.is_stale(later) and s.age_label() == "3h old"


def test_provider_selection_and_unknown(qapp, raw_feed):
    raw_feed["providers"]["newco"] = {"name": "NewCo", "status": "ok", "balance": 1.5}
    s = make_strip(qapp, providers=["acme", "newco"])
    s.set_feed(feedmod.parse_feed(raw_feed), now=NOW)
    assert s.cell_texts() == ["$7.25", "$1.50"]


def test_logo_override_and_fallback_badge(qapp, tmp_path, raw_feed):
    from fumes import ui
    (tmp_path / "logos").mkdir()
    (tmp_path / "logos" / "acme.svg").write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1 1"><rect width="1" height="1" fill="#00ff00"/></svg>')
    s = ui.Strip(cfgmod.Config(), user_logo_dir=tmp_path, theme="dark")
    assert s._find_logo("acme").parent == tmp_path / "logos"
    assert s._find_logo("deepseek").parent == ui.LOGO_DIR
    assert s._find_logo("acme2") is None
    img = s._logo_pixmap("unknown", "Qux", 1.0).toImage()
    assert img.pixelColor(2, 2).alpha() > 0          # badge painted
    assert ui.badge_color("acme") == "#d55181"


def test_render_png(qapp, raw_feed, tmp_path):
    s = make_strip(qapp)
    s.set_feed(feedmod.parse_feed(raw_feed), now=NOW)
    out = tmp_path / "x.png"
    assert s.render_png(out, 2.0) and out.stat().st_size > 500


def test_cli_once_png(tmp_path, monkeypatch, raw_feed):
    from fumes import __main__ as m
    f = tmp_path / "f.json"
    f.write_text(json.dumps(raw_feed))
    out = tmp_path / "o.png"
    assert m.main(["--config", str(tmp_path / "c.toml"), "--once-png", str(out),
                   "--feed-file", str(f), "--theme", "light"]) == 0
    assert out.exists()


def test_run_command_starts_the_package_from_its_folder_without_a_console():
    cmd = win.run_command(root=r"C:\Users\Alex Kim\dev\fumes", exe=r"C:\py\python.exe")
    assert cmd.startswith('"C:\\py\\pythonw.exe" -c "')      # pythonw, quoted, no cmd /c
    assert "cmd" not in cmd.split('"')[1].lower() and cmd.count('"') == 4
    assert "sys.path.insert(0,'C:/Users/Alex Kim/dev/fumes')" in cmd
    assert "runpy.run_module('fumes',run_name='__main__')" in cmd


def test_run_command_refuses_a_folder_it_cannot_quote():
    import pytest
    with pytest.raises(ValueError):
        win.run_command(root="C:/it's/here", exe="pythonw.exe")


def test_the_run_command_really_starts_the_package(tmp_path):
    # Same command shape, run here with this interpreter from an unrelated folder: `--help` must work.
    import shlex, subprocess, sys, pathlib
    root = str(pathlib.Path(win.__file__).resolve().parent.parent)
    cmd = win.run_command(root=root, exe=sys.executable)
    out = subprocess.run(shlex.split(cmd) + ["--help"], cwd=tmp_path, capture_output=True, text=True, timeout=60)
    assert out.returncode == 0 and "--once-png" in out.stdout


def test_open_url_refuses_non_http_and_never_looks_for_chrome():
    seen = []
    for bad in ("file:///c:/x", "javascript:1", "ftp://x", "", None, "chrome.exe"):
        assert win.open_url(bad, "chrome", chrome_lookup=lambda: seen.append(1) or "C:/c.exe",
                            popen=lambda a: seen.append(a)) is False
    assert seen == []


def test_open_url_launches_chrome_with_an_argument_list():
    calls = []
    assert win.open_url("https://x.example/a b", "chrome", chrome_lookup=lambda: r"C:\c\chrome.exe",
                        popen=calls.append) is True
    assert calls == [[r"C:\c\chrome.exe", "https://x.example/a b"]]


def test_open_url_false_when_chrome_missing_or_launch_fails():
    assert win.open_url("https://x", "chrome", chrome_lookup=lambda: None, popen=lambda a: 1 / 0) is False

    def boom(a):
        raise OSError("no")
    assert win.open_url("https://x", "chrome", chrome_lookup=lambda: "c.exe", popen=boom) is False
    if not win.ON_WINDOWS:     # on Windows the real lookup may find Chrome, and would open it
        assert win.open_url("https://x") is False      # real lookup off Windows: not found


def test_browser_default_skips_the_chrome_lookup():
    looked = []
    assert win.open_url("https://x", "default", chrome_lookup=lambda: looked.append(1) or "c.exe",
                        popen=lambda a: looked.append(a)) is False
    assert looked == []


def test_open_link_falls_back_to_the_system_opener(qapp):
    from fumes import links
    got = []
    cfg = cfgmod.Config()
    assert links.open_link(cfg, "https://x", opener=lambda u, b: True, fallback=got.append) and got == []
    assert links.open_link(cfg, "https://x", opener=lambda u, b: False, fallback=lambda u: got.append(u) or True)
    assert got == ["https://x"]
    assert links.open_link(cfg, "file:///x", opener=lambda u, b: True, fallback=got.append) is False
    seen = []
    links.open_link(cfgmod.Config(browser="default"), "https://x", opener=lambda u, b: seen.append(b) or False,
                    fallback=lambda u: True)
    assert seen == ["default"]


def test_restart_argv_keeps_interpreter_folder_and_options():
    argv = win.restart_argv(root=r"C:\Users\Alex\dev\fumes", exe=r"C:\py\python.exe", args=["--config", "x.toml"])
    assert argv[0] == r"C:\py\python.exe"                 # same interpreter as this run
    assert argv[1] == "-c" and "sys.path.insert(0,'C:/Users/Alex/dev/fumes')" in argv[2]
    assert argv[3:] == ["--config", "x.toml"]


def test_relaunch_hands_the_argv_to_popen_and_reports_failure():
    seen = []
    assert win.relaunch(["a", "b"], popen=seen.append) is True and seen == [["a", "b"]]

    def boom(a):
        raise OSError("nope")
    assert win.relaunch(["a"], popen=boom) is False


def test_the_restart_argv_really_starts_the_package(tmp_path):
    import subprocess, sys
    argv = win.restart_argv(exe=sys.executable, args=["--help"])
    out = subprocess.run(argv, cwd=tmp_path, capture_output=True, text=True, timeout=60)
    assert out.returncode == 0 and "--once-png" in out.stdout


def test_restart_quits_only_when_the_new_copy_started(monkeypatch):
    from fumes import ui
    quits = []
    monkeypatch.setattr(ui.QApplication, "quit", staticmethod(lambda: quits.append(1)))
    monkeypatch.setattr(win, "relaunch", lambda: False)
    ui.App.restart(None)
    assert quits == []
    monkeypatch.setattr(win, "relaunch", lambda: True)
    ui.App.restart(None)
    assert quits == [1]


def test_release_build_commands_point_at_the_two_programs(monkeypatch, tmp_path):
    """Frozen (PyInstaller): autostart and Restart start Fumes.exe; the status line is fumes-cli.exe statusline."""
    exe = tmp_path / "Fumes app" / "Fumes.exe"
    monkeypatch.setattr(win.sys, "frozen", True, raising=False)
    monkeypatch.setattr(win.sys, "executable", str(exe))
    folder = str(exe.parent).replace("\\", "/")
    assert win.run_command() == f'"{folder}/Fumes.exe"'
    assert win.statusline_command() == f'"{folder}/fumes-cli.exe" statusline'
    assert win.restart_argv(args=["--config", "c.toml"]) == [f"{folder}/Fumes.exe", "--config", "c.toml"]
    from fumes import diagnose
    assert diagnose.install_root() == str(exe.parent)


def test_statusline_subcommand_runs_the_status_line(monkeypatch, capsys, tmp_path):
    import io
    import json
    from fumes import __main__ as m
    monkeypatch.setenv("FUMES_LIMITS_OUT", str(tmp_path / "cl.json"))
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"context_window": {"used_percentage": 12}})))
    assert m.main(["statusline"]) == 0
    assert capsys.readouterr().out.strip() == "ctx 12%"
