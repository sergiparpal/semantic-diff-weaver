# Semantic Diff Weaver

> **Product and Technical Specification**  
> English name for **“Tejedor por Diff Semántico”**  
> Proposed repository: `semantic-diff-weaver`  
> Document status: Draft v0.1  
> Date: 2026-07-11

> **This is the original design specification, kept as written.** It is a historical record, not
> the current interface. Where it describes the initial release as a Hermes Agent plugin, the
> shipped tool also runs as a standalone CLI and a GitHub Action, and it can ground coverage in an
> ingested report. For current behavior read [`../README.md`](../README.md),
> [`architecture.md`](architecture.md), and [`configuration.md`](configuration.md); for what
> changed and why, [`decisions.md`](decisions.md) and [`../CHANGELOG.md`](../CHANGELOG.md).

## 1. Executive Summary

**Semantic Diff Weaver** analyzes a pull request at the behavioral level and produces a traceable, risk-ranked set of test obligations.

Traditional diff tools answer **what lines changed**. Semantic Diff Weaver answers:

- What observable behavior may have changed?
- Which assumptions, boundaries, error paths, and state transitions are affected?
- Which existing tests appear relevant?
- Which test scenarios are missing?
- How confident is the analysis, and what evidence supports it?

The initial release is an **advisory Hermes Agent plugin**. It does not modify source code, generate committed tests, execute a test suite, or block a merge. Its primary output is a structured JSON report plus a concise Markdown test plan suitable for a pull request review.

---

## 2. Product Thesis

A source diff is not equivalent to a behavioral diff.

A one-line change can alter retry policy, authorization scope, rounding behavior, state transitions, error handling, or boundary conditions. Conversely, a large refactor may preserve externally visible behavior.

The product creates value by translating implementation changes into **test obligations** that are:

1. tied to specific evidence in the diff;
2. prioritized by risk;
3. expressed as observable scenarios;
4. mapped to existing tests when possible;
5. explicit about uncertainty.

### Product promise

> **Turn every pull request into a focused, evidence-backed test plan based on what the change actually means.**

---

## 3. Target Users

### Primary users

- Software engineers reviewing pull requests.
- QA automation engineers deciding what to test.
- Test leads reviewing change risk.
- Maintainers of libraries or services with large regression suites.

### Secondary users

- Platform teams building CI quality gates.
- Engineering managers seeking a consistent PR risk summary.
- AI coding agents that need a test-design step before generating tests.

### Best initial fit

The MVP is best suited to repositories that have:

- a recognizable source/test directory structure;
- pull-request-based development;
- unit or integration tests stored in the same repository;
- changes small enough to review as a coherent diff;
- code written in Python for the first language-specific adapter.

---

## 4. Core Use Cases

### UC-1: Generate a focused test plan for a pull request

A developer provides a repository and base/head references. The plugin identifies changed behavior and returns prioritized test scenarios.

### UC-2: Explain why a change is risky

A reviewer asks why a seemingly small change needs additional testing. The plugin links risk statements to changed symbols, conditions, exceptions, or state transitions.

### UC-3: Find apparent test gaps

The plugin compares the inferred test obligations with existing tests using names, paths, imports, symbols, docstrings, and optional lightweight semantic matching.

### UC-4: Feed a downstream test generator

A separate plugin or agent consumes the structured obligations and generates candidate test code. This is an extension point, not part of the MVP.

---

## 5. Goals and Non-Goals

### Goals

The MVP shall:

- analyze a Git diff between two revisions;
- identify changed functions, methods, classes, conditions, exceptions, and signatures;
- infer likely behavioral changes;
- classify each behavioral change using a stable taxonomy;
- generate concrete test obligations;
- search the repository for potentially relevant existing tests;
- rank obligations by risk and confidence;
- produce machine-readable JSON and human-readable Markdown;
- explain every material conclusion with source evidence;
- degrade gracefully when semantic inference is uncertain.

### Non-goals for the MVP

The MVP shall not:

- edit or commit code;
- generate complete executable test files;
- execute tests;
- calculate dynamic coverage;
- perform mutation testing;
- inspect production telemetry;
- fetch pull requests from GitHub, GitLab, or Bitbucket directly;
- maintain cross-repository history;
- guarantee that a behavior changed;
- act as a mandatory merge gate;
- support every programming language.

---

## 6. MVP Scope

### Supported input

- Local Git repository.
- Base and head revision, branch, or commit.
- Unified diff generated locally.
- Python source files.
- Common Python test conventions:
  - `test_*.py` and `*_test.py`;
  - `tests/` directory;
  - pytest- and unittest-style test functions/classes.

### Supported semantic signals

- Function or method added, removed, renamed, or signature-changed.
- Conditional logic added, removed, or inverted.
- Comparison and boundary operator changes.
- Default value changes.
- Exception type or error-path changes.
- Return-value shape or type changes visible in source.
- State assignment and transition changes.
- Authorization or validation call changes detectable through names and call structure.
- Retry, timeout, loop bound, or limit changes.
- Side-effect call additions or removals.

### Output modes

- `json`: canonical structured result.
- `markdown`: PR-friendly test brief.
- `both`: default.

---

## 7. Semantic Change Taxonomy

Every inferred behavior change must use one or more stable categories.

| Category | Description | Typical test obligation |
|---|---|---|
| `boundary_change` | Comparison, threshold, range, or limit changed | Test values below, at, and above the boundary |
| `validation_change` | Accepted or rejected input conditions changed | Test newly valid and newly invalid inputs |
| `error_handling_change` | Exception, fallback, or error response changed | Test trigger, propagated error, and recovery behavior |
| `state_transition_change` | Allowed state movement or assignment changed | Test valid, invalid, and repeated transitions |
| `authorization_change` | Permission, ownership, role, or identity check changed | Test allowed and denied principals |
| `retry_timeout_change` | Retry count, delay, timeout, or stop condition changed | Test recoverable, terminal, and limit conditions |
| `output_contract_change` | Return shape, field, type, status, or value changed | Test consumer-visible output contract |
| `side_effect_change` | External call, persistence, event, or notification changed | Test occurrence, absence, ordering, and idempotency |
| `ordering_change` | Execution or precedence changed | Test competing conditions and sequence-sensitive behavior |
| `default_behavior_change` | Default argument or implicit path changed | Test omitted input and explicit old/new values |
| `dependency_interaction_change` | Calls to another component changed | Test dependency success, failure, and unexpected response |
| `refactor_likely_no_behavior_change` | Structural change with no strong behavioral signal | Recommend characterization or targeted regression only |
| `unknown_semantic_change` | Change is material but cannot be classified confidently | Request human review and identify missing context |

---

## 8. Analysis Pipeline

```text
Repository + base/head refs
        │
        ▼
1. Diff Collector
   - validate repository boundary
   - obtain changed files and hunks
   - exclude generated/vendor files
        │
        ▼
2. Structural Analyzer
   - parse old and new Python ASTs
   - identify changed symbols
   - extract signatures, branches, calls, raises, returns, assignments
        │
        ▼
3. Semantic Candidate Builder
   - convert structural deltas into compact evidence records
   - detect deterministic patterns such as < → <=
        │
        ▼
4. Semantic Interpreter
   - infer observable behavior changes
   - return schema-constrained results through ctx.llm
        │
        ▼
5. Test Mapper
   - find existing tests by path, symbol, import, name, and text evidence
        │
        ▼
6. Obligation Generator
   - produce positive, negative, boundary, error, and regression scenarios
        │
        ▼
7. Risk and Confidence Scorer
   - rank findings
   - penalize unsupported inference
        │
        ▼
8. Renderer
   - canonical JSON
   - concise Markdown report
```

### Design principle

Use deterministic analysis to collect and compress evidence. Use the LLM only for semantic interpretation and scenario phrasing. Validate and score the result deterministically afterward.

The full raw repository or an unbounded diff must never be sent to the model.

---

## 9. Functional Requirements

### FR-1: Diff collection

The plugin shall accept:

- `repo_path`;
- `base_ref`;
- `head_ref`;
- optional include/exclude globs.

It shall reject paths outside the resolved repository root.

### FR-2: Structural delta extraction

For every changed Python symbol, the plugin shall capture where available:

- qualified symbol name;
- file path;
- old and new line ranges;
- signature changes;
- control-flow changes;
- call additions/removals;
- changed literals and operators;
- added/removed exceptions;
- changed returns and assignments.

### FR-3: Behavioral inference

The plugin shall infer zero or more `behavior_changes` from structural evidence.

Every behavior change shall include:

- category;
- summary;
- observable impact;
- evidence references;
- confidence;
- assumptions.

### FR-4: Test obligation generation

Every material behavior change shall generate one or more test obligations.

An obligation shall describe:

- the condition or setup;
- the action;
- the expected observable result;
- the obligation type;
- priority;
- linked behavior-change IDs.

### FR-5: Existing test mapping

The MVP shall search for candidate tests using:

1. mirrored source/test paths;
2. changed symbol names;
3. imported modules;
4. test names and docstrings;
5. configurable mappings.

The plugin must call these **candidate tests**, not verified coverage relationships.

### FR-6: Explainability

No high-priority finding may be emitted without at least one evidence reference.

Evidence shall point to:

- a file;
- a symbol or diff hunk;
- an old/new expression or summarized structural delta.

### FR-7: Uncertainty handling

The plugin shall lower confidence when:

- context is truncated;
- symbols cannot be parsed;
- behavior depends on runtime metaprogramming;
- an external contract is unavailable;
- the change is mainly configuration or generated code;
- the model inference is not supported by deterministic evidence.

### FR-8: Stable output

The JSON result must be versioned with `schema_version` and remain the canonical interface for downstream plugins.

---

## 10. Hermes Tool Interface

### Primary tool

`analyze_semantic_diff`

### Proposed tool description

> Analyze the behavioral meaning of a Git diff and produce risk-ranked test obligations with evidence and candidate existing tests. Use when reviewing a code change or planning regression tests. This tool is advisory and does not execute or modify code.

### Input schema

```json
{
  "type": "object",
  "properties": {
    "repo_path": {
      "type": "string",
      "description": "Path to the local Git repository."
    },
    "base_ref": {
      "type": "string",
      "description": "Base branch, tag, or commit."
    },
    "head_ref": {
      "type": "string",
      "description": "Head branch, tag, or commit. Defaults to HEAD."
    },
    "risk_profile": {
      "type": "string",
      "description": "Optional path to a YAML risk profile."
    },
    "include": {
      "type": "array",
      "items": {"type": "string"}
    },
    "exclude": {
      "type": "array",
      "items": {"type": "string"}
    },
    "output_format": {
      "type": "string",
      "enum": ["json", "markdown", "both"],
      "default": "both"
    }
  },
  "required": ["repo_path", "base_ref"]
}
```

### Optional future tools

- `render_semantic_diff_report(analysis_path, format)`
- `compare_test_plan_with_changes(previous_analysis, current_analysis)`
- `generate_test_skeletons(analysis_path, framework)`

The future tools are explicitly outside the MVP.

---

## 11. Canonical Output Model

```json
{
  "schema_version": "1.0",
  "analysis_id": "sdw_01J...",
  "repository": {
    "path": ".",
    "base_ref": "main",
    "head_ref": "feature/retry-policy",
    "base_commit": "abc123",
    "head_commit": "def456"
  },
  "summary": {
    "changed_files": 3,
    "changed_symbols": 4,
    "behavior_changes": 2,
    "test_obligations": 6,
    "overall_risk": "high",
    "overall_confidence": 0.86
  },
  "behavior_changes": [
    {
      "id": "bc-001",
      "category": "retry_timeout_change",
      "summary": "Authorization failures now stop retries immediately.",
      "observable_impact": "Requests receiving an authorization error should perform one attempt instead of continuing the retry loop.",
      "risk": "high",
      "confidence": 0.93,
      "evidence": [
        {
          "file": "src/payments/retry.py",
          "symbol": "RetryPolicy.should_retry",
          "old": "return attempts < max_attempts",
          "new": "return error.retryable and attempts < max_attempts",
          "lines": "42-44"
        }
      ],
      "assumptions": [
        "error.retryable is false for authorization failures"
      ]
    }
  ],
  "test_obligations": [
    {
      "id": "to-001",
      "behavior_change_ids": ["bc-001"],
      "type": "negative",
      "priority": 96,
      "title": "Do not retry authorization failures",
      "given": "A payment attempt returns a terminal authorization error",
      "when": "The retry policy evaluates the failure",
      "then": "No additional attempt is scheduled",
      "candidate_existing_tests": [
        {
          "path": "tests/payments/test_retry.py",
          "symbol": "test_terminal_error_is_not_retried",
          "match_score": 0.78,
          "match_reasons": ["same module", "retry terminology", "terminal error terminology"]
        }
      ],
      "coverage_status": "candidate_exists_unverified"
    }
  ],
  "warnings": [],
  "limitations": [
    "Candidate test mapping is static and does not prove runtime coverage."
  ]
}
```

---

## 12. Markdown Report Format

```markdown
## Semantic Diff Test Brief

**Risk:** High  
**Confidence:** 86%  
**Scope:** 3 files, 4 changed symbols, 2 inferred behavior changes

### What appears to have changed

1. **Authorization failures now stop retries immediately** — High risk, 93% confidence
   - Evidence: `src/payments/retry.py::RetryPolicy.should_retry`
   - Change: retry eligibility now depends on `error.retryable`.

### Required test obligations

- [ ] Terminal authorization failure performs no retry.
- [ ] Retryable network failure still retries below the attempt limit.
- [ ] Retryable failure stops exactly at the configured limit.

### Candidate existing tests

- `tests/payments/test_retry.py::test_terminal_error_is_not_retried`
  - Candidate match only; runtime coverage has not been verified.

### Review notes

- Confirm that authorization errors always set `retryable = false`.
```

---

## 13. Risk and Confidence Model

Risk and confidence must be separate.

- **Risk** estimates the impact if the inferred behavior is real and insufficiently tested.
- **Confidence** estimates how strongly the evidence supports the inference.

### Risk score

Recommended initial score from 0 to 100:

```text
risk_score =
    behavioral_impact      × 0.35
  + critical_path_weight   × 0.25
  + test_gap_weight        × 0.25
  + change_surface_weight  × 0.15
```

Each component is normalized to 0–100.

Suggested labels:

- `0–29`: low
- `30–59`: medium
- `60–79`: high
- `80–100`: critical

### Confidence score

Confidence should combine:

- deterministic pattern strength;
- parser completeness;
- contextual sufficiency;
- agreement between structural evidence and LLM output;
- number and quality of assumptions;
- whether the behavior is externally observable.

A high-risk, low-confidence finding should be presented as a **review question**, not as a fact.

---

## 14. Configuration

Suggested file: `.hermes/semantic-diff-weaver.yaml` or repository-local `.semantic-diff-weaver.yaml`.

```yaml
version: 1

language:
  primary: python

paths:
  include:
    - "src/**/*.py"
  exclude:
    - "**/migrations/**"
    - "**/generated/**"
    - "**/vendor/**"
  test_roots:
    - "tests"

critical_paths:
  - pattern: "src/auth/**"
    weight: 90
  - pattern: "src/payments/**"
    weight: 100

rules:
  max_changed_files: 40
  max_diff_lines: 3000
  minimum_report_confidence: 0.45
  emit_low_risk_refactors: false

mapping:
  - source: "src/auth/**"
    tests:
      - "tests/auth/**"
      - "tests/e2e/test_login.py"

privacy:
  redact_patterns: true
  allow_network: false
```

---

## 15. Architecture

### Plugin category

`general`

The plugin should register one primary tool through `ctx.register_tool`. It should use `ctx.llm.complete_structured` for schema-constrained semantic inference.

It must not modify Hermes core files.

### Proposed package layout

```text
semantic-diff-weaver/
├── plugin.yaml
├── __init__.py
├── README.md
├── schemas.py
├── config.py
├── git_diff.py
├── ast_diff.py
├── semantic_candidates.py
├── semantic_interpreter.py
├── test_mapper.py
├── scoring.py
├── renderer.py
├── models.py
└── tests/
    ├── fixtures/
    ├── test_git_diff.py
    ├── test_ast_diff.py
    ├── test_patterns.py
    ├── test_test_mapper.py
    ├── test_scoring.py
    ├── test_renderer.py
    └── test_register.py
```

### Internal module responsibilities

| Module | Responsibility |
|---|---|
| `git_diff.py` | Resolve refs, collect files/hunks, enforce bounds |
| `ast_diff.py` | Parse old/new Python source and compute structural deltas |
| `semantic_candidates.py` | Detect deterministic behavior-change candidates |
| `semantic_interpreter.py` | Call `ctx.llm.complete_structured` with bounded evidence |
| `test_mapper.py` | Find and rank candidate existing tests |
| `scoring.py` | Risk, priority, and confidence calculations |
| `renderer.py` | JSON and Markdown output |
| `models.py` | Typed internal models and validation |

---

## 16. LLM Interaction Design

### Input to the model

The model should receive:

- repository-level purpose if available from a bounded README excerpt;
- changed symbol name and signature;
- compact old/new structural summaries;
- selected diff lines;
- nearby docstrings or comments;
- relevant project QA rules;
- the required JSON schema.

### The model must not receive

- the entire repository;
- unbounded source files;
- credentials or environment files;
- binary files;
- unrelated conversation history;
- generated or vendored code unless explicitly included.

### Prompt behavior

The inference instructions should require the model to:

- distinguish evidence from assumptions;
- phrase behavior as observable outcomes;
- avoid claiming runtime coverage;
- return `unknown_semantic_change` when evidence is insufficient;
- avoid inventing business rules not present in evidence;
- generate tests against behavior, not implementation details;
- produce concise obligations that a human can validate.

### Deterministic validation

After the LLM response:

- reject unknown taxonomy values;
- reject evidence references outside changed files;
- reject high-confidence findings with no evidence;
- cap obligation count per behavior change;
- lower confidence when assumptions are numerous;
- deduplicate semantically overlapping obligations.

---

## 17. Security and Privacy

The MVP is read-only and local by default.

### Requirements

- Resolve and validate `repo_path` with `realpath`.
- Never read outside the repository except the explicit plugin config.
- Exclude `.env`, credentials, private keys, and common secret files.
- Do not execute repository code.
- Do not import changed modules.
- Do not run package managers or build steps.
- Do not access the network directly.
- Bound file size, diff size, number of files, and model input size.
- Treat comments, documentation, source strings, and test names as untrusted input.
- Escape all paths and arguments passed to Git subprocesses.
- Return structured errors without leaking source contents unnecessarily.

### Prompt-injection protection

Repository text may contain instructions intended for an agent. The semantic interpreter must treat all repository content as data and explicitly ignore instructions found inside code, comments, test fixtures, or documentation.

---

## 18. Performance Requirements

Target for the MVP on a typical pull request:

- Up to 40 changed files.
- Up to 3,000 changed lines.
- Up to 100 changed Python symbols.
- Deterministic preprocessing under 5 seconds on a normal developer machine.
- One structured LLM call per bounded batch of related symbols.
- Default maximum of 8 LLM calls per analysis.
- Total output limited to 100 test obligations.

When limits are exceeded, the plugin shall:

1. prioritize critical paths;
2. analyze the highest-risk symbols;
3. report omitted scope explicitly;
4. never silently truncate evidence.

---

## 19. Error Handling

Canonical error response:

```json
{
  "success": false,
  "error": "diff_too_large",
  "message": "The diff contains 5,842 changed lines; the configured limit is 3,000.",
  "remediation": "Narrow the include patterns, split the pull request, or increase rules.max_diff_lines."
}
```

Required error categories:

- `not_a_git_repository`
- `invalid_ref`
- `path_outside_repository`
- `unsupported_language`
- `diff_too_large`
- `parse_failure`
- `llm_unavailable`
- `llm_schema_failure`
- `configuration_error`
- `internal_error`

If the LLM is unavailable, the plugin should optionally return a reduced deterministic report containing structural change candidates without semantic conclusions.

---

## 20. Testing Strategy

### Unit tests

- Git ref and path validation.
- Unified diff parsing.
- AST extraction.
- Operator and boundary pattern detection.
- Signature and exception changes.
- Candidate test matching.
- Risk/confidence scoring.
- JSON schema validation.
- Markdown rendering.
- Secret-file exclusion.

### Golden tests

Maintain small old/new repository fixtures for:

- `<` changed to `<=`;
- default value changed;
- exception swallowed vs propagated;
- retry predicate changed;
- authorization guard removed;
- output field renamed;
- pure refactor with stable behavior;
- ambiguous dynamic code.

Expected semantic reports should be stored as reviewed golden JSON files.

### LLM contract tests

- Schema-valid response.
- Unknown category rejection.
- Unsupported evidence rejection.
- Prompt-injection fixture.
- Low-context uncertainty behavior.
- Deduplication of overlapping obligations.

### Integration tests

- Plugin registration in Hermes.
- Analysis of a temporary Git repository with two commits.
- JSON and Markdown output generation.
- Graceful behavior with no changed source files.

---

## 21. MVP Acceptance Criteria

The MVP is complete when all of the following are true:

1. A user can analyze a local Python repository by providing `repo_path` and `base_ref`.
2. The plugin returns valid schema-versioned JSON.
3. The plugin detects at least the following deterministic patterns:
   - comparison boundary change;
   - default argument change;
   - exception-path change;
   - retry/loop-limit change;
   - function signature change.
4. Every reported behavior change contains evidence and confidence.
5. Every high- or critical-risk behavior change has at least one test obligation.
6. Existing tests are presented only as candidates, never as proven coverage.
7. The plugin does not execute or modify repository code.
8. Diff and model-input limits are enforced.
9. A reviewed fixture set achieves:
   - at least 80% precision for material behavior-change findings;
   - at least 70% recall on the supported deterministic pattern set;
   - zero fabricated evidence references.
10. A Markdown report can be pasted into a pull request without manual reformatting.

---

## 22. Product Metrics

### Primary validation metrics

- **Accepted obligation rate:** percentage of suggested test obligations reviewers mark as useful.
- **Material finding precision:** percentage of reported behavior changes judged real or plausibly relevant.
- **Miss rate:** known important behavior changes absent from the report.
- **Evidence correctness:** percentage of evidence references that genuinely support the finding.
- **Time saved:** median reduction in manual PR test-planning time.

### Secondary metrics

- Analyses per active repository.
- Repeat usage over four weeks.
- Percentage of reports with at least one accepted missing test.
- Number of obligations converted into tests by users or downstream tools.
- Average cost and latency per analysis.
- Frequency of `unknown_semantic_change`.

### Metrics to avoid optimizing early

- Total number of generated scenarios.
- Report length.
- Number of changed lines analyzed.
- Apparent confidence without human validation.

---

## 23. Key Risks and Mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| Generic or obvious recommendations | Low perceived value | Require evidence, project rules, and behavior-specific obligations |
| Hallucinated business behavior | Loss of trust | Separate evidence/assumptions; lower confidence; use unknown category |
| Too many findings | Review fatigue | Risk threshold, deduplication, obligation caps |
| Static test mapping is mistaken for coverage | Misleading output | Label mappings as candidates and state limitation prominently |
| Large diffs overwhelm the model | Cost and poor quality | Symbol batching, critical-path prioritization, hard limits |
| Language-specific complexity grows quickly | Scope explosion | Python-only MVP and adapter interface for later languages |
| Refactors produce false positives | Noise | Refactor category and conservative materiality threshold |
| Repository prompt injection | Unsafe agent behavior | Treat repository content as untrusted data; no tool execution from model output |
| LLM variability | Unstable reports | Structured schema, deterministic evidence, golden evaluations, bounded temperature |

---

## 24. Roadmap

### v0.1 — Advisory semantic test plan

- Python AST adapter.
- Local Git diff.
- Behavioral change taxonomy.
- Test obligations.
- Candidate test mapping.
- JSON and Markdown reports.

### v0.2 — Stronger repository context

- Dependency graph.
- Config and API schema awareness.
- Optional composition with test history and coverage history.
- Improved cross-file change grouping.

### v0.3 — Test skeleton generation

- Generate framework-specific test skeletons from approved obligations.
- Human approval required before writing files.
- Patch output only; no direct commit.

### v0.4 — CI and pull-request integration

- GitHub Action wrapper.
- PR comment renderer.
- Baseline comparison between analysis revisions.
- Policy thresholds for advisory `pass`, `warn`, or `review_required`.

### v1.0 — Multi-language semantic test intelligence

- Adapter API for TypeScript/JavaScript and Java.
- Dynamic coverage enrichment.
- Verified source-to-test mapping.
- Evaluation corpus and public benchmark.
- Downstream integration with regression selection and autonomous test generation.

---

## 25. Suggested Positioning

### Short description

> An evidence-backed semantic diff analyzer that turns code changes into risk-ranked test obligations.

### GitHub repository description

> Hermes Agent plugin that analyzes the behavioral meaning of Git diffs and generates evidence-backed, risk-ranked test plans for pull requests.

### Differentiation

The product is not:

- another line-diff summarizer;
- a code-review chatbot;
- a coverage dashboard;
- an unrestricted test generator.

Its differentiated artifact is the **traceable test obligation**:

```text
structural change
    → inferred observable behavior
    → evidence
    → risk and confidence
    → test obligation
    → candidate existing test or explicit gap
```

---

## 26. Open Design Questions

1. Should the MVP infer repository purpose from README content, or require an explicit project summary?
2. Should the plugin issue one LLM call per changed symbol group or one call per file?
3. What is the minimum useful project-level risk configuration?
4. Should low-confidence findings appear by default or only with a verbose flag?
5. How should renamed and moved symbols be matched across revisions?
6. Which public repositories can form an evaluation corpus with human-labeled behavioral diffs?
7. Should deterministic findings be emitted when the LLM is unavailable?
8. What threshold should distinguish a likely refactor from a material behavior change?

---

## 27. Recommended First Implementation Slice

Build one vertical path before implementing the full taxonomy:

```text
Python Git diff
  → changed function extraction
  → boundary/default/exception pattern detection
  → structured semantic interpretation
  → three-part test obligation
  → Markdown report
```

Use five carefully curated fixtures and optimize for precision rather than breadth. The first compelling demonstration should show that the plugin turns a small, non-obvious code change into a concrete set of boundary and negative tests that a reviewer agrees are necessary.
