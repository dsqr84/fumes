# Adding a built-in provider

Most services start as a [command provider](../fumes/guides/command.md) on one person's machine. When
one works and others would want it, it can become built in. This is the checklist for that pull
request. `tests/test_conformance.py` checks the mechanical parts for every registered provider.

## The pieces

| Piece | What it must do |
|---|---|
| `fumes/collect/<id>.py` | `ID`, `NAME`, `KEY_LABEL`, `ANCHORED`, `run(ctx)` returning `{"rows", "balance", "note"}`, and `spend_today(ctx)` if `ANCHORED`. Every request goes through `ctx.http` (HTTPS only, no redirects, bounded). Raise `NotConnected` for a missing or refused key and `ProviderError` for anything else. No new dependencies. |
| `fumes/guides/<id>.md` | Where to create the key, the narrowest kind or permission that works, what the figures mean. Starts with a `# Title`. |
| `fumes/logos/<id>.svg` | The provider's mark, from a source whose licence allows it; say which in the pull request. |
| `tests/test_providers.py` | FakeHttp cases: a normal answer, paging, malformed buckets, a missing balance, each refusal, the exact auth header, and that no note or error contains the key or the response body. No real keys or real account data, anywhere. |
| Registration | `collect.providers()`, `config.DEFAULT_LINKS` (an `https` console URL), `feed.KNOWN_ORDER`. |
| Key formats | If the provider's keys have an unmistakable prefix, add it to `keycheck._OWNERS` so a pasted key is never sent to the wrong provider. |

## The bar for review

- Official, documented, **read-only** endpoints only: balance, usage, costs. Nothing that spends,
  creates or changes anything.
- The **smallest key** the API accepts, named in the guide (fal's `BILLING` preset is the model).
- A fixed `https://` host per provider; the key goes only in the header the provider documents.
- Untrusted answers: a malformed or hostile response never raises out of `run()` as anything but
  `ProviderError`, and never puts response text into a note or error.
- Each new provider gets a security pass of its own before it is merged.
