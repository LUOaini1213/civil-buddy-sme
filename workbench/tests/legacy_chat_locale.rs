use axum::{body::Body, http::{Request, StatusCode}, routing::post, Json, Router};
use civil_workbench::{api::{self, AppState}, config::Paths, py_engine::PyEngine};
use http_body_util::BodyExt;
use serde_json::{json, Value};
use std::{path::PathBuf, sync::{Arc, Mutex}};
use tower::ServiceExt;

const TOKEN: &str = "synthetic-locale-sidecar-token-32-characters";

#[test]
fn audit_retains_old_rust_traces_and_new_specialist_source_receipts() {
    let temp = Temp(std::env::temp_dir().join(format!("civil-audit-{}", uuid::Uuid::new_v4())));
    let mut paths = Paths::from_demo(temp.0.clone()); paths.out_root = temp.0.join("out");
    let root = paths.out_root.join("audit-session/runs");
    let old = root.join("old-run"); let current = root.join("new-run");
    std::fs::create_dir_all(&old).unwrap(); std::fs::create_dir_all(&current).unwrap();
    std::fs::write(old.join("trace.json"), json!({"run_id":"old-run","steps":[{"name":"inspect","tool":"legacy-read","ok":true,"legal":true}]}).to_string()).unwrap();
    let manifest = json!([{"id":"selected-original","name":"panels.xlsx","sha256":"a".repeat(64),"bytes":7}]);
    std::fs::write(current.join("workbench.json"), json!({"schema":"civil.workbench.run.v1","run_id":"new-run",
        "mtime":"2026-10-12T10:00:00+00:00","ok":true,"input_files":manifest,"nodes":[{"kind":"tool","title":"packing"}],
        "deliverables":[{"name":"draft.md","path":"synthetic-not-a-proof-of-current-existence"}]}).to_string()).unwrap();
    let audit = civil_workbench::harness::audit_session(&paths, "audit-session");
    assert_eq!(audit["counts"]["runs"], 2);
    assert!(audit["runs"].as_array().unwrap().iter().any(|run| run["run_id"] == "old-run"));
    let new = audit["runs"].as_array().unwrap().iter().find(|run| run["run_id"] == "new-run").unwrap();
    assert_eq!(new["input_files"], manifest);
    assert_eq!(new["n_nodes"], 1);
    assert_eq!(audit["counts"]["writes"], 1);
    assert_eq!(audit["counts"]["tools"], 2);
    let forged = root.join("wrong-name"); std::fs::create_dir(&forged).unwrap();
    std::fs::write(forged.join("workbench.json"), json!({"schema":"civil.workbench.run.v1","run_id":"new-run"}).to_string()).unwrap();
    assert_eq!(civil_workbench::harness::audit_session(&paths, "audit-session")["counts"]["runs"], 2);
}

struct Temp(PathBuf);
impl Drop for Temp {
    fn drop(&mut self) { let _ = std::fs::remove_dir_all(&self.0); }
}

#[tokio::test]
async fn legacy_chat_locale_defaults_reach_the_sidecar_without_changing_sources_or_approval() {
    let seen = Arc::new(Mutex::new(Vec::<Value>::new()));
    let captured = seen.clone();
    let sidecar = Router::new().route("/api/chat", post(
        move |headers: axum::http::HeaderMap, Json(body): Json<Value>| {
            let seen = captured.clone();
            async move {
                assert_eq!(headers["authorization"], format!("Bearer {TOKEN}"));
                // Match the Python ChatIn schema: an empty locale is not valid.
                assert!(matches!(body["locale"].as_str(), Some("zh-CN" | "en")));
                seen.lock().unwrap().push(body.clone());
                Json(body)
            }
        }
    ));
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let base = format!("http://{}", listener.local_addr().unwrap());
    let server = tokio::spawn(async move { axum::serve(listener, sidecar).await.unwrap() });
    let temp = Temp(std::env::temp_dir().join(format!("civil-chat-locale-{}", uuid::Uuid::new_v4())));
    std::fs::create_dir_all(&temp.0).unwrap();
    let mut state = AppState::live(Paths::from_demo(temp.0.clone()));
    state.engine = Some(Arc::new(PyEngine::attach(&base, TOKEN).unwrap()));
    let app = api::app(state);
    let message = "检查招标原文：Source-图纸.xlsx；尺寸 350 mm，不修改原件";
    for (input, expected) in [(None, "zh-CN"), (Some(""), "zh-CN"), (Some("zh-CN"), "zh-CN"), (Some("en"), "en")] {
        let mut body = json!({"message":message,"attachments":["source-id"],"confirm_ok":false,"confirm_text":""});
        if let Some(locale) = input { body["locale"] = json!(locale); }
        let response = app.clone().oneshot(Request::builder().method("POST").uri("/api/chat")
            .header("content-type", "application/json").body(Body::from(body.to_string())).unwrap()).await.unwrap();
        assert_eq!(response.status(), StatusCode::OK, "input locale {input:?}");
        let forwarded: Value = serde_json::from_slice(&response.into_body().collect().await.unwrap().to_bytes()).unwrap();
        assert_eq!(forwarded["locale"], expected);
        assert_eq!(forwarded["message"], message);
        assert_eq!(forwarded["attachments"], json!(["source-id"]));
        assert_eq!(forwarded["confirm_ok"], false);
        assert_eq!(forwarded["confirm_text"], "");
    }
    let response = app.oneshot(Request::builder().method("POST").uri("/api/chat")
        .header("content-type", "application/json").body(Body::from(json!({"message":message,"locale":"en; arbitrary command"}).to_string())).unwrap()).await.unwrap();
    assert_eq!(response.status(), StatusCode::BAD_REQUEST);
    assert_eq!(seen.lock().unwrap().len(), 4, "unsupported locale must not reach the sidecar");
    server.abort();
}

#[tokio::test]
async fn selected_specialists_and_attachments_route_to_domain_tools_without_language_or_role_hints() {
    let seen = Arc::new(Mutex::new(Vec::<Value>::new()));
    let captured = seen.clone();
    let sidecar = Router::new().route("/api/chat", post(move |Json(body): Json<Value>| {
        let seen = captured.clone(); async move { seen.lock().unwrap().push(body.clone()); Json(body) }
    }));
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let base = format!("http://{}", listener.local_addr().unwrap());
    let server = tokio::spawn(async move { axum::serve(listener, sidecar).await.unwrap() });
    let temp = Temp(std::env::temp_dir().join(format!("civil-routing-{}", uuid::Uuid::new_v4())));
    std::fs::create_dir_all(&temp.0).unwrap();
    let mut state = AppState::live(Paths::from_demo(temp.0.clone()));
    state.engine = Some(Arc::new(PyEngine::attach(&base, TOKEN).unwrap()));
    let app = api::app(state);
    for (index, fields) in [
        json!({"expert_ids":["bid-parse"]}),
        json!({"attachments":["tender-id","panels-id"]}),
        json!({"expert_ids":["pm-daily"],"attachments":["daily-record"]}),
        json!({"expert_ids":["hydraulic"]}),
    ].into_iter().enumerate() {
        let mut payload = json!({"session_id":format!("scope-{index}"), "locale":"en",
            "message":"Use the selected materials; preview only. Do not write any files.",
            "confirm_ok":false,"confirm_text":""});
        payload.as_object_mut().unwrap().extend(fields.as_object().unwrap().clone());
        let response = app.clone().oneshot(Request::builder().method("POST").uri("/api/chat")
            .header("content-type", "application/json").body(Body::from(payload.to_string())).unwrap()).await.unwrap();
        assert_eq!(response.status(), StatusCode::OK);
        let forwarded: Value = serde_json::from_slice(&response.into_body().collect().await.unwrap().to_bytes()).unwrap();
        assert_eq!(forwarded["message"], payload["message"]);
        assert_eq!(forwarded["expert_ids"], payload.get("expert_ids").cloned().unwrap_or(json!([])));
        assert_eq!(forwarded["attachments"], payload.get("attachments").cloned().unwrap_or(json!([])));
        assert_eq!(forwarded["attachment_roles"], json!({}));
        assert_eq!(forwarded["confirm_text"], "");
        assert_eq!(forwarded["confirm_ok"], false);
    }
    assert_eq!(seen.lock().unwrap().len(), 4);
    server.abort();
}
