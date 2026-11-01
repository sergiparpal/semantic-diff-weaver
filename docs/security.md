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
- A baseline analysis is untrusted input data in the same way: bounded by `max_baseline_bytes`,
  read as strict UTF-8, parsed with the standard library, authorized like any other
  caller-selected path, and validated field by field — a path in it must be repository-relative,
  and a category or risk must be a known value. Only those validated fields reach the result and
  the brief, escaped; the baseline's summaries and other prose are never rendered.
- An analysis read for test skeletons is untrusted input data in the same way, under the same
  `max_baseline_bytes`, and the code generated from it cannot be written by it. Its text reaches a
  test file only as comments, each on a line of its own, with every non-printable character —
  line breaks, NUL, and bidirectional controls among them — written as its escape, so it can
  neither end its comment nor reorder what a reviewer sees. Test and class names are ASCII
  identifiers, the only string literal names a pattern-validated obligation ID, and no skeleton
  takes a name its file already binds, which would replace an existing test. Test files are read
  from the analysis's head commit, a commit the analysis names exactly rather than a ref of that
  name; a symbolic link, submodule, excluded file, file that does not parse, or path a patch
  header would need quoted takes no skeleton, and neither does a production module merely named
  like a test. Nothing is written: the result is a patch the user reviews and applies, and a
  skipped obligation's reason has its path escaped too. `tests/security/test_skeleton_inputs.py`
  holds the adversarial regressions.
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

## Authorization under Claude Code

The MCP server in `mcp_server.py` is the Hermes boundary over a different transport, so it keeps the
Hermes rule rather than the CLI's: a model supplies `repo_path`, and no tool argument ever names or
widens an authorized root. The schema's `additionalProperties: false` rejects any attempt to pass one.

The one value taken from the host is the project directory. Claude Code does not document the working
directory it starts a plugin server in, but it exports the project root as `CLAUDE_PROJECT_DIR`; the
server changes into it at start-up, so `path_policy`'s default root is the project rather than the
plugin's installation directory. An inaccessible value stops the server before it reads a message.
An operator-set `SEMANTIC_DIFF_WEAVER_ALLOWED_ROOTS` still takes precedence, so a project outside the
operator's bound fails with `path_outside_repository` exactly as under Hermes. The refusal's
remediation names the two real remedies — a path inside the workspace, or an operator listing the
directory in that variable — with the separator, and warns that setting the variable replaces the
default root, so the project must be listed too. A model that receives it can tell the user exactly
what to do instead of trying another path or reading the source to find out.

Stdout carries the protocol and nothing else: while serving, `sys.stdout` points at stderr. Each
message is bounded to 1 MiB and an oversized line is discarded without being buffered whole. An
unexpected exception becomes a generic JSON-RPC internal error that never echoes its text, and an
analysis failure is returned as a tool result carrying the stable error payload. A successful default
result is the brief as plain Markdown rather than a JSON string, so the renderer's escaping is what
keeps repository content from posing as the server's own structure: names are escaped, expression
evidence stays on prefixed lines inside a fence it cannot close, and the closing comment carries only
the pattern-validated analysis ID. `tests/security/test_mcp_brief.py` plants a forged footer and
heading to pin that. The server runs the deterministic analysis only and never reads
`ANTHROPIC_API_KEY`.

`tests/security/test_mcp_authorization.py` holds the adversarial regressions for each rule above.

## The GitHub Action's boundary

The action wraps the CLI and adds two calls through `gh`: `scripts/pr_comment.py` reading its
previous comment's baseline digest, and posting the brief. Both match only the comment the
action's own identity wrote, so another participant's comment carrying the marker is neither
edited nor read; the digest is inflated within a fixed bound and validated by the analyzer like
any baseline, so it cannot change what the brief claims. Each call is argument-list-only with
`shell=False`, the untrusted comment body goes through a file rather than the command line, and
the repository and pull-request number are constrained to their documented shapes before they
are interpolated into a `gh api` resource path, so neither can retarget the request at another
endpoint. The analyzer itself still makes no network request and reads no pull-request API. See
[github-action.md](github-action.md).
