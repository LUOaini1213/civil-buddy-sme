"""Workbench task routing, separate from the shared Python/Rust legacy matcher.

Pure routing data only: no execution, model calls, approval, or inferred inputs.
The caller validates enabled/custom selections against its live catalog.
"""
from __future__ import annotations

import re

from packing_assistant.expert_roster import list_experts
from packing_assistant.intent_contract import contract_strong
from packing_assistant.understand import understand

_TASK_PHRASES = {
    "pm-daily": ("整理日报", "项目日报", "写日报", "做日报", "生成日报", "日报模板", "工程日志"),
    "dispatch": ("调度日报", "生产调度", "调度指令"),
    "it-data": ("备份策略", "备份计划", "备份方案", "恢复演练", "数据备份"),
    "steel": ("钢构说明", "钢结构说明", "钢结构", "钢构"),
    "bid-parse": ("招标解析", "解析招标", "抽取评分点", "提取评分点", "评分点表"),
    "bid-compliance": ("投标响应", "响应检查", "检查响应", "响应缺口", "废标检查", "废标风险"),
    "bid-tech": ("技术标", "技术响应草稿"),
    "construction": ("施工方案", "专项方案", "专项施工方案"),
    "admin-office": ("会务清单", "会议安排", "整理会务", "接待清单"),
    "method-hazard": ("危大识别", "危大判定", "是否危大", "危大工程"),
}
_DETERMINER = re.compile(r"(解析|分析|梳理|检查|核对)(?:一下|下)?(?:这份|这个|这一份|该份|该|此份|此|本|这)(?=招标|投标|磋商|谈判|采购|响应|技术标)")
_AMBIGUOUS_REASONS = {
    "design-coord": "处理图纸会审、专业接口与设计变更技术事项",
    "variation": "整理变更签证的事实、依据与工程量栏",
}
_PREFIX = r"(?:(?:请帮我|帮我|麻烦|请|这次|本次|暂时|现在|进一步|继续|接着|先|再|仅仅|只需|只要|仅|只)\s*)*"
_NEGATED = re.compile(r"^" + _PREFIX + r"(?:不要|不用|无需|暂不|不需要|不想|不做|不写|不生成|别|禁止|我不(?:想|需要|打算|要求))"
                      r"|(?i:^(?:(?:please|pls|just)\s+)?(?:don[’']?t|dont|do\s+not|no\s+need|not\s+(?:yet|now)|never|stop|hold\s+off|skip|cancel)(?=\s+[a-z]|\s*$))")
# 意图判定用的三张词表，各分组；每一组带来多少请求、又放进来多少提问，
# 见 scripts/eval_task_intent.py --variant all（基准 test/benchmarks/task_intent）。
# "core" 三组合起来就是改动前的写法，保留它是为了随时能量出「之前是多少」。
ACTION_VERBS = {
    "core": ("整理", "编制", "生成", "制作", "起草", "准备", "审查", "核对", "检查", "评审", "制定", "写", "做"),
    # 起草类口语动词。单字动词只在后面跟着量词/「一下」时才算：「编一份」算，「编制依据」里的编不靠这条。
    # 「汇总的时候按班组还是按工种」里的汇总是名词用法：后面跟「的」不算动作（留出集第一轮量出来的误执行）。
    "draft": ("草拟", r"汇总(?!的)", r"更新(?!的)", r"拟(?=一?[份个张版]|写|定一?[份个])", r"编(?=一?[份个张版下]|写)",
              r"填(?=一?[份个张下]|写|报)", r"补(?=一?[份个张]|写|填)", r"(?:弄|记|搞|改)一下"),
    # 「给我一份 / 来一份 / 出个 / 要一份」：要的是东西，不是解释。量词是关键——「给我讲讲」「来了多少人」不带。
    "give": (r"(?:给我|帮我|替我|为我)\s*(?:出|来|弄|搞|开|列)?\s*一?[份个张版](?!人)", r"(?:出|来|搞|弄|开|列)(?=一?[份个张版](?!人))",
             r"(?:要|需要)一[份个张版]", r"出(?=日报|周报|月报|方案|计划|台账|清单|纪要|交底|报表)"),
}
QUESTION_MARKS = {
    "core": ("什么", "如何", "怎么", "为什么", "能做什么", "需要哪些", "有哪些", "是否", "能否", "能不能", "区别", "含义"),
    # 动词表一放宽，这些问法就会被当成请求执行掉（「台账多久更新一次」「谁来拟纪要」），所以两张表必须一起加。
    "more": ("谁", "多久", "多少", "几[个份天次时]", "要不要", "是不是", "有没有", "何时", "什么时候", "哪[些个里]", r"吗\s*[？?]?\s*$", r"[？?]\s*$",
             "还是"),    # 「按班组还是按工种」是选择问句。「还是出一份吧」会因此漏成提问——便宜的方向，接受。
}
READ_VERBS = {
    "core": ("解释", "说明一下", "介绍", "聊聊", "讨论", "说说", "讲解", "科普", "概述", "咨询"),
    "more": ("讲讲", "讲一下", r"说明(?!书)"),
}
ACTION_VARIANTS = {
    "core (before)": {"verbs": ("core",), "questions": ("core",), "reads": ("core",)},
    "+draft verbs": {"verbs": ("core", "draft"), "questions": ("core",), "reads": ("core",)},
    "+give verbs": {"verbs": ("core", "draft", "give"), "questions": ("core",), "reads": ("core",)},
    "+question marks": {"verbs": ("core", "draft", "give"), "questions": ("core", "more"), "reads": ("core",)},
    "+read verbs": {"verbs": ("core", "draft", "give"), "questions": ("core", "more"), "reads": ("core", "more")},
    "ablate: question marks": {"verbs": ("core", "draft", "give"), "questions": ("core",), "reads": ("core", "more")},
    "+english (shipped)": {"verbs": ("core", "draft", "give"), "questions": ("core", "more"), "reads": ("core", "more"), "english": True},
}
ACTION_VARIANTS["shipped"] = dict(ACTION_VARIANTS["+english (shipped)"])

# English. An imperative starts its clause and takes an object, so a verb counts only there and only before a
# determiner, a deliverable or a file: "I'll write it later", "Plan B is ...", "Update on the tender: ...", "Review
# comments are in ..." are not requests. Deliverable nouns are the contract's Latin strong phrases ("Daily report for
# block B: ..."); one followed by a status ("... was wrong", "... on Monday at 8am") is news about it, not a request.
# Scored on test/benchmarks/task_intent/heldout_en*.json by scripts/test_english_intents.py.
_EN_CLAUSE = r"(?:^|(?<=，)|(?<=[.!?;:]\s))\s*"
_EN_LEAD = r"(?:(?:please|pls|kindly|also|now|first|then|just|so|ok|okay|and|go\s+ahead\s+and|help\s+me|i\s+need\s+you\s+to|let[’']?s)[\s,]+)*"
_EN_VERBS = ("write", "draft", "prepare", "generate", "create", "produce", "compile", r"make(?!\s+sure)", "update", "revise",
             "amend", "edit", "redo", r"fill\s+(?:in|out|up)", r"put\s+together", "summari[sz]e", "parse", "extract", "review",
             "check", "plan", "pack", "list", "tabulate", "outline", "calculate", r"work\s+out", "record")
_EN_DELIVERABLES = "|".join(r"\s+".join(map(re.escape, phrase.split())) for phrase in sorted(
    {p for p, _ in contract_strong() if p.isascii() and " " in p and p.split()[0] not in ("the", "this", "how")}, key=len, reverse=True))
_EN_VERB = "(?:" + "|".join(_EN_VERBS) + r")(?![-\w])"
_EN_AUX = r"(?:is|are|was|were|am|do|does|did|has|have|had|can|could|should|would|will|shall|may|might|must)"
_EN_OBJECT = (r"\s+(?:up\s+)?(?:(?:a|an|the|this|these|those|our|my|your|one|(?:today|tomorrow|yesterday)[’']?s|(?:this|next)\s+week[’']?s)(?![-\w])"
              r"|(?:" + _EN_DELIVERABLES + r")|[\w.-]+\.(?:xlsx|xlsm|xls|csv|md|docx|doc|pdf|txt|json)\b)")
_EN_STATUS = (r"\b(?:" + _EN_AUX + r"|be|been|isn[’']?t|wasn[’']?t|went|got|came|\w+ed\s+by|approved|rejected|sent|received|submitted"
              r"|attached|done|completed|cancell?ed|postponed|delayed|moved|attend\w*)\b")
_EN_WHEN = r"(?:(?:mon|tues|wednes|thurs|fri|satur|sun)day|\d{1,2}(?::\d{2})?\s*[ap]\.?m\b|\d{1,2}:\d{2})"
_EN_ACTION = ("(?i:" + _EN_CLAUSE + _EN_LEAD + r"(?:" + _EN_VERB + _EN_OBJECT + r"|(?:calculate|work\s+out)\s+how\s+(?:many|much)\b)"
              + "|" + _EN_CLAUSE + r"(?:(?:today[’']?s|tomorrow[’']?s|a|an|new)\s+)?(?:" + _EN_DELIVERABLES + r")(?:e?s)?\s*"
              + r"(?:[:\-–—](?!\s*" + _EN_STATUS + r")|(?:for|on|about)\b(?:(?!" + _EN_STATUS + r")[^:.!?;]){0,120}:(?!\d)"
              + r"|(?:for|on|about)\b(?:(?!" + _EN_STATUS + "|" + _EN_WHEN + r")[^:]){0,300}$)"
              + "|" + _EN_CLAUSE + _EN_LEAD + r"(?:(?:i|we)\s+(?:need|want|would\s+like)|give\s+me|send\s+me)\s+(?:(?:a|an|the|today[’']?s|new)\s+)?(?:"
              + _EN_DELIVERABLES + r")(?!\s+from\b))")
_EN_QUESTION = ("(?i:" + _EN_CLAUSE + r"(?:(?:so|and|but|ok|okay|hi|hey|also|then|just|quick\s+question)[\s,]+)*"
                r"(?:what|whats|how|why|when|where|who|whom|whose|which|isn[’']?t|aren[’']?t|" + _EN_AUX + r"(?!\s+not\b))(?![-\w])"
                r"|\bwhether\b|\b(?:check|see|confirm|find\s+out)\s+if\b)")
_EN_READ = (r"(?i:(?:(?:please|pls|kindly|just|first|now|so|ok|okay|can\s+you|could\s+you|would\s+you)[\s,]+)*"
            r"(?:explain|describe|tell\s+me|walk\s+me\s+through|talk\s+me\s+through|clarify|remind\s+me|help\s+me\s+understand)\b)")
_EN_FOLLOWUP = (r"(?i:\b(?:and\s+then|then|after\s+that|afterwards|next)[\s,，]+(?:please\s+)?(?:(?:help\s+me|can\s+you|could\s+you)\s+)?"
                + _EN_VERB + r"(?:" + _EN_OBJECT + r"|\s+(?:it|them)\b))")
# "Can you pack these into 40HQ?" asks for the plan, not whether it is possible.
_EN_POLITE = re.compile(r"(?i)^\s*(?:(?:please|pls|kindly|hi|hey|ok|okay)[\s,，]+)?(?:can|could|would|will)\s+you\s+(?:please\s+|kindly\s+)?(?="
                        + _EN_VERB + ")")
_EN_STOP = re.compile(r"[.!?;]\s+")


def compile_intent(verbs=("core",), questions=("core",), reads=("core",), english=False):
    """(_QUESTION, _READ_REQUEST, _ACTION, _FOLLOWUP_ACTION) for the chosen word groups."""
    verb = "|".join(part for group in verbs for part in ACTION_VERBS[group])
    en_q, en_r, en_a, en_f = ("|" + p for p in (_EN_QUESTION, _EN_READ, _EN_ACTION, _EN_FOLLOWUP)) if english else ("",) * 4
    return (re.compile("|".join(part for group in questions for part in QUESTION_MARKS[group]) + en_q),
            re.compile(r"^" + _PREFIX + "(?:" + "|".join(part for group in reads for part in READ_VERBS[group]) + en_r + ")"),
            re.compile(r"(?:帮我|请|需要|想要|先|再|只)?\s*(?:" + verb + ")" + en_a),
            re.compile(r"(?:然后|并且|之后|随后|接着|同时|再|并|后)\s*(?:请帮我|帮我|请|给我|为我)?\s*(?:" + verb + ")" + en_f))


_QUESTION, _READ_REQUEST, _ACTION, _FOLLOWUP_ACTION = compile_intent(**ACTION_VARIANTS["shipped"])
# Contract strong phrases in Latin script, matched on word boundaries (plural s allowed).
_LATIN_STRONG = tuple((re.compile(r"(?<![a-z0-9_-])" + r"\s+".join(map(re.escape, phrase.split())) + r"(?:e?s)?(?![a-z0-9_-])", re.I),
                       phrase, eid) for phrase, eid in contract_strong() if phrase.isascii())
_QUOTED = re.compile(r'```[\s\S]*?```|`[^`\n]*`|“[^”]*”|‘[^’]*’|「[^」]*」|『[^』]*』|"[^"\n]*"|(?<!\w)\'[^\'\n]*\'')
_REFERENCE_END = re.compile(r"的(?:流程|注意事项|注意点|步骤|原因|含义|区别|意义|作用|风险|要求|方法|思路)[？?。！!\s]*$")
_SEQUENCE = re.compile(r"先.+(?:再|然后)|之后|随后|接着", re.S)
_TENDER = ("bid-parse", "bid-tech", "bid-compliance")
# Tender <-> packing in one run (tender_packing_link.py, reached through the 招标解析 post): the tender's logistics
# clauses, a plan from the named panel list under them, and the statements that answer them. Asked for by name only:
# "the tender" and "packing list" alone still go to their own posts. A dot inside a file name does not end the sentence.
_LINK_ZH = ("物流应答", "装柜应答", "投标装柜联动", "招标装柜联动", "标书装柜联动", "招标与装柜联动", "按招标装柜", "按招标要求装柜",
            "按招标条款装柜")
_LINK_EN = re.compile(
    r"(?i)\b(?:(?:tender|bid|itt)[\s-]+(?:packing|logistics|shipping|delivery)[\s-]+(?:link|response|section|statements?)"
    r"|logistics\s+(?:response|section|statements?)"
    r"|link(?:s|ed|ing)?\s+(?:the\s+|this\s+|our\s+)?(?:tender|itt|bid)\b(?:[^.?!]|\.(?=\w)){0,120}?\b(?:packing|panel|loading)\s+(?:list|plan)"
    r"|(?:pack|plan)\b(?:[^.?!]|\.(?=\w)){0,80}?\b(?:to|against|under)\s+the\s+(?:tender|itt)(?:[’']s)?\s+(?:clauses?|logistics|terms|requirements))\b")
_FILE = re.compile(r"[\w.-]+\.(?:xlsx|xlsm|xls|csv|md|docx|pdf|txt)\b", re.I)
# The other way in, for the wording an estimator actually uses ("Check facade_panels.xlsx against the shipping
# requirements in facade_itt_doc.md", "Match the ITT against the panel list", "核对 X.xlsx 是否满足 Y.md 的物流条款"):
# the request names one tender document and one panel / packing list, and asks to set one against the other. The
# table must read as a packing list (its name or the words around it), and the document as the tender, so a BOQ or
# a price schedule checked against a tender stays with its own post. Scored on test/benchmarks/link_routing/dev.json
# (a DEV set: written before this rule and used to build it) by scripts/eval_link_routing.py.
_TABLE_FILE = re.compile(r"[\w.-]+\.(?:xlsx|xlsm|xls|csv)\b", re.I)
_DOC_FILE = re.compile(r"[\w.-]+\.(?:md|docx|pdf|txt)\b", re.I)
_PACKING_WORD = re.compile(r"(?i)pack(?:ing)?|panel|loading|crate|container|shipping|shipment|logistic|cargo|stillage|(?<![a-z])pl[_-]"
                           r"|装箱|装柜|箱单|板块|面板|货物")
_TENDER_WORD = re.compile(r"(?i)(?<![a-z])(?:tender|itt|bid|rfp|invitation)|招标|标书|投标")
_LINK_CUE = re.compile(
    r"(?i)\b(?:link(?:s|ed|ing)?|match(?:es|ed|ing)?|(?:cross-)?check(?:s|ed|ing)?|against|compl(?:y|ies|iance|iant)"
    r"|meet(?:s|ing)?|clauses?|statements?|logistics\s+(?:section|response|requirements?)|shipping\s+requirements?)\b"
    r"|联动|对照|核对|比对|匹配|满足|符合|条款|物流要求|运输要求|装柜要求|物流章节")
# A link request put as "how do I ... / why is ... / what does ..." asks about the link; "does X meet Y?", "check
# whether X meets Y" and "which statements does the plan support?" ask for it to be run.
_ASKS_ABOUT = re.compile(r"(?i)(?:^|[.?!]\s+)\W*(?:how|why|what|when|where|who)\b|怎么|怎样|如何|为什么|为何|什么|是啥")
_LINK_REFUSED = re.compile(
    r"(?i)(?:\b(?:not|never|without|no)|n[’']t)\b(?:\W+\w+){0,5}?\W+(?:link(?:ing)?|check(?:ing)?|cross-check(?:ing)?"
    r"|match(?:ing)?|compar(?:e|ing)|map(?:ping)?)\b"
    r"|\b(?:ignor(?:e|ing)|skip(?:ping)?|disregard(?:ing)?|leave\s+out|hold\s+off(?:\s+on)?)\b(?:\W+\w+){0,3}?\W+"
    r"(?:clauses?|tender|itt|link|linking|check|requirements?)\b"
    r"|(?:不要|别|无需|不用|暂不|先不|不想|跳过|忽略)\S{0,6}?(?:核对|对照|联动|比对|匹配|条款)")
_LINK_ACTION = re.compile(
    r"(?i)(?:^|[，,:;.!?]\s*)\s*" + _EN_LEAD
    + r"(?:(?:can|could|would|will)\s+you\s+(?:please\s+)?)?"
    r"(?:link|match|map|compare|(?:cross-)?check|verify|answer)\b"
    r"|^" + _PREFIX + r"(?:核对|对照|比对|匹配|把|用|按招标|招标装柜联动|投标装柜联动|标书装柜联动)")
_LINK_CHECK_QUESTION = re.compile(
    r"(?i)^\s*(?:do|does)\b.{0,500}\b(?:comply|meet|satisfy)\b"
    r"|\bwhich\s+(?:bid\s+)?statements\b.{0,100}\bplan\b.{0,80}\bsupport\b"
    r"|^装箱单.{0,500}(?:符合|满足).{0,500}吗[？?]?$")


def _link_requests_execution(text: str) -> bool:
    """A named link is a topic, not authorization. Promote only a positive action/check request."""
    clean = _FILE.sub("〔文件〕", _positive_text(_QUOTED.sub("〔引用〕", text)))
    if _READ_REQUEST.search(clean) or _ASKS_ABOUT.search(clean):
        return False
    return bool(_LINK_ACTION.search(clean) or _LINK_CHECK_QUESTION.search(clean))


def _names_tender_and_list(text: str) -> bool:
    tables = set(m.group() for m in _TABLE_FILE.finditer(text))
    documents = set(m.group() for m in _DOC_FILE.finditer(text))
    if len(tables) != 1 or len(documents) != 1:
        return False
    rest = _FILE.sub(" ", text)
    (table,), (document,) = tables, documents
    return bool((_PACKING_WORD.search(table) or _PACKING_WORD.search(rest))
                and (_TENDER_WORD.search(document) or _TENDER_WORD.search(rest)))


def wants_link(message: str) -> bool:
    """The request asks for the tender and the packing to be done as one linked run."""
    text = _QUOTED.sub("〔引用〕", message or "")
    if _LINK_REFUSED.search(_FILE.sub(" ", text)):
        return False
    text = _positive_text(text)
    if any(phrase in text for phrase in _LINK_ZH) or _LINK_EN.search(text):
        return True
    return _names_tender_and_list(text) and bool(_LINK_CUE.search(_FILE.sub(" ", text)))


def _positive_text(message: str) -> str:
    return "，".join(part.strip() for part in re.split(r"[，,。；;\n]", message)
                    if part.strip() and not _NEGATED.match(part.strip()))


def _intent(text: str) -> str:
    # Quoted operations describe source material, not actions authorized by this
    # turn. Keep the original text separately for selecting the relevant post.
    text = _positive_text(_QUOTED.sub("〔引用〕", text))
    if polite := _EN_POLITE.match(text):
        text = text[polite.end():].rstrip(" ?？")
    base = understand(text)
    read_request = bool(_QUESTION.search(text) or _READ_REQUEST.search(text))
    if read_request:
        followup_action = False
        for match in _FOLLOWUP_ACTION.finditer(text):
            clause_start = text.rfind("，", 0, match.start()) + 1
            if match.group()[0].isascii():      # "What goes in a site diary? Then draft one": a sentence also ends at "? "
                clause_start = max([clause_start, *(stop.end() for stop in _EN_STOP.finditer(text, 0, match.start()))])
            clause_end = text.find("，", match.end())
            clause_end = len(text) if clause_end < 0 else clause_end
            # "为什么先检查再生成" asks about a sequence. A separate
            # "然后生成" clause, or "解释后生成", still requests a deliverable.
            if not _QUESTION.search(text[clause_start:match.start()]) and not _REFERENCE_END.search(text[match.end():clause_end]):
                followup_action = True
                break
        return "both" if followup_action else "chat"
    if _ACTION.search(text):
        return "both" if base == "both" else "run"
    return base


def _label(eid: str, roster: dict) -> str:
    expert = roster.get(eid)
    return expert.name if expert else eid


def _candidate(eid: str, roster: dict) -> dict:
    expert = roster[eid]
    return {"expert_ids": [eid], "label": expert.name,
            "reason": _AMBIGUOUS_REASONS.get(eid, expert.title)}


def _steps(ids: list[str], workflow: str, text: str, roster: dict) -> list[dict]:
    if workflow == "tender-review":
        return [
            {"id": "parse", "expert_id": "bid-parse", "label": "解析招标原文", "depends_on": []},
            {"id": "tech", "expert_id": "bid-tech", "label": "整理技术响应", "depends_on": ["parse"]},
            {"id": "compliance", "expert_id": "bid-compliance", "label": "检查响应缺口", "depends_on": ["parse"]},
            {"id": "aggregate", "expert_id": "", "label": "汇总证据与未解决事项", "depends_on": ["tech", "compliance"]},
        ]
    sequential = bool(_SEQUENCE.search(text))
    return [{"id": f"task-{i + 1}", "expert_id": eid, "label": _label(eid, roster),
             "depends_on": [f"task-{i}"] if sequential and i else []} for i, eid in enumerate(ids)]


def _mention_labels(roster: dict) -> dict[str, set[str]]:
    labels: dict[str, set[str]] = {}
    for expert in roster.values():
        for label in (expert.id, expert.name, *expert.aliases):
            if label:
                labels.setdefault(label.casefold(), set()).add(expert.id)
    return labels


def _without_leading_mentions(text: str, roster: dict) -> str:
    """Known addressing prefixes do not change the intent of the following words."""
    labels = sorted(_mention_labels(roster), key=len, reverse=True)
    while marker := re.match(r"(?:[@$]|召唤)\s*", text):
        tail = text[marker.end():]
        label = next((label for label in labels if tail.casefold().startswith(label)
                      and not (re.fullmatch(r"[a-z0-9-]+", label)
                               and re.match(r"[a-z0-9_-]", tail[len(label):], re.I))), None)
        if label is None:
            break
        text = tail[len(label):].lstrip(" \t\r\n,，:：;；")
    return text


def _explicit(text: str, roster: dict) -> tuple[list[str], list[dict]]:
    """Longest exact label after @/$/召唤; shared aliases remain ambiguous."""
    labels = _mention_labels(roster)
    ordered = sorted(labels, key=len, reverse=True)
    ids: list[str] = []
    candidates: list[dict] = []
    for marker in re.finditer(r"[@$]|召唤", text):
        tail = text[marker.end():].lstrip().casefold()
        for label in ordered:
            if not tail.startswith(label):
                continue
            if re.fullmatch(r"[a-z0-9-]+", label) and re.match(r"[a-z0-9_-]", tail[len(label):]):
                continue
            matches = labels[label]
            if len(matches) > 1:
                candidates.extend(_candidate(eid, roster) for eid in sorted(matches))
            else:
                eid = next(iter(matches))
                if eid not in ids:
                    ids.append(eid)
            break
    return ids, candidates


def route_task(message: str, expert_ids: list[str] | None = None) -> dict:
    """Return selection, ambiguity and dependency data without doing any work."""
    if not isinstance(message, str):
        raise ValueError("任务内容必须是文字")
    roster = {expert.id: expert for expert in list_experts()}
    text = _positive_text(message.strip())
    result = {"expert_ids": [], "workflow": "", "candidates": [], "reason": "尚未匹配明确岗位，可浏览岗位目录选择。",
              "ambiguous": False, "intent": _intent(_without_leading_mentions(message.strip(), roster)), "steps": []}
    explicit = False
    if expert_ids:
        if any(not isinstance(eid, str) or not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", eid) for eid in expert_ids):
            raise ValueError("岗位选择格式无效")
        ids = list(dict.fromkeys(expert_ids))
        explicit = True
        result["reason"] = "按你已选择的岗位执行。"
    else:
        ids, candidates = _explicit(text, roster)
        if candidates:
            result.update(ambiguous=True, candidates=candidates, reason="这个岗位称呼有多个含义，请选择本次要处理的事项。")
            return result
        explicit = bool(ids)
        if explicit:
            result["reason"] = "按任务中明确点名的岗位执行。"
    if not explicit and wants_link(text):
        ids = ["bid-parse"]
        result["reason"] = "招标与装柜联动：先读招标的物流条款，再按条款柜型用点名的装箱单真算，逐条写应答并记联动。"
        if result["intent"] == "chat" and _FILE.search(text) and _link_requests_execution(text):
            # "按招标 X.md 和 Y.xlsx 出物流应答" names its inputs: it asks for the run. So does "check whether Y.xlsx meets
            # the clauses of X.md" or "does Y.xlsx comply with X.md?"; "how do I check Y.xlsx against X.md?" does not.
            result["intent"] = "run"
    elif not explicit:
        comprehensive = (bool(re.search(r"招标|投标|技术标", text))
                         and bool(re.search(r"综合|全面|整体|成套|完整|全套|三岗", text))
                         and bool(re.search(r"检查|审查|响应|评审|审阅", text)))
        if comprehensive:
            ids = list(_TENDER)
            result["workflow"] = "tender-review"
            result["reason"] = "综合投标响应检查需要先解析原文，再分别整理技术响应和检查缺口，最后汇总。"
        else:
            hits: dict[str, tuple[int, int]] = {}
            matched_labels: dict[str, set[str]] = {}
            # "解析这份招标文件" asks what "解析招标文件" asks: a determiner between the verb and the document it names
            matchable = _DETERMINER.sub(lambda found: found.group(1), text)
            for expert in roster.values():
                phrases = set(_TASK_PHRASES.get(expert.id, ())) | {p for p in (expert.name, *expert.aliases) if len(p) >= 4}
                for phrase in phrases:
                    if phrase in matchable:
                        hits[expert.id] = max(hits.get(expert.id, (0, 0)), (len(phrase), -matchable.index(phrase)))
                        matched_labels.setdefault(phrase, set()).add(expert.id)
            # English, only when no Chinese phrase named the post: the job is the first contract phrase the request
            # verb governs ("For the tender, draft a method statement"; "2 containers arrived, write the daily
            # report"), else the earliest one (a pasted ITT opens with "Invitation to Tender"; "then draft one").
            # "the tender response" is one phrase, not "the tender".
            def latin_from(pos: int) -> list:
                found = [(m.start(), m.end(), phrase, eid) for pattern, phrase, eid in _LATIN_STRONG
                         if eid in roster and (m := pattern.search(matchable, pos))]
                return [h for h in found if not any(o[1] - o[0] > h[1] - h[0] and o[0] < h[1] and h[0] < o[1] for o in found)]
            latin = latin_from(0) if not hits else []
            if latin:
                polite = _EN_POLITE.match(matchable)
                verbs = [found.start() for found in (_ACTION.search(matchable), _FOLLOWUP_ACTION.search(matchable)) if found]
                anchor = polite.end() if polite else min(verbs, default=len(matchable) + 1)
                start, _, phrase, eid = min(latin_from(anchor) or latin)
                hits[eid] = max(hits.get(eid, (0, 0)), (len(phrase), -start))
                matched_labels.setdefault(phrase, set()).add(eid)
            # A shared label alone cannot justify selecting two different duties.
            ambiguous = {eid for label, owners in matched_labels.items() if len(owners) > 1
                         for eid in owners if hits.get(eid, (0,))[0] <= len(label)}
            if len(ambiguous) > 1:
                result.update(ambiguous=True, candidates=[_candidate(eid, roster) for eid in sorted(ambiguous)],
                              reason="任务同时对应不同职责，请明确要技术会审还是变更签证等具体交付。")
                return result
            # A specific compound name wins over its contained general label.
            for label, owners in matched_labels.items():
                for other, other_owners in matched_labels.items():
                    if label != other and label in other and len(owners) == 1:
                        eid = next(iter(owners))
                        if eid not in other_owners and hits.get(eid, (0,))[0] <= len(label):
                            hits.pop(eid, None)
            ids = sorted(hits, key=lambda eid: (-hits[eid][1], -hits[eid][0]))
            if ids:
                result["reason"] = "根据任务中的具体交付和专业词选用：" + "、".join(_label(eid, roster) for eid in ids) + "。"
    if len(ids) > 8:
        result.update(ambiguous=True, candidates=[_candidate(eid, roster) for eid in ids if eid in roster],
                      reason="涉及岗位较多，请先选择本轮的主要交付。")
        return result
    if set(ids) == set(_TENDER) and result["intent"] != "chat":
        result["workflow"] = "tender-review"
        ids = list(_TENDER)
    # Educational questions can route to a post, but never launch a workflow.
    if result["intent"] == "chat":
        result["workflow"] = ""
    result["expert_ids"] = ids
    result["steps"] = _steps(ids, result["workflow"], text, roster)
    return result
