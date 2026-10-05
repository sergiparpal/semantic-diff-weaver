---
name: semantic-diff
description: Review the behavioral meaning of a committed Python change and plan regression tests with the read-only analyze_semantic_diff tool. Use when the user asks what a diff, branch, or pull request changes in behavior, how risky it is, or what to test before merging.
---

# Semantic diff review

The `analyze_semantic_diff` tool from the `semantic-diff-weaver` MCP server reads two
committed Git revisions and returns evidence-backed, risk-ranked test obligations. It never
imports, executes, builds, or modifies the repository.

1. **Pick the refs.** `head_ref` defaults to `HEAD`. For `base_ref`, use what the user named;
   otherwise the branch the work will merge into — usually `main` or `origin/main`, which
   `git rev-parse --abbrev-ref origin/HEAD` confirms. Only *committed* changes are analyzed:
   if `git status` shows uncommitted edits to Python files, say that they are not included.
2. **Call the tool** with `repo_path` set to the project directory (`.` works), `base_ref`,
   and `head_ref`, and leave `output_format` unset. The default is the Markdown brief: every
   finding with its evidence IDs, at about half the size of the JSON. Pass `coverage_report`
   only when a coverage.py JSON or lcov file already exists; do not run tests to create one.
3. **Keep a large diff readable.** If the result is saved to a file for being too large, read
   only its first lines for the overall risk and scope, then analyze one area per call with
   `include` (for example `src/**`, then `scripts/**`) instead of reading the whole file.
   Narrow with `include`, never with `exclude`: `include` limits only which changed files are
   analyzed, while `exclude` also hides files from candidate-test discovery, so excluding a
   tests directory silently drops every candidate test. Ask for `output_format: "json"` only
   on a narrowed call, when you need fields the brief leaves out, such as obligation IDs.
4. **On an error result**, read its `error` code and `remediation`. `path_outside_repository`
   means `repo_path` is outside the authorized workspace — the project directory, unless the
   operator set `SEMANTIC_DIFF_WEAVER_ALLOWED_ROOTS`. Do not retry with another path to get
   around it; tell the user how to authorize the directory instead.
5. **Report** the overall risk, then the obligations highest risk first. For each one give the
   behavior that changed, the file and symbol, and the evidence IDs that support it. Present
   the listed candidate tests as *unverified* — the tool matches them statically and never
   runs them. Say so if the result reports truncation or omitted scope.

The analysis is deterministic. Interpreting it — which obligations matter most for this
change and how to test them — is your part; ground every claim in the evidence the tool
returned rather than in guesses about the code.
