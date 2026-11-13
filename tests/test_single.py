import uuid

from PyQt6.QtCore import QCoreApplication
from PyQt6.QtWidgets import QApplication

from fumes import single


def _app():
    return QApplication.instance() or QApplication([])


def test_name_is_per_user_and_safe():
    n = single.server_name()
    assert n.startswith("fumes-") and all(c.isalnum() or c in "-_." for c in n)


def test_nothing_running_means_no_one_to_ask():
    _app()
    assert single.ask_running_to_quit("fumes-test-" + uuid.uuid4().hex, 200) is False


def test_a_running_copy_is_told_to_quit_and_the_name_is_free_after():
    """The old copy's server lives in this process; a client asks it to quit (run in the event loop the
    way a second process would see it), and once it closes the new copy can listen on the same name."""
    _app()
    name = "fumes-test-" + uuid.uuid4().hex
    quits = []
    old = single.listen(name, on_quit=lambda: (quits.append(1), old.close()))
    assert old is not None

    from PyQt6.QtNetwork import QLocalSocket
    sock = QLocalSocket()
    sock.connectToServer(name)
    assert sock.waitForConnected(1000)
    sock.write(single.QUIT)
    sock.flush()
    for _ in range(200):
        QCoreApplication.processEvents()
        if quits:
            break
    assert quits == [1]
    new = single.listen(name, on_quit=lambda: None)
    assert new is not None
    new.close()


def test_other_bytes_do_not_quit():
    _app()
    name = "fumes-test-" + uuid.uuid4().hex
    quits = []
    srv = single.listen(name, on_quit=lambda: quits.append(1))
    from PyQt6.QtNetwork import QLocalSocket
    sock = QLocalSocket()
    sock.connectToServer(name)
    assert sock.waitForConnected(1000)
    sock.write(b"hello\n")
    sock.flush()
    for _ in range(100):
        QCoreApplication.processEvents()
    assert quits == []
    srv.close()
