use axum::{body::Body, http::{Request, StatusCode}, Router};
use civil_workbench::{config::Paths, product::api::{router, ProductState}};
use http_body_util::BodyExt;
use serde_json::{json, Value};
use tower::ServiceExt;

async fn call(app: &Router, method: &str, url: &str, body: Value) -> (StatusCode, Value) {
    let response = app.clone().oneshot(Request::builder().method(method).uri(url)
        .header("Content-Type", "application/json").body(Body::from(body.to_string())).unwrap()).await.unwrap();
    let status = response.status();
    (status, serde_json::from_slice(&response.into_body().collect().await.unwrap().to_bytes()).unwrap())
}

#[tokio::test]
async fn language_is_validated_persisted_and_used_for_offline_replies() {
    let root = std::env::temp_dir().join(format!("civil-locale-{}", uuid::Uuid::new_v4().simple()));
    let workspace = root.join("job"); std::fs::create_dir_all(&workspace).unwrap();
    let mut paths = Paths::from_demo(root.join("demo"));
    paths.repo_root = std::path::PathBuf::from(env!("CARGO_MANIFEST_DIR")).parent().unwrap().to_path_buf();
    let app = router(ProductState::open(paths).unwrap());
    let (_, registered) = call(&app, "POST", "/api/agent/workspaces", json!({"path":workspace})).await;
    let wid = registered["workspace"]["id"].as_str().unwrap();
    let bad = call(&app,"POST","/api/agent/turns",json!({"workspace":wid,"session_id":"bad","message":"inspect","mode":"steps","locale":"en; bypass checks"})).await;
    assert_eq!(bad.0, StatusCode::BAD_REQUEST);
    for (index, locale, prefix) in [(0,"en","Select project materials"),(1,"zh-CN","请选择工程资料")] {
        let session = format!("locale-{index}");
        let (status, started) = call(&app,"POST","/api/agent/turns",json!({"workspace":wid,"session_id":session,"message":"Inspect without writing","mode":"steps","locale":locale})).await;
        assert_eq!(status, StatusCode::ACCEPTED);
        let url = format!("/api/agent/turns/{}/events?workspace={wid}&session_id={session}",started["turn_id"].as_str().unwrap());
        let mut result = Value::Null;
        for _ in 0..100 {
            result = call(&app,"GET",&url,Value::Null).await.1;
            if result["turn"]["status"] == "completed" {break;}
            tokio::time::sleep(std::time::Duration::from_millis(20)).await;
        }
        assert_eq!(result["turn"]["status"],"completed","{result}");
        assert_eq!(result["turn"]["request"]["locale"],locale);
        assert!(result["turn"]["result"]["reply"].as_str().unwrap().starts_with(prefix));
        assert_eq!(result["turn"]["request"]["risk_confirmation_present"],false);
    }
}
