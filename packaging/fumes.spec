# PyInstaller spec: one folder, two programs sharing it.
#   Fumes.exe      windowed: the strip, Settings, Details
#   fumes-cli.exe  console: `fumes-cli key set ...`, `doctor`, and Claude Code's status line (needs stdout)
# One folder, not one file: a one-file build unpacks itself on every start, too slow for a status line
# that runs after every Claude answer. Build from the repo root: pyinstaller packaging/fumes.spec
import os

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))
DATAS = [(os.path.join(ROOT, "fumes", d), os.path.join("fumes", d)) for d in ("fonts", "logos", "guides", "icon")]
ICON = os.path.join(ROOT, "fumes", "icon", "fumes.ico")
EXCLUDES = ["tkinter", "unittest", "pydoc", "PyQt6.QtWebEngineCore", "PyQt6.QtQml", "PyQt6.QtQuick",
            "PyQt6.QtMultimedia", "PyQt6.QtBluetooth", "PyQt6.QtPdf", "PyQt6.Qt3DCore"]

app = Analysis([os.path.join(SPECPATH, "fumes_app.py")], pathex=[ROOT], datas=DATAS,
               hiddenimports=["fumes.collect.deepseek", "fumes.collect.openai", "fumes.collect.fal",
                              "fumes.collect.anthropic", "fumes.settings", "fumes.details", "fumes.peek"],
               excludes=EXCLUDES)
cli = Analysis([os.path.join(SPECPATH, "fumes_cli.py")], pathex=[ROOT], datas=DATAS,
               hiddenimports=["fumes.collect.deepseek", "fumes.collect.openai", "fumes.collect.fal",
                              "fumes.collect.anthropic", "fumes.statusline", "fumes.diagnose"],
               excludes=EXCLUDES + ["PyQt6"])

app_exe = EXE(PYZ(app.pure), app.scripts, [], exclude_binaries=True, name="Fumes", console=False,
              upx=False, icon=ICON)
cli_exe = EXE(PYZ(cli.pure), cli.scripts, [], exclude_binaries=True, name="fumes-cli", console=True,
              upx=False, icon=ICON)
COLLECT(app_exe, app.binaries, app.datas, cli_exe, cli.binaries, cli.datas, name="Fumes", upx=False)
