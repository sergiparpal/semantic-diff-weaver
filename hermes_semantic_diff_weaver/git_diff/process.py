"""Bounded concurrent drain of untrusted Git child processes."""

from __future__ import annotations

import subprocess
import threading
import time
from pathlib import Path
from typing import BinaryIO

from .limits import GIT_TIMEOUT_SECONDS


class OutputLimitExceeded(Exception):
    """Raised when a Git child exceeds the configured stdout/stderr byte budget."""


# Backward-compatible private alias for tests and existing imports.
_OutputLimitExceeded = OutputLimitExceeded


def run_bounded_process(
    command: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
    input_data: bytes | None,
    max_bytes: int,
) -> subprocess.CompletedProcess[bytes]:
    """Drain both pipes concurrently and stop the child as soon as either cap is exceeded."""
    process = subprocess.Popen(
        command,
        cwd=cwd,
        env=env,
        shell=False,
        stdin=subprocess.PIPE if input_data is not None else subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        bufsize=0,
    )
    stdout = bytearray()
    stderr = bytearray()
    overflow = threading.Event()
    reader_errors: list[OSError] = []

    def drain(stream: BinaryIO, destination: bytearray) -> None:
        try:
            while chunk := stream.read(64 * 1024):
                remaining = max_bytes - len(destination)
                if len(chunk) > remaining:
                    destination.extend(chunk[: max(0, remaining)])
                    overflow.set()
                    process.kill()
                    return
                destination.extend(chunk)
        except OSError as exc:
            reader_errors.append(exc)

    if process.stdout is None or process.stderr is None:
        process.kill()
        raise OSError("Git process pipes were unavailable")
    readers = [
        threading.Thread(target=drain, args=(process.stdout, stdout), daemon=True),
        threading.Thread(target=drain, args=(process.stderr, stderr), daemon=True),
    ]
    for thread in readers:
        thread.start()

    writer: threading.Thread | None = None
    if input_data is not None:
        if process.stdin is None:
            process.kill()
            raise OSError("Git process stdin was unavailable")
        process_stdin = process.stdin

        def write_input() -> None:
            try:
                process_stdin.write(input_data)
                process_stdin.close()
            except BrokenPipeError:
                pass
            except OSError as exc:
                reader_errors.append(exc)

        writer = threading.Thread(target=write_input, daemon=True)
        writer.start()

    deadline = time.monotonic() + GIT_TIMEOUT_SECONDS
    try:
        while process.poll() is None:
            if overflow.is_set():
                process.kill()
                break
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                process.kill()
                raise subprocess.TimeoutExpired(command, GIT_TIMEOUT_SECONDS)
            try:
                # Prefer a longer wait so short Git commands are not paced by a 50ms poll.
                process.wait(timeout=min(0.25, remaining))
            except subprocess.TimeoutExpired:
                continue
    finally:
        if process.poll() is None:
            process.kill()
        process.wait()
        for thread in readers:
            thread.join(timeout=1)
        if writer is not None:
            writer.join(timeout=1)
    if overflow.is_set():
        raise OutputLimitExceeded
    if reader_errors:
        raise reader_errors[0]
    return subprocess.CompletedProcess(command, process.returncode, bytes(stdout), bytes(stderr))


# Backward-compatible private alias.
_run_bounded_process = run_bounded_process
