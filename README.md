# Fumes

**Know before you're running on fumes.** A slim strip over the Windows 11 taskbar that shows what is
left on each of your AI accounts: the prepaid balance on every API provider, and how much of your
Claude subscription's 5-hour and weekly windows you have used.

![Fumes on a dark taskbar](docs/screenshot-dark.png)

> **Status: pre-release.** Fumes reads DeepSeek, OpenAI, fal.ai and the Anthropic API with your own
> keys, kept in Windows Credential Manager. A Settings window, a details window and a Windows
> installer are being built before the first release; until then keys are added from the command line.

## What it shows

- **Two rows, like the taskbar clock.** Providers sit two to a column, each a logo and its balance
  (or `mtd $6.10`, month-to-date spend, for a provider with no balance; an en dash when neither is
  known).
- **Claude limits.** The leftmost cell: `5h` above `wk`, each a small bar that fills from the left.
  The colour is the reading: green while there is room, yellow around half, red near the cap. Hover
  for the percentages and reset times.
- **Peeks.** Click a provider for its peek: credit left, month to date, the last 30 days, a daily
  spend chart, the spend by model, and links to that provider's console. Click another provider to
  switch; anywhere else (or Esc) closes it.

  <img src="docs/screenshot-peek.png" alt="The OpenAI peek" width="368">
- **Trust at a glance.** A cell at full strength is live. A dimmed one is stale or failing, with its
  age shown when the whole strip is out of date.
- **Out of the way.** The chevron folds the strip down to just the Claude cell. Right-click for Move
  (drag it along the taskbar), Collapsed, Refresh now, Start with Windows, Restart and Quit.

## Install and run (Windows 11, Python 3.11+)

```
git clone https://github.com/<owner>/fumes.git
cd fumes
py -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\pythonw -m fumes
```

Use `python -m fumes` instead of `pythonw` to see errors on the console. Starting Fumes again
replaces the copy that is already running.

## Add your keys

Each provider needs its own key; [`fumes/guides/`](fumes/guides/) says which kind and where to
create it. Until the Settings window lands, from the Fumes folder:

```
.venv\Scripts\python -m fumes key set openai        # asks for the key; nothing is shown or saved to a file
.venv\Scripts\python -m fumes anchor openai 25.00   # OpenAI and Anthropic: the credit your billing page shows
.venv\Scripts\python -m fumes key list              # which providers have a key (never the key itself)
.venv\Scripts\python -m fumes collect               # ask every provider once and print the result
```

Keys are generic credentials named `Fumes/<provider>` in Windows Credential Manager, readable only by
your Windows account. Fumes sends each one only to that provider's own API, over HTTPS.

**Claude limits** come from Claude Code: set its status line to `python -m fumes.statusline` (see
[`fumes/guides/claude.md`](fumes/guides/claude.md)) and the cell fills in during your sessions.

## Configure

Settings live in `%APPDATA%\fumes\config.toml`; every key is optional (see
[`config.example.toml`](config.example.toml)). Your own logo for a provider can go in
`%APPDATA%\fumes\logos\<id>.svg` or `.png`. Window position, the collapsed state and the last
good readings are kept in `state.json` in the same folder.

- `url`, `limits_url`: an optional remote feed and Claude limits document, used instead of the
  built-in collectors and the local status-line file when set.
- `providers`: which providers to show, in order.
- `anchor`: `tray` (default, left of the notification area), `left` or `right`.
- `theme`: `auto` (follows Windows), `dark` or `light`.
- `[links]`: the console page opened from each provider's peek (http/https only). Links open in
  Chrome when it is installed (`browser = "default"` uses the system browser).

**Start with Windows** (in the menu) writes `HKCU\Software\Microsoft\Windows\CurrentVersion\Run\Fumes`,
which starts Fumes with the same Python and from the same folder it runs from now. If you move the
folder or rebuild the virtual environment, untick and tick it again.

## Develop

```
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest
QT_QPA_PLATFORM=offscreen .venv/bin/python -m fumes --once-png out.png --feed-file tests/sample-feed-live.json \
    --limits-file tests/sample-limits.json --theme dark --dpr 2
```

`--once-png` renders the strip (or `--peek <id>`, `--collapsed`, `--hover <id>`) to a PNG without a
taskbar, so most of the UI can be developed and tested off Windows. How the code fits together, and
what can only be checked on Windows, is in [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

## Credits and licence

Fumes is free software under the [GNU General Public License v3.0](LICENSE).

- Font: [Inter](https://rsms.me/inter/) 4.1 by Rasmus Andersson, SIL Open Font License 1.1
  ([`fumes/fonts/LICENSE-Inter.txt`](fumes/fonts/LICENSE-Inter.txt)).
- Provider logos: [@lobehub/icons](https://github.com/lobehub/lobe-icons) 1.102.0, MIT licence. The
  logos and names are trademarks of their owners; Fumes is not affiliated with or endorsed by any of
  them.
