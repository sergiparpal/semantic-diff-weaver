# Real pull-request validation

Generated 2026-07-25 by `scripts/validate_real_prs.py`, deterministic mode (`--no-llm`), up to 4 recent merges per repository.

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
| Findings per PR (mean) | 21.64 |
| Findings per PR (median) | 4.0 |
| Findings per PR (max) | 166 |
| PRs with zero findings | 5 |
| Mean confidence | 0.751 |
| Wall clock per PR (mean) | 0.19s |
| Wall clock per PR (max) | 0.84s |

## Category distribution

| Category | Count |
| --- | --- |
| `unknown_semantic_change` | 116 |
| `state_transition_change` | 48 |
| `dependency_interaction_change` | 38 |
| `boundary_change` | 37 |
| `output_contract_change` | 30 |
| `side_effect_change` | 21 |
| `retry_timeout_change` | 9 |
| `error_handling_change` | 4 |

## Risk distribution

| Risk | Count |
| --- | --- |
| high | 6 |
| medium | 297 |

## Per-merge detail

| Repository | Merge | Files | Findings | Obligations | Overall risk | Seconds |
| --- | --- | --- | --- | --- | --- | --- |
| psf/requests | `514b3f2345e5` | 1 | 0 | 0 | low | 0.036 |
| psf/requests | `7112775514a8` | 1 | 0 | 0 | low | 0.01 |
| psf/requests | `968863678bc0` | 1 | 0 | 0 | low | 0.011 |
| psf/requests | `42eaeb4da873` | 3 | 0 | 0 | low | 0.01 |
| pallets/click | `0f4738df88e3` | 11 | 1 | 1 | medium | 0.52 |
| pallets/click | `0c9e836c7c22` | 7 | 0 | 0 | low | 0.01 |
| pallets/click | `b3a191bf58db` | 27 | 166 | 20 | medium | 0.844 |
| pallets/click | `10b43c211b82` | 7 | 44 | 17 | medium | 0.381 |
| psf/black | `68cc9786d3db` | 6 | 3 | 4 | medium | 0.177 |
| psf/black | `3c62b9cada3f` | 8 | 50 | 20 | medium | 0.178 |
| encode/httpx | `c7af2b1a5d1f` | 4 | 10 | 15 | high | 0.126 |
| encode/httpx | `5961bd8ba3e1` | 5 | 17 | 15 | high | 0.154 |
| encode/httpx | `c2eeb662dfcc` | 2 | 7 | 10 | high | 0.096 |
| encode/httpx | `d0953d1d863a` | 2 | 5 | 9 | medium | 0.103 |

## Hand-reviewed sample

Ten findings read against their diffs, marked plausible or implausible with one line of
reasoning. These were reviewed by hand; the harness generates the tables above but never a
verdict here.

| # | Repository / symbol | Category | Verdict | Reasoning |
| --- | --- | --- | --- | --- |
| 1 | psf/black `is_python36` | `boundary_change` (0.897) | **Implausible** | The change is `ch.type == token.STAR or ch.type == token.DOUBLESTAR` → `ch.type in STARS`, where `STARS = {token.STAR, token.DOUBLESTAR}`. Semantics are identical; this is a pure refactor reported as a behavior change at high confidence. |
| 2 | encode/httpx `RedirectMiddleware.redirect_headers` | `output_contract_change` (0.879) | **Plausible finding, wrong category** | A parameter really was removed (`(self, request, url, method)` → `(self, request, url)`), so flagging it is right. But that is an *input* contract change; `output_contract_change` reads as a changed return value. |
| 3 | encode/httpx `RedirectMiddleware.redirect_headers` | `boundary_change` (0.897) | **Plausible finding, wrong category** | Two guards were genuinely deleted (`method != request.method`, `method == 'GET'`), which is worth review. They are equality tests on a method string, not boundaries — the comparison-operator probe reads `==`/`!=` as boundary evidence regardless of operand type. |
| 4 | encode/httpx `Stream.get_http_version` | `output_contract_change` (0.879) | **Plausible** | A new return path was added (`'HTTP/1.1'` before the existing conditional). A changed set of return values on a version-negotiation method is exactly what a reviewer wants surfaced. |
| 5 | encode/httpx `SSLConfig._create_default_ssl_context` | `unknown_semantic_change` (0.68) | **Plausible finding, weak category** | `ssl.HAS_NPN` was dropped from the guarded conditions — real, and security-adjacent. A reviewer would call it a dependency or compatibility change; `unknown_semantic_change` gives no help deciding how to test it. |
| 6 | encode/httpx `MockDispatch.send` (in `tests/`) | `side_effect_change` (0.726, **high** risk) | **Implausible as presented** | The change is real, but the symbol is a test double inside `tests/client/test_redirects.py`. Reporting a change to test code as a high-risk side effect needing new test obligations inverts the tool's purpose. |
| 7 | encode/httpx `test_body_redirect` (in `tests/`) | `boundary_change` (0.897) | **Implausible as presented** | An assertion was rewritten (`response.json()['body']` → `response.json() ==`). Correct as a description, but a changed assertion in a test file is not a behavior change needing its own obligation. |
| 8 | psf/black `tests/expression.py` `<unparsed>` | `unknown_semantic_change` (0.52) | **Correct but noisy** | The file genuinely does not parse — it is a black fixture containing deliberately unusual syntax. Fail-closed reporting is right, but it surfaces as a finding rather than as scope metadata. |
| 9 | encode/httpx `example.py` `<module>` | `unknown_semantic_change` (0.69) | **Correct but low value** | An example script was deleted; `symbol_removed` on its module is accurate. A reviewer does not need a test obligation for a removed example. |
| 10 | pallets/click `split_arg_string` | `unknown_semantic_change` (0.665, `unknown_structure`) | **Plausible** | The function body did change in a way the AST comparison could not classify, and the report says exactly that (`body changed`) at the lowest confidence in the sample. Honest under-claiming rather than a guess. |

Four of the ten are implausible or misdirected as presented, three are plausible findings under
a category a reviewer would dispute, and three are plausible and useful. No finding in the
sample was fabricated — every one pointed at a real edit, which is consistent with the
evidence-anchor result in `docs/evaluation.md`. The recurring problem is *classification and
relevance*, not invention.

## Where a category fires more often than a reviewer would expect

**`unknown_semantic_change` dominates: 116 of 303 findings (38%).** On the synthetic corpus it
is a rare fallback; on real diffs it is the single largest category. Every such finding costs a
reviewer attention while telling them only that something changed. This is the strongest signal
in the run and the first thing to work on.

**Test files are analyzed as changed source and produce their own findings and obligations.**
The default `paths.exclude` covers `**/migrations/**`, `**/generated/**`, and `**/vendor/**`,
but not test roots — so a PR that touches tests generates high-risk `side_effect_change` and
`boundary_change` findings about the tests themselves, complete with obligations to write tests
for the tests. In the httpx sample this accounted for 3 of 10 findings. Anyone enabling the
Action should set `include` to their source tree; the dogfooding workflow in this repository
already does.

**`boundary_change` fires on any `==`/`!=`, not only on ordering comparisons.** Findings 1, 3,
and 7 are all equality tests on enums, strings, or dicts. The category name promises off-by-one
and range reasoning, and a reviewer reads it that way.

**Findings-per-PR has a long tail: mean 21.6 against a median of 4.** One 27-file click merge
produced 166 findings. Median 4 is a plausible review load; the tail is not, and a per-PR cap or
a stricter default `minimum_report_confidence` would matter more in practice than the mean
suggests. Five of fourteen merges produced zero findings, which is the correct answer for
documentation-only and dependency-bump merges.

**Deterministic-mode latency is not a concern.** Mean 0.19s and max 0.84s per merge, well inside
the performance budget, with no model calls.

## Reproducing

```bash
python scripts/validate_real_prs.py \
  --repo psf/requests --repo pallets/click --repo psf/black --repo encode/httpx \
  --count 4 --scratch /tmp/sdw-validation
```

Repositories are cloned with `--no-checkout` into the scratch directory, never into the working
tree. Nothing from them is installed or executed; the analyzer reads Git objects only.
