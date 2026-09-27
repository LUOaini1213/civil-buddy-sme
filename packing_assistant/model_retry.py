"""Bounded retry for one model request (Chat Completions), shared by every Python caller.

A model request has no side effects: it reads the conversation and returns text or tool calls. Retrying it never
re-runs a tool, because the caller only runs tools after this returns. So a transient failure is retried here,
and nowhere else:

* at most ``MAX_RETRIES`` = 2 retries (3 attempts; ``CIVIL_MODEL_RETRIES`` can lower it to 0 or 1);
* only on HTTP 429, HTTP 5xx, a connect or read timeout, and a connection reset / dropped connection -
  never on another 4xx (a bad key, model name or request stays wrong however often it is sent) and never on a
  refused connection (a dead endpoint);
* exponential backoff with full jitter: retry n waits ``uniform(0, min(CAP_S, BASE_S * 2**n))``;
* ``Retry-After`` (seconds or an HTTP date) is honoured instead, up to ``RETRY_AFTER_CAP_S``; a longer ask is not
  retried, it is reported;
* everything - attempts and waits - fits in the caller's existing timeout (``budget_s``): a retry is only made
  when at least ``MIN_ATTEMPT_S`` is left, and each attempt's timeout is clipped to what is left. With the
  default settings a read timeout has already used the whole budget, so a hung endpoint is still bounded by it.

Each retry is logged (``civil.model_retry``) with the reason and the upstream error body cut to 300 characters
with the key and bearer-like tokens removed (``safe_excerpt``).
"""

from __future__ import annotations

import logging
import os
import random
import re
import time
from email.utils import parsedate_to_datetime
from typing import Any, Callable, Iterable, Optional, TypeVar

logger = logging.getLogger("civil.model_retry")

MAX_RETRIES = 2
BASE_S = 1.0
CAP_S = 8.0
RETRY_AFTER_CAP_S = 8.0
MIN_ATTEMPT_S = 1.0
EXCERPT_CHARS = 300

T = TypeVar("T")


class Transient(Exception):
    """Raised by an attempt for a failure worth retrying. ``final`` is what the caller raises if it is not: an
    exception, or a function of the number of attempts made that returns one."""

    def __init__(self, final: Any, *, reason: str, retry_after: Optional[float] = None,
                 excerpt: str = "") -> None:
        super().__init__(reason)
        self.final = final
        self.reason = reason
        self.retry_after = retry_after
        self.excerpt = excerpt


def max_retries() -> int:
    raw = (os.getenv("CIVIL_MODEL_RETRIES") or "").strip()
    try:
        return min(MAX_RETRIES, max(0, int(raw))) if raw else MAX_RETRIES
    except ValueError:
        return MAX_RETRIES


def retryable_status(status: int) -> bool:
    return status == 429 or 500 <= status <= 599


def parse_retry_after(value: Any, *, now: Optional[float] = None) -> Optional[float]:
    """Seconds to wait from a Retry-After header (delta-seconds or HTTP date); None if absent or unreadable."""
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return max(0.0, float(text))
    except ValueError:
        pass
    try:
        when = parsedate_to_datetime(text)
    except (TypeError, ValueError, IndexError):
        return None
    if when is None:
        return None
    return max(0.0, when.timestamp() - (time.time() if now is None else now))


def backoff(retry_index: int, *, rng: Optional[Callable[[], float]] = None) -> float:
    """Full jitter: uniform in [0, min(CAP_S, BASE_S * 2**retry_index))."""
    return (rng or random.random)() * min(CAP_S, BASE_S * (2 ** retry_index))


_TOKENISH = re.compile(r"(?i)(bearer\s+)[^\s\"',;]+|\b(sk|pk|rk|key|tok)-[A-Za-z0-9_\-]{8,}|\b[A-Za-z0-9_\-]{32,}\b")


def safe_excerpt(text: Any, secrets: Iterable[str] = (), limit: int = EXCERPT_CHARS) -> str:
    """An upstream error body fit to show: configured secrets and token-like strings removed, whitespace
    collapsed, cut to ``limit`` characters."""
    out = str(text or "")
    for secret in secrets:
        if secret and len(secret) >= 4:
            out = out.replace(secret, "***")
    out = _TOKENISH.sub(lambda m: (m.group(1) or "") + "***", out)
    out = " ".join(out.split())
    return out if len(out) <= limit else out[: limit - 1] + "…"


def read_body(response: Any, limit: int = 4096) -> str:
    """At most ``limit`` bytes of an httpx streaming response's body, decoded leniently; '' on any error."""
    chunks, size = [], 0
    try:
        for chunk in response.iter_bytes():
            chunks.append(chunk)
            size += len(chunk)
            if size >= limit:
                break
    except Exception:  # noqa: BLE001 - the body is a courtesy, the status already says what failed
        pass
    return b"".join(chunks)[:limit].decode("utf-8", "replace")


def run(attempt: Callable[[float], T], *, budget_s: float, label: str,
        wait: Optional[Callable[[float], None]] = None, retries: Optional[int] = None,
        rng: Optional[Callable[[], float]] = None, clock: Callable[[], float] = time.monotonic) -> T:
    """Call ``attempt(remaining_s)`` until it returns, raises a non-Transient error, or retries run out.

    ``wait(seconds)`` sleeps between attempts and must raise if the turn is cancelled meanwhile (the default is
    time.sleep). On give-up the attempt's ``final`` exception is raised."""
    retries = max_retries() if retries is None else retries
    start = clock()
    sleep = wait or time.sleep
    n = 0
    while True:
        remaining = budget_s - (clock() - start)
        try:
            return attempt(max(remaining, MIN_ATTEMPT_S))
        except Transient as failure:
            left = budget_s - (clock() - start)
            if n >= retries:
                why = f"no retries left ({retries})"
            elif failure.retry_after is not None and failure.retry_after > RETRY_AFTER_CAP_S:
                why = f"Retry-After {failure.retry_after:.0f} s is over the {RETRY_AFTER_CAP_S:.0f} s cap"
            else:
                delay = failure.retry_after if failure.retry_after is not None else backoff(n, rng=rng)
                if left - delay >= MIN_ATTEMPT_S:
                    n += 1
                    logger.warning("%s: attempt %d/%d failed (%s)%s; retrying in %.2f s", label, n, retries + 1,
                                   failure.reason, f" - upstream said: {failure.excerpt}" if failure.excerpt else "",
                                   delay)
                    sleep(delay)
                    continue
                why = f"only {max(left, 0.0):.1f} s of the {budget_s:.0f} s budget left"
            logger.warning("%s: attempt %d failed (%s)%s; not retrying: %s", label, n + 1, failure.reason,
                           f" - upstream said: {failure.excerpt}" if failure.excerpt else "", why)
            final = failure.final(n + 1) if callable(failure.final) else failure.final
            raise final from None
