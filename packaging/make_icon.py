"""Rebuild fumes/icon/fumes.ico from the two SVGs. Run from the repo root after changing either:
    QT_QPA_PLATFORM=offscreen python packaging/make_icon.py
Needs PyQt6 and Pillow. The .ico is committed, so a release build does not run this."""

import sys
import tempfile
from pathlib import Path

from PIL import Image
from PyQt6.QtWidgets import QApplication

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from fumes import ui  # noqa: E402

app = QApplication.instance() or QApplication(sys.argv[:1])
icon = ui.tray_icon()
images = []
with tempfile.TemporaryDirectory() as tmp:
    for size in (256, 128, 64, 48, 32, 24, 20, 16):
        f = str(Path(tmp) / f"{size}.png")
        if not icon.pixmap(size, size).save(f, "PNG"):
            sys.exit(f"could not render {size} px")
        images.append(Image.open(f).convert("RGBA"))
out = ui.ICON_DIR / "fumes.ico"
images[0].save(out, format="ICO", sizes=[(i.width, i.height) for i in images], append_images=images[1:])
print(f"wrote {out}: {[i.width for i in images]}")
