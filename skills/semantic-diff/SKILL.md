---
name: semantic-diff
description: Review the behavioral meaning of a committed Python change and plan regression tests with the read-only analyze_semantic_diff tool, then scaffold failing tests for the obligations the user approves with generate_test_skeletons. Use when the user asks what a diff, branch, or pull request changes in behavior, how risky it is, what to test before merging, or to scaffold those tests.
---

# Semantic diff review

The `analyze_semantic_diff` tool from the `semantic-diff-weaver` MCP server reads two
committed Git revisions and returns evidence-backed, risk-ranked test obligations, and its
`generate_test_skeletons` tool scaffolds failing tests for the ones the user approves. Neither
imports, executes, builds, or modifies the repository.

1. **Pick the refs.** `head_ref` defaults to `HEAD`. The base is what the user named, or else
   the branch the work will merge into: `git symbolic-ref --short refs/remotes/origin/HEAD`
   names the remote's default branch when one is recorded, and `main` or `master` stands in
   when none is. A local branch can lag behind its remote copy, and a stale `main` would count
   work that is already merged as part of this change, so when both exist, use the one that
   contains the other (`git merge-base --is-ancestor main origin/main` succeeds when
   `origin/main` does), and ask the user if they have diverged. Then pass
   `git merge-base <base> <head>` as `base_ref`, unless the user asked to compare the two
   revisions as they stand: the tool diffs the two trees directly, so commits the base gained
   after the work branched off would read as reverted by it. None of this needs a `git fetch`.
   Say which base and merge-base commit you used. Only *committed* changes are analyzed: if
   `git status` shows uncommitted edits to Python files, say that they are not included.
2. **Call the tool** with `repo_path` set to the project directory (`.` works), `base_ref`,
   and `head_ref`, and leave `output_format` unset. The default is the Markdown brief: every
   finding and obligation with its ID, at about half the size of the JSON. When a large brief
   says its evidence excerpts were shortened, `git diff` shows the code. Pass `coverage_report`
   only when a coverage.py JSON or lcov file already exists; do not run tests to create one.
   Pass `baseline_report` only to compare with an earlier analysis saved as JSON, when the user
   asks what changed since it; the brief then marks each finding new or updated.
3. **Keep a large diff readable.** If the result is saved to a file for being too large, read
   only its first lines for the overall risk and scope, then analyze one area per call with
   `include` (for example `src/**`, then `scripts/**`) instead of reading the whole file.
   Narrow with `include`, never with `exclude`: `include` limits only which changed files are
   analyzed, while `exclude` also hides files from candidate-test discovery, so excluding a
   tests directory silently drops every candidate test. By default, changed tests are candidate
   tests rather than behavior under review: they are set aside before `include` applies, so an
   `include` that covers only tests analyzes nothing. Ask for `output_format: "json"` only on a
   narrowed call, when you need fields the brief leaves out, such as score explanations.
   IDs (`bc-`, `to-`, `ev-`) number positions within one result, and a narrowed call renumbers
   them: quote IDs only from the result you are reporting.
4. **On an error result**, read its `error` code and `remediation`. `path_outside_repository`
   means `repo_path` is outside the authorized workspace — the project directory, unless the
   operator set `SEMANTIC_DIFF_WEAVER_ALLOWED_ROOTS`. Do not retry with another path to get
   around it; tell the user how to authorize the directory instead.
5. **Report** the verdict and overall risk the brief opens with, then the obligations highest
   risk first. For each one give its ID, the behavior that changed, the file and symbol, and the
   evidence IDs that support it. Present the listed candidate tests as *unverified* — the tool
   matches them statically and never runs them. A candidate shown as a whole file stands for
   tests related to the change only through their file, none of them by name. Say so if the
   result reports truncation or omitted scope.
6. **Scaffold only what the user approves.** When the user names obligations to test, call
   `generate_test_skeletons` with their IDs and the same `repo_path`, `base_ref`, `head_ref`,
   `include`, `exclude`, `risk_profile`, and `coverage_report` as the analysis whose brief they
   read: the server analyzes again without inference, so the brief's IDs name the same
   obligations. Never choose obligations for the user. Pass `framework: "unittest"` when the
   project's tests are `unittest.TestCase` classes. The result is JSON: show the patch, say
   where each test went, and give the reason for any obligation that got none. The tool writes
   nothing; apply the patch with `git apply` only if the user asks. Each skeleton fails until
   it is written, so offer to write the tests the obligations describe.

**Explaining a risk score.** The brief gives each finding's label; the JSON's
`score_explanation` gives its four axes, which combine as
`risk_score = round(behavioral_impact * 0.35 + critical_path_weight * 0.25 + test_gap_weight * 0.25 + change_surface_weight * 0.15)`.
Labels run `critical` from 80, `high` from 60, `medium` from 30, and `low` below that. Explain a
score from these numbers rather than from the tool's source, which the analyzed project rarely
contains.

The analysis is deterministic. Interpreting it — which obligations matter most for this
change and how to test them — is your part; ground every claim in the evidence the tool
returned rather than in guesses about the code.
