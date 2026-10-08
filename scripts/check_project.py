#!/usr/bin/env python3
"""Civil Buddy's offline quality gate, shared by Python, npm and CI."""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class Check:
    name: str
    args: tuple[str, ...]
    runtime: str = "python"
    timeout: int = 180


CHECKS = (
    Check("runner", ("scripts/test_check_project.py",)),
    Check("secrets", ("scripts/scan_tracked_secrets.py",)),
    Check("offline-assets", ("scripts/test_no_external_urls.py",)),
    Check("js-syntax", ("scripts/test_js_syntax.py",)),
    Check("vue-bindings", ("scripts/test_vue_bindings.py",)),
    Check("chat-stream", ("scripts/test_chat_stream.cjs",), "node"),
    Check("cad-geometry", ("scripts/test_cad_geometry.py",)),
    Check("cad-selection", ("scripts/test_cad_selection.py",)),
    Check("cad-imports", ("scripts/test_cad_imports.py",)),
    Check("cad-dimensions", ("scripts/test_cad_dimensions.py",)),
    Check("cad-workflow", ("scripts/test_cad_workflow.py",)),
    Check("cad-step", ("scripts/test_cad_step.py",)),
    Check("cad-commands", ("scripts/test_cad_commands.py",)),
    Check("cad-api", ("scripts/test_cad_api.py",)),
    Check("cad-projects", ("scripts/test_cad_projects.py",)),
    Check("cad-agent", ("scripts/test_cad_agent.py",), timeout=300),
    Check("cad-ui", ("scripts/test_cad_ui.cjs",), "node"),
    Check("engineering-frame", ("scripts/test_frame_analysis.py",)),
    Check("engineering-section", ("scripts/test_engineering_section.py",)),
    Check("engineering-ifc", ("scripts/test_engineering_ifc.py",)),
    Check("engineering-records", ("scripts/test_engineering_records.py",)),
    Check("engineering-schedule", ("scripts/test_engineering_schedule.py",)),
    Check("engineering-schedule-ui", ("scripts/test_engineering_schedule_ui.cjs",), "node"),
    Check("engineering-ui", ("scripts/test_engineering_ui.cjs",), "node"),
    Check("engineering-agent", ("scripts/test_engineering_agent.py",)),
    Check("engineering-api", ("scripts/test_engineering_api.py",), timeout=300),
    Check("ui-modules", ("scripts/test_modules.cjs",), "node"),  # demo/static/modules/*.js on their own
    Check("agent-ui", ("scripts/test_agent_ui.cjs",), "node"),
    Check("project-control-ui", ("scripts/test_project_control_ui.cjs",), "node"),
    Check("product-i18n", ("scripts/test_i18n.cjs",), "node"),
    Check("product-language", ("scripts/test_product_language.py",)),
    Check("engineering-i18n", ("scripts/test_engineering_i18n.cjs",), "node"),
    Check("logistics-language", ("scripts/test_logistics_language.py",)),
    Check("logistics-i18n", ("scripts/test_logistics_language.cjs",), "node"),
    Check("document-worker", ("scripts/test_document_worker.py",)),
    Check("document-readiness", ("scripts/test_document_readiness.py",)),
    Check("source-retrieval", ("scripts/test_source_retrieval.py",)),
    Check("host-worker", ("scripts/test_host_worker.py",)),
    Check("packing-replan-worker", ("scripts/test_packing_replan_worker.py",), timeout=300),
    Check("unified-acceptance-oracle", ("scripts/test_unified_acceptance.py",)),
    Check("unified-launcher", ("scripts/test_unified_launcher.py",)),
    Check("workbench-preflight", ("scripts/test_workbench_preflight.py",)),
    Check("domain-service", ("scripts/test_domain_service.py",), timeout=300),
    Check("unified-packing", ("scripts/test_unified_packing.py",)),
    # Windows temporary Git fixtures and extracted-package reads exceeded 180s under load.
    Check("unified-release", ("scripts/test_unified_release.py",), timeout=600),
    Check("ui-dom", ("scripts/e2e/ui_dom.cjs",), "node"),  # real page + real backend in jsdom
    Check("engineering-planning", ("scripts/test_engineering_planning.py",)),
    Check("planning-exchange", ("scripts/test_planning_exchange.py",)),
    Check("planning-workbench", ("scripts/test_planning_workbench.py",), timeout=180),
    Check("planning-agent", ("scripts/test_planning_agent.py",), timeout=180),
    Check("planning-chat-api", ("scripts/test_planning_chat_api.py",)),
    Check("planning-chat-host", ("scripts/test_planning_chat_host.py",)),
    Check("planning-chat-ui", ("scripts/test_planning_chat_ui.cjs",), "node"),
    Check("planning-bundle", ("scripts/test_planning_bundle.py",), timeout=180),
    Check("logistics-intake", ("scripts/test_logistics_intake.py",)),
    Check("logistics-groups", ("scripts/test_logistics_groups.py",)),
    Check("logistics-workbench", ("scripts/test_logistics_workbench.py",)),
    Check("transport-constraints", ("scripts/test_transport_constraints.py",)),
    Check("logistics-chat", ("scripts/test_logistics_chat.py",)),
    Check("logistics-ui", ("scripts/test_logistics_ui.cjs",), "node"),
    Check("engineering-routing", ("scripts/test_engineering_routing.py",)),
    Check("planning-ui", ("scripts/test_engineering_planning_ui.cjs",), "node"),
    Check("planning-optimizer", ("scripts/test_planning_optimizer.py",)),
    Check("stack-parity", ("scripts/test_stack_parity.py",)),
    Check("expert-capabilities", ("scripts/test_expert_capabilities.py",)),
    Check("tool-contracts", ("scripts/test_tool_contracts.py",)),
    Check("readonly-sources", ("scripts/test_readonly_sources.py",)),
    Check("task-routing", ("scripts/test_task_router.py",)),
    Check("readonly-routing", ("scripts/test_readonly_routing.py",)),
    Check("tender-workflow", ("scripts/test_tender_workflow.py",)),
    Check("tender-response-match", ("scripts/test_tender_response_match.py",)),
    Check("tender-response-bench", ("scripts/eval_tender_response_match.py", "--check")),
    Check("tender-facts", ("scripts/test_tender_facts.py",)),
    Check("bid-posts", ("scripts/test_bid_posts.py",)),
    Check("bid-files", ("scripts/test_bid_files.py",)),
    Check("tender-document", ("scripts/test_tender_document.py",)),
    Check("pdf-grid", ("scripts/test_pdf_grid.py",)),
    Check("lots-addenda", ("scripts/test_lots_addenda.py",), timeout=600),
    Check("english-itt", ("scripts/test_english_itt.py",), timeout=600),
    Check("facade-tender", ("scripts/test_facade_tender.py",)),
    Check("facade-demo", ("scripts/test_facade_demo.py",)),
    # the partner's problem: tender and packing as one run that stays linked (tender_packing_link.py)
    Check("tender-packing-link", ("scripts/test_tender_packing_link.py",), timeout=600),
    # how the link reads clauses: nothing silently dropped, per-package limits apart, cites as written, the DEV set floors
    Check("tender-link-clauses", ("scripts/test_tender_link_clauses.py",), timeout=600),
    # the same reader on the sealed held-out set written blind on 2026-09-26: floors = its first scored run (README there)
    Check("tender-link-sealed", ("test/benchmarks/tender_link_sealed/score_sealed.py", "--check"), timeout=600),
    # the reader is bounded: a sentence too dense to read figure by figure goes to a person, runs stop at checkpoints
    Check("tender-link-bounded", ("scripts/test_tender_link_bounded.py",), timeout=600),
    # planted text in SYNTHETIC tender / panel-list files does not change statuses, approve anything or become a
    # statement (steps mode, gateway, and a scripted fake model that obeys the plant); a live model was not tested
    Check("injection-plants", ("scripts/test_injection_plants.py",), timeout=600),
    # the sealed held-out English verdict set and the planted-instruction set, written blind on 2026-09-26: floors =
    # PR #72's first scored run (README there); the obeying-fake-model row is printed, not pinned
    Check("safety-sealed", ("test/benchmarks/safety_sealed/score_sealed.py", "--check"), timeout=600),
    Check("real-tender", ("scripts/test_real_tender.py",), timeout=1200),
    # Offline, model-free, a second or two each - and until 2026-09-20 run by nothing: not by ci.yml,
    # not by this registry, not by the acceptance glob. They pin the parser the three bid posts stand on.
    Check("tender-parse", ("scripts/test_tender_parse.py",)),
    Check("tender-parse-engine", ("scripts/test_tender_parse_engine.py",)),
    Check("tender-originals", ("scripts/test_tender_originals.py",)),
    Check("tender-handoff", ("scripts/test_tender_handoff.py",)),
    Check("tender-review", ("scripts/test_tender_review.py",)),
    Check("tender-ingest", ("scripts/test_tender_ingest.py",)),
    Check("tender-delivery-api", ("scripts/test_tender_delivery_api.py",)),
    Check("demo-bid-handoff", ("scripts/test_demo_bid_handoff.py",)),
    Check("acceptance-cases", ("scripts/test_acceptance_cases.py",), timeout=600),
    Check("post-facts", ("scripts/test_post_facts.py",)),
    Check("post-content", ("scripts/test_post_content.py",), timeout=1800),
    Check("post-robustness", ("scripts/test_post_robustness.py",), timeout=900),
    Check("workbench-collaboration", ("scripts/test_workbench_collaboration.py",)),
    Check("middleware", ("scripts/test_agent_middleware.py",)),
    Check("deadlock", ("scripts/test_deadlock.py",)),
    Check("runtime-cancel-isolation", ("scripts/test_runtime_cancel_isolation.py",)),
    # the shared ToolEngine's fault circuit: open after 3 faults, half-open trial after the cool-down
    Check("tool-circuit", ("scripts/test_tool_circuit.py",)),
    Check("lg-checkpoint-errors", ("scripts/test_lg_checkpoint_errors.py",)),
    Check("sandbox", ("scripts/test_sandbox.py",)),
    Check("pack-ship-read-sandbox", ("scripts/test_pack_ship_read_sandbox.py",), timeout=300),
    Check("civil-cli", ("scripts/test_civil_codex.py",)),
    Check("civil-config", ("scripts/test_civil_config.py",)),
    Check("civil-workspace", ("scripts/test_civil_workspace.py",)),
    Check("model-loop", ("scripts/test_model_loop.py",)),
    Check("model-refusal", ("scripts/test_model_refusal.py",)),
    # bounded retry of one model request (429/5xx/timeouts/resets) against a fake endpoint on 127.0.0.1
    Check("model-retry", ("scripts/test_model_retry.py",), timeout=300),
    # model mode on the link: deterministic first, the model only explains; its claims checked against the record
    Check("model-mode-link", ("scripts/test_model_mode_link.py",), timeout=300),
    # 12 frozen requests against a scripted OpenAI-compatible server on 127.0.0.1 (no network, no key)
    Check("model-mode-eval", ("scripts/eval_model_mode.py", "--check"), timeout=300),
    # model mode on the sealed held-out link requests and injections (written blind 2026-09-26); floors = the first
    # scored run after merging #67 (README there)
    Check("model-mode-sealed", ("test/benchmarks/model_mode_sealed/score_sealed.py", "--check"), timeout=600),
    Check("post-scorecard", ("scripts/eval_post_scorecard.py", "--all-pilots"), timeout=300),
    Check("workbench-model-turn", ("scripts/test_workbench_model_turn.py",), timeout=300),
    Check("steps-job-files", ("scripts/test_steps_job_files.py",)),
    Check("civil-review", ("scripts/test_civil_review.py",)),
    Check("os-sandbox", ("scripts/test_os_sandbox.py",), timeout=300),
    Check("plugins", ("scripts/test_plugins.py",)),
    Check("desktop-app", ("scripts/test_desktop.py",), timeout=300),
    Check("example-plugin", ("-m", "packing_assistant.civil", "plugin", "validate", "examples/plugins/site-forms")),
    Check("task-intent-bench", ("scripts/eval_task_intent.py", "--check")),
    Check("english-intents", ("scripts/test_english_intents.py",)),
    Check("link-routing-bench", ("scripts/eval_link_routing.py", "--check")),
    Check("english-requests-heldout", ("test/benchmarks/english_requests/score.py", "--check")),
    Check("link-confirmation", ("scripts/test_link_confirmation_regressions.py",), timeout=300),
    Check("verdict-bench", ("scripts/eval_verdicts.py", "--check")),
    # round-3 guard fixes on their DEV set: verdict bypasses, record-guard negations, whole-sentence claim corrections
    Check("guards-round3", ("scripts/test_guards_round3.py",)),
    Check("number-provenance-bench", ("scripts/eval_number_provenance.py", "--check")),
    Check("runtime-threads", ("scripts/test_runtime_threads.py",)),
    Check("worktree-bg", ("scripts/test_worktree_bg.py",)),
    Check("app-launcher", ("scripts/test_app_launcher.py",)),
    Check("workbench-settings", ("scripts/test_workbench_settings.py",)),
    Check("access-guard", ("scripts/test_access_guard.py",)),
    # the link from a browser (upload + /demo, token-gated) and the page a visitor without a token lands on
    Check("web-tender-link", ("scripts/test_web_link.py",), timeout=600),
    # docker-compose, the Lightsail override, Caddyfile, launch script and guides say what they promise
    Check("deploy-config", ("scripts/test_deploy_config.py",)),
    Check("workbench-uploads", ("scripts/test_workbench_uploads.py",)),
    Check("document-text", ("scripts/test_document_text.py",)),
    Check("workbench-flow", ("scripts/test_workbench_flow.py",)),
    Check("context-budget", ("scripts/test_context_budget.py",)),
    Check("task-memory", ("scripts/test_task_memory.py",)),
    Check("local-retrieval", ("scripts/test_local_retrieval.py",)),
    Check("context-flow", ("scripts/test_context_flow.py",)),
    Check("draft-material-budget", ("scripts/test_draft_material_budget.py",)),
    Check("context-rebuild", ("scripts/test_context_rebuild.py",)),
    Check("semantic-memory", ("scripts/test_semantic_memory.py",)),
    Check("semantic-integration", ("scripts/test_semantic_integration.py",)),
    Check("semantic-http", ("scripts/test_semantic_http.py",)),
    Check("draft-context", ("scripts/test_draft_context_integrity.py",)),
    Check("project-storage", ("scripts/test_project_storage.py",)),
    Check("workbench-cancel", ("scripts/test_workbench_cancel.py",)),
    Check("cancel-inside-agent", ("scripts/test_cancel_inside_agent.py",)),
    Check("session-backup", ("scripts/test_session_bundle.py",)),
    Check("word-export", ("scripts/test_word_export.py",)),
    Check("runtime-office", ("scripts/test_runtime_office_exports.py",)),
    Check("http-confirmation", ("scripts/test_http_confirmation.py",)),
    Check("human-approval", ("scripts/test_human_approval.py",)),
    Check("daily-report", ("scripts/test_daily_report_content.py",)),
    Check("finance-tax", ("scripts/test_finance_tax_drafts.py",)),
    Check("post-dispatch", ("scripts/test_post_dispatch.py",)),
    Check("hr-drafts", ("scripts/test_hr_drafts.py",)),
    Check("admin-drafts", ("scripts/test_admin_drafts.py",)),
    Check("it-drafts", ("scripts/test_it_drafts.py",)),
    Check("bim-drafts", ("scripts/test_bim_drafts.py",)),
    Check("design-basic", ("scripts/test_design_basic_drafts.py",)),
    Check("design-services", ("scripts/test_design_services_drafts.py",)),
    Check("design-specialties", ("scripts/test_design_specialties_drafts.py",)),
    Check("design-infrastructure", ("scripts/test_design_infrastructure_drafts.py",)),
    Check("expert-drafts", ("scripts/test_expert_turn.py",)),
    Check("release-package", ("scripts/test_trial_pack.py",)),
    Check("business-files", ("scripts/test_business_reliability.py",)),
    Check("trace-artifacts", ("scripts/test_trace_artifacts.py",)),
    Check("hitl-disk-resume", ("scripts/test_hitl_resume_competition.py",), timeout=300),
    Check("pack-ship-weight-validation", ("scripts/test_pack_ship_weight_validation.py",)),
    Check("pack-ship-conservation", ("scripts/test_pack_ship_conservation.py",), timeout=300),
    Check("pack-ship-crates-structure", ("scripts/test_pack_ship_crates_structure.py",)),
    Check("table-quantity-cells", ("scripts/test_table_quantity_cells.py",)),
    Check("panel-list-reading", ("scripts/test_panel_list_reading.py",), timeout=300),
    # the blind panel lists (SYNTHETIC, written 2026-09-26 before PR #69): first-run floors, see its README
    Check("panel-lists-sealed", ("test/benchmarks/panel_lists_sealed/score_sealed.py", "--check"), timeout=300),
    Check("workbench-needs-human", ("scripts/test_workbench_needs_human.py",)),
    Check("storage-parent", ("scripts/test_storage_ensure_run.py",)),
    # confirm runs Team B once (replay / 409), exports never collide, busy 429s carry Retry-After, restarts mark
    # running sessions interrupted, a pipeline concurrency cap
    Check("gateway-state-safety", ("scripts/test_gateway_state_safety.py",), timeout=300),
    Check("python-idempotency", ("scripts/test_python_idempotency.py",), timeout=300),
    Check("offline-eval", ("-c", "from packing_assistant.runtime.eval_live import live_eval; "
          "v=live_eval(); assert v.get('verdict')=='offline_gate_pass', v; print(v['verdict'])")),
    Check("industry-eval", ("scripts/test_industry_agent_eval.py",)),
    Check("product-smoke", ("scripts/smoke_agent_product.py",), timeout=300),
)
FULL_CHECKS = (
    Check("http-demo", ("-m", "pytest", "demo/tests", "-q", "--basetemp=output/check-project-pytest")),
    Check("runtime-api", ("scripts/test_runtime_p0.py",)),
    Check("office-job", ("scripts/test_office_job.py",)),
    Check("pipeline", ("scripts/test_p0_p1_p2_full.py",), timeout=600),
    Check("shadow-eval", ("scripts/eval_workteams_cli.py", "--tiny-only"), timeout=600),
    # Scripted-provider fixtures change process-wide configuration; keep them sequential.
    Check("rust", ("test", "--locked", "--offline", "--manifest-path", "workbench/Cargo.toml", "--", "--test-threads=1"), "cargo", 1200),
    Check("rust-build", ("build", "--locked", "--offline", "--manifest-path", "workbench/Cargo.toml", "--bin", "civil-workbench"), "cargo", 900),
    Check("unified-runtime", ("scripts/test_unified_runtime_http.py", "--binary",
          "workbench/target/debug/civil-workbench" + (".exe" if os.name == "nt" else "")), timeout=900),
)


def check_environment() -> dict[str, str]:
    """Do not load personal model credentials or use paid model calls in checks."""
    env = dict(os.environ)
    for key in ("CIVIL_API_KEY", "OPENAI_API_KEY", "LLM_API_KEY", "DEEPSEEK_API_KEY", "ZAI_API_KEY", "JEV_API_KEY", "TYPESAFE_API_KEY"):
        env.pop(key, None)
    env.update(PYTHONUTF8="1", PYTHONIOENCODING="utf-8", PYTHON_DOTENV_DISABLED="1", PYTHONOPTIMIZE="0",
               CIVIL_JOB_ROOT=str(ROOT / "output" / "check-project" / "jobs"))
    return env


def run_check(check: Check, env: dict[str, str]) -> tuple[bool, str]:
    executable = sys.executable if check.runtime == "python" else shutil.which(check.runtime)
    if not executable:
        return False, f"missing required runtime: {check.runtime}"
    started = time.monotonic()
    print(f"\n[RUN] {check.name}", flush=True)
    try:
        result = subprocess.run([executable, *check.args], cwd=ROOT, env=env, timeout=check.timeout)
    except subprocess.TimeoutExpired:
        return False, f"timeout after {check.timeout}s"
    except OSError as exc:
        return False, f"could not start: {exc}"
    return result.returncode == 0, f"exit {result.returncode}, {time.monotonic() - started:.1f}s"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--full", action="store_true", help="also check API, packing pipeline and cached Rust build")
    parser.add_argument("--only", help="comma-separated check names (see --list)")
    parser.add_argument("--list", action="store_true", help="list checks without running them")
    args = parser.parse_args(argv)
    available = CHECKS + FULL_CHECKS
    selected = CHECKS + (FULL_CHECKS if args.full else ())
    if args.only is not None:
        names = {name.strip() for name in args.only.split(",") if name.strip()}
        unknown = names - {check.name for check in available}
        if not names or unknown:
            parser.error("--only requires known check names; unknown: " + ", ".join(sorted(unknown)))
        selected = tuple(check for check in available if check.name in names)
    if args.list:
        for check in available:
            print(f"{check.name:18} {check.runtime:6} {' '.join(check.args)}")
        return 0
    env = check_environment()
    results = []
    print(f"Civil Buddy checks · Python: {sys.executable}", flush=True)
    for check in selected:
        ok, detail = run_check(check, env)
        results.append((check.name, ok, detail))
        print(f"[{'PASS' if ok else 'FAIL'}] {check.name}: {detail}", flush=True)
    print(f"\n{sum(ok for _, ok, _ in results)}/{len(results)} checks passed", flush=True)
    for name, ok, detail in results:
        if not ok:
            print(f"  FAIL {name}: {detail}", flush=True)
    return 0 if all(ok for _, ok, _ in results) else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\nChecks interrupted.", file=sys.stderr)
        raise SystemExit(130)
