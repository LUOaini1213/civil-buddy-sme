from __future__ import annotations

import hashlib
import math
from pathlib import Path


class DocumentError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def fail(code: str, message: str):
    raise DocumentError(code, message)


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def bounded(workspace: Path, value: str) -> Path:
    if not isinstance(value, str) or not value or "\0" in value:
        fail("invalid_request", "A nonempty workspace-relative path is required")
    candidate = Path(value)
    target = (candidate if candidate.is_absolute() else workspace / candidate).resolve()
    if not target.is_relative_to(workspace):
        fail("path_denied", "Document paths must stay within the workspace")
    return target


def text(value, *, maximum=100_000) -> str:
    if not isinstance(value, str) or len(value) > maximum:
        fail("invalid_patch", "Text must be a string within the operation limit")
    if any(ord(c) < 32 and c not in "\n\t\r" for c in value):
        fail("invalid_patch", "Control characters are not supported")
    return value


def integer(value, minimum=0, maximum=10_000) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        fail("invalid_request", "Integer locator is outside the supported bounds")
    return value


def number(value):
    if type(value) not in (int, float) or not math.isfinite(value) or abs(value) > 1e100:
        fail("invalid_patch", "A finite bounded number is required")
    return value


def fields(value, required, optional=()):
    if not isinstance(value, dict) or not set(required) <= value.keys() or set(value) - set(required) - set(optional):
        fail("invalid_patch", "Operation has missing or unsupported fields")


def validation(**extra):
    return {"structure": "pass", "render": "not_checked", "engineering_facts": "not_checked", **extra}
