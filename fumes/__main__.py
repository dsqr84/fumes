"""python -m fumes [--once-png PATH] [--url URL] [--config FILE]"""

import argparse
import json
import sys
from pathlib import Path

from . import config as cfgmod
from . import feed as feedmod
from . import limits as limmod


def make_store():
    from . import keystore
    return keystore.default_store()


def make_http():
    from .collect import Http
    return Http()


def _now():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc)


def _demo_providers(folder):
    """With --settings custom-manual|custom-command|add-provider: a throwaway folder holding two example
    providers, so the pages can be rendered without touching the real config."""
    from .collect import custom
    custom.save(folder, [
        {"id": "prepaid", "name": "Prepaid card", "kind": "manual", "burn_per_day": 1.5},
        {"id": "acme", "name": "Acme", "kind": "command", "timeout": 20, "key": True,
         "argv": ["python", "C:\\Tools\\acme-balance.py", "--json"]},
    ])


COMMANDS = ("key", "anchor", "collect", "status", "doctor", "setup-prompt")


def _report(cmd, rest, folder, cfg_path):
    """setup-prompt, status and doctor: read-only, and never print a key."""
    from . import diagnose
    as_json = "--json" in rest
    rest = [a for a in rest if a != "--json"]
    if rest or (as_json and cmd == "setup-prompt"):
        print(f"usage: fumes {cmd}" + ("" if cmd == "setup-prompt" else " [--json]"), file=sys.stderr)
        return 2
    if cmd == "setup-prompt":
        sys.stdout.write(diagnose.setup_prompt())
        return 0
    store = make_store()
    if cmd == "status":
        from .collect import Ledger
        cfg = cfgmod.load_config(cfg_path)
        doc = diagnose.gather(folder, store, cfg, cfgmod.State.load(folder / "state.json"),
                              Ledger.load(folder / "ledger.json"))
        print(json.dumps(doc, indent=2) if as_json else diagnose.render_status(doc))
        return 0
    warnings = []
    cfgmod.load_config(cfg_path, warn=warnings.append)
    checks, snip = diagnose.run_doctor(folder, store, _now(), config_warnings=warnings, config_path=cfg_path)
    if as_json:
        print(json.dumps({"checks": checks, "snippet": snip}, indent=2))
    else:
        print(diagnose.render_doctor(checks, snip))
    return 1 if any(c["state"] == "fix" for c in checks) else 0


def cli(argv):
    """key / anchor / collect / status / doctor / setup-prompt subcommands. Returns an exit code, or None when
    argv is not one of them."""
    if not argv or argv[0] not in COMMANDS:
        return None
    import getpass

    from . import collect as col
    from . import keystore
    from .collect import custom

    cmd, rest = argv[0], list(argv[1:])
    folder = cfgmod.config_dir()
    cfg_path = folder / "config.toml"
    if "--config" in rest:            # the same folder the strip uses with --config, so the ledger is shared
        i = rest.index("--config")
        if i + 1 >= len(rest):
            print("fumes: --config needs a path", file=sys.stderr)
            return 2
        cfg_path = Path(rest[i + 1])
        folder = cfg_path.parent
        del rest[i:i + 2]
    try:
        if cmd in ("setup-prompt", "status", "doctor"):
            return _report(cmd, rest, folder, cfg_path)
        mine = custom.load(folder)       # the providers added in Settings (providers.json), never a feed's
        if cmd == "key":
            store = make_store()
            if rest == ["list"]:
                for m in list(col.providers()) + [c for c in mine if c.KEY_LABEL]:
                    print(f"{m.ID}\t{m.NAME}\t{m.KEY_LABEL}\t{'saved' if store.get(m.ID) else 'not saved'}")
                return 0
            if len(rest) == 2 and rest[0] in ("set", "delete"):
                m = col.by_id(rest[1], mine)
                if m is None:
                    print(f"fumes: unknown provider {rest[1]!r}", file=sys.stderr)
                    return 2
                if rest[0] == "set" and not m.KEY_LABEL:
                    print(f"fumes: {m.NAME} does not use a key", file=sys.stderr)
                    return 2
                if rest[0] == "set":
                    from . import keycheck
                    value = keystore.valid_key(getpass.getpass(f"{m.NAME} {m.KEY_LABEL} (not shown): "))
                    if value is None:
                        print("fumes: that does not look like a key; nothing saved", file=sys.stderr)
                        return 2
                    bad = keycheck.foreign(m.ID, value)
                    if bad:
                        print(f"fumes: {bad}", file=sys.stderr)
                        return 2
                    store.set(m.ID, value)
                    print(f"saved the {m.NAME} key")
                    hint = keycheck.kind_hint(m.ID, value)
                    if hint:
                        print(f"fumes: note: {hint}", file=sys.stderr)
                else:
                    store.delete(m.ID)
                    print(f"removed the {m.NAME} key from this computer. It still works until you revoke it "
                          f"on {m.NAME}'s website.")
                return 0
            print("usage: fumes key list | key set <id> | key delete <id>", file=sys.stderr)
            return 2
        if cmd == "anchor":
            if len(rest) != 2:
                print("usage: fumes anchor <id> <usd>", file=sys.stderr)
                return 2
            try:
                usd = float(rest[1].lstrip("$").replace(",", ""))
            except ValueError:
                print(f"fumes: {rest[1]!r} is not a dollar amount", file=sys.stderr)
                return 2
            ledger = col.Ledger.load(folder / "ledger.json")
            rec, warning = col.set_anchor(rest[0], usd, _now(), make_store(), ledger, http=make_http(),
                                          custom=mine)
            ledger.save()
            print(f"{rest[0]}: balance recorded {col.money(rec['usd'])} (today's spend so far "
                  f"{col.money(rec['day_spend'])})")
            if warning:
                print(f"warning: {warning}")
            return 0
        if rest:
            print("usage: fumes collect", file=sys.stderr)
            return 2
        ledger = col.Ledger.load(folder / "ledger.json")
        payload, failed = col.collect(_now(), make_store(), ledger, http=make_http(), custom=mine)
        ledger.save()
        for pid, p in payload["providers"].items():
            line = f"{pid}\t{p['status']}\tbalance {col.money(p.get('balance'))}\tmtd {col.money(p.get('mtd'))}"
            if p.get("error"):
                line += f"\t{p['error']}"
            print(line)
        return 1 if failed else 0
    except (keystore.KeyStoreError, ValueError, OSError) as e:
        print(f"fumes: {e}", file=sys.stderr)
        return 1


def main(argv=None):
    code = cli(list(sys.argv[1:] if argv is None else argv))
    if code is not None:
        return code
    ap = argparse.ArgumentParser(prog="fumes")
    ap.add_argument("--config", help="path to config.toml (default: the config folder)")
    ap.add_argument("--url", help="feed URL (overrides the config)")
    ap.add_argument("--once-png", metavar="PATH", help="render the strip to a PNG and exit")
    ap.add_argument("--feed-file", help="with --once-png: read the feed from this JSON file")
    ap.add_argument("--theme", choices=["dark", "light"], help="with --once-png: force the theme")
    ap.add_argument("--dpr", type=float, default=1.0, help="with --once-png: device pixel ratio")
    ap.add_argument("--now", help="with --once-png: pretend it is this ISO time (for age/staleness)")
    ap.add_argument("--peek", metavar="ID", help="with --once-png: render that provider's peek instead of the strip "
                    "(claude = the limits peek; provider:claude = a provider named claude)")
    ap.add_argument("--limits-file", help="with --once-png: read the Claude limits document from this JSON file")
    ap.add_argument("--collapsed", action="store_true", help="with --once-png: render the strip collapsed")
    ap.add_argument("--hover", metavar="TARGET", help="with --once-png: draw the hover plate on `claude`, a "
                    "provider id or `button`")
    ap.add_argument("--settings", metavar="PAGE", help="with --once-png: render the Settings window on that page "
                    "(a provider id, `claude` or `general`) instead of the strip")
    ap.add_argument("--details", action="store_true", help="with --once-png: render the Details window (the "
                    "whole page) instead of the strip")
    ap.add_argument("--details-table", action="store_true", help="like --details with the table shown")
    ap.add_argument("--stale", action="store_true", help="with --once-png: draw as a failed fetch")
    args = ap.parse_args(argv)

    cfg_path = Path(args.config) if args.config else cfgmod.config_dir() / "config.toml"
    cfg = cfgmod.load_config(cfg_path)
    if args.url:
        cfg.url = args.url
    folder = cfg_path.parent

    from PyQt6.QtWidgets import QApplication

    from . import ui

    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.setQuitOnLastWindowClosed(False)

    if args.once_png and args.settings:
        import tempfile

        from . import collect, keystore
        from .settings import SettingsWindow
        with tempfile.TemporaryDirectory() as tmp:
            if args.settings in ("custom-manual", "custom-command", "add-provider"):
                _demo_providers(Path(tmp))
            w = SettingsWindow(keystore.MemoryStore(), collect.Ledger(Path(tmp) / "ledger.json"), folder=Path(tmp),
                               cfg=cfg, theme=args.theme or "dark", dpr=args.dpr)
            if args.settings not in w.ids and args.settings not in ("custom-manual", "custom-command", "add-provider"):
                print(f"fumes: no settings page {args.settings!r}", file=sys.stderr)
                return 1
            if args.settings == "add-provider":
                return 0 if w.render_add_dialog(args.once_png, args.dpr) else 1
            w.show_page({"custom-manual": "prepaid", "custom-command": "acme"}.get(args.settings, args.settings))
            return 0 if w.render_png(args.once_png, args.dpr) else 1

    if args.once_png:
        strip = ui.Strip(cfg, user_logo_dir=folder, theme=args.theme)
        now = feedmod.parse_time(args.now)
        if now is not None:
            strip.now_fn = lambda: now
        strip.collapsed = args.collapsed
        try:
            if args.feed_file:
                feed = feedmod.parse_feed(json.loads(Path(args.feed_file).read_text("utf-8")))
            else:
                feed, _ = feedmod.fetch(cfg.url)
            strip.set_feed(feed, failed=args.stale, now=feedmod.parse_time(args.now))
        except (feedmod.FeedError, OSError, ValueError) as e:
            print(f"fumes: {e}", file=sys.stderr)
            strip.set_failed(True)
        if cfg.show_limits:
            try:
                if args.limits_file:
                    lim = limmod.parse_limits(json.loads(Path(args.limits_file).read_text("utf-8")))
                elif not args.feed_file:
                    lim, _ = limmod.fetch(cfg.limits_url)
                else:
                    lim = None
                if lim is not None:
                    strip.set_limits(lim, now=now)
            except (feedmod.FeedError, OSError, ValueError) as e:
                print(f"fumes: limits: {e}", file=sys.stderr)
                strip.set_limits_failed(True)
        if args.hover:
            strip._hover = (ui.BUTTON if args.hover == "button"
                            else ui.LIMITS_KEY if args.hover == "claude" else ("provider", args.hover))
        if args.details or args.details_table:
            from .details import DetailsWindow
            w = DetailsWindow(strip.theme, now_fn=strip.now_fn, tz=strip.tz, feed=strip.feed,
                              table=args.details_table)
            return 0 if w.render_png(args.once_png, args.dpr) else 1
        if args.peek:
            from .peek import Peek
            peek = Peek(strip)
            if args.peek == "claude":
                ok = peek.set_limits()
            else:
                ok = peek.set_provider(args.peek.removeprefix("provider:"))
            if not ok:
                print(f"fumes: no provider {args.peek!r} in the feed", file=sys.stderr)
                return 1
            return 0 if peek.render_png(args.once_png, args.dpr) else 1
        ok = strip.render_png(args.once_png, args.dpr)
        return 0 if ok else 1

    from . import single
    guard = single.take_over()  # noqa: F841 (before State.load: the old copy saves as it quits)
    state = cfgmod.State.load(folder / "state.json")
    keep = ui.App(cfg, state, user_logo_dir=folder)  # noqa: F841 (keeps references alive)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
