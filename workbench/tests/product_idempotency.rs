//! Retried deliveries: a turn request resent with the same Idempotency-Key,
//! and a document apply delivered twice. Real router and real Python document
//! worker; the only model is a local scripted server.
use axum::{
    body::Body,
    http::{Request, StatusCode},
    routing::post,
    Json, Router,
};
use civil_workbench::{
    config::{set_runtime_llm, LlmConfig, Paths},
    product::{
        api::{router, ProductState},
        tools::{sha256, ToolScope},
        worker::document_call_id,
    },
    runtime_core::{CancellationToken, WorkspaceContext},
};
use http_body_util::BodyExt;
use serde_json::{json, Value};
use std::{path::PathBuf, process::Command, sync::Arc, time::Duration};
use tower::ServiceExt;

async fn http(
    app: &Router,
    method: &str,
    url: &str,
    key: Option<&str>,
    body: Value,
) -> (StatusCode, Value) {
    let mut builder = Request::builder()
        .method(method)
        .uri(url)
        .header("Content-Type", "application/json");
    if let Some(key) = key {
        builder = builder.header("Idempotency-Key", key);
    }
    let response = app
        .clone()
        .oneshot(builder.body(Body::from(body.to_string())).unwrap())
        .await
        .unwrap();
    let status = response.status();
    let bytes = response.into_body().collect().await.unwrap().to_bytes();
    (status, serde_json::from_slice(&bytes).unwrap())
}

fn repo() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .unwrap()
        .to_path_buf()
}

#[tokio::test]
async fn idempotency_key_replays_the_finished_turn_instead_of_running_it_again() {
    let root = std::env::temp_dir().join(format!(
        "civil-product-idempotency-{}",
        uuid::Uuid::new_v4().simple()
    ));
    let workspace = root.join("job");
    std::fs::create_dir_all(&workspace).unwrap();
    std::fs::write(workspace.join("brief.txt"), "Source quantity: 12 panels\n").unwrap();
    let mut paths = Paths::from_demo(root.join("demo"));
    paths.repo_root = repo();
    let app = router(ProductState::open(paths).unwrap());
    let wid = http(&app, "POST", "/api/agent/workspaces", None, json!({"path":workspace}))
        .await
        .1["workspace"]["id"]
        .as_str()
        .unwrap()
        .to_owned();
    let body = json!({"workspace":wid,"session_id":"retry","message":"inspect","mode":"steps","files":["brief.txt"]});
    let (status, first) = http(&app, "POST", "/api/agent/turns", Some("client-retry-1"), body.clone()).await;
    assert_eq!(status, StatusCode::ACCEPTED, "{first}");
    let turn = first["turn_id"].as_str().unwrap().to_owned();
    let url = format!("/api/agent/turns/{turn}?workspace={wid}&session_id=retry");
    for _ in 0..200 {
        if http(&app, "GET", &url, None, Value::Null).await.1["turn"]["status"] == "completed" {
            break;
        }
        tokio::time::sleep(Duration::from_millis(10)).await;
    }
    // The client lost the first response and resends the same request: header
    // or body field, the key must return the stored turn, not start a second run.
    let (status, again) = http(&app, "POST", "/api/agent/turns", Some("client-retry-1"), body.clone()).await;
    let mut in_body = body.clone();
    in_body["idempotency_key"] = json!("client-retry-1");
    let (field_status, field_again) = http(&app, "POST", "/api/agent/turns", None, in_body).await;
    let listed = http(
        &app,
        "GET",
        &format!("/api/agent/turns?workspace={wid}&session_id=retry"),
        None,
        Value::Null,
    )
    .await
    .1;
    assert_eq!(
        listed["turns"].as_array().unwrap().len(),
        1,
        "a retried request with the same Idempotency-Key started another run: {listed}"
    );
    assert_eq!(status, StatusCode::OK, "{again}");
    assert_eq!(again["turn_id"], turn.as_str());
    assert_eq!(again["replayed"], true);
    assert_eq!(again["status"], "completed");
    assert!(again["result"]["findings"].is_array(), "{again}");
    assert_eq!((field_status, &field_again["turn_id"]), (StatusCode::OK, &again["turn_id"]));

    // Same key, different request: refused, nothing started.
    let mut changed = body.clone();
    changed["message"] = json!("inspect something else");
    let (status, refused) = http(&app, "POST", "/api/agent/turns", Some("client-retry-1"), changed).await;
    assert_eq!(status, StatusCode::UNPROCESSABLE_ENTITY, "{refused}");
    let mut other_session = body.clone();
    other_session["session_id"] = json!("elsewhere");
    assert_eq!(
        http(&app, "POST", "/api/agent/turns", Some("client-retry-1"), other_session).await.0,
        StatusCode::UNPROCESSABLE_ENTITY
    );
    // Header and body naming different keys is ambiguous; malformed keys are refused.
    let mut mismatched = body.clone();
    mismatched["idempotency_key"] = json!("client-retry-2");
    assert_eq!(
        http(&app, "POST", "/api/agent/turns", Some("client-retry-1"), mismatched).await.0,
        StatusCode::BAD_REQUEST
    );
    assert_eq!(
        http(&app, "POST", "/api/agent/turns", Some(&"k".repeat(129)), body.clone()).await.0,
        StatusCode::BAD_REQUEST
    );
    // Two Idempotency-Key headers (a client or proxy appending one) are just as
    // ambiguous: refused, and no turn starts under either key.
    let mut duplicated = body.clone();
    duplicated["session_id"] = json!("duplicated");
    let response = app
        .clone()
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/api/agent/turns")
                .header("Content-Type", "application/json")
                .header("Idempotency-Key", "dup-a")
                .header("Idempotency-Key", "dup-b")
                .body(Body::from(duplicated.to_string()))
                .unwrap(),
        )
        .await
        .unwrap();
    let dup_turns = http(
        &app,
        "GET",
        &format!("/api/agent/turns?workspace={wid}&session_id=duplicated"),
        None,
        Value::Null,
    )
    .await
    .1["turns"]
        .as_array()
        .unwrap()
        .len();
    assert_eq!(
        (response.status(), dup_turns),
        (StatusCode::BAD_REQUEST, 0),
        "two Idempotency-Key headers were not refused"
    );
    // Without a key, or with a new key, a request is a new turn as before.
    let (status, fresh) = http(&app, "POST", "/api/agent/turns", Some("client-retry-2"), body.clone()).await;
    assert_eq!(status, StatusCode::ACCEPTED, "{fresh}");
    assert_ne!(fresh["turn_id"], turn.as_str());
    let _ = std::fs::remove_dir_all(root);
}

/// A job folder with report.docx, built by the same fixture as the gate tests.
struct Job {
    root: PathBuf,
    state: Arc<ProductState>,
    workspace: WorkspaceContext,
    selected: Vec<String>,
    cancel: CancellationToken,
}
impl Job {
    fn new() -> Self {
        assert!(
            std::env::var_os("CIVIL_STATE_ROOT").is_none(),
            "Unset CIVIL_STATE_ROOT for these tests; they must use isolated state"
        );
        let repo = repo();
        let root = repo
            .parent()
            .unwrap()
            .join(format!("document-idempotency-{}", uuid::Uuid::new_v4().simple()));
        let job = root.join("job");
        let python = std::env::var_os("CIVIL_PYTHON").unwrap_or_else(|| "python".into());
        let made = Command::new(python)
            .args([
                "-B",
                "-c",
                "from pathlib import Path; import sys; from scripts.unified_acceptance import fixture; fixture(Path(sys.argv[1]))",
            ])
            .arg(&job)
            .current_dir(&repo)
            .env("PYTHONUTF8", "1")
            .output()
            .unwrap();
        assert!(
            made.status.success(),
            "fixture: {}",
            String::from_utf8_lossy(&made.stderr)
        );
        let mut paths = Paths::from_demo(repo.join("demo"));
        paths.data_dir = root.join("state");
        Self {
            state: ProductState::open(paths).unwrap(),
            workspace: WorkspaceContext::new(&job).unwrap(),
            selected: vec!["report.docx".into()],
            cancel: CancellationToken::new(),
            root,
        }
    }
    fn scope(&self) -> ToolScope<'_> {
        ToolScope {
            state: &self.state,
            workspace: &self.workspace,
            selected: &self.selected,
            user_request: "修改待核查草稿",
            write: true,
            cancel: &self.cancel,
        }
    }
    fn args(&self, text: &str) -> Value {
        let source = std::fs::read(self.workspace.root().join("report.docx")).unwrap();
        json!({"source":"report.docx","expected_sha256":sha256(&source),"patches":[
            {"op":"replace_paragraph","paragraph_id":"p:1","expected_text":"sample_count=4","text":text}]})
    }
    fn drafts(&self) -> usize {
        std::fs::read_dir(self.workspace.output_root())
            .map(|entries| {
                entries
                    .filter(|e| e.as_ref().unwrap().file_name().to_string_lossy().ends_with(".docx"))
                    .count()
            })
            .unwrap_or(0)
    }
}
impl Drop for Job {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.root);
    }
}

#[tokio::test]
async fn repeated_apply_delivery_with_the_host_call_id_writes_one_document() {
    let job = Job::new();
    let scope = job.scope();
    let args = job.args("Source record remains subject to review.");
    let preview = sha256(args.to_string().as_bytes());
    let call_id = document_call_id("turn-a", &preview);
    assert_eq!(call_id, document_call_id("turn-a", &preview));
    assert!(call_id.len() <= 80 && call_id.bytes().all(|b| b.is_ascii_alphanumeric() || b == b'-'));
    // Host -> real document worker, the same operation delivered twice.
    let first = scope
        .execute_as("apply_document", args.clone(), Some(&call_id))
        .await
        .unwrap();
    assert_eq!(first["ok"], true, "{first}");
    let again = scope
        .execute_as("apply_document", args.clone(), Some(&call_id))
        .await
        .unwrap();
    assert_eq!(again["ok"], true, "{again}");
    assert_eq!(again["result"]["replayed"], true, "{again}");
    assert_eq!(again["result"]["output_path"], first["result"]["output_path"]);
    assert_eq!(job.drafts(), 1, "a repeated delivery wrote a second draft");
    // The key cannot be reused for a different patch.
    let changed = job.args("Source record remains subject to a second review.");
    let conflict = scope
        .execute_as("apply_document", changed, Some(&call_id))
        .await
        .unwrap();
    assert_eq!(conflict["ok"], false, "{conflict}");
    assert_eq!(conflict["error"]["code"], "conflict");
    assert_eq!(job.drafts(), 1);
    // A later turn is a new operation and may write a new draft.
    let later = scope
        .execute_as("apply_document", args, Some(&document_call_id("turn-b", &preview)))
        .await
        .unwrap();
    assert_eq!(later["ok"], true, "{later}");
    assert_ne!(later["result"]["replayed"], true);
    assert_eq!(job.drafts(), 2);
}

#[tokio::test]
async fn model_applying_one_preview_twice_in_a_turn_publishes_one_draft() {
    struct ResetModel;
    impl Drop for ResetModel {
        fn drop(&mut self) {
            set_runtime_llm(None);
        }
    }
    let _reset = ResetModel;
    fn call(id: &str, name: &str, args: Value) -> Value {
        json!({"id":id,"type":"function","function":{"name":name,"arguments":args.to_string()}})
    }
    let job = Job::new();
    let args = job.args("Source record remains subject to review.");
    let server = Router::new().route("/chat/completions", post(move |Json(payload): Json<Value>| {
        let args = args.clone();
        async move {
            let messages = payload["messages"].as_array().unwrap();
            let preview = messages.iter().find(|m| m["role"] == "tool" && m["tool_call_id"] == "preview");
            let applied = messages.iter().any(|m| m["role"] == "tool" && m["tool_call_id"] == "apply");
            let message = if applied {
                json!({"role":"assistant","content":"待核查内容已记录。"})
            } else if let Some(preview) = preview {
                let result: Value = serde_json::from_str(preview["content"].as_str().unwrap()).unwrap();
                let apply = json!({"preview_id":result["result"]["preview_id"]});
                // A repeated delivery of the same approved operation in one turn.
                json!({"role":"assistant","content":null,"tool_calls":[
                    call("apply","apply_document",apply.clone()),call("apply-again","apply_document",apply)]})
            } else {
                json!({"role":"assistant","content":null,"tool_calls":[
                    call("inspect","read_file",json!({"source":"report.docx","operation":"inspect"})),
                    call("preview","preview_document",args)]})
            };
            let finish = if message["tool_calls"].is_array() { "tool_calls" } else { "stop" };
            Json(json!({"model":"repeat-scripted","choices":[{"message":message,"finish_reason":finish}],
                "usage":{"prompt_tokens":100,"completion_tokens":100}}))
        }
    }));
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let base_url = format!("http://{}", listener.local_addr().unwrap());
    let server = tokio::spawn(async move {
        axum::serve(listener, server).await.unwrap();
    });
    set_runtime_llm(Some(LlmConfig {
        api_key: "scripted-local-only".into(),
        base_url,
        model: "repeat-scripted".into(),
    }));
    let app = router(job.state.clone());
    let wid = job.state.register(job.workspace.root().to_str().unwrap()).unwrap()["id"]
        .as_str()
        .unwrap()
        .to_owned();
    let (status, started) = http(&app, "POST", "/api/agent/turns", None, json!({"workspace":wid,"session_id":"twice",
        "message":"修改待核查草稿","mode":"model","sandbox":"workspace-write","files":job.selected,"expert_id":"pm-daily"})).await;
    assert_eq!(status, StatusCode::ACCEPTED, "{started}");
    let url = format!(
        "/api/agent/turns/{}/events?workspace={wid}&session_id=twice",
        started["turn_id"].as_str().unwrap()
    );
    let mut result = Value::Null;
    for _ in 0..300 {
        result = http(&app, "GET", &url, None, Value::Null).await.1;
        if result["turn"]["status"] != "running" {
            break;
        }
        tokio::time::sleep(Duration::from_millis(100)).await;
    }
    set_runtime_llm(None);
    server.abort();
    assert_eq!(result["turn"]["status"], "completed", "{result}");
    let applies: Vec<&Value> = result["events"]
        .as_array()
        .unwrap()
        .iter()
        .filter(|e| e["kind"] == "tool_finished" && e["data"]["name"] == "apply_document")
        .collect();
    assert_eq!(applies.len(), 2, "{result}");
    let artifacts = result["turn"]["result"]["artifacts"].as_array().unwrap();
    let drafts = job.drafts();
    assert_eq!(
        (drafts, artifacts.len()),
        (1, 1),
        "the same preview applied twice wrote {drafts} drafts and {} artifacts",
        artifacts.len()
    );
    assert_eq!(applies[1]["data"]["result"]["ok"], true, "{}", applies[1]);
    assert_eq!(applies[1]["data"]["result"]["result"]["replayed"], true);
    assert_eq!(
        applies[1]["data"]["result"]["result"]["output_path"],
        applies[0]["data"]["result"]["result"]["output_path"]
    );
    assert_eq!(result["turn"]["result"]["tool_errors"], 0);
}
