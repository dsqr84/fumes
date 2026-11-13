"""python -m fumes [--once-png PATH] [--url URL] [--config FILE]"""

import argparse
import json
import sys
from pathlib import Path

from . import config as cfgmod
from . import feed as feedmod
from . import limits as limmod


def main(argv=None):
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
