"""One request per process. stdin JSON line; stdout JSON response only."""
from __future__ import annotations

import json
import sys

from .service import handle


def main():
    raw = sys.stdin.buffer.readline(1_100_001)
    if len(raw) > 1_100_000:
        response = {"version": 1, "ok": False, "call_id": None, "result": None,
                    "error": {"code": "too_large", "message": "Document request exceeds the input limit"}}
    else:
        try:
            request = json.loads(raw)
        except (ValueError, UnicodeError):
            request = None
        response = handle(request)
    sys.stdout.buffer.write((json.dumps(response, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8"))
    sys.stdout.buffer.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
