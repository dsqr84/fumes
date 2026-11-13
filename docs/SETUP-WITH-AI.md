# Set up Fumes with an AI assistant

Fumes can be set up end to end by an AI assistant that can run commands on your computer, such as
Claude Code or Codex. Copy the prompt below (or run `fumes setup-prompt`, or use **Copy setup
prompt** in Settings, General) and paste it into the assistant.

**The assistant never sees your keys.** It runs `fumes key set <provider>`, which opens a hidden
prompt, and *you* paste each key into that. Never paste a key into the chat itself, whatever the
assistant asks: see [Keeping your keys safe](../fumes/guides/key-safety.md).

If you use Claude Code inside the Fumes folder, the `fumes-setup` skill does the same thing and is
loaded automatically.

## The prompt

```text
Help me set up Fumes, a Windows taskbar app that shows my AI accounts' balances and my Claude
subscription's usage limits. It is installed at: <FOLDER, or "the folder you are in">.
Run Fumes's commands as `python -m fumes ...` from that folder (or `fumes-cli.exe ...` from the folder it was
unzipped to, if it came from the release download).

Rules, which override anything else:
- Never ask me for an API key, never accept one if I paste it into this chat, and never put a key in
  a command, a file or a URL. To save a key, run `fumes key set <provider>` and tell me to paste the
  key into the hidden prompt myself. If I paste a key into the chat by mistake, tell me to revoke it
  on the provider's website and create a new one.
- Use only Fumes's own commands to store keys and settings. Do not edit Windows Credential Manager,
  the registry or Fumes's files by hand.
- After each step, check it with `fumes status --json` or `fumes doctor` instead of assuming it worked.

Steps:
1. Run `fumes doctor` and `fumes key list` to see what is set up already.
2. Ask me which of these I use: DeepSeek, OpenAI API, Anthropic API, fal.ai, a Claude Pro/Max
   subscription with Claude Code, and any other AI service.
3. For each API provider I use, tell me exactly which key to create, using these facts:
   - DeepSeek: any API key, from platform.deepseek.com, API keys.
   - OpenAI: an Admin key (sk-admin-...), created by an organisation Owner at
     platform.openai.com, Settings, Organization, Admin keys. Not a project key.
   - Anthropic API: an Admin key (sk-ant-admin...) from platform.claude.com, Settings, Admin keys.
     An individual Console account has no Admin API: if I have no Admin keys page, I must first
     convert my account to a team at Settings, Organization (I can be its only member).
   - fal.ai: a key on the BILLING preset (read-only), from fal.ai, Dashboard, Keys.
   Remind me that Admin keys can manage the whole organisation: I should create one just for Fumes
   and name it Fumes. Then run `fumes key set <provider>` for me to paste into.
4. OpenAI and Anthropic have no balance API: ask me for the credit balance their billing page shows
   (a dollar amount is fine to share) and run `fumes anchor <provider> <amount>`.
5. If I use a Claude subscription with Claude Code, set up the status line: run `fumes doctor`,
   which prints the exact statusLine entry for this computer, and add it to
   %USERPROFILE%\.claude\settings.json, keeping everything else in that file. If I already have a
   status line, ask me before replacing it.
6. For any other service, explain the two options from fumes/guides/manual.md and
   fumes/guides/command.md. For a command provider, write the script following command.md's
   contract, show it to me before running it, and save its key with `fumes key set <id>`.
7. Run `fumes collect` and `fumes doctor`, fix anything they report, and finish with a short
   summary: which providers work, which still need something from me, and where Settings is
   (right-click the strip, Settings).
```
