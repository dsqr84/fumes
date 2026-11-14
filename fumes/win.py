"""All Windows-only calls (ctypes/user32, winreg). Imported lazily; no-ops off Windows."""

import sys

from .place import Rect, is_fullscreen

ON_WINDOWS = sys.platform == "win32"
PERSONALIZE = r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_NAME = "Fumes"
CHROME_KEY = r"Software\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe"


def _user32():
    import ctypes
    from ctypes import wintypes

    u = ctypes.windll.user32
    u.FindWindowW.restype = wintypes.HWND
    u.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
    u.FindWindowExW.restype = wintypes.HWND
    u.FindWindowExW.argtypes = [wintypes.HWND, wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR]
    u.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    u.GetForegroundWindow.restype = wintypes.HWND
    u.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    u.MonitorFromWindow.restype = wintypes.HANDLE
    u.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
    u.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.c_void_p]   # undeclared, a 64-bit handle is truncated
    u.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                               ctypes.c_int, ctypes.c_int, wintypes.UINT]
    return u


def _rect(u, hwnd):
    import ctypes
    from ctypes import wintypes

    r = wintypes.RECT()
    if not hwnd or not u.GetWindowRect(hwnd, ctypes.byref(r)):
        return None
    return Rect(r.left, r.top, r.right, r.bottom)


def taskbar_rects():
    """(taskbar Rect, tray Rect or None) in physical px, or None if not found / not Windows."""
    if not ON_WINDOWS:
        return None
    try:
        u = _user32()
        tray_wnd = u.FindWindowW("Shell_TrayWnd", None)
        taskbar = _rect(u, tray_wnd)
        if taskbar is None:
            return None
        notify = u.FindWindowExW(tray_wnd, None, "TrayNotifyWnd", None)
        return taskbar, _rect(u, notify)
    except Exception:
        return None


def uses_light_theme():
    """True/False from the registry; None when unknown (off Windows)."""
    if not ON_WINDOWS:
        return None
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, PERSONALIZE) as k:
            return bool(winreg.QueryValueEx(k, "SystemUsesLightTheme")[0])
    except Exception:
        return None


def set_topmost(hwnd):
    """Re-assert HWND_TOPMOST without moving, resizing or activating."""
    if not ON_WINDOWS or not hwnd:
        return
    try:
        u = _user32()
        HWND_TOPMOST, FLAGS = -1, 0x0001 | 0x0002 | 0x0010  # NOSIZE|NOMOVE|NOACTIVATE
        u.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0, FLAGS)
    except Exception:
        pass


def foreground_fullscreen(strip_hwnd):
    """True if the foreground window covers the strip's monitor and is not the shell."""
    if not ON_WINDOWS:
        return False
    try:
        import ctypes
        from ctypes import wintypes

        u = _user32()
        fg = u.GetForegroundWindow()
        if not fg or fg == strip_hwnd:
            return False
        buf = ctypes.create_unicode_buffer(256)
        u.GetClassNameW(fg, buf, 256)
        mon = u.MonitorFromWindow(fg, 2)  # MONITOR_DEFAULTTONEAREST
        if mon != u.MonitorFromWindow(strip_hwnd, 2):
            return False

        class MONITORINFO(ctypes.Structure):
            _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                        ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]

        mi = MONITORINFO()
        mi.cbSize = ctypes.sizeof(MONITORINFO)
        if not u.GetMonitorInfoW(mon, ctypes.byref(mi)):
            return False
        m = mi.rcMonitor
        return is_fullscreen(_rect(u, fg), Rect(m.left, m.top, m.right, m.bottom), buf.value)
    except Exception:
        return False


GUI_EXE, CLI_EXE = "Fumes.exe", "fumes-cli.exe"     # the release build (PyInstaller, one folder)
# Claude Code on Windows runs its status line through Git Bash, which expands these even inside double
# quotes: a folder named `Fumes$(...)` would run a command. Such a path is refused, not quoted.
SHELL_UNSAFE = "'\"$`!\\"


def frozen():
    return bool(getattr(sys, "frozen", False))


def _frozen_exe(name):
    """The quoted path of one of the release's two programs, beside the running one."""
    import os
    path = os.path.join(os.path.dirname(os.path.abspath(sys.executable)), name).replace("\\", "/")
    if any(ch in path for ch in SHELL_UNSAFE):
        raise ValueError(f"cannot quote {path!r} for a command")
    return path


def run_command(root=None, exe=None):
    """The Run-key value: pythonw.exe running the fumes package from `root` (the project folder).

    A Run entry has no working directory, so the folder goes on sys.path inside the command itself.
    No `cmd /c`: that would flash a console window at every login. In the release build it is just
    Fumes.exe.
    """
    import os

    if frozen() and root is None and exe is None:
        return f'"{_frozen_exe(GUI_EXE)}"'
    exe = exe or sys.executable
    if exe.lower().endswith("python.exe"):
        exe = exe[: -len("python.exe")] + "pythonw.exe"
    root = root or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return f'"{exe}" -c "{_launch_code(root)}"'


def statusline_command(root=None, exe=None):
    """The Claude Code status line command: the console python.exe (the status line needs stdout, so
    never pythonw.exe) running `fumes.statusline` from `root`; in the release build, `fumes-cli.exe statusline`
    (the console program: Fumes.exe has no stdout). Raises ValueError for a path with quotes."""
    import os

    if frozen() and root is None and exe is None:
        return f'"{_frozen_exe(CLI_EXE)}" statusline'

    exe = exe or os.path.abspath(sys.executable)      # abspath: no `..` in a command users paste
    if exe.lower().endswith("pythonw.exe"):
        exe = exe[: -len("pythonw.exe")] + "python.exe"
    exe = exe.replace("\\", "/")
    if any(ch in exe for ch in SHELL_UNSAFE):
        raise ValueError(f"cannot quote {exe!r} for a command")
    root = root or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    path = root.replace("\\", "/")
    if any(ch in path for ch in SHELL_UNSAFE):
        raise ValueError(f"cannot quote {root!r} for a command")
    return (f'"{exe}" -c "import sys,runpy;sys.path.insert(0,\'{path}\');'
            f'runpy.run_module(\'fumes.statusline\',run_name=\'__main__\')"')


def _launch_code(root):
    # Forward slashes: Windows accepts them, and no backslash can then end up before a quote.
    path = root.replace("\\", "/")
    if "'" in path or '"' in path:
        raise ValueError(f"cannot quote {root!r} for the Run key")
    return f"import sys,runpy;sys.path.insert(0,'{path}');runpy.run_module('fumes',run_name='__main__')"


def restart_argv(root=None, exe=None, args=None):
    """The argv for a fresh copy of this fumes: same interpreter, same package folder, same
    command-line options. (Detached, so a copy restarted from `python` runs without a console.)"""
    import os

    args = list(sys.argv[1:] if args is None else args)
    if frozen() and root is None and exe is None:
        return [_frozen_exe(GUI_EXE), *args]
    exe = exe or sys.executable
    root = root or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return [exe, "-c", _launch_code(root), *args]


def relaunch(argv=None, popen=None):
    """Start a fresh copy, detached so it outlives this process. Returns True if it started."""
    try:
        argv = argv or restart_argv()
        if popen is None:
            import subprocess

            flags = 0
            if ON_WINDOWS:
                flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
            popen = lambda a: subprocess.Popen(a, creationflags=flags, close_fds=True,  # noqa: E731
                                               stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                               stderr=subprocess.DEVNULL)
        popen(argv)
        return True
    except Exception:
        return False


def autostart_enabled():
    if not ON_WINDOWS:
        return False
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            winreg.QueryValueEx(k, RUN_NAME)
            return True
    except Exception:
        return False


def set_autostart(enabled, root=None):
    """Write or remove the Run value. Returns True on success (False off Windows)."""
    if not ON_WINDOWS:
        return False
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
            if enabled:
                winreg.SetValueEx(k, RUN_NAME, 0, winreg.REG_SZ, run_command(root=root))
            else:
                try:
                    winreg.DeleteValue(k, RUN_NAME)
                except FileNotFoundError:
                    pass
        return True
    except Exception:
        return False


def find_chrome():
    """Path of chrome.exe from the App Paths registry value (HKCU, then HKLM), or None."""
    if not ON_WINDOWS:
        return None
    try:
        import os
        import winreg

        for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
            try:
                with winreg.OpenKey(hive, CHROME_KEY) as k:
                    path = winreg.QueryValueEx(k, "")[0]
            except OSError:
                continue
            if isinstance(path, str) and path and os.path.isfile(path):
                return path
    except Exception:
        pass
    return None


def open_url(url, browser="chrome", chrome_lookup=None, popen=None):
    """Open url in Chrome. True when launched; False means the caller should fall back to the
    system opener (not http(s), browser = "default", Chrome not found, launch failed, not Windows).
    chrome_lookup and popen are injectable for tests. Argument list, never a shell string."""
    if not isinstance(url, str) or not url.startswith(("http://", "https://")):
        return False
    if browser != "chrome":
        return False
    lookup = chrome_lookup if chrome_lookup is not None else (find_chrome if ON_WINDOWS else (lambda: None))
    path = lookup()
    if not path:
        return False
    try:
        if popen is None:
            import subprocess
            popen = subprocess.Popen
        popen([path, url])
        return True
    except Exception:
        return False
