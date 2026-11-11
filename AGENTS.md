# Repository Guidelines

## Project Structure & Module Organization

Production code lives in `semantic_diff_weaver/`. The `git_diff/` package collects committed Git data, the `ast_diff/` package extracts structural changes from Python, the `schema_diff/` package compares changed API schemas and the configuration files a repository names, and `service.py` orchestrates the pipeline. `source.py` holds the `SourceRevisionPair` contract between them, so neither `ast_diff/` nor `schema_diff/` imports `git_diff/`. `taxonomy.py` is the single per-`BehaviorCategory` profile consumed by `scoring.py`, `obligations.py`, and `test_mapper.py`. `evidence_scenarios.py` writes a Python finding's obligations from what its evidence changed, the values `ast_diff/` records on each delta, as code spans the renderer keeps as code; the category's scenarios in `taxonomy.py` remain for every other finding. `call_graph.py` follows calls across modules for `test_mapper.py`, which finds the tests that reach a change, and for `change_groups.py`, which groups the findings that calls, and the module variables code reads, connect. `selection.py` follows imports from the test files to name those a change can affect, as the analysis's `test_selection` and the CLI's `select` subcommand. `plugin.py`, root `__init__.py`, and `plugin.yaml` provide Hermes registration, and `plugin.py` also registers `hermes_skill.md`, the skill that tells a Hermes agent when to use the tools, as `skills/` does for Claude Code (keep the two in step); the root `__init__.py` is also the loadable entry point of a `hermes plugins install OWNER/REPO` clone, and `after-install.md` is the post-install guide that install path shows. `skeletons.py` serves the CLI's `skeletons` subcommand and the `generate_test_skeletons` tool, which turn approved obligations into a patch of failing tests and write no file. `mcp_server.py` serves the same two tools to Claude Code over MCP stdio; `.claude-plugin/` holds that plugin's manifest (which declares the server inline) and marketplace, and `skills/` its skill. `evals/` is that skill's `claude plugin eval` suite: each case's `scaffold.sh` builds the repository it reviews with `evals/fixture.sh`, and its graders read the tool calls and the answer. Do not add a root `.mcp.json`: Claude Code also reads it as this repository's own project MCP configuration, where `${CLAUDE_PLUGIN_ROOT}` is undefined. `.semantic-diff-weaver.yaml` scopes the tool's analysis of this repository to production code; add any new production directory to its `include`, and never narrow scope with `exclude`, which also hides candidate tests.

Each package's `__init__.py` exports only its pipeline-facing surface. Import internals (parsers, the process runner, limit constants) from their defining module rather than widening the facade, and do not re-export private names.

Tests are grouped under `tests/unit`, `contract`, `integration`, `security`, `performance`, and `evaluation`; reusable inputs belong in `tests/fixtures`. User-facing design and operational notes belong in `docs/`. `benchmark/cases.json` is the labeled public benchmark of real pull requests that `scripts/run_benchmark.py` measures into `docs/benchmark.md`; never change a label to agree with the analyzer, and correct one found wrong only with its reason in the commit. Its `llm` subcommand measures the inference layer through `scripts/openai_compatible.py`, which stays out of the package: it needs a spending cap, sends only public code, and never runs a held-out case. `scripts/mutate_select.py` measures `select` against mutants of a repository's product code, and since it runs that repository's tests, it stays out of the package too. `scripts/shadow_select.py` measures `select` against the failures of public projects' red pull-request runs, read from their CI logs with a GitHub token; it runs nothing of theirs, and stays out of the package as well. `scripts/prompt_injection.py` measures the inference layer against the repositories of `tests/fixtures/prompt_injection_cases.json`, which tell the model what to say; it sends only those fixtures, needs a spending cap like `run_benchmark.py llm`, and stays out of the package.

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
- `python scripts/check_hermes_release.py`: compare the Hermes release the `hermes-current` job
  tests with the latest release of NousResearch/hermes-agent; it exits 1 when CI is behind.
- `python scripts/check_anthropic_sdk.py`: check that the newest `anthropic` release the extra
  admits sends the API version the Anthropic adapter was checked against, and that its
  `messages.create` takes every keyword the adapter passes; it exits 1 when either moved.
- `claude plugin eval . --scaffold --allow-real-servers --allow-tools Bash
  'mcp__plugin_semantic-diff-weaver_semantic-diff-weaver__*' --no-publish --max-cost-usd <usd>`:
  run the Claude Code skill's eval suite in `evals/` with the real server. Every run is a full Claude
  session billed to whoever runs it, so cap it, and run it when `SKILL.md` changes and before a
  release.
- `python scripts/time_performance.py --runs 6 new=. old=<worktree of the previous tag>`: time
  `tests/performance` in each tree, interleaved, and print each test's median call phase, as
  `docs/evaluation.md` records them for a release.
- `python scripts/check_coverage.py coverage.json --per-module evidence_scenarios.py`: also print
  each critical module's branch coverage, lowest first.
- `python scripts/release_notes.py X.Y.Z --out notes.md`: write a GitHub Release's notes from the
  version's `CHANGELOG.md` section; `python scripts/check_release.py vX.Y.Z <dist>` then checks the
  published Release's title, notes, and attached files against the tag, those notes, and `<dist>`.

Use Python 3.11 or newer and install the exact development versions from `requirements-dev.lock`.

## Coding Style & Naming Conventions

Use four-space indentation, explicit UTF-8 encodings, type annotations, and a 100-character line target. Ruff is authoritative, and its lint set includes flake8-bandit (`S`); the only exemptions are the documented per-file ignores for tests and scripts in `pyproject.toml`. Use `snake_case` for modules and functions, `PascalCase` for classes and Pydantic models, and `UPPER_SNAKE_CASE` for constants. Preserve stable error codes, schemas, taxonomy values, and evidence IDs.

## Testing Guidelines

Pytest files and functions use `test_*.py` and `test_*`. Add unit tests for algorithms, contract tests for interfaces, and integration tests for end-to-end behavior. Security-sensitive changes require adversarial regressions. Maintain at least 85% overall coverage and 90% branch coverage for every critical module named in `scripts/check_coverage.py` — the boundary and transport modules plus the `ast_diff/`, `git_diff/`, and `schema_diff/` packages. Tests must be offline and deterministic; use fake Hermes LLM responses.

## Commit & Pull Request Guidelines

History uses Conventional Commit subjects, for example `feat: implement semantic diff weaver MVP`. Continue with `feat:`, `fix:`, `test:`, or `docs:`. Commit message bodies and pull requests should explain behavior and contract impact and call out security, schema, evaluation, or documentation changes; a pull request also links issues and lists the commands run. Include screenshots only when rendered Markdown changes materially.

`main` is protected by a branch ruleset: **direct pushes are rejected**, and merging requires the `ci-complete` status check to pass. Nobody can bypass it, the repository owner included. So the loop is branch, push, open a PR, let CI go green, merge. CI runs on the pull request, not on a branch pushed without one, and again on `main` after the merge. Review approvals are **not** required (the count is 0) — a solo change stays a one-person operation, gated by CI rather than by a second pair of eyes.

Before pushing, run the local gate, which is what the `test` and `action-pins` jobs run:

```bash
python -m pytest --cov=semantic_diff_weaver --cov-branch --cov-report=json:coverage.json
python scripts/check_coverage.py coverage.json
python -m ruff check . && python -m ruff format --check .
python -m mypy
python scripts/check_action_pins.py
python -m build && python scripts/verify_wheel.py dist
```

CI also covers what the local gate cannot: the matrix of Python versions and operating systems (the macOS leg runs everything but the performance tests, `-m "not performance"`, because its Intel runner's speed varies too much for their wall-clock ceilings, which the Ubuntu and Windows legs keep timing), the `hermes-compatibility` job, which checks the lowest supported Hermes release from PyPI, and the `hermes-current` job. That job builds the current release from its pinned Git tag, since PyPI stops at 0.19.0. It installs the plugin with `hermes plugins install`, scan included, then checks discovery. When a change touches Hermes registration, `python scripts/verify_hermes.py` against an installed wheel reproduces `hermes-compatibility` locally. For `hermes-current`, build Hermes as that job does, with Python 3.14 and `uv sync --frozen` on the tag's checkout, and run `python scripts/verify_hermes_install.py` with that environment's interpreter before installing the wheel. When a new Hermes release comes out, move `HERMES_TAG`, `HERMES_COMMIT`, and `UV_VERSION` together. Nothing else notices one, since PyPI stops at 0.19.0, so the weekly `hermes-release` workflow runs `scripts/check_hermes_release.py --open-issue` and opens an issue for each release CI is behind on, naming the commit the tag points to. No job installs the `anthropic` extra, and a release inside its range reaches users with no Dependabot pull request, so the weekly `anthropic-sdk` workflow runs `scripts/check_anthropic_sdk.py --open-issue`: it opens an issue when the newest such release sends an API version the adapter was not checked against, or its `messages.create` stops taking a keyword the adapter passes. A stalled test leg ends its pytest step, which keeps the step's log, before the job's timeout cancels the job, which can lose it: a test stops at `pyproject.toml`'s 30-second `timeout` or its mark's limit, and at the latest at the 240-second `faulthandler_timeout`, which prints every thread's stack and exits even where a thread is stuck in C code; the session stops at 18 minutes (`--session-timeout`), the step at 25, and the job at 30. Keep each bound inside the next, which `tests/unit/test_ci_workflow.py` checks.

`ci-complete` is a single aggregating job that fails unless `test`, `action-pins`, `hermes-compatibility`, and `hermes-current` all succeeded, every matrix leg included: it reads each leg of its run from the API, because GitHub reports a matrix job as a success when one of its legs is cancelled before any runner takes it. **Any new job must be added to its `needs:` list**, or it silently stops gating anything, and keep its id as its name, since the gate finds each leg by the job id it is named after. The ruleset requires that one stable name rather than the individual matrix legs on purpose: a dropped Python version would otherwise become a required check that never reports again and would block every merge permanently. The `pr-review` workflow is deliberately outside the gate — it is dogfooding, not a check.

GitHub Actions are pinned to **full commit SHAs** with a trailing `# vX.Y.Z` comment, never to tags. Here that is not just convention but an enforced policy: `scripts/check_action_pins.py` runs offline in `ci` on every PR (so a Dependabot bump fails immediately rather than a week later) and again in the weekly `action-pins` cron with `--verify-remote --check-latest`. It also compares the workflows against the YAML quoted in `README.md` and `docs/`, which Dependabot never rewrites — so update the docs alongside any pin change or the check fails.

## Security & Configuration Tips

Treat repositories, Git metadata, YAML, and model output as untrusted. Never execute or import analyzed code, follow paths outside the repository, expose secrets, use shell-interpolated Git commands, or weaken deterministic fallback and evidence validation to satisfy a test.

Security fixtures keep their hostile strings; do not rewrite or obfuscate them to satisfy Hermes's install-time scan. That scan lowers findings in a test tree by one severity for exactly this case (hermes-agent #89610), so the plugin installs with a `caution` verdict that the README explains. `hermes-current` fails if a high or critical finding appears anywhere outside `tests/`, or if the verdict turns `dangerous`.
