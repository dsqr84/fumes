# Claude subscription limits

The leftmost cell shows how much of your Claude Pro or Max plan's **5-hour** and **weekly** usage
windows you have used, with a blue bar for a per-model weekly limit (for example Fable) when your plan
has one.

## Nothing to set up

Use [Claude Code](https://claude.com/claude-code) with your Claude subscription (signed in with
`/login`, not an API key). When the Claude Code program is installed on this computer, Fumes starts it
in the background every five minutes with the `/usage` command and records the answer. That is a local
command: it makes no model call, costs nothing and leaves no session behind. The readings stay on your
computer.

On Windows this needs Claude Code's own installer (`claude.exe`); the npm version is not started. To
turn it off, set `limits_poll_minutes = 0` in `config.toml`.

## Optional: a reading after every answer

Fumes also includes a small status line script. With it, the bars move after each Claude Code answer
instead of every five minutes. Settings shows the exact line for your machine, with a **Copy** button,
to add to your Claude Code settings (`%USERPROFILE%\.claude\settings.json`).

**If you already have a status line, keep it.** Claude Code has one status line, so adding Fumes's
replaces yours, and you do not need it: the bars work without it. Settings hides the line when it sees
you have your own.

The script writes one small file in the Fumes folder and sends nothing anywhere.

## Claude Code on another computer

If Claude Code runs on a different computer, the status line there writes the readings there. Publish
that computer's `claude-limits.json` somewhere this one can reach, and enter its address in the box
at the top of this page. Fumes then reads the limits from that address and ignores this computer's
status line. **Use this computer** switches back.
