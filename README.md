# Semantic Diff Weaver

Semantic Diff Weaver is an advisory, read-only reviewer for a bounded Git diff between two committed
revisions. It statically extracts structural changes from Python source, from OpenAPI, Swagger, and
JSON Schema documents, and from the configuration files a repository names, infers evidence-backed
behavior changes, ranks risk separately from confidence, and produces concrete test obligations
plus unverified candidate existing tests. Run it from the command line, as a GitHub Action on pull
requests, as a Hermes Agent plugin, or as a Claude Code plugin.

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
- [`uv`](https://docs.astral.sh/uv/) on `PATH` *only* for the Claude Code plugin, which uses it
  to build the server and its dependencies from the plugin checkout.

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
| `--coverage-data PATH` | the same run's `.coverage` data file, recorded with per-test contexts, to name the tests that ran each changed line |
| `--baseline PATH` | an earlier analysis's JSON, to mark findings new, updated, unchanged, or resolved |
| `--model ID` | inference model; overrides `SEMANTIC_DIFF_WEAVER_MODEL` |
| `--no-llm` | skip inference; deterministic structural findings only |
| `--format {json,markdown,both}` | defaults to `markdown` |
| `--allow-root PATH` | repeatable additional authorized root |
| `--fail-on {none,low,medium,high,critical}` | defaults to `none` |

`markdown` prints the PR-ready brief, `json` prints the canonical schema-versioned analysis, and
`both` prints the envelope carrying the analysis and the brief together. Both the brief and the
analysis open with an advisory verdict, `pass`, `warn`, or `review_required`, read from the
overall risk under [configurable thresholds](docs/configuration.md); an analysis that left part of
its scope unread is never `pass`. Exit codes are:

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

Record the run with per-test contexts, `pytest --cov --cov-context=test`, and pass the `.coverage`
data file it leaves beside the report, and each test that ran a changed line is a candidate test for
the change, whatever its name or imports say:

```bash
semantic-diff-weaver --repo . --base main --coverage coverage.json --coverage-data .coverage
```

### Test skeletons

Once you have read a brief and decided which obligations to test, the `skeletons` subcommand
scaffolds them. It reads the analysis you saved and prints a patch adding one **failing** test per
approved obligation; you review the patch and apply it yourself:

```bash
semantic-diff-weaver --repo . --base main --format json > analysis.json
semantic-diff-weaver skeletons --analysis analysis.json \
    --obligation to-001 --obligation to-004 > skeletons.patch
git apply skeletons.patch
```

| Flag | Meaning |
| --- | --- |
| `--analysis PATH` | the analysis, as `--format json` or `both` wrote it; **required** |
| `--obligation ID` | repeatable ID of an approved obligation, such as `to-001`; **required** |
| `--framework {pytest,unittest}` | pytest functions or `unittest.TestCase` methods; `pytest` by default |
| `--format {patch,json}` | the patch alone, or the JSON result carrying it; defaults to `patch` |
| `--repo PATH` | the repository the analysis is of; defaults to `.` |
| `--allow-root PATH` | repeatable additional authorized root, as for an analysis |

Each skeleton lands at the end of the test file named for the changed module, `test_<module>.py`,
preferring one among the obligation's candidate tests; when there is none, that file is created
beside the obligation's first candidate test, or in the first test root if it has none. It carries
the obligation's Given/When/Then, the behavior it tests, and its unverified candidate tests as
comments, and it fails until it is written — `raise NotImplementedError(...)` under pytest,
`self.fail(...)` under unittest — so an approved obligation cannot pass unwritten. No existing name
is reused, so no existing test is replaced.

The command never writes a file and never runs anything: the patch is built from the analysis's
head commit, so it applies to a checkout of that commit. The analysis is read as untrusted data
— its text reaches the test file only as escaped comments. An obligation that cannot be placed,
for instance because its test file is a symbolic link, is reported on stderr with the reason;
exit code `1` means none could be. See
[the decision record](docs/decisions.md#test-skeletons-2026-11-01).

### Model provider

Inference is optional and never required. Install the extra and export a key to enable it:

```bash
python -m pip install '.[anthropic]' && export ANTHROPIC_API_KEY=...
```

The CLI then adds the evidence-backed inference layer to the deterministic findings. The model
defaults to `claude-opus-5` and is overridable with `--model ID` or `SEMANTIC_DIFF_WEAVER_MODEL`.
`--no-llm` skips provider resolution entirely.

The adapter never sends sampling parameters, which API version 2026-10-21, the default from SDK
2.0, rejects on every model. It shapes `thinking` to what the named model family accepts: the
parameter is omitted for families whose thinking is always on, sent as `between_tools` to
`claude-sonnet-5-5`, and sent as disabled everywhere else. A parameter a family rejects fails
*every* call, and the run then degrades to deterministic findings with only a generic per-batch
warning to show for it. So check an override against the prefix lists in
`semantic_diff_weaver/providers/anthropic_client.py` before setting it in CI; the reasoning is in
[docs/decisions.md](docs/decisions.md).

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
      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1
        with:
          fetch-depth: 0        # required: the analyzer reads committed objects and never fetches
      - uses: sergiparpal/semantic-diff-weaver@v0.13.0
        with:
          fail-on: none
```

Re-runs **edit** the existing comment rather than appending a new one, matched on a hidden
marker. Third-party actions are pinned to a full commit SHA because a moving tag is mutable and
this job holds `pull-requests: write`; `scripts/check_action_pins.py` runs in CI and fails if a
pin loses its SHA or if the examples above stop naming the same commit the workflows use.
Inputs, the fork-pull-request caveat, and how to pass a coverage report from a prior job are in
[docs/github-action.md](docs/github-action.md).

## Install and enable the Hermes plugin

```bash
hermes plugins install sergiparpal/semantic-diff-weaver --enable
```

That is the whole setup. Hermes clones this repository into its plugins directory, reads
`plugin.yaml`, and registers the single `analyze_semantic_diff` tool — no hooks, commands, or
overrides. Plugins are opt-in, so `--enable` is what saves you a separate
`hermes plugins enable semantic-diff-weaver`; `hermes plugins list` should then show the plugin
enabled, with one tool and no error. [`after-install.md`](after-install.md) walks through the
first run.

Installing clones the repository, it does not build it, so the two runtime dependencies —
Pydantic 2 and PyYAML 6 — have to be importable in the environment that runs Hermes, and Git has
to be on `PATH`. A missing dependency is reported by name with the command that fixes it, rather
than as a bare import failure.

### Other install paths

To try a working copy, point the installer at the checkout with a `file://` URL. It is cloned like
any other Git remote, so commit first — the working tree itself is not copied:

```bash
hermes plugins install file:///absolute/path/to/semantic-diff-weaver --enable
```

Copying (or symlinking) the repository directory to `~/.hermes/plugins/semantic-diff-weaver/` is
the same directory plugin by hand. For a project plugin, copy it to
`.hermes/plugins/semantic-diff-weaver/` and explicitly trust project plugin discovery:

```text
HERMES_ENABLE_PROJECT_PLUGINS=true
```

Installing the package instead registers the plugin through its `hermes_agent.plugins` entry point
and brings the dependencies with it:

```bash
python -m pip install .
hermes plugins enable semantic-diff-weaver
```

Set `HERMES_PLUGINS_DEBUG=1` and inspect the Hermes plugin logs if discovery or registration fails.
The package exposes the entry point and the repository root contains `plugin.yaml`, the loadable
`__init__.py`, and `after-install.md`.

## Install the Claude Code plugin

```text
/plugin marketplace add /absolute/path/to/semantic-diff-weaver
/plugin install semantic-diff-weaver@semantic-diff-weaver
```

The repository is its own single-plugin marketplace, added here from a local checkout; a hosted
copy is added the same way by `owner/repo` or Git URL. The plugin contributes two things:

- an MCP server, `semantic-diff-weaver`, exposing the same two tools and input schemas as the
  Hermes plugin, `analyze_semantic_diff` and `generate_test_skeletons`, annotated read-only,
  except that `output_format` defaults to the Markdown brief. Claude Code names them
  `mcp__plugin_semantic-diff-weaver_semantic-diff-weaver__analyze_semantic_diff` and
  `mcp__plugin_semantic-diff-weaver_semantic-diff-weaver__generate_test_skeletons`, the names
  to use in permission rules;
- a `semantic-diff` skill telling Claude when to call the tools, how to pick the refs, how to
  report obligations without overstating the unverified candidate tests, and to scaffold only
  the obligations you approve.

Then ask for it in a session — *"what does this branch change in behavior compared with `main`,
and what should I test?"* — or invoke `/semantic-diff` directly. Approve the obligations you want
tested — *"scaffold to-001 and to-004"* — and Claude passes the same arguments to
`generate_test_skeletons`: the server's analysis is always deterministic, so the brief's IDs name
the same obligations there. It shows you the patch, which it applies only if you ask.

The server is started with `uvx --from ${CLAUDE_PLUGIN_ROOT} semantic-diff-weaver-mcp`, so `uv`
must be on `PATH`; the first start builds the package and fetches Pydantic and PyYAML, later starts
reuse uv's cache. It speaks MCP over stdio with no dependency beyond the base install.

Two behaviors differ from the CLI on purpose:

- **Authorization follows Hermes, not the CLI.** The model chooses `repo_path`, so the server
  authorizes the Claude Code project directory (`CLAUDE_PROJECT_DIR`) and nothing else, and makes
  it the working directory so `repo_path: "."` means the project. A trusted operator widens or
  narrows that with `SEMANTIC_DIFF_WEAVER_ALLOWED_ROOTS`, which always takes precedence. Additional
  directories granted with `/add-dir` are *not* authorized automatically.
- **No second model.** The server always runs the deterministic analysis — Claude is already the
  interpreter — and never reads `ANTHROPIC_API_KEY`, so a key present for Claude Code itself cannot
  start a separately billed inference pass.

**Results are sized for Claude Code.** Claude Code keeps a tool result in the conversation up to
50,000 characters by default and saves anything longer to a file. The server therefore defaults
`output_format` to the Markdown brief — as the CLI does, where Hermes defaults to `both` — returns
it as plain Markdown rather than a JSON string, and declares a 100,000-character threshold for the
tool through `anthropic/maxResultSizeChars`. On this repository's own 19-file range the brief was
57,238 characters and `both` 158,368. A brief that would still not fit is rendered again with its
evidence excerpts shortened — never its findings, obligations, or IDs — because Claude Code saves
an oversized result as a JSON file the model cannot read in parts. Request `json` or `both`
explicitly, with `include` narrowed to a few files. Narrow with `include`, not `exclude`: `exclude` also removes files from
candidate-test discovery, so excluding a tests directory drops every candidate test.

To use the server without the plugin, install the package and register its console script:

```bash
python -m pip install . && claude mcp add semantic-diff-weaver -- semantic-diff-weaver-mcp
```

`claude plugin validate .` checks the manifests from a checkout, and
`claude --plugin-dir /path/to/semantic-diff-weaver` loads a working copy for one session without
installing it.

## Tool input

Hermes registers two tools. The first, `analyze_semantic_diff`, analyzes a diff:

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
an optional `coverage_report` may name a coverage.py JSON or lcov `.info` report to ground coverage
in, an optional `coverage_data` may name the same run's `.coverage` data file, recorded with
per-test contexts, to name the tests that ran each changed line, and an optional `baseline_report`
may name the JSON of an earlier analysis to compare with ([baseline
comparison](docs/configuration.md#baseline-comparison)). Unknown arguments are rejected.

Caller-selected local paths are authorized independently of repository containment. By default the
tool may access only paths below the Hermes process working directory. A trusted host operator can
authorize additional bounded roots with the platform-path-separator-delimited
`SEMANTIC_DIFF_WEAVER_ALLOWED_ROOTS` environment variable. For example, on Linux/macOS:

```text
SEMANTIC_DIFF_WEAVER_ALLOWED_ROOTS=/work/project:/work/shared-profiles
```

`repo_path`, an external `risk_profile`, an external `coverage_report` or `coverage_data`, an
external `baseline_report`, and a skeleton tool's `analysis_path` must all resolve below one of
these roots. Filesystem roots are never accepted as authorization roots.

The handler always returns a JSON-encoded string. JSON mode returns the canonical schema-versioned
analysis. Markdown mode returns a JSON envelope containing the PR-ready brief. Both mode returns the
canonical analysis and matching Markdown together.

The second, `generate_test_skeletons`, is the [`skeletons`](#test-skeletons) subcommand as a
tool. Given the IDs of the obligations the user approved, it returns a patch adding a failing
test for each, and never applies it:

```json
{
  "repo_path": "/path/to/local/repository",
  "base_ref": "main",
  "head_ref": "HEAD",
  "include": ["src/**/*.py"],
  "obligation_ids": ["to-001", "to-004"],
  "framework": "pytest"
}
```

`repo_path` and `obligation_ids` are required, and `framework` is `pytest` or `unittest`. The
obligations come from exactly one analysis: either the one `base_ref` with the analysis tool's
`head_ref`, `include`, `exclude`, `risk_profile`, `coverage_report`, and `coverage_data` describes,
analyzed again without inference, or an `analysis_path` the JSON of a saved analysis is at. A
deterministic analysis numbers its obligations alike on every run, so the same arguments name the
same obligations; one made with inference can number them otherwise, which is what `analysis_path`
is for. The result is the JSON of `skeletons --format json`: the patch, where each test went, and
why any obligation got none.

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

An `include` like this one also leaves out the repository's API schemas; name them too, as
`"api/openapi.yaml"`, to keep them. Configuration files are compared only where `paths.config_files`
names them, as `config_files: ["config/**"]`, and a file it names needs no `include` of its own.

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

- Python source, OpenAPI 3, Swagger 2, and JSON Schema documents, the YAML, JSON, and TOML
  configuration files a repository names, and common pytest/unittest layouts only.
- Committed base/head content only; staged and working-tree changes are outside the MVP.
- Candidate tests are not verified coverage: they are name, import, call, and history matches, or,
  with per-test contexts, the tests a run executed the changed lines in, never a claim that a test
  asserts the changed behavior. This holds even with a coverage report supplied — an ingested report
  says a changed *line* was executed by the suite, or by a test, which is a different and weaker
  claim.
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
