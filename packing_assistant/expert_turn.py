"""Every summoned expert uses the same understand → chat | run | both loop.

Writes only exclusive tools (or HITL pending). No 66 personality prompts.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Optional
from uuid import uuid4

import subprocess
import sys

from packing_assistant.expert_roster import ExpertRec, exclusive_tools, get_expert, list_experts
from packing_assistant.sandbox import guarded_write_text
from packing_assistant.understand import understand

_ROOT = Path(__file__).resolve().parents[1]
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
DISCLAIMER = (
    "本文件由 Civil Buddy 根据用户输入生成，仅供内部讨论与起草。"
    "不构成设计文件、法定专项施工方案、交底签认件、监理指令、专家论证材料或开工/竣工验收依据。"
)
from packing_assistant.runtime.civil_config import CONFIRM, CONFIRM_EN  # noqa: E402,F401  (one definition)
FORBIDDEN = ("可以投标", "可以开工", "中标率")


def _kb_snip(expert: ExpertRec, query: str, limit: int = 900) -> str:
    paths = [
        _ROOT / "demo" / "kb" / expert.category / expert.id / "web-knowledge.md",
        _ROOT / "demo" / "kb" / expert.category / "_shared" / "web-knowledge.md",
    ]
    if expert.category == "plugin":
        from packing_assistant.runtime import plugins

        item = plugins.skill(expert.id)
        paths = [item.knowledge] if item and item.knowledge else []
    chunks: List[str] = []
    q = (query or "").strip()
    for p in paths:
        if not p.is_file():
            continue
        text = p.read_text(encoding="utf-8", errors="ignore")
        if q and q in text:
            i = text.find(q)
            chunks.append(text[max(0, i - 80) : i + 400])
        else:
            chunks.append(text[:limit])
        if sum(len(c) for c in chunks) >= limit:
            break
    return "\n".join(chunks)[:limit].strip()


def explain_expert(expert: ExpertRec, text: str, previous_jurisdiction: str = "") -> str:
    bits = [
        f"本岗：{expert.name}（{expert.category_name} / {expert.id}）。内部讨论 AI 草稿。提问不写盘，不判定可投标。",
        expert.title,
    ]
    blob = text or ""
    tax_question = "GST" in blob.upper() or "税率" in blob or expert.id == "finance-tax"
    if tax_question:
        from packing_assistant.tax_context import explain_tax
        bits.append(explain_tax(blob, previous=previous_jurisdiction))
    if any(k in blob for k in ("危大", "临边", "专家论证")) or expert.id == "method-hazard":
        bits.append(
            "是否危大、要不要专家论证，须由持证人员按专项目录与现场判定。本岗不判定可以开工。"
        )
    # Static KB excerpts are historical reference material, not evidence of a
    # current tax rate. They cannot undo the unknown-rate boundary above.
    snip = "" if tax_question else _kb_snip(expert, blob)
    if snip:
        bits.append("本岗可见知识摘录：\n" + snip)
    bits.append("要成稿请明说写/编制/出一份。高风险写盘须确认句：「" + CONFIRM + "」。")
    reply = "\n".join(b for b in bits if b)
    for bad in FORBIDDEN:
        if bad in reply and f"不判定{bad}" not in reply and f"不{bad}" not in reply:
            reply = reply.replace(bad, "（禁止断言）")
    return reply


def _write_tools(expert: ExpertRec) -> List[str]:
    return [t for t in expert.exclusive if "fill_scheme" not in t]


_SCHEME_CHAPTERS = (
    "封面与文件控制",
    "草稿与责任声明",
    "工程概况",
    "编制依据",
    "施工部署与工艺",
    "质量",
    "安全与应急",
    "环保与文明施工",
    "资源计划",
    "验收与资料",
    "附录",
)

_SURVEY_CHAPTERS = (
    "封面与文件控制",
    "草稿与责任声明",
    "任务范围与部位",
    "已知起算",
    "控制网与加密",
    "放样内容",
    "竖向传递",
    "复测与检核",
    "仪器与人员",
    "停测与异常",
    "附录",
)

_VARIATION_CHAPTERS = (
    "封面与草稿声明",
    "文件类型判定",
    "事实栏",
    "依据栏",
    "工程量栏",
    "价款调整方法",
    "签认栏",
    "与索赔、验工的接口",
    "附件目录",
    "自检",
)

_RES_QTY = re.compile(
    r"(?P<qty>\d+(?:\.\d+)?)\s*(?P<unit>人|工日|台班|台|t|吨|kg|m3|m³)",
    re.I,
)


_SUPERVISION_CHAPTERS = (
    "文头",
    "致",
    "来文要点复述",
    "原因分析",
    "拟办",
    "完成时限",
    "证据目录",
    "自检",
    "签发",
    "闭合台账行",
    "禁令",
)

_ENV_CHAPTERS = (
    "封面",
    "声明",
    "扬尘",
    "弃土与建筑垃圾",
    "污水与泥浆",
    "噪声与夜间",
    "文明施工市容",
    "与商务接口",
    "停工与升级",
    "签字栏",
)

_EMERGENCY_CHAPTERS = (
    "封面与声明",
    "编制说明",
    "综合预案目录",
    "专项预案目录",
    "现场处置方案",
    "应急处置卡",
    "信息报告",
    "演练计划与记录表头",
    "附件",
    "备案与评估节点",
    "禁令",
)

_EMERGENCY_SPECIALS = (
    "高处坠落",
    "物体打击",
    "坍塌",
    "触电",
    "起重机械",
    "火灾爆炸",
    "中毒窒息/有限空间",
    "车辆伤害",
    "疫情或突发环境事件",
)

_EMERGENCY_HINTS = (
    ("火灾", "火灾爆炸"),
    ("fire", "火灾爆炸"),
    ("爆炸", "火灾爆炸"),
    ("坠落", "高处坠落"),
    ("高处", "高处坠落"),
    ("打击", "物体打击"),
    ("坍塌", "坍塌"),
    ("触电", "触电"),
    ("起重", "起重机械"),
    ("有限空间", "中毒窒息/有限空间"),
    ("中毒", "中毒窒息/有限空间"),
    ("车辆", "车辆伤害"),
    ("疫情", "疫情或突发环境事件"),
)


_WH_CHAPTERS = (
    "草稿声明",
    "库区与分类",
    "入库验收",
    "标识与保管",
    "限额领料出库",
    "盘点",
    "收发存表头",
    "危险品台账",
    "禁令",
)

_WH_SKIP = {
    "草稿提纲",
    "收发存",
    "收发存台账",
    "仓管",
    "待填",
}


_FB_CHAPTERS = (
    "封面",
    "草稿声明",
    "核算对象",
    "科目对照",
    "商务口径",
    "报销勾选",
    "安全生产费用",
    "对账缺口",
    "自检",
    "禁令",
)

_FB_PERIOD = re.compile(r"(20\d{2})[-./年](\d{1,2})")

_WB_CHAPTERS = (
    "今天干什么",
    "哪儿会掉、会砸、会淹",
    "三步怎么干",
    "谁喊停、找谁",
    "结束",
)

_WB_SKIP = {
    "草稿提纲",
    "班前白话",
    "班前白话稿",
    "口播",
    "待填",
}

_PD_CHAPTERS = (
    "报头",
    "天气",
    "部位",
    "形象进度",
    "出勤",
    "人机料",
    "安全质量记事",
    "明日拟安排",
)

_PD_SKIP = {
    "草稿提纲",
    "项目日报",
    "项目日报草稿",
    "日报",
    "工程日志",
    "待填",
}

_PD_WEATHER_WORDS = r"晴天|晴|多云|阴天|阴|雷阵雨|暴雨|大雨|中雨|小雨|阵雨|雨|大雪|中雪|小雪|雪|大雾|雾|台风|fine|rainy|cloudy|overcast"
_PD_WEATHER_VALUE = rf"(?:{_PD_WEATHER_WORDS})(?:转(?:{_PD_WEATHER_WORDS}))?"
_PD_WEATHER_RE = re.compile(
    rf"(?<![A-Za-z\u4e00-\u9fff])({_PD_WEATHER_VALUE})(?![A-Za-z\u4e00-\u9fff])",
    re.I,
)
_PD_WEATHER_FIELD_RE = re.compile(
    rf"(?:天气|weather|今日天气|今天天气|上午|下午)\s*[:：=]?\s*({_PD_WEATHER_VALUE})(?![A-Za-z\u4e00-\u9fff])",
    re.I,
)
_PD_LABOR_RE = re.compile(
    r"(?:(?:木工|钢筋工|砼工|混凝土工|架子工|电焊工?|普工|电工|瓦工|工人|管理人员|劳务人员|出勤|到场|实到|到岗|人数)\s*[:：=]?\s*)?\d+\s*人"
)
_PD_SITE_FIELD_RE = re.compile(r"(?:施工部位|作业部位|作业位置|部位|位置|site)\s*[:：=]\s*([^；;，,。\n]+)", re.I)
_PD_SITE_TOKEN_RE = re.compile(
    r"(?:[A-Za-z0-9一二三四五六七八九十]+(?:号楼|栋|层|区)|\d+#|地下室|楼梯间|基坑|承台|桩基|桥墩|桥台|隧道|临边|屋面|楼板|轴线)"
)
_PD_NONFACT = re.compile(r"(?:待填|未填|未提供|未知|不确定|未确定|未说明|UNSPECIFIED|例如|比如|示例|预报|预计|计划|明日|明天|安排|需填写|要填写|请填写|不要填写|不少于|至少|上限|容纳)", re.I)
_PD_NAMED_FIELDS = {
    "项目名称": "project", "工程名称": "project", "项目": "project",
    "填报日期": "date", "报告日期": "date", "日期": "date",
    "安全质量记事": "hse", "安全质量记录": "hse", "安全质量": "hse",
    "安全记事": "hse", "质量记事": "hse",
    "明日计划": "tomorrow", "明日拟安排": "tomorrow", "明日安排": "tomorrow",
    "形象进度": "progress", "施工进度": "progress", "今日进度": "progress",
    "今日完成": "progress", "完成工作": "progress", "今日工作": "progress",
    "机械材料": "resources", "机械材料记事": "resources", "人机料": "resources",
    "材料问题": "resources", "设备材料": "resources", "问题": "issues",
    "天气": "weather", "部位": "site", "施工部位": "site", "位置": "site",
    "出勤": "labor", "人数": "labor", "工人": "labor",
    "今日出勤": "labor", "当日出勤": "labor", "今日天气": "weather", "当日天气": "weather",
    "今日部位": "site", "当日部位": "site", "今日机械材料": "resources", "当日机械材料": "resources",
    "project name": "project", "project": "project", "report date": "date", "date": "date",
    "weather": "weather", "location": "site", "site": "site",
    "work completed": "progress", "completed work": "progress", "progress": "progress",
    "workers": "labor", "attendance": "labor", "labor": "labor",
    "actual workers": "labor", "today's workers": "labor", "today's weather": "weather",
    "today's location": "site", "today's materials": "resources",
    "materials and equipment": "resources", "resources": "resources", "materials": "resources",
    "issue": "issues", "issues": "issues", "safety and quality": "hse", "hse": "hse",
    "tomorrow's plan": "tomorrow", "tomorrow": "tomorrow", "next day plan": "tomorrow",
}
_PD_FIELD_RE = re.compile(
    r"(?<![A-Za-z0-9_\u4e00-\u9fff])(" + "|".join(sorted(_PD_NAMED_FIELDS, key=len, reverse=True))
    + r"|作业部位|作业位置|辖区)\s*[:：=]\s*", re.I,
)

_HR_CHAPTERS = ("职责", "任职", "面试问法")

_HR_SKIP = {
    "草稿提纲",
    "招聘简报",
    "招聘",
    "岗位说明书",
    "面试提纲",
    "待填",
}

_HR_PAY_RE = re.compile(
    r"(?:薪资|月薪|工资|salary|SGD|sgd)\s*[:：]?\s*(\d{3,6})"
    r"|(\d{3,6})\s*(?:元|SGD|sgd)",
    re.I,
)

_MM_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(mm|毫米)", re.I)


_QTY_RE = re.compile(
    r"(?P<qty>\d+(?:\.\d+)?)\s*(?P<unit>m2|m²|m3|t|吨|kg|工日|项)?",
    re.I,
)

_VAR_NO_RE = re.compile(r"(?i)\b(?:VO|SI|DC|VAR)[-_./]?\d+[A-Za-z]?\b")

_POINT_RE = re.compile(r"(?i)\b(?:CP|BM|PT|TP|GC|SP)[-_]?\d+[A-Za-z]?\b")
_SENSITIVE_KEYS = (
    "危大",
    "临边",
    "基坑",
    "开挖",
    "起重",
    "脚手架",
    "模板",
    "有限空间",
    "拆除",
    "爆破",
    "高处",
    "PTW",
    "excavation",
    "lifting",
    "scaffold",
)


def _copy_survey_points(blob: str) -> List[str]:
    rows: List[str] = []
    for line in (blob or "").splitlines():
        t = line.strip()
        if not t or "用户未提供" in t:
            continue
        if "点号" in t or "控制点" in t or _POINT_RE.search(t):
            rows.append(t[:200])
    return rows


def _copy_sensitive_jobs(blob: str) -> List[str]:
    hits: List[str] = []
    for line in (blob or "").replace("；", "\n").replace(";", "\n").splitlines():
        t = line.strip()
        if not t:
            continue
        if any(k.lower() in t.lower() for k in _SENSITIVE_KEYS):
            hits.append(t[:120])
    return hits


def _survey_record_md(text: str) -> str:
    from packing_assistant.post_drafts.site_records import build_draft
    return build_draft("survey", "survey__record", text)


def _classify_variation_kind(blob: str) -> str:
    t = blob or ""
    low = t.lower()
    hits: List[str] = []
    if "设计变更" in t or "design change" in low:
        hits.append("设计变更")
    if "工程签证" in t or "签证" in t or "variation" in low:
        hits.append("工程签证")
    if "洽商" in t:
        hits.append("工程洽商")
    if "联系单" in t:
        hits.append("工程联系单")
    if "工程量确认" in t or "qty confirm" in low:
        hits.append("工程量确认单")
    uniq = list(dict.fromkeys(hits))
    if len(uniq) > 1:
        return "混写，须拆开。本表不混写，待用户指定一类。"
    if len(uniq) == 1:
        return uniq[0]
    return "信息不足，待用户指定一类（设计变更 / 工程签证 / 工程洽商 / 工程联系单 / 工程量确认单）。"


def _copy_variation_no(blob: str) -> str:
    rows: List[str] = []
    for line in (blob or "").splitlines():
        t = line.strip()
        if not t:
            continue
        if "变更编号" in t or _VAR_NO_RE.search(t):
            rows.append(t[:160])
    if rows:
        return "\n".join(f"- {r}" for r in rows)
    return "变更编号待填。禁止引用未提供的图号。条款号 UNSPECIFIED。"


def _variation_form_md(text: str) -> str:
    from packing_assistant.post_drafts.business_observations import build_draft
    return build_draft("variation", "variation__form", text)


def _claim_notice_md(text: str) -> str:
    from packing_assistant.post_drafts.claim import build_draft
    return build_draft('claim', 'claim__notice', text)


def _subcontract_sheet_md(text: str) -> str:
    from packing_assistant.post_drafts.subcontract import build_draft
    return build_draft('subcontract', 'subcontract__sheet', text)


def _interim_measure_md(text: str) -> str:
    from packing_assistant.post_drafts.interim import build_draft

    return build_draft('interim', 'interim__measure', text)


def _plan_master_md(text: str) -> str:
    from packing_assistant.post_drafts.planning import build_draft

    return build_draft("plan-master", "plan-master__network", text)


def _plan_lookahead_md(text: str) -> str:
    from packing_assistant.post_drafts.planning import build_draft

    return build_draft("plan-lookahead", "plan-lookahead__week", text)


def _plan_resource_md(text: str) -> str:
    from packing_assistant.post_drafts.planning import build_draft

    return build_draft("plan-resource", "plan-resource__peak", text)




def _mix_zone(blob: str) -> str:
    from packing_assistant.jurisdiction import infer_jurisdiction

    return infer_jurisdiction(blob or "")


def _lab_mix_md(text: str) -> str:
    from packing_assistant.post_drafts.lab import build_draft

    return build_draft("lab-mix", "lab-mix__report", text)


def _lab_sample_md(text: str) -> str:
    from packing_assistant.post_drafts.lab import build_draft

    return build_draft("lab-sample", "lab-sample__list", text)


def _lab_record_md(text: str) -> str:
    from packing_assistant.post_drafts.lab import build_draft

    return build_draft("lab-record", "lab-record__ledger", text)


def _supervision_md(text: str) -> str:
    from packing_assistant.post_drafts.site_records import build_draft
    return build_draft("supervision", "supervision__reply", text)


def _safety_brief_md(text: str) -> str:
    from packing_assistant.post_drafts.safety_brief import build_draft
    return build_draft('safety-brief', 'safety-brief__talk', text)


def _quality_md(text: str) -> str:
    from packing_assistant.post_drafts.quality import build_draft
    return build_draft('quality', 'quality__lot', text)


def _env_md(text: str) -> str:
    from packing_assistant.post_drafts.site_records import build_draft
    return build_draft("env", "env__list", text)


def _named_emergency_specials(blob: str) -> List[str]:
    t = (blob or "").lower()
    raw = blob or ""
    named: List[str] = []
    for hint, spec in _EMERGENCY_HINTS:
        if hint.lower() in t or hint in raw:
            if spec not in named:
                named.append(spec)
    return named


def _emergency_md(text: str) -> str:
    from packing_assistant.post_drafts.site_briefs import build_draft
    return build_draft('emergency', 'emergency__plan', text)


def _equip_md(text: str) -> str:
    from packing_assistant.post_drafts.equipment import build_draft

    return build_draft("equip", "equip__ledger", text)


_WH_COLUMNS = {
    "inbound": ("入库", "进场", "到货", "进货", "收料"),
    "outbound": ("出库", "领用", "领料", "发料", "领走", "发出"),
    "returned": ("退库", "退料", "退回"),
    "opening": ("期初结存", "上期结存", "期初"),
    "balance": ("账面结存", "结存", "库存"),
    "variance": ("盘点差异", "盘点差", "盘亏", "盘盈", "差异"),
    "counted": ("盘点实存", "实盘", "实存", "盘点"),
    "price": ("单价",),
}
_WH_LABELS = {
    "doc": ("来源单据号", "入库单号", "送货单号", "领料单号", "出库单号", "单据号", "单号"),
    "batch": ("炉批号", "批次号", "批号", "批次"),
    "supplier": ("供应商", "供货单位", "供货商", "厂家"),
    "location": ("库位", "库区", "仓库", "堆放位置", "存放位置"),
}
_WH_KEEPERS = ("仓库管理员", "仓管员", "保管员", "库管员", "材料员", "库管", "仓管", "经办人", "验收人", "盘点人", "经办")


def _parse_wh_rows(blob: str) -> List[Dict[str, str]]:
    """One row per material the user named, each figure from its own clause; see post_facts.object_rows."""
    from packing_assistant import post_facts

    # The UI's wrapper and the user's instructions are not objects in a stock ledger.
    pieces = []
    for piece in re.split(r"[。\n]", blob or ""):
        piece = piece.strip()
        if not piece or re.fullmatch(r"【[^】]*】", piece):
            continue
        piece = re.sub(r"^SYNTHETIC\b[\s:：-]*", "", piece, flags=re.I)
        # A missing opening balance does not negate a separate, positive receipt.
        # Remove only the explicit preface, never the material clauses after it.
        piece = re.sub(r"^(?:没有|未提供|未知)(?:期初库存|期初结存|期初余额)[，,；;]\s*", "", piece)
        if re.match(r"(?:没有|未提供|不要|不得|不推算|未知|其余)", piece):
            continue
        pieces.append(post_facts.strip_command(piece))
    material = "\n".join(pieces)
    aliases = {
        **_WH_LABELS, "name": ("材料名称", "物资名称", "品名", "材料", "物资"),
        "spec": ("规格型号", "规格", "型号"), "quantity": ("所述数量", "数量"),
        "unit": ("计量单位", "单位"),
        **{key: tuple(label + "数量" for label in labels) + tuple(labels)
           for key, labels in _WH_COLUMNS.items()},
    }
    record_starts = list(re.finditer(r"(?:^|[\n；;])\s*(?:材料名称|物资名称|品名|材料|物资)\s*[:：]", material))
    if record_starts:
        rows = []
        for index, start in enumerate(record_starts):
            stop = record_starts[index + 1].start() if index + 1 < len(record_starts) else len(material)
            source = material[start.start():stop].strip()
            values = post_facts.labelled(source, aliases)
            if not values.get("name"):
                continue
            row = {"name": values.pop("name"), "source": source}
            for key, value in values.items():
                if not re.fullmatch(r"待填|未知|未提供|TBD|UNSPECIFIED", value, re.I):
                    row[key] = value
            rows.append(row)
    else:
        rows = post_facts.object_rows(material, _WH_COLUMNS, drop=("台账", "收发存"))
    # Only an explicitly requested one-way ledger supplies the movement context.
    # A generic stock ledger or a mixed inbound/outbound request does not.
    requested = re.search(r"(?:生成|制作|整理|编制|写)\s*(?:一份)?[^。\n；;]{0,12}?(入库|出库)台账", blob or "")
    for row in rows:
        if requested and row.get("quantity") and not any(row.get(k) for k in ("inbound", "outbound")):
            row["inbound" if requested.group(1) == "入库" else "outbound"] = row.pop("quantity")
        found = post_facts.labelled(row["source"], _WH_LABELS)
        row.update({key: value for key, value in found.items() if key not in row})
        # A thickness is a specification, not the unit of an inbound or issued quantity.
        dimensions = list(re.finditer(r"(?<![\w.])\d+(?:\.\d+)?\s*(?:mm|cm)\b", row["name"], re.I))
        if dimensions:
            row["spec"] = " ".join(filter(None, [row.get("spec"), *(m.group() for m in dimensions)]))
            row["name"] = re.sub(r"(?<![\w.])\d+(?:\.\d+)?\s*(?:mm|cm)\b", "", row["name"], flags=re.I).strip()
        units = list(dict.fromkeys(
            q.unit for key in (*_WH_COLUMNS, "quantity") if key != "price"
            for q in post_facts.quantities(row.get(key, "")) if q.unit
        ))
        if "unit" not in row and len(units) == 1:
            row["unit"] = units[0]
        elif len(units) > 1:
            row["unit"] = "待核：" + " / ".join(units)
    return rows


def _warehouse_md(text: str) -> str:
    from packing_assistant import post_facts

    blob = text or ""
    zone = _mix_zone(blob)
    rows = _parse_wh_rows(blob)
    header = post_facts.labelled(blob, {"project": ("项目名称", "项目", "工程名称"),
                                       "date": ("入库日期", "出库日期", "记账日期")})
    keeper = post_facts.person_for(blob, _WH_KEEPERS)
    period = next((row["period"] for row in rows if row.get("period")), "")
    has_count = any(k in blob for k in ("盘点", "实存", "实盘"))
    if not rows:
        short = (
            "| 物资 | 入库 | 出库 | 结存 | 备注 |\n"
            "| --- | --- | --- | --- | --- |\n"
            "| 待填物资 | TBD | TBD | TBD | 待填 |\n"
        )
        full = (
            "| 物资 | 规格批次 | 单位 | 期初 | 入库 | 出库 | 账面结存 | 盘点实存 | 差异 | 来源单据号 | 单价 | 原文数量（未指明收发） |\n"
            "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |\n"
            "| 待填物资 | 待填 | 待填 | TBD | TBD | TBD | TBD | TBD | TBD | 待填 | TBD | TBD |\n"
        )
    else:
        def cell(row: Dict[str, str], key: str, missing: str = "TBD") -> str:
            return post_facts.table_cell(row.get(key) or missing)

        short = (
            "| 物资 | 入库 | 出库 | 结存 | 备注 |\n"
            "| --- | --- | --- | --- | --- |\n"
            + "".join(
                f"| {post_facts.table_cell(' '.join(filter(None, (row['name'], row.get('spec')))))} | {cell(row, 'inbound')} | "
                f"{cell(row, 'outbound')} | {cell(row, 'balance')} | "
                f"{post_facts.table_cell('；'.join(filter(None, (row.get('location'), row.get('supplier')))) or '待填')} |\n"
                for row in rows
            )
        )
        full = (
            "| 物资 | 规格批次 | 单位 | 期初 | 入库 | 出库 | 账面结存 | 盘点实存 | 差异 | 来源单据号 | 单价 | 原文数量（未指明收发） |\n"
            "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |\n"
            + "".join(
                f"| {cell(row, 'name')} | {post_facts.table_cell(' '.join(filter(None, (row.get('spec'), row.get('batch')))) or '待填')} | "
                f"{cell(row, 'unit', '待填')} | {cell(row, 'opening')} | {cell(row, 'inbound')} | {cell(row, 'outbound')} | "
                f"{cell(row, 'balance')} | {cell(row, 'counted')} | {cell(row, 'variance')} | {cell(row, 'doc', '待填')} | "
                f"{cell(row, 'price')} | {cell(row, 'quantity')} |\n"
                for row in rows
            )
        )
    count_note = (
        "有盘点栏。账、卡、物三栏和差异原因待现场填写。未签字确认不得向现场材料提供盈亏数。"
        if has_count
        else "[A001] 无盘点不编盈亏。"
    )
    lines = [
        "# 收发存台账口径（AI 草稿）",
        "",
        DISCLAIMER,
        "",
        "内部讨论，不替代正式入库单签认，不替代财务记账，不给材料合格结论。",
        "",
        f"- 辖区：{zone}",
        f"- 台账期间：{period or '待填'}",
        f"- 项目：{post_facts.table_cell(header.get('project') or '待填')}",
        f"- 收发日期（用户提供）：{post_facts.table_cell(header.get('date') or '待填')}",
        f"- 仓管 / 经办：{keeper or '待填'}",
        "",
        "## 用户原文",
        "",
        blob.strip() or "（未提供）",
        "",
    ]
    for i, title in enumerate(_WH_CHAPTERS, 1):
        lines.append(f"## {i} {title}")
        lines.append("")
        if i == 1:
            lines.append("内部讨论。不替代正式入库单签认，不替代财务记账，不给材料合格结论。")
        elif i == 2:
            lines.append(
                "合格区、待检区、不合格隔离区分开。甲指、甲限、自采分堆分账。"
                "危险品单独库位。堆码上盖下垫，留通道。本岗不编间距米数。"
            )
        elif i == 3:
            lines.append(
                "对照采购订单或送货单核名称、规格、数量、批次、外观。"
                "需复试的材料进待检区，试验报告未出不得当作合格料发放。"
                "实收与应收差异记数量，不涂改凑平。"
            )
        elif i == 4:
            lines.append("每垛标明名称、规格、批次、进场日期、状态（合格 / 待检 / 不合格）。不擅自报废数字。")
        elif i == 5:
            lines.append("必须凭限额领料单。无单不发料。超限额走追加审批，不口头超发。")
        elif i == 6:
            lines.append(count_note)
            lines.append("")
            lines.append("至少月清。账物不符先记差异，禁止改台账凑数。[A001] 无盘点不编盈亏。")
        elif i == 7:
            lines.append(short)
            lines.append("")
            lines.append(full)
            lines.append("")
            lines.append("有数只抄用户原文。无数 TBD。单价无询价或合同价则 TBD。FIFO 不是法定检定周期。")
        elif i == 8:
            lines.append(
                "| 物资 | 入库 | 领用 | 退回 | 结存 | 双人复核 |\n"
                "| --- | --- | --- | --- | --- | --- |\n"
                "| 待填 | TBD | TBD | TBD | TBD | 待填 |"
            )
            lines.append("")
            lines.append("消防间距和存储限量以用户平面和安质环要求为准，本岗不编间距米数。")
        else:
            lines.append(
                "不把待检料写成已合格。不给复试合格结论。"
                "不编定额章节和综合单价。塔吊证件交设备管理岗。"
            )
        lines.append("")
    if zone in ("SG", "DUAL"):
        lines.append("SG：Factory Notification 不是损耗公式。")
    if zone in ("CN", "DUAL"):
        lines.append("CN：收发存台账不是特种设备检定周期。")
    lines.append("")
    return "\n".join(lines)


def _material_site_md(text: str) -> str:
    from packing_assistant.post_drafts.site_materials import build_draft
    return build_draft('material-site', 'material-site__recon', text)


def _proc_plan_md(text: str) -> str:
    from packing_assistant.post_drafts.procurement import build_draft
    return build_draft("proc-plan", "proc-plan__schedule", text)


def _compare_md(text: str) -> str:
    from packing_assistant.post_drafts.procurement import build_draft
    return build_draft("proc-compare", "proc-compare__table", text)


def _vendor_eval_md(text: str) -> str:
    from packing_assistant.post_drafts.procurement import build_draft
    return build_draft("proc-vendor", "proc-vendor__eval", text)


def _fb_period(blob: str) -> str:
    m = _FB_PERIOD.search(blob or "")
    if not m:
        return "待填"
    return f"{m.group(1)}-{int(m.group(2)):02d}"


def _finance_book_md(text: str) -> str:
    from packing_assistant.post_drafts.business_observations import build_draft
    return build_draft("finance-book", "finance-book__check", text)


def _fund_plan_md(text: str) -> str:
    from packing_assistant.post_drafts.funding import build_draft
    return build_draft('finance-fund', 'finance-fund__plan', text)


def _wb_job(blob: str) -> str:
    t = (blob or "").strip()
    t = re.sub(r"^写一份\S*\s*", "", t).strip()
    t = re.sub(r"^(班前白话稿|班前白话|口播)\s*", "", t).strip()
    for piece in t.replace("；", "\n").replace(";", "\n").splitlines():
        p = piece.strip()
        if not p or p in _WB_SKIP or p.lower() in _WB_SKIP:
            continue
        if p.startswith("#") or p.startswith("内部"):
            continue
        if p in {"JGJ", "SAC", "CN", "SG", "DUAL"}:
            continue
        p = re.sub(r"\s+", " ", p)
        return p[:80]
    return "[A001] 待填部位"


def _wb_mm(blob: str) -> str:
    m = _MM_RE.search(blob or "")
    if not m:
        return ""
    return f"{m.group(1)}{m.group(2)}"


def _worker_brief_md(text: str) -> str:
    from packing_assistant.post_drafts.site_briefs import build_draft
    return build_draft('worker-brief', 'worker-brief__talk', text)


def _pd_site(blob: str) -> str:
    explicit = _PD_SITE_FIELD_RE.search(blob or "")
    if explicit:
        value = re.split(r"\s+(?:天气|出勤|人数|日期|weather)\s*[:：=]?", explicit.group(1), maxsplit=1)[0].strip()
        if value and not _PD_NONFACT.search(value):
            return value[:80]
        return "[A001] 待填部位"
    text = re.sub(r"[@$][\w-]+", "", blob or "")
    text = re.sub(r"(?:请|帮我)?(?:写|编制|生成|出)(?:一份|个|一张)?\s*(?:项目日报|日报|工程日志)(?:草稿|模板|提纲)?", "", text)
    for piece in re.split(r"[；;，,。\n]", text):
        if _PD_NONFACT.search(piece) or not _PD_SITE_TOKEN_RE.search(piece):
            continue
        piece = _PD_WEATHER_FIELD_RE.sub("", piece)
        piece = _PD_WEATHER_RE.sub("", piece)
        piece = _PD_LABOR_RE.sub("", piece)
        piece = re.sub(r"(?:完成率?|进度)?\s*\d+(?:\.\d+)?\s*%", "", piece)
        piece = re.sub(r"\b(?:SG|CN|EU|DUAL|JGJ|SAC|BCA)\b|住建部", "", piece, flags=re.I)
        piece = re.sub(r"\s+", " ", piece).strip(" ：:")
        if piece and _PD_SITE_TOKEN_RE.search(piece):
            return piece[:80]
    return "[A001] 待填部位"


def _pd_weather(blob: str) -> str:
    facts: List[str] = []
    for piece in re.split(r"[；;，,。\n]", blob or ""):
        if _PD_NONFACT.search(piece):
            continue
        matches = list(_PD_WEATHER_FIELD_RE.finditer(piece)) or list(_PD_WEATHER_RE.finditer(piece))
        facts.extend(match.group(1) for match in matches)
    if not facts:
        return "天气待填"
    return f"用户口述：{'；'.join(dict.fromkeys(facts))}。非气象站记录。"


def _pd_labor(blob: str) -> str:
    facts = []
    for piece in re.split(r"[；;，,。\n]", blob or ""):
        if not _PD_NONFACT.search(piece):
            facts.extend(match.group(0) for match in _PD_LABOR_RE.finditer(piece))
    if not facts:
        return "出勤待填"
    return f"用户给出的出勤记事：{'；'.join(dict.fromkeys(facts))}。未给的工种不编人数。"


def _pd_named_fields(blob: str) -> Dict[str, str]:
    """Copy labelled report fields, without interpreting free-form sentences."""
    from packing_assistant.tools.daily_observations import future_scope

    found: Dict[str, str] = {}
    seen: Dict[str, set] = {}
    future = False
    matches = list(_PD_FIELD_RE.finditer(blob or ""))
    for index, match in enumerate(matches):
        key = _PD_NAMED_FIELDS.get(match.group(1).casefold())
        if key is None:
            continue
        end = matches[index + 1].start() if index + 1 < len(matches) else len(blob)
        value = blob[match.end():end]
        for separator in re.finditer(r"[；;\r\n]", value):
            if separator.group() == ";" and re.search(r"&(?:#\d+|#x[\da-fA-F]+|[A-Za-z][A-Za-z0-9]*);$", value[:separator.end()]):
                continue
            value = value[:separator.start()]
            break
        # A trailing instruction belongs to the request, not to the preceding report field.
        value = re.split(
            r"[。.!?]\s*(?=(?:缺失信息|未知信息|未提供的信息|其余|不要|请|"
            r"leave\b|do not\b|keep\b|missing information\b))", value, maxsplit=1, flags=re.I,
        )[0].strip(" ,，；。.")
        future = future_scope(match.group(1), value, future)
        if not value or re.fullmatch(r"[（(]?(?:待填|未填|未提供|未知|未说明|不确定|UNSPECIFIED|TBD|N/A|空)[）)]?[。.]?", value, re.I):
            continue
        if future and key in {"weather", "site", "progress", "labor", "resources", "hse", "issues"}:
            # Preserve the supplied value, but never call a planned count or
            # machine an actual observation merely because it has a label.
            value = f"{match.group(1)}：{value}"
            key = "tomorrow"
        if value not in seen.setdefault(key, set()):
            found[key] = f"{found[key]}；{value}" if key in found else value
            seen[key].add(value)
    return found


def _pm_daily_en(blob: str, named: Dict[str, str], zone: str, observations=None) -> str:
    """English report notes from the same literal fields; no generated quantities or sign-off."""
    from packing_assistant.post_facts import table_cell

    labels = (
        ("project", "Project"), ("date", "Date"), ("weather", "Weather"),
        ("site", "Location"), ("progress", "Work completed"), ("labor", "Workers"),
        ("resources", "Materials and equipment"), ("hse", "Safety and quality"),
        ("issues", "Issues"), ("tomorrow", "Tomorrow"),
    )
    lines = [
        "# Daily report draft", "",
        "AI draft for internal discussion and human review. Not a signed site record, approval, "
        "construction instruction or acceptance certificate.", "",
        f"Jurisdiction: {zone}", "", "## Report fields", "",
        "| Field | Draft |", "| --- | --- |",
    ]
    lines.extend(f"| {label} | {table_cell(named.get(key) or 'Not provided')} |" for key, label in labels)
    for key, label in labels:
        lines.extend(["", f"## {label}", "", named.get(key) or "Not provided."])
    lines.extend([
        "", "## Review and sign-off", "",
        "Prepared by: __________  Reviewed by: __________", "",
        "User-supplied observations have not been independently verified. No completion percentage, "
        "missing headcount, stock balance or safety approval is inferred. Tomorrow's plan is a "
        "proposal, not permission to start work.", "",
    ])
    if observations:
        names = dict(labels)
        lines.extend(["## Reported observations (not independently verified)", "",
                      "Unspecified locations are not inferred from neighbouring records. "
                      "Tomorrow's plans are not today's completed work.", "",
                      "| Field | User-reported text | Location stated in this clause |",
                      "| --- | --- | --- |"])
        for record in observations:
            lines.append(f"| {names[record.field]} | {table_cell(record.text)} | {table_cell(record.location or 'Not provided')} |")
        lines.append("")
    lines.extend(["## Source input", "", blob.strip(), ""])
    return "\n".join(lines)


def _pm_daily_md(text: str) -> str:
    blob = text or ""
    zone = _mix_zone(blob)
    named = _pd_named_fields(blob)
    from packing_assistant.tools import daily_observations

    observations = daily_observations.extract(blob)
    observed = daily_observations.fields(observations)
    for key in daily_observations.LABELS:
        if key in observed:
            named.setdefault(key, observed[key])
    site = named.get("site") or observed.get("site") or _pd_site(blob)
    if _PD_NONFACT.search(site):
        site = "[A001] 待填部位"
    raw_weather = named.get("weather") or observed.get("weather")
    weather = (f"用户口述：{raw_weather}。非气象站记录。" if raw_weather and not _PD_NONFACT.search(raw_weather)
               else "天气待填")
    labor = named.get("labor") or observed.get("labor") or "出勤待填"
    if _PD_NONFACT.search(labor):
        labor = "出勤待填"
    if named.get("issues") and re.search(r"材料|机械|设备|到货|material|equipment|deliver", named["issues"], re.I):
        named.setdefault("resources", named["issues"])
    from packing_assistant.runtime.reply_language import english_request

    request = re.sub(r"^【[^】]*】\s*", "", blob, flags=re.M)
    explicit_language = re.search(r"(?:输出语言|output language)\s*[:：=]\s*(英文|English|en\b)", request, re.I)
    if explicit_language or english_request(request):
        return _pm_daily_en(blob, named, zone, observations)
    import html

    def cell(value):
        return html.escape(value, quote=False).replace("|", "&#124;")

    four = (
        "| 栏 | 本稿 |\n"
        "| --- | --- |\n"
        f"| 项目名称 | {cell(named.get('project') or '待填')} |\n"
        f"| 日期 | {cell(named.get('date') or '待填')} |\n"
        f"| 天气 | {cell(weather)} |\n"
        f"| 部位 | {cell(site)} |\n"
        f"| 形象进度 | {cell(named.get('progress') or '待填')} |\n"
        f"| 出勤 | {cell(labor)} |\n"
        f"| 机械材料 | {cell(named.get('resources') or '待填')} |\n"
        f"| 安全质量记事 | {cell(named.get('hse') or '待填')} |\n"
        f"| 问题 | {cell(named.get('issues') or '待填')} |\n"
        f"| 明日计划 | {cell(named.get('tomorrow') or '待填')} |\n"
    )
    lines = [
        "# 项目日报草稿（AI 草稿 · 内部讨论）",
        "",
        DISCLAIMER,
        "",
        "项目办日清。不是施工日志签认件，不是监理日志。不下开工结论。",
        "",
        f"- 辖区：{zone}",
        "",
        "## 用户原文",
        "",
        blob.strip() or "（未提供）",
        "",
        "## 日报字段",
        "",
        four,
        "",
    ]
    for i, title in enumerate(_PD_CHAPTERS, 1):
        lines.append(f"## {i} {title}")
        lines.append("")
        if i == 1:
            project_line = f"项目名称：{named['project']}。" if named.get("project") else "项目名称待填。"
            date_line = f"日期：{named['date']}。" if named.get("date") else "日期待填。"
            lines.append(project_line + date_line + "填报人空栏。审核人空栏。按单位工程分篇。")
        elif i == 2:
            lines.append(weather)
        elif i == 3:
            lines.append(site)
        elif i == 4:
            lines.append(f"用户提供的形象进度：{named['progress']}" if named.get("progress") else "形象进度待填。")
            lines.append(f"今日作业位置：{site}。未给完成率不计算；用户记录未核验。")
        elif i == 5:
            lines.append(labor)
        elif i == 6:
            lines.append(f"用户提供的机械材料记事：{named['resources']}" if named.get("resources") else "机械材料记事待填。数量 TBD。")
            lines.append("未给过磅和盘点资料不推算吨数或盈亏；记录状态不视为验收合格。")
        elif i == 7:
            if named.get("hse"):
                lines.append(f"用户提供的安全质量记事：{named['hse']}")
            else:
                lines.append("安全质量记事待填。用户未提供巡查事实则本栏不编。")
            lines.append("隐患未改就写未改。不签发合格。班前是否开过只摘事实。")
        else:
            if named.get("tomorrow"):
                lines.append(f"用户提供的明日计划：{named['tomorrow']}")
            else:
                lines.append("明日计划待填。")
            lines.append("继续哪段视书面交底与现场防护。条件未知不编全面铺开。本稿不下开工结论。")
        lines.append("")
    if named.get("issues"):
        lines.extend(["## 待处理问题（用户提供，未核验）", "", named["issues"], ""])
    if observations:
        lines.extend(["## 当日口述记录（未核验）", "",
                      "按原句记录，不替未说明部位的人员或设备补归属；明日安排不算今日完成。", "",
                      "| 字段 | 用户口述（未核验） | 关联部位（仅该句明确时） |",
                      "| --- | --- | --- |"])
        for record in observations:
            lines.append(f"| {daily_observations.LABELS[record.field]} | {cell(record.text)} | {cell(record.location or '未说明')} |")
        lines.append("")
    lines.append("[A001] 天气无记录则待填。出勤无点名则待填。未给完成率不推算百分比。本稿不是监理日志。")
    if zone in ("SG", "DUAL"):
        lines.append("SG：BCA Construction site records / site record book 只写标题。本岗不是这份法定现场簿。")
    if zone in ("CN", "DUAL"):
        lines.append("CN：施工日志只写习惯名。本稿不是监理日志。")
    lines.append("")
    return "\n".join(lines)


def _hr_zone(blob: str) -> str:
    return _mix_zone(blob)


def _hr_role(blob: str) -> str:
    t = (blob or "").strip()
    t = re.sub(r"^写一份\S*\s*", "", t).strip()
    t = re.sub(r"^(招聘简报|招聘|岗位说明书|面试提纲)\s*", "", t).strip()
    t = _HR_PAY_RE.sub("", t)
    for piece in t.replace("；", "\n").replace(";", "\n").splitlines():
        p = piece.strip()
        p = _HR_PAY_RE.sub("", p)
        p = re.sub(r"\s+", " ", p).strip()
        if not p or p in _HR_SKIP or p.lower() in _HR_SKIP:
            continue
        if p.startswith("#") or p.startswith("内部"):
            continue
        if p in {
            "JGJ",
            "SAC",
            "CN",
            "SG",
            "DUAL",
            "住建部",
            "劳动合同法",
            "就业促进法",
        }:
            continue
        return p[:80]
    return "待填"


def _hr_pay(blob: str) -> str:
    m = _HR_PAY_RE.search(blob or "")
    if not m:
        return "薪资：待填。不编市场带宽。"
    fig = next((g for g in m.groups() if g), "")
    if not fig:
        return "薪资：待填。不编市场带宽。"
    return f"薪资：用户给出 {fig}。只抄这一处，不编市场带宽。"


def _hr_duties(role: str, zone: str) -> str:
    eight = (
        "施工现场专业人员可对照 JGJ/T 250-2011 八类名称，用户没点名不要硬套。"
        if zone in ("CN", "DUAL")
        else "施工现场专业人员可对照八类现场岗位名称，用户没点名不要硬套。"
    )
    return (
        f"本岗（{role}）职责按用户描述扩写。"
        f"{eight}"
        "劳资专管员才写实名制、考勤、工资表审核配合。"
        "安全员写监督巡查，不写可替代项目负责人。"
    )


def _hr_qual() -> str:
    return (
        "任职条件只列门槛栏，不替用人部门圈已符合。"
        "学历专业、类似工程经验年限用户未给则待填。"
        "证书与社保以原件核验为准。"
        "身体条件只写适应现场，不写性别、婚育、年龄、地域、民族、户籍限制。"
    )


def _hr_interview() -> str:
    return (
        "行为面：类似项目如何协调进度与安全。"
        "专业面：读图、危大旁站、资料闭合、实名制。"
        "每题只列追问点，不写标准答案分数。"
        "不问婚育、籍贯、房产或证书挂靠。"
    )


def _hr_recruit_md(text: str) -> str:
    from packing_assistant.post_drafts.hr_observations import build_draft
    return build_draft('hr-recruit', 'hr-recruit__brief', text)




def _dispatch_daily_md(text: str) -> str:
    from packing_assistant.post_drafts.dispatch import build_draft

    return build_draft('dispatch', 'dispatch__daily', text)


def _try_fill_scheme_docx(out_dir: Path, project: str, jurisdiction: str = "UNSPECIFIED") -> Dict[str, Any]:
    """T005: attempt skill fill_scheme_docx; fail → docx_pending."""
    scripts = _ROOT / "skills" / "civil-buddy" / "scripts"
    fill_py = scripts / "fill_scheme_template.py"
    scan_py = scripts / "scan_forbidden_inventions.py"
    template = _ROOT / "skills" / "civil-buddy" / "references" / "templates" / "scheme-cn-a4.docx"
    draft = out_dir / "construction__scheme_draft.md"
    if jurisdiction not in {"CN", "SG", "EU", "DUAL"}:
        return {"docx_pending": True, "reason": "辖区未提供，Word 模板待确认辖区后填充"}
    if not fill_py.is_file() or not template.is_file() or not draft.is_file():
        return {"docx_pending": True}
    assumptions = out_dir / "assumptions.md"
    citations = out_dir / "citations.md"
    if not assumptions.is_file():
        guarded_write_text(assumptions, "# 假设\n\n- [A001] 用户未提供的尺寸、荷载一律待填。\n")
    if not citations.is_file():
        guarded_write_text(
            citations,
            "# 已核实\n\n（无）\n\n# 未核实 / UNSPECIFIED\n\n未抽出规范原文。\n",
        )
    docx = out_dir / "专项施工方案-AI草稿.docx"
    cmd = [
        sys.executable,
        str(fill_py),
        "--template",
        str(template),
        "--draft",
        str(draft),
        "--assumptions",
        str(assumptions),
        "--citations",
        str(citations),
        "--jurisdiction",
        jurisdiction,
        "--stamp",
        "AI-DRAFT",
        "--project-name",
        (project or "未命名工程")[:80],
        "--short-name",
        (project or "工程")[:12],
        "--out",
        str(docx),
    ]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=45,
            cwd=str(scripts),
        )
    except (OSError, subprocess.TimeoutExpired):
        return {"docx_pending": True}
    if proc.returncode != 0 or not docx.is_file():
        return {"docx_pending": True}
    if scan_py.is_file():
        try:
            scan = subprocess.run(
                [
                    sys.executable,
                    str(scan_py),
                    "--draft",
                    str(draft),
                    "--docx",
                    str(docx),
                    "--citations",
                    str(citations),
                    "--jurisdiction",
                    jurisdiction,
                ],
                capture_output=True,
                text=True,
                timeout=20,
                cwd=str(scripts),
            )
        except (OSError, subprocess.TimeoutExpired):
            return {"docx_pending": True, "docx": str(docx)}
        if scan.returncode != 0:
            return {
                "docx_pending": True,
                "docx": str(docx),
                "p0_reject_scan": {"hits": (scan.stdout or scan.stderr or "")[:400]},
            }
    return {"docx_pending": False, "docx": str(docx)}


def _construction_eleven(text: str) -> str:
    from packing_assistant.post_drafts.site_briefs import build_draft
    return build_draft('construction', 'construction__scheme_draft', text)


_COVERED = frozenset({"covered", "ok", "done"})
_OPEN = frozenset({"gap", "pending", "missing", "uncovered", "open", "partial", "human_required", "review"})


def _compliance_gaps_md(handoff: Optional[Dict[str, Any]], matrix: Optional[Dict[str, Any]], *,
                        ours: Optional[Dict[str, Any]] = None, comparison: Optional[List[Dict[str, Any]]] = None,
                        unreadable: Optional[List[Dict[str, Any]]] = None,
                        evidence: Optional[List[Dict[str, Any]]] = None,
                        checked: Optional[List[Dict[str, Any]]] = None,
                        responses: Optional[List[Dict[str, Any]]] = None) -> str:
    """响应缺口对照：七节 + 「事项｜招标要求｜响应原文或证据｜三态｜缺口｜责任人」。

    招标要求来自交接里的字段层（handoff["facts"]）和解析器的要求行；我方说法来自 ``ours``（本轮
    文本的字段层）与 ``comparison``（tender_response_match.compare_responses）。三态只描述
    「给没给、对不对得上」，不写合格 / 废标。成稿见 tools/tender_tables.compliance_gaps。"""
    from packing_assistant.tools.tender_tables import compliance_gaps

    return compliance_gaps(handoff, matrix, ours=ours, comparison=comparison, disclaimer=DISCLAIMER,
                           unreadable=unreadable, evidence=evidence, checked=checked, responses=responses)


def _draft_markdown(expert: ExpertRec, tool: str, text: str) -> str:
    return (
        f"# {expert.name} · {tool}\n\n{DISCLAIMER}\n\n"
        f"- 专家：{expert.id}\n- 独有工具：{tool}\n- 产出口径：{expert.delivers}\n"
        f"- 缺的数字 [A001] / UNSPECIFIED\n\n"
        f"## 用户原文\n\n{text.strip() or '（未提供）'}\n\n"
        f"## 草稿\n\n按本岗独有工具出内部讨论提纲。规范只写全名，条款 UNSPECIFIED。"
        f"不是签认件，不作为投标或开工依据。\n"
    )


def _attach_office(out: Dict[str, Any]) -> Dict[str, Any]:
    """Export real Word/Excel files and keep partial results if an export fails."""
    if not out.get("wrote"):
        return out
    from packing_assistant.office_job import export_md_to_docx, export_md_to_xlsx

    extra: List[Dict[str, str]] = []
    errors = []
    for f in list(out.get("files") or []):
        p = Path(str(f.get("path") or ""))
        if p.suffix.lower() != ".md":
            continue
        try:
            for xp in export_md_to_xlsx(p):
                extra.append({"name": xp.name, "path": str(xp), "tool": "office__xlsx"})
        except (OSError, RuntimeError, ValueError):
            errors.append("Excel 导出失败")
        try:
            word = export_md_to_docx(p)
            if word is not None:
                extra.append({"name": word.name, "path": str(word), "tool": "office__docx"})
        except (OSError, RuntimeError, ValueError):
            errors.append("Word 导出失败")
    if extra:
        files = list(out.get("files") or [])
        files.extend(extra)
        out["files"] = files
        ran = list(out.get("tools_run") or [])
        for item in extra:
            if item["tool"] not in ran:
                ran.append(item["tool"])
        out["tools_run"] = ran
        reply = str(out.get("reply") or "")
        formats = [label for tool, label in (("office__docx", "Word"), ("office__xlsx", "Excel"))
                   if any(item["tool"] == tool for item in extra)]
        out["reply"] = (reply + " 已另存 " + "、".join(formats) + "，可下载编辑。").strip()
    if errors:
        out.update(ok=False, error_code="office_export_failed", export_errors=list(dict.fromkeys(errors)))
        out["reply"] = str(out.get("reply") or "") + " " + "、".join(dict.fromkeys(errors)) + "；已生成的文件已保留，请检查目录权限后重试。"
    return out


def _printable(markdown: str) -> str:
    """A draft carries no control characters, whatever the user pasted in.

    They arrive with text copied out of a PDF, a terminal or another workbook, they are invisible,
    and no deliverable format takes them: the Word exporter refuses the draft outright and openpyxl
    used to raise from inside the Excel export and take the whole turn down with it. Dropping them
    changes nothing a person can read.
    """
    return "".join(ch for ch in (markdown or "") if ch in "\t\n" or ord(ch) >= 32)


def _save_drafts(out_dir: Path, drafts: List[tuple[str, str]], reply: str) -> Dict[str, Any]:
    """Validate all drafts before writing; report only artifacts actually saved."""
    from packing_assistant.tools.tender_review import forbidden_hits

    drafts = [(tool, _printable(markdown)) for tool, markdown in drafts]
    result: Dict[str, Any] = {
        "wrote": False, "hitl_pending": False, "files": [], "tools_run": [],
        "submit_blocked": True,
    }
    for _, markdown in drafts:
        hits = forbidden_hits(markdown)
        if hits:
            return {**result, "ok": False, "error_code": "forbidden_content",
                    "reply": "禁语扫描命中，未报成功：" + "、".join(hits)}
    for tool, markdown in drafts:
        path = out_dir / f"{tool}.md"
        try:
            guarded_write_text(path, markdown)
        except (OSError, RuntimeError) as exc:
            code = "permission_denied" if isinstance(exc, PermissionError) else "write_failed"
            return {**result, "ok": False, "error_code": code,
                    "reply": "草稿保存失败。已保存的文件保留供核对，请检查作业目录权限后重试。"}
        result["files"].append({"name": path.name, "path": str(path), "tool": tool})
        result["tools_run"].append(tool)
        result["wrote"] = True
    result["reply"] = reply
    return result


def _run_exclusive(
    expert: ExpertRec,
    text: str,
    *,
    confirm_ok: bool,
    session_id: str,
    packing_summary: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    from packing_assistant.office_job import job_files_blob
    from packing_assistant.runtime.civil_config import high_risk_unconfirmed, load_config

    extra = ""
    if load_config().allow_write() and not high_risk_unconfirmed(risk=expert.risk, confirmed=confirm_ok):
        extra = job_files_blob(text)
    run_text = f"{text}\n\n{extra}".strip() if extra else text
    result = _attach_office(
        _run_exclusive_body(
            expert,
            run_text,
            confirm_ok=confirm_ok,
            session_id=session_id,
            packing_summary=packing_summary,
        )
    )
    if expert.id == "construction" and result.get("wrote"):
        # The same eleven-chapter discussion template supplies Markdown and
        # editable Office copies. Do not select a CN legal form from a guess.
        has_word = any(str(item.get("name") or "").endswith(".docx") for item in result.get("files") or [])
        result["docx_pending"] = not has_word
        if has_word:
            result["tools_run"] = list(dict.fromkeys([*result.get("tools_run", []), "construction__fill_scheme_docx"]))
    if extra:
        result["job_files"] = True
    return result


# All simple post writers use the same scan, save and export path.
_SIMPLE_DRAFTS = {
    'survey': (_survey_record_md, 'survey__record', '已出测量记录草稿。只抄已给点号。submit_blocked=true。'),
    'dispatch': (_dispatch_daily_md, 'dispatch__daily', '已出调度日报草稿。敏感作业交 method-hazard。submit_blocked=true。'),
    'variation': (_variation_form_md, 'variation__form', '已出变更签证草稿。金额 TBD。submit_blocked=true。'),
    'claim': (_claim_notice_md, 'claim__notice', '已整理索赔意向资料。用户合同条款、通知和签收待核，工期金额 TBD。submit_blocked=true。'),
    'subcontract': (_subcontract_sheet_md, 'subcontract__sheet', '已出分包结算表头。用户核量、拟扣款与已付分列，尚未核验；本轮合价与应付 TBD。submit_blocked=true。'),
    'interim': (_interim_measure_md, 'interim__measure', '已出验工计价草稿。用户量与监理/业主记录分列，尚未核验；不编应付合价。submit_blocked=true。'),
    'plan-master': (_plan_master_md, 'plan-master__network', '已出总控计划草稿。关键线路=待计算。submit_blocked=true。'),
    'plan-lookahead': (_plan_lookahead_md, 'plan-lookahead__week', '已出四周滚动草稿。制约未清不得写入本周承诺。submit_blocked=true。'),
    'plan-resource': (_plan_resource_md, 'plan-resource__peak', '已出资源负荷三表。数量待填。submit_blocked=true。'),
    'lab-mix': (_lab_mix_md, 'lab-mix__report', '已出配比报告提纲。无试验数据不给施工配合比。submit_blocked=true。'),
    'lab-sample': (_lab_sample_md, 'lab-sample__list', '已出取样送检清单。见证人空栏。组数 [A001]。submit_blocked=true。'),
    'lab-record': (_lab_record_md, 'lab-record__ledger', '已出试验台账骨架。报告编号待核。结论待填。submit_blocked=true。'),
    'supervision': (_supervision_md, 'supervision__reply', '已出监理通知回复草稿。暂停/复工只出目录。submit_blocked=true。'),
    'safety-brief': (_safety_brief_md, 'safety-brief__talk', '已出安全交底草稿。用户步骤、危险点和措施分列，尚未核验；签认栏保持空白。submit_blocked=true。'),
    'quality': (_quality_md, 'quality__lot', '已出质量检查表。主控/一般/隐蔽结果=未检。submit_blocked=true。'),
    'env': (_env_md, 'env__list', '已出环保文明清单。五行限值 UNSPECIFIED。submit_blocked=true。'),
    'emergency': (_emergency_md, 'emergency__plan', '已整理应急场景、联络资源与拟演练资料；未核验、未签发、未演练，submit_blocked=true。'),
    'equip': (_equip_md, 'equip__ledger', '已出设备台账。只抄用户设备名与已给证件。无证件不编进场结论。submit_blocked=true。'),
    'warehouse': (_warehouse_md, 'warehouse__log', '已出收发存台账。有数只抄。无盘点不编盈亏。submit_blocked=true。'),
    'material-site': (_material_site_md, 'material-site__recon', '已出材料核算表头。算不出节超则 TBD。submit_blocked=true。'),
    'proc-plan': (_proc_plan_md, 'proc-plan__schedule', '已出采购计划表。先分甲供/甲指/自采。提前期 UNSPECIFIED。submit_blocked=true。'),
    'proc-vendor': (_vendor_eval_md, 'proc-vendor__eval', '已出供方评价表头。准入/考察/短名单。分数结论待核。submit_blocked=true。'),
    'finance-book': (_finance_book_md, 'finance-book__check', '已出核算检查表。报销勾选/科目对照/对账缺口。金额 [A001]。submit_blocked=true。'),
    'finance-fund': (_fund_plan_md, 'finance-fund__plan', '已出资金计划草稿。收入/支出窗口。金额 TBD。不是付款指令。submit_blocked=true。'),
    'worker-brief': (_worker_brief_md, 'worker-brief__talk', '已按作业整理班前口播讨论词；联系人、防护与现场条件待核，不是交底签认件。submit_blocked=true。'),
    'pm-daily': (_pm_daily_md, 'pm-daily__log', '已出项目日报草稿。天气待填｜部位｜形象不写百分比｜出勤待填。不是监理日志。submit_blocked=true。'),
    'hr-recruit': (_hr_recruit_md, 'hr-recruit__brief', '已出招聘简报。职责｜任职｜面试问法。薪资未给则待填。不是录用通知。submit_blocked=true。'),
}


def _run_exclusive_body(
    expert: ExpertRec,
    text: str,
    *,
    confirm_ok: bool,
    session_id: str,
    packing_summary: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    tools = _write_tools(expert)
    from packing_assistant.runtime.civil_config import high_risk_unconfirmed, hitl_reply, load_config
    from packing_assistant.runtime.reply_language import english_request

    if not load_config().allow_write():
        return {
            "ok": False, "error_code": "permission_denied", "wrote": False,
            "hitl_pending": False, "files": [], "tools_run": [], "submit_blocked": True,
            "reply": "当前为只读模式，未生成业务文件。",
        }

    if high_risk_unconfirmed(risk=expert.risk, confirmed=confirm_ok):
        return {
            "wrote": False,
            "hitl_pending": True,
            "files": [],
            "tools_run": [],
            "reply": hitl_reply(expert.name, english=english_request(text)),
            "submit_blocked": True,
        }
    if expert.id == "admin-office":
        from packing_assistant.post_drafts.admin import _Facts, _document_kind

        if _document_kind(_Facts(text.rsplit("【本轮用户要求】", 1)[-1])) == "纪要":
            return {
                "ok": False, "error_code": "wrong_post", "wrote": False,
                "hitl_pending": False, "files": [], "tools_run": [], "submit_blocked": True,
                "suggested_expert_id": "admin-doc",
                "reply": "会议纪要应由公文印章岗（admin-doc）起草，请切换岗位后重试。"
                         "会务后勤岗只整理会务安排；本轮未把会务清单冒充会议纪要。",
            }
    out_dir = _out_root() / session_id / expert.id
    out_dir.mkdir(parents=True, exist_ok=True)
    files: List[Dict[str, str]] = []
    ran: List[str] = []

    if expert.id == "bid-parse":
        from packing_assistant.runtime.session_handoff import save_handoff
        from packing_assistant.tools.tender_parse import run_tender_pipeline

        pipe = run_tender_pipeline(text, source="expert-turn", project_name=expert.name)
        request_text = text.rsplit("【本轮用户要求】", 1)[-1]
        path = out_dir / "bid-parse__extract.md"
        guarded_write_text(path, str(pipe.get("extract_table_markdown") or _draft_markdown(expert, "bid-parse__extract", text)))
        files.append({"name": path.name, "path": str(path), "tool": "bid-parse__extract"})
        ran.append("bid-parse__extract")
        ho = pipe.get("handoff") if isinstance(pipe.get("handoff"), dict) else {}
        hp = save_handoff(session_id, ho)
        if hp:
            files.append({"name": hp.name, "path": str(hp), "tool": "tender.handoff"})
            ran.append("tender.handoff")
        return {
            "wrote": True,
            "hitl_pending": False,
            "files": files,
            "tools_run": ran,
            "reply": ("Created an English requirements table and tender.handoff.json. "
                      "AI draft only; submit_blocked=true." if english_request(request_text) else
                      "已按招标解析岗抽出表，并落下 tender.handoff.json 供后岗读。仍是 AI 草稿，submit_blocked=true。"),
            "submit_blocked": True,
            "matrix": pipe.get("matrix"),
            "handoff": pipe.get("handoff"),
            "review": pipe.get("review"),
            "submit_block_reason": pipe.get("submit_block_reason"),
        }

    if expert.id == "bid-compliance":
        from packing_assistant.runtime.session_handoff import load_handoff, save_handoff
        from packing_assistant.tools.tender_parse import run_tender_pipeline

        from packing_assistant.tools.tender_facts import extract as extract_facts, split_sides
        from packing_assistant.tools.tender_response_match import compare_responses

        ho = load_handoff(session_id)
        matrix = None
        ours = extract_facts(text or "").to_dict()
        comparison: List[Dict[str, Any]] = []
        turn_asks = False
        if text and len(text.strip()) > 40:
            pipe = run_tender_pipeline(text, source="expert-compliance", project_name=expert.name)
            said = (pipe.get("handoff") or {}).get("facts") or {}
            asks = bool((pipe.get("parse") or {}).get("requirements")) or any(
                m.get("side") == "tender" for m in said.get("mentions") or []) or bool(said.get("scores") or said.get("specials"))
            # 本轮说了招标要求就以本轮为准；只说了我方情况（"保函开好了，工期改成360天"）则沿用本会话
            # 已解析的招标要求，把这一轮当作响应去对照——否则一句补充就把上一轮的解析冲掉了。
            if asks or not ho:
                turn_asks = True
                matrix = pipe.get("matrix") if isinstance(pipe.get("matrix"), dict) else None
                if isinstance(pipe.get("handoff"), dict) and pipe.get("handoff"):
                    ho = pipe["handoff"]
                    save_handoff(session_id, ho)
                _theirs, mine = split_sides(text)
                sources = [{"source_id": "user-ours", "role": "response", "text": mine, "start": 0}] if mine else []
                comparison = compare_responses((pipe.get("parse") or {}).get("requirements") or [], sources)
        # a job file this turn named and could not read: said in the draft even when the requirements
        # are the ones an earlier turn parsed
        from packing_assistant.office_job import material_role, unread_files

        unread = [{**item, "role": material_role(item["title"])} for item in unread_files(text or "")]
        # Which texts this check is about (tools/bid_check_record.py). When the turn held the tender's
        # words and ours, the two are hashed apart: "工期改成365天" then shows as our side changing.
        from packing_assistant.runtime.worker_context import canonical
        from packing_assistant.tools import bid_check_record as check_record

        if turn_asks:
            theirs, mine = split_sides(text or "")
            checked = [check_record.entry("招标方的话（本轮）", "tender", theirs), check_record.entry("我方的话（本轮）", "response", mine)]
        else:
            checked = [check_record.entry("招标要求（本会话此前解析）", "tender", canonical(ho or {})),
                       check_record.entry("本轮文本", "response", text or "")]
        md = _compliance_gaps_md(ho, matrix, ours=ours, comparison=comparison, unreadable=unread, checked=checked)
        path = out_dir / "bid-compliance__gaps.md"
        record_path = out_dir / ("bid-compliance__gaps" + check_record.RECORD_SUFFIX)
        record = check_record.build(kind="post", session_id=session_id, inputs=checked, drafts=[],
                                    rows=check_record.rows_of(md), unreadable=unread)
        previous = check_record.load(record_path)
        if previous is not None:
            md += "\n" + "\n".join(check_record.comparison_section(previous, record))
        guarded_write_text(path, md)
        record["drafts"] = [{"name": path.name, "sha256": check_record.sha(md)}]
        guarded_write_text(record_path, check_record.dumps(record))
        files.append({"name": path.name, "path": str(path), "tool": "bid-compliance__gaps"})
        files.append({"name": record_path.name, "path": str(record_path), "tool": "bid-compliance__check"})
        ran.append("bid-compliance__gaps")
        return {
            "wrote": True,
            "hitl_pending": False,
            "files": files,
            "tools_run": ran,
            "reply": "已按交接/矩阵出三列对照。不代判废标。submit_blocked=true。",
            "submit_blocked": True,
            "handoff": ho,
            "matrix": matrix,
        }

    if expert.id == "bid-tech":
        from packing_assistant.runtime.session_handoff import load_handoff, save_handoff
        from packing_assistant.tools.tender_parse import build_tech_outline_from_handoff, run_tender_pipeline
        from packing_assistant.tools.technical_response import collect

        ho = load_handoff(session_id)
        current_technical = collect(text)
        if text and (len(text.strip()) > 40 or current_technical):
            pipe = run_tender_pipeline(text, source="expert-tech", project_name=expert.name)
            fresh = pipe.get("handoff") if isinstance(pipe.get("handoff"), dict) else {}
            said = fresh.get("facts") or {}
            # 这一轮自己带了评分点 / 专项 / 工程情况，就按这一轮排目录。此前只要会话里有旧交接就不看
            # 本轮文字：换了一份招标文件再问，出来的还是上一份的目录。只有「出一份技术标目录」这种
            # 不带内容的话才沿用本会话 bid-parse 落下的交接。
            carries = bool(fresh.get("scoring_points") or fresh.get("specials") or said.get("scores")
                           or said.get("specials") or said.get("mentions"))
            new_tender = bool(said.get("scores") or said.get("specials") or any(
                m.get("side") != "ours" and m.get("value") for m in said.get("mentions") or []))
            if ho and current_technical and not new_tender:
                # A prose-only follow-up extends this tender's draft, not a new empty tender.
                amended = {(r.get("section"), r.get("lot", "")) for r in current_technical}
                prior = [r for r in ho.get("technical_response") or [] if isinstance(r, dict)
                         and (r.get("section"), r.get("lot", "")) not in amended]
                ho = {**ho, "technical_response": prior + current_technical}
                save_handoff(session_id, ho)
            elif fresh and (carries or not ho or current_technical):
                ho = fresh
                save_handoff(session_id, ho)
        outline = build_tech_outline_from_handoff(ho or {}, project_name=expert.name)
        md = str(outline.get("markdown") or "")
        if not outline.get("from_extracted_scores"):
            md += "\n\n原文未检出评分点。禁止套上个项目技术标目录。条款 UNSPECIFIED。\n"
        path = out_dir / "bid-tech__expand.md"
        guarded_write_text(path, md)
        files.append({"name": path.name, "path": str(path), "tool": "bid-tech__expand"})
        ran.append("bid-tech__expand")
        return {
            "wrote": True,
            "hitl_pending": False,
            "files": files,
            "tools_run": ran,
            "reply": (
                "已将提供的响应措施整理进对应正文；评分点与未给参数仍须核对。"
                if outline.get("technical_response_count") else
                "已按抽出评分点排技术标目录；未提供我方技术措施，正文仍待补。"
                if outline.get("from_extracted_scores")
                else "未检出评分点，只出待对照前附表，未套上个项目模板。"
            )
            + " 仍是 AI 草稿，submit_blocked=true。",
            "submit_blocked": True,
            "handoff": ho,
            "tech_outline": outline,
        }

    if expert.id == "pack-ship":
        from packing_assistant.runtime.session_packing import load_packing_snapshot
        from packing_assistant.runtime.tool_engine import get_engine

        snap = packing_summary if isinstance(packing_summary, dict) else load_packing_snapshot(session_id)
        connected = bool(snap)
        eng = get_engine()
        health = eng.execute(
            "pack-ship__health",
            {"solver": snap},
            expert_id="pack-ship",
            intent="run",
        )
        listed = eng.execute("pack-ship__list", {}, expert_id="pack-ship", intent="run")
        plan = eng.execute(
            "pack-ship__plan",
            {"solver": snap, "connected": connected, "materials": text},
            expert_id="pack-ship",
            intent="run",
        )
        exported = eng.execute(
            "pack-ship__export",
            {"solver": snap, "connected": connected},
            expert_id="pack-ship",
            intent="run",
        )
        md = str((exported.get("data") or exported).get("markdown") or exported.get("markdown") or "")
        path = out_dir / "pack-ship__export.md"
        guarded_write_text(path, md)
        files.append({"name": path.name, "path": str(path), "tool": "pack-ship__export"})
        ran.extend(["pack-ship__health", "pack-ship__list", "pack-ship__plan", "pack-ship__export"])
        src = "solver" if connected else "disconnected"
        reply = (
            "装柜证据只抄 solver 快照，未重算 xyz。"
            if connected
            else "装柜证据只抄 solver；本轮未接通，utilization/can_fit/mid50/系固待办 为 UNSPECIFIED。"
        )
        return {
            "wrote": True,
            "hitl_pending": False,
            "files": files,
            "tools_run": ran,
            "reply": reply,
            "submit_blocked": True,
            "pack_ship": {
                "source": src,
                "health": health,
                "list": (listed.get("data") or listed).get("names") if isinstance(listed.get("data") or listed, dict) else listed.get("names"),
                "plan": plan.get("data") or plan,
                "export": exported.get("data") or exported,
            },
        }

    if expert.id == "method-hazard":
        from packing_assistant.post_drafts.hazard import TOOL, build_draft
        return _save_drafts(
            out_dir, [(TOOL, build_draft(expert.id, TOOL, text))],
            "已出危大资料对照草稿。未作法定判定或签发，submit_blocked=true。",
        )

    if expert.id == "finance-tax":
        from packing_assistant.tax_context import draft_tax
        return _save_drafts(
            out_dir, [("finance-tax__calendar", draft_tax(text, DISCLAIMER))],
            "已登记用户税务日历资料。给定税率与节点仅作待核输入，缺项 UNSPECIFIED；未计算、申报或缴税，submit_blocked=true。",
        )

    spec = _SIMPLE_DRAFTS.get(expert.id)
    if spec is not None:
        builder, tool, reply = spec
        return _save_drafts(out_dir, [(tool, builder(text))], reply)


    if expert.id == "proc-compare":
        md = _compare_md(text)
        from packing_assistant.tools.tender_review import forbidden_hits

        hits = forbidden_hits(md)
        if hits:
            return {
                "ok": False,
                "error_code": "forbidden_content",
                "wrote": False,
                "hitl_pending": False,
                "files": [],
                "tools_run": [],
                "reply": "禁语扫描命中，未报成功：" + "、".join(hits),
                "submit_blocked": True,
            }
        path = out_dir / "proc-compare__table.md"
        guarded_write_text(path, md)
        files.append({"name": path.name, "path": str(path), "tool": "proc-compare__table"})
        ran.append("proc-compare__table")
        post = forbidden_hits(path.read_text(encoding="utf-8"))
        ran.append("procurement__scan_forbidden")
        if post:
            return {
                "ok": False,
                "error_code": "forbidden_content",
                "wrote": False,
                "hitl_pending": False,
                "files": files,
                "tools_run": ran,
                "reply": "写盘后扫描命中，未报成功：" + "、".join(post),
                "submit_blocked": True,
            }
        return {
            "wrote": True,
            "hitl_pending": False,
            "files": files,
            "tools_run": ran,
            "reply": "已出比价表。一行一家多列。定商标待制度定。写盘后扫描通过。submit_blocked=true。",
            "submit_blocked": True,
        }


    if expert.id == "cost":
        from packing_assistant.post_drafts.business_observations import build_draft
        return _save_drafts(
            out_dir, [("cost__takeoff", build_draft("cost", "cost__takeoff", text))],
            "已整理逐项工程量资料草稿。未计量、组价或批准，submit_blocked=true。",
        )

    if expert.id == "construction":
        return _save_drafts(out_dir, [("construction__scheme_draft", _construction_eleven(text))],
                            "已整理十一章专项讨论草稿。未计算、审批或签认，submit_blocked=true。")

    if not tools:
        tools = [f"{expert.id}__draft"]
    from packing_assistant.post_drafts import build_draft

    drafts = []
    for tool in tools:
        md = build_draft(expert.id, tool, text)
        drafts.append((tool, md if md is not None else _draft_markdown(expert, tool, text)))
    return _save_drafts(
        out_dir, drafts,
        f"{expert.name} 已出内部讨论草稿（{', '.join(tools)}）。不可递交。",
    )


def run_named_exclusive(name: str, args: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """ToolEngine entry: exclusive name → that expert's writer. HITL stays here."""
    from packing_assistant.expert_roster import exclusive_owner, get_expert

    args = args or {}
    owner = exclusive_owner(name)
    exp = get_expert(owner or "")
    if not exp:
        return {
            "ok": False,
            "wrote": False,
            "error_code": "invalid_args",
            "reply": f"未知独有工具 {name}",
            "submit_blocked": True,
            "files": [],
            "tools_run": [],
        }
    bits = [str(args.get("text") or args.get("task") or "").strip()]
    for k in (
        "window",
        "constraints",
        "works",
        "jobs",
        "milestones",
        "trades",
        "labor",
        "plant",
        "equipment",
        "material",
        "materials",
        "items",
        "package",
        "samples",
        "notice",
        "reply_points",
        "work_item",
        "hazards",
        "controls",
        "inspection_lot",
        "site",
        "issues",
        "scenario",
        "certs",
        "item",
        "vendors",
        "vendor",
        "criteria",
        "period",
        "work_today",
        "watchouts",
        "note",
        "notes",
        "progress",
        "weather",
        "labor",
        "site",
        "attendance",
        "resources",
        "hse",
        "role",
        "duties",
        "salary",
        "pay",
        "qualifications",
        "interview",
    ):
        v = args.get(k)
        if v:
            label = {"site": "部位", "weather": "天气", "labor": "出勤", "attendance": "出勤",
                     "progress": "形象进度", "work_today": "今日完成", "resources": "机械材料",
                     "hse": "安全质量记事", "issues": "问题"}.get(k)
            bits.append(f"{label}：{str(v).strip()}" if exp.id == "pm-daily" and label else str(v).strip())
    if args.get("jurisdiction"):
        bits.append(f"辖区：{args['jurisdiction']}")
    if args.get("has_trial_data") is True:
        bits.append("已有试验数据")
    elif args.get("has_trial_data") is False:
        bits.append("无试验数据")
    return _run_exclusive(
        exp,
        "\n".join(b for b in bits if b),
        confirm_ok=args.get("confirm_ok") is True or args.get("p0_confirmed") is True,
        session_id=str(args.get("session_id") or "tool"),
        packing_summary=args.get("packing_summary") if isinstance(args.get("packing_summary"), dict) else None,
    )


def run_expert_turn(
    text: str,
    expert_id: str,
    *,
    confirm_ok: bool = False,
    session_id: str = "",
    force_intent: Optional[str] = None,
    packing_summary: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    exp = get_expert(expert_id)
    if not exp:
        return {
            "ok": False,
            "schema": "civil.expert_turn.v1",
            "error": f"unknown expert: {expert_id}",
            "intent": "chat",
            "wrote": False,
        }
    intent = force_intent if force_intent in {"chat", "run", "both"} else understand(text)
    from packing_assistant.runtime.scheduler import get_scheduler

    sid = session_id or f"turn-{uuid4().hex[:8]}"
    from packing_assistant.runtime.memory import assemble_context, prompt_prefix

    ctx = assemble_context(sid, text=text, p0_confirmed=confirm_ok)
    confirm_ok = confirm_ok is True or ctx.get("p0_confirmed") is True
    ctx_prefix = prompt_prefix(ctx)
    sched = get_scheduler()
    run = sched.create_run(sid, expert_id=exp.id, intent=intent)
    if run.error_code == "session_busy":
        return {
            "ok": False, "schema": "civil.expert_turn.v1", "intent": intent,
            "expert_id": exp.id, "session_id": sid, "run_id": run.run_id,
            "state": run.state, "error_code": "session_busy", "wrote": False,
            "hitl_pending": False, "files": [], "tools_run": [], "submit_blocked": True,
            "reply": "当前任务仍在执行，请等待完成后重试。",
        }
    sched.transition(run, "planning")
    base: Dict[str, Any] = {
        "ok": True,
        "schema": "civil.expert_turn.v1",
        "intent": intent,
        "expert_id": exp.id,
        "expert_name": exp.name,
        "category": exp.category,
        "exclusive": list(exp.exclusive),
        "risk": exp.risk,
        "wrote": False,
        "hitl_pending": False,
        "files": [],
        "tools_run": [],
        "reply": "",
        "submit_blocked": True,
        "n_experts": len(list_experts()),
        "run_id": run.run_id,
        "state": run.state,
        "session_id": sid,
        "context": {
            "jurisdiction": ctx.get("jurisdiction"),
            "project": ctx.get("project"),
            "p0_confirmed": ctx.get("p0_confirmed"),
            "compressed": ctx.get("compressed"),
            "has_handoff": ctx.get("has_handoff"),
            "has_packing": ctx.get("has_packing"),
        },
    }
    try:
        if intent == "chat":
            body = explain_expert(exp, text, previous_jurisdiction=str(ctx.get("jurisdiction") or ""))
            base["reply"] = f"{ctx_prefix}\n{body}".strip() if ctx_prefix else body
            sched.transition(run, "done")
        else:
            ran = _run_exclusive(
                exp, text, confirm_ok=confirm_ok, session_id=sid, packing_summary=packing_summary
            )
            base.update(ran)
            if ran.get("hitl_pending"):
                sched.transition(run, "waiting_hitl")
            elif ran.get("ok") is False:
                run.error_code = str(ran.get("error_code") or "tool_failed")
                sched.transition(run, "failed")
            elif run.cancelled:
                base.update(ok=False, error_code="cancelled", reply="任务已取消，已保存的文件保留供核对。")
            else:
                sched.transition(run, "acting")
                sched.transition(run, "done")
            if intent == "both" and run.state == "done":
                explained = explain_expert(exp, text, previous_jurisdiction=str(ctx.get("jurisdiction") or ""))
                if ctx_prefix:
                    explained = f"{ctx_prefix}\n{explained}"
                base["reply"] = explained + "\n\n" + str(ran.get("reply") or "")
    except Exception:
        run.error_code = "expert_failed"
        if run.state not in {"done", "failed", "cancelled"}:
            sched.transition(run, "failed")
        base.update(ok=False, error_code=run.error_code,
                    reply="岗位任务未完成，请检查输入与作业目录后重试。已保存的文件保留供核对。")
    finally:
        sched.release(sid)
    base["state"] = run.state
    return base
