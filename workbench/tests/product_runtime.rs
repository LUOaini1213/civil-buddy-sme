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

static SCRIPTED_MODEL_LOCK: Mutex<()> = Mutex::new(());

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
    let _model_guard = SCRIPTED_MODEL_LOCK.lock().unwrap();
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

#[tokio::test]
async fn full_completion_endpoint_and_base_url_use_the_same_provider_route() {
    let received = Arc::new(Mutex::new(0));
    let count = received.clone();
    let router = Router::new().route("/v1/chat/completions", post(move || {
        let count = count.clone();
        async move {
            *count.lock().unwrap() += 1;
            Json(json!({"choices":[{"message":{"role":"assistant","content":"Local fixture"},"finish_reason":"stop"}],
                "usage":{"prompt_tokens":5,"completion_tokens":3}}))
        }
    }));
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let base = format!("http://{}", listener.local_addr().unwrap());
    let server = tokio::spawn(async move { axum::serve(listener, router).await.unwrap() });
    for suffix in ["/v1", "/v1/", "/v1/chat/completions", "/v1/chat/completions/"] {
        let cfg = LlmConfig { api_key: "offline-only".into(), base_url: format!("{base}{suffix}"), model: "fixture".into() };
        let task = TaskId::new();
        let budget = BudgetTree::new(task.clone(), BudgetLimits::default()).unwrap();
        let response = providers::complete(&cfg, &[json!({"role":"user","content":"hello"})], &[], 100, 4096,
            &budget, &task, &CancellationToken::new()).await.unwrap();
        assert_eq!(response.message["content"], "Local fixture");
    }
    assert_eq!(*received.lock().unwrap(), 4);
    for suffix in ["/v1/chat/completions?token=fixture", "/v1#fragment"] {
        let cfg = LlmConfig { api_key: "offline-only".into(), base_url: format!("{base}{suffix}"), model: "fixture".into() };
        let task = TaskId::new();
        let budget = BudgetTree::new(task.clone(), BudgetLimits::default()).unwrap();
        assert!(providers::complete(&cfg, &[json!({"role":"user","content":"hello"})], &[], 100, 4096,
            &budget, &task, &CancellationToken::new()).await.is_err());
    }
    assert_eq!(*received.lock().unwrap(), 4, "Invalid addresses must be rejected before an HTTP request");
    server.abort();
}

#[tokio::test]
async fn final_publication_claims_require_registered_outputs_but_explanations_remain_available() {
    publication_cases(&["no_tools", "read_only", "failed_read", "failed_apply", "english", "done", "formatted_done", "emoji_done", "edit_done", "create_zh", "create_en", "explain", "conditional", "negative_explanation"]).await;
}

#[tokio::test]
async fn repair_wording_requires_receipts_and_keeps_explanations() {
    publication_cases(&["repair_hao", "repair_yixia", "repair_xiufu", "repair_xiuzheng", "repair_fix", "repair_correct", "repair_noop_zh", "repair_explain_zh", "repair_noop_en", "repair_negative_en", "repair_explain_en", "repair_explain_attachment", "repair_adjective_question", "repair_adjective_check", "repair_claim_zh", "repair_claim_en"]).await;
}

// Request, scripted reply, conversational response expected, explicit write request.
fn repair_case(scenario: &str) -> Option<(&'static str, &'static str, bool, bool)> {
    Some(match scenario {
        "repair_hao" => ("把错字改好", "Done.", false, true),
        "repair_yixia" => ("改一下", "Done.", false, true),
        "repair_xiufu" => ("修复附件", "Done.", false, true),
        "repair_xiuzheng" => ("修正附件", "Done.", false, true),
        "repair_fix" => ("Fix the typo in report.docx", "Done.", false, true),
        "repair_correct" => ("Correct the spreadsheet", "Done.", false, true),
        "repair_noop_zh" => ("不要修复附件，只解释原因", "我不会修改附件；以下解释修复流程。", true, false),
        "repair_explain_zh" => ("解释如何修复附件", "修复附件通常先检查文件结构。", true, false),
        "repair_noop_en" => ("Don't fix the file", "I have not fixed the document.", true, false),
        "repair_negative_en" => ("Don't fix the file", "I haven't corrected the document.", true, false),
        "repair_explain_en" => ("Explain how to correct the spreadsheet", "Correcting a spreadsheet starts with checking its structure.", true, false),
        "repair_explain_attachment" => ("Explain the attachment", "The attachment contains source material.", true, false),
        "repair_adjective_question" => ("Is this spreadsheet correct?", "The selected material needs further checks.", true, false),
        "repair_adjective_check" => ("Check whether the totals are correct.", "I need to inspect the source values first.", true, false),
        "repair_claim_zh" => ("解释附件结构，不修改文件。", "已修复附件。", false, false),
        "repair_claim_en" => ("Explain the selected file structure.", "I fixed the attachment.", false, false),
        _ => return None,
    })
}

async fn publication_cases(scenarios: &[&str]) {
    let _model_guard = SCRIPTED_MODEL_LOCK.lock().unwrap();
    struct ResetModel;
    impl Drop for ResetModel {
        fn drop(&mut self) { civil_workbench::config::set_runtime_llm(None); }
    }
    let _reset = ResetModel;
    let root = std::env::temp_dir().join(format!("civil-final-report-{}", uuid::Uuid::new_v4().simple()));
    let workspace = root.join("job");
    std::fs::create_dir_all(&workspace).unwrap();
    std::fs::write(workspace.join("brief.txt"), "Source quantity: 12 panels\n").unwrap();
    let mut paths = Paths::from_demo(root.join("demo"));
    paths.repo_root = std::path::PathBuf::from(env!("CARGO_MANIFEST_DIR")).parent().unwrap().to_path_buf();
    let state = ProductState::open(paths).unwrap();
    let wid = state.register(workspace.to_str().unwrap()).unwrap()["id"].as_str().unwrap().to_owned();
    let app = router(state.clone());
    let explanation = "保存副本会保留原件。请先预览差异，再保存副本。";
    let conditional = "如果文件已保存，界面会显示下载链接。For example, the document has been saved is a status message.";
    let router = Router::new().route("/chat/completions", post(move |Json(payload): Json<Value>| async move {
        let has_tool = payload["messages"].as_array().unwrap().iter().any(|m| m["role"] == "tool");
        let scenario = payload["model"].as_str().unwrap();
        let message = if !has_tool && matches!(scenario, "read_only" | "failed_read" | "failed_apply") {
            let (name, args) = if scenario == "failed_apply" {
                ("apply_document", json!({"preview_id":"unknown"}))
            } else {
                ("read_file", json!({"source":if scenario=="failed_read" {"unauthorized.txt"}else{"brief.txt"}}))
            };
            json!({"role":"assistant","content":null,"tool_calls":[{"id":"proof","type":"function","function":{"name":name,"arguments":args.to_string()}}]})
        } else {
            let text = if let Some((_, text, _, _)) = repair_case(scenario) {text} else {match scenario {
                "explain" => explanation,
                "conditional" => conditional,
                "done" | "create_en" => "Done.",
                "formatted_done" => "**Done.**",
                "emoji_done" => "✅ 已完成。",
                "edit_done" | "create_zh" => "完成了。",
                "negative_explanation" => "文件尚未保存成功，请检查目录权限。",
                "english" => "I have saved the report. All files have been generated with quantity 987654.",
                _ => "已将报告修改并保存为新副本，所有文件已生成，数量已经更新为 987654。",
            }};
            json!({"role":"assistant","content":text})
        };
        let finish = if message["tool_calls"].is_array() {"tool_calls"} else {"stop"};
        Json(json!({"choices":[{"message":message,"finish_reason":finish}],"usage":{"prompt_tokens":20,"completion_tokens":20}}))
    }));
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let base_url = format!("http://{}", listener.local_addr().unwrap());
    let server = tokio::spawn(async move { axum::serve(listener, router).await.unwrap() });
    for &scenario in scenarios {
        civil_workbench::config::set_runtime_llm(Some(LlmConfig { api_key:"offline-only".into(), base_url:base_url.clone(), model:scenario.into() }));
        let repair = repair_case(scenario);
        let conversational = repair.map(|case| case.2).unwrap_or(matches!(scenario, "explain" | "conditional" | "negative_explanation"));
        let requested_publication = repair.map(|case| case.3).unwrap_or(!conversational);
        let task = if let Some((task, _, _, _)) = repair {task}
            else if scenario == "negative_explanation" {"为什么没有保存成功？"}
            else if conversational {"解释如何保存副本，不要执行。"}
            else if scenario == "edit_done" {"把 brief.txt 的标题改成 X。"}
            else if scenario == "create_zh" {"帮我生成一份报告。"}
            else if scenario == "create_en" {"Create a report.docx"}
            else {"请修改所选资料并保存新副本。"};
        let files = if matches!(scenario, "create_zh" | "create_en") {json!([])}else{json!(["brief.txt"])};
        let (status, started) = request(&app, "POST", "/api/agent/turns", json!({"workspace":wid,"session_id":scenario,
            "message":task,"mode":"model","sandbox":"workspace-write","files":files})).await;
        assert_eq!(status, StatusCode::ACCEPTED, "{started}");
        let url = format!("/api/agent/turns/{}?workspace={wid}&session_id={scenario}",started["turn_id"].as_str().unwrap());
        let deadline = tokio::time::Instant::now() + Duration::from_secs(60);
        let mut record = Value::Null;
        while tokio::time::Instant::now() < deadline {
            record = request(&app,"GET",&url,Value::Null).await.1;
            if !matches!(record["turn"]["status"].as_str(), Some("running" | "cancelling")) { break; }
            tokio::time::sleep(Duration::from_millis(25)).await;
        }
        assert_eq!(record["turn"]["status"], "completed", "{scenario}: {record}");
        let result = &record["turn"]["result"];
        assert!(result["artifacts"].as_array().unwrap().is_empty());
        assert_eq!(result["execution_evidence"]["registered_documents"], 0);
        assert_eq!(result["execution_evidence"]["requested_task_complete"], if conversational {Value::Null}else{json!(false)}, "{scenario}: {result}");
        assert_eq!(result["execution_evidence"]["requested_document_publication"], requested_publication, "{scenario}: {result}");
        assert_eq!(result["partial"], !conversational, "{scenario}: {result}");
        let reply = result["reply"].as_str().unwrap();
        if conversational {
            assert_eq!(reply, repair.map(|case| case.1).unwrap_or(match scenario { "explain" => explanation, "negative_explanation" => "文件尚未保存成功，请检查目录权限。", _ => conditional }));
            assert_eq!(result["execution_evidence"]["publication_summary_from_receipts"], false);
        } else {
            assert!(reply.contains("本轮没有成功保存"), "{scenario}: {reply}");
            assert!(!reply.contains("987654"));
            assert!(!reply.contains("所有文件已生成"));
            assert!(!reply.contains("I have saved"));
        }
    }
    server.abort();
    assert_eq!(std::fs::read_to_string(workspace.join("brief.txt")).unwrap(), "Source quantity: 12 panels\n");
    drop(app); drop(state);
    std::fs::remove_dir_all(root).unwrap();
}
