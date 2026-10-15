//! Current imported source selection is a revision binding, not a hidden scan.
use axum::{
    body::Body,
    http::{Request, StatusCode},
    Router,
};
use civil_workbench::{
    config::Paths,
    product::{
        api::{router, ProductState, TurnRequest},
        auth::InstanceAuth,
        controlled_sources::{self, FileBinding, SourceOrigin},
        tools::{sha256, SourceEvidence, ToolScope},
    },
    runtime_core::{CancellationToken, WorkspaceContext},
};
use http_body_util::BodyExt;
use rusqlite::{params, Connection};
use serde_json::{json, Value};
use std::{path::PathBuf, sync::Arc};
use tower::ServiceExt;

struct Fixture {
    root: PathBuf,
    state: Arc<ProductState>,
    ws: WorkspaceContext,
    w: String,
    doc: String,
    import: String,
    path: String,
    hash: String,
    cancel: CancellationToken,
}
impl Fixture {
    fn new(ext: &str, bytes: &[u8]) -> Self {
        assert!(std::env::var_os("CIVIL_STATE_ROOT").is_none());
        let root = std::env::temp_dir().join(format!("agent-handoff-{}", uuid::Uuid::new_v4()));
        std::fs::create_dir_all(root.join("job")).unwrap();
        let repo = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .parent()
            .unwrap()
            .to_owned();
        let mut paths = Paths::from_demo(repo.join("demo"));
        paths.data_dir = root.join("state");
        let state = ProductState::open_with_auth(paths, InstanceAuth::local()).unwrap();
        let w = state.register(root.join("job").to_str().unwrap()).unwrap()["id"]
            .as_str()
            .unwrap()
            .to_owned();
        let ws = WorkspaceContext::new(root.join("job")).unwrap();
        let doc = "b".repeat(32);
        let import = "a".repeat(32);
        let path = format!(".civil-buddy/out/handoffs/{import}/{doc}.{ext}");
        let target = ws.root().join(&path);
        std::fs::create_dir_all(target.parent().unwrap()).unwrap();
        std::fs::write(&target, bytes).unwrap();
        let hash = sha256(bytes);
        let f = Self {
            root,
            state,
            ws,
            w,
            doc,
            import,
            path,
            hash,
            cancel: CancellationToken::new(),
        };
        let d = f.record(1);
        let db = f.db();
        db.execute(
            "INSERT INTO project_documents VALUES(?1,?2,?3)",
            params![f.w, f.doc, d.to_string()],
        )
        .unwrap();
        db.execute(
            "INSERT INTO project_revisions VALUES(?1,?2,1,?3,?4)",
            params![f.w, f.doc, d.to_string(), bytes],
        )
        .unwrap();
        let receipt = json!({"schema_version":1,"workspace":f.w,"import_id":f.import,"package_sha256":"c".repeat(64),"document_mapping":[{"local_document_id":f.doc,"local_revision":1,"source_sha256":f.hash}],"source_metadata_bytes":0});
        db.execute(
            "INSERT INTO project_imports VALUES(?1,?2,?3)",
            params![f.w, "c".repeat(64), receipt.to_string()],
        )
        .unwrap();
        std::fs::write(
            f.ws.root().join(format!(
                ".civil-buddy/out/handoffs/{}/source-manifest.json",
                f.import
            )),
            "{\"inert_source\":true}",
        )
        .unwrap();
        f
    }
    fn db(&self) -> Connection {
        Connection::open(self.root.join("state/unified/index.sqlite")).unwrap()
    }
    fn record(&self, revision: u64) -> Value {
        json!({"id":self.doc,"workspace":self.w,"path":self.path,"title":"Received engineering brief 原始资料","discipline":"Site","revision":revision,"review_version":revision,"revision_label":format!("R{revision}"),"sha256":self.hash,"status":"work_in_progress","actor_id":"local-owner","updated_at":"2026-10-15T00:00:00Z","provenance":{"import_id":self.import,"package_sha256":"c".repeat(64),"source_revision":1}})
    }
    fn binding(&self, revision: u64) -> FileBinding {
        FileBinding {
            path: self.path.clone(),
            origin: SourceOrigin {
                kind: "project_handoff".into(),
                import_id: self.import.clone(),
                document_id: self.doc.clone(),
                revision,
                registered_sha256: self.hash.clone(),
            },
        }
    }
    fn advance(&self, revision: u64) {
        let d = self.record(revision);
        let bytes = std::fs::read(self.ws.root().join(&self.path)).unwrap();
        let db = self.db();
        db.execute(
            "INSERT INTO project_revisions VALUES(?1,?2,?3,?4,?5)",
            params![
                self.w,
                self.doc,
                i64::try_from(revision).unwrap(),
                d.to_string(),
                bytes
            ],
        )
        .unwrap();
        db.execute(
            "UPDATE project_documents SET record=?1 WHERE workspace=?2 AND id=?3",
            params![d.to_string(), self.w, self.doc],
        )
        .unwrap();
    }
    fn scope<'a>(&'a self, selected: &'a [String], bindings: &'a [FileBinding]) -> ToolScope<'a> {
        ToolScope {
            state: &self.state,
            workspace: &self.ws,
            selected,
            file_bindings: bindings,
            user_request: "Check the received source; preserve original facts",
            write: true,
            cancel: &self.cancel,
        }
    }
    fn payload(&self, binding: FileBinding) -> Value {
        json!({"workspace":self.w,"session_id":"d".repeat(32),"message":"Inspect this received source","mode":"steps","sandbox":"read-only","files":[self.path],"file_bindings":[binding]})
    }
}
impl Drop for Fixture {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.root);
    }
}
async fn call(
    app: &Router,
    method: &str,
    url: &str,
    body: Value,
    key: Option<&str>,
) -> (StatusCode, Value) {
    let mut req = Request::builder()
        .method(method)
        .uri(url)
        .header("content-type", "application/json");
    if let Some(k) = key {
        req = req.header("Idempotency-Key", k);
    }
    let response = app
        .clone()
        .oneshot(req.body(Body::from(body.to_string())).unwrap())
        .await
        .unwrap();
    let s = response.status();
    let b = response.into_body().collect().await.unwrap().to_bytes();
    (
        s,
        serde_json::from_slice(&b).unwrap_or_else(|_| json!({"body":String::from_utf8_lossy(&b)})),
    )
}

#[tokio::test]
async fn inventory_only_adds_registered_current_supported_handoffs_with_truthful_500_cap() {
    let f = Fixture::new("txt", b"Delivery deadline is 60 days.");
    let stray = f.ws.root().join(format!(
        ".civil-buddy/out/handoffs/{}/{}.txt",
        f.import,
        "e".repeat(32)
    ));
    std::fs::write(stray, "Not registered").unwrap();
    std::fs::create_dir_all(f.ws.root().join(".private")).unwrap();
    std::fs::write(f.ws.root().join(".private/note.md"), "Not scanned").unwrap();
    let report = controlled_sources::inventory(&f.state, &f.ws).unwrap();
    assert!(report.complete);
    assert_eq!(report.files.len(), 1);
    assert_eq!(report.files[0].path, f.path);
    assert_eq!(
        report.files[0].origin.as_ref().unwrap(),
        &f.binding(1).origin
    );
    for n in 0..499 {
        std::fs::write(f.ws.root().join(format!("file-{n:03}.md")), "ordinary").unwrap();
    }
    let report = controlled_sources::inventory(&f.state, &f.ws).unwrap();
    assert_eq!(report.returned_count, 500);
    assert!(!report.complete && report.file_limit_reached);
    assert_eq!(report.incomplete_reasons, vec!["file_limit"]);
    assert_eq!(
        report.files.iter().filter(|r| r.origin.is_some()).count(),
        1
    );
    std::fs::remove_file(f.ws.root().join("file-000.md")).unwrap();
    assert!(
        controlled_sources::inventory(&f.state, &f.ws)
            .unwrap()
            .complete
    );
    let deep = f.ws.root().join("one/two/three/four/five/six");
    std::fs::create_dir_all(&deep).unwrap();
    std::fs::write(deep.join("beyond-cap.md"), "not enumerated").unwrap();
    let depth = controlled_sources::inventory(&f.state, &f.ws).unwrap();
    assert!(depth.depth_limit_reached && !depth.complete && !depth.file_limit_reached);
    assert_eq!(depth.returned_count, 499);
    f.db().execute("DELETE FROM project_imports", []).unwrap();
    assert!(
        controlled_sources::inventory(&f.state, &f.ws).is_err(),
        "Unknown receipt must not produce a complete empty response"
    );
    let unsupported = Fixture::new("ifc", b"Non-agent model snapshot");
    assert!(
        controlled_sources::inventory(&unsupported.state, &unsupported.ws)
            .unwrap()
            .files
            .is_empty()
    );
    assert!(controlled_sources::validate_selection(
        &unsupported.state,
        &unsupported.ws,
        &[unsupported.path.clone()],
        &[unsupported.binding(1)]
    )
    .is_err());
}

#[tokio::test]
async fn binding_rejects_forged_scope_unregistered_siblings_aliases_and_same_size_changes() {
    let f = Fixture::new("txt", b"Verified source bytes.");
    let files = vec![f.path.clone()];
    let binding = f.binding(1);
    controlled_sources::validate_selection(&f.state, &f.ws, &files, &[binding.clone()]).unwrap();
    assert!(controlled_sources::validate_selection(&f.state, &f.ws, &files, &[]).is_err());
    for key in ["document", "import", "hash", "revision"] {
        let mut b = binding.clone();
        match key {
            "document" => b.origin.document_id = "e".repeat(32),
            "import" => b.origin.import_id = "f".repeat(32),
            "hash" => b.origin.registered_sha256 = "0".repeat(64),
            _ => b.origin.revision = 2,
        };
        assert!(controlled_sources::validate_selection(&f.state, &f.ws, &files, &[b]).is_err());
    }
    for path in [
        f.path.replace(".civil-buddy/", ".civil-buddy//"),
        f.path.replace("out/", "./out/"),
        f.path.to_ascii_uppercase(),
        f.path.replace('/', "\\"),
        format!(
            ".civil-buddy/out/handoffs/{}/source-manifest.json",
            f.import
        ),
    ] {
        assert!(controlled_sources::is_handoff(&path));
        assert!(controlled_sources::validate_selection(&f.state, &f.ws, &[path], &[]).is_err());
    }
    std::fs::create_dir(f.root.join("other")).unwrap();
    let other = WorkspaceContext::new(f.root.join("other")).unwrap();
    f.state.register(other.root().to_str().unwrap()).unwrap();
    assert!(
        controlled_sources::validate_selection(&f.state, &other, &files, &[binding.clone()])
            .is_err()
    );
    std::fs::write(f.ws.root().join(&f.path), b"Modified source bytes.").unwrap();
    assert_eq!(
        b"Modified source bytes.".len(),
        b"Verified source bytes.".len()
    );
    assert!(controlled_sources::validate_selection(&f.state, &f.ws, &files, &[binding]).is_err());
}

#[tokio::test]
async fn active_second_revision_same_hash_is_valid_but_prior_binding_and_old_hidden_path_are_not() {
    let f = Fixture::new("txt", b"Unchanged bytes across a label revision.");
    let files = vec![f.path.clone()];
    let first = f.binding(1);
    f.advance(2);
    assert!(controlled_sources::validate_selection(&f.state, &f.ws, &files, &[first]).is_err());
    controlled_sources::validate_selection(&f.state, &f.ws, &files, &[f.binding(2)]).unwrap();
    assert_eq!(
        controlled_sources::inventory(&f.state, &f.ws)
            .unwrap()
            .files[0]
            .origin
            .as_ref()
            .unwrap()
            .revision,
        2
    );
    std::fs::write(
        f.ws.root().join("new.md"),
        b"Unchanged bytes across a label revision.",
    )
    .unwrap();
    let app = router(f.state.clone());
    let(s,v)=call(&app,"POST","/api/project-control/documents",json!({"workspace":f.w,"document_id":f.doc,"expected_revision":2,"path":"new.md","title":"Moved controlled document","discipline":"Site","revision_label":"R3"}),None).await;
    assert_eq!(s, StatusCode::OK, "{v}");
    let r = controlled_sources::inventory(&f.state, &f.ws).unwrap();
    assert_eq!(r.files.len(), 1);
    assert_eq!(r.files[0].path, "new.md");
    assert!(r.files[0].origin.is_none());
    assert!(
        controlled_sources::validate_selection(&f.state, &f.ws, &files, &[f.binding(2)]).is_err()
    );
    controlled_sources::validate_selection(&f.state, &f.ws, &["new.md".into()], &[]).unwrap();
}

#[tokio::test]
async fn plain_read_and_all_rag_routes_use_bound_bytes_and_stale_registration_is_not_adopted() {
    let f = Fixture::new(
        "txt",
        b"Delivery deadline is 60 days. Original source evidence.",
    );
    let files = vec![f.path.clone()];
    let bindings = vec![f.binding(1)];
    let scope = f.scope(&files, &bindings);
    let read = scope
        .execute("read_file", json!({"source":f.path}))
        .await
        .unwrap();
    assert_eq!(read["result"]["source_sha256"], f.hash);
    let search = scope
        .execute("search_sources", json!({"query":"delivery deadline"}))
        .await
        .unwrap();
    assert_eq!(search["ok"], true, "{search}");
    let hits = search["result"]["hits"].as_array().unwrap();
    assert!(!hits.is_empty());
    assert!(hits.iter().all(|h| h["source_sha256"] == f.hash));
    let verify = scope
        .execute("verify_sources", json!({"references":[hits[0].clone()]}))
        .await
        .unwrap();
    assert_eq!(verify["result"]["valid"], true, "{verify}");
    let mut collected = SourceEvidence::default();
    collected.observe("search_sources", &json!({}), Some(&search));
    assert_eq!(collected.clone().verify(&scope).await["status"], "verified");
    f.advance(2);
    assert!(scope
        .execute("read_file", json!({"source":f.path}))
        .await
        .is_err());
    assert!(scope
        .execute("search_sources", json!({"query":"delivery"}))
        .await
        .is_err());
    assert!(scope
        .execute("verify_sources", json!({"references":[hits[0].clone()]}))
        .await
        .is_err());
    let stale_receipt = collected.verify(&scope).await;
    assert_eq!(stale_receipt["status"], "attention_required");
    assert!(stale_receipt["references"]
        .as_array()
        .unwrap()
        .iter()
        .all(|r| r["status"] != "valid"));
}

#[tokio::test]
async fn document_rag_patch_and_receipt_recheck_current_registry_after_worker_await() {
    // A deliberately synthetic fixed-host protocol peer creates a deterministic
    // await boundary. It does not claim to exercise Python parsing/confinement;
    // the real fixed-worker tests above and below cover those execution paths.
    for mode in [
        "document_read",
        "search_sources",
        "verify_sources",
        "patch_evidence",
        "receipt",
    ] {
        let mut f = Fixture::new("docx", b"Synthetic await-boundary source bytes");
        let host = f.root.join("synthetic-host/packing_assistant");
        std::fs::create_dir_all(&host).unwrap();
        std::fs::write(host.join("host_worker.py"), r#"import json,sys,time
from pathlib import Path
r=json.load(sys.stdin)
out=Path(r['output_dir'])
out.mkdir(parents=True,exist_ok=True)
(out/'synthetic-ready.json').write_text(json.dumps(r),encoding='utf-8')
end=time.monotonic()+15
while not (out/'synthetic-release').exists():
    if time.monotonic()>end: raise SystemExit(3)
    time.sleep(.005)
sources=[{'source':s,'source_sha256':h} for s,h in r.get('expected_versions',{}).items()]
result={'source_sha256':r.get('expected_sha256'),'sources':sources,'valid':True,'references':[],'hits':[]}
print(json.dumps({'version':1,'ok':True,'result':result,'sandbox':{'enforces':{'write':True,'spawn':True}}}))
"#).unwrap();
        Arc::get_mut(&mut f.state).unwrap().worker.repo_root = host.parent().unwrap().to_owned();
        let files = vec![f.path.clone()];
        let bindings = vec![f.binding(1)];
        let scope = f.scope(&files, &bindings);
        let reference = json!({"source":f.path,"source_sha256":f.hash,"locator":{"paragraph_id":"p:1"},"quote":"Synthetic await-boundary source bytes"});
        let operation = async {
            match mode {
                "receipt"=> {
                    let mut evidence=SourceEvidence::default();
                    evidence.observe("search_sources",&json!({}),Some(&json!({"ok":true,"result":{"hits":[reference.clone()]}})));
                    Ok(evidence.verify(&scope).await)
                },
                "patch_evidence"=>scope.execute("preview_document",json!({"source":f.path,"expected_sha256":f.hash,"patches":[{"op":"replace_paragraph","paragraph_id":"p:1","expected_text":"Synthetic await-boundary source bytes","text":"Clarified source wording","evidence":[reference.clone()]}]})).await,
                "document_read"=>scope.execute("read_file",json!({"source":f.path})).await,
                "search_sources"=>scope.execute(mode,json!({"query":"source"})).await,
                _=>scope.execute(mode,json!({"references":[reference.clone()]})).await,
            }
        };
        let mutate = async {
            let marker = f.ws.output_root().join("synthetic-ready.json");
            let deadline = tokio::time::Instant::now() + std::time::Duration::from_secs(10);
            while !marker.exists() {
                assert!(
                    tokio::time::Instant::now() < deadline,
                    "worker never reached {mode} await boundary"
                );
                tokio::time::sleep(std::time::Duration::from_millis(5)).await;
            }
            let r: Value = serde_json::from_slice(&std::fs::read(marker).unwrap()).unwrap();
            if mode == "document_read" {
                assert_eq!(r["expected_sha256"], f.hash);
            } else {
                assert_eq!(r["expected_versions"][&f.path], f.hash);
            }
            f.advance(2); // identical bytes; only the accepted registry identity changed
            std::fs::write(f.ws.output_root().join("synthetic-release"), "").unwrap();
        };
        let (outcome, ()) = tokio::join!(operation, mutate);
        if mode == "receipt" {
            let receipt = outcome.unwrap();
            assert_eq!(receipt["status"], "attention_required", "{receipt}");
            assert_eq!(receipt["references"][0]["status"], "unavailable");
        } else {
            let error = outcome.unwrap_err();
            assert!(
                error.contains("Imported source registration could not be verified"),
                "{mode} rejected for an unrelated reason: {error}"
            );
        }
    }
}

#[tokio::test]
async fn real_controlled_word_preview_apply_preserve_original_and_host_origin() {
    let repo = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .unwrap()
        .to_owned();
    let temp = std::env::temp_dir().join(format!("handoff-word-{}", uuid::Uuid::new_v4()));
    let python = std::env::var_os("CIVIL_PYTHON").unwrap_or_else(|| "python".into());
    let made=std::process::Command::new(python).args(["-B","-c","from pathlib import Path; import sys; from scripts.unified_acceptance import fixture; fixture(Path(sys.argv[1]))"]).arg(&temp).current_dir(repo).output().unwrap();
    assert!(
        made.status.success(),
        "{}",
        String::from_utf8_lossy(&made.stderr)
    );
    let bytes = std::fs::read(temp.join("report.docx")).unwrap();
    std::fs::remove_dir_all(temp).unwrap();
    let f = Fixture::new("docx", &bytes);
    let files = vec![f.path.clone()];
    let bindings = vec![f.binding(1)];
    let scope = f.scope(&files, &bindings);
    let read = scope
        .execute("read_file", json!({"source":f.path}))
        .await
        .unwrap();
    assert_eq!(read["ok"], true, "{read}");
    assert_eq!(read["result"]["source_sha256"], f.hash);
    let search = scope
        .execute("search_sources", json!({"query":"sample_count"}))
        .await
        .unwrap();
    let hit = search["result"]["hits"]
        .as_array()
        .unwrap()
        .iter()
        .find(|h| {
            h["quote"]
                .as_str()
                .is_some_and(|q| q.contains("sample_count=4"))
        })
        .unwrap();
    let args = json!({"source":f.path,"expected_sha256":f.hash,"patches":[{"op":"replace_paragraph","paragraph_id":"p:1","expected_text":"sample_count=4","text":"Recorded sample_count=4","evidence":[hit]}]});
    let preview = scope
        .execute("preview_document", args.clone())
        .await
        .unwrap();
    assert_eq!(preview["ok"], true, "{preview}");
    assert_eq!(
        preview["result"]["source_origin"],
        json!(f.binding(1).origin)
    );
    assert_eq!(
        preview["result"]["evidence_validation"]["references"]["valid"],
        true
    );
    let applied = scope.execute("apply_document", args).await.unwrap();
    assert_eq!(applied["ok"], true, "{applied}");
    let record = f.state.register_artifact(&f.w, &applied["result"]).unwrap();
    assert_eq!(record["source_origin"], json!(f.binding(1).origin));
    assert_ne!(applied["result"]["output_sha256"], f.hash);
    assert_eq!(std::fs::read(f.ws.root().join(&f.path)).unwrap(), bytes);
    f.advance(2);
    assert!(
        f.state.register_artifact(&f.w, &applied["result"]).is_err(),
        "Late artifact adoption must recheck source identity"
    );
}

#[tokio::test]
async fn controlled_bindings_persist_in_history_and_idempotent_identity_while_ordinary_empty_is_compatible(
) {
    let ordinary = json!({"workspace":"w","session_id":"s","message":"ordinary"});
    let req: TurnRequest = serde_json::from_value(ordinary.clone()).unwrap();
    let before = serde_json::to_vec(&req).unwrap();
    assert_eq!(
        String::from_utf8(before.clone()).unwrap(),
        r#"{"workspace":"w","session_id":"s","message":"ordinary","locale":"zh-CN","mode":"model","sandbox":"read-only","files":[],"engineering":[],"planning_action":"inspect","packing_sources":[],"expert_id":"","risk_confirmation":""}"#
    );
    let old = serde_json::to_value(&req).unwrap();
    assert!(old.get("file_bindings").is_none());
    let mut explicit = ordinary;
    explicit["file_bindings"] = json!([]);
    let req: TurnRequest = serde_json::from_value(explicit).unwrap();
    assert_eq!(sha256(&serde_json::to_vec(&req).unwrap()), sha256(&before));
    let f = Fixture::new("txt", b"Source for idempotent read-only inspection.");
    let app = router(f.state.clone());
    let body = f.payload(f.binding(1));
    let (s, started) = call(
        &app,
        "POST",
        "/api/agent/turns",
        body.clone(),
        Some("bound-attempt"),
    )
    .await;
    assert_eq!(s, StatusCode::ACCEPTED, "{started}");
    let (s, replay) = call(
        &app,
        "POST",
        "/api/agent/turns",
        body.clone(),
        Some("bound-attempt"),
    )
    .await;
    assert_eq!(s, StatusCode::OK, "{replay}");
    assert_eq!(replay["turn_id"], started["turn_id"]);
    assert_eq!(replay["replayed"], true);
    let url = format!(
        "/api/agent/turns/{}?workspace={}&session_id={}",
        started["turn_id"].as_str().unwrap(),
        f.w,
        "d".repeat(32)
    );
    let (s, saved) = call(&app, "GET", &url, Value::Null, None).await;
    assert_eq!(s, StatusCode::OK);
    assert_eq!(
        saved["turn"]["request"]["file_bindings"],
        body["file_bindings"]
    );
    let mut terminal = false;
    for _ in 0..300 {
        let (_, row) = call(&app, "GET", &url, Value::Null, None).await;
        if matches!(
            row["turn"]["status"].as_str(),
            Some("completed" | "failed" | "cancelled")
        ) {
            terminal = true;
            break;
        }
        tokio::time::sleep(std::time::Duration::from_millis(10)).await;
    }
    assert!(
        terminal,
        "The accepted read-only turn did not settle before history retry checks"
    );
    f.advance(2);
    let (s, _) = call(
        &app,
        "POST",
        "/api/agent/turns",
        body,
        Some("bound-attempt"),
    )
    .await;
    assert_eq!(s, StatusCode::BAD_REQUEST);
    let (s, v) = call(
        &app,
        "POST",
        "/api/agent/turns",
        f.payload(f.binding(2)),
        Some("bound-attempt"),
    )
    .await;
    assert_eq!(s, StatusCode::UNPROCESSABLE_ENTITY, "{v}");
    let (s, _) = call(&app, "GET", &url, Value::Null, None).await;
    assert_eq!(
        s,
        StatusCode::OK,
        "History discovery remains readable without authorizing execution"
    );
}

#[tokio::test]
async fn bound_artifact_readiness_detects_same_byte_new_revision_and_origin_cannot_be_forged() {
    let repo = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .unwrap()
        .to_owned();
    let temp = std::env::temp_dir().join(format!("handoff-pdf-{}", uuid::Uuid::new_v4()));
    std::fs::create_dir(&temp).unwrap();
    let python = std::env::var_os("CIVIL_PYTHON").unwrap_or_else(|| "python".into());
    let made=std::process::Command::new(python).args(["-B","-c","from pathlib import Path; import sys; from scripts.test_document_readiness import pdf; Path(sys.argv[1]).write_bytes(pdf())"]).arg(temp.join("source.pdf")).current_dir(repo).output().unwrap();
    assert!(
        made.status.success(),
        "{}",
        String::from_utf8_lossy(&made.stderr)
    );
    let bytes = std::fs::read(temp.join("source.pdf")).unwrap();
    std::fs::remove_dir_all(temp).unwrap();
    let f = Fixture::new("pdf", &bytes);
    let output = f.ws.output_root().join("copy.pdf");
    std::fs::write(&output, &bytes).unwrap();
    let result = json!({"output_path":output,"source":f.ws.root().join(&f.path),"source_sha256":f.hash,"output_sha256":f.hash,"source_origin":f.binding(1).origin,"validation":{}});
    let artifact = f.state.register_artifact(&f.w, &result).unwrap();
    assert_eq!(artifact["source_origin"], result["source_origin"]);
    let mut forged = result.clone();
    forged["source_origin"]["revision"] = json!(2);
    assert!(f.state.register_artifact(&f.w, &forged).is_err());
    let app = router(f.state.clone());
    let ready = format!(
        "/api/agent/artifacts/{}/readiness?workspace={}",
        artifact["id"].as_str().unwrap(),
        f.w
    );
    let preview = format!(
        "/api/agent/artifacts/{}/preview?workspace={}",
        artifact["id"].as_str().unwrap(),
        f.w
    );
    let (s, r) = call(&app, "POST", &ready, Value::Null, None).await;
    assert_eq!(s, StatusCode::OK, "{r}");
    assert_eq!(r["original_source_status"], "current");
    assert_eq!(r["readiness"]["preview"]["eligible"], true, "{r}");
    let rendered = app
        .clone()
        .oneshot(
            Request::builder()
                .uri(&preview)
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(rendered.status(), StatusCode::OK);
    assert_eq!(rendered.headers()["content-type"], "application/pdf");
    let preview_bytes = rendered.into_body().collect().await.unwrap().to_bytes();
    assert_eq!(preview_bytes.as_ref(), bytes.as_slice());
    let receipt: String = f
        .db()
        .query_row(
            "SELECT record FROM project_imports WHERE workspace=?1 AND package_sha256=?2",
            params![f.w, "c".repeat(64)],
            |r| r.get(0),
        )
        .unwrap();
    f.db()
        .execute("DELETE FROM project_imports WHERE workspace=?1", [&f.w])
        .unwrap();
    let (s, r) = call(&app, "POST", &ready, Value::Null, None).await;
    assert_eq!(s, StatusCode::OK, "{r}");
    assert_eq!(
        r["original_source_status"], "unavailable",
        "Missing proof must not assert a known revision change"
    );
    let (s, _) = call(&app, "GET", &preview, Value::Null, None).await;
    assert_eq!(s, StatusCode::CONFLICT);
    f.db()
        .execute(
            "INSERT INTO project_imports VALUES(?1,?2,?3)",
            params![f.w, "c".repeat(64), receipt],
        )
        .unwrap();
    let (s, r) = call(&app, "POST", &ready, Value::Null, None).await;
    assert_eq!(s, StatusCode::OK, "{r}");
    assert_eq!(r["original_source_status"], "current");
    f.advance(2);
    let (s, _) = call(&app, "GET", &preview, Value::Null, None).await;
    assert_eq!(s, StatusCode::CONFLICT);
    let (s, r) = call(&app, "POST", &ready, Value::Null, None).await;
    assert_eq!(s, StatusCode::OK, "{r}");
    assert_eq!(r["original_source_status"], "changed");
}
