"""Confirmed ledger to the existing solver, keeping packages distinct from materials."""
from __future__ import annotations

import math
from packing_assistant.runtime.cancel import check


def _positive(value, integer=False):
    return type(value) in (int, float) and math.isfinite(value) and value > 0 and (not integer or int(value) == value)


def prepare(project, mode, container_type, max_containers):
    from .ledger import validate_document, audit_document, package_identities
    from packing_assistant.transport_constraints import ledger_issues, requirements
    if not project.get("confirmed"):
        raise ValueError("请先核对并确认当前版本台账；未确认台账不能进入装箱。")
    if mode not in {"packaged", "materials"} or container_type not in {"20GP", "40GP", "40HQ", "45HQ"}:
        raise ValueError("装箱模式或柜型无效。")
    if type(max_containers) is not int or not 1 <= max_containers <= 40:
        raise ValueError("用户柜数上限须为 1–40。")
    doc = validate_document(project["document"])
    errors = [r for r in audit_document(doc)["issues"] if r["severity"] == "error"]
    if errors:
        return {"ok": False, "error": "ledger_errors", "detail": "台账存在未解决错误。", "needs_human": errors}
    if not doc["rows"]:
        raise ValueError("台账没有货物行。")
    needs, boxes, materials, seen, constraints = [], [], [], set(), []
    identities, _ = package_identities(doc["rows"])
    for row in doc["rows"]:
        check()
        ident = row["id"]
        blocked = ledger_issues(row, mode)
        if blocked:
            needs.extend(blocked)
            continue
        required = ["length_mm", "width_mm", "height_mm", "gross_kg" if mode == "packaged" else "net_kg", "quantity"] + (["package_count"] if mode == "packaged" else [])
        missing = [f for f in required if not _positive(row.get(f), f in {"quantity", "package_count"})]
        if any(row.get("evidence", {}).get(f, {}).get("group") for f in required):
            missing.append("跨材料行共享数值未拆分为独立包装或构件，不能直接用于装载")
        if mode == "materials" and str(row.get("unit", "")).strip().lower() not in {"pc", "pcs", "ea", "件", "个", "piece", "pieces"}:
            missing.append("裸材料需要明确单件数量，不能把米、卷或套直接当构件件数")
        scope = "package" if mode == "packaged" else "item"
        if row.get("dimension_scope") != scope:
            missing.append("dimension_scope=" + scope)
        if row.get("weight_scope") != scope:
            missing.append("weight_scope=" + scope)
        if mode == "materials" and (row.get("package_id") not in (None, "", "UNSPECIFIED") or row.get("package_count") not in (None, "", "UNSPECIFIED")):
            missing.append("已包装行不能再次成箱；请选择已包装箱拼柜")
        identity = identities[ident]
        if mode == "packaged" and identity is not None:
            if identity in seen:
                missing.append("同一箱号跨多行，须先明确一箱的外廓与总毛重，不能把材料行重复当箱")
            seen.add(identity)
        if missing:
            needs.append({"row_id": ident, "fields": missing, "message": "缺失、无效或口径未明确：" + "、".join(missing)})
            continue
        if row["quantity"] > 5000 or (mode == "packaged" and row["package_count"] > 200):
            needs.append({"row_id": ident, "message": "单次最多 200 个包装箱或 5000 件材料，请拆分台账。"})
            continue
        if mode == "packaged":
            declared, _ = requirements(row)
            constraints.append({"row_id": ident, "declared": declared, "handling_requirements": row.get("handling_requirements", "UNSPECIFIED"),
                                "effective_orientation": "fixed", "effective_stacking": "floor_only",
                                "frame_mass_capacity_checked": bool(declared.get("frame")),
                                "not_checked": ["frame_structure", "stability", "securing", "lifting"]})
            for index in range(int(row["package_count"])):
                boxes.append({"box_id": f"{ident}-{index + 1}", "source_row_id": ident, "source_package_id": row.get("package_id"),
                              "source_container_id": identity[0] if identity else row.get("container_id"),
                              "box_type": "已包装箱", "outer_size_mm": {"length": row["length_mm"], "width": row["width_mm"], "height": row["height_mm"]},
                              "gross_weight_kg": row["gross_kg"], "stackable": False, "allowRotate": False,
                              "booking_volume_m3": row["length_mm"] * row["width_mm"] * row["height_mm"] / 1e9,
                              "max_stack_layers": 1, "prefer_bottom": True, "content": []})
        else:
            materials.append({"id": ident, "name": row.get("name", ""), "spec": row.get("spec", ""),
                              "length_mm": row["length_mm"], "width_mm": row["width_mm"], "height_mm": row["height_mm"],
                              "weight_kg": row["net_kg"], "quantity": int(row["quantity"])})
    if len(boxes) > 200 or sum(m["quantity"] for m in materials) > 5000:
        needs.append({"row_id": "", "message": "单次总量超过 200 箱 / 5000 件，请拆分台账。"})
    if needs:
        return {"ok": False, "error": "needs_human", "needs_human": needs, "solver_connected": False}
    return {"ok": True, "boxes": boxes, "materials": materials, "constraints": constraints}


def verify_packaged_layout(boxes, plan, container_type, max_containers):
    """Independently reject omission, duplicate boxes, altered sizes, rotation and stacking."""
    from packing_assistant.knowledge import container_inner_mm
    try:
        cab = container_inner_mm()[container_type]
        layout = plan["layout"]
        if not isinstance(layout, list) or len(layout) != len(boxes):
            return False
        by_id, seen, groups, weights = {b["box_id"]: b for b in boxes}, set(), {}, {}
        for item in layout:
            check()
            ident, number = item["box_id"], item["container_no"]
            if ident not in by_id or ident in seen or type(number) is not int or not 1 <= number <= max_containers:
                return False
            seen.add(ident)
            box, pos, size = by_id[ident], item["position"], item["size"]
            dims = box["outer_size_mm"]
            numbers = [pos[k] for k in ("x", "y", "z")] + [size[k] for k in ("dx", "dy", "dz")]
            if any(type(n) not in (int, float) or not math.isfinite(n) for n in numbers):
                return False
            if pos["z"] != 0 or any(not math.isclose(size[k], dims[d], abs_tol=1e-6) for k, d in (("dx", "length"), ("dy", "width"), ("dz", "height"))):
                return False
            if any(pos[p] < 0 or size[s] <= 0 or pos[p] + size[s] > cab[c] + 1e-6 for p, s, c in (("x", "dx", "L"), ("y", "dy", "W"), ("z", "dz", "H"))):
                return False
            for other in groups.setdefault(number, []):
                if all(pos[p] < other["position"][p] + other["size"][s] - 1e-6 and other["position"][p] < pos[p] + size[s] - 1e-6 for p, s in (("x", "dx"), ("y", "dy"), ("z", "dz"))):
                    return False
            groups[number].append(item)
            weights[number] = weights.get(number, 0) + box["gross_weight_kg"]
        return len(groups) == plan["containers_used"] and all(w <= cab["max_load_kg"] + 1e-6 for w in weights.values())
    except (KeyError, TypeError, ValueError):
        return False


def pack(project, mode, container_type, max_containers):
    prepared = prepare(project, mode, container_type, max_containers)
    if not prepared["ok"]:
        return prepared
    check()
    options = {"lock_max_containers": True, "container_budget": max_containers, "prefer_stack": False, "max_stack_layers": 1}
    if mode == "materials":
        from packing_assistant.tools.pack_ship_solve import run_plan
        result = run_plan(materials=prepared["materials"], container_type=container_type, max_containers=max_containers, packing_options=options)
        check()
        result["ok"] = bool(result.get("ok") and result.get("can_fit") is True and type(result.get("containers_used")) is int and 0 < result["containers_used"] <= max_containers)
        result["mode_note"] = "裸材料先按现有成箱规则生成包装，再拼柜；包装参数是引擎规则，须另行复核。"
        return result
    from packing_assistant.agents.loader import agent_loader
    from packing_assistant.tools.booking import compute_booking
    boxes = prepared["boxes"]
    booking = compute_booking(boxes=boxes, container_type=container_type)
    solved = agent_loader({"boxes": boxes, "container_type": container_type, "max_containers": max_containers,
                           "booking": booking, "plan": {"container_type": container_type, "max_containers": max_containers,
                           "n0": booking["n0"]}, "packing_options": options})
    check()
    plan = solved.get("container_plan") or {}
    success = plan.get("can_fit") is True and type(plan.get("containers_used")) is int and 0 < plan["containers_used"] <= max_containers
    if "1d" in str(plan.get("engine", "")).lower():
        success = False
    verified = verify_packaged_layout(boxes, plan, container_type, max_containers)
    success = success and verified
    return {"ok": success, "solver_connected": True, "can_fit": plan.get("can_fit", False), "boxes": boxes,
            "layout_verified": verified,
             "constraints": prepared["constraints"],
            "container_plan": plan, "n_boxes": len(boxes), "containers_used": plan.get("containers_used", "UNSPECIFIED"), "container_type": container_type,
             "detail": "已包装整体保持所填长宽高方向、地板单层排布，不重新成箱；允许旋转或堆叠也仍采用此保守计算范围。" if success else "引擎未得到满足当前上限的三维可用方案。",
             "mode_note": "仅核对外包络几何与声明重量/载荷；未校核架体结构、防倾稳定、绑扎系固或吊装，不是订舱或 VGM 签认。"}


def run_pack(project, mode, container_type, max_containers, *, timeout=60):
    """Fixed local solver in a killable process; no model/tool-supplied executable or path."""
    import json
    import os
    from pathlib import Path
    import subprocess
    import sys
    import tempfile
    import time
    prepared = prepare(project, mode, container_type, max_containers)
    if not prepared["ok"]:
        return prepared
    payload = json.dumps([project, mode, container_type, max_containers], ensure_ascii=False, allow_nan=False)
    if len(payload.encode()) > 16 * 1024 * 1024:
        raise ValueError("装箱输入超过 16 MiB。")
    check()
    with tempfile.TemporaryDirectory(prefix="civil-logistics-pack-") as folder:
        source, target = Path(folder) / "input.json", Path(folder) / "output.json"
        source.write_text(payload, encoding="utf-8")
        env = dict(os.environ)
        for key in list(env):
            if key.endswith("API_KEY") or key in {"CIVIL_TOKEN", "LLM_API_KEY"}:
                env.pop(key, None)
        env.update(PYTHON_DOTENV_DISABLED="1", PYTHONUTF8="1", PACKING_SKIP_SKJOLBER="1", OPENBLAS_NUM_THREADS="1", OMP_NUM_THREADS="1")
        process_options = {"creationflags": subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt" else {"start_new_session": True}
        process = subprocess.Popen([sys.executable, "-m", "packing_assistant.logistics.packing", str(source), str(target)],
                                   cwd=Path(__file__).resolve().parents[2], env=env, stdin=subprocess.DEVNULL,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **process_options)
        started = time.monotonic()
        try:
            while process.poll() is None:
                check()
                if time.monotonic() - started > timeout:
                    raise ValueError("装箱计算超过 60 秒，已停止本次进程；请拆分台账。")
                time.sleep(.05)
            check()
            if process.returncode or not target.is_file() or target.stat().st_size > 12 * 1024 * 1024:
                raise ValueError("装箱进程未返回有效有界结果。")
            result = json.loads(target.read_text(encoding="utf-8"))
            if "worker_error" in result:
                raise ValueError(result["worker_error"])
            return result
        finally:
            if process.poll() is None:
                if os.name == "nt":
                    subprocess.run([str(Path(os.environ.get("SystemRoot", "C:/Windows")) / "System32/taskkill.exe"), "/PID", str(process.pid), "/T", "/F"],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10, check=False,
                                   creationflags=subprocess.CREATE_NO_WINDOW)
                else:
                    import signal
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                if process.poll() is None:
                    process.kill()
            process.wait(timeout=10)


if __name__ == "__main__":
    import json
    from pathlib import Path
    import sys
    source, target = map(Path, sys.argv[1:])
    try:
        if source.stat().st_size > 16 * 1024 * 1024:
            raise ValueError("装箱输入超限。")
        result = pack(*json.loads(source.read_text(encoding="utf-8")))
    except Exception as exc:
        result = {"worker_error": str(exc)[:1200] or type(exc).__name__}
    encoded = json.dumps(result, ensure_ascii=False, allow_nan=False)
    if len(encoded.encode()) > 12 * 1024 * 1024:
        encoded = json.dumps({"worker_error": "装箱结果超过 12 MiB。"}, ensure_ascii=False)
    target.write_text(encoded, encoding="utf-8")
