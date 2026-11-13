"""Every built-in provider ships everything a provider needs, so adding one cannot leave a hole."""
import re
from pathlib import Path

import pytest

from fumes import collect, config, feed
from fumes.collect import custom

ROOT = Path(__file__).parent.parent
GUIDES = ROOT / "fumes" / "guides"
LOGOS = ROOT / "fumes" / "logos"
MODS = collect.providers()
IDS = [m.ID for m in MODS]


@pytest.mark.parametrize("mod", MODS, ids=IDS)
def test_module_shape(mod):
    for attr in ("ID", "NAME", "KEY_LABEL", "ANCHORED", "run"):
        assert hasattr(mod, attr), f"{mod.__name__} lacks {attr}"
    assert isinstance(mod.ID, str) and feed.valid_id(mod.ID)
    assert isinstance(mod.NAME, str) and mod.NAME
    assert isinstance(mod.KEY_LABEL, str) and mod.KEY_LABEL     # built-ins always need a key
    assert callable(mod.run)
    if mod.ANCHORED:
        assert callable(getattr(mod, "spend_today", None))


@pytest.mark.parametrize("pid", IDS)
def test_guide_logo_link_order(pid):
    guide = GUIDES / f"{pid}.md"
    assert guide.is_file() and guide.read_text("utf-8").lstrip().startswith("# ")
    assert (LOGOS / f"{pid}.svg").is_file()
    assert config.DEFAULT_LINKS.get(pid, "").startswith("https://")
    assert pid in feed.KNOWN_ORDER


@pytest.mark.parametrize("pid", IDS)
def test_has_a_provider_test(pid):
    text = (ROOT / "tests" / "test_providers.py").read_text("utf-8")
    assert re.search(rf"def test_{re.escape(pid)}_|\b{re.escape(pid)}\.run\(", text), f"no test mentions {pid}"


def test_ids_are_unique_and_not_reserved_words():
    assert len(set(IDS)) == len(IDS) and "claude" not in IDS


def test_every_guide_is_reachable():
    readme = (ROOT / "README.md").read_text("utf-8")
    reachable = set(IDS) | {"claude", "key-safety"} | set(custom.KINDS)   # the pages Settings builds
    for g in GUIDES.glob("*.md"):
        assert g.stem in reachable or f"guides/{g.name}" in readme, f"{g.name} is not linked from anywhere"
        assert g.read_text("utf-8").lstrip().startswith("# ")
