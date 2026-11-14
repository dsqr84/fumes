"""A paint that raises would abort the whole app under PyQt6, so paint events catch everything and come
here instead: the widget simply draws nothing this frame, and the first failure per widget class is
reported to stderr (when there is one: the windowed release build has none)."""

import sys
import traceback

_seen = set()


def paint_failed(widget):
    name = type(widget).__name__
    if name in _seen:
        return
    _seen.add(name)
    try:
        if sys.stderr is not None:
            print(f"fumes: {name} could not be drawn:", file=sys.stderr)
            traceback.print_exc(file=sys.stderr)
    except Exception:       # noqa: BLE001
        pass


def install_excepthook():
    """PyQt6 aborts the whole process on an exception escaping any Qt callback (paint, mouse, keys, a slot)
    while sys.excepthook is Python's default; with a hook of our own it calls that instead and carries on.
    So one bad value can cost a frame or a click, never the app. Reports each distinct error once."""
    seen = set()

    def hook(kind, value, tb):
        sig = (kind.__name__, str(value)[:200])
        if sig in seen:
            return
        seen.add(sig)
        try:
            if sys.stderr is not None:
                print("fumes: unexpected error (the app keeps running):", file=sys.stderr)
                traceback.print_exception(kind, value, tb, file=sys.stderr)
        except Exception:       # noqa: BLE001
            pass

    sys.excepthook = hook
    return hook
