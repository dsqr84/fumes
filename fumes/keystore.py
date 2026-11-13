"""API keys: Windows Credential Manager, never a file Fumes writes.

Each key is a generic credential named `Fumes/<provider id>`, persisted for this Windows user on
this machine (CRED_PERSIST_LOCAL_MACHINE: it does not roam). Windows encrypts it with the user's
logon secret; other users of the machine cannot read it, but any program running as this user can,
as with every credential in the Credential Manager.

Off Windows (development, tests) keys are read from the environment, `FUMES_KEY_<ID>`, and cannot
be saved. Nothing here logs, prints or returns a key except `get`.
"""

import os
import re
import sys

ON_WINDOWS = sys.platform == "win32"
PREFIX = "Fumes/"
MAX_KEY = 1024                  # far above any provider's key; the generic-credential blob limit is 2560 bytes
_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")


class KeyStoreError(Exception):
    """A key could not be stored or removed (the reason is safe to show)."""


def valid_key(value):
    """A pasted key, cleaned: surrounding whitespace dropped. None if it cannot be a key (empty, too long,
    spaces or control characters inside)."""
    if not isinstance(value, str):
        return None
    v = value.strip()
    if not v or len(v) > MAX_KEY or any(ch.isspace() or ord(ch) < 0x21 or ord(ch) == 0x7f for ch in v):
        return None
    return v


def _check_id(pid):
    if not isinstance(pid, str) or not _ID.match(pid):
        raise KeyStoreError("bad provider id")


class MemoryStore:
    """For tests: a dict."""

    def __init__(self, keys=None):
        self._keys = dict(keys or {})

    def get(self, pid):
        _check_id(pid)
        return self._keys.get(pid)

    def set(self, pid, value):
        _check_id(pid)
        v = valid_key(value)
        if v is None:
            raise KeyStoreError("that does not look like a key")
        self._keys[pid] = v

    def delete(self, pid):
        _check_id(pid)
        self._keys.pop(pid, None)

    def has(self, pid):
        return self.get(pid) is not None


class EnvStore:
    """Off Windows: FUMES_KEY_<ID> environment variables, read-only."""

    def get(self, pid):
        _check_id(pid)
        return valid_key(os.environ.get("FUMES_KEY_" + pid.upper().replace("-", "_"), ""))

    def set(self, pid, value):
        raise KeyStoreError("keys can only be saved on Windows; set FUMES_KEY_" + pid.upper() + " instead")

    def delete(self, pid):
        raise KeyStoreError("keys can only be removed on Windows")

    def has(self, pid):
        return self.get(pid) is not None


class WindowsStore:
    """Generic credentials through advapi32 (CredReadW / CredWriteW / CredDeleteW)."""

    CRED_TYPE_GENERIC = 1
    CRED_PERSIST_LOCAL_MACHINE = 2
    ERROR_NOT_FOUND = 1168

    def __init__(self):
        import ctypes
        from ctypes import wintypes

        class FILETIME(ctypes.Structure):
            _fields_ = [("dwLowDateTime", wintypes.DWORD), ("dwHighDateTime", wintypes.DWORD)]

        class CREDENTIALW(ctypes.Structure):
            _fields_ = [("Flags", wintypes.DWORD), ("Type", wintypes.DWORD), ("TargetName", wintypes.LPWSTR),
                        ("Comment", wintypes.LPWSTR), ("LastWritten", FILETIME),
                        ("CredentialBlobSize", wintypes.DWORD), ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)),
                        ("Persist", wintypes.DWORD), ("AttributeCount", wintypes.DWORD),
                        ("Attributes", ctypes.c_void_p), ("TargetAlias", wintypes.LPWSTR),
                        ("UserName", wintypes.LPWSTR)]

        self._ct, self._C = ctypes, CREDENTIALW
        adv = ctypes.WinDLL("advapi32", use_last_error=True)
        self._read = adv.CredReadW
        self._read.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                               ctypes.POINTER(ctypes.POINTER(CREDENTIALW))]
        self._read.restype = wintypes.BOOL
        self._write = adv.CredWriteW
        self._write.argtypes = [ctypes.POINTER(CREDENTIALW), wintypes.DWORD]
        self._write.restype = wintypes.BOOL
        self._delete = adv.CredDeleteW
        self._delete.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD]
        self._delete.restype = wintypes.BOOL
        self._free = adv.CredFree
        self._free.argtypes = [ctypes.c_void_p]
        self._free.restype = None

    def get(self, pid):
        _check_id(pid)
        ct = self._ct
        p = ct.POINTER(self._C)()
        if not self._read(PREFIX + pid, self.CRED_TYPE_GENERIC, 0, ct.byref(p)):
            return None                  # not found, or unreadable: either way there is no key to use
        try:
            c = p.contents
            raw = ct.string_at(c.CredentialBlob, c.CredentialBlobSize) if c.CredentialBlobSize else b""
        finally:
            self._free(p)
        try:
            return valid_key(raw.decode("utf-8"))
        except UnicodeDecodeError:
            return None

    def set(self, pid, value):
        _check_id(pid)
        v = valid_key(value)
        if v is None:
            raise KeyStoreError("that does not look like a key")
        ct = self._ct
        blob = v.encode("utf-8")
        buf = (ct.c_ubyte * len(blob)).from_buffer_copy(blob)
        c = self._C()
        c.Type = self.CRED_TYPE_GENERIC
        c.TargetName = PREFIX + pid
        c.Comment = "API key used by Fumes"
        c.CredentialBlobSize = len(blob)
        c.CredentialBlob = ct.cast(buf, ct.POINTER(ct.c_ubyte))
        c.Persist = self.CRED_PERSIST_LOCAL_MACHINE
        c.UserName = "fumes"
        ok = self._write(ct.byref(c), 0)
        ct.memset(buf, 0, len(blob))         # best effort: Python's own copy of the string cannot be wiped
        if not ok:
            raise KeyStoreError(f"Windows could not save the key (error {ct.get_last_error()})")

    def delete(self, pid):
        _check_id(pid)
        if not self._delete(PREFIX + pid, self.CRED_TYPE_GENERIC, 0):
            err = self._ct.get_last_error()
            if err != self.ERROR_NOT_FOUND:
                raise KeyStoreError(f"Windows could not remove the key (error {err})")

    def has(self, pid):
        return self.get(pid) is not None


def default_store():
    return WindowsStore() if ON_WINDOWS else EnvStore()
