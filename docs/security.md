# Security and privacy

## Threat model

The repository, refs, filenames, source, tests, README text, configuration, Git metadata, and model
output are untrusted. The target may contain traversal paths, external diff drivers, symlinks,
credentials, prompt injection, invalid encodings, oversized data, or code intended to execute on
import.

## Invariants

- The target repository is never modified.
- Target code is never imported or executed; tests, hooks, interpreters, builders, package managers,
  generators, and non-Git repository tools are never run.
- Base/head refs are resolved to full commits before diff and blob commands.
- Git uses argument arrays, `shell=False`, timeouts, bounded input/output, noninteractive operation,
  disabled lazy object fetching and replacement objects, a scrubbed Git-specific environment,
  literal untrusted pathspecs, `--no-ext-diff`, and `--no-textconv` where relevant. Each child is
  owned for the length of the call so its pipes close deterministically rather than at collection.
- Malformed Git metadata is treated as untrusted input rather than raising past the declared error
  contract: an unparseable `--numstat` count skips that record, and a truncated rename pair ends
  the record stream.
- Caller-selected repository and profile paths must resolve below bounded host-authorized workspace
  roots; the process working directory is the secure default.
- Every Git/config path is normalized and checked; VCS metadata, traversal, absolute/drive/UNC paths,
  reserved device names, and mandatory secret classes are rejected or excluded.
- Repository-local config symlinks must resolve inside the repository, and both sides of a rename are
  checked so renaming a secret-looking path cannot expose its old committed content.
- Binary, oversized, non-UTF-8, credential-named, cache, environment, and control files are omitted
  visibly.
- Sensitive assignments, authorization values, credential-bearing URIs, common tokens, and private
  keys are redacted before evidence, model input, generated prose, output, or errors.
- Decorator arguments are never retained as evidence; only bounded decorator names are recorded.
- AST node count, depth, extracted symbols, similarity candidates, and a cooperative wall-clock
  deadline covering parsing, matching, and comparison have immutable budgets; files exceeding a
  budget fail closed with explicit incomplete scope. A deadline that has been reached is spent, so
  platform clock granularity cannot extend the budget.
- Candidate-test indexing is bounded by per-file size and aggregate file/byte ceilings; reaching an
  aggregate ceiling marks mapping incomplete instead of silently claiming a complete index.
- Model input separates trusted instructions from delimited untrusted evidence. Output may reference
  only deterministic evidence IDs and cannot trigger reads, tools, subprocesses, network access, or
  configuration changes.
- Delimiter characters inside untrusted model data are JSON-escaped, and terminal/bidirectional
  controls are rendered visibly in Markdown.
- An ingested coverage report is untrusted input data: bounded by `max_coverage_bytes`, read
  as strict UTF-8, parsed with the standard library, and authorized like any other
  caller-selected path. Its entries are lookup keys only — no path inside a report is ever
  opened, resolved against the filesystem, or executed. Traversal, absolute, drive-relative,
  and UNC prefixes are stripped from keys rather than followed, and a null byte discards the
  entry. XML coverage formats are excluded so no entity-expansion surface exists.
- The plugin makes no direct network request and emits no plugin telemetry.
- Resource prioritization is deterministic and applies only to explicitly configured critical paths;
  it never expands the configured file, line, symbol, evidence, or model-input ceilings.

Expected errors expose safe normalized counts or paths only. Raw source, prompts, provider responses,
environment values, credentials, and absolute repository paths are not returned.

## Authorization on the command line

`path_policy.authorized_roots` defaults to the process working directory. That containment exists
because of *who chooses the path*: under Hermes an instructed model supplies `repo_path`, and the
default root is the only thing between a redirected model and the rest of the filesystem.

On the command line a person types the path, and that typing is the authorization. The CLI therefore
populates `SEMANTIC_DIFF_WEAVER_ALLOWED_ROOTS` from the resolved `--repo` plus any `--allow-root`
values when the variable is unset, and restores the environment afterwards so the grant lasts only
for that analysis.

The concession stops there. When an operator has already set the variable, they have expressed a
narrower intent than the invocation, and the CLI never widens it:

- `--allow-root` is refused outright with a distinct argument-error exit code, not merged;
- a `--repo` outside the operator's bound fails with `path_outside_repository`, as under Hermes;
- a filesystem root (`/`, `C:\`) is refused as an authorization root by either path — the CLI names
  the offending flag, and `authorized_roots` refuses it independently;
- `--risk-profile` remains subject to `ensure_authorized_path`, so an external profile requires its
  directory to be named with `--allow-root`, and a symlink out of the repository is refused after
  resolution rather than before.

Nothing else about the CLI differs from the plugin: the same analysis, the same read-only Git
boundary, the same redaction, and the same stable error codes. `tests/security/test_cli_authorization.py`
holds the adversarial regressions for each rule above.

## The GitHub Action's boundary

The action wraps the CLI and adds one outward call: `scripts/pr_comment.py` posting the brief
through `gh`. That call is argument-list-only with `shell=False`, the untrusted comment body goes
through a file rather than the command line, and the repository and pull-request number are
constrained to their documented shapes before they are interpolated into a `gh api` resource path,
so neither can retarget the request at another endpoint. The analyzer itself still makes no network
request and reads no pull-request API. See [github-action.md](github-action.md).
