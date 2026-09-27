//! Boundary tests only: a scripted domain service, no solver or live model.
use axum::{
    body::Body,
    http::{Request, StatusCode},
    routing::get,
    Json, Router,
};
use civil_workbench::{
    config::Paths,
    product::api::{router, ProductState, TurnRequest},
    runtime_core::{SessionId, TurnStatus},
};
use http_body_util::BodyExt;
use serde_json::{json, Value};
use std::{ffi::OsString, path::PathBuf};
use tower::ServiceExt;

const CAD: &str = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
const FRAME: &str = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb";

struct Temp(PathBuf);
impl Temp {
    fn new() -> Self {
        let path =
            std::env::temp_dir().join(format!("civil-engineering-api-{}", uuid::Uuid::new_v4()));
        std::fs::create_dir_all(&path).unwrap();
        Self(path)
    }
}
impl Drop for Temp {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.0);
    }
}
struct DomainEnv(Option<OsString>, Option<OsString>);
impl DomainEnv {
    fn set(value: &str) -> Self {
        let old = std::env::var_os("CIVIL_DOMAIN_URL");
        let old_token = std::env::var_os("CIVIL_DOMAIN_TOKEN");
        std::env::set_var("CIVIL_DOMAIN_URL", value);
        std::env::set_var("CIVIL_DOMAIN_TOKEN", "synthetic-domain-service-token-at-least-32-bytes");
        Self(old, old_token)
    }
}
impl Drop for DomainEnv {
    fn drop(&mut self) {
        match &self.0 {
            Some(value) => std::env::set_var("CIVIL_DOMAIN_URL", value),
            None => std::env::remove_var("CIVIL_DOMAIN_URL"),
        }
        match &self.1 {
            Some(value) => std::env::set_var("CIVIL_DOMAIN_TOKEN", value),
            None => std::env::remove_var("CIVIL_DOMAIN_TOKEN"),
        }
    }
}

async fn request(app: &Router, method: &str, uri: &str, body: Value) -> (StatusCode, Value) {
    let response = app
        .clone()
        .oneshot(
            Request::builder()
                .method(method)
                .uri(uri)
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
            .unwrap_or_else(|_| json!({"detail":String::from_utf8_lossy(&bytes)})),
    )
}

#[test]
fn existing_turn_payload_keeps_defaults_and_rejects_geometry_in_selection() {
    let old = json!({"workspace":"job", "session_id":"session", "message":"Read source"});
    let req: TurnRequest = serde_json::from_value(old.clone()).unwrap();
    assert!(req.engineering.is_empty());
    assert!(req.expert_id.is_empty());
    assert!(req.risk_confirmation.is_empty());
    assert_eq!(req.mode, "model");
    assert_eq!(req.sandbox, "read-only");
    let mut extra = old;
    extra["engineering"] = json!([{"kind":"cad_section", "project_id":CAD, "revision":1,
        "source_sha256":"a".repeat(64), "inputs_sha256":"b".repeat(64), "vertices":[[1,2]]}]);
    assert!(serde_json::from_value::<TurnRequest>(extra).is_err());
}

#[tokio::test]
async fn api_exposes_saved_projects_and_validates_turn_bindings_before_creation() {
    let domain = Router::new()
        .route("/api/cad/projects", get(|| async { Json(json!({"projects":[{"id":CAD,"revision":4,"name":"Saved CAD"}]})) }))
        .route("/api/engineering/projects", get(|| async { Json(json!({"projects":[{"id":FRAME,"revision":2,"kind":"frame","name":"Frame"}, {"id":"c".repeat(32),"revision":1,"kind":"ifc_diff"}]})) }))
        .route(&format!("/api/cad/projects/{CAD}"), get(|| async { Json(json!({"ok":true,
            "project":{"id":CAD,"revision":4,"name":"Saved CAD"},
            "document":{"sha256":"a".repeat(64),"filename":"section.dxf","entities":[{"id":"entity"}]},
            "draft_config":{"mode":"section","unit":"mm","layers":{"S":"section"},"confirmed_solid":true}})) }))
        .route(&format!("/api/engineering/projects/{FRAME}"), get(|| async { Json(json!({"ok":true,
            "project":{"id":FRAME,"revision":2,"name":"Incomplete saved frame","kind":"frame"},
            "snapshot":{"kind":"frame","inputs":{"schema_version":1,"units":"SI","nodes":[]}}})) }));
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let _env = DomainEnv::set(&format!("http://{}", listener.local_addr().unwrap()));
    let server = tokio::spawn(async move {
        axum::serve(listener, domain).await.unwrap();
    });
    let folder = Temp::new();
    let workspace = folder.0.join("job");
    std::fs::create_dir_all(&workspace).unwrap();
    let state = ProductState::open(Paths::from_demo(folder.0.join("demo"))).unwrap();
    // Direct registration avoids launching a worker capability probe in this API test.
    let registered = state.register(workspace.to_str().unwrap()).unwrap();
    let wid = registered["id"].as_str().unwrap();
    let app = router(state.clone());
    let base = "/api/agent/engineering/projects";
    assert_eq!(
        request(
            &app,
            "GET",
            &format!("{base}?workspace=unknown"),
            Value::Null
        )
        .await
        .0,
        StatusCode::BAD_REQUEST
    );
    let (status, list) =
        request(&app, "GET", &format!("{base}?workspace={wid}"), Value::Null).await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(list["projects"].as_array().unwrap().len(), 2);
    let (status, inspected) = request(
        &app,
        "GET",
        &format!("{base}/cad_section/{CAD}?workspace={wid}"),
        Value::Null,
    )
    .await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(inspected["status"], "confirmation_required");
    assert_eq!(inspected["selection"]["confirmed_solid"], false);
    assert_eq!(inspected["selection"]["revision"], 4);
    assert_eq!(inspected["selection"]["source_sha256"], "a".repeat(64));
    let (_, incomplete) = request(
        &app,
        "GET",
        &format!("{base}/saved_frame/{FRAME}?workspace={wid}"),
        Value::Null,
    )
    .await;
    assert_eq!(incomplete["status"], "missing_inputs");
    assert!(!incomplete["missing_inputs"].as_array().unwrap().is_empty());
    for suffix in [format!("unknown/{CAD}"), "cad_section/not-an-id".into()] {
        assert_eq!(
            request(
                &app,
                "GET",
                &format!("{base}/{suffix}?workspace={wid}"),
                Value::Null
            )
            .await
            .0,
            StatusCode::BAD_REQUEST
        );
    }
    let mut valid = json!({"workspace":wid,"session_id":"selection","message":"Review selected project","mode":"steps",
        "engineering":[inspected["selection"].clone()],"expert_id":"structure","risk_confirmation":""});
    // Pick an actual catalog ID without depending on catalog order or aliases.
    valid["expert_id"] = json!(civil_workbench::catalog::seed().experts[0].id);
    let mut invalids = Vec::new();
    let mut invalid = valid.clone();
    invalid["engineering"] = json!(vec![inspected["selection"].clone(); 5]);
    invalids.push(invalid);
    let mut invalid = valid.clone();
    invalid["expert_id"] = json!("not-a-known-expert");
    invalids.push(invalid);
    let mut invalid = valid.clone();
    invalid["engineering"][0]["revision"] = json!(0);
    invalids.push(invalid);
    let mut invalid = valid.clone();
    invalid["engineering"][0]["project_id"] = json!("../other");
    invalids.push(invalid);
    let mut invalid = valid.clone();
    invalid["engineering"][0]["inputs_sha256"] = json!("fake");
    invalids.push(invalid);
    let mut invalid = valid.clone();
    invalid["engineering"][0]["source_sha256"] = Value::Null;
    invalids.push(invalid);
    let mut invalid = valid.clone();
    invalid["engineering"][0]["kind"] = json!("saved_frame");
    invalids.push(invalid);
    let mut invalid = valid.clone();
    invalid["risk_confirmation"] = json!("x".repeat(81));
    invalids.push(invalid);
    for invalid in invalids {
        let (status, response) = request(&app, "POST", "/api/agent/turns", invalid).await;
        assert_eq!(status, StatusCode::BAD_REQUEST, "{response}");
    }
    let ws = state.workspace(wid).unwrap();
    assert!(state.runtime.list_turns(&ws, None, 100).unwrap().is_empty());
    // Reserve the session to prove a well-shaped binding passes validation
    // without launching either a model or an engineering calculation.
    let session = SessionId::parse("selection").unwrap();
    let lease = state.runtime.begin_turn(&ws, &session, json!({})).unwrap();
    assert_eq!(
        request(&app, "POST", "/api/agent/turns", valid).await.0,
        StatusCode::CONFLICT
    );
    lease.finish(TurnStatus::Completed, json!({})).unwrap();

    let (status, started) = request(
        &app,
        "POST",
        "/api/agent/turns",
        json!({"workspace":wid,"session_id":"legacy","message":"inspect","mode":"steps"}),
    )
    .await;
    assert_eq!(status, StatusCode::ACCEPTED);
    let turn_id =
        civil_workbench::runtime_core::TurnId::parse(started["turn_id"].as_str().unwrap()).unwrap();
    let legacy = SessionId::parse("legacy").unwrap();
    for _ in 0..50 {
        let record = state.runtime.turn(&ws, &legacy, &turn_id).unwrap();
        if record.status == TurnStatus::Completed {
            break;
        }
        tokio::time::sleep(std::time::Duration::from_millis(10)).await;
    }
    let persisted = state.runtime.turn(&ws, &legacy, &turn_id).unwrap();
    assert_eq!(persisted.status, TurnStatus::Completed);
    assert_eq!(persisted.request["engineering"], json!([]));
    assert_eq!(persisted.request["expert_id"], "");
    assert_eq!(persisted.request["risk_confirmation"], "");
    server.abort();
}
