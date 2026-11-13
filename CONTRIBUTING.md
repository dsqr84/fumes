# Contributing to Fumes

Thanks for helping. Fumes holds people's API keys, so changes are held to a careful bar.

- **Run the tests** before you open a pull request:
  `QT_QPA_PLATFORM=offscreen python -m pytest` (see the README's Develop section).
- **A new provider?** Start with [docs/ADDING-A-PROVIDER.md](docs/ADDING-A-PROVIDER.md).
- **Never commit a key**, real account data or anything personal: test fixtures are made up. If a key
  ever lands in a commit, revoke it at the provider; removing the commit does not help.
- **Security issues**: please report them privately (GitHub's "Report a vulnerability" on the
  repository) rather than in a public issue.
- How the code fits together, and the landmines, are in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

By contributing you agree that your contribution is licensed under the GPL-3.0, like the rest of
Fumes.
