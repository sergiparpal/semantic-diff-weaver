# Repository Guidelines

## Project Structure & Module Organization

Production code lives in `semantic_diff_weaver/`. The `git_diff/` package collects committed Git data, the `ast_diff/` package extracts structural changes, and `service.py` orchestrates the pipeline. `source.py` holds the `SourceRevisionPair` contract between them, so `ast_diff/` never imports `git_diff/`. `taxonomy.py` is the single per-`BehaviorCategory` profile consumed by `scoring.py`, `obligations.py`, and `test_mapper.py`. `plugin.py`, root `__init__.py`, and `plugin.yaml` provide Hermes registration; the root `__init__.py` is also the loadable entry point of a `hermes plugins install OWNER/REPO` clone, and `after-install.md` is the post-install guide that install path shows.

Each package's `__init__.py` exports only its pipeline-facing surface. Import internals (parsers, the process runner, limit constants) from their defining module rather than widening the facade, and do not re-export private names.

Tests are grouped under `tests/unit`, `contract`, `integration`, `security`, `performance`, and `evaluation`; reusable inputs belong in `tests/fixtures`. User-facing design and operational notes belong in `docs/`.

## Build, Test, and Development Commands

- `python -m pytest`: run the complete test suite.
- `python -m pytest tests/unit tests/contract`: run fast model and interface checks.
- `python -m pytest tests/integration`: exercise temporary Git repositories and transports.
- `python -m pytest tests/security tests/performance`: verify security and resource budgets.
- `python -m pytest --cov=semantic_diff_weaver --cov-branch`: produce branch coverage.
- `python -m ruff check .`: lint for correctness and imports.
- `python -m ruff format --check .`: verify formatting; omit `--check` to reformat.
- `python -m build`: create wheel and source distributions in `dist/`.
- `python scripts/check_action_pins.py`: verify every GitHub Actions pin in the workflows and
  in the documentation examples is a full commit SHA, carries a `# vX.Y.Z` comment, and names
  the same commit everywhere.

Use Python 3.11 or newer and install the exact development versions from `requirements-dev.lock`.

## Coding Style & Naming Conventions

Use four-space indentation, explicit UTF-8 encodings, type annotations, and a 100-character line target. Ruff is authoritative, and its lint set includes flake8-bandit (`S`); the only exemptions are the documented per-file ignores for tests and scripts in `pyproject.toml`. Use `snake_case` for modules and functions, `PascalCase` for classes and Pydantic models, and `UPPER_SNAKE_CASE` for constants. Preserve stable error codes, schemas, taxonomy values, and evidence IDs.

## Testing Guidelines

Pytest files and functions use `test_*.py` and `test_*`. Add unit tests for algorithms, contract tests for interfaces, and integration tests for end-to-end behavior. Security-sensitive changes require adversarial regressions. Maintain at least 85% overall coverage and 90% branch coverage for every critical module named in `scripts/check_coverage.py` — the boundary and transport modules plus the `ast_diff/` and `git_diff/` packages. Tests must be offline and deterministic; use fake Hermes LLM responses.

## Commit & Pull Request Guidelines

History uses Conventional Commit subjects, for example `feat: implement semantic diff weaver MVP`. Continue with `feat:`, `fix:`, `test:`, or `docs:`. Pull requests should explain behavior and contract impact, link issues, list commands run, and call out security, schema, evaluation, or documentation changes. Include screenshots only when rendered Markdown changes materially.

`main` is protected by a branch ruleset: **direct pushes are rejected**, and merging requires the `ci-complete` status check to pass. So the loop is branch, push, open a PR, let CI go green, merge. Review approvals are **not** required (the count is 0) — a solo change stays a one-person operation, gated by CI rather than by a second pair of eyes.

`ci-complete` is a single aggregating job that fails unless `test`, `action-pins`, and `hermes-compatibility` all succeeded. **Any new job must be added to its `needs:` list**, or it silently stops gating anything. The ruleset requires that one stable name rather than the individual matrix legs on purpose: a dropped Python version would otherwise become a required check that never reports again and would block every merge permanently. The `pr-review` workflow is deliberately outside the gate — it is dogfooding, not a check.

GitHub Actions are pinned to **full commit SHAs** with a trailing `# vX.Y.Z` comment, never to tags. Here that is not just convention but an enforced policy: `scripts/check_action_pins.py` runs offline in `ci` on every PR (so a Dependabot bump fails immediately rather than a week later) and again in the weekly `action-pins` cron with `--verify-remote --check-latest`. It also compares the workflows against the YAML quoted in `README.md` and `docs/`, which Dependabot never rewrites — so update the docs alongside any pin change or the check fails. This posture is shared across all six plugin repositories in this account.

## Security & Configuration Tips

Treat repositories, Git metadata, YAML, and model output as untrusted. Never execute or import analyzed code, follow paths outside the repository, expose secrets, use shell-interpolated Git commands, or weaken deterministic fallback and evidence validation to satisfy a test.
