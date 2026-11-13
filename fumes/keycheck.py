"""Recognise which provider a pasted key belongs to, before it is sent anywhere.

Sending a key to the wrong company is itself a leak, so a key that is clearly another provider's is
refused on every path: Save, Test, the CLI, and collection (a key stored before this check, or put
in the Credential Manager by hand, is still never sent). Only unmistakable formats are recognised;
an unknown one is let through, never guessed at.
"""

import re

NAMES = {"anthropic": "Anthropic", "openai": "OpenAI", "deepseek": "DeepSeek", "fal": "fal.ai"}   # built-ins only

# (pattern, provider id). Checked in order; the first match names the owner.
_OWNERS = [
    (re.compile(r"^sk-ant-"), "anthropic"),
    (re.compile(r"^sk-(proj|admin|svcacct)-"), "openai"),
    (re.compile(r"^sk-[A-Za-z0-9_-]*T3BlbkFJ"), "openai"),     # legacy OpenAI keys carry "OpenAI" in base64
    (re.compile(r"^sk-[0-9a-f]{32}$"), "deepseek"),
]

# (provider id, pattern, hint): the right provider but a key kind that provider's API will refuse.
_KINDS = [
    ("openai", re.compile(r"^sk-(proj|svcacct)-"),
     "This is a project key. OpenAI's costs API needs an Admin key (it starts with sk-admin-)."),
    ("openai", re.compile(r"^sk-[A-Za-z0-9_-]*T3BlbkFJ"),
     "This is an ordinary OpenAI key. The costs API needs an Admin key (it starts with sk-admin-)."),
    ("anthropic", re.compile(r"^sk-ant-api"),
     "This is a workspace API key. Anthropic's cost report needs an Admin key (it starts with sk-ant-admin)."),
]


def owner(key):
    """The provider a key clearly belongs to, or None when the format is not unmistakable."""
    if not isinstance(key, str):
        return None
    for pat, pid in _OWNERS:
        if pat.match(key):
            return pid
    return None


def foreign(pid, key):
    """A refusal message when `key` is clearly another provider's than `pid`, else None. The message
    never contains the key."""
    if pid not in NAMES:
        return None      # a user's own provider: the key goes to their local program, not to a company
    who = owner(key)
    if who is None or who == pid:
        return None
    return (f"This looks like {_a(NAMES.get(who, who))} key, not {_a(NAMES.get(pid, pid))} one. It was not "
            "saved or sent anywhere. Paste it on its own provider's page instead.")


def _a(name):
    return ("an " if name[:1].lower() in "aeiou" else "a ") + name


def kind_hint(pid, key):
    """A hint when the key is this provider's but the wrong kind for what Fumes calls, else None."""
    if not isinstance(key, str):
        return None
    for p, pat, hint in _KINDS:
        if p == pid and pat.match(key):
            return hint
    return None
