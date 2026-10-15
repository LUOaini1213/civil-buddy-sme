//! Offline configuration boundaries: two owned loopback providers, fake keys,
//! real task admission/persistence, and a barrier during an in-flight request.
use axum::{
    body::Body,
    extract::State,
    http::{HeaderMap, Request, StatusCode},
    routing::post,
    Json, Router,
};
use civil_workbench::{
    api::{app, AppState},
    config::{self, LlmConfig, Paths},
    product::{
        api::{router, ProductState},
        auth::InstanceAuth,
    },
};
use http_body_util::BodyExt;
use serde_json::{json, Value};
use std::{
    path::PathBuf,
    sync::{Arc, Mutex},
    time::Duration,
};
use tokio::sync::Notify;
use tower::ServiceExt;

static CONFIG_TEST: tokio::sync::Mutex<()> = tokio::sync::Mutex::const_new(());
const OLD_KEY: &str = "offline-old-provider-sentinel-do-not-persist";
const NEW_KEY: &str = "offline-new-provider-sentinel-do-not-persist";

struct Fixture {
    root: PathBuf,
    app: Router,
    wid: String,
}
impl Fixture {
    fn new() -> Self {
        assert!(std::env::var_os("CIVIL_STATE_ROOT").is_none());
        let root = std::env::temp_dir().join(format!(
            "civil-config-snapshot-{}",
            uuid::Uuid::new_v4().simple()
        ));
        let job = root.join("job");
        std::fs::create_dir_all(&job).unwrap();
        let repo = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .parent()
            .unwrap()
            .to_owned();
        let mut paths = Paths::from_demo(repo.join("demo"));
        paths.data_dir = root.join("state");
        let state = ProductState::open_with_auth(paths.clone(), InstanceAuth::local()).unwrap();
        let wid = state.register(job.to_str().unwrap()).unwrap()["id"]
            .as_str()
            .unwrap()
            .to_owned();
        Self {
            root,
            app: app(AppState::live(paths)).merge(router(state)),
            wid,
        }
    }
    fn request(&self, session: &str, mode: &str) -> Value {
        json!({"workspace":self.wid,"session_id":session,"message":"Explain the available workflow without changing files.","locale":"en","mode":mode,"files":[]})
    }
    async fn finished(&self, turn: &str, session: &str) -> Value {
        tokio::time::timeout(Duration::from_secs(15), async {
            loop {
                let (status, body) = http(
                    &self.app,
                    "GET",
                    &format!(
                        "/api/agent/turns/{turn}?workspace={}&session_id={session}",
                        self.wid
                    ),
                    None,
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
        .expect("owned local task did not finish")
    }
    async fn events(&self, turn: &str, session: &str) -> Value {
        http(
            &self.app,
            "GET",
            &format!(
                "/api/agent/turns/{turn}/events?workspace={}&session_id={session}",
                self.wid
            ),
            None,
            Value::Null,
        )
        .await
        .1
    }
}
impl Drop for Fixture {
    fn drop(&mut self) {
        config::set_runtime_llm(None);
        let _ = std::fs::remove_dir_all(&self.root);
    }
}

async fn http(
    app: &Router,
    method: &str,
    url: &str,
    key: Option<&str>,
    value: Value,
) -> (StatusCode, Value) {
    let mut request = Request::builder()
        .method(method)
        .uri(url)
        .header("Content-Type", "application/json");
    if let Some(key) = key {
        request = request.header("Idempotency-Key", key);
    }
    let response = app
        .clone()
        .oneshot(request.body(Body::from(value.to_string())).unwrap())
        .await
        .unwrap();
    let status = response.status();
    let bytes = response.into_body().collect().await.unwrap().to_bytes();
    (status, serde_json::from_slice(&bytes).unwrap())
}

#[derive(Clone, Debug)]
struct Seen {
    correct_key: bool,
    other_key: bool,
    model: String,
    child: bool,
    path: String,
}
struct ProviderState {
    expected_key: &'static str,
    other_key: &'static str,
    delegate: bool,
    seen: Mutex<Vec<Seen>>,
    entered: Notify,
    release: Notify,
}
struct LocalProvider {
    base: String,
    state: Arc<ProviderState>,
    server: tokio::task::JoinHandle<()>,
}
impl LocalProvider {
    async fn new(expected_key: &'static str, other_key: &'static str, delegate: bool) -> Self {
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let base = format!("http://{}/v1", listener.local_addr().unwrap());
        let state = Arc::new(ProviderState {
            expected_key,
            other_key,
            delegate,
            seen: Mutex::new(vec![]),
            entered: Notify::new(),
            release: Notify::new(),
        });
        let routes = Router::new()
            .route("/{*path}", post(model))
            .with_state(state.clone());
        let server = tokio::spawn(async move {
            axum::serve(listener, routes).await.unwrap();
        });
        Self {
            base,
            state,
            server,
        }
    }
    fn config(&self, key: &str, model: &str) -> LlmConfig {
        LlmConfig {
            api_key: key.into(),
            base_url: self.base.clone(),
            model: model.into(),
        }
    }
    fn calls(&self) -> Vec<Seen> {
        self.state.seen.lock().unwrap().clone()
    }
}
impl Drop for LocalProvider {
    fn drop(&mut self) {
        self.server.abort();
    }
}

async fn model(
    State(state): State<Arc<ProviderState>>,
    uri: axum::http::Uri,
    headers: HeaderMap,
    Json(body): Json<Value>,
) -> Json<Value> {
    let authorization = headers
        .get("authorization")
        .and_then(|h| h.to_str().ok())
        .unwrap_or("");
    let child = body["messages"].as_array().unwrap().iter().any(|message| {
        message["role"] == "system"
            && message["content"]
                .as_str()
                .is_some_and(|text| text.starts_with("你是只读"))
    });
    let first = {
        let mut seen = state.seen.lock().unwrap();
        let first = seen.is_empty();
        seen.push(Seen {
            correct_key: authorization == format!("Bearer {}", state.expected_key),
            other_key: authorization == format!("Bearer {}", state.other_key),
            model: body["model"].as_str().unwrap().into(),
            child,
            path: uri.path().into(),
        });
        first
    };
    let message = if state.delegate && first {
        state.entered.notify_one();
        tokio::time::timeout(Duration::from_secs(15), state.release.notified())
            .await
            .expect("test did not release owned provider barrier");
        json!({"role":"assistant","content":null,"tool_calls":[{"id":"offline-delegate","type":"function","function":{"name":"delegate","arguments":json!({"tasks":[{"role":"evidence","goal":"Explain the read-only workflow."},{"role":"review","goal":"Describe the review boundary."}]}).to_string()}}]})
    } else {
        json!({"role":"assistant","content":"The workflow supports reviewing selected material. This is an explanation only."})
    };
    Json(
        json!({"model":body["model"],"choices":[{"message":message,"finish_reason":if state.delegate && first {"tool_calls"} else {"stop"}}],"usage":{"prompt_tokens":30,"completion_tokens":20,"total_tokens":50}}),
    )
}

fn assert_no_secrets(value: &Value) {
    let encoded = value.to_string();
    assert!(
        !encoded.contains(OLD_KEY) && !encoded.contains(NEW_KEY),
        "secret entered a response or stored turn"
    );
    assert!(
        !encoded.contains("api_key"),
        "secret-bearing config entered task records"
    );
}

#[tokio::test]
async fn blank_key_cross_origin_is_rejected_before_mutation_or_provider_contact() {
    let _guard = CONFIG_TEST.lock().await;
    let f = Fixture::new();
    let old = LocalProvider::new(OLD_KEY, NEW_KEY, false).await;
    let new = LocalProvider::new(NEW_KEY, OLD_KEY, false).await;
    let initial = old.config(OLD_KEY, "old-model");
    config::set_runtime_llm(Some(initial.clone()));
    let (status, refused) = http(
        &f.app,
        "POST",
        "/api/llm-config",
        None,
        json!({"base_url":new.base,"api_key":"  ","model":"new-model"}),
    )
    .await;
    assert_eq!(status, StatusCode::BAD_REQUEST);
    assert_eq!(
        refused,
        json!({"ok":false,"code":"provider_key_required","error":"provider_key_required"})
    );
    assert_eq!(config::runtime_llm(), Some(initial.clone()));
    assert_no_secrets(&refused);
    assert!(old.calls().is_empty() && new.calls().is_empty());

    // A retained key may change only the path/model within the same origin.
    let path = old.base.replace("/v1", "/same-origin/v2");
    assert_eq!(
        http(
            &f.app,
            "POST",
            "/api/llm-config",
            None,
            json!({"base_url":path,"api_key":"","model":"same-origin-model"})
        )
        .await
        .0,
        StatusCode::OK
    );
    let retained = config::runtime_llm().unwrap();
    assert_eq!(retained.api_key, OLD_KEY);
    assert_eq!(retained.model, "same-origin-model");
    let (status, accepted) = http(
        &f.app,
        "POST",
        "/api/agent/turns",
        None,
        f.request("retained", "model"),
    )
    .await;
    assert_eq!(status, StatusCode::ACCEPTED);
    assert_eq!(
        f.finished(accepted["turn_id"].as_str().unwrap(), "retained")
            .await["status"],
        "completed"
    );
    assert_eq!(old.calls().len(), 1);
    assert!(old.calls()[0].correct_key && !old.calls()[0].other_key);
    assert_eq!(old.calls()[0].path, "/same-origin/v2/chat/completions");

    // Supplying a new key explicitly authorizes a different provider origin.
    assert_eq!(
        http(
            &f.app,
            "POST",
            "/api/llm-config",
            None,
            json!({"base_url":new.base,"api_key":NEW_KEY,"model":"new-model"})
        )
        .await
        .0,
        StatusCode::OK
    );
    let accepted = http(
        &f.app,
        "POST",
        "/api/agent/turns",
        None,
        f.request("switched", "model"),
    )
    .await
    .1;
    let turn = f
        .finished(accepted["turn_id"].as_str().unwrap(), "switched")
        .await;
    assert_eq!(turn["status"], "completed", "{turn}");
    assert_no_secrets(&turn);
    assert_eq!(new.calls().len(), 1);
    assert!(new.calls()[0].correct_key && !new.calls()[0].other_key);
    assert_eq!(new.calls()[0].model, "new-model");
}

#[tokio::test]
async fn provider_url_validation_and_parsed_origin_keep_the_existing_secret_bound() {
    let _guard = CONFIG_TEST.lock().await;
    let f = Fixture::new();
    let initial = LlmConfig {
        api_key: OLD_KEY.into(),
        base_url: "https://EXAMPLE.invalid:443/v1".into(),
        model: "old".into(),
    };
    let sensitive_debug = format!(
        "{:?}",
        LlmConfig {
            api_key: OLD_KEY.into(),
            base_url: format!("https://user:{NEW_KEY}@example.invalid/v1"),
            model: "safe-model".into()
        }
    );
    assert!(!sensitive_debug.contains(OLD_KEY) && !sensitive_debug.contains(NEW_KEY));
    assert!(!sensitive_debug.contains("user:"));
    config::set_runtime_llm(Some(initial));
    assert!(config::update_runtime_llm("", "https://example.invalid/new/path", "next").is_ok());
    let retained = config::runtime_llm().unwrap();
    assert_eq!(retained.api_key, OLD_KEY);
    assert_eq!(retained.base_url, "https://example.invalid/new/path");
    for target in [
        "https://elsewhere.invalid/v1",
        "https://example.invalid:444/v1",
        "http://localhost/v1",
    ] {
        assert_eq!(
            config::update_runtime_llm("", target, ""),
            Err("provider_key_required")
        );
        assert_eq!(config::runtime_llm(), Some(retained.clone()));
    }
    for target in [
        "http://example.invalid/v1",
        "http://127.0.0.2/v1",
        "https://user:password@example.invalid/v1",
        "https://@example.invalid/v1",
        "https://example.invalid/v1?q=1",
        "https://example.invalid/v1#fragment",
        "ftp://example.invalid/v1",
        "file:///tmp/model",
        "https://example.invalid:0/v1",
        "https://exam ple.invalid/v1",
        "https://example.invalid/line\nbreak",
    ] {
        let (status, error) = http(
            &f.app,
            "POST",
            "/api/llm-config",
            None,
            json!({"base_url":target,"api_key":NEW_KEY}),
        )
        .await;
        assert_eq!(status, StatusCode::BAD_REQUEST, "unsafe URL accepted");
        assert_eq!(
            error,
            json!({"ok":false,"code":"invalid_provider_url","error":"invalid_provider_url"})
        );
        assert_eq!(config::runtime_llm(), Some(retained.clone()));
    }
    // Explicit credentials can change the origin; loopback HTTP is supported.
    assert!(config::update_runtime_llm(NEW_KEY, "http://[::1]:18000/v1", "").is_ok());
    assert_eq!(config::runtime_llm().unwrap().api_key, NEW_KEY);
}

#[tokio::test]
async fn accepted_turn_and_all_children_keep_snapshot_while_future_turn_uses_new_config() {
    let _guard = CONFIG_TEST.lock().await;
    let mut f = Fixture::new();
    let old = LocalProvider::new(OLD_KEY, NEW_KEY, true).await;
    let new = LocalProvider::new(NEW_KEY, OLD_KEY, false).await;
    config::set_runtime_llm(Some(old.config(OLD_KEY, "old-model")));
    let request = f.request("inflight", "model");
    let (status, accepted) = http(
        &f.app,
        "POST",
        "/api/agent/turns",
        Some("same-accepted-turn"),
        request.clone(),
    )
    .await;
    assert_eq!(status, StatusCode::ACCEPTED, "{accepted}");
    let id = accepted["turn_id"].as_str().unwrap();
    tokio::time::timeout(Duration::from_secs(10), old.state.entered.notified())
        .await
        .expect("old provider did not reach barrier");
    assert_eq!(
        http(
            &f.app,
            "POST",
            "/api/llm-config",
            None,
            json!({"base_url":new.base,"api_key":NEW_KEY,"model":"new-model"})
        )
        .await
        .0,
        StatusCode::OK
    );
    old.state.release.notify_one();
    let turn = f.finished(id, "inflight").await;
    assert_eq!(turn["status"], "completed", "{turn}");
    assert_eq!(turn["result"]["partial"], false, "{turn}");
    let calls = old.calls();
    assert_eq!(
        calls.len(),
        4,
        "expected root + both children + root continuation: {calls:?}"
    );
    assert_eq!(calls.iter().filter(|call| call.child).count(), 2);
    assert!(calls
        .iter()
        .all(|call| call.correct_key && !call.other_key && call.model == "old-model"));
    assert!(
        new.calls().is_empty(),
        "an in-flight root or child used future configuration"
    );
    assert_no_secrets(&turn);
    let events = f.events(id, "inflight").await;
    assert_no_secrets(&events);
    assert_eq!(
        events["events"]
            .as_array()
            .unwrap()
            .iter()
            .filter(|event| event["kind"] == "subtask_finished")
            .count(),
        2
    );

    // The private snapshot is not fingerprinted; retry returns the old turn.
    let (status, retry) = http(
        &f.app,
        "POST",
        "/api/agent/turns",
        Some("same-accepted-turn"),
        request,
    )
    .await;
    assert_eq!(status, StatusCode::OK, "{retry}");
    assert_eq!(retry["turn_id"], id);
    assert_eq!(retry["replayed"], true);
    assert_eq!(old.calls().len(), 4);
    assert!(new.calls().is_empty());
    assert_no_secrets(&retry);

    let accepted = http(
        &f.app,
        "POST",
        "/api/agent/turns",
        None,
        f.request("future", "model"),
    )
    .await
    .1;
    let future = f
        .finished(accepted["turn_id"].as_str().unwrap(), "future")
        .await;
    assert_eq!(future["status"], "completed", "{future}");
    assert_eq!(new.calls().len(), 1);
    assert!(
        new.calls()[0].correct_key
            && !new.calls()[0].other_key
            && new.calls()[0].model == "new-model"
    );
    assert_no_secrets(&future);

    // Restoring settings to an empty-key environment must still recover an
    // accepted delivery; neither a conflict nor a miss creates a new turn.
    assert_eq!(
        http(
            &f.app,
            "POST",
            "/api/llm-config",
            None,
            json!({"clear":true})
        )
        .await
        .0,
        StatusCode::OK
    );
    assert!(
        config::llm_config().api_key.is_empty(),
        "offline test requires provider keys to be absent from its child environment"
    );
    let before = http(
        &f.app,
        "GET",
        &format!("/api/agent/turns?workspace={}", f.wid),
        None,
        Value::Null,
    )
    .await
    .1;
    let events_before = f.events(id, "inflight").await;
    let request = f.request("inflight", "model");
    let (status, replay) = http(
        &f.app,
        "POST",
        "/api/agent/turns",
        Some("same-accepted-turn"),
        request.clone(),
    )
    .await;
    assert_eq!(status, StatusCode::OK, "{replay}");
    assert_eq!(replay["turn_id"], id);
    assert_eq!(replay["replayed"], true);
    assert_eq!(replay["result"], turn["result"]);
    let mut conflict = request.clone();
    conflict["message"] = json!("different request");
    assert_eq!(
        http(
            &f.app,
            "POST",
            "/api/agent/turns",
            Some("same-accepted-turn"),
            conflict
        )
        .await
        .0,
        StatusCode::UNPROCESSABLE_ENTITY
    );
    let mut wrong_session = request.clone();
    wrong_session["session_id"] = json!("wrong-session");
    assert_eq!(
        http(
            &f.app,
            "POST",
            "/api/agent/turns",
            Some("same-accepted-turn"),
            wrong_session
        )
        .await
        .0,
        StatusCode::UNPROCESSABLE_ENTITY
    );
    assert_eq!(
        http(
            &f.app,
            "POST",
            "/api/agent/turns",
            Some("new-key-with-no-model-key"),
            request.clone()
        )
        .await
        .0,
        StatusCode::BAD_REQUEST
    );
    assert_eq!(
        http(&f.app, "POST", "/api/agent/turns", None, request)
            .await
            .0,
        StatusCode::BAD_REQUEST
    );
    assert_eq!(
        http(
            &f.app,
            "GET",
            &format!("/api/agent/turns?workspace={}", f.wid),
            None,
            Value::Null
        )
        .await
        .1,
        before
    );
    assert_eq!(f.events(id, "inflight").await, events_before);
    assert_eq!((old.calls().len(), new.calls().len()), (4, 1));
    // Release the owned host's SQLite/owner locks before reading its raw disk
    // bytes on Windows. All tasks are already terminal; no service is stopped.
    drop(std::mem::replace(&mut f.app, Router::new()));
    // Inspect every remaining persisted file for the fake sentinels. A clean
    // last-connection close may checkpoint/remove the WAL as normal.
    for entry in std::fs::read_dir(f.root.join("state/unified")).unwrap() {
        let path = entry.unwrap().path();
        if path.is_file() {
            let bytes = std::fs::read(path).unwrap();
            for secret in [OLD_KEY, NEW_KEY] {
                assert!(
                    !bytes
                        .windows(secret.len())
                        .any(|window| window == secret.as_bytes()),
                    "fake secret was persisted"
                );
            }
        }
    }
}

#[tokio::test]
async fn snapshot_is_taken_at_admission_before_the_spawned_future_runs() {
    let _guard = CONFIG_TEST.lock().await;
    let f = Fixture::new();
    let old = LocalProvider::new(OLD_KEY, NEW_KEY, false).await;
    let new = LocalProvider::new(NEW_KEY, OLD_KEY, false).await;
    config::set_runtime_llm(Some(old.config(OLD_KEY, "admitted-model")));
    let (status, accepted) = http(
        &f.app,
        "POST",
        "/api/agent/turns",
        None,
        f.request("admission", "model"),
    )
    .await;
    assert_eq!(status, StatusCode::ACCEPTED, "{accepted}");
    // No await between the accepted response and this update on the test's
    // single-thread runtime: the spawned task has not been polled yet.
    config::set_runtime_llm(Some(new.config(NEW_KEY, "future-model")));
    let turn = f
        .finished(accepted["turn_id"].as_str().unwrap(), "admission")
        .await;
    assert_eq!(turn["status"], "completed", "{turn}");
    assert_eq!(old.calls().len(), 1);
    assert_eq!(old.calls()[0].model, "admitted-model");
    assert!(old.calls()[0].correct_key && !old.calls()[0].other_key);
    assert!(new.calls().is_empty());
}

#[tokio::test]
async fn steps_mode_does_not_require_or_use_a_model_snapshot() {
    let _guard = CONFIG_TEST.lock().await;
    let f = Fixture::new();
    let stub = LocalProvider::new(OLD_KEY, NEW_KEY, false).await;
    config::set_runtime_llm(Some(stub.config("", "unused")));
    let (status, accepted) = http(
        &f.app,
        "POST",
        "/api/agent/turns",
        None,
        f.request("steps", "steps"),
    )
    .await;
    assert_eq!(status, StatusCode::ACCEPTED, "{accepted}");
    let turn = f
        .finished(accepted["turn_id"].as_str().unwrap(), "steps")
        .await;
    assert_eq!(turn["status"], "completed", "{turn}");
    assert!(stub.calls().is_empty());
    assert_no_secrets(&turn);
}
