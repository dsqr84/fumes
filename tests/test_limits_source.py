"""SPEND-23: the Claude limits address in Settings: config.toml is edited one key at a time and never
damaged, and the running app switches source at once."""

import json
import tomllib

import pytest

from fumes import collect, keystore, settings, ui
from fumes import config as cfgmod

URL = "http://z800.example:8017/claude-limits.json?raw"
LIMITS = {"five_hour": {"used_percentage": 12.0, "resets_at": 4000000000},
          "seven_day": {"used_percentage": 34.0, "resets_at": 4000000000}, "updated": "2026-11-16T10:00:00Z"}


def test_set_top_level_keeps_everything_else():
    text = '# mine\ntheme = "dark"\n\n[links]\nopenai = "https://example.com/x"\n'
    out = cfgmod.set_top_level(text, "limits_url", URL)
    assert out == f'# mine\ntheme = "dark"\nlimits_url = "{URL}"\n\n[links]\nopenai = "https://example.com/x"\n'
    again = cfgmod.set_top_level(out, "limits_url", "https://b.example/l.json")
    assert again.count("limits_url") == 1 and tomllib.loads(again)["limits_url"] == "https://b.example/l.json"
    assert cfgmod.set_top_level(again, "limits_url", None) == text
    assert cfgmod.set_top_level("", "limits_url", URL) == f'limits_url = "{URL}"\n'
    assert cfgmod.set_top_level("a = 1\r\n", "limits_url", URL) == f'a = 1\r\nlimits_url = "{URL}"\r\n'


def test_a_key_of_the_same_name_inside_a_table_is_not_the_setting():
    text = '[links]\nlimits_url = "https://example.com/inside"\n'
    out = cfgmod.set_top_level(text, "limits_url", URL)
    doc = tomllib.loads(out)
    assert doc["limits_url"] == URL and doc["links"]["limits_url"] == "https://example.com/inside"


def test_save_setting_creates_edits_and_removes(tmp_path):
    f = tmp_path / "sub" / "config.toml"
    cfgmod.save_setting(f, "limits_url", URL)
    assert cfgmod.load_config(f).limits_url == URL
    f.write_text('anchor = "left"\n' + f.read_text("utf-8"), "utf-8")
    cfgmod.save_setting(f, "limits_url", None)
    cfg = cfgmod.load_config(f)
    assert cfg.limits_url == "" and cfg.anchor == "left"
    assert [p.name for p in f.parent.iterdir()] == ["config.toml"]            # no temp file left behind


@pytest.mark.parametrize("broken", ['theme = "dark\n', 'limits_url = """\nlimits_url = "x"\n"""\n'])
def test_a_file_it_cannot_edit_safely_is_left_alone(tmp_path, broken):
    f = tmp_path / "config.toml"
    f.write_text(broken, "utf-8")
    with pytest.raises(cfgmod.ConfigError):
        cfgmod.save_setting(f, "limits_url", URL)
    assert f.read_text("utf-8") == broken


@pytest.mark.parametrize("bad", ["", "ftp://x/y", "file:///etc/passwd", "http://", 'http://a/"b', "http://a b/c",
                                 "http://a/\nb", "javascript:alert(1)", "http://a/" + "x" * 3000])
def test_only_plain_web_addresses_are_accepted(bad):
    assert not cfgmod.valid_remote_url(bad)
    assert cfgmod.valid_remote_url(URL)


def window(tmp_path, cfg=None, called=None):
    return settings.SettingsWindow(keystore.MemoryStore(), collect.Ledger(tmp_path / "l.json"), folder=tmp_path,
                                   cfg=cfg or cfgmod.Config(), config_path=tmp_path / "config.toml",
                                   on_limits_source=(lambda: called.append(1)) if called is not None else None)


def test_settings_saves_and_clears_the_address(qapp, tmp_path):
    called, cfg = [], cfgmod.Config()
    w = window(tmp_path, cfg, called)
    p = w.pages["claude"]
    assert "this computer" in p.source.text() and not p.url_clear.isEnabled()
    p.url.setText("  " + URL + " ")
    p.do_save_url()
    assert cfg.limits_url == URL and called == [1] and cfgmod.load_config(tmp_path / "config.toml").limits_url == URL
    assert URL in p.source.text() and p.url_msg.text().startswith("Saved") and p.url_clear.isEnabled()
    p.do_clear_url()
    assert cfg.limits_url == "" and called == [1, 1] and "this computer" in p.source.text()
    assert cfgmod.load_config(tmp_path / "config.toml").limits_url == ""
    w.close()


def test_settings_refuses_a_bad_address_and_a_broken_file(qapp, tmp_path):
    called, cfg = [], cfgmod.Config()
    w = window(tmp_path, cfg, called)
    p = w.pages["claude"]
    p.url.setText("file:///C:/Users/x/secret.json")
    p.do_save_url()
    assert cfg.limits_url == "" and not called and not (tmp_path / "config.toml").exists()
    assert p.url_msg.property("bad") is True
    (tmp_path / "config.toml").write_text("theme = \n", "utf-8")
    p.url.setText(URL)
    p.do_save_url()
    assert cfg.limits_url == "" and not called and "left alone" in p.url_msg.text()
    assert (tmp_path / "config.toml").read_text("utf-8") == "theme = \n"
    w.close()


def test_without_a_config_path_the_field_is_off(qapp, tmp_path):
    w = settings.SettingsWindow(keystore.MemoryStore(), collect.Ledger(tmp_path / "l.json"), folder=tmp_path,
                                cfg=cfgmod.Config())
    p = w.pages["claude"]
    assert not p.url.isEnabled() and not p.url_save.isEnabled()
    with pytest.raises(cfgmod.ConfigError):
        w.set_limits_url(URL)
    w.close()


def test_the_app_switches_source_at_once(qapp, tmp_path, monkeypatch):
    (tmp_path / "claude-limits.json").write_text(json.dumps(LIMITS), "utf-8")
    fetched = []

    class FakeThread:
        def __init__(self, url):
            self.url = url
            fetched.append(url)
            self.done = type("S", (), {"connect": lambda self, fn: None})()

        def isRunning(self):  # noqa: N802
            return False

        def start(self):
            pass

    monkeypatch.setattr(ui, "LimitsThread", FakeThread)
    app = ui.App(cfgmod.Config(), cfgmod.State(tmp_path / "s.json"), user_logo_dir=tmp_path,
                 store=keystore.MemoryStore())
    assert app.config_path == tmp_path / "config.toml"
    app.refresh_limits()
    assert app.strip.limits.five_hour.used == 12.0 and not fetched             # local file
    app.open_settings("claude")
    app.settings_window.set_limits_url(URL)
    assert fetched == [URL] and app.strip.limits is None                         # the local reading is dropped
    app.settings_window.set_limits_url("")
    assert app.strip.limits.five_hour.used == 12.0 and fetched == [URL]         # back to the local file
    app.settings_window.close()
