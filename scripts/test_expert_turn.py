#!/usr/bin/env python3
"""All 66 experts share understand → chat | run. Questions do not write."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main() -> int:
    from packing_assistant.expert_roster import exclusive_tools, get_expert, list_experts
    from packing_assistant.expert_turn import run_expert_turn
    from packing_assistant.understand import understand

    roster = list_experts()
    assert len(roster) == 66, len(roster)
    assert all(e.exclusive for e in roster), [e.id for e in roster if not e.exclusive]
    assert get_expert("finance-tax") and get_expert("method-hazard") and get_expert("pack-ship")

    q = "什么是 GST"
    assert understand(q) == "chat"
    chat = run_expert_turn(q, "finance-tax")
    assert chat["intent"] == "chat" and chat["wrote"] is False
    assert chat["files"] == []
    assert "UNSPECIFIED" in chat["reply"]
    assert "9%" not in chat["reply"]
    assert "可以开工" not in chat["reply"]

    hz = run_expert_turn("临边防护算不算危大？要不要专家论证？", "method-hazard")
    assert hz["intent"] == "chat" and hz["wrote"] is False

    hz_block = run_expert_turn(
        "写一份危大判定书 临边开挖",
        "method-hazard",
        confirm_ok=False,
        force_intent="run",
    )
    assert hz_block["wrote"] is False and hz_block.get("hitl_pending") is True
    hz_ok = run_expert_turn(
        "写一份危大判定书 临边开挖\nSG",
        "method-hazard",
        confirm_ok=True,
        force_intent="run",
        session_id="t002-mh-sg",
    )
    assert hz_ok["wrote"] is True
    hz_text = Path(hz_ok["files"][0]["path"]).read_text(encoding="utf-8")
    assert "WSH" in hz_text and "PTW" in hz_text
    assert "37 号令" not in hz_text
    assert "可以开工" not in hz_text
    assert "信息不足" in hz_text
    hz_cn = run_expert_turn(
        "写一份危大判定书 37 号令 基坑",
        "method-hazard",
        confirm_ok=True,
        force_intent="run",
        session_id="t002-mh-cn",
    )
    cn_text = Path(hz_cn["files"][0]["path"]).read_text(encoding="utf-8")
    assert "37 号令" in cn_text
    assert "可以开工" not in cn_text

    cal = run_expert_turn("出一份税务日历", "finance-tax")
    assert cal["intent"] == "run" and cal["wrote"] is True
    assert "finance-tax__calendar" in cal["tools_run"]
    assert cal["files"] and Path(cal["files"][0]["path"]).is_file()
    cal_text = Path(cal["files"][0]["path"]).read_text(encoding="utf-8")
    assert "UNSPECIFIED" in cal_text
    assert "9%" not in cal_text
    assert "申报期" in cal_text and "待核" in cal_text
    assert "待填" in cal_text

    cost = run_expert_turn("写一份工程量拆分表 临边栏杆", "cost", session_id="t006-cost")
    assert cost["wrote"] is True
    assert "cost__takeoff" in cost["tools_run"]
    cost_text = Path(cost["files"][0]["path"]).read_text(encoding="utf-8")
    assert "综合单价" in cost_text and "合价" in cost_text
    assert "UNSPECIFIED" in cost_text

    sv_chat = run_expert_turn("测量点号怎么填？", "survey")
    assert sv_chat["intent"] == "chat" and sv_chat["wrote"] is False
    sv_empty = run_expert_turn(
        "写一份测量记录",
        "survey",
        confirm_ok=True,
        force_intent="run",
        session_id="t030-sv-empty",
    )
    assert sv_empty["wrote"] is True
    assert "survey__record" in sv_empty["tools_run"]
    sv_empty_text = Path(sv_empty["files"][0]["path"]).read_text(encoding="utf-8")
    for title in (
        "封面与文件控制",
        "已知起算",
        "放样内容",
        "复测与检核",
        "附录",
    ):
        assert title in sv_empty_text, title
    assert "[A001]" in sv_empty_text
    assert "CP99" not in sv_empty_text
    assert "可以开工" not in sv_empty_text
    sv_ok = run_expert_turn(
        "写一份测量记录 点号 CP01 东 12345.67 北 23456.89",
        "survey",
        confirm_ok=True,
        force_intent="run",
        session_id="t030-sv-cp01",
    )
    sv_ok_text = Path(sv_ok["files"][0]["path"]).read_text(encoding="utf-8")
    assert "CP01" in sv_ok_text
    assert "12345.67" in sv_ok_text
    assert "23456.89" in sv_ok_text

    dp_chat = run_expert_turn("调度日报怎么写？", "dispatch")
    assert dp_chat["intent"] == "chat" and dp_chat["wrote"] is False
    dp = run_expert_turn(
        "写一份调度日报 临边防护 白班",
        "dispatch",
        force_intent="run",
        session_id="t030-dp",
    )
    assert dp["wrote"] is True
    assert "dispatch__daily" in dp["tools_run"]
    dp_text = Path(dp["files"][0]["path"]).read_text(encoding="utf-8")
    for title in (
        "报头",
        "当日实际",
        "危大/高处/临边等敏感作业清单",
        "明日条件与待决策",
        "附件表头",
    ):
        assert title in dp_text, title
    assert "临边" in dp_text
    assert "method-hazard" in dp_text
    assert "可以开工" not in dp_text

    var_chat = run_expert_turn("签证单怎么填？", "variation")
    assert var_chat["intent"] == "chat" and var_chat["wrote"] is False
    var_empty = run_expert_turn(
        "写一份变更签证草稿",
        "variation",
        force_intent="run",
        session_id="t031-var-empty",
    )
    assert var_empty["wrote"] is True
    assert "variation__form" in var_empty["tools_run"]
    ve = Path(var_empty["files"][0]["path"]).read_text(encoding="utf-8")
    for title in ("文件类型判定", "事实栏", "依据栏", "签认栏", "自检"):
        assert title in ve, title
    assert "工程签证" in ve
    assert "变更编号待填" in ve or "待填" in ve
    assert "见图" not in ve
    assert "TBD" in ve
    assert "可以开工" not in ve
    var_no = run_expert_turn(
        "写一份变更签证 临边栏杆 变更编号 VO-12",
        "variation",
        force_intent="run",
        session_id="t031-var-vo",
    )
    vn = Path(var_no["files"][0]["path"]).read_text(encoding="utf-8")
    assert "VO-12" in vn
    assert "见图" not in vn

    cl_chat = run_expert_turn("索赔意向怎么写？", "claim")
    assert cl_chat["intent"] == "chat" and cl_chat["wrote"] is False
    cl_empty = run_expert_turn(
        "写一份索赔意向草稿",
        "claim",
        force_intent="run",
        session_id="t031-cl-empty",
    )
    assert cl_empty["wrote"] is True
    assert "claim__notice" in cl_empty["tools_run"]
    ce = Path(cl_empty["files"][0]["path"]).read_text(encoding="utf-8")
    for title in ("事件识别", "合同时钟", "意向通知必备", "证据清单", "调概专节"):
        assert title in ce, title
    assert "条款原文待贴" in ce
    assert "TBD" in ce
    assert "可以开工" not in ce
    cl_ev = run_expert_turn(
        "写一份索赔意向 图纸迟到 证据：监理通知 NCR-1；停工令 8 月 1 日",
        "claim",
        force_intent="run",
        session_id="t031-cl-ev",
    )
    cv = Path(cl_ev["files"][0]["path"]).read_text(encoding="utf-8")
    assert "监理通知 NCR-1" in cv or "NCR-1" in cv
    assert "停工令" in cv

    sub_chat = run_expert_turn("分包结算怎么填？", "subcontract")
    assert sub_chat["intent"] == "chat" and sub_chat["wrote"] is False
    sub_empty = run_expert_turn(
        "写一份分包结算草稿",
        "subcontract",
        force_intent="run",
        session_id="t031-sub-empty",
    )
    assert sub_empty["wrote"] is True
    assert "subcontract__sheet" in sub_empty["tools_run"]
    se = Path(sub_empty["files"][0]["path"]).read_text(encoding="utf-8")
    for title in ("本期完成", "合同内价款栏", "扣款表头", "农民工工资专节", "会签栏"):
        assert title in se, title
    assert "TBD" in se
    assert "可以开工" not in se
    sub_ok = run_expert_turn(
        "写一份分包结算 模板 120m2；钢筋 2.5t",
        "subcontract",
        force_intent="run",
        session_id="t031-sub-rows",
    )
    so = Path(sub_ok["files"][0]["path"]).read_text(encoding="utf-8")
    assert "模板" in so
    assert "120" in so
    assert "钢筋" in so
    assert "2.5" in so
    assert so.count("TBD") >= 2

    it_chat = run_expert_turn("验工计价怎么填？", "interim")
    assert it_chat["intent"] == "chat" and it_chat["wrote"] is False
    it_empty = run_expert_turn(
        "写一份验工计价草稿",
        "interim",
        force_intent="run",
        session_id="t031-it-empty",
    )
    assert it_empty["wrote"] is True
    assert "interim__measure" in it_empty["tools_run"]
    ie = Path(it_empty["files"][0]["path"]).read_text(encoding="utf-8")
    for title in ("本期范围", "计量草表", "监理审", "业主核", "报审签认"):
        assert title in ie, title
    assert "TBD" in ie
    assert "可以开工" not in ie
    assert "报审通过" not in ie
    it_ok = run_expert_turn(
        "写一份验工计价 模板 120m2",
        "interim",
        force_intent="run",
        session_id="t031-it-row",
    )
    io = Path(it_ok["files"][0]["path"]).read_text(encoding="utf-8")
    assert "模板" in io
    assert "120" in io
    assert "应付合价" in io or "不编应付" in io or "不编本期应付" in io

    pm_chat = run_expert_turn("总控计划怎么编？", "plan-master")
    assert pm_chat["intent"] == "chat" and pm_chat["wrote"] is False
    pm_empty = run_expert_turn(
        "写一份总进度计划草稿",
        "plan-master",
        force_intent="run",
        session_id="t032-pm-empty",
    )
    assert pm_empty["wrote"] is True
    assert "plan-master__network" in pm_empty["tools_run"]
    pe = Path(pm_empty["files"][0]["path"]).read_text(encoding="utf-8")
    for title in ("工作分解", "逻辑关系", "一级网络与里程碑", "关键线路"):
        assert title in pe, title
    assert "WBS" in pe
    assert "紧前" in pe
    assert "里程碑待填" in pe or "待填" in pe
    assert "关键线路=待计算" in pe or "关键线路待计算" in pe
    assert "可以开工" not in pe
    pm_ok = run_expert_turn(
        "写一份总控计划 主体结构；封顶",
        "plan-master",
        force_intent="run",
        session_id="t032-pm-wbs",
    )
    po = Path(pm_ok["files"][0]["path"]).read_text(encoding="utf-8")
    assert "主体结构" in po
    assert "封顶" in po

    pl_chat = run_expert_turn("周计划怎么编？", "plan-lookahead")
    assert pl_chat["intent"] == "chat" and pl_chat["wrote"] is False
    pl_empty = run_expert_turn(
        "写一份四周滚动计划",
        "plan-lookahead",
        force_intent="run",
        session_id="t032-pl-empty",
    )
    assert pl_empty["wrote"] is True
    assert "plan-lookahead__week" in pl_empty["tools_run"]
    ple = Path(pl_empty["files"][0]["path"]).read_text(encoding="utf-8")
    for title in ("封面", "从总控抽取窗口", "近细远粗", "制约因素", "周承诺"):
        assert title in ple, title
    assert "第1周" in ple and "第4周" in ple
    assert "制约未清" in ple
    assert "不得写入本周承诺" in ple
    assert "可以开工" not in ple
    assert "可以复工" not in ple
    assert "一定完成" not in ple
    pl_block = run_expert_turn(
        "写一份周计划 3层砌筑；塔吊未到",
        "plan-lookahead",
        force_intent="run",
        session_id="t032-pl-block",
    )
    pb = Path(pl_block["files"][0]["path"]).read_text(encoding="utf-8")
    assert "3层砌筑" in pb
    assert "塔吊未到" in pb
    sec_b = pb.split("## 5 周承诺")[1].split("## 6")[0]
    assert "不得写入本周承诺" in sec_b
    assert "3层砌筑" not in sec_b
    pl_ok = run_expert_turn(
        "写一份周计划 3层砌筑；制约已清",
        "plan-lookahead",
        force_intent="run",
        session_id="t032-pl-ok",
    )
    plo = Path(pl_ok["files"][0]["path"]).read_text(encoding="utf-8")
    assert "3层砌筑" in plo
    sec_ok = plo.split("## 5 周承诺")[1].split("## 6")[0]
    assert "3层砌筑" in sec_ok
    assert "不得写入本周承诺" not in sec_ok
    assert "可以复工" not in plo

    pr_chat = run_expert_turn("资源负荷怎么编？", "plan-resource")
    assert pr_chat["intent"] == "chat" and pr_chat["wrote"] is False
    pr_empty = run_expert_turn(
        "写一份资源负荷表",
        "plan-resource",
        force_intent="run",
        session_id="t032-pr-empty",
    )
    assert pr_empty["wrote"] is True
    assert "plan-resource__peak" in pr_empty["tools_run"]
    pre = Path(pr_empty["files"][0]["path"]).read_text(encoding="utf-8")
    for title in ("劳动力负荷表头", "施工机具负荷表头", "主要材料与周转料表头"):
        assert title in pre, title
    assert "TBD" in pre
    assert "可以开工" not in pre
    assert "已满足施工需要" not in pre
    assert "已安排进场" not in pre
    pr_ok = run_expert_turn(
        "写一份资源负荷 木工；塔吊；钢筋",
        "plan-resource",
        force_intent="run",
        session_id="t032-pr-rows",
    )
    pro = Path(pr_ok["files"][0]["path"]).read_text(encoding="utf-8")
    labor_sec = pro.split("## 3 劳动力负荷表头")[1].split("## 4")[0]
    plant_sec = pro.split("## 4 施工机具负荷表头")[1].split("## 5")[0]
    mat_sec = pro.split("## 5 主要材料与周转料表头")[1].split("## 6")[0]
    assert "木工" in labor_sec
    assert "塔吊" in plant_sec
    assert "钢筋" in mat_sec
    assert "TBD" in labor_sec
    pr_qty = run_expert_turn(
        "写一份资源负荷 木工20人",
        "plan-resource",
        force_intent="run",
        session_id="t032-pr-qty",
    )
    pq = Path(pr_qty["files"][0]["path"]).read_text(encoding="utf-8")
    labor_q = pq.split("## 3 劳动力负荷表头")[1].split("## 4")[0]
    assert "木工" in labor_q
    assert "20人" in labor_q
    assert "用户给定" in labor_q

    mx_chat = run_expert_turn("施工配合比怎么换？", "lab-mix")
    assert mx_chat["intent"] == "chat" and mx_chat["wrote"] is False
    mx_hitl = run_expert_turn(
        "写一份配比报告提纲 C40",
        "lab-mix",
        confirm_ok=False,
        force_intent="run",
    )
    assert mx_hitl["wrote"] is False and mx_hitl.get("hitl_pending") is True
    mx_empty = run_expert_turn(
        "写一份配比报告提纲 C40\nSG",
        "lab-mix",
        confirm_ok=True,
        force_intent="run",
        session_id="t033-mx-empty",
    )
    assert mx_empty["wrote"] is True
    assert "lab-mix__report" in mx_empty["tools_run"]
    me = Path(mx_empty["files"][0]["path"]).read_text(encoding="utf-8")
    for title in ("初步（理论）配合比", "基准配合比", "试验室配合比", "施工配合比"):
        assert title in me, title
    assert "C40" in me
    assert "不给施工配合比" in me
    assert "SAC" in me
    assert "JGJ" not in me
    assert "可以开工" not in me
    assert "已具备开盘条件" not in me
    mx_trial = run_expert_turn(
        "写一份配比报告提纲 C40 已有试验数据",
        "lab-mix",
        confirm_ok=True,
        force_intent="run",
        session_id="t033-mx-trial",
    )
    mt = Path(mx_trial["files"][0]["path"]).read_text(encoding="utf-8")
    assert "施工配比数字仍须试验室签认" in mt
    sec4 = mt.split("| 施工配合比 |")[1].split("\n")[0]
    assert "不给施工配合比" not in sec4
    mx_cn = run_expert_turn(
        "写一份配比报告提纲 JGJ C30",
        "lab-mix",
        confirm_ok=True,
        force_intent="run",
        session_id="t033-mx-cn",
    )
    mcn = Path(mx_cn["files"][0]["path"]).read_text(encoding="utf-8")
    assert "辖区：CN" in mcn
    assert "JGJ" in mcn
    assert "SAC" not in mcn
    assert "不给施工配合比" in mcn

    ls_chat = run_expert_turn("见证取样要哪些？", "lab-sample")
    assert ls_chat["intent"] == "chat" and ls_chat["wrote"] is False
    ls_hitl = run_expert_turn(
        "写一份取样送检清单",
        "lab-sample",
        confirm_ok=False,
        force_intent="run",
    )
    assert ls_hitl["wrote"] is False and ls_hitl.get("hitl_pending") is True
    ls_ok = run_expert_turn(
        "写一份取样送检清单 钢筋",
        "lab-sample",
        confirm_ok=True,
        force_intent="run",
        session_id="t033-ls",
    )
    assert ls_ok["wrote"] is True
    assert "lab-sample__list" in ls_ok["tools_run"]
    lst = Path(ls_ok["files"][0]["path"]).read_text(encoding="utf-8")
    for col in ("类别", "部位", "见证人", "升级路径"):
        assert col in lst, col
    assert "钢筋" in lst
    assert "[A001]" in lst
    assert "（空）" in lst
    assert "可以开工" not in lst
    assert "已取样合格" not in lst

    lr_chat = run_expert_turn("试验台账怎么建？", "lab-record")
    assert lr_chat["intent"] == "chat" and lr_chat["wrote"] is False
    lr_ok = run_expert_turn(
        "写一份试验台账 cube-1",
        "lab-record",
        force_intent="run",
        session_id="t033-lr",
    )
    assert lr_ok["wrote"] is True
    assert "lab-record__ledger" in lr_ok["tools_run"]
    lrt = Path(lr_ok["files"][0]["path"]).read_text(encoding="utf-8")
    for col in ("报告编号", "仪器检定", "结论"):
        assert col in lrt, col
    assert "cube-1" in lrt
    assert "待核" in lrt
    assert "待填" in lrt
    assert "可以开工" not in lrt

    sv_chat = run_expert_turn("监理通知怎么回？", "supervision")
    assert sv_chat["intent"] == "chat" and sv_chat["wrote"] is False
    sv_ok = run_expert_turn(
        "写一份监理回复 NCR-1 钢筋保护层",
        "supervision",
        force_intent="run",
        session_id="t034-sv",
    )
    assert sv_ok["wrote"] is True
    assert "supervision__reply" in sv_ok["tools_run"]
    svt = Path(sv_ok["files"][0]["path"]).read_text(encoding="utf-8")
    for col in ("来文要点复述", "拟办", "证据目录"):
        assert col in svt, col
    assert "NCR-1" in svt
    assert "可以开工" not in svt
    assert "可以复工" not in svt
    sv_stop = run_expert_turn(
        "写一份监理回复 暂停令",
        "supervision",
        force_intent="run",
        session_id="t034-sv-stop",
    )
    sst = Path(sv_stop["files"][0]["path"]).read_text(encoding="utf-8")
    assert "只出目录" in sst
    assert "可以复工" not in sst

    sb_chat = run_expert_turn("安全技术交底怎么理解？", "safety-brief")
    assert sb_chat["intent"] == "chat" and sb_chat["wrote"] is False
    sb_hitl = run_expert_turn(
        "写一份安全交底 临边",
        "safety-brief",
        confirm_ok=False,
        force_intent="run",
    )
    assert sb_hitl["wrote"] is False and sb_hitl.get("hitl_pending") is True
    sb_ok = run_expert_turn(
        "写一份安全交底 临边",
        "safety-brief",
        confirm_ok=True,
        force_intent="run",
        session_id="t035-sb",
    )
    assert sb_ok["wrote"] is True
    assert "safety-brief__talk" in sb_ok["tools_run"]
    sbt = Path(sb_ok["files"][0]["path"]).read_text(encoding="utf-8")
    for title in ("封面", "危险源", "防护要点", "应急要点", "签字栏"):
        assert title in sbt, title
    assert "[A001]" in sbt
    assert "电话" in sbt
    assert "可以开工" not in sbt

    q_chat = run_expert_turn("检验批怎么检查？", "quality")
    assert q_chat["intent"] == "chat" and q_chat["wrote"] is False
    q_ok = run_expert_turn(
        "写一份质量检查表 cover",
        "quality",
        confirm_ok=True,
        force_intent="run",
        session_id="t035-q",
    )
    assert q_ok["wrote"] is True
    assert "quality__lot" in q_ok["tools_run"]
    qt = Path(q_ok["files"][0]["path"]).read_text(encoding="utf-8")
    for col in ("主控项目检查栏", "一般项目检查栏", "隐蔽专项"):
        assert col in qt, col
    assert "未检" in qt
    assert "cover" in qt
    assert "可以开工" not in qt

    env_chat = run_expert_turn("扬尘怎么管？", "env")
    assert env_chat["intent"] == "chat" and env_chat["wrote"] is False
    env_ok = run_expert_turn(
        "写一份环保文明清单 Tuas",
        "env",
        force_intent="run",
        session_id="t035-env",
    )
    assert env_ok["wrote"] is True
    assert "env__list" in env_ok["tools_run"]
    et = Path(env_ok["files"][0]["path"]).read_text(encoding="utf-8")
    for row in ("扬尘", "弃土", "污水", "夜间", "市容"):
        assert row in et, row
    assert "UNSPECIFIED" in et
    assert "可以开工" not in et

    em_chat = run_expert_turn("应急预案怎么理解？", "emergency")
    assert em_chat["intent"] == "chat" and em_chat["wrote"] is False
    em_hitl = run_expert_turn(
        "写一份应急预案 fire",
        "emergency",
        confirm_ok=False,
        force_intent="run",
    )
    assert em_hitl["wrote"] is False and em_hitl.get("hitl_pending") is True
    em_ok = run_expert_turn(
        "写一份应急预案 fire",
        "emergency",
        confirm_ok=True,
        force_intent="run",
        session_id="t035-em",
    )
    assert em_ok["wrote"] is True
    assert "emergency__plan" in em_ok["tools_run"]
    emt = Path(em_ok["files"][0]["path"]).read_text(encoding="utf-8")
    for title in ("综合预案目录", "专项预案目录", "演练计划与记录表头"):
        assert title in emt, title
    assert "火灾爆炸" in emt
    assert "本轮点名" in emt
    assert "[A001]" in emt
    assert "电话" in emt
    assert "医院" in emt
    assert "可以开工" not in emt
    assert "演练合格" not in emt

    eq_chat = run_expert_turn("特种设备证件怎么理解？", "equip")
    assert eq_chat["intent"] == "chat" and eq_chat["wrote"] is False
    eq_hitl = run_expert_turn(
        "写一份设备台账 tower crane",
        "equip",
        confirm_ok=False,
        force_intent="run",
    )
    assert eq_hitl["wrote"] is False and eq_hitl.get("hitl_pending") is True
    eq_ok = run_expert_turn(
        "写一份设备台账 tower crane",
        "equip",
        confirm_ok=True,
        force_intent="run",
        session_id="t036-eq",
    )
    assert eq_ok["wrote"] is True
    assert "equip__ledger" in eq_ok["tools_run"]
    eqt = Path(eq_ok["files"][0]["path"]).read_text(encoding="utf-8")
    assert "tower crane" in eqt
    assert "进场验收" in eqt and "证件" in eqt and "维保" in eqt
    assert "无证件不编进场结论" in eqt
    assert "[A001]" in eqt
    assert "可以开工" not in eqt
    eq_cert = run_expert_turn(
        "写一份设备台账 塔吊 合格证 TS-88",
        "equip",
        confirm_ok=True,
        force_intent="run",
        session_id="t036-eq-cert",
    )
    eqc = Path(eq_cert["files"][0]["path"]).read_text(encoding="utf-8")
    assert "塔吊" in eqc
    assert "TS-88" in eqc
    assert "用户给定" in eqc

    wh_chat = run_expert_turn("收发存怎么理解？", "warehouse")
    assert wh_chat["intent"] == "chat" and wh_chat["wrote"] is False
    wh_ok = run_expert_turn(
        "写一份收发存台账 rebar",
        "warehouse",
        force_intent="run",
        session_id="t036-wh",
    )
    assert wh_ok["wrote"] is True
    assert "warehouse__log" in wh_ok["tools_run"]
    wht = Path(wh_ok["files"][0]["path"]).read_text(encoding="utf-8")
    assert "rebar" in wht
    assert "TBD" in wht
    assert "无盘点不编盈亏" in wht
    assert "可以开工" not in wht
    wh_qty = run_expert_turn(
        "写一份收发存台账 钢筋 入库 12吨",
        "warehouse",
        force_intent="run",
        session_id="t036-wh-qty",
    )
    whq = Path(wh_qty["files"][0]["path"]).read_text(encoding="utf-8")
    assert "钢筋" in whq
    assert "12吨" in whq

    ms_chat = run_expert_turn("节超怎么理解？", "material-site")
    assert ms_chat["intent"] == "chat" and ms_chat["wrote"] is False
    ms_ok = run_expert_turn(
        "写一份材料核算 rebar",
        "material-site",
        force_intent="run",
        session_id="t036-ms",
    )
    assert ms_ok["wrote"] is True
    assert "material-site__recon" in ms_ok["tools_run"]
    mst = Path(ms_ok["files"][0]["path"]).read_text(encoding="utf-8")
    assert "rebar" in mst
    assert "应耗" in mst and "领料" in mst and "节超" in mst
    assert "TBD" in mst
    assert "无盘点不编盈亏" in mst
    assert "可以开工" not in mst
    ms_qty = run_expert_turn(
        "写一份材料核算 钢筋 应耗 10吨 领料 12吨",
        "material-site",
        force_intent="run",
        session_id="t036-ms-qty",
    )
    msq = Path(ms_qty["files"][0]["path"]).read_text(encoding="utf-8")
    assert "钢筋" in msq
    assert "10吨" in msq
    assert "12吨" in msq
    sec = msq.split("## 6 核算表头")[1].split("## 7")[0]
    assert "TBD" in sec

    pp_chat = run_expert_turn("提前期怎么理解？", "proc-plan")
    assert pp_chat["intent"] == "chat" and pp_chat["wrote"] is False
    pp_ok = run_expert_turn(
        "写一份采购计划 rebar\nSG",
        "proc-plan",
        force_intent="run",
        session_id="t037-pp",
    )
    assert pp_ok["wrote"] is True
    assert "proc-plan__schedule" in pp_ok["tools_run"]
    ppt = Path(pp_ok["files"][0]["path"]).read_text(encoding="utf-8")
    assert "rebar" in ppt
    assert "甲供" in ppt and "甲指" in ppt and "自采" in ppt
    assert "待划" in ppt
    assert "UNSPECIFIED" in ppt
    assert "[A001]" in ppt
    assert "CRS" in ppt
    assert "可以开工" not in ppt
    assert "一律提前" not in ppt
    pp_split = run_expert_turn(
        "写一份采购计划 钢筋 甲供；水泥 自采",
        "proc-plan",
        force_intent="run",
        session_id="t037-pp-split",
    )
    pps = Path(pp_split["files"][0]["path"]).read_text(encoding="utf-8")
    assert "钢筋" in pps
    assert "水泥" in pps
    gong = pps.split("### 甲供")[1].split("###")[0]
    zi = pps.split("### 自采")[1].split("###")[0]
    assert "钢筋" in gong
    assert "水泥" in zi

    pc_chat = run_expert_turn("询价和比价怎么理解？", "proc-compare")
    assert pc_chat["intent"] == "chat" and pc_chat["wrote"] is False
    pc_ok = run_expert_turn(
        "写一份比价表 rebar\nSG",
        "proc-compare",
        force_intent="run",
        session_id="t037-pc",
    )
    assert pc_ok["wrote"] is True
    assert "proc-compare__table" in pc_ok["tools_run"]
    assert "procurement__scan_forbidden" in pc_ok["tools_run"]
    pct = Path(pc_ok["files"][0]["path"]).read_text(encoding="utf-8")
    assert "rebar" in pct
    assert "规格响应" in pct and "到货期" in pct and "质保" in pct and "付款" in pct
    assert "TBD" in pct
    assert "待制度定" in pct
    assert "GeBIZ" in pct
    assert "[A001]" in pct
    assert "可以开工" not in pct
    assert "现定标" not in pct
    assert "报审通过" not in pct
    pc_v = run_expert_turn(
        "写一份比价表 钢筋 甲；乙",
        "proc-compare",
        force_intent="run",
        session_id="t037-pc-v",
    )
    pcv = Path(pc_v["files"][0]["path"]).read_text(encoding="utf-8")
    assert "甲" in pcv and "乙" in pcv
    assert "不足三家" in pcv
    sec = pcv.split("## 5 比价表")[1].split("## 6")[0]
    assert "甲" in sec and "乙" in sec

    pv_chat = run_expert_turn("准入和短名单怎么理解？", "proc-vendor")
    assert pv_chat["intent"] == "chat" and pv_chat["wrote"] is False
    pv_ok = run_expert_turn(
        "写一份供方评价 local fab\nSG",
        "proc-vendor",
        force_intent="run",
        session_id="t037-pv",
    )
    assert pv_ok["wrote"] is True
    assert "proc-vendor__eval" in pv_ok["tools_run"]
    pvt = Path(pv_ok["files"][0]["path"]).read_text(encoding="utf-8")
    assert "local fab" in pvt
    assert "准入" in pvt and "考察" in pvt and "短名单" in pvt
    assert "待核" in pvt
    assert "分数" in pvt
    assert "GeBIZ" in pvt
    assert "[A001]" in pvt
    assert "可以开工" not in pvt
    assert "中标" not in pvt
    pv_two = run_expert_turn(
        "写一份供方评价 甲；乙",
        "proc-vendor",
        force_intent="run",
        session_id="t037-pv-two",
    )
    pvv = Path(pv_two["files"][0]["path"]).read_text(encoding="utf-8")
    acc = pvv.split("## 3 准入")[1].split("## 4")[0]
    assert "甲" in acc and "乙" in acc

    fb_chat = run_expert_turn("报销怎么理解？", "finance-book")
    assert fb_chat["intent"] == "chat" and fb_chat["wrote"] is False
    fb_ok = run_expert_turn(
        "写一份核算检查 2026-08\nSG",
        "finance-book",
        force_intent="run",
        session_id="t038-fb",
    )
    assert fb_ok["wrote"] is True
    assert "finance-book__check" in fb_ok["tools_run"]
    fbt = Path(fb_ok["files"][0]["path"]).read_text(encoding="utf-8")
    assert "2026-08" in fbt
    assert "报销勾选" in fbt and "科目对照" in fbt and "对账缺口" in fbt
    assert "[A001]" in fbt
    assert "发票查验" in fbt
    assert "人工费" in fbt
    assert "office__xlsx" in fb_ok["tools_run"]
    xlsx_paths = [Path(f["path"]) for f in fb_ok["files"] if str(f.get("name") or "").endswith(".xlsx")]
    assert xlsx_paths and xlsx_paths[0].is_file()
    assert "GST" in fbt or "IRAS" in fbt
    assert "可以开工" not in fbt
    assert "账已平" not in fbt
    assert "已具备入账条件" not in fbt
    fb_gap = run_expert_turn(
        "写一份核算检查 2026-08 收发存未盘点",
        "finance-book",
        force_intent="run",
        session_id="t038-fb-gap",
    )
    fbg = Path(fb_gap["files"][0]["path"]).read_text(encoding="utf-8")
    assert "收发存" in fbg
    assert "不编盈亏" in fbg

    ff_chat = run_expert_turn("以收定支怎么理解？", "finance-fund")
    assert ff_chat["intent"] == "chat" and ff_chat["wrote"] is False
    ff_ok = run_expert_turn(
        "写一份资金计划 2026-08",
        "finance-fund",
        force_intent="run",
        session_id="t038-ff",
    )
    assert ff_ok["wrote"] is True
    assert "finance-fund__plan" in ff_ok["tools_run"]
    fft = Path(ff_ok["files"][0]["path"]).read_text(encoding="utf-8")
    assert "2026-08" in fft
    assert "收入栏" in fft and "支出栏" in fft
    assert "TBD" in fft
    assert "不构成付款指令" in fft
    assert "无法试算" in fft
    assert "可以开工" not in fft
    assert "资金充足" not in fft
    ff_io = run_expert_turn(
        "写一份资金计划 收入 业主进度款；支出 钢筋",
        "finance-fund",
        force_intent="run",
        session_id="t038-ff-io",
    )
    ffi = Path(ff_io["files"][0]["path"]).read_text(encoding="utf-8")
    inc = ffi.split("## 5 收入栏")[1].split("## 6")[0]
    exp = ffi.split("## 6 支出栏")[1].split("## 7")[0]
    assert "业主进度款" in inc
    assert "钢筋" in exp
    assert "TBD" in inc and "TBD" in exp

    wb_chat = run_expert_turn("班前会怎么理解？", "worker-brief")
    assert wb_chat["intent"] == "chat" and wb_chat["wrote"] is False
    wb_ok = run_expert_turn(
        "写一份班前白话 edge\nSG",
        "worker-brief",
        force_intent="run",
        session_id="t039-wb",
    )
    assert wb_ok["wrote"] is True
    assert "worker-brief__talk" in wb_ok["tools_run"]
    wbt = Path(wb_ok["files"][0]["path"]).read_text(encoding="utf-8")
    assert "edge" in wbt
    assert "今天干什么" in wbt and "哪儿会掉" in wbt and "三步怎么干" in wbt
    assert "toolbox" in wbt
    assert "毫米" not in wbt
    assert "可以开工" not in wbt
    wb_mm = run_expert_turn(
        "写一份班前白话 临边 1200mm",
        "worker-brief",
        force_intent="run",
        session_id="t039-wb-mm",
    )
    wbm = Path(wb_mm["files"][0]["path"]).read_text(encoding="utf-8")
    assert "临边" in wbm
    assert "1200mm" in wbm

    pd_chat = run_expert_turn("项目日报怎么理解？", "pm-daily")
    assert pd_chat["intent"] == "chat" and pd_chat["wrote"] is False
    pd_ok = run_expert_turn(
        "写一份项目日报 临边防护\nSG",
        "pm-daily",
        force_intent="run",
        session_id="t039-pmd",
    )
    assert pd_ok["wrote"] is True
    assert "pm-daily__log" in pd_ok["tools_run"]
    pdt = Path(pd_ok["files"][0]["path"]).read_text(encoding="utf-8")
    assert "临边防护" in pdt
    assert "天气待填" in pdt
    assert "出勤待填" in pdt
    img = pdt.split("## 4 形象进度")[1].split("## 5")[0]
    assert "%" not in img
    assert "BCA" in pdt or "site record" in pdt
    assert "可以开工" not in pdt
    assert "office__xlsx" in pd_ok["tools_run"]
    pd_wx = run_expert_turn(
        "写一份项目日报 临边 晴 木工12人",
        "pm-daily",
        force_intent="run",
        session_id="t039-pmd-wx",
    )
    pdx = Path(pd_wx["files"][0]["path"]).read_text(encoding="utf-8")
    wxsec = pdx.split("## 2 天气")[1].split("## 3")[0]
    assert "晴" in wxsec and "天气待填" not in wxsec
    lbsec = pdx.split("## 5 出勤")[1].split("## 6")[0]
    assert "木工" in lbsec and "出勤待填" not in lbsec
    pd_pct = run_expert_turn(
        "写一份项目日报 临边 完成30%",
        "pm-daily",
        force_intent="run",
        session_id="t039-pmd-pct",
    )
    pdp = Path(pd_pct["files"][0]["path"]).read_text(encoding="utf-8")
    img2 = pdp.split("## 4 形象进度")[1].split("## 5")[0]
    assert "%" not in img2
    pd_cn = run_expert_turn(
        "写一份项目日报 临边防护 住建部",
        "pm-daily",
        force_intent="run",
        session_id="t039-pmd-cn",
    )
    pdc = Path(pd_cn["files"][0]["path"]).read_text(encoding="utf-8")
    assert "辖区：CN" in pdc
    assert "BCA" not in pdc
    assert "site record" not in pdc

    hr_chat = run_expert_turn("招聘简报怎么理解？", "hr-recruit", session_id="t040-hr-chat")
    assert hr_chat["intent"] == "chat" and hr_chat["wrote"] is False
    hr_ok = run_expert_turn(
        "写一份招聘简报 施工员\nSG",
        "hr-recruit",
        force_intent="run",
        session_id="t040-hr",
    )
    assert hr_ok["wrote"] is True
    assert hr_ok["submit_blocked"] is True
    assert "hr-recruit__brief" in hr_ok["tools_run"]
    hrt = Path(hr_ok["files"][0]["path"]).read_text(encoding="utf-8")
    assert "施工员" in hrt
    duty = hrt.split("## 职责")[1].split("## 任职")[0]
    qual = hrt.split("## 任职")[1].split("## 面试问法")[0]
    ivw = hrt.split("## 面试问法")[1].split("## 薪资")[0]
    assert duty.strip() != qual.strip()
    assert qual.strip() != ivw.strip()
    assert "八类" in duty or "职责" in duty
    assert "门槛" in qual or "适应现场" in qual
    assert "行为面" in ivw or "追问" in ivw
    pay = hrt.split("## 薪资")[1].split("[A001]")[0]
    assert "待填" in pay
    assert "不编市场带宽" in pay
    assert "Fair Consideration" in hrt or "Key Employment Terms" in hrt
    assert "可以开工" not in hrt
    assert "男士优先" not in hrt
    assert "35岁以下" not in hrt and "35 岁以下" not in hrt
    assert "office__xlsx" in hr_ok["tools_run"]
    hr_pay = run_expert_turn(
        "写一份招聘简报 施工员 月薪8000",
        "hr-recruit",
        force_intent="run",
        session_id="t040-hr-pay",
    )
    hrp = Path(hr_pay["files"][0]["path"]).read_text(encoding="utf-8")
    pay2 = hrp.split("## 薪资")[1].split("[A001]")[0]
    assert "8000" in pay2
    assert "待填" not in pay2
    hr_cn = run_expert_turn(
        "写一份招聘简报 施工员 住建部",
        "hr-recruit",
        force_intent="run",
        session_id="t040-hr-cn",
    )
    hrc = Path(hr_cn["files"][0]["path"]).read_text(encoding="utf-8")
    # A ministry name is not an explicit project jurisdiction.
    assert "辖区：UNSPECIFIED" in hrc
    assert "劳动合同法" not in hrc
    assert "Fair Consideration" not in hrc
    assert "MyCareersFuture" not in hrc
    assert "FCF" not in hrc
    from packing_assistant.runtime.tool_engine import ERR_DENIED, get_engine

    sib = get_engine().execute(
        "hr-recruit__brief",
        {"text": "写一份招聘简报 施工员", "session_id": "t040-hr-sib"},
        expert_id="hr-labor",
        intent="run",
    )
    assert sib.get("error_code") == ERR_DENIED, sib
    assert "hr-recruit" in str(sib.get("reason") or "")

    high = [e for e in roster if e.risk == "high"][0]
    blocked = run_expert_turn("写一份专项方案讨论提纲", high.id, confirm_ok=False)
    assert blocked["intent"] == "run"
    assert blocked["wrote"] is False and blocked["hitl_pending"] is True
    okh = run_expert_turn("写一份专项方案讨论提纲", high.id, confirm_ok=True)
    assert okh["wrote"] is True

    pack = run_expert_turn("出一份装箱作业单 铁架", "pack-ship")
    assert pack["wrote"] is True
    ev = (pack.get("pack_ship") or {}).get("plan") or {}
    assert ev.get("can_fit") == "UNSPECIFIED"

    n_chat = n_run = 0
    for e in roster:
        c = run_expert_turn("这是什么意思，先别写", e.id)
        assert c["wrote"] is False, e.id
        n_chat += 1
        with patch("packing_assistant.expert_turn._draft_markdown", side_effect=AssertionError(f"{e.id} used generic fallback")):
            r = run_expert_turn("写一份草稿提纲", e.id, confirm_ok=True, session_id=f"t-{e.id}")
        assert r["intent"] == "run", e.id
        assert r["wrote"] is True, e.id
        assert r["tools_run"], e.id
        n_run += 1
    assert n_chat == 66 and n_run == 66

    from fastapi.testclient import TestClient

    from gateway.app import app

    client = TestClient(app)
    listed = client.get("/api/experts")
    assert listed.status_code == 200
    assert listed.json()["n"] == 66
    ids = {e["id"] for e in listed.json()["experts"]}
    assert ids == {e.id for e in roster}
    http = client.post(
        "/api/turn",
        json={"text": "什么是 GST", "expert_id": "finance-tax"},
    )
    assert http.status_code == 200, http.text
    assert http.json().get("intent") == "chat"
    assert http.json().get("wrote") is False
    index = (ROOT / "frontend" / "index.html").read_text(encoding="utf-8")
    assert "expertSel" in index and "66 岗" in index
    assert exclusive_tools("pack-ship")
    print("PASS expert_turn", f"n={len(roster)} chat={n_chat} run={n_run}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
