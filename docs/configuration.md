# Configuration

Configuration precedence, highest first:

1. Tool `include` and `exclude` arguments.
2. Explicit `risk_profile` YAML.
3. `.hermes/semantic-diff-weaver.yaml`.
4. `.semantic-diff-weaver.yaml`.
5. Built-in defaults.

Lists replace lower-precedence lists. Mandatory secret/control exclusions remain additive.

```yaml
version: 1
language:
  primary: python
paths:
  include: ["**/*.py", "**/*.yaml", "**/*.yml", "**/*.json"]
  exclude: ["**/migrations/**", "**/generated/**", "**/vendor/**"]
  test_roots: ["tests"]
critical_paths: []
rules:
  max_changed_files: 40
  max_diff_lines: 3000
  max_changed_symbols: 100
  max_file_bytes: 1000000
  max_readme_chars: 4000
  max_evidence_chars_per_symbol: 6000
  max_model_input_chars_per_call: 48000
  max_llm_calls: 8
  max_obligations_per_behavior: 6
  max_test_obligations: 100
  max_candidate_tests_per_obligation: 5
  minimum_report_confidence: 0.45
  review_question_confidence: 0.60
  refactor_materiality_threshold: 0.25
  emit_low_risk_refactors: false
  analyze_test_files: false
  analyze_api_schemas: true
  deterministic_fallback: true
mapping: []
privacy:
  redact_patterns: true
  allow_network: false
verdict:
  warn_at: medium
  review_required_at: high
```

`summary.verdict` is an advisory `pass`, `warn`, or `review_required`, read from the overall risk:
`warn` from `verdict.warn_at` and `review_required` from `verdict.review_required_at`, each a risk
label or `none` to turn that level off, with `warn_at` no higher than `review_required_at`. An
analysis that left scope unread — `scope.truncated`, after a limit, a parse failure, or an omitted
model batch — or that compared a commit with itself is at least `warn` whatever the thresholds say,
since an unread file is not a file without findings. Nothing blocks on the verdict: `--fail-on`
still decides the CLI's exit code, and a workflow can act on the Action's `verdict` output.

Critical paths use `{pattern, weight}` entries. A path matched by no pattern contributes a
default weight of 10 to the risk score, so an explicit `weight: 0` entry ranks a path *below* one
that is not listed at all: listing a path at zero asserts it is not critical, which is a stronger
statement than silence. Use it to deprioritize generated or legacy trees, and simply omit paths you
have no opinion about. Mapping entries use `{source, tests}` and influence only static candidate
ranking. YAML is size-bounded and loaded with a `SafeLoader` subclass that enforces node, depth, and
alias budgets in a single composing pass. Unknown sections, custom tags, unsupported versions, a
scalar where a section is expected (`language: python` rather than a `language:` mapping with
`primary: python`), invalid ranges, duplicate mapping sources, absolute patterns, drive paths, NULs,
and parent traversal are rejected as `configuration_error`; an unsupported `language.primary` is
rejected as `unsupported_language`. Repository-local configuration and every file it resolves
through must remain inside the repository after symlink resolution; an
explicitly named external `risk_profile` is the only exception to repository containment. It must
still remain inside a host-authorized workspace root. The default authorized root is the process
working directory; trusted operators can provide additional roots through the path-separator-delimited
`SEMANTIC_DIFF_WEAVER_ALLOWED_ROOTS` environment variable.

Changed test code is left out of the analysis and counted as `test_file`, because a test is not the
behavior under review: analyzed, each changed test function became a finding with an obligation to
test it. A path settles it for anything under a `test_roots` entry and for `conftest.py`, and those
files are left out before the changed-file and changed-line budgets apply. A name does not: a file
named like a pytest test module (`test_*.py`, `*_test.py`) is test code only if it defines a test,
so a production module such as `test_mapper.py` is still analyzed. Such a file has to be read to
tell, so it counts toward the budgets. Test files stay candidate tests either way. Set
`rules.analyze_test_files: true` to analyze them as product code.

The three model-input bounds are reported separately when they drop something, since they count
different units: `max_llm_calls` omits whole evidence batches as `llm_batch_limit`,
`max_model_input_chars_per_call` drops a symbol whose payload cannot fit a single call as
`model_input_symbol_limit`, and `max_evidence_chars_per_symbol` shortens a symbol's evidence as
`model_evidence_limit`. Each lowers reported confidence and marks scope truncated.

`privacy.redact_patterns` must remain `true` and `privacy.allow_network` must remain `false`.
Configured excludes are additive with mandatory control, credential, key, token, cache, environment,
and cloud-configuration exclusions. If a file/line budget is exceeded, configured critical paths are
prioritized only when this can be done within the same hard bounds; all remaining scope is reported as
omitted.

Repository configuration cannot raise the immutable collector ceilings: at most 1,000 included
changed Python files are analyzed, each source blob is limited to 8 MiB, and retained base/head
source is limited to 64 MiB in aggregate. Configured values below those ceilings remain effective.
Files omitted by an immutable ceiling are reported explicitly and mark the analysis scope as
truncated.

AST processing applies tighter independent ceilings before structural matching: 1,000,000 UTF-8
bytes per file version, 16 MiB of aggregate source, 50,000 nodes and 2,000 symbols per file, 4,000
retained symbols across the request, bounded similarity candidate windows, and a cooperative
10-second analysis deadline that spans parsing, matching, and comparison. Reaching the deadline
counts as spending it, so a zero budget bounds the run immediately. Exceeding any ceiling produces
explicit `ast_resource_limit` scope.

Candidate-test indexing also has non-configurable safety ceilings of 500 test files and 8 MiB of
aggregate UTF-8 test source. These ceilings cannot be expanded by repository configuration. Reaching
either one emits a warning and uses `mapping_incomplete` when no candidate is found.

Path-pattern strings are limited to 512 characters. Include, exclude, test-root, critical-path, and
mapping collections have immutable item-count limits so repository configuration cannot create an
unbounded glob-matching workload.

## API schemas

A changed `.yaml`, `.yml`, or `.json` file is read whole, and compared as an API schema when
either revision's root says it is one:

| Document | Its root |
| --- | --- |
| OpenAPI 3 | `openapi: 3.x`, with `paths`, `components`, or `webhooks` |
| Swagger 2 | `swagger: "2.0"`, with `paths` or `definitions` |
| JSON Schema | a `$schema` naming a `json-schema.org` meta-schema or the OpenAPI 3.1 dialect |

The content decides, not the name, and a configuration file that names the schema validating it
in its `$schema`, as `tsconfig.json` does, is no schema. Every other file is counted as
`not_api_schema`, and one that cannot be read as bounded text — larger than
`rules.max_file_bytes`, not UTF-8, or a symbolic link — as `unreadable_document`. Neither marks
scope truncated: most changed YAML and JSON files are lockfiles, manifests, and data, and whether
an unread one was a schema cannot be told. A schema that does not parse within its budgets is
different: when its text opens with a schema's key and version, it is a finding of its own,
counted as `parse_incomplete_documents`, and the analysis is truncated.

Documents are read apart from Python source, under ceilings of their own that configuration
cannot raise: at most 200 changed YAML and JSON files, critical paths first, and 64 MiB of base and
head text in all. Those past either are omitted as `document_limit`, which truncates the analysis.
Parsing allows a document 200,000 nodes, a depth of 64, and 100 YAML aliases, and the comparison of
one document stops at 500 changes, as `schema_delta_limit`.

`paths.include` narrows documents as it narrows Python: an include of `src/**/*.py` alone analyzes
no schema and counts each as `path_filter`, so name the schemas too, as `api/openapi.yaml`. A
document under a `test_roots` entry is a fixture, left out as `test_file`.
`rules.analyze_api_schemas: false` turns the comparison off, and YAML and JSON files are then
`unsupported_extension`, as every file that is not Python was before.

A change to an operation maps to the tests that request its route: a test whose string literals,
or those of the same-file helpers it calls, write out a path the route template matches after any
base path, and more strongly one that also calls the operation's method by name, as
`client.delete(...)`. A change to a reusable schema gets no candidate test, since `$ref` is never
followed to the operations that use it.

## Coverage grounding

The tool can ingest a coverage report your own CI already produced. **It consumes a coverage
report and never produces one**, and it runs nothing to obtain one — the artifact is read as
untrusted input data, exactly like repository content.

```yaml
coverage:
  report_path: "coverage.json"
rules:
  max_coverage_bytes: 20000000
```

Equivalently, `--coverage PATH` on the command line or `coverage_report` in the tool arguments.
The path is subject to the same authorization rules as `risk_profile`: it must resolve below an
authorized root, so a report outside the repository needs its directory passed with
`--allow-root`.

Two formats are supported, sniffed by content rather than by file extension:

| Format | Produced by | Parsed with |
| --- | --- | --- |
| coverage.py JSON | `coverage json` / `pytest --cov-report=json:coverage.json` | `json` |
| lcov `.info` | most non-Python toolchains, `coverage lcov` | line-oriented reader |

Cobertura and JaCoCo XML are **not** supported, deliberately — see `docs/decisions.md`.

`max_coverage_bytes` defaults to 20 MB and is clamped to `[1024, 200000000]`. An unreadable,
malformed, oversized, or unrecognized report is a `coverage_unreadable` error rather than a
silent downgrade: someone who asked for grounded coverage should not be handed ungrounded
output that looks identical.

### What a grounded verdict means

Coverage reports store paths relative to the CI working directory, which need not match the
analyzer's repository-relative paths. Both sides are normalized to POSIX and resolved by
longest matching suffix, with an exact match winning outright and a tie between unrelated
files resolving to *unknown* rather than to a guess.

A changed file absent from the report is **`unknown`, never `uncovered`** — otherwise a
misconfigured path prefix would be indistinguishable from a repository with no tests. The
number of unmatched files is reported in `coverage.unmatched_files` and named in a warning.

An obligation is marked `changed_lines_uncovered` only when *every* changed line the finding
rests on is unexecuted, and `covered_by_existing_tests` only when every one is executed. A
mixed or unknown range keeps the existing `candidate_exists_unverified` / `no_candidate_found`
semantics. `CandidateTest.verified` stays `false` in every case: coverage says a *line* was
executed by the suite, not that a specific candidate test asserts the changed behavior.


## Baseline comparison

An analysis can be compared with an earlier one of the same repository, as a pull request moves
on: `--baseline PATH` on the command line or `baseline_report` in the tool arguments names the
JSON an earlier run wrote with `--format json` or `--format both`. Like a coverage report, the
file is untrusted input data: bounded by `rules.max_baseline_bytes` (20 MB by default, clamped to
`[1024, 200000000]`), read as strict UTF-8, authorized like `risk_profile`, and read only for the
fields the comparison needs. Any 1.x schema is accepted.

Each finding is matched by its category, path, and symbol, the key findings are grouped by within
one analysis, and `baseline` in the result lists this analysis's finding IDs as `new`, `updated`
when the finding's evidence changed since, or `unchanged`, besides the baseline's findings no
longer reported, as `resolved`. Line numbers are left out of the comparison, so an unrelated edit
above a symbol does not make its finding updated. The brief says how many fall in each group,
marks new and updated findings beside their IDs, and names each resolved finding by its category,
path, symbol, and risk — never by the baseline's prose, which could otherwise write into the brief.

An unreadable, malformed, oversized, or non-1.x baseline, or a `--format markdown` brief, is a
`baseline_unreadable` error rather than an analysis that silently compared nothing.

## Test skeletons

`semantic-diff-weaver skeletons` reads the repository's own configuration, with no risk profile or
override: a new test file is created beside the obligation's first candidate test, or in the first
`paths.test_roots` entry without one, a test file under `paths.exclude` takes no skeleton, and
`rules.max_file_bytes` bounds each test file it reads. The analysis it reads is bounded by
`rules.max_baseline_bytes`, as a baseline is, and an unreadable, malformed, oversized, or non-1.x
analysis, or a `--format markdown` brief, is an `analysis_unreadable` error.
