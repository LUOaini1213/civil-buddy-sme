"""Complete agent loop: understand → Scheduler → ToolEngine (sandbox on writes).

Production default is steps planning. Chat never executes write tools.
Numbers (xyz / can_fit / GST rate) stay in tools and in-repo KB — the loop
only orchestrates. submit_blocked stays true.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from uuid import uuid4

from packing_assistant.runtime.bus import get_bus
from packing_assistant.runtime.scheduler import Scheduler, get_scheduler
from packing_assistant.runtime.tool_engine import ToolEngine, get_engine
from packing_assistant.understand import understand

_ROOT = Path(__file__).resolve().parents[2]
from packing_assistant.runtime.paths import default_out_root
_OUT = default_out_root(_ROOT)


def _out_root() -> Path:
    from packing_assistant.runtime.workspace_ctx import current_worktree

    wt = current_worktree()
    if wt:
        p = Path(wt) / ".civil-buddy" / "out"
        p.mkdir(parents=True, exist_ok=True)
        return p
    return _OUT


from packing_assistant.runtime.civil_config import CONFIRM, CONFIRM_EN  # noqa: E402,F401  (one definition)
FORBIDDEN = ("可以投标", "可以开工", "中标率")
_PIPE_KEYS = (
    "matrix",
    "handoff",
    "review",
    "tech_outline",
    "bidbook_markdown",
    "export_markdown",
    "extract_table_markdown",
    "matrix_csv",
    "p0_reject_scan",
    "submit_block_reason",
)


def _safe_sid(session_id: str) -> str:
    sid = (session_id or "default").replace("..", "_").replace("/", "_").replace("\\", "_")
    return sid or "default"


def _scrub(text: str) -> str:
    reply = text or ""
    for bad in FORBIDDEN:
        if bad in reply and f"不判定{bad}" not in reply and f"不{bad}" not in reply:
            reply = reply.replace(bad, "（禁止断言）")
    return reply


def _explain(text: str, expert_id: str, prefix: str = "") -> str:
    import re

    # Read only the structured jurisdiction slot, never project names or
    # incidental country words from the rest of the rendered context.
    slot = re.match(r"本会话槽：辖区=(CN|SG|EU|DUAL|UNSPECIFIED)；", prefix or "")
    previous_jurisdiction = slot.group(1) if slot else ""
    eid = (expert_id or "").strip()
    if eid:
        from packing_assistant.expert_roster import get_expert
        from packing_assistant.expert_turn import explain_expert
        from packing_assistant.runtime.expert_skills import prompt_suffix

        exp = get_expert(eid)
        if exp:
            body = explain_expert(exp, text, previous_jurisdiction=previous_jurisdiction)
            sop = prompt_suffix(eid)
            blob = f"{prefix}\n{body}\n{sop}".strip() if prefix else f"{body}\n{sop}".strip()
            return blob
    from packing_assistant.product_turn import explain

    body = explain(text, previous_jurisdiction=previous_jurisdiction)
    return f"{prefix}\n{body}".strip() if prefix else body


_TABLE_EXTS = (".xlsx", ".csv", ".pdf")
_DOCUMENT_EXTS = (".docx", ".pdf", ".txt", ".md")


def _named_packing_list(text: str) -> str:
    """The one packing list the task names, or "" — two named tables is a question, not a guess."""
    from packing_assistant.office_job import files_named_in

    tables = files_named_in(text, _TABLE_EXTS)
    return str(tables[0]) if len(tables) == 1 else ""


def _named_container_type(text: str, *files: Any) -> List[str]:
    """Container types the request names ("按 X.xlsx 装柜，柜型 20GP"), read with the tender parser's own rule
    (tender_parse._container_codes), the named files' own names left out. Unknown ones are passed on as named:
    run_plan refuses them (unknown_container_type) instead of planning in another type."""
    from packing_assistant.tools.tender_parse import _container_codes

    rest = text or ""
    for name in files:
        if name:
            rest = rest.replace(Path(str(name)).name, " ")
    return sorted(_container_codes(rest))


_LINK_QUESTION_EN = (
    "This reads as a question about the tender <-> packing link, so nothing was run and nothing was written. The linked "
    "run reads the tender's logistics clauses, plans the named panel list in the container type the clause names, and "
    "writes one English statement per clause, each tied to its clause and to the plan figure behind it; what the plan "
    "does not model (securing, handling, delivery sequence) is left [TO CONFIRM] for the named person. To run it, name "
    "one tender document and one panel list and ask for it, for example: \"Check facade_panels.xlsx against the "
    "logistics clauses of facade_itt_doc.md\".")
_QUESTION_EN = ("Nothing was run and nothing was written: no draft was asked for in this turn. To get one, ask for it (for "
                "example \"Draft the ... for ...\"). The reference notes below are the post's own and are in Chinese.")


def _chat_reply(text: str, expert_id: str, prefix: str = "") -> str:
    """The answer to a question. An English one gets an English lead and no internal slot line; a question about
    the link gets an English answer only."""
    from packing_assistant.runtime.reply_language import english_request
    from packing_assistant.runtime.task_router import wants_link

    if not english_request(text):
        return _explain(text, expert_id, prefix)
    if (expert_id or "bid-parse") == "bid-parse" and wants_link(text):
        return _LINK_QUESTION_EN
    body = _explain(text, expert_id, prefix)
    kept = [line for line in body.splitlines() if not line.startswith(("本会话槽：", "这是提问。"))]
    return _QUESTION_EN + "\n\n" + "\n".join(kept).strip()


def _link_inputs(text: str) -> Any:
    """(tender, panel list) for a tender <-> packing link request, a sentence saying what is missing, or None when
    the request is not one. Only a request that asks for the link gets it: a tender named beside a bill of
    quantities is still a plain 招标解析."""
    from packing_assistant.office_job import files_named_in
    from packing_assistant.runtime.task_router import wants_link

    if not wants_link(text):
        return None
    tables = files_named_in(text, (".xlsx", ".xlsm", ".xls", ".csv"))
    documents = [p for p in files_named_in(text, (".docx", ".pdf", ".txt", ".md")) if p not in tables]
    if len(tables) == 1 and len(documents) == 1:
        return documents[0], tables[0]
    from packing_assistant.runtime.reply_language import english_request

    zh = "" if english_request(text) else "招标与装柜联动需要在任务里各点名一份招标文件和一份装箱单。"
    return ("Linking the tender to the packing needs exactly one tender document (.docx / .pdf / .md / .txt) and one "
            f"panel list (.xlsx / .csv) named in the request; this one names {len(documents)} document(s) and "
            f"{len(tables)} table(s). {zh}Nothing was written.")


def _with_named_documents(text: str, *, whole: bool = False) -> str:
    """The task plus the text of the job documents it names (the steps path reads what you point at).

    ``whole``: the caller parses the document itself (招标解析), so the file is read to its end instead of
    to the 8 000 characters a prompt can spare - the first five pages of a tender are not the tender."""
    from packing_assistant.office_job import DOCUMENT_FILE_CHARS, DOCUMENT_TOTAL_CHARS, files_named_in, named_files_blob, read_material

    named = files_named_in(text, _DOCUMENT_EXTS)
    limits = {"per_file": DOCUMENT_FILE_CHARS, "total": DOCUMENT_TOTAL_CHARS} if whole else {}
    blob = named_files_blob(named, reader=read_material, **limits) if named else ""
    return f"{text}\n\n{blob}" if blob else text


def _tender_materials(text: str) -> Tuple[Optional[List[Dict[str, Any]]], List[Dict[str, str]]]:
    """Named job documents as workflow sources, roles read off the file names - and the named ones that
    gave no text, each with why.

    The sources are None unless exactly one readable file is recognisably the tender: which document is
    the tender is not something to guess, and the workflow's own marker parsing still applies to pasted
    text. A file that could not be read used to be left out without a word, so a check that was handed
    a scanned 投标响应.pdf reported the response as never given.
    """
    from packing_assistant.office_job import DOCUMENT_FILE_CHARS, files_named_in, job_root, material_role, read_material_checked

    sources: List[Dict[str, Any]] = []
    unread: List[Dict[str, str]] = []
    root = job_root().resolve()
    for index, path in enumerate(files_named_in(text, _DOCUMENT_EXTS)):
        role = material_role(path.name)     # one rule for what a file name says (技术标.docx, 养护方案.docx are ours too)
        body, why = read_material_checked(path, DOCUMENT_FILE_CHARS)   # 40 000 used to be the cut: a quarter of a real tender
        if why:
            unread.append({"title": path.name, "role": role, "reason": why})
            continue
        source = {"source_id": f"{role}-{index + 1}", "title": path.name, "text": body, "start": 0,
                  "end": len(body), "role": role, "kind": "job_file"}
        try:
            source["path"] = path.resolve().relative_to(root).as_posix()   # so a later `civil review` can read it again
        except ValueError:
            pass
        sources.append(source)
    return (sources if sum(1 for source in sources if source["role"] == "tender") == 1 else None), unread


def _tender_sources(text: str) -> Optional[List[Dict[str, Any]]]:
    return _tender_materials(text)[0]


def _names_both_sides(text: str) -> bool:
    """The task names exactly one tender document and at least one document of ours (roles read off the file
    names, as everywhere)."""
    from packing_assistant.office_job import files_named_in, material_role

    # by its full name, extension included: "招标文件要求工期60日历天" talks ABOUT the tender, it does not point at a file
    names = [path.name.lower() for path in files_named_in(text, _DOCUMENT_EXTS) if path.name.lower() in (text or "").lower()]
    roles = [material_role(n) for n in names]
    return roles.count("tender") == 1 and "response" in roles


def _draft_md(expert_id: str, tool: str, text: str) -> str:
    from packing_assistant.expert_roster import get_expert
    from packing_assistant.expert_turn import _draft_markdown
    from packing_assistant.runtime import plugins

    item = plugins.skill(expert_id)
    if item is not None:
        from packing_assistant.runtime.project_instructions import load

        slots = load().slots
        drafted = plugins.render_draft(item, _with_named_documents(text), project=slots.get("project", ""),
                                       jurisdiction=slots.get("jurisdiction", ""))
        if drafted is not None:
            return drafted
    exp = get_expert(expert_id)
    if not exp:
        return (
            f"# {tool}\n\n内部讨论 AI 草稿。不是签认件，不判定可投标，不判定可以开工。\n\n"
            f"## 用户原文\n\n{(text or '').strip() or '（未提供）'}\n"
        )
    return _draft_markdown(exp, tool, text)


def _plan_calls(
    text: str,
    *,
    expert_id: str,
    session_id: str,
    p0_confirmed: bool,
    packing_summary: Optional[Dict[str, Any]],
    project_name: str,
    packing_list: str = "",
    request_text: Optional[str] = None,
) -> Dict[str, Any]:
    from packing_assistant.expert_roster import get_expert

    exp = get_expert(expert_id) if expert_id else None
    from packing_assistant.runtime.civil_config import high_risk_unconfirmed, hitl_reply
    from packing_assistant.runtime.reply_language import english_request

    if exp and high_risk_unconfirmed(risk=exp.risk, confirmed=p0_confirmed):
        return {
            "hitl": True,
            "calls": [],
            "reply": hitl_reply(exp.name, english=english_request(text)),
        }
    sid = _safe_sid(session_id)
    out_dir = _out_root() / sid / (exp.id if exp else "ops")
    calls: List[Dict[str, Any]] = []
    requested = text if request_text is None else request_text

    if exp and exp.id == "pack-ship" and packing_list:
        # 任务点名了文件夹里的装箱单：真算。柜数与利用率出自装箱引擎，不再只抄快照。
        # 柜型照任务里写的算（「柜型 20GP」以前被丢掉，一律按 40HQ）；没写才是 40HQ，写了两种就问。
        codes = _named_container_type(requested, packing_list)
        if len(codes) > 1:
            return {"hitl": False, "calls": [], "stop_code": "ambiguous_container_type",
                    "stop": f"任务里写了不止一种柜型（{'、'.join(codes)}）；装箱引擎一次只算一种柜型 × N：请只写一种。本轮未出方案。"}
        arguments = {"file_path": packing_list, **({"container_type": codes[0]} if codes else {})}
        calls.append({"name": "pack-ship__plan", "arguments": arguments, "tool_label": "pack-ship__plan"})
        return {"hitl": False, "calls": calls, "connected": True, "snap": None, "packing_list": packing_list}

    if exp and exp.id == "pack-ship":
        from packing_assistant.runtime.session_packing import load_packing_snapshot

        snap = packing_summary if isinstance(packing_summary, dict) else load_packing_snapshot(session_id)
        connected = bool(snap)
        calls.append({"name": "pack-ship__health", "arguments": {"solver": snap}})
        calls.append({"name": "pack-ship__list", "arguments": {}})
        calls.append(
            {
                "name": "pack-ship__plan",
                "arguments": {"solver": snap, "connected": connected, "materials": text},
            }
        )
        calls.append(
            {
                "name": "pack-ship__export",
                "arguments": {"solver": snap, "connected": connected},
            }
        )
        return {"hitl": False, "calls": calls, "connected": connected, "snap": snap}

    if exp is not None and exp.id == "bid-parse":
        linked = _link_inputs(requested)
        if isinstance(linked, str):
            return {"hitl": False, "calls": [], "stop": linked, "stop_code": "link_inputs"}
        if linked is not None:
            # 招标 + 装箱单一次跑完：条款 → 按条款柜型真算 → 逐条应答与联动记录（tender_packing_link.py）
            from packing_assistant.tender_packing_link import LINK_FILE

            tender, table = linked
            # a type typed in the request is a person's choice (the ITT names none, several, or a size only)
            codes = _named_container_type(requested, tender, table)
            if len(codes) > 1:
                return {"hitl": False, "calls": [], "stop_code": "ambiguous_container_type",
                        "stop": f"The request names more than one container type ({', '.join(codes)}); the planner plans one "
                                f"type x N at a time: name one. "
                                + ("" if english_request(text) else f"任务里写了不止一种柜型（{'、'.join(codes)}），请只写一种。")
                                + "Nothing was written."}
            calls.append({"name": "tender.packing_link", "tool_label": "tender.packing_link",
                          "arguments": {"tender_path": str(tender), "packing_list": str(table),
                                        "previous_path": str(out_dir / LINK_FILE),
                                        **({"container_type": codes[0]} if codes else {})}})
            return {"hitl": False, "calls": calls, "out_dir": str(out_dir), "link": True}
    if exp is None or exp.id == "bid-parse":
        calls.append(
            {
                "name": "tender.parse",
                "arguments": {
                    "text": _with_named_documents(text, whole=True),
                    "source": "agent-loop",
                    "project_name": project_name,
                    "p0_confirmed": p0_confirmed,
                },
            }
        )
        return {"hitl": False, "calls": calls, "out_dir": str(out_dir)}

    if exp.id in {"bid-compliance", "bid-tech"}:
        from packing_assistant.runtime.session_handoff import load_handoff
        from packing_assistant.runtime.tool_engine import get_engine

        ho = load_handoff(session_id) or {}
        primary = (exp.exclusive[0] if exp.exclusive else "") or f"{exp.id}__draft"
        if primary in get_engine().tools:
            calls.append(
                {
                    "name": primary,
                    "arguments": {
                        "text": text,
                        "session_id": session_id,
                        "confirm_ok": p0_confirmed,
                    },
                    "tool_label": primary,
                }
            )
            return {"hitl": False, "calls": calls, "out_dir": str(out_dir), "handoff": ho}

    tools = [t for t in (exp.exclusive or ()) if "fill_scheme" not in t] or [f"{exp.id}__draft"]
    from packing_assistant.runtime.project_instructions import with_facts
    from packing_assistant.runtime.tool_engine import get_engine

    # CIVIL.md 里写明的项目事实随任务一起交给起草工具；用户本次写了的以用户为准。
    text = with_facts(text)
    eng = get_engine()
    primary = tools[0]
    if primary in eng.tools and exp.category != "plugin":
        calls.append(
            {
                "name": primary,
                "arguments": {
                    "text": text,
                    "session_id": session_id,
                    "confirm_ok": p0_confirmed,
                    "packing_summary": packing_summary,
                },
                "tool_label": primary,
            }
        )
        return {"hitl": False, "calls": calls}
    if exp.category == "plugin":
        # 插件岗位的稿走「写盘 + Office 导出」那一段（和装柜、招标解析同一段），这样它也有 Word / Excel。
        tool = tools[0]
        return {"hitl": False, "calls": [], "follow": [{"name": "write_deliverable", "tool_label": tool,
                "arguments": {"path": str(out_dir / f"{tool}.md"), "text": _draft_md(exp.id, tool, text)}}]}
    for tool in tools:
        path = out_dir / f"{tool}.md"
        calls.append(
            {
                "name": "write_deliverable",
                "arguments": {"path": str(path), "text": _draft_md(exp.id, tool, text)},
                "tool_label": tool,
            }
        )
    return {"hitl": False, "calls": calls}


def _merge_pipe(out: Dict[str, Any], result: Dict[str, Any]) -> None:
    data = result.get("data") if isinstance(result.get("data"), dict) else result
    if not isinstance(data, dict):
        return
    for key in _PIPE_KEYS:
        if key in data and data[key] is not None:
            out[key] = data[key]
    if data.get("submit_blocked") is not None:
        out["submit_blocked"] = True


def run_agent(
    text: str,
    *,
    session_id: str = "",
    expert_id: str = "",
    p0_confirmed: bool = False,
    force_intent: Optional[str] = None,
    packing_summary: Optional[Dict[str, Any]] = None,
    project_name: str = "幕墙项目投标应答（草稿）",
    tools: Optional[ToolEngine] = None,
    max_steps: int = 8,
    scheduler: Optional[Scheduler] = None,
    cancel_event: Any = None,
    request_text: Optional[str] = None,
) -> Dict[str, Any]:
    from packing_assistant.expert_roster import get_expert, list_experts
    from packing_assistant.otel_hooks import span

    requested = text if request_text is None else request_text
    intent = force_intent if force_intent in {"chat", "run", "both"} else understand(requested)
    eid = (expert_id or "").strip()
    skill_source = "given" if eid else ""
    route = None
    if not eid:
        from packing_assistant.runtime.task_router import route_task
        route = route_task(requested)
        if force_intent not in {"chat", "run", "both"}:
            intent = route["intent"]
        if route["ambiguous"]:
            return {"ok": True, "schema": "civil.agent.v1", "intent": "chat", "wrote": False,
                    "files": [], "reply": route["reason"], "route": route, "submit_blocked": True}
        if len(route["expert_ids"]) > 1 and not route["workflow"]:
            children = []
            sequence_sid = session_id or f"sess-{uuid4().hex[:8]}"
            for selected in route["expert_ids"]:
                child = run_agent(text, session_id=sequence_sid, expert_id=selected,
                    p0_confirmed=p0_confirmed, force_intent=intent, packing_summary=packing_summary,
                    project_name=project_name, tools=tools, max_steps=max_steps,
                    scheduler=scheduler, cancel_event=cancel_event, request_text=requested)
                children.append(child)
                if not child.get("ok") or child.get("hitl_pending") or child.get("cancelled"):
                    break
            return {"ok": all(c.get("ok") for c in children), "schema": "civil.agent.v1",
                    "intent": intent, "session_id": sequence_sid, "route": route, "children": children,
                    "wrote": any(c.get("wrote") for c in children), "submit_blocked": True,
                    "hitl_pending": any(c.get("hitl_pending") for c in children),
                    "cancelled": any(c.get("cancelled") for c in children),
                    "pending_expert_ids": route["expert_ids"][len(children):],
                    "reply": "\n\n".join(c.get("reply", "") for c in children),
                    "files": [f for c in children for f in c.get("files", [])],
                    "artifacts": [f for c in children for f in c.get("artifacts", [])]}
        from packing_assistant.runtime.expert_skills import match_skill
        if not route["workflow"] and route["expert_ids"] == ["bid-compliance"] and intent != "chat" and _names_both_sides(text):
            # "废标检查 招标文件.docx 投标函.docx": two documents to set against each other. The one-post path would
            # paste the first 8 000 characters of each into one text with nobody's role on it.
            route = {**route, "workflow": "tender-review", "expert_ids": ["bid-parse", "bid-tech", "bid-compliance"],
                     "reason": route.get("reason", "") + " 点名了招标文件和我方文件：逐份整读、按角色对照，走全面核对流程。"}
        eid = (route["expert_ids"][0] if len(route["expert_ids"]) == 1 else "") or (match_skill(text) if not route["workflow"] else "") or ""
        skill_source = "matched" if eid else ""
    exp = get_expert(eid) if eid else None
    if eid and not exp:
        return {
            "ok": False,
            "schema": "civil.agent.v1",
            "error": f"unknown expert: {eid}",
            "intent": "chat",
            "wrote": False,
            "submit_blocked": True,
        }
    from packing_assistant.runtime.civil_config import CONFIRM, CONFIRM_EN, decide_gate, load_config
    from packing_assistant.runtime.reply_language import english_request

    packing_list = _named_packing_list(requested) if exp and exp.id == "pack-ship" else ""
    if packing_list and intent == "chat" and force_intent not in {"chat", "run", "both"}:
        intent = "run"      # 「packing.csv 要几个柜」点名了装箱单，是要算，不是要聊
    cfg = load_config()
    gate = decide_gate(
        intent=intent,
        risk=(exp.risk if exp else "low"),
        confirmed=p0_confirmed,
        cfg=cfg,
    )
    sid = session_id or f"sess-{uuid4().hex[:8]}"
    from packing_assistant.runtime.memory import assemble_context, prompt_prefix
    from packing_assistant.runtime.project_instructions import seed_session

    seed_session(sid)
    ctx = assemble_context(
        sid,
        text=text,
        project_name=project_name,
        p0_confirmed=p0_confirmed,
    )
    p0_confirmed = ctx.get("p0_confirmed") is True
    project_name = str(ctx.get("project") or project_name)
    ctx_prefix = prompt_prefix(ctx)
    sched = scheduler or get_scheduler()
    engine = tools or get_engine()
    bus = get_bus()
    from packing_assistant.runtime.deadlock import get_watch
    from packing_assistant.runtime import cancel as cancellation

    watch = get_watch()
    watch_on = False
    run = sched.create_run(sid, expert_id=eid, intent=intent, max_steps=max_steps)
    if run.error_code == "session_busy":
        return {
            "ok": False,
            "schema": "civil.agent.v1",
            "error_code": "session_busy",
            "reason": f"拒绝：session {sid} 已有进行中的 run，同会话串行，不是死锁。",
            "intent": intent,
            "wrote": False,
            "submit_blocked": True,
            "run_id": run.run_id,
            "state": run.state,
            "session_id": sid,
        }
    holds = [f"session:{sid}"]
    if eid:
        holds.append(f"expert:{eid}")
    d0 = watch.begin(run.run_id, holds=holds, label=eid or "router")
    watch_on = True
    if not d0.allow:
        run.error_code = d0.err
        try:
            sched.transition(run, "failed")
        except Exception:
            run.state = "failed"
        if d0.err == "deadlock":
            bus.emit(
                run.run_id,
                "deadlock",
                {"reason": d0.reason, "cycle": list(d0.cycle), "path": d0.path},
            )
        watch.end(run.run_id)
        watch_on = False
        sched.release(sid)
        run.stamp_end()
        return {
            "ok": False,
            "schema": "civil.agent.v1",
            "error_code": d0.err,
            "reason": d0.reason,
            "cycle": list(d0.cycle),
            "deadlock": d0.to_dict(),
            "intent": intent,
            "wrote": False,
            "submit_blocked": True,
            "run_id": run.run_id,
            "state": run.state,
            "session_id": sid,
            "expert_id": eid,
        }
    sched.transition(run, "planning")
    bus.emit(run.run_id, "run_started", {"intent": intent, "expert_id": eid})
    messages: List[Dict[str, Any]] = [{"role": "user", "content": text}]
    out: Dict[str, Any] = {
        "ok": True,
        "schema": "civil.agent.v1",
        "intent": intent,
        "wrote": False,
        "reply": "",
        "matrix": None,
        "submit_blocked": True,
        "submit_block_reason": "未成稿或仍是 AI 草稿，不可递交。",
        "run_id": run.run_id,
        "state": run.state,
        "session_id": sid,
        "expert_id": eid,
        "expert_name": exp.name if exp else "",
        "skill": eid,
        "skill_source": skill_source,
        "sandbox_mode": cfg.sandbox,
        "approval": cfg.approval,
        "cloud": False,
        "generic_shell": False,
        "messages": messages,
        "tools_used": [],
        "tools_run": [],
        "artifacts": [],
        "tool_results": [],
        "sandbox": [],
        "files": [],
        "events": [],
        "hitl_pending": False,
        "n_experts": len(list_experts()),
        "error_code": "",
        "agent_mode": "steps",
        "context": {
            "jurisdiction": ctx.get("jurisdiction"),
            "project": ctx.get("project"),
            "p0_confirmed": ctx.get("p0_confirmed"),
            "compressed": ctx.get("compressed"),
            "has_handoff": ctx.get("has_handoff"),
            "has_packing": ctx.get("has_packing"),
        },
    }

    def _finish(state_ok: bool = True) -> Dict[str, Any]:
        run.messages = list(messages)
        run.tools_used = list(out["tools_used"])
        run.artifacts = list(out["artifacts"])
        run.stamp_end()
        out["messages"] = list(messages)
        out["state"] = run.state
        out["error_code"] = run.error_code or out.get("error_code") or ""
        out["events"] = [e.to_dict() for e in bus.for_run(run.run_id)]
        out["reply"] = _scrub(str(out.get("reply") or ""))
        out["submit_blocked"] = True
        out["cloud"] = False
        out["generic_shell"] = False
        if eid:
            from packing_assistant.runtime.expert_skills import skill_body

            out["skill_sop_loaded"] = bool(skill_body(eid))
        else:
            out["skill_sop_loaded"] = False
        out["duration_ms"] = run.duration_ms
        out["history"] = list(run.history)
        from packing_assistant.runtime.memory import assemble_context

        assemble_context(
            sid,
            text=text,
            project_name=project_name,
            p0_confirmed=p0_confirmed,
            compressed=bool(ctx.get("compressed")),
        )
        bus.emit(run.run_id, "run_ended", {"state": run.state, "wrote": out["wrote"]})
        from packing_assistant.runtime.middleware import annotate

        annotate(
            out,
            gate=gate,
            sandbox_mode=cfg.sandbox,
            approval=cfg.approval,
            intent=intent,
        )
        return out

    def _cancel_requested() -> bool:
        return (run.cancelled or run.state == "cancelled" or cancellation.is_cancelled(run.run_id)
                or bool(cancel_event is not None and cancel_event.is_set()))

    def _finish_cancelled() -> Dict[str, Any]:
        run.cancelled = True
        run.error_code = "cancelled"
        if run.state not in {"done", "failed", "cancelled"}:
            sched.transition(run, "cancelled")
        out.update(ok=False, cancelled=True, error_code="cancelled",
                   wrote=bool(out["files"] or out["artifacts"]),
                   reply="本轮已取消，停止后续步骤；已完成的文件保留供下载和核对。")
        if out.get("worker_running"):
            out["reply"] = "已请求取消；当前工具仍在退出，资源保持占用，后续步骤不会启动。已完成的文件保留供核对。"
        messages.append({"role": "assistant", "content": out["reply"]})
        bus.emit(run.run_id, "cancelled", {"wrote": out["wrote"]})
        return _finish()

    try:
        with span(
            "civil.agent",
            {"run_id": run.run_id, "intent": intent, "expert_id": eid, "node": "agent_loop"},
        ), cancellation.scope(*cancellation.current_keys(), run.run_id, event=cancel_event):
            if _cancel_requested():
                return _finish_cancelled()
            if route and route["workflow"] and intent != "chat" and gate == "go":
                from packing_assistant.runtime.tender_workflow import run_tender_workflow
                if max_steps < 4:
                    out.update(ok=False, error_code="max_steps", reply="招标协作需要解析、两项检查与汇总，请提高步骤预算。")
                    sched.transition(run, "failed")
                    return _finish()
                sources, unread = _tender_materials(text)
                lost = [item for item in unread if item["role"] == "tender"]
                if lost and sources is None:
                    # the tender itself gave no text: there is nothing to compare against, and the sentence
                    # that named the file is not a tender to parse instead
                    names = "；".join(f"{item['title']}（{item['reason']}）" for item in lost)
                    out.update(ok=False, error_code="tender_unreadable", wrote=False, unreadable=unread,
                               reply=f"点名的招标文件没读出来：{names}。没有招标正文就无从对照，本轮未写盘。")
                    sched.transition(run, "failed")
                    messages.append({"role": "assistant", "content": out["reply"]})
                    return _finish()
                sched.transition(run, "acting")
                result = run_tender_workflow(text, session_id=sid, output_root=_out_root(), sources=sources, unreadable=unread,
                    confirmed=p0_confirmed, cancel_event=cancel_event)
                out.update({key: value for key, value in result.items() if key not in {"run_id", "session_id", "schema", "state"}})
                out["route"], out["collaboration"] = route, result
                out["tools_run"] = ["tender.parse", "bid-tech__expand", "bid-compliance__gaps", "tender.review"]
                out["tools_used"] = list(out["tools_run"])
                run.steps = 4
                if _cancel_requested():
                    return _finish_cancelled()
                sched.transition(run, "reflecting")
                sched.transition(run, "done" if result["ok"] else "failed")
                messages.append({"role": "assistant", "content": out["reply"]})
                return _finish()
            if intent == "chat":
                reply = _chat_reply(text, eid, ctx_prefix)
                if _cancel_requested():
                    return _finish_cancelled()
                messages.append({"role": "assistant", "content": reply})
                sched.transition(run, "done")
                out["reply"] = reply
                out["state"] = run.state
                return _finish()

            if gate == "read_only":
                reply = (
                    f"sandbox=read-only：本 thread 只读，不成稿。"
                    f"改用 /sandbox workspace-write，或 CIVIL_SANDBOX=workspace-write。"
                )
                messages.append({"role": "assistant", "content": reply})
                sched.transition(run, "done")
                out["reply"] = reply
                out["wrote"] = False
                return _finish()

            if gate == "hitl":
                sched.transition(run, "waiting_hitl")
                who = f"{exp.name} " if exp else ""
                reply = (f"approval={cfg.approval}: {who or 'this post '}writes only after a person types the sign-off sentence "
                         f"\"{CONFIRM_EN}\" (or 「{CONFIRM}」) in this turn. Nothing was written."
                         if english_request(text) else f"approval={cfg.approval}：{who}写盘须确认句「{CONFIRM}」。本轮未写盘。")
                messages.append({"role": "assistant", "content": reply})
                bus.emit(run.run_id, "hitl", {"required": True})
                out["reply"] = reply
                out["hitl_pending"] = True
                out["wrote"] = False
                return _finish()

            planned = _plan_calls(
                text,
                expert_id=eid,
                session_id=sid,
                p0_confirmed=p0_confirmed,
                packing_summary=packing_summary,
                project_name=project_name,
                packing_list=packing_list,
                request_text=requested,
            )
            if _cancel_requested():
                return _finish_cancelled()
            if planned.get("stop"):
                out.update(ok=False, wrote=False, error_code=str(planned.get("stop_code") or "stopped"), reply=str(planned["stop"]))
                run.error_code = out["error_code"]
                sched.transition(run, "failed")
                messages.append({"role": "assistant", "content": out["reply"]})
                return _finish()
            if planned.get("handoff"):
                out["handoff"] = planned["handoff"]
            if planned.get("hitl"):
                sched.transition(run, "waiting_hitl")
                reply = str(planned.get("reply") or "高风险写盘须确认句。本轮未写盘。")
                messages.append({"role": "assistant", "content": reply})
                bus.emit(run.run_id, "hitl", {"required": True})
                out["reply"] = reply
                out["hitl_pending"] = True
                out["wrote"] = False
                return _finish()

            if not sched.transition(run, "acting"):
                out["ok"] = False
                out["error_code"] = run.error_code or "illegal_edge"
                out["reply"] = "无法进入 acting。"
                return _finish()

            explain_prefix = _explain(text, eid, ctx_prefix) if intent == "both" else ""
            pack_ship: Dict[str, Any] = {}
            last_export_md = ""
            plan_record = ""
            export_name = "pack-ship__export"
            last_extract = ""
            link_data: Dict[str, Any] = {}
            link_writes: List[Dict[str, Any]] = []

            for call in planned.get("calls") or []:
                if _cancel_requested():
                    return _finish_cancelled()
                name = str(call.get("name") or "")
                args = dict(call.get("arguments") or {})
                if not sched.transition(run, "waiting_tool"):
                    out["ok"] = False
                    out["error_code"] = run.error_code or "max_steps"
                    out["reply"] = "达到最大步数，请缩小任务范围"
                    messages.append({"role": "assistant", "content": out["reply"]})
                    return _finish()
                bus.emit(run.run_id, "tool_call", {"name": name})
                result = engine.execute(
                    name,
                    args,
                    expert_id=eid,
                    intent="run",
                    cancelled=run.cancelled,
                    run_id=run.run_id,
                )
                bus.emit(
                    run.run_id,
                    "tool_result",
                    {"name": name, "error_code": result.get("error_code"), "ok": result.get("ok")},
                )
                messages.append(
                    {
                        "role": "tool",
                        "name": name,
                        "content": str(result.get("detail") or result.get("error_code") or "ok")[:500],
                        "error_code": result.get("error_code"),
                    }
                )
                out["tool_results"].append(
                    {
                        "name": name,
                        "ok": result.get("ok"),
                        "error_code": result.get("error_code"),
                        "duration_ms": result.get("duration_ms"),
                    }
                )
                out["tools_used"].append(name)
                label = str(call.get("tool_label") or name)
                out["tools_run"].append(label)
                if result.get("sandbox"):
                    out["sandbox"].append(result["sandbox"])
                if not result.get("ok"):
                    out["worker_running"] = bool(result.get("worker_running"))
                    if result.get("error_code") == "cancelled" or _cancel_requested():
                        return _finish_cancelled()
                    out["ok"] = False
                    run.error_code = str(result.get("error_code") or "tool_failed")
                    out["error_code"] = run.error_code
                    out["reply"] = f"工具 {label} 未完成（{run.error_code}），本轮已停止。已完成的文件保留供核对。"
                    if out["worker_running"]:
                        out["reply"] = f"工具 {label} 超时，已请求停止；当前工具仍在退出，资源保持占用，后续步骤不会启动。"
                    if name == "pack-ship__plan" and packing_list and result.get("error"):
                        from packing_assistant.tools.pack_ship_solve import plan_reply

                        run.error_code = out["error_code"] = str(result["error"])
                        out["reply"] = plan_reply(result, Path(packing_list).name)
                        out["pack_ship"] = {"source": result.get("source"), "needs_human": result.get("needs_human") or []}
                    sched.transition(run, "failed")
                    messages.append({"role": "assistant", "content": out["reply"]})
                    return _finish()
                data = result.get("data") if isinstance(result.get("data"), dict) else result
                path = data.get("path") if isinstance(data, dict) else None
                if result.get("ok") and path:
                    out["artifacts"].append(str(path))
                    out["wrote"] = True
                    out["files"].append(
                        {
                            "name": Path(str(path)).name,
                            "path": str(path),
                            "tool": label,
                        }
                    )
                if result.get("ok") and isinstance(data, dict):
                    for f in data.get("files") or []:
                        if not isinstance(f, dict):
                            continue
                        fp = str(f.get("path") or "")
                        if not fp:
                            continue
                        out["artifacts"].append(fp)
                        out["wrote"] = True
                        out["files"].append(
                            {
                                "name": str(f.get("name") or Path(fp).name),
                                "path": fp,
                                "tool": str(f.get("tool") or label),
                            }
                        )
                    if data.get("handoff"):
                        out["handoff"] = data["handoff"]
                    if data.get("docx_pending") is not None:
                        out["docx_pending"] = data["docx_pending"]
                    if data.get("tools_run"):
                        for t in data["tools_run"]:
                            if t not in out["tools_run"]:
                                out["tools_run"].append(t)
                if name == "tender.packing_link" and result.get("ok"):
                    link_data = data if isinstance(data, dict) else {}
                    link_dir = Path(str(planned.get("out_dir") or ""))
                    for item in link_data.get("deliverables") or []:
                        link_writes.append({"name": "write_deliverable", "tool_label": "tender.packing_link",
                                            "arguments": {"path": str(link_dir / item["name"]), "text": item["text"]}})
                    for key in ("matrix", "bidbook_markdown"):
                        if link_data.get(key) is not None:
                            out[key] = link_data[key]
                    ho = link_data.get("handoff")
                    if isinstance(ho, dict) and ho:       # bid-tech / bid-compliance read it next, as after 招标解析
                        from packing_assistant.runtime.session_handoff import save_handoff

                        hp = save_handoff(sid, ho)
                        if hp:
                            out["artifacts"].append(str(hp))
                            out["files"].append({"name": hp.name, "path": str(hp), "tool": "tender.handoff"})
                if name == "tender.parse" and result.get("ok"):
                    _merge_pipe(out, result)
                    data = result.get("data") if isinstance(result.get("data"), dict) else result
                    last_extract = str((data or {}).get("extract_table_markdown") or "")
                    out["wrote"] = True
                    ho = (data or {}).get("handoff") if isinstance(data, dict) else None
                    if isinstance(ho, dict) and ho:
                        from packing_assistant.runtime.session_handoff import save_handoff

                        hp = save_handoff(sid, ho)
                        if hp:
                            out["artifacts"].append(str(hp))
                            out["files"].append(
                                {"name": hp.name, "path": str(hp), "tool": "tender.handoff"}
                            )
                if name.startswith("pack-ship__") and result.get("ok"):
                    data = result.get("data") if isinstance(result.get("data"), dict) else result
                    pack_ship[name.split("__", 1)[-1]] = data
                    if name == "pack-ship__export":
                        last_export_md = str((data or {}).get("markdown") or data.get("markdown") or "")
                    if name == "pack-ship__plan" and packing_list and data.get("source") == "solver":
                        from packing_assistant.tools.pack_ship_solve import plan_record_json, plan_report_md

                        last_export_md, export_name = plan_report_md(data, Path(packing_list).name), "pack-plan"
                        plan_record = plan_record_json(data, Path(packing_list).name)
                if run.state == "waiting_tool":
                    sched.transition(run, "acting")
                if _cancel_requested():
                    return _finish_cancelled()

            # Follow-on writes (still through the engine + sandbox).
            follow: List[Dict[str, Any]] = list(planned.get("follow") or []) + link_writes
            office_reply = ""
            out_dir = _out_root() / _safe_sid(sid) / (exp.id if exp else "ops")
            if last_export_md:
                follow.append(
                    {
                        "name": "write_deliverable",
                        "arguments": {
                            "path": str(out_dir / f"{export_name}.md"),
                            "text": last_export_md,
                        },
                        "tool_label": "pack-ship__plan" if export_name == "pack-plan" else "pack-ship__export",
                    }
                )
                if plan_record:       # the tool result the report's numbers come from, kept beside it
                    follow.append({"name": "write_deliverable", "tool_label": "pack-ship__plan",
                                   "arguments": {"path": str(out_dir / "pack-plan.json"), "text": plan_record}})
            elif last_extract:
                follow.append(
                    {
                        "name": "write_deliverable",
                        "arguments": {
                            "path": str(out_dir / "tender.parse.md"),
                            "text": last_extract,
                        },
                        "tool_label": "tender.parse",
                    }
                )
            for call in follow:
                if _cancel_requested():
                    return _finish_cancelled()
                name = str(call["name"])
                if not sched.transition(run, "waiting_tool"):
                    out["ok"] = False
                    out["error_code"] = run.error_code or "max_steps"
                    out["reply"] = "达到最大步数，请缩小任务范围"
                    messages.append({"role": "assistant", "content": out["reply"]})
                    return _finish()
                result = engine.execute(
                    name,
                    dict(call.get("arguments") or {}),
                    expert_id=eid,
                    intent="run",
                    cancelled=run.cancelled,
                    run_id=run.run_id,
                )
                out["tools_used"].append(name)
                out["tools_run"].append(str(call.get("tool_label") or name))
                out["tool_results"].append(
                    {
                        "name": name,
                        "ok": result.get("ok"),
                        "error_code": result.get("error_code"),
                        "duration_ms": result.get("duration_ms"),
                    }
                )
                if result.get("sandbox"):
                    out["sandbox"].append(result["sandbox"])
                if not result.get("ok"):
                    out["worker_running"] = bool(result.get("worker_running"))
                    if result.get("error_code") == "cancelled" or _cancel_requested():
                        return _finish_cancelled()
                    out["ok"] = False
                    run.error_code = str(result.get("error_code") or "tool_failed")
                    out["error_code"] = run.error_code
                    out["reply"] = f"保存交付物未完成（{run.error_code}），本轮已停止。已完成的文件保留供核对。"
                    if out["worker_running"]:
                        out["reply"] = "保存交付物超时，已请求停止；当前工具仍在退出，资源保持占用，后续步骤不会启动。"
                    sched.transition(run, "failed")
                    messages.append({"role": "assistant", "content": out["reply"]})
                    return _finish()
                path = (result.get("data") or {}).get("path") if isinstance(result.get("data"), dict) else result.get("path")
                if result.get("ok") and path:
                    out["artifacts"].append(str(path))
                    out["wrote"] = True
                    out.setdefault("files", []).append(
                        {
                            "name": Path(str(path)).name,
                            "path": str(path),
                            "tool": str(call.get("tool_label") or name),
                        }
                    )
                messages.append(
                    {
                        "role": "tool",
                        "name": name,
                        "content": str(result.get("error_code") or "ok"),
                        "error_code": result.get("error_code"),
                    }
                )
                if run.state == "waiting_tool":
                    sched.transition(run, "acting")

                if _cancel_requested():
                    return _finish_cancelled()

                if path:
                    # These two tools return Markdown rather than going through
                    # expert_turn's draft writer. Export just the newly saved
                    # document; other posts already attach their Office files.
                    from packing_assistant.expert_turn import _attach_office

                    office = _attach_office({
                        "ok": True, "wrote": True,
                        "files": [out["files"][-1]], "tools_run": [], "reply": "",
                    })
                    known = {item["path"] for item in out["files"]}
                    for item in office.get("files", []):
                        if item["path"] not in known:
                            out["files"].append(item)
                            out["artifacts"].append(item["path"])
                            known.add(item["path"])
                    for tool in office.get("tools_run", []):
                        if tool not in out["tools_run"]:
                            out["tools_run"].append(tool)
                    out["tool_results"].append({
                        "name": "office__export", "ok": office.get("ok", True),
                        "error_code": office.get("error_code"),
                    })
                    if office.get("export_errors"):
                        out["export_errors"] = office["export_errors"]
                    # A current export may finish after cancellation. Register
                    # its actual files before stopping any subsequent work.
                    if _cancel_requested():
                        return _finish_cancelled()
                    if not office.get("ok", True):
                        out["ok"] = False
                        run.error_code = str(office.get("error_code") or "office_export_failed")
                        out["reply"] = str(office.get("reply") or "Office 导出失败，已生成的文件保留。")
                        sched.transition(run, "failed")
                        messages.append({"role": "assistant", "content": out["reply"]})
                        return _finish()
                    office_reply = str(office.get("reply") or "")

            if _cancel_requested():
                return _finish_cancelled()

            if link_data:
                record = link_data.get("record") or {}
                out["tender_packing_link"] = {
                    "container": record.get("container"), "inputs": record.get("inputs"), "clauses": record.get("clauses"),
                    "statements": record.get("statements"), "plan": record.get("plan"), "plan_refusal": record.get("plan_refusal"),
                    "changes_since_previous": record.get("changes_since_previous"), "confirmed_by_person": False}
                out["wrote"] = True
                out["reply"] = str(link_data.get("reply") or "")
            elif pack_ship:
                plan = pack_ship.get("plan") or {}
                connected = bool(planned.get("connected"))
                out["pack_ship"] = {
                    "source": "solver" if connected else "disconnected",
                    **pack_ship,
                }
                out["wrote"] = True
                out["reply"] = (
                    "装柜证据只抄 solver 快照，未重算 xyz。"
                    if connected
                    else "装柜证据只抄 solver；本轮未接通，utilization/can_fit/mid50/系固待办 为 UNSPECIFIED。"
                )
                if packing_list and plan.get("source") == "solver":
                    from packing_assistant.tools.pack_ship_solve import plan_reply

                    out["reply"] = plan_reply(plan, Path(packing_list).name)
                    if plan.get("can_fit") is False:
                        out["ok"] = False
                        run.error_code = out["error_code"] = "cannot_fit"
                elif not packing_list and not connected:
                    from packing_assistant.office_job import files_named_in, job_tree_files

                    tables = [row["name"] for row in job_tree_files() if row["suffix"] in _TABLE_EXTS]
                    if len(files_named_in(text, _TABLE_EXTS)) > 1:
                        out["reply"] += " 任务里点了不止一份表，没法替你选：一次点名一份装箱单。"
                    elif tables:
                        out["reply"] += " 文件夹里有 " + "、".join(tables[:5]) + "；在任务里点名其中一份，就由装箱引擎真算。"
            elif out.get("matrix"):
                out["reply"] = "已按招标节选进矩阵。仍是 AI 草稿，submit_blocked=true，不可递交。"
            elif out["wrote"]:
                names = ", ".join(out["tools_run"]) or "draft"
                who = exp.name if exp else "经营岗"
                out["reply"] = f"{who} 已出内部讨论草稿（{names}）。不可递交。"
            else:
                out["reply"] = "本轮未写盘。"

            if office_reply:
                out["reply"] += " " + office_reply

            if explain_prefix:
                out["reply"] = explain_prefix + "\n\n" + str(out.get("reply") or "")

            if run.state == "acting":
                sched.transition(run, "done" if out["ok"] else "failed")
            elif run.state not in {"done", "failed", "cancelled", "waiting_hitl"}:
                sched.transition(run, "done" if out["ok"] else "failed")
            messages.append({"role": "assistant", "content": out["reply"]})
            return _finish()
    except cancellation.RunCancelled:
        return _finish_cancelled()
    except Exception as exc:  # noqa: BLE001 — surface as failed run, do not invent numbers
        run.error_code = run.error_code or "unspecified"
        try:
            if run.state not in {"done", "failed", "cancelled"}:
                sched.transition(run, "failed")
        except Exception:
            run.state = "failed"
        out["ok"] = False
        out["error_code"] = run.error_code
        out["reply"] = f"agent failed: {str(exc)[:200]}"
        return _finish()
    finally:
        # A persistence/annotation failure during _finish must not strand the
        # scheduler lease and block every later turn in this session.
        def release_resources() -> None:
            if watch_on:
                watch.end(run.run_id)
            sched.release(sid)
            cancellation.clear(run.run_id)

        # A timeout returns before a non-cooperative tool exits. Keep its lease
        # until that actual worker stops; never admit an overlapping writer.
        when_idle = getattr(engine, "when_idle", None)
        if callable(when_idle):
            when_idle(run.run_id, release_resources)
        else:
            release_resources()
