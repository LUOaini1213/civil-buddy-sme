use axum::{
    body::Body,
    http::{Request, StatusCode},
    routing::post,
    Json, Router,
};
use civil_workbench::{
    config::{LlmConfig, Paths},
    product::{
        api::{router, ProductState},
        providers::{self, DecisionQuestion},
    },
    runtime_core::{BudgetLimits, BudgetTree, CancellationToken, TaskId},
};
use http_body_util::BodyExt;
use serde_json::{json, Value};
use std::{
    collections::BTreeMap,
    sync::{Arc, Mutex},
    time::Duration,
};
use tower::ServiceExt;

async fn request(app: &Router, method: &str, url: &str, body: Value) -> (StatusCode, Value) {
    let response = app
        .clone()
        .oneshot(
            Request::builder()
                .method(method)
                .uri(url)
                .header("Content-Type", "application/json")
                .body(Body::from(body.to_string()))
                .unwrap(),
        )
        .await
        .unwrap();
    let status = response.status();
    let bytes = response.into_body().collect().await.unwrap().to_bytes();
    (status, serde_json::from_slice(&bytes).unwrap())
}

#[test]
fn jev_rejects_stale_candidates_and_invalid_scores() {
    let questions = BTreeMap::from([
        (
            "route".into(),
            DecisionQuestion::Choice {
                instructions: "Choose a legal next step".into(),
                criteria: BTreeMap::from([
                    ("replan".into(), "Try legal layout".into()),
                    ("request_input".into(), "Missing source facts".into()),
                ]),
            },
        ),
        (
            "severity".into(),
            DecisionQuestion::Score {
                instructions: "Review urgency".into(),
                criteria: vec!["low".into(), "high".into()],
            },
        ),
        (
            "conflict".into(),
            DecisionQuestion::Noul {
                instructions: "Source descriptions conflict".into(),
            },
        ),
    ]);
    let raw = json!({"answers":{"route":{"type":"choice","choice":"replan","confidence":0.9,"probabilities":{"replan":0.8,"request_input":0.2}},"severity":{"type":"score","score":0.8,"confidence":0.9,"legend":{"0":"low","1":"high"},"probabilities":{"0":0.2,"1":0.8}},"conflict":{"type":"noul","noul":0.7}}});
    assert!(providers::validate_answers(&questions, &raw).is_ok());
    let mut invalid = raw.clone();
    invalid["answers"]["route"]["choice"] = json!("deliver_success");
    assert!(providers::validate_answers(&questions, &invalid).is_err());
    let mut invalid = raw.clone();
    invalid["answers"]["route"]["probabilities"] = json!({"replan":0.8,"deliver_success":0.2});
    assert!(providers::validate_answers(&questions, &invalid).is_err());
    let mut invalid = raw.clone();
    invalid["answers"]["severity"]["score"] = json!(2);
    assert!(providers::validate_answers(&questions, &invalid).is_err());
    let mut invalid = raw;
    invalid["answers"]["severity"]["legend"]["1"] = json!("approved");
    assert!(providers::validate_answers(&questions, &invalid).is_err());
}

#[tokio::test]
async fn provider_accounts_usage_and_cancellation_consumes_attempt() {
    let captured = Arc::new(Mutex::new(Vec::<Value>::new()));
    let capture = captured.clone();
    let server=Router::new().route("/chat/completions",post(move |Json(payload):Json<Value>|{let capture=capture.clone();async move{
        capture.lock().unwrap().push(payload.clone());
        if payload["model"]=="slow"{tokio::time::sleep(Duration::from_secs(10)).await;}
        Json(json!({"model":"test-model","choices":[{"message":{"role":"assistant","content":"evidence read"},"finish_reason":"stop"}],"usage":{"prompt_tokens":31,"completion_tokens":5}}))
    }}));
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let base_url = format!("http://{}", listener.local_addr().unwrap());
    let server = tokio::spawn(async move { axum::serve(listener, server).await.unwrap() });
    let cfg = LlmConfig {
        api_key: "scripted-key".into(),
        base_url,
        model: "test".into(),
    };
    let task = TaskId::new();
    let budget = BudgetTree::new(task.clone(), BudgetLimits::default()).unwrap();
    let cancel = CancellationToken::new();
    let result = providers::complete(
        &cfg,
        &[json!({"role":"user","content":"Read"})],
        &[],
        100,
        4096,
        &budget,
        &task,
        &cancel,
    )
    .await
    .unwrap();
    assert_eq!(result.usage.input_tokens, 31);
    assert!(!result.usage.estimated);
    assert_eq!(budget.snapshot().unwrap().spent_tokens, 36);
    assert_eq!(captured.lock().unwrap()[0]["max_tokens"], 100);
    let cfg = LlmConfig {
        model: "slow".into(),
        ..cfg
    };
    let token = cancel.clone();
    tokio::spawn(async move {
        tokio::time::sleep(Duration::from_millis(80)).await;
        token.cancel();
    });
    assert!(providers::complete(
        &cfg,
        &[json!({"role":"user","content":"Read"})],
        &[],
        100,
        4096,
        &budget,
        &task,
        &cancel
    )
    .await
    .is_err());
    let snapshot = budget.snapshot().unwrap();
    assert_eq!(snapshot.model_calls, 2);
    assert_eq!(snapshot.reserved_tokens, 0);
    assert!(snapshot.spent_tokens > 36);
    server.abort();
}

#[tokio::test]
async fn api_runs_persistent_scoped_tools_and_shared_child_tasks() {
    let root = std::env::temp_dir().join(format!(
        "civil-product-test-{}",
        uuid::Uuid::new_v4().simple()
    ));
    let workspace = root.join("job");
    std::fs::create_dir_all(&workspace).unwrap();
    std::fs::write(workspace.join("brief.txt"), "Source quantity: 12 panels\n").unwrap();
    let mut paths = Paths::from_demo(root.join("demo"));
    paths.repo_root = std::path::PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .unwrap()
        .to_path_buf();
    let state = ProductState::open(paths.clone()).unwrap();
    assert!(ProductState::open(paths.clone()).is_err());
    let app = router(state.clone());
    let (_, registered) = request(
        &app,
        "POST",
        "/api/agent/workspaces",
        json!({"path":workspace}),
    )
    .await;
    let wid = registered["workspace"]["id"].as_str().unwrap();
    let invalid=request(&app,"POST","/api/agent/turns",json!({"workspace":wid,"session_id":"test","message":"inspect","mode":"steps","files":["../outside.txt"]})).await;
    assert_eq!(invalid.0, StatusCode::BAD_REQUEST);
    let (status,started)=request(&app,"POST","/api/agent/turns",json!({"workspace":wid,"session_id":"test","message":"inspect","mode":"steps","files":["brief.txt"]})).await;
    assert_eq!(status, StatusCode::ACCEPTED);
    let turn = started["turn_id"].as_str().unwrap();
    let url = format!("/api/agent/turns/{turn}/events?workspace={wid}&session_id=test");
    let mut result = Value::Null;
    for _ in 0..50 {
        result = request(&app, "GET", &url, Value::Null).await.1;
        if result["turn"]["status"] == "completed" {
            break;
        }
        tokio::time::sleep(Duration::from_millis(10)).await;
    }
    assert_eq!(result["turn"]["status"], "completed");
    assert!(result["events"]
        .as_array()
        .unwrap()
        .iter()
        .any(|e| e["kind"] == "tool_finished"));
    let last_seq = result["events"].as_array().unwrap().last().unwrap()["seq"]
        .as_u64()
        .unwrap();
    assert!(request(
        &app,
        "GET",
        &format!("{url}&after_seq={last_seq}"),
        Value::Null
    )
    .await
    .1["events"]
        .as_array()
        .unwrap()
        .is_empty());

    let captured = Arc::new(Mutex::new(Vec::<Value>::new()));
    let capture = captured.clone();
    let server=Router::new().route("/chat/completions",post(move |Json(payload):Json<Value>|{let capture=capture.clone();async move{
        capture.lock().unwrap().push(payload.clone());
        let messages=payload["messages"].as_array().unwrap();let is_child=messages[0]["content"].as_str().unwrap().starts_with("你是只读");
        let has_tool=messages.iter().any(|m|m["role"]=="tool");
        let message=if is_child{json!({"role":"assistant","content":"资料尚未核验，需读取brief.txt"})}
            else if !has_tool{json!({"role":"assistant","content":null,"tool_calls":[{"id":"call_delegate","type":"function","function":{"name":"delegate","arguments":json!({"tasks":[{"role":"evidence","goal":"Find source facts"},{"role":"review","goal":"Find missing inputs"}]}).to_string()}}]})}
            else{json!({"role":"assistant","content":"子任务已返回，资料仍需核验。"})};
        Json(json!({"model":"scripted","choices":[{"message":message,"finish_reason":if has_tool||is_child{"stop"}else{"tool_calls"}}],"usage":{"prompt_tokens":40,"completion_tokens":10}}))
    }}));
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let base_url = format!("http://{}", listener.local_addr().unwrap());
    let server = tokio::spawn(async move { axum::serve(listener, server).await.unwrap() });
    civil_workbench::config::set_runtime_llm(Some(LlmConfig {
        api_key: "scripted-only".into(),
        base_url,
        model: "scripted".into(),
    }));
    let (_,started)=request(&app,"POST","/api/agent/turns",json!({"workspace":wid,"session_id":"model","message":"Review selected source","mode":"model","files":["brief.txt"]})).await;
    let url = format!(
        "/api/agent/turns/{}/events?workspace={wid}&session_id=model",
        started["turn_id"].as_str().unwrap()
    );
    let deadline = tokio::time::Instant::now() + Duration::from_secs(60);
    while tokio::time::Instant::now() < deadline {
        result = request(&app, "GET", &url, Value::Null).await.1;
        if result["turn"]["status"] == "completed" || result["turn"]["status"] == "failed" {
            break;
        }
        tokio::time::sleep(Duration::from_millis(25)).await;
    }
    civil_workbench::config::set_runtime_llm(None);
    server.abort();
    assert_eq!(result["turn"]["status"], "completed", "{result}");
    assert_eq!(result["turn"]["result"]["usage"]["task_count"], 3);
    assert_eq!(result["turn"]["result"]["usage"]["model_calls"], 4);
    assert_eq!(captured.lock().unwrap().len(), 4);
    assert_eq!(
        result["events"]
            .as_array()
            .unwrap()
            .iter()
            .filter(|e| e["kind"] == "subtask_finished")
            .count(),
        2
    );
    // A restarted host can read completed events without replaying model calls.
    drop(app);
    drop(state);
    let reopened = ProductState::open(paths).unwrap();
    let app = router(reopened);
    assert_eq!(
        request(&app, "GET", &url, Value::Null).await.1["turn"]["status"],
        "completed"
    );
}
