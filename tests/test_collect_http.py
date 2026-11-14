import pytest

from fumes.collect import http as h


def test_non_https_refused_before_request():
    calls = []

    class Recorder:
        def open(self, *a, **k):
            calls.append(a)
            raise OSError("would have connected")
    for url in ("http://example.com/x", "ftp://example.com", "file:///etc/passwd", "https:///x"):
        with pytest.raises(h.ProviderError, match="non-HTTPS"):
            h.Http(opener=Recorder()).get(url, {"Authorization": "Bearer k"})
    assert calls == []            # refused before any connection: the key never leaves


def test_redirect_not_followed():
    assert h._NoRedirect().redirect_request(None, None, 302, "Found", {}, "https://evil/") is None


class Resp:
    def __init__(self, status=200, body=b"{}"):
        self.status, self._b = status, body

    def read(self, n=-1):         # a stream: each read consumes what it returns
        out = self._b if n < 0 else self._b[:n]
        self._b = self._b[len(out):]
        return out

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class Opener:
    def __init__(self, result):
        self.result, self.reqs = result, []

    def open(self, req, timeout=None):
        self.reqs.append((req, timeout))
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def test_json_text_and_headers():
    op = Opener(Resp(200, b'{"a": 1}'))
    http = h.Http(opener=op)
    assert http.get("https://x.test/p", {"Authorization": "Bearer k"}) == (200, {"a": 1})
    req, timeout = op.reqs[0]
    assert req.get_header("Authorization") == "Bearer k" and timeout == h.TIMEOUT
    assert req.get_header("User-agent").startswith("fumes/")
    assert h.Http(opener=Opener(Resp(200, b"plain"))).get("https://x.test", {}) == (200, "plain")


def test_http_error_status_returned_not_raised():
    import io
    import urllib.error
    err = urllib.error.HTTPError("https://x.test", 302, "Found", {}, io.BytesIO(b'{"error": {"type": "t"}}'))
    assert h.Http(opener=Opener(err)).get("https://x.test", {}) == (302, {"error": {"type": "t"}})


def test_over_cap_body_raises():
    http = h.Http(max_bytes=10, opener=Opener(Resp(200, b"x" * 11)))
    with pytest.raises(h.ProviderError, match="too large"):
        http.get("https://x.test/a", {})
    assert h.Http(max_bytes=10, opener=Opener(Resp(200, b"x" * 10))).get("https://x.test", {})[0] == 200


def test_network_error_message_is_host_and_type_only():
    http = h.Http(opener=Opener(OSError("connect failed Bearer sk-secret-key at /path?key=sk-secret-key")))
    with pytest.raises(h.ProviderError) as e:
        http.get("https://api.test/v1/x?key=sk-secret-key", {"Authorization": "Bearer sk-secret-key"})
    assert str(e.value) == "api.test: OSError"
    assert "secret" not in str(e.value) and e.value.__cause__ is None


def test_get_json_non_200_and_non_dict():
    with pytest.raises(h.ProviderError, match="HTTP 401"):
        h.Http(opener=Opener(Resp(401, b'{"error": {"message": "bad key sk-abc"}}'))).get_json("https://x.test", {})
    with pytest.raises(h.ProviderError):
        h.Http(opener=Opener(Resp(200, b"[1]"))).get_json("https://x.test", {})


def test_refusal_keeps_only_code_shaped_types():
    body = {"error": {"type": "auth<script>error", "message": "Invalid key sk-abc123"}}
    r = h.refusal(401, body)
    assert r == "HTTP 401" and "sk-abc123" not in r                          # odd type: dropped whole
    assert h.refusal(403, {"error": {"type": "permission_error"}}) == "HTTP 403 (permission_error)"
    for echoed in ("sk-ant-admin01-ABCDEFGHIJKLMNOP", "sk-proj-abc123", "Bearer xyz"):
        assert h.refusal(401, {"error": {"code": echoed}}) == "HTTP 401"       # a reflected key never survives
    assert h.refusal(500, "oops text") == "HTTP 500"
    assert h.refusal(403, {"error": {"code": "forbidden"}}) == "HTTP 403 (forbidden)"
    assert h.refusal(400, {"type": "error", "error": "plain string"}) == "HTTP 400 (error)"
    assert h.refusal(400, {"error": "plain string"}) == "HTTP 400"
    assert h.refusal(400, {"error": {"type": "a" * 100}}) == "HTTP 400"          # longer than any code: dropped
