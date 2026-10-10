"""Canonical saved-plan bindings shared by the fixed API and host worker."""
from __future__ import annotations

import json

from .planning import validate_plan
from .planning_records import digest


def inputs(plan, method):
    if not isinstance(method, str) or method not in {"cpm", "resource"}:
        raise ValueError("已保存计划的计算方式无效；不会自动切换为 CPM。")
    return {"plan": validate_plan(plan), "method": method}


def context(project):
    payload = inputs(project["plan"], project["method"])
    # This string is also checked and hashed by Rust. Keeping the actual UTF-8
    # canonical bytes avoids float/exponent serialization drift across runtimes.
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                           allow_nan=False, separators=(",", ":"))
    source = project.get("import_source")
    return {"agent_selection": {"kind": "saved_planning", "project_id": project["id"],
            "revision": project["revision"], "source_sha256": source["source"]["sha256"] if source else None,
            "inputs_sha256": digest(payload), "confirmed_solid": False},
            "agent_inputs_json": canonical}


def calculate_saved(payload):
    if not isinstance(payload, dict) or set(payload) != {"plan", "method"}:
        raise ValueError("计划计算只接受已保存的 plan 和 method。")
    authorized = inputs(payload["plan"], payload["method"])
    if authorized != payload:
        raise ValueError("计划输入不是已保存的规范化快照。")
    if authorized["method"] == "resource":
        from .planning_optimize import optimize
        try:
            computed = optimize(authorized["plan"])
        except ImportError as exc:
            raise ValueError("资源排程依赖未就绪；当前资源方案已保留，不会改算 CPM。") from exc
    else:
        from .planning import calculate
        computed = calculate(authorized["plan"])
    if computed["plan"] != authorized["plan"]:
        raise ValueError("计算返回的计划输入与授权快照不一致。")
    return {"kind": "planning", "method": authorized["method"], **computed,
            "inputs_sha256": digest(authorized)}
