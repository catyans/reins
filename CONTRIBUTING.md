# Contributing to Reins

Thanks for your interest in contributing! Here's how to get started.

## Development Setup

```bash
git clone https://github.com/catyans/reins.git
cd reins
pip install -e ".[dev,all]"
pytest tests/ -v
```

## Making Changes

1. **Fork** the repo and create a branch from `main`
2. **Write code** — follow the existing style (ruff handles formatting)
3. **Add tests** — all new features and bug fixes need tests
4. **Run checks** before submitting:
   ```bash
   ruff check src/ tests/       # lint
   ruff format src/ tests/      # format
   pytest tests/ -v             # tests
   ```
5. **Submit a PR** — describe what you changed and why

## Commit Messages

Use [Conventional Commits](https://www.conventionalcommits.org/):

```
feat: add Slack webhook alerts
fix: budget not resetting on daily boundary
docs: update proxy setup guide
test: add integration tests for CrewAI adapter
chore: bump DuckDB dependency
```

## What to Contribute

- Bug fixes
- New framework adapters (see `src/reins/adapters/`)
- Model pricing updates (`src/reins/core/pricing.py`)
- Documentation improvements
- Test coverage improvements

## Code Style

- Python 3.9+ with `from __future__ import annotations`
- Type hints on all public functions
- No unnecessary abstractions — simple is better
- Tests go in `tests/unit/` or `tests/integration/`

## License

By contributing, you agree that your contributions will be licensed under the project's [BSL 1.1 License](LICENSE).

## Questions?

Open a [Discussion](https://github.com/catyans/reins/discussions) or reach out to the maintainer.
