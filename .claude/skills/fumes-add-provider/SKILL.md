---
name: fumes-add-provider
description: Add a service Fumes does not support to the user's strip - as a manual provider (entered balance) or a command provider (a small script that prints Fumes's JSON), and optionally turn a working one into a built-in provider pull request. Use when the user wants Fumes to show a provider, reseller, prepaid card or local log that it does not support yet.
---

# Add a provider to Fumes

Read `fumes/guides/manual.md` and `fumes/guides/command.md` first: they define both kinds and the
command provider's JSON contract. Ask the user which service it is and whether it has an API or a
log that reports a balance or spend.

- **No API:** a manual provider. Tell the user to add it in Settings (Add provider, Manual) and
  enter its balance; offer a daily burn if they know their typical spend.
- **An API or a local log:** a command provider.
  1. Write the script to the contract in `fumes/guides/command.md`, following every rule in its "Have
     an AI write it" prompt: the key only from the `FUMES_KEY` environment variable, read-only
     endpoints, standard library only, HTTPS to the service's own host, a timeout, nothing but the
     JSON on standard output.
  2. Save it outside the repository (for example `%USERPROFILE%\fumes-providers\<id>.py`) and
     **show it to the user before it is run**.
  3. Tell the user which key to create and its narrowest permission, then run
     `python -m fumes key set <id>` for them to paste into. **Never see the key**: never ask for it,
     never accept it in the chat, never put it in a command or a file.
  4. Have the user add it in Settings (Add provider, Command) with the exact program and arguments,
     then choose Test; or run `python -m fumes collect` and read its line for the provider.

**Built in, for everyone:** when a command provider works and the service is widely used, offer to
turn it into a pull request following `docs/ADDING-A-PROVIDER.md` (module, guide, logo with its
licence, FakeHttp tests, registration, key prefix). Use made-up figures in every test fixture, never
the user's real data, and run the whole test suite before proposing it.
