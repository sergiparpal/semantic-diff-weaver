# Decisions and compatibility record

## MVP fixed decisions

- Configuration is optional and conservative defaults provide first-run output.
- Evidence is batched by module under per-call bounds and an eight-call total ceiling.
- Low-confidence material risk is presented as a review question; below-threshold findings become
  visible omissions/limitations.
- Exact names, Git rename metadata, signatures, normalized fingerprints, and conservative similarity
  support symbol matching without forced ambiguous matches.
- Deterministic fallback is enabled by default.
- Stable behavior fingerprints plus low materiality classify refactors; otherwise the strongest
  supported category or `unknown_semantic_change` is used.
- The handler always returns JSON text, including Markdown-only mode.
- The user's active Hermes provider/model/profile is used without routing overrides.
- The plugin registers one standalone tool and no hooks, commands, skills, or overrides.

## Compatibility snapshot (2026-07-18)

- Local test runtime: CPython 3.12.3 on Linux.
- Local Git: 2.43.0.
- Hermes Agent 0.14.0 was installed in an isolated environment and passed pip-entry-point and
  temporary-home directory discovery, registration, and exact-tool smoke tests.
- Hermes Agent 0.13.0 was tested as the only earlier published release and failed the required
  contract because `PluginContext.register_tool` lacks the `override` parameter. PyPI's release index
  begins at 0.13.0, so 0.14.0 is the lowest verified compatible release.
- The current Hermes Agent 0.18.2 release was also installed and passed the same discovery and
  registration checks. It requires Python `>=3.11,<3.14`; this project remains Python `>=3.11` and
  does not force-install or pin the host runtime.
- `PluginContext.register_tool` accepts `name`, `toolset`, `schema`, `handler`, optional checks/env,
  async/description/emoji fields, and `override=False`.
- `PluginLlm.complete_structured` accepts `instructions`, typed `input`, `json_schema`, `schema_name`,
  request shaping (`temperature`, `max_tokens`, `timeout`, `purpose`), and gated routing overrides.
  This plugin supplies no provider, model, agent, or profile override.
- Directory and pip entry-point discovery remain opt-in; project plugins require
  `HERMES_ENABLE_PROJECT_PLUGINS`.

The CI compatibility matrix installs Hermes 0.14.0 and 0.18.2 with the built plugin wheel, then
repeats both real discovery paths for each version. The regular fake-context suite continues to
validate registration without requiring Hermes or a live model.

## Repository-clone plugin install (2026-07-30)

`hermes plugins install sergiparpal/semantic-diff-weaver --enable` is the documented install path,
matching the sibling plugin repositories. The installer clones the repository into the Hermes
plugins directory and reads `plugin.yaml`; it does not build the project or install its
dependencies. Four consequences are deliberate:

- The root `__init__.py` appends its own directory to `sys.path` before importing the package.
  Hermes loads that file through `spec_from_file_location` and does not put the plugin directory on
  `sys.path`, so without the bootstrap `import semantic_diff_weaver` resolves only when the package
  happens to be pip-installed as well — which a clone install never does. Appending rather than
  inserting keeps an installed copy authoritative when both exist.
- A missing Pydantic or PyYAML raises an `ImportError` naming the dependency and the command that
  fixes it. It is the one failure a clone install can plausibly hit that a wheel install cannot,
  and the alternative is a bare `ModuleNotFoundError` from inside the import chain that reads like
  a defect in the plugin. Any other missing module propagates untouched.
- `plugin.yaml` and `after-install.md` stay out of the wheel. They belong to the clone form; the
  wheel is discovered through the `hermes_agent.plugins` entry point and never reads the manifest.
  Shipping them would put both files at the root of `site-packages`, where every plugin's
  `plugin.yaml` would claim the same path.
- The manifest declares `manifest_version: 1` and an explicit empty `provides_hooks`. `--enable`
  makes the plugin live at install time, so "one tool, no hooks" is worth stating in the file the
  installer reads rather than only in prose.

## Output-affecting corrections (2026-07-25)

These change what an existing repository is reported as, so they are recorded here rather than only
in the changelog.

- Conditions are classified by their changed expressions, never by the enclosing symbol name. The
  synthetic `<module>` and `<unparsed>` names were read as containing `<` and `>` comparison
  operators, forcing every module-scope or unparsed condition to `boundary_change` or
  `retry_timeout_change` ahead of the authorization, validation, and retry-guard rules. A
  module-scope authorization guard now reports impact 92 instead of 64, with the obligation
  scenarios and candidate-test terminology that follow from the corrected category.
- PEP 695 type parameters are part of the signature contract for both callables and classes.
  `def f[T]` to `def f[T, U]`, or an added bound such as `[T: int]`, previously fingerprinted
  identically, was labelled `structural_refactor`, and was then dropped under the default
  `emit_low_risk_refactors: false`. Such changes are now `signature_change`.
- A scalar `language:` section is a `configuration_error` with remediation text instead of an
  `AttributeError` surfacing as an opaque `internal_error`.
- The AST analysis deadline bounds matching and comparison as well as parsing, and is reached
  inclusively, so a spent or zero budget stops work on platforms with a coarse monotonic clock.
- The reviewed evaluation goldens required no regeneration: no corpus case contains a module-scope
  condition or PEP 695 syntax. Schema version, taxonomy values, error codes, and evidence IDs are
  unchanged.

## Output-affecting corrections (2026-07-25, statement ordering)

Ordering is now defined as a permutation. This removes findings from existing reports, so it is
recorded here alongside the corrections above.

- **What `statement_order` was comparing, and why it was wrong.** `ast_diff/extract.py` builds
  `statement_order` entries that embed unparsed expression *content*, not only sequence identity:
  `if:<test>`, `raise:<exc>`, `return:<value>`, and `assign:<targets>` all carry the expression
  text, and only `call:<name>` is content-free. `compare.py` then treated any inequality of that
  tuple as an ordering signal, so a pure content edit synthesized a phantom reordering. Adding a
  keyword argument to `return client.fetch(x)` moved nothing, yet reported `ordering_change`.
- **Ordering is now a permutation**, tested by `compare.is_reordering`, mirroring the call-order
  test `_append_call_delta` has always applied: the delta is emitted only when the two tuples are
  unequal *and* sort to the same multiset. The `ORDER_SENSITIVE_KINDS` guard is unchanged, so
  `call_order_change` still suppresses the fallback rather than double-reporting the same move.
  `extract.py` is unchanged — the embedded content is read as a prefix by `match.py` and is useful
  as evidence text.
- **Findings removed, and why none were genuine.** Two corpus cases lost their `ordering_change`.
  The `dependency arguments` case added only a keyword argument, which was a false positive
  outright. The `side effect` case inserted `notify(user)` ahead of a return; an insertion changes
  the multiset rather than permuting it, and the inserted call is already reported as
  `call_change`, which classifies as `side_effect_change` — so the ordering finding was redundant,
  not lost information. A genuine swap (`f(); g()` to `g(); f()`) is still reported, via
  `call_order_change`, and swapped sibling assignments are still reported via `assignment_change`.
- **Before and after, measured on the 17-case corpus.** Material precision rose from 88.24%
  (15 of 17 predicted categories) to 100.00% (15 of 15). Recall is unchanged at 100% (15 of 15),
  as are 100% evidence-anchor correctness, 100% obligation-concept match, and zero fabricated
  evidence references. There are now no surplus predictions in the corpus at all.
- `tests/fixtures/golden/canonical_outputs.json` was regenerated for exactly those two cases and
  reviewed field by field; the other fifteen are byte-identical, and both affected cases retain
  their genuine finding, its risk, its confidence, and their unchanged `risk_score`. The expected
  labels in `evaluation_expected.json` were not touched. Schema version, taxonomy values, error
  codes, and evidence IDs are unchanged.

## Output-affecting corrections (2026-07-26)

Review fixes. `SCHEMA_VERSION` stays `"1.1"`; the only new value any consumer can observe is one
additional omission reason.

- **A module-level type comment no longer makes a file unparseable.** `extract.py` parsed with
  `type_comments=True`, which accepts a strictly *narrower* grammar than the default: a stray
  `# type:` comment outside a function is a `SyntaxError` under the flag and valid Python without
  it. Such a file was reported as `parse_incomplete` and emitted a fabricated
  `unknown_semantic_change` finding at 0.68 baseline confidence — a claim about source that Python
  itself accepts. `parse_module` now falls back to a plain parse, so type comments enrich the
  rendered signature when they parse and never decide parseability. `test_mapper.py` never read
  one and no longer asks for them.
- **Oversized symbols are reported under their own reason.** Omitted LLM batches and symbols too
  large for any single call were summed into one counter published as `llm_batch_limit`, reporting
  a symbol count under a reason named for batches. A symbol whose payload alone exceeds
  `max_model_input_chars_per_call` now carries `model_input_symbol_limit`, with its own warning.
  This is the output difference: a consumer switching on `scope.omitted[].reason` sees a value it
  has not seen before, and a previously inflated `llm_batch_limit` count drops to the batches it
  actually names.
- **A module that still carries a precise delta keeps its changed-symbol count.**
  `_drop_redundant_module_deltas` filtered the changed-symbol key set by *path*, so a file with any
  detailed delta lost its `<module>` key even though only `symbol_added`, `symbol_removed`, and
  `structural_refactor` are dropped — a surviving module-scope condition change was therefore
  missing from `summary.changed_symbols`. The key set is now filtered by what actually survived.
- The reviewed goldens required no regeneration. No corpus case carries a module-level type
  comment, exceeds the model-input symbol bound, or mixes a surviving module-scope delta with a
  detailed one in the same file, so `tests/fixtures/golden/canonical_outputs.json` is
  byte-identical and `docs/evaluation.md` carries its corpus figures forward. Schema version,
  taxonomy values, error codes, and evidence IDs are unchanged.

## Anthropic request shaping is per model family (2026-07-26)

The optional provider adapter shapes each request to what the named model accepts, matching model
families by prefix so dated snapshots and aliases behave identically:

- families that removed `temperature`/`top_p`/`top_k` are sent none of them;
- families whose thinking is always on are sent no `thinking` parameter at all, because an explicit
  `{"type": "disabled"}` returns HTTP 400 at any effort level. Everywhere the parameter is honored
  it is still sent as disabled, so the interpreter's small `max_tokens` budget bounds response text
  rather than being consumed by thinking before the JSON is emitted.

This is recorded because the failure mode it fixes is close to invisible. Sending a parameter the
family rejects fails *every* call; each failure maps to `ProviderUnavailable` and surfaces only as
the generic "One structured LLM batch failed" warning, and the analysis then collapses into
deterministic fallback — a supported, non-erroring mode. Nothing in the output says the request
shape was at fault. A model override (`--model`, `SEMANTIC_DIFF_WEAVER_MODEL`) therefore has to be
shaped as deliberately as the default, `claude-opus-5`.

## Coverage-artifact grounding (2026-07-25)

### Schema version bumped to 1.1

`CoverageStatus` gains two members, `covered_by_existing_tests` and `changed_lines_uncovered`,
and `AnalysisResult` gains an optional `coverage` object. Every existing member, value, error
code, taxonomy value, and evidence ID is unchanged, and `coverage` is `null` when no report is
supplied — so the addition is backward-compatible in the loose sense.

It is still a bump, because a consumer that exhaustively matches on `coverage_status` now sees
values it has never seen, and would do so with no version signal to key on. `SCHEMA_VERSION` is
therefore `"1.1"`, the `Literal["1.0"]` annotations on `AnalysisResult`, `MarkdownEnvelope`, and
`BothEnvelope` moved with it, and `tests/contract/` plus the canonical goldens were updated
intentionally. The golden diff is exactly 17 `schema_version` lines and 17 additive
`"coverage": null` lines; no finding, risk, confidence, or obligation changed.

`ErrorCode.COVERAGE_UNREADABLE` (`coverage_unreadable`) is new and additive. `coverage_report`
is a new optional tool argument and CLI flag.

### Cobertura and JaCoCo XML are excluded on purpose

Only coverage.py's native JSON and lcov `.info` are supported. Both parse with the standard
library, and neither carries an entity-expansion surface.

Supporting Cobertura or JaCoCo would mean parsing untrusted XML, which leaves two options and
no third: add a `defusedxml` dependency, or accept a billion-laughs surface. Adding a
dependency to the base install to read an optional artifact is a poor trade for a tool whose
selling point is safety when pointed at a hostile repository, and accepting the surface is
simply the thing this project exists not to do. `tests/security/test_coverage_inputs.py`
pins the outcome: an entity-expansion payload is refused as unreadable input, not expanded.

This is recorded so the exclusion is not silently revisited. If XML support is ever wanted, the
decision to revisit is the dependency question, not the parser.

### What a coverage claim is allowed to mean

- A changed file absent from the report is `unknown`, never `uncovered`. The opposite choice
  would make a misconfigured CI path prefix indistinguishable from a repository with no tests,
  which is the single most damaging thing this feature could get wrong. Unmatched files are
  counted in the output and named in a warning.
- A grounded verdict requires unanimity across the changed lines a finding rests on, and a
  merged obligation keeps one only when every behavior it stands for agrees.
- `CandidateTest.verified` stays `Literal[False]`. Coverage says a *line* was executed by the
  suite, not that a specific candidate test asserts the changed behavior. The README's
  "does not claim runtime coverage" wording was revised precisely rather than deleted.
- Scoring is nudged, not driven: the grounded state adjusts the existing test-gap axis by +10
  when uncovered and −15 when covered, leaving behavioral impact and critical-path weight
  untouched. See `docs/architecture.md`.

The no-execute invariant is unchanged. The tool ingests a report the user's own CI already
produced, as untrusted input data, and runs nothing to obtain one. Report entries are lookup
keys; none is ever opened or resolved against the filesystem.

## GitHub Action output format (2026-07-25)

### No SARIF, and no check annotations

The action posts one Markdown pull-request comment and nothing else. This is recorded so the
choice is not silently revisited.

SARIF's model is a flat list of results, each with a `ruleId`, a `level`, and a location. This
tool's whole output shape is the opposite: `scoring.py` produces a risk score and a confidence
score *separately and deliberately*, and `taxonomy.py` exists to keep behavior category,
observable impact, and obligation type distinct from both. Flattening that into a single
`level` per result would discard the distinction the analysis is built to express — a
high-risk/low-confidence finding is presented as a review *question*, not as an error, and
SARIF has nowhere to put that difference. Check annotations have the same problem plus a
narrower one: they anchor to a line, and many findings here are about a symbol's contract
rather than a line.

The Markdown brief from `renderer.py` already carries the obligations, review questions,
evidence anchors, and stated limitations, in the form they were designed to be read in.

### The comment is upserted, never appended

Idempotency is keyed on a hidden `<!-- semantic-diff-weaver:v1 -->` marker as the comment's
first line, not on comment authorship or position. A re-run edits. The marker is versioned so a
future format change can migrate rather than collide.

### The action is composite, not Docker

No image to publish or keep patched, faster cold start, and the dependency surface stays
readable in `action.yml`. The action contains no analysis logic; it wraps the Stage 3 CLI.

## Release note

The repository is licensed under MIT. Publishing or pushing remains a separately authorized action.

## Evaluation label review (2026-07-17)

The retry-predicate fixture originally listed `state_transition_change` even though its increment
statement was identical at both revisions. The expected label was removed after the required separate
fixture review so evaluation does not reward an unsupported state-change finding.

## Plan-conformance closure (2026-07-19)

- Callable signatures now include return annotations and type comments; decorator evidence retains
  safe names only.
- Symbol inventory distinguishes methods and async methods, always records a module snapshot, and
  preserves overload-style duplicate qualified names.
- Matching now performs a conservative second pass across changed files, while ambiguous near-ties
  remain separate findings with explicit warnings and reduced confidence.
- Equivalent obligations merge by normalized Given/When/Then semantics, union behavior links and
  candidate tests, preserve the strongest priority/confidence, and retain the global candidate cap.
- LLM batching connects same-module evidence and cross-module changes sharing a changed dependency
  call before applying input and call ceilings.
- Candidate-test discovery now has aggregate file and byte safety caps, and the full deterministic
  performance regression ceiling is five seconds as required by the project performance
  specification.
  Performance fixtures pause coverage tracing while timed application code runs, so the ceiling
  consistently measures production execution rather than instrumentation overhead across systems.
- Committed tree metadata and source blobs are collected in bounded Git plumbing batches instead of
  spawning size/read/mode processes per file; literal pathspecs and disabled lazy fetching preserve
  the repository boundary while meeting the cross-platform performance target.
- All 17 evaluation cases now have reviewed, normalized canonical JSON goldens in addition to their
  machine-readable category/evidence/scenario labels.
