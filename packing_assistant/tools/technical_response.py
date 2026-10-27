"""Organize explicitly supplied bidder prose; never turn tender requirements into commitments.

This is a source-backed draft, not a method solver or a model writer. Unknown sections,
unread material and missing parameters remain human work.
"""
from __future__ import annotations

import re
from typing import Any, Mapping, Sequence

from packing_assistant.tools.tender_facts import _segments
from packing_assistant.tools.verdict_guard import stated_verdicts

SECTIONS = {
    "deployment": ("部署工艺", "施工部署", "技术措施", "施工工艺", "Deployment", "Technical measures"),
    "schedule": ("工期资源", "进度保障", "资源安排", "进度计划", "Programme", "Resources"),
    "quality": ("质量措施", "质量管理", "质量保证", "Quality measures", "Quality management"),
    "hse": ("安全环保", "安全环保措施", "安全管理", "环保措施", "Safety and environment"),
    "organization": ("组织机构", "人员分工", "Organization", "Responsibilities"),
}
_ALIASES = {alias.casefold(): section for section, aliases in SECTIONS.items() for alias in aliases}
_HEAD = re.compile(
    r"^\s*(?:#{1,6}\s*|[-*]\s*)?"
    r"(?:(?P<lot>第?[一二三四五六七八九十\d]+标段|Lot\s+[A-Za-z0-9_-]+)\s*)?"
    r"(?P<own>我方|我们的|响应草稿|Our\s+)?\s*"
    r"(?P<label>" + "|".join(re.escape(a) for a in sorted(_ALIASES, key=len, reverse=True)) + r")\s*[:：]\s*(?P<value>.*)$",
    re.I,
)
_OTHER_HEADING = re.compile(r"^\s*(?:#{1,6}\s|[^。；;\n:：]{1,30}[:：])")


def collect(text: str = "", *, sources: Sequence[Mapping[str, Any]] | None = None) -> list[dict[str, str]]:
    """Only explicit bidder labels, response blocks or caller-declared response files are eligible.

    A declared tender/reference file wins over any role-looking words inside it.
    Each record keeps its own lot, source and line; no section borrows another's numbers.
    """
    inputs = sources if sources is not None else [{"text": text, "title": "本轮用户输入", "role": "user"}]
    records: list[dict[str, str]] = []
    for source in inputs:
        role = source.get("role", "user")
        if role not in {"user", "response"}:
            continue
        raw = str(source.get("text") or "")
        title = str(source.get("title") or source.get("source_id") or "本轮用户输入")
        if len(raw) > 200_000:
            records.append({"section": "review", "value": "", "source": title,
                            "status": "too_large", "reason": "响应原文超过读取上限，未组织技术正文，请拆分后重试。"})
            continue
        current = None

        def finish():
            nonlocal current
            if current and current["value"].strip():
                current["value"] = current["value"].strip(" \t\n；;")
                if len(current["value"]) > 12_000:
                    current.update(status="too_large", reason="该技术段落超过读取上限，未组织正文。")
                elif stated_verdicts(current["value"], quotes=False):
                    current.update(status="needs_review", reason="响应原文含禁止代作的签认或实施结论，未放入正文。")
                else:
                    current["status"] = "source_draft"
                records.append(current)
            current = None

        for line, body, side in _segments(raw, "none" if role == "response" else "auto"):
            if side in {"theirs", "instruction"}:
                finish()
                continue
            if role != "response" and side != "ours":
                finish()
            for part in re.split(r"(?<=[；;])", body):
                head = _HEAD.match(part)
                if head:
                    finish()
                    if role != "response" and side != "ours" and not head["own"]:
                        continue
                    current = {"section": _ALIASES[head["label"].casefold()], "value": head["value"],
                               "source": title, "ref": f"L{line}", "lot": head["lot"] or ""}
                elif _OTHER_HEADING.match(part) or not part.strip():
                    finish()
                elif current:
                    current["value"] += "\n" + part.strip()
            if len(records) > 60:
                # Do not silently claim complete coverage of an arbitrarily large response.
                return [{"section": "review", "value": "", "source": title, "status": "too_large",
                         "reason": "技术响应段落超过数量上限，未组织正文，请拆分后重试。"}]
            if role != "response" and side != "ours":
                finish()
        finish()
    # A standalone labelled line is finalized after the per-line check above.
    # Include that last record (and all supplied files) in the same hard limit.
    if len(records) > 60:
        return [{"section": "review", "value": "", "source": records[-1]["source"],
                 "status": "too_large",
                 "reason": "技术响应段落超过数量上限，未组织正文，请拆分后重试。"}]
    return records
