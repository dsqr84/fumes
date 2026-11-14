# Security

Fumes holds API keys, some of which (OpenAI and Anthropic Admin keys) can manage a whole
organisation. Please treat security reports accordingly.

## Reporting a vulnerability

Use GitHub's **Report a vulnerability** button on this repository (Security tab) to send a private
report. Please do not open a public issue for a security problem. Include what an attacker needs,
what they get, and steps to reproduce; never include a real key.

## What Fumes protects, and from whom

- **Keys** are stored only in Windows Credential Manager (`Fumes/<provider>`), never in a file or a
  log, never shown back, and sent only to their own provider's fixed HTTPS API host. A key that
  looks like another provider's is refused on every path. Any program running as your Windows user
  can read your credentials; Fumes cannot protect against malware on your account.
- **Network answers are untrusted**, from providers and from an optional remote feed: HTTPS only for
  keys, no redirects followed, bounded size and total time, numbers must be finite and sane, text is
  drawn as plain text, and an answer echoing a key is scrubbed before it is stored or shown.
- **A bad value never takes the app down.** Under PyQt6 an exception escaping any Qt callback would
  abort the process; Fumes installs its own exception hook and guards every paint, so the worst case
  is one blank frame.
- **Command providers** run a program you configured yourself, as a list of arguments with no shell,
  no console, its own key only (as `FUMES_KEY`), bounded output and a timeout. Batch files are
  refused, because Windows passes their arguments through `cmd.exe`. Commands are never taken from a
  remote feed.
- **Releases** are built by `.github/workflows/release.yml` on GitHub's runners from the tagged
  source, with every action pinned to a commit and every Python package to its PyPI hash, a SHA-256
  file beside each zip and, for the public repository, a build provenance attestation
  (`gh attestation verify <zip> --repo dsqr84/fumes`). The programs are not code-signed.

## Reviews

Before the first public release (v0.1.0) the code had a security review covering key storage and
handling, outbound HTTP, untrusted rendering, local code execution, the files Fumes reads and writes,
Windows integration (autostart, single-instance pipe, the Claude Code status line command), the
release pipeline and dependency pinning, and the AI setup prompt and skills. It was done twice,
independently: once by Claude (Anthropic) and once by GPT-6.1 (OpenAI, through Codex). Every
finding was fixed, with a test that fails without the fix, except two known limits:

- A command provider's own child processes are not contained: one that keeps the output pipe open
  can outlive the provider's timeout (Fumes no longer waits for it). A Windows Job Object would close
  this.
- Plain `http://` is accepted for an optional remote feed (which carries figures, never keys) so a
  feed on a private network works; use `https://` anywhere else.
