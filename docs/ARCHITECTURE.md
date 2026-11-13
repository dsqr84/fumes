# Fumes: architecture and landmines

A Windows 11 taskbar strip (Python, PyQt6, tray icon) showing each AI provider's logo and remaining balance, and
the Claude subscription's usage windows. Windows-only at runtime; nearly all of it renders and is tested off Windows
through `--once-png` and the offscreen Qt platform.

These are durable facts about the code: how the parts fit and what is baked in that is easy to break.

## Facts

- **Collectors** (`fumes/collect/`): with no `url` set, each refresh runs `collect.collect()` on a `CollectThread`,
  which returns the same payload shape as the remote feed, so parsing, cache and display are shared. One module per
  provider (`run(ctx)`, and `spend_today(ctx)` for the anchored ones); a failing provider keeps its last figures as
  `stale`. `collect/http.py` is the only network path for keys: HTTPS only (refused before connecting), redirects
  never followed (urllib would resend the key header), 8 MB cap, 20 s timeout, and errors carry the host and
  exception type or the HTTP status and error type only, never a body (a provider's message can quote the key).
- **Wrong-provider check** (`keycheck.py`): a key whose format clearly belongs to another provider (`sk-ant-`,
  `sk-proj-`/`sk-admin-`/`sk-svcacct-`, legacy OpenAI keys containing `T3BlbkFJ`, DeepSeek's `sk-` + 32 hex) is never
  saved or sent: Save, Test (`settings.run_test` is the last gate), `fumes key set`, `collect()` and `set_anchor`. Only
  unmistakable formats are recognised (fal's is not); anything else passes. `kind_hint` warns about the right
  provider's wrong kind of key (project or workspace keys where an Admin key is needed).
- **Keys** (`keystore.py`): Windows Credential Manager generic credentials `Fumes/<id>`, persisted per user on this
  machine, through ctypes (no extra dependency). Off Windows, read-only `FUMES_KEY_<ID>` environment variables.
  **The ctypes calls are not run off Windows.**
- **Ledger** (`collect/ledger.py`, `ledger.json`): anchors (a credit the user entered for OpenAI or Anthropic, which
  have no balance API; `day_spend` keeps today's spend from being subtracted twice, since costs come per whole day)
  and balance samples (DeepSeek has no usage API: spend is the falls between readings, rises are top-ups).
  Loaded as untrusted; replaced atomically.
- **Claude limits, local** (`statusline.py`): `python -m fumes.statusline` as Claude Code's status line writes
  `claude-limits.json` in the config folder; with no `limits_url` the App reads that file (64 KB cap; no file is
  "no reading", a bad one dims the cell). It must never import Qt (a test checks) and never print an error.

- **Feed** (optional, `url` in the config; off when unset): a JSON document with a `providers` map, fetched every
  `refresh_minutes`; the last good payload is cached in `state.json`, so a failed fetch shows the old figures dimmed.
  `tests/sample-feed.json` (synthetic) covers `mtd` and the hidden no-figure provider;
  `tests/sample-feed-live.json` (synthetic) is the newer shape (balances for OpenAI and Anthropic, plus a hidden `example` provider).
- **Logos**: `fumes/logos/*.svg` from @lobehub/icons-static-svg 1.102.0 (MIT). `openai` and `anthropic`
  use `fill="currentColor"`, which QtSvg paints black: `fmt.recolor_svg` substitutes the ink colour.
  `acme` (the tests' made-up provider) and unknown ids get a drawn badge. User overrides: `<config folder>/logos/<id>.svg|png`.
- **Cannot be tested off Windows**: every real call in `win.py` (taskbar/tray rects, registry theme,
  SetWindowPos topmost, foreground-fullscreen, Run-key autostart), the tray icon and its click/menu,
  always-on-top and no-focus behaviour, and DPI on a real multi-monitor display. Only the pure maths
  (`place.py`) and the no-op fallbacks are tested.
- **Landmines**: the strip paints a 1/255-alpha fill so it stays clickable (fully transparent pixels of a
  layered window are click-through on Windows). Win32 rects are physical px and divided by one
  primary-screen dpr, so a secondary monitor with a different scale may be off.
- **Peek modes** (`feed.peek_mode`): `full`, `figures` (a balance but no spend data: no chart or breakdown) and `empty`
  (header, the note in primary ink, footer). "No spend in the last 30 days" is only for a connected provider. One neutral chart
  colour for every provider; backticks are stripped from feed text (`fmt.clean_text`). The footer is always one row.
- **Peek** (`peek.py`): a `Qt.Popup` window. Everything from the feed is drawn with `QPainter.drawText`, never
  rich text; console URLs come from `config.py` (`DEFAULT_LINKS`, `[links]`), never from the feed. It relies on
  Qt delivering a press that lands outside the popup to the popup's own `mousePressEvent` (then it switches
  provider or closes; a deferred re-show covers Qt closing the popup after the handler). **Not run on Windows:**
  that mouse grab and outside-press delivery, popup stacking against the taskbar and the no-focus strip, cursor shapes.
- **Move mode** (right-click menu, Move): drag only moves the strip in this mode; outside it a left click on a cell
  opens the peek. Esc needs `grabKeyboard()` (the strip takes no focus), **not run on Windows**: right-click is the
  guaranteed way to cancel. Links open via `win.open_url` (Chrome from the App Paths registry key, argument-list
  `Popen`), falling back to `QDesktopServices`; the real Chrome launch is untested off Windows.
- **Claude limits cell** (`limits.py`, Qt-free; painting in `gauge.py`): a second document (`limits_url`) written by a
  Claude Code status line script, so the readings move only while a Claude Code session is active. Untrusted input: a bad window is absent, `used_percentage` clamps
  to 999, a window at or past `resets_at` reads 0 % used. `tests/sample-limits.json` is synthetic. Logo
  `fumes/logos/claude.svg` from @lobehub/icons-static-svg 1.102.0 (MIT), fill `#D97757` baked in. Cells are keyed
  `(kind, id)`; the limits cell is `("limits", "claude")`, so a feed provider called `claude` cannot collide
  (`[links] claude` sets the limits cell's Usage link, never a provider's console).
  **The strip shows no numbers for it**: a label (`5h`, `wk`) and a 28 x 5 px horizontal bar per window, one window per row.
  The gradient (`limits.GRADIENT`, per theme) is fixed to the track and the fill only
  reveals it, so the colour is the reading; the fill is drawn as an intersected path, not a clip region (Qt clips are not
  antialiased). Numbers are in the tooltip and the peek. No reading: empty track at 0.10; reset window: 0 at 0.18.
- **Strip grammar**: opacity means data trust only (`ui.cell_opacity`): live cells are 1.0 (labels and the no-figure dash 0.62),
  not-live cells a flat 0.45 on every part, never multiplied. Month-to-date is `mtd` label + amount; no figure is an en dash.
  A provider that is `not_connected` with no balance and no mtd is hidden (`feed.is_hidden`, applied in `feed.select`, so
  the `providers` config cannot resurrect it); one with a hand-entered balance stays, dimmed. A hairline replaces the gap
  after the Claude cell. The chevron's direction follows the anchor (`place.chevron_tip`); hover plates repaint only when the
  hovered target changes and are suppressed in move mode.
- **Sizes and grid** (a one-row, logo-height version looked far too big on a real taskbar): the strip is
  40 px tall with two rows (centres 11 and 29, `Strip.row_centre`), like the taskbar clock. Logos 14 px, figure digits 9 px
  cap height (`FIG_CAP`, `ui.figure_pixel_size`), small labels 7.5 pt. Providers fill columns of two, top row first; a
  column's cells share x and width, the figure starts beside the logo. The Claude cell spans both rows (`row None`).
  Hit tests use x and y (`Strip.hit(x, y)`: each row owns its half); without y, the top row. `font_size` (pt) is only an override.
- **Collapse**: `Strip.offset` is the *wanted* offset from the anchor and only a drag rewrites it (`reposition(commit=True)`);
  clamping must not write back, or an offset valid for the narrow strip is lost on expanding. Tray/right anchors keep the
  right edge, left keeps the left. **Not run on Windows**: the real chevron click and taskbar placement after collapse.
- **One copy at a time** (`single.py`): each start connects to the local socket `fumes-<user>` (a named pipe on
  Windows), asks the running copy to quit, waits up to 5 s, then listens itself, before `state.json` is read. A copy
  older than this guard cannot be asked and has to be ended by hand once. Tested with two processes on Linux; **not run on Windows**.
- **Font**: Inter 4.1, bundled in `fumes/fonts/` (static TTFs, SIL OFL, licence beside them) and loaded by
  `fonts.py` onto the strip; the peek inherits it. Tabular figures (`tnum`) only on the strip's figure font: in the
  peek they space out the hyphens in model names. If loading fails the system font is kept.
