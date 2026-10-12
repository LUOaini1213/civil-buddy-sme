"""Model-free, selected-source quotations, never synthesized facts or approvals.

Read fresh extraction text and verify its equality to the indexed citation source.
Only current selected uploads participate: history and other uploaded versions do
not supply answers. This is excerpt retrieval, not unrestricted semantic Q&A.
"""
from __future__ import annotations

from hashlib import sha256
import re

import local_retrieval
from session_context import citation
import uploads

MAX_EXCERPTS = 6
MAX_QUOTE_CHARS = 1000
MAX_REPLY_CHARS = 4800
_FILE = re.compile(r"[\w.-]+\.(?:xlsx|csv|md|docx|pdf|txt|json|log)\b", re.I)
_REFERENCE = re.compile(r"\b(?:file|files|document|documents|tender|itt|attachment|attachments|source)\b|附件|文件|招标|原文|资料|这份|这两个", re.I)
_READ_CUE = re.compile(r"\b(?:what|which|where|when|who|how|why|does|is|are|explain|find|show|tell)\b|什么|哪些|何时|什么时候|多少|几点|柜型|解释|查找|检索|看看|有没有|是否", re.I)
_STOP = frozenset("what which where when who how why does do did is are was were has have had can could should would will shall may might must the a an this that these those it its my our your please kindly tell me about explain only from in on at of for to and or not no require requires required say says stated show find read file files document documents attachment attachments source sources selected using".split())
_TOPICS = (
    (r"container\s+type|type\s+of\s+container|柜型|集装箱类型", r"\bcontainers?\b|\b(?:20|40|45)\s*(?:GP|HQ|HC|DC)\b|柜型|集装箱"),
    (r"deadline|closing|submission\s+(?:date|time)|截止|截标", r"deadline|closing|submission|截止|截标"),
    (r"payload|weight\s+limit|载重|限重|重量", r"payload|weight|载重|限重|重量"),
    (r"dimensions?|size\s+limit|尺寸|净空", r"dimensions?|size|净空|尺寸"),
    (r"contract\s+(?:period|duration)|工期", r"contract\s+(?:period|duration)|calendar\s+days|工期"),
    (r"bond|guarantee|保证金|保函", r"bond|guarantee|保证金|保函"),
    (r"retention|保留金|质保金", r"retention|保留金|质保金"),
    (r"damages|违约金", r"damages|违约金"),
    (r"warranty|defects|质保|保修|缺陷", r"warranty|defects|质保|保修|缺陷"),
    (r"validity|有效期", r"validity|有效期"),
)


def _patterns(message: str):
    clean = _FILE.sub(" ", message)
    patterns = [re.compile(source, re.I) for question, source in _TOPICS if re.search(question, clean, re.I)]
    words = list(dict.fromkeys(word.casefold() for word in re.findall(r"[A-Za-z][A-Za-z0-9_-]{2,}", clean)
                               if word.casefold() not in _STOP))[:24]
    patterns += [re.compile(r"(?<!\w)" + re.escape(word) + r"(?!\w)", re.I) for word in words]
    # Chinese field names need no spaces. Discard conversational framing before
    # considering meaningful literal pairs; never use a filename as relevance.
    clean = re.sub(r"(?:请|帮我|告诉我|只解释|解释一下|不生成文件|不要生成文件|选中的?|附件|文件|原文|资料|里面|什么|哪些|怎么|如何|是否|有没有|要求|规定|本轮)", " ", clean)
    pairs = list(dict.fromkeys(part[i:i+2] for part in re.findall(r"[\u3400-\u9fff]+", clean)
                               for i in range(len(part)-1)))[:32]
    patterns += [re.compile(re.escape(pair)) for pair in pairs]
    return patterns


def _excerpts(text: str, patterns):
    offset = index = 0
    lines = text.splitlines(keepends=True)
    while index < len(lines):
        start = offset
        line = lines[index]
        offset += len(line)
        index += 1
        if line.lstrip().startswith("|"):
            # A table header alone is not an answer: include its literal rows.
            # Large tables retain separate bounded windows at matching fields
            # and identities, never synthesize a field/value association.
            while index < len(lines) and lines[index].lstrip().startswith("|"):
                offset += len(lines[index])
                index += 1
        block = text[start:offset]
        matches = [m for pattern in patterns if (m := pattern.search(block))]
        if not matches or not block.strip():
            continue
        # Keep ordinary clauses intact. Long lines are explicitly bounded
        # excerpts with exact offsets, not a claim to have interpreted a table.
        anchors = sorted({m.start() for m in matches}) if len(block) > MAX_QUOTE_CHARS else [0]
        spans = set()
        for first in anchors:
            low = start + max(0, first - MAX_QUOTE_CHARS // 3) if len(block) > MAX_QUOTE_CHARS else start
            high = min(offset, low + MAX_QUOTE_CHARS)
            while low < high and text[low].isspace():
                low += 1
            while high > low and text[high-1].isspace():
                high -= 1
            if (low, high) not in spans:
                spans.add((low, high))
                yield {"start": low, "end": high, "text": text[low:high], "score": sum(bool(p.search(text[low:high])) for p in patterns)}


def answer_from_selected(root, sid: str, message: str, identifiers: list[str], prepared: dict, *, locale="zh-CN"):
    """Return a literal-source response or None for an ordinary non-file chat.

    The caller must already have classified the current turn as read-only. This
    helper has no write tools, file-copy context, model calls or guessed values.
    """
    named = {match.group().casefold() for match in _FILE.finditer(message)}
    patterns = _patterns(message)
    if not named and not (identifiers and (_REFERENCE.search(message) or _READ_CUE.search(message))):
        return None
    en = locale == "en"
    if not identifiers:
        return {"reply": "Select the referenced attachment in this session first; no source was read." if en
                else "请先在当前会话中选中所问的附件；本轮未读取该文件。", "citations": [], "source_answer": {"mode": "no-selected-source"}}
    documents = uploads.extracted_documents(sid, identifiers)
    if named:
        missing = named - {doc["name"].casefold() for doc in documents}
        if missing:
            return {"reply": "The named file is not selected. Select the matching attachment; other files cannot stand in for it." if en
                    else "问题中点名的文件未被选中。请选中对应附件，不能用其他文件代替它回答。",
                    "citations": [], "source_answer": {"mode": "named-source-not-selected"}}
        documents = [doc for doc in documents if doc["name"].casefold() in named]
    by_id = {item["hit"]["attachment_id"]: item["hit"] for item in prepared.get("sources", [])
             if item.get("hit", {}).get("kind") == "attachment"}
    candidates, checked = [], []
    for order, doc in enumerate(documents):
        indexed_hit = by_id.get(doc["id"])
        indexed = local_retrieval.source(root, sid, indexed_hit["source_id"]) if indexed_hit else None
        if not indexed or indexed["kind"] != "attachment" or indexed["attachment_id"] != doc["id"] or indexed["text"] != doc["text"]:
            return {"reply": "The source changed or its citation is unavailable. Retry to refresh it; no answer was inferred." if en
                    else "附件文本已变化或引用不可用，请重试刷新来源；本轮未推断答案。", "citations": [],
                    "source_answer": {"mode": "source-changed"}}
        checked.append({"id": doc["id"], "name": doc["name"], "extracted_text_sha256": sha256(doc["text"].encode("utf-8")).hexdigest()})
        candidates += [{**indexed, **excerpt, "order": order} for excerpt in _excerpts(doc["text"], patterns)]
    if not candidates:
        return {"reply": "UNSPECIFIED: no matching passage was found in the selected source text. This does not prove the document contains no such requirement. Try a field name or configure a model for interpretation." if en
                else "UNSPECIFIED：选中附件的文本中未检索到匹配原文。这不证明文件没有该要求。请改用字段名提问，或配置模型进行语义解读。",
                "citations": [], "source_answer": {"mode": "no-matching-excerpt", "sources_checked": checked}}
    candidates.sort(key=lambda item: (-item["score"], item["order"], item["start"]))
    selected = candidates[:MAX_EXCERPTS]
    selected.sort(key=lambda item: (item["order"], item["start"]))
    parts = ["Matching original excerpts from the selected attachments (not a compliance or safety finding):" if en
             else "以下是选中附件中匹配的原文摘录，不是合规或安全结论："]
    footer = ("\n\nExcerpts only, not a full-document assessment. Conflicts, exceptions and approvals remain for a person to check. No files were generated." if en
              else "\n\n这里只展示匹配摘录，不是全文审查；冲突、例外和签认由人核对。本轮未生成文件。")
    citations = []
    for hit in selected:
        fence = "`" * max(3, 1 + max((len(m.group()) for m in re.finditer(r"`+", hit["text"])), default=0))
        label = f"{hit['title']} · {hit['attachment_id']} · chars {hit['start']}–{hit['end']}"
        block = f"\n\n{label}\n{fence}text\n{hit['text']}\n{fence}"
        if sum(map(len, parts)) + len(block) + len(footer) > MAX_REPLY_CHARS:
            break
        parts.append(block)
        citations.append(citation(sid, hit))
    parts.append(footer)
    return {"reply": "".join(parts), "citations": citations,
            "source_answer": {"mode": "literal-excerpts", "sources_checked": checked, "excerpts_shown": len(citations),
                              "matches_omitted": len(candidates) - len(citations)}}
