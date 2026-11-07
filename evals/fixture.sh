#!/usr/bin/env bash
# Builds an eval case's repository in the current directory, the run's working directory:
# `bash fixture.sh <variant>`. Each case's scaffold.sh calls it with the case's variant.
#
# Every variant has a package whose limit a feature branch makes inclusive, the change under
# review, checked out at the feature's head, and a test that checks only below the limit.
#
#   plain        main ── A ── C feature/limits (HEAD)
#   stale-main   main ── A                      local main, behind its remote copy
#                origin/main ── A ── B          B changed pkg/report.py, merged already
#                feature/limits ── B ── C (HEAD)
#   diverged     main ── A ── D                 a local commit the remote never got
#                origin/main ── A ── B
#                feature/limits ── B ── C (HEAD)
#   uncommitted  plain, plus an edit to pkg/report.py left uncommitted
#   large        plain, with C changing 105 modules in three directories, past the analysis's
#                40-file limit, which each directory alone stays under
#   elsewhere    plain, built in /tmp/sdw-eval-elsewhere, outside the run's working directory,
#                which holds no repository (runs of this case must not overlap)
#
# Remote-tracking refs are written directly: no remote, fetch, or network is involved.
set -euo pipefail

variant="${1:-plain}"
if [ "${variant}" = elsewhere ]; then
  rm -rf /tmp/sdw-eval-elsewhere
  mkdir -p /tmp/sdw-eval-elsewhere
  cd /tmp/sdw-eval-elsewhere
fi
export GIT_AUTHOR_NAME="Eval" GIT_AUTHOR_EMAIL="eval@example.invalid"
export GIT_COMMITTER_NAME="Eval" GIT_COMMITTER_EMAIL="eval@example.invalid"
export GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_SYSTEM=/dev/null
# Fixed dates make every commit, and so every SHA a grader names, the same on every run.
export GIT_AUTHOR_DATE="2026-11-01T12:00:00Z" GIT_COMMITTER_DATE="2026-11-01T12:00:00Z"

module() {  # module <path> <comparison>: a function that checks a limit
  mkdir -p "$(dirname "$1")"
  printf 'LIMIT = 5\n\n\ndef allowed(count: int) -> bool:\n    return count %s LIMIT\n' "$2" > "$1"
}

git init -q -b main .
mkdir -p pkg tests
: > pkg/__init__.py
cat > pkg/limits.py <<'PY'
MAX_ITEMS = 5


def allowed(count: int) -> bool:
    """Whether a basket of `count` items may be checked out."""
    return count < MAX_ITEMS
PY
cat > pkg/report.py <<'PY'
def summarize(values: list[int]) -> str:
    return ", ".join(str(value) for value in values)
PY
: > tests/__init__.py
cat > tests/test_limits.py <<'PY'
from pkg.limits import allowed


def test_a_small_basket_is_allowed():
    assert allowed(4)
PY
if [ "${variant}" = large ]; then
  for area in billing shipping scripts; do
    for index in $(seq -w 1 35); do
      module "${area}/check_${index}.py" "<"
    done
  done
fi
git add -A
git commit -q -m "Add the basket limit"
base="$(git rev-parse HEAD)"

case "${variant}" in
  stale-main | diverged)
    sed -i 's/", "\.join/"; ".join/' pkg/report.py
    git commit -q -am "Separate the summary with semicolons"
    git update-ref refs/remotes/origin/main HEAD
    git symbolic-ref refs/remotes/origin/HEAD refs/remotes/origin/main
    git reset -q --hard "${base}"
    if [ "${variant}" = diverged ]; then
      printf '\n\ndef count_items(values: list[int]) -> int:\n    return len(values)\n' >> pkg/report.py
      git commit -q -am "Count the summary's items"
    fi
    git switch -q -c feature/limits origin/main
    ;;
  *)
    git switch -q -c feature/limits
    ;;
esac

sed -i 's/return count < MAX_ITEMS/return count <= MAX_ITEMS/' pkg/limits.py
if [ "${variant}" = large ]; then
  for area in billing shipping scripts; do
    for index in $(seq -w 1 35); do
      module "${area}/check_${index}.py" "<="
    done
  done
fi
git commit -q -am "Let a basket of exactly MAX_ITEMS through"

if [ "${variant}" = uncommitted ]; then
  sed -i 's/", "\.join/" | ".join/' pkg/report.py
fi
