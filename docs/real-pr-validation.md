# Real pull-request validation

Generated 2026-10-31 by `scripts/validate_real_prs.py`, deterministic mode (`--no-llm`), up to 4 of the most recent two-parent merges per repository, each reviewed from where its pull request branched off.

**These are observations on unlabeled data, not precision measurements.** No one has
reviewed these diffs to the standard the golden corpus requires, so nothing here is a
true-positive or false-positive rate. Real-PR cases are deliberately *not* added to the
golden corpus. The measured numbers live in `docs/evaluation.md`.

## Aggregate

| Metric | Value |
| --- | --- |
| Repositories analyzed | 4 |
| Merges analyzed | 14 |
| Merges skipped | 0 |
| Findings per PR (mean) | 5.57 |
| Findings per PR (median) | 1.5 |
| Findings per PR (max) | 34 |
| PRs with zero findings | 6 |
| Mean confidence | 0.769 |
| Wall clock per PR (mean) | 0.33s |
| Wall clock per PR (max) | 1.01s |

## Category distribution

| Category | Count |
| --- | --- |
| `unknown_semantic_change` | 29 |
| `dependency_interaction_change` | 15 |
| `boundary_change` | 13 |
| `output_contract_change` | 12 |
| `retry_timeout_change` | 3 |
| `default_behavior_change` | 2 |
| `side_effect_change` | 2 |
| `error_handling_change` | 1 |
| `validation_change` | 1 |

## Risk distribution

| Risk | Count |
| --- | --- |
| medium | 78 |

## Per-merge detail

| Repository | PR head | Files | Findings | Obligations | Overall risk | Seconds |
| --- | --- | --- | --- | --- | --- | --- |
| psf/requests | `514b3f2345e5` | 1 | 0 | 0 | low | 0.121 |
| psf/requests | `7112775514a8` | 1 | 0 | 0 | low | 0.01 |
| psf/requests | `968863678bc0` | 1 | 0 | 0 | low | 0.011 |
| psf/requests | `42eaeb4da873` | 3 | 0 | 0 | low | 0.011 |
| pallets/click | `9f0bcdf0d868` | 7 | 17 | 14 | medium | 0.907 |
| pallets/click | `b90faad1788b` | 8 | 8 | 7 | medium | 0.807 |
| pallets/click | `bb695aaecdef` | 11 | 34 | 27 | medium | 1.011 |
| pallets/click | `05f6fd0df4ec` | 5 | 7 | 12 | medium | 0.76 |
| psf/black | `68cc9786d3db` | 6 | 2 | 3 | medium | 0.286 |
| psf/black | `3c62b9cada3f` | 2 | 0 | 0 | low | 0.011 |
| encode/httpx | `c7af2b1a5d1f` | 2 | 0 | 0 | low | 0.013 |
| encode/httpx | `5961bd8ba3e1` | 2 | 4 | 9 | medium | 0.243 |
| encode/httpx | `c2eeb662dfcc` | 1 | 1 | 1 | medium | 0.196 |
| encode/httpx | `d0953d1d863a` | 2 | 5 | 10 | medium | 0.251 |

## Correction (2026-10-31)

The first version of this page, generated 2026-07-25, analyzed each merge from its first parent
to its second. The analyzer compares two trees as they are, so every commit the target branch
gained after a pull request branched off read as reverted by that pull request — the flaw the
GitHub Action's `base-ref` default had until 2026-10-19. The harness now starts from the
`git merge-base` of the two parents.

Measured on the same 14 merges:

| Range and analyzer | Findings | Mean / max per PR | PRs with zero findings | `unknown_semantic_change` |
| --- | --- | --- | --- | --- |
| First parent, 2026-07-25 | 303 | 21.64 / 166 | 5 | 116 (38%) |
| First parent, 2026-10-31 | 163 | 11.64 / 78 | 5 | 72 (44%) |
| Merge-base, 2026-10-31 | 13 | 0.93 / 5 | 9 | 5 (38%) |

The analyzer's changes since July cut the old range's findings from 303 to 163, and 150 of those
163 were not changes the pull requests made. The 166-finding click merge, `b3a191bf58db`, fixed
the sdist include and changed no Python file; its range carried 20 Python files from 16 later
commits on `main`. The long tail and the category mix were measured mostly on such reverted
commits, and three of the ten findings July's review read came from them.

The sample is also older than "recent merges" suggests. `psf/black` squash-merges, and its only
two merge commits date from 2018; the most recent merges in `encode/httpx` are from 2019; the four
in `psf/requests` are Dependabot bumps of GitHub Actions with no Python change. Only
`pallets/click` contributes current pull requests, which is why its rows changed since July and
the others did not. Sampling recent squash-merged pull requests needs the harness to read them as
single commits, which it does not yet do.

Of the ten findings reviewed by hand on 2026-07-25:

- Five are still reported, unchanged, on the pull requests that made them: black `is_python36`
  (finding 9 below); httpx `Stream.get_http_version`, `SSLConfig._create_default_ssl_context`
  (finding 10 below), and `example.py`; and click `split_arg_string`.
- Two, both on httpx `RedirectMiddleware.redirect_headers`, came from `c7af2b1a5d1f`, a
  documentation pull request whose old range read the later changes of encode/httpx#310
  backwards. The `method` parameter and the two guards they showed as removed were added, and
  that pull request (`5961bd8ba3e1`) now reports them that way.
- Two, httpx `MockDispatch.send` and `test_body_redirect`, are gone because changed test files
  are no longer analyzed as product code.
- One, black `tests/expression.py` `<unparsed>`, came only from the old range.

## Hand-reviewed sample

Ten findings from the tables above, read against their diffs and marked plausible or implausible,
with one line of reasoning each. They cover six of the eight merges with findings and every
category with more than two, and were chosen from the list of findings before their diffs were
read. The harness generates the tables above but never a verdict here.

| # | Repository / symbol | Category | Verdict | Reasoning |
| --- | --- | --- | --- | --- |
| 1 | pallets/click `Argument.get_error_hint` (`9f0bcdf0d868`) | `output_contract_change` (0.879) | **Implausible as presented** | The hint now comes from `get_help_spec(ctx)` and `spec` instead of `make_metavar(ctx)` and `human_readable_name`, but for an `Argument` each new call returns what the old one did. The text changes only for a subclass overriding `human_readable_name`, which Click no longer reads; "consumers may observe a different value" overstates that. |
| 2 | pallets/click `Parameter.human_readable_name` (`9f0bcdf0d868`) | `dependency_interaction_change` (0.714) | **Plausible finding, wrong category** | The property now emits a `DeprecationWarning` and returns `spec`, so `times` becomes `--times`. Both matter, but `warnings.warn` is not a dependency: the warning is a side effect, and the new value is reported separately as `output_contract_change`. |
| 3 | pallets/click `Argument.get_help_spec` (`9f0bcdf0d868`) | `unknown_semantic_change` (0.69) | **Correct but low value** | A new public method returning `make_metavar(ctx)`. Asking for a characterizing test is fair; the category says nothing about what to test. |
| 4 | pallets/click `Option._parse_decls` (`b90faad1788b`) | `boundary_change` (0.897) | **Plausible finding, wrong category** | An option's name now goes through `_check_name_is_usable`, and an explicit one through `_check_name_is_normalized`, both of which warn. The comparisons involved are `is None` and `is not None`; a reviewer would call this validation, not a boundary. |
| 5 | pallets/click `_make_default_short_help` (`bb695aaecdef`) | `boundary_change` (0.897) | **Plausible** | The truncation was rewritten around `textwrap.shorten`: text at `max_length`, a `max_length` shorter than `...`, and a period inside an abbreviation now behave differently. These are exactly the boundary cases to test. |
| 6 | pallets/click `_make_default_short_help` (`bb695aaecdef`) | `retry_timeout_change` (0.847) | **Implausible** | The removed `while i > 0` loop dropped words until the text fit. It is finding 5's change again, called a retry or timeout because `SDW-RULE-LOOP` reports every changed loop that way. |
| 7 | pallets/click `Argument.get_help_record` (`05f6fd0df4ec`) | `boundary_change` (0.897) | **Implausible as presented** | The removed `if self.help is None: return None` is an identity test, not a boundary. What it changed, a row in the help page for every positional argument, is finding 8. |
| 8 | pallets/click `Argument.get_help_record` (`05f6fd0df4ec`) | `output_contract_change` (0.879) | **Plausible** | The method no longer returns `None`, so a caller's `None` check never fires, and `Command.format_arguments` now lists an undocumented argument whenever another one has help. The rule's impact text is about callers supplying different arguments, which fits a parameter change better than a return type. |
| 9 | psf/black `is_python36` (`68cc9786d3db`) | `boundary_change` (0.897) | **Implausible** | `ch.type == token.STAR or ch.type == token.DOUBLESTAR` became `ch.type in STARS`, where `STARS = {token.STAR, token.DOUBLESTAR}`: a refactor, read the same way on 2026-07-25. |
| 10 | encode/httpx `SSLConfig._create_default_ssl_context` (`d0953d1d863a`) | `unknown_semantic_change` (0.68) | **Plausible finding, weak category** | The branch setting NPN protocols when `ssl.HAS_NPN` is true was removed. Real and security-adjacent, as on 2026-07-25; a reviewer would call it a compatibility or dependency change. |

Two of the ten are plausible as presented, three are real changes under a category a reviewer
would dispute, one is correct but tells a reviewer little, and four are implausible or redundant
as presented. No finding was fabricated: every one points at a real edit. As in July, the
recurring problem is classification and relevance, not invention.

## Where a category fires more often than a reviewer would expect

**`boundary_change` is any changed comparison.** `SDW-RULE-BOUNDARY` makes every
`comparison_change` a boundary without reading its operator, so identity and membership tests
count: `is None` in findings 4 and 7, and `in STARS` in finding 9. The condition rule has read
operators from the AST since 2026-10-05 and leaves `in` and `is` out; the comparison rule does
not. Of the four boundary findings reviewed, one is a boundary.

**Every changed loop is a `retry_timeout_change`.** `SDW-RULE-LOOP` has no other outcome, so the
word-trimming loop of finding 6 and the `for` loop over a command's parameters in
`Command.format_arguments` are reported as retry or timeout changes. None of the three loop
findings in this run involves a retry.

**`unknown_semantic_change` is still the largest category: 29 of 78 findings (37%).** Fifteen are
changed assignments, which `SDW-RULE-UNKNOWN-ASSIGNMENT` has sent here since 2026-10-19 unless
they are named for state, and nine are added or removed symbols, as in finding 3. They are honest,
but each tells a reviewer only that something changed.

**Annotation changes are output-contract changes.** Four of the 12 `output_contract_change`
findings are typing changes to `click.types.Path` in `bb695aaecdef` — a generic base class, a
parameter annotation, and two return annotations — which change what a type checker infers, not
what a call returns. The same change gave `Path.coerce_path_result` a local alias for `self.type`,
reported besides as a boundary, a dependency, and an unknown change.

**A merge into another branch is counted twice.** The harness takes every two-parent merge
reachable from the default branch, so pallets/click#3860, merged into `stable`, is reported alone
(`05f6fd0df4ec`, 7 findings) and again inside the merge of `stable` into `main` (`bb695aaecdef`,
34 findings, all 7 among them).

**Deterministic-mode latency is still not a concern.** Mean 0.33s and max 1.01s per merge, with no
model calls.

## Reproducing

```bash
python scripts/validate_real_prs.py \
  --repo psf/requests --repo pallets/click --repo psf/black --repo encode/httpx \
  --count 4 --scratch /tmp/sdw-validation
```

Repositories are cloned with `--no-checkout` into the scratch directory, never into the working
tree. Nothing from them is installed or executed; the analyzer reads Git objects only. The script
writes this page with the two hand-written sections empty, so write it elsewhere with `--output`
and carry the tables over.
