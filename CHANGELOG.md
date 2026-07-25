# Changelog

## Unreleased

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
- Rename the project from `hermes-semantic-diff-weaver` to `semantic-diff-weaver`. The distribution,
  the `semantic_diff_weaver` package, and the plugin entry-point name all drop the `hermes-` prefix.
- **Breaking:** rename the `HERMES_SEMANTIC_DIFF_WEAVER_ALLOWED_ROOTS` environment variable to
  `SEMANTIC_DIFF_WEAVER_ALLOWED_ROOTS`. The old name is no longer read; hosts that set it lose the
  extra authorized roots and fall back to the process working directory.
- Harden secret egress, model-data framing, terminal/control rendering, repository authorization,
  Git replacement-object handling, inherited Git environment isolation, configuration complexity,
  and adversarial AST resource limits.
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
- License the project under MIT.
