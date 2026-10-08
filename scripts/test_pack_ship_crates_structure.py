#!/usr/bin/env python3
"""成品框架写成「数量 2」仍然是框架；箱的结构验算结论要写进方案。

两件事，都是 #49 把丢掉的数量找回来之后才看得见的：

1. `_module_like_majority` 要求「单行单件」。那条限制是替当量直通兜底的（直通一行只出一箱，
   数量会丢）；直通改成一件一箱后，它只剩副作用——2100×1100×1200、单件 1.8 t 的成品框架
   写成数量 2 就不算模块，被送进标准箱库按跨距上限切成虚拟件。
   test/benchmarks/excel/case_b_long_frames_40hq.xlsx（9 个框架，23.8 t）：
   48 个箱、can_fit=False、N0 13  →  9 个箱、can_fit=True、3 × 40HQ、N0 2。
2. 成箱引擎逐箱做了结构验算，run_plan 一个字不提：t30_pallet_like_s5 的 28 个箱全部
   「不通过」，方案照样 ok=True、can_fit=True，回复里没有任何提示。
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("PACKING_SKIP_SKJOLBER", "1")

from packing_assistant.agents.box_scheme import _module_like_majority, agent_box_scheme  # noqa: E402
from packing_assistant.tools.pack_ship_solve import (  # noqa: E402
    plan_record_json,
    plan_reply,
    plan_report_md,
    run_plan,
    structure_summary,
)

CASE_B = ROOT / "test/benchmarks/excel/case_b_long_frames_40hq.xlsx"
BEAMS_CSV = (
    "编号,品名,数量,单重(kg),长(mm),宽(mm),高(mm)\n"
    "GL-1,钢梁 GL-1,2,850,6000,300,400\n"
    "GZ-2,钢柱 GZ-2,4,620,5200,350,350\n"
    "LB-3,铝板 LB-3,40,18,2400,1200,3\n"
    "DJ-4,吊架 DJ-4,12,95,1800,400,300\n"
)


def _frame(**extra):
    base = {"id": "F1", "name": "成品框架", "quantity": 2, "weight_kg": 1800, "total_weight_kg": 3600,
            "length_mm": 2100, "width_mm": 1100, "height_mm": 1200}
    base.update(extra)
    return base


def test_crate_rows_of_any_quantity_are_crates() -> None:
    plan = run_plan(file_path=str(CASE_B))
    assert plan["ok"] is True and plan["can_fit"] is True, plan  # 修复前 can_fit=False
    assert plan["n_boxes"] == 9, plan["n_boxes"]  # 9 个框架 9 个箱；修复前 48
    assert plan["containers_used"] <= 3 and plan["n0"] <= 3, plan  # 修复前 9 个柜装不下、N0 13
    kept = plan["conservation"]
    assert (kept["pieces_in"], kept["pieces_out"]) == (9, 9) and kept["kg_out"] == 23800.0, kept
    assert kept["mass_split_rows"] == [], kept  # 整件装，不再有虚拟拆分
    assert plan["structure"]["pass"] == 9 and plan["structure"]["fail"] == 0, plan["structure"]  # 修复前 48 个不通过


def test_module_majority_judges_the_unit_not_the_row() -> None:
    assert _module_like_majority([_frame()]) is True  # 修复前 False：数量 2
    assert _module_like_majority([_frame(quantity=1, total_weight_kg=1800)]) is True
    assert _module_like_majority([_frame(qty=2, quantity=None)]) is True  # qty 与 quantity 同义
    # 单件重，不是行总重：10 件共 500 kg 的矮箱不是模块
    assert _module_like_majority([_frame(quantity=10, weight_kg=50, total_weight_kg=500, height_mm=600)]) is False
    assert _module_like_majority([_frame(width_mm=400)]) is False  # 梁、柱不是模块
    # 仍是「多数」规则：3 行里 1 行像模块，不整票直通
    small = {"id": "S", "name": "配件", "quantity": 6, "weight_kg": 20, "total_weight_kg": 120,
             "length_mm": 600, "width_mm": 400, "height_mm": 300}
    assert _module_like_majority([_frame(), dict(small, id="S1"), dict(small, id="S2")]) is False


def test_bare_beams_are_still_crated_by_the_library() -> None:
    """梁、柱不是成品箱；标准库的虚拟拆件保留对账，但不能成为可用方案。"""
    path = Path(tempfile.gettempdir()) / "cb_crates_beams.csv"
    path.write_text(BEAMS_CSV, encoding="utf-8")
    plan = run_plan(file_path=str(path))
    assert plan["ok"] is False and plan["can_fit"] is False, plan
    assert plan["error"] == "physical_split_not_authorized" and "containers_used" not in plan, plan
    assert [row["id"] for row in plan["conservation"]["mass_split_rows"]] == ["GL-1", "GZ-2"], plan["conservation"]


def test_structure_verdict_is_part_of_the_plan() -> None:
    doc = json.loads((ROOT / "test/sim_materials/t30_pallet_like_s5/materials.json").read_text(encoding="utf-8"))
    plan = run_plan(materials=doc["materials"])
    assert plan["ok"] is True and plan["can_fit"] is True, plan  # can_fit 只说几何与载重，这一点不变
    structure = plan["structure"]
    assert structure["n_boxes"] == plan["n_boxes"] == 28, structure
    assert structure["fail"] == 28, structure  # 修复前同样是 28 个不通过，只是方案里不写
    assert sum(structure[k] for k in ("pass", "needs_reinforcement", "fail", "pending_design")) == 28
    assert structure["failing"] and structure["failing"][0]["boxes"] == 28 and structure["failing"][0]["reason"]
    report = plan_report_md(plan, "t30.json")
    assert "不通过 28" in report and structure["failing"][0]["reason"] in report, report
    assert "28 个箱按预置截面结构验算不通过" in plan_reply(plan, "t30.json")
    assert json.loads(plan_record_json(plan, "t30.json"))["structure"]["fail"] == 28  # 报告里的数在记录里有出处
    # 没有不通过的箱时回复不加这句
    assert "结构验算不通过" not in plan_reply(run_plan(file_path=str(CASE_B)), "case_b.xlsx")


def test_structure_summary_counts() -> None:
    def box(verdict, risk="", kind="6米框"):
        return {"structure_conclusion": verdict, "base_box_type": kind, "structure_calc": {"风险点": [risk] if risk else []}}

    boxes = ([box("通过(当量直通)"), box("通过"), box("需加强"), box("待详设"), box("")]
             + [box("不通过", "挠度超限")] * 3 + [box("不通过", "", "4米铁架")]
             + [box("不通过", f"原因{i}", "2米框") for i in range(6)])
    found = structure_summary(boxes)
    assert (found["pass"], found["needs_reinforcement"], found["pending_design"], found["fail"]) == (2, 1, 1, 10), found
    assert found["n_boxes"] == 15  # 没有结论的箱不计入任何一类，但总数照实
    assert found["failing"][0] == {"box_type": "6米框", "reason": "挠度超限", "boxes": 3}, found["failing"]
    assert len(found["failing"]) == 5  # 只列前 5 类
    # 引擎没给风险点时也有一句可读的原因
    assert structure_summary([box("不通过", "", "4米铁架")])["failing"] == [
        {"box_type": "4米铁架", "reason": "结构验算不通过", "boxes": 1}]
    assert structure_summary([]) == {"pass": 0, "needs_reinforcement": 0, "fail": 0, "pending_design": 0,
                                     "n_boxes": 0, "failing": []}


def test_passthrough_verdict_reaches_the_pipeline_state() -> None:
    scheme = agent_box_scheme({"materials": [_frame()], "container_type": "40HQ", "packing_options": {}})
    assert (scheme.get("team_a_summary") or {}).get("packing_mode") == "crate_passthrough", scheme.get("team_a_summary")
    assert len(scheme["boxes"]) == 2 and scheme["cargo_conservation"]["ok"] is True, scheme["boxes"]
    assert scheme.get("ship_ok") is not False


def main() -> int:
    tests = [
        test_crate_rows_of_any_quantity_are_crates,
        test_module_majority_judges_the_unit_not_the_row,
        test_bare_beams_are_still_crated_by_the_library,
        test_structure_verdict_is_part_of_the_plan,
        test_structure_summary_counts,
        test_passthrough_verdict_reaches_the_pipeline_state,
    ]
    for test in tests:
        test()
        print(f"PASS {test.__name__}")
    print(f"{len(tests)}/{len(tests)} passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
