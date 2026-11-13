"""A fake Http for the collector tests: URL prefix -> canned answers, recording every request."""
from fumes.collect.http import ProviderError, refusal


class FakeHttp:
    def __init__(self, routes=None):
        self.routes = {}              # prefix -> (status, body) or a list of them (consumed in order)
        self.calls = []               # (url, headers)
        for k, v in (routes or {}).items():
            self.add(k, v)

    def add(self, prefix, answer):
        self.routes[prefix] = answer

    def get(self, url, headers):
        self.calls.append((url, dict(headers)))
        for prefix in sorted(self.routes, key=len, reverse=True):
            if url.startswith(prefix):
                ans = self.routes[prefix]
                if isinstance(ans, list):
                    ans = ans.pop(0) if len(ans) > 1 else ans[0]
                if isinstance(ans, Exception):
                    raise ans
                return ans
        raise ProviderError("no route")

    def get_json(self, url, headers):
        status, body = self.get(url, headers)
        if status != 200 or not isinstance(body, dict):
            raise ProviderError(refusal(status, body))
        return body
