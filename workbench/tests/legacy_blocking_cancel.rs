use axum::{
    body::Body,
    http::{Request, StatusCode},
    Router,
};
use civil_workbench::{
    agent::LlmMode,
    api::{self, AppState},
    config::Paths,
    turns,
};
use http_body_util::BodyExt;
use serde_json::json;
use std::{path::PathBuf, time::Duration};
use tower::ServiceExt;

struct Temp(PathBuf);
impl Temp {
    fn new() -> Self {
        let path = std::env::temp_dir().join(format!("civil-blocking-{}", uuid::Uuid::new_v4()));
        std::fs::create_dir_all(&path).unwrap();
        Self(path)
    }
}
impl Drop for Temp {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.0);
    }
}
async fn request(app: &Router, method: &str, uri: &str, payload: &str) -> (StatusCode, String) {
    let response = app
        .clone()
        .oneshot(
            Request::builder()
                .method(method)
                .uri(uri)
                .header("content-type", "application/json")
                .body(Body::from(payload.to_owned()))
                .unwrap(),
        )
        .await
        .unwrap();
    (
        response.status(),
        String::from_utf8_lossy(&response.into_body().collect().await.unwrap().to_bytes())
            .into_owned(),
    )
}

#[tokio::test]
async fn cancel_waits_for_real_blocking_write_and_prevents_downstream_harness_steps() {
    let temp = Temp::new();
    let paths = Paths::from_demo(temp.0.join("demo"));
    let mut state = AppState::live(paths.clone());
    state.llm = LlmMode::Hold;
    state.force_has_key = Some(true);
    let app = api::app(state);
    let running_app = app.clone();
    let mut running = tokio::spawn(async move {
        request(
            &running_app,
            "POST",
            "/api/chat",
            &json!({"session_id":"blocking01","message":"hello"}).to_string(),
        )
        .await
    });
    tokio::time::timeout(Duration::from_secs(5), async {
        while !turns::is_active("blocking01") {
            tokio::task::yield_now().await;
        }
    })
    .await
    .unwrap();
    let marker = temp.0.join("started-operation.txt");
    let file = marker.clone();
    let (entered, began) = tokio::sync::oneshot::channel();
    let (release, gate) = std::sync::mpsc::channel();
    let worker = tokio::spawn(async move {
        turns::blocking("blocking01", move || {
            std::fs::write(&file, "started").unwrap();
            entered.send(()).unwrap();
            gate.recv_timeout(Duration::from_secs(10)).unwrap();
            std::fs::write(&file, "finished before cancellation completion").unwrap();
            let ticket = civil_workbench::harness::Ticket::from_args(
                "blocking01",
                &json!({"brief":"生成项目日报","confirm_ok":true}),
            );
            let expert = civil_workbench::catalog::seed()
                .experts
                .iter()
                .find(|e| e.id == "pm-daily")
                .unwrap()
                .clone();
            let run = civil_workbench::harness::run_expert_steps(&paths, &expert, ticket);
            assert!(run.error.unwrap().contains("cancelled"));
            assert!(run.files.is_empty());
            assert!(run.steps.is_empty());
            let bid = civil_workbench::firm::run_bid_job(
                &paths,
                "blocking01",
                &json!({"brief":"评分:工期","confirm_ok":true}),
            );
            assert!(bid["error"].as_str().unwrap().contains("cancelled"));
            assert!(!paths.out_root.join("blocking01/firm/价表-待填.md").exists());
        })
        .await
        .unwrap();
    });
    began.await.unwrap();
    let cancel = request(&app, "POST", "/api/sessions/blocking01/cancel", "").await;
    assert_eq!(cancel.0, StatusCode::OK);
    let value: serde_json::Value = serde_json::from_str(&cancel.1).unwrap();
    assert_eq!(value["state"], "cancelling");
    assert_eq!(value["cancelled"], false);
    assert!(
        tokio::time::timeout(Duration::from_millis(100), &mut running)
            .await
            .is_err(),
        "cancel done arrived before the blocked write finished"
    );
    assert_eq!(
        request(
            &app,
            "POST",
            "/api/chat",
            &json!({"session_id":"blocking01","message":"hello"}).to_string()
        )
        .await
        .0,
        StatusCode::CONFLICT
    );
    assert_eq!(
        request(&app, "GET", "/api/sessions/blocking01/export", "")
            .await
            .0,
        StatusCode::CONFLICT
    );
    assert_eq!(std::fs::read_to_string(&marker).unwrap(), "started");
    release.send(()).unwrap();
    worker.await.unwrap();
    let result = tokio::time::timeout(Duration::from_secs(5), running)
        .await
        .unwrap()
        .unwrap();
    assert!(result.1.contains("cancelling"));
    assert!(result.1.contains("\"cancelled\":true"));
    assert_eq!(
        std::fs::read_to_string(&marker).unwrap(),
        "finished before cancellation completion"
    );
    assert!(!turns::is_active("blocking01"));
    let (flag, _) = turns::try_begin("blocking01").unwrap();
    assert!(!flag.load(std::sync::atomic::Ordering::SeqCst));
    turns::end("blocking01", &flag);
}

#[tokio::test]
async fn dropped_async_owner_keeps_lease_until_detached_blocking_work_has_exited() {
    let (flag, _) = turns::try_begin("dropped01").unwrap();
    let owner = turns::Guard {
        sid: "dropped01".into(),
        flag,
    };
    let (entered, began) = tokio::sync::oneshot::channel();
    let (release, gate) = std::sync::mpsc::channel();
    let task = tokio::spawn(turns::blocking("dropped01", move || {
        entered.send(()).unwrap();
        gate.recv_timeout(Duration::from_secs(5)).unwrap();
    }));
    began.await.unwrap();
    task.abort();
    drop(owner);
    assert!(turns::try_begin("dropped01").is_err());
    release.send(()).unwrap();
    tokio::time::timeout(Duration::from_secs(5), async {
        while turns::is_active("dropped01") {
            tokio::task::yield_now().await;
        }
    })
    .await
    .unwrap();
    let (flag, _) = turns::try_begin("dropped01").unwrap();
    turns::end("dropped01", &flag);
}
