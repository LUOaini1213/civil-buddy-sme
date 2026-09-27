#!/usr/bin/env python3
"""Does a model endpoint answer, and does a tool call come back as a tool call? Two calls, nothing else.

Call 1 sends one SYNTHETIC sentence with no tools; call 2 the same kind of sentence with one tool. For each it prints
the HTTP status, the latency, the finish reason, whether tool_calls came back (and their names) and, on an error, the
first 200 characters of the response body with the key masked. It never prints the key, and it sends no job data.

Settings, the same ones civil uses: CIVIL_API_BASE, CIVIL_API_KEY, CIVIL_MODEL. Use a SHORT-TERM key and let it expire.

  Amazon Bedrock, OpenAI-compatible Chat Completions (openai.gpt-oss models), in ap-southeast-2 (Sydney):
    CIVIL_API_BASE=https://bedrock-runtime.ap-southeast-2.amazonaws.com/openai/v1
    CIVIL_MODEL=openai.gpt-oss-120b-1:0
    CIVIL_API_KEY=<short-term Bedrock API key>
    python scripts/check_model_endpoint.py --reasoning-effort low --record bedrock-check.json

  Amazon Bedrock, Converse (Claude or Nova; they are not served through Chat Completions):
    CIVIL_API_BASE=https://bedrock-runtime.<region>.amazonaws.com   CIVIL_MODEL=<model or inference-profile id>
    python scripts/check_model_endpoint.py --api converse --record bedrock-converse-check.json
    civil's model loop speaks Chat Completions only: a Converse result says the account and model answer, not that
    model mode will run on them.

  Local Ollama:  CIVIL_API_BASE=http://127.0.0.1:11434/v1  CIVIL_API_KEY=ollama  CIVIL_MODEL=qwen2.5:3b-16k

What the region and model facts are, and where they come from: docs/model-mode.md. Exit 0 when both calls return
200 and the tool call comes back as the one tool; 1 otherwise; 2 when a setting is missing.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from urllib.parse import quote, urlsplit

import httpx

PLAIN = "SYNTHETIC endpoint check. Reply with the single word: ready."
PROMPT = ("SYNTHETIC endpoint check. A 40HQ container holds 6 crates of 1,078.8 kg each. "
          "Call the tool gross_mass with the crate count and the crate mass.")
TOOL = {"type": "function", "function": {
    "name": "gross_mass", "description": "Return the gross mass of a container load.",
    "parameters": {"type": "object", "properties": {"crates": {"type": "integer"}, "crate_kg": {"type": "number"}},
                   "required": ["crates", "crate_kg"], "additionalProperties": False}}}
_ERROR_CHARS = 200


def _masked(text: str, key: str) -> str:
    text = text or ""
    if key:
        text = text.replace(key, "***")
    return text[:_ERROR_CHARS]


def chat(base: str, key: str, model: str, tools: bool, *, timeout: float, max_tokens: int, effort: str) -> dict:
    """POST {base}/chat/completions, as runtime/model_client.py does."""
    payload = {"model": model, "messages": [{"role": "user", "content": PROMPT if tools else PLAIN}],
               "max_tokens": max_tokens, "temperature": 0, "stream": False}
    if effort:
        payload["reasoning_effort"] = effort
    if tools:
        payload.update(tools=[TOOL], tool_choice="auto")
    t0 = time.perf_counter()
    try:
        r = httpx.post(base.rstrip("/") + "/chat/completions", json=payload, timeout=timeout,
                       headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"})
    except httpx.HTTPError as exc:
        return {"status": None, "seconds": round(time.perf_counter() - t0, 2), "error": type(exc).__name__}
    row = {"status": r.status_code, "seconds": round(time.perf_counter() - t0, 2),
           "request_id": r.headers.get("x-amzn-requestid", "")}
    if r.status_code >= 400:
        row["error_body"] = _masked(r.text, key)
        return row
    try:
        data = r.json()
        choice = data["choices"][0]
        msg = choice["message"]
    except (ValueError, KeyError, IndexError, TypeError):
        row["error_body"] = "unparseable response: " + _masked(r.text, key)
        return row
    calls = msg.get("tool_calls") or []
    row.update(model_reported=str(data.get("model") or ""), finish_reason=choice.get("finish_reason"),
               usage=data.get("usage") or {}, text_chars=len(str(msg.get("content") or "")),
               reasoning_chars=len(str(msg.get("reasoning_content") or msg.get("reasoning") or "")),
               tool_calls_returned=bool(calls), tool_calls=[(c.get("function") or {}).get("name") for c in calls])
    if calls:
        try:
            row["tool_args"] = json.loads((calls[0].get("function") or {}).get("arguments") or "{}")
        except (ValueError, TypeError):
            row["tool_args"] = "unparseable"
    return row


def converse(base: str, key: str, model: str, tools: bool, *, timeout: float, max_tokens: int, effort: str) -> dict:
    """Bedrock Converse over HTTPS with a bearer API key (no SDK): POST {base}/model/{id}/converse."""
    body = {"messages": [{"role": "user", "content": [{"text": PROMPT if tools else PLAIN}]}],
            "inferenceConfig": {"maxTokens": max_tokens, "temperature": 0}}
    if tools:
        f = TOOL["function"]
        body["toolConfig"] = {"tools": [{"toolSpec": {"name": f["name"], "description": f["description"],
                                                      "inputSchema": {"json": f["parameters"]}}}]}
    t0 = time.perf_counter()
    try:
        r = httpx.post(base.rstrip("/") + "/model/" + quote(model, safe="") + "/converse", json=body, timeout=timeout,
                       headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"})
    except httpx.HTTPError as exc:
        return {"status": None, "seconds": round(time.perf_counter() - t0, 2), "error": type(exc).__name__}
    row = {"status": r.status_code, "seconds": round(time.perf_counter() - t0, 2),
           "request_id": r.headers.get("x-amzn-requestid", "")}
    if r.status_code >= 400:
        row["error_body"] = _masked(r.text, key)
        return row
    try:
        data = r.json()
        parts = data["output"]["message"]["content"]
    except (ValueError, KeyError, TypeError):
        row["error_body"] = "unparseable response: " + _masked(r.text, key)
        return row
    uses = [p["toolUse"] for p in parts if isinstance(p, dict) and "toolUse" in p]
    row.update(finish_reason=data.get("stopReason"), usage=data.get("usage") or {},
               server_latency_ms=(data.get("metrics") or {}).get("latencyMs"),
               text_chars=sum(len(p.get("text", "")) for p in parts if isinstance(p, dict)),
               tool_calls_returned=bool(uses), tool_calls=[u.get("name") for u in uses])
    if uses:
        row["tool_args"] = uses[0].get("input")
    return row


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--api", choices=("chat", "converse"), default="chat",
                    help="chat: {base}/chat/completions (what civil uses); converse: Bedrock {base}/model/<id>/converse")
    ap.add_argument("--max-tokens", type=int, default=1024, help="output cap per call (a reasoning model needs room: 1024+)")
    ap.add_argument("--reasoning-effort", default="", choices=("", "low", "medium", "high"),
                    help="sent as reasoning_effort (gpt-oss); left out when empty")
    ap.add_argument("--timeout", type=float, default=60.0)
    ap.add_argument("--record", default="", help="also write the result (no key, no text) to this JSON file")
    args = ap.parse_args(argv)
    base, key, model = (os.getenv(n, "").strip() for n in ("CIVIL_API_BASE", "CIVIL_API_KEY", "CIVIL_MODEL"))
    if not (base and key and model):
        print("set CIVIL_API_BASE, CIVIL_API_KEY and CIVIL_MODEL (the key is never printed)", file=sys.stderr)
        return 2
    fn = converse if args.api == "converse" else chat
    options = dict(timeout=args.timeout, max_tokens=args.max_tokens, effort=args.reasoning_effort)
    result = {"endpoint_host": urlsplit(base).hostname or "", "model": model, "api": args.api,
              "checked_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "max_tokens": args.max_tokens,
              "reasoning_effort": args.reasoning_effort or None,
              "no_tools": fn(base, key, model, False, **options), "one_tool": fn(base, key, model, True, **options)}
    text = json.dumps(result, indent=1, ensure_ascii=False)
    if key in text:                                   # belt and braces: the key never leaves this process
        text = text.replace(key, "***")
    print(text)
    if args.record:
        with open(args.record, "w", encoding="utf-8") as fh:
            fh.write(text + "\n")
    ok = (result["no_tools"].get("status") == 200 and result["one_tool"].get("status") == 200
          and result["one_tool"].get("tool_calls") == ["gross_mass"])
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
