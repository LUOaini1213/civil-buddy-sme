//! Read-only source-version diagnostics against isolated synthetic local files.
//! No model, provider, production instance or professional validation is involved.
use axum::{
    body::Body,
    http::{Request, StatusCode},
    Router,
};
use civil_workbench::{
    config::Paths,
    product::{
        api::{router, ProductState},
        tools::sha256,
    },
    runtime_core::{SessionId, TurnStatus, WorkspaceContext},
};
use http_body_util::BodyExt;
use serde_json::{json, Value};
use std::{path::PathBuf, sync::Arc, time::Duration};
use tower::ServiceExt;

struct Fixture {
    root: PathBuf,
    state: Arc<ProductState>,
    ws: WorkspaceContext,
    id: String,
}
impl Fixture {
    fn new() -> Self {
        assert!(std::env::var_os("CIVIL_STATE_ROOT").is_none());
        let root =
            std::env::temp_dir().join(format!("civil-source-versions-{}", uuid::Uuid::new_v4()));
        let job = root.join("job");
        std::fs::create_dir_all(&job).unwrap();
        let mut paths = Paths::from_demo(
            PathBuf::from(env!("CARGO_MANIFEST_DIR"))
                .parent()
                .unwrap()
                .join("demo"),
        );
        paths.data_dir = root.join("state");
        let state = ProductState::open(paths).unwrap();
        let id = state.register(job.to_str().unwrap()).unwrap()["id"]
            .as_str()
            .unwrap()
            .to_owned();
        Self {
            root,
            state,
            ws: WorkspaceContext::new(&job).unwrap(),
            id,
        }
    }
    fn seed(&self, files: Value, events: Vec<(&str, Value)>, result: Value) -> String {
        let session = SessionId::parse("source-session").unwrap();
        let lease = self
            .state
            .runtime
            .begin_turn(
                &self.ws,
                &session,
                json!({"workspace":self.id,"files":files,"message":"Synthetic diagnostic"}),
            )
            .unwrap();
        let id = lease.turn_id().to_string();
        for (name, data) in events {
            lease.emit(name, data).unwrap();
        }
        lease.finish(TurnStatus::Completed, result).unwrap();
        id
    }
    fn url(&self, turn: &str) -> String {
        format!(
            "/api/agent/turns/{turn}/source-status?workspace={}&session_id=source-session",
            self.id
        )
    }
}
impl Drop for Fixture {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.root);
    }
}

async fn http(app: &Router, method: &str, url: &str, body: Value) -> (StatusCode, Value) {
    let response = app
        .clone()
        .oneshot(
            Request::builder()
                .method(method)
                .uri(url)
                .header("content-type", "application/json")
                .body(Body::from(body.to_string()))
                .unwrap(),
        )
        .await
        .unwrap();
    let status = response.status();
    let bytes = response.into_body().collect().await.unwrap().to_bytes();
    (
        status,
        serde_json::from_slice(&bytes)
            .unwrap_or_else(|_| json!({"text":String::from_utf8_lossy(&bytes)})),
    )
}
fn receipt(source: &str, hash: &str) -> Value {
    json!({"name":"read_file","result":{"ok":true,"result":{"source":source,"source_sha256":hash}}})
}

#[tokio::test]
async fn actual_steps_reads_detect_same_size_changes_missing_sources_and_restoration_without_reexecution(
) {
    let f = Fixture::new();
    let app = router(f.state.clone());
    let original = b"count=4\n";
    std::fs::write(f.ws.root().join("brief.txt"), original).unwrap();
    let (status, started) = http(&app, "POST", "/api/agent/turns", json!({"workspace":f.id,
        "session_id":"source-session","message":"Inspect brief.txt","files":["brief.txt"],"mode":"steps"})).await;
    assert_eq!(status, StatusCode::ACCEPTED);
    let turn = started["turn_id"].as_str().unwrap();
    let record_url = format!(
        "/api/agent/turns/{turn}?workspace={}&session_id=source-session",
        f.id
    );
    let mut record = Value::Null;
    for _ in 0..100 {
        record = http(&app, "GET", &record_url, Value::Null).await.1;
        if record["turn"]["status"] != "running" {
            break;
        }
        tokio::time::sleep(Duration::from_millis(10)).await;
    }
    assert_eq!(record["turn"]["status"], "completed", "{record}");
    let (status, report) = http(&app, "GET", &f.url(turn), Value::Null).await;
    assert_eq!(status, StatusCode::OK, "{report}");
    assert_eq!(report["sources"][0]["status"], "unchanged");
    assert_eq!(
        report["sources"][0]["recorded_sha256"],
        json!([sha256(original)])
    );
    assert_eq!(report["all_recorded_sources_unchanged"], true);
    std::fs::write(f.ws.root().join("brief.txt"), b"count=9\n").unwrap();
    let changed = http(&app, "GET", &f.url(turn), Value::Null).await.1;
    assert_eq!(changed["sources"][0]["status"], "changed");
    assert_eq!(changed["needs_review"], true);
    assert_eq!(changed["automatic_recompute"], false);
    assert_eq!(changed["semantic_validation"], false);
    assert_eq!(
        http(&app, "GET", &record_url, Value::Null).await.1,
        record,
        "Check mutated the original task"
    );
    std::fs::remove_file(f.ws.root().join("brief.txt")).unwrap();
    assert_eq!(
        http(&app, "GET", &f.url(turn), Value::Null).await.1["sources"][0]["status"],
        "missing"
    );
    std::fs::write(f.ws.root().join("brief.txt"), original).unwrap();
    assert_eq!(
        http(&app, "GET", &f.url(turn), Value::Null).await.1["sources"][0]["status"],
        "unchanged"
    );
    assert_eq!(
        std::fs::read(f.ws.root().join("brief.txt")).unwrap(),
        original
    );
}

#[tokio::test]
async fn source_checks_keep_workspace_session_and_running_turn_boundaries() {
    let f = Fixture::new();
    let app = router(f.state.clone());
    let lease = f
        .state
        .runtime
        .begin_turn(
            &f.ws,
            &SessionId::parse("source-session").unwrap(),
            json!({"workspace":f.id,"files":[]}),
        )
        .unwrap();
    let id = lease.turn_id().to_string();
    assert_eq!(
        http(&app, "GET", &f.url(&id), Value::Null).await.0,
        StatusCode::CONFLICT
    );
    lease.finish(TurnStatus::Completed, json!({})).unwrap();
    assert_eq!(
        http(
            &app,
            "GET",
            &f.url(&id).replace("source-session", "another-session"),
            Value::Null
        )
        .await
        .0,
        StatusCode::BAD_REQUEST
    );
    let other = f.root.join("another-job");
    std::fs::create_dir(&other).unwrap();
    let other_id = f.state.register(other.to_str().unwrap()).unwrap()["id"]
        .as_str()
        .unwrap()
        .to_owned();
    assert_eq!(
        http(
            &app,
            "GET",
            &f.url(&id).replace(&f.id, &other_id),
            Value::Null
        )
        .await
        .0,
        StatusCode::BAD_REQUEST
    );
    let no_sources = http(&app, "GET", &f.url(&id), Value::Null).await.1;
    assert_eq!(no_sources["all_recorded_sources_unchanged"], false);
    assert_eq!(no_sources["sources"], json!([]));
}

#[tokio::test]
async fn unread_files_and_model_or_failed_tool_claims_never_become_version_receipts() {
    let f = Fixture::new();
    let hash = sha256(b"own bytes");
    std::fs::write(f.ws.root().join("brief.txt"), b"own bytes").unwrap();
    std::fs::write(f.ws.root().join("unselected.txt"), b"own bytes").unwrap();
    let mut failed = receipt("brief.txt", &hash);
    failed["result"]["ok"] = json!(false);
    let mut wrong_tool = receipt("brief.txt", &hash);
    wrong_tool["name"] = json!("delegate");
    let id = f.seed(
        json!(["brief.txt"]),
        vec![
            ("model", receipt("brief.txt", &hash)),
            ("tool_finished", failed),
            ("tool_finished", wrong_tool),
            ("tool_finished", receipt("unselected.txt", &hash)),
        ],
        json!({"reply":format!("I read brief.txt with SHA-256 {hash}"),"source_sha256":hash}),
    );
    let report = http(&router(f.state.clone()), "GET", &f.url(&id), Value::Null)
        .await
        .1;
    assert_eq!(report["sources"].as_array().unwrap().len(), 1);
    assert_eq!(report["sources"][0]["status"], "not_read");
    assert_eq!(report["sources"][0]["current_sha256"], Value::Null);
    assert_eq!(report["needs_review"], true);
}

#[tokio::test]
async fn retrieval_and_absolute_receipts_preserve_every_observed_version() {
    let f = Fixture::new();
    let current = sha256(b"new version");
    let old = sha256(b"old version");
    let path = f.ws.root().join("brief.txt");
    std::fs::write(&path, b"new version").unwrap();
    let id = f.seed(
        json!(["brief.txt"]),
        vec![
            ("tool_finished", receipt(path.to_str().unwrap(), &old)),
            (
                "tool_finished",
                json!({"name":"search_sources","result":{"ok":true,"result":{
            "sources":[{"source":"brief.txt","source_sha256":current}],
            "references":[{"source":"brief.txt","source_sha256":"a".repeat(64)}]}}}),
            ),
        ],
        json!({}),
    );
    let report = http(&router(f.state.clone()), "GET", &f.url(&id), Value::Null)
        .await
        .1;
    assert_eq!(report["sources"][0]["status"], "mixed_versions");
    assert_eq!(
        report["sources"][0]["recorded_sha256"]
            .as_array()
            .unwrap()
            .len(),
        2
    );
    assert_eq!(report["sources"][0]["current_sha256"], current);
    assert_eq!(report["needs_review"], true);
}

#[tokio::test]
async fn historical_unsafe_paths_and_oversized_files_are_unknown_not_read_or_current() {
    let f = Fixture::new();
    std::fs::write(f.ws.root().join(".env"), b"synthetic secret").unwrap();
    std::fs::write(
        f.root.join("outside.txt"),
        b"not a selected workspace source",
    )
    .unwrap();
    let large = std::fs::File::create(f.ws.root().join("large.txt")).unwrap();
    large.set_len(25_000_001).unwrap();
    drop(large);
    let id = f.seed(
        json!([".env", "../outside.txt", "large.txt"]),
        vec![
            ("tool_finished", receipt(".env", &"a".repeat(64))),
            ("tool_finished", receipt("../outside.txt", &"a".repeat(64))),
            ("tool_finished", receipt("large.txt", &"a".repeat(64))),
        ],
        json!({}),
    );
    let report = http(&router(f.state.clone()), "GET", &f.url(&id), Value::Null)
        .await
        .1;
    for source in report["sources"].as_array().unwrap() {
        assert_eq!(source["status"], "unknown");
        assert_eq!(source["current_sha256"], Value::Null);
    }
    assert!(report["sources"]
        .as_array()
        .unwrap()
        .iter()
        .any(|source| source["reason"] == "source_too_large"));
    assert_eq!(
        std::fs::read(f.ws.root().join(".env")).unwrap(),
        b"synthetic secret"
    );
}
