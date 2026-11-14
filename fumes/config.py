"""Config (config.toml) and state (state.json). No Qt."""

import json
import os
import sys
import tempfile
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

# A remote feed is optional and off by default: empty means "not set".
DEFAULT_URL = ""
DEFAULT_DETAILS = ""
DEFAULT_LIMITS_URL = ""
CLAUDE_USAGE_URL = "https://claude.ai/settings/usage"
DEFAULT_LINKS = {
    "deepseek": "https://platform.deepseek.com/usage",
    "openai": "https://platform.openai.com/settings/organization/billing/overview",
    "fal": "https://fal.ai/dashboard/billing",
    "anthropic": "https://platform.claude.com/settings/billing",
}
BROWSERS = ("chrome", "default")
ANCHORS = ("tray", "left", "right")
THEMES = ("auto", "dark", "light")


@dataclass
class Config:
    url: str = DEFAULT_URL
    details_url: str = DEFAULT_DETAILS
    refresh_minutes: float = 10
    stale_after_minutes: float = 150
    anchor: str = "tray"
    margin: int = 8
    theme: str = "auto"
    providers: list = field(default_factory=list)  # empty = all
    font_size: float | None = None   # points; None = size the figures to match the logos
    limits_url: str = DEFAULT_LIMITS_URL
    limits_refresh_minutes: float = 2
    show_limits: bool = True
    limits_link: str = CLAUDE_USAGE_URL   # [links] claude: kept apart from `links` (provider ids)
    browser: str = "chrome"   # "chrome" tries Chrome first; "default" uses the system browser
    links: dict = field(default_factory=lambda: dict(DEFAULT_LINKS))  # provider id -> console URL


def config_dir():
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or str(Path.home())
        return Path(base) / "fumes"
    return Path.home() / ".config" / "fumes"


def _is_num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _pos_num(v):
    return _is_num(v) and v > 0


def _nonneg_int(v):
    return isinstance(v, int) and not isinstance(v, bool) and v >= 0


def _nonempty_str(v):
    return isinstance(v, str) and v.strip() != ""


def _str_list(v):
    return isinstance(v, list) and all(isinstance(x, str) for x in v)


_RULES = {
    "url": _nonempty_str,
    "details_url": _nonempty_str,
    "refresh_minutes": _pos_num,
    "stale_after_minutes": _pos_num,
    "anchor": lambda v: v in ANCHORS,
    "margin": _nonneg_int,
    "theme": lambda v: v in THEMES,
    "providers": _str_list,
    "font_size": _pos_num,
    "browser": lambda v: v in BROWSERS,
    "limits_url": _nonempty_str,
    "limits_refresh_minutes": _pos_num,
    "show_limits": lambda v: isinstance(v, bool),
}


def is_web_url(v):
    return isinstance(v, str) and v.startswith(("https://", "http://")) and len(v) > 8 and v.isprintable()


def parse_config(data):
    """dict -> (Config, warnings). Bad values fall back to defaults; unknown keys ignored."""
    cfg = Config()
    warnings = []
    for key, ok in _RULES.items():
        if key not in data:
            continue
        v = data[key]
        if ok(v):
            setattr(cfg, key, v)
        else:
            warnings.append(f"config: bad value for {key!r} ({v!r}), using default")
    if "links" in data:
        links = data["links"]
        if not isinstance(links, dict):
            warnings.append(f"config: [links] must be a table, ignoring it ({links!r})")
        else:
            for pid, url in links.items():
                if is_web_url(url):
                    if pid == "claude":   # the limits cell's page, never a provider's console
                        cfg.limits_link = url
                    else:
                        cfg.links[pid] = url
                else:
                    warnings.append(f"config: links.{pid} must be an http:// or https:// URL, ignoring it")
    return cfg, warnings


def load_config(path=None, warn=None):
    """Read config.toml. Missing file: defaults. Warnings go to stderr (or warn())."""
    path = Path(path) if path else config_dir() / "config.toml"
    warn = warn or (lambda m: print(m, file=sys.stderr))
    data = {}
    if path.is_file():
        try:
            with open(path, "rb") as f:
                data = tomllib.load(f)
        except (OSError, tomllib.TOMLDecodeError) as e:
            warn(f"config: cannot read {path}: {e}, using defaults")
    cfg, warnings = parse_config(data)
    for w in warnings:
        warn(w)
    return cfg


class State:
    """state.json: drag offset and the cached last good payload."""

    def __init__(self, path):
        self.path = Path(path)
        self.offset = 0
        self.cache = None  # raw payload dict
        self.limits_cache = None  # raw limits document
        self.collapsed = False

    @classmethod
    def load(cls, path):
        st = cls(path)
        try:
            d = json.loads(Path(path).read_text("utf-8"))
        except (OSError, ValueError, RecursionError):
            return st
        if not isinstance(d, dict):
            return st
        off = d.get("offset")
        if isinstance(off, int) and not isinstance(off, bool):
            st.offset = off
        if isinstance(d.get("cache"), dict):
            st.cache = d["cache"]
        st.collapsed = d.get("collapsed") is True
        if isinstance(d.get("limits_cache"), dict):
            st.limits_cache = d["limits_cache"]
        return st

    def save(self):
        """Atomic: write a temp file beside the target, then replace."""
        # The caches are raw documents from the network: one that cannot be written (nested too deeply,
        # odd values) is dropped rather than raised, which on the UI thread would abort the app.
        try:
            text = json.dumps({"offset": self.offset, "cache": self.cache,
                               "limits_cache": self.limits_cache, "collapsed": self.collapsed})
        except (ValueError, TypeError, RecursionError):
            self.cache = self.limits_cache = None
            text = json.dumps({"offset": self.offset, "cache": None, "limits_cache": None,
                               "collapsed": self.collapsed})
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=".state-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(text)
            os.replace(tmp, self.path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
