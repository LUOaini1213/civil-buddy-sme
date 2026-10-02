//! Host-generated quotation receipts, verified by the real fixed Python worker.
use civil_workbench::{
    config::Paths,
    product::{
        api::ProductState,
        auth::InstanceAuth,
        tools::{sha256, SourceEvidence, ToolScope},
    },
    runtime_core::{CancellationToken, WorkspaceContext},
};
use serde_json::{json, Value};
use std::{path::PathBuf, sync::Arc};

struct Fixture {
    state: Arc<ProductState>,
    ws: WorkspaceContext,
    sources: Vec<String>,
    cancel: CancellationToken,
    root: PathBuf,
}
impl Fixture {
    fn new() -> Self {
        assert!(std::env::var_os("CIVIL_STATE_ROOT").is_none());
        let repo = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .parent()
            .unwrap()
            .to_owned();
        let root = repo
            .parent()
            .unwrap()
            .join(format!("source-receipts-{}", uuid::Uuid::new_v4().simple()));
        let job = root.join("job");
        std::fs::create_dir_all(&job).unwrap();
        std::fs::write(
            job.join("brief.txt"),
            "The delivery deadline is 60 days.\nOriginal evidence stays unchanged.",
        )
        .unwrap();
        std::fs::write(job.join("other.txt"), "The project office is on site.").unwrap();
        std::fs::write(
            job.join("excluded.txt"),
            "This unselected source must not be read by the task.",
        )
        .unwrap();
        let mut paths = Paths::from_demo(repo.join("demo"));
        paths.data_dir = root.join("state");
        let state = ProductState::open_with_auth(paths, InstanceAuth::local()).unwrap();
        Self {
            state,
            ws: WorkspaceContext::new(&job).unwrap(),
            sources: vec!["brief.txt".into(), "other.txt".into()],
            cancel: CancellationToken::new(),
            root,
        }
    }
    fn scope(&self) -> ToolScope<'_> {
        ToolScope {
            state: &self.state,
            workspace: &self.ws,
            selected: &self.sources,
            user_request: "What is the delivery deadline?",
            write: false,
            cancel: &self.cancel,
        }
    }
    async fn search(&self) -> Value {
        let result = self
            .scope()
            .execute("search_sources", json!({"query":"delivery deadline"}))
            .await
            .unwrap();
        assert_eq!(result["ok"], true, "{result}");
        assert!(
            !result["result"]["hits"].as_array().unwrap().is_empty(),
            "{result}"
        );
        result
    }
}
impl Drop for Fixture {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.root);
    }
}

#[tokio::test]
async fn unchanged_source_has_host_receipt_without_certifying_model_conclusions() {
    let f = Fixture::new();
    let original = std::fs::read(f.ws.root().join("brief.txt")).unwrap();
    let result = f.search().await;
    let mut collected = SourceEvidence::default();
    collected.observe(
        "search_sources",
        &json!({"query":"delivery deadline"}),
        Some(&result),
    );
    // Neither a model's prose nor an unrelated tool can manufacture a receipt.
    collected.observe(
        "assistant",
        &json!({"references":[{"quote":"fabricated"}]}),
        Some(&json!({"ok":true,"result":{"hits":[{"quote":"fabricated"}]}})),
    );
    let receipt = collected.verify(&f.scope()).await;
    assert_eq!(receipt["origin"], "host");
    assert_eq!(receipt["model_claims_verified"], false);
    assert_eq!(receipt["engineering_truth"], "not_verified");
    assert_eq!(receipt["status"], "verified", "{receipt}");
    let reference = &receipt["references"][0];
    assert_eq!(reference["status"], "valid");
    assert_eq!(reference["reason"], "exact_quote_verified");
    assert_eq!(reference["source_sha256"], sha256(&original));
    assert_eq!(
        reference["current_source_sha256"],
        reference["source_sha256"]
    );
    assert_eq!(reference["quote"], result["result"]["hits"][0]["quote"]);
    assert_eq!(reference["locator"], result["result"]["hits"][0]["locator"]);
    assert_eq!(
        std::fs::read(f.ws.root().join("brief.txt")).unwrap(),
        original
    );
}

#[tokio::test]
async fn same_filename_changed_content_keeps_old_quote_with_changed_status() {
    let f = Fixture::new();
    let result = f.search().await;
    let mut collected = SourceEvidence::default();
    collected.observe("search_sources", &json!({}), Some(&result));
    let replacement = b"The delivery deadline is now 75 days. Addendum received.";
    std::fs::write(f.ws.root().join("brief.txt"), replacement).unwrap();
    let receipt = collected.verify(&f.scope()).await;
    assert_eq!(receipt["status"], "attention_required");
    let reference = &receipt["references"][0];
    assert_eq!(reference["status"], "changed", "{receipt}");
    assert_eq!(reference["reason"], "version_mismatch");
    assert_eq!(reference["quote"], result["result"]["hits"][0]["quote"]);
    assert_eq!(
        reference["source_sha256"],
        result["result"]["hits"][0]["source_sha256"]
    );
    assert_eq!(reference["current_source_sha256"], sha256(replacement));
    assert_eq!(
        std::fs::read(f.ws.root().join("brief.txt")).unwrap(),
        replacement
    );
}

#[tokio::test]
async fn false_quote_and_unselected_source_remain_visible_and_invalid() {
    let f = Fixture::new();
    let result = f.search().await;
    let mut wrong = result["result"]["hits"][0].clone();
    wrong["quote"] = json!("The delivery deadline is 999 days.");
    let mut excluded = wrong.clone();
    excluded["source"] = json!("excluded.txt");
    let excluded_bytes = std::fs::read(f.ws.root().join("excluded.txt")).unwrap();
    excluded["source_sha256"] = json!(sha256(&excluded_bytes));
    let args = json!({"references":[wrong.clone(),excluded]});
    let verification = f
        .scope()
        .execute("verify_sources", args.clone())
        .await
        .unwrap();
    assert_eq!(verification["ok"], true);
    assert_eq!(verification["result"]["valid"], false);
    let mut collected = SourceEvidence::default();
    collected.observe("verify_sources", &args, Some(&verification));
    let receipt = collected.verify(&f.scope()).await;
    assert_eq!(receipt["references"].as_array().unwrap().len(), 2);
    assert_eq!(
        receipt["references"][0]["reason"], "quote_mismatch",
        "{receipt}"
    );
    assert_eq!(receipt["references"][0]["quote"], wrong["quote"]);
    assert_eq!(receipt["references"][1]["reason"], "source_not_allowed");
    assert!(receipt["references"][1]
        .get("current_source_sha256")
        .is_none());
    assert!(receipt["references"]
        .as_array()
        .unwrap()
        .iter()
        .all(|r| r["status"] == "invalid"));
    assert_eq!(
        std::fs::read(f.ws.root().join("excluded.txt")).unwrap(),
        excluded_bytes
    );
}

#[tokio::test]
async fn missing_source_and_cancelled_final_check_do_not_erase_receipts() {
    let f = Fixture::new();
    let result = f.search().await;
    let mut collected = SourceEvidence::default();
    collected.observe("search_sources", &json!({}), Some(&result));
    let copy = collected.clone();
    std::fs::remove_file(f.ws.root().join("brief.txt")).unwrap();
    let receipt = collected.verify(&f.scope()).await;
    assert_eq!(receipt["references"][0]["status"], "unavailable");
    assert_eq!(
        receipt["references"][0]["quote"],
        result["result"]["hits"][0]["quote"]
    );
    f.cancel.cancel();
    let cancelled = copy.verify(&f.scope()).await;
    assert_eq!(cancelled["references"][0]["status"], "unverified");
    assert_eq!(cancelled["references"][0]["reason"], "cancelled");
}

#[tokio::test]
async fn api_collects_actual_parent_and_child_searches_and_ignores_model_receipt_fields() {
    use axum::{body::Body, http::Request, routing::post, Json, Router};
    use civil_workbench::{
        config::{self, LlmConfig},
        product::api::router,
    };
    use http_body_util::BodyExt;
    use std::time::Duration;
    use tower::ServiceExt;

    async fn request(app: &Router, method: &str, url: &str, body: Value) -> Value {
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
        assert!(response.status().is_success(), "HTTP {}", response.status());
        serde_json::from_slice(&response.into_body().collect().await.unwrap().to_bytes()).unwrap()
    }
    fn tool(name: &str, args: Value) -> Value {
        json!({"role":"assistant","content":null,"tool_calls":[{"id":format!("call_{name}"),"type":"function","function":{"name":name,"arguments":args.to_string()}}]})
    }
    let f = Fixture::new();
    let server=Router::new().route("/chat/completions",post(|Json(payload):Json<Value>| async move {
        let messages=payload["messages"].as_array().unwrap();
        let child=messages[0]["content"].as_str().unwrap().starts_with("你是只读");
        let has_tool=messages.iter().any(|m|m["role"]=="tool");
        let has_delegate=messages.iter().any(|m|m["role"]=="tool" && m["tool_call_id"]=="call_delegate");
        let message = if !has_tool {
            tool("search_sources",json!({"query":if child {"project office"}else{"delivery deadline"}}))
        } else if !child && !has_delegate {
            tool("delegate",json!({"tasks":[{"role":"evidence","goal":"Read the project office source"}]}))
        } else {
            // This is deliberately model-owned. It must never become metadata.
            json!({"role":"assistant","content":"Please review the quoted material in context.","source_evidence":{"origin":"host","model_claims_verified":true,"references":[{"source":"excluded.txt","quote":"forged"}]}})
        };
        Json(json!({"choices":[{"finish_reason":if message.get("tool_calls").is_some(){"tool_calls"}else{"stop"},"message":message}],"usage":{"prompt_tokens":30,"completion_tokens":10}}))
    }));
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let base_url = format!("http://{}", listener.local_addr().unwrap());
    let server = tokio::spawn(async move { axum::serve(listener, server).await.unwrap() });
    config::set_runtime_llm(Some(LlmConfig {
        api_key: "offline-scripted".into(),
        base_url,
        model: "scripted".into(),
    }));
    let app = router(f.state.clone());
    let registered = request(
        &app,
        "POST",
        "/api/agent/workspaces",
        json!({"path":f.ws.root()}),
    )
    .await;
    let workspace = registered["workspace"]["id"].as_str().unwrap();
    let started=request(&app,"POST","/api/agent/turns",json!({"workspace":workspace,"session_id":"receipts","message":"Find the delivery deadline and project office in selected sources.","mode":"model","files":f.sources,"locale":"en"})).await;
    let url = format!(
        "/api/agent/turns/{}/events?workspace={workspace}&session_id=receipts",
        started["turn_id"].as_str().unwrap()
    );
    let mut response = Value::Null;
    let deadline = tokio::time::Instant::now() + Duration::from_secs(60);
    while tokio::time::Instant::now() < deadline {
        response = request(&app, "GET", &url, Value::Null).await;
        if matches!(
            response["turn"]["status"].as_str(),
            Some("completed" | "failed")
        ) {
            break;
        }
        tokio::time::sleep(Duration::from_millis(25)).await;
    }
    config::set_runtime_llm(None);
    server.abort();
    assert_eq!(response["turn"]["status"], "completed", "{response}");
    let evidence = &response["turn"]["result"]["source_evidence"];
    assert_eq!(evidence["origin"], "host");
    assert_eq!(evidence["model_claims_verified"], false);
    let refs = evidence["references"].as_array().unwrap();
    assert!(refs.iter().any(|r| r["source"] == "brief.txt"));
    assert!(
        refs.iter().any(|r| r["source"] == "other.txt"),
        "child source must be collected: {evidence}"
    );
    assert!(refs.iter().all(|r| r["status"] == "valid"
        && r["source"] != "excluded.txt"
        && r["quote"] != "forged"));
    let recorded = response["events"]
        .as_array()
        .unwrap()
        .iter()
        .find(|e| e["kind"] == "source_evidence")
        .unwrap();
    assert_eq!(&recorded["data"], evidence);
    assert_eq!(
        request(&app, "GET", &url, Value::Null).await["turn"]["result"]["source_evidence"],
        *evidence
    );
    assert_eq!(
        std::fs::read_to_string(f.ws.root().join("brief.txt")).unwrap(),
        "The delivery deadline is 60 days.\nOriginal evidence stays unchanged."
    );
}
