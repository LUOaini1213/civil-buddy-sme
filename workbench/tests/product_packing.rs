//! Packing authorization/protocol tests use a clearly scripted fixed worker.
//! The final test separately calls the actual Python deterministic solver.
use axum::{
    body::Body,
    http::{Request, StatusCode},
    routing::post,
    Json, Router,
};
use civil_workbench::{
    config::{LlmConfig, Paths},
    product::{
        api::{router, ProductState},
        packing::{bind_selections, selection_index, PackingSelection, PackingTurn},
        providers::{JevConfig, JevMode},
        tools::sha256,
        worker::WorkerHost,
    },
    runtime_core::{BudgetLimits, BudgetTree, CancellationToken, TaskId, WorkspaceContext},
};
use http_body_util::BodyExt;
use serde_json::{json, Value};
use std::{
    path::PathBuf,
    sync::{Arc, Mutex},
    time::Duration,
};
use tower::ServiceExt;

struct Fixture {
    root: PathBuf,
    ws: WorkspaceContext,
    worker: WorkerHost,
    selected: PackingSelection,
}
impl Fixture {
    fn new() -> Self {
        let root = std::env::temp_dir().join(format!("civil-packing-{}", uuid::Uuid::new_v4()));
        std::fs::create_dir_all(root.join("job")).unwrap();
        std::fs::create_dir_all(root.join("packing_assistant")).unwrap();
        std::fs::write(
            root.join("job/cargo.json"),
            "{\"scope\":\"scripted protocol fixture, not a solver input\"}",
        )
        .unwrap();
        std::fs::write(root.join("packing_assistant/host_worker.py"), r#"
import json, pathlib, sys
r=json.load(sys.stdin); root=pathlib.Path(__file__).resolve().parents[1]
if r['operation']=='packing_replan':
    counter=root/'calls.txt'; counter.write_text(str(int(counter.read_text())+1) if counter.exists() else '1')
    (root/'request.json').write_text(json.dumps(r),encoding='utf-8')
    result=json.loads((root/'result.json').read_text(encoding='utf-8'))
    result['source']={'path':r['payload']['source'],'sha256':r['payload']['expected_sha256']}
    if (root/'mutate').exists():
        (pathlib.Path(r['workspace'])/r['payload']['source']).write_text('changed during calculation')
elif r['operation']=='verdicts':
    result={'results':[{'text':t,'notice':'','found':[]} for t in r['texts']]}
else:
    raise ValueError('unexpected fixture operation')
print(json.dumps({'version':1,'ok':True,'call_id':r['call_id'],'result':result,
    'sandbox':{'enforces':{'write':True,'spawn':True}}}))
"#).unwrap();
        let ws = WorkspaceContext::new(root.join("job")).unwrap();
        let mut selected = vec![PackingSelection {
            source: "cargo.json".into(),
            source_sha256: None,
        }];
        bind_selections(&ws, &["cargo.json".into()], &mut selected).unwrap();
        let f = Self {
            worker: WorkerHost::detect(root.clone()),
            root,
            ws,
            selected: selected.remove(0),
        };
        f.set_result(valid_result());
        f
    }
    fn set_result(&self, value: Value) {
        std::fs::write(self.root.join("result.json"), value.to_string()).unwrap();
    }
    fn calls(&self) -> u32 {
        std::fs::read_to_string(self.root.join("calls.txt"))
            .unwrap_or_default()
            .parse()
            .unwrap_or(0)
    }
    fn app(&self) -> Router {
        let mut paths = Paths::from_demo(self.root.join("demo"));
        paths.data_dir = self.root.join("state");
        let mut state = ProductState::open(paths).unwrap();
        Arc::get_mut(&mut state).unwrap().worker = self.worker.clone();
        router(state)
    }
}
impl Drop for Fixture {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.root);
    }
}
fn summary(hash: &str) -> Value {
    json!({"can_fit":true,"layout_verified":true,"containers_used":2,"worst_mid50":0.2,
    "n_boxes":3,"engine":"scripted-protocol-only","plan_sha256":hash.repeat(64),"layout":[],"structure":{"status":"not_checked"}})
}
fn valid_result() -> Value {
    json!({"kind":"packing_replan","schema":"packing_replan.result.v1","status":"completed","ok":true,
    "source":{},"originals_unchanged":true,"baseline":summary("a"),"final":summary("a"),
    "rounds":[{"round":1,"candidate_id":"size_desc","parameters_sha256":"c".repeat(64),"summary":summary("b"),"accepted":false,"reason":"no_improvement"},
        {"round":2,"candidate_id":"weight_desc","parameters_sha256":"d".repeat(64),"summary":null,"accepted":false,"reason":"candidate_solver_failed"}],
    "outcome":"unchanged","hard_constraints":{"originals_immutable":true,"shipping_release":false,"professional_signoff":false,"max_rounds":2,"container_upgrades":false},
    "constraints_sha256":"e".repeat(64),"needs_human":[],"limitations":["Protocol fixture only"],"artifacts":[],"professional_signoff":false})
}
fn cfg(mode: JevMode) -> JevConfig {
    JevConfig {
        endpoint: "http://127.0.0.1:1".into(),
        api_key: String::new(),
        model: "offline-fixture".into(),
        mode,
    }
}
fn budget(max_calls: u32) -> (BudgetTree, TaskId) {
    let task = TaskId::new();
    let limits = BudgetLimits {
        max_model_calls: max_calls,
        ..Default::default()
    };
    (BudgetTree::new(task.clone(), limits).unwrap(), task)
}

#[test]
fn selections_require_selected_bounded_json_and_exact_current_hash() {
    let f = Fixture::new();
    for source in ["../outside.json", "absent.json", "cargo.json"] {
        let mut s = vec![PackingSelection {
            source: source.into(),
            source_sha256: None,
        }];
        assert!(bind_selections(&f.ws, &[], &mut s).is_err());
    }
    let mut stale = vec![PackingSelection {
        source: "cargo.json".into(),
        source_sha256: Some("f".repeat(64)),
    }];
    assert!(bind_selections(&f.ws, &["cargo.json".into()], &mut stale).is_err());
    let mut duplicates = vec![f.selected.clone(), f.selected.clone()];
    assert!(bind_selections(&f.ws, &["cargo.json".into()], &mut duplicates).is_err());
    std::fs::write(
        f.ws.root().join("large.json"),
        vec![b' '; 2 * 1024 * 1024 + 1],
    )
    .unwrap();
    let mut large = vec![PackingSelection {
        source: "large.json".into(),
        source_sha256: None,
    }];
    assert!(bind_selections(&f.ws, &["large.json".into()], &mut large).is_err());
    assert_eq!(
        selection_index(&json!({"selection_index":0}), &[f.selected.clone()]),
        Some(0)
    );
    for args in [
        json!({"selection_index":0,"state":{}}),
        json!({"selection_index":0,"options":{}}),
        json!({"selection_index":1}),
        json!({"selection_index":-1}),
    ] {
        assert_eq!(selection_index(&args, &[f.selected.clone()]), None);
    }
}

#[tokio::test]
async fn absent_key_and_off_keep_baseline_and_failed_candidate_and_cache_once() {
    let f = Fixture::new();
    let (budget, task) = budget(8);
    let cancel = CancellationToken::new();
    let mut run = PackingTurn::default();
    let result = run
        .calculate(
            &f.worker,
            &f.ws,
            &f.selected,
            &cfg(JevMode::Shadow),
            &budget,
            &task,
            &cancel,
        )
        .await
        .unwrap();
    assert_eq!(result["decision_proposal"]["reason"], "key_not_configured");
    assert_eq!(result["result"]["final"], result["result"]["baseline"]);
    assert!(result["result"]["rounds"][1]["summary"].is_null());
    assert_eq!(result["provenance"]["shipping_release"], false);
    let again = run
        .calculate(
            &f.worker,
            &f.ws,
            &f.selected,
            &cfg(JevMode::Off),
            &budget,
            &task,
            &cancel,
        )
        .await
        .unwrap();
    assert_eq!(again["provenance"]["replayed_in_turn"], true);
    assert_eq!(f.calls(), 1);
    assert_eq!(budget.snapshot().unwrap().model_calls, 0);
    let payload: Value =
        serde_json::from_slice(&std::fs::read(f.root.join("request.json")).unwrap()).unwrap();
    assert_eq!(payload["payload"].as_object().unwrap().len(), 2);
    assert!(payload["payload"].get("state").is_none());
}

struct JevServer {
    cfg: JevConfig,
    seen: Arc<Mutex<Vec<Value>>>,
    server: tokio::task::JoinHandle<()>,
}
impl Drop for JevServer {
    fn drop(&mut self) {
        self.server.abort();
    }
}
async fn jev(invalid: bool, mutate: Option<PathBuf>, delay: Duration) -> JevServer {
    let seen = Arc::new(Mutex::new(Vec::new()));
    let recorded = seen.clone();
    let app=Router::new().route("/decision",post(move |Json(body):Json<Value>| {
        let seen=recorded.clone(); let mutate=mutate.clone(); async move {
            seen.lock().unwrap().push(body);
            if let Some(path)=mutate { std::fs::write(path,"source changed during decision").unwrap(); }
            tokio::time::sleep(delay).await;
            Json(json!({"model":"scripted-jev","answers":{"preferred_recomputed_candidate":{"type":"choice","choice":if invalid{"invent_new_geometry"}else{"round_1"},
                "confidence":1.0,"probabilities":{"keep_baseline":0.0,"round_1":1.0}}},"usage":{"input_tokens":10,"output_tokens":10}}))
        }
    }));
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let endpoint = format!("http://{}/decision", listener.local_addr().unwrap());
    let server = tokio::spawn(async move { axum::serve(listener, app).await.unwrap() });
    JevServer {
        cfg: JevConfig {
            endpoint,
            api_key: "local-fixture-not-a-provider-key".into(),
            model: "fixture".into(),
            mode: JevMode::Assist,
        },
        seen,
        server,
    }
}

#[tokio::test]
async fn assist_is_shadow_only_and_host_builds_options_without_failed_candidates() {
    let f = Fixture::new();
    let server = jev(false, None, Duration::ZERO).await;
    let (budget, task) = budget(8);
    let value = PackingTurn::default()
        .calculate(
            &f.worker,
            &f.ws,
            &f.selected,
            &server.cfg,
            &budget,
            &task,
            &CancellationToken::new(),
        )
        .await
        .unwrap();
    assert_eq!(value["decision_proposal"]["requested_mode"], "assist");
    assert_eq!(value["decision_proposal"]["mode"], "shadow");
    assert_eq!(value["decision_proposal"]["applied"], false);
    assert_eq!(value["result"]["baseline"], value["result"]["final"]);
    let requests = server.seen.lock().unwrap();
    assert_eq!(requests.len(), 1);
    assert_eq!(requests[0]["state"]["phase"], "packing_replan_shadow_v1");
    assert_eq!(
        requests[0]["questions"]["preferred_recomputed_candidate"]["criteria"]
            .as_object()
            .unwrap()
            .len(),
        2
    );
    assert!(requests[0]["state"]["candidates"].get("round_2").is_none());
    assert!(!requests[0]
        .to_string()
        .contains("scripted protocol fixture, not a solver input"));
}

#[tokio::test]
async fn invalid_jev_candidate_and_exhausted_budget_keep_original_deterministic_result() {
    let f = Fixture::new();
    let server = jev(true, None, Duration::ZERO).await;
    let (normal, task) = budget(8);
    let value = PackingTurn::default()
        .calculate(
            &f.worker,
            &f.ws,
            &f.selected,
            &server.cfg,
            &normal,
            &task,
            &CancellationToken::new(),
        )
        .await
        .unwrap();
    assert_eq!(
        value["decision_proposal"]["status"],
        "deterministic_fallback"
    );
    assert_eq!(value["result"]["baseline"], value["result"]["final"]);
    let (empty, task) = budget(0);
    let calls = server.seen.lock().unwrap().len();
    let value = PackingTurn::default()
        .calculate(
            &f.worker,
            &f.ws,
            &f.selected,
            &server.cfg,
            &empty,
            &task,
            &CancellationToken::new(),
        )
        .await
        .unwrap();
    assert_eq!(
        value["decision_proposal"]["status"],
        "deterministic_fallback"
    );
    assert_eq!(server.seen.lock().unwrap().len(), calls);
    assert_eq!(value["result"]["baseline"], value["result"]["final"]);
}

#[tokio::test]
async fn decision_timeout_falls_back_without_retry_or_changing_plan() {
    let f = Fixture::new();
    let server = jev(false, None, Duration::from_secs(30)).await;
    let (budget, task) = budget(8);
    let started = std::time::Instant::now();
    let value = PackingTurn::default()
        .calculate(
            &f.worker,
            &f.ws,
            &f.selected,
            &server.cfg,
            &budget,
            &task,
            &CancellationToken::new(),
        )
        .await
        .unwrap();
    assert!(started.elapsed() < Duration::from_secs(22));
    assert_eq!(server.seen.lock().unwrap().len(), 1);
    assert_eq!(
        value["decision_proposal"]["status"],
        "deterministic_fallback"
    );
    assert_eq!(value["result"]["baseline"], value["result"]["final"]);
}

#[tokio::test]
async fn stale_sources_after_solver_or_decision_are_never_published() {
    let f = Fixture::new();
    let (budget, task) = budget(8);
    std::fs::write(f.root.join("mutate"), "yes").unwrap();
    assert!(PackingTurn::default()
        .calculate(
            &f.worker,
            &f.ws,
            &f.selected,
            &cfg(JevMode::Off),
            &budget,
            &task,
            &CancellationToken::new()
        )
        .await
        .is_err());
    let f = Fixture::new();
    let server = jev(false, Some(f.ws.root().join("cargo.json")), Duration::ZERO).await;
    assert!(PackingTurn::default()
        .calculate(
            &f.worker,
            &f.ws,
            &f.selected,
            &server.cfg,
            &budget,
            &task,
            &CancellationToken::new()
        )
        .await
        .is_err());
}

#[tokio::test]
async fn invalid_worker_contract_is_rejected_without_repeating_solver() {
    let f = Fixture::new();
    let (budget, task) = budget(8);
    let mut run = PackingTurn::default();
    let mut invalid = valid_result();
    invalid["hard_constraints"]["shipping_release"] = json!(true);
    f.set_result(invalid);
    for _ in 0..2 {
        assert!(run
            .calculate(
                &f.worker,
                &f.ws,
                &f.selected,
                &cfg(JevMode::Off),
                &budget,
                &task,
                &CancellationToken::new()
            )
            .await
            .is_err());
    }
    assert_eq!(f.calls(), 1);
}

async fn request(app: &Router, method: &str, url: &str, value: Value) -> (StatusCode, Value) {
    let response = app
        .clone()
        .oneshot(
            Request::builder()
                .method(method)
                .uri(url)
                .header("content-type", "application/json")
                .body(Body::from(value.to_string()))
                .unwrap(),
        )
        .await
        .unwrap();
    let status = response.status();
    let bytes = response.into_body().collect().await.unwrap().to_bytes();
    (status, serde_json::from_slice(&bytes).unwrap())
}
async fn wait_turn(app: &Router, wid: &str, id: &str, session: &str) -> Value {
    let url = format!("/api/agent/turns/{id}/events?workspace={wid}&session_id={session}");
    for _ in 0..300 {
        let value = request(app, "GET", &url, Value::Null).await.1;
        if matches!(
            value["turn"]["status"].as_str(),
            Some("completed" | "failed" | "cancelled")
        ) {
            return value;
        }
        tokio::time::sleep(Duration::from_millis(40)).await;
    }
    panic!("packing turn timed out")
}

#[tokio::test]
async fn api_steps_only_runs_explicit_packing_sources_and_stores_host_hash() {
    let f = Fixture::new();
    let app = f.app();
    assert_eq!(
        request(&app, "GET", "/api/agent/capabilities", Value::Null)
            .await
            .1["features"]["packing_replan"],
        true
    );
    let registered = request(
        &app,
        "POST",
        "/api/agent/workspaces",
        json!({"path":f.ws.root()}),
    )
    .await
    .1;
    let wid = registered["workspace"]["id"].as_str().unwrap();
    let mut input = json!({"workspace":wid,"session_id":"plain","message":"Read selected JSON","mode":"steps","files":["cargo.json"]});
    let (status, started) = request(&app, "POST", "/api/agent/turns", input.clone()).await;
    assert_eq!(status, StatusCode::ACCEPTED);
    let _ = wait_turn(&app, wid, started["turn_id"].as_str().unwrap(), "plain").await;
    assert_eq!(f.calls(), 0);
    input["session_id"] = json!("packing");
    input["packing_sources"] = json!([{"source":"cargo.json","source_sha256":"f".repeat(64)}]);
    assert_eq!(
        request(&app, "POST", "/api/agent/turns", input.clone())
            .await
            .0,
        StatusCode::BAD_REQUEST
    );
    input["packing_sources"] = json!([{"source":"cargo.json"}]);
    let (status, started) = request(&app, "POST", "/api/agent/turns", input).await;
    assert_eq!(status, StatusCode::ACCEPTED, "{started}");
    let completed = wait_turn(&app, wid, started["turn_id"].as_str().unwrap(), "packing").await;
    assert_eq!(completed["turn"]["status"], "completed", "{completed}");
    assert_eq!(
        completed["turn"]["request"]["packing_sources"][0]["source_sha256"],
        json!(f.selected.source_sha256)
    );
    assert_eq!(
        completed["turn"]["result"]["findings"][0]["result"]["kind"],
        "packing_replan"
    );
    assert_eq!(f.calls(), 1);
    assert_eq!(completed["turn"]["result"]["usage"]["model_calls"], 0);
}

#[tokio::test]
async fn scripted_agent_can_reach_packing_tool_without_free_state_or_provider_calls() {
    let f = Fixture::new();
    let mut large = valid_result();
    large["baseline"]["layout"] = json!(["large audit placement ".repeat(4000)]);
    large["final"]["solver_plan"] = json!({"raw":"full solver audit ".repeat(4000)});
    f.set_result(large);
    let app = f.app();
    let seen = Arc::new(Mutex::new(Vec::<Value>::new()));
    let captures = seen.clone();
    let model=Router::new().route("/chat/completions",post(move |Json(body):Json<Value>| {
        let seen=captures.clone(); async move {
            let messages=body["messages"].as_array().unwrap();
            let skip=messages.iter().any(|message|message["role"]=="user" && message["content"].as_str().is_some_and(|text|text.contains("scripted-skip-packing-call")));
            let first=!skip && !messages.iter().any(|message|message["role"]=="tool");
            seen.lock().unwrap().push(body);
            Json(json!({"model":"scripted-main","choices":[{"finish_reason":if first{"tool_calls"}else{"stop"},"message":if first{
                json!({"role":"assistant","tool_calls":[{"id":"pack-call","type":"function","function":{"name":"packing_replan","arguments":"{\"selection_index\":0}"}}]})
            }else{json!({"role":"assistant","content":"Completed."})}}],"usage":{"prompt_tokens":10,"completion_tokens":10}}))
        }
    }));
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let base = format!("http://{}", listener.local_addr().unwrap());
    let server = tokio::spawn(async move { axum::serve(listener, model).await.unwrap() });
    civil_workbench::config::set_runtime_llm(Some(LlmConfig {
        api_key: "scripted-local-only".into(),
        base_url: base,
        model: "scripted-main".into(),
    }));
    let registered = request(
        &app,
        "POST",
        "/api/agent/workspaces",
        json!({"path":f.ws.root()}),
    )
    .await
    .1;
    let wid = registered["workspace"]["id"].as_str().unwrap();
    let (_,started)=request(&app,"POST","/api/agent/turns",json!({"workspace":wid,"session_id":"agent","message":"Please reorder the selected cargo","mode":"model","files":["cargo.json"],"packing_sources":[{"source":"cargo.json"}]})).await;
    let completed = wait_turn(&app, wid, started["turn_id"].as_str().unwrap(), "agent").await;
    assert_eq!(completed["turn"]["status"], "completed", "{completed}");
    assert_eq!(completed["turn"]["result"]["partial"], false);
    assert_eq!(completed["turn"]["result"]["execution_evidence"]["packing_unfinished_selection_indexes"], json!([]));
    assert_eq!(f.calls(), 1);
    let requests = seen.lock().unwrap().clone();
    let packing = requests[0]["tools"]
        .as_array()
        .unwrap()
        .iter()
        .find(|tool| tool["function"]["name"] == "packing_replan")
        .unwrap();
    assert_eq!(
        packing["function"]["parameters"]["properties"]
            .as_object()
            .unwrap()
            .len(),
        1
    );
    assert_eq!(
        packing["function"]["parameters"]["additionalProperties"],
        false
    );
    let tool_message = requests[1]["messages"]
        .as_array()
        .unwrap()
        .iter()
        .find(|m| m["role"] == "tool")
        .unwrap();
    let tool_value: Value =
        serde_json::from_str(tool_message["content"].as_str().unwrap()).unwrap();
    assert_eq!(tool_value["ok"], true);
    assert_eq!(tool_value["result"]["final"]["containers_used"], 2);
    assert_eq!(tool_value["layout_and_boxes_omitted"], true);
    assert!(tool_message["content"].as_str().unwrap().len() < 32_000);

    let mut refused = valid_result();
    refused["status"] = json!("needs_human");
    refused["outcome"] = json!("needs_human");
    refused["baseline"] = Value::Null;
    refused["final"] = Value::Null;
    refused["rounds"] = json!([]);
    refused["needs_human"] = json!([{"field":"cargo.weight","reason":"missing_weight"}]);
    let mut cannot_fit = valid_result();
    cannot_fit["baseline"]["can_fit"] = json!(false);
    cannot_fit["final"]["can_fit"] = json!(false);
    for (session, worker_result) in [("missing_inputs", refused), ("cannot_fit", cannot_fit)] {
        f.set_result(worker_result);
        let (status, started) = request(&app, "POST", "/api/agent/turns", json!({
            "workspace":wid,"session_id":session,"message":"Please reorder the selected cargo",
            "mode":"model","files":["cargo.json"],"packing_sources":[{"source":"cargo.json"}]
        })).await;
        assert_eq!(status, StatusCode::ACCEPTED, "{started}");
        let finished = wait_turn(&app, wid, started["turn_id"].as_str().unwrap(), session).await;
        assert_eq!(finished["turn"]["status"], "completed", "{finished}");
        let result = &finished["turn"]["result"];
        assert_eq!(result["reply"], "Completed.");
        assert_eq!(result["partial"], true, "{finished}");
        assert_eq!(result["tool_errors"], 0);
        assert_eq!(result["execution_evidence"]["packing_incomplete"], true);
        assert_eq!(result["execution_evidence"]["packing_unfinished_selection_indexes"], json!([0]));
        assert_eq!(result["execution_evidence"]["requested_task_complete"], false);
        assert_eq!(result["execution_evidence"]["successful_tools"], json!(["packing_replan"]));
    }
    assert_eq!(f.calls(), 3);
    assert_eq!(seen.lock().unwrap().len(), 6);

    // A final model sentence is not a receipt: zero tool calls or a result for
    // only one of two selected sources must leave the requested task partial.
    f.set_result(valid_result());
    std::fs::copy(f.ws.root().join("cargo.json"), f.ws.root().join("second.json")).unwrap();
    for (session, message, files, selections, unfinished, expected_calls, tools) in [
        ("skipped", "Please reorder the selected cargo: scripted-skip-packing-call", json!(["cargo.json"]),
            json!([{"source":"cargo.json"}]), json!([0]), 3, json!([])),
        ("subset", "Please reorder both selected cargo sources", json!(["cargo.json","second.json"]),
            json!([{"source":"cargo.json"},{"source":"second.json"}]), json!([1]), 4, json!(["packing_replan"]))
    ] {
        let (status, started) = request(&app, "POST", "/api/agent/turns", json!({
            "workspace":wid,"session_id":session,"message":message,"mode":"model",
            "files":files,"packing_sources":selections
        })).await;
        assert_eq!(status, StatusCode::ACCEPTED, "{started}");
        let finished = wait_turn(&app, wid, started["turn_id"].as_str().unwrap(), session).await;
        assert_eq!(finished["turn"]["status"], "completed", "{finished}");
        let result = &finished["turn"]["result"];
        assert_eq!(result["reply"], "Completed.");
        assert_eq!(result["partial"], true, "{finished}");
        assert_eq!(result["tool_errors"], 0);
        assert_eq!(result["execution_evidence"]["packing_incomplete"], true);
        assert_eq!(result["execution_evidence"]["packing_unfinished_selection_indexes"], unfinished);
        assert_eq!(result["execution_evidence"]["requested_task_complete"], false);
        assert_eq!(result["execution_evidence"]["successful_tools"], tools);
        assert_eq!(f.calls(), expected_calls);
    }
    assert_eq!(seen.lock().unwrap().len(), 9);
    civil_workbench::config::set_runtime_llm(None);
    server.abort();
}

#[tokio::test]
async fn actual_fixed_worker_recomputes_geometry_example_and_preserves_source() {
    let f = Fixture::new();
    let repo = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .unwrap()
        .to_path_buf();
    let input = std::fs::read(repo.join("examples/packing-replan/geometry-only.json")).unwrap();
    std::fs::write(f.ws.root().join("cargo.json"), &input).unwrap();
    let mut selections = vec![PackingSelection {
        source: "cargo.json".into(),
        source_sha256: None,
    }];
    bind_selections(&f.ws, &["cargo.json".into()], &mut selections).unwrap();
    let (budget, task) = budget(8);
    let value = PackingTurn::default()
        .calculate(
            &WorkerHost::detect(repo),
            &f.ws,
            &selections[0],
            &cfg(JevMode::Off),
            &budget,
            &task,
            &CancellationToken::new(),
        )
        .await
        .unwrap();
    assert_eq!(value["ok"], true, "{value}");
    assert_eq!(value["result"]["status"], "completed", "{value}");
    assert!(value["result"]["rounds"].as_array().unwrap().len() <= 2);
    assert_eq!(value["result"]["final"]["layout_verified"], true);
    assert_eq!(
        value["result"]["hard_constraints"]["shipping_release"],
        false
    );
    let compact = civil_workbench::product::packing::model_result_summary(&value);
    assert_eq!(compact["result"]["requires_human_review"], true);
    assert_eq!(
        compact["result"]["stop_reason"],
        value["result"]["stop_reason"]
    );
    assert!(compact["result"]["stop_reason"].is_string());
    for name in ["pass", "needs_reinforcement", "fail", "pending_design"] {
        assert_eq!(
            compact["result"]["final"]["structure"][name],
            value["result"]["final"]["structure"][name]
        );
    }
    assert_eq!(
        sha256(&std::fs::read(f.ws.root().join("cargo.json")).unwrap()),
        sha256(&input)
    );
}
