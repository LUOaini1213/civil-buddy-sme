#!/usr/bin/env python3
"""Synthetic linked tender/packing acceptance through an actual compiled local host.

python scripts/test_linked_runtime_http.py --binary workbench/target/debug/civil-workbench.exe

Uploads original bytes through authenticated HTTP, runs deterministic business tools,
checks downloadable copies and source receipts, restarts, then selects revised inputs.
Never connects to production or a real provider. This is not real-project or cross-PC acceptance.
"""
from __future__ import annotations

import argparse
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
import sys
import threading
import time
import urllib.parse
from uuid import uuid4
from xml.etree import ElementTree as ET
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "scripts")]
from scripts import unified_acceptance
from scripts.test_unified_runtime_http import Instance, json_request, legacy_chat, request, require

EXAMPLES = ROOT / "examples" / "facade-demo"
TENDER = "north_itt.md"
PANELS = "glazing_panels.xlsx"


def upload(instance, cookie, session, filename, data):
    boundary = "civil-linked-" + uuid4().hex
    raw = (f'--{boundary}\r\nContent-Disposition: form-data; name="session_id"\r\n\r\n{session}\r\n'
           f'--{boundary}\r\nContent-Disposition: form-data; name="files"; filename="{filename}"\r\n'
           'Content-Type: application/octet-stream\r\n\r\n').encode() + data + f"\r\n--{boundary}--\r\n".encode()
    status, _, response = request(instance.base, "/api/upload", raw, cookie=cookie,
                                  content_type="multipart/form-data; boundary=" + boundary)
    require(status == 200, f"Upload failed: HTTP {status}: {response[:300]!r}")
    files = json.loads(response)["files"]
    require(len(files) == 1 and files[0]["name"] == filename, "Upload changed source identity")
    return files[0]


def original_bytes(instance, cookie, session, identifier):
    query = urllib.parse.urlencode({"session": session, "upload": identifier})
    status, _, raw = request(instance.base, "/api/file?" + query, cookie=cookie)
    require(status == 200, f"Cannot download original: HTTP {status}")
    return raw


def artifact_bytes(instance, cookie, item):
    query = urllib.parse.urlencode({"path": item["path"], "name": item["name"]})
    status, _, raw = request(instance.base, "/api/file?" + query, cookie=cookie)
    require(status == 200 and raw, f"Cannot download {item['name']}: HTTP {status}")
    return raw


def successful_link(instance, cookie, session, selected, names, expected_containers, output, *, explicit_post=True):
    done = legacy_chat(instance, cookie, {"session_id": session, "locale": "en", "expert_ids": ["bid-parse"] if explicit_post else [],
        "message": f"Link the tender {names[0]} to the packing list {names[1]} and write the logistics response.",
        "attachments": [item["id"] for item in selected], "confirm_text": "我明白，将由持证人员签认"}, output)
    require(done.get("ok") is True and done.get("wrote") is True, f"Linked task failed: {done}")
    require(done.get("submit_blocked") is True, "Draft was marked releasable")
    artifacts = {item["name"]: item for item in done["deliverables"]}
    required = {"tender-packing-link.json", "tender-packing-link.md", "bidbook.en.md", "pack-plan.json", "tender.handoff.json"}
    require(required <= artifacts.keys(), f"Missing linked artifacts: {required - artifacts.keys()}")
    blobs = {name: artifact_bytes(instance, cookie, item) for name, item in artifacts.items()}
    record = json.loads(blobs["tender-packing-link.json"])
    plan = json.loads(blobs["pack-plan.json"])
    require(record["plan"]["can_fit"] is True and plan["can_fit"] is True, "Failed physical plan was treated as fitting")
    require(record["plan"]["containers_used"] == expected_containers and plan["containers_used"] == expected_containers,
            "Container count differs from the synthetic input fixture")
    require(record["container"]["type"] == plan["container_type"] == "40HQ" and record["container"]["clause"] == "4.8"
            and record["container"]["source"] == "tender_clause", "Plan is disconnected from the container clause")
    require(record["submit_blocked"] is True and record["confirmed_by_person"] is False, "Tool claimed professional sign-off")
    for role, item in zip(("tender", "panel_list"), selected):
        content = original_bytes(instance, cookie, session, item["id"])
        require(record["inputs"][role]["sha256"] == sha256(content).hexdigest(), "Link points at a different source version")
    clauses = {row["clause"]: row for row in record["clauses"]}
    for statement in record["statements"]:
        require(statement["clause"] in clauses and statement["clause_sha256"] == clauses[statement["clause"]]["sha256"],
                "Statement clause receipt is unbound")
    human_rows = [row for row in record["statements"] if row["clause"] in {"4.7", "4.10", "4.11"} or row["kind"] == "crate_structure"]
    require(human_rows and all(row["status"] != "covered" for row in human_rows),
            "Human packing/securing/design work was automatically approved")
    book = blobs["bidbook.en.md"].decode("utf-8")
    require(f"{expected_containers} x 40HQ" in book and "S$ [TO FILL]" in book and "[TO FILL]" in book,
            "Bid book lost computed logistics or human price/qualification fields")
    words = [name for name in artifacts if name.startswith("bidbook.en") and name.endswith(".docx")]
    require(words, "No actual downloadable Word bid copy was generated")
    for name in words:
        with ZipFile(BytesIO(blobs[name])) as archive:
            require(archive.testzip() is None, "Corrupt Word export")
            text = "".join(ET.fromstring(archive.read("word/document.xml")).itertext())
            require(f"{expected_containers} x 40HQ" in text and "[TO FILL]" in text, "Word copy differs from the linked draft")
    audit = json_request(instance.base, "/api/harness/audit/" + session, cookie=cookie)
    runs = [row for row in audit["runs"] if row["run_id"] in done["run_ids"]]
    require(len(runs) == 1, "Linked task does not have one current run receipt")
    run = runs[0]
    manifest = run["input_files"]
    require({row["id"] for row in manifest} == {item["id"] for item in selected}, "Unselected files entered the run")
    for row in manifest:
        content = original_bytes(instance, cookie, session, row["id"])
        require(row["sha256"] == sha256(content).hexdigest() and row["bytes"] == len(content), "Incorrect input-byte receipt")
    return {"done": done, "record": record, "artifacts": artifacts, "hashes": {name: sha256(blob).hexdigest() for name, blob in blobs.items()},
            "input_files": manifest, "word_copies": words}


def retained_artifacts(instance, cookie, previous):
    require(all(sha256(artifact_bytes(instance, cookie, item)).hexdigest() == previous["hashes"][name]
                for name, item in previous["artifacts"].items()), "An earlier downloadable copy was overwritten or lost")


def run(binary, output):
    output.mkdir(parents=True, exist_ok=False)
    report = {"passed": False, "synthetic": True, "real_provider_calls": False, "cross_computer": False,
              "scope": "authenticated_compiled_host_deterministic_linked_business_flow", "binary": str(binary),
              "binary_sha256": sha256(binary.read_bytes()).hexdigest(), "checks": {}}
    report["source_sha256"] = {name: sha256((ROOT / name).read_bytes()).hexdigest() for name in (
        "scripts/test_linked_runtime_http.py", "scripts/test_unified_runtime_http.py", "demo/chat_service.py",
        "demo/selected_inputs.py", "demo/domain_service.py", "packing_assistant/runtime/task_router.py",
        "packing_assistant/runtime/agent_loop.py", "packing_assistant/tender_packing_link.py",
        "scripts/start_unified_workbench.py", "packing_assistant/filesystem.py",
        "workbench/src/api.rs", "workbench/src/harness.rs",
        "packing_assistant/tools/tender_parse.py", "packing_assistant/tools/tender_tables.py",
        "packing_assistant/tools/pdf_grid.py")}
    provider = unified_acceptance.model_server(0)
    calls = []
    parent = provider.RequestHandlerClass
    class CountedProvider(parent):
        def do_POST(self):
            calls.append(self.path)
            return super().do_POST()
    provider.RequestHandlerClass = CountedProvider
    thread = threading.Thread(target=provider.serve_forever, daemon=True); thread.start()
    instance = Instance(binary, output / "instance", output / "state", output / "workspace", "linked_acceptance", provider.server_port)
    session = "link-http-" + uuid4().hex[:16]
    tender_raw = (EXAMPLES / "facade_itt_doc.md").read_bytes()
    panels_raw = (EXAMPLES / "facade_panels.xlsx").read_bytes()
    revision_raw = (EXAMPLES / "facade_panels_rev_b.xlsx").read_bytes()
    uploads = []
    try:
        instance.start(); cookie = instance.login()
        tender = upload(instance, cookie, session, TENDER, tender_raw)
        panels = upload(instance, cookie, session, PANELS, panels_raw)
        revision = upload(instance, cookie, session, "glazing_panels_rev_b.xlsx", revision_raw)
        uploads += [(tender, tender_raw), (panels, panels_raw), (revision, revision_raw)]
        first = successful_link(instance, cookie, session, [tender, panels], [TENDER, PANELS], 6, output / "original.sse")
        require(first["record"]["changes_since_previous"] is None, "Fresh session used another run's link record")
        report["checks"]["upload_clause_plan_statement_word_download"] = True
        report["checks"]["only_selected_original_bytes_and_human_gates"] = True
        detail = json_request(instance.base, "/api/sessions/" + session, cookie=cookie)
        instance.stop(); instance.start()
        require(request(instance.base, "/api/sessions/" + session, cookie=cookie)[0] == 401, "Old cookie survived restart")
        cookie = instance.login()
        restored = json_request(instance.base, "/api/sessions/" + session, cookie=cookie)
        require(detail["transcript"] == restored["transcript"] and detail["deliverables"] == restored["deliverables"], "Restart changed session data")
        retained_artifacts(instance, cookie, first)
        report["checks"]["restart_preserves_downloads_and_session"] = True
        repeated = successful_link(instance, cookie, session, [tender, panels], [TENDER, PANELS], 6, output / "unchanged-repeat.sse")
        same = repeated["record"]["changes_since_previous"]
        require(same and not same["inputs_changed"] and not same["changed"] and not same["needs_reconfirmation"],
                "New staging paths falsely invalidated an unchanged input/plan version")
        retained_artifacts(instance, cookie, first)
        report["checks"]["unchanged_rerun_does_not_create_false_stale_statements"] = True
        revised = successful_link(instance, cookie, session, [tender, revision], [TENDER, "glazing_panels_rev_b.xlsx"], 8, output / "panels-revised.sse")
        changes = revised["record"]["changes_since_previous"]
        require(changes and {row["input"] for row in changes["inputs_changed"]} == {"panel_list", "plan"}, "Panel revision did not compare with prior run")
        changed = {row["id"]: row for row in changes["changed"]}
        require(changed["S2"]["figures"]["containers_used"] == [6, 8] and "S2" in changes["needs_reconfirmation"], "Stale container-count statement was not flagged")
        require(any(name.endswith(".docx") for name in changes.get("stale_exports", [])), "Prior Word bid copy was not identified as stale")
        retained_artifacts(instance, cookie, first)
        report["checks"]["panel_revision_recomputes_and_names_stale_statements"] = True
        lines = tender_raw.decode("utf-8").splitlines(keepends=True)
        removed = [line for line in lines if line.startswith("4.11 ")]
        require(len(removed) == 1, "Synthetic tender fixture's delivery-sequence clause is missing")
        amended_raw = "".join(line for line in lines if not line.startswith("4.11 ")).encode("utf-8")
        amended = upload(instance, cookie, session, TENDER, amended_raw); uploads.append((amended, amended_raw))
        third = successful_link(instance, cookie, session, [amended, revision], [TENDER, "glazing_panels_rev_b.xlsx"], 8, output / "tender-revised.sse", explicit_post=False)
        withdrawn = third["record"]["changes_since_previous"]["withdrawn"]
        require(any(row["key"] == "delivery_sequence@4.11" for row in withdrawn), "Removed tender clause did not withdraw its old statement")
        retained_artifacts(instance, cookie, first); retained_artifacts(instance, cookie, revised)
        report["checks"]["tender_revision_withdraws_statement_without_overwriting_copies"] = True
        no_write = legacy_chat(instance, cookie, {"session_id": session, "locale": "en", "expert_ids": ["bid-parse"],
            "attachments": [amended["id"], revision["id"]], "confirm_text": "我明白，将由持证人员签认",
            "message": f"Link the tender {TENDER} to the packing list glazing_panels_rev_b.xlsx; do not write any files."}, output / "no-files.sse")
        require(no_write.get("wrote") is False and not no_write.get("deliverables"), "No-files task generated deliverables")
        retained_artifacts(instance, cookie, third)
        report["checks"]["no_files_preserves_existing_copies"] = True
        duplicate = upload(instance, cookie, session, PANELS, revision_raw); uploads.append((duplicate, revision_raw))
        refused = legacy_chat(instance, cookie, {"session_id": session, "locale": "en", "expert_ids": ["bid-parse"],
            "attachments": [amended["id"], panels["id"], duplicate["id"]],
            "message": f"Link the tender {TENDER} to the packing list {PANELS} and write the logistics response."}, output / "duplicate.sse")
        require(refused.get("ok") is False and refused.get("wrote") is False and not refused.get("deliverables"), "Ambiguous same-name versions reached calculation")
        require("duplicate filenames" in refused.get("text", "").lower(), "Duplicate-version refusal is not actionable")
        retained_artifacts(instance, cookie, first); retained_artifacts(instance, cookie, revised); retained_artifacts(instance, cookie, third)
        retained_artifacts(instance, cookie, repeated)
        report["checks"]["duplicate_versions_refused_without_losing_prior_copies"] = True
        for item, raw in uploads:
            require(original_bytes(instance, cookie, session, item["id"]) == raw, "Uploaded original was mutated")
        require(not calls, f"Deterministic linked workflow called a model: {calls}")
        report["checks"]["all_originals_immutable_and_zero_model_calls"] = True
        report["first"] = first; report["unchanged_repeat"] = repeated; report["panel_revision"] = revised; report["tender_revision"] = third
        report["model_calls"] = len(calls)
        instance.stop(); report["checks"]["host_and_sidecar_stopped"] = True; report["passed"] = True
    except Exception as exc:
        report["error"] = {"type": type(exc).__name__, "message": str(exc)}
    finally:
        try: instance.stop()
        except Exception as exc:
            report["passed"] = False; report["cleanup_error"] = str(exc)
        provider.shutdown(); provider.server_close(); thread.join(timeout=5)
        (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    binary = args.binary.resolve()
    if not binary.is_file(): parser.error("--binary must name an existing compiled executable")
    output = (args.output or ROOT / "output" / ("linked-business-http-" + time.strftime("%Y%m%d-%H%M%S") + "-" + uuid4().hex[:6])).resolve()
    report = run(binary, output)
    print(json.dumps({"passed": report["passed"], "report": str(output / "report.json"), "error": report.get("error")}, ensure_ascii=False))
    return 0 if report["passed"] else 1


if __name__ == "__main__": raise SystemExit(main())
