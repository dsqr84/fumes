"""Open a web link: Chrome when asked and found, else the system browser. Qt."""

from PyQt6.QtCore import QUrl
from PyQt6.QtGui import QDesktopServices

from . import win

DETAILS_MARKER = "fumes:details"   # an internal footer link: opens the Details window, never a browser


def open_link(cfg, url, opener=None, fallback=None):
    """Chrome via win.open_url, falling back to QDesktopServices. Only http(s) URLs are opened."""
    if not isinstance(url, str) or not url.startswith(("http://", "https://")):
        return False
    opener = opener or win.open_url
    if opener(url, cfg.browser):
        return True
    fallback = fallback or (lambda u: QDesktopServices.openUrl(QUrl(u)))
    return bool(fallback(url))
