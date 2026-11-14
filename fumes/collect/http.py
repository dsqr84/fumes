"""Outbound HTTP for the collectors: HTTPS only, certificate checked, no redirects, bounded.

Every call carries an API key in a header, so:
- only https:// URLs are fetched (a plain-HTTP URL is refused before any connection);
- redirects are never followed: urllib would resend the same headers, key included, to wherever
  the Location points, so a 3xx comes back as a plain status instead;
- the body is capped (MAX_BYTES) and every call has a timeout;
- an error names the host and the exception type only, never the response body or the URL's query.
"""

import json
import re
import time
import ssl
import urllib.error
import urllib.parse
import urllib.request

from .. import __version__

MAX_BYTES = 8 * 1024 * 1024
TIMEOUT = 20
DEADLINE = 60          # seconds for a whole request: a server dripping bytes cannot hold it open longer
USER_AGENT = f"fumes/{__version__}"


class ProviderError(Exception):
    """A provider could not be read this time (network, refusal, bad answer). Shown as stale or error."""


class NotConnected(Exception):
    """No usable key for this provider: shown as not connected, not as a failure."""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None          # urllib then raises HTTPError with the 3xx status, which get() returns


def _opener():
    ctx = ssl.create_default_context()
    return urllib.request.build_opener(urllib.request.HTTPSHandler(context=ctx), _NoRedirect())


class Http:
    def __init__(self, timeout=TIMEOUT, max_bytes=MAX_BYTES, opener=None):
        self.timeout, self.max_bytes = timeout, max_bytes
        self._opener = opener or _opener()

    def get(self, url, headers):
        """(status, body): body is the parsed JSON where it parses, else text."""
        parts = urllib.parse.urlsplit(url)
        if parts.scheme != "https" or not parts.hostname:
            raise ProviderError("refused a non-HTTPS URL")
        req = urllib.request.Request(url, headers=dict(headers, **{"User-Agent": USER_AGENT,
                                                                   "Accept": "application/json"}))
        try:
            with self._opener.open(req, timeout=self.timeout) as r:
                status, raw = r.status, _read_until(r, self.max_bytes + 1, time.monotonic() + DEADLINE)
        except urllib.error.HTTPError as e:
            status = e.code
            try:
                raw = e.read(self.max_bytes + 1)
            except Exception:
                raw = b""
            finally:
                e.close()
        except Exception as e:
            raise ProviderError(f"{parts.hostname}: {type(e).__name__}") from None
        if len(raw) > self.max_bytes:
            raise ProviderError(f"{parts.hostname}: answer too large")
        text = raw.decode("utf-8", "replace")
        try:
            return status, json.loads(text)
        except (ValueError, RecursionError):
            return status, text

    def get_json(self, url, headers):
        """The JSON object of a 200 answer; anything else raises ProviderError with a safe reason."""
        status, body = self.get(url, headers)
        if status != 200 or not isinstance(body, dict):
            raise ProviderError(refusal(status, body))
        return body


def _read_until(resp, limit, deadline):
    """Read at most `limit` bytes, in chunks, giving up at `deadline` (time.monotonic). Each chunk still has
    the socket timeout; the deadline bounds the whole body."""
    parts, n = [], 0
    while n < limit:
        if time.monotonic() > deadline:
            raise TimeoutError("the answer took too long")
        chunk = resp.read(min(65536, limit - n))
        if not chunk:
            break
        parts.append(chunk)
        n += len(chunk)
    return b"".join(parts)


def refusal(status, body):
    """A short reason for a non-200 answer: the status and the error's type or code only. A provider's
    message can quote part of the key it was sent, so it is never shown."""
    kind = ""
    if isinstance(body, dict):
        err = body.get("error")
        if isinstance(err, dict):
            kind = err.get("type") or err.get("code") or ""
        elif isinstance(body.get("type"), str):
            kind = body["type"]
    # Only something shaped like an error code is kept: a hostile or buggy answer could put (part of)
    # the key it was sent in here, and keys have digits, capitals and dashes that codes do not.
    kind = str(kind) if re.fullmatch(r"[a-z][a-z_]{0,39}", str(kind)) else ""
    return f"HTTP {status}" + (f" ({kind})" if kind else "")
