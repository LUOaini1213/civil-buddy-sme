"""不该由 AI、也不该由一份草稿下的结论：可以订舱、符合招标文件的要求、可以开工、报审通过……

数字溯源（tools/number_provenance）管「这个数有没有出处」，管不了「这句话是不是在下结论」。
实测本机小模型在装柜回复里写了「可以订舱」（工具报告写的是不可直接订舱），在招标对照里写了
「符合招标文件的要求」（工具只给候选对照，明确不判合规）。这里是对应的确定性检查：

    stated_verdicts(text) -> 文中**下了**的结论（原文片段、位置）

「下了」是关键，三种情形不算，判定范围都是**同一个分句**：
    否认 / 疑问    不判定可以开工 · 是否可以订舱 · 可以订舱吗？
    条件 / 要求    验收合格后方可进入下道工序 · 补齐之后才谈得上可以投标 · 须满足规范要求
    前一分句的否定不算：「重量不大，体积也不大，可以订舱」照报

数字见 test/benchmarks/verdicts/README.md（scripts/eval_verdicts.py --variant all）。先说最要紧的一条：
开发集上是满分，第一轮留出集上**只有 P 0.667 / R 0.500**——第一版是字面短语表，没见过的说法抓不到一半，
还误报了两句条件句。现在的句式模式和条件判定就是据此改的；改完之后的数以第二轮留出集为准。

结论的种类宁紧勿宽：只收投标、开工、订舱发运、验收、报审论证、对招标/规范/合同的符合性这几类
本产品明令不许下的结论。「可以使用」「可以实施」这类泛用说法不收。要加，先往基准里加正例和反例。

English (the bid output is English): a separate rule family, _EN_PATTERNS, for the same verdicts in English:
every clause / requirement covered or met, complies / compliant / conforms with the tender, ITT, specification,
contract or code, approved (for submission), passes review or inspection, ready / cleared / can / may to submit,
book, ship, bid or commence, no risk of disqualification, substantially responsive. An English match is let
through when, as for Chinese, its clause negates it (not, never, none, nothing, n't, whether, if) or makes it a
condition or a requirement (must, shall, should, once, after, unless, provided, only after ... on either side, or a
sentence that opens with If / Once / Unless ...), and also when its sentence is a question (ends with "?"), sits
inside quotation marks, or reports someone else (says, stated, claims, asks, according to ...). Before it,
only "ready to submit" and "compliant with the tender" were caught: English DEV set (english_dev.json, written
before the family and used to build it, so not a held-out number): its first 65 sentences scored P 0.500 / R 0.069 before, see
test/benchmarks/verdicts/README.md for after. The Chinese rules and their numbers are unchanged.
Round 3 (dev_round3.json, 2026-09-27): a leading "with no ... / having found no ... / without ..." phrase does not
negate the verdict after it, after / when are conditions only before a subordinate clause, "I would say" / "you
should go ahead and" state the verdict, and "every clause of the ITT" is a noun phrase like "every clause".
"""

from __future__ import annotations

import re
from typing import Any, Dict, List

from packing_assistant.tools.tender_review import ASSERTIVE

#: 与 runtime/agent_loop.FORBIDDEN 同一组词；放在这里是为了让 tools 层不反过来依赖 runtime。
_FORBIDDEN = ("可以投标", "可以开工", "中标率")
#: 第一版：字面短语。保留它是为了量得出「句式模式带来了什么」。
_LITERAL = (
    "可以订舱", "可直接订舱", "可以发运", "可以发货",
    r"符合招标(?:文件)?的?要求", r"满足招标(?:文件)?的?要求", "已实质性响应", "已实质响应", "没有废标风险", "无废标风险",
    "验收合格", "可以验收",
    r"compliant with the tender", r"ready to submit", r"ready to book", r"may commence work",
)
_PATTERNS = (
    r"可(?:以|直接)?(?:投标|开工|订舱|发运|发货|验收|报审)",
    r"可以投(?=[。，,；;！!\s]|$)",                                   # 「这个标可以投。」；不含「可以投入使用」
    r"(?:符合|满足)(?:招标文件|招标|规范|设计|合同|标准|图纸)(?:的)?(?:全部|各项)?要求",
    r"(?:已经?)?通过(?:了)?(?:专家论证|专家评审|报审|审查|验收)", r"(?:论证|验收|报审)通过",
    r"(?:没有|不存在|无)废标(?:风险|问题|项)", r"已实质性?响应",
    r"验收合格",
    r"compliant with the (?:tender|specification|contract)", r"ready to (?:submit|book|ship)", r"may commence work",
)
#: the three English entries above are what the guard had before the English family; ``english=False`` keeps them
_PATTERNS_ZH = _PATTERNS[:-3]

# English family. One word of a noun phrase, and the documents a compliance verdict is about.
_W = r"(?:[\w'’-]+\s+)"
_DOC = (r"(?:tender(?:\s+documents?)?|ITT|RFP|RFQ|RFT|specifications?|spec|contract|conditions\s+of\s+contract|"
        r"bid\s+documents?|requirements|drawings|standards?|code|regulations|rules|criteria|clauses)\b")
_DOC_NP = r"(?:the\s+|all\s+(?:the\s+)?|every\s+|each\s+|its\s+|this\s+|our\s+)?" + _W + r"{0,2}?" + _DOC
_FOR = r"(?:submission|booking|shipment|shipping|dispatch|construction|installation|tender(?:ing)?|issue|fabrication|use|delivery)\b"
_ITEMS = r"(?:requirements?|clauses?|statements?|items?|conditions|criteria|points?|rows?)"
# "every clause of the ITT", "all requirements in the tender documents" (round 3, dev_round3.json)
_ITEMS_OF = (r"(?:\s+(?:of|in|from)\s+(?:the\s+|this\s+|our\s+|your\s+)?(?:tender|ITT|RFP|RFQ|RFT|specifications?|contract|"
             r"bid|response|submission)(?:\s+documents?)?)?")
_EN_PATTERNS = (
    # every clause / requirement covered, met, answered ("nearly all ..." is a count, held to the record by claim_check)
    r"(?<!nearly\s)(?<!almost\s)\b(?:all|every|each)\s+(?:of\s+the\s+|the\s+)?" + _W + r"{0,2}?" + _ITEMS + _ITEMS_OF
    + r"\s+(?:is|are|has\s+been|have\s+been|was|were)\s+(?:(?:now|fully|all|already)\s+){0,2}"
      r"(?:covered|met|satisfied|fulfill?ed|addressed|answered|compliant|approved)\b",
    r"\b(?:covers?|covered|addresses|addressed|answers|answered)\s+(?:all|every|each)\s+(?:of\s+the\s+|the\s+)?" + _W
    + r"{0,2}?" + _ITEMS + r"\b",
    r"\b(?:is|are)\s+(?:now\s+)?fully\s+(?:covered|addressed|met)\b",
    r"\beverything\s+(?:is|has\s+been)\s+(?:now\s+)?(?:covered|addressed|met|compliant|approved|in\s+order)\b",
    # meets / satisfies / fulfils the requirements
    r"\b(?:meets?|met|meeting|satisf(?:y|ies|ied|ying)|fulfil(?:s|l|ls|led|ling)?)\s+"
    r"(?:all\s+(?:of\s+)?(?:the\s+)?|every\s+|each\s+|the\s+)" + _W + r"{0,2}(?:conditions|clauses?|specifications?|" + _DOC[3:]
    + r"(?!\s+(?:deadline|date|closing|period|programme|schedule)\b)",
    # compliance with the tender, the ITT, the specification, the contract, a code
    r"\b(?:compl(?:y|ies|ied|ying)|conform(?:s|ed|ing)?)\s+(?:fully\s+|in\s+full\s+)?(?:with|to)\s+" + _DOC_NP,
    r"\bcompliant\s+(?:with|to)\s+" + _DOC_NP,
    r"\b(?:in|confirms?|confirmed|achieves?|achieved|demonstrates?|demonstrated)\s+(?:full\s+|complete\s+)?compliance\s+with\s+" + _DOC_NP,
    r"\b(?:fully|wholly|entirely)\s+compl(?:y|ies|ied)\b",
    r"\b(?:is|are|was|were|been)\s+(?:(?:fully|entirely|now)\s+)?(?:[\w-]+\s+)?(?:in\s+line|in\s+accordance|consistent)\s+with\s+"
    + _DOC_NP,
    r"\b(?:fully|wholly|entirely|100\s*%)\s+compliant\b(?!\s+(?:with|to)\b)",
    r"(?:\b(?:is|are)|['’](?:s|re))\s+(?:now\s+)?compliant\b(?!\s+(?:with|to)\b)",
    # approval
    r"\bapproved\s+for\s+" + _FOR,
    r"(?:\b(?:is|are|was|were|has\s+been|have\s+been|had\s+been|been|got|gets)|['’](?:s|re))\s+"
    r"(?:(?:now|already|fully|formally|officially)\s+)?(?:approved|accepted|signed\s+off)\b(?!\s+for\s+" + _FOR + r")",
    r"\bapproval\s+(?:is\s+|was\s+|has\s+been\s+)?(?:granted|given|obtained)\b",
    r"\b(?:pass(?:es|ed)?|has\s+passed|have\s+passed)\s+(?:the\s+|all\s+(?:the\s+)?|its\s+|every\s+)?" + _W
    + r"{0,1}?(?:review|inspection|assessment|evaluation|audit|vetting|approval|checks|compliance\s+checks?)\b",
    # readiness and permission: submit, book, ship, bid, start
    r"\b(?:ready|cleared|good|ok|okay|safe)\s+(?:to|for)\s+(?:be\s+)?(?:submit(?:ted)?|submission|book(?:ed)?|booking|ship(?:ped)?|"
    r"shipment|shipping|dispatch(?:ed)?|bid|bidding|tender(?:ing)?|construction|installation|fabrication|commence)\b",
    r"\bgood\s+to\s+go\b",
    r"\ball\s+set\s+(?:to|for)\s+(?:submit|submission|book|booking|ship|shipment|bid)\b",
    r"\b(?:can|may)\s+(?:(?:now|safely|already|immediately|therefore|then|also)\s+)?(?:go\s+ahead\s+and\s+)?(?:be\s+)?"
    r"(?:book(?:ed)?|ship(?:ped)?|submit(?:ted)?|bid|tender(?:ed)?|dispatch(?:ed)?|commenced?|"
    r"start\s+(?:work|works|installation|construction|shipping|fabrication)|"
    r"proceed\s+(?:with|to)\s+(?:the\s+)?(?:booking|shipment|shipping|submission|bid|construction|installation|works?|book|ship|submit)|"
    r"go\s+ahead\s+(?:with|and)\s+(?:the\s+)?(?:booking|shipment|submission|book|ship|submit|bid))\b",
    r"\bgo\s+ahead\s+(?:and|with|to)\s+(?:the\s+)?(?:book(?:ing)?|ship(?:ping|ment)?|submit|submission|bid|tender)\b",
    r"\b(?:booking|shipment|shipping|submission|dispatch|bidding|construction|installation)\s+(?:can|may)\s+(?:now\s+)?"
    r"(?:go\s+ahead|proceed|start|begin|commence)\b",
    # the bid itself
    r"\bno\s+(?:risk|chance|danger|possibility)\s+of\s+(?:disqualification|rejection|being\s+(?:disqualified|rejected))\b",
    r"\b(?:substantially|fully)\s+responsive\b",
    r"\bwill\s+(?:win|be\s+awarded)\s+(?:the\s+)?(?:tender|bid|contract|job|project)\b",
)


def _compile(parts) -> re.Pattern:
    return re.compile("|".join(sorted(parts, key=len, reverse=True)), re.I)


_BASE = tuple(re.escape(p) for p in (*ASSERTIVE, *_FORBIDDEN))
_VERDICT_LITERAL = _compile((*_BASE, *_LITERAL))
_VERDICT = _compile((*_BASE, *_PATTERNS))
_VERDICT_ZH = _compile((*_BASE, *_PATTERNS_ZH))
_VERDICT_EN = re.compile("|".join(_EN_PATTERNS), re.I)       # order kept: the first alternative that fits at a position wins
_CLAUSE_BREAK = re.compile(r"[，,。；;：:！!？?\n]")
_NEGATION = re.compile(r"不|非|未|无法|禁止|勿|别|没有?(?!废标)|是否|能否|能不能|可否|算不算|\b(?:not|no|never|cannot|whether|if)\b|n't", re.I)
# 同一分句里、结论之前出现这些，说的是条件或要求，不是结论。
_CONDITION_BEFORE = re.compile(r"才|方可|方能|如果|倘若|若|一旦|(?<![接对招期等])待(?!遇)|须|必须|应当|应(?![答对用力])|需(?!要说明)|确保|保证(?!金)|要求|\b(?:must|shall|should|once|after|when|unless)\b", re.I)
# 结论后面紧跟这些，它是别的动作的前提（验收合格后方可……）。
_CONDITION_AFTER = re.compile(r"^(?:之?后|以后|之?前|以前|时|的话|的前提|的条件|者|与否|才)")
_QUESTION_TAIL = re.compile(r"^[^，,。；;！!\n]{0,12}(?:吗|么|呢)?\s*[？?]|^\s*(?:吗|么)")

# English checks. A clause ends at , ; : ! ? a full stop, a line break, or a joining and / but / so / while.
_EN_CLAUSE_BREAK = re.compile(r"[，,。；;：:！!？?\n]|\.(?=\s|$)|\b(?:and|but|so|while|whereas)\b", re.I)
# Before a verdict a spaced dash or an em dash also ends a clause: "No gaps remain - the bid is ready to submit" states
# it (the "no" belongs to the other clause). After a verdict the dash is not a break, so "ready to submit - once the
# bond is attached" still reads as a condition. (Reviewer probe 2026-09-27; see test/benchmarks/verdicts/README.md.)
_EN_CLAUSE_BREAK_BEFORE = re.compile(_EN_CLAUSE_BREAK.pattern + r"|\s[-–—]\s|—", re.I)
_EN_SENTENCE_BREAK = re.compile(r"[.!?](?=\s|$)|[;\n。！？；]")
_EN_SENTENCE_END = re.compile(r"[.!?](?=\s|$)|[\n。！？]")
_EN_NEGATION = re.compile(r"\b(?:not|no|never|none|nothing|neither|nor|cannot|without|unable|nobody|whether|if|hardly|barely)\b|n['’]t", re.I)
_EN_CONDITION_BEFORE = re.compile(
    r"\b(?:must|shall|should|once|after|when|whenever|unless|until|before|would|could|might|provided|providing|assuming|"
    r"subject\s+to|as\s+soon\s+as|need(?:s|ed)?\s+to|has\s+to|have\s+to|had\s+to|required|requires?|expected|"
    r"in\s+order\s+to|so\s+that|want(?:s|ed)?\s+to|intend(?:s|ed)?\s+to)\b", re.I)
_EN_CONDITION_AFTER = re.compile(r"\b(?:once|after|when|whenever|if|unless|until|provided|providing|subject\s+to|as\s+soon\s+as|"
                                 r"pending|following|on\s+condition|assuming)\b", re.I)
_EN_CONDITION_LEAD = re.compile(r"^\s*(?:if|once|when|whenever|unless|after|provided|providing|as\s+soon\s+as|until|before|"
                                r"in\s+case|assuming|on\s+condition|subject\s+to|pending|only\s+(?:if|when|after|once))\b", re.I)
_EN_REPORTED = re.compile(
    r"\b(?:says?|said|saying|states?|stated|stating|claims?|claimed|claiming|asks?|asked|asking|writes?|wrote|written|"
    r"reported|requests?|requested|alleges?|alleged|told|tells|instructs?|instructed|"
    r"according\s+to|mentions?|mentioned|labell?ed|titled|asserts?|asserted|insists?|insisted|suggests?|suggested)\b",
    re.I)
# A reporting word in the product's own voice reports nobody else: "as mentioned", "as written above", "I told you",
# "we have stated". Such a sentence is not reported speech. ("note to" is no longer a reporting word: "Note to the
# user: the bid is ready to submit" states the verdict.)
_EN_OWN_VOICE = re.compile(r"(?:^|\b)(?:as|i|we)\s+(?:(?:have|had|already|just|previously|also|would|could|might|must|can|"
                           r"will|do)\s+)?$|(?:^|\b)(?:i|we)['’]d\s+$"
                           # "it is fair / safe to say", "needless to say" (review of #74, dev_round3_review.json)
                           r"|\b(?:safe|fair|true|accurate|correct|reasonable|needless)\s+to\s+$|\bsuffice\s+(?:it\s+)?to\s+$", re.I)

# Round 3 (review of 2026-09-27, dev_round3.json). Three ways a stated verdict slipped past the checks above:
# (c) a leading "with no ... / having found no ... / without ..." adverbial carries a negation that belongs to the
#     adverbial, not to the verdict: "With no gaps the containers can be booked now". When the adverbial is a short
#     phrase that ends where the verdict's subject (the, this, it, we, all ...) begins, only the rest of the clause is
#     searched for a negation. "Without the plan being compliant ..." has no phrase before its "the", so it still negates.
_EN_SUBJECT_START = r"(?:the|this|these|those|your|our|its|their|it|we|you|they|all|every|each|everything)\b"
#     Review of #74 (dev_round3_review.json): the phrase is looked for from the start of the sentence, so "With zero gaps
#     and no open items the bid ..." (the clause splits at "and") and a leading reason ("Since there are no gaps the
#     containers ...") count too. whether / if inside it are not its own ("Without checking whether the plan is
#     compliant ..." asks, it does not state), and a phrase ending in a word that takes the verdict as its object
#     ("With nothing showing the plan is compliant ...", "no evidence that ...") negates the verdict.
_EN_NEG_ADVERBIAL = re.compile(
    r"^\s*(?:with\s+(?:no|nothing|zero)|having\s+(?:found|seen|had|identified|raised|flagged|noted)\s+(?:no|nothing)|"
    r"(?:finding|seeing)\s+(?:no|nothing)|without(?:\s+any)?|"
    r"(?:since|as|because|given\s+that|now\s+that|seeing\s+that)\s+(?:[\w'’-]+\s+){0,3}?(?:no|nothing|zero|none))\s+"
    r"(?:(?:(?:and|or|nor)\s+|(?<=,\s))(?:no|zero|nothing|without(?:\s+any)?)\s+|"
    r"(?!" + _EN_SUBJECT_START + r"|not\b|no\b|never\b|whether\b|if\b)[\w'’-]+,?\s+){1,6}?(?=" + _EN_SUBJECT_START + r")", re.I)
_EN_ADVERBIAL_GOVERNS = re.compile(
    r"\b(?:show\w*|prov(?:e|es|ed|en|ing)|proof|suggest\w*|indicat\w*|confirm\w*|say\w*|said|tell\w*|told|mean\w*|"
    r"impl(?:y|ies|ied|ying)|guarantee\w*|establish\w*|demonstrat\w*|evidence|signs?|reason|basis|way|that|to\s+[\w'’-]+)\s+$",
    re.I)
# "Not only is the plan compliant with the tender" affirms the verdict: "not only / not just" is not a negation
_EN_NOT_ONLY = re.compile(r"\bnot\s+(?:only|just|merely|simply)\b|不(?:仅|但|光|只)", re.I)
# (d) after / when open a condition only when a subordinate clause follows them: a subject and a verb ("after the
#     engineer signs", "when it is confirmed") or a participle done by someone ("when signed by the PE"). "After review
#     the bid is ready" and "When checked against the ITT the plan meets ..." report a check already made.
_EN_FINITE = r"(?:is|are|was|were|has|have|had|will|can|may|shall|must|does|do|did|[a-z]+(?:s|ed))\b"
_EN_SUBORDINATE = re.compile(
    r"^\s*(?:(?:the|a|an|this|that|these|those|your|our|its|their|his|her|my)\s+(?:(?!(?:the|a|an|this|that)\b)[\w'’-]+\s+){1,3}?"
    # "After the detailed checks the bid ...": a plural check noun is not the verb (review of #74)
    + r"(?!(?:checks|cross-checks|reviews|process|processes|results|findings|comparisons|assessments|inspections|tests|"
      r"analysis|analyses|audits|evaluations|runs|passes|calculations|verifications)\b)" + _EN_FINITE
    + r"|(?:i|we|you|they|he|she|it|someone|somebody|anyone|everyone)\s+(?:\w+\s+)?" + _EN_FINITE
    + r"|[a-z]+(?:ed|en)\s+(?:[\w'’-]+\s+){0,2}?by\b"
    # a participle or state someone else still has to reach ("when confirmed", "after signing", "when ready"); the
    # product's own check ("when checked against the ITT", "after review") is not one
    + r"|(?!(?:checked|cross-checked|reviewed|compared|assessed|verified|tested|matched|analy[sz]ed|evaluated|examined|"
      r"inspected|linked|read|run|scored|seven|eleven|ten|even|often|then|open|hundred)\b)[a-z]+(?:ed|en)\b"
      r"|(?:signing|confirming|approving|ready|complete|available|received|finished|done)\b)", re.I)
#     would / should / could / might followed by an opinion verb ("I would say the plan meets ...") put the verdict in
#     the product's own voice; the modal governs "say", not the verdict.
_EN_OPINION_MODAL = re.compile(r"\b(?:would|should|could|might)\s+(?:(?:just|still|also|honestly|probably)\s+)?"
                               r"(?:be\s+(?:[\w'’-]+\s+)?(?:safe|fair|true|accurate|correct|reasonable)\s+to\s+)?"
                               r"(?:say|think|argue|conclude|reckon|guess|consider|add|note|state)\b", re.I)
#     "you should go ahead and book" is advice to act on the verdict, not a requirement on the thing ("The response
#     should be ready for submission by Friday" stays a requirement).
_EN_ADVICE = re.compile(r"\b(?:you|we|i)\s+(?:(?:really|now|just|[a-z]+ly)\s+)?(?:should|could|ought\s+to)\s+"
                        r"(?:(?:now|just|simply|safely|really|[a-z]+ly)\s+){0,2}$", re.I)
_EN_ACT = re.compile(r"(?:go\s+ahead|proceed)\b", re.I)
# the Chinese condition words alone: in an English clause its English words are _en_condition_before's to judge
_CONDITION_BEFORE_CJK = re.compile(_CONDITION_BEFORE.pattern.replace(r"|\b(?:must|shall|should|once|after|when|unless)\b", ""))


def _en_condition_before(clause: str, verdict: str) -> bool:
    """A word in the clause before an English verdict makes it a condition or a requirement (round-3 exceptions above).
    must / shall / once / unless ... always do; after / when only with a subordinate clause; would / should / could /
    might not when they govern an opinion verb, nor should / could in "you should go ahead and ..."."""
    for m in _EN_CONDITION_BEFORE.finditer(clause):
        word = m.group(0).lower()
        if word in ("after", "when"):
            if _EN_SUBORDINATE.match(clause[m.end():] + verdict):
                return True
            continue
        if word in ("would", "should", "could", "might"):
            if _EN_OPINION_MODAL.match(clause, m.start()):
                continue
            if word in ("should", "could") and _EN_ADVICE.search(clause) and _EN_ACT.match(verdict):
                continue
        return True
    return False


def _en_condition_lead(sentence: str) -> bool:
    """The sentence opens with a condition (If / Once / Unless ...); After / When only with a subordinate clause."""
    m = _EN_CONDITION_LEAD.search(sentence)
    if not m:
        return False
    if m.group(0).strip().lower() in ("after", "when"):
        return bool(_EN_SUBORDINATE.match(sentence[m.end():]))
    return True


def _reports(sentence: str) -> bool:
    """The sentence reports someone else's words: a reporting word not said in the product's own voice."""
    return any(not _EN_OWN_VOICE.search(sentence[: m.start()]) for m in _EN_REPORTED.finditer(sentence))


def _last_break(pattern: re.Pattern, blob: str, end: int) -> int:
    """End of the last break before ``end``. Every break pattern here counts a line break, so the scan starts at the
    last line break: the same answer as scanning from 0, without going over the whole text for every match (a
    216k-character draft with 2 000 verdicts took 9 s that way)."""
    start = blob.rfind("\n", 0, end)
    last = start + 1 if start >= 0 else 0
    for m in pattern.finditer(blob, max(start, 0), end):
        last = m.end()
    return last


def _quoted(blob: str, start: int) -> bool:
    """Inside quotation marks: an odd number of " before it on its line, or an opening mark not yet closed."""
    line = blob[blob.rfind("\n", 0, start) + 1: start]
    return (line.count('"') % 2 == 1 or line.rfind("“") > line.rfind("”") or line.rfind("「") > line.rfind("」")
            or line.rfind("『") > line.rfind("』") or line.rfind("‘") > line.rfind("’"))


def _english_stated(blob: str, match: re.Match, *, negation: bool, conditions: bool, questions: bool, reported: bool,
                    quotes: bool) -> bool:
    clause = blob[_last_break(_EN_CLAUSE_BREAK_BEFORE, blob, match.start()): match.start()]
    sentence = blob[_last_break(_EN_SENTENCE_BREAK, blob, match.start()): match.start()]
    rest = _EN_CLAUSE_BREAK.search(blob, match.end())
    after = blob[match.end(): rest.start() if rest else len(blob)]
    negatable = clause
    adverbial = _EN_NEG_ADVERBIAL.match(sentence)
    if adverbial and not _EN_ADVERBIAL_GOVERNS.search(sentence[: adverbial.end()]):
        cut = match.start() - len(sentence) + adverbial.end()
        if cut > match.start() - len(clause):
            negatable = blob[cut: match.start()]
    negatable = _EN_NOT_ONLY.sub(" ", negatable)
    if negation and (_NEGATION.search(negatable) or _EN_NEGATION.search(negatable)):
        return False
    if conditions and (_CONDITION_BEFORE_CJK.search(clause) or _en_condition_before(clause, match.group(0))
                       or _EN_CONDITION_AFTER.search(after)
                       or (len(sentence) > len(clause) and _en_condition_lead(sentence))):
        return False
    if questions:
        end = _EN_SENTENCE_END.search(blob, match.end())
        if end and end.group(0) in "?？":
            return False
    if reported and _reports(sentence):
        return False
    if quotes and _quoted(blob, match.start()):
        return False
    return True


#: zero-width and invisible format characters. "ready to sub\u200bmit" and "compli\u00adant" read as the words they
#: hide (review of #74, dev_round3_review.json)
INVISIBLE = re.compile("[\u00ad\u180e\u200b-\u200f\u202a-\u202e\u2060-\u2064\ufeff]")


def visible_variants(text: str):
    """(variant, positions) pairs for text that carries invisible characters: each one read as a space (same length,
    positions 1:1) and each one dropped (``positions[i]`` is the index in ``text`` of the variant's character i)."""
    spaced = INVISIBLE.sub(" ", text)
    where = [i for i, ch in enumerate(text) if not INVISIBLE.match(ch)]
    return ((spaced, None), ("".join(text[i] for i in where), where))


def stated_verdicts(text: str, **flags: bool) -> List[Dict[str, Any]]:
    """Verdicts stated in ``text`` (see _stated_verdicts for the flags); invisible characters do not hide one."""
    blob = text or ""
    if not INVISIBLE.search(blob):
        return _stated_verdicts(blob, **flags)
    found: List[Dict[str, Any]] = []
    for variant, where in visible_variants(blob):
        for item in _stated_verdicts(variant, **flags):
            start, end = item["start"], item["end"]
            if where is not None:
                start, end = where[start], where[end - 1] + 1
            if not any(f["start"] < end and start < f["end"] for f in found):
                found.append(dict(item, start=start, end=end))
    return sorted(found, key=lambda item: item["start"])


def _stated_verdicts(text: str, *, negation: bool = True, clause_scope: bool = True, questions: bool = True,
                     conditions: bool = True, patterns: bool = True, english: bool = True, reported: bool = True,
                     quotes: bool = True) -> List[Dict[str, Any]]:
    blob = text or ""
    found: List[Dict[str, Any]] = []
    use_english = english and patterns
    for match in (_VERDICT_ZH if use_english else _VERDICT if patterns else _VERDICT_LITERAL).finditer(blob):
        if clause_scope:
            breaks = [m.end() for m in _CLAUSE_BREAK.finditer(blob, 0, match.start())]
            before = blob[breaks[-1] if breaks else 0: match.start()]
        else:
            before = blob[max(0, match.start() - 8): match.start()]
        after = blob[match.end(): match.end() + 16]
        if negation and _NEGATION.search(before):
            continue
        if conditions and (_CONDITION_BEFORE.search(before) or _CONDITION_AFTER.search(after)):
            continue
        if questions and _QUESTION_TAIL.search(after):
            continue
        found.append({"kind": "verdict", "text": match.group(0), "start": match.start(), "end": match.end()})
    if use_english:
        for match in _VERDICT_EN.finditer(blob):
            if any(item["start"] < match.end() and match.start() < item["end"] for item in found):
                continue
            if _english_stated(blob, match, negation=negation, conditions=conditions, questions=questions,
                               reported=reported, quotes=quotes):
                found.append({"kind": "verdict", "text": match.group(0), "start": match.start(), "end": match.end(),
                              "lang": "en"})
        found.sort(key=lambda item: item["start"])
    return found


def notice(found: List[Dict[str, Any]], *, english: bool = False) -> str:
    if not found:
        return ""
    zh = list(dict.fromkeys(str(item["text"]) for item in found if item.get("lang") != "en" and not english))
    en = list(dict.fromkeys(str(item["text"]) for item in found if item.get("lang") == "en" or english))
    lines = []
    if zh:
        lines.append(f"⚠ 以下结论不由本系统下，已从回复里去掉，请由有资格的人判断：{'、'.join(zh)}")
    if en:
        lines.append("⚠ These verdicts are not this system's to give; they were taken out of the reply, and a qualified "
                     "person decides: " + ", ".join(en))
    return "\n".join(lines)


#: an English verdict, or any verdict in an English turn, is replaced by this
EN_STRUCK = "(verdict removed: not this system's call)"


def strike(text: str, found: List[Dict[str, Any]], *, english: bool = False) -> str:
    """The text with each stated verdict replaced, right to left so positions stay valid."""
    out = text
    for item in sorted(found, key=lambda entry: entry["start"], reverse=True):
        mark = EN_STRUCK if english or item.get("lang") == "en" else "（此处结论不由本系统判定）"
        out = out[: item["start"]] + mark + out[item["end"]:]
    return out


_SHIPPED = dict(negation=True, clause_scope=True, questions=True, conditions=True, patterns=True, english=True,
                reported=True, quotes=True)
ABLATIONS: Dict[str, Dict[str, bool]] = {
    "literal phrases, no condition check (v1)": dict(_SHIPPED, patterns=False, conditions=False, english=False),
    "+ sentence patterns": dict(_SHIPPED, conditions=False, english=False),
    "+ condition check (before English)": dict(_SHIPPED, english=False),
    "+ English family (shipped)": dict(_SHIPPED),
    "ablate: 8-char window, not the clause": dict(_SHIPPED, clause_scope=False),
    "ablate: no negation check": dict(_SHIPPED, negation=False),
    "ablate: no question check": dict(_SHIPPED, questions=False),
    "ablate: no condition check": dict(_SHIPPED, conditions=False),
    "ablate: English, no reported check": dict(_SHIPPED, reported=False),
    "ablate: English, no quotation check": dict(_SHIPPED, quotes=False),
}
ABLATIONS["shipped"] = dict(_SHIPPED)
