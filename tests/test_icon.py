"""The app icon: both SVGs render, the tray icon carries every size, and the .ico matches the SVGs' sizes."""

from PyQt6.QtCore import QSize

from fumes import ui

SIZES = (16, 20, 24, 32, 48, 64, 128, 256)


def test_tray_icon_has_every_size_and_is_not_blank(qapp):
    icon = ui.tray_icon()
    assert sorted(s.width() for s in icon.availableSizes()) == list(SIZES)
    for size in (16, 256):
        img = icon.pixmap(QSize(size, size)).toImage()
        centre = img.pixelColor(size // 2, size // 2)
        assert centre.alpha() == 255                                # the chamber, not an empty pixmap


def test_small_sizes_use_the_cut_without_the_tile(qapp):
    icon = ui.tray_icon()
    assert icon.pixmap(QSize(16, 16)).toImage().pixelColor(0, 0).alpha() == 0      # no tile behind the diamond
    assert icon.pixmap(QSize(256, 256)).toImage().pixelColor(128, 4).alpha() == 255  # the tile


def test_the_ico_is_built_and_bundled():
    data = (ui.ICON_DIR / "fumes.ico").read_bytes()
    assert data[:4] == b"\x00\x00\x01\x00" and int.from_bytes(data[4:6], "little") == len(SIZES)
    spec = (ui.ICON_DIR.parent.parent / "packaging" / "fumes.spec").read_text("utf-8")
    assert '"icon")' in spec and spec.count("icon=ICON") == 2
