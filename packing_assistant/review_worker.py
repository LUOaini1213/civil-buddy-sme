"""Pure deterministic review adapter; no model, file writes or nested runtime."""
from __future__ import annotations

from packing_assistant.tools.verdict_guard import stated_verdicts, strike, notice


def handle(request):
    call_id = request.get("call_id") if isinstance(request, dict) else None
    if not isinstance(request, dict) or request.get("version") != 1 or request.get("operation") != "verdicts":
        return {"version": 1, "call_id": call_id, "ok": False, "result": None,
                "error": {"code": "invalid_request", "message": "Only version 1 verdict review is supported"}}
    texts = request.get("texts")
    if not isinstance(texts, list) or len(texts) > 200 or any(not isinstance(t, str) for t in texts) or sum(len(t) for t in texts) > 500_000:
        return {"version": 1, "call_id": call_id, "ok": False, "result": None,
                "error": {"code": "invalid_request", "message": "Review accepts at most 200 bounded text values"}}
    results = []
    for text in texts:
        found = stated_verdicts(text)
        results.append({"found": found, "text": strike(text, found), "notice": notice(found)})
    return {"version": 1, "call_id": call_id, "ok": True, "result": {"results": results}, "error": None}
