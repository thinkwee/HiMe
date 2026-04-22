# Contributing to HiMe

Thanks for your interest in HiMe. This document outlines how to set up a dev environment, the conventions we follow, and the process for submitting changes.

For an architectural overview and deeper developer documentation, see [`docs/DEVELOPMENT.md`](docs/DEVELOPMENT.md).

## Getting started

1. Fork the repository and clone your fork.
2. Follow the setup steps in [`docs/DEVELOPMENT.md`](docs/DEVELOPMENT.md) to get the backend, frontend, and (optionally) the iOS app running locally.
3. Create a feature branch off `main`:
   ```bash
   git checkout -b feat/short-description
   ```

## Prerequisites

| Tool | Minimum version | Notes |
|------|----------------|-------|
| Python | 3.10+ | CI tests against 3.10, 3.11, 3.12 |
| Node.js | 20+ | For the frontend SPA |
| Ruff | latest | Python linter/formatter |

## Development workflow

- Make your change in small, focused commits.
- Run the test suite before pushing:
  ```bash
  python -m pytest tests/ -x -q
  ```
- For frontend changes, verify the build:
  ```bash
  cd frontend && npm run build
  ```
- If you touch Python code, run the linter -- it must pass with zero errors:
  ```bash
  ruff check backend/ tests/
  ```
- Keep type hints on all new functions and classes (this is enforced informally; CI will run `ruff` but type-checking is currently advisory).

## Commit messages

We use short, imperative-mood commit subjects:

- `add cron-driven analysis scheduler`
- `fix duplicate Telegram message dedup window`
- `refactor agent_loops to extract chat handling`

Avoid trailing periods. Body is optional but encouraged for non-trivial changes — explain *why*, not *what*.

## Pull requests

1. Push your branch and open a PR against `main`.
2. Fill in the PR template (summary + test plan).
3. Ensure CI is green.
4. A maintainer will review and either merge or request changes.

## Reporting bugs

Open an issue with:
- What you expected to happen.
- What actually happened.
- Steps to reproduce.
- Your environment (OS, Python version, Node version, LLM provider).
- Relevant log excerpts from `logs/backend.log`.

## Reporting security issues

**Do not** open a public issue for security vulnerabilities. See [`SECURITY.md`](SECURITY.md).

## Code of conduct

Be respectful. Disagreements are fine; personal attacks are not. We follow the spirit of the [Contributor Covenant](https://www.contributor-covenant.org/).
