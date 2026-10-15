#!/usr/bin/env python3
"""装箱单上的每一件、每一公斤都要在箱里；对不上就不出方案。

修复前（origin/main 1e347db 实测）：单件重超过箱型净重上限的行按质量切成虚拟件时只切
第一件，行上的数量被丢掉。下面这张 4 行装箱单 58 件 / 6040 kg，成箱后剩 3330 kg
（= 850 一根梁 + 620 一根柱 + 720 + 1140），run_plan 仍给 ok=True、can_fit=True、3 个柜。
同类的另外两处：当量直通把数量 N 的行出成 1 个箱；13.5 m 的梁被钳进 12.032 m 的箱后
报 can_fit=True。还有一处在截面上：标准箱外宽一律 1100 mm，1200 mm 见方的电缆盘照样
放进去、只挂「尺寸紧张」，方案 ok=True（59 个夹具里 6 个、共 69 条）。

用法：python scripts/test_pack_ship_conservation.py            （CI，约 25 s）
      python scripts/test_pack_ship_conservation.py --numbers  （另外打印每个夹具的进/出账）
      python scripts/test_pack_ship_conservation.py --all      （连同 9 个 300–570 行的 t80_* 夹具，约 70 s）
"""
from __future__ import annotations

import json
import math
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("PACKING_SKIP_SKJOLBER", "1")

from packing_assistant.agents.box_scheme import agent_box_scheme  # noqa: E402
from packing_assistant.tools import pack_ship_solve  # noqa: E402
from packing_assistant.tools.cargo_conservation import (  # noqa: E402
    NOT_CONSERVED,
    check_conservation,
    violation_sentences,
)
from packing_assistant.tools.pack_ship_solve import (  # noqa: E402
    _solve_boxes,
    draft_booking,
    draft_vgm,
    load_materials,
    plan_report_md,
    run_plan,
)
from packing_assistant.tools.packing import _explode_items_by_net_cap  # noqa: E402

REPRO_CSV = (
    "编号,品名,数量,单重(kg),长(mm),宽(mm),高(mm)\n"
    "GL-1,钢梁 GL-1,2,850,6000,300,400\n"
    "GZ-2,钢柱 GZ-2,4,620,5200,350,350\n"
    "LB-3,铝板 LB-3,40,18,2400,1200,3\n"
    "DJ-4,吊架 DJ-4,12,95,1800,400,300\n"
)
GIRDER = [{"id": "G-1", "name": "主梁 G-1", "quantity": 1, "weight_kg": 400, "total_weight_kg": 400,
           "length_mm": 13500, "width_mm": 300, "height_mm": 400}]


def _row(**extra):
    base = {"id": "R1", "name": "Panel", "weight_kg": 50, "length_mm": 1200, "width_mm": 400, "height_mm": 300}
    base.update(extra)
    return base


def _repro_path() -> str:
    path = Path(tempfile.gettempdir()) / "cb_conservation_repro.csv"
    path.write_text(REPRO_CSV, encoding="utf-8")
    return str(path)


def _pieces_out(boxes) -> float:
    return sum(int(c["quantity"]) / int(c.get("split_of") or 1) for b in boxes for c in b["contents"])


# ---- 主缺陷：质量拆分不得丢数量 -----------------------------------------------------------------

def test_repro_carries_every_unit() -> None:
    mats = load_materials(None, _repro_path())["materials"]
    solved = _solve_boxes(mats, container_type="40HQ")
    boxes = solved["boxes"]
    assert sum(int(m["quantity"]) for m in mats) == 58
    assert _pieces_out(boxes) == 58, _pieces_out(boxes)
    kg_out = sum(b["net_weight_kg"] for b in boxes)
    assert abs(kg_out - 6040.0) < 1.5, kg_out  # 修复前 3330.0
    beam_parts = [c for b in boxes for c in b["contents"] if c["source_material_id"] == "GL-1"]
    assert len(beam_parts) == 4 and all(c["split_of"] == 2 for c in beam_parts), beam_parts  # 2 根 × 2 份；修复前 2
    column_parts = [c for b in boxes for c in b["contents"] if c["source_material_id"] == "GZ-2"]
    assert len(column_parts) == 8, len(column_parts)  # 4 根 × 2 份；修复前 2
    assert len({c["material_id"] for c in beam_parts + column_parts}) == 12, "拆出的每一份编号唯一"
    assert solved["conservation"]["ok"] is True, solved["conservation"]


def test_run_plan_reports_the_account() -> None:
    result = run_plan(file_path=_repro_path())
    assert result["ok"] is True, result
    kept = result["conservation"]
    assert (kept["pieces_in"], kept["pieces_out"]) == (58, 58), kept
    assert kept["kg_in"] == 6040.0 and abs(kept["kg_out"] - 6040.0) < 1.5, kept
    assert kept["per_row_checked"] is True
    split = {row["id"]: row for row in kept["mass_split_rows"]}
    assert split["GL-1"]["units"] == 2 and split["GZ-2"]["units"] == 4, split
    report = plan_report_md(result, "repro.csv")
    assert "件数 58 → 58" in report and "净重 6040.0 →" in report, report
    assert "钢梁 GL-1：2 件" in report and "计算上的拆分" in report, report  # 虚拟拆分必须说出来


def test_mass_split_unit_level() -> None:
    item = {"名称": "重梁", "数量": 3, "单重_kg": 1000.0, "总重_kg": 3000.0, "加工件编号": "B1",
            "外尺寸_mm": {"长": 6000.0, "宽": 300.0, "高": 400.0}}
    out = _explode_items_by_net_cap([item], 480.0)
    assert len(out) == 9, len(out)  # 3 件 × ceil(1000/480)=3 份；修复前 3
    assert math.isclose(sum(x["总重_kg"] for x in out), 3000.0, abs_tol=1e-6), sum(x["总重_kg"] for x in out)
    assert all(x["总重_kg"] <= 480.0 + 1e-6 and x["数量"] == 1 for x in out), out
    assert all(x["源编号"] == "B1" and x["质量拆分份数"] == 3 for x in out), out
    assert len({x["加工件编号"] for x in out}) == 9 and len({x["名称"] for x in out}) == 9
    # 除不尽时末份收尾差：1000/3 取整三位后仍合回 1000，而不是 999.999
    single = _explode_items_by_net_cap([dict(item, 数量=1, 总重_kg=1000.0)], 480.0)
    assert sum(x["总重_kg"] for x in single) == 1000.0, [x["总重_kg"] for x in single]
    # 数量为 1 的行名称与编号保持原样（下游有按名字认的）
    assert [x["名称"] for x in single] == ["重梁(重拆1/3)", "重梁(重拆2/3)", "重梁(重拆3/3)"]
    assert [x["加工件编号"] for x in single] == ["B1-W1", "B1-W2", "B1-W3"]


def test_split_weight_follows_row_total() -> None:
    """单重与总重对不上时两条拆分路径都按总重摊，不会同一行拆与不拆装出两个质量。"""
    item = {"名称": "件", "数量": 4, "单重_kg": 100.0, "总重_kg": 1000.0, "加工件编号": "X",
            "外尺寸_mm": {"长": 1000.0, "宽": 300.0, "高": 300.0}}
    out = _explode_items_by_net_cap([item], 600.0)  # 按件数拆
    assert sum(x["数量"] for x in out) == 4 and math.isclose(sum(x["总重_kg"] for x in out), 1000.0, abs_tol=0.01), out
    out = _explode_items_by_net_cap([item], 200.0)  # 250 > 200 → 按质量拆
    assert math.isclose(sum(x["总重_kg"] for x in out), 1000.0, abs_tol=1e-6), out


# ---- 同类：当量直通一件一箱 ---------------------------------------------------------------------

def test_passthrough_one_crate_per_unit() -> None:
    doc = json.loads((ROOT / "test/sim_materials/ns_factory_crate_path/materials.json").read_text(encoding="utf-8"))
    mats = doc["materials"]
    scheme = agent_box_scheme({"materials": mats, "container_type": "40HQ", "packing_options": {}})
    assert (scheme.get("team_a_summary") or {}).get("packing_mode") == "crate_passthrough"
    boxes = scheme["boxes"]
    assert len(boxes) == 5, len(boxes)  # 3 + 2 个出厂架；修复前 2 个箱
    assert len({b["box_id"] for b in boxes}) == 5
    assert check_conservation(mats, boxes)["ok"] is True
    outer_m3 = sum(b["outer_m3"] for b in boxes)
    assert abs(outer_m3 - (3 * 3.0 * 1.1 * 1.5 + 2 * 5.8 * 1.0 * 1.2)) < 1e-6, outer_m3  # 修复前 11.91，少了 3 个架的体积
    # 行上只有单重时，修复前连重量也只剩一件的
    bare = [{"id": "F9", "name": "叠层架-出厂", "quantity": 3, "weight_kg": 450,
             "length_mm": 3000, "width_mm": 1100, "height_mm": 1500, "note": "crate_equiv"}]
    boxes = agent_box_scheme({"materials": bare, "container_type": "40HQ", "packing_options": {}})["boxes"]
    assert len(boxes) == 3 and sum(b["net_weight_kg"] for b in boxes) == 1350.0, boxes


# ---- 核对器本身：不依赖引擎，喂错账必须报 --------------------------------------------------------

def _box(box_id, net, contents, outer=(2000, 1100, 1750)):
    return {"box_id": box_id, "net_weight_kg": net,
            "outer_size_mm": {"length": outer[0], "width": outer[1], "height": outer[2]}, "contents": contents}


def _line(src, qty, kg, dims=(1200, 400, 300), split_of=1):
    return {"material_id": src, "source_material_id": src, "name": src, "quantity": qty, "split_of": split_of,
            "weight_kg": kg, "outer_size_mm": {"length": dims[0], "width": dims[1], "height": dims[2]}}


def test_checker_catches_each_kind_of_loss() -> None:
    mats = [_row(id="A", quantity=4, total_weight_kg=200), _row(id="B", quantity=2, weight_kg=850, total_weight_kg=1700)]
    good = [_box("1", 200, [_line("A", 4, 200)]),
            _box("2", 850, [_line("B", 1, 425, split_of=2), _line("B", 1, 425, split_of=2)]),
            _box("3", 850, [_line("B", 1, 425, split_of=2), _line("B", 1, 425, split_of=2)])]
    assert check_conservation(mats, good)["ok"] is True, check_conservation(mats, good)

    kinds = lambda boxes: {v["kind"] for v in check_conservation(mats, boxes)["violations"]}  # noqa: E731
    assert kinds(good[:2]) == {"pieces_lost", "kg_lost", "row_pieces"}  # 整件丢了（本次缺陷的形状）
    # 件数账平、重量少：把 split_of 标错掩盖不了
    mislabelled = [good[0], _box("2", 850, [_line("B", 2, 850)])]
    assert "kg_lost" in kinds(mislabelled) and "pieces_lost" not in kinds(mislabelled)
    # 重量账平、件数少
    assert kinds([_box("1", 200, [_line("A", 3, 200)]), good[1], good[2]]) == {"pieces_lost", "row_pieces"}
    # 此消彼长：总数平，逐行不平
    swapped = [_box("1", 200, [_line("A", 5, 200)]), _box("2", 1700, [_line("B", 1, 1700)])]
    assert kinds(swapped) == {"row_pieces"}, kinds(swapped)
    # 货比箱长
    long_box = [_box("1", 200, [_line("A", 4, 200, dims=(2500, 400, 300))]), good[1], good[2]]
    found = check_conservation(mats, long_box)
    assert {v["kind"] for v in found["violations"]} == {"content_exceeds_box"}
    assert "进不了箱" in violation_sentences(found)[0]
    # 货的截面比箱外廓大：同样进不了箱。原先只记 warnings、ok=True
    wide = check_conservation(mats, [_box("1", 200, [_line("A", 4, 200, dims=(1200, 1150, 1120))]), good[1], good[2]])
    assert wide["ok"] is False and [v["kind"] for v in wide["violations"]] == ["content_section_exceeds_box"], wide
    assert "截面" in violation_sentences(wide)[0] and "进不了箱" in violation_sentences(wide)[0]
    assert "warnings" not in wide  # 没有「只记数」的几何问题了
    # 贴着外廓（1 mm 以内）不算超
    snug = check_conservation(mats, [_box("1", 200, [_line("A", 4, 200, dims=(1200, 1100.5, 300))]), good[1], good[2]])
    assert snug["ok"] is True, snug
    # 多出来的货同样是错账
    assert "pieces_gained" in kinds(good + [_box("4", 50, [_line("A", 1, 50)])])


def test_plan_fails_when_engine_loses_cargo() -> None:
    """引擎再出同类缺陷时，三个工具都不给 ok=True。这里让成箱器丢掉最后一个箱。"""
    real = agent_box_scheme

    def lossy(state):
        out = dict(real(state))
        out["boxes"] = list(out["boxes"])[:-1]
        return out

    mats = [_row(id="A", quantity=4), _row(id="B", quantity=30, length_mm=2600)]
    assert run_plan(materials=mats)["ok"] is True
    with patch("packing_assistant.agents.box_scheme.agent_box_scheme", lossy):
        plan = run_plan(materials=mats)
        vgm = draft_vgm(materials=mats)
        booking = draft_booking(materials=mats)
    for out in (plan, vgm, booking):
        assert out["ok"] is False and out["error"] == NOT_CONSERVED, out
        assert out["conservation"]["pieces_out"] < out["conservation"]["pieces_in"], out["conservation"]
        assert "can_fit" not in out and "containers_used" not in out and "booking_request" not in out, out
    assert "件数对不上" in " ".join(plan["detail"]), plan["detail"]
    assert "件数对不上" in plan_report_md(plan, "x.csv") and "未出方案" in plan_report_md(plan, "x.csv")
    assert NOT_CONSERVED in pack_ship_solve.plan_reply(plan, "x.csv")


# ---- 同类：超限件不得「钳进箱里」就算装下 ------------------------------------------------------

def test_oversize_is_refused_not_clamped() -> None:
    solved = _solve_boxes(GIRDER, container_type="40HQ")  # 绕过闸门直接求解：核对器自己也要拦得住
    kinds = {v["kind"] for v in solved["conservation"]["violations"]}
    assert kinds == {"content_exceeds_box"}, solved["conservation"]  # 13500 mm 的货在 12032 mm 的箱里

    refused = run_plan(materials=GIRDER, container_type="40HQ")  # 修复前 ok=True can_fit=True 1 个柜
    assert refused["ok"] is False and refused["error"] == "oversize_for_container", refused
    assert "45HQ" in refused["needs_human"][0]["ask"], refused["needs_human"]
    assert "can_fit" not in refused
    assert run_plan(materials=GIRDER, container_type="45HQ")["ok"] is True  # 13556 mm 的柜装得下
    assert draft_vgm(materials=GIRDER)["error"] == "oversize_for_container"
    assert draft_booking(materials=GIRDER)["error"] == "oversize_for_container"

    wide = json.loads((ROOT / "test/sim_materials/ns_over_container_width/materials.json").read_text(encoding="utf-8"))
    refused = run_plan(materials=wide["materials"])  # 3000×2800×900：2800 大于柜内任何一边可用的 2698
    assert refused["error"] == "oversize_for_container" and [r["id"] for r in refused["needs_human"]] == ["W1"], refused
    assert "框架柜" in refused["needs_human"][0]["ask"]  # 没有哪种柜装得下时不乱推荐
    # 绕过闸门：定制外廓也被柜内净空钳住，货仍比箱大——核对器判失败，不再只是 3 条 warnings
    solved = _solve_boxes(wide["materials"], container_type="40HQ")
    assert {v["kind"] for v in solved["conservation"]["violations"]} == {"content_section_exceeds_box"}, solved["conservation"]
    scheme = agent_box_scheme({"materials": wide["materials"], "container_type": "40HQ", "packing_options": {}})
    assert scheme["ship_ok"] is False and "cargo_not_conserved" in scheme["errors"][0], scheme.get("errors")

    # 20GP 遇长件引擎自动改 40HQ；闸门按引擎实际用的柜型判，不误拒 6 m 的货
    six = [dict(GIRDER[0], length_mm=6000)]
    assert run_plan(materials=six, container_type="20GP")["ok"] is True


# ---- 同类：截面比任何标准箱都大的件，按货定制外廓 ----------------------------------------------

def _outer_sorted(box):
    size = box["outer_size_mm"]
    return sorted((size["length"], size["width"], size["height"]), reverse=True)


def test_piece_wider_than_any_standard_crate_gets_a_custom_outer() -> None:
    """修复前：6 个 1200 mm 见方的电缆盘各进一只 6000×1100×1550 的「6米框」，结构不通过、
    只挂「尺寸紧张」，run_plan 照样 ok=True。"""
    from packing_assistant.tools.packing import CUSTOM_SECTION_TAG

    mats = load_materials(None, str(ROOT / "test/generic_tables/G6_messy_headers/materials.csv"))["materials"]
    result = run_plan(materials=mats)
    assert result["ok"] is True and result["conservation"]["ok"] is True, result
    custom = result["custom_section_boxes"]
    assert len(custom) == 6 and all(row["names"] == [f"Cable drum(拆{i})"] for i, row in enumerate(custom, 1)), custom
    assert all(row["outer_mm"] == [1350.0, 1450.0, 1390.0] for row in custom), custom
    report = plan_report_md(result, "G6.csv")
    assert "定制箱 6 个" in report and "1350×1450×1390 mm" in report, report  # 不是标准箱，得让人看见

    boxes = _solve_boxes(mats, container_type="40HQ")["boxes"]
    drums = [b for b in boxes if CUSTOM_SECTION_TAG in b["special_attributes"]]
    assert len(drums) == 6 and len(boxes) == 9, (len(drums), len(boxes))  # 箱数不变，修复前也是 9
    for box in drums:
        assert all(o >= 1200 for o in _outer_sorted(box)), box["outer_size_mm"]  # 修复前外廓有一边 1100
        assert "尺寸紧张" not in box["special_attributes"] and "标准箱库" not in box["special_attributes"], box
        assert "定制外廓" in box["special_attributes"] and box["structure_conclusion"] != "不通过", box
        assert box["outer_size_mm"]["length"] == 1350.0, box  # 贴货做，不套 3 m 模块长
    # 其余的件照旧进标准箱
    others = [b for b in boxes if b not in drums]
    assert all("标准箱库" in b["special_attributes"] and b["outer_size_mm"]["width"] == 1100.0 for b in others), others

    # 装得进标准箱的件不受影响：1000×450 的截面（加间隙 1050×500）侧放进得了 2 米铁架 1000×1650 的内腔
    fits = [_row(id="F", quantity=1, length_mm=1500, width_mm=1000, height_mm=450, weight_kg=100)]
    box = _solve_boxes(fits, container_type="40HQ")["boxes"][0]
    assert CUSTOM_SECTION_TAG not in box["special_attributes"] and "标准箱库" in box["special_attributes"], box

    # 引擎再放一件进比它小的箱里，三个工具都不给 ok=True
    real = agent_box_scheme

    def squeezed(state):
        out = dict(real(state))
        out["boxes"] = [dict(b, outer_size_mm=dict(b["outer_size_mm"], width=1100.0, height=1100.0))
                        if CUSTOM_SECTION_TAG in b["special_attributes"] else b for b in out["boxes"]]
        return out

    with patch("packing_assistant.agents.box_scheme.agent_box_scheme", squeezed):
        outs = (run_plan(materials=mats), draft_vgm(materials=mats), draft_booking(materials=mats))
    for out in outs:
        assert out["ok"] is False and out["error"] == NOT_CONSERVED, out
        assert "can_fit" not in out and "containers_used" not in out, out
    assert "截面" in " ".join(outs[0]["detail"]), outs[0]["detail"]


def test_unknown_container_type_is_refused() -> None:
    out = run_plan(materials=[_row(quantity=2)], container_type="53HC")  # 修复前静默按 40HQ 出方案
    assert out["ok"] is False and out["error"] == "unknown_container_type", out
    assert out["supported_container_types"] == ["20GP", "40GP", "40HQ", "45HQ"], out
    assert "can_fit" not in out and out["solver_connected"] is False
    assert draft_vgm(materials=[_row(quantity=2)], container_type="53HC")["error"] == "unknown_container_type"
    assert draft_booking(materials=[_row(quantity=2)], container_type="")["error"] == "unknown_container_type"
    assert run_plan(materials=[_row(quantity=2)], container_type="40hq")["ok"] is True  # 大小写不算不认识


def test_quantity_gate() -> None:
    for bad in (math.nan, math.inf, True, False, -3, 2.7, 0, "abc", "2.5"):
        out = run_plan(materials=[_row(quantity=bad)])  # 修复前：NaN/inf 崩溃，其余静默当成 1 或 2 件
        assert out["ok"] is False and out["error"] == "invalid_quantity", (bad, out)
    for good, pieces in ((3, 3), ("3", 3), (3.0, 3), ("3.0", 3), (None, 1), ("", 1)):
        out = run_plan(materials=[_row(quantity=good)])
        assert out["ok"] is True and out["conservation"]["pieces_in"] == pieces, (good, out)
    # `qty` 与 quantity 同义（cargo_feasibility 一直这么读）；修复前装箱只装 1 件
    out = run_plan(materials=[_row(qty=5)])
    assert out["conservation"]["pieces_in"] == 5 and out["conservation"]["kg_in"] == 250.0, out["conservation"]
    assert run_plan(materials=[_row(qty=-1)])["error"] == "invalid_quantity"


def test_max_containers_reaches_the_loader() -> None:
    """原先写进 packing_options.n_max，没有任何代码读它；拼柜器读的是 state.max_containers。"""
    from packing_assistant.agents import loader

    seen = {}
    real = loader.agent_loader

    def spy(state):
        seen.update(max_containers=state.get("max_containers"), n_max=(state.get("packing_options") or {}).get("n_max"))
        return real(state)

    with patch("packing_assistant.agents.loader.agent_loader", spy):
        _solve_boxes([_row(quantity=2)], max_containers=2)
    assert seen == {"max_containers": 2, "n_max": None}, seen


# ---- 仓库里的夹具：全部守恒 ---------------------------------------------------------------------

def _fixture_sets():
    for path in sorted((ROOT / "test/sim_materials").glob("*/materials.json")):
        doc = json.loads(path.read_text(encoding="utf-8"))
        yield f"sim/{path.parent.name}", doc.get("materials") or [], doc.get("packing_options_hint") or {}
    for path in sorted((ROOT / "test/generic_tables").glob("*/materials.csv")):
        yield f"generic/{path.parent.name}", load_materials(None, str(path))["materials"], {}
    for path in sorted((ROOT / "test/excel/synthetic").glob("*.xlsx")):
        yield f"excel/{path.stem}", load_materials(None, str(path))["materials"], {}
    for path in sorted((ROOT / "test/benchmarks/excel").glob("*.xlsx")):  # 含 CI 的 MCP 步骤用的 case_b
        yield f"bench/{path.stem}", load_materials(None, str(path))["materials"], {}
    monster = json.loads((ROOT / "test/phase0/over_payload_monster.json").read_text(encoding="utf-8"))
    yield "phase0/over_payload_monster", monster["materials"], {}


def test_every_tracked_fixture_conserves(show: bool = False, everything: bool = False) -> None:
    """只跑成箱（不拼柜）就能对账。修复前 59 个夹具里 9 个丢重量、另 1 个只丢件数；
    另有 6 个夹具共 69 条「货的截面比箱外廓大」，现在只剩比柜还大的那 3 条。"""
    from packing_assistant.tools.packing import CUSTOM_SECTION_TAG

    checked = split = custom = handling_refused = 0
    for name, mats, opts in _fixture_sets():
        if name.startswith("sim/t80_") and not everything:
            continue  # 300–570 行，成箱各 4–16 s；修复前后都守恒，另有 test_anchor_t80_long_mix.py 盯着
        scheme = agent_box_scheme({"materials": mats, "container_type": "40HQ", "packing_options": dict(opts)})
        if scheme.get("materials_incomplete"):
            assert not scheme["boxes"], name  # 缺尺寸：整票拒收，不是丢货
            if any(row.get("reason") == "unsupported_transport_requirements" for row in scheme.get("needs_human", [])):
                handling_refused += 1
                assert scheme["ship_ok"] is False, name
                assert any(row.get("requirements") for row in scheme["needs_human"]), name
            continue
        found = check_conservation(mats, scheme["boxes"])
        oversize = pack_ship_solve.rows_oversize_for_container(mats, "40HQ")
        # 比柜还大的件（run_plan 的闸门会先拦下）定制外廓也装不下，两种「货比箱大」都在预期之内
        bigger_than_box = ("content_exceeds_box", "content_section_exceeds_box")
        real = [v for v in found["violations"] if not (v["kind"] in bigger_than_box and oversize)]
        assert not real, (name, violation_sentences(found))
        checked += 1
        split += bool(found["mass_split_rows"])
        n_custom = sum(CUSTOM_SECTION_TAG in (b.get("special_attributes") or []) for b in scheme["boxes"])
        custom += bool(n_custom)
        if show:
            refused = sum(v["kind"] in bigger_than_box for v in found["violations"])
            print(f"  {name:40s} pieces {found['pieces_in']:>5} → {found['pieces_out']:>5}  "
                  f"kg {found['kg_in']:>10} → {found['kg_out']:>10}  boxes {found['n_boxes']:>4}"
                  f"{'  mass-split ' + str(len(found['mass_split_rows'])) + ' rows' if found['mass_split_rows'] else ''}"
                  f"{'  custom-section ' + str(n_custom) if n_custom else ''}"
                  f"{'  bigger-than-box ' + str(refused) + ' (oversize row, refused by the gate)' if refused else ''}")
    # Explicit fragile/upright/no-stack fixtures must be refused before automatic
    # boxing. They remain covered fixtures; missing rows may not simply disappear.
    assert checked + handling_refused >= (59 if everything else 50), (checked, handling_refused)
    assert handling_refused >= 2, handling_refused
    assert split >= 5, split  # 夹具里确实有走质量拆分的，这个测试不是空转
    assert custom >= (6 if everything else 5), custom  # 同上：确实有夹具走按货定制


def main() -> int:
    show = "--numbers" in sys.argv
    tests = [
        test_repro_carries_every_unit,
        test_run_plan_reports_the_account,
        test_mass_split_unit_level,
        test_split_weight_follows_row_total,
        test_passthrough_one_crate_per_unit,
        test_checker_catches_each_kind_of_loss,
        test_plan_fails_when_engine_loses_cargo,
        test_oversize_is_refused_not_clamped,
        test_piece_wider_than_any_standard_crate_gets_a_custom_outer,
        test_unknown_container_type_is_refused,
        test_quantity_gate,
        test_max_containers_reaches_the_loader,
    ]
    for test in tests:
        test()
        print(f"PASS {test.__name__}")
    test_every_tracked_fixture_conserves(show, "--all" in sys.argv)
    print("PASS test_every_tracked_fixture_conserves")
    print(f"{len(tests) + 1}/{len(tests) + 1} passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
