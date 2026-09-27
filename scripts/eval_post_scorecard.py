#!/usr/bin/env python3
"""每岗记分卡（R5）：试点 + 第二波 + 第三波，全部离线、零 API Key、steps 模式。

门禁：
  G1 意图命中  test/eval/intents_golden.json 中该岗金句 → (intent, skill) 全对
              （复用 packing_assistant.understand + runtime/expert_skills.match_skill）。
  G2 KB 检索   demo/rag.search_kb 命中 demo/kb/<大类>/<岗>/ 私有库，且不漏兄弟岗
              （复用 scripts/test_kb_k4_depth.py 的 search/list 用法）。
  G3 交付物    经 packing_assistant.expert_turn.run_named_exclusive（ToolEngine 同一入口）
              以最小合法输入跑该岗 exclusive 工具，产出 markdown 覆盖
              demo/kb/<大类>/<岗>/README.md 字段表必需栏（K4 同款字段表解析）。
  G4 诚实度    缺数据输入 → 交付物保留 [A001]/UNSPECIFIED/待填/未在原文检出，
              且 packing_assistant.tools.tender_review.forbidden_hits == 0。

用法：
  python scripts/eval_post_scorecard.py --post cost          # 单岗
  python scripts/eval_post_scorecard.py --all-pilots         # 全量试点+第二波
  python scripts/eval_post_scorecard.py --all-pilots --quick # quick 只跑 2 岗

输出：output/posts/<id>.json（gitignore 内）+ 控制台摘要表。
某岗 exclusive 工具无法离线跑（需 key/外部服务）→ 该岗 G3/G4 降级为结构断言
并标 mode=schema-only，绝不造假绿。

试点 5 岗（bid/commercial/hse）：bid-parse / bid-compliance / bid-tech / cost / safety-brief。
第二波 8 岗：plan-master / quality / proc-plan / lab-record / finance-book / warehouse / worker-brief / survey。
第三波补齐其余无记分卡车道岗（计划/施工/安质/商务/采购/物机/试验/财务/资料/日报等），不含已有专测的设计 20 与 BIM/HR/行政/IT。
不宣称新能力：G1–G4 只固化已有 exclusive 写盘与 KB 私库；L3 仍仅 pack-ship。
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEMO = ROOT / "demo"
OUT_DIR = ROOT / "output" / "posts"
GOLDEN_JSON = ROOT / "test" / "eval" / "intents_golden.json"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(DEMO))

# 每岗：KB 检索查询词 + G3 必需栏（均可回溯到 demo/kb/<cat>/<id>/README.md 字段表栏名）
PILOTS: dict[str, dict] = {
    "bid-parse": {
        "category": "bid",
        "exclusive": "bid-parse__extract",
        "g3_args": {"text": "某学校教学楼项目施工总承包招标，总承包一级资质，工期540天。"},
        "kb_queries": ["招标解析", "评分点"],
        "required_bars": ["评分点", "资质", "工期", "专项触发"],
        "bars_trace": "评分点→评分点表 / 资质→投标人资格 / 工期→时间轴 / 专项触发→必须编制的专项 / 危大（SKILL.md 第 7 节）",
    },
    "bid-compliance": {
        "category": "bid",
        "exclusive": "bid-compliance__gaps",
        "g3_args": {"text": "废标检查：要求投标保证金50万元、总承包一级资质、近三年两项同类业绩。"},
        "kb_queries": ["废标检查"],
        "required_bars": ["已响应", "未响应", "招标未提供", "UNSPECIFIED"],
        "bars_trace": "已响应/未响应/招标未提供→响应缺口清单三列 / UNSPECIFIED→条款栏",
    },
    "bid-tech": {
        "category": "bid",
        "exclusive": "bid-tech__expand",
        "g3_args": {"text": "技术标：教学楼施工组织设计，工期540天，含质量安全保证措施。"},
        "kb_queries": ["技术标目录"],
        "required_bars": ["技术标目录", "评分点"],
        "bars_trace": "技术标目录→技术标目录/草稿 / 评分点→按评分点排目录",
    },
    "cost": {
        "category": "commercial",
        "exclusive": "cost__takeoff",
        "g3_args": {"text": "造价：教学楼土建工程量清单组价，混凝土C30，钢筋一级钢。"},
        "kb_queries": ["工程量清单", "造价"],
        "required_bars": ["工程量拆分表", "分项", "综合单价", "合价"],
        "bars_trace": "工程量拆分表/拆分总表→表题与表头 / 分项/综合单价/合价→拆分总表列",
    },
    "safety-brief": {
        "category": "hse",
        "exclusive": "safety-brief__talk",
        # 高风险岗：写盘须确认句「我明白，将由持证人员签认」→ confirm_ok=True
        "g3_args": {"text": "安全交底：基坑开挖作业，深度3米，工人10人，今日进场。", "confirm_ok": True},
        "kb_queries": ["安全交底"],
        "required_bars": ["草稿声明", "作业部位", "危险源", "防护要点", "个人防护", "禁止事项", "应急要点", "签字栏"],
        "bars_trace": "八栏同名字段表：草稿声明/作业部位与范围/危险源/防护要点/个人防护/禁止事项与喊停条件/应急要点/签字栏",
    },
    "plan-master": {
        "category": "planning",
        "exclusive": "plan-master__network",
        "g3_args": {"text": "总进度计划：教学楼土建，列出基坑、主体、装修三个 WBS。"},
        "kb_queries": ["总进度计划", "关键线路"],
        "required_bars": ["总进度计划", "关键线路", "里程碑", "紧前"],
        "bars_trace": "标题施工总进度计划 / 章关键线路 / 里程碑表 / 紧前列",
    },
    "quality": {
        "category": "hse",
        "exclusive": "quality__lot",
        "g3_args": {"text": "质量检查表：三层梁板钢筋检验批，主控项目先空着。", "confirm_ok": True},
        "kb_queries": ["质量检查表", "检验批"],
        "required_bars": ["质量检查表", "检验批", "主控", "隐蔽", "通病"],
        "bars_trace": "标题质量检查表 / 检验批部位 / 主控项目检查栏 / 隐蔽专项 / 通病防治",
    },
    "proc-plan": {
        "category": "procurement",
        "exclusive": "proc-plan__schedule",
        "g3_args": {"text": "采购计划：钢筋自采、电梯甲指，提前期待填。"},
        "kb_queries": ["采购计划", "甲指"],
        "required_bars": ["采购计划表", "提前期", "到货节点", "供应方式"],
        "bars_trace": "标题采购计划表 / 提前期倒排 / 到货节点 / 供应方式列",
    },
    "lab-record": {
        "category": "lab",
        "exclusive": "lab-record__ledger",
        "g3_args": {"text": "试验台账：C30 试块报告编号待核。"},
        "kb_queries": ["试验台账", "报告编号"],
        "required_bars": ["试验台账", "报告编号", "仪器检定"],
        "bars_trace": "标题试验台账骨架 / 报告编号列 / 仪器检定列",
    },
    "finance-book": {
        "category": "finance",
        "exclusive": "finance-book__check",
        "g3_args": {"text": "核算检查：报销审核清单，发票待核。"},
        "kb_queries": ["核算检查", "报销"],
        "required_bars": ["核算检查表", "报销", "科目", "[A001]"],
        "bars_trace": "标题项目部核算检查表 / 报销审核清单 / 科目对照 / 金额 [A001]",
    },
    "warehouse": {
        "category": "plant",
        "exclusive": "warehouse__log",
        "g3_args": {"text": "收发存：钢筋入库 12 吨，限额领料待填。"},
        "kb_queries": ["收发存", "限额领料"],
        "required_bars": ["收发存", "限额领料", "盘点", "TBD"],
        "bars_trace": "标题收发存台账口径 / 限额领料出库 / 盘点 / 无数 TBD",
    },
    "worker-brief": {
        "category": "people",
        "exclusive": "worker-brief__talk",
        "g3_args": {"text": "班前白话：今天三层临边防护，不报未给的尺寸。"},
        "kb_queries": ["班前白话", "工友白话"],
        "required_bars": ["班前白话稿", "今天", "临边"],
        "bars_trace": "标题班前白话稿 / 今天干什么 / 用户点名临边",
    },
    "survey": {
        "category": "construction",
        "exclusive": "survey__record",
        "g3_args": {"text": "测量方案：基坑放样，坐标用户未给。", "confirm_ok": True},
        "kb_queries": ["测量方案", "放样"],
        "required_bars": ["测量方案", "[A001]", "坐标"],
        "bars_trace": "标题测量方案/记录表 / 缺坐标 [A001] / 禁止编造坐标",
    },
    "plan-lookahead": {
        "category": "planning",
        "exclusive": "plan-lookahead__week",
        "g3_args": {"text": "四周滚动计划：主体与装修交叉，停工条件待填。"},
        "kb_queries": ["四周滚动", "月度计划"],
        "required_bars": ["四周滚动", "月度计划", "[A001]"],
        "bars_trace": "标题四周滚动计划/月度计划 / 缺数 [A001]",
    },
    "plan-resource": {
        "category": "planning",
        "exclusive": "plan-resource__peak",
        "g3_args": {"text": "资源负荷：钢筋工与塔吊峰值待填。"},
        "kb_queries": ["资源负荷", "峰值"],
        "required_bars": ["资源负荷", "TBD"],
        "bars_trace": "标题资源负荷表 / 无数 TBD",
    },
    "construction": {
        "category": "construction",
        "exclusive": "construction__scheme_draft",
        "g3_args": {"text": "专项施工方案：临边防护讨论提纲。", "confirm_ok": True},
        "kb_queries": ["专项施工方案", "十一章"],
        "required_bars": ["专项施工方案", "[A001]", "UNSPECIFIED"],
        "bars_trace": "标题专项施工方案讨论提纲 / 缺数 [A001] / 条款 UNSPECIFIED",
    },
    "method-hazard": {
        "category": "construction",
        "exclusive": "method-hazard__judge_hazard",
        "g3_args": {"text": "危大识别：基坑深度用户未给。", "confirm_ok": True},
        "kb_queries": ["危大", "判定书"],
        "required_bars": ["危大判定", "UNSPECIFIED"],
        "bars_trace": "标题危大判定书 / 缺深 UNSPECIFIED",
    },
    "dispatch": {
        "category": "construction",
        "exclusive": "dispatch__daily",
        "g3_args": {"text": "调度日报：今日指令下达，节点待填。"},
        "kb_queries": ["调度日报", "指令"],
        "required_bars": ["调度日报", "[A001]"],
        "bars_trace": "标题调度日报草稿 / 缺数 [A001]",
    },
    "env": {
        "category": "hse",
        "exclusive": "env__list",
        "g3_args": {"text": "环保文明：扬尘与夜间施工口径待填。"},
        "kb_queries": ["环保文明", "扬尘"],
        "required_bars": ["环保文明", "[A001]"],
        "bars_trace": "标题环保文明清单 / 缺数 [A001]",
    },
    "emergency": {
        "category": "hse",
        "exclusive": "emergency__plan",
        "g3_args": {"text": "应急预案：演练记录联系人待填。", "confirm_ok": True},
        "kb_queries": ["应急预案", "演练"],
        "required_bars": ["应急预案", "[A001]"],
        "bars_trace": "标题应急预案提纲 / 联系人 [A001]",
    },
    "variation": {
        "category": "commercial",
        "exclusive": "variation__form",
        "g3_args": {"text": "工程签证：事实栏有，金额待填。"},
        "kb_queries": ["工程签证", "设计变更"],
        "required_bars": ["签证", "UNSPECIFIED"],
        "bars_trace": "标题工程签证 / 金额 UNSPECIFIED",
    },
    "claim": {
        "category": "commercial",
        "exclusive": "claim__notice",
        "g3_args": {"text": "索赔意向：证据清单待填。"},
        "kb_queries": ["索赔意向", "调概"],
        "required_bars": ["索赔意向", "[A001]"],
        "bars_trace": "标题索赔意向 / 缺数 [A001]",
    },
    "subcontract": {
        "category": "commercial",
        "exclusive": "subcontract__sheet",
        "g3_args": {"text": "分包结算：劳务验工表头，扣款待填。"},
        "kb_queries": ["分包结算", "劳务"],
        "required_bars": ["分包", "结算"],
        "bars_trace": "标题分包（劳务）结算表头",
    },
    "interim": {
        "category": "commercial",
        "exclusive": "interim__measure",
        "g3_args": {"text": "验工计价：对上计量，业主未确认金额。"},
        "kb_queries": ["验工计价", "计量"],
        "required_bars": ["验工计价", "[A001]"],
        "bars_trace": "标题对上验工计价草稿 / 金额 [A001]",
    },
    "proc-compare": {
        "category": "procurement",
        "exclusive": "proc-compare__table",
        "g3_args": {"text": "询价比价：三家报价未到。"},
        "kb_queries": ["询价", "比价"],
        "required_bars": ["比价", "TBD"],
        "bars_trace": "标题询价比价表 / 无报价 TBD",
    },
    "proc-vendor": {
        "category": "procurement",
        "exclusive": "proc-vendor__eval",
        "g3_args": {"text": "供应商准入：考察记录待填。"},
        "kb_queries": ["供应商", "准入"],
        "required_bars": ["供应商", "[A001]"],
        "bars_trace": "标题供应商评价表 / 缺数 [A001]",
    },
    "pack-ship": {
        "category": "plant",
        "exclusive": "pack-ship__list",
        "g3_args": {"text": "装箱作业：物料清单未上传。"},
        "kb_queries": ["装箱", "拼柜"],
        "required_bars": ["UNSPECIFIED"],
        "bars_trace": "断线字段字面 UNSPECIFIED，不编 xyz",
    },
    "equip": {
        "category": "plant",
        "exclusive": "equip__ledger",
        "g3_args": {"text": "设备台账：塔吊进场，证件待填。", "confirm_ok": True},
        "kb_queries": ["设备台账", "特种设备"],
        "required_bars": ["设备台账", "[A001]"],
        "bars_trace": "标题设备台账/维保计划 / 证件 [A001]",
    },
    "material-site": {
        "category": "plant",
        "exclusive": "material-site__recon",
        "g3_args": {"text": "现场材料核算：节超待盘点。"},
        "kb_queries": ["材料核算", "节超"],
        "required_bars": ["材料核算", "TBD"],
        "bars_trace": "标题材料核算表头 / 无盘点 TBD",
    },
    "lab-mix": {
        "category": "lab",
        "exclusive": "lab-mix__report",
        "g3_args": {"text": "施工配合比：C30 试验数据未给。", "confirm_ok": True},
        "kb_queries": ["施工配合比", "配比"],
        "required_bars": ["配比", "[A001]"],
        "bars_trace": "标题配比报告提纲 / 无试验数据 [A001]",
    },
    "lab-sample": {
        "category": "lab",
        "exclusive": "lab-sample__list",
        "g3_args": {"text": "见证取样：钢筋原材送检。", "confirm_ok": True},
        "kb_queries": ["见证取样", "送检"],
        "required_bars": ["取样送检", "[A001]"],
        "bars_trace": "标题取样送检清单 / 缺数 [A001]",
    },
    "finance-tax": {
        "category": "finance",
        "exclusive": "finance-tax__calendar",
        "g3_args": {"text": "税务日历：GST 税率用户未给。"},
        "kb_queries": ["税务日历", "GST"],
        "required_bars": ["税务日历", "UNSPECIFIED"],
        "bars_trace": "标题税务日历/检查表 / 税率 UNSPECIFIED",
    },
    "finance-fund": {
        "category": "finance",
        "exclusive": "finance-fund__plan",
        "g3_args": {"text": "资金计划：本月收支节点待填。"},
        "kb_queries": ["资金计划", "以收定支"],
        "required_bars": ["资金计划", "[A001]"],
        "bars_trace": "标题项目资金计划草稿 / 缺数 [A001]",
    },
    "supervision": {
        "category": "docs",
        "exclusive": "supervision__reply",
        "g3_args": {"text": "监理通知回复：验收资料目录待填。"},
        "kb_queries": ["监理通知", "验收资料"],
        "required_bars": ["监理通知", "[A001]"],
        "bars_trace": "标题监理通知回复草稿 / 缺数 [A001]",
    },
    "pm-daily": {
        "category": "people",
        "exclusive": "pm-daily__log",
        "g3_args": {"text": "项目日报：形象进度待填，天气晴。"},
        "kb_queries": ["项目日报", "形象进度"],
        "required_bars": ["项目日报", "[A001]"],
        "bars_trace": "标题项目日报草稿 / 缺数 [A001]",
    },
}

QUICK_PILOTS = ["bid-parse", "cost"]
HONEST_MARKERS = ("[A001]", "UNSPECIFIED", "待填", "未在原文检出", "（未提供）", "（空）")


def g1_intent(post: str) -> dict:
    from packing_assistant.runtime.expert_skills import match_skill
    from packing_assistant.understand import understand

    golden = json.loads(GOLDEN_JSON.read_text(encoding="utf-8"))
    cases = [c for c in golden.get("cases", []) if c.get("skill") == post]
    if not cases:
        return {"pass": False, "detail": "金句文件中无该岗金句（应在 test/eval/intents_golden.json 增加）"}
    bad = []
    for c in cases:
        got_i, got_s = understand(c["text"]), match_skill(c["text"])
        if got_i != c["intent"] or got_s != post:
            bad.append(f"{c['text']!r}: want=({c['intent']},{post}) got=({got_i},{got_s})")
    return {
        "pass": not bad,
        "cases": len(cases),
        "detail": "all hit" if not bad else "; ".join(bad[:3]),
    }


def g2_kb(post: str, cfg: dict) -> dict:
    from rag import search_kb

    cat = cfg["category"]
    bad, n_hit = [], 0
    for q in cfg["kb_queries"]:
        hits = search_kb(post, cat, q, limit=6)
        paths = [h.path.replace("\\", "/") for h in hits]
        private = [p for p in paths if p.startswith(f"{cat}/{post}/")]
        if private:
            n_hit += 1
        else:
            bad.append(f"search_kb({q!r}) 未命中私有库 {cat}/{post}/（top={paths[:2]}）")
        leaked = [p for p in paths if p.startswith(f"{cat}/") and not p.startswith(f"{cat}/{post}/") and "/_shared/" not in p]
        if leaked:
            bad.append(f"search_kb({q!r}) 泄漏兄弟岗 {leaked[0]}")
    return {"pass": not bad, "queries": len(cfg["kb_queries"]), "hit": n_hit, "detail": "all hit" if not bad else "; ".join(bad[:3])}


def parse_field_table(post: str, cfg: dict) -> list[str]:
    """K4 同款：README.md 的「## 字段表」| 栏 | 表 → 栏名列表。"""
    readme = (DEMO / "kb" / cfg["category"] / post / "README.md").read_text(encoding="utf-8")
    assert "| 栏 |" in readme and "字段表" in readme, f"{post}: README 缺字段表"
    bars: list[str] = []
    in_tbl = False
    for line in readme.splitlines():
        if "| 栏 |" in line:
            in_tbl = True
            continue
        if in_tbl:
            s = line.strip()
            if s.startswith("|") and s.endswith("|") and "---" not in s:
                name = s.removeprefix("|").removesuffix("|").split("|")[0].strip()
                if name and name != "栏":
                    bars.append(name)
            elif not s:
                break
    return bars


def _md_path(result: dict) -> str | None:
    for f in result.get("files") or []:
        if str(f.get("name") or "").endswith(".md"):
            return str(f.get("path"))
    return None


def _run_exclusive(post: str, cfg: dict, args: dict) -> dict:
    from packing_assistant.expert_turn import run_named_exclusive

    return run_named_exclusive(cfg["exclusive"], args)


def g3_deliverable(post: str, cfg: dict) -> dict:
    readme_bars = parse_field_table(post, cfg)
    struct = {"readme_field_bars": len(readme_bars), "required_bars": cfg["required_bars"], "bars_trace": cfg["bars_trace"]}
    if len(readme_bars) < 6:
        return {"pass": False, **struct, "detail": f"README 字段表栏数 {len(readme_bars)} < 6"}
    try:
        result = _run_exclusive(post, cfg, cfg["g3_args"])
    except Exception as e:  # 离线跑不起来：降级 schema-only，不许造假绿
        return {"pass": False, "mode": "execution-unavailable", **struct, "detail": f"exclusive 离线不可跑（{type(e).__name__}），未完成运行验证"}
    md_path = _md_path(result)
    if not result.get("wrote") or not md_path:
        return {"pass": False, **struct, "detail": f"wrote={result.get('wrote')} md 缺失 reply={result.get('reply')}"}
    md = Path(md_path).read_text(encoding="utf-8")
    missing = [b for b in cfg["required_bars"] if b not in md]
    if missing:
        return {"pass": False, **struct, "md": md_path, "detail": f"md 缺必需栏 {missing}"}
    if cfg["exclusive"] not in (result.get("tools_run") or []):
        return {"pass": False, **struct, "md": md_path, "detail": f"tools_run 未含 {cfg['exclusive']}"}
    return {"pass": True, "mode": "offline", **struct, "md": md_path, "detail": f"{len(cfg['required_bars'])}/{len(readme_bars)} 必需栏覆盖"}


def g4_honesty(post: str, cfg: dict) -> dict:
    from packing_assistant.tools.tender_review import forbidden_hits

    try:
        result = _run_exclusive(post, cfg, {"text": "", **{k: v for k, v in cfg["g3_args"].items() if k == "confirm_ok"}})
    except Exception as e:
        return {"pass": False, "mode": "execution-unavailable", "detail": f"exclusive 离线不可跑（{type(e).__name__}），未完成运行验证"}
    md_path = _md_path(result)
    if not md_path:
        return {"pass": False, "detail": f"空输入未产出 md reply={result.get('reply')}"}
    md = Path(md_path).read_text(encoding="utf-8")
    keep = [m for m in HONEST_MARKERS if m in md]
    hits = forbidden_hits(md)
    blocked = result.get("submit_blocked") is True
    ok = bool(keep) and not hits and blocked
    return {
        "pass": ok,
        "md": md_path,
        "markers_kept": keep[:3],
        "forbidden_hits": hits,
        "submit_blocked": blocked,
        "detail": "诚实空态保留" if ok else f"markers={keep} hits={hits} blocked={blocked}",
    }


def run_post(post: str) -> dict:
    cfg = PILOTS[post]
    t0 = time.time()
    gates = {
        "G1_intent": g1_intent(post),
        "G2_kb": g2_kb(post, cfg),
        "G3_deliverable": g3_deliverable(post, cfg),
        "G4_honesty": g4_honesty(post, cfg),
    }
    ok = all(g["pass"] for g in gates.values())
    return {
        "post": post,
        "category": cfg["category"],
        "exclusive": cfg["exclusive"],
        "mode": gates["G3_deliverable"].get("mode", "offline"),
        "pass": ok,
        "gates": gates,
        "secs": round(time.time() - t0, 2),
    }


def print_table(rows: list[dict]) -> None:
    print()
    print(f"{'岗':<16}{'模式':<12}{'G1':<4}{'G2':<4}{'G3':<4}{'G4':<4}{'结果':<6}耗时")
    print("-" * 64)
    for r in rows:
        g = r["gates"]
        cells = "".join(("P" if g.get(k, {}).get("pass", False) else "F") + "".join(" " for _ in range(3)) for k in ("G1_intent", "G2_kb", "G3_deliverable", "G4_honesty"))
        print(f"{r['post']:<16}{r['mode']:<12}{cells}{'PASS' if r['pass'] else 'FAIL':<6}{r['secs']}s")
    print("-" * 64)


def main() -> int:
    ap = argparse.ArgumentParser(description="每岗记分卡试点（G1 意图 / G2 KB / G3 交付物 / G4 诚实度）")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--post", choices=sorted(PILOTS), help="单岗记分卡")
    g.add_argument("--all-pilots", action="store_true", help="全部试点岗")
    ap.add_argument("--quick", action="store_true", help="quick 预算：只跑前 2 个试点岗")
    args = ap.parse_args()

    posts = QUICK_PILOTS if (args.all_pilots and args.quick) else (sorted(PILOTS) if args.all_pilots else [args.post])
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    rows = []
    for post in posts:
        try:
            rows.append(run_post(post))
        except Exception as e:  # 单岗基础设施级异常也要落 JSON，不许静默
            rows.append({"post": post, "category": PILOTS[post]["category"], "mode": "error", "pass": False,
                         "gates": {}, "error": f"{type(e).__name__}: {e}", "secs": 0.0})
        r = rows[-1]
        (OUT_DIR / f"{post}.json").write_text(json.dumps(r, ensure_ascii=False, indent=1), encoding="utf-8")

    print_table(rows)
    failed = [r["post"] for r in rows if not r["pass"]]
    schema_only = [r["post"] for r in rows if r.get("mode") == "schema-only"]
    print(f"scorecard posts={len(rows)} pass={len(rows) - len(failed)} failed={failed or 'none'}"
          + (f" schema_only={schema_only}" if schema_only else ""))
    print(f"JSON → {OUT_DIR}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
