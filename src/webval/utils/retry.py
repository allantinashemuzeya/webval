"""Async retry with exponential backoff for flaky network/browser operations."""

from __future__ import annotations

import asyncio
import functools
from collections.abc import Awaitable, Callable
from typing import ParamSpec, TypeVar

from webval.utils.logging import get_logger

P = ParamSpec("P")
T = TypeVar("T")

log = get_logger("retry")


def retry_async(
    attempts: int = 3,
    backoff_s: float = 1.5,
    exceptions: tuple[type[BaseException], ...] = (Exception,),
) -> Callable[[Callable[P, Awaitable[T]]], Callable[P, Awaitable[T]]]:
    """Retry an async callable with exponential backoff.

    Delay sequence: backoff_s, backoff_s*2, backoff_s*4, ...
    The last failure is re-raised so callers convert it into a Status.ERROR
    result rather than crashing the run.
    """

    def decorator(fn: Callable[P, Awaitable[T]]) -> Callable[P, Awaitable[T]]:
        @functools.wraps(fn)
        async def wrapper(*args: P.args, **kwargs: P.kwargs) -> T:
            delay = backoff_s
            for attempt in range(1, attempts + 1):
                try:
                    return await fn(*args, **kwargs)
                except exceptions as exc:
                    if attempt == attempts:
                        raise
                    log.warning(
                        "%s failed (attempt %d/%d): %s — retrying in %.1fs",
                        fn.__qualname__,
                        attempt,
                        attempts,
                        exc,
                        delay,
                    )
                    await asyncio.sleep(delay)
                    delay *= 2
            raise RuntimeError("unreachable")  # pragma: no cover

        return wrapper

    return decorator
