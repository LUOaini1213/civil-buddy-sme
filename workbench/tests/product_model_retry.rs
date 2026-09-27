//! Transient model-provider failures (429 / 5xx / timeouts / dropped
//! connections) are retried a bounded number of times, but only before any
//! tool has run in the turn. Every server here is a local scripted fake.
use axum::{
    body::Body,
    http::{Request, StatusCode},
    response::IntoResponse,
    routing::post,
    Json, Router,
};
use civil_workbench::{
    config::{LlmConfig, Paths},
    product::{
        api::{router, ProductState},
        providers,
    },
    runtime_core::{BudgetLimits, BudgetTree, CancellationToken, TaskId},
};
use http_body_util::BodyExt;
use serde_json::{json, Value};
use std::{
    collections::VecDeque,
    sync::{Arc, Mutex},
    time::{Duration, Instant},
};
use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tower::ServiceExt;

#[derive(Clone)]
enum Step {
    Status(u16, Option<&'static str>),
    Reply(Value),
}

fn completion(message: Value) -> Value {
    let finish = if message["tool_calls"].is_array() {
        "tool_calls"
    } else {
        "stop"
    };
    json!({"model":"scripted","choices":[{"message":message,"finish_reason":finish}],"usage":{"prompt_tokens":10,"completion_tokens":2}})
}

/// A fake Chat Completions endpoint that answers each request with the next
/// scripted step and counts every request it receives.
async fn scripted(script: Vec<Step>) -> (String, Arc<Mutex<usize>>, tokio::task::JoinHandle<()>) {
    let queue = Arc::new(Mutex::new(VecDeque::from(script)));
    let hits = Arc::new(Mutex::new(0usize));
    let counter = hits.clone();
    let app = Router::new().route(
        "/chat/completions",
        post(move |Json(_payload): Json<Value>| {
            let queue = queue.clone();
            let counter = counter.clone();
            async move {
                *counter.lock().unwrap() += 1;
                let step = queue.lock().unwrap().pop_front();
                match step {
                    Some(Step::Status(status, retry_after)) => {
                        let mut response = (
                            StatusCode::from_u16(status).unwrap(),
                            Json(json!({"error":{"message":"scripted failure"}})),
                        )
                            .into_response();
                        if let Some(value) = retry_after {
                            response
                                .headers_mut()
                                .insert("retry-after", value.parse().unwrap());
                        }
                        response
                    }
                    Some(Step::Reply(message)) => Json(completion(message)).into_response(),
                    None => (StatusCode::GONE, "script exhausted").into_response(),
                }
            }
        }),
    );
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let base = format!("http://{}", listener.local_addr().unwrap());
    let server = tokio::spawn(async move { axum::serve(listener, app).await.unwrap() });
    (base, hits, server)
}

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

async fn run_turn(app: &Router, wid: &str, session: &str, base_url: String) -> Value {
    civil_workbench::config::set_runtime_llm(Some(LlmConfig {
        api_key: "scripted-only".into(),
        base_url,
        model: "scripted".into(),
    }));
    let (status, started) = request(
        app,
        "POST",
        "/api/agent/turns",
        json!({"workspace":wid,"session_id":session,"message":"Review the selected brief","mode":"model","files":["brief.txt"]}),
    )
    .await;
    assert_eq!(status, StatusCode::ACCEPTED, "{started}");
    let url = format!(
        "/api/agent/turns/{}/events?workspace={wid}&session_id={session}",
        started["turn_id"].as_str().unwrap()
    );
    let deadline = tokio::time::Instant::now() + Duration::from_secs(90);
    let mut result = Value::Null;
    while tokio::time::Instant::now() < deadline {
        result = request(app, "GET", &url, Value::Null).await.1;
        if matches!(
            result["turn"]["status"].as_str(),
            Some("completed" | "failed" | "cancelled")
        ) {
            break;
        }
        tokio::time::sleep(Duration::from_millis(25)).await;
    }
    civil_workbench::config::set_runtime_llm(None);
    result
}

/// One test function: the runtime LLM configuration is process-global.
#[tokio::test]
async fn turn_retries_transient_model_failures_only_before_any_tool_runs() {
    let root = std::env::temp_dir().join(format!(
        "civil-retry-test-{}",
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
    let app = router(ProductState::open(paths).unwrap());
    let (_, registered) = request(
        &app,
        "POST",
        "/api/agent/workspaces",
        json!({"path":workspace}),
    )
    .await;
    let wid = registered["workspace"]["id"].as_str().unwrap().to_owned();
    let answer = json!({"role":"assistant","content":"The brief lists 12 panels; the quantity still needs checking."});

    // A 5xx before any tool: retried, and the turn completes.
    let (base, hits, server) =
        scripted(vec![Step::Status(500, None), Step::Reply(answer.clone())]).await;
    let result = run_turn(&app, &wid, "transient", base).await;
    server.abort();
    println!(
        "5xx-then-ok: status={} requests={}",
        result["turn"]["status"],
        hits.lock().unwrap()
    );
    assert_eq!(result["turn"]["status"], "completed", "{result}");
    assert_eq!(*hits.lock().unwrap(), 2);
    assert_eq!(result["turn"]["result"]["usage"]["model_calls"], 2);
    assert!(
        result["events"]
            .as_array()
            .unwrap()
            .iter()
            .any(|e| e["kind"] == "status" && e["data"]["phase"] == "model_retry"),
        "{result}"
    );

    // Rate limited on every attempt: 1 + 2 retries, then an honest message.
    let (base, hits, server) = scripted(vec![
        Step::Status(429, Some("0")),
        Step::Status(429, Some("0")),
        Step::Status(429, Some("0")),
    ])
    .await;
    let result = run_turn(&app, &wid, "limited", base).await;
    server.abort();
    let error = result["turn"]["result"]["error"]
        .as_str()
        .unwrap_or("")
        .to_owned();
    println!(
        "429x3: status={} requests={} error={error}",
        result["turn"]["status"],
        hits.lock().unwrap()
    );
    assert_eq!(result["turn"]["status"], "failed", "{result}");
    assert_eq!(*hits.lock().unwrap(), 3);
    assert!(error.contains("rate-limited"), "{error}");
    assert!(!error.contains("check configuration"), "{error}");

    // After a tool has run, a failing model call is never retried.
    let tool_call = json!({"role":"assistant","content":null,"tool_calls":[{"id":"call_read","type":"function","function":{"name":"read_file","arguments":json!({"source":"brief.txt","operation":"inspect"}).to_string()}}]});
    let (base, hits, server) = scripted(vec![
        Step::Reply(tool_call),
        Step::Status(503, Some("0")),
        Step::Reply(answer),
    ])
    .await;
    let result = run_turn(&app, &wid, "after-tool", base).await;
    server.abort();
    println!(
        "tool-then-503: status={} requests={}",
        result["turn"]["status"],
        hits.lock().unwrap()
    );
    assert!(
        result["events"]
            .as_array()
            .unwrap()
            .iter()
            .any(|e| e["kind"] == "tool_finished"),
        "{result}"
    );
    assert_eq!(result["turn"]["status"], "failed", "{result}");
    assert_eq!(*hits.lock().unwrap(), 2);
}

fn fast() -> providers::RetryPolicy {
    providers::RetryPolicy {
        max_retries: 2,
        base_delay: Duration::from_millis(20),
        max_delay: Duration::from_secs(8),
    }
}

struct Call {
    result: Result<providers::Completion, providers::ProviderError>,
    notices: Vec<providers::RetryNotice>,
    elapsed: Duration,
    budget: BudgetTree,
}

async fn call(
    base_url: String,
    limits: BudgetLimits,
    policy: providers::RetryPolicy,
    cancel: CancellationToken,
) -> Call {
    let cfg = LlmConfig {
        api_key: "scripted-key".into(),
        base_url,
        model: "scripted".into(),
    };
    let task = TaskId::new();
    let budget = BudgetTree::new(task.clone(), limits).unwrap();
    let mut notices = Vec::new();
    let started = Instant::now();
    let result = providers::complete_with_retry(
        &cfg,
        &[json!({"role":"user","content":"Read"})],
        &[],
        100,
        4096,
        &budget,
        &task,
        &cancel,
        &policy,
        |notice| notices.push(notice.clone()),
    )
    .await;
    Call {
        result,
        notices,
        elapsed: started.elapsed(),
        budget,
    }
}

fn ok_reply() -> Step {
    Step::Reply(json!({"role":"assistant","content":"evidence read"}))
}

#[tokio::test]
async fn server_errors_are_retried_with_a_fresh_reservation_per_attempt() {
    let (base, hits, server) = scripted(vec![
        Step::Status(500, None),
        Step::Status(502, None),
        ok_reply(),
    ])
    .await;
    let c = call(
        base,
        BudgetLimits::default(),
        fast(),
        CancellationToken::new(),
    )
    .await;
    server.abort();
    println!(
        "500,502,200: ok={} attempts={} elapsed_ms={}",
        c.result.is_ok(),
        hits.lock().unwrap(),
        c.elapsed.as_millis()
    );
    let completion = c.result.unwrap();
    assert_eq!(completion.usage.input_tokens, 10);
    assert_eq!(*hits.lock().unwrap(), 3);
    assert_eq!(c.notices.len(), 2);
    assert!(
        c.notices
            .iter()
            .all(|n| n.delay <= Duration::from_millis(40)),
        "{:?}",
        c.notices
    );
    let snapshot = c.budget.snapshot().unwrap();
    assert_eq!(snapshot.model_calls, 3);
    assert_eq!(snapshot.reserved_tokens, 0);
    // A rejected attempt generated no output: its call and estimated input stay
    // charged, its unused output reservation does not.
    let [first, second, last] = &snapshot.settlements[..] else {
        panic!("{:?}", snapshot.settlements)
    };
    for failed in [first, second] {
        assert_eq!(
            (failed.model_calls, failed.output_tokens, failed.estimated),
            (1, 0, true)
        );
        assert!(failed.input_tokens > 0);
    }
    assert_eq!(
        (last.input_tokens, last.output_tokens, last.estimated),
        (10, 2, false)
    );
}

#[tokio::test]
async fn rate_limit_honours_retry_after_and_says_it_is_not_configuration() {
    let (base, hits, server) = scripted(vec![Step::Status(429, Some("1")), ok_reply()]).await;
    let c = call(
        base,
        BudgetLimits::default(),
        providers::RetryPolicy::default(),
        CancellationToken::new(),
    )
    .await;
    server.abort();
    println!(
        "429(Retry-After 1),200: ok={} attempts={} elapsed_ms={}",
        c.result.is_ok(),
        hits.lock().unwrap(),
        c.elapsed.as_millis()
    );
    assert!(c.result.is_ok());
    assert_eq!(*hits.lock().unwrap(), 2);
    assert_eq!(c.notices[0].delay, Duration::from_secs(1));
    assert!(
        c.elapsed >= Duration::from_secs(1) && c.elapsed < Duration::from_secs(4),
        "{:?}",
        c.elapsed
    );
    assert!(
        c.notices[0].reason.contains("rate-limited"),
        "{}",
        c.notices[0].reason
    );

    // A wait longer than the cap is not shortened: no retry, and the message
    // passes the provider's wait on instead of blaming the configuration.
    let (base, hits, server) = scripted(vec![Step::Status(429, Some("30")), ok_reply()]).await;
    let c = call(
        base,
        BudgetLimits::default(),
        fast(),
        CancellationToken::new(),
    )
    .await;
    server.abort();
    let message = c.result.unwrap_err().to_string();
    println!(
        "429(Retry-After 30): attempts={} error={message}",
        hits.lock().unwrap()
    );
    assert_eq!(*hits.lock().unwrap(), 1);
    assert!(
        message.contains("rate-limited") && message.contains("30 s"),
        "{message}"
    );
    assert!(!message.contains("check configuration"), "{message}");
}

#[tokio::test]
async fn client_errors_are_not_retried() {
    let (base, hits, server) = scripted(vec![Step::Status(400, None), ok_reply()]).await;
    let c = call(
        base,
        BudgetLimits::default(),
        fast(),
        CancellationToken::new(),
    )
    .await;
    server.abort();
    println!("400: attempts={}", hits.lock().unwrap());
    assert!(matches!(c.result, Err(providers::ProviderError::Http(400))));
    assert_eq!(*hits.lock().unwrap(), 1);
    assert!(c.notices.is_empty());
    assert_eq!(c.budget.snapshot().unwrap().model_calls, 1);
}

#[tokio::test]
async fn cancellation_interrupts_the_backoff_sleep() {
    let (base, hits, server) = scripted(vec![Step::Status(503, Some("8")), ok_reply()]).await;
    let cancel = CancellationToken::new();
    let token = cancel.clone();
    tokio::spawn(async move {
        tokio::time::sleep(Duration::from_millis(150)).await;
        token.cancel();
    });
    let c = call(
        base,
        BudgetLimits::default(),
        providers::RetryPolicy::default(),
        cancel,
    )
    .await;
    server.abort();
    println!(
        "503(Retry-After 8)+cancel@150ms: attempts={} elapsed_ms={}",
        hits.lock().unwrap(),
        c.elapsed.as_millis()
    );
    assert_eq!(c.notices.len(), 1);
    assert_eq!(c.notices[0].delay, Duration::from_secs(8));
    assert!(matches!(
        c.result,
        Err(providers::ProviderError::Runtime(_))
    ));
    assert_eq!(*hits.lock().unwrap(), 1);
    assert!(c.elapsed < Duration::from_secs(2), "{:?}", c.elapsed);
}

#[tokio::test]
async fn a_retry_never_runs_past_the_task_deadline() {
    let (base, hits, server) = scripted(vec![Step::Status(503, Some("1")), ok_reply()]).await;
    let limits = BudgetLimits {
        timeout_ms: 3_000,
        ..BudgetLimits::default()
    };
    let c = call(base, limits, fast(), CancellationToken::new()).await;
    server.abort();
    assert!(matches!(
        c.result,
        Err(providers::ProviderError::Unavailable { status: 503, .. })
    ));
    assert_eq!(*hits.lock().unwrap(), 1);
    assert!(c.notices.is_empty());
}

#[tokio::test]
async fn a_hung_attempt_is_cut_at_the_task_deadline_and_not_retried() {
    let app = Router::new().route(
        "/chat/completions",
        post(|| async {
            tokio::time::sleep(Duration::from_secs(60)).await;
            Json(json!({}))
        }),
    );
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let base = format!("http://{}", listener.local_addr().unwrap());
    let server = tokio::spawn(async move { axum::serve(listener, app).await.unwrap() });
    let limits = BudgetLimits {
        timeout_ms: 6_000,
        ..BudgetLimits::default()
    };
    let c = call(base, limits, fast(), CancellationToken::new()).await;
    server.abort();
    println!(
        "hang with 6 s deadline: elapsed_ms={}",
        c.elapsed.as_millis()
    );
    assert!(matches!(c.result, Err(providers::ProviderError::Transport)));
    assert!(c.notices.is_empty());
    assert!(
        c.elapsed >= Duration::from_secs(5) && c.elapsed < Duration::from_secs(9),
        "{:?}",
        c.elapsed
    );
}

#[tokio::test]
async fn dropped_connections_are_retried() {
    // Raw TCP: the first two connections are closed without any response.
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let base = format!("http://{}", listener.local_addr().unwrap());
    let accepted = Arc::new(Mutex::new(0usize));
    let counter = accepted.clone();
    let server = tokio::spawn(async move {
        loop {
            let (mut socket, _) = listener.accept().await.unwrap();
            let n = {
                let mut count = counter.lock().unwrap();
                *count += 1;
                *count
            };
            let mut request = Vec::new();
            let mut buf = [0u8; 4096];
            loop {
                let read = socket.read(&mut buf).await.unwrap_or(0);
                if read == 0 {
                    break;
                }
                request.extend_from_slice(&buf[..read]);
                let text = String::from_utf8_lossy(&request).to_string();
                if let Some(end) = text.find("\r\n\r\n") {
                    let length = text[..end]
                        .lines()
                        .find_map(|line| {
                            line.to_ascii_lowercase()
                                .strip_prefix("content-length:")
                                .map(|v| v.trim().parse::<usize>().unwrap_or(0))
                        })
                        .unwrap_or(0);
                    if request.len() >= end + 4 + length {
                        break;
                    }
                }
            }
            if n <= 2 {
                drop(socket);
                continue;
            }
            let body =
                completion(json!({"role":"assistant","content":"evidence read"})).to_string();
            let response = format!(
                "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}",
                body.len()
            );
            socket.write_all(response.as_bytes()).await.unwrap();
            socket.shutdown().await.ok();
        }
    });
    let c = call(
        base,
        BudgetLimits::default(),
        fast(),
        CancellationToken::new(),
    )
    .await;
    server.abort();
    println!(
        "drop,drop,200: ok={} connections={}",
        c.result.is_ok(),
        accepted.lock().unwrap()
    );
    assert!(c.result.is_ok(), "{:?}", c.result.err());
    assert_eq!(*accepted.lock().unwrap(), 3);
    assert_eq!(c.budget.snapshot().unwrap().model_calls, 3);
}
