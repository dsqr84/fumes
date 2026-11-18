---
name: fumes-setup
description: Set up Fumes (the Windows taskbar strip of AI balances and Claude usage limits) for the user, end to end - which keys to create, saving them without ever seeing them, entered credit, the Claude Code status line, and checking it all works. Use when the user asks to set up, configure or install Fumes, add their API keys to it, or fix a provider that shows as not connected.
---

# Set up Fumes

Follow the prompt in `docs/SETUP-WITH-AI.md` (the fenced block under "The prompt"); it is the single
source of truth for the steps and the facts about each provider's key. Run Fumes as
`python -m fumes ...` from the repository root.

The rules there are absolute. In short:

- **Never see a key.** Do not ask for one, do not accept one pasted into the chat, never put one in a
  command, a file or a URL. Save keys only with `python -m fumes key set <provider>` and have the
  user paste into its hidden prompt. If a key is pasted into the chat, tell the user to revoke it at
  the provider and make a new one.
- **Verify, do not assume.** After each step run `python -m fumes status --json` or
  `python -m fumes doctor` and read the result.
- **Leave Claude Code's settings alone.** Fumes needs no status line: it asks Claude Code for the
  limits itself. Never add or replace one in `~/.claude/settings.json`.

Provider facts (also in `fumes/guides/<provider>.md`): OpenAI and Anthropic need Admin keys
(OpenAI's with Read only permissions; Anthropic's with an expiry date), and an individual Anthropic Console account must be converted to a team first; fal needs a key on the
read-only BILLING preset; OpenAI and Anthropic also need the credit balance entered with
`python -m fumes anchor <provider> <amount>`.
