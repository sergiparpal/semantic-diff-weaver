# Repository Guidelines

## Project Structure & Module Organization

Production code lives in `semantic_diff_weaver/`. The `git_diff/` package collects committed Git data, the `ast_diff/` package extracts structural changes, and `service.py` orchestrates the pipeline. `source.py` holds the `SourceRevisionPair` contract between them, so `ast_diff/` never imports `git_diff/`. `taxonomy.py` is the single per-`BehaviorCategory` profile consumed by `scoring.py`, `obligations.py`, and `test_mapper.py`. `plugin.py`, root `__init__.py`, and `plugin.yaml` provide Hermes registration.

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

Use Python 3.11 or newer and install the exact development versions from `requirements-dev.lock`.

## Coding Style & Naming Conventions

Use four-space indentation, explicit UTF-8 encodings, type annotations, and a 100-character line target. Ruff is authoritative. Use `snake_case` for modules and functions, `PascalCase` for classes and Pydantic models, and `UPPER_SNAKE_CASE` for constants. Preserve stable error codes, schemas, taxonomy values, and evidence IDs.

## Testing Guidelines

Pytest files and functions use `test_*.py` and `test_*`. Add unit tests for algorithms, contract tests for interfaces, and integration tests for end-to-end behavior. Security-sensitive changes require adversarial regressions. Maintain at least 85% overall coverage and 90% branch coverage for critical boundary and transport modules. Tests must be offline and deterministic; use fake Hermes LLM responses.

## Commit & Pull Request Guidelines

History uses Conventional Commit subjects, for example `feat: implement semantic diff weaver MVP`. Continue with `feat:`, `fix:`, `test:`, or `docs:`. Pull requests should explain behavior and contract impact, link issues, list commands run, and call out security, schema, evaluation, or documentation changes. Include screenshots only when rendered Markdown changes materially.

## Security & Configuration Tips

Treat repositories, Git metadata, YAML, and model output as untrusted. Never execute or import analyzed code, follow paths outside the repository, expose secrets, use shell-interpolated Git commands, or weaken deterministic fallback and evidence validation to satisfy a test.
