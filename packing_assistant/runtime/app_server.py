"""Civil app-server: Codex-shaped JSON-RPC on the civil harness.

Not openai/codex wire-compatible. No Cloud. No generic shell.
Clients drive thread/start + turn/start; tools still compute numbers.
"""

from __future__ import annotations

import json
import sys
from typing import Any, Dict, Optional

PROTOCOL = "civil-app-server/v1"
from packing_assistant.runtime.civil_config import CONFIRM, CONFIRM_EN, is_confirmation  # noqa: E402  (one definition)


def initialize() -> Dict[str, Any]:
    from packing_assistant.office_job import job_root, job_root_granted
    from packing_assistant.runtime.civil_config import load_config
    from packing_assistant.runtime.expert_skills import list_expert_skill_ids

    cfg = load_config()
    root = ""
    if job_root_granted():
        root = str(job_root())
    elif cfg.job_root:
        root = cfg.job_root
    return {
        "protocol": PROTOCOL,
        "host": "civil-buddy",
        "submit_blocked": True,
        "confirm_sentence": CONFIRM,
        "confirm_sentence_en": CONFIRM_EN,
        "sandbox": cfg.sandbox,
        "approval": cfg.approval,
        "job_root": root,
        "skills_n": len(list_expert_skill_ids()),
        "cloud": False,
        "kernel_jail": False,
        "generic_shell": False,
        "not": "openai-codex-binary",
    }


def handle_rpc(msg: Dict[str, Any]) -> Dict[str, Any]:
    mid = msg.get("id")
    method = str(msg.get("method") or "")
    params = msg.get("params") if isinstance(msg.get("params"), dict) else {}
    try:
        result = dispatch(method, params)
    except ValueError as exc:
        return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": str(exc)}}
    except Exception as exc:  # noqa: BLE001
        return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32000, "message": str(exc)}}
    return {"jsonrpc": "2.0", "id": mid, "result": result}


def dispatch(method: str, params: Dict[str, Any]) -> Any:
    from packing_assistant.runtime.civil_config import load_config
    from packing_assistant.runtime.expert_skills import catalog, format_catalog_listing
    from packing_assistant.runtime.threads import (
        list_threads,
        new_thread,
        run_on_thread,
        thread_status,
    )

    if method in {"initialize", "init"}:
        return initialize()
    if method in {"skills/list", "skills.list"}:
        if params.get("listing"):
            return {"text": format_catalog_listing(), "n": 66}
        return {"skills": catalog(), "n": len(catalog())}
    if method in {"config/get", "config.get"}:
        return load_config().to_dict()
    if method in {"thread/start", "thread.start"}:
        # A program drives this protocol: a thread never starts pre-approved; each turn carries its own sentence.
        th = new_thread(str(params.get("title") or params.get("text") or ""), confirm=False)
        return th.to_dict()
    if method in {"thread/list", "thread.list"}:
        return {"threads": [t.to_dict() for t in list_threads()]}
    if method in {"thread/status", "thread.status"}:
        tid = str(params.get("thread_id") or params.get("id") or "")
        return thread_status(tid)
    if method in {"turn/start", "turn.start"}:
        text = str(params.get("text") or params.get("input") or "").strip()
        if not text:
            raise ValueError("turn/start 需要 text")
        if params.get("confirm", False) is not False:
            raise ValueError("confirm 不能代替确认：请在 confirm_text 原样键入确认句")
        tid = str(params.get("thread_id") or "")
        if not tid:
            tid = new_thread(text[:40], confirm=False).thread_id
        return run_on_thread(
            tid,
            text,
            skill=str(params.get("skill") or ""),
            confirm=is_confirmation(str(params.get("confirm_text") or "")),
            background=bool(params.get("background")),
        )
    raise ValueError(f"unknown method {method}")


def serve_stdio() -> int:
    print(json.dumps({"jsonrpc": "2.0", "method": "server/ready", "params": initialize()}, ensure_ascii=False), flush=True)
    for raw in sys.stdin:
        line = raw.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            print(json.dumps({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}}, ensure_ascii=False), flush=True)
            continue
        if not isinstance(msg, dict):
            continue
        if msg.get("method") and msg.get("id") is None:
            continue
        print(json.dumps(handle_rpc(msg), ensure_ascii=False, default=str), flush=True)
        if str(msg.get("method") or "") in {"shutdown", "exit"}:
            return 0
    return 0
