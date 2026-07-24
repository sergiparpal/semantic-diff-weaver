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

Critical paths use `{pattern, weight}` entries. Mapping entries use `{source, tests}` and influence
only static candidate ranking. YAML is size-bounded and loaded with `safe_load`. Unknown sections,
custom tags, unsupported versions/languages, invalid ranges, duplicate mapping sources, absolute
patterns, drive paths, NULs, and parent traversal are rejected. Repository-local configuration and
every file it resolves through must remain inside the repository after symlink resolution; an
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
10-second analysis deadline. Exceeding one produces explicit `ast_resource_limit` scope.

Candidate-test indexing also has non-configurable safety ceilings of 500 test files and 8 MiB of
aggregate UTF-8 test source. These ceilings cannot be expanded by repository configuration. Reaching
either one emits a warning and uses `mapping_incomplete` when no candidate is found.

Path-pattern strings are limited to 512 characters. Include, exclude, test-root, critical-path, and
mapping collections have immutable item-count limits so repository configuration cannot create an
unbounded glob-matching workload.
