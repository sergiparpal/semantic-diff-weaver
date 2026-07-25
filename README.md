# Semantic Diff Weaver

Semantic Diff Weaver is an advisory, read-only reviewer for a bounded Git diff between two committed
revisions. It statically extracts Python structural changes, infers evidence-backed behavior changes,
ranks risk separately from confidence, and produces concrete test obligations plus unverified
candidate existing tests. Run it from the command line, or as a Hermes Agent plugin.

It never imports, executes, builds, installs, tests, or modifies the analyzed repository. It does not
run your tests or measure coverage itself — it can *ingest* a coverage report your own CI already
produced, and reports what that report says about the changed lines. Repository content is treated as
untrusted data, and the analysis degrades to deterministic structural findings when no model is
available.

## Requirements

- Python 3.11 or later.
- Git available on `PATH`.
- Pydantic 2 and PyYAML 6 (installed with the package).
- Hermes Agent 0.14.0 or later *only* for the plugin path. The package deliberately does not
  force-install Hermes or constrain its version in metadata.

## Standalone CLI

```bash
pipx run --spec . semantic-diff-weaver --repo . --base main --head HEAD
```

Installed into the current environment instead, the same run is:

```bash
python -m pip install . && semantic-diff-weaver --repo . --base main --head HEAD
```

`python -m semantic_diff_weaver` is equivalent to the console script.

| Flag | Meaning |
| --- | --- |
| `--repo PATH` | repository to analyze; defaults to `.` |
| `--base REF` | base revision; **required** |
| `--head REF` | head revision; defaults to `HEAD` |
| `--include GLOB` | repeatable include pattern |
| `--exclude GLOB` | repeatable exclude pattern |
| `--risk-profile PATH` | additional bounded YAML configuration file |
| `--coverage PATH` | coverage.py JSON or lcov `.info` report to ground coverage in |
| `--model ID` | inference model; overrides `SEMANTIC_DIFF_WEAVER_MODEL` |
| `--no-llm` | skip inference; deterministic structural findings only |
| `--format {json,markdown,both}` | defaults to `markdown` |
| `--allow-root PATH` | repeatable additional authorized root |
| `--fail-on {none,low,medium,high,critical}` | defaults to `none` |

`markdown` prints the PR-ready brief, `json` prints the canonical schema-versioned analysis, and
`both` prints the envelope carrying the analysis and the brief together. Exit codes are:

| Code | Meaning |
| --- | --- |
| `0` | success, and overall risk is below `--fail-on` |
| `1` | analysis error; the stable error code, message, and remediation go to stderr |
| `2` | argument error |
| `3` | success, but overall risk reached `--fail-on` |

Exit code `3` still prints the full report — the threshold is a signal, not a reason to withhold the
analysis.

**Without a model provider the CLI runs in deterministic mode.** It reports structural findings,
their taxonomy classification, risk, and obligations, all sourced from the AST comparison alone.
What it does not add is the inference layer: findings are marked `deterministic_fallback` rather
than `llm_supported`, descriptions are drawn from the built-in per-category templates instead of
being written against the specific diff, and no review questions are raised beyond those the
deterministic rules produce. See [the provider section](#model-provider) to enable inference.

### Grounding coverage

Point the tool at a coverage report your CI already produced and it will report which changed
lines the suite actually executed:

```bash
semantic-diff-weaver --repo . --base main --coverage coverage.json
```

coverage.py JSON and lcov `.info` are both accepted, sniffed by content. A changed file absent
from the report is reported as **unknown, never uncovered**, so a path-prefix mismatch shows up
as a warning rather than as a fake coverage gap. See
[configuration](docs/configuration.md#coverage-grounding).

### Model provider

Inference is optional and never required. Install the extra and export a key to enable it:

```bash
python -m pip install '.[anthropic]' && export ANTHROPIC_API_KEY=...
```

The CLI then adds the evidence-backed inference layer to the deterministic findings. The model
defaults to the latest capable Claude model and is overridable with `--model ID` or
`SEMANTIC_DIFF_WEAVER_MODEL`. `--no-llm` skips provider resolution entirely.

When the package or the key is missing, the CLI prints a one-line notice to stderr and
continues in deterministic mode — **missing credentials are never a hard failure**, and neither
is a provider that errors, times out, or returns output the schema rejects. Every one of those
degrades to the same deterministic structural findings, which is the behavior the Hermes path
already had.

The key is read from the environment only. It is never logged, never included in an error
path, and never written to output; `tests/security/test_provider_secrets.py` asserts it cannot
reach a rendered brief or a `WeaverError` payload even when the provider embeds it in an
exception message.

### CLI authorization

Caller-selected local paths are authorized independently of repository containment, and the CLI
resolves that authorization differently from the plugin **on purpose**. Under Hermes a *model*
chooses `repo_path`, so the default authorized root is the process working directory and nothing
else. On a command line a *person* types the path, and that is the authorization.

So when `SEMANTIC_DIFF_WEAVER_ALLOWED_ROOTS` is **unset**, the CLI sets it for the analysis to the
resolved `--repo` plus any `--allow-root` values, then restores the environment. When the variable
is **already set** by an operator, the CLI never widens it: `--allow-root` is refused with exit code
`2`, and a `--repo` outside the operator's bound fails exactly as it does under Hermes. A filesystem
root is never accepted as an authorization root, by either path.

An external `--risk-profile` must resolve below an authorized root, so pass its directory with
`--allow-root` when it lives outside the repository.

## GitHub Action

Post the brief as a single pull-request comment. The action wraps the CLI and contains no
analysis logic of its own.

```yaml
name: pr-review
on: [pull_request]
permissions:
  contents: read
  pull-requests: write
jobs:
  review:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@9c091bb21b7c1c1d1991bb908d89e4e9dddfe3e0 # v7.0.0
        with:
          fetch-depth: 0        # required: the analyzer reads committed objects and never fetches
      - uses: your-org/semantic-diff-weaver@v0
        with:
          fail-on: none
```

Re-runs **edit** the existing comment rather than appending a new one, matched on a hidden
marker. Third-party actions are pinned to a full commit SHA because a moving tag is mutable and
this job holds `pull-requests: write`. Inputs, the fork-pull-request caveat, and how to pass a
coverage report from a prior job are in [docs/github-action.md](docs/github-action.md).

## Install and enable the Hermes plugin

For development as a user directory plugin, copy this repository directory to:

```text
~/.hermes/plugins/semantic-diff-weaver/
```

For a project plugin, copy it to `.hermes/plugins/semantic-diff-weaver/` and explicitly trust
project plugin discovery:

```text
HERMES_ENABLE_PROJECT_PLUGINS=true
```

For package installation:

```text
python -m pip install .
hermes plugins enable semantic-diff-weaver
hermes plugins list
```

Plugins are opt-in. Set `HERMES_PLUGINS_DEBUG=1` and inspect the Hermes plugin logs if discovery or
registration fails. The package exposes the `hermes_agent.plugins` entry point and the directory
contains both `plugin.yaml` and a root `__init__.py`.

## Tool input

Hermes registers exactly one tool, `analyze_semantic_diff`:

```json
{
  "repo_path": "/path/to/local/repository",
  "base_ref": "main",
  "head_ref": "HEAD",
  "include": ["src/**/*.py"],
  "exclude": ["**/generated/**"],
  "output_format": "both"
}
```

`repo_path` and `base_ref` are required. `head_ref` defaults to `HEAD`; `output_format` may be
`json`, `markdown`, or `both`. An optional `risk_profile` may name a bounded YAML file explicitly,
and an optional `coverage_report` may name a coverage.py JSON or lcov `.info` report to ground
coverage in. Unknown arguments are rejected.

Caller-selected local paths are authorized independently of repository containment. By default the
tool may access only paths below the Hermes process working directory. A trusted host operator can
authorize additional bounded roots with the platform-path-separator-delimited
`SEMANTIC_DIFF_WEAVER_ALLOWED_ROOTS` environment variable. For example, on Linux/macOS:

```text
SEMANTIC_DIFF_WEAVER_ALLOWED_ROOTS=/work/project:/work/shared-profiles
```

`repo_path`, an external `risk_profile`, and an external `coverage_report` must all resolve below
one of these roots. Filesystem roots are never accepted as authorization roots.

The handler always returns a JSON-encoded string. JSON mode returns the canonical schema-versioned
analysis. Markdown mode returns a JSON envelope containing the PR-ready brief. Both mode returns the
canonical analysis and matching Markdown together.

## Configuration

All configuration is optional. Precedence is tool arguments, explicit risk profile,
`.hermes/semantic-diff-weaver.yaml`, `.semantic-diff-weaver.yaml`, and built-in conservative
defaults. See [configuration](docs/configuration.md) for the full schema and limits.

Minimal example:

```yaml
version: 1
paths:
  include: ["src/**/*.py"]
  test_roots: ["tests"]
critical_paths:
  - pattern: "src/auth/**"
    weight: 90
rules:
  minimum_report_confidence: 0.45
  deterministic_fallback: true
```

Mandatory secret and control-directory exclusions cannot be disabled. Configuration cannot enable
network access, code execution, or paths outside the repository boundary.

## Development gates

```text
python -m pytest
python -m pytest tests/unit tests/contract
python -m pytest tests/integration
python -m pytest tests/security
python -m pytest tests/evaluation
python -m pytest tests/performance
python -m pytest --cov=semantic_diff_weaver --cov-branch --cov-report=json:coverage.json
python scripts/check_coverage.py coverage.json
python -m ruff check .
python -m ruff format --check .
python -m mypy
python -m build
python scripts/verify_wheel.py dist
python scripts/verify_hermes.py  # with Hermes >=0.14.0 and the wheel installed
```

Tests use temporary Git repositories and fake Hermes contexts/models. They do not change the real
Hermes home and do not require a paid or live LLM.

## Limitations

- Python source and common pytest/unittest layouts only.
- Committed base/head content only; staged and working-tree changes are outside the MVP.
- Static candidate tests are not verified coverage: they are name and import matches, never a
  claim that a test asserts the changed behavior. This holds even with a coverage report
  supplied — an ingested report says a changed *line* was executed by the suite, which is a
  different and weaker claim.
- The tool consumes a coverage report and never produces one; a changed file absent from the
  report is reported as unknown, never as uncovered.
- Dynamic metaprogramming and external contracts may produce review questions or unknown semantic
  changes.
- No network ref lookup, test execution, or test generation. The GitHub Action posts a comment
  through the `gh` CLI using the workflow's own token; the analyzer itself makes no network
  request and reads no pull-request API.
- Multi-language support is out of scope: `ast_diff/` is built on Python's own `ast`.

## License

Licensed under the [MIT License](LICENSE).
