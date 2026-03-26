# Contributing to promptvault

Thanks for your interest in contributing! Here's how to get started.

## Setup

```bash
git clone https://github.com/reidemeister94/promptvault.git
cd promptvault
pip install -e .
pip install -r requirements-dev.txt
make setup-dev-env  # installs pre-commit hooks
```

## Development workflow

1. Create a branch: `git checkout -b my-feature`
2. Make your changes
3. Run tests: `pytest tests/ -v`
4. Run linter: `ruff check . && ruff format --check .`
5. Commit and push
6. Open a PR

## Guidelines

- **Zero dependencies.** promptvault uses only Python stdlib. Don't add `pip install` requirements.
- **Tests required.** All new features and bug fixes need tests. We use synthetic data only — never touch real `~/.claude/`.
- **Keep it simple.** Read the [Core Pillars](CLAUDE.md) before proposing large changes.

## What to work on

- Check [open issues](https://github.com/reidemeister94/promptvault/issues) for bugs and feature requests
- Look at the [Roadmap](README.md#roadmap) for planned features
- Open an issue to discuss before starting large changes

## Running tests

```bash
pytest tests/ -v          # all tests
pytest tests/test_sync.py  # specific module
```

## Code style

We use [ruff](https://docs.astral.sh/ruff/) with a 100-character line limit. Pre-commit hooks enforce this automatically.
