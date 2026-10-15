//! Persisted history discovery without a browser cache, workers or model calls.
use axum::{body::Body, http::{Request, StatusCode}, Router};
use civil_workbench::{
    config::Paths,
    product::{api::{self, ProductState}, auth::{self, InstanceAuth}, tools::sha256},
    runtime_core::{SessionId, TurnId, TurnRecord, TurnStatus, WorkspaceContext},
};
use http_body_util::BodyExt;
use rusqlite::{params, Connection};
use serde_json::{json, Value};
use std::{collections::HashSet, path::PathBuf, sync::Arc};
use tower::ServiceExt;

const ALICE: &str = "synthetic-alice-session-token-at-least-32-bytes";
const BOB: &str = "synthetic-bob-session-token-at-least-32-bytes";

struct Temp(PathBuf);
impl Temp {
    fn new() -> Self {
        assert!(std::env::var_os("CIVIL_STATE_ROOT").is_none(), "Tests require isolated state");
        let root = std::env::temp_dir().join(format!("civil-session-pages-{}", uuid::Uuid::new_v4()));
        std::fs::create_dir_all(&root).unwrap();
        Self(root)
    }
    fn dir(&self, name: &str) -> PathBuf {
        let path = self.0.join(name);
        std::fs::create_dir_all(&path).unwrap();
        path
    }
    fn paths(&self, name: &str) -> Paths {
        let mut paths = Paths::from_demo(self.dir(&format!("{name}-demo")));
        paths.data_dir = self.dir(&format!("{name}-state"));
        paths
    }
}
impl Drop for Temp {
    fn drop(&mut self) { let _ = std::fs::remove_dir_all(&self.0); }
}
fn named(user: &str, token: &str, root: PathBuf) -> Arc<InstanceAuth> {
    InstanceAuth::named(user, &sha256(token.as_bytes()), vec![root], None).unwrap()
}
fn app(state: &Arc<ProductState>) -> Router {
    auth::protect(api::router(state.clone()), state.auth.clone())
}
async fn request(app: &Router, method: &str, uri: &str, token: Option<&str>, body: Value) -> (StatusCode, Value) {
    let mut builder = Request::builder().method(method).uri(uri)
        .header("host", "127.0.0.1:8765").header("content-type", "application/json");
    if let Some(token) = token { builder = builder.header("authorization", format!("Bearer {token}")); }
    let response = app.clone().oneshot(builder.body(Body::from(body.to_string())).unwrap()).await.unwrap();
    let status = response.status();
    let data = response.into_body().collect().await.unwrap().to_bytes();
    (status, serde_json::from_slice(&data).unwrap_or_else(|_| json!({"detail":String::from_utf8_lossy(&data)})))
}
async fn get(app: &Router, uri: &str, token: Option<&str>) -> (StatusCode, Value) {
    request(app, "GET", uri, token, Value::Null).await
}
fn seed(state: &ProductState, workspace: &WorkspaceContext, session: &str, actor: Option<&str>, status: TurnStatus) -> TurnRecord {
    let mut payload = json!({"message":"private source text must not enter a session summary", "files":["private-source.docx"]});
    if let Some(actor) = actor { payload["actor_id"] = json!(actor); }
    state.runtime.begin_turn(workspace, &SessionId::parse(session).unwrap(), payload).unwrap()
        .finish(status, json!({"reply":"private result must not enter a session summary","risk_confirmation_present":true})).unwrap()
}
fn cursor_json(encoded: &str) -> Value {
    let bytes: Vec<u8> = (0..encoded.len()).step_by(2).map(|i| u8::from_str_radix(&encoded[i..i+2],16).unwrap()).collect();
    serde_json::from_slice(&bytes).unwrap()
}
fn cursor_encode(value: &Value) -> String {
    value.to_string().as_bytes().iter().map(|b|format!("{b:02x}")).collect()
}

#[tokio::test]
async fn api_created_sessions_survive_restart_without_replay_or_permission_metadata() {
    let temp = Temp::new();
    let paths = temp.paths("persist");
    let root = temp.dir("job");
    std::fs::write(root.join("source.txt"), "original unchanged").unwrap();
    let state = ProductState::open_with_auth(paths.clone(), InstanceAuth::local()).unwrap();
    let workspace_id = state.register(root.to_str().unwrap()).unwrap()["id"].as_str().unwrap().to_owned();
    let workspace = state.workspace(&workspace_id).unwrap();
    let router = app(&state);
    let (status, start) = request(&router, "POST", "/api/agent/turns", None,
        json!({"workspace":workspace_id,"session_id":"api-created","message":"Inspect selected material", "mode":"steps","sandbox":"read-only","files":[]})).await;
    assert_eq!(status, StatusCode::ACCEPTED, "{start}");
    let session = SessionId::parse("api-created").unwrap();
    let turn_id = TurnId::parse(start["turn_id"].as_str().unwrap()).unwrap();
    tokio::time::timeout(std::time::Duration::from_secs(5), async {
        while !state.runtime.turn(&workspace, &session, &turn_id).unwrap().status.is_terminal() {
            tokio::task::yield_now().await;
        }
    }).await.unwrap();
    let latest = seed(&state, &workspace, "api-created", Some("local-owner"), TurnStatus::Failed);
    seed(&state, &workspace, "another", Some("local-owner"), TurnStatus::Cancelled);
    let before_events = state.runtime.replay(&workspace, &session, &turn_id, 0, 100).unwrap().len();
    let url = format!("/api/agent/sessions?workspace={workspace_id}");
    let (status, before) = get(&router, &url, None).await;
    assert_eq!(status, StatusCode::OK, "{before}");
    assert_eq!(before["workspace"], workspace_id);
    let summary = before["sessions"].as_array().unwrap().iter().find(|s|s["session_id"]=="api-created").unwrap();
    assert_eq!(summary["turn_count"], 2);
    assert_eq!(summary["latest_turn_id"], latest.turn_id.as_str());
    assert_eq!(summary["status"], "failed");
    assert_eq!(summary.as_object().unwrap().len(), 5);
    for forbidden in ["private source", "private result", "private-source.docx", "risk_confirmation", "sandbox", root.to_str().unwrap()] {
        assert!(!before.to_string().contains(forbidden), "Metadata leak: {forbidden}");
    }
    assert_eq!(state.runtime.replay(&workspace, &session, &turn_id, 0, 100).unwrap().len(), before_events);
    drop(router);
    drop(state);
    let reopened = ProductState::open_with_auth(paths, InstanceAuth::local()).unwrap();
    let router = app(&reopened);
    assert_eq!(get(&router, &url, None).await, (StatusCode::OK, before));
    assert_eq!(reopened.runtime.replay(&workspace, &session, &turn_id, 0, 100).unwrap().len(), before_events);
    let (_, turns) = get(&router, &format!("/api/agent/turns?workspace={workspace_id}&session_id=api-created"), None).await;
    assert_eq!(turns["turns"].as_array().unwrap().len(), 2);
    assert_eq!(turns["turns"][0]["turn_id"], latest.turn_id.as_str());
    assert!(turns["next_cursor"].is_null());
    assert_eq!(get(&router, &format!("/api/agent/turns/{turn_id}?workspace={workspace_id}&session_id=another"), None).await.0, StatusCode::BAD_REQUEST);
    assert_eq!(std::fs::read_to_string(root.join("source.txt")).unwrap(), "original unchanged");
}

#[tokio::test]
async fn session_pages_filter_actor_and_workspace_before_counts_with_stable_ties() {
    let temp = Temp::new();
    let paths = temp.paths("pages");
    let state = ProductState::open_with_auth(paths.clone(), InstanceAuth::local()).unwrap();
    let wid = state.register(temp.dir("one").to_str().unwrap()).unwrap()["id"].as_str().unwrap().to_owned();
    let other_id = state.register(temp.dir("two").to_str().unwrap()).unwrap()["id"].as_str().unwrap().to_owned();
    let workspace = state.workspace(&wid).unwrap();
    for i in 0..105 { seed(&state, &workspace, &format!("session-{i:03}"), Some("local-owner"), TurnStatus::Completed); }
    seed(&state, &workspace, "session-104", Some("someone-else"), TurnStatus::Failed);
    seed(&state, &workspace, "foreign-actor", Some("someone-else"), TurnStatus::Completed);
    seed(&state, &workspace, "legacy-without-owner", None, TurnStatus::Completed);
    seed(&state, &state.workspace(&other_id).unwrap(), "foreign-workspace", Some("local-owner"), TurnStatus::Completed);
    let db = Connection::open(paths.data_dir.join("unified/runtime.sqlite")).unwrap();
    db.execute("UPDATE runtime_turns SET updated_at='2026-09-30T00:00:00.000Z'", []).unwrap();
    drop(db);
    let router = app(&state);
    let base = format!("/api/agent/sessions?workspace={wid}");
    let (status, first) = get(&router, &base, None).await;
    assert_eq!(status, StatusCode::OK, "{first}");
    assert_eq!(first["sessions"].as_array().unwrap().len(), 50);
    assert_eq!(first["sessions"][0]["session_id"], "session-104");
    assert_eq!(first["sessions"][0]["turn_count"], 1);
    assert_eq!(first["sessions"][0]["status"], "completed", "Another actor cannot change the visible summary");
    let cursor = first["next_cursor"].as_str().unwrap();
    assert_eq!(get(&router, &format!("/api/agent/sessions?workspace={other_id}&cursor={cursor}"), None).await.0, StatusCode::BAD_REQUEST);
    let mut ids: Vec<String> = first["sessions"].as_array().unwrap().iter().map(|s|s["session_id"].as_str().unwrap().to_owned()).collect();
    let mut next = Some(cursor.to_owned());
    let mut pages = 1;
    while let Some(cursor) = next {
        let (status, page) = get(&router, &format!("{base}&cursor={cursor}"), None).await;
        assert_eq!(status, StatusCode::OK, "{page}");
        ids.extend(page["sessions"].as_array().unwrap().iter().map(|s|s["session_id"].as_str().unwrap().to_owned()));
        next = page["next_cursor"].as_str().map(str::to_owned);
        pages += 1;
        assert!(pages <= 3, "Cursor did not advance");
    }
    assert_eq!(ids, (0..105).rev().map(|i|format!("session-{i:03}")).collect::<Vec<_>>());
    for extra in ["&limit=0", "&limit=101", "&cursor=not-hex", "&actor_id=someone-else"] {
        assert_eq!(get(&router, &format!("{base}{extra}"), None).await.0, StatusCode::BAD_REQUEST);
    }
    let (_, other) = get(&router, &format!("/api/agent/sessions?workspace={other_id}"), None).await;
    assert_eq!(other["sessions"].as_array().unwrap().len(), 1);
    assert_eq!(other["sessions"][0]["session_id"], "foreign-workspace");
}

#[tokio::test]
async fn named_history_requires_own_login_and_rejects_other_workspaces_and_cursors() {
    let temp = Temp::new();
    let alice_root = temp.dir("alice-job");
    let bob_root = temp.dir("bob-job");
    let alice = ProductState::open_with_auth(temp.paths("alice"), named("alice", ALICE, alice_root.clone())).unwrap();
    let bob = ProductState::open_with_auth(temp.paths("bob"), named("bob", BOB, bob_root.clone())).unwrap();
    let aid = alice.register(alice_root.to_str().unwrap()).unwrap()["id"].as_str().unwrap().to_owned();
    let bid = bob.register(bob_root.to_str().unwrap()).unwrap()["id"].as_str().unwrap().to_owned();
    for sid in ["alice-private-a", "alice-private-b"] { seed(&alice, &alice.workspace(&aid).unwrap(), sid, Some("alice"), TurnStatus::Completed); }
    seed(&bob, &bob.workspace(&bid).unwrap(), "bob-private", Some("bob"), TurnStatus::Completed);
    let a = app(&alice);
    let b = app(&bob);
    for route in [format!("/api/agent/sessions?workspace={aid}"), format!("/api/agent/turns?workspace={aid}&session_id=alice-private-a")] {
        for token in [None, Some(BOB)] { assert_eq!(get(&a, &route, token).await.0, StatusCode::UNAUTHORIZED); }
    }
    let (_, own) = get(&a, &format!("/api/agent/sessions?workspace={aid}&limit=1"), Some(ALICE)).await;
    let cursor = own["next_cursor"].as_str().unwrap();
    let (status, denied) = get(&b, &format!("/api/agent/sessions?workspace={bid}&cursor={cursor}"), Some(BOB)).await;
    assert_eq!(status, StatusCode::BAD_REQUEST);
    assert!(!denied.to_string().contains("alice-private"));
    let (status, denied) = get(&a, &format!("/api/agent/sessions?workspace={bid}"), Some(ALICE)).await;
    assert_eq!(status, StatusCode::BAD_REQUEST);
    assert!(!denied.to_string().contains("bob-private"));
    assert!(alice.register(bob_root.to_str().unwrap()).is_err());
}

#[tokio::test]
async fn task_pages_recover_more_than_one_hundred_with_no_replay_and_scoped_boundaries() {
    let temp = Temp::new();
    let paths = temp.paths("tasks");
    let state = ProductState::open_with_auth(paths.clone(), InstanceAuth::local()).unwrap();
    let wid = state.register(temp.dir("job").to_str().unwrap()).unwrap()["id"].as_str().unwrap().to_owned();
    let workspace = state.workspace(&wid).unwrap();
    let mut ids = Vec::new();
    for _ in 0..105 { ids.push(seed(&state, &workspace, "long-history", Some("local-owner"), TurnStatus::Completed).turn_id.to_string()); }
    let foreign = seed(&state, &workspace, "long-history", Some("foreign-actor"), TurnStatus::Completed);
    let other = seed(&state, &workspace, "other-session", Some("local-owner"), TurnStatus::Completed);
    let db = Connection::open(paths.data_dir.join("unified/runtime.sqlite")).unwrap();
    db.execute("UPDATE runtime_turns SET created_at='2026-09-30T00:00:00.000Z' WHERE workspace_id=?1", params![workspace.id()]).unwrap();
    let event_count: i64 = db.query_row("SELECT COUNT(*) FROM runtime_events", [], |r|r.get(0)).unwrap();
    let router = app(&state);
    let base = format!("/api/agent/turns?workspace={wid}&session_id=long-history");
    let (status, first) = get(&router, &base, None).await;
    assert_eq!(status, StatusCode::OK, "{first}");
    assert_eq!(first["workspace"], wid);
    assert_eq!(first["session_id"], "long-history");
    assert_eq!(first["turns"].as_array().unwrap().len(), 100, "Preserve the old default limit");
    let cursor = first["next_cursor"].as_str().unwrap();
    let mut collected: Vec<String> = first["turns"].as_array().unwrap().iter().map(|t|t["turn_id"].as_str().unwrap().to_owned()).collect();
    let (_, rest) = get(&router, &format!("{base}&limit=50&cursor={cursor}"), None).await;
    collected.extend(rest["turns"].as_array().unwrap().iter().map(|t|t["turn_id"].as_str().unwrap().to_owned()));
    assert!(rest["next_cursor"].is_null());
    assert_eq!(collected, ids.into_iter().rev().collect::<Vec<_>>());
    assert_eq!(collected.iter().collect::<HashSet<_>>().len(), 105);
    assert_eq!(db.query_row("SELECT COUNT(*) FROM runtime_events", [], |r|r.get::<_,i64>(0)).unwrap(), event_count);
    assert_eq!(get(&router, &format!("/api/agent/turns?workspace={wid}&session_id=other-session&cursor={cursor}"), None).await.0, StatusCode::BAD_REQUEST);
    for turn in [&foreign.turn_id, &other.turn_id] {
        let mut forged = cursor_json(cursor);
        forged["turn_id"] = json!(turn.as_str());
        assert_eq!(get(&router, &format!("{base}&cursor={}", cursor_encode(&forged)), None).await.0, StatusCode::BAD_REQUEST,
            "Even a forged scope string cannot make the boundary lookup read another actor/session");
    }
    let (_, workspace_page) = get(&router, &format!("/api/agent/turns?workspace={wid}&limit=100"), None).await;
    assert_eq!(workspace_page["turns"].as_array().unwrap().len(), 100);
    assert!(workspace_page["session_id"].is_null(), "Workspace-wide legacy request remains supported");
    assert!(workspace_page["turns"].as_array().unwrap().iter().all(|t|t["actor_id"]=="local-owner"));
    for extra in ["&limit=0", "&limit=101", "&cursor=bad", "&actor_id=foreign-actor"] {
        assert_eq!(get(&router, &format!("{base}{extra}"), None).await.0, StatusCode::BAD_REQUEST);
    }
}

#[tokio::test]
async fn discovery_does_not_cancel_or_replay_an_active_session() {
    let temp = Temp::new();
    let state = ProductState::open_with_auth(temp.paths("active"), InstanceAuth::local()).unwrap();
    let wid = state.register(temp.dir("job").to_str().unwrap()).unwrap()["id"].as_str().unwrap().to_owned();
    let workspace = state.workspace(&wid).unwrap();
    let sid = SessionId::parse("active").unwrap();
    let lease = state.runtime.begin_turn(&workspace, &sid, json!({"actor_id":"local-owner"})).unwrap();
    let router = app(&state);
    let before = state.runtime.turn(&workspace, &sid, lease.turn_id()).unwrap();
    for _ in 0..3 {
        let (_, sessions) = get(&router, &format!("/api/agent/sessions?workspace={wid}"), None).await;
        assert_eq!(sessions["sessions"][0]["status"], "running");
        assert_eq!(sessions["sessions"][0]["turn_count"], 1);
        assert_eq!(get(&router, &format!("/api/agent/turns?workspace={wid}&session_id=active"), None).await.0, StatusCode::OK);
    }
    let after = state.runtime.turn(&workspace, &sid, lease.turn_id()).unwrap();
    assert_eq!(before.last_seq, after.last_seq);
    assert_eq!(after.status, TurnStatus::Running);
    assert!(!lease.cancellation().is_cancelled());
    lease.finish(TurnStatus::Cancelled, json!({"reason":"test cleanup"})).unwrap();
}
