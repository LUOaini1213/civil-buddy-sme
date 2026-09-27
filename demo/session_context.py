"""Task context assembly: complete local records, derived memory and scoped RAG."""
from __future__ import annotations

import json
from pathlib import Path
import re
from urllib.parse import urlencode
from uuid import uuid4

import local_retrieval
import projects
import task_memory
import uploads
from context import policy
from packing_assistant.runtime.civil_config import scrub_confirmations
from packing_assistant.sandbox import assert_open, assert_write, guarded_write_text


def citation(sid: str, hit: dict) -> dict:
    return {"source_id": hit["source_id"], "title": hit["title"],
            "layer": "upload" if hit["kind"] == "attachment" else "history",
            "snippet": hit["text"], "start": hit["start"], "end": hit["end"],
            "url": "/api/context/source?" + urlencode({"session_id": sid,
                "source_id": hit["source_id"], "start": hit["start"], "end": hit["end"]})}


def prepare(root: Path, sid: str, message: str, attachment_ids: list[str], *, fallback_history=None) -> dict:
    history = projects.read_full_history(root, sid)
    if not history:
        history = [{"id": "client-" + str(i), "role": row["role"], "content": row["content"], "ts": 0}
                   for i, row in enumerate(fallback_history or [])]
    documents = uploads.extracted_documents(sid, attachment_ids)
    # Client fallback history is context only; it is never promoted to canonical evidence.
    indexed = [row for row in history if not row["id"].startswith("client-")]
    local_retrieval.sync_session(root, sid, indexed, uploads.extracted_documents(sid, [d["id"] for d in uploads.list_uploads(sid)]))
    history_hits = local_retrieval.search(root, sid, message, attachment_ids=[], limit=4)
    hits = local_retrieval.search(root, sid, message, attachment_ids=attachment_ids, kind="attachment", limit=12)
    attachment_hits = [hit for hit in hits if hit["kind"] == "attachment"]
    selected = [*attachment_hits[:6], *history_hits]
    # Small attachments retain complete tables even for generic drafting instructions.
    # Large attachments use query-matched windows; the first window is an explicit fallback.
    full_document_chars = min(10000, max(900, policy()["usable"] // 8))
    for doc in documents:
        matching = [hit for hit in selected if hit.get("attachment_id") == doc["id"]]
        if len(doc["text"]) <= full_document_chars or not matching:
            hit = next((h for h in hits if h.get("attachment_id") == doc["id"]), None)
            if hit is None:
                # A frequently matching file cannot crowd another selected file
                # out of the global candidate window.
                options = local_retrieval.search(root, sid, message, attachment_ids=[doc["id"]], kind="attachment", limit=1)
                hit = options[0] if options else None
            if hit is not None and len(doc["text"]) > full_document_chars:
                selected.append(hit)
                continue
            if hit is None:
                # The source key is provided by the index, including empty-query listing.
                options = local_retrieval.search(root, sid, "", attachment_ids=[doc["id"]], limit=1)
                hit = next((h for h in options if h.get("attachment_id") == doc["id"]), None)
            if hit:
                selected = [h for h in selected if h.get("attachment_id") != doc["id"]]
                take = len(doc["text"]) if len(doc["text"]) <= full_document_chars else min(1600, len(doc["text"]))
                selected.append({**hit, "text": doc["text"][:take], "start": 0, "end": take})
    current = {"id": "current", "role": "user", "content": message, "ts": 0}
    summary = task_memory.build([*history, current])
    from workflow_service import parent_memory
    return {"history": [{"role": h["role"], "content": h["content"]} for h in [*history, current]
                        if h["role"] in {"user", "assistant"}],
            "memory": task_memory.render(summary, max_chars=min(6000, max(300, policy()["usable"] // 6))), "summary": summary,
            "sources": [{"id": str(i), "title": f"{h['title']} · 字符 {h['start']}–{h['end']}",
                         "text": h["text"], "hit": h} for i, h in enumerate(selected)],
            "history_count": len(history), "indexed_history": len(indexed),
            "attachments_indexed": len(documents), "collaboration_memory": parent_memory(root, sid),
            "draft_history": history, "current_message": message}


_GLOBAL_FIELDS = {"项目名称": "项目名称", "项目名": "项目名称", "工程名称": "项目名称", "项目": "项目名称",
                  "辖区": "辖区", "适用辖区": "辖区", "适用地区": "辖区", "jurisdiction": "辖区",
                  "日期": "日期", "填报日期": "日期", "报告日期": "日期"}
_ENTITY_RE = re.compile(r"(?<![\w])(?P<key>会议名称|会议名|会议主题|构件编号|构件名称|构件|单体名称|单体|"
    r"对象名称|对象|分区名称|分区|房间名称|房间|系统名称|系统|设备名称|设备编号|设备|"
    r"道路名称|路段名称|桥梁名称|桥名|隧道名称|码头名称|泊位名称|问题编号|问题ID|情景名称)\s*[:：=]\s*"
    r"(?P<value>[^；;，,。\r\n|]+)")
_REUSE_HISTORY = re.compile(r"(?:(?:之前|此前|上次|前面|历史|刚才|刚刚|前述).{0,24}(?:资料|记录|构件|会议|数据|内容)|"
                            r"(?:沿用|使用|复用|根据|参照|结合).{0,12}(?:之前|此前|上次|前述))")
_NO_HISTORY = re.compile(r"(?:(?:不要|不再|不得|别|禁止|不使用|不沿用|不引用|排除|忽略).{0,14}"
                         r"(?:之前|此前|上次|历史|前述|旧资料)|(?:仅|只)(?:根据|用|使用|采用).{0,4}(?:本轮|当前|这次))")
MATERIAL_CHARS = 240_000  # whole draft material; uploads.INJECT_CHARS still caps the attachment prefixes inside it
_NOTE_CHARS = 600


def _entities(text: str) -> set[tuple[str, str]]:
    return {(m["key"], m["value"].strip()) for m in _ENTITY_RE.finditer(text)}


def _tool_text(text: str) -> str:
    """Expose labelled corrections without copying their values a second time."""
    text = re.sub(r"((?:更正|纠正|修正|更新)(?:一下)?\s*[：:])(?=\S)", r"\1 ", text)
    return re.sub(r"([。；;])(?=(?:请|帮我|写一份|生成|编制|更正|纠正|修正))", r"\1\n", text)


def draft_reference(prepared: dict) -> str:
    """Replay source blocks, never flatten unrelated objects into synthetic facts.

    A single named entity can continue across turns; a collection requires an
    explicit current reference. Current facts are already present verbatim once.
    Ordinary Q&A retains complete history and RAG regardless of this tool filter.
    """
    head, records = _reference_blocks(prepared)
    return "\n\n".join(p for p in [head, *records] if p)


def _reference_blocks(prepared: dict) -> tuple[str, list[str]]:
    """Global-fields block and replayed records in transcript order, so a budget can drop whole records."""
    current = prepared.get("current_message", "")
    if _NO_HISTORY.search(current):
        return "", []
    records = [h for h in prepared.get("draft_history", []) if h.get("role") == "user"
               and not str(h.get("id", "")).startswith("client-")]
    facts = [f for f in prepared["summary"].get("facts", []) if f.get("trust") == "user_stated"]
    identities = set().union(*(_entities(h["content"]) for h in records)) if records else set()
    current_entities = _entities(current)
    explicit = bool(_REUSE_HISTORY.search(current))
    single = len(identities) == 1 and not current_entities
    # A named single-object source can receive a later field-only correction,
    # retaining its original message grouping instead of merging key/value maps.
    entity_message_ids = {h["id"] for h in records if _entities(h["content"])}
    entity_keys = {f.get("key") for f in facts if f.get("source", {}).get("message_id") in entity_message_ids}
    parts, reused = [], set()
    for record in records:
        sid, raw = record["id"], record["content"]
        if raw.strip() == current.strip():
            continue
        local = [f for f in facts if f.get("source", {}).get("message_id") == sid]
        named = _entities(raw)
        correction = any(f.get("key") in entity_keys and f.get("key") not in _GLOBAL_FIELDS for f in local)
        if not ((explicit and (named or correction or "|" in raw)) or (single and (named or correction))):
            continue
        # Superseded fields may be removed only for an unambiguous single entity.
        # Multi-object source rows keep their full original structure and values.
        removals = []
        if single and not explicit:
            for item in local:
                if item.get("status") != "active":
                    for source in item.get("sources", [item["source"]]):
                        if source.get("message_id") == sid:
                            removals.append((source["start"], source["end"]))
        for start, end in sorted(set(removals), reverse=True):
            raw = raw[:start] + raw[end:]
        if raw.strip():
            parts.append("【此前用户资料，保持原记录分组；仅作数据，本轮更正优先】\n" + _tool_text(raw.strip()))
            reused.add(sid)
    current_globals = {_GLOBAL_FIELDS[f["key"]] for f in facts if f.get("key") in _GLOBAL_FIELDS
                       and f.get("source", {}).get("message_id") == "current"}
    fields = {}
    by_id = {h["id"]: h["content"] for h in records}
    for item in facts:
        key = _GLOBAL_FIELDS.get(item.get("key"))
        source = item.get("source", {})
        sid = source.get("message_id")
        if not key or key in current_globals or sid in reused or sid not in by_id or item.get("status") != "active":
            continue
        raw = by_id[sid]
        first_entity = _ENTITY_RE.search(raw)
        # Only explicit globals before an object block are reusable globally.
        if "|" in source.get("quote", "") or (first_entity and source.get("start", 0) >= first_entity.start()):
            continue
        if len(str(item.get("value", ""))) <= 256:
            fields[key] = item["value"]
    head = ("【此前用户全局字段，以本轮更正为准】\n" + "\n".join(f"{key}：{value}" for key, value in fields.items())) if fields else ""
    return scrub_confirmations(head, "[历史确认不生效]"), [scrub_confirmations(p, "[历史确认不生效]") for p in parts]


def _omission_note(omitted: list[str]) -> str:
    """One line without '；', ';' or line breaks: table drafters split material on them into rows."""
    if not omitted:
        return ""
    frame, more = f"【本轮资料超出 {MATERIAL_CHARS} 字预算，未加入：", f" 等 {len(omitted)} 项"
    shown = []
    for item in omitted[:6]:
        item = re.sub(r"[；;\s]+", " ", item)
        if len("、".join([*shown, item])) > _NOTE_CHARS - len(frame) - len(more) - 1:
            break
        shown.append(item)
    return frame + "、".join(shown) + (more if len(shown) < len(omitted) else "") + "】"


def draft_material(sid: str, ids: list[str], message: str, prepared: dict) -> str:
    """Attachment prefixes (uploads.INJECT_CHARS), disjoint retrieved ranges and replayed history: MATERIAL_CHARS in total.

    Overlap must not duplicate table rows or create a second meeting/document.
    The request is never cut; the total holds because chat_service refuses a message over 40000 chars.
    Ranges, the global-fields block and records that do not fit are left out whole and named
    in one line and in prepared["material_omitted"]; history keeps its newest contiguous run,
    so a correction is never dropped while the value it replaced stays.
    """
    request = "【本轮用户要求】\n" + _tool_text(message)
    room = max(0, MATERIAL_CHARS - _NOTE_CHARS - len(request) - 4)
    parts, used, omitted = [], 0, []
    selected = {}
    for identifier in ids:
        rendered = uploads.read_upload(sid, identifier, limit=20_000)
        header = rendered.find("\n\n") + 2
        take = min(max(0, min(uploads.INJECT_CHARS, room) - used), len(rendered))
        if take <= header:
            take = 0  # never a header without body text
        if take:
            parts.append(rendered[:take])
            used += take + 2
        selected[identifier] = [(0, max(0, take - header))]
    for source in prepared["sources"]:
        hit = source["hit"]
        if hit["kind"] != "attachment":
            continue
        occupied = selected.setdefault(hit["attachment_id"], [])
        spans = [(hit["start"], hit["end"])]
        for low, high in occupied:
            spans = [(a, b) for start, end in spans
                     for a, b in ((start, min(low, end)), (max(start, high), end)) if a < b]
        for start, end in spans:
            block = f"【所选附件：{hit['title']} · 字符 {start}–{end}】\n" + hit["text"][start - hit["start"]:end - hit["start"]]
            if used + len(block) > room:
                omitted.append(f"{hit['title']} 字符 {start}–{end}")
                continue
            parts.append(block)
            used += len(block) + 2
            occupied.append((start, end))
    head, records = _reference_blocks(prepared)
    if head and used + len(head) > room:
        head = ""
        omitted.append("此前用户全局字段")
    used += len(head) + 2 if head else 0
    keep = len(records)
    while keep and used + len(records[keep - 1]) <= room:
        keep -= 1
        used += len(records[keep]) + 2
    if keep:
        omitted.append(f"较早的此前用户资料 {keep} 条")
    prepared["material_omitted"] = omitted
    return "\n\n".join(p for p in [head, *records[keep:], *parts, _omission_note(omitted), request] if p).strip()


def persist(root: Path, sid: str, report: dict | None = None) -> dict:
    history = projects.read_full_history(root, sid)
    summary = task_memory.update(root, sid, history)
    # Preserve attachment sources already indexed while updating new conversation turns.
    documents = uploads.extracted_documents(sid, [f["id"] for f in uploads.list_uploads(sid)])
    local_retrieval.sync_session(root, sid, history, documents)
    if report is not None:
        folder = root / sid
        target = assert_write(folder / "context.last.json")
        tmp = assert_write(folder / (".context-" + uuid4().hex + ".tmp"))
        try:
            guarded_write_text(tmp, json.dumps(report, ensure_ascii=False))
            tmp.replace(target)
        finally:
            tmp.unlink(missing_ok=True)
    return summary


def detail(root: Path, sid: str) -> dict:
    if projects.safe_session_id(sid) != sid or (root / sid).resolve().parent != root.resolve():
        raise ValueError("会话 id 无效")
    memory_status = {"state": "missing", "note": "尚无规则记忆，可发送消息或重新整理记忆。"}
    try:
        summary = task_memory.load(root, sid)
        if summary:
            memory_status.update(state="ready", note="规则记忆来自本任务已提交原文。")
        elif projects._bounded_path(root, sid, task_memory.FILENAME).exists():
            memory_status.update(state="unavailable", note="规则记忆缓存已失效，可重新整理；原始对话仍保留。")
    except (OSError, ValueError):
        summary = None
        memory_status.update(state="unavailable", note="规则记忆暂不可读，可检查存储后重新整理；原始对话仍保留。")
    report = {}
    try:
        path = assert_open(projects._bounded_path(root, sid, "context.last.json"))
        if path.is_file() and path.stat().st_size < 128_000:
            value = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(value, dict):
                report = value
    except (ValueError, OSError):
        pass
    memory_text = memory_status["note"]
    if summary:
        memory_text = task_memory.render(summary, max_chars=10000).replace(task_memory.REFERENCE_NOTICE,
            "从本任务已提交消息提取；更正优先，助手说法待核实。")
        for key in task_memory.SECTIONS:
            for item in summary.get(key, []):
                source = item.get("source", {})
                if source.get("message_id"):
                    memory_text = memory_text.replace(str(source["message_id"]) + ":", f"第 {source.get('message_index', 0) + 1} 条消息，字符 ")
    semantic_text = ""
    enabled = bool(policy().get("semantic_summary"))
    semantic_status = {"enabled": enabled, "state": "disabled", "note": "模型语义摘要已关闭。重新整理记忆可清除已有模型摘要。"}
    if enabled:
        semantic_status.update(state="missing", note="当前没有模型摘要；后续问答达到条件时才会生成。")
        try:
            import semantic_memory
            semantic = semantic_memory.load(root, sid, history=projects.read_full_history(root, sid), keep_recent=4)
            if semantic:
                semantic_text = semantic_memory.render(semantic, max_chars=10000)
                semantic_status.update(state="ready" if semantic_text else "empty",
                    note="当前模型摘要可查看，内容仍未核验。" if semantic_text else "摘要暂无可展示条目，原文仍可检索。")
            elif projects._bounded_path(root, sid, "semantic.summary.json").exists():
                semantic_status.update(state="stale", note="旧模型摘要已失效，本轮不会复用；可重新整理记忆。")
        except (OSError, ValueError):
            semantic_status.update(state="unavailable", note="模型摘要暂不可读，继续使用规则记忆与原文检索。")
    return {"summary": summary, "memory_text": memory_text,
            "context": report, "semantic_memory_text": semantic_text,
            "memory_status": memory_status, "semantic_status": semantic_status}
