"""Entry point of fumes-cli.exe (console): the commands and Claude Code's status line. Never starts the strip."""
import sys

from fumes.__main__ import COMMANDS, cli

code = cli(sys.argv[1:])
if code is None:
    print("usage: fumes-cli <" + "|".join(COMMANDS) + "> ...   (run Fumes.exe for the strip)", file=sys.stderr)
    code = 2
sys.exit(code)
