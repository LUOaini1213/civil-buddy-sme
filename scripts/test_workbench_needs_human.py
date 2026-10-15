#!/usr/bin/env python3
"""工作台路径：读不出件数、缺重量的行停下来问人，不按猜出来的件数或零重量成箱。

页面的真实走法是 POST /api/table/parse（store_session=1）→ 把回执里的行 POST /api/pipeline。
修复前（fix/table-mapper-invalid-quantity 94e115f 实测），下面这张表

    编号,品名,数量,单重(kg),长(mm),宽(mm),高(mm)
    A,件A,2.7,50,1200,400,300
    B,件B,0,50,1200,400,300
    C,件C,-3,50,1200,400,300
    D,件D,abc,50,1200,400,300

上传回执 ok=True、没有 needs_human，页面提示「可点表材料跑」；随后 A、C、D 各按 1 件成箱：
3 箱、1 个 40HQ、can_fit=True、ship_ok=True、phase=done，没有任何 error / warning。
#51 给这三行打的标记（quantity=0 + meta.quantity_invalid）到了 material_parser 被
`max(int(float(quantity or 1)), 1)` 读回 1 件；这条路径上没有任何一处调 rows_blocking_plan。

同一次复现还撞上两处从未定义的名字：gateway/app.py 有 8 处调 `_load_session`，仓库历史上没有
任何一次提交定义过它（`_SESSIONS.get(sid) or …` 的短路让它只在 session 不在内存时才炸）——
新会话第一次上传就是 HTTP 500；`/api/revise-nl` 引用了别的函数里的局部变量 `_notice`，每次必 500。

规则只有一套：pack_ship_solve.rows_blocking_plan（缺重量 / 缺尺寸 / 数量不可用），上传回执、
material_parser、box_scheme 三处调的是同一个函数。

用法：python scripts/test_workbench_needs_human.py            （CI，约 10 s）
      python scripts/test_workbench_needs_human.py --numbers  （另外打印夹具计数）
"""
from __future__ import annotations

import ast
import builtins
import json
import math
import os
import sys
import uuid
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("PACKING_SKIP_SKJOLBER", "1")

from fastapi.testclient import TestClient  # noqa: E402

from gateway.app import app  # noqa: E402
from packing_assistant.agents.box_scheme import agent_box_scheme  # noqa: E402
from packing_assistant.agents.material_parser import _normalize_llm_materials, agent_material_parser  # noqa: E402
from packing_assistant.harness import public_response  # noqa: E402
from packing_assistant.tools.pack_ship_solve import load_materials, rows_blocking_plan, rows_needing_human  # noqa: E402

CLIENT = TestClient(app, raise_server_exceptions=False)
HEADER = "编号,品名,数量,单重(kg),长(mm),宽(mm),高(mm)"
REPRO = [HEADER, "A,件A,2.7,50,1200,400,300", "B,件B,0,50,1200,400,300",
         "C,件C,-3,50,1200,400,300", "D,件D,abc,50,1200,400,300"]
PAGE_OPTIONS = {"profile_id": "generic_table", "crate_passthrough": True, "multi_start": True, "cog_aware": True}


def _sid() -> str:
    return "needs-human-" + uuid.uuid4().hex[:10]  # 每次新会话：不在内存里，正是 _load_session 会炸的那种


def _upload(lines: List[str], sid: str) -> Dict[str, Any]:
    r = CLIENT.post("/api/table/parse", files={"file": ("list.csv", ("\n".join(lines) + "\n").encode("utf-8"), "text/csv")},
                    data={"session_id": sid, "store_session": "1"})
    assert r.status_code == 200, (r.status_code, r.text[:300])  # 修复前：新会话 500（NameError: _load_session）
    return r.json()


def _pipeline(materials: List[Dict[str, Any]], sid: str, **extra: Any) -> Dict[str, Any]:
    r = CLIENT.post("/api/pipeline", json={"user_input": "通用材料表装柜", "session_id": sid, "materials": materials,
                                           "container_type": "40HQ", "enable_auto_confirm": True,
                                           "packing_options": PAGE_OPTIONS, **extra})
    assert r.status_code == 200, (r.status_code, r.text[:300])
    return r.json()


def _row(**over: Any) -> Dict[str, Any]:
    row = {"id": "R1", "name": "件", "quantity": 2, "weight_kg": 50, "length_mm": 1200, "width_mm": 400, "height_mm": 300}
    row.update(over)
    return row


def test_upload_asks_and_does_not_store() -> None:
    sid = _sid()
    body = _upload(REPRO, sid)
    assert body["ok"] is False and body["stored"] is False, body  # 修复前 ok=True、stored=True
    assert [(r["id"], r["reason"], r["raw"]) for r in body["needs_human"]] == [
        ("A", "invalid_quantity", "2.7"), ("C", "invalid_quantity", "-3"), ("D", "invalid_quantity", "abc")], body["needs_human"]
    assert "「abc」" in body["errors"][0] and body["errors"][0].startswith("3 处需要人工处理"), body["errors"]
    assert [m["id"] for m in body["materials"]] == ["A", "C", "D"]  # 行照样回给页面看
    assert CLIENT.get(f"/api/session/{sid}").status_code == 404    # 没写进 session

    rows = [{"品名": "件A", "数量": "2.7", "单重(kg)": 50, "长(mm)": 1200, "宽(mm)": 400, "高(mm)": 300}]
    via_json = CLIENT.post("/api/table/parse/json", json={"rows": rows, "session_id": _sid(), "store_session": True}).json()
    assert via_json["ok"] is False and via_json["stored"] is False, via_json
    assert [r["reason"] for r in via_json["needs_human"]] == ["invalid_quantity"], via_json


def test_pipeline_refuses_marked_rows_even_if_the_client_posts_them() -> None:
    marked = _upload(REPRO, _sid())["materials"]
    out = _pipeline(marked, _sid())
    pub, summary = out["public"], out["summary"]
    # 修复前：boxes=3、containers_used=1、can_fit=True、ship_ok=True，三行各 1 件
    assert summary["boxes"] == 0 and summary["ship_ok"] is False and not summary["can_fit"], summary
    assert [(m["id"], m["quantity"]) for m in pub["materials"]] == [("A", 0), ("C", 0), ("D", 0)], pub["materials"]
    assert [r["id"] for r in pub["needs_human"]] == ["A", "C", "D"] and pub["materials_incomplete"] is True
    banner = pub["error_banner"]
    assert banner["level"] == "block" and banner["title"] == "⛔ 3 行需要人工处理", banner
    assert any("「2.7」" in line for line in banner["lines"]) and "缺尺寸" not in banner["title"], banner
    assert any(e.startswith("materials_need_human") for e in pub["errors"]), pub["errors"]


def test_pipeline_refuses_unmarked_array_rows() -> None:
    """不经过上传、直接给数组的调用方：没有标记，数值本身过不了同一条规则。"""
    measured_before = {  # 94e115f 上逐个 POST /api/pipeline 的实测
        2.7: "2 箱，ship_ok=True",
        -3: "1 箱，ship_ok=True（另有一条净重对不上的 warning）",
        0: "1 箱，ship_ok=True",
        "abc": "四个节点各抛一次 ValueError，横幅「运行报错」+ Python 异常原文，没说是哪一行",
        "inf": "同上，OverflowError",  # JSON 里没有 inf；字符串走同一条 float() 路
    }
    for bad in measured_before:
        out = _pipeline([_row(quantity=bad)], _sid())
        assert out["summary"]["boxes"] == 0 and out["summary"]["ship_ok"] is False, (bad, out["summary"])
        assert [r["reason"] for r in out["public"]["needs_human"]] == ["invalid_quantity"], (bad, out["public"]["needs_human"])
        assert out["public"]["error_banner"]["title"] == "⛔ 1 行需要人工处理", (bad, out["public"]["error_banner"])
        assert not any("could not convert" in e or "cannot convert" in e for e in out["public"]["errors"]), out["public"]["errors"]
    good = _pipeline([_row(quantity="3")], _sid())
    assert [(m["id"], m["quantity"]) for m in good["public"]["materials"]] == [("R1", 3)], good["public"]["materials"]
    assert good["summary"]["boxes"] == 3 and good["public"]["needs_human"] == [], good["summary"]
    # 不在本次范围：只写 `qty: 5` 的行在这条路径上仍按 1 件（material_parser 不读 qty，实测 1 箱）。
    # 这里不断言它——既不把缺陷钉进 CI，也不顺手改：一改 fan-out 喂的件数就变了，见 PR 说明。


def test_missing_weight_is_asked_about_too() -> None:
    """决定：这条路径上缺重量同样停下来问。修复前 0 kg 的行照常成箱拼柜，载重利用率与 VGM 建在零质量上。"""
    sid = _sid()
    body = _upload([HEADER, "A,件A,2,,1200,400,300", "B,件B,3,50,1200,400,300"], sid)
    assert body["ok"] is False and [(r["id"], r["reason"]) for r in body["needs_human"]] == [("A", "missing_weight")], body
    out = _pipeline(body["materials"], _sid())
    assert out["summary"]["boxes"] == 0 and out["summary"]["ship_ok"] is False, out["summary"]  # 修复前 boxes=5、ship_ok=True
    assert out["public"]["error_banner"]["title"] == "⛔ 1 行需要人工处理", out["public"]["error_banner"]


def test_missing_dimensions_block_is_unchanged() -> None:
    """缺尺寸原来就拦（materials_incomplete → blocked_missing_dims）：横幅、模式、errors 一字不变，只多了逐行的 needs_human。"""
    table = ROOT / "test/generic_tables/G7_missing_dims/materials.csv"
    body = CLIENT.post("/api/table/parse", files={"file": ("materials.csv", table.read_bytes(), "text/csv")},
                       data={"session_id": _sid(), "store_session": "1"}).json()
    assert body["ok"] is False and {r["reason"] for r in body["needs_human"]} == {"missing_dimensions"}, body
    out = _pipeline(body["materials"], _sid())
    pub = out["public"]
    assert pub["error_banner"]["title"] == "⛔ 缺尺寸 / 材料不完整", pub["error_banner"]
    assert "box_scheme_blocked: materials_missing_dims" in pub["errors"], pub["errors"]
    assert not any(e.startswith("materials_need_human") for e in pub["errors"]), pub["errors"]
    assert out["summary"]["boxes"] == 0 and out["summary"]["ship_ok"] is False


def test_corrected_table_runs_the_written_counts() -> None:
    sid = _sid()
    body = _upload([HEADER, "A,件A,3,50,1200,400,300", "B,件B,0,50,1200,400,300",
                    "C,件C,3 EA,50,1200,400,300", "D,件D,,50,1200,400,300"], sid)
    assert body["ok"] is True and body["stored"] is True and body["needs_human"] == [], body
    assert CLIENT.get(f"/api/session/{sid}").status_code == 200
    out = _pipeline(body["materials"], sid)
    assert [(m["id"], m["quantity"]) for m in out["public"]["materials"]] == [("A", 3), ("C", 3), ("D", 1)]
    assert out["summary"]["boxes"] == 7 and out["summary"]["ship_ok"] is not False, out["summary"]  # 3 + 3 + 1，B 明写 0 不发
    assert out["public"]["needs_human"] == [] and out["public"]["error_banner"]["level"] != "block"


def test_normalisation_keeps_the_evidence() -> None:
    rows = [_row(id="a", quantity=2.7), _row(id="b", quantity="abc"), _row(id="c", quantity=math.nan),
            _row(id="d", quantity=0, meta={"quantity_invalid": True, "quantity_raw": "-3", "source": "csv"}),
            _row(id="e", quantity="3"), _row(id="f", quantity=None), _row(id="g", quantity=None, 数量=4)]
    given = json.dumps(rows, default=str, ensure_ascii=False)
    out = _normalize_llm_materials(rows)  # 修复前："abc" 与 NaN 在这里抛异常
    assert [(m["id"], m["quantity"]) for m in out] == [("a", 0), ("b", 0), ("c", 0), ("d", 0), ("e", 3), ("f", 1), ("g", 4)], out
    assert [m["meta"]["quantity_raw"] for m in out[:4]] == ["2.7", "abc", "nan", "-3"], out  # 已有的原始格不被覆盖
    assert out[3]["meta"]["source"] == "csv" and all(m["total_weight_kg"] == 0.0 for m in out[:4])
    assert json.dumps(rows, default=str, ensure_ascii=False) == given  # 不改调用方的行


def test_box_scheme_backstop_without_the_parser() -> None:
    """保留材料的调整指令、graph 模式、直接调节点都不经过 material_parser 的闸门。"""
    out = agent_box_scheme({"materials": [_row(quantity=0, meta={"quantity_invalid": True, "quantity_raw": "2.7"})],
                            "packing_options": dict(PAGE_OPTIONS)})
    assert out["boxes"] == [] and out["ship_ok"] is False and out["team_a_summary"]["packing_mode"] == "blocked_needs_human", out
    assert out["errors"] == ["box_scheme_blocked: materials_need_human"] and "「2.7」" in out["messages"][0]["content"]
    banner = public_response({**out, "materials": []})["error_banner"]
    assert banner["title"] == "⛔ 1 行需要人工处理", banner

    retained = agent_material_parser({"materials": [_row(quantity=-3)], "adjust_note": "去掉 无关件", "user_input": ""})
    assert "needs_human" not in retained  # 这一支原样返回材料，不做闸门——所以 box_scheme 必须自己拦
    assert agent_box_scheme({"materials": retained["materials"]})["team_a_summary"]["packing_mode"] == "blocked_needs_human"

    graph = _pipeline([_row(quantity=2.7)], _sid(), mode="graph")  # 修复前 graph 模式：2 箱，ship_ok=True
    assert graph["summary"]["boxes"] == 0 and graph["summary"]["ship_ok"] is False, graph["summary"]
    assert [r["reason"] for r in graph["public"]["needs_human"]] == ["invalid_quantity"], graph["public"]["needs_human"]
    assert _pipeline([_row(quantity=3)], _sid(), mode="graph")["summary"]["boxes"] == 3  # 好的行照常成箱


def test_weight_rule_reads_the_keys_the_engine_reads() -> None:
    chinese = {"名称": "H型钢柱", "数量": 4, "单重_kg": 85, "外尺寸_mm": {"长": 3800, "宽": 400, "高": 200}}
    assert rows_blocking_plan([chinese]) == [], rows_blocking_plan([chinese])  # 修复前判成 missing_weight，引擎其实读得到 85
    assert rows_needing_human([{"name": "x", "总重_kg": 340}]) == []
    for unusable in ({"weight_kg": 0, "单重_kg": 0}, {"单重_kg": "abc"}, {"weight_kg": True}, {"总重_kg": -3, "weight_kg": 12.5}, {}):
        assert [r["reason"] for r in rows_needing_human([{"name": "x", **unusable}])] == ["missing_weight"], unusable


# ---- 网关里没有定义过的名字 -------------------------------------------------------------------

_SCOPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)


def _own(scope: ast.AST):
    stack = list(ast.iter_child_nodes(scope))
    while stack:
        node = stack.pop()
        yield node
        if isinstance(node, _SCOPES):  # 嵌套作用域的函数体不属于这一层；装饰器、默认值、基类属于
            stack += list(getattr(node, "decorator_list", [])) + list(getattr(node, "bases", []))
            if hasattr(node, "args"):
                stack += [d for d in node.args.defaults + node.args.kw_defaults if d is not None]
            continue
        stack += list(ast.iter_child_nodes(node))


def _bound(scope: ast.AST) -> set:
    names = set()
    if hasattr(scope, "args"):
        a = scope.args
        names |= {x.arg for x in a.posonlyargs + a.args + a.kwonlyargs + [y for y in (a.vararg, a.kwarg) if y]}
    for node in _own(scope):
        if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            names.add(node.id)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            names |= {(alias.asname or alias.name).split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ExceptHandler) and node.name:
            names.add(node.name)
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            names |= set(node.names)
    return names


def _undefined_names(path: Path) -> List[tuple]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found = set()

    def visit(scope: ast.AST, visible: set) -> None:
        here = visible if isinstance(scope, ast.ClassDef) else visible | _bound(scope)
        local = here | _bound(scope)
        for node in _own(scope):
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load) and node.id not in local:
                found.add((node.lineno, node.id, getattr(scope, "name", "<lambda>")))
            if isinstance(node, _SCOPES):
                visit(node, here)

    top = _bound(tree) | set(dir(builtins)) | {"__file__", "__name__", "__doc__"}
    for node in _own(tree):
        if isinstance(node, _SCOPES):
            visit(node, top)
    return sorted(found)


def test_gateway_has_no_undefined_names() -> None:
    """修复前 9 处：_load_session × 8（8 个路由）+ api_revise_nl 里的 _notice。"""
    assert _undefined_names(ROOT / "gateway/app.py") == []
    for path in ("packing_assistant/agents/material_parser.py", "packing_assistant/agents/box_scheme.py"):
        assert _undefined_names(ROOT / path) == [], path


def test_session_routes_answer_for_a_session_not_in_memory() -> None:
    unknown = _sid()
    for path, body, status in (("/api/export/shipment", {"session_id": unknown}, 404),
                               ("/api/checklist", {"session_id": unknown}, 404),
                               ("/api/p2/evidence", {"session_id": unknown}, 404),
                               ("/api/whatif/apply", {"session_id": _sid(), "whatif_session_id": unknown}, 404),
                               ("/api/p2/vgm-submit", {"session_id": unknown}, 200)):
        r = CLIENT.post(path, json=body)
        assert r.status_code == status, (path, r.status_code, r.text[:200])  # 修复前全部 500
    r = CLIENT.post("/api/revise-nl", json={"session_id": _sid(), "instruction": "只准 1 个柜", "rerun_team_a": False})
    assert r.status_code == 200 and "revise_status" in r.json(), (r.status_code, r.text[:200])  # 修复前每次 500


# ---- 夹具：干净的表一张都不该被拦 ---------------------------------------------------------------

def _fixture_lists():
    for path in sorted((ROOT / "test/generic_tables").glob("*/materials.csv")):
        yield f"generic/{path.parent.name}", load_materials(None, str(path))["materials"]
    for path in sorted((ROOT / "test/excel/synthetic").glob("*.xlsx")):
        yield f"excel/{path.stem}", load_materials(None, str(path))["materials"]
    for path in sorted((ROOT / "test/benchmarks/excel").glob("*.xlsx")):
        yield f"bench/{path.stem}", load_materials(None, str(path))["materials"]
    for path in sorted((ROOT / "test/sim_materials").glob("*/materials.json")):
        yield f"sim/{path.parent.name}", json.loads(path.read_text(encoding="utf-8")).get("materials") or []


def test_numeric_history_and_current_transport_policy(show: bool = False) -> None:
    """2026-09-21 历史记录：60 张表 / 4129 行，只有两张缺尺寸而停止，数量归一化不变。

    2026-09-30 增加运输约束后，两张原有模拟表另因明确禁翻/禁叠停止；不改原表，
    不把这个新政策结果回写成旧运行的成绩，也不将其归因于 fragile 一词。"""
    stopped, rows, changed, transport_evidence = {}, 0, 0, {}
    for name, mats in _fixture_lists():
        rows += len(mats)
        normalised = _normalize_llm_materials([dict(m) for m in mats])
        for raw, norm in zip(mats, normalised):
            before = max(int(float(raw.get("quantity") or raw.get("数量") or 1)), 1)  # 94e115f 的读法
            changed += before != norm["quantity"]
        blocking = rows_blocking_plan(normalised)
        if blocking:
            stopped[name] = sorted({r["reason"] for r in blocking})
            for requirement in blocking:
                if requirement["reason"] == "unsupported_transport_requirements":
                    transport_evidence[requirement["id"]] = requirement["requirements"]
    historical_numeric_stops = {"generic/G7_missing_dims": ["missing_dimensions"],
                                "sim/ns_missing_dims_mix": ["missing_dimensions"]}
    current_transport_stops = {"sim/ns_fragile_process": ["unsupported_transport_requirements"],
                              "sim/ns_mixed_industry_bundle": ["unsupported_transport_requirements"]}
    assert stopped == historical_numeric_stops | current_transport_stops, stopped
    assert transport_evidence["G1"]["this_side_up"] is True, transport_evidence
    assert transport_evidence["G2"]["stackable"] is False, transport_evidence
    assert transport_evidence["M4"]["no_stack"] is True, transport_evidence
    assert changed == 0, changed
    if show:
        print(f"  lists={sum(1 for _ in _fixture_lists())} rows={rows} stopped={stopped} quantities_changed={changed}")


def main() -> int:
    show = "--numbers" in sys.argv[1:]
    tests = [
        test_upload_asks_and_does_not_store,
        test_pipeline_refuses_marked_rows_even_if_the_client_posts_them,
        test_pipeline_refuses_unmarked_array_rows,
        test_missing_weight_is_asked_about_too,
        test_missing_dimensions_block_is_unchanged,
        test_corrected_table_runs_the_written_counts,
        test_normalisation_keeps_the_evidence,
        test_box_scheme_backstop_without_the_parser,
        test_weight_rule_reads_the_keys_the_engine_reads,
        test_gateway_has_no_undefined_names,
        test_session_routes_answer_for_a_session_not_in_memory,
    ]
    for test in tests:
        test()
        print(f"[OK] {test.__name__}")
    test_numeric_history_and_current_transport_policy(show)
    print("[OK] test_numeric_history_and_current_transport_policy")
    print("WORKBENCH NEEDS HUMAN PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
