//! Offline identity boundaries. No live provider, private input or server required.
use axum::{
    body::Body,
    http::{Request, StatusCode},
    routing::get,
    Router,
};
use civil_workbench::{
    config::Paths,
    product::{
        agent::current_turn_confirmation,
        api::{self, ProductState, TurnRequest},
        auth::{self, InstanceAuth},
    },
    runtime_core::{RuntimeCore, SessionId, TurnStatus, WorkspaceContext},
};
use http_body_util::BodyExt;
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::{path::PathBuf, sync::Arc};
use tower::ServiceExt;

const ALICE_TOKEN: &str = "synthetic-alice-token-with-at-least-32-bytes";
const BOB_TOKEN: &str = "synthetic-bob-token-with-at-least-32-bytes";

struct Temp(PathBuf);
impl Temp {
    fn new() -> Self {
        let path = std::env::temp_dir().join(format!("civil-identity-{}", uuid::Uuid::new_v4()));
        std::fs::create_dir_all(&path).unwrap();
        Self(path)
    }
    fn dir(&self, name: &str) -> PathBuf {
        let path = self.0.join(name);
        std::fs::create_dir_all(&path).unwrap();
        path
    }
}
impl Drop for Temp {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.0);
    }
}
fn identity(user: &str, token: &str, root: PathBuf) -> Arc<InstanceAuth> {
    InstanceAuth::named(
        user,
        &format!("{:x}", Sha256::digest(token.as_bytes())),
        vec![root],
        None,
    )
    .unwrap()
}
fn fake() -> Router {
    Router::new()
        .route("/api/agent/files", get(|| async { "agent-private" }))
        .route("/api/sessions", get(|| async { "legacy-private" }))
        .route("/api/cad/projects", get(|| async { "domain-private" }))
        .route("/api/file", get(|| async { "artifact-private" }))
        .route("/api/local", get(|| async { "unsafe-local" }))
        .route("/api/studio/file", get(|| async { "shared-write" }))
        .route("/", get(|| async { "home" }))
}
async fn request(
    app: &Router,
    method: &str,
    uri: &str,
    bearer: Option<&str>,
    cookie: Option<&str>,
    headers: &[(&str, &str)],
    body: &str,
) -> (StatusCode, axum::http::HeaderMap, String) {
    let mut req = Request::builder().method(method).uri(uri);
    if !headers.iter().any(|(name, _)| *name == "host") {
        req = req.header("host", "127.0.0.1:8765");
    }
    if let Some(token) = bearer {
        req = req.header("authorization", format!("Bearer {token}"));
    }
    if let Some(cookie) = cookie {
        req = req.header("cookie", cookie);
    }
    for (name, value) in headers {
        req = req.header(*name, *value);
    }
    let response = app
        .clone()
        .oneshot(req.body(Body::from(body.to_owned())).unwrap())
        .await
        .unwrap();
    let status = response.status();
    let headers = response.headers().clone();
    let bytes = response.into_body().collect().await.unwrap().to_bytes();
    (
        status,
        headers,
        String::from_utf8_lossy(&bytes).into_owned(),
    )
}

#[tokio::test]
async fn outer_gate_covers_agent_legacy_domain_and_artifact_routes() {
    let temp = Temp::new();
    let auth = identity("alice", ALICE_TOKEN, temp.dir("job"));
    let app = auth::protect(fake(), auth);
    for route in [
        "/api/agent/files",
        "/api/sessions",
        "/api/cad/projects",
        "/api/file",
    ] {
        for token in [None, Some(BOB_TOKEN)] {
            assert_eq!(
                request(&app, "GET", route, token, None, &[], "").await.0,
                StatusCode::UNAUTHORIZED,
                "{route}"
            );
        }
        assert_eq!(
            request(&app, "GET", route, Some(ALICE_TOKEN), None, &[], "")
                .await
                .0,
            StatusCode::OK,
            "{route}"
        );
    }
    for route in ["/api/local", "/api/studio/file"] {
        assert_eq!(
            request(&app, "GET", route, Some(ALICE_TOKEN), None, &[], "")
                .await
                .0,
            StatusCode::FORBIDDEN
        );
    }
    assert_eq!(
        request(&app, "GET", "/", None, None, &[], "").await.0,
        StatusCode::SEE_OTHER
    );
}

#[tokio::test]
async fn login_uses_http_only_cookie_logout_and_restart_revoke_it() {
    let temp = Temp::new();
    let root = temp.dir("job");
    let auth = identity("alice", ALICE_TOKEN, root.clone());
    let app = auth::protect(fake(), auth);
    let headers = [
        ("content-type", "application/x-www-form-urlencoded"),
        ("origin", "http://127.0.0.1:8765"),
    ];
    assert_eq!(
        request(
            &app,
            "POST",
            "/auth/login",
            None,
            None,
            &headers,
            "token=incorrect"
        )
        .await
        .0,
        StatusCode::UNAUTHORIZED
    );
    let (status, headers, body) = request(
        &app,
        "POST",
        "/auth/login",
        None,
        None,
        &headers,
        &format!("token={ALICE_TOKEN}"),
    )
    .await;
    assert_eq!(status, StatusCode::SEE_OTHER);
    assert!(!body.contains(ALICE_TOKEN));
    let cookie = headers["set-cookie"].to_str().unwrap();
    assert!(cookie.contains("HttpOnly") && cookie.contains("SameSite=Strict"));
    assert!(!cookie.contains(ALICE_TOKEN));
    let cookie = cookie.split(';').next().unwrap();
    assert_eq!(
        request(&app, "GET", "/api/sessions", None, Some(cookie), &[], "")
            .await
            .0,
        StatusCode::OK
    );
    let restarted = auth::protect(fake(), identity("alice", ALICE_TOKEN, root));
    assert_eq!(
        request(
            &restarted,
            "GET",
            "/api/sessions",
            None,
            Some(cookie),
            &[],
            ""
        )
        .await
        .0,
        StatusCode::UNAUTHORIZED
    );
    assert_eq!(
        request(&app, "POST", "/auth/logout", None, Some(cookie), &[], "")
            .await
            .0,
        StatusCode::SEE_OTHER
    );
    assert_eq!(
        request(&app, "GET", "/api/sessions", None, Some(cookie), &[], "")
            .await
            .0,
        StatusCode::UNAUTHORIZED
    );
}

#[tokio::test]
async fn cross_origin_and_query_credentials_are_refused_even_with_valid_token() {
    let temp = Temp::new();
    let app = auth::protect(fake(), identity("alice", ALICE_TOKEN, temp.dir("job")));
    for headers in [
        vec![("origin", "https://attacker.example")],
        vec![("sec-fetch-site", "cross-site")],
        vec![("host", "attacker.example")],
        vec![("origin", "https://127.0.0.1:8765")],
    ] {
        assert_eq!(
            request(
                &app,
                "GET",
                "/api/sessions",
                Some(ALICE_TOKEN),
                None,
                &headers,
                ""
            )
            .await
            .0,
            StatusCode::FORBIDDEN
        );
    }
    for uri in [
        "/api/sessions?token=redacted",
        "/auth/login?%74oken=redacted",
    ] {
        assert_eq!(
            request(&app, "GET", uri, Some(ALICE_TOKEN), None, &[], "")
                .await
                .0,
            StatusCode::BAD_REQUEST
        );
    }
}

#[tokio::test]
async fn configured_https_origin_sets_secure_cookie_and_rotation_rejects_old_tokens() {
    let temp=Temp::new();let root=temp.dir("job");
    let auth=InstanceAuth::named("alice",&format!("{:x}",Sha256::digest(ALICE_TOKEN.as_bytes())),vec![root.clone()],Some("https://workbench.example".into())).unwrap();
    let app=auth::protect(fake(),auth);
    let (status,headers,_)=request(&app,"POST","/auth/login",None,None,&[("host","workbench.example"),("origin","https://workbench.example"),("content-type","application/x-www-form-urlencoded")],&format!("token={ALICE_TOKEN}")).await;
    assert_eq!(status,StatusCode::SEE_OTHER);assert!(headers["set-cookie"].to_str().unwrap().contains("; Secure"));
    let rotated=auth::protect(fake(),identity("alice",BOB_TOKEN,root));
    assert_eq!(request(&rotated,"GET","/api/sessions",Some(ALICE_TOKEN),None,&[],"").await.0,StatusCode::UNAUTHORIZED);
    assert_eq!(request(&rotated,"GET","/api/sessions",Some(BOB_TOKEN),None,&[],"").await.0,StatusCode::OK);
}

#[test]
fn state_is_bound_to_one_owner_and_one_exact_workspace() {
    let temp = Temp::new();
    let root = temp.dir("job");
    let other = temp.dir("other");
    let state = temp.dir("state");
    let alice = identity("alice", ALICE_TOKEN, root.clone());
    alice.claim_state(&state).unwrap();
    alice.claim_state(&state).unwrap();
    assert!(identity("bob", BOB_TOKEN, root.clone())
        .claim_state(&state)
        .is_err());
    assert!(identity("alice", ALICE_TOKEN, other.clone())
        .claim_state(&state)
        .is_err());
    assert!(identity("bob", BOB_TOKEN, root.clone())
        .claim_state(&temp.dir("bob-state"))
        .is_err());
    assert!(identity("alice", ALICE_TOKEN, root.clone())
        .claim_state(&temp.dir("second-alice-state"))
        .is_err());
    assert!(alice.authorize_workspace(&root).is_ok());
    assert!(alice.authorize_workspace(&other).is_err());
    assert!(alice.authorize_workspace(&temp.dir("job/child")).is_err());
    assert!(alice.authorize_workspace(&temp.0).is_err());
    let digest = format!("{:x}", Sha256::digest(ALICE_TOKEN.as_bytes()));
    assert!(InstanceAuth::named("alice", &digest, vec![root, other], None).is_err());
    assert_eq!(alice.capabilities()["multi_tenant"], false);
}

#[test]
fn workspace_ownership_rejects_nested_roots_in_both_registration_orders() {
    let temp = Temp::new();
    let parent = temp.dir("parent-first");
    let child = temp.dir("parent-first/child");
    identity("alice", ALICE_TOKEN, parent).claim_state(&temp.dir("state-parent")).unwrap();
    assert!(identity("bob", BOB_TOKEN, child).claim_state(&temp.dir("state-child")).unwrap_err().contains("nested inside"));
    let parent = temp.dir("child-first");
    let child = temp.dir("child-first/child");
    identity("bob", BOB_TOKEN, child).claim_state(&temp.dir("state-child2")).unwrap();
    assert!(identity("alice", ALICE_TOKEN, parent).claim_state(&temp.dir("state-parent2")).unwrap_err().contains("contains an owned"));
}

#[tokio::test]
async fn unconfigured_local_mode_rejects_proxy_forwarding() {
    let app = auth::protect(fake(), InstanceAuth::local());
    assert_eq!(
        request(&app, "GET", "/api/sessions", None, None, &[], "")
            .await
            .0,
        StatusCode::OK
    );
    assert_eq!(
        request(
            &app,
            "GET",
            "/api/sessions",
            None,
            None,
            &[("x-forwarded-for", "198.51.100.1")],
            ""
        )
        .await
        .0,
        StatusCode::FORBIDDEN
    );
}

#[tokio::test]
async fn named_instances_cannot_read_each_others_workspace_or_spoof_audit_actor() {
    let temp = Temp::new();
    let alice_root = temp.dir("alice-job");
    let bob_root = temp.dir("bob-job");
    std::fs::write(alice_root.join("alice.txt"), "Alice source").unwrap();
    std::fs::write(bob_root.join("bob.txt"), "Bob source").unwrap();
    let a = ProductState::open_with_auth(
        Paths::from_demo(temp.dir("alice-demo")),
        identity("alice", ALICE_TOKEN, alice_root.clone()),
    )
    .unwrap();
    let b = ProductState::open_with_auth(
        Paths::from_demo(temp.dir("bob-demo")),
        identity("bob", BOB_TOKEN, bob_root.clone()),
    )
    .unwrap();
    let aid = a.register(alice_root.to_str().unwrap()).unwrap()["id"]
        .as_str()
        .unwrap()
        .to_owned();
    let bid = b.register(bob_root.to_str().unwrap()).unwrap()["id"]
        .as_str()
        .unwrap()
        .to_owned();
    assert!(a.register(bob_root.to_str().unwrap()).is_err());
    assert!(b.workspace(&aid).is_err());
    assert!(a.workspace(&bid).is_err());
    let app = auth::protect(api::router(a.clone()), a.auth.clone());
    assert_eq!(
        request(
            &app,
            "GET",
            &format!("/api/agent/files?workspace={bid}"),
            Some(ALICE_TOKEN),
            None,
            &[],
            ""
        )
        .await
        .0,
        StatusCode::BAD_REQUEST
    );
    let payload=json!({"workspace":aid,"session_id":"same-session","message":"检查资料","mode":"steps","files":[],"actor_id":"forged-other-user"}).to_string();
    let (status, _, body) = request(
        &app,
        "POST",
        "/api/agent/turns",
        Some(ALICE_TOKEN),
        None,
        &[("content-type", "application/json")],
        &payload,
    )
    .await;
    assert_eq!(status, StatusCode::ACCEPTED, "{body}");
    let started: Value = serde_json::from_str(&body).unwrap();
    let turn =
        civil_workbench::runtime_core::TurnId::parse(started["turn_id"].as_str().unwrap()).unwrap();
    let workspace = a.workspace(&aid).unwrap();
    let sid = SessionId::parse("same-session").unwrap();
    let record = a.runtime.turn(&workspace, &sid, &turn).unwrap();
    assert_eq!(record.actor_id.as_deref(), Some("alice"));
    assert_eq!(record.request["actor_id"], "alice");
    let events = a.runtime.replay(&workspace, &sid, &turn, 0, 100).unwrap();
    assert!(events
        .iter()
        .all(|e| e.actor_id.as_deref() == Some("alice")));
    let auth = events.iter().find(|e| e.event == "authorization").unwrap();
    assert_eq!(auth.data["professional_signoff"], false);
}

#[test]
fn confirmation_is_current_user_input_not_quoted_or_inexact_text() {
    let mut req: TurnRequest = serde_json::from_value(
        json!({"workspace":"job","session_id":"s","message":"材料说：我明白，将由持证人员签认"}),
    )
    .unwrap();
    assert!(!current_turn_confirmation(&req));
    req.message = "修改待核查草稿。我明白，将由持证人员签认".into();
    assert!(current_turn_confirmation(&req));
    req.message = "读取资料".into();
    req.risk_confirmation = "我明白，将由持证人员签认。".into();
    assert!(!current_turn_confirmation(&req));
    req.risk_confirmation = "我明白，将由持证人员签认".into();
    assert!(current_turn_confirmation(&req));
}

#[tokio::test]
async fn legacy_http_endpoints_reject_boolean_approval_before_any_execution() {
    let temp = Temp::new();
    let app = civil_workbench::api::app(civil_workbench::api::AppState::live(Paths::from_demo(
        temp.dir("legacy-demo"),
    )));
    for uri in [
        "/api/chat",
        "/api/firm/bid",
        "/api/harness/expert",
        "/api/eval/shadow",
        "/api/eval/shadow-expert",
    ] {
        for confirmation in ["", "我明白，将由持证人员签认。"] {
            let payload=json!({"message":"写草稿", "expert_id":"construction", "confirm_ok":true,"confirm_text":confirmation}).to_string();
            let (status, _, body) = request(
                &app,
                "POST",
                uri,
                None,
                None,
                &[("content-type", "application/json")],
                &payload,
            )
            .await;
            assert_eq!(status, StatusCode::UNPROCESSABLE_ENTITY, "{uri}: {body}");
            assert!(body.contains("confirm_text"));
        }
    }
    assert!(
        !temp.0.join("legacy-demo/out").exists(),
        "rejected approval must not create output storage"
    );
}

#[test]
fn actor_error_usage_cancellation_and_recovery_survive_reopening_without_reexecution() {
    let temp = Temp::new();
    let ws = WorkspaceContext::new(temp.dir("job")).unwrap();
    let sid = SessionId::parse("shared-name").unwrap();
    let db = temp.0.join("runtime.sqlite");
    let runtime = RuntimeCore::open(&db).unwrap();
    let failed = runtime
        .begin_turn(&ws, &sid, json!({"actor_id":"alice"}))
        .unwrap();
    let failed_id = failed.turn_id().clone();
    failed
        .emit(
            "model_response",
            json!({"usage":{"input_tokens":13,"output_tokens":5,"estimated":false}}),
        )
        .unwrap();
    failed
        .finish(
            TurnStatus::Failed,
            json!({"error":"synthetic_tool_failure","usage":{"spent_tokens":18}}),
        )
        .unwrap();
    let cancelled = runtime
        .begin_turn(&ws, &sid, json!({"actor_id":"alice"}))
        .unwrap();
    let cancelled_id = cancelled.turn_id().clone();
    runtime.cancel_turn(&ws, &sid, &cancelled_id).unwrap();
    cancelled
        .finish_resolving_cancel(
            TurnStatus::Completed,
            json!({"artifacts":[],"usage":{"spent_tokens":0}}),
        )
        .unwrap();
    let interrupted = runtime
        .begin_turn(&ws, &sid, json!({"actor_id":"alice"}))
        .unwrap();
    let interrupted_id = interrupted.turn_id().clone();
    let reopened = RuntimeCore::open(&db).unwrap();
    assert_eq!(reopened.recover_interrupted().unwrap(), 1);
    assert_eq!(reopened.recover_interrupted().unwrap(), 0);
    for (id, status) in [
        (&failed_id, TurnStatus::Failed),
        (&cancelled_id, TurnStatus::Cancelled),
        (&interrupted_id, TurnStatus::Interrupted),
    ] {
        let record = reopened.turn(&ws, &sid, id).unwrap();
        assert_eq!(record.status, status);
        assert_eq!(record.actor_id.as_deref(), Some("alice"));
        let events = reopened.replay(&ws, &sid, id, 0, 100).unwrap();
        assert!(events
            .iter()
            .all(|e| e.actor_id.as_deref() == Some("alice")));
        assert_eq!(
            events
                .iter()
                .filter(|e| matches!(
                    e.event.as_str(),
                    "turn.failed" | "turn.cancelled" | "turn.interrupted"
                ))
                .count(),
            1
        );
    }
    assert_eq!(
        reopened
            .turn(&ws, &sid, &failed_id)
            .unwrap()
            .result
            .unwrap()["error"],
        "synthetic_tool_failure"
    );
    assert_eq!(
        reopened
            .turn(&ws, &sid, &failed_id)
            .unwrap()
            .result
            .unwrap()["usage"]["spent_tokens"],
        18
    );
    assert_eq!(
        reopened
            .turn(&ws, &sid, &interrupted_id)
            .unwrap()
            .result
            .unwrap()["reason"],
        "runtime_restart"
    );
}

#[tokio::test]
async fn legacy_chat_validates_before_proxy_and_keeps_the_current_typed_confirmation() {
    use std::sync::Mutex;
    let temp=Temp::new();
    let seen=Arc::new(Mutex::new(Vec::<Value>::new()));let captured=seen.clone();
    let domain=Router::new().route("/api/chat",axum::routing::post(move |headers:axum::http::HeaderMap, axum::Json(body):axum::Json<Value>| {
        let seen=captured.clone();async move {
            assert_eq!(headers["authorization"],format!("Bearer {ALICE_TOKEN}"));
            seen.lock().unwrap().push(body); axum::Json(json!({"ok":true}))
        }
    }));
    let listener=tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();let base=format!("http://{}",listener.local_addr().unwrap());
    let server=tokio::spawn(async move {axum::serve(listener,domain).await.unwrap()});
    let mut state=civil_workbench::api::AppState::live(Paths::from_demo(temp.dir("proxy-demo")));
    state.engine=Some(Arc::new(civil_workbench::py_engine::PyEngine::attach(&base,ALICE_TOKEN).unwrap()));
    let app=civil_workbench::api::app(state);
    let mut payload=json!({"message":"核对资料","cad_project_id":"a".repeat(32),"confirm_ok":true});
    assert_eq!(request(&app,"POST","/api/chat",None,None,&[("content-type","application/json")],&payload.to_string()).await.0,StatusCode::UNPROCESSABLE_ENTITY);
    assert!(seen.lock().unwrap().is_empty());
    payload["confirm_text"]=json!("我明白，将由持证人员签认");
    assert_eq!(request(&app,"POST","/api/chat",None,None,&[("content-type","application/json")],&payload.to_string()).await.0,StatusCode::OK);
    let seen=seen.lock().unwrap();assert_eq!(seen.len(),1);assert_eq!(seen[0]["confirm_text"],"我明白，将由持证人员签认");
    server.abort();
}

#[tokio::test]
async fn full_session_bundle_proxy_preserves_binary_sources_artifacts_and_content_type() {
    use axum::response::IntoResponse;
    use std::{io::Write, sync::Mutex};
    let temp = Temp::new();
    let attachment = b"original drawing\0\xff\x80";
    let artifact = b"generated workbook\0\x90\xfe";
    let mut writer = zip::ZipWriter::new(std::io::Cursor::new(Vec::new()));
    for (name, bytes) in [
        ("bundle.json", br#"{"schema":"civil.session.bundle.v1"}"#.as_slice()),
        ("attachments/source.bin", attachment.as_slice()),
        ("artifacts/result.bin", artifact.as_slice()),
    ] {
        writer.start_file(name, zip::write::SimpleFileOptions::default()).unwrap();
        writer.write_all(bytes).unwrap();
    }
    let bundle = writer.finish().unwrap().into_inner();
    let output = bundle.clone();
    let seen = Arc::new(Mutex::new(Vec::<(String, Vec<u8>)>::new()));
    let captured = seen.clone();
    let sidecar = Router::new()
        .route("/api/sessions/import-copy", get(|| async { axum::Json(json!({"session_id":"import-copy","attachments":[{"id":"source"}],"deliverables":[{"file":"result.bin"}]})) }))
        .route("/api/sessions/bundle01/export", get(move |headers: axum::http::HeaderMap| {
            let output = output.clone();
            async move {
                assert_eq!(headers["authorization"], format!("Bearer {ALICE_TOKEN}"));
                ([("content-type", "application/zip")], output).into_response()
            }
        }))
        .route("/api/session-import", axum::routing::post(move |headers: axum::http::HeaderMap, body: axum::body::Bytes| {
            let captured = captured.clone();
            async move {
                assert_eq!(headers["authorization"], format!("Bearer {ALICE_TOKEN}"));
                captured.lock().unwrap().push((headers["content-type"].to_str().unwrap().to_owned(), body.to_vec()));
                axum::Json(json!({"ok":true,"session_id":"import-copy","confirmation_reset":true,"attachments":1,"deliverables":1}))
            }
        }));
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let base = format!("http://{}", listener.local_addr().unwrap());
    let server = tokio::spawn(async move { axum::serve(listener, sidecar).await.unwrap() });
    let mut state = civil_workbench::api::AppState::live(Paths::from_demo(temp.dir("bundle-demo")));
    state.engine = Some(Arc::new(civil_workbench::py_engine::PyEngine::attach(&base, ALICE_TOKEN).unwrap()));
    let app = civil_workbench::api::app(state);
    let response = app.clone().oneshot(Request::builder().uri("/api/sessions/bundle01/export").body(Body::empty()).unwrap()).await.unwrap();
    assert_eq!(response.status(), StatusCode::OK);
    assert_eq!(response.headers()["content-type"], "application/zip");
    assert!(response.headers().contains_key("content-disposition"));
    let exported = response.into_body().collect().await.unwrap().to_bytes();
    assert_eq!(exported.as_ref(), bundle.as_slice());
    for content_type in ["application/zip", "multipart/form-data; boundary=literal-boundary"] {
        let response = app.clone().oneshot(Request::builder().method("POST").uri("/api/session-import")
            .header("content-type", content_type).body(Body::from(exported.clone())).unwrap()).await.unwrap();
        assert_eq!(response.status(), StatusCode::OK);
        let value: Value = serde_json::from_slice(&response.into_body().collect().await.unwrap().to_bytes()).unwrap();
        assert_eq!(value["confirmation_reset"], true);
        assert_eq!(value["attachments"], 1);
        assert_eq!(value["deliverables"], 1);
    }
    let captured = seen.lock().unwrap();
    assert_eq!(captured.len(), 2);
    assert_eq!(captured[0], ("application/zip".to_owned(), bundle.clone()));
    assert_eq!(captured[1], ("multipart/form-data; boundary=literal-boundary".to_owned(), bundle));
    drop(captured);
    let (status, _, body) = request(&app, "GET", "/api/sessions/import-copy", None, None, &[], "").await;
    assert_eq!(status, StatusCode::OK);
    let detail: Value = serde_json::from_str(&body).unwrap();
    assert_eq!(detail["attachments"][0]["id"], "source");
    assert_eq!(detail["deliverables"][0]["file"], "result.bin");
    let (flag, _) = civil_workbench::turns::try_begin("bundle01").unwrap();
    assert_eq!(request(&app, "GET", "/api/sessions/bundle01/export", None, None, &[], "").await.0, StatusCode::CONFLICT);
    assert!(civil_workbench::turns::request("bundle01"));
    assert_eq!(request(&app, "GET", "/api/sessions/bundle01/export", None, None, &[], "").await.0, StatusCode::CONFLICT);
    civil_workbench::turns::end("bundle01", &flag);
    server.abort();
}

#[tokio::test]
async fn no_engine_refuses_incomplete_session_backup_instead_of_silently_losing_files() {
    let temp = Temp::new();
    let app = civil_workbench::api::app(civil_workbench::api::AppState::live(Paths::from_demo(temp.dir("no-engine-demo"))));
    assert_eq!(request(&app, "GET", "/api/sessions/bundle02/export", None, None, &[], "").await.0, StatusCode::SERVICE_UNAVAILABLE);
    assert_eq!(request(&app, "POST", "/api/session-import", None, None, &[("content-type", "application/zip")], "old transcript-only archive").await.0, StatusCode::SERVICE_UNAVAILABLE);
}

#[test]
fn legacy_session_reservation_is_atomic_and_cannot_replace_a_cancelled_slot() {
    let sid = format!("atomic-{}", uuid::Uuid::new_v4().simple());
    let barrier = Arc::new(std::sync::Barrier::new(16));
    let threads: Vec<_> = (0..16).map(|_| {
        let barrier = barrier.clone(); let sid = sid.clone();
        std::thread::spawn(move || { barrier.wait(); civil_workbench::turns::try_begin(&sid).ok() })
    }).collect();
    let winners: Vec<_> = threads.into_iter().filter_map(|t| t.join().unwrap()).collect();
    assert_eq!(winners.len(), 1);
    assert!(civil_workbench::turns::request(&sid));
    assert!(civil_workbench::turns::try_begin(&sid).is_err());
    civil_workbench::turns::end(&sid, &winners[0].0);
    let (fresh, _) = civil_workbench::turns::try_begin(&sid).unwrap();
    assert!(!fresh.load(std::sync::atomic::Ordering::SeqCst));
    civil_workbench::turns::end(&sid, &fresh);
}

async fn wait_for_legacy_turn(sid: &str) {
    tokio::time::timeout(std::time::Duration::from_secs(5), async {
        while !civil_workbench::turns::is_active(sid) { tokio::task::yield_now().await; }
    }).await.expect("legacy task did not acquire the session");
}

#[tokio::test]
async fn legacy_native_cancel_keeps_other_sessions_and_allows_a_fresh_turn_only_after_finish() {
    let temp = Temp::new();
    let mut state = civil_workbench::api::AppState::live(Paths::from_demo(temp.dir("native-cancel-demo")));
    state.llm = civil_workbench::agent::LlmMode::Hold;
    state.force_has_key = Some(true);
    let app = civil_workbench::api::app(state);
    let start = |sid: &'static str| {
        let app = app.clone();
        tokio::spawn(async move {
            request(&app, "POST", "/api/chat", None, None, &[("content-type", "application/json")],
                &json!({"session_id":sid,"message":"hello"}).to_string()).await
        })
    };
    let old = start("native-old");
    let other = start("native-other");
    wait_for_legacy_turn("native-old").await;
    wait_for_legacy_turn("native-other").await;
    let payload = json!({"session_id":"native-old","message":"hello"}).to_string();
    assert_eq!(request(&app, "POST", "/api/chat", None, None, &[("content-type", "application/json")], &payload).await.0, StatusCode::CONFLICT);
    assert_eq!(request(&app, "GET", "/api/sessions/native-old/export", None, None, &[], "").await.0, StatusCode::CONFLICT);
    assert_eq!(request(&app, "POST", "/api/sessions/native-old/cancel", None, None, &[], "").await.0, StatusCode::OK);
    let result = tokio::time::timeout(std::time::Duration::from_secs(5), old).await.unwrap().unwrap();
    assert!(result.2.contains("\"cancelled\":true"));
    assert!(civil_workbench::turns::is_active("native-other"));
    let fresh = start("native-old");
    wait_for_legacy_turn("native-old").await;
    request(&app, "POST", "/api/sessions/native-old/cancel", None, None, &[], "").await;
    assert!(tokio::time::timeout(std::time::Duration::from_secs(5), fresh).await.unwrap().unwrap().2.contains("\"cancelled\":true"));
    request(&app, "POST", "/api/sessions/native-other/cancel", None, None, &[], "").await;
    assert!(tokio::time::timeout(std::time::Duration::from_secs(5), other).await.unwrap().unwrap().2.contains("\"cancelled\":true"));
}

#[tokio::test]
async fn export_and_forwarded_chat_hold_the_same_exclusive_legacy_lease() {
    use axum::response::IntoResponse;
    let temp = Temp::new();
    let entered = Arc::new(tokio::sync::Notify::new());
    let release = Arc::new(tokio::sync::Notify::new());
    let cancel_release = release.clone();
    let (e, r) = (entered.clone(), release.clone());
    let (e2, r2) = (entered.clone(), release.clone());
    let sidecar = Router::new()
        .route("/api/sessions/lease01/export", get(move || {
            let (entered, release) = (e.clone(), r.clone());
            async move { entered.notify_one(); release.notified().await; ([("content-type", "application/zip")], b"binary bundle").into_response() }
        }))
        .route("/api/chat", axum::routing::post(move || {
            let (entered, release) = (e2.clone(), r2.clone());
            async move { entered.notify_one(); release.notified().await; axum::Json(json!({"cancelled":true})) }
        }))
        .route("/api/sessions/lease01/cancel", axum::routing::post(move || {
            let release = cancel_release.clone(); async move { release.notify_one(); axum::Json(json!({"cancel_requested":true})) }
        }));
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let base = format!("http://{}", listener.local_addr().unwrap());
    let server = tokio::spawn(async move { axum::serve(listener, sidecar).await.unwrap() });
    let mut state = civil_workbench::api::AppState::live(Paths::from_demo(temp.dir("lease-demo")));
    state.engine = Some(Arc::new(civil_workbench::py_engine::PyEngine::attach(&base, ALICE_TOKEN).unwrap()));
    state.force_has_key = Some(true);
    let app = civil_workbench::api::app(state);
    for exporting in [true, false] {
        let active_app = app.clone();
        let active = tokio::spawn(async move {
            if exporting { request(&active_app, "GET", "/api/sessions/lease01/export", None, None, &[], "").await }
            else { request(&active_app, "POST", "/api/chat", None, None, &[("content-type","application/json")],
                &json!({"session_id":"lease01","message":"hello","cad_project_id":"a".repeat(32)}).to_string()).await }
        });
        tokio::time::timeout(std::time::Duration::from_secs(5), entered.notified()).await.unwrap();
        for cad in ["", "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"] {
            assert_eq!(request(&app, "POST", "/api/chat", None, None, &[("content-type","application/json")],
                &json!({"session_id":"lease01","message":"hello","cad_project_id":cad}).to_string()).await.0, StatusCode::CONFLICT);
        }
        assert_eq!(request(&app, "GET", "/api/sessions/lease01/export", None, None, &[], "").await.0, StatusCode::CONFLICT);
        if exporting { release.notify_one(); }
        else { request(&app, "POST", "/api/sessions/lease01/cancel", None, None, &[], "").await; }
        let response = tokio::time::timeout(std::time::Duration::from_secs(5), active).await.unwrap().unwrap();
        assert_eq!(response.0, StatusCode::OK);
        assert!(!civil_workbench::turns::is_active("lease01"));
    }
    server.abort();
}
