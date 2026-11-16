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

## Claude Code on another computer

If Claude Code runs on a different computer, that computer has to publish its limits as a small
`claude-limits.json` file that this one can reach (Fumes writes one in its folder wherever it runs).
Enter the file's address in the box at the top of this page. Fumes then reads the limits from that address. **Use this computer**
switches back.
