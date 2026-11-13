from pathlib import Path

from fumes import fmt

LOGOS = Path(__file__).parent.parent / "fumes" / "logos"


def test_bundled_current_color_logos_get_substituted():
    for name in ("openai", "anthropic"):
        raw = (LOGOS / f"{name}.svg").read_text()
        assert "currentColor" in raw
        out = fmt.recolor_svg(raw, "#f2f2f2")
        assert "currentColor" not in out and "#f2f2f2" in out


def test_rendered_mono_logo_is_ink_not_black(qapp):
    from fumes import config, ui
    s = ui.Strip(config.Config(), theme="dark")
    img = s._logo_pixmap("openai", "OpenAI", 1.0).toImage()
    seen = {(c.red() > 200) for x in range(ui.LOGO) for y in range(ui.LOGO)
            if (c := img.pixelColor(x, y)).alpha() > 200}
    assert seen == {True}
