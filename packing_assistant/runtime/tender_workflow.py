"""A bounded parse -> parallel technical/compliance -> controller workflow.

Workers receive immutable JSON handoffs, never another session's live state.
Only this module writes deliverables; injected model runners are read-only.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import hashlib
import json
from pathlib import Path, PureWindowsPath
from queue import Empty, Queue
import re
from threading import Event, RLock, Thread
import time
from uuid import uuid4

from packing_assistant.runtime.worker_context import BudgetExceeded, BudgetLimits, SharedBudget, canonical, tokens, worker_messages
from packing_assistant.sandbox import assert_open, assert_write, guarded_write_text

_ACTIVE: set[str] = set()
_ACTIVE_LOCK = RLock()
TERMINAL = {"done", "failed", "cancelled", "timed_out", "interrupted", "waiting_hitl"}
SKILLS = ("bid-tech", "bid-compliance")


def _folder(root, session, run_id):
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{3,31}", session or "") or PureWindowsPath(session).is_reserved():
        raise ValueError("协作会话ID无效")
    if not re.fullmatch(r"wf-[a-f0-9]{16}", run_id or ""):
        raise ValueError("协作运行ID无效")
    base = Path(root).resolve()
    directory = base / session / "workflows" / run_id
    if directory.resolve() != directory or not directory.is_relative_to(base):
        raise ValueError("协作目录不可为链接或越界")
    return directory


def _atomic(path, value):
    target = assert_write(path)
    temporary = target.with_name(target.name + "." + uuid4().hex + ".tmp")
    try:
        guarded_write_text(temporary, json.dumps(value, ensure_ascii=False, indent=2))
        temporary.replace(target)
    finally:
        if temporary.exists():
            assert_write(temporary).unlink()


def load_workflow(output_root, session_id, run_id):
    path = assert_open(_folder(output_root, session_id, run_id) / "workflow.json")
    value = json.loads(path.read_text(encoding="utf-8"))
    if value.get("run_id") != run_id or value.get("session_id") != session_id:
        raise ValueError("协作记录身份不匹配")
    with _ACTIVE_LOCK:
        active = str(path) in _ACTIVE
    value["active"] = active
    if not active and value.get("state") not in TERMINAL:
        value.update(state="interrupted", ok=False, error_code="process_interrupted")
        for child in value.get("children", []):
            if child.get("status") not in TERMINAL:
                child.update(status="interrupted", error_code="process_interrupted")
    return value


class _Stop:
    def __init__(self, external, deadline):
        self.external, self.deadline, self.local = external, deadline, Event()

    def is_set(self):
        return self.local.is_set() or bool(self.external is not None and self.external.is_set()) or time.monotonic() >= self.deadline

    def set(self):
        self.local.set()

    def wait(self, timeout=None):
        end = time.monotonic() + timeout if timeout is not None else self.deadline
        while not self.is_set() and time.monotonic() < end:
            self.local.wait(min(0.02, max(0, end - time.monotonic())))
        return self.is_set()

    def check(self):
        if time.monotonic() >= self.deadline:
            self.set()
            raise TimeoutError("协作达到时间上限")
        if self.is_set():
            raise InterruptedError("协作已取消，已完成文件保留")


def _analysis(runner, messages, limit, stop):
    queue = Queue(maxsize=1)

    def invoke():
        try:
            queue.put((True, runner(deepcopy(messages), max_tokens=limit, cancel_event=stop)))
        except Exception as exc:
            queue.put((False, exc))

    Thread(target=invoke, name="civil-tender-model", daemon=True).start()
    while True:
        stop.check()
        try:
            success, value = queue.get(timeout=0.02)
        except Empty:
            continue
        stop.check()
        if not success:
            raise RuntimeError("子任务模型分析失败") from value
        if isinstance(value, str):
            return value, {}
        if isinstance(value, dict) and isinstance(value.get("text"), str):
            return value["text"], value.get("usage") or {}
        raise ValueError("子任务模型必须返回文本或{text,usage}")


def _validate_analysis(text, evidence):
    from packing_assistant.tools.tender_review import forbidden_hits
    data = json.loads(text)
    if not isinstance(data, dict) or set(data) - {"conclusions", "unresolved"}:
        raise ValueError("模型不能修改工具交接或请求新任务")
    conclusions, unresolved = data.get("conclusions", []), data.get("unresolved", [])
    if not isinstance(conclusions, list) or not isinstance(unresolved, list) or len(conclusions) > 16 or len(unresolved) > 16 or not (conclusions or unresolved):
        raise ValueError("模型分析结构无效")
    allowed = {item["source_id"] for item in evidence}
    quotes = "\n".join(item["quote"] for item in evidence)
    for item in conclusions:
        if not isinstance(item, dict) or set(item) - {"text", "evidence_refs"} or not isinstance(item.get("text"), str):
            raise ValueError("模型结论结构无效")
        refs = item.get("evidence_refs")
        if not isinstance(refs, list) or not refs or any(not isinstance(ref, str) or ref not in allowed for ref in refs):
            raise ValueError("模型结论缺少有效来源")
        body = item["text"]
        if forbidden_hits(body) or any(v in body for v in ("可以投标", "已通过审查", "已确认合格")):
            raise ValueError("模型分析包含禁止的签认结论")
        numbers = set(re.findall(r"\d+(?:\.\d+)?", "\n".join(e["quote"] for e in evidence if e["source_id"] in refs)))
        if set(re.findall(r"\d+(?:\.\d+)?", body)) - numbers:
            raise ValueError("模型分析包含来源未给出的数字")
        item["origin"], item["verified"] = "model_analysis", False
    if any(not isinstance(item, str) or len(item) > 2000 for item in unresolved):
        raise ValueError("模型缺项结构无效")
    for item in unresolved:
        if forbidden_hits(item) or "可以投标" in item or set(re.findall(r"\d+(?:\.\d+)?", item)) - set(re.findall(r"\d+(?:\.\d+)?", quotes)):
            raise ValueError("模型缺项包含未给数值或禁止结论")
    return conclusions, unresolved


def _source_roles(text, sources):
    # Explicit uploaded roles take precedence over marker-looking document data.
    if any(s.get("role") in {"tender", "response"} for s in sources):
        tender = [s for s in sources if s.get("role") == "tender"]
        return "\n".join(s["text"] for s in (tender or [s for s in sources if s.get("role") != "response"])), sources
    explicit = re.search(r"招标正文[：:]([\s\S]+?)投标响应[：:]([\s\S]+)", text)
    if explicit:
        parts = []
        for group, role in ((1, "tender"), (2, "response")):
            raw, start = explicit.group(group), explicit.start(group)
            start += len(raw) - len(raw.lstrip())
            part = {"source_id": "explicit-" + role, "title": "用户明确" + ("招标正文" if role == "tender" else "投标响应"),
                    "text": raw.strip(), "role": role, "start": start, "end": start + len(raw.strip()), "kind": "user"}
            original = next((s for s in sources if text in s["text"]), None)
            if original:
                part.update(source_id=original["source_id"] + ":" + role, title=original.get("title", part["title"]),
                            parent_source_id=original["source_id"], kind=original.get("kind", "user"))
                part["start"] += original.get("start", 0) + original["text"].find(text)
                part["end"] = part["start"] + len(part["text"])
            parts.append(part)
        return parts[0]["text"], parts
    tender = [s for s in sources if s.get("role") == "tender"]
    responses = [s for s in sources if s.get("role") == "response"]
    if tender:
        return "\n".join(s["text"] for s in tender), sources
    if responses:
        # Explicit responses must never be re-parsed as tender requirements.
        return "\n".join(s["text"] for s in sources if s.get("role") != "response"), sources
    return "\n".join(s["text"] for s in sources), sources


def _response_comparison(requirements, sources):
    """One row per distinct tender line, with candidate response lines and numeric mismatches.

    The matching itself lives in tools/tender_response_match.py, where every mechanism is
    switched by a flag and scored against test/benchmarks/tender_response/cases.json.
    """
    from packing_assistant.tools.tender_response_match import compare_responses

    return compare_responses(requirements, sources)


def _comparison_status_text(row):
    """Status cell for the Markdown table: the status code, plus what the numbers say."""
    notes = [c["note"] for c in row.get("conflicts") or []]
    return row["status"] + ("：" + "；".join(notes) if notes else "")


def _comparison_unresolved(row):
    if row.get("conflicts"):
        return [row["requirement_ref"] + "：" + c["note"] for c in row["conflicts"]]
    return [row["requirement_ref"] + "：" + ("候选响应原文待人工核验" if row["response_evidence"] else "未检出对应响应证据")]


def _unreadable(value):
    """[{title, role, reason}]: files the caller was pointed at and got no text out of. Bounded like sources."""
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > 24 or any(not isinstance(item, dict) for item in value):
        raise ValueError("未读出文件清单无效")
    result = []
    for item in value:
        title, role = item.get("title"), item.get("role", "reference")
        if not isinstance(title, str) or not title.strip() or role not in {"tender", "response", "reference"}:
            raise ValueError("未读出文件清单无效")
        entry = {"title": title.strip()[:120], "role": role, "reason": str(item.get("reason") or "未读出")[:120]}
        if entry["title"] not in {r["title"] for r in result}:
            result.append(entry)
    return result


def _properties_of(source):
    """The document properties of a job file of ours (author, last modified by, company …); {} for pasted text, for a
    file outside the job folder and for a file that holds none."""
    path = str(source.get("path") or "")
    if not path or source.get("kind") != "job_file":
        return {}
    from packing_assistant.office_job import job_root
    from packing_assistant.tools import file_properties

    root = job_root().resolve()
    target = (root / path).resolve()
    try:
        target.relative_to(root)
    except ValueError:
        return {}
    return file_properties.read(target) if target.is_file() else {}


def _checked_entry(source):
    """One source as the check record keeps it: the hash of the text as read, and the path of a job file so
    that it can be read again."""
    from packing_assistant.tools.bid_check_record import entry

    return entry(str(source.get("title") or source["source_id"]), str(source.get("role") or "reference"), source["text"],
                 kind=source.get("kind"), path=source.get("path"), source_id=source["source_id"])


def _evidence_files(sources):
    """Reference files count as evidence only beside a declared tender: without one, _source_roles reads
    them as the tender text itself. What the user typed this turn is never an evidence file."""
    if not any(s.get("role") == "tender" for s in sources):
        return []
    return [{"title": str(s.get("title") or s["source_id"]), "text": s["text"]} for s in sources
            if s.get("role", "reference") == "reference" and s.get("kind") != "user"]


def run_tender_workflow(text, *, session_id, output_root, sources=None, confirmed=False,
                        cancel_event=None, model_runner=None, budget=None, parallel=True, on_event=None,
                        unreadable=None):
    """Run an isolated workflow; model_runner(messages, *, max_tokens, cancel_event).

    A model runner returns a JSON string or {text: JSON-string, usage: {...}}.
    It must not perform tool calls or filesystem writes. Cancelled work is never
    resumed automatically; load_workflow truthfully recovers terminal artifacts.

    ``unreadable`` lists files that were pointed at and gave no text. They take no part in the
    comparison, and the drafts say so: a row they might have answered is 未能判断, not 未响应.
    """
    from packing_assistant.runtime.civil_config import CONFIRM, CONFIRM_EN, decide_gate, load_config
    from packing_assistant.tools.tender_parse import (parse_tender_text, build_response_matrix,
        build_workbench_extract_table, build_tech_outline_from_handoff)
    from packing_assistant.tools.tender_review import gap_rows, review_draft
    from packing_assistant.expert_turn import _compliance_gaps_md
    limits = BudgetLimits.from_value(budget)
    rid = "wf-" + uuid4().hex[:16]
    directory = _folder(output_root, session_id, rid)
    if not isinstance(text, str) or not text.strip():
        raise ValueError("请提供明确的招标资料")
    empty = {"ok": False, "run_id": rid, "parent_run_id": rid, "session_id": session_id,
             "files": [], "artifacts": [], "wrote": False, "children": [], "submit_blocked": True}
    # These three skills create low-risk internal drafts. The caller admits the
    # current write intent; confirmation never upgrades P0 or submission status.
    config = load_config()
    if not config.allow_write():
        return {**empty, "state": "failed", "error_code": "permission_denied", "reply": "只读模式未生成协作文件"}
    if decide_gate(intent="run", risk="low", confirmed=confirmed is True, cfg=config) == "hitl":
        return {**empty, "ok": True, "state": "waiting_hitl", "hitl_pending": True,
                "reply": "approval=untrusted：本轮协作写盘须确认句「" + CONFIRM + "」（或 / or: " + CONFIRM_EN + "）。本轮未写盘。"}
    supplied = deepcopy(sources) if sources is not None else [{"source_id": "current-input", "title": "当前招标资料", "text": text, "kind": "user"}]
    if not isinstance(supplied, list) or not supplied or any(not isinstance(s, dict) or not isinstance(s.get("text"), str) for s in supplied):
        raise ValueError("来源必须包含明确正文")
    for index, source in enumerate(supplied):
        source["source_id"] = str(source.get("source_id") or source.get("id") or f"source-{index + 1}")
    if len({s["source_id"] for s in supplied}) != len(supplied):
        raise ValueError("来源ID不可重复")
    for source in supplied:
        if source.get("role", "reference") not in {"tender", "response", "reference"}:
            raise ValueError("来源角色无效")
        if type(source.get("start", 0)) is not int or source.get("start", 0) < 0:
            raise ValueError("来源位置无效")
    unread = _unreadable(unreadable)
    tender_text, supplied = _source_roles(text, supplied)
    # Sources that came with a role are documents: every word of the tender text is the tender's, and
    # a document says "已经取得许可证的须提供复印件" without anybody of our side speaking. Only a request
    # typed into one box is read clause by clause for whose words they are (tools/tender_facts.py).
    declared = any(s.get("role") in {"tender", "response"} for s in supplied)
    lock, ledger = RLock(), SharedBudget(limits)
    started = time.monotonic()
    stop = _Stop(cancel_event, started + limits.timeout_s)
    state = {**empty, "schema": "civil.tender.workflow.v1", "state": "planning", "started_at": time.time(),
             "parallel": bool(parallel), "directory": str(directory), "reply": "招标协作处理中",
             "tool_input_chars": len(tender_text) + sum(len(s["text"]) for s in supplied if s.get("role") == "response")}
    source_pointers = [{key: s[key] for key in ("source_id", "title", "start", "end", "role", "kind") if key in s}
                       for s in supplied]
    manifest = directory / "workflow.json"

    def publish(event=None):
        with lock:
            state["metrics"] = {**ledger.snapshot(), "duration_ms": round((time.monotonic() - started) * 1000)}
            state["metrics"]["quality"] = state.get("quality", {})
            state["metrics"]["tool_input_chars"] = state["tool_input_chars"]
            state["metrics"]["tool_calls"] = int("handoff_hash" in state) + sum(c.get("metrics", {}).get("tool_calls", 0) for c in state["children"])
            state["aggregate_metrics"] = dict(state["metrics"])
            state["artifacts"] = [f["path"] for f in state["files"]]
            state["wrote"] = bool(state["files"])
            _atomic(manifest, state)
            if event and on_event:
                try:
                    on_event({"parent_run_id": rid, **event})
                except Exception:
                    pass  # A disconnected observer cannot invalidate completed work.

    def file_saved(path, tool, child=None):
        item = {"name": path.name, "path": str(path), "tool": tool}
        with lock:
            state["files"].append(item)
            if child is not None:
                child["files"].append(item)
            publish()

    def document(folder, name, markdown, child=None):
        from packing_assistant.office_job import export_md_to_docx, tables_from_md, write_xlsx
        stop.check()
        path = guarded_write_text(folder / (name + ".md"), markdown)
        file_saved(path, name, child)
        stop.check()
        word = export_md_to_docx(path)
        if word is None:
            raise RuntimeError("Word导出未生成文件")
        file_saved(word, "office__docx", child)
        stop.check()
        sheets = tables_from_md(markdown)
        if sheets:
            excel = write_xlsx(path.with_suffix(".xlsx"), sheets)
            file_saved(excel, "office__xlsx", child)

    with _ACTIVE_LOCK:
        _ACTIVE.add(str(manifest))
    try:
        stop.check()
        ledger.reserve("parse", tokens({"task": "本地解析当前选定招标资料", "sources": source_pointers}))
        state["state"] = "parsing"
        publish({"kind": "workflow", "state": "parsing"})
        parsed = parse_tender_text(tender_text, source="workflow:" + rid, sides="none" if declared else "auto")
        stop.check()
        requirements = parsed.get("requirements", [])
        if not requirements:
            raise ValueError("未从当前资料检出可解析招标要求")
        matrix = build_response_matrix(requirements)
        handoff = parsed["handoff"]
        unread = _unreadable(list(handoff.get("unreadable") or []) + unread)
        if unread:
            handoff["unreadable"] = unread  # the three drafts read it off the handoff
            state["unreadable"] = unread
        evidence = []
        for requirement in requirements:
            quote = str(requirement.get("exact_text") or "")
            matched = [s for s in supplied if s.get("role") != "response" and quote and quote in s["text"]]
            for source in matched:
                evidence.append({"source_id": source["source_id"], "title": source.get("title", ""),
                    "requirement_ref": requirement.get("requirement_ref", ""), "quote": quote,
                    "kind": requirement.get("item_kind", ""), "category": requirement.get("category", ""),
                    "start": source.get("start", 0) + source["text"].find(quote),
                    "end": source.get("start", 0) + source["text"].find(quote) + len(quote)})
        comparison = _response_comparison(requirements, supplied)
        snapshot = {"schema": "civil.tender.handoff.v1", "sources": supplied, "evidence": evidence,
                    "handoff": handoff, "matrix": matrix, "response_comparison": comparison}
        immutable = canonical(snapshot)
        state["handoff_hash"] = hashlib.sha256(immutable.encode()).hexdigest()
        ledger.reserve("controller", tokens({"task": "核对缺项依据冲突并汇总", "sources": source_pointers,
            "handoff_hash": state["handoff_hash"], "requirements": len(requirements),
            "duration_days": handoff.get("duration_days"), "submit_blocked": True}))
        handoff_path = directory / "handoff.json"
        _atomic(handoff_path, snapshot)
        file_saved(handoff_path, "tender.handoff")
        document(directory, "tender-extract", build_workbench_extract_table(parsed, project_name="当前招标协作"))
        state["state"] = "running"

        contexts = {}
        for skill in SKILLS:
            selected = [e for e in evidence if skill == "bid-compliance" or e["kind"] in {"scoring_point", "special", "star"}]
            if skill == "bid-compliance":
                selected += [{**entry, "kind": "response_candidate", "requirement_ref": row["requirement_ref"]}
                             for row in comparison for entry in row["response_evidence"]]
            child = {"task_id": "worker-" + skill, "parent_run_id": rid, "skill": skill, "status": "pending",
                     "handoff_hash": state["handoff_hash"], "conclusions": [], "evidence": selected,
                     "unresolved": [], "files": [], "metrics": {}}
            state["children"].append(child)
            data = {"handoff": snapshot["handoff"], "evidence": selected}
            if skill == "bid-compliance":
                data["matrix"] = snapshot["matrix"]
                data["response_comparison"] = comparison
                data["response_sources"] = [s for s in source_pointers if s.get("role") == "response"]
            messages = worker_messages(skill, "按工具交接编制技术响应提纲" if skill == "bid-tech" else "逐项检查响应依据与缺项", data)
            contexts[skill] = messages
            if model_runner is None:
                # Nothing is sent anywhere: the drafts are made by tools, and a model's window is no limit for them.
                # A real tender's handoff is several times the 32 768 a worker may hold - the check used to stop
                # here with "子任务完整输入与输出预留超出预算" before a single draft was written.
                ledger.reserve(child["task_id"], 0, 0, model=False)
                continue
            try:
                ledger.reserve(child["task_id"], tokens(messages) + 8 * len(messages) + 8, limits.output_tokens, model=True)
            except BudgetExceeded as exc:
                # The material is never cut to fit a window. The tools still write their drafts; the model is
                # not asked, and the child says why.
                child["model_skipped"] = str(exc)
                ledger.reserve(child["task_id"], 0, 0, model=False)
        publish({"kind": "workflow", "state": "running"})

        def worker(child):
            begun = time.monotonic()
            skill = child["skill"]
            tool_calls = 0
            try:
                stop.check()
                with lock:
                    child.update(status="running", started_at=time.time())
                    publish({"kind": "worker", "task_id": child["task_id"], "skill": skill, "state": "running"})
                local = json.loads(immutable)  # No mutable sharing or live session reads.
                tool_calls += 1
                if skill == "bid-tech":
                    outline = build_tech_outline_from_handoff(local["handoff"], project_name="当前招标协作")
                    markdown = str(outline["markdown"])
                    child["conclusions"] = [{"text": "技术目录已依据当前评分点组织" if outline.get("from_extracted_scores") else "原文未检出评分点，目录待核",
                                              "origin": "tool", "evidence_refs": sorted({e["source_id"] for e in child["evidence"]})}]
                    if not outline.get("from_extracted_scores"):
                        child["unresolved"].append("未提供明确评分点；技术响应范围待核")
                else:
                    # One table. The response documents used to be compared in a second table appended
                    # below a first one that knew nothing of them - "未响应" above, the candidate quote and
                    # the numeric conflict below. The comparison now answers the rows themselves.
                    markdown = _compliance_gaps_md(local["handoff"], local["matrix"], comparison=local["response_comparison"],
                                                   evidence=_evidence_files(local["sources"]),
                                                   checked=[_checked_entry(s) for s in local["sources"]],
                                                   responses=[{"title": str(s.get("title") or s["source_id"]), "text": s["text"],
                                                               "properties": _properties_of(s)}
                                                              for s in local["sources"] if s.get("role") == "response"])
                    child["response_comparison"] = local["response_comparison"]
                    child["unresolved"] = [str(g.get("title") or g.get("req_id")) for g in gap_rows(local["matrix"])]
                    child["unresolved"].extend(item for row in local["response_comparison"] for item in _comparison_unresolved(row))
                    missed = local["handoff"].get("unreadable") or []
                    child["unresolved"].extend(f"{u['title']} 未读出（{u['reason']}）：其中内容未参与对照" for u in missed)
                    if not any(s.get("role") == "response" for s in local["sources"]):
                        child["unresolved"].append("响应资料给了但未读出，不能认定未响应，也不能认定已响应"
                                                   if any(u["role"] != "tender" for u in missed)
                                                   else "用户未明确提供投标响应资料，不能认定已响应")
                    child["conclusions"] = [{"text": "已逐项整理响应缺项，未代判投标资格", "origin": "tool",
                                              "evidence_refs": sorted({e["source_id"] for e in child["evidence"]})}]
                stop.check()
                document(directory / child["task_id"], skill, markdown, child)
                if child.get("model_skipped"):
                    raise BudgetExceeded(child["model_skipped"])   # after the draft is on disk, not instead of it
                if model_runner is not None:
                    ledger.start_model(child["task_id"])
                    answer, usage = _analysis(model_runner, contexts[skill], limits.output_tokens, stop)
                    ledger.finish_model(child["task_id"], answer, usage)
                    conclusions, unresolved = _validate_analysis(answer, child["evidence"])
                    child["conclusions"].extend(conclusions)
                    child["unresolved"].extend(unresolved)
                    rendered = "# 专业分析（模型意见，未核验）\n\n" + "\n".join(
                        "- " + item["text"] + " [" + ", ".join(item["evidence_refs"]) + "]" for item in conclusions)
                    document(directory / child["task_id"], "model-analysis", rendered, child)
                child["status"] = "done"
            except Exception as exc:
                child.update(status="timed_out" if isinstance(exc, TimeoutError) else "cancelled" if isinstance(exc, InterruptedError) else "failed",
                             error_code="budget_exceeded" if isinstance(exc, BudgetExceeded) else type(exc).__name__,
                             error="子任务未完成，已生成文件保留")
            finally:
                child["metrics"] = {"duration_ms": round((time.monotonic() - begun) * 1000), "tool_calls": tool_calls,
                    "evidence_count": len(child["evidence"]), "unresolved_count": len(child["unresolved"])}
                _atomic(directory / child["task_id"] / "task.json", child)
                publish({"kind": "worker", "task_id": child["task_id"], "skill": skill, "state": child["status"]})

        with ThreadPoolExecutor(max_workers=2 if parallel else 1, thread_name_prefix="civil-tender") as pool:
            futures = [pool.submit(worker, child) for child in state["children"]]
            for future in futures:
                future.result()
        stop.check()
        combined = "\n".join(Path(item["path"]).read_text(encoding="utf-8") for child in state["children"] for item in child["files"] if item["path"].endswith(".md"))
        # with no model there is no window the controller's summary has to fit into: on a real tender the list of
        # open items alone is longer than a small budget, and the run would fail after all its work was done
        ledger.reserve("controller-review", tokens({"children": [
            {key: child[key] for key in ("skill", "status", "conclusions", "unresolved")}
            for child in state["children"]]}) if model_runner is not None else 0)
        review = review_draft(draft=combined, matrix=snapshot["matrix"])
        conflicts = []
        categories = {}
        for entry in evidence:
            nums = tuple(re.findall(r"\d+(?:\.\d+)?", entry["quote"]))
            if entry["category"] == "schedule" and nums:
                categories.setdefault("schedule", set()).add(nums)
        if len(categories.get("schedule", [])) > 1:
            conflicts.append({"field": "schedule", "status": "needs_review", "note": "原文存在多个工期/时间数值，请逐项核对适用对象，未自动选值"})
        # 响应里写的数与招标写的数对不上：和「原文多个工期」一样进冲突清单，只陈述、不裁决。
        conflicts.extend({"field": c["label"], "status": "needs_review", "note": row["requirement_ref"] + "：" + c["note"]}
                         for row in comparison for c in row.get("conflicts") or [])
        response_gaps = [{"req_id": row["requirement_ref"], "title": row["requirement"], "status": row["status"],
                          "response_evidence": row["response_evidence"]} for row in comparison]
        unmapped = [{"requirement_ref": req.get("requirement_ref", ""), "status": "source_not_located"}
                    for req in requirements if not any(e["requirement_ref"] == req.get("requirement_ref") for e in evidence)]
        state["review"] = {**review, "gaps": review["gaps"] + response_gaps, "unmapped_requirements": unmapped,
                           "conflicts": conflicts, "evidence_count": len(evidence),
                           "response_comparison": comparison,
                           "response_evidence_supplied": any(s.get("role") == "response" for s in supplied),
                           "unreadable": unread, "evidence_files": [e["title"] for e in _evidence_files(supplied)],
                           "handoff_unchanged": hashlib.sha256(canonical(snapshot).encode()).hexdigest() == state["handoff_hash"]}
        state["review"].update(n_gaps=len(state["review"]["gaps"]), 缺项=state["review"]["gaps"])
        state["quality"] = {"requirements": len(requirements), "requirements_with_sources": len(requirements) - len(unmapped),
            "response_candidates": sum(bool(row["response_evidence"]) for row in comparison), "responses_verified": 0,
            "unresolved": sum(len(child["unresolved"]) for child in state["children"]), "conflicts": len(conflicts),
            "forbidden_claims": len(review["forbidden_hits"]), "children_completed": sum(c["status"] == "done" for c in state["children"]),
            "unreadable_files": len(unread)}
        state["ok"] = all(c["status"] == "done" for c in state["children"]) and not review["forbidden_hits"]
        if any(c.get("error_code") == "budget_exceeded" for c in state["children"]):
            state["error_code"] = "budget_exceeded"
        state["state"] = "reviewing"
        state["reply"] = "招标协作已完成内部草稿；缺项与冲突待人工核对，不可递交。" if state["ok"] else "部分子任务未完成，已生成草稿和来源保留。"
        skipped = next((c["model_skipped"] for c in state["children"] if c.get("model_skipped")), "")
        if skipped:
            state["reply"] = f"工具草稿已全部生成；模型分析未做（{skipped}）。资料不会为了塞进模型窗口而被截断。缺项与冲突待人工核对，不可递交。"
        if unread:
            state["reply"] += f" 有 {len(unread)} 份文件没读出来（{'、'.join(u['title'] for u in unread)}），相关行标为「未能判断」，不是「未响应」。"
        md = "# 招标协作汇总\n\nAI 草稿，不可递交；不代替资格、废标或签认判断。\n\n"
        md += f"交接SHA256：{state['handoff_hash']}\n\n| 子任务 | 状态 | 未解决项 |\n| --- | --- | --- |\n"
        for child in state["children"]:
            md += f"| {child['skill']} | {child['status']} | {len(child['unresolved'])} |\n"
            md += ""
        md += "\n## 缺项与冲突\n\n" + "\n".join("- " + s for child in state["children"] for s in child["unresolved"])
        md += "\n" + "\n".join("- " + c["note"] for c in conflicts)
        md += "\n\n## 来源\n\n" + "\n".join("- [" + s["source_id"] + "] " + str(s.get("title", "")) for s in supplied)
        if unread:
            md += "\n\n## 未读出的文件\n\n" + "\n".join(f"- {u['title']}（{u['role']}）：{u['reason']}" for u in unread)
            md += "\n\n这些文件未参与解析和对照；相关行是「未能判断」，不是「未响应」。"
        # Which texts this run read, and what moved since the last run of this task (tools/bid_check_record.py).
        from packing_assistant.tools import bid_check_record as check_record

        checked = [_checked_entry(s) for s in supplied]
        gaps_md = next((Path(item["path"]).read_text(encoding="utf-8") for child in state["children"] if child["skill"] == "bid-compliance"
                        for item in child["files"] if item["name"] == "bid-compliance.md"), "")
        record = check_record.build(kind="workflow", session_id=session_id, inputs=checked, drafts=[], rows=check_record.rows_of(gaps_md),
                                    unreadable=unread, run_id=rid, handoff_sha256=state["handoff_hash"])
        earlier = [assert_open(p) for p in directory.parent.glob("wf-*/" + check_record.WORKFLOW_RECORD) if p.parent != directory]
        previous = check_record.latest(earlier)
        md += "\n\n## 核对对象\n\n" + "\n".join(check_record.inputs_table(checked))
        md += "文字一改，sha256 就变：本次各表只对上面这些文字成立。\n"
        if previous is not None:
            md += "\n" + "\n".join(check_record.comparison_section(previous, record))
            moved = check_record.compare(previous, record)
            state["check"] = {"compared_with": previous.get("run_id"), "inputs_changed": [i["title"] for i in moved["inputs"]],
                              "rows_changed": [r["label"] for r in moved["rows"]]}
        ledger.reserve("controller-output", 0, tokens(md) if model_runner is not None else 0)
        document(directory, "collaboration-review", md)
        record["drafts"] = [{"name": Path(item["path"]).name, "path": Path(item["path"]).relative_to(directory).as_posix(),
                             "sha256": check_record.sha(Path(item["path"]).read_text(encoding="utf-8"))}
                            for item in state["files"] if item["path"].endswith(".md")]
        record_path = directory / check_record.WORKFLOW_RECORD
        _atomic(record_path, record)
        file_saved(record_path, "bid.check")
        state.setdefault("check", {})["record"] = str(record_path)
        state["state"] = "done" if state["ok"] else "failed"
    except Exception as exc:
        state.update(ok=False, state="timed_out" if isinstance(exc, TimeoutError) else "cancelled" if isinstance(exc, InterruptedError) else "failed",
            error_code="budget_exceeded" if isinstance(exc, BudgetExceeded) else type(exc).__name__,
            reply=str(exc) if isinstance(exc, (BudgetExceeded, TimeoutError, InterruptedError)) else "招标协作未完成，请核对资料与输出目录；已生成文件保留。")
        for child in state["children"]:
            if child["status"] not in TERMINAL:
                child["status"] = state["state"]
    finally:
        try:
            publish({"kind": "workflow", "state": state["state"]})
        finally:
            with _ACTIVE_LOCK:
                _ACTIVE.discard(str(manifest))
    return state
