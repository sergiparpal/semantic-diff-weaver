# Semantic Diff Weaver

Semantic Diff Weaver is an advisory, read-only Hermes Agent plugin for reviewing a bounded
Git diff between two committed revisions. It statically extracts Python structural changes, infers
evidence-backed behavior changes, ranks risk separately from confidence, and produces concrete test
obligations plus unverified candidate existing tests.

The plugin never imports, executes, builds, installs, tests, or modifies the analyzed repository. It
does not claim runtime coverage. Repository content is treated as untrusted data, and the analysis
degrades to deterministic structural findings when the Hermes-hosted model is unavailable.

## Requirements

- Python 3.11 or later.
- Git available on `PATH`.
- Pydantic 2 and PyYAML 6 (installed with the package).
- Hermes Agent 0.14.0 or later for plugin discovery and optional structured LLM inference. The
  package deliberately does not force-install Hermes or constrain its version in metadata.

## Install and enable

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
`json`, `markdown`, or `both`. An optional `risk_profile` may name a bounded YAML file explicitly.
Unknown arguments are rejected.

Caller-selected local paths are authorized independently of repository containment. By default the
tool may access only paths below the Hermes process working directory. A trusted host operator can
authorize additional bounded roots with the platform-path-separator-delimited
`HERMES_SEMANTIC_DIFF_WEAVER_ALLOWED_ROOTS` environment variable. For example, on Linux/macOS:

```text
HERMES_SEMANTIC_DIFF_WEAVER_ALLOWED_ROOTS=/work/project:/work/shared-profiles
```

Both `repo_path` and an external `risk_profile` must resolve below one of these roots. Filesystem
roots are never accepted as authorization roots.

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
- Static candidates are not verified coverage.
- Dynamic metaprogramming and external contracts may produce review questions or unknown semantic
  changes.
- No network ref lookup, pull-request API integration, test execution, or test generation.

## License

Licensed under the [MIT License](LICENSE).
