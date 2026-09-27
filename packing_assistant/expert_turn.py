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

_DISPATCH_CHAPTERS = (
    "报头",
    "草稿声明",
    "计划接口",
    "当日实际",
    "人机料动态",
    "指令栏",
    "交叉作业与工作面交接",
    "停复工与异常",
    "危大/高处/临边等敏感作业清单",
    "明日条件与待决策",
    "附件表头",
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

_CLAIM_CHAPTERS = (
    "封面与草稿声明",
    "事件识别",
    "合同时钟",
    "意向通知必备",
    "证据清单",
    "因果与责任栏",
    "费用组成口径",
    "调概专节",
    "与签证、验工接口",
    "自检",
)

_SUBCONTRACT_CHAPTERS = (
    "封面与草稿声明",
    "合同关系",
    "本期完成",
    "合同内价款栏",
    "合同外",
    "扣款表头",
    "质量与质保",
    "农民工工资专节",
    "会签栏",
    "与对上验工、财务接口",
    "自检",
)

_INTERIM_CHAPTERS = (
    "封面与草稿声明",
    "原则",
    "本期范围",
    "计量依据",
    "计量草表",
    "变更、物价、索赔",
    "过程结算与进度款",
    "农民工工资列示",
    "扣减与预留",
    "不予计价警示",
    "报审签认",
    "自检",
)

_PLAN_MASTER_CHAPTERS = (
    "封面与文件控制",
    "草稿声明",
    "编制依据",
    "开竣工口径提示",
    "工作分解",
    "逻辑关系",
    "一级网络与里程碑",
    "关键线路",
    "表达方式",
    "检查与基线",
    "进度变更",
    "待填与禁令",
)

_PLAN_LOOKAHEAD_CHAPTERS = (
    "封面",
    "从总控抽取窗口",
    "近细远粗",
    "制约因素",
    "周承诺",
    "交叉作业",
    "停工条件",
    "与总控的回写",
    "月度形象对照",
    "待填与禁令",
)

_LOOKAHEAD_SKIP = {
    "草稿提纲",
    "总进度计划",
    "总控计划",
    "周计划",
    "月计划",
    "四周滚动",
    "周月计划",
    "四周滚动计划",
    "master",
    "lookahead",
    "制约已清",
    "条件已具备",
    "待填",
    "四周",
}

_LOOKAHEAD_BLOCK = ("未清", "未到", "未交", "无图", "未发", "过期")

_PLAN_RESOURCE_CHAPTERS = (
    "封面与声明",
    "输入清单",
    "劳动力负荷表头",
    "施工机具负荷表头",
    "主要材料与周转料表头",
    "峰值与错峰",
    "冲突提示栏",
    "与周月、采购、资金的接口",
    "优化记录",
    "禁令",
)

_RESOURCE_SKIP = {
    "草稿提纲",
    "资源负荷",
    "资源计划",
    "资源负荷表",
    "峰值",
    "待填",
    "四周",
    "master",
    "lookahead",
}

_PLANT_KEYS = (
    "塔吊",
    "泵车",
    "挖机",
    "吊车",
    "机械",
    "机具",
    "台班",
    "crane",
    "excavator",
    "pump",
    "tower",
)

_MAT_KEYS = (
    "周转",
    "水泥",
    "砂",
    "材料",
    "rebar",
    "concrete",
    "钢筋",
    "模板",
    "混凝土",
)

_RES_QTY = re.compile(
    r"(?P<qty>\d+(?:\.\d+)?)\s*(?P<unit>人|工日|台班|台|t|吨|kg|m3|m³)",
    re.I,
)

_LAB_MIX_CHAPTERS = (
    "封面与文件控制",
    "草稿声明",
    "选用口径",
    "原材料一致性",
    "调整权限",
    "编制依据",
    "与见证取样、台账的接口",
    "资料目录",
    "禁令",
)

_MIX_TRIAL_YES = ("已有试验数据", "试配记录", "试拌记录", "含水率已测", "试验室配合比已批")
_MIX_GRADE_RE = re.compile(r"([CM]\d{1,3})", re.I)

_LAB_SAMPLE_CHAPTERS = (
    "封面",
    "角色",
    "必须纳入见证取样的类别",
    "比例口径",
    "现场动作提纲",
    "不合格升级",
    "报告效力",
    "与配比、台账、仓管、资料的接口",
    "禁令",
)

_SAMPLE_DEFAULT = (
    "承重结构混凝土试块",
    "承重墙体砌筑砂浆试块",
    "承重结构钢筋及连接接头试件",
    "承重墙的砖和混凝土小型砌块",
    "拌制混凝土和砌筑砂浆的水泥",
    "承重结构混凝土用掺加剂",
    "地下、屋面、厕浴间防水材料",
    "国家规定的其他项目（地方加长项待核）",
)

_SAMPLE_SKIP = {
    "草稿提纲",
    "取样送检清单",
    "见证取样",
    "送检清单",
    "待填",
}

_LAB_RECORD_CHAPTERS = (
    "封面",
    "编号总则",
    "建议分册",
    "原始记录纪律",
    "仪器三件事",
    "公开名称备查",
    "闭合检查表头",
    "接口",
    "禁令",
)

_RECORD_SKIP = {
    "草稿提纲",
    "试验台账",
    "试验台账骨架",
    "待填",
}

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

_SAFETY_BRIEF_CHAPTERS = (
    "封面",
    "草稿声明",
    "作业部位与范围",
    "作业内容和工序步骤",
    "危险源",
    "防护要点",
    "个人防护",
    "禁止事项与喊停条件",
    "应急要点",
    "依据",
    "签字栏",
)

_QUALITY_CHAPTERS = (
    "封面与声明",
    "划分说明",
    "进场与依据",
    "主控项目检查栏",
    "一般项目检查栏",
    "隐蔽专项",
    "通病防治核对",
    "不符合时的处理路径栏目",
    "资料闭合",
    "签字栏",
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

_EQUIP_CHAPTERS = (
    "封面与草稿声明",
    "设备清单表头",
    "进场验收",
    "租赁与台班",
    "维保计划",
    "证件与检验台账",
    "退场与结算附件目录",
    "资料目录",
    "禁令",
)

_EQUIP_SKIP = {
    "草稿提纲",
    "设备台账",
    "维保计划",
    "待填",
}

_CERT_COPY = re.compile(
    r"(合格证|使用登记|作业人员证件|作业证)[:：\s]*([A-Za-z0-9][\w\-./]{2,})"
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

_MS_CHAPTERS = (
    "草稿声明",
    "核算对象",
    "应耗量口径",
    "实耗量口径",
    "节超口径",
    "核算表头",
    "原因类型",
    "周转材料",
    "节奏与会签",
    "禁令",
)

_MS_SKIP = {
    "草稿提纲",
    "材料核算",
    "材料核算表头",
    "现场材料",
    "待填",
    "no stocktake",
}

_PP_CHAPTERS = (
    "封面与文件控制",
    "草稿声明",
    "合同供应方式分列",
    "需用计划来源",
    "物资分类",
    "提前期倒排",
    "到货节点",
    "采购方式初判",
    "接口栏",
    "禁令",
)

_PP_SKIP = {
    "草稿提纲",
    "采购计划",
    "采购计划表",
    "待填",
}

_LEAD_QTY = re.compile(
    r"(?P<qty>\d+(?:\.\d+)?)\s*(?P<unit>工日|天|周|日)",
    re.I,
)

_PC_CHAPTERS = (
    "范围与方式定性",
    "草稿声明",
    "询价文件必备栏",
    "邀请口径",
    "比价表",
    "评审口径",
    "串标与形式审查",
    "定商建议栏",
    "接口栏",
    "禁令",
)

_PC_SKIP = {
    "草稿提纲",
    "比价",
    "询价",
    "比价表",
    "询价比价",
    "询价比价表",
    "待填",
}

_QUOTE_QTY = re.compile(
    r"(?:单价|报价)[:：\s]*(?P<qty>\d+(?:\.\d+)?)",
)

_PV_CHAPTERS = (
    "封面与目的",
    "草稿声明",
    "准入",
    "初审",
    "考察",
    "短名单",
    "评价表头",
    "动态与退出",
    "接口栏",
    "禁令",
)

_PV_SKIP = {
    "草稿提纲",
    "供方评价",
    "供方评价表头",
    "供应商",
    "准入",
    "考察",
    "短名单",
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

_FF_CHAPTERS = (
    "封面",
    "草稿声明",
    "编制原则",
    "合同价款节点",
    "收入栏",
    "支出栏",
    "平衡试算",
    "风险提示",
    "自检",
    "禁令",
)

_FF_SKIP = {
    "草稿提纲",
    "资金计划",
    "资金计划草稿",
    "待填",
}

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
    "机械材料": "resources", "机械材料记事": "resources", "人机料": "resources",
}
_PD_FIELD_RE = re.compile(
    r"(?<![A-Za-z0-9_\u4e00-\u9fff])(" + "|".join(_PD_NAMED_FIELDS)
    + r"|施工部位|作业部位|作业位置|部位|位置|天气|出勤|人数|辖区)\s*[:：=]\s*"
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

_MILESTONE_KEYS = ("桩基", "±0", "封顶", "砌筑", "机电", "装饰", "竣工")

_QTY_RE = re.compile(
    r"(?P<qty>\d+(?:\.\d+)?)\s*(?P<unit>m2|m²|m3|t|吨|kg|工日|项)?",
    re.I,
)

_EVIDENCE_KEYS = (
    "函",
    "通知",
    "停工",
    "天气",
    "影像",
    "照片",
    "试验",
    "会议纪要",
    "回证",
    "letter",
    "notice",
    "photo",
    "record",
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
    points = _copy_survey_points(text)
    known = (
        "\n".join(f"- {p}" for p in points)
        if points
        else "| 点号 | 东坐标 | 北坐标 | 高程 | 来源 |\n| --- | --- | --- | --- | --- |\n| [A001] | [A001] | [A001] | [A001] | 用户未给 |"
    )
    lines = [
        "# 测量方案/记录表（AI 草稿）",
        "",
        DISCLAIMER,
        "",
        f"不是复测签认件。只抄用户已给点号/坐标。缺数 [A001]。条款 UNSPECIFIED。辖区：{_mix_zone(text)}。",
        "",
        "## 用户原文",
        "",
        (text or "").strip() or "（未提供）",
        "",
    ]
    for i, title in enumerate(_SURVEY_CHAPTERS, 1):
        lines.append(f"## {i} {title}")
        lines.append("")
        if i == 2:
            lines.append(DISCLAIMER)
        elif i == 3:
            lines.append((text or "").strip()[:200] or "整节待填。[A001]")
        elif i == 4:
            lines.append(known)
            lines.append("")
            lines.append("禁止编造坐标或点号。无用户坐标不编点号。")
        else:
            lines.append("待按用户点号/图纸填写。[A001]")
        lines.append("")
    lines.append("SG：SVY21 / SHD 只写坐标系统名。CN：工程测量标准只写全名。本记录不是施工依据。")
    lines.append("")
    return "\n".join(lines)


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
    kind = _classify_variation_kind(text)
    basis_no = _copy_variation_no(text)
    facts = (text or "").strip()[:240] or "整节待填。[A001]"
    lines = [
        "# 工程签证 / 设计变更费用口径草稿（AI 草稿）",
        "",
        DISCLAIMER,
        "",
        "不构成已签认签证，不替代设计变更通知单。金额 TBD。条款 UNSPECIFIED。",
        "",
        "## 用户原文",
        "",
        (text or "").strip() or "（未提供）",
        "",
    ]
    for i, title in enumerate(_VARIATION_CHAPTERS, 1):
        lines.append(f"## {i} {title}")
        lines.append("")
        if i == 1:
            lines.append(DISCLAIMER)
        elif i == 2:
            lines.append(f"本表文种：**{kind}**。只选一类。")
        elif i == 3:
            lines.append(facts)
            lines.append("")
            lines.append("时间/部位/事由/谁提出：用户未给的格子待填。[A001]")
        elif i == 4:
            lines.append(basis_no)
            lines.append("")
            lines.append("合同条款只写名称，不编条款号。无用户变更编号则依据待填。")
        elif i == 5:
            lines.append("计算式或现场实测待填。单位待填。与原清单对应编码无则新建项待定。[A001]")
        elif i == 6:
            lines.append(
                "只写路径，不填数：有适用单价则用该单价；只有类似单价则参照并说明差异；都没有则协商，人材机口径单价 TBD。"
            )
        elif i == 7:
            lines.append("| 角色 | 姓名 | 日期 |\n| --- | --- | --- |\n| 监理对事实 |  |  |\n| 造价对价款 |  |  |")
            lines.append("")
            lines.append("空栏，不代签。不把现场确认写成已定价。")
        elif i == 8:
            lines.append("指令内调价走本节。指令外损失、逾期失权风险走索赔调概（claim）。当期计量走验工计价（interim）。")
        elif i == 9:
            lines.append("照片/实测草图/变更单扫描/原清单摘录：有则列名，无则写用户未提供。")
        else:
            lines.append("无金额编造。无事后补签装成当时签。不编无来源限额。")
        lines.append("")
    lines.append("SG：PSSCOC 2020 / PSSCOC-lite 2025 / SIA / REDAS 只写合同族名，条款 UNSPECIFIED。")
    lines.append("CN：GF-2017-0201 / GB/T 50500-2024 只写全名；财建〔2004〕369 号程序是否适用看用户合同，不编确认天数。")
    lines.append("")
    return "\n".join(lines)


def _copy_claim_evidence(blob: str) -> str:
    rows: List[str] = []
    for line in (blob or "").replace("；", "\n").replace(";", "\n").splitlines():
        t = line.strip()
        if not t or t in {"待列", "待补"}:
            continue
        low = t.lower()
        if any(k in t or k in low for k in _EVIDENCE_KEYS):
            rows.append(t[:160])
    if rows:
        return "\n".join(f"- {r}（只抄用户已给）" for r in rows)
    return (
        "| 证据 | 状态 |\n| --- | --- |\n"
        "| 往来函 / 监理通知 / 停工令 | 待补 |\n"
        "| 天气或停水停电记录 | 待补 |\n"
        "| 人员机械进出场 / 影像 / 试验报告 | 待补 |\n"
        "| 采购合同 / 会议纪要 / 送达回证 | 待补 |"
    )


def _claim_notice_md(text: str) -> str:
    event = (text or "").strip()[:240] or "整节待填。[A001]"
    evidence = _copy_claim_evidence(text)
    lines = [
        "# 索赔意向 / 调概事项草稿（AI 草稿）",
        "",
        DISCLAIMER,
        "",
        "不是已送达的索赔报告，不构成调概批复。工期天数 TBD。金额 TBD。条款原文待贴。",
        "",
        "## 用户原文",
        "",
        (text or "").strip() or "（未提供）",
        "",
    ]
    for i, title in enumerate(_CLAIM_CHAPTERS, 1):
        lines.append(f"## {i} {title}")
        lines.append("")
        if i == 1:
            lines.append(DISCLAIMER)
        elif i == 2:
            lines.append(event)
            lines.append("")
            lines.append("费用索赔与工期索赔分列。变更指令内调价优先走变更签证（variation），不重复当索赔。")
        elif i == 3:
            lines.append("用户合同索赔条款原文待贴。不编条款号。时限以用户纸本为准，本表不代填天数。")
            lines.append("")
            lines.append("只提示逾期风险，不断言已失权，不断言一定能要回。")
        elif i == 4:
            lines.append("| 栏 | 内容 |\n| --- | --- |\n| 事件事由 | 只抄用户原文 |\n| 发生时间 | 待填 |\n| 合同依据名称 | 待贴原文 |\n| 可能费用和／或工期 | TBD |\n| 已采取减损 | 待填 |\n| 证据目录 | 见第 5 节 |")
            lines.append("")
            lines.append("不填索赔总价。")
        elif i == 5:
            lines.append(evidence)
        elif i == 6:
            lines.append("事件 → 影响工作面 → 关键线路是否被占（无网络图则工期影响待填）→ 己方有无扩大损失。[A001]")
        elif i == 7:
            lines.append(
                "| 组成 | 单价 |\n| --- | --- |\n| 人工停置 | TBD |\n| 机械停滞 | TBD |\n| 材料仓储或贬值 | TBD |\n| 赶工 | TBD |\n| 利润（是否计取看合同） | TBD |\n| 总部管理费 | TBD |"
            )
        elif i == 8:
            lines.append("政府投资调概只出事项对照表。预备费能覆盖的不调概。本岗不下报批结论。")
        elif i == 9:
            lines.append("能签认的事实先固定在签证。索赔成立后的金额进验工计价或过程结算，无业主确认不编入当期付款。")
        else:
            lines.append("无编造条款号。无编造索赔额。无胜诉或必然支持。")
        lines.append("")
    lines.append("SG：Building and Construction Industry Security of Payment Act 只写全名，时限 UNSPECIFIED。PSSCOC-lite 2025 / Clause 23 Procedure for Claims 只写条名。")
    lines.append("CN：GF-2017-0201 索赔意向/报告天数以用户合同为准。发改投资〔2015〕482 号只写全名。GB 50500 只出现在 CN 栏。")
    lines.append("")
    return "\n".join(lines)


def _parse_subcontract_lines(blob: str) -> List[tuple[str, str, str]]:
    rows: List[tuple[str, str, str]] = []
    for raw in (blob or "").replace("；", "\n").replace(";", "\n").splitlines():
        t = raw.strip()
        t = re.sub(r"^写一份\S*\s*", "", t).strip()
        if not t or t in {"草稿提纲", "待填分包", "待计量"}:
            continue
        if t.startswith("#") or t.startswith("内部"):
            continue
        m = _QTY_RE.search(t)
        if m:
            name = (t[: m.start()] + t[m.end() :]).strip(" ，,") or t[:80]
            unit = m.group("unit") or "TBD"
            rows.append((name[:80], unit, m.group("qty")))
        elif len(t) <= 80:
            rows.append((t[:80], "TBD", "TBD"))
    return rows


def _subcontract_sheet_md(text: str) -> str:
    items = _parse_subcontract_lines(text)
    if items:
        table = "| 分项 | 单位 | 数量 | 合同单价 | 合价 | 来源 |\n| --- | --- | --- | --- | --- | --- |\n"
        table += "".join(
            f"| {n} | {u} | {q} | TBD | TBD | 用户细目 |\n" for n, u, q in items
        )
    else:
        table = (
            "| 分项 | 单位 | 数量 | 合同单价 | 合价 | 来源 |\n| --- | --- | --- | --- | --- | --- |\n"
            "| [A001] | TBD | TBD | TBD | TBD | 用户未给细目 |\n"
        )
    lines = [
        "# 分包（劳务）结算表头（AI 草稿）",
        "",
        DISCLAIMER,
        "",
        "内部对下结算讨论稿，不是已生效结算协议。无总包/业主确认不编金额。",
        "",
        "## 用户原文",
        "",
        (text or "").strip() or "（未提供）",
        "",
    ]
    for i, title in enumerate(_SUBCONTRACT_CHAPTERS, 1):
        lines.append(f"## {i} {title}")
        lines.append("")
        if i == 1:
            lines.append(DISCLAIMER)
        elif i == 2:
            lines.append("专业分包或劳务分包待用户指定。禁止把违法转包写成合法分包。合同编号/计价方式待贴。[A001]")
        elif i == 3:
            lines.append(table)
            lines.append("")
            lines.append("量只抄用户任务单或实测。禁止用形象百分比空估。对上未批则本期待填。")
        elif i == 4:
            lines.append("数量 × 合同单价。无合同单价、无总包/业主确认则合价 TBD。")
        elif i == 5:
            lines.append("洽商、签证另表。无签认不进结算。")
        elif i == 6:
            lines.append(
                "| 扣款项 | 金额 |\n| --- | --- |\n| 甲供材领用 / 水电 / 周转料具损坏 | TBD |\n| 质量/安全罚款（须书面通知） | TBD |\n| 预付款抵扣 / 前期末扣清 | TBD |\n| 农民工工资代发已付 / 其他 | TBD |"
            )
            lines.append("")
            lines.append("没有凭证不编扣款。")
        elif i == 7:
            lines.append("缺陷责任期内预留质量保证金。预留比例待按建质〔2017〕138 号与用户合同核对，不另编百分比当结算结论。")
        elif i == 8:
            lines.append("| 栏 | 金额 |\n| --- | --- |\n| 应付人工费 | TBD |\n| 应付分包工程款 | TBD |")
            lines.append("")
            lines.append("两栏分列，不混。保障农民工工资支付条例只写全名。")
        elif i == 9:
            lines.append(
                "| 部门 | 意见 |\n| --- | --- |\n| 现场工长核量 | 未会签 |\n| 工程部 / 安质 / 物资 / 商务 | 未会签 |\n| 项目经理 | 未会签 |"
            )
        elif i == 10:
            lines.append("对下累计原则上不超过对上已计价对应份额。付款申请交 finance-fund，发票税目交 finance-tax。")
        else:
            lines.append("无编造工日单价。无把工人生活费写成已结清工资。本表不下发放结论。")
        lines.append("")
    lines.append("SG：PSSCOC Nominated Sub-Contract / SOP Act 只写全名。")
    lines.append("CN：保障农民工工资支付条例只写全名，金额 TBD。GB 50500 只出现在 CN 栏。")
    lines.append("")
    return "\n".join(lines)


def _interim_measure_md(text: str) -> str:
    items = _parse_subcontract_lines(text)
    header_row = (
        "| 清单编码 | 名称 | 单位 | 合同量 | 上期末开累 | 本期申报 | 监理审 | 业主核 | 单价 | 本期价 |\n"
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |\n"
    )
    if items:
        table = header_row + "".join(
            f"| TBD | {n} | {u} | TBD | TBD | {q} | TBD | TBD | TBD | TBD |\n" for n, u, q in items
        )
    else:
        table = header_row + "| TBD | [A001] | TBD | TBD | TBD | TBD | TBD | TBD | TBD | TBD |\n"
    period = "待填"
    blob = text or ""
    if "月" in blob or "季" in blob or "期" in blob:
        period = blob.strip()[:80] or "待填"
    lines = [
        "# 对上验工计价草稿（AI 草稿）",
        "",
        DISCLAIMER,
        "",
        "内部报审讨论稿，不是已核准验工报表，不是付款指令。无业主确认不编本期应付。",
        "",
        "## 用户原文",
        "",
        (text or "").strip() or "（未提供）",
        "",
    ]
    for i, title in enumerate(_INTERIM_CHAPTERS, 1):
        lines.append(f"## {i} {title}")
        lines.append("")
        if i == 1:
            lines.append(DISCLAIMER)
        elif i == 2:
            lines.append("有实物工作量的先验工、后计价。不合格、未履行变更程序、超出合同的，不予计价。")
        elif i == 3:
            lines.append(f"期次：{period}。开累与本期分列。起止日期待填。[A001]")
        elif i == 4:
            lines.append("已标价清单及计算规则；经审核施工图及批准变更；质量合格证明。条款原文待贴。")
        elif i == 5:
            lines.append(table)
            lines.append("")
            lines.append("监理审、业主核、单价、本期价无确认则 TBD。不编应付合价。")
        elif i == 6:
            lines.append("只列入已批准文件对应金额或「已批文号 + 金额待填」。未批变更不得计价。")
        elif i == 7:
            lines.append("预付款 / 进度款 / 竣工结算只写财建〔2004〕369 号全名。进度款比例待按财建〔2022〕183 号与用户合同核对，不另编百分比。")
        elif i == 8:
            lines.append("| 栏 | 金额 |\n| --- | --- |\n| 用于支付农民工工资的工程款 | TBD |")
        elif i == 9:
            lines.append("| 项 | 金额 |\n| --- | --- |\n| 预付款抵扣 / 甲供材 / 质保金 / 违约金 | TBD |")
            lines.append("")
            lines.append("有合同和凭证才列。质保金比例待按办法与用户合同核对。")
        elif i == 10:
            lines.append("无开工报告、质量不合格、超图未变、重复计量、超前报量且长期未实施：本期不计价。不作指控。")
        elif i == 11:
            lines.append("承包人编制 → 监理审核 → 建设单位核准。缺一环不写付款结论。")
        else:
            lines.append("无业主确认不编应付合价。价税分开表头保留。")
        lines.append("")
    lines.append("SG：Security of Payment Act payment claim 只写标题，时限 UNSPECIFIED。")
    lines.append("CN：验工计价按用户合同，金额 TBD。GB 50500 只出现在 CN 栏。")
    lines.append("")
    return "\n".join(lines)


def _parse_wbs_names(blob: str) -> List[str]:
    rows: List[str] = []
    for raw in (blob or "").replace("；", "\n").replace(";", "\n").splitlines():
        t = raw.strip()
        t = re.sub(r"^写一份\S*\s*", "", t).strip()
        if not t or t in {"草稿提纲", "总进度计划", "总控计划"}:
            continue
        if t.startswith("#") or t.startswith("内部"):
            continue
        if len(t) <= 80:
            rows.append(t[:80])
    return rows


def _plan_master_md(text: str) -> str:
    names = _parse_wbs_names(text)
    if names:
        wbs = "| 编码 | 名称 | 责任单位 | 工程量来源 | 持续时间来源 | 紧前 |\n| --- | --- | --- | --- | --- | --- |\n"
        wbs += "".join(
            f"| TBD | {n} | TBD | 待填 | 待填 | 待填 |\n" for n in names
        )
    else:
        wbs = (
            "| 编码 | 名称 | 责任单位 | 工程量来源 | 持续时间来源 | 紧前 |\n"
            "| --- | --- | --- | --- | --- | --- |\n"
            "| TBD | [A001] | TBD | 待填 | 待填 | 待填 |\n"
        )
    blob = text or ""
    miles = [k for k in _MILESTONE_KEYS if k in blob]
    if miles:
        mile_tbl = "| 里程碑 | 日期 |\n| --- | --- |\n" + "".join(f"| {m} | 待填 |\n" for m in miles)
    else:
        mile_tbl = (
            "| 里程碑 | 日期 |\n| --- | --- |\n"
            "| 桩基完成 / ±0.000 / 主体封顶（候选） | 里程碑待填 |\n"
        )
    lines = [
        "# 施工总进度计划（AI 草稿 · 内部讨论）",
        "",
        DISCLAIMER,
        "",
        "不是监理批准件，也不是可据以开工的进度计划。禁止编持续时间和关键线路。",
        "",
        "## 用户原文",
        "",
        (text or "").strip() or "（未提供）",
        "",
    ]
    for i, title in enumerate(_PLAN_MASTER_CHAPTERS, 1):
        lines.append(f"## {i} {title}")
        lines.append("")
        if i == 1:
            lines.append("项目名称/合同工期/计划开工竣工待填。签认栏留空。[A001]")
        elif i == 2:
            lines.append(DISCLAIMER)
        elif i == 3:
            lines.append("只列用户已给名称。无定额或方案则依据栏待补。不默写定额号。条款 UNSPECIFIED。")
        elif i == 4:
            lines.append("开竣工日期争议提示查阅法释〔2020〕25 号第八条、第九条认定顺序。本岗不代法院认定日期。")
        elif i == 5:
            lines.append(wbs)
            lines.append("")
            lines.append("WBS。无图纸清单则工程量与持续时间一律待填。")
        elif i == 6:
            lines.append("紧前、紧后、搭接类型（FS/SS/FF/SF）只写用户确认的工艺顺序。禁止编虚工作逻辑。")
        elif i == 7:
            lines.append(mile_tbl)
            lines.append("")
            lines.append("未给定的里程碑名称可列候选，日期待填。")
        elif i == 8:
            lines.append("关键线路=待计算。用户未提供网络参数时禁止本稿指定。关键线路上的作业变更必须回写本总控。")
        elif i == 9:
            lines.append("本稿出表头+文字逻辑，不假装已出批准用网络图。软件名、图号须来自用户。")
        elif i == 10:
            lines.append("冻结基线版本。偏差先对照总时差，再判断是否动总工期。总时差待计算。")
        elif i == 11:
            lines.append("变更原因、是否关键线路、对里程碑的影响待填。金额与意向书改召唤索赔调概（claim）。")
        else:
            lines.append("无来源数字写待填。禁止断言计划合理、一定能按期竣工。")
        lines.append("")
    lines.append("SG：PSSCOC 工期条款只写族名。Programme 提交以用户合同为准。")
    lines.append("CN：施工组织设计规范 / 工程网络计划技术规程只写全名，不编关键线路。")
    lines.append("")
    return "\n".join(lines)


def _lookahead_blocked(line: str) -> bool:
    t = line or ""
    if any(m in t for m in _LOOKAHEAD_BLOCK):
        return True
    if "制约已清" in t or "条件已具备" in t:
        return False
    return "制约" in t


def _clean_lookahead_job(line: str) -> str:
    t = line or ""
    for m in ("制约已清", "条件已具备"):
        t = t.replace(m, "")
    return re.sub(r"\s+", " ", t).strip(" ，,;；")


def _parse_lookahead(blob: str) -> tuple:
    """(window_jobs, blocked_jobs, can_promise). 制约未清不得写入本周承诺。"""
    raw = blob or ""
    any_cleared = "制约已清" in raw or "条件已具备" in raw
    jobs: List[str] = []
    blocked: List[str] = []
    for piece in raw.replace("；", "\n").replace(";", "\n").splitlines():
        t = piece.strip()
        t = re.sub(r"^写一份\S*\s*", "", t).strip()
        if not t or t in _LOOKAHEAD_SKIP:
            continue
        if t.startswith("#") or t.startswith("内部"):
            continue
        if t.startswith("第") and "周" in t[:8]:
            continue
        if len(t) > 80:
            t = t[:80]
        name = _clean_lookahead_job(t) or t
        if not name or name in _LOOKAHEAD_SKIP:
            continue
        if _lookahead_blocked(t):
            blocked.append(name)
        elif name not in jobs:
            jobs.append(name)
    can_promise = bool(any_cleared and not blocked and jobs)
    return jobs, blocked, can_promise


def _plan_lookahead_md(text: str) -> str:
    jobs, blocked, can_promise = _parse_lookahead(text)
    week_jobs = "；".join(jobs) if jobs else "待填 [A001]"
    week_block = "；".join(blocked) if blocked else ("制约未清" if not can_promise else "无未清制约")
    four = (
        "| 周次 | 粒度 | 作业 | 制约状态 |\n"
        "| --- | --- | --- | --- |\n"
        f"| 第1周 | 班组、工作面、日顺序 | {week_jobs} | {week_block} |\n"
        f"| 第2周 | 分项与责任人 | {week_jobs} | {week_block} |\n"
        f"| 第3周 | 分项与制约（较粗） | {week_jobs} | {week_block} |\n"
        f"| 第4周 | 分项与制约（较粗） | {week_jobs} | {week_block} |\n"
    )
    if blocked:
        cons = (
            "| 工作 | 制约 | 责任人 | 计划清除日 |\n"
            "| --- | --- | --- | --- |\n"
            + "".join(f"| {n} | 未清 | 待填 | 待填 |\n" for n in blocked)
        )
    else:
        cons = (
            "| 工作 | 制约 | 责任人 | 计划清除日 |\n"
            "| --- | --- | --- | --- |\n"
            "| [A001] | 待填 | 待填 | 待填 |\n"
        )
    if can_promise:
        promise = (
            "| 作业 | 认领人 | 周末兑现 |\n"
            "| --- | --- | --- |\n"
            + "".join(f"| {n} | 待填 | 待对照 |\n" for n in jobs)
        )
        promise_note = "只列入用户已标明条件已具备的工作。工长认领栏待填。"
    else:
        promise = (
            "| 作业 | 认领人 | 周末兑现 |\n"
            "| --- | --- | --- |\n"
            "| （空） | — | — |\n"
        )
        promise_note = "制约未清，不得写入本周承诺。"
    lines = [
        "# 四周滚动计划 / 月度计划（AI 草稿 · 内部讨论）",
        "",
        DISCLAIMER,
        "",
        "必须挂在总控里程碑下。禁止用周计划改合同工期。不是工期签证，不是复工许可。",
        "",
        "## 用户原文",
        "",
        (text or "").strip() or "（未提供）",
        "",
    ]
    for i, title in enumerate(_PLAN_LOOKAHEAD_CHAPTERS, 1):
        lines.append(f"## {i} {title}")
        lines.append("")
        if i == 1:
            lines.append("计划期（哪四周或哪一自然月）待填。对应总控版本号待填。编制人栏空。内部讨论草稿。[A001]")
        elif i == 2:
            lines.append("把总控里落在未来约四周的工作拉到工长能认领的粒度。总控没有该窗口的工作，本栏写待补，不要发明作业。")
            if jobs:
                lines.append("")
                lines.append("本轮点名作业：" + "；".join(jobs))
        elif i == 3:
            lines.append(four)
            lines.append("")
            lines.append("第 1 周量化到班组、工作面、日顺序；第 2 周到分项与责任人；第 3–4 周保留分项与制约，允许较粗。不编持续天数。")
        elif i == 4:
            lines.append(cons)
            lines.append("")
            lines.append("每条制约指定责任人和计划清除日。未清项不得列入第 5 节周承诺。")
        elif i == 5:
            lines.append(promise_note)
            lines.append("")
            lines.append(promise)
            lines.append("")
            lines.append("周末对照承诺兑现（完成项 / 承诺项）。未完成只记原因分类（图、料、人、机、面、天气、指令），不写处罚结论。")
        elif i == 6:
            lines.append(
                "同一工作面或上下立体空间有两个及以上专业时，单列交叉窗口：谁先谁后、防护谁做、吊装禁区、噪音时段。"
                "计划只排窗口。安全措施改召唤安全交底或施工方案，不在本稿编栏杆高度或吊装半径。"
            )
        elif i == 7:
            lines.append(
                "本月可能触发暂停的外部条件，日期待填：大风、暴雨暴雪、能见度不足、高温橙色以上、"
                "冬期测温未达标、台风预警、政府停工令、危大方案未论证、特种设备证件过期。"
                "停工后只列复工条件栏。本岗不签发复工许可，不编风速限值。"
            )
        elif i == 8:
            lines.append(
                "本周若拖的是关键工作或吃完总时差，必须回写总控版本，并提示索赔调概看时限。"
                "非关键工作的小调整可留在四周窗口内，纪要写明未改总工期。"
            )
        elif i == 9:
            lines.append(
                "| 形象部位 | 计划形象 | 实际形象 | 偏差天数 | 原因 | 纠偏 |\n"
                "| --- | --- | --- | --- | --- | --- |\n"
                "| 待填 | 待填 | 待填 | 待填 | 待填 | 待填 |"
            )
            lines.append("")
            lines.append("无现场反馈则实际栏待填。不要把照片描述写成已验收合格。")
        else:
            lines.append(
                "无班组名单、无总控版本、无制约责任人，对应整节待填。"
                "禁止断言本周计划必定兑现、交叉作业已安全、停工后即可实施。"
            )
        lines.append("")
    lines.append("SG：Last Planner lookahead 只写方法名，不是合同工期变更。")
    lines.append("CN：周月计划不是工期签证。")
    lines.append("")
    return "\n".join(lines)


def _resource_kind(line: str) -> str:
    t = line or ""
    low = t.lower()
    if any(k in t or k in low for k in _PLANT_KEYS):
        return "plant"
    if any(k in t for k in ("工", "班组", "劳动力")):
        return "labor"
    if any(k in t or k in low for k in _MAT_KEYS):
        return "mat"
    if "formwork" in low:
        return "labor"
    return "labor"


def _split_resource_qty(line: str) -> tuple:
    t = (line or "").strip()
    m = _RES_QTY.search(t)
    if not m:
        return t, "TBD", "待填"
    name = (t[: m.start()] + t[m.end() :]).strip(" ，,;；") or t
    return name, f"{m.group('qty')}{m.group('unit')}", "用户给定"


def _parse_resource_items(blob: str) -> tuple:
    labor: List[tuple] = []
    plant: List[tuple] = []
    mat: List[tuple] = []
    for piece in (blob or "").replace("；", "\n").replace(";", "\n").splitlines():
        t = piece.strip()
        t = re.sub(r"^写一份\S*\s*", "", t).strip()
        if not t or t in _RESOURCE_SKIP:
            continue
        if t.startswith("#") or t.startswith("内部"):
            continue
        if re.match(r"^W\d+$", t, re.I):
            continue
        if len(t) > 80:
            t = t[:80]
        name, qty, src = _split_resource_qty(t)
        if not name or name in _RESOURCE_SKIP:
            continue
        kind = _resource_kind(t)
        row = (name, qty, src)
        if kind == "plant":
            plant.append(row)
        elif kind == "mat":
            mat.append(row)
        else:
            labor.append(row)
    return labor, plant, mat


def _resource_table(kind: str, rows: List[tuple]) -> str:
    if kind == "labor":
        head = (
            "| 工种 | 工作 | 计划时段 | 需用人数 | 来源 | 峰值周 | 可否错峰 |\n"
            "| --- | --- | --- | --- | --- | --- | --- |\n"
        )
        if not rows:
            return head + "| [A001] | 待填 | 待填 | TBD | 待填 | 待填 | 待填 |\n"
        return head + "".join(
            f"| {n} | 待填 | 待填 | {q} | {s} | 待填 | 待填 |\n" for n, q, s in rows
        )
    if kind == "plant":
        head = (
            "| 机械名称 | 规格 | 进场日 | 退场日 | 台班或台数 | 对应工作 | 证件 |\n"
            "| --- | --- | --- | --- | --- | --- | --- |\n"
        )
        if not rows:
            return head + "| [A001] | 待填 | 待填 | 待填 | TBD | 待填 | 待核 |\n"
        return head + "".join(
            f"| {n} | 待填 | 待填 | 待填 | {q} | 待填 | 待核 |\n" for n, q, s in rows
        )
    head = (
        "| 名称 | 需用窗口 | 计划进场 | 计划耗尽 | 堆场 | 甲指或自采 | 数量 |\n"
        "| --- | --- | --- | --- | --- | --- | --- |\n"
    )
    if not rows:
        return head + "| [A001] | 待填 | 待填 | 待填 | 待填 | 待填 | TBD |\n"
    return head + "".join(
        f"| {n} | 待填 | 待填 | 待填 | 待填 | 待填 | {q} |\n" for n, q, s in rows
    )


def _plan_resource_md(text: str) -> str:
    labor, plant, mat = _parse_resource_items(text)
    lines = [
        "# 资源负荷表（AI 草稿 · 内部讨论）",
        "",
        DISCLAIMER,
        "",
        "默认交付是表头和口径说明，不是劳动力需用计划定案，也不是采购订单。本表不报价。",
        "",
        "## 用户原文",
        "",
        (text or "").strip() or "（未提供）",
        "",
    ]
    for i, title in enumerate(_PLAN_RESOURCE_CHAPTERS, 1):
        lines.append(f"## {i} {title}")
        lines.append("")
        if i == 1:
            lines.append(
                "对应总控版本、计划期、资源种类范围待填。[A001] "
                "无定额、无劳务计划、无设备台账、无材料需用表时，数量列全部待填。"
            )
        elif i == 2:
            lines.append(
                "须核对：总控或四周窗口、分部分项工程量来源、定额或企业消耗指标、"
                "劳务班组编制、机械台账与证件、甲指/自采划分、堆场与宿舍上限。缺哪一项，对应资源列不填数。"
            )
        elif i == 3:
            lines.append(_resource_table("labor", labor))
            lines.append("")
            lines.append("只汇总用户已给的人数。来源为定额工日或用户给定；否则待填。禁止按经验编人数。")
        elif i == 4:
            lines.append(_resource_table("plant", plant))
            lines.append("")
            lines.append("特种设备证件待核。无证件不得列入进场安排。数量来自施工部署或用户台账，不来自本岗估算。")
        elif i == 5:
            lines.append(_resource_table("mat", mat))
            lines.append("")
            lines.append("数量来自需用计划或清单。本岗不算量、不组价。到货价改召唤采购；收发存改召唤仓管或现场材料。")
        elif i == 6:
            lines.append(
                "横轴为周或旬，纵轴为数量（有数才画）。峰值时段待填。"
                "错峰口径：总工期不变，利用非关键工作时差削峰填谷。禁止为削峰压缩关键工作持续时间。"
            )
        elif i == 7:
            lines.append(
                "| 项 | 提示 |\n| --- | --- |\n"
                "| 宿舍/食堂容量 | 可能冲突，待用户给上限 |\n"
                "| 塔吊台班窗口 | 可能冲突，待用户给上限 |\n"
                "| 混凝土日供应 | 可能冲突，待用户给上限 |\n"
                "| 作业面人数密度 | 可能冲突，待用户给上限 |\n"
                "| 夜间施工许可 | 可能冲突，待用户给上限 |"
            )
            lines.append("")
            lines.append("只标可能冲突。不写已经超标或已经合规。")
        elif i == 8:
            lines.append(
                "四周滚动看本表「这周人机料是否同时具备」；采购看需用窗口和提前期栏；"
                "资金看大额进场时点栏，金额待填，改召唤资金或验工计价。"
            )
        elif i == 9:
            lines.append("未做均衡，仅列表头。未计算时差，不写移动了哪些非关键工作。")
        else:
            lines.append(
                "不编工日、台班、吨数、综合单价、市场价。"
                "禁止宣称资源已经够用。无证件设备不列入进场安排。"
                "关键线路资源缺口必须回写总控，不得只在本表删掉该工作。"
            )
        lines.append("")
    lines.append("SG：Code of Practice on Buildability 只写标题，最低分 UNSPECIFIED。C-Score 不是劳动力需用计划。")
    lines.append("CN：施工组织设计规范 / 劳动定额只写全名，不编工日。")
    lines.append("")
    return "\n".join(lines)


def _mix_has_trial(blob: str) -> bool:
    t = blob or ""
    if "无试验数据" in t:
        return False
    return any(k in t for k in _MIX_TRIAL_YES)


def _mix_zone(blob: str) -> str:
    from packing_assistant.jurisdiction import infer_jurisdiction

    return infer_jurisdiction(blob or "")


def _lab_mix_md(text: str) -> str:
    blob = text or ""
    has = _mix_has_trial(blob)
    zone = _mix_zone(blob)
    gm = _MIX_GRADE_RE.search(blob)
    grade = gm.group(1).upper() if gm else "[A001] 待填"
    kind = "砂浆" if ("砂浆" in blob or (gm and gm.group(1).upper().startswith("M"))) else "混凝土"
    prep = "预拌" if "预拌" in blob else "现场拌合（待核）"
    if has:
        layer4 = "用户声明已有试验数据：可列换算栏，施工配比数字仍须试验室签认。本稿不编 kg/m³。"
    else:
        layer4 = "无试验数据：不给施工配合比，整节待填。含水率未测不得换算湿料。"
    four = (
        "| 层次 | 本稿 |\n| --- | --- |\n"
        "| 初步（理论）配合比 | 缺原材料密度、含水、需水量则停。用量待填。 |\n"
        "| 基准配合比 | 无试拌记录不锁基准。 |\n"
        "| 试验室配合比 | 强度与耐久性复核通过后才能作为换算起点。 |\n"
        f"| 施工配合比 | {layer4} |\n"
    )
    lines = [
        "# 配比报告提纲（AI 草稿）",
        "",
        DISCLAIMER,
        "",
        "本提纲不是法定配合比报告，不是搅拌站开盘依据，不构成浇筑许可。",
        "",
        f"- 辖区：{zone}",
        f"- 种类：{kind} / {prep}",
        "",
        "## 用户原文",
        "",
        blob.strip() or "（未提供）",
        "",
    ]
    for i, title in enumerate(_LAB_MIX_CHAPTERS, 1):
        lines.append(f"## {i} {title}")
        lines.append("")
        if i == 1:
            lines.append(
                f"工程名称待填。部位待填。强度等级/砂浆等级：{grade}。"
                "坍落度或稠度要求待填。全部只引用户或项目包。空签认栏。[A001]"
            )
        elif i == 2:
            lines.append(DISCLAIMER)
            lines.append("")
            lines.append("不是法定配合比报告，不是搅拌站开盘依据。")
        elif i == 3:
            lines.append(four)
            lines.append("")
            lines.append("只写层次，不写用量。砂浆与混凝土分开写，预拌与现场拌合分开写。")
        elif i == 4:
            lines.append(
                "水泥、掺合料、砂、石、外加剂、拌合水须与试配时同一品种、规格、产地口径。"
                "进场复试未出或异常，不得换算施工配比，也不得自行改砂率、水胶比、外加剂掺量。"
            )
        elif i == 5:
            lines.append(
                "试验员可记录含水率和开盘观察，不得口头改配比。"
                "超出批准范围的调整要试验数据 + 试验室主任/技术负责人 + 监理/建设知情。本提纲不代批。"
            )
        elif i == 6:
            if zone in ("CN", "DUAL"):
                lines.append(
                    "公开名称，年份以项目现行有效版为准，状态 unverified / unspecified_clause。"
                    "《普通混凝土配合比设计规程》JGJ 55；《砌筑砂浆配合比设计规程》JGJ/T 98；"
                    "《混凝土质量控制标准》GB 50164；《混凝土结构工程施工质量验收规范》GB 50204；"
                    "《预拌混凝土》GB/T 14902。用户未提供文本则不得写入已核实块，不得摘条款。"
                )
            else:
                lines.append(
                    "公开名称只写族名。条款 unspecified_clause。"
                    "用户未提供文本则不得写入已核实块，不得摘条款。"
                )
        elif i == 7:
            lines.append(
                "| 编号 | 本稿 |\n| --- | --- |\n"
                "| 原材料复试报告编号 | 待填 |\n"
                "| 试配记录编号 | 待填 |\n"
                "| 开盘鉴定记录编号 | 待填 |"
            )
            lines.append("")
            lines.append("有则抄用户，无则待填。编号规则见 lab-record，本岗不编新号。")
        elif i == 8:
            lines.append(
                "试配申请、原材料报告、试拌记录、强度/耐久性试件、批准的试验室配合比、"
                "含水率测定、施工配合比通知单。开盘条件栏待核，本稿不下开盘结论。"
            )
        else:
            lines.append(
                "不编水胶比、砂率、每立方米用量、水泥强度、外加剂掺量。"
                "不把搅拌站经验配比或网上例题当成工程配比。不因商务催省水泥而改单。"
            )
        lines.append("")
    if zone in ("SG", "DUAL"):
        lines.append("SG：SAC laboratory accreditation / CT 06 Ready-Mixed Concrete Producers 只写标题。SS EN 206 / SS 544 只写族名。不得把已过时的 SS 289 / CP 65 当现行配比依据。")
    if zone in ("CN", "DUAL"):
        lines.append("CN：普通混凝土配合比设计规程只写全名，不给施工配比。")
    lines.append("")
    return "\n".join(lines)


def _parse_sample_cats(blob: str) -> List[str]:
    rows: List[str] = []
    for piece in (blob or "").replace("；", "\n").replace(";", "\n").splitlines():
        t = piece.strip()
        t = re.sub(r"^写一份\S*\s*", "", t).strip()
        if not t or t in _SAMPLE_SKIP:
            continue
        if t.startswith("#") or t.startswith("内部"):
            continue
        if t in {"JGJ", "SAC", "CN", "SG", "DUAL"}:
            continue
        if len(t) > 80:
            t = t[:80]
        if t not in rows:
            rows.append(t)
    return rows


def _lab_sample_md(text: str) -> str:
    blob = text or ""
    zone = _mix_zone(blob)
    cats = _parse_sample_cats(blob)
    if not cats:
        cats = list(_SAMPLE_DEFAULT)
    table = (
        "| 类别 | 部位 | 见证人 | 组数 | 升级路径 |\n"
        "| --- | --- | --- | --- | --- |\n"
        + "".join(
            f"| {c} | 待填 | （空） | [A001] | 不合格 24 小时上报；停止相关使用；隔离待处置 |\n"
            for c in cats
        )
    )
    lines = [
        "# 取样送检清单（AI 草稿）",
        "",
        DISCLAIMER,
        "",
        "本清单只排计划与缺口，不判定材料合格，不编组数。不是工程质量验收资料。",
        "",
        f"- 辖区：{zone}",
        "",
        "## 用户原文",
        "",
        blob.strip() or "（未提供）",
        "",
    ]
    for i, title in enumerate(_LAB_SAMPLE_CHAPTERS, 1):
        lines.append(f"## {i} {title}")
        lines.append("")
        if i == 1:
            lines.append("工程名称、施工段、计划周期、检测机构名称待填（须用户给出且为建设委托）。空签认栏。[A001]")
        elif i == 2:
            lines.append(
                "取样员属施工单位；见证人属建设单位或监理。取样员与见证人不得写成同一人同一单位。"
                "建设委托的检测，施工人员须在见证下现场取样；委托单须送检人、见证人签字。"
            )
        elif i == 3:
            lines.append(table)
            lines.append("")
            lines.append("全国公开底线只列名称。地方加长项待核，不编造地方条款。")
        elif i == 4:
            lines.append(
                "涉及结构安全的试块、试件和材料，见证取样和送检比例不得低于有关技术标准规定应取样数量的 30%。"
                "30% 是下限。具体每批组数按该项现行标准 + 用户计划，缺则 [A001]，禁止估算组数。"
            )
        elif i == 5:
            lines.append(
                "按计划取样 → 标识封志 → 共同送检 → 填委托单 → 检测机构核封志。"
                "试样损伤、超时、掉封不得当见证样。"
            )
        elif i == 6:
            lines.append(
                "样品或报告不合格：24 小时内上报，停止相关加工与使用，书面通知监理/建设，隔离待处置。"
                "本清单不代做复检结论。项目试验室负责把报告送达路径写进清单，不冒充主管部门。"
            )
        elif i == 7:
            lines.append(
                "见证取样检测报告须加盖见证取样检测专用章。"
                "非建设单位委托的检测报告不得作为工程质量验收资料。"
                "出厂合格证、供方自检不能替代见证送检。"
            )
        elif i == 8:
            lines.append(
                "未复试或不合格的原材料，lab-mix 不得出施工配比；报告编号连续登记走 lab-record；"
                "实物隔离走 warehouse；资料目录走 supervision。本岗只留接口栏。"
            )
        else:
            lines.append(
                "不写取样合格结论。不编检测数据。"
                "不把监督抽检、企业试验室自检、见证取样混成一种报告。"
            )
        lines.append("")
    if zone in ("SG", "DUAL"):
        lines.append("SG：SAC laboratory accreditation / BCA construction site records 只写标题。")
    if zone in ("CN", "DUAL"):
        lines.append("CN：见证取样和送检的规定 / 建设工程质量检测管理办法只写全名。建建〔2000〕211 号只列名称。")
    lines.append("")
    return "\n".join(lines)


def _parse_record_samples(blob: str) -> List[str]:
    rows: List[str] = []
    for piece in (blob or "").replace("；", "\n").replace(";", "\n").splitlines():
        t = piece.strip()
        t = re.sub(r"^写一份\S*\s*", "", t).strip()
        if not t or t in _RECORD_SKIP or t in _SAMPLE_SKIP:
            continue
        if t.startswith("#") or t.startswith("内部"):
            continue
        if t in {"JGJ", "SAC", "CN", "SG", "DUAL"}:
            continue
        if len(t) > 80:
            t = t[:80]
        if t not in rows:
            rows.append(t)
    return rows


def _lab_record_md(text: str) -> str:
    blob = text or ""
    zone = _mix_zone(blob)
    samples = _parse_record_samples(blob)
    if samples:
        table = (
            "| 试样 | 试验项 | 报告编号 | 仪器检定 | 结论 |\n"
            "| --- | --- | --- | --- | --- |\n"
            + "".join(f"| {s} | 待填 | 待核 | 待核 | 待填 |\n" for s in samples)
        )
    else:
        table = (
            "| 试样 | 试验项 | 报告编号 | 仪器检定 | 结论 |\n"
            "| --- | --- | --- | --- | --- |\n"
            "| 待填 | 待填 | 待核 | 待核 | 待填 |\n"
        )
    lines = [
        "# 试验台账骨架（AI 草稿）",
        "",
        DISCLAIMER,
        "",
        "内部讨论用，不是 CMA/CNAS 证书，不是竣工归档正本。不填检测数据，不给合格结论。",
        "",
        f"- 辖区：{zone}",
        "",
        "## 用户原文",
        "",
        blob.strip() or "（未提供）",
        "",
    ]
    for i, title in enumerate(_LAB_RECORD_CHAPTERS, 1):
        lines.append(f"## {i} {title}")
        lines.append("")
        if i == 1:
            lines.append("工程或试验室名称、年度、台账种类待填。[A001]")
        elif i == 2:
            lines.append(
                "检测合同、委托单、原始记录、检测报告按年度统一编号，编号连续，不得随意抽撤、涂改。"
                "用户未给现行编号规则则只出表头 + [A001] 待填，不发明一套工程代号。"
            )
        elif i == 3:
            lines.append(
                "- 原材料进场复试台账\n"
                "- 混凝土 / 砂浆试配与施工配合比通知台账（只登记编号与日期，用量见 lab-mix）\n"
                "- 试件成型、养护、试压台账\n"
                "- 见证取样送检台账\n"
                "- 检测结果不合格项目台账（单独建册）\n"
                "- 仪器设备台账与检定/校准/期间核查计划\n"
                "- 标准物质与试模、养护室温湿度记录"
            )
            lines.append("")
            lines.append(table)
        elif i == 4:
            lines.append("记录真实、按年连续编号。严禁涂改，笔误杠改并签改人改期。记录、报告、影像与样品标识对同一唯一号。")
        elif i == 5:
            lines.append(
                "检定：对照法定要求给出合格与否，属法制计量。未检、逾期、不合格不得使用。"
                "校准：给出示值误差和不确定度，用于溯源和修正，不等于法定检定。"
                "期间核查：两次检定或校准之间的运行检查，不是再做一次检定。"
                "仪器超检定期不得使用，不得继续出具数据。追溯清单待用户提供，不编报告号。"
            )
        elif i == 6:
            if zone in ("CN", "DUAL"):
                lines.append("《建设工程质量检测管理办法》；《中华人民共和国计量法》。试验方法标准只写名称，正文禁止摘步骤。")
            else:
                lines.append("公开名称只写族名。试验方法标准只写名称，正文禁止摘步骤。条款 unspecified_clause。")
        elif i == 7:
            lines.append(
                "| 检查 | 状态 |\n| --- | --- |\n"
                "| 有取样计划是否有委托单 | 待核 |\n"
                "| 有委托单是否有报告 | 待核 |\n"
                "| 有不合格是否有 24 小时上报和处置 | 待核 |\n"
                "| 有仪器是否在有效期内 | 待核 |"
            )
            lines.append("")
            lines.append("缺一项标缺口。本稿不下归档结论。")
        elif i == 8:
            lines.append("配合比通知单编号给 lab-mix；见证委托单给 lab-sample；资料总目录给 supervision；账物隔离给 warehouse。")
        else:
            lines.append("不编造已完成的检定证书号、报告号、温湿度曲线。不把校准证书改写成法定检定。")
        lines.append("")
    if zone in ("SG", "DUAL"):
        lines.append("SG：SAC laboratory accreditation 只写标题。")
    if zone in ("CN", "DUAL"):
        lines.append("CN：建设工程质量检测管理办法只写全名。")
    lines.append("")
    return "\n".join(lines)


def _supervision_md(text: str) -> str:
    blob = text or ""
    zone = _mix_zone(blob)
    notice = blob.strip() or "待填"
    notice = re.sub(r"^写一份\S*\s*", "", notice).strip() or "待填"
    if notice in {"草稿提纲", "监理回复", "待填"}:
        notice = "待填"
    stop_note = (
        "暂停令、复工报审只出目录和拟办提纲。本岗不签发复工。"
        if any(k in blob for k in ("暂停", "复工", "停工"))
        else "若来文是暂停/复工，只出目录和拟办提纲。本岗不签发复工。"
    )
    lines = [
        "# 监理通知回复草稿（AI 草稿）",
        "",
        DISCLAIMER,
        "",
        "本回复是资料草稿，不是监理指令。待持证人员审核签发后报出。",
        "",
        f"- 辖区：{zone}",
        "",
        "## 用户原文",
        "",
        blob.strip() or "（未提供）",
        "",
    ]
    for i, title in enumerate(_SUPERVISION_CHAPTERS, 1):
        lines.append(f"## {i} {title}")
        lines.append("")
        if i == 1:
            lines.append("工程名称待填。回复编号待填。对应来文编号/日期待填。[A001]")
        elif i == 2:
            lines.append("致：项目监理机构。抄送栏待填。")
        elif i == 3:
            lines.append(notice[:400])
            lines.append("")
            lines.append("只复述用户提供的事由、部位、条数，不扩写没给的事实。")
        elif i == 4:
            lines.append("管理/工艺/材料/资料。缺事实则待填。[A001]")
        elif i == 5:
            lines.append("逐条对应来文，一条不漏。举一反三和预防只作栏目，不编造已培训记录。")
            lines.append("")
            lines.append(stop_note)
        elif i == 6:
            lines.append("从来文或合同抄，否则 [A001] 待填。")
        elif i == 7:
            lines.append(
                "| 证据 | 本稿 |\n| --- | --- |\n"
                "| 整改前后影像 | 待附 |\n"
                "| 检查记录 | 待附 |\n"
                "| 检测报告 | 待附 |\n"
                "| 方案/交底目录 | 待附 |"
            )
        elif i == 8:
            lines.append("项目技术/质量负责人栏空白。")
        elif i == 9:
            lines.append("本回复为 AI 草稿，待项目经理等持证人员审核签发后报出。")
        elif i == 10:
            lines.append(
                "| 来文号 | 要求闭合日 | 实际回复日 | 复查意见 |\n"
                "| --- | --- | --- | --- |\n"
                "| 待填 | 待填 | 待填 | （空，复查属监理） |"
            )
        else:
            lines.append(
                "不写验收合格、资料已闭合可备案。不冒充总监签发。"
                "不编报告编号、强度、闭合天数。暂停/复工只出目录。"
            )
        lines.append("")
    if zone in ("SG", "DUAL"):
        lines.append("SG：BCA construction site records / record structural plan C-forms 只写标题。")
    if zone in ("CN", "DUAL"):
        lines.append("CN：建设工程监理规范只写全名。")
    lines.append("")
    return "\n".join(lines)


def _safety_brief_md(text: str) -> str:
    blob = text or ""
    zone = _mix_zone(blob)
    work = re.sub(r"^写一份\S*\s*", "", blob.strip()).strip() or "待填。[A001]"
    if work in {"草稿提纲", "安全交底", "待填"}:
        work = "待填。[A001]"
    lines = [
        "# 安全技术交底草稿（AI 草稿 · 内部讨论）",
        "",
        DISCLAIMER,
        "",
        "给现场技术员的讨论用交底草稿，不是工人口播，也不是签认件。须持证人员按正式文本复核签字后才可实施。",
        "",
        f"- 辖区：{zone}",
        "",
        "## 用户原文",
        "",
        blob.strip() or "（未提供）",
        "",
    ]
    for i, title in enumerate(_SAFETY_BRIEF_CHAPTERS, 1):
        lines.append(f"## {i} {title}")
        lines.append("")
        if i == 1:
            lines.append("工程名称待填。作业部位、工序待填。交底日期待填。交底人/接受人空栏。[A001]")
        elif i == 2:
            lines.append(DISCLAIMER)
        elif i == 3:
            lines.append(work[:200])
            lines.append("")
            lines.append("轴线、楼层、基坑侧未给则 [A001]。禁止虚构图号。")
        elif i == 4:
            lines.append("只列用户或方案里出现的步骤。未给则待填。[A001]")
        elif i == 5:
            lines.append("只写本部位可能碰到的：临边坠落、洞口、物体打击、坍塌、触电、起重碰撞、有限空间、火灾。不抄全集充数。")
        elif i == 6:
            lines.append("栏杆、盖板、安全带挂点、通道、警戒、湿法、通风检测。高度、间距、荷载一律 [A001]，不编毫米数。")
        elif i == 7:
            lines.append("帽、鞋、镜、手套、安全带、呼吸防护。规格待填。[A001]")
        elif i == 8:
            lines.append("无防护不作业；酒后/带病不上高；有限空间未通风检测不进；指挥信号不清不起吊。")
        elif i == 9:
            lines.append("就近撤离方向待填。急救原则：高坠不乱搬、触电先断电。报告对象待填。电话 [A001]。")
        elif i == 10:
            lines.append("用户点名的规范全名。未提供文本则未核实表 + 条款 UNSPECIFIED。")
        else:
            lines.append("| 交底人 | 接受班组 | 安全员 | 日期 |\n| --- | --- | --- | --- |\n| （空） | （空） | （空） | 待填 |")
            lines.append("")
            lines.append("不预填姓名。本稿不下交底完毕结论。")
        lines.append("")
    if zone in ("SG", "DUAL"):
        lines.append("SG：WSH Council toolbox meeting 导则只写标题。")
    if zone in ("CN", "DUAL"):
        lines.append("CN：安全技术交底按专项方案实施程序只写标题，本岗不签认。")
    lines.append("")
    return "\n".join(lines)


def _parse_qc_items(blob: str) -> List[str]:
    rows: List[str] = []
    for piece in (blob or "").replace("；", "\n").replace(";", "\n").splitlines():
        t = piece.strip()
        t = re.sub(r"^写一份\S*\s*", "", t).strip()
        if not t or t in {"草稿提纲", "质量检查表", "检验批", "待填"}:
            continue
        if t.startswith("#") or t.startswith("内部"):
            continue
        if t in {"JGJ", "SAC", "CN", "SG", "DUAL", "CONQUAS"}:
            continue
        if len(t) > 80:
            t = t[:80]
        if t not in rows:
            rows.append(t)
    return rows


def _qc_table(title_row: str, items: List[str], empty: str) -> str:
    head = "| 检查内容 | 设计或标准要求 | 实测或观察 | 结果 | 处理意见 |\n| --- | --- | --- | --- | --- |\n"
    if not items:
        return head + f"| {empty} | 待填 | 待填 | 未检 | （空） |\n"
    return head + "".join(
        f"| {it} | 待填 | 待填 | 未检 | （空） |\n" for it in items
    )


def _quality_md(text: str) -> str:
    blob = text or ""
    zone = _mix_zone(blob)
    items = _parse_qc_items(blob)
    lot = items[0] if items else "待填"
    lines = [
        "# 质量检查表（AI 草稿 · 内部讨论）",
        "",
        DISCLAIMER,
        "",
        "检验批、隐蔽验收、通病防治的检查栏目。不给合格结论，不替代监理组织验收。",
        "",
        f"- 辖区：{zone}",
        f"- 检验批部位：{lot}",
        "",
        "## 用户原文",
        "",
        blob.strip() or "（未提供）",
        "",
    ]
    for i, title in enumerate(_QUALITY_CHAPTERS, 1):
        lines.append(f"## {i} {title}")
        lines.append("")
        if i == 1:
            lines.append("工程/楼栋/检验批部位待填。对应分项名称待填。检查表编号待填。[A001]")
        elif i == 2:
            lines.append("本表覆盖哪一段、哪一层、哪一批待填。用户未给批量、抽样数量则 [A001]，不编最小抽样。")
        elif i == 3:
            lines.append("图纸图号仅用户清单。施工方案讨论稿名称待填。材料报告编号空则待填。禁止自造图号。")
        elif i == 4:
            lines.append(_qc_table("主控", items, "待列主控项 [A001]"))
            lines.append("")
            lines.append("对安全、节能、环保和主要使用功能起决定作用的项。结果=未检。")
        elif i == 5:
            lines.append(_qc_table("一般", [], "待列一般项 [A001]"))
            lines.append("")
            lines.append("外观、尺寸偏差。同样不预填合格。结果=未检。")
        elif i == 6:
            lines.append(_qc_table("隐蔽", [], "待列隐蔽项 [A001]"))
            lines.append("")
            lines.append("隐蔽前通知、影像、旁站记录栏目。未验收不建议进入下道，但不写开工令。")
        elif i == 7:
            lines.append("楼板裂缝、填充墙裂缝、外墙/屋面/门窗渗漏、回填下沉、保护层、线管叠放、抹灰空鼓。只列易发部位和预防动作。")
        elif i == 8:
            lines.append("返工返修后重新检查。检测鉴定、设计核算等路径只列名称，结论待有资质单位。")
        elif i == 9:
            lines.append("施工记录、测量、材料/试块报告与试验室台账是否对得上。缺报告写缺口，不编强度。")
        else:
            lines.append("| 质检员 | 工长 | 技术负责人 | 监理 |\n| --- | --- | --- | --- |\n| （空） | （空） | （空） | （空） |")
            lines.append("")
            lines.append("禁止预填同意验收。")
        lines.append("")
    if zone in ("SG", "DUAL"):
        lines.append("SG：CONQUAS 只写标题，不是本表评分。")
    if zone in ("CN", "DUAL"):
        lines.append("CN：建筑工程施工质量验收统一标准只写全名。条款 UNSPECIFIED。")
    lines.append("")
    return "\n".join(lines)


def _env_md(text: str) -> str:
    blob = text or ""
    zone = _mix_zone(blob)
    site = re.sub(r"^写一份\S*\s*", "", blob.strip()).strip() or "待填工地"
    if site in {"草稿提纲", "环保文明清单", "待填"}:
        site = "待填工地"
    rows = (
        "| 项 | 措施栏 | 限值 |\n| --- | --- | --- |\n"
        "| 扬尘 | 围挡、道路硬化冲洗、裸土覆盖、粉料入库存罐 | UNSPECIFIED |\n"
        "| 弃土 | 分类堆放、联单或核准去向待填 | UNSPECIFIED |\n"
        "| 污水 | 沉淀/洗车台排水去向待填，不得直排 | UNSPECIFIED |\n"
        "| 夜间 | 属地夜间限制段待核，连续作业报批单另附 | UNSPECIFIED |\n"
        "| 市容 | 大门、公示牌、堆码、人车分流 | UNSPECIFIED |\n"
    )
    lines = [
        "# 环保文明清单（AI 草稿 · 内部讨论）",
        "",
        DISCLAIMER,
        "",
        "覆盖扬尘、弃土、污水、噪声/夜间施工、市容围挡。不是排污许可，也不是城管销号证明。",
        "",
        f"- 辖区：{zone}",
        f"- 工地：{site[:80]}",
        "",
        "## 用户原文",
        "",
        blob.strip() or "（未提供）",
        "",
    ]
    for i, title in enumerate(_ENV_CHAPTERS, 1):
        lines.append(f"## {i} {title}")
        lines.append("")
        if i == 1:
            lines.append("项目、标段、清单日期、责任人空栏、属地区县待填。[A001]")
        elif i == 2:
            lines.append("AI 草稿。措施落实与是否达标由现场和属地监管确认。")
        elif i == 3:
            lines.append(rows)
            lines.append("")
            lines.append("风速阈值用户给才写。监测设备以属地是否要求为准。")
        elif i == 4:
            lines.append("产生部位、暂存点、分类、运输单位、消纳单位、联单编号全部待填。禁止写可随意外运。")
        elif i == 5:
            lines.append("沉淀池/洗车台排水去向待填。不得直排市政管或河道。容量、排放口编号待填。")
        elif i == 6:
            lines.append("昼间/夜间作业时段以属地公告为准。敏感点距离用户给才写。限值 UNSPECIFIED。")
        elif i == 7:
            lines.append("大门、公示牌（建设/监理/施工扬尘责任人和投诉电话）、材料堆码、人员通道与车辆分流。")
        elif i == 8:
            lines.append("安全文明施工费、扬尘防治增加费只列措施事实和影像、验收单名称。费率 TBD，交商务。")
        elif i == 9:
            lines.append("重污染天气、大风、投诉、执法检查——列接到哪一级指令停哪一类作业。本岗不下停工令。")
        else:
            lines.append("| 环保员 | 生产经理 | 资料员 |\n| --- | --- | --- |\n| （空） | （空） | （空） |")
        lines.append("")
    if zone in ("SG", "DUAL"):
        lines.append("SG：NEA Construction Noise Control / Sundays and PH / Noise Management Plan；PUB Earth Control Measures。只列标题，限值 UNSPECIFIED。")
    if zone in ("CN", "DUAL"):
        lines.append("CN：噪声法/扬尘口径只列名称。不编 TSP 限值。")
    lines.append("")
    return "\n".join(lines)


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
    blob = text or ""
    zone = _mix_zone(blob)
    named = _named_emergency_specials(blob)
    special_rows = "| 专项 | 本稿 |\n| --- | --- |\n"
    for spec in _EMERGENCY_SPECIALS:
        if spec in named:
            special_rows += f"| {spec} | 本轮点名。只列名称，不展开假场景。 |\n"
        else:
            special_rows += f"| {spec} | 常见名。用户未点名不展开。 |\n"
    drill = (
        "| 时间 | 科目 | 参演单位 | 评估人 | 发现问题 | 修订意见 |\n"
        "| --- | --- | --- | --- | --- | --- |\n"
        "| 待填 | 待填 | 待填 | 待填 | 待填 | 待填 |\n"
    )
    lines = [
        "# 生产安全事故应急预案提纲（AI 草稿 · 内部讨论）",
        "",
        DISCLAIMER,
        "",
        "只出目录、演练记录表头和待填附件。不签发预案。联系人通讯录全部 [A001]。",
        "",
        f"- 辖区：{zone}",
        "",
        "## 用户原文",
        "",
        blob.strip() or "（未提供）",
        "",
    ]
    for i, title in enumerate(_EMERGENCY_CHAPTERS, 1):
        lines.append(f"## {i} {title}")
        lines.append("")
        if i == 1:
            lines.append("单位/项目待填。预案名称待填。版本待填。签署人空栏。联系人通讯录全部 [A001]。")
        elif i == 2:
            lines.append(
                "风险辨识结论栏待填。应急资源调查清单栏：队伍、车辆、担架、灭火器、洗消、医院。"
                "无现场盘点不编数量。医院名称和电话待填。"
            )
        elif i == 3:
            lines.append(
                "1. 组织机构与职责\n"
                "2. 预案体系\n"
                "3. 风险描述\n"
                "4. 预警与信息报告\n"
                "5. 响应分级\n"
                "6. 保障\n"
                "7. 培训演练与管理"
            )
        elif i == 4:
            lines.append(special_rows)
            lines.append("")
            lines.append("用户没点名则只列常见名、不展开假场景。")
        elif i == 5:
            lines.append("按场所：基坑、脚手架、配电房、食堂、宿舍、桩机区。含职责、措施、注意事项。未给场所则待填。")
        elif i == 6:
            lines.append("一岗一卡，短步骤 + 联络人待填。电话 [A001]。")
        elif i == 7:
            lines.append("内部升级顺序待填。向属地应急和行业主管部门报告的内容栏待填。不编已报告结论。")
        elif i == 8:
            lines.append(drill)
            lines.append("")
            lines.append("评估、问题、修订意见待填。本稿不下演练结论。")
        elif i == 9:
            lines.append(
                "| 附件 | 本稿 |\n| --- | --- |\n"
                "| 通讯录 | 待填；电话 [A001] |\n"
                "| 物资台账 | 待填 |\n"
                "| 医院路线 | 医院名称待填；电话 [A001] |\n"
                "| 周边告知 | 待填 |"
            )
        elif i == 10:
            lines.append("公布日、拟备案机关、评估年待用户填。备案条件栏待核，本稿不下备案结论。")
        else:
            lines.append(
                "不编医院名称和电话，不编响应时间分钟数。"
                "有限空间救援强调禁止盲目进入。"
                "本稿不下演练通过结论。"
            )
        lines.append("")
    if zone in ("SG", "DUAL"):
        lines.append("SG：SCDF Emergency Response Plan 只写标题。")
    if zone in ("CN", "DUAL"):
        lines.append("CN：生产安全事故应急预案管理办法只写标题。")
    lines.append("")
    return "\n".join(lines)


def _parse_equip_names(blob: str) -> List[str]:
    rows: List[str] = []
    for piece in (blob or "").replace("；", "\n").replace(";", "\n").splitlines():
        t = piece.strip()
        t = re.sub(r"^写一份\S*\s*", "", t).strip()
        for key in ("合格证", "使用登记", "作业人员证件", "作业证"):
            if key in t:
                t = t.split(key)[0].strip()
        if not t or t in _EQUIP_SKIP:
            continue
        if t.startswith("#") or t.startswith("内部"):
            continue
        if t in {"JGJ", "SAC", "CN", "SG", "DUAL", "MOM"}:
            continue
        if len(t) > 80:
            t = t[:80]
        if t not in rows:
            rows.append(t)
    return rows


def _copy_equip_certs(blob: str) -> List[tuple]:
    found = []
    for m in _CERT_COPY.finditer(blob or ""):
        found.append((m.group(1), m.group(2)))
    return found


def _equip_md(text: str) -> str:
    blob = text or ""
    zone = _mix_zone(blob)
    names = _parse_equip_names(blob)
    certs = _copy_equip_certs(blob)
    cert_cell = "特种设备证件待核"
    if certs:
        cert_cell = "；".join(f"{n} {c}（用户给定）" for n, c in certs)
    if names:
        inv = (
            "| 名称 | 规格型号 | 厂编号或备案号 | 自有或租赁 | 计划进退场 | 当前状态 |\n"
            "| --- | --- | --- | --- | --- | --- |\n"
            + "".join(
                f"| {n} | 待填 | 待填 | 待填 | 待填 | 待进场 |\n" for n in names
            )
        )
        gate = (
            "| 设备 | 进场验收 | 证件 | 维保 |\n"
            "| --- | --- | --- | --- |\n"
            + "".join(
                f"| {n} | 待做 | {cert_cell} | 待排 |\n" for n in names
            )
        )
    else:
        inv = (
            "| 名称 | 规格型号 | 厂编号或备案号 | 自有或租赁 | 计划进退场 | 当前状态 |\n"
            "| --- | --- | --- | --- | --- | --- |\n"
            "| [A001] | 待填 | 待填 | 待填 | 待填 | 待进场 |\n"
        )
        gate = (
            "| 设备 | 进场验收 | 证件 | 维保 |\n"
            "| --- | --- | --- | --- |\n"
            f"| 待填 | 待做 | {cert_cell} | 待排 |\n"
        )
    cert_tbl = (
        "| 证书名称 | 编号 | 有效期 | 作业项目 | 状态 |\n"
        "| --- | --- | --- | --- | --- |\n"
    )
    if certs:
        cert_tbl += "".join(
            f"| {n} | {c} | 待填 | 待填 | 用户给定 |\n" for n, c in certs
        )
    else:
        cert_tbl += (
            "| 产品合格证 | 待核 | 待填 | 待填 | 待核 |\n"
            "| 使用登记 | 待核 | 待填 | 待填 | 待核 |\n"
            "| 作业人员证件 | 待核 | 待填 | 待填 | 待核 |\n"
        )
    lines = [
        "# 设备台账 / 维保计划（AI 草稿）",
        "",
        DISCLAIMER,
        "",
        "内部讨论。不构成特种设备使用登记、安装验收签认、法定专项方案或开工依据。签认栏留空。",
        "",
        f"- 辖区：{zone}",
        "",
        "## 用户原文",
        "",
        blob.strip() or "（未提供）",
        "",
    ]
    for i, title in enumerate(_EQUIP_CHAPTERS, 1):
        lines.append(f"## {i} {title}")
        lines.append("")
        if i == 1:
            lines.append("标明内部讨论。签认栏留空。[A001]")
        elif i == 2:
            lines.append(inv)
            lines.append("")
            lines.append("无用户清单不编造机号和备案号。只抄用户设备名。")
        elif i == 3:
            lines.append(gate)
            lines.append("")
            lines.append("[A001] 无证件不编进场结论。缺一件写不得进场。本岗不签发使用登记。")
        elif i == 4:
            lines.append("合同要素：谁负责安拆、顶升附着、维保和检测费用；按台班还是包月。无报价则租金和合价 TBD。")
        elif i == 5:
            lines.append("按台分列日常点检、定期保养、故障修理。顶升和附着单独留栏。写过计划不等于已经保养，完成记录栏待填。")
        elif i == 6:
            lines.append(cert_tbl)
            lines.append("")
            lines.append("只抄用户已给证件。过期视同缺失。不编证号。")
        elif i == 7:
            lines.append("进退场单、台班单、维保和修理记录、检测报告复印件、租赁补充协议。金额待填。")
        elif i == 8:
            lines.append("资料目录交给资料监理专家闭合。本岗不宣称资料已闭合。安装拆卸方案交施工方案；是否危大交 method-hazard。")
        else:
            lines.append("不签发使用登记。不编租金、折旧率和综合单价。不宣称通过专家论证或可以投入使用。")
        lines.append("")
    if zone in ("SG", "DUAL"):
        lines.append("SG：MOM lifting equipment / approved crane contractor 只写标题。")
    if zone in ("CN", "DUAL"):
        lines.append("CN：特种设备安全法只写全名。")
    lines.append("")
    return "\n".join(lines)


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
    "location": ("库位", "库区", "堆放位置", "存放位置"),
}
_WH_KEEPERS = ("仓库管理员", "仓管员", "保管员", "库管员", "材料员", "库管", "仓管", "经办人", "验收人", "盘点人", "经办")


def _parse_wh_rows(blob: str) -> List[Dict[str, str]]:
    """One row per material the user named, each figure from its own clause; see post_facts.object_rows."""
    from packing_assistant import post_facts

    rows = post_facts.object_rows(blob, _WH_COLUMNS, drop=("台账", "收发存"))
    for row in rows:
        found = post_facts.labelled(row["source"], _WH_LABELS)
        row.update({key: value for key, value in found.items() if key not in row})
        units = [q.unit for q in post_facts.quantities(row["source"]) if q.unit]
        if units:
            row["unit"] = units[0]
    return rows


def _warehouse_md(text: str) -> str:
    from packing_assistant import post_facts

    blob = text or ""
    zone = _mix_zone(blob)
    rows = _parse_wh_rows(blob)
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
            "| 物资 | 规格批次 | 单位 | 期初 | 入库 | 出库 | 账面结存 | 盘点实存 | 差异 | 来源单据号 | 单价 |\n"
            "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |\n"
            "| 待填物资 | 待填 | 待填 | TBD | TBD | TBD | TBD | TBD | TBD | 待填 | TBD |\n"
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
            "| 物资 | 规格批次 | 单位 | 期初 | 入库 | 出库 | 账面结存 | 盘点实存 | 差异 | 来源单据号 | 单价 |\n"
            "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |\n"
            + "".join(
                f"| {cell(row, 'name')} | {post_facts.table_cell(' '.join(filter(None, (row.get('spec'), row.get('batch')))) or '待填')} | "
                f"{cell(row, 'unit', '待填')} | {cell(row, 'opening')} | {cell(row, 'inbound')} | {cell(row, 'outbound')} | "
                f"{cell(row, 'balance')} | {cell(row, 'counted')} | {cell(row, 'variance')} | {cell(row, 'doc', '待填')} | "
                f"{cell(row, 'price')} |\n"
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


def _qty_after(key: str, line: str) -> str:
    i = (line or "").find(key)
    if i < 0:
        return "TBD"
    m = _RES_QTY.search(line[i + len(key) :])
    if not m:
        return "TBD"
    return f"{m.group('qty')}{m.group('unit')}"


def _parse_ms_rows(blob: str) -> List[tuple]:
    rows: List[tuple] = []
    for piece in (blob or "").replace("；", "\n").replace(";", "\n").splitlines():
        t = piece.strip()
        t = re.sub(r"^写一份\S*\s*", "", t).strip()
        if not t or t.lower() in _MS_SKIP or t in _MS_SKIP:
            continue
        if t.startswith("#") or t.startswith("内部"):
            continue
        if t in {"JGJ", "SAC", "CN", "SG", "DUAL"}:
            continue
        should = _qty_after("应耗", t)
        issued = _qty_after("领料", t)
        counted = _qty_after("盘点", t)
        actual = _qty_after("实耗", t)
        variance = _qty_after("节超", t)
        name = t
        for key in ("应耗", "领料", "退料", "盘点", "实耗", "节超"):
            name = name.replace(key, "")
        name = _RES_QTY.sub("", name)
        name = re.sub(r"\s+", " ", name).strip(" ，,;；") or t[:80]
        if len(name) > 80:
            name = name[:80]
        rows.append((name, should, issued, counted, actual, variance))
    return rows


def _material_site_md(text: str) -> str:
    blob = text or ""
    zone = _mix_zone(blob)
    rows = _parse_ms_rows(blob)
    if not rows:
        short = (
            "| 材料 | 应耗 | 实耗 | 节超 | 备注 |\n"
            "| --- | --- | --- | --- | --- |\n"
            "| 待填 | TBD | TBD | TBD | 无盘点不编 |\n"
        )
        full = (
            "| 分部或部位 | 材料 | 规格 | 单位 | 已完工程量 | 工程量来源 | 消耗指标 | 指标来源 | 应耗 | 领料 | 退料 | 盘点调整 | 实耗 | 节超 | 原因类型 | 单价 | 合价 |\n"
            "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |\n"
            "| 待填 | 待填 | 待填 | 待填 | TBD | 待填 | TBD | 待填 | TBD | TBD | TBD | TBD | TBD | TBD | 待填 | TBD | TBD |\n"
        )
    else:
        short = (
            "| 材料 | 应耗 | 实耗 | 节超 | 备注 |\n"
            "| --- | --- | --- | --- | --- |\n"
            + "".join(
                f"| {n} | {sh} | {ac if ac != 'TBD' else 'TBD'} | {va} | 算不出节超则 TBD |\n"
                for n, sh, _iss, _c, ac, va in rows
            )
        )
        full = (
            "| 分部或部位 | 材料 | 规格 | 单位 | 已完工程量 | 工程量来源 | 消耗指标 | 指标来源 | 应耗 | 领料 | 退料 | 盘点调整 | 实耗 | 节超 | 原因类型 | 单价 | 合价 |\n"
            "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |\n"
            + "".join(
                f"| 待填 | {n} | 待填 | 待填 | TBD | 待填 | TBD | 待填 | {sh} | {iss} | TBD | {cnt} | TBD | {va} | 待填 | TBD | TBD |\n"
                for n, sh, iss, cnt, _ac, va in rows
            )
        )
    lines = [
        "# 材料核算表头（AI 草稿）",
        "",
        DISCLAIMER,
        "",
        "耗用核算和节超分析口径。不是仓库收发，不是设备台班，也不是造价组价。",
        "",
        f"- 辖区：{zone}",
        "",
        "## 用户原文",
        "",
        blob.strip() or "（未提供）",
        "",
    ]
    for i, title in enumerate(_MS_CHAPTERS, 1):
        lines.append(f"## {i} {title}")
        lines.append("")
        if i == 1:
            lines.append("内部讨论。无本周期盘点，不填盈亏。不给材料合格结论。")
        elif i == 2:
            lines.append("主要材料与周转材料分表。甲指、甲限、自采分列。")
        elif i == 3:
            lines.append("应耗 = 已完工程量 × 消耗指标。无工程量或无指标，应耗整列待填，不得用经验百分比填实。不写臆造的定额编号。")
        elif i == 4:
            lines.append("实耗 = 本期领料 − 退料 ± 经盘点确认的调整。无盘点不得把感觉少了写成盘亏。")
        elif i == 5:
            lines.append("本节约定：节超量 = 应耗 − 实耗。正数为节约，负数为超耗。缺应耗或实耗则节超 TBD，不演算。")
        elif i == 6:
            lines.append(short)
            lines.append("")
            lines.append(full)
            lines.append("")
            lines.append("按行只抄应耗、领料、盘点。算不出节超则 TBD。单价 TBD。合价 TBD。")
        elif i == 7:
            lines.append("指标未定、变更未计量、超领未退、盘点未做、浇筑与小票差、不合格隔离、雨损待估。不把超耗写成索赔已经成立。")
        elif i == 8:
            lines.append("进场、在用、维修、报废、退租分栏。摊销方法由财务或用户指定，本岗不编摊销率和会计分录。")
        elif i == 9:
            lines.append("月核算、季分析。工程量问施工或商务；库存问仓管；复试问试验室；单价问采购或造价。")
        else:
            lines.append("无盘点不编盈亏。无定额或指标不编应耗数字。不摘录定额全文。不给综合单价。")
        lines.append("")
    lines.append("[A001] 无盘点不编盈亏。无指标不编应耗百分比。禁止编造损耗率。")
    if zone in ("SG", "DUAL"):
        lines.append("SG：Factory Notification 不是损耗公式。")
    if zone in ("CN", "DUAL"):
        lines.append("CN：无指标不编应耗。不摘定额章节。")
    lines.append("")
    return "\n".join(lines)


def _pp_kind(line: str) -> str:
    if "甲供" in line:
        return "甲供"
    if "甲指" in line:
        return "甲指"
    if "甲限" in line or "甲控" in line:
        return "甲限"
    if "自采" in line or "乙供" in line:
        return "自采"
    return "待划"


def _lead_after(line: str) -> str:
    for key in ("提前期", "周期", "提前"):
        i = (line or "").find(key)
        if i < 0:
            continue
        m = _LEAD_QTY.search(line[i + len(key) :])
        if m:
            return f"{m.group('qty')}{m.group('unit')}"
    return "UNSPECIFIED"


def _pp_qty(line: str) -> str:
    cut = line or ""
    for key in ("提前期", "周期", "提前"):
        i = cut.find(key)
        if i >= 0:
            cut = cut[:i]
    m = _RES_QTY.search(cut)
    if not m:
        return "[A001]"
    return f"{m.group('qty')}{m.group('unit')}"


def _pp_node(line: str) -> str:
    i = (line or "").find("到货")
    if i < 0:
        return "待填"
    rest = line[i + len("到货") :].strip(" ：:，,")
    rest = rest[:40].strip()
    return rest or "待填"


def _parse_pp_rows(blob: str) -> List[tuple]:
    rows: List[tuple] = []
    for piece in (blob or "").replace("；", "\n").replace(";", "\n").splitlines():
        t = piece.strip()
        t = re.sub(r"^写一份\S*\s*", "", t).strip()
        t = re.sub(r"^(采购计划表|采购计划)\s*", "", t).strip()
        if not t or t.lower() in _PP_SKIP or t in _PP_SKIP:
            continue
        if t.startswith("#") or t.startswith("内部"):
            continue
        if t in {"JGJ", "SAC", "CN", "SG", "DUAL"}:
            continue
        kind = _pp_kind(t)
        lead = _lead_after(t)
        qty = _pp_qty(t)
        node = _pp_node(t)
        name = t
        for key in ("甲供", "甲指", "甲限", "甲控", "自采", "乙供", "提前期", "周期", "提前", "到货"):
            name = name.replace(key, "")
        name = _RES_QTY.sub("", name)
        name = _LEAD_QTY.sub("", name)
        name = re.sub(r"\s+", " ", name).strip(" ，,;；") or t[:80]
        if len(name) > 80:
            name = name[:80]
        if not name or name in _PP_SKIP:
            continue
        rows.append((name, kind, qty, lead, node))
    return rows


def _pp_group_table(rows: List[tuple], kind: str) -> str:
    picked = [r for r in rows if r[1] == kind]
    lines = [
        f"### {kind}",
        "",
        "| 物资 | 数量 | 提前期 | 到货节点 | 来源 |",
        "| --- | --- | --- | --- | --- |",
    ]
    if not picked:
        lines.append("| 待填 | [A001] | UNSPECIFIED | 待填 | 未划 |")
    else:
        for n, _k, q, lead, node in picked:
            lines.append(f"| {n} | {q} | {lead} | {node} | 用户原文 |")
    return "\n".join(lines)


def _proc_plan_md(text: str) -> str:
    blob = text or ""
    zone = _mix_zone(blob)
    rows = _parse_pp_rows(blob)
    if not rows:
        combined = (
            "| 物资 | 供应方式 | 提前期 | 到货节点 |\n"
            "| --- | --- | --- | --- |\n"
            "| 待填 | 待划 | UNSPECIFIED | 待填 |\n"
        )
    else:
        combined = (
            "| 物资 | 供应方式 | 提前期 | 到货节点 |\n"
            "| --- | --- | --- | --- |\n"
            + "".join(f"| {n} | {k} | {lead} | {node} |\n" for n, k, _q, lead, node in rows)
        )
    groups = "\n\n".join(
        _pp_group_table(rows, kind) for kind in ("甲供", "甲指", "自采", "甲限", "待划")
    )
    lines = [
        "# 采购计划表（AI 草稿）",
        "",
        DISCLAIMER,
        "",
        "物资采购计划。不是采购合同、招标文件或付款指令。先分供应方式，再列表。",
        "",
        f"- 辖区：{zone}",
        "",
        "## 用户原文",
        "",
        blob.strip() or "（未提供）",
        "",
    ]
    for i, title in enumerate(_PP_CHAPTERS, 1):
        lines.append(f"## {i} {title}")
        lines.append("")
        if i == 1:
            lines.append("项目、标段、编制人空栏、日期、版本待填。[A001] 标明内部讨论，不是签认件。")
        elif i == 2:
            lines.append("本表只供内部讨论，不构成采购合同、招标文件或付款指令。")
        elif i == 3:
            lines.append("必须先分甲供 / 甲指 / 自采，再列表。用户未写供应方式的进待划，禁止猜成自采。")
            lines.append("")
            lines.append(groups)
            lines.append("")
            lines.append(combined)
            lines.append("")
            lines.append("按行只抄用户已写的供应方式。未写则待划。提前期无供方周期则 UNSPECIFIED。")
        elif i == 4:
            lines.append("数量来源写图纸 / 需用单编号 / 用户口述。无需用计划不得臆造数量，缺则 [A001]。")
        elif i == 5:
            lines.append("主材、构配件、周转材料（买或租分开）、辅材、劳保、危化品（单独行）、小型机具。办公后勤不进本表。")
        elif i == 6:
            lines.append(
                "提前期 = 提需审批 + 寻源询价或招标程序时间 + 供方生产 + 运输 + 进场验收。"
                "用户未给供方周期则提前期 UNSPECIFIED。禁止编造提前天数。"
            )
        elif i == 7:
            lines.append("只写用户或计划岗已给的形象节点。对不齐就标注进度节点待计划岗确认。不编关键线路。")
        elif i == 8:
            lines.append(
                "只列询比 / 竞价 / 谈判 / 直接采购 / 招标程序名称。"
                "金额门槛不默写。制度未提供则采购方式待企业制度。本表不裁定必须招标。"
            )
        elif i == 9:
            lines.append("仓储卸货条件、试验复试批次、资金计划月份、危大方案是否占用该批材料：只留表头，不替别岗填数。")
        else:
            lines.append(
                "不编综合单价和市场价。不把甲供数量写成自采。不把周转租赁写成购置。"
                "无需用计划不编工程量。本稿不是下单指令。"
            )
        lines.append("")
    lines.append("[A001] 无需用计划不编数量。无供方周期则提前期 UNSPECIFIED。禁止编造提前天数。")
    if zone in ("SG", "DUAL"):
        lines.append("SG：BCA CRS 投标限额只写门户标题。GeBIZ 只当门户。")
    if zone in ("CN", "DUAL"):
        lines.append("CN：无需用计划不编数量。必须招标的工程项目规定只写全名。")
    lines.append("")
    return "\n".join(lines)


def _pc_price(line: str) -> str:
    if "无报价" in (line or ""):
        return "TBD"
    m = _QUOTE_QTY.search(line or "")
    if not m:
        return "TBD"
    return f"{m.group('qty')}（用户报价）"


def _parse_compare(blob: str) -> tuple:
    item = ""
    vendors: List[tuple] = []
    for piece in (blob or "").replace("；", "\n").replace(";", "\n").splitlines():
        raw = piece.strip()
        t = re.sub(r"^写一份\S*\s*", "", raw).strip()
        t = re.sub(r"^(询价比价表|比价表草稿|比价表|询价比价|询价|比价)\s*", "", t).strip()
        if not t or t.lower() in _PC_SKIP or t in _PC_SKIP:
            continue
        if t.startswith("#") or t.startswith("内部"):
            continue
        if t in {"JGJ", "SAC", "CN", "SG", "DUAL"}:
            continue
        marked = ("供方" in t) or ("供应商" in t)
        cleaned = re.sub(r"供方|供应商", "", t)
        cleaned = _QUOTE_QTY.sub("", cleaned)
        cleaned = re.sub(r"\s+", " ", cleaned).strip(" ，,;；") or t
        if len(cleaned) > 40:
            cleaned = cleaned[:40]
        price = _pc_price(raw)
        if marked:
            if cleaned and cleaned not in _PC_SKIP:
                vendors.append((cleaned, price))
            continue
        parts = cleaned.split()
        if not item:
            item = parts[0] if parts else t[:40]
            for p in parts[1:]:
                if p and p not in _PC_SKIP:
                    vendors.append((p, price))
        else:
            if cleaned and cleaned != item:
                vendors.append((cleaned, price))
    return item or "待填", vendors


def _compare_md(text: str) -> str:
    blob = text or ""
    zone = _mix_zone(blob)
    item, vendors = _parse_compare(blob)
    head = (
        "| 供方名称 | 规格响应 | 品牌产地 | 数量 | 单价 | 合价 | 到货期 | 质保 | 付款 | 运费装卸 | 发票种类 | 偏离说明 | 资料是否齐全 |\n"
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |\n"
    )
    if not vendors:
        table = head + "| 待询 | 待填 | 待填 | [A001] | TBD | TBD | 待填 | 待填 | 待填 | 待填 | 待填 | 待填 | 待核 |\n"
        invite = "拟询对象待填。企业询比通常不少于三家；不足三家写明原因，不得虚构第三家。"
    else:
        table = head + "".join(
            f"| {n} | 待填 | 待填 | [A001] | {p} | TBD | 待填 | 待填 | 待填 | 待填 | 待填 | 待填 | 待核 |\n"
            for n, p in vendors
        )
        if len(vendors) < 3:
            invite = f"本轮列 {len(vendors)} 家。有效报价不足三家及原因待填。不得虚构第三家。"
        else:
            invite = f"本轮列 {len(vendors)} 家。来源（合格名录 / 业主书面短名单 / 用户指定）待填。"
    lines = [
        "# 询价比价表（AI 草稿）",
        "",
        DISCLAIMER,
        "",
        "询价比价口径。无供应商书面报价一律不填单价。不是招标文件，也不是成交通知。",
        "",
        f"- 辖区：{zone}",
        f"- 标的：{item}",
        "",
        "## 用户原文",
        "",
        blob.strip() or "（未提供）",
        "",
    ]
    for i, title in enumerate(_PC_CHAPTERS, 1):
        lines.append(f"## {i} {title}")
        lines.append("")
        if i == 1:
            lines.append(
                "先定性本包是现场自采询比、依法必须招标货物，还是政府采购，再列表。"
                "三类法规名称不同。金额门槛不默写。是否达到必须招标，待用户提供估算依据，本岗不随口估合同额。"
            )
        elif i == 2:
            lines.append("内部讨论。无报价不编价。不做价格本。本稿不定标。")
        elif i == 3:
            lines.append(
                "物资名称、规格型号、计量单位、数量来源、交货期、交货地点、运输卸车、质保、售后、"
                "付款、发票、验收、样品/检测、有效期、违约责任。空栏待填。不要只写请报价。"
            )
        elif i == 4:
            lines.append(invite)
        elif i == 5:
            lines.append(table)
            lines.append("")
            lines.append("一行一家，一列一项。禁止只留总价列。无报价单价 TBD。无单价不乘合价。")
        elif i == 6:
            lines.append(
                "同等条件比到货期、质保、付款、运距、检测、售后。价格只是一列。"
                "不得用市场价补未报列。不得写建议成交价。"
            )
        elif i == 7:
            lines.append("只列异常现象（同一模板、同一小数、同一邮箱）。不下已串标法律结论。")
        elif i == 8:
            lines.append("待用户/评标小组按制度定。可列响应缺口。本稿不定标，不写现定给哪一家。")
        elif i == 9:
            lines.append("本表编号回写采购计划行号。是否已在合格名录问供应商岗。付款条件原文抄给财务，不替财务做资金计划。")
        else:
            lines.append(
                "无报价不编单价。不做价格本、信息价本。"
                "不把政府采购方式不加说明地套到非政府采购项目。本稿不下成交结论。"
            )
        lines.append("")
    lines.append("[A001] 无报价不编价。定商标待制度定。禁止编造市场价。")
    if zone in ("SG", "DUAL"):
        lines.append("SG：GeBIZ / MOF value for money 只写标题。不把 PQM 权重抄进材料比价。")
    if zone in ("CN", "DUAL"):
        lines.append("CN：无报价不编价。询价程序不是招标定标。必须招标的工程项目规定只写全名。")
    lines.append("")
    return "\n".join(lines)


def _parse_pv_names(blob: str) -> List[str]:
    names: List[str] = []
    for piece in (blob or "").replace("；", "\n").replace(";", "\n").splitlines():
        t = piece.strip()
        t = re.sub(r"^写一份\S*\s*", "", t).strip()
        t = re.sub(
            r"^(供方评价表头|供应商评价表|供方评价|供应商评价|评价表|准入考察)\s*",
            "",
            t,
        ).strip()
        t = re.sub(r"^(供方|供应商)\s*", "", t).strip()
        if not t or t.lower() in _PV_SKIP or t in _PV_SKIP:
            continue
        if t.startswith("#") or t.startswith("内部"):
            continue
        if t in {"JGJ", "SAC", "CN", "SG", "DUAL"}:
            continue
        if len(t) > 80:
            t = t[:80]
        if t not in names:
            names.append(t)
    return names


def _pv_three_tables(names: List[str]) -> tuple:
    rows = names or ["待填供方"]
    access = (
        "| 供方 | 执照 | 许可 | 业绩 | 有效期 | 初审 |\n"
        "| --- | --- | --- | --- | --- | --- |\n"
        + "".join(f"| {n} | 未提供 | 许可种类待核对 | 待填 | 待核 | 待核 |\n" for n in rows)
    )
    visit = (
        "| 供方 | 厂址与库容 | 产线与样品 | 检测设备 | 考察人日期 | 结论 |\n"
        "| --- | --- | --- | --- | --- | --- |\n"
        + "".join(f"| {n} | 待填 | 待填 | 待填 | 待填 | 待核 |\n" for n in rows)
    )
    short = (
        "| 供方 | 口径 | 来源 | 结论 |\n"
        "| --- | --- | --- | --- |\n"
        + "".join(f"| {n} | 待核（拟入名录 / 仅本项目短名单 / 观察期） | 待填 | 待核 |\n" for n in rows)
    )
    score = (
        "| 供方 | 供货批次 | 规格符合 | 到货及时 | 资料齐全 | 售后 | 取样复试 | 安全文明 | 书面报价 | 分数 | 结论 |\n"
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |\n"
        + "".join(
            f"| {n} | 待填 | 待核 | 待核 | 待核 | 待核 | 待核 | 待核 | 无书面报价 | 待核 | 待核 |\n"
            for n in rows
        )
    )
    return access, visit, short, score


def _vendor_eval_md(text: str) -> str:
    blob = text or ""
    zone = _mix_zone(blob)
    names = _parse_pv_names(blob)
    access, visit, short, score = _pv_three_tables(names)
    shown = "、".join(names) if names else "待填供方"
    lines = [
        "# 供应商准入 / 考察 / 评价表（AI 草稿）",
        "",
        DISCLAIMER,
        "",
        "供方建档口径。不是成交通知，不是合格证，也不是进场许可。缺证照就待填，不编证书号和业绩额。",
        "",
        f"- 辖区：{zone}",
        f"- 供方：{shown}",
        "",
        "## 用户原文",
        "",
        blob.strip() or "（未提供）",
        "",
    ]
    for i, title in enumerate(_PV_CHAPTERS, 1):
        lines.append(f"## {i} {title}")
        lines.append("")
        if i == 1:
            lines.append("本次是新供方准入、既有名录复评、项目短名单，还是退出建议，待用户标明。项目、品类、编制人空栏。[A001]")
        elif i == 2:
            lines.append("内部讨论。无履约事实不打分。权重待企业制度。本稿不下成交结论。")
        elif i == 3:
            lines.append("有原件/复印件/系统截图才勾。没有就写未提供。不编许可证号。未提供自愿性证书不写已通过。")
            lines.append("")
            lines.append(access)
        elif i == 4:
            lines.append("证照是否在有效期、经营范围是否覆盖本包，只记录用户出示的名单名称。不联网查询后认定。")
        elif i == 5:
            lines.append("去了才填。没去就整节待填。考察人、日期、照片编号留空给用户。")
            lines.append("")
            lines.append(visit)
        elif i == 6:
            lines.append("列拟入名录 / 仅本项目短名单 / 观察期。名录规则待企业制度。不新造黑名单栏目当已生效文件。")
            lines.append("")
            lines.append(short)
        elif i == 7:
            lines.append("无履约事实不打分。价格只作有无书面报价，不填金额。禁止发明权重。")
            lines.append("")
            lines.append(score)
        elif i == 8:
            lines.append("质量事故、虚假资料、无故断供、拒绝配合复试：只列事实和证据编号。退出写提请按企业制度审议，不写已清退出库。")
        elif i == 9:
            lines.append("未准入是否允许被询价按企业制度，制度待填。甲指供方仍要资料建档。厂家报告交试验室，取样结论不由本岗改写。")
        else:
            lines.append(
                "不编业绩和证书编号。不因关系户省略考察栏。"
                "能进名录不等于已经成交。评价不打价格分。不替代特种设备或危化品许可本身。"
            )
        lines.append("")
    lines.append("[A001] 分数待核。结论待核。不编证书号和业绩额。禁止编造成交结论。")
    if zone in ("SG", "DUAL"):
        lines.append("SG：GeBIZ / BCA CRS 只写门户标题。GTP 不等于执照。")
    if zone in ("CN", "DUAL"):
        lines.append("CN：无证照不编已准入。国资采购管理工作指导意见只写全名。")
    lines.append("")
    return "\n".join(lines)


def _fb_period(blob: str) -> str:
    m = _FB_PERIOD.search(blob or "")
    if not m:
        return "待填"
    return f"{m.group(1)}-{int(m.group(2)):02d}"


def _finance_book_md(text: str) -> str:
    blob = text or ""
    zone = _mix_zone(blob)
    period = _fb_period(blob)
    named = []
    for k in ("收发存", "盘点", "分包", "台班", "工资专户", "发票"):
        if k in blob:
            named.append(k)
    gap_note = (
        "用户点名：" + "、".join(named) + "。缺哪边台账就列缺口，不编盈亏。"
        if named
        else "缺哪边台账就列缺口。无盘点不编盈亏。"
    )
    subjects = (
        "| 成本项目 | 账套科目名称 | 金额 |\n"
        "| --- | --- | --- |\n"
        "| 人工费 | 待核 | [A001] |\n"
        "| 材料费 | 待核 | [A001] |\n"
        "| 机械使用费 | 待核 | [A001] |\n"
        "| 其他直接费 | 待核 | [A001] |\n"
        "| 间接费用 | 待核 | [A001] |\n"
        "| 工程结算 | 待核 | [A001] |\n"
    )
    reimb = (
        "| 检查项 | 本稿 |\n"
        "| --- | --- |\n"
        "| 发票查验 | 待核 |\n"
        "| 票面与业务 | 待核 |\n"
        "| 审批链 | 待核 |\n"
        "| 附件 | 待核 |\n"
        "| 重复报 / 与项目无关 | 待核 |\n"
        "| 专款范围 | 待核 |\n"
    )
    gaps = (
        "| 台账 | 本稿 |\n"
        "| --- | --- |\n"
        "| 物资收发存 | 缺口待列 |\n"
        "| 分包对上对下 | 缺口待列 |\n"
        "| 机械租赁台班 | 缺口待列 |\n"
        "| 农民工工资专户代发回单 | 缺口待列 |\n"
    )
    lines = [
        "# 项目部核算检查表（AI 草稿）",
        "",
        DISCLAIMER,
        "",
        "内部讨论。不构成记账凭证、审计结论或税务意见。不编会计分录。",
        "",
        f"- 辖区：{zone}",
        f"- 报告期：{period}",
        "",
        "## 用户原文",
        "",
        blob.strip() or "（未提供）",
        "",
    ]
    for i, title in enumerate(_FB_CHAPTERS, 1):
        lines.append(f"## {i} {title}")
        lines.append("")
        if i == 1:
            lines.append(f"项目名称待填。账套主体待填。报告期：{period}。编制人空栏。[A001]")
        elif i == 2:
            lines.append("内部讨论。不构成记账凭证、审计结论或税务意见。本稿不下平账结论。")
        elif i == 3:
            lines.append("以施工合同或内部承包责任书为对象。用户未给合同编号则对象待填。")
        elif i == 4:
            lines.append("只列科目与成本项目名称，不写借贷。企业现行账套不同则以用户科目表为准，禁止擅自改账。")
            lines.append("")
            lines.append(subjects)
        elif i == 5:
            lines.append("验工计价确认的形象进度不是自动入账依据。无业主/监理签认的结算单则工程结算侧待填。")
        elif i == 6:
            lines.append("逐票或逐单勾选。缺一项即退回业务部门，不代补。税额栏不由核算岗计算。")
            lines.append("")
            lines.append(reimb)
        elif i == 7:
            lines.append("专项核算、不得挤占挪用。只问是否落在使用范围名称内。提取比例、分录待用户或制度文本，此处不填数字。")
        elif i == 8:
            lines.append(gap_note)
            lines.append("")
            lines.append(gaps)
            lines.append("")
            lines.append("无仓库盘点表不编盈亏。金额 [A001]。")
        elif i == 9:
            lines.append("无来源金额一律 [A001]。未写任何借贷分录。本稿不下平账结论，也不下入账结论。")
        else:
            lines.append("不编会计分录。不编税负。无合同、无计量单、无发票原件的金额栏全部 [A001]。")
        lines.append("")
    lines.append("[A001] 无来源金额待填。不编分录。禁止编造平账结论。")
    if zone in ("SG", "DUAL"):
        lines.append("SG：GST / 账套口径以 IRAS / ACRA 原文为准。")
    if zone in ("CN", "DUAL"):
        lines.append("CN：增值税法 / 会计法只写全名。施工企业会计核算办法只写全名。")
    lines.append("")
    return "\n".join(lines)


def _parse_fund_windows(blob: str) -> tuple:
    income: List[str] = []
    spend: List[str] = []
    for piece in (blob or "").replace("；", "\n").replace(";", "\n").splitlines():
        t = piece.strip()
        t = re.sub(r"^写一份\S*\s*", "", t).strip()
        t = re.sub(r"^(资金计划草稿|资金计划)\s*", "", t).strip()
        if not t or t.lower() in _FF_SKIP or t in _FF_SKIP:
            continue
        if t.startswith("#") or t.startswith("内部"):
            continue
        if t in {"JGJ", "SAC", "CN", "SG", "DUAL"}:
            continue
        if _FB_PERIOD.search(t) and len(t) <= 12:
            continue
        raw = t
        is_in = any(k in t for k in ("收入", "到账", "预付款", "进度款", "结算款", "质保金", "拨款"))
        is_out = any(k in t for k in ("支出", "付款", "材料", "分包", "工资", "机械", "现场经费", "安措"))
        for k in ("收入", "支出", "付款"):
            t = t.replace(k, "")
        t = re.sub(r"\s+", " ", t).strip(" ，,;；") or raw
        if len(t) > 40:
            t = t[:40]
        if is_in and not is_out:
            income.append(t)
        elif is_out:
            spend.append(t)
    return income, spend


def _fund_plan_md(text: str) -> str:
    blob = text or ""
    zone = _mix_zone(blob)
    period = _fb_period(blob)
    income, spend = _parse_fund_windows(blob)
    if not income:
        inc_tbl = (
            "| 节点名称 | 计划日 | 到账账户 | 状态 | 金额 |\n"
            "| --- | --- | --- | --- | --- |\n"
            "| 业主到账 | 待填 | 待填 | 未报量 | TBD |\n"
            "| 质保金返还 | 待填 | 待填 | 待填 | TBD |\n"
            "| 公司拨款 | 待填 | 待填 | 待填 | TBD |\n"
        )
    else:
        inc_tbl = (
            "| 节点名称 | 计划日 | 到账账户 | 状态 | 金额 |\n"
            "| --- | --- | --- | --- | --- |\n"
            + "".join(f"| {n} | 待填 | 待填 | 待填 | TBD |\n" for n in income)
        )
    if not spend:
        exp_tbl = (
            "| 事项 | 账户 | 是否专户 | 金额 |\n"
            "| --- | --- | --- | --- |\n"
            "| 材料 | 基本结算户 | 否 | TBD |\n"
            "| 人工费代发 | 农民工工资专用账户 | 是 | TBD |\n"
            "| 现场经费 | 基本结算户 | 否 | TBD |\n"
        )
    else:
        exp_tbl = (
            "| 事项 | 账户 | 是否专户 | 金额 |\n"
            "| --- | --- | --- | --- |\n"
            + "".join(
                (
                    f"| {n} | 农民工工资专用账户 | 是 | TBD |\n"
                    if any(k in n for k in ("工资", "人工"))
                    else f"| {n} | 基本结算户 | 否 | TBD |\n"
                )
                for n in spend
            )
        )
    lines = [
        "# 项目资金计划草稿（AI 草稿）",
        "",
        DISCLAIMER,
        "",
        "内部讨论。不构成付款指令、银行划款依据或融资承诺。",
        "",
        f"- 辖区：{zone}",
        f"- 计划期：{period}",
        "",
        "## 用户原文",
        "",
        blob.strip() or "（未提供）",
        "",
    ]
    for i, title in enumerate(_FF_CHAPTERS, 1):
        lines.append(f"## {i} {title}")
        lines.append("")
        if i == 1:
            lines.append(f"项目待填。币种待填。计划期：{period}。编制人空栏。[A001]")
        elif i == 2:
            lines.append("内部讨论。不构成付款指令、银行划款依据或融资承诺。")
        elif i == 3:
            lines.append("以收定支、量入为出、专户分账。没有预计收款节点，整表待填。")
        elif i == 4:
            lines.append(
                "预付款、进度款、竣工结算款、质量保证金：只列名称与日期栏。"
                "时限与比例以本合同及现行办法为准，禁止默写百分比。"
            )
        elif i == 5:
            lines.append("每笔写节点、计划日、账户、状态。无证据则待填。索赔意向不计入可支。")
            lines.append("")
            lines.append(inc_tbl)
            lines.append("")
            lines.append("金额 TBD。未把验工金额当成已到账。")
        elif i == 6:
            lines.append("分账户，禁止混户。专户资金不用于材料款或其他工程款。")
            lines.append("")
            lines.append(exp_tbl)
            lines.append("")
            lines.append("金额 TBD。无三方协议则专户栏待填。")
        elif i == 7:
            lines.append(
                "| 期初可用 | 计划收款 | 计划付款 | 期末 |\n"
                "| --- | --- | --- | --- |\n"
                "| TBD | TBD | TBD | 无法试算 |"
            )
            lines.append("")
            lines.append("四格都缺数就写无法试算，不要编现金。")
        elif i == 8:
            lines.append("计量未签认则进度款计划日待填。专户未开则工资支出不得列入可付。无三方协议则专户栏待填。")
        elif i == 9:
            lines.append("未把商务验工金额当成已到账。本稿不是银行划款依据。不承诺垫资。")
        else:
            lines.append("无合同节点不编付款承诺。不编利率、融资方案。不把工资专户余额拿去平衡材料缺口。")
        lines.append("")
    lines.append("[A001] 无合同节点则整表待填。金额 TBD。本稿不是付款指令。")
    if zone in ("SG", "DUAL"):
        lines.append("SG：CPF 缴交义务只写 CPF Board 标题。GST 备付只列申报期空栏。")
    if zone in ("CN", "DUAL"):
        lines.append("CN：工资专户 / 农民工工资条例只写标题。")
    lines.append("")
    return "\n".join(lines)


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
    blob = text or ""
    zone = _mix_zone(blob)
    job = _wb_job(blob)
    mm = _wb_mm(blob)
    size_line = (
        f"用户给的尺寸：{mm}。只抄这一处，别的尺寸仍按书面交底。"
        if mm
        else "尺寸按书面交底，口播不报未给的尺寸。"
    )
    hazards = []
    for k in ("临边", "洞口", "吊", "电", "基坑", "有限空间", "动火", "交叉"):
        if k in blob:
            hazards.append(k)
    hazard_line = (
        "、".join(hazards) + "。先讲会死的，再讲会受伤的。用户没点名的危险源不编。"
        if hazards
        else "临边、洞口、吊物下、用电：用户没点名的危险源不编，只点到为止。"
    )
    lines = [
        "# 班前白话稿（AI 草稿 · 内部讨论）",
        "",
        DISCLAIMER,
        "",
        "3 分钟口播讨论稿。不是交底签认件，不能代替书面安全技术交底和班组签字。",
        "",
        f"- 辖区：{zone}",
        "",
        "## 用户原文",
        "",
        blob.strip() or "（未提供）",
        "",
    ]
    for i, title in enumerate(_WB_CHAPTERS, 1):
        lines.append(f"## {i} {title}")
        lines.append("")
        if i == 1:
            lines.append(f"今天：{job}。一两句人话。禁止全面推进主体结构。")
        elif i == 2:
            lines.append(hazard_line)
            lines.append("")
            lines.append(size_line)
        elif i == 3:
            lines.append("先看防护齐不齐。再干活。活完盖好、清场、断电。特殊工种无证不干。起重信号不明不吊。高处：帽、带、挂点。")
            lines.append("")
            lines.append(size_line)
        elif i == 4:
            lines.append("防护没了、指挥乱了、身体不舒服、看不懂，先停。班组长可以喊停全班。找不到人就看门口告示牌。电话待填。")
        else:
            lines.append("没听懂再问，不丢人。问完再上。口播代替不了书面交底和签字。")
        lines.append("")
    lines.append("[A001] 缺部位待填。无尺寸不报未给的尺寸。本稿不是交底签认件。")
    if zone in ("SG", "DUAL"):
        lines.append("SG：toolbox meeting 导则只写标题。")
    if zone in ("CN", "DUAL"):
        lines.append("CN：班前会不是安全技术交底。")
    lines.append("")
    return "\n".join(lines)


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
    found: Dict[str, str] = {}
    seen: Dict[str, set] = {}
    matches = list(_PD_FIELD_RE.finditer(blob or ""))
    for index, match in enumerate(matches):
        key = _PD_NAMED_FIELDS.get(match.group(1))
        if key is None:
            continue
        end = matches[index + 1].start() if index + 1 < len(matches) else len(blob)
        value = blob[match.end():end]
        for separator in re.finditer(r"[；;\r\n]", value):
            if separator.group() == ";" and re.search(r"&(?:#\d+|#x[\da-fA-F]+|[A-Za-z][A-Za-z0-9]*);$", value[:separator.end()]):
                continue
            value = value[:separator.start()]
            break
        value = value.strip(" ,，；")
        if not value or re.fullmatch(r"[（(]?(?:待填|未填|未提供|未知|未说明|不确定|UNSPECIFIED|TBD|N/A|空)[）)]?[。.]?", value, re.I):
            continue
        if value not in seen.setdefault(key, set()):
            found[key] = f"{found[key]}；{value}" if key in found else value
            seen[key].add(value)
    return found


def _pm_daily_md(text: str) -> str:
    blob = text or ""
    zone = _mix_zone(blob)
    site = _pd_site(blob)
    weather = _pd_weather(blob)
    labor = _pd_labor(blob)
    named = _pd_named_fields(blob)
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
    blob = text or ""
    zone = _hr_zone(blob)
    role = _hr_role(blob)
    pay = _hr_pay(blob)
    duties = _hr_duties(role, zone)
    qual = _hr_qual()
    interview = _hr_interview()
    four = (
        "| 栏 | 本稿 |\n"
        "| --- | --- |\n"
        f"| 职责 | {role} |\n"
        "| 任职 | 门槛栏待核，不圈已符合 |\n"
        "| 面试问法 | 行为面+专业面，无标准答案 |\n"
        f"| 薪资 | {pay} |\n"
    )
    lines = [
        "# 招聘简报（AI 草稿 · 内部讨论）",
        "",
        DISCLAIMER,
        "",
        "岗位说明书 + 面试提纲。不是录用通知，不是薪酬批复。",
        "",
        f"- 辖区：{zone}",
        "",
        "## 用户原文",
        "",
        blob.strip() or "（未提供）",
        "",
        four,
        "",
        "## 岗位",
        "",
        role,
        "",
    ]
    for title, body in zip(_HR_CHAPTERS, (duties, qual, interview)):
        lines.append(f"## {title}")
        lines.append("")
        lines.append(body)
        lines.append("")
    lines.append("## 薪资")
    lines.append("")
    lines.append(pay)
    lines.append("")
    lines.append("[A001] 用户没给报酬则整栏待填。本稿不是录用通知。")
    if zone in ("SG", "DUAL"):
        lines.append(
            "SG：Fair Consideration Framework / Key Employment Terms 只写标题。"
            "MyCareersFuture 广告连续 14 日只写标题，不编录用。"
        )
    if zone in ("CN", "DUAL"):
        lines.append("CN：劳动合同法招用告知口径只写标题，不编薪资。")
    lines.append("")
    return "\n".join(lines)


def _dispatch_daily_md(text: str) -> str:
    jobs = _copy_sensitive_jobs(text)
    sensitive = (
        "\n".join(f"- {j}（只列名称；判定交 method-hazard）" for j in jobs)
        if jobs
        else "- （本轮用户未点名敏感作业。判定仍交 method-hazard，本岗不判危大。）"
    )
    lines = [
        "# 调度日报草稿（AI）",
        "",
        DISCLAIMER,
        "",
        "不是调度令、停复工令或工期承诺。缺数 [A001]。本日报不是开工许可。",
        "",
        "## 用户原文",
        "",
        (text or "").strip() or "（未提供）",
        "",
    ]
    for i, title in enumerate(_DISPATCH_CHAPTERS, 1):
        lines.append(f"## {i} {title}")
        lines.append("")
        if i == 2:
            lines.append(DISCLAIMER)
        elif i == 4:
            lines.append((text or "").strip()[:200] or "待填。[A001]")
        elif i == 9:
            lines.append(sensitive)
            lines.append("")
            lines.append("敏感作业只列名称与时段。是否危大、要否 PTW 交 method-hazard。本岗不签发。")
        else:
            lines.append("待按现场记录填写。[A001] 不编产量、工日、台班。")
        lines.append("")
    lines.append("CN：调度日报不是危大文件。SG：BCA construction site records 只写标题。")
    lines.append("")
    return "\n".join(lines)


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
    lines = [
        "# 专项施工方案讨论提纲（AI 草稿）",
        "",
        DISCLAIMER,
        "",
        f"不是法定专项方案，不是签认件。缺数 [A001]。条款 UNSPECIFIED。辖区：{_mix_zone(text)}。",
        "",
        "## 用户原文",
        "",
        (text or "").strip() or "（未提供）",
        "",
    ]
    for i, title in enumerate(_SCHEME_CHAPTERS, 1):
        lines.append(f"## {i} {title}")
        lines.append("")
        if i == 2:
            lines.append(DISCLAIMER)
        elif i == 10:
            lines.append("验收结论待持证人员。本页不给合格结论。")
        else:
            lines.append("待按项目 pack / 图纸填写。[A001]")
        lines.append("")
    return "\n".join(lines)


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
    if extra:
        result["job_files"] = True
    return result


# All simple post writers use the same scan, save and export path.
_SIMPLE_DRAFTS = {
    'survey': (_survey_record_md, 'survey__record', '已出测量记录草稿。只抄已给点号。submit_blocked=true。'),
    'dispatch': (_dispatch_daily_md, 'dispatch__daily', '已出调度日报草稿。敏感作业交 method-hazard。submit_blocked=true。'),
    'variation': (_variation_form_md, 'variation__form', '已出变更签证草稿。金额 TBD。submit_blocked=true。'),
    'claim': (_claim_notice_md, 'claim__notice', '已出索赔意向草稿。条款原文待贴。工期金额 TBD。submit_blocked=true。'),
    'subcontract': (_subcontract_sheet_md, 'subcontract__sheet', '已出分包结算表头。无总包/业主确认金额 TBD。submit_blocked=true。'),
    'interim': (_interim_measure_md, 'interim__measure', '已出验工计价草稿。监理审/业主核空栏。不编应付合价。submit_blocked=true。'),
    'plan-master': (_plan_master_md, 'plan-master__network', '已出总控计划草稿。关键线路=待计算。submit_blocked=true。'),
    'plan-lookahead': (_plan_lookahead_md, 'plan-lookahead__week', '已出四周滚动草稿。制约未清不得写入本周承诺。submit_blocked=true。'),
    'plan-resource': (_plan_resource_md, 'plan-resource__peak', '已出资源负荷三表。数量待填。submit_blocked=true。'),
    'lab-mix': (_lab_mix_md, 'lab-mix__report', '已出配比报告提纲。无试验数据不给施工配合比。submit_blocked=true。'),
    'lab-sample': (_lab_sample_md, 'lab-sample__list', '已出取样送检清单。见证人空栏。组数 [A001]。submit_blocked=true。'),
    'lab-record': (_lab_record_md, 'lab-record__ledger', '已出试验台账骨架。报告编号待核。结论待填。submit_blocked=true。'),
    'supervision': (_supervision_md, 'supervision__reply', '已出监理通知回复草稿。暂停/复工只出目录。submit_blocked=true。'),
    'safety-brief': (_safety_brief_md, 'safety-brief__talk', '已出安全交底草稿。毫米/电话 [A001]。submit_blocked=true。'),
    'quality': (_quality_md, 'quality__lot', '已出质量检查表。主控/一般/隐蔽结果=未检。submit_blocked=true。'),
    'env': (_env_md, 'env__list', '已出环保文明清单。五行限值 UNSPECIFIED。submit_blocked=true。'),
    'emergency': (_emergency_md, 'emergency__plan', '已出应急预案提纲。电话医院待填。submit_blocked=true。'),
    'equip': (_equip_md, 'equip__ledger', '已出设备台账。只抄用户设备名与已给证件。无证件不编进场结论。submit_blocked=true。'),
    'warehouse': (_warehouse_md, 'warehouse__log', '已出收发存台账。有数只抄。无盘点不编盈亏。submit_blocked=true。'),
    'material-site': (_material_site_md, 'material-site__recon', '已出材料核算表头。算不出节超则 TBD。submit_blocked=true。'),
    'proc-plan': (_proc_plan_md, 'proc-plan__schedule', '已出采购计划表。先分甲供/甲指/自采。提前期 UNSPECIFIED。submit_blocked=true。'),
    'proc-vendor': (_vendor_eval_md, 'proc-vendor__eval', '已出供方评价表头。准入/考察/短名单。分数结论待核。submit_blocked=true。'),
    'finance-book': (_finance_book_md, 'finance-book__check', '已出核算检查表。报销勾选/科目对照/对账缺口。金额 [A001]。submit_blocked=true。'),
    'finance-fund': (_fund_plan_md, 'finance-fund__plan', '已出资金计划草稿。收入/支出窗口。金额 TBD。不是付款指令。submit_blocked=true。'),
    'worker-brief': (_worker_brief_md, 'worker-brief__talk', '已出班前白话稿。三段口播。无尺寸不报未给的尺寸。submit_blocked=true。'),
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
    out_dir = _out_root() / session_id / expert.id
    out_dir.mkdir(parents=True, exist_ok=True)
    files: List[Dict[str, str]] = []
    ran: List[str] = []

    if expert.id == "bid-parse":
        from packing_assistant.runtime.session_handoff import save_handoff
        from packing_assistant.tools.tender_parse import run_tender_pipeline

        pipe = run_tender_pipeline(text, source="expert-turn", project_name=expert.name)
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
            "reply": "已按招标解析岗抽出表，并落下 tender.handoff.json 供后岗读。仍是 AI 草稿，submit_blocked=true。",
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

        ho = load_handoff(session_id)
        if text and len(text.strip()) > 40:
            pipe = run_tender_pipeline(text, source="expert-tech", project_name=expert.name)
            fresh = pipe.get("handoff") if isinstance(pipe.get("handoff"), dict) else {}
            said = fresh.get("facts") or {}
            # 这一轮自己带了评分点 / 专项 / 工程情况，就按这一轮排目录。此前只要会话里有旧交接就不看
            # 本轮文字：换了一份招标文件再问，出来的还是上一份的目录。只有「出一份技术标目录」这种
            # 不带内容的话才沿用本会话 bid-parse 落下的交接。
            carries = bool(fresh.get("scoring_points") or fresh.get("specials") or said.get("scores")
                           or said.get("specials") or said.get("mentions"))
            if fresh and (carries or not ho):
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
                "已按抽出评分点排技术标目录。"
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
        blob = text or ""
        zone = _mix_zone(blob)
        depth = "未提供"
        height = "未提供"
        md = (
            f"# 危大判定书（AI 草稿）\n\n{DISCLAIMER}\n\n"
            f"- 辖区：{zone}\n"
            f"- 作业名称：{blob[:80] or '未说明作业'}\n"
            f"- 触发词：临边 / 开挖 / 起重（仅当用户写了才勾）\n"
            f"- 是否危大：信息不足\n"
            f"- 是否可能超规模需论证：信息不足\n"
            f"- 高度 m：{height}\n- 开挖深度 m：{depth}\n"
        )
        if zone in {"SG", "DUAL"}:
            md += (
                "- 依据：Workplace Safety and Health Act / WSH (Construction) Regulations 2007 PTW。"
                "需按对应辖区核实。\n"
                "- 建议下一步：交施工方案专家出讨论提纲。本岗不签发 PTW。\n"
            )
        if zone in {"CN", "DUAL"}:
            md += (
                "- 依据：住建部令第 37 号要点 + 用户尺寸（无尺寸则信息不足）。\n"
                "- 建议下一步：交施工方案专家出讨论提纲。\n"
            )
        if zone not in {"CN", "SG", "DUAL"}:
            md += "- 依据：UNSPECIFIED，须补充项目辖区及适用文件后核实。\n"
        md += "\n本岗不签发、不给开工许可。条款 UNSPECIFIED。\n"
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
        path = out_dir / "method-hazard__judge.md"
        guarded_write_text(path, md)
        files.append({"name": path.name, "path": str(path), "tool": "method-hazard__judge_hazard"})
        ran.append("method-hazard__judge_hazard")
        return {
            "wrote": True,
            "hitl_pending": False,
            "files": files,
            "tools_run": ran,
            "reply": "已出判定讨论卡。不是签发件，submit_blocked=true。",
            "submit_blocked": True,
        }

    if expert.id == "finance-tax":
        from packing_assistant.tax_context import draft_tax
        return _save_drafts(
            out_dir, [("finance-tax__calendar", draft_tax(text, DISCLAIMER))],
            "已出税务日历草稿。未核验的辖区、税率及申报节点保持 UNSPECIFIED，税额待专业复核。submit_blocked=true。",
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
        md = (
            f"# 工程量拆分表（AI 草稿）\n\n{DISCLAIMER}\n\n"
            "| 分项 | 单位 | 数量 | 综合单价 | 合价 | 来源 |\n| --- | --- | --- | --- | --- | --- |\n"
            f"| {text.strip()[:80] or '未提供分项 [A001]'} | TBD | TBD | UNSPECIFIED | UNSPECIFIED | 用户表 |\n\n"
            "无清单/报价不编单价。条款 UNSPECIFIED。\n"
        )
        path = out_dir / "cost__takeoff.md"
        guarded_write_text(path, md)
        files.append({"name": path.name, "path": str(path), "tool": "cost__takeoff"})
        ran.append("cost__takeoff")
        return {
            "wrote": True,
            "hitl_pending": False,
            "files": files,
            "tools_run": ran,
            "reply": "已出工程量拆分表。单价 UNSPECIFIED。submit_blocked=true。",
            "submit_blocked": True,
        }

    if expert.id == "construction":
        md = _construction_eleven(text)
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
                "p0_reject_scan": {"hits": hits},
            }
        path = out_dir / "construction__scheme_draft.md"
        guarded_write_text(path, md)
        files.append({"name": path.name, "path": str(path), "tool": "construction__scheme_draft"})
        ran.append("construction__scheme_draft")
        fill = _try_fill_scheme_docx(out_dir, (text or "").strip()[:40] or "未命名工程", _mix_zone(text))
        pending = bool(fill.get("docx_pending", True))
        if fill.get("docx"):
            dp = Path(str(fill["docx"]))
            files.append({"name": dp.name, "path": str(dp), "tool": "construction__fill_scheme_docx"})
            ran.append("construction__fill_scheme_docx")
        reply = (
            "已出十一章讨论提纲并填 docx。不是法定专项，submit_blocked=true。"
            if not pending
            else "已出十一章讨论提纲（docx_pending）。不是法定专项，submit_blocked=true。"
        )
        out: Dict[str, Any] = {
            "wrote": True,
            "hitl_pending": False,
            "files": files,
            "tools_run": ran,
            "reply": reply,
            "submit_blocked": True,
            "docx_pending": pending,
        }
        if fill.get("p0_reject_scan"):
            out["p0_reject_scan"] = fill["p0_reject_scan"]
        return out

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
            label = {"site": "部位", "weather": "天气", "labor": "出勤", "attendance": "出勤"}.get(k)
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
