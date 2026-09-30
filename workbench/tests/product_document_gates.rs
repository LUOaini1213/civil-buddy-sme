//! Real worker tests for publication gates. No provider or model calls.
use civil_workbench::{
    config::Paths,
    product::{
        api::ProductState,
        tools::{sha256, ToolScope},
    },
    runtime_core::{CancellationToken, WorkspaceContext},
};
use serde_json::{json, Value};
use std::{
    path::{Path, PathBuf},
    process::Command,
    sync::Arc,
};

// The runtime model override is process-wide; scripted loop tests must not
// replace another test's provider while its turn is still running.
static SCRIPTED_MODEL_LOCK: std::sync::Mutex<()> = std::sync::Mutex::new(());

struct Fixture {
    state: Arc<ProductState>,
    workspace: WorkspaceContext,
    selected: Vec<String>,
    cancel: CancellationToken,
}

impl Fixture {
    fn new() -> Self {
        assert!(
            std::env::var_os("CIVIL_STATE_ROOT").is_none(),
            "Unset CIVIL_STATE_ROOT for these tests; they must use isolated state"
        );
        let repo = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .parent()
            .unwrap()
            .to_path_buf();
        let root = repo
            .parent()
            .unwrap()
            .join(format!("document-gates-{}", uuid::Uuid::new_v4().simple()));
        let job = root.join("job");
        let python = std::env::var_os("CIVIL_PYTHON").unwrap_or_else(|| "python".into());
        let script = "from pathlib import Path; import sys; from scripts.unified_acceptance import fixture; from scripts.test_document_worker import pdf_fixture; p=Path(sys.argv[1]); fixture(p); (p/'form.pdf').write_bytes(pdf_fixture())";
        let made = Command::new(python)
            .args(["-B", "-c", script])
            .arg(&job)
            .current_dir(&repo)
            .env("PYTHONUTF8", "1")
            .output()
            .unwrap();
        assert!(
            made.status.success(),
            "fixture: {}",
            String::from_utf8_lossy(&made.stderr)
        );
        let mut paths = Paths::from_demo(repo.join("demo"));
        paths.data_dir = root.join("state");
        let state = ProductState::open(paths).unwrap();
        Self {
            state,
            workspace: WorkspaceContext::new(&job).unwrap(),
            selected: vec![
                "requirements.pdf".into(),
                "quantities.xlsx".into(),
                "report.docx".into(),
                "form.pdf".into(),
            ],
            cancel: CancellationToken::new(),
        }
    }
    fn scope(&self, request: &'static str, write: bool) -> ToolScope<'_> {
        ToolScope {
            state: &self.state,
            workspace: &self.workspace,
            selected: &self.selected,
            user_request: request,
            write,
            cancel: &self.cancel,
        }
    }
    fn args(&self, source: &str, patch: Value) -> Value {
        json!({"source":source,"expected_sha256":sha256(&std::fs::read(self.workspace.root().join(source)).unwrap()),"patches":[patch]})
    }
    fn no_documents_published(&self) {
        fn documents(path: &Path) -> usize {
            if !path.is_dir() {
                return 0;
            }
            std::fs::read_dir(path)
                .unwrap()
                .map(|entry| {
                    let path = entry.unwrap().path();
                    if path.is_dir() {
                        documents(&path)
                    } else {
                        usize::from(matches!(
                            path.extension().and_then(|v| v.to_str()),
                            Some("docx" | "xlsx" | "pdf")
                        ))
                    }
                })
                .sum()
        }
        assert_eq!(
            documents(&self.workspace.output_root()),
            0,
            "rejected or preview-only operation published a document"
        );
    }
}

fn word(text: &str) -> Value {
    json!({"op":"replace_paragraph","paragraph_id":"p:1","expected_text":"sample_count=4","text":text})
}
fn cell(kind: &str, value: Value) -> Value {
    json!({"op":"set_cell","sheet":"Counts","cell":"B2","expected":{"type":"number","value":4},"value":{"type":kind,"value":value}})
}
fn rejected(result: Result<Value, String>) {
    assert!(
        result.as_ref().is_err() || result.as_ref().is_ok_and(|v| v["ok"] == false),
        "must reject, got {result:?}"
    );
}

#[tokio::test]
async fn novel_numbers_without_evidence_are_rejected_for_word_excel_and_pdf() {
    let f = Fixture::new();
    let scope = f.scope("核对资料后修改新副本", true);
    for (source, patch) in [
        ("report.docx", word("sample_count=6")),
        ("quantities.xlsx", cell("number", json!(6))),
        (
            "requirements.pdf",
            json!({"op":"annotate","page":1,"rect":[20,20,40,40],"text":"sample_count=6"}),
        ),
        (
            "form.pdf",
            json!({"op":"fill_fields","fields":{"project":"sample_count=6"}}),
        ),
    ] {
        let result = scope.execute("apply_document", f.args(source, patch)).await;
        assert!(
            result.as_ref().is_err_and(|s| s.contains("新增数字")),
            "numeric gate: {result:?}"
        );
        f.no_documents_published();
    }
}

#[tokio::test]
async fn forged_reference_hash_or_quote_is_rejected_and_valid_preview_writes_nothing() {
    let f = Fixture::new();
    let scope = f.scope("依照选中文件修改", true);
    let search = scope
        .execute("search_sources", json!({"query":"sample_count"}))
        .await
        .unwrap();
    assert_eq!(search["ok"], true, "{search}");
    let hit = search["result"]["hits"]
        .as_array()
        .unwrap()
        .iter()
        .find(|h| h["source"] == "requirements.pdf")
        .unwrap()
        .clone();
    for (field, value) in [
        ("quote", json!("sample_count=6 forged text")),
        ("source_sha256", json!("0".repeat(64))),
    ] {
        let mut forged = hit.clone();
        forged[field] = value;
        let mut patch = word("sample_count=6");
        patch["evidence"] = json!([forged]);
        let result = scope
            .execute("apply_document", f.args("report.docx", patch))
            .await;
        assert!(
            result.as_ref().is_err_and(|s| s.contains("引用未通过")),
            "reference gate: {result:?}"
        );
        f.no_documents_published();
    }
    let mut patch = word("sample_count=6");
    patch["evidence"] = json!([hit]);
    let preview = scope
        .execute("preview_document", f.args("report.docx", patch))
        .await
        .unwrap();
    assert_eq!(preview["ok"], true, "{preview}");
    assert_eq!(
        preview["result"]["evidence_validation"]["references"]["valid"],
        true
    );
    f.no_documents_published();
}

#[tokio::test]
async fn read_only_rejects_even_explicitly_requested_number() {
    let f = Fixture::new();
    let scope = f.scope("将 sample_count 修改为 6", false);
    let result = scope
        .execute(
            "apply_document",
            f.args("report.docx", word("sample_count=6")),
        )
        .await;
    assert!(
        result.as_ref().is_err_and(|s| s.contains("read-only")),
        "{result:?}"
    );
    f.no_documents_published();
}

#[tokio::test]
async fn asserted_verdict_is_rejected_but_negative_statement_can_be_previewed() {
    let f = Fixture::new();
    let scope = f.scope("记录待核查内容", true);
    let result = scope
        .execute("apply_document", f.args("report.docx", word("可以开工")))
        .await;
    assert!(
        result.as_ref().is_err_and(|s| s.contains("工程结论")),
        "{result:?}"
    );
    let preview = scope
        .execute(
            "preview_document",
            f.args("report.docx", word("不判定可以开工")),
        )
        .await
        .unwrap();
    assert_eq!(preview["ok"], true, "{preview}");
    f.no_documents_published();
}

#[tokio::test]
async fn forged_old_value_metadata_does_not_authorize_a_new_number() {
    let f = Fixture::new();
    let scope = f.scope("修改草稿", true);
    let mut patch = cell("number", json!(6));
    patch["expected_text"] = json!("6");
    rejected(
        scope
            .execute("apply_document", f.args("quantities.xlsx", patch))
            .await,
    );
    f.no_documents_published();
}

#[tokio::test]
async fn formula_string_cannot_publish_an_asserted_engineering_verdict() {
    let f = Fixture::new();
    let scope = f.scope("记录待核查内容", true);
    rejected(
        scope
            .execute(
                "apply_document",
                f.args("quantities.xlsx", cell("formula", json!("=\"可以开工\""))),
            )
            .await,
    );
    f.no_documents_published();
}

#[tokio::test]
async fn formula_constant_cannot_bypass_numeric_source_gate() {
    let f = Fixture::new();
    let scope = f.scope("核对资料后修改新副本", true);
    rejected(
        scope
            .execute(
                "apply_document",
                f.args("quantities.xlsx", cell("formula", json!("=987654"))),
            )
            .await,
    );
    f.no_documents_published();
}

#[tokio::test]
async fn formula_coordinates_are_not_treated_as_new_engineering_quantities() {
    let f = Fixture::new();
    let scope = f.scope("整理现有工作表计算引用", true);
    let result = scope
        .execute(
            "preview_document",
            f.args(
                "quantities.xlsx",
                cell("formula", json!("=SUM(A1:A123)+$B$23")),
            ),
        )
        .await
        .unwrap();
    assert_eq!(result["ok"], true, "{result}");
    f.no_documents_published();
}

#[tokio::test]
async fn quoted_formula_text_is_not_misclassified_as_a_cell_coordinate() {
    let f = Fixture::new();
    let scope = f.scope("核对资料后修改新副本", true);
    rejected(
        scope
            .execute(
                "apply_document",
                f.args("quantities.xlsx", cell("formula", json!("=\"A987654\""))),
            )
            .await,
    );
    f.no_documents_published();
}

#[tokio::test]
async fn sourced_formula_constant_and_bounded_range_preview_are_allowed() {
    let f = Fixture::new();
    let scope = f.scope("依照资料整理计算草稿", true);
    let search = scope
        .execute("search_sources", json!({"query":"sample_count"}))
        .await
        .unwrap();
    let hit = search["result"]["hits"]
        .as_array()
        .unwrap()
        .iter()
        .find(|h| h["source"] == "requirements.pdf")
        .unwrap()
        .clone();
    let mut patch = cell("formula", json!("=A1+6"));
    patch["evidence"] = json!([hit]);
    let result = scope
        .execute("preview_document", f.args("quantities.xlsx", patch))
        .await
        .unwrap();
    assert_eq!(result["ok"], true, "source-supported formula: {result}");
    let expected: Vec<Value> = (0..201)
        .map(|_| json!([{"type":"blank","value":null}]))
        .collect();
    let values: Vec<Value> = (0..201)
        .map(|_| json!([{"type":"text","value":"Pending review"}]))
        .collect();
    let range = json!({"op":"set_range","sheet":"Counts","range":"E1:E201","expected":expected,"values":values});
    let result = scope
        .execute("preview_document", f.args("quantities.xlsx", range))
        .await
        .unwrap();
    assert_eq!(result["ok"], true, "bounded multi-cell review: {result}");
    f.no_documents_published();
}

#[tokio::test]
async fn oversized_selected_text_is_rejected() {
    let mut f = Fixture::new();
    std::fs::write(
        f.workspace.root().join("large.txt"),
        vec![b'x'; 256 * 1024 + 1],
    )
    .unwrap();
    f.selected.push("large.txt".into());
    let result = f
        .scope("读取选中文件", false)
        .execute("read_file", json!({"source":"large.txt"}))
        .await;
    assert!(
        result.as_ref().is_err_and(|s| s.contains("256 KiB")),
        "{result:?}"
    );
    f.no_documents_published();
}

#[tokio::test]
async fn model_loop_requires_human_post_classification_and_current_risk_acknowledgement() {
    let _model_guard = SCRIPTED_MODEL_LOCK.lock().unwrap();
    use axum::{
        body::Body,
        http::{Request, StatusCode},
        routing::{get, post},
        Json, Router,
    };
    use civil_workbench::config::{set_runtime_llm, LlmConfig};
    use http_body_util::BodyExt;
    use std::time::Duration;
    use tower::ServiceExt;
    struct ResetEnvironment(Option<std::ffi::OsString>, Option<std::ffi::OsString>);
    impl Drop for ResetEnvironment {
        fn drop(&mut self) {
            set_runtime_llm(None);
            for (key, old) in [("CIVIL_DOMAIN_URL", &self.0), ("CIVIL_DOMAIN_TOKEN", &self.1)] {
                match old {
                    Some(value) => std::env::set_var(key, value),
                    None => std::env::remove_var(key),
                }
            }
        }
    }
    let _reset = ResetEnvironment(std::env::var_os("CIVIL_DOMAIN_URL"), std::env::var_os("CIVIL_DOMAIN_TOKEN"));
    async fn http(app: &Router, method: &str, url: &str, body: Value) -> (StatusCode, Value) {
        let result = app
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
        let status = result.status();
        let bytes = result.into_body().collect().await.unwrap().to_bytes();
        (status, serde_json::from_slice(&bytes).unwrap())
    }
    let high_skill = civil_workbench::catalog::seed()
        .experts
        .iter()
        .find(|s| s.risk == "high")
        .unwrap()
        .id
        .clone();
    let low_skill = "pm-daily";
    assert_eq!(civil_workbench::catalog::seed().experts.iter().find(|s| s.id == low_skill).unwrap().risk, "low");
    for scenario in [
        "unread",
        "different_preview",
        "automatic_no_skill",
        "automatic_model_low_skill",
        "automatic_signed",
        "selected_low_risk",
        "high_risk_unsigned",
        "high_risk_signed",
        "high_risk_then_low_skill",
        "high_risk_late_tool",
        "engineering_selected_no_call",
        "engineering_unsigned",
        "engineering_signed",
        "engineering_late_tool",
        "child_unsigned",
        "child_signed",
        "child_failed_unsigned",
        "explicit_unsigned",
        "explicit_signed_field",
        "explicit_whole_message",
        "explicit_pasted_line",
        "explicit_pasted_sentence",
        "explicit_inexact_field",
        "explicit_same_session_history",
        "explicit_other_session_history",
    ] {
        let f = Fixture::new();
        let original = std::fs::read(f.workspace.root().join("report.docx")).unwrap();
        let preview = f.args(
            "report.docx",
            word("Source record remains subject to review."),
        );
        let mut apply = preview.clone();
        if scenario == "different_preview" {
            apply["patches"][0]["text"] = json!("Changed after preview.");
        }
        let mut planned = Vec::<(&str, Value)>::new();
        if scenario != "unread" {
            planned.push((
                "read_file",
                json!({"source":"report.docx","operation":"inspect"}),
            ));
        }
        if scenario.starts_with("high_risk") && scenario != "high_risk_late_tool" {
            planned.push(("load_skill", json!({"skill_id":high_skill})));
        }
        if matches!(scenario, "automatic_model_low_skill" | "high_risk_then_low_skill") {
            planned.push(("load_skill", json!({"skill_id":low_skill})));
        }
        if matches!(scenario, "engineering_unsigned" | "engineering_signed") {
            planned.push(("engineering_analyze", json!({"selection_index":0})));
        }
        if scenario.starts_with("child_") {
            planned.push(("delegate", json!({"tasks":[{"role":"review","goal":"Review selected sources only."}]})));
        }
        planned.push(("preview_document", preview));
        planned.push(("apply_document", apply));
        if scenario == "high_risk_late_tool" {
            planned.push(("load_skill", json!({"skill_id":high_skill})));
        }
        if scenario == "engineering_late_tool" {
            planned.push(("engineering_analyze", json!({"selection_index":0})));
        }
        // A real host invocation rejects incomplete saved inputs; even that
        // cannot clear engineering risk before a later document publication.
        let frame_id = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb";
        let frame_inputs = json!({"schema_version":1,"units":"SI","nodes":[]});
        let engineering = if scenario.starts_with("engineering_") {
            json!([{"kind":"saved_frame","project_id":frame_id,"revision":1,"source_sha256":null,
                "inputs_sha256":sha256(&serde_json::to_vec(&frame_inputs).unwrap()),"confirmed_solid":false}])
        } else { json!([]) };
        let frame_record = json!({"ok":true,"project":{"id":frame_id,"revision":1,"kind":"frame"},
            "snapshot":{"kind":"frame","inputs":frame_inputs}});
        let calls:Vec<Value>=planned.iter().enumerate().map(|(i,(name,args))|json!({"id":format!("gate_{i}"),"type":"function","function":{"name":name,"arguments":args.to_string()}})).collect();
        let child_calls = json!([{"id":"child_high_skill","type":"function","function":{"name":"load_skill",
            "arguments":json!({"skill_id":high_skill}).to_string()}}]);
        let server=Router::new().route("/chat/completions",post(move |Json(payload):Json<Value>| {
            let calls=calls.clone(); let child_calls=child_calls.clone(); async move {
                let is_child=payload["messages"].as_array().unwrap().iter().any(|m|m["role"]=="user" && m["content"]=="Review selected sources only.");
                let has_tool=payload["messages"].as_array().unwrap().iter().any(|m|m["role"]=="tool");
                let message=if has_tool {json!({"role":"assistant","content":"待核查内容已记录。"})}
                    else {json!({"role":"assistant","content":null,"tool_calls":if is_child {child_calls}else{json!(calls)}})};
                let finish_reason=if is_child && has_tool && scenario=="child_failed_unsigned" {"length"} else if has_tool {"stop"}else{"tool_calls"};
                Json(json!({"model":"local-gates","choices":[{"message":message,"finish_reason":finish_reason}],"usage":{"prompt_tokens":100,"completion_tokens":100}}))
            }
        })).route(&format!("/api/engineering/projects/{frame_id}"), get(move || {
            let record = frame_record.clone(); async move { Json(record) }
        }));
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let base_url = format!("http://{}", listener.local_addr().unwrap());
        std::env::set_var("CIVIL_DOMAIN_URL", &base_url);
        std::env::set_var("CIVIL_DOMAIN_TOKEN", "synthetic-domain-service-token-at-least-32-bytes");
        let server = tokio::spawn(async move { axum::serve(listener, server).await.unwrap() });
        set_runtime_llm(Some(LlmConfig {
            api_key: "local-scripted-only".into(),
            base_url,
            model: "local-gates".into(),
        }));
        let app = civil_workbench::product::api::router(f.state.clone());
        let (_, registered) = http(
            &app,
            "POST",
            "/api/agent/workspaces",
            json!({"path":f.workspace.root()}),
        )
        .await;
        let wid = registered["workspace"]["id"].as_str().unwrap();
        if scenario.ends_with("session_history") {
            let prior_session = if scenario == "explicit_same_session_history" {
                scenario
            } else {
                "signed_elsewhere"
            };
            let (status, prior) = http(
                &app,
                "POST",
                "/api/agent/turns",
                json!({
                    "workspace":wid,"session_id":prior_session,"message":"登记本工程会话签认",
                    "mode":"steps","sandbox":"read-only","files":[],
                    "risk_confirmation":"我明白，将由持证人员签认"
                }),
            )
            .await;
            assert_eq!(status, StatusCode::ACCEPTED, "{prior}");
            let prior_url = format!(
                "/api/agent/turns/{}?workspace={wid}&session_id={prior_session}",
                prior["turn_id"].as_str().unwrap()
            );
            let mut previous = Value::Null;
            for _ in 0..100 {
                previous = http(&app, "GET", &prior_url, Value::Null).await.1;
                if previous["turn"]["status"] != "running" {
                    break;
                }
                tokio::time::sleep(Duration::from_millis(20)).await;
            }
            assert_eq!(previous["turn"]["status"], "completed", "{previous}");
        }
        let message = match scenario {
            "explicit_whole_message" => "我明白，将由持证人员签认",
            "explicit_pasted_line" => "材料原文如下：\n我明白，将由持证人员签认\n修改待核查草稿",
            "explicit_pasted_sentence" => "修改待核查草稿。我明白，将由持证人员签认",
            _ => "修改待核查草稿",
        };
        let confirmation = match scenario {
            "explicit_signed_field" | "high_risk_signed" | "automatic_signed" | "engineering_signed" | "child_signed" => "我明白，将由持证人员签认",
            "explicit_inexact_field" => "我明白，将由持证人员签认。",
            _ => "",
        };
        let expert = if scenario.starts_with("explicit_") {
            high_skill.as_str()
        } else if scenario.starts_with("automatic_") {
            ""
        } else {
            low_skill
        };
        let (status,started)=http(&app,"POST","/api/agent/turns",json!({"workspace":wid,"session_id":scenario,"message":message,"mode":"model","sandbox":"workspace-write","files":f.selected,"expert_id":expert,"risk_confirmation":confirmation,"engineering":engineering})).await;
        assert_eq!(status, StatusCode::ACCEPTED, "{started}");
        let url = format!(
            "/api/agent/turns/{}/events?workspace={wid}&session_id={scenario}",
            started["turn_id"].as_str().unwrap()
        );
        let mut result = Value::Null;
        for _ in 0..300 {
            result = http(&app, "GET", &url, Value::Null).await.1;
            if result["turn"]["status"] != "running" {
                break;
            }
            tokio::time::sleep(Duration::from_millis(100)).await;
        }
        set_runtime_llm(None);
        server.abort();
        assert_eq!(
            result["turn"]["status"], "completed",
            "{scenario}: {result}"
        );
        assert_eq!(result["turn"]["request"]["document_write_classification"]["source"],
            if scenario.starts_with("automatic_") {"unclassified"} else {"user_selected_post"}, "{scenario}: {result}");
        assert_eq!(std::fs::read(f.workspace.root().join("report.docx")).unwrap(), original,
            "{scenario}: the source document changed");
        if scenario.starts_with("automatic_") {
            assert!(result["events"].as_array().unwrap().iter().any(|event|
                event["kind"] == "tool_finished" && event["data"]["name"] == "preview_document"
                    && event["data"]["result"]["ok"] == true),
                "automatic mode must still allow previews: {result}");
            assert!(result["events"].as_array().unwrap().iter().any(|event|
                event["kind"] == "authorization" && event["data"]["reason"] == "post_selection_required"
                    && event["data"]["document_write_allowed"] == false),
                "the UI needs an actionable publication denial: {result}");
        }
        let apply = result["events"]
            .as_array()
            .unwrap()
            .iter()
            .find(|e| e["kind"] == "tool_finished" && e["data"]["name"] == "apply_document")
            .expect("apply event");
        if scenario.starts_with("engineering_") && scenario != "engineering_selected_no_call" {
            let calculation = result["events"].as_array().unwrap().iter().find(|event|
                event["kind"] == "tool_finished" && event["data"]["name"] == "engineering_analyze").unwrap();
            assert!(calculation["data"]["result"]["error"].as_str().is_some_and(|error| error.contains("工程输入不完整")),
                "the real saved-input host must have run: {calculation}");
        }
        if scenario.starts_with("child_") {
            assert!(result["events"].as_array().unwrap().iter().any(|event|
                event["kind"] == "tool_finished" && event["data"]["call_id"] == "child_high_skill"
                    && event["data"]["result"]["risk"] == "high"),
                "the read-only child must actually load the high-risk SOP: {result}");
            if scenario == "child_failed_unsigned" {
                assert!(result["events"].as_array().unwrap().iter().any(|event|
                    event["kind"] == "subtask_finished" && event["data"]["status"] == "failed"),
                    "failed child coverage: {result}");
            }
        }
        if scenario.starts_with("explicit_") {
            assert!(
                result["events"]
                    .as_array()
                    .unwrap()
                    .iter()
                    .any(|event| event["kind"] == "skill"
                        && event["data"]["selected_by"] == "user"
                        && event["data"]["risk"] == "high"),
                "selected high-risk skill missing: {result}"
            );
            assert!(
                !result["events"]
                    .as_array()
                    .unwrap()
                    .iter()
                    .any(|event| event["kind"] == "tool_started"
                        && event["data"]["name"] == "load_skill"),
                "scripted model must not request load_skill in explicit cases"
            );
        }
        if matches!(
            scenario,
            "high_risk_signed" | "explicit_signed_field" | "explicit_whole_message" | "selected_low_risk"
                | "engineering_signed" | "engineering_selected_no_call" | "child_signed"
        ) {
            assert_eq!(apply["data"]["result"]["ok"], true, "{apply}");
            assert_eq!(
                result["turn"]["result"]["artifacts"]
                    .as_array()
                    .unwrap()
                    .len(),
                1
            );
        } else {
            assert_eq!(apply["data"]["result"]["ok"], false, "{scenario}: {apply}");
            let text = apply["data"]["result"]["error"].as_str().unwrap();
            assert!(
                text.contains(
                    if scenario.starts_with("automatic_") {
                        "明确选择"
                    } else if scenario.starts_with("high_risk") || scenario.starts_with("explicit_") || scenario.starts_with("engineering_") || scenario.starts_with("child_") {
                        "持证人员"
                    } else {
                        "必须先读取"
                    }
                ),
                "{apply}"
            );
            assert!(result["turn"]["result"]["artifacts"]
                .as_array()
                .unwrap()
                .is_empty());
            f.no_documents_published();
        }
    }
}

#[tokio::test]
async fn preview_ids_apply_the_cached_patch_and_reject_unknown_or_mixed_arguments() {
    use axum::{
        body::Body,
        http::{Request, StatusCode},
        routing::post,
        Json, Router,
    };
    use civil_workbench::config::{set_runtime_llm, LlmConfig};
    use http_body_util::BodyExt;
    use std::time::Duration;
    use tower::ServiceExt;
    let _model_guard = SCRIPTED_MODEL_LOCK.lock().unwrap();
    struct ResetModel;
    impl Drop for ResetModel {
        fn drop(&mut self) {
            set_runtime_llm(None);
        }
    }
    let _reset = ResetModel;

    async fn http(app: &Router, method: &str, url: &str, body: Value) -> (StatusCode, Value) {
        let response = app
            .clone()
            .oneshot(
                Request::builder()
                    .method(method)
                    .uri(url)
                    .header("content-type", "application/json")
                    .body(Body::from(body.to_string()))
                    .unwrap(),
            )
            .await
            .unwrap();
        let status = response.status();
        let bytes = response.into_body().collect().await.unwrap().to_bytes();
        (status, serde_json::from_slice(&bytes).unwrap())
    }
    fn call(id: &str, name: &str, args: Value) -> Value {
        json!({"id":id,"type":"function","function":{"name":name,"arguments":args.to_string()}})
    }

    for scenario in ["cached", "unknown", "mixed", "partly_saved"] {
        let fixture = Fixture::new();
        let original = std::fs::read(fixture.workspace.root().join("report.docx")).unwrap();
        let args = fixture.args(
            "report.docx",
            word("Source record remains subject to review."),
        );
        let expected_preview_id = sha256(args.to_string().as_bytes());
        let pending = fixture.args("quantities.xlsx", cell("text", json!("Requires review")));
        let server = Router::new().route("/chat/completions",post(move |Json(payload): Json<Value>| {
            let args = args.clone();
            let pending = pending.clone();
            async move {
                let messages = payload["messages"].as_array().unwrap();
                let preview_response = messages.iter().find(|m| m["role"] == "tool" && m["tool_call_id"] == "preview");
                let applied = messages.iter().any(|m| m["role"] == "tool" && m["tool_call_id"] == "apply");
                let message = if applied {
                    // The host must report actual saved files rather than this
                    // false all-complete statement or its invented quantity.
                    json!({"role":"assistant","content":"所有文档已修改并保存，数量已更新为 987654，全部工作完成。"})
                } else if let Some(preview) = preview_response {
                    let result: Value = serde_json::from_str(preview["content"].as_str().unwrap()).unwrap();
                    let mut apply = json!({"preview_id":result["result"]["preview_id"]});
                    if scenario == "unknown" { apply["preview_id"] = json!("0".repeat(64)); }
                    if scenario == "mixed" { apply["source"] = json!("report.docx"); }
                    json!({"role":"assistant","content":null,"tool_calls":[call("apply","apply_document",apply)]})
                } else {
                    let mut calls = vec![
                        call("inspect","read_file",json!({"source":"report.docx","operation":"inspect"})),
                        call("preview","preview_document",args)];
                    if scenario == "partly_saved" {
                        calls.push(call("inspect_pending","read_file",json!({"source":"quantities.xlsx","operation":"inspect"})));
                        calls.push(call("preview_pending","preview_document",pending));
                    }
                    json!({"role":"assistant","content":null,"tool_calls":calls})
                };
                let finish = if message["tool_calls"].is_array() { "tool_calls" } else { "stop" };
                Json(json!({"model":"preview-id-scripted","choices":[{"message":message,"finish_reason":finish}],
                    "usage":{"prompt_tokens":100,"completion_tokens":100}}))
            }
        }));
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let base_url = format!("http://{}", listener.local_addr().unwrap());
        let server = tokio::spawn(async move {
            axum::serve(listener, server).await.unwrap();
        });
        set_runtime_llm(Some(LlmConfig {
            api_key: "scripted-local-only".into(),
            base_url,
            model: "preview-id-scripted".into(),
        }));
        let app = civil_workbench::product::api::router(fixture.state.clone());
        let registered = fixture
            .state
            .register(fixture.workspace.root().to_str().unwrap())
            .unwrap();
        let wid = registered["id"].as_str().unwrap();
        let (status, started) = http(&app,"POST","/api/agent/turns",json!({"workspace":wid,"session_id":scenario,
            "message":"修改待核查草稿","mode":"model","sandbox":"workspace-write","files":fixture.selected,"expert_id":"pm-daily"})).await;
        assert_eq!(status, StatusCode::ACCEPTED, "{started}");
        let url = format!(
            "/api/agent/turns/{}/events?workspace={wid}&session_id={scenario}",
            started["turn_id"].as_str().unwrap()
        );
        let mut result = Value::Null;
        for _ in 0..300 {
            result = http(&app, "GET", &url, Value::Null).await.1;
            if result["turn"]["status"] != "running" {
                break;
            }
            tokio::time::sleep(Duration::from_millis(100)).await;
        }
        set_runtime_llm(None);
        server.abort();
        assert_eq!(
            result["turn"]["status"], "completed",
            "{scenario}: {result}"
        );
        let events = result["events"].as_array().unwrap();
        let preview = events
            .iter()
            .find(|event| {
                event["kind"] == "tool_finished" && event["data"]["name"] == "preview_document"
            })
            .unwrap();
        assert_eq!(preview["data"]["result"]["ok"], true, "{preview}");
        assert_eq!(
            preview["data"]["result"]["result"]["preview_id"],
            expected_preview_id
        );
        let applied = events
            .iter()
            .find(|event| {
                event["kind"] == "tool_finished" && event["data"]["name"] == "apply_document"
            })
            .unwrap();
        assert_eq!(
            std::fs::read(fixture.workspace.root().join("report.docx")).unwrap(),
            original,
            "original modified"
        );
        let artifacts = result["turn"]["result"]["artifacts"].as_array().unwrap();
        let summary = result["turn"]["result"]["reply"].as_str().unwrap();
        assert!(!summary.contains("987654"), "Unsupported model quantity survived: {summary}");
        assert!(!summary.contains("全部工作完成"), "Unverified task completion survived: {summary}");
        assert_eq!(result["turn"]["result"]["execution_evidence"]["publication_summary_from_receipts"], true);
        assert_eq!(result["turn"]["result"]["execution_evidence"]["requested_task_complete"], if scenario == "cached" {Value::Null}else{json!(false)});
        if matches!(scenario, "cached" | "partly_saved") {
            assert_eq!(applied["data"]["result"]["ok"], true, "{applied}");
            assert_eq!(artifacts.len(), 1);
            assert_eq!(result["turn"]["result"]["tool_errors"], 0);
            assert!(summary.contains(artifacts[0]["name"].as_str().unwrap()), "Saved file missing from authoritative report");
            assert!(summary.contains(artifacts[0]["source_sha256"].as_str().unwrap()), "Source evidence missing");
            assert_eq!(result["turn"]["result"]["partial"], scenario == "partly_saved");
            assert_eq!(result["turn"]["result"]["execution_evidence"]["unapplied_previews"], if scenario == "partly_saved" {1} else {0});
            let path = applied["data"]["result"]["result"]["output_path"]
                .as_str()
                .unwrap();
            let bytes = std::fs::read(path).unwrap();
            assert_ne!(bytes, original);
            assert_eq!(sha256(&bytes), artifacts[0]["output_sha256"]);
        } else {
            assert_eq!(applied["data"]["result"]["ok"], false, "{applied}");
            let error = applied["data"]["result"]["error"].as_str().unwrap();
            assert!(
                error.contains(if scenario == "unknown" {
                    "不属于本轮成功预览"
                } else {
                    "不能与新的补丁参数混用"
                }),
                "{error}"
            );
            assert!(artifacts.is_empty());
            fixture.no_documents_published();
        }
    }
}
