# Claude subscription limits

The leftmost cell shows how much of your Claude Pro or Max plan's **5-hour** and **weekly** usage
windows you have used. Claude Code reports these to its status line, so Fumes includes a small status
line script that saves each reading where Fumes can see it.

## Set it up

1. Make sure you use [Claude Code](https://claude.com/claude-code) with your Claude subscription
   (signed in with `/login`, not an API key).
2. Add the status line to your Claude Code settings (`%USERPROFILE%\.claude\settings.json`). Settings
   shows the exact line for your machine, with a **Copy** button.
3. Start a Claude Code session. The cell fills in after Claude Code's first answer, and updates while
   a session is active.

The readings stay on your computer: the script writes one small file in the Fumes folder and sends
nothing anywhere.

## Claude Code on another computer

If Claude Code runs on a different computer, the status line there writes the readings there. Publish
that computer's `claude-limits.json` somewhere this one can reach, and enter its address in the box
at the top of this page. Fumes then reads the limits from that address and ignores this computer's
status line. **Use this computer** switches back.
