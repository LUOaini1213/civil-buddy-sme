"""投标文件要点抽取（MVP）：规则+关键词，不做幻觉写标。

schema: tender.parse.v1 / checklist.v1 / response_matrix.v1 / handoff.v1
合规矩阵字段对齐业界实践：requirement_ref · owner · risk · status · evidence
（DeepRFP / AutoRFP / 知乎「响应矩阵中枢」等公开材料）

v1.1：条款级行项目（★ / 评分点 / 专项）+ 经营岗交接 handoff + P0 废标扫描。
不编造分值、资质、BCA、天数；只抄原文。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Optional


# A short Latin code is a token: "CTU" not inside "struCTUral", "OT" not in "not", "ISO" not in "isolation",
# "lashing" not in "flashing", "20GP" not in "20GPa", "A证" not in "CA证书". \b cannot say it: a CJK character
# is a word character too, so r"\bBCA\b" missed 「在BCA注册」.
_NO_L = r"(?<![A-Za-z])"
_NO_R = r"(?![A-Za-z])"

# (id, category, patterns, title, default_owner, risk)
_RULES: List[tuple] = [
    (
        "pkg_standard",
        "packaging",
        [r"包装", r"木箱", r"铁架", r"(?<!集)装箱", r"防护", r"防潮", r"packing", r"crate"],   # 集装箱 is a container, not packing
        "包装/装箱要求",
        "delivery",
        "medium",
    ),
    (
        "transport_container",
        "transport",
        [r"集装箱", r"柜型", r"40HQ", r"40GP" + _NO_R, r"20GP" + _NO_R, r"整柜", r"拼柜", r"海运", r"container"],
        "集装箱/运输方式",
        "delivery",
        "high",
    ),
    (
        "weight_limit",
        "transport",
        [r"货载", r"限重", r"吨位", r"payload", r"超重", r"最大重量"],
        "重量/货载限制",
        "delivery",
        "high",
    ),
    (
        "overlength",
        "transport",
        [r"超长", r"超限", r"异形", r"框架柜", r"开顶", _NO_L + r"OT" + _NO_R],
        "超长/异形运输",
        "delivery",
        "high",
    ),
    (
        "cog_lashing",
        "transport",
        [r"重心", r"绑扎", r"加固", r"系固", _NO_L + r"CTU" + _NO_R, _NO_L + r"lashing"],
        "重心/绑扎/系固",
        "delivery",
        "high",
    ),
    (
        "delivery_time",
        "schedule",
        [r"交货期", r"工期", r"到货", r"交货时间", r"calendar day", r"工作日", r"日历天"],
        "交货期/工期",
        "pm",
        "medium",
    ),
    (
        "qualification",
        "qualification",
        [r"资质", r"业绩", r"类似项目", r"注册资金", r"许可证", _NO_L + r"ISO" + _NO_R],
        "资格/业绩",
        "commercial",
        "critical",
    ),
    (
        "reject_item",
        "reject",
        [r"废标", r"否决", r"无效投标", r"实质性响应", r"重大偏差"],
        "废标/实质性响应",
        "legal",
        "critical",
    ),
    (
        "insurance_vgm",
        "compliance",
        [r"保险", r"VGM", r"提单", r"报关", r"原产地"],
        "保险/单证/VGM",
        "commercial",
        "medium",
    ),
    (
        "scoring",
        "scoring",
        [r"评分", r"分值", r"技术分", r"商务分", r"评标办法"],
        "评分办法",
        "proposal",
        "medium",
    ),
    # ---- 土建施工招标的条款。此前这些句子不带 ★ 就抽不出要求，也就永远没法和响应对照：
    # 「项目经理须具备一级注册建造师资格」「投标有效期90天」（test/benchmarks/tender_response/README.md）。
    # 触发词同时是响应对照的候选词，所以只收「条款里才这么写」的说法：裸的「项目经理」
    # 会把「项目经理部设在现场」连上资格要求，裸的「有效期」会把「保函有效期」连上投标有效期。
    (
        "bid_validity",
        "validity",
        [r"投标有效期", r"报价有效期", r"tender validity", r"bid validity", r"validity period"],
        "投标有效期",
        "commercial",
        "critical",
    ),
    (
        "personnel",
        "qualification",
        [r"建造师", r"注册证", r"安全生产考核", _NO_L + r"[ABC]\s*证", r"职称", r"项目经理须", r"项目经理应",
         r"项目负责人须", r"项目负责人应", r"技术负责人须", r"技术负责人应"],
        "人员资格",
        "commercial",
        "critical",
    ),
    (
        "registration",
        "qualification",
        [_NO_L + r"BCA" + _NO_R, r"workhead", r"registered with", r"financial grade"],
        "注册/工作类别 (BCA workhead)",
        "commercial",
        "critical",
    ),
    (
        "price_cap",
        "price",
        [r"最高投标限价", r"最高限价", r"招标控制价", r"拦标价", r"控制价"],
        "最高限价",
        "commercial",
        "critical",
    ),
    (
        "quality_standard",
        "quality",
        [r"质量标准", r"质量要求", r"质量目标", r"质量等级"],
        "质量标准",
        "pm",
        "medium",
    ),
    (
        "warranty",
        "warranty",
        [r"质保期", r"保修期", r"缺陷责任期", r"质量保证期", r"defects liability", r"warranty"],
        "质保期/缺陷责任期",
        "commercial",
        "medium",
    ),
    (
        "payment",
        "payment",
        [r"预付款", r"进度款", r"结算款", r"付款方式", r"支付比例", r"质保金", r"advance payment", r"payment terms?"],
        "付款条件",
        "commercial",
        "medium",
    ),
]


def _line_ref(idx: int) -> str:
    return f"L{idx + 1}"


_STAR_RE = re.compile(r"[★☆＊]|不满足即废标|必须满足项")
_SCORE_RE = re.compile(
    r"评分点|评标办法|技术分|商务分|分值|施工组织设计|"
    r"(?:Quality|Price)\s+\d+\s*%|"
    r"\d+\s*分(?!公司)",
    re.I,
)
_SPECIAL_RE = re.compile(
    r"专项(?:方案|施工)?|危大|临边|method statement|working at height",
    re.I,
)
_DAYS_RE = re.compile(
    r"(\d+)\s*(?:个)?\s*(日历天|calendar\s*days?|工作日)",
    re.I,
)
_WORKHEAD_RE = re.compile(r"(?<![A-Za-z0-9])(?:CW0[0-9]|CR\d{2})(?![A-Za-z0-9])", re.I)   # CR16 Curtain Walls is a BCA CR workhead
_ENVELOPE_RE = re.compile(
    r"双信封|两信封|三信封|two[\s-]*envelope|technical and (?:financial|price)|"
    r"技术标与报价分投|暗标",
    re.I,
)
_EBID_RE = re.compile(r"电子标|电子投标|CA\s*锁|投标文件加密|加密递交")
_BOND_RE = re.compile(r"投标保证金|履约保证金|投标保函")
_BOND_AMT_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(万|万元|元|%|％)")
_EVAL_RE = re.compile(
    r"综合评估法|经评审的最低投标价法|合理低价(?:法)?|Price Quality Method|PQM|QFM|"
    r"Quality Fee Method",
    re.I,
)
_DEADLINE_RE = re.compile(
    r"(提问截止|异议截止|澄清截止|答疑截止|投标截止|递交截止|开标)"
    r"[^\n]{0,48}?"
    r"(\d{4}\s*[-./年]\s*\d{1,2}\s*[-./月]\s*\d{1,2}(?:\s*[日号])?"
    r"(?:\s+\d{1,2}:\d{2})?)",
    re.I,
)


def rule_patterns_for_line(line: str) -> List[str]:
    """主题规则里命中这一行的关键词模式。响应对照复用同一套词表：招标句因为
    「许可证」被抽出来，响应里提到「许可证」的句子就是它的候选。"""
    text = line or ""
    found = [pat for _rid, _cat, patterns, _title, _owner, _risk in _RULES for pat in patterns
             if re.search(pat, text, flags=re.I)]
    # 逐行条目（电子标 / 保证金 / 专项）没有主题规则，用它们在这一行里实际命中的词
    for rx in (_EBID_RE, _BOND_RE, _SPECIAL_RE):
        found.extend(re.escape(m.group(0)) for m in rx.finditer(text))
    return list(dict.fromkeys(found))


def _is_star_line(ln: str) -> bool:
    return bool(_STAR_RE.search(ln or ""))


def _is_score_line(ln: str) -> bool:
    t = ln or ""
    if "不合格" in t and "分" not in t:
        return False
    return bool(_SCORE_RE.search(t))


def _is_special_line(ln: str) -> bool:
    return bool(_SPECIAL_RE.search(ln or ""))


def _duration_days_from_lines(lines: List[str]) -> Optional[int]:
    """Copy calendar-day counts already in the text. Never invent a period."""
    for ln in lines:
        m = _DAYS_RE.search(ln)
        if m:
            try:
                n = int(m.group(1))
            except (TypeError, ValueError):
                continue
            if 1 <= n <= 3650:
                return n
    return None


def _tender_duration(facts: Any, lines: List[str]) -> Optional[int]:
    """The tender's own duration in calendar days, or None - never the bidder's, never a blend.

    A value a 补遗 gave wins over the one it replaced. Lots with different durations have no single
    answer, so the answer is None and each lot keeps its own in ``facts``. Only a count written as
    日历天 / calendar days / 工作日 is copied, as before: "工期300天" stays "300天" in the table.
    """
    found = [m for m in facts.mentions if m.topic in ("duration", "delivery") and m.side == "tender" and m.value]
    found = [m for m in found if m.origin] or found
    days: List[tuple] = []
    for m in found:
        hit = _DAYS_RE.search(m.value)
        if hit and 1 <= int(hit.group(1)) <= 3650:
            days.append((m.lot, int(hit.group(1))))
    common = [n for lot, n in days if not lot]
    if common:
        return common[0]
    if days:
        return days[0][1] if len({n for _lot, n in days}) == 1 else None
    return _duration_days_from_lines(lines)


def _line_item(
    *,
    rid: str,
    kind: str,
    cat: str,
    title: str,
    line: str,
    idx: int,
    owner: str,
    risk: str,
    req_type: str,
    must: bool,
) -> Dict[str, Any]:
    return {
        "id": rid,
        "item_kind": kind,
        "category": cat,
        "title": title,
        "snippets": [line],
        "exact_text": line,
        "requirement_ref": _line_ref(idx),
        "owner": owner,
        "risk": risk,
        "requirement_type": req_type,
        "priority": "high" if risk in ("critical", "high") else "medium",
        "must_respond": must,
    }


def _squash(text: str) -> str:
    return re.sub(r"[\W_]+", "", text or "")   # letters and digits only: a table row and its cells joined are one text


def _document_items(doc: Any) -> List[Dict[str, Any]]:
    """Rejection clauses and front-table obligations of a document as requirements, one each."""
    from packing_assistant.tools import tender_document

    items: List[Dict[str, Any]] = []
    issued: Dict[str, int] = {}

    def rid(prefix: str, line: int) -> str:
        base = f"{prefix}_L{line}"
        issued[base] = issued.get(base, 0) + 1
        return base if issued[base] == 1 else f"{base}_{issued[base]}"

    for found in tender_document.rejections(doc):
        piece = found.piece
        item = _line_item(rid=rid("reject", piece.line), kind="star" if found.star else "reject_clause", cat="reject",
                          title="★/必须满足项" if found.star else "否决/拒收条款", line=piece.text, idx=piece.line - 1,
                          owner="legal", risk="critical", req_type="mandatory", must=True)
        item["locator"] = piece.ref
        if found.cited:
            item["cited"] = [{"locator": c.ref, "text": c.text[:400]} for c in found.cited]
        items.append(item)
    for piece in tender_document.obligations(doc):
        item = _line_item(rid=rid("oblige", piece.line), kind="obligation", cat="compliance", title=f"前附表要求：{piece.heading}",
                          line=piece.text, idx=piece.line - 1, owner="commercial", risk="high", req_type="mandatory", must=True)
        item["locator"] = piece.ref
        items.append(item)
    return items


def _document_summary(doc: Any) -> Dict[str, Any]:
    from packing_assistant.tools import tender_document

    return {"schema": "tender.document.v1", **tender_document.summary(doc),
            "candidates": [{"text": p.text[:300], "locator": p.ref} for p in tender_document.rejection_candidates(doc)],
            "cut": any(p.text.startswith("（未读完）") for p in doc.pieces),
            "forms": [{"name": name, "locator": piece.ref} for name, piece in tender_document.forms(doc)],
            "remainder": [{"name": row.name, "text": row.content, "locator": row.piece.ref}
                          for row in tender_document.front_remainder(doc)],
            "review": [{"group": group, "factor": factor, "standard": standard[:300], "locator": piece.ref}
                       for group, factor, standard, piece in tender_document.review_standards(doc)]}


def _extract_line_items(lines: List[str]) -> List[Dict[str, Any]]:
    """One row per ★ / scoring-point / special line (AutoRFP item-level matrix)."""
    items: List[Dict[str, Any]] = []
    seen: set = set()
    units: List[tuple] = []
    for i, ln in enumerate(lines):
        pieces = [p.strip() for p in re.split(r"[。；;]", ln) if p.strip()]
        if not pieces:
            continue
        for p in pieces:
            units.append((i, p))
    # An id names one clause, not one line. "施工组织设计评分35分。进度计划评分12.5分。" on a single
    # line used to give both points the id score_L1; parse_tender_text drops a repeated id, so the
    # second point vanished - and with it its chapter in the technical outline. The first clause of
    # a kind on a line keeps the old id (score_L1), later ones get score_L1_2, score_L1_3.
    issued: Dict[str, int] = {}

    def _rid(prefix: str, idx: int) -> str:
        base = f"{prefix}_{_line_ref(idx)}"
        issued[base] = issued.get(base, 0) + 1
        return base if issued[base] == 1 else f"{base}_{issued[base]}"

    for i, ln in units:
        kinds: List[str] = []
        if _is_star_line(ln):
            kinds.append("star")
        if _is_score_line(ln):
            kinds.append("scoring_point")
        if _is_special_line(ln):
            kinds.append("special")
        if _EBID_RE.search(ln or ""):
            kinds.append("ebid")
        if _BOND_RE.search(ln or ""):
            kinds.append("bond")
        # ★ line that is also a special stays one star row; still listed in handoff.specials
        if "star" in kinds and "special" in kinds:
            kinds = [k for k in kinds if k != "special"]
        if "star" in kinds and "scoring_point" in kinds:
            kinds = [k for k in kinds if k != "scoring_point"]
        for kind in kinds:
            key = (kind, ln)
            if key in seen:
                continue
            seen.add(key)
            if kind == "star":
                items.append(
                    _line_item(
                        rid=_rid("star", i),
                        kind="star",
                        cat="reject",
                        title="★/必须满足项",
                        line=ln,
                        idx=i,
                        owner="legal",
                        risk="critical",
                        req_type="mandatory",
                        must=True,
                    )
                )
            elif kind == "scoring_point":
                items.append(
                    _line_item(
                        rid=_rid("score", i),
                        kind="scoring_point",
                        cat="scoring",
                        title="评分点",
                        line=ln,
                        idx=i,
                        owner="proposal",
                        risk="medium",
                        req_type="evaluated",
                        must=False,
                    )
                )
            elif kind == "bond":
                items.append(
                    _line_item(
                        rid=_rid("bond", i),
                        kind="bond",
                        cat="qualification",
                        title="保证金/保函",
                        line=ln,
                        idx=i,
                        owner="commercial",
                        risk="critical",
                        req_type="mandatory",
                        must=True,
                    )
                )
            elif kind == "ebid":
                items.append(
                    _line_item(
                        rid=_rid("ebid", i),
                        kind="ebid",
                        cat="reject",
                        title="电子标/加密/CA锁",
                        line=ln,
                        idx=i,
                        owner="legal",
                        risk="high",
                        req_type="mandatory",
                        must=True,
                    )
                )
            else:
                items.append(
                    _line_item(
                        rid=_rid("special", i),
                        kind="special",
                        cat="scoring",
                        title="必须专项/危大",
                        line=ln,
                        idx=i,
                        owner="proposal",
                        risk="high",
                        req_type="mandatory",
                        must=True,
                    )
                )
    return items


def _detect_eval_method(blob: str) -> Optional[str]:
    m = _EVAL_RE.search(blob or "")
    return m.group(0) if m else None


def _detect_envelope(blob: str) -> Optional[str]:
    """Copy envelope scheme from text. None = 招标未写. Never invent 两信封."""
    t = blob or ""
    if re.search(r"三信封|three[\s-]*envelope", t, re.I):
        return "three"
    if re.search(
        r"双信封|两信封|two[\s-]*envelope|技术标与报价分投",
        t,
        re.I,
    ):
        return "two"
    if "暗标" in t:
        return "blind_tech"
    return None


def _extract_deadlines(blob: str) -> List[Dict[str, str]]:
    """Only dated milestones already in the text."""
    out: List[Dict[str, str]] = []
    seen = set()
    for m in _DEADLINE_RE.finditer(blob or ""):
        label = m.group(1)
        when = re.sub(r"\s+", " ", m.group(2)).strip()
        key = (label, when)
        if key in seen:
            continue
        seen.add(key)
        out.append({"label": label, "when": when, "source": "verbatim"})
    return out


def _bond_from_requirements(requirements: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Copy bond wording only. Never invent 2% or an account number."""
    bonds = [r for r in requirements if r.get("item_kind") == "bond"]
    amount = None
    account = None
    for r in bonds:
        text = str(r.get("exact_text") or "")
        m = _BOND_AMT_RE.search(text)
        if m and amount is None:
            amount = f"{m.group(1)}{m.group(2)}"
        if re.search(r"账户|账号", text) and account is None:
            account = "见原文（本工具不抄账号数字）" if re.search(r"\d{6,}", text) else "招标提到账户但未写账号"
    return {
        "mentioned": bool(bonds),
        "amount_verbatim": amount,
        "account": account,
        "n": len(bonds),
    }


def build_handoff(
    requirements: List[Dict[str, Any]],
    *,
    duration_days: Optional[int] = None,
    envelope: Optional[str] = None,
    deadlines: Optional[List[Dict[str, str]]] = None,
    eval_method: Optional[str] = None,
    facts: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """经营岗交接：评分点 → bid-tech；★/废标 → bid-compliance。不判定可投标。

    ``facts``（tools/tender_facts.py 的字段层）随交接落盘：后岗在另一轮里只拿得到
    tender.handoff.json，工程概况、各标段工期、拟派人员都得从这里读。"""
    scoring = [r for r in requirements if r.get("item_kind") == "scoring_point"]
    stars = [r for r in requirements if r.get("item_kind") == "star"]
    specials = [
        r
        for r in requirements
        if r.get("item_kind") == "special"
        or (r.get("item_kind") == "star" and _is_special_line(str((r.get("exact_text") or ""))))
    ]
    workheads: List[str] = []
    for r in requirements:
        blob = " ".join([str(r.get("exact_text") or "")] + [str(s) for s in (r.get("snippets") or [])])
        for m in _WORKHEAD_RE.finditer(blob):
            wh = m.group(0).upper()
            if wh not in workheads:
                workheads.append(wh)
    next_experts: List[str] = []
    if scoring or specials:
        next_experts.append("bid-tech")
    if stars or any(r.get("category") == "reject" for r in requirements):
        next_experts.append("bid-compliance")
    if specials:
        next_experts.append("construction")
    p0_src = [
        r
        for r in requirements
        if r.get("item_kind") == "star" or r.get("category") in ("reject", "qualification", "validity", "price")
    ]
    p0_items = []
    if envelope == "blind_tech":
        p0_items.append(
            {
                "req_id": "blind_identity",
                "title": "暗标露名风险",
                "exact_text": "原文检出暗标：封面/页眉/可识别业绩地名须人工核对，系统不保证看不出来。",
                "requirement_ref": "envelope",
                "risk": "high",
                "owner": "legal",
            }
        )
    for r in p0_src:
        p0_items.append(
            {
                "req_id": r.get("id"),
                "title": r.get("title"),
                "exact_text": r.get("exact_text") or ((r.get("snippets") or [None])[0]),
                "requirement_ref": r.get("requirement_ref"),
                "risk": r.get("risk"),
                "owner": r.get("owner"),
            }
        )
    def _brief(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        out = []
        for r in rows:
            out.append(
                {
                    "id": r.get("id"),
                    "text": r.get("exact_text") or ((r.get("snippets") or [""])[0]),
                    "requirement_ref": r.get("requirement_ref"),
                }
            )
        return out

    return {
        "schema": "tender.handoff.v1",
        "scoring_points": _brief(scoring),
        "star_items": _brief(stars),
        "specials": _brief(specials),
        "workheads": workheads,
        "duration_days": duration_days,
        "envelope": envelope,
        "eval_method": eval_method,
        "deadlines": list(deadlines or []),
        "bid_bond": _bond_from_requirements(requirements),
        "facts": facts or None,
        "bid_decision": "human_required",
        "next_experts": next_experts,
        "p0_reject_scan": {
            "schema": "tender.p0_reject_scan.v1",
            "human_confirm_required": True,
            "n": len(p0_items),
            "items": p0_items,
            "note": "P0 废标/资格/★项须人工确认。系统不判定可投标、不编造否决结论。",
        },
    }


def build_tech_outline_from_handoff(
    handoff: Optional[Dict[str, Any]] = None,
    *,
    project_name: str = "未命名项目",
) -> Dict[str, Any]:
    """按抽出的评分点出技术标目录草稿。无评分点则只给待对照前附表，不套模板冒充本标。

    成稿九节（评分目录映射 / 依据概况 / … / 缺项与自检）见 tools/tender_tables.tech_outline。
    返回结构不变：chapters、from_extracted_scores、markdown。"""
    from packing_assistant.tools.tender_tables import tech_outline

    return tech_outline(handoff, project_name=project_name)


def build_workbench_extract_table(
    parsed: Optional[Dict[str, Any]] = None,
    *,
    project_name: str = "未命名招标",
) -> str:
    """同一张招标解析表：主线 C 与 workbench sidecar 共用，禁止编造空栏。

    九节 + 「事项｜要求原文｜来源页段｜是否检出｜澄清建议」，见 tools/tender_tables.extract_table。"""
    from packing_assistant.tools.tender_tables import extract_table

    return extract_table(parsed, project_name=project_name)


def workbench_bid_extract(text: str, *, project_name: str = "未命名招标") -> Dict[str, Any]:
    """Workbench bid-parse extract entry. Same transform as packing tender-handoff."""
    parsed = parse_tender_text(text, source="workbench-bid-parse")
    ho = parsed.get("handoff") or {}
    return {
        "ok": bool(parsed.get("requirements")),
        "schema": "tender.workbench_extract.v1",
        "duration_days": parsed.get("duration_days"),
        "star_items": list(ho.get("star_items") or []),
        "scoring_points": list(ho.get("scoring_points") or []),
        "handoff": ho,
        "submit_blocked": True,
        "submit_block_reason": "P0 资格/废标/★项须人工确认；成果仍是 AI 草稿，不可递交。",
        "extract_table_markdown": build_workbench_extract_table(
            parsed, project_name=project_name
        ),
    }


def parse_tender_text(text: str, *, source: str = "text", sides: str = "auto") -> Dict[str, Any]:
    """从纯文本招标节选抽取 requirements 列表（含 ref/owner/risk）。

    ``sides``：调用方已经知道整段都是招标方原文（上传时标了角色的文件）就传 "none"。默认 "auto"
    把文本当成人说的话来读——「招标要求工期60日历天，我们投标函写了999日历天」里，999 那半句
    是我方的，不进 requirements，也不会被当成工期。分句规则见 tools/tender_facts.py。
    """
    from packing_assistant.runtime import cancel
    from packing_assistant.tools.tender_facts import extract as extract_facts, tender_pieces

    raw = (text or "").strip()
    facts = extract_facts(raw, sides=sides)
    pieces = tender_pieces(raw, sides=sides)  # 与原文非空行一一对应，行号引用不变
    lines = ["；".join(parts) for parts in pieces]
    blob = "\n".join(lines)
    requirements: List[Dict[str, Any]] = []
    seen = set()

    for rid, cat, patterns, title, owner, risk in _RULES:
        cancel.check()
        hits: List[str] = []
        refs: List[str] = []
        for i, parts in enumerate(pieces):
            for ln in parts:  # 一行被拆过就逐片引用，exact_text 仍是原文的字面片段
                if any(re.search(pat, ln, flags=re.I) for pat in patterns):
                    hits.append(ln)
                    refs.append(_line_ref(i))
                    break
        if not hits:
            continue
        if rid in seen:
            continue
        seen.add(rid)
        must = cat in (
            "reject",
            "qualification",
            "transport",
            "packaging",
            "schedule",
            "validity",
            "price",
            "quality",
            "warranty",
        )
        # Inventive/AutoRFP: requirement type = mandatory | evaluated | administrative | informational
        if cat in ("reject", "qualification", "validity", "price", "quality", "warranty"):
            req_type = "mandatory"
        elif cat in ("transport", "packaging", "schedule", "compliance"):
            req_type = "mandatory" if must else "evaluated"
        elif cat == "scoring":
            req_type = "evaluated"
        else:
            req_type = "informational"
        requirements.append(
            {
                "id": rid,
                "item_kind": "theme",
                "category": cat,
                "title": title,
                "snippets": hits[:5],
                "exact_text": hits[0],
                "requirement_ref": ",".join(refs[:5]),
                "owner": owner,
                "risk": risk,
                "requirement_type": req_type,
                "priority": (
                    "high"
                    if cat in ("reject", "qualification", "transport", "packaging")
                    or risk == "critical"
                    else "medium"
                ),
                "must_respond": must,
            }
        )

    theme_ids = {r["id"] for r in requirements}
    for item in _extract_line_items(lines):
        if item["id"] in theme_ids:
            continue
        requirements.append(item)
        theme_ids.add(item["id"])

    # A document says where things are (tools/tender_document.py): every clause that gets a bid rejected is a
    # requirement of its own with the clause it stands in - a theme row with five snippets is no checklist.
    from packing_assistant.tools import tender_document

    document: Optional[Dict[str, Any]] = None
    if tender_document.is_document(text or ""):
        doc = tender_document.read(text or "")
        document = _document_summary(doc)
        for item in _document_items(doc):
            mine = _squash(item["exact_text"])
            twin = next((r for r in requirements if r.get("item_kind") == "star" and mine
                         and (mine in _squash(str(r.get("exact_text") or "")) or _squash(str(r.get("exact_text") or "")) in mine)), None)
            if twin is not None:
                twin["locator"] = item["locator"]   # the ★ line is already a row: it gains its clause
                twin["display"] = item["exact_text"]   # ... and, for a table row, its cells without the bars
                continue
            if item["id"] not in theme_ids:
                requirements.append(item)
                theme_ids.add(item["id"])
        # a line that merely holds a ★ is a must-meet item of a typed request. In a document the structure decides:
        # the ★ in the 备注 column of a hazard list marks a major hazard, and tender_document.rejections left it out
        requirements = [r for r in requirements if not (r.get("item_kind") == "star" and not r.get("locator"))]

    duration_days = _tender_duration(facts, lines)
    envelope = _detect_envelope(blob)
    eval_method = _detect_eval_method(blob)
    deadlines = _extract_deadlines(blob)
    handoff = build_handoff(
        requirements,
        duration_days=duration_days,
        envelope=envelope,
        deadlines=deadlines,
        eval_method=eval_method,
        facts=facts.to_dict(),
    )
    # Keep the actual submission wording as well as the normalized scheme.
    # `pieces` already excludes the bidder's statements in a mixed request.
    envelope_sources = []
    seen_envelopes: set = set()
    for index, parts in enumerate(pieces):
        for part in parts:
            if _ENVELOPE_RE.search(part) and part not in seen_envelopes:
                seen_envelopes.add(part)
                envelope_sources.append({"text": part, "locator": _line_ref(index)})
    if envelope_sources:
        handoff["envelope_sources"] = envelope_sources
    # A job file that was named and gave no text (office_job writes "（读失败）why" under its heading).
    # The later posts only get the handoff: "未在原文检出" means something else when the 原文 was never read.
    from packing_assistant.office_job import material_role, unread_files

    unread = [{**item, "role": material_role(item["title"])} for item in unread_files(text or "")]
    if unread:
        handoff["unreadable"] = unread
    if document is not None:
        handoff["document"] = document
        handoff["rejection_clauses"] = [{"text": r.get("display") or r.get("exact_text"), "locator": r.get("locator") or r.get("requirement_ref"),
                                         "star": r.get("item_kind") == "star"}
                                        for r in requirements if r.get("category") == "reject" and r.get("item_kind") in ("reject_clause", "star")]

    return {
        "schema": "tender.parse.v1",
        "tool": "tender.parse",
        "source": source,
        "n_lines": len(lines),
        "n_chars": len(blob),
        "requirements": requirements,
        "duration_days": duration_days,
        "handoff": handoff,
        "facts": facts.to_dict(),
        "summary": {
            "n_requirements": len(requirements),
            "n_line_items": sum(1 for r in requirements if r.get("item_kind") != "theme"),
            "categories": sorted({r["category"] for r in requirements}),
            "must_respond_n": sum(1 for r in requirements if r.get("must_respond")),
            "critical_n": sum(1 for r in requirements if r.get("risk") == "critical"),
            "owners": sorted({str(r.get("owner")) for r in requirements if r.get("owner")}),
            "duration_days": duration_days,
            "n_scoring_points": len(handoff.get("scoring_points") or []),
            "n_star_items": len(handoff.get("star_items") or []),
            "envelope": envelope,
            "eval_method": eval_method,
            "n_deadlines": len(deadlines),
        },
    }


def build_checklist(requirements: List[Dict[str, Any]]) -> Dict[str, Any]:
    """生成人工勾选清单（owner/risk 透传）。"""
    items = []
    for r in requirements or []:
        items.append(
            {
                "req_id": r.get("id"),
                "title": r.get("title"),
                "category": r.get("category"),
                "requirement_ref": r.get("requirement_ref"),
                "owner": r.get("owner") or "unassigned",
                "risk": r.get("risk") or "medium",
                "requirement_type": r.get("requirement_type") or "informational",
                "must_respond": bool(r.get("must_respond")),
                "status": "pending",
                "evidence": None,
            }
        )
    return {
        "schema": "tender.checklist.v1",
        "tool": "tender.checklist",
        "items": items,
        "n_pending": len(items),
        "n_must": sum(1 for i in items if i.get("must_respond")),
        "n_critical": sum(1 for i in items if i.get("risk") == "critical"),
    }


# A packing run shows that the goods fit, in which containers and how many, under payload, and the mid50 share.
# How they are packed, stood, stacked or carried out of gauge it does not model: such a clause is not met
# because the goods fit. Nor is a clause naming another container than the run's, or refusing the run's one,
# nor a lashing / securing / CTU clause by mid50, which is the centre of gravity only.
_PACK_ADDRESSES = {"transport_container", "weight_limit", "cog_lashing"}
_PACK_UNMODELLED_RE = re.compile(_NO_L + r"(?:A[\s-]?frames?|stillages?|upright|vertical(?:ly)?|stack(?:ed|ing|able|s)?|fragile|"
                                 r"protect(?:ed|ion|s)?)" + _NO_R + r"|竖放|立放|直立|竖立|堆叠|叠放|堆码|易碎|防护|防潮|防雨|熏蒸", re.I)
_CONTAINER_RE = re.compile(
    r"(?<![A-Za-z0-9])(20|40|45)\s*(?:ft|foot|feet|['’])?[\s-]*(?i:(GP|HQ|HC|OT|FR|high[\s-]?cube|open[\s-]?top|flat[\s-]?rack))(?![A-Za-z])"
    r"|(?<![A-Za-z0-9])(OT|FR|(?i:high[\s-]?cube|open[\s-]?top|flat[\s-]?rack))(?![A-Za-z])|(开顶|框架)(?:柜|集装箱)")
_CONTAINER_KIND = {"HC": "HQ", "HIGHCUBE": "HQ", "OPENTOP": "OT", "FLATRACK": "FR", "开顶": "OT", "框架": "FR"}
_REFUSE_RE = re.compile(r"(?<![A-Za-z])(?:not|no(?!\.))(?![A-Za-z])|n't|prohibit|forbid|disallow|exclud|"
                        r"不得|不接受|不允许|不可|不能|禁止|严禁|除外", re.I)
_LASHING_RE = re.compile(r"(?<![A-Za-z])(?:lash(?:ing|ed)?|secur(?:e|ed|ing)|CTU)(?![A-Za-z])|绑扎|捆扎|系固|加固", re.I)


def _container_codes(text: str) -> set:
    codes = set()
    for m in _CONTAINER_RE.finditer(text):
        kind = re.sub(r"[\s-]", "", m.group(2) or m.group(3) or m.group(4)).upper()
        codes.add((m.group(1) or "") + _CONTAINER_KIND.get(kind, kind))
    return codes


def build_response_matrix(
    requirements: List[Dict[str, Any]],
    *,
    packing_summary: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """条款 × 证据矩阵（装柜结果可覆盖运输/包装/重心类）。"""
    pack = packing_summary or {}
    can_fit = pack.get("can_fit")
    used = pack.get("containers_used")
    n0 = pack.get("n0")
    ship_ok = pack.get("ship_ok")
    mid50 = pack.get("mid50")

    rows = []
    for r in requirements or []:
        cat = r.get("category")
        rid = r.get("id")
        status = "pending"
        evidence = None
        if cat in ("transport", "packaging") and can_fit is not None:
            status = "covered" if can_fit is True else "gap"
            evidence = {
                "type": "packing",
                "can_fit": can_fit,
                "containers_used": used,
                "n0": n0,
                "ship_ok": ship_ok,
                "mid50": mid50,
            }
            texts = [str(s) for s in (r.get("snippets") or [r.get("exact_text") or ""])]
            unmodelled = sorted({m.group(0).lower() for s in texts for m in _PACK_UNMODELLED_RE.finditer(s)})
            if can_fit is True and (rid not in _PACK_ADDRESSES or unmodelled):
                status = "human_required"
                evidence["note"] = ("装柜结果只说明装得下（柜型/柜数/货载/mid50），不说明本条"
                                    + (f"：{'、'.join(unmodelled)}" if unmodelled else "的包装方式或超限运输"))
            elif rid == "transport_container" and can_fit is True:
                run = str(pack.get("container_type") or "").strip().upper()
                named = set().union(*(_container_codes(s) for s in texts))
                refused = any(_REFUSE_RE.search(s) for s in texts if _container_codes(s))
                if named and (refused or not run or any(c != run and (c[:1].isdigit() or not run.endswith(c)) for c in named)):
                    status = "human_required"
                    evidence["note"] = (f"条款写明柜型 {'、'.join(sorted(named))}"
                                        + ("（含不接受/不得）" if refused else "")
                                        + f"，装柜结果用的是 {run or '（未给柜型）'}：柜型是否满足本条须人工确认")
            # 重心条款：需要 mid50 证据；mid50 只说明重心，不说明绑扎/系固
            elif rid == "cog_lashing" and can_fit is True:
                lashing = any(_LASHING_RE.search(s) for s in texts)
                if mid50 is not None and float(mid50) >= 0.55 and not lashing:
                    status = "covered"
                else:
                    status = "partial"
                    evidence["note"] = ("mid50 只说明重心，不说明绑扎/系固/CTU 合规" if lashing
                                        else "装柜可 fit，但 mid50 未达标或未提供")
        elif cat == "qualification":
            status = "human_required"
            evidence = {"type": "manual", "note": "资质/业绩须人工提供"}
        elif cat == "reject":
            status = "review"
            evidence = {"type": "manual", "note": "废标项须人工核对"}
        elif cat == "scoring":
            status = "human_required"
            evidence = {"type": "manual", "note": "评分策略由标书负责人确认"}
        elif cat == "schedule":
            status = "human_required"
            evidence = {"type": "manual", "note": "交货期须项目经理确认"}
        elif cat == "compliance":
            status = "human_required"
            evidence = {"type": "manual", "note": "VGM/保险/单证须商务确认"}
        elif cat == "validity":
            status = "review"
            evidence = {"type": "manual", "note": "投标有效期须与投标函、保函有效期逐一核对"}
        elif cat == "price":
            status = "review"
            evidence = {"type": "manual", "note": "报价须人工对照最高限价；系统不读报价、不判定是否超限"}
        elif cat == "quality":
            status = "human_required"
            evidence = {"type": "manual", "note": "质量承诺须项目经理确认"}
        elif cat == "warranty":
            status = "human_required"
            evidence = {"type": "manual", "note": "质保期/缺陷责任期承诺须商务确认"}
        elif cat == "payment":
            status = "human_required"
            evidence = {"type": "manual", "note": "付款条件是否接受由商务确认"}
        rows.append(
            {
                "req_id": rid,
                "title": r.get("title"),
                "category": cat,
                "item_kind": r.get("item_kind") or "theme",
                "requirement_ref": r.get("requirement_ref"),
                "owner": r.get("owner") or "unassigned",
                "risk": r.get("risk") or "medium",
                "requirement_type": r.get("requirement_type") or "informational",
                "status": status,
                # 业界合规矩阵：应答落点（提案章节），便于评审对照
                "proposal_location": _proposal_location(cat, rid, r.get("item_kind")),
                "compliance_label": _compliance_label(status),
                "knowledge_ref": _knowledge_ref(cat, rid),
                "public_ref": _public_ref(cat, rid),
                "evidence": evidence,
                "snippets": r.get("snippets") or [],
                "exact_text": r.get("exact_text") or ((r.get("snippets") or [None])[0]),
            }
        )

    summary = {
        "n": len(rows),
        "covered": sum(1 for x in rows if x["status"] == "covered"),
        "partial": sum(1 for x in rows if x["status"] == "partial"),
        "pending": sum(1 for x in rows if x["status"] == "pending"),
        "human_required": sum(1 for x in rows if x["status"] == "human_required"),
        "gap": sum(1 for x in rows if x["status"] == "gap"),
        "review": sum(1 for x in rows if x["status"] == "review"),
        "by_owner": _count_by(rows, "owner"),
        "by_risk": _count_by(rows, "risk"),
    }
    summary["readiness_score"] = _readiness_score(summary)
    _attach_knowledge_excerpts(rows)
    return {
        "schema": "tender.response_matrix.v1",
        "tool": "tender.response_matrix",
        "rows": rows,
        "summary": summary,
    }


def _attach_knowledge_excerpts(rows: List[Dict[str, Any]]) -> None:
    """Bind in-repo tender-delivery notes onto matrix rows. No invented clauses."""
    root = Path(__file__).resolve().parents[2]
    cache: Dict[str, str] = {}
    for row in rows:
        rel = str(row.get("knowledge_ref") or "")
        if not rel:
            continue
        if rel not in cache:
            path = root / rel
            if not path.is_file():
                cache[rel] = ""
            else:
                body = path.read_text(encoding="utf-8")
                if body.startswith("---"):
                    parts = body.split("---", 2)
                    body = parts[2] if len(parts) >= 3 else body
                cache[rel] = " ".join(body.split())[:400]
        excerpt = cache[rel]
        if excerpt:
            row["knowledge_excerpt"] = excerpt


def _count_by(rows: List[Dict[str, Any]], key: str) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for r in rows:
        k = str(r.get(key) or "unknown")
        out[k] = out.get(k, 0) + 1
    return out


def _proposal_location(
    category: Optional[str], req_id: Optional[str], item_kind: Optional[str] = None
) -> str:
    """默认应答落点（可被人工改写）。"""
    kind = str(item_kind or "")
    if kind == "star":
        return "标书响应声明 / 偏离表（★项须逐条响应）"
    if kind == "scoring_point":
        return "技术标 · 对应评分点章节"
    if kind == "special":
        return "技术标 · 危大及招标点名专项"
    if kind == "ebid":
        return "形式评审 · 电子标加密/CA锁（截止后补传无效）"
    m = {
        "packaging": "技术标 · 包装与装箱方案",
        "transport": "技术标 · 运输与装柜方案",
        "qualification": "商务标 · 资格与业绩附件",
        "reject": "标书响应声明 / 偏离表",
        "schedule": "技术标 · 工期与交付计划",
        "compliance": "商务标 · 单证与合规附件",
        "scoring": "标书编制说明 / 评分对照表",
        "validity": "投标函 · 投标有效期",
        "price": "商务标 · 投标报价（对照最高限价）",
        "quality": "投标函 · 质量承诺",
        "warranty": "投标函 / 合同条款响应 · 质保期",
        "payment": "商务标 · 合同条款响应（付款）",
    }
    if req_id == "cog_lashing":
        return "技术标 · 装柜重心与系固说明（CTU）"
    return m.get(str(category or ""), "技术标 · 通用响应")


def _compliance_label(status: Optional[str]) -> str:
    """AutoRFP-style compliance wording for matrix export / UI."""
    m = {
        "covered": "compliant",
        "partial": "partially_compliant",
        "gap": "non_compliant",
        "human_required": "needs_sme",
        "review": "pending_review",
        "pending": "not_started",
    }
    return m.get(str(status or ""), "not_started")


def _public_ref(category: Optional[str], req_id: Optional[str]) -> Optional[str]:
    """Official portal titles only. No invented clause numbers."""
    if req_id == "cog_lashing" or str(category or "") in ("transport", "packaging"):
        return (
            "IMO/ILO/UNECE CTU Code (non-mandatory) · "
            "https://unece.org/transport/intermodal-transport/"
            "imoilounece-code-practice-packing-cargo-transport-units-ctu-code"
        )
    if str(category or "") in ("qualification", "scoring"):
        return (
            "BCA Price Quality Method (PQM) Framework (portal, 26 Jan 2026) · "
            "https://www1.bca.gov.sg/growth-and-transformation/procurement/"
            "procurement-and-legal-frameworks/price-quality-method-pqm-framework/"
        )
    return None


def _knowledge_ref(category: Optional[str], req_id: Optional[str]) -> Optional[str]:
    """Map clause to in-repo tender-delivery knowledge (M4 light binding)."""
    cat = str(category or "")
    rid = str(req_id or "")
    if rid == "cog_lashing" or cat in ("transport", "packaging"):
        return "knowledge_base/08_tender_delivery/rules/transport_packaging_redlines.md"
    if cat in ("qualification", "reject", "scoring"):
        return "knowledge_base/08_tender_delivery/strategies/facade_bid_response.md"
    if cat in ("schedule", "compliance"):
        return "knowledge_base/08_tender_delivery/trajectories/TD1_tender_to_pack.md"
    return "knowledge_base/08_tender_delivery/README.md"


def _readiness_score(summary: Dict[str, Any]) -> float:
    """0–1：covered 加权，gap/review 重罚（非官方分，仅内部就绪度）。"""
    n = int(summary.get("n") or 0)
    if n <= 0:
        return 0.0
    covered = int(summary.get("covered") or 0)
    partial = int(summary.get("partial") or 0)
    gap = int(summary.get("gap") or 0)
    review = int(summary.get("review") or 0)
    human = int(summary.get("human_required") or 0)
    raw = (covered + 0.5 * partial + 0.25 * human - 0.75 * gap - 0.35 * review) / n
    return float(max(0.0, min(1.0, round(raw, 3))))


def matrix_to_csv(matrix: Dict[str, Any]) -> str:
    """Compliance-matrix CSV for bid leads (owner / status / evidence). No invented cells."""
    rows = list((matrix or {}).get("rows") or [])
    headers = [
        "req_id",
        "title",
        "exact_text",
        "requirement_type",
        "status",
        "compliance_label",
        "owner",
        "risk",
        "proposal_location",
        "requirement_ref",
        "item_kind",
    ]

    def _cell(v: Any) -> str:
        s = "" if v is None else str(v)
        s = s.replace("\r", " ").replace("\n", " ").replace('"', "'")
        if "," in s or "'" in s:
            return f'"{s}"'
        return s

    lines = [",".join(headers)]
    for r in rows:
        lines.append(",".join(_cell(r.get(h)) for h in headers))
    return "\n".join(lines) + "\n"


def matrix_to_markdown(matrix: Dict[str, Any]) -> str:
    """导出合规矩阵 Markdown（评委/客户可读，非原始 JSON）。"""
    rows = list((matrix or {}).get("rows") or [])
    sm = (matrix or {}).get("summary") or {}
    lines = [
        "# 合规响应矩阵",
        "",
        f"- 条款数: {sm.get('n', len(rows))}",
        f"- covered: {sm.get('covered', 0)} · partial: {sm.get('partial', 0)} · gap: {sm.get('gap', 0)}",
        f"- readiness: {sm.get('readiness_score', '—')}",
        "",
        "| 条款 | 类型 | 状态 | 合规 | 负责人 | 风险 | 应答落点 | 引用 |",
        "|------|------|------|------|--------|------|----------|------|",
    ]
    for r in rows:
        title = str(r.get("title") or r.get("req_id") or "").replace("|", "/")
        loc = str(r.get("proposal_location") or "").replace("|", "/")
        ref = str(r.get("requirement_ref") or "—").replace("|", "/")
        rtype = str(r.get("requirement_type") or "—").replace("|", "/")
        clabel = str(r.get("compliance_label") or _compliance_label(r.get("status"))).replace(
            "|", "/"
        )
        lines.append(
            f"| {title} | {rtype} | {r.get('status')} | {clabel} | {r.get('owner')} | {r.get('risk')} | {loc} | {ref} |"
        )
    return "\n".join(lines) + "\n"


def open_actions(matrix: Dict[str, Any]) -> List[Dict[str, Any]]:
    """人仍须处理的条款（HITL 待办，非自动盖章）。"""
    actions: List[Dict[str, Any]] = []
    for r in list((matrix or {}).get("rows") or []):
        st = str(r.get("status") or "")
        if st in ("covered",):
            continue
        actions.append(
            {
                "req_id": r.get("req_id"),
                "title": r.get("title"),
                "status": st,
                "owner": r.get("owner") or "unassigned",
                "risk": r.get("risk") or "medium",
                "proposal_location": r.get("proposal_location"),
                "action": _action_hint(st, r.get("category"), r.get("risk")),
            }
        )
    # critical / gap first
    order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    actions.sort(key=lambda a: (order.get(str(a.get("risk")), 9), str(a.get("status"))))
    return actions


def _action_hint(status: str, category: Optional[str], risk: Optional[str]) -> str:
    if status == "gap":
        return "装柜证据未覆盖：调整方案或写偏离/澄清"
    if status == "partial":
        return "部分证据已有：补齐 mid50/系固说明或人工复核"
    if status == "review":
        return "废标/实质性响应：法务/标书负责人逐条核对"
    if status == "human_required":
        if category == "qualification":
            return "附资质与类似业绩扫描件（人工）"
        if category == "schedule":
            return "项目经理确认交货期与到港节点"
        if category == "compliance":
            return "商务准备 VGM/保险/单证"
        if category == "scoring":
            return "标书编制对照评分点"
        if category in ("quality", "warranty"):
            return "项目经理/商务确认承诺值并写入投标函"
        if category == "payment":
            return "商务确认是否接受付款条件，不接受则写偏离"
        if category in ("packaging", "transport"):
            return "装柜结果不覆盖本条：交付负责人确认柜型/包装/竖放/不叠放/超限运输做法，做不到则写偏离"
        return "人工补充证据后改状态"
    return "待处理"


def build_response_package(
    *,
    matrix: Dict[str, Any],
    packing_summary: Optional[Dict[str, Any]] = None,
    parse_summary: Optional[Dict[str, Any]] = None,
    project_name: str = "幕墙项目投标应答（草稿）",
    handoff: Optional[Dict[str, Any]] = None,
    tech_outline: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """一页应答摘要：交付证据 + 矩阵就绪度 + 人工待办（可导出 Markdown）。"""
    sm = (matrix or {}).get("summary") or {}
    pack = packing_summary or {}
    actions = open_actions(matrix)
    md = _compose_package_markdown(
        project_name=project_name,
        pack=pack,
        sm=sm,
        actions=actions,
        matrix=matrix,
        parse_summary=parse_summary,
        handoff=handoff,
        tech_outline=tech_outline,
    )
    return {
        "schema": "tender.response_package.v1",
        "project_name": project_name,
        "open_actions": actions,
        "n_open": len(actions),
        "readiness_score": sm.get("readiness_score"),
        "packing_summary": pack or None,
        "markdown": md,
    }


def _compose_package_markdown(
    *,
    project_name: str,
    pack: Dict[str, Any],
    sm: Dict[str, Any],
    actions: List[Dict[str, Any]],
    matrix: Dict[str, Any],
    parse_summary: Optional[Dict[str, Any]] = None,
    handoff: Optional[Dict[str, Any]] = None,
    tech_outline: Optional[Dict[str, Any]] = None,
) -> str:
    mid = pack.get("mid50") if pack else None
    mid_s = "—"
    if mid is not None:
        try:
            mf = float(mid)
            mid_s = f"{mf * 100:.0f}%" if mf <= 1.0 else f"{mf:.0f}%"
        except (TypeError, ValueError):
            mid_s = str(mid)
    lines = [
        f"# {project_name}",
        "",
        "> 自动生成草稿 · **非**盖章投标文件 · 柜数/坐标/重心来自 tools；资质/价格/授权仅人工。",
        "",
        "## 1. 交付证据（装柜）",
        "",
    ]
    if pack:
        lines += [
            f"- can_fit: **{pack.get('can_fit')}**",
            f"- 柜型 / 使用: {pack.get('container_type') or '—'} · used={pack.get('containers_used')} · N0={pack.get('n0')}",
            f"- ship_ok: **{pack.get('ship_ok')}** · phase: {pack.get('phase') or '—'}",
            f"- mid50（CTU 中段质量）: **{mid_s}**",
            "",
        ]
    else:
        lines += ["- （未跑交付装柜）", ""]
    if parse_summary:
        days = parse_summary.get("duration_days")
        days_s = f"{days} 日历天" if days is not None else "招标未写"
        lines += [
            "## 2. 招标要点摘要",
            "",
            f"- 抽取条款: {parse_summary.get('n_requirements', '—')}",
            f"- 行项目: {parse_summary.get('n_line_items', '—')} · 评分点 {parse_summary.get('n_scoring_points', '—')} · ★项 {parse_summary.get('n_star_items', '—')}",
            f"- 类别: {', '.join(parse_summary.get('categories') or []) or '—'}",
            f"- 必应 / 关键: must={parse_summary.get('must_respond_n', '—')} · critical={parse_summary.get('critical_n', '—')}",
            f"- 工期（只抄原文）: {days_s}",
            f"- 信封: {parse_summary.get('envelope') or '招标未写'}",
            "",
        ]
        sec_ready = "3"
        sec_todo = "4"
        sec_mx = "5"
    else:
        sec_ready, sec_todo, sec_mx = "2", "3", "4"
    lines += [
        f"## {sec_ready}. 响应就绪度",
        "",
        f"- 条款: {sm.get('n', 0)} · covered: {sm.get('covered', 0)} · partial: {sm.get('partial', 0)}",
        f"- human_required: {sm.get('human_required', 0)} · gap: {sm.get('gap', 0)} · review: {sm.get('review', 0)}",
        f"- readiness_score: **{sm.get('readiness_score', '—')}**",
        "",
        f"## {sec_todo}. 人工待办（按风险）",
        "",
    ]
    if not actions:
        lines.append("- 无未覆盖项（仍须人签商务）")
    else:
        for a in actions:
            lines.append(
                f"- **[{a.get('risk')}]** {a.get('title')} · {a.get('owner')} · `{a.get('status')}` — {a.get('action')}"
            )
    ho = handoff or {}
    p0 = ho.get("p0_reject_scan") or {}
    nxt = ho.get("next_experts") or []
    if p0 or nxt:
        lines += [
            "",
            "## 经营岗交接 / P0",
            "",
            f"- 下一岗: {', '.join(nxt) or '—'}",
            f"- P0 人工确认: **{p0.get('human_confirm_required', True)}** · {p0.get('n', 0)} 项",
            f"- {p0.get('note') or '资格/废标/★项不自动关闭'}",
            "",
        ]
    outline = tech_outline or {}
    if outline.get("markdown"):
        lines += ["", "## 技术标目录（按抽出评分点）", "", outline["markdown"].rstrip(), ""]
    lines += ["", f"## {sec_mx}. 合规矩阵", ""]
    lines.append(matrix_to_markdown(matrix).rstrip())
    lines.append("")
    return "\n".join(lines)


def run_tender_pipeline(
    text: str,
    *,
    packing_summary: Optional[Dict[str, Any]] = None,
    source: str = "text",
    project_name: str = "幕墙项目投标应答（草稿）",
    p0_confirmed: bool = False,
    ingest: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """投标解析 → 清单 → 响应矩阵 → 应答包一站式（可接装柜 summary）。"""
    import uuid

    from packing_assistant.otel_hooks import span as otel_span
    from packing_assistant.tools.tender_review import review_draft

    run_id = f"tender-{uuid.uuid4().hex[:8]}"
    with otel_span(
        "tender.pipeline",
        {"run_id": run_id, "node": "tender.parse", "tool": "tender.parse"},
    ):
        parsed = parse_tender_text(text, source=source)
        reqs = list(parsed.get("requirements") or [])
        checklist = build_checklist(reqs)
        matrix = build_response_matrix(reqs, packing_summary=packing_summary)
        handoff = parsed.get("handoff") or build_handoff(reqs, duration_days=parsed.get("duration_days"))
        outline = build_tech_outline_from_handoff(handoff, project_name=project_name)
        extract_table = build_workbench_extract_table(parsed, project_name=project_name)
        package = build_response_package(
            matrix=matrix,
            packing_summary=packing_summary,
            parse_summary=parsed.get("summary"),
            project_name=project_name,
            handoff=handoff,
            tech_outline=outline,
        )
        from packing_assistant.bidbook.sg_facade import build_sg_facade_bidbook

        bidbook = build_sg_facade_bidbook(
            tender_text=text,
            parsed=parsed,
            matrix=matrix,
            packing_summary=packing_summary,
            open_actions=package["open_actions"],
            p0_confirmed=p0_confirmed,
        )
        review = review_draft(
            draft="\n".join(
                [
                    str(outline.get("markdown") or ""),
                    str((bidbook or {}).get("markdown") or ""),
                    str(package.get("markdown") or ""),
                ]
            ),
            matrix=matrix,
            packing_summary=packing_summary,
            tech_outline=outline,
            bidbook_markdown=str((bidbook or {}).get("markdown") or ""),
        )
        return {
            "schema": "tender.pipeline.v1",
            "product_mainline": "C_tender_delivery",
            "run_id": run_id,
            "parse": parsed,
            "handoff": handoff,
            "extract_table_markdown": extract_table,
            "tech_outline": outline,
            "tech_outline_markdown": outline.get("markdown"),
            "p0_reject_scan": (handoff or {}).get("p0_reject_scan"),
            "checklist": checklist,
            "matrix": matrix,
            "matrix_markdown": matrix_to_markdown(matrix),
            "matrix_csv": matrix_to_csv(matrix),
            "open_actions": package["open_actions"],
            "response_package": package,
            "export_markdown": package["markdown"],
            "bidbook": bidbook,
            "bidbook_markdown": bidbook.get("markdown"),
            "review": review,
            "p0_confirmed": bool(p0_confirmed),
            "submit_blocked": True,
            "submit_block_reason": (
                "P0 资格/废标/★项尚未人工确认；成果仍是 AI 草稿，不可递交。"
                if not p0_confirmed
                else "已记录 P0 核对，仍是 AI 草稿：无盖章、无业绩附件、不可递交。"
            ),
            "ingest": ingest,
            "ok": bool(reqs),
        }
