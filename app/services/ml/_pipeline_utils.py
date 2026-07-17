"""Shared stateless helpers used across the data pipeline services."""

from __future__ import annotations

import time
from typing import Any


def _is_control_signal_exception(exc: Exception) -> bool:
    """Return True when an exception is a worker pause/cancel control signal."""
    return exc.__class__.__name__ in {"JobPausedError", "JobCancelledError"}


def _sleep_with_abort(seconds: float, *, abort_cb: Any = None) -> None:
    if seconds <= 0:
        return
    remaining = float(seconds)
    step = 1.0
    while remaining > 0:
        if abort_cb:
            abort_cb()
        time.sleep(min(step, remaining))
        remaining -= step
