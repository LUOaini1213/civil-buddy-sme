#!/usr/bin/env python3
"""Does a model endpoint answer, and does a tool call come back as a tool call? Two calls, nothing else.

Call 1 sends one SYNTHETIC sentence with no tools; call 2 the same kind of sentence with one tool. For each it prints
the HTTP status, the latency, the finish reason, whether tool_calls came back (and their names) and, on an error, the
first 200 characters of the response body with the key masked. It never prints the key, and it sends no job data.

Settings, the same ones civil uses, read from the environment only: CIVIL_API_BASE, CIVIL_API_KEY, CIVIL_MODEL. Use a
SHORT-TERM key where the provider offers one and let it expire.

  Any OpenAI-compatible Chat Completions endpoint (for example an API URL and key a hackathon platform hands out):
    CIVIL_API_BASE=<the base URL; a URL that already ends in /chat/completions is accepted and cut back to its base>
    CIVIL_MODEL=<the model name the platform lists>   CIVIL_API_KEY=<the key>
    python scripts/check_model_endpoint.py --record endpoint-check.json
    civil sends the key as "Authorization: Bearer <key>". A gateway that wants another header answers 401 or 403; the
    first 200 characters of its answer are printed, key masked.

  --eval then runs the frozen 12-request model-mode set (test/benchmarks/model_mode/requests.json, scripts/
  eval_model_mode.py) against the same endpoint, once, and puts the result in the record: roughly 20-30 model calls.
    python scripts/check_model_endpoint.py --eval --record model-mode-real.json
    python scripts/check_model_endpoint.py --eval --only link-en,q-count --record model-mode-real.json

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
200 and the tool call comes back as the one tool (and, with --eval, every request of the set passes); 1 otherwise;
2 when a setting is missing. The record holds the endpoint's host name, never its full URL, and never the key.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import re
import sys
import time
from urllib.parse import quote, quote_plus, urlsplit

import httpx

PLAIN = "SYNTHETIC endpoint check. Reply with the single word: ready."
PROMPT = ("SYNTHETIC endpoint check. A 40HQ container holds 6 crates of 1,078.8 kg each. "
          "Call the tool gross_mass with the crate count and the crate mass.")
TOOL = {"type": "function", "function": {
    "name": "gross_mass", "description": "Return the gross mass of a container load.",
    "parameters": {"type": "object", "properties": {"crates": {"type": "integer"}, "crate_kg": {"type": "number"}},
                   "required": ["crates", "crate_kg"], "additionalProperties": False}}}
_ERROR_CHARS = 200


#: an echo of this many consecutive characters of the key (or of one of its encodings) is masked, so a gateway that
#: prints the key cut short, or inside "Basic base64(user:key)", does not get it past _hide
_ECHO_CHARS = 12


def _key_forms(key: str) -> list:
    """The key as a careless gateway may echo it: as sent, JSON-escaped (a quote, a backslash or a non-ASCII character
    is escaped when the record is written), URL-encoded (a key put in a query string) and base64 (a key inside Basic
    auth or a token blob). base64 depends on where the key starts in the encoded bytes: for each of the three
    alignments, only the characters made from the key's own bits are kept, so the form matches whatever surrounds it."""
    forms = [key, json.dumps(key)[1:-1], json.dumps(key, ensure_ascii=False)[1:-1], quote(key, safe=""),
             quote_plus(key, safe=""), quote(key)]
    # the same with lower-case %xx escapes, a JSON writer that escapes '/' as '\/', and hex (a key dumped as bytes)
    forms += [re.sub(r"%[0-9A-F]{2}", lambda m: m.group(0).lower(), f) for f in forms[3:6]]
    forms += [json.dumps(key)[1:-1].replace("/", "\\/")]
    raw = key.encode("utf-8", "surrogatepass")
    forms += [raw.hex(), raw.hex().upper()]
    for off in range(3):
        start, end = -(-8 * off // 6), 8 * (off + len(raw)) // 6
        for encode in (base64.b64encode, base64.urlsafe_b64encode):
            forms.append(encode(b"\0" * off + raw).decode("ascii")[start:end])
    return [f for f in dict.fromkeys(forms) if len(f) >= min(4, len(key))]


def _hide(text: str, key: str) -> str:
    """The key out of ``text`` in every form of _key_forms, and any run of _ECHO_CHARS consecutive characters of one of
    those forms (a key cut short by one character, or the part of a base64 blob that holds the key)."""
    text = text or ""
    if not key:
        return text
    spans = []
    for form in _key_forms(key):
        width = min(len(form), _ECHO_CHARS)
        for i in range(len(form) - width + 1):
            window, at = form[i:i + width], 0
            while (at := text.find(window, at)) != -1:
                spans.append((at, at + width))
                at += 1
    merged: list = []
    for begin, finish in sorted(spans):
        if merged and begin <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], finish)
        else:
            merged.append([begin, finish])
    out, last = [], 0
    for begin, finish in merged:
        out += [text[last:begin], "***"]
        last = finish
    return "".join(out) + text[last:]


def _masked(text: str, key: str) -> str:
    return _hide(text, key)[:_ERROR_CHARS]


def normalise_base(base: str) -> str:
    """The base URL civil appends /chat/completions to. A pasted full endpoint URL is cut back to its base, so
    .../v1/chat/completions and .../v1 reach the same place (runtime/model_client.py appends the path itself)."""
    base = (base or "").strip().rstrip("/")
    if base.endswith("/chat/completions"):
        base = base[: -len("/chat/completions")].rstrip("/")
    return base


def run_eval(only: str) -> dict:
    """The frozen model-mode set against the endpoint now in CIVIL_API_*, through run_turn and the real client."""
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import eval_model_mode

    result = eval_model_mode.run([x for x in only.split(",") if x], real=True)
    rows = result["rows"]
    summary = dict(result["summary"])
    summary["model_calls"] = sum(int(r.get("model_calls") or 0) for r in rows)
    summary["seconds"] = round(sum(float(r.get("seconds") or 0) for r in rows), 1)
    return {"set": result["set"], "label": result["label"], "summary": summary, "rows": rows,
            "note": ("One run against a real endpoint. must_not_survive (the scripted model's wrong sentences) does not "
                     "apply; right tool, statuses = steps, model-written statements in files, approval attempts do.")}


def _redact(value, key: str):
    """Mask values before JSON escaping, including provider-returned object keys."""
    if isinstance(value, str):
        return _hide(value, key) if key else value
    if isinstance(value, dict):
        return {_redact(k, key): _redact(v, key) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_redact(item, key) for item in value]
    return value


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
    except (httpx.HTTPError, httpx.InvalidURL, UnicodeError) as exc:   # a key or URL httpx cannot send: the type
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
    except (httpx.HTTPError, httpx.InvalidURL, UnicodeError) as exc:   # a key or URL httpx cannot send: the type
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
    ap.add_argument("--record", default="", help="also write the result (no key; with --eval, the replies) to this JSON file")
    ap.add_argument("--eval", action="store_true",
                    help="then run the frozen 12-request model-mode set against this endpoint (chat only; not when the "
                         "no-tools call failed)")
    ap.add_argument("--only", default="", help="with --eval: comma-separated request ids of the set")
    args = ap.parse_args(argv)
    base, key, model = (os.getenv(n, "").strip() for n in ("CIVIL_API_BASE", "CIVIL_API_KEY", "CIVIL_MODEL"))
    if not (base and key and model):
        print("set CIVIL_API_BASE, CIVIL_API_KEY and CIVIL_MODEL (the key is never printed)", file=sys.stderr)
        return 2
    if args.eval and args.api != "chat":
        print("--eval needs --api chat: civil's model loop speaks Chat Completions only", file=sys.stderr)
        return 2
    if args.api == "chat":
        base = normalise_base(base)
    fn = converse if args.api == "converse" else chat
    options = dict(timeout=args.timeout, max_tokens=args.max_tokens, effort=args.reasoning_effort)
    result = {"endpoint_host": urlsplit(base).hostname or "", "model": model, "api": args.api,
              "checked_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "max_tokens": args.max_tokens,
              "reasoning_effort": args.reasoning_effort or None,
              "no_tools": fn(base, key, model, False, **options), "one_tool": fn(base, key, model, True, **options)}
    ok = (result["no_tools"].get("status") == 200 and result["one_tool"].get("status") == 200
          and result["one_tool"].get("tool_calls") == ["gross_mass"])
    if args.eval:
        if result["no_tools"].get("status") != 200:
            result["eval"] = {"skipped": "the no-tools call did not return 200; no credits spent on the set"}
            ok = False
        else:
            os.environ["CIVIL_API_BASE"] = base      # the normalised base, for runtime/model_client.py
            try:
                result["eval"] = run_eval(args.only)
            except Exception as exc:  # noqa: BLE001 - reported in the record, never with the key
                result["eval"] = {"error": type(exc).__name__ + ": " + _masked(str(exc), key)}
                ok = False
            else:
                summary = result["eval"]["summary"]
                ok = ok and summary["passed"] == summary["n"]
    result = _redact(result, key)
    text = json.dumps(result, indent=1, ensure_ascii=False, default=str)
    text = _hide(text, key)                           # belt and braces: the key never leaves this process
    if args.eval and isinstance(result.get("eval"), dict) and "rows" in result["eval"]:
        shown = {k: v for k, v in result.items() if k != "eval"}
        print(_hide(json.dumps(shown, indent=1, ensure_ascii=False), key))
        for row in result["eval"]["rows"]:
            eq = "-" if row["statuses_equal"] is None else ("yes" if row["statuses_equal"] else "NO")
            print(f"{row['id']:<17}{'PASS' if row['passed'] else 'FAIL':<6}tool={'yes' if row['right_tool'] else 'NO':<4}"
                  f"=steps={eq:<4}stmts={row['model_statements']:<3}appr={row['approval_attempts']:<3}"
                  f"calls={row['model_calls']!s:<5}{row['seconds']}s  {row['error_code']}")
        s = result["eval"]["summary"]
        print(f"eval: passed {s['passed']}/{s['n']} · right tool {s['right_tool']}/{s['n']} · statuses = steps "
              f"{s['statuses_equal']} · model-written statements {s['model_statements']} · approval attempts "
              f"{s['approval_attempts']} · model calls {s['model_calls']} · {s['seconds']} s")
    else:
        print(text)
    if args.record:
        with open(args.record, "w", encoding="utf-8") as fh:
            fh.write(text + "\n")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
