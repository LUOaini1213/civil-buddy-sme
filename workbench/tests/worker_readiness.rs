//! Offline router/transport contracts. The owned test bootstrap supplies typed
//! sandbox receipts; it does not test or claim real kernel confinement. The
//! successful path imports the shipped deterministic verdict sanitizer.
use axum::{
    body::Body,
    extract::State,
    http::{Request, StatusCode},
    routing::post,
    Json, Router,
};
use civil_workbench::{
    config::{self, LlmConfig, Paths},
    product::{
        api::{router, ProductState},
        auth::InstanceAuth,
        tools::{sha256, ToolScope},
        worker::validated_review_results,
    },
    runtime_core::{CancellationToken, WorkspaceContext},
};
use http_body_util::BodyExt;
use serde_json::{json, Value};
use std::{
    path::PathBuf,
    sync::{
        atomic::{AtomicUsize, Ordering},
        Arc,
    },
    time::Duration,
};
use tower::ServiceExt;

static CONFIG_TEST: tokio::sync::Mutex<()> = tokio::sync::Mutex::const_new(());
const KEY: &str = "offline-worker-readiness-key-do-not-persist";
const RAW: &str = "unchecked-draft-private-sentinel";

const BOOTSTRAP: &str = r#"
import json, os, pathlib, sys, time
sys.path.insert(0, __SOURCE__)
scenario = __SCENARIO__
# Match the real host's binary UTF-8 protocol. Python -I ignores the host's
# PYTHONUTF8/PYTHONIOENCODING environment, so Windows text streams can otherwise
# decode the draft into different characters before the actual guard sees it.
request = json.loads(sys.stdin.buffer.read())
texts = request.get('texts', [])
ready = texts == ['Civil Buddy fixed review runtime readiness check.']
record = {'module': os.environ.get('CIVIL_HOST_MODULE'), 'operation': request.get('operation'),
          'workspace': request.get('workspace'), 'output_dir': request.get('output_dir'),
          'backend': os.environ.get('CIVIL_HOST_SANDBOX'), 'readiness': ready, 'text_count': len(texts)}
with open('calls.jsonl', 'a', encoding='utf-8') as stream:
    stream.write(json.dumps(record) + '\n')
pathlib.Path('entered').write_text('ready', encoding='utf-8')
if scenario == 'delayed':
    time.sleep(30)
    pathlib.Path('late-output').write_text('worker was not cancelled', encoding='utf-8')
if scenario == 'transport':
    sys.stderr.write('private-bootstrap-error-path-and-key')
    sys.exit(7)
from packing_assistant.review_worker import handle
response = handle(request)
if scenario == 'prefail' or (scenario == 'finalfail' and not ready):
    response.update(ok=False, result=None, error={'code': 'sandbox_unavailable',
        'message': 'private-bootstrap-error-path-and-key',
        'details': {'phase': 'kernel_confinement', 'category': 'permission_denied', 'os_error_code': 5,
                    'path': 'private-bootstrap-error-path-and-key', 'api_key': 'private-bootstrap-error-path-and-key',
                    'selftest': {'spawn_process': 'denied', 'inet_socket': 'unavailable',
                                 'write_home': 'private-bootstrap-error-path-and-key',
                                 'unknown': 'allowed'}}})
else:
    mutation = scenario.removeprefix('final_') if not ready else scenario
    if mutation == 'missing_found':
        response['result']['results'][0].pop('found')
    elif mutation == 'wrong_found':
        response['result']['results'][0]['found'] = None
    elif mutation == 'missing_text':
        response['result']['results'][0].pop('text')
    elif mutation == 'wrong_notice':
        response['result']['results'][0]['notice'] = 4
    elif mutation == 'extra_result':
        response['result']['results'].append(response['result']['results'][0])
    elif mutation == 'wrong_call_id':
        response['call_id'] = 'not-the-request-id'
    elif mutation == 'error_on_success':
        response['error'] = {'code': 'worker_failed', 'message': 'private-bootstrap-error-path-and-key'}
    elif mutation == 'extra_field':
        response['result']['results'][0]['untrusted'] = 'private-bootstrap-error-path-and-key'
response['sandbox'] = {'asked': os.environ.get('CIVIL_HOST_SANDBOX'),
                       'enforces': {'write': True, 'spawn': True, 'read': False, 'network': False}}
sys.stdout.buffer.write((json.dumps(response, ensure_ascii=False) + '\n').encode('utf-8'))
sys.stdout.buffer.flush()
"#;

struct Fixture {
    root: PathBuf,
    app: Router,
    state: Arc<ProductState>,
    workspace: WorkspaceContext,
    wid: String,
}
impl Fixture {
    fn new(scenario: &str) -> Self {
        assert!(std::env::var_os("CIVIL_STATE_ROOT").is_none());
        let source = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .parent()
            .unwrap()
            .to_owned();
        let root = std::env::temp_dir().join(format!(
            "civil-worker-readiness-{}",
            uuid::Uuid::new_v4().simple()
        ));
        let job = root.join("job");
        let bootstrap = root.join("bootstrap");
        std::fs::create_dir_all(&job).unwrap();
        std::fs::create_dir_all(bootstrap.join("packing_assistant")).unwrap();
        std::fs::write(
            bootstrap.join("packing_assistant/host_worker.py"),
            BOOTSTRAP
                .replace(
                    "__SOURCE__",
                    &serde_json::to_string(&source.to_string_lossy()).unwrap(),
                )
                .replace("__SCENARIO__", &serde_json::to_string(scenario).unwrap()),
        )
        .unwrap();
        let mut paths = Paths::from_demo(source.join("demo"));
        paths.data_dir = root.join("state");
        let mut state = ProductState::open_with_auth(paths, InstanceAuth::local()).unwrap();
        Arc::get_mut(&mut state).unwrap().worker.repo_root = bootstrap;
        let wid = state.register(job.to_str().unwrap()).unwrap()["id"]
            .as_str()
            .unwrap()
            .to_owned();
        let workspace = WorkspaceContext::new(job).unwrap();
        Self {
            root,
            app: router(state.clone()),
            state,
            workspace,
            wid,
        }
    }
    async fn start(&self, mode: &str) -> String {
        let (status, body) = http(&self.app, "POST", "/api/agent/turns",
            json!({"workspace":self.wid,"session_id":"offline","message":"Explain the workflow.","locale":"en","mode":mode,"files":[]})).await;
        assert_eq!(status, StatusCode::ACCEPTED, "{body}");
        body["turn_id"].as_str().unwrap().to_owned()
    }
    async fn finished(&self, id: &str) -> Value {
        tokio::time::timeout(Duration::from_secs(15), async {
            loop {
                let (status, body) = http(
                    &self.app,
                    "GET",
                    &format!(
                        "/api/agent/turns/{id}?workspace={}&session_id=offline",
                        self.wid
                    ),
                    Value::Null,
                )
                .await;
                assert_eq!(status, StatusCode::OK);
                if matches!(
                    body["turn"]["status"].as_str(),
                    Some("completed" | "failed" | "cancelled")
                ) {
                    return body["turn"].clone();
                }
                tokio::time::sleep(Duration::from_millis(10)).await;
            }
        })
        .await
        .expect("offline task did not finish")
    }
    async fn events(&self, id: &str) -> Value {
        http(
            &self.app,
            "GET",
            &format!(
                "/api/agent/turns/{id}/events?workspace={}&session_id=offline",
                self.wid
            ),
            Value::Null,
        )
        .await
        .1
    }
    fn calls(&self) -> Vec<Value> {
        std::fs::read_to_string(self.root.join("bootstrap/calls.jsonl"))
            .unwrap_or_default()
            .lines()
            .map(|line| serde_json::from_str(line).unwrap())
            .collect()
    }
    fn no_outputs(&self) {
        assert!(
            !self.workspace.output_root().exists(),
            "rejected operation created an output directory"
        );
    }
}
impl Drop for Fixture {
    fn drop(&mut self) {
        config::set_runtime_llm(None);
        let _ = std::fs::remove_dir_all(&self.root);
    }
}

async fn http(app: &Router, method: &str, url: &str, value: Value) -> (StatusCode, Value) {
    let response = app
        .clone()
        .oneshot(
            Request::builder()
                .method(method)
                .uri(url)
                .header("Content-Type", "application/json")
                .body(Body::from(value.to_string()))
                .unwrap(),
        )
        .await
        .unwrap();
    let status = response.status();
    let bytes = response.into_body().collect().await.unwrap().to_bytes();
    (status, serde_json::from_slice(&bytes).unwrap())
}

struct ProviderState {
    calls: AtomicUsize,
    reply: String,
}
struct LocalProvider {
    base: String,
    state: Arc<ProviderState>,
    server: tokio::task::JoinHandle<()>,
}
impl LocalProvider {
    async fn new(reply: &str) -> Self {
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let base = format!("http://{}/v1", listener.local_addr().unwrap());
        let state = Arc::new(ProviderState {
            calls: AtomicUsize::new(0),
            reply: reply.into(),
        });
        let app = Router::new()
            .route("/v1/chat/completions", post(model))
            .with_state(state.clone());
        let server = tokio::spawn(async move {
            axum::serve(listener, app).await.unwrap();
        });
        Self {
            base,
            state,
            server,
        }
    }
    fn configure(&self) {
        config::set_runtime_llm(Some(LlmConfig {
            api_key: KEY.into(),
            base_url: self.base.clone(),
            model: "offline-readiness-model".into(),
        }));
    }
    fn calls(&self) -> usize {
        self.state.calls.load(Ordering::SeqCst)
    }
}
impl Drop for LocalProvider {
    fn drop(&mut self) {
        self.server.abort();
    }
}
async fn model(State(state): State<Arc<ProviderState>>, Json(_body): Json<Value>) -> Json<Value> {
    state.calls.fetch_add(1, Ordering::SeqCst);
    Json(
        json!({"model":"offline-readiness-model","choices":[{"message":{"role":"assistant","content":state.reply},"finish_reason":"stop"}],"usage":{"prompt_tokens":5,"completion_tokens":5,"total_tokens":10}}),
    )
}

fn assert_no_private(value: &Value) {
    let text = value.to_string();
    for forbidden in [
        RAW,
        KEY,
        "private-bootstrap-error-path-and-key",
        "api_key",
        "最终回复未通过工程结论检查",
    ] {
        assert!(
            !text.contains(forbidden),
            "unchecked or private value entered the turn/event record"
        );
    }
}

#[tokio::test]
async fn readiness_failure_stops_before_provider_and_records_safe_os_diagnostics() {
    let _guard = CONFIG_TEST.lock().await;
    let provider = LocalProvider::new(RAW).await;
    let f = Fixture::new("prefail");
    provider.configure();
    let id = f.start("model").await;
    let turn = f.finished(&id).await;
    let events = f.events(&id).await;
    assert_eq!(turn["status"], "failed", "{turn}");
    assert_eq!(provider.calls(), 0);
    assert_eq!(
        turn["result"]["review_receipt"],
        json!({"phase":"worker_readiness","status":"unavailable","code":"sandbox_unavailable","details":{"phase":"kernel_confinement","category":"permission_denied","os_error_code":5,"selftest":{"spawn_process":"denied","inet_socket":"unavailable"}}})
    );
    assert!(events["events"]
        .as_array()
        .unwrap()
        .iter()
        .any(|event| event["kind"] == "review_failed"
            && event["data"] == turn["result"]["review_receipt"]));
    assert_eq!(turn["result"]["artifacts"], json!([]));
    assert_no_private(&turn);
    assert_no_private(&events);
    f.no_outputs();
}

#[tokio::test]
async fn readiness_transport_and_malformed_success_fail_closed_without_model_calls() {
    let _guard = CONFIG_TEST.lock().await;
    let provider = LocalProvider::new(RAW).await;
    for scenario in [
        "transport",
        "missing_found",
        "wrong_found",
        "missing_text",
        "wrong_notice",
        "extra_result",
        "wrong_call_id",
        "error_on_success",
        "extra_field",
    ] {
        let f = Fixture::new(scenario);
        provider.configure();
        let id = f.start("model").await;
        let turn = f.finished(&id).await;
        assert_eq!(turn["status"], "failed", "scenario {scenario}: {turn}");
        assert_eq!(provider.calls(), 0);
        assert_eq!(
            turn["result"]["review_receipt"]["code"],
            if scenario == "transport" {
                "worker_transport_unavailable"
            } else {
                "malformed_review_response"
            }
        );
        assert_no_private(&turn);
        assert_no_private(&f.events(&id).await);
        f.no_outputs();
    }
}

#[tokio::test]
async fn final_review_service_failure_is_incomplete_and_withholds_unchecked_draft() {
    let _guard = CONFIG_TEST.lock().await;
    for scenario in ["finalfail", "final_missing_found"] {
        let f = Fixture::new(scenario);
        let provider = LocalProvider::new(RAW).await;
        provider.configure();
        let id = f.start("model").await;
        let turn = f.finished(&id).await;
        assert_eq!(provider.calls(), 1);
        assert_eq!(turn["status"], "failed", "{turn}");
        assert_eq!(turn["result"]["partial"], true);
        assert_eq!(turn["result"]["review_receipt"]["phase"], "final_review");
        assert_eq!(
            turn["result"]["review_receipt"]["code"],
            if scenario == "finalfail" {
                "sandbox_unavailable"
            } else {
                "malformed_review_response"
            }
        );
        assert!(turn["result"]["reply"]
            .as_str()
            .unwrap()
            .contains("review service is unavailable"));
        let events = f.events(&id).await;
        assert_no_private(&turn);
        assert_no_private(&events);
        let calls = f.calls();
        assert_eq!(calls.len(), 2);
        assert_eq!(calls[0]["readiness"], true);
        assert_eq!(calls[1]["readiness"], false);
        assert_eq!(calls[0]["workspace"], calls[1]["workspace"]);
        assert_eq!(calls[0]["output_dir"], calls[1]["output_dir"]);
        assert_eq!(calls[0]["backend"], calls[1]["backend"]);
        f.no_outputs();
    }
}

#[tokio::test]
async fn readiness_is_async_cancellable_and_never_reaches_provider_after_cancel() {
    let _guard = CONFIG_TEST.lock().await;
    let f = Fixture::new("delayed");
    let provider = LocalProvider::new(RAW).await;
    provider.configure();
    let id = f.start("model").await;
    tokio::time::timeout(Duration::from_secs(10), async {
        while !f.root.join("bootstrap/entered").exists() {
            tokio::time::sleep(Duration::from_millis(10)).await;
        }
    })
    .await
    .unwrap();
    let events = f.events(&id).await;
    assert!(events["events"]
        .as_array()
        .unwrap()
        .iter()
        .any(|event| event["kind"] == "status" && event["data"]["phase"] == "worker_readiness"));
    assert_eq!(
        http(
            &f.app,
            "POST",
            &format!(
                "/api/agent/turns/{id}/cancel?workspace={}&session_id=offline",
                f.wid
            ),
            Value::Null
        )
        .await
        .0,
        StatusCode::OK
    );
    let turn = f.finished(&id).await;
    assert_eq!(turn["status"], "cancelled", "{turn}");
    assert!(turn["result"].get("review_receipt").is_none());
    assert_eq!(provider.calls(), 0);
    assert!(!f.root.join("bootstrap/late-output").exists());
    f.no_outputs();
}

#[tokio::test]
async fn steps_mode_skips_readiness_and_the_model() {
    let _guard = CONFIG_TEST.lock().await;
    let f = Fixture::new("prefail");
    let provider = LocalProvider::new(RAW).await;
    provider.configure();
    let id = f.start("steps").await;
    let turn = f.finished(&id).await;
    assert_eq!(turn["status"], "completed", "{turn}");
    assert_eq!(provider.calls(), 0);
    assert!(f.calls().is_empty());
    assert!(!f.events(&id).await["events"]
        .as_array()
        .unwrap()
        .iter()
        .any(|event| event["kind"] == "worker_readiness"
            || event["data"]["phase"] == "worker_readiness"));
    f.no_outputs();
}

#[tokio::test]
async fn successful_review_still_strikes_dangerous_engineering_verdicts() {
    let _guard = CONFIG_TEST.lock().await;
    let f = Fixture::new("clean");
    let provider = LocalProvider::new("可以投标。可以开工。可以订舱。").await;
    provider.configure();
    let id = f.start("model").await;
    let turn = f.finished(&id).await;
    assert_eq!(turn["status"], "completed", "{turn}");
    assert_eq!(provider.calls(), 1);
    assert_eq!(turn["result"]["verdict_guard"].as_array().unwrap().len(), 3);
    assert!(turn["result"]["reply"]
        .as_str()
        .unwrap()
        .starts_with("（此处结论不由本系统判定）"));
    assert_no_private(&turn);
}

#[tokio::test]
async fn malformed_patch_review_blocks_preview_and_apply_before_document_dispatch() {
    let _guard = CONFIG_TEST.lock().await;
    for scenario in [
        "missing_found",
        "wrong_found",
        "extra_result",
        "wrong_call_id",
        "error_on_success",
    ] {
        let f = Fixture::new(scenario);
        let selected = vec!["report.docx".to_owned()];
        let path = f.workspace.root().join(&selected[0]);
        let original = b"original-document-sentinel";
        std::fs::write(&path, original).unwrap();
        let before = sha256(original);
        let cancel = CancellationToken::new();
        let scope = ToolScope {
            state: &f.state,
            workspace: &f.workspace,
            selected: &selected,
            file_bindings: &[],
            user_request: "Replace the wording with revised wording",
            write: true,
            cancel: &cancel,
        };
        for name in ["preview_document", "apply_document"] {
            let result = scope.execute(name, json!({"source":selected[0],"expected_sha256":before,
                "patches":[{"op":"replace_paragraph","paragraph_id":"p:1","expected_text":"original wording","text":"revised wording"}]})).await;
            assert!(
                result.is_err(),
                "malformed {scenario} review allowed {name}: {result:?}"
            );
        }
        assert_eq!(sha256(&std::fs::read(&path).unwrap()), before);
        assert_eq!(f.calls().len(), 2);
        assert!(f
            .calls()
            .iter()
            .all(|call| call["module"] == "packing_assistant.review_worker"));
        f.no_outputs();
    }
}

#[test]
fn shared_review_schema_requires_request_identity_exact_count_and_complete_rows() {
    let clean = json!({"version":1,"call_id":"request","ok":true,"result":{"results":[{"text":"ok","notice":"","found":[]}]},"error":null});
    assert!(validated_review_results(&clean, "request", 1).is_some());
    assert!(validated_review_results(&clean, "other", 1).is_none());
    assert!(validated_review_results(&clean, "request", 2).is_none());
    for field in ["text", "notice", "found"] {
        let mut missing = clean.clone();
        missing["result"]["results"][0]
            .as_object_mut()
            .unwrap()
            .remove(field);
        assert!(validated_review_results(&missing, "request", 1).is_none());
    }
    let mut contradictory = clean;
    contradictory["error"] = json!({"code":"worker_failed"});
    assert!(validated_review_results(&contradictory, "request", 1).is_none());
}
