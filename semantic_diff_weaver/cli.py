"""The standalone command line: the same analysis without a Hermes host.

The one substantive difference from the plugin path is authorization. `path_policy`
defaults its authorized roots to the process working directory because, under Hermes, a
*model* chooses `repo_path` and containment is the only thing standing between an
instructed model and the rest of the filesystem. On a command line a *person* types the
path, and that typing is the authorization. So an unset `SEMANTIC_DIFF_WEAVER_ALLOWED_ROOTS`
is populated from `--repo` and `--allow-root`.

An operator who has already set that variable has expressed a narrower intent than the
invocation, and the CLI never widens it: `--allow-root` is refused rather than merged, and
a `--repo` outside the operator's bound fails exactly as it does today.
"""

from __future__ import annotations

import argparse
import json
import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from .errors import as_public_error
from .llm_client import LlmClient
from .models import MAX_PATH_PATTERNS, OutputFormat, RiskLabel
from .path_policy import ALLOWED_ROOTS_ENV

PROGRAM = "semantic-diff-weaver"

EXIT_SUCCESS = 0
EXIT_ANALYSIS_ERROR = 1
EXIT_ARGUMENT_ERROR = 2
EXIT_RISK_THRESHOLD = 3

FAIL_ON_CHOICES = ("none", "low", "medium", "high", "critical")
# Ascending, so a threshold is met when the overall risk sorts at or above it.
RISK_ORDER = (RiskLabel.LOW, RiskLabel.MEDIUM, RiskLabel.HIGH, RiskLabel.CRITICAL)


class ArgumentError(Exception):
    """A caller-fixable problem with the command line itself."""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=PROGRAM,
        description=(
            "Analyze the behavioral meaning of a committed Git diff and emit risk-ranked "
            "test obligations. Read-only: never executes, imports, builds, or modifies the "
            "analyzed repository."
        ),
    )
    parser.add_argument("--repo", default=".", metavar="PATH", help="repository to analyze")
    parser.add_argument("--base", required=True, metavar="REF", help="base revision")
    parser.add_argument("--head", default="HEAD", metavar="REF", help="head revision")
    parser.add_argument(
        "--include", action="append", metavar="GLOB", help="repeatable include pattern"
    )
    parser.add_argument(
        "--exclude", action="append", metavar="GLOB", help="repeatable exclude pattern"
    )
    parser.add_argument("--risk-profile", metavar="PATH", help="additional configuration file")
    parser.add_argument(
        "--coverage",
        metavar="PATH",
        help=(
            "coverage.py JSON or lcov .info report to ground candidate coverage in; "
            "read as untrusted data and never executed"
        ),
    )
    parser.add_argument(
        "--format",
        default="markdown",
        choices=("json", "markdown", "both"),
        help="markdown brief, canonical analysis JSON, or the full envelope",
    )
    parser.add_argument(
        "--allow-root",
        action="append",
        metavar="PATH",
        default=None,
        help=(
            "additional authorized root; refused when "
            f"{ALLOWED_ROOTS_ENV} is already set by the operator"
        ),
    )
    parser.add_argument(
        "--fail-on",
        default="none",
        choices=FAIL_ON_CHOICES,
        help=f"exit {EXIT_RISK_THRESHOLD} when overall risk reaches this level",
    )
    parser.add_argument(
        "--model",
        metavar="ID",
        default=None,
        help=(
            "inference model; overrides SEMANTIC_DIFF_WEAVER_MODEL. Without a provider the "
            "analysis runs in deterministic mode"
        ),
    )
    parser.add_argument(
        "--no-llm",
        action="store_true",
        help="skip inference entirely and report deterministic structural findings only",
    )
    return parser


def _meets_threshold(overall_risk: str, fail_on: str) -> bool:
    if fail_on == "none":
        return False
    labels = [item.value for item in RISK_ORDER]
    if overall_risk not in labels:
        return False
    return labels.index(overall_risk) >= labels.index(fail_on)


def _resolve_root(value: str, *, label: str) -> Path:
    try:
        resolved = Path(value).expanduser().resolve(strict=True)
    except OSError as exc:
        raise ArgumentError(f"{label} {value!r} is not an accessible path.") from exc
    if not resolved.is_dir():
        raise ArgumentError(f"{label} {value!r} is not a directory.")
    if resolved == Path(resolved.anchor):
        # `path_policy.authorized_roots` refuses this too; failing here names the flag.
        raise ArgumentError(f"{label} {value!r} is a filesystem root and cannot authorize access.")
    return resolved


@contextmanager
def _authorization(repo: str, allow_roots: list[str]) -> Iterator[None]:
    """Populate the authorized roots from the invocation, never widening an operator's."""
    configured = os.environ.get(ALLOWED_ROOTS_ENV)
    if configured is not None:
        if allow_roots:
            raise ArgumentError(
                f"--allow-root cannot widen {ALLOWED_ROOTS_ENV}, which is already set. "
                "Unset it, or add the path to it directly."
            )
        yield
        return
    roots = [_resolve_root(repo, label="--repo")]
    roots.extend(_resolve_root(value, label="--allow-root") for value in allow_roots)
    os.environ[ALLOWED_ROOTS_ENV] = os.pathsep.join(dict.fromkeys(str(item) for item in roots))
    try:
        yield
    finally:
        os.environ.pop(ALLOWED_ROOTS_ENV, None)


def _request(namespace: argparse.Namespace) -> dict[str, Any]:
    request: dict[str, Any] = {
        "repo_path": namespace.repo,
        "base_ref": namespace.base,
        "head_ref": namespace.head,
        # Always analyzed as `both` so `--fail-on` can read the overall risk regardless of
        # what the caller asked to be printed. The JSON below is the same object either way.
        "output_format": OutputFormat.BOTH.value,
    }
    for name, values in (("include", namespace.include), ("exclude", namespace.exclude)):
        if values is None:
            continue
        if len(values) > MAX_PATH_PATTERNS:
            raise ArgumentError(f"--{name} accepts at most {MAX_PATH_PATTERNS} patterns.")
        request[name] = values
    if namespace.risk_profile is not None:
        request["risk_profile"] = namespace.risk_profile
    if namespace.coverage is not None:
        request["coverage_report"] = namespace.coverage
    return request


def _emit(envelope: dict[str, Any], output_format: str, stream: Any) -> None:
    if output_format == "markdown":
        print(envelope["markdown"], file=stream)
        return
    payload = envelope if output_format == "both" else envelope["analysis"]
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), file=stream)


def main(argv: list[str] | None = None) -> int:
    """Run one analysis. See the module docstring for the authorization rule."""
    import sys

    parser = build_parser()
    try:
        namespace = parser.parse_args(argv)
        request = _request(namespace)
    except ArgumentError as exc:
        print(f"{PROGRAM}: {exc}", file=sys.stderr)
        return EXIT_ARGUMENT_ERROR
    except SystemExit as exc:
        # argparse already reported the problem on stderr.
        return EXIT_SUCCESS if exc.code in (0, None) else EXIT_ARGUMENT_ERROR

    llm, notice = load_llm(namespace)
    if notice:
        print(f"{PROGRAM}: {notice}", file=sys.stderr)

    try:
        with _authorization(namespace.repo, list(namespace.allow_root or ())):
            from .service import analyze

            envelope = analyze(request, llm=llm)
    except ArgumentError as exc:
        print(f"{PROGRAM}: {exc}", file=sys.stderr)
        return EXIT_ARGUMENT_ERROR
    except Exception as exc:
        # Deliberately broad: every failure is flattened to the stable public error payload,
        # which never echoes an unrecognized exception's text. Same rule as the Hermes handler.
        error = as_public_error(exc)
        print(f"{PROGRAM}: {error['error']}: {error['message']}", file=sys.stderr)
        print(f"{PROGRAM}: {error['remediation']}", file=sys.stderr)
        return EXIT_ANALYSIS_ERROR

    _emit(envelope, namespace.format, sys.stdout)
    overall_risk = str(envelope["analysis"]["summary"]["overall_risk"])
    if _meets_threshold(overall_risk, namespace.fail_on):
        print(
            f"{PROGRAM}: overall risk {overall_risk} reaches --fail-on {namespace.fail_on}.",
            file=sys.stderr,
        )
        return EXIT_RISK_THRESHOLD
    return EXIT_SUCCESS


def load_llm(namespace: argparse.Namespace) -> tuple[LlmClient | None, str | None]:
    """Resolve an optional inference provider. Deterministic mode is never a failure.

    Imported lazily so the base install stays dependency-light: a missing package or a
    missing credential returns a notice, never an exception.
    """
    if getattr(namespace, "no_llm", False):
        return None, None
    from .providers.anthropic_client import AnthropicClient

    return AnthropicClient.from_environment(model=getattr(namespace, "model", None))
