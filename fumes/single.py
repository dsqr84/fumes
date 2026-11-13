"""One fumes at a time. A new copy asks the running one to quit, waits for it, then takes the name.

Starting fumes again (after a pull, from autostart, or from the menu's Restart) therefore replaces the
running copy instead of drawing a second strip over it. A copy too old to listen cannot be asked; the new
one runs anyway, and the old one has to be ended by hand once."""

import getpass
import re

from PyQt6.QtCore import QCoreApplication
from PyQt6.QtNetwork import QLocalServer, QLocalSocket

QUIT = b"quit\n"


def server_name():
    try:
        user = getpass.getuser()
    except Exception:
        user = "user"
    return "fumes-" + (re.sub(r"[^A-Za-z0-9_.-]", "_", user) or "user")


def ask_running_to_quit(name, timeout_ms=5000):
    """True if a running copy answered and has gone; False if none was listening (or it did not go)."""
    sock = QLocalSocket()
    sock.connectToServer(name)
    if not sock.waitForConnected(500):
        return False
    sock.write(QUIT)
    sock.flush()
    sock.waitForBytesWritten(500)
    gone = sock.state() == QLocalSocket.LocalSocketState.UnconnectedState or sock.waitForDisconnected(timeout_ms)
    sock.abort()
    return gone


def listen(name, on_quit=None):
    """Serve the name so a later copy can ask this one to quit. Returns the server (keep a reference) or None."""
    on_quit = on_quit or QCoreApplication.quit
    server = QLocalServer()
    server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
    if not server.listen(name):
        QLocalServer.removeServer(name)          # a stale socket file left by a crash (Unix)
        if not server.listen(name):
            return None

    def accept():
        while server.hasPendingConnections():
            s = server.nextPendingConnection()
            s.setParent(server)

            def read(s=s):
                if bytes(s.readAll()).startswith(QUIT.strip()):
                    on_quit()
            s.readyRead.connect(read)
            if s.bytesAvailable():
                read()
    server.newConnection.connect(accept)
    return server


def take_over(name=None, timeout_ms=5000):
    name = name or server_name()
    ask_running_to_quit(name, timeout_ms)
    return listen(name)
