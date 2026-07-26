# Release checklist

- [x] Choose and add an explicit license (MIT).
- [x] Run the full tests, lint, format, coverage, evaluation, and performance gates.
- [x] Build wheel and source distribution.
- [x] Inspect artifacts for caches, tests, secrets, local paths, metadata, and license inclusion.
- [x] Install the wheel in an isolated environment and inspect `hermes_agent.plugins` entry points.
- [x] Verify the `semantic-diff-weaver` console script is declared, importable, and runs `--help`
      from the installed environment (`scripts/verify_wheel.py` asserts all three).
- [x] Confirm `pyproject.toml`, `plugin.yaml`, and `semantic_diff_weaver.__version__` carry the
      same version; they must not drift. `tests/unit/test_regressions.py` pins the three together,
      after `__version__` was found reporting a stale release.
- [x] Parse `action.yml` and `.github/workflows/pr-review.yml`, and confirm every third-party
      action is pinned to a full commit SHA with a version comment.
- [x] Confirm the action's declared permissions are still exactly `contents: read` and
      `pull-requests: write`.
- [x] Run the full suite with no `ANTHROPIC_API_KEY` present; it must pass offline.
- [x] Record the lowest real Hermes release passing discovery tests (0.14.0).
- [x] Test the current Hermes release (0.18.2) through pip and directory discovery.
- [x] Update changelog and evaluation measurements if behavior changed.
- [x] Re-point the moving `v0` action tag at the new release commit and force-push it, so
      `uses: sergiparpal/semantic-diff-weaver@v0` resolves to what was just released. Skip only
      when the release does not change `action.yml` or anything it runs.
- [x] Publish or push only with separate authorization.
