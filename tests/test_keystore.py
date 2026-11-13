import pytest

from fumes import keystore as ks


@pytest.mark.parametrize("bad", ["", "   ", "a b", "a\tb", "a\nb", "a\x00b", "a\x7fb", "x" * (ks.MAX_KEY + 1), None, 5])
def test_valid_key_rejects(bad):
    assert ks.valid_key(bad) is None


def test_valid_key_strips():
    assert ks.valid_key("  sk-abc \n") == "sk-abc"
    assert ks.valid_key("x" * ks.MAX_KEY) == "x" * ks.MAX_KEY


def test_memory_store():
    s = ks.MemoryStore()
    assert s.get("openai") is None and not s.has("openai")
    s.set("openai", " sk-1 ")
    assert s.get("openai") == "sk-1" and s.has("openai")
    s.delete("openai")
    assert s.get("openai") is None
    s.delete("openai")      # idempotent
    with pytest.raises(ks.KeyStoreError):
        s.set("openai", "has space")


@pytest.mark.parametrize("pid", ["", "UPPER", "../x", "a b", "x" * 40, None, "-a"])
def test_bad_ids_raise(pid):
    for store in (ks.MemoryStore(), ks.EnvStore()):
        with pytest.raises(ks.KeyStoreError):
            store.get(pid)
    with pytest.raises(ks.KeyStoreError):
        ks.MemoryStore().set(pid, "k")
    with pytest.raises(ks.KeyStoreError):
        ks.MemoryStore().delete(pid)


def test_env_store(monkeypatch):
    monkeypatch.setenv("FUMES_KEY_OPENAI", " sk-env ")
    monkeypatch.setenv("FUMES_KEY_FAL", "bad value")
    s = ks.EnvStore()
    assert s.get("openai") == "sk-env" and s.has("openai")
    assert s.get("fal") is None
    assert s.get("deepseek") is None
    with pytest.raises(ks.KeyStoreError):
        s.set("openai", "sk-new")
    with pytest.raises(ks.KeyStoreError):
        s.delete("openai")


def test_default_store_off_windows(monkeypatch):
    monkeypatch.setattr(ks, "ON_WINDOWS", False)
    assert isinstance(ks.default_store(), ks.EnvStore)


def test_windows_store_class_importable_without_windows():
    assert ks.WindowsStore.CRED_PERSIST_LOCAL_MACHINE == 2   # defined; constructing it needs advapi32
