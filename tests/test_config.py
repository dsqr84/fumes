import json

from fumes import config as c


def test_defaults(tmp_path):
    cfg = c.load_config(tmp_path / "none.toml")
    assert cfg == c.Config()
    assert cfg.refresh_minutes == 10 and cfg.stale_after_minutes == 150 and cfg.margin == 8
    assert cfg.anchor == "tray" and cfg.theme == "auto" and cfg.font_size is None


def test_values_bad_values_unknown_keys(tmp_path):
    f = tmp_path / "config.toml"
    f.write_text('anchor = "left"\nmargin = -3\nrefresh_minutes = "x"\nfont_size = 12\n'
                 'providers = ["acme", "fal"]\ntheme = "pink"\nmystery = 1\nurl = "http://x/y"\n')
    warns = []
    cfg = c.load_config(f, warn=warns.append)
    assert cfg.anchor == "left" and cfg.font_size == 12 and cfg.providers == ["acme", "fal"]
    assert cfg.url == "http://x/y"
    assert cfg.margin == 8 and cfg.refresh_minutes == 10 and cfg.theme == "auto"
    assert len(warns) == 3 and not any("mystery" in w for w in warns)


def test_broken_toml(tmp_path):
    f = tmp_path / "config.toml"
    f.write_text("this = = bad")
    warns = []
    assert c.load_config(f, warn=warns.append) == c.Config()
    assert len(warns) == 1


def test_bool_not_a_number():
    cfg, w = c.parse_config({"margin": True, "refresh_minutes": True})
    assert cfg.margin == 8 and cfg.refresh_minutes == 10 and len(w) == 2


def test_state_round_trip_and_atomic(tmp_path):
    p = tmp_path / "sub" / "state.json"
    st = c.State(p)
    st.offset = -42
    st.cache = {"generated": "x", "providers": {}}
    st.save()
    back = c.State.load(p)
    assert back.offset == -42 and back.cache == st.cache
    assert [f.name for f in p.parent.iterdir()] == ["state.json"]  # no temp left behind
    st.offset = 5
    st.save()
    assert json.loads(p.read_text())["offset"] == 5


def test_state_corrupt_or_missing(tmp_path):
    assert c.State.load(tmp_path / "nope.json").offset == 0
    p = tmp_path / "s.json"
    p.write_text("[1")
    assert c.State.load(p).cache is None
    p.write_text('{"offset": "a", "cache": 3}')
    s = c.State.load(p)
    assert s.offset == 0 and s.cache is None


def test_failed_save_leaves_old_file(tmp_path, monkeypatch):
    p = tmp_path / "state.json"
    st = c.State(p)
    st.offset = 1
    st.save()
    st.offset = 2
    monkeypatch.setattr(c.os, "replace", lambda *a: (_ for _ in ()).throw(OSError("x")))
    try:
        st.save()
    except OSError:
        pass
    assert json.loads(p.read_text())["offset"] == 1
    assert [f.name for f in tmp_path.iterdir()] == ["state.json"]


def test_links_defaults_override_and_bad_scheme(tmp_path):
    cfg = c.load_config(tmp_path / "none.toml")
    assert cfg.links["openai"].startswith("https://platform.openai.com/") and "acme" not in cfg.links
    assert cfg.browser == "chrome"
    f = tmp_path / "config.toml"
    f.write_text('browser = "default"\n[links]\nopenai = "http://example.com/x"\nacme = "https://j.example/b"\n'
                 'fal = "javascript:alert(1)"\nanthropic = "file:///c:/x"\ndeepseek = 5\n')
    warns = []
    cfg = c.load_config(f, warn=warns.append)
    assert cfg.browser == "default"
    assert cfg.links["openai"] == "http://example.com/x" and cfg.links["acme"] == "https://j.example/b"
    assert cfg.links["fal"] == c.DEFAULT_LINKS["fal"] and cfg.links["anthropic"] == c.DEFAULT_LINKS["anthropic"]
    assert cfg.links["deepseek"] == c.DEFAULT_LINKS["deepseek"]
    assert len(warns) == 3
    assert c.parse_config({"browser": "ie"})[0].browser == "chrome"
    assert len(c.parse_config({"links": "x"})[1]) == 1
    c.parse_config({"links": {"openai": "https://x/y"}})   # does not mutate the shared defaults
    assert c.DEFAULT_LINKS["openai"].startswith("https://platform")


def test_limits_keys_default_and_set():
    cfg = c.Config()
    assert cfg.url == cfg.details_url == cfg.limits_url == ""          # the remote feed is optional: off by default
    assert cfg.limits_refresh_minutes == 2 and cfg.show_limits is True
    assert cfg.limits_link == "https://claude.ai/settings/usage"
    cfg, w = c.parse_config({"limits_url": "http://x/l", "limits_refresh_minutes": 5, "show_limits": False,
                             "links": {"claude": "https://example.com/u", "openai": "https://o.example"}})
    assert (cfg.limits_url, cfg.limits_refresh_minutes, cfg.show_limits) == ("http://x/l", 5, False)
    assert cfg.limits_link == "https://example.com/u" and w == []
    assert "claude" not in cfg.links and cfg.links["openai"] == "https://o.example"


def test_limits_keys_bad_values_fall_back():
    cfg, w = c.parse_config({"limits_url": "", "limits_refresh_minutes": 0, "show_limits": "yes",
                             "links": {"claude": "ftp://x"}})
    assert cfg.limits_url == c.DEFAULT_LIMITS_URL and cfg.limits_refresh_minutes == 2
    assert cfg.show_limits is True and cfg.limits_link == c.CLAUDE_USAGE_URL and len(w) == 4


def test_font_size_absent_means_automatic_and_set_is_an_override():
    assert c.parse_config({})[0].font_size is None
    assert c.parse_config({"font_size": 13})[0].font_size == 13
    cfg, w = c.parse_config({"font_size": 0})
    assert cfg.font_size is None and len(w) == 1
