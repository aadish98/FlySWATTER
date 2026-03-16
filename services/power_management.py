"""Best-effort sleep prevention while long analyses are running."""

from __future__ import annotations

import contextlib
import ctypes
import platform
import subprocess
from typing import Iterator, Optional


class _WindowsExecutionState:
    ES_CONTINUOUS = 0x80000000
    ES_SYSTEM_REQUIRED = 0x00000001
    ES_AWAYMODE_REQUIRED = 0x00000040


@contextlib.contextmanager
def prevent_sleep() -> Iterator[None]:
    system = platform.system()
    proc: Optional[subprocess.Popen] = None
    previous_state = None
    try:
        if system == "Darwin":
            proc = subprocess.Popen(
                ["caffeinate", "-dimsu"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        elif system == "Windows":
            kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
            previous_state = kernel32.SetThreadExecutionState(
                _WindowsExecutionState.ES_CONTINUOUS
                | _WindowsExecutionState.ES_SYSTEM_REQUIRED
                | _WindowsExecutionState.ES_AWAYMODE_REQUIRED
            )
        yield
    finally:
        if proc is not None and proc.poll() is None:
            proc.terminate()
        if system == "Windows":
            try:
                kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
                kernel32.SetThreadExecutionState(_WindowsExecutionState.ES_CONTINUOUS)
            except Exception:
                pass
