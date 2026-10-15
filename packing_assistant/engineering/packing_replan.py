"""Hash-bound, read-only packing experiments. No model, export or free option delta.

The v1 lane explicitly keeps generated boxes in their original orientation on the
floor. The existing boxing/loader algorithms remain the baseline; the only new
search candidates are two fixed box priority orders. Critic options are evidence,
never executable parameters. Every returned feasible layout is checked again.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import math
import os
from pathlib import Path
import re

MAX_SOURCE = 2 * 1024 * 1024
MAX_ROUNDS = 2
TOP_FIELDS = {"schema", "container_type", "max_containers", "clearance_mm",
              "max_box_net_kg", "layout_policy", "materials"}
ROW_FIELDS = {"id", "name", "spec", "length_mm", "width_mm", "height_mm", "weight_kg",
              "total_weight_kg", "quantity", "note", "orientation", "stacking", "handling_requirements",
              "package_type", "upright", "this_side_up", "no_stack", "stackable", "a_frame", "fragile"}
UNSUPPORTED_HANDLING_FIELDS = {"handling", "handling_instructions", "transport_requirements", "shipping_instructions"}
LIMITATIONS = [
    "Geometry and declared weight only; not shipping release, engineering approval or professional signoff.",
    "Initial boxes use the existing standard boxing rules; their structure status is reported separately.",
    "Fixed box orientation and floor-only layout; securing, lifting and vehicle stability are not verified.",
    "Only two predefined priority-order experiments; no optimality or improvement guarantee.",
]


def _hash(value):
    data = value if isinstance(value, bytes) else json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False).encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def _unique(pairs):
    out = {}
    for key, value in pairs:
        if key in out:
            raise ValueError("Duplicate JSON field")
        out[key] = value
    return out


def _number(value, low, high, *, integer=False):
    return type(value) in ((int,) if integer else (int, float)) and math.isfinite(value) and low <= value <= high


def _validate(document):
    if not isinstance(document, dict) or set(document) != TOP_FIELDS or document.get("schema") != "packing_replan.v1":
        raise ValueError("Use packing_replan.v1 with its explicit container, capacity, gap, boxing and layout fields; arbitrary options/state are forbidden")
    if document["container_type"] not in {"20GP", "40GP", "40HQ", "45HQ"}:
        raise ValueError("Unsupported explicit container_type")
    if not _number(document["max_containers"], 1, 40, integer=True):
        raise ValueError("max_containers must be an integer from 1 to 40")
    if not _number(document["clearance_mm"], 0, 80, integer=True):
        raise ValueError("clearance_mm must be an integer from 0 to 80; it is never reduced")
    if not _number(document["max_box_net_kg"], 1, 10000):
        raise ValueError("max_box_net_kg must be an explicit positive value no greater than 10000")
    if document["layout_policy"] != {"orientation": "fixed", "stacking": "floor_only"}:
        raise ValueError("v1 supports only explicitly declared fixed orientation and floor_only layout")
    rows = document["materials"]
    if not isinstance(rows, list) or not 1 <= len(rows) <= 40:
        raise ValueError("Select 1 to 40 material rows")
    ids = set()
    for row in rows:
        if isinstance(row, dict) and (aliases := set(row) & UNSUPPORTED_HANDLING_FIELDS):
            raise ValueError("Unsupported transport requirement field(s): " + ", ".join(sorted(aliases))
                             + "; preserve all original requirement text under handling_requirements for human review; do not remove the restrictions")
        if not isinstance(row, dict) or set(row) - ROW_FIELDS:
            raise ValueError("Unsupported material field; source constraints cannot be silently ignored")
        ident = row.get("id")
        if not isinstance(ident, str) or not ident or len(ident) > 80 or ident in ids:
            raise ValueError("Every material requires a distinct nonempty id of at most 80 characters")
        ids.add(ident)
        if any(isinstance(v, (dict, list)) or isinstance(v, str) and len(v) > 2000 for v in row.values()):
            raise ValueError("Material values must be bounded scalar source fields")
    # Missing/invalid dimensions, quantities and weight are handled by the
    # existing source-preserving needs_human gate, never by numeric defaults.
    return rows


def _summary(plan, boxes, constraints, structure):
    from packing_assistant.logistics.packing import verify_packaged_layout
    checked = verify_packaged_layout(boxes, plan, constraints["container_type"], constraints["max_containers"])
    gap = constraints["clearance_mm"]
    layout = plan.get("layout") or []
    if checked:
        for index, item in enumerate(layout):
            for other in layout[index + 1:]:
                if item["container_no"] != other["container_no"]:
                    continue
                # In the floor-only lane one horizontal axis must contain the
                # entire declared clearance. No solver policy echo is evidence.
                if not any(item["position"][axis] + item["size"][dim] + gap <= other["position"][axis] + 1e-6
                           or other["position"][axis] + other["size"][dim] + gap <= item["position"][axis] + 1e-6
                           for axis, dim in (("x", "dx"), ("y", "dy"))):
                    checked = False
    used = plan.get("containers_used")
    mid = plan.get("worst_mid50")
    return {"can_fit": plan.get("can_fit") is True and checked and "1d" not in str(plan.get("engine", "")).lower(),
            "solver_can_fit": plan.get("can_fit") is True, "layout_verified": checked,
            "containers_used": used if type(used) is int else None,
            "worst_mid50": mid if _number(mid, 0, 1) else None,
            "n_boxes": len(boxes), "engine": str(plan.get("engine") or "unknown"),
            "structure": deepcopy(structure), "plan_sha256": _hash(plan), "layout": deepcopy(layout),
            "solver_plan": deepcopy(plan)}


def _better(candidate, previous):
    if not candidate["can_fit"]:
        return False
    if not previous["can_fit"]:
        return True
    if candidate["containers_used"] != previous["containers_used"]:
        return candidate["containers_used"] < previous["containers_used"]
    new, old = candidate["worst_mid50"], previous["worst_mid50"]
    return new is not None and old is not None and new > old + 1e-6


def _solve(state):
    from packing_assistant.agents.loader import agent_loader
    # This is a fixed local numerical operation, including direct test callers.
    # The worker already has a scrubbed environment; never consult an HTTP packer.
    previous = os.environ.get("PACKING_SKIP_SKJOLBER")
    os.environ["PACKING_SKIP_SKJOLBER"] = "1"
    try:
        return (agent_loader(deepcopy(state)).get("container_plan") or {})
    finally:
        if previous is None:
            os.environ.pop("PACKING_SKIP_SKJOLBER", None)
        else:
            os.environ["PACKING_SKIP_SKJOLBER"] = previous


def _calculate(document, result):
    from packing_assistant.runtime.cancel import check, RunCancelled
    from packing_assistant.tools.pack_ship_solve import rows_blocking_plan, structure_summary
    from packing_assistant.agents.box_scheme import agent_box_scheme, effective_container_type
    from packing_assistant.tools.cargo_conservation import check_conservation
    from packing_assistant.tools.booking import compute_booking
    from packing_assistant.bounded_debate import run_bounded_debate

    materials = _validate(document)
    constraints = result["hard_constraints"]
    constraints.update({key: deepcopy(document[key]) for key in TOP_FIELDS - {"schema", "materials"}})
    result["constraints_sha256"] = _hash(constraints)
    needs = rows_blocking_plan(materials, document["container_type"], lang="en")
    if needs:
        result.update(ok=False, status="needs_human", outcome="needs_human", needs_human=needs)
        return
    if any(not _number(row.get("quantity"), 1, 200, integer=True) for row in materials) or sum(row["quantity"] for row in materials) > 200:
        raise ValueError("The bounded v1 lane accepts at most 200 pieces")
    if effective_container_type(materials, document["container_type"]) != document["container_type"]:
        raise ValueError("Existing boxing would change the explicit container type; change the source only after human review")
    options = {"standard_boxes": True, "force_dense_sheets": False, "crate_passthrough": False,
               "max_box_net_kg": document["max_box_net_kg"], "lock_max_containers": True,
               "container_budget": document["max_containers"], "clearance_mm": document["clearance_mm"],
               "max_stack_layers": 1, "prefer_stack": False, "export_strict": True}
    state = {"materials": deepcopy(materials), "container_type": document["container_type"],
             "max_containers": document["max_containers"], "packing_options": options}
    check()
    scheme = agent_box_scheme(deepcopy(state))
    boxes = deepcopy(scheme.get("boxes") or [])
    conservation = check_conservation(materials, boxes)
    if not boxes or len(boxes) > 200 or not conservation.get("ok"):
        result.update(ok=False, status="needs_human", outcome="needs_human",
                      needs_human=[{"reason": "boxing_not_conserved", "conservation": conservation}])
        return
    # Freeze generated outer dimensions and gross masses across every candidate.
    # Source handling constraints were checked before automatic boxing.
    for box in boxes:
        box.update(allowRotate=False, stackable=False, max_stack_layers=1, max_top_load_kg=0, prefer_bottom=True)
    if any(not _number(box.get("net_weight_kg"), .000001, document["max_box_net_kg"] + .1)
           or not _number(box.get("gross_weight_kg"), box.get("net_weight_kg", 0), 100000)
           for box in boxes):
        raise ValueError("Generated box has invalid mass or exceeds the source net mass limit")
    box_hash = _hash(boxes)
    booking = compute_booking(boxes=boxes, container_type=document["container_type"])
    state.update(boxes=boxes, booking=booking,
                 plan={"container_type": document["container_type"], "max_containers": document["max_containers"], "n0": booking["n0"]})
    structure = structure_summary(boxes)
    check()
    baseline_plan = _solve(state)
    baseline = _summary(baseline_plan, boxes, constraints, structure)
    result.update(baseline=baseline, final=deepcopy(baseline), boxes_sha256=box_hash,
                  boxes=deepcopy(boxes), conservation=conservation,
                  baseline_parameters={"priority_order": [], "packing_options": deepcopy(options), "boxes_sha256": box_hash})
    best_plan = baseline_plan
    # Parameters describe effective calls, not the critic's unsafe options_next.
    seen = {_hash({"priority_order": [], "packing_options": options, "boxes_sha256": box_hash})}
    candidates = [("size_desc", lambda b: (-math.prod(b["outer_size_mm"].values()), b["box_id"])),
                  ("weight_desc", lambda b: (-b["gross_weight_kg"], b["box_id"]))]
    for ident, order_key in candidates:
        check()
        critic_state = deepcopy(state)
        critic_state.update(container_plan=best_plan, replan_round=len(result["rounds"]))
        advice = run_bounded_debate(critic_state, max_rounds=2)
        proposal = advice.get("replan_proposal") or {}
        # A stop is honored. A box_scheme-only remedy cannot be implemented by
        # changing priorities, so no pretend reboxing or relaxed constraints.
        if proposal.get("stop") or proposal.get("route") == "box_scheme":
            result["stop_reason"] = "deterministic_stop" if proposal.get("stop") else "reboxing_requires_human"
            break
        priority = [box["box_id"] for box in sorted(boxes, key=order_key)]
        candidate_state = deepcopy(state)
        candidate_state["plan"]["priority_order"] = priority
        candidate_state["packing_options"]["drop_load_priority"] = False
        parameters_hash = _hash({"priority_order": priority, "packing_options": candidate_state["packing_options"], "boxes_sha256": box_hash})
        if parameters_hash in seen:
            continue
        seen.add(parameters_hash)
        try:
            plan = _solve(candidate_state)
            check()
            summary = _summary(plan, boxes, constraints, structure)
        except RunCancelled:
            raise
        except Exception as error:
            # Cancellation still propagates. A numerical candidate failure does
            # not erase the independently checked baseline or become improvement.
            check()
            result["rounds"].append({"round": len(result["rounds"]) + 1, "candidate_id": ident,
                "parameters_sha256": parameters_hash, "priority_order": priority, "summary": None,
                "accepted": False, "reason": "candidate_solver_failed", "error_type": type(error).__name__})
            continue
        accepted = _better(summary, result["final"])
        result["rounds"].append({"round": len(result["rounds"]) + 1, "candidate_id": ident,
            "parameters_sha256": parameters_hash, "priority_order": priority, "summary": summary,
            "packing_options": deepcopy(candidate_state["packing_options"]),
            "accepted": accepted, "reason": "verified_improvement" if accepted else "not_better_or_invalid",
            "critic": {"route": proposal.get("route"), "suggested_delta_keys": sorted((proposal.get("packing_options_delta") or {}).keys()),
                       "delta_applied": False, "outcome": (advice.get("bounded_debate") or {}).get("outcome")}})
        if accepted:
            result["final"], best_plan = summary, plan
    result["outcome"] = "improved" if any(r["accepted"] for r in result["rounds"]) else "unchanged"
    result["requires_human_review"] = not result["final"]["can_fit"] or any(
        structure.get(key, 0) for key in ("fail", "needs_reinforcement", "pending_design"))
    result.setdefault("stop_reason", "round_limit" if len(result["rounds"]) == MAX_ROUNDS else "no_new_candidate")


def run(workspace: Path, payload: dict) -> dict:
    """Called only by the registered host operation. Does not write any file."""
    from packing_assistant.host_worker import _checked_path
    if not isinstance(payload, dict) or set(payload) != {"source", "expected_sha256"}:
        raise ValueError("packing_replan accepts only a selected source and its host-bound hash")
    expected = payload["expected_sha256"]
    if not isinstance(expected, str) or not re.fullmatch(r"[a-f0-9]{64}", expected):
        raise ValueError("A host-bound SHA-256 is required")
    source = _checked_path(workspace, payload["source"])
    if source.suffix.lower() != ".json" or source.is_relative_to(workspace / ".civil-buddy"):
        raise ValueError("Select an original packing_replan.v1 JSON file")
    with source.open("rb") as stream:
        raw = stream.read(MAX_SOURCE + 1)
    if len(raw) > MAX_SOURCE:
        raise ValueError("Packing source exceeds 2 MiB")
    if _hash(raw) != expected:
        raise ValueError("Selected packing source changed since the task started")
    result = {"kind": "packing_replan", "schema": "packing_replan.result.v1", "ok": True,
              "status": "completed", "source": {"path": payload["source"], "sha256": expected}, "originals_unchanged": True,
              "baseline": None, "final": None, "rounds": [], "outcome": "unchanged", "needs_human": [],
              "hard_constraints": {"originals_immutable": True, "shipping_release": False, "professional_signoff": False,
                                   "max_rounds": MAX_ROUNDS, "container_upgrades": False, "fixed_boxes": True},
              "constraints_sha256": None, "limitations": LIMITATIONS.copy(), "artifacts": [], "professional_signoff": False}
    try:
        document = json.loads(raw, object_pairs_hook=_unique, parse_constant=lambda _: (_ for _ in ()).throw(ValueError("Nonfinite JSON")))
        _calculate(document, result)
    finally:
        # Includes cancellation/exceptions: a stale source never returns a plan.
        current_source = _checked_path(workspace, payload["source"])
        if current_source != source or not current_source.is_file():
            raise ValueError("Selected packing source changed during calculation; discard all results")
        with current_source.open("rb") as stream:
            current = stream.read(MAX_SOURCE + 1)
        if len(current) > MAX_SOURCE or _hash(current) != expected:
            raise ValueError("Selected packing source changed during calculation; discard all results")
    result["decision_context"] = {"phase": "packing_replan", "source_sha256": expected,
        "constraints_sha256": result["constraints_sha256"], "candidate_ids": ["baseline"] + ["round_" + str(r["round"]) for r in result["rounds"]],
        "deterministic_outcome": result["outcome"], "shipping_release": False}
    return result
