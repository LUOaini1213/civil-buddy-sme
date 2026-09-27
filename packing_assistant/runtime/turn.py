"""One turn, in whichever mode is configured.

    steps   rule routing + the deterministic pipeline; never calls a model (the default, so a
            machine that merely has a key in its environment does not start talking to it)
    model   the model-driven loop (runtime/model_loop.py); a tender <-> packing link request and a request routed to
            a fixed workflow run deterministically first, as in steps (``deterministic_first``), and for the link
            the model then only explains the record
    auto    model when a model is configured and reachable, steps otherwise

Set it with ``civil --mode``, ``/mode`` in the TUI, ``CIVIL_AGENT_MODE``, or ``agent_mode`` in
civil.toml. Every caller that runs a turn for the CLI goes through ``run_turn``.

Independently of the mode, ``sandbox_backend = os | auto`` moves the turn's tool work into a worker
process the kernel confines (runtime/os_sandbox): the whole deterministic turn in ``steps``; the
file-reading and writing tools in ``model``, while the model conversation stays here with the network.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple


def resolve_mode(requested: str = "") -> Tuple[str, str]:
    """(the mode that will run, why it is not the one asked for — empty when it is)."""
    from packing_assistant.llm import llm_config
    from packing_assistant.runtime.civil_config import AGENT_MODES, _strip_mode, load_config

    asked = _strip_mode(requested, AGENT_MODES, "") if requested else load_config().agent_mode
    if asked == "steps":
        return "steps", ""
    if llm_config().get("api_key"):
        return "model", ""
    if asked == "model":
        return "steps", "agent_mode=model，但没有配置模型 Key（CIVIL_API_KEY / CIVIL_API_BASE / CIVIL_MODEL）；本轮按 steps 执行。"
    return "steps", ""


def deterministic_first(text: str, *, skill: str = "", intent: str = "") -> Tuple[str, str]:
    """In model mode, what runs before (or instead of) the model loop, and the intent the rules read.

    "link"      a tender <-> packing link request (task_router.wants_link) that asks for the run: the deterministic
                link runs exactly as in steps mode (same tool, same statuses, same stops), then the model only
                explains the record (model_loop.explain_link). Measured 2026-09-26 on local qwen2.5:3b: given the
                request directly the model planned in 40GP against a 40HQ clause and never reached the link (0 of 6).
    "workflow"  a request the rules route to a fixed workflow (tender-review): it runs in steps, as the workbench
                already does (demo/chat_service.py, routed_model ... and not workflow)
    ""          the model loop gets the request
    The intent is "chat" when the rules read a question, so a question in model mode cannot write (the workbench
    already passes its own intent); otherwise "" (the loop decides, as before)."""
    from packing_assistant.runtime.task_router import route_task, wants_link

    route = route_task(text)
    asked = route.get("intent") or ""
    read_only = "chat" if asked == "chat" else ""
    if intent == "chat" or (skill and skill != "bid-parse"):
        return "", read_only
    if wants_link(text) and asked != "chat":
        return "link", read_only
    if route.get("workflow") and not route.get("ambiguous"):
        return "workflow", read_only
    return "", read_only


def run_turn(text: str, *, session_id: str = "", skill: str = "", confirm: bool = False,
             history: Optional[List[Dict[str, str]]] = None, approve: Optional[Callable[[Dict[str, Any]], bool]] = None,
             cancel_event: Any = None, mode: str = "", material: str = "", intent: str = "",
             cad_context: Optional[Dict[str, Any]] = None,
             planning_context: Optional[Dict[str, Any]] = None,
             logistics_context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    from packing_assistant.runtime.agent_loop import run_agent
    from packing_assistant.runtime.civil_config import load_config

    from packing_assistant.runtime import cancel, os_sandbox
    from packing_assistant.runtime.workspace import active

    chosen, notice = resolve_mode(mode)
    if sum(bool(value) for value in (cad_context, planning_context, logistics_context)) > 1:
        return {"ok": False, "schema": "civil.agent.v1", "error_code": "ambiguous_context", "wrote": False,
                "files": [], "artifacts": [], "submit_blocked": True, "reply": "一次对话只能绑定一个 CAD、施工计划或箱单项目。"}
    def cancelled_result(out=None):
        return {"schema": "civil.agent.v1", "wrote": False, "files": [], "artifacts": [],
                "submit_blocked": True, "intent": "chat", "agent_mode": chosen, "session_id": session_id,
                **(out or {}), "ok": False, "cancelled": True, "state": "cancelled", "error_code": "cancelled",
                "planning_proposal": None, "planning_action": None,
                "logistics_proposal": None, "logistics_action": None,
                "reply": "本轮已取消；已完成的文件保留，未执行后续操作。"}

    def is_cancelled():
        return cancel_event is not None and cancel_event.is_set()

    if is_cancelled():
        return cancelled_result()
    try:
        backend, sandbox_notice = os_sandbox.resolve_backend()
    except PermissionError as exc:          # sandbox_backend=os and it cannot be had: refuse, do not quietly run unconfined
        return {"ok": False, "schema": "civil.agent.v1", "error_code": "sandbox_unavailable", "reply": str(exc), "wrote": False,
                "files": [], "artifacts": [], "submit_blocked": True, "intent": "chat", "agent_mode": chosen, "session_id": session_id}
    worker = None
    try:
        if backend == "os":
            try:
                worker = os_sandbox.Worker(active(), cancel_event=cancel_event).__enter__()
            except os_sandbox.WorkerCancelled:
                raise
            except (os_sandbox.WorkerError, OSError) as exc:
                if load_config().sandbox_backend == "os":
                    return {"ok": False, "schema": "civil.agent.v1", "error_code": "sandbox_unavailable", "wrote": False, "files": [],
                            "artifacts": [], "submit_blocked": True, "intent": "chat", "agent_mode": chosen, "session_id": session_id,
                            "reply": "系统级沙箱没有启动，本轮未执行：" + str(exc)}
                backend, sandbox_notice = "app", "系统级沙箱没有启动（" + str(exc)[:120] + "）；本轮只有应用层策略。"
        out: Dict[str, Any] = {}
        if is_cancelled():
            raise os_sandbox.WorkerCancelled("本轮已取消。")
        first = ""
        if chosen == "model" and not (cad_context or planning_context or logistics_context):
            first, routed_intent = deterministic_first(text, skill=skill, intent=intent)
            if not first:              # what runs deterministically first gets the intent steps mode would give it
                intent = intent or routed_intent
        if chosen == "model" and not first:
            from packing_assistant.runtime.model_loop import run_model_agent

            out = run_model_agent(text, session_id=session_id, expert_id=skill, p0_confirmed=confirm,
                                  history=history, approve=approve, cancel_event=cancel_event, worker=worker,
                                  material=material, intent=intent, cad_context=cad_context, planning_context=planning_context,
                                  logistics_context=logistics_context)
            asked = mode or load_config().agent_mode
            if is_cancelled():
                out = cancelled_result(out)
            elif asked == "auto" and out.get("error_code") == "model_unavailable" and not out.get("tools_run"):
                notice = "模型接口不可用（" + str(out.get("reply") or "")[:80] + "）；本轮按 steps 执行。"
                chosen = "steps"
        if chosen != "model" or first:
            if is_cancelled():
                raise os_sandbox.WorkerCancelled("本轮已取消。")
            if logistics_context:
                out = _logistics_steps(text, logistics_context, session_id=session_id,
                                       cancel_event=cancel_event, worker=worker)
            elif planning_context:
                out = _planning_steps(text, planning_context, session_id=session_id,
                                      cancel_event=cancel_event, worker=worker)
            elif cad_context:
                out = _cad_steps(text, cad_context, session_id=session_id, confirmed=confirm,
                                 cancel_event=cancel_event, worker=worker)
            elif worker is not None:
                out = worker.call("run_agent", text=material or text, session_id=session_id, expert_id=skill,
                                  p0_confirmed=confirm, force_intent=intent or None, cancel_event=cancel_event)["out"]
            else:
                out = run_agent(material or text, session_id=session_id, expert_id=skill, p0_confirmed=confirm,
                                cancel_event=cancel_event, force_intent=intent or None)
            if first == "link" and not is_cancelled():
                from packing_assistant.runtime.model_loop import explain_link

                out = explain_link(text, out, session_id=session_id, cancel_event=cancel_event)
                if out.get("mode_notice"):
                    notice = " ".join(n for n in (notice, out.pop("mode_notice")) if n)
            elif first:                # a fixed workflow: no model took part
                out["agent_mode"], out["deterministic_first"] = "steps", first
    except (os_sandbox.WorkerCancelled, cancel.RunCancelled):
        out = cancelled_result(locals().get("out"))
    except os_sandbox.WorkerError as exc:
        out = {"ok": False, "schema": "civil.agent.v1", "error_code": "sandbox_worker_failed", "wrote": False, "files": [],
               "artifacts": [], "submit_blocked": True, "intent": "chat", "agent_mode": chosen, "session_id": session_id,
               "reply": "沙箱工作进程中断，本轮没有完成：" + str(exc)}
    finally:
        confined = dict(worker.confined) if worker is not None else {}
        if worker is not None:
            worker.close()
    if is_cancelled():
        out = cancelled_result(out)
    if confined and not out.get("cancelled"):
        from packing_assistant.office_job import publish_root_copy

        for item in list(out.get("files") or []):
            if is_cancelled():
                out = cancelled_result(out)
                break
            copied = publish_root_copy(Path(str(item.get("path") or ""))) if isinstance(item, dict) else None
            if copied is not None and all(f.get("path") != str(copied) for f in out["files"]):
                out["files"].append({"name": copied.name, "path": str(copied), "tool": "office__xlsx"})
                out.setdefault("artifacts", []).append(str(copied))
    out["sandbox_backend"] = ({"backend": confined.get("backend"), "enforces": confined.get("enforces"), "selftest": confined.get("selftest")}
                              if confined else {"backend": "app", "enforces": {"write": False, "spawn": False, "network": False}})
    notices = [n for n in (notice, sandbox_notice) if n]
    if notices:
        out["mode_notice"] = " ".join(notices)
        out["reply"] = out["mode_notice"] + "\n\n" + str(out.get("reply") or "")
    return out


def _logistics_steps(text: str, context: dict, *, session_id: str,
                     cancel_event: Any = None, worker: Any = None) -> Dict[str, Any]:
    from uuid import uuid4
    from packing_assistant.logistics.agent import operation, reply_for
    from packing_assistant.runtime import model_loop, os_sandbox, cancel
    turn = model_loop._Turn(session_id=session_id, run_id="run-" + uuid4().hex[:8], user_text=text,
                           confirmed=False, approve=None, cancel_event=cancel_event, logistics_context=context)
    name = "logistics_inspect"
    try:
        name = operation(text, context)
        result = model_loop._dispatch(turn, name, {}, worker)
    except (os_sandbox.WorkerCancelled, cancel.RunCancelled):
        raise
    except (ValueError, OSError, RuntimeError, KeyError, TypeError) as exc:
        result = {"ok": False, "error_code": "logistics_failed", "reason": str(exc)}
    if not turn.logistics_results or turn.logistics_results[-1] is not result:
        turn.logistics_results.append(result)
    return {"ok": bool(result.get("ok")), "schema": "civil.agent.v1", "agent_mode": "steps",
            "run_id": turn.run_id, "session_id": session_id, "intent": "chat", "wrote": False,
            "files": [], "artifacts": [], "submit_blocked": True, "tools_run": [name],
            "reply": reply_for(turn.logistics_results, context), "error_code": result.get("error_code", ""),
            "logistics_results": turn.logistics_results, "logistics_changed": False,
            "logistics_proposal": result.get("logistics_proposal") if result.get("ok") else None,
            "logistics_action": result.get("logistics_action") if result.get("ok") else None}


def _planning_steps(text: str, context: dict, *, session_id: str,
                    cancel_event: Any = None, worker: Any = None) -> Dict[str, Any]:
    from uuid import uuid4
    from packing_assistant.engineering.planning_agent import operation, reply_for
    from packing_assistant.runtime import model_loop, os_sandbox, cancel
    turn = model_loop._Turn(session_id=session_id, run_id="run-" + uuid4().hex[:8], user_text=text,
                           confirmed=False, approve=None, cancel_event=cancel_event, planning_context=context)
    name = "planning_propose"
    try:
        name = operation(text, context)
        result = model_loop._dispatch(turn, name, {}, worker)
    except (os_sandbox.WorkerCancelled, cancel.RunCancelled):
        raise
    except (ValueError, OSError, RuntimeError, KeyError, TypeError) as exc:
        result = {"ok": False, "error_code": "planning_failed", "reason": str(exc)}
    if not turn.planning_results or turn.planning_results[-1] is not result:
        turn.planning_results.append(result)
    return {"ok": bool(result.get("ok")), "schema": "civil.agent.v1", "agent_mode": "steps",
            "run_id": turn.run_id, "session_id": session_id, "intent": "chat", "wrote": False,
            "files": [], "artifacts": [], "submit_blocked": True, "tools_run": [name],
            "reply": reply_for(turn.planning_results, context), "error_code": result.get("error_code", ""),
            "planning_results": turn.planning_results, "planning_changed": False,
            "planning_proposal": result.get("planning_proposal") if result.get("ok") else None,
            "planning_action": result.get("planning_action") if result.get("ok") else None}


def _cad_steps(text: str, context: dict, *, session_id: str, confirmed: bool,
               cancel_event: Any = None, worker: Any = None) -> Dict[str, Any]:
    from uuid import uuid4
    from packing_assistant.cad3d.agent import operation, reply_for
    from packing_assistant.runtime import model_loop, os_sandbox
    turn = model_loop._Turn(session_id=session_id, run_id="run-" + uuid4().hex[:8], user_text=text,
                           confirmed=confirmed, approve=None, cancel_event=cancel_event,
                           cad_context=context, cad_confirmed=confirmed)
    name = operation(text, context)
    try:
        result = model_loop._dispatch(turn, name, {}, worker)
    except os_sandbox.WorkerCancelled:
        raise
    except (ValueError, OSError, RuntimeError) as exc:
        result = {"ok": False, "error_code": "cad_failed", "reason": str(exc)}
    if not turn.cad_results or turn.cad_results[-1] is not result:
        turn.cad_results.append(result)
    return {"ok": bool(result.get("ok")), "schema": "civil.agent.v1", "agent_mode": "steps",
            "run_id": turn.run_id, "session_id": session_id, "intent": "run" if turn.wrote else "chat",
            "reply": reply_for(turn.cad_results, turn.cad_context), "files": turn.files,
            "artifacts": [row["path"] for row in turn.files], "wrote": turn.wrote, "submit_blocked": True,
            "tools_run": [name], "hitl_pending": turn.hitl_pending,
            "error_code": result.get("error_code", ""), "cad_context": turn.cad_context,
            "cad_changed": turn.cad_changed}
