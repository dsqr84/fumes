# Claude subscription limits

The leftmost cell shows how much of your Claude Pro or Max plan's **5-hour** and **weekly** usage
windows you have used, with a blue bar for a per-model weekly limit (for example Fable) when your plan
has one.

## Nothing to set up

Use [Claude Code](https://claude.com/claude-code) on this computer with your Claude subscription
(signed in with `/login`, not an API key). Every five minutes Fumes reads the limits one of two ways:

1. **With Claude Code's sign-in.** Fumes reads the sign-in Claude Code keeps on this computer and asks
   Anthropic for your usage, the same request Claude Code makes for its own `/usage` screen. The
   sign-in is used for that one request to `api.anthropic.com` and nothing else: Fumes never saves it,
   changes it, or sends it anywhere else.
2. **By asking Claude Code.** When the sign-in has expired or cannot be read, Fumes starts Claude Code
   in the background with the `/usage` command. That is a local command: no model call, no cost, no
   session left behind. On Windows this needs Claude Code's own installer (`claude.exe`).

Neither is an official, documented interface, so either could stop working after a Claude Code update;
the bars then keep their last reading. In `config.toml`, `limits_use_signin = false` turns the first way
off, and `limits_poll_minutes = 0` turns both off.

## Claude Code on another computer

If Claude Code runs on a different computer, that computer has to publish its limits as a small
`claude-limits.json` file that this one can reach (Fumes writes one in its folder wherever it runs).
Enter the file's address in the box at the top of this page. Fumes then reads the limits from that address. **Use this computer**
switches back.
