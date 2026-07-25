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
  include: ["**/*.py"]
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
  deterministic_fallback: true
mapping: []
privacy:
  redact_patterns: true
  allow_network: false
```

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

