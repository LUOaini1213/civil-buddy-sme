"""Model-free native engineering acceptance through the authenticated Rust host.

Fresh loopback instances and synthetic data only. No running product, provider,
real project, OS installation or deployment is touched. Requires installed core,
CAD and engineering dependencies, and an explicitly selected compiled binary.
"""
from __future__ import annotations

import argparse
import base64
from copy import deepcopy
from hashlib import sha256
import importlib.util
import json
import math
import os
from pathlib import Path
import sys
import time
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "scripts")]
from scripts import native_engineering_acceptance as gate
from scripts.test_unified_runtime_http import Instance, json_request, request, require

CHECKS = (
    "authenticated_model_free_native_capabilities", "same_origin_required",
    "actual_frame_matches_analytic_reaction", "actual_hollow_section_and_source_hash",
    "actual_ifc_ids_and_diff_with_source_hashes", "exact_export_acknowledgement",
    "successful_runs_only_and_bad_inputs_preserve_records", "revision_conflict_and_history",
    "cross_account_projects_and_run_cache_isolated", "real_restart_and_cookie_revocation",
    "saved_native_results_and_sources_restored", "section_recomputed_after_cache_reset",
    "both_process_families_stopped", "tested_sources_unchanged",
)


def preflight():
    dependencies = gate.dependency_snapshot()
    errors = gate.preflight_errors(dependencies)
    errors += [f"Missing core dependency: {module}" for module in
               ("uvicorn", "langgraph", "langchain_core", "openpyxl", "pypdf")
               if importlib.util.find_spec(module) is None]
    if sys.flags.optimize:
        errors.append("Run Python without -O")
    return dependencies, errors


def source_snapshot():
    sources = gate.source_snapshot()
    for name in ("scripts/native_engineering_runtime.py", "scripts/test_unified_runtime_http.py",
                 "scripts/unified_acceptance.py", "scripts/start_unified_workbench.py",
                 "demo/domain_service.py"):
        path = ROOT / name
        sources[name] = sha256(path.read_bytes()).hexdigest() if path.is_file() else None
    return sources


def multipart_dxf(raw):
    boundary = "civil-native-" + uuid4().hex
    body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"synthetic-section.dxf\"\r\n"
            "Content-Type: application/dxf\r\n\r\n").encode() + raw + f"\r\n--{boundary}--\r\n".encode()
    return body, "multipart/form-data; boundary=" + boundary


def beam_reaction(model):
    require(model["source"] == "synthetic" and not model["nodal_loads"], "Unexpected beam fixture")
    load = model["member_loads"][0]
    require(load["direction"] == "FY" and load["w1_N_m"] == load["w2_N_m"], "Expected uniform vertical fixture")
    factor = model["combinations"][0]["factors"][load["case_id"]]
    return -load["w1_N_m"] * (load["x2_m"] - load["x1_m"]) * factor


def run(binary: Path, output: Path):
    output.mkdir(parents=True, exist_ok=False)
    dependencies, errors = preflight()
    sources = source_snapshot()
    errors += [f"Missing acceptance source: {name}" for name, digest in sources.items() if digest is None]
    report = {"schema": "civil-buddy.native-engineering-runtime.v1", "passed": False,
              "synthetic": True, "cross_computer": False, "real_provider_calls": False,
              "model_configured": False, "professional_approval": False,
              "binary": str(binary), "binary_sha256": sha256(binary.read_bytes()).hexdigest(),
              "source_commit": gate.source_commit(), "source_sha256": sources,
              "source_identity_note": "HEAD/package-declared commit plus hashes of actual tested files; not binary provenance",
              "python": sys.version.split()[0], "dependencies": dependencies,
              "transitive_versions": gate.transitive_versions(), "preflight_errors": errors, "checks": {}}
    instances = []
    started = time.monotonic()

    def checked(name):
        require(name in CHECKS, "Unregistered native runtime check")
        report["checks"][name] = True
        print("[PASS] " + name, flush=True)

    try:
        require(not errors, "Native runtime preflight failed: " + "; ".join(errors))
        first = Instance(binary, output / "alice", output / "state", output / "workspace-alice", "native_alice", None)
        second = Instance(binary, output / "bob", output / "state", output / "workspace-bob", "native_bob", None)
        instances.extend([first, second])
        first.start()
        require(request(first.base, "/api/engineering/capabilities")[0] == 401, "Native endpoint bypassed login")
        cookie = first.login()
        require(json_request(first.base, "/api/health", cookie=cookie)["has_key"] is False, "Model configuration was inherited")
        tools = json_request(first.base, "/api/engineering/capabilities", cookie=cookie)["tools"]
        for name, version in (("frame", "3.2.0"), ("section", "3.10.2"), ("ifc_check", "0.8.5"), ("ifc_diff", "0.8.5")):
            require(tools[name]["available"] is True and tools[name]["version"] == version, f"Host did not expose native {name}")
        checked("authenticated_model_free_native_capabilities")
        model = json_request(first.base, "/api/engineering/examples/frame", cookie=cookie)["model"]
        require(request(first.base, "/api/engineering/frame", {"model": model}, cookie=cookie,
                        origin="https://untrusted.invalid")[0] == 403, "Cross-site native calculation was accepted")
        checked("same_origin_required")
        beam = json_request(first.base, "/api/engineering/frame", {"model": model}, cookie=cookie)
        expected = beam_reaction(model)
        observed = sum(node["reaction_N"][1] for node in beam["result"]["combinations"][0]["nodes"])
        require(math.isclose(observed, expected, rel_tol=1e-9, abs_tol=1e-7), "Native beam does not balance its stated load")
        report["beam"] = {"expected_reaction_N": expected, "actual_reaction_N": observed}
        checked("actual_frame_matches_analytic_reaction")

        from scripts.test_cad_api import fixture, config
        dxf = fixture()
        body, content_type = multipart_dxf(dxf)
        status, _, data = request(first.base, "/api/cad/import", body, cookie=cookie, content_type=content_type)
        require(status == 200, f"Native CAD upload returned {status}: {data[:300]!r}")
        imported = json.loads(data)
        cfg = config("section")
        cfg["parameters"]["section"]["height_m"] = None
        section = json_request(first.base, "/api/engineering/section",
                               {"document_id": imported["document_id"], "config": cfg}, cookie=cookie)
        # Analytic area of the explicitly synthetic fixture's 2000x1000 mm
        # rectangle with a 1000x500 mm hole. No real drawing dimensions guessed.
        require(math.isclose(section["result"]["total_area_mm2"], 2000 * 1000 - 1000 * 500, rel_tol=1e-9), "Section hole/area changed")
        require(section["result"]["source_sha256"] == sha256(dxf).hexdigest(), "Section source hash does not match uploaded bytes")
        checked("actual_hollow_section_and_source_hash")

        ifc_sources = {}
        for name in ("old.ifc", "new.ifc", "requirements.ids"):
            status, _, raw = request(first.base, "/api/engineering/examples/ifc/" + name, cookie=cookie)
            require(status == 200, "Actual IFC fixture unavailable")
            ifc_sources[name] = {"name": name, "data_b64": base64.b64encode(raw).decode("ascii")}
        ifc_check = json_request(first.base, "/api/engineering/ifc/check",
                                 {"ifc": ifc_sources["old.ifc"], "ids": ifc_sources["requirements.ids"]}, cookie=cookie)
        ifc_diff = json_request(first.base, "/api/engineering/ifc/diff",
                                {"old": ifc_sources["old.ifc"], "new": ifc_sources["new.ifc"]}, cookie=cookie)
        require(ifc_check["result"]["report"]["total_checks_fail"] == 1, "Synthetic missing Name was not identified")
        require(len(ifc_diff["result"]["added"]) == 1 and len(ifc_diff["result"]["changed"]) == 1, "Synthetic IFC versions incorrectly compared")
        for source, receipt in (("old.ifc", ifc_check["result"]["source"]),
                                ("requirements.ids", ifc_check["result"]["rules_source"]),
                                ("old.ifc", ifc_diff["result"]["old_source"]),
                                ("new.ifc", ifc_diff["result"]["new_source"])):
            require(receipt["sha256"] == sha256(base64.b64decode(ifc_sources[source]["data_b64"])).hexdigest(), "IFC source bytes changed")
        checked("actual_ifc_ids_and_diff_with_source_hashes")

        runs = {"frame": beam, "section": section, "ifc_check": ifc_check, "ifc_diff": ifc_diff}
        for item in runs.values():
            for rejected in ("", " 我明白，将由持证人员签认"):
                json_request(first.base, "/api/engineering/export", {"run_id": item["run_id"], "confirmation": rejected}, cookie=cookie, expected=403)
            exported = json_request(first.base, "/api/engineering/export",
                                    {"run_id": item["run_id"], "confirmation": "我明白，将由持证人员签认"}, cookie=cookie)
            require(exported["result"] == item["result"], "Export replaced native results")
        checked("exact_export_acknowledgement")
        saved = {kind: json_request(first.base, "/api/engineering/projects",
                                   {"run_id": item["run_id"], "name": "Synthetic native " + kind}, cookie=cookie)
                 for kind, item in runs.items()}
        baseline = json_request(first.base, "/api/engineering/projects/" + saved["frame"]["project"]["id"], cookie=cookie)
        json_request(first.base, "/api/engineering/frame", {"model": {"code": "print(1)"}}, cookie=cookie, expected=422)
        json_request(first.base, "/api/engineering/projects", {"run_id": "f" * 32, "name": "Synthetic unknown run"}, cookie=cookie, expected=410)
        json_request(first.base, "/api/engineering/projects", {"run_id": beam["run_id"], "name": "Forged", "snapshot": {}}, cookie=cookie, expected=422)
        require(json_request(first.base, "/api/engineering/projects/" + saved["frame"]["project"]["id"], cookie=cookie)["snapshot"] == baseline["snapshot"], "Rejected input changed saved result")
        checked("successful_runs_only_and_bad_inputs_preserve_records")

        revised_model = deepcopy(model)
        for load in revised_model["member_loads"]:
            load["w1_N_m"] *= 2
            load["w2_N_m"] *= 2
        revised = json_request(first.base, "/api/engineering/frame", {"model": revised_model}, cookie=cookie)
        reaction = sum(n["reaction_N"][1] for n in revised["result"]["combinations"][0]["nodes"])
        require(math.isclose(reaction, beam_reaction(revised_model), rel_tol=1e-9), "Native revised beam not recomputed")
        revision = {"run_id": revised["run_id"], "name": "Synthetic native frame revised",
                    "id": saved["frame"]["project"]["id"], "expected_revision": 1}
        saved["frame"] = json_request(first.base, "/api/engineering/projects", revision, cookie=cookie)
        json_request(first.base, "/api/engineering/projects", revision, cookie=cookie, expected=409)
        historical = json_request(first.base, "/api/engineering/projects/" + revision["id"] + "?version=1", cookie=cookie)
        require(historical["snapshot"] == baseline["snapshot"] and historical["version"] == 1, "Historical beam was overwritten")
        checked("revision_conflict_and_history")

        second.start()
        second_cookie = second.login()
        require(request(second.base, "/api/engineering/capabilities", cookie=cookie)[0] == 401, "Another account accepted Alice's cookie")
        require(json_request(second.base, "/api/engineering/projects", cookie=second_cookie)["projects"] == [], "Native project listing crossed accounts")
        for item in saved.values():
            json_request(second.base, "/api/engineering/projects/" + item["project"]["id"], cookie=second_cookie, expected=404)
        json_request(second.base, "/api/engineering/export", {"run_id": beam["run_id"], "confirmation": "我明白，将由持证人员签认"}, cookie=second_cookie, expected=410)
        checked("cross_account_projects_and_run_cache_isolated")
        second.stop()

        first.stop()
        first.start()
        require(request(first.base, "/api/engineering/projects", cookie=cookie)[0] == 401, "Old cookie survived a real restart")
        cookie = first.login()
        checked("real_restart_and_cookie_revocation")
        restored = {}
        for kind, item in saved.items():
            restored[kind] = json_request(first.base, "/api/engineering/projects/" + item["project"]["id"], cookie=cookie)
            require(restored[kind]["snapshot"] == item["snapshot"], f"Restart lost native {kind} inputs/results")
            require(restored[kind]["project"]["revision"] == item["project"]["revision"], "Restart changed native revision")
        json_request(first.base, "/api/engineering/export", {"run_id": beam["run_id"], "confirmation": "我明白，将由持证人员签认"}, cookie=cookie, expected=410)
        checked("saved_native_results_and_sources_restored")
        json_request(first.base, "/api/engineering/section", {"document_id": imported["document_id"], "config": cfg}, cookie=cookie, expected=410)
        recalculated = json_request(first.base, "/api/engineering/section",
                                    {"document_id": restored["section"]["document_id"], "config": cfg}, cookie=cookie)
        require(recalculated["result"] == section["result"], "Restored section did not reproduce actual result")
        checked("section_recomputed_after_cache_reset")
        report["saved_projects"] = {kind: {"id": item["project"]["id"], "revision": item["project"]["revision"]} for kind, item in saved.items()}
        report["results"] = {kind: item["snapshot"] for kind, item in restored.items()}
        first.stop()
        checked("both_process_families_stopped")
        require(sources == source_snapshot(), "Acceptance sources changed during runtime")
        checked("tested_sources_unchanged")
        report["passed"] = set(report["checks"]) == set(CHECKS) and all(report["checks"].values())
    except Exception as exc:
        report["error"] = {"type": type(exc).__name__, "message": str(exc)}
    finally:
        for instance in reversed(instances):
            try:
                instance.stop()
            except Exception as exc:
                report["passed"] = False
                report.setdefault("cleanup_errors", []).append(str(exc))
        report["elapsed_seconds"] = round(time.monotonic() - started, 3)
        (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, allow_nan=False, indent=2) + "\n", encoding="utf-8")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--output", type=Path, help="New output directory; never overwrite previous evidence")
    args = parser.parse_args()
    binary = args.binary.resolve()
    if not binary.is_file():
        parser.error("--binary must identify an existing compiled executable")
    output = (args.output or ROOT / "output" / ("native-runtime-" + time.strftime("%Y%m%d-%H%M%S") + "-" + uuid4().hex[:6])).resolve()
    report = run(binary, output)
    print(json.dumps({"passed": report["passed"], "checks": len(report["checks"]),
                      "report": str(output / "report.json"), "error": report.get("error")}, ensure_ascii=False))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    environment = gate.offline_environment()
    os.environ.clear()
    os.environ.update(environment)
    raise SystemExit(main())
