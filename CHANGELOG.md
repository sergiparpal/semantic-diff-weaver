# Changelog

## 0.2.0 - 2026-07-25

The project becomes runnable without Hermes: a standalone CLI, a GitHub Action, an optional
inference provider, and coverage grounding. The Hermes plugin path is unchanged.

- **Fix:** define statement ordering as a permutation instead of content inequality.
  `SymbolSnapshot.statement_order` entries embed unparsed expression content, not just sequence
  identity, so comparing the tuples for inequality reported a phantom reordering on any pure
  content edit — adding a keyword argument to `return client.fetch(x)` moved nothing yet emitted
  `ordering_change`. Ordering now requires a permutation, mirroring the test `_append_call_delta`
  already applied to call names. Material precision on the 17-case corpus rises from 88.24%
  (15/17) to 100.00% (15/15) with recall unchanged at 100%; both removed findings were verified
  non-genuine. See `docs/decisions.md`.
- **Breaking (schema):** `SCHEMA_VERSION` is now `"1.1"`. `CoverageStatus` gains
  `covered_by_existing_tests` and `changed_lines_uncovered`, and `AnalysisResult` gains an
  optional `coverage` object. Every existing value is unchanged and `coverage` is `null` without a
  report, but a consumer that exhaustively matches `coverage_status` now sees new values and needs
  a version signal.
- **Feature:** ground candidate coverage in an ingested coverage report. `--coverage PATH`,
  `coverage_report`, or `coverage.report_path` accepts a coverage.py JSON or lcov `.info` report,
  sniffed by content and bounded by the new `rules.max_coverage_bytes` (default 20 MB). The report
  is untrusted input data and nothing in it is ever opened or executed. A changed file absent from
  the report is reported as **unknown, never uncovered**, and unmatched files are counted and
  warned about. `CandidateTest.verified` stays `false`: coverage says a line ran, not that a test
  asserts the change. Cobertura/JaCoCo XML is excluded deliberately — see `docs/decisions.md`.
- **Fix (Windows):** the CLI writes UTF-8 regardless of the platform locale encoding. The
  brief contains U+00B7, U+2014, and U+2205, and Python selects the *locale* encoding for
  stdout — cp1252 on a default Windows install, where U+2205 has no mapping. A diff that added
  or removed a symbol therefore died mid-report with an unhandled `UnicodeEncodeError`, and
  redirected output was undecodable even when the run survived. The output encoding is the
  CLI's contract, so it now sets it rather than inheriting it.
- **Feature:** add a standalone command line, `semantic-diff-weaver` and
  `python -m semantic_diff_weaver`, with `--repo`, `--base`, `--head`, `--include`, `--exclude`,
  `--risk-profile`, `--coverage`, `--format`, `--allow-root`, `--fail-on`, `--model`, and
  `--no-llm`. Exit codes are `0` success, `1` analysis error, `2` argument error, and `3` success
  above the `--fail-on` risk threshold.
- **Security:** the CLI populates `SEMANTIC_DIFF_WEAVER_ALLOWED_ROOTS` from `--repo` and
  `--allow-root` when it is unset, because on a command line a person types the path and that is
  the authorization. An operator-set value is never widened: `--allow-root` is refused and a
  `--repo` outside the bound fails as before. See `docs/security.md`.
- **Feature:** add a GitHub Action that posts the review brief as one pull-request comment,
  upserted by hidden marker so re-runs edit rather than append, and truncated at a section
  boundary with an explicit notice above GitHub's 65,536-character cap. No SARIF and no check
  annotations, by decision. See `docs/github-action.md`.
- **Feature:** add an optional Anthropic provider (`pip install 'semantic-diff-weaver[anthropic]'`)
  so the CLI keeps the inference layer. A missing package, missing key, provider error, timeout,
  or schema failure all degrade to deterministic findings; credentials are never a hard failure
  and are never logged, echoed in an error, or written to output.
- **Refactor:** name the structured-inference contract as an explicit `LlmClient` protocol.
  `llm: Any` was threaded from the Hermes handler through the service into the interpreter, where
  exactly one method is ever called. Hermes' `PluginLlm` satisfies it unchanged.
- Add a new stable error code, `coverage_unreadable`, and a new optional tool argument,
  `coverage_report`. All existing codes, taxonomy values, and evidence IDs are unchanged.
- Widen the CI interpreter matrix with Python 3.12 and 3.13 on Linux. 3.12 is the local
  development and `hermes-compatibility` interpreter and was previously never exercised by the
  main test job.
- Hold `cli.py` and `coverage_map.py` to the 90% critical-module branch bar; both are at 100%.

## 0.1.1 - 2026-07-25

- Rename the project from `hermes-semantic-diff-weaver` to `semantic-diff-weaver`. The distribution,
  the `semantic_diff_weaver` package, and the plugin entry-point name all drop the `hermes-` prefix.
  Existing installations must uninstall the old distribution and re-enable the plugin under its new
  name.
- **Breaking:** rename the `HERMES_SEMANTIC_DIFF_WEAVER_ALLOWED_ROOTS` environment variable to
  `SEMANTIC_DIFF_WEAVER_ALLOWED_ROOTS`. The old name is no longer read; hosts that set it lose the
  extra authorized roots and fall back to the process working directory.
- **Fix:** classify module-scope and unparsed condition changes by their changed expressions
  instead of their symbol name. The synthetic names `<module>` and `<unparsed>` contain angle
  brackets, which the comparison-operator probe read as `<` and `>`, so every such condition was
  forced to `boundary_change` or `retry_timeout_change` and could never reach the authorization,
  validation, or retry-guard rules. A module-scope authorization guard now scores at impact 92
  rather than 64, with the matching obligation scenarios and candidate-test terminology.
- **Fix:** report PEP 695 type parameters as part of a callable or class signature. Changing
  `def f[T]` to `def f[T, U]`, or adding a bound such as `[T: int]`, previously fingerprinted
  identically, was labelled `structural_refactor`, and was then dropped entirely under the default
  `emit_low_risk_refactors: false`. Such changes are now `signature_change`.
- **Fix:** raise a `configuration_error` for a scalar `language:` section. `language: python`
  previously raised `AttributeError` and surfaced as an opaque `internal_error` with no
  remediation.
- **Fix:** own Git child processes with a context manager so their pipes close deterministically.
  Every Git invocation previously leaked a `ResourceWarning` per stream until refcounting
  collected the `Popen`.
- **Fix:** skip malformed `--numstat` counts and truncated rename records instead of raising bare
  `ValueError`/`IndexError` past the error contract, and tolerate an empty path in
  `exclusion_reason`.
- Bound the AST analysis deadline across matching and comparison, not only parsing, and compare
  against it inclusively. `time.monotonic()` is only as fine as the platform clock — about 15.6ms
  on Windows before Python 3.13 — so a strict comparison let an already-spent budget read as live
  there, and a zero timeout did no bounding at all. Files reached after the deadline are now
  reported as resource-limited rather than analyzed unbounded.
- Resolve candidate-test signals into memoized position sets and score only a candidate's
  structural hits instead of the whole index; dotted imports are matched through a leaf index.
  Output is unchanged (verified against the previous algorithm over randomized inputs) and the
  mapping stage is ~2.5x faster at the documented caps.
- Load configuration YAML through a `SafeLoader` subclass that enforces the node, depth, and alias
  budgets while composing, replacing a separate counting pass over every file.
- Move the taxonomy completeness guard ahead of the registry it protects, so an incomplete
  taxonomy fails with its named error rather than a bare `KeyError`.
- Enable the `S` (flake8-bandit) lint rules, and hold `ast_diff/` to the 90% critical-module
  branch-coverage bar.
- Read per-file hunks with one bounded Git command per path chunk instead of one process per
  changed file, keeping the previous single-path semantics via `--no-renames` and falling back to
  the per-file reader for any path Git does not report. Analysis output is unchanged.
- Remove redundant work from candidate-test mapping, candidate deduplication, and model batch
  packing, and memoize the pure path-glob and redaction helpers.
- Fix the omitted-obligation count when the global cap collapses overflowing high-risk behaviors
  into one grouped review obligation. The synthetic obligation replaces a generated one, so the
  previous count under-reported the omission by one. Canonical scope and the Markdown scope line
  now report the corrected number; all other output is unchanged.
- Make `SemanticCandidate` immutable and give it explicit `path`/`symbol` fields instead of deriving
  them from `evidence[0]`. Merging re-sorts evidence, so a candidate could previously change the
  identity that deduplication and batching had already keyed on.
- Decouple `ast_diff/` from `git_diff/` behind the new `source.py` `SourceRevisionPair` contract, and
  pass AST safety budgets as an explicit `AstBudget` instead of resolving them through a cached
  self-import of the package. `git_diff/` likewise calls its process runner directly.
- Merge the three per-`BehaviorCategory` tables in `scoring`, `obligations`, and `test_mapper` into
  one `taxonomy.py` registry whose completeness is checked at import and pinned by a contract test.
- Narrow `score_risk`, `generate_obligations`, and the candidate classifiers to the configuration
  they actually read, and trim both package `__init__` exports to the pipeline-facing surface,
  removing nine backward-compatible aliases that existed only for tests.
- Bring the documentation current with the above and correct the evaluation record. The reviewed
  17-case corpus measures 88.24% material precision (15 of 17 predicted categories), not the 100%
  the 2026-07-19 note claimed; that figure never held for this corpus, and the two extra
  `ordering_change` findings are present in the reviewed goldens from the corpus's first commit.

## 0.1.0 - 2026-07-18

- Add the read-only `analyze_semantic_diff` Hermes tool.
- Add bounded committed Git collection, Python AST structural comparison, deterministic behavior
  rules, structured Hermes-hosted interpretation, static candidate-test mapping, test obligations,
  risk/confidence scoring, and JSON/Markdown transports.
- Add safe configuration, path containment, secret redaction, deterministic fallback, packaging,
  CI, and automated evaluation support.
- Complete conservative rename matching, parse-incomplete unknown findings, critical-path resource
  prioritization, bounded README/model context, schema retry/reconciliation limits, and explicit scope
  truncation reporting.
- Expand the corpus, enforce overall and critical-module coverage policy, add a full-pipeline
  performance fixture, and add clean-wheel plus real Hermes 0.14.0/0.18.2 discovery gates.
- Complete return-annotation signature detection, method/module inventory, overload preservation,
  cross-file symbol moves, semantic obligation merging, shared-call LLM batching, and aggregate
  candidate-test index bounds; expand the reviewed corpus to 17 cases.
- Harden secret egress, model-data framing, terminal/control rendering, repository authorization,
  Git replacement-object handling, inherited Git environment isolation, configuration complexity,
  and adversarial AST resource limits.
- License the project under MIT.
