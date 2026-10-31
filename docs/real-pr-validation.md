# Real pull-request validation

Generated 2026-10-31 by `scripts/validate_real_prs.py`, deterministic mode (`--no-llm`), the 5 most recent pull requests merged into each repository's default branch: a squash merge reviewed from its parent, a merge commit from where its pull request branched off.

**These are observations on unlabeled data, not precision measurements.** No one has
reviewed these diffs to the standard the golden corpus requires, so nothing here is a
true-positive or false-positive rate. Real-PR cases are deliberately *not* added to the
golden corpus. The measured numbers live in `docs/evaluation.md`.

## Aggregate

| Metric | Value |
| --- | --- |
| Repositories analyzed | 4 |
| Pull requests analyzed | 20 |
| Pull requests skipped | 0 |
| Findings per PR (mean) | 3.80 |
| Findings per PR (median) | 0.0 |
| Findings per PR (max) | 29 |
| PRs with zero findings | 11 |
| Mean confidence | 0.729 |
| Wall clock per PR (mean) | 0.38s |
| Wall clock per PR (max) | 1.03s |

## Category distribution

| Category | Count |
| --- | --- |
| `unknown_semantic_change` | 38 |
| `dependency_interaction_change` | 15 |
| `output_contract_change` | 12 |
| `boundary_change` | 3 |
| `error_handling_change` | 3 |
| `default_behavior_change` | 2 |
| `side_effect_change` | 2 |
| `validation_change` | 1 |

## Risk distribution

| Risk | Count |
| --- | --- |
| medium | 76 |

## Per-pull-request detail

| Repository | PR | Head | Files | Findings | Obligations | Overall risk | Seconds |
| --- | --- | --- | --- | --- | --- | --- | --- |
| psf/requests | `#7636` | `3c7baaa05ab4` | 2 | 0 | 0 | low | 0.06 |
| psf/requests | `#7628` | `611c6162cbc4` | 1 | 0 | 0 | low | 0.011 |
| psf/requests | `#7616` | `dae7ef63b4df` | 1 | 0 | 0 | low | 0.01 |
| psf/requests | `#7609` | `5460f467b02e` | 1 | 0 | 0 | low | 0.014 |
| psf/requests | `#7608` | `6af0b94158bb` | 1 | 0 | 0 | low | 0.015 |
| pallets/click | `#3877` | `9f0bcdf0d868` | 7 | 17 | 11 | medium | 0.913 |
| pallets/click | `#3866` | `b90faad1788b` | 8 | 7 | 4 | medium | 0.816 |
| pallets/click | `#3876` | `bb695aaecdef` | 11 | 29 | 24 | medium | 1.028 |
| pallets/click | `#3851` | `7944461a80b7` | 29 | 12 | 12 | medium | 0.888 |
| pallets/click | `#3801` | `dda8db496c69` | 3 | 0 | 0 | low | 0.014 |
| psf/black | `#5642` | `93e221bb4cd8` | 1 | 0 | 0 | low | 0.011 |
| psf/black | `#5641` | `4d4cf3114206` | 3 | 1 | 2 | medium | 0.746 |
| psf/black | `#5626` | `408211f4ae4e` | 3 | 3 | 6 | medium | 0.952 |
| psf/black | `#5630` | `d16946630ead` | 5 | 4 | 7 | medium | 0.751 |
| psf/black | `#5639` | `a74759ef4c97` | 1 | 1 | 1 | medium | 0.89 |
| encode/httpx | `#3773` | `b5addb64f016` | 1 | 0 | 0 | low | 0.014 |
| encode/httpx | `#3699` | `ae1b9f66238f` | 3 | 2 | 1 | medium | 0.398 |
| encode/httpx | `#3703` | `ca097c96f97d` | 1 | 0 | 0 | low | 0.013 |
| encode/httpx | `#3692` | `def4778d622e` | 1 | 0 | 0 | low | 0.01 |
| encode/httpx | `#3677` | `435e1dac899a` | 2 | 0 | 0 | low | 0.01 |

## Corrections (2026-10-31)

Three corrections were made on 2026-10-31, to what this page measures and to the analyzer it
measures. The tables above reflect all three.

**The range.** The first version of this page, generated 2026-07-25, analyzed each merge from its
first parent to its second. The analyzer compares two trees as they are, so every commit the
target branch gained after a pull request branched off read as reverted by that pull request — the
flaw the GitHub Action's `base-ref` default had until 2026-10-19. A merge commit is now reviewed
from the `git merge-base` of its two parents. Measured on July's 14 merges:

| Range and analyzer | Findings | Mean / max per PR | PRs with zero findings | `unknown_semantic_change` |
| --- | --- | --- | --- | --- |
| First parent, 2026-07-25 | 303 | 21.64 / 166 | 5 | 116 (38%) |
| First parent, 2026-10-31 | 163 | 11.64 / 78 | 5 | 72 (44%) |
| Merge-base, 2026-10-31 | 13 | 0.93 / 5 | 9 | 5 (38%) |

The analyzer's changes since July cut the old range's findings from 303 to 163, and 150 of those
163 were not changes the pull requests made. July's long tail and category mix were measured
mostly on such reverted commits.

**The sample.** The harness took the most recent two-parent merges reachable from the default
branch. `psf/black` and `encode/httpx` squash-merge, so their merges dated from 2018 and 2019, and
a pull request merged into another branch counted twice: pallets/click#3860, merged into
`stable`, was reported alone and again inside the merge of `stable` into `main`. The harness now
reads the default branch's first-parent history and takes each commit whose subject names a pull
request, `Title (#123)` or `Merge pull request #123 from`: a squash merge is reviewed from its
parent, a merge commit from where its pull request branched off. The sample is the five latest
pull requests of each repository — black's from the last three days, click's from the last two
months, httpx's from 2025-10 to 2026-02 — and requests' five are still bumps without a Python
change.

**The analyzer.** The page's second version, generated earlier on 2026-10-31 from merge-base
ranges, found that `SDW-RULE-BOUNDARY` called every changed comparison a boundary, that
`SDW-RULE-LOOP` called every changed loop a retry, and that typing changes were worded as argument
changes. Three corrections followed, recorded in `docs/decisions.md` as the 2026-10-31
"Output-affecting corrections" for boundary comparisons, loops, and output contracts. On that
version's 14 merges they take the findings from 78 to 69, the boundary findings from 13 to 2 and
the retry findings from 3 to 0, and report four return changes a changed comparison had hidden.
Of the ten findings its hand review read:

- The two identity tests it reported as boundaries, in click's `Option._parse_decls` and
  `Argument.get_help_record` (its findings 4 and 7), are unknown changes that join the condition
  holding them, as is black's `is_python36` (finding 9), an equality rewritten as membership.
- The word-trimming loop of `_make_default_short_help` (finding 6) is an unknown change, not a
  retry; the truncation beside it (finding 5) is still a boundary.
- `Argument.get_help_record`'s output change (finding 8) now leads with the return change that
  makes it.
- Findings 1, 2, and 3, on pallets/click#3877, are reported as before. Finding 10, httpx's NPN
  branch, is unchanged on its 2019 merge, which is no longer in the sample, and neither is black's.

## Hand-reviewed sample

Ten findings from the tables above, read against their diffs and marked plausible or implausible,
with one line of reasoning each. They cover all nine pull requests with findings and every
category with more than two, and were chosen from the list of findings before their diffs were
read. The harness generates the tables above but never a verdict here.

| # | Repository / symbol | Category | Verdict | Reasoning |
| --- | --- | --- | --- | --- |
| 1 | psf/black `Cache.read` (`#5641`) | `error_handling_change` (0.888) | **Plausible** | The `try` now covers opening the file too, and `OSError` replaces its subclass `PermissionError`, so any unreadable cache file is a miss instead of an exception. That is the category, and its obligations are the tests to write. |
| 2 | psf/black `_continues_fmt_skip_line` (`#5626`) | `output_contract_change` (0.879) | **Plausible** | The predicate now also requires `not _starts_statement(leaf)`, so it returns `False` for a leaf that starts a statement: the two `# fmt: skip` statements in a row that crashed. A changed return value is what changed. |
| 3 | psf/black `mask_cell` (`#5630`) | `boundary_change` (0.897) | **Plausible finding, wrong category** | The check that leaves a cell unformatted now compares the cell's lines, once its magics are put back, with the original's instead of comparing two line counts, so IPython help syntax and `>>>` prompts are skipped. It counts as a boundary only because the old side compared two `len()`s; what changed is which cells are rejected. |
| 4 | psf/black `BaseStringSplitter._iter_nameescape_slices` (`#5639`) | `unknown_semantic_change` (0.62) | **Implausible** | The inner loop's `idx, c` became `end_idx, end_char` so as not to shadow the outer loop's, and nothing reads either after the inner loop: behavior is unchanged. The rename shows as a changed comparison, condition, assignment, and loop. Before 2026-10-31 it was also a boundary and a retry. |
| 5 | encode/httpx `httpx/_auth.py` `<module>` (`#3699`) | `unknown_semantic_change` (0.68) | **Correct but low value** | `__all__` gained `FunctionAuth`, so a star import now exports it. Real and harmless: a reviewer needs one import test, not a review of what an assigned value means. |
| 6 | pallets/click `Command.main` (`#3851`) | `error_handling_change` (0.888) | **Plausible** | Every handler now hands its message and exit code to one teardown after the `try` instead of exiting itself, and `Exit` and `Abort` are caught with the others in the inner `try`. Each failure path takes a new route, in standalone and in non-standalone mode, and each deserves the test the obligations ask for. |
| 7 | pallets/click `_outside_click_stacklevel` (`#3877`) | `unknown_semantic_change` (0.68) | **Plausible finding, weak category** | The stack walk now also passes over the modules in the new `skip` parameter. That is a membership test, which this page's previous version reported as a boundary; `skip` is empty by default, so existing callers see no change, and the test to write — a caller passing `abc` — is one the category cannot name. |
| 8 | pallets/click `Path.__init__` (`#3876`) | `output_contract_change` (0.80) | **Correct but low value** | `path_type: type \| None` became `type[_PathT_co] \| None`: typing alone, and the finding now says so, at a lower confidence. Callers pass what they did; only a type checker sees a difference. |
| 9 | pallets/click `Argument.get_help_record` (`#3876`) | `output_contract_change` (0.879) | **Plausible** | The method no longer returns `None` for an argument without help, so `Command.format_arguments` lists every argument once one of them has help. The finding holds the return change and the narrowed return annotation and leads with the former; the removed `is None` check is a separate unknown change. |
| 10 | pallets/click `Option._parse_decls` (`#3866`) | `dependency_interaction_change` (0.714) | **Plausible finding, wrong category** | New calls to the class's own `_check_name_is_usable` and `_check_name_is_normalized` warn about names Click 9.0 will reject or spell differently. That is validation, as this page's previous review said of the same symbol; the call rule calls any changed call that no keyword explains a dependency. Since this run it is an unknown change, as the note below says. |

Four of the ten are plausible as presented, three are real changes under a category a reviewer would
dispute or that gives no help, two are correct but tell a reviewer little, and one is a rename
read as a change. No finding was fabricated: every one points at a real edit. The October review,
of a different sample, found two plausible and four implausible or redundant, and three of those
four were the boundary and loop misreadings corrected since.

## Where a category fires more often than a reviewer would expect

**`unknown_semantic_change` is half the findings: 38 of 76.** Fourteen are added symbols, nine are
changed assignments, and eleven are comparisons and conditions that are no longer called boundaries
— identity and membership tests, and equalities without a number. They are honest, but each tells
a reviewer only that something changed. The share rose because wrong categories became this one,
not because the analyzer reads less; lowering it needs categories that say more, such as an added
public symbol or a changed constant, rather than looser rules.

**`dependency_interaction_change` is any changed call that no keyword explains.** Of its 15
findings, 8 change calls to the symbol's own class or module — `self.get_help_spec(ctx)` for
`self.make_metavar(ctx)`, a new `self._check_name_is_usable(...)`, a helper such as
`_starts_statement` — and others add `t.TypeVar` declarations or a `warnings.warn`, or call the
same object under a new local name. The category's obligations ask how the code handles a
dependency's failures and unexpected responses, which none of these has.

*Corrected since this run.* A changed call is now an unknown change when every call that changed
reaches the symbol's own module: a method one of its classes defines, a function or class the
module defines, or a builtin that computes in-process, such as `len`. Measured again on these 20
pull requests, 5 of the 15 become unknown changes, finding 10 among them, which joins its symbol's
unknown change; `Parameter.human_readable_name`'s new `warnings.warn` becomes a side effect; and
click's `Option.__init__` moves from validation to dependency, its validation word having come
from a call that did not change. Each of the 10 dependency findings left changes a call into
another module, such as `textwrap.shorten` or `t.TypeVar`, or on a value whose type static
analysis does not know, such as `help.split()`. The tables above are this run's. See
`docs/decisions.md`, "Output-affecting corrections (2026-10-31, calls)".

**A boundary is an ordering, or an equality with a number or a length.** The three boundary
findings are a version gate (`sys.version_info >= (3, 13)`), the truncation in
`_make_default_short_help`, and finding 3, whose old side compared two `len()`s. A length compared
with another length is where the length rule reaches too far.

**A rename reads as a change.** Finding 4's loop variables, renamed to stop shadowing the outer
loop's, are reported as a changed comparison, condition, assignment, and loop, because the AST
comparison matches values as text.

**Pull requests that merge a branch carry the tail.** pallets/click#3876, `Merge stable into main`,
has 29 findings and pallets/click#3851, `Stable`, has 12: each brings a release branch's worth of
pull requests into one range. The median pull request has no finding, and the mean of 3.8 is
mostly these two.

**Typing-only signature changes say so.** Four signature changes in this sample change only
annotations. Two share a finding with a return change and give way to it, and two, `Path.__init__`
and `Path.convert`, are findings of their own, worded as typing alone at 0.80.

**Deterministic-mode latency is still not a concern.** Mean 0.38s and max 1.03s per pull request,
with no model calls.

## Reproducing

```bash
python scripts/validate_real_prs.py \
  --repo psf/requests --repo pallets/click --repo psf/black --repo encode/httpx \
  --count 5 --scratch /tmp/sdw-validation
```

Repositories are cloned with `--no-checkout` into the scratch directory, never into the working
tree. Nothing from them is installed or executed; the analyzer reads Git objects only. The script
writes this page with the two hand-written sections empty, so write it elsewhere with `--output`
and carry the tables over. The sample follows each repository's history, so a later run reviews
later pull requests.
