use axum::{body::Body, http::{Request, StatusCode}, routing::post, Json, Router};
use civil_workbench::{api::{self, AppState}, config::Paths, py_engine::PyEngine};
use http_body_util::BodyExt;
use serde_json::{json, Value};
use std::{path::PathBuf, sync::{Arc, Mutex}};
use tower::ServiceExt;

const TOKEN: &str = "synthetic-locale-sidecar-token-32-characters";

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
