# Security Policy

## Reporting a vulnerability

Please report security issues privately through
[GitHub Security Advisories](https://github.com/sergiparpal/semantic-diff-weaver/security/advisories/new)
rather than opening a public issue.

Expect an initial response within 7 days. If a report is confirmed, the fix and the advisory
are published together.

## Supported versions

Only the latest release on `main` receives security fixes.

## Scope

This project ships as a CLI, a **GitHub Action**, and a Hermes plugin. The Action is the part
that runs in someone else's CI with a token, so it carries the most weight here.

The analyzer is read-only by design: it reads committed Git objects and **never executes or
modifies the target repository**. A report of code execution against an analyzed repository is
therefore always in scope and always a defect.

The parts most worth scrutiny are therefore:

- **Action token handling** — the Action requests `pull-requests: write` in order to comment.
  Anything that widens that, leaks the token, or lets analyzed content influence what is done
  with it is in scope.
- **Untrusted diff content** — analyzed code is attacker-controlled when the pull request comes
  from a fork. Anything where it reaches execution, or is injected unescaped into a posted
  comment or a workflow command, is in scope.
- **Action pinning policy** — `scripts/check_action_pins.py` enforces that workflows and the
  YAML quoted in the docs stay pinned to verified commit SHAs. A way to defeat that check is in
  scope.
- **Dependency supply chain** — `requirements-dev.lock` and the built wheel.

Out of scope: false positives and false negatives in the semantic analysis, the ranked risk or
confidence of a finding, and the usefulness of a generated test obligation.
