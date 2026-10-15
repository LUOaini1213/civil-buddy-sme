//! Business handoffs cross instances as new drafts, never as restored state.
use axum::{
    body::Body,
    http::{Request, StatusCode},
    Router,
};
use civil_workbench::{
    config::Paths,
    product::{
        api::{router, ProductState},
        auth::InstanceAuth,
    },
};
use http_body_util::BodyExt;
use rusqlite::{params, Connection};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::{
    collections::BTreeMap,
    io::{Cursor, Read, Write},
    path::PathBuf,
    sync::Arc,
};
use tower::ServiceExt;

struct Fixture(PathBuf);
impl Fixture {
    fn new() -> Self {
        let p = std::env::temp_dir().join(format!("civil-handoff-import-{}", uuid::Uuid::new_v4()));
        for d in ["source-demo", "target-demo", "source", "target", "third"] {
            std::fs::create_dir_all(p.join(d)).unwrap();
        }
        Self(p)
    }
    fn source(&self) -> Arc<ProductState> {
        let auth = InstanceAuth::named(
            "source-engineer",
            &"a".repeat(64),
            vec![self.0.join("source")],
            None,
        )
        .unwrap();
        ProductState::open_with_auth(Paths::from_demo(self.0.join("source-demo")), auth).unwrap()
    }
    fn target(&self) -> Arc<ProductState> {
        ProductState::open_with_auth(
            Paths::from_demo(self.0.join("target-demo")),
            InstanceAuth::local(),
        )
        .unwrap()
    }
    fn db(&self) -> Connection {
        Connection::open(self.0.join("target-demo/data/unified/index.sqlite")).unwrap()
    }
}
impl Drop for Fixture {
    fn drop(&mut self) {
        // Read-only original handoff manifests are intentionally durable on success.
        fn clean(p: &std::path::Path) {
            if let Ok(items) = std::fs::read_dir(p) {
                for i in items.flatten() {
                    let path = i.path();
                    if path.is_dir() {
                        clean(&path);
                    } else {
                        #[cfg(windows)]
                        {
                            let mut perm = i.metadata().unwrap().permissions();
                            perm.set_readonly(false);
                            let _ = std::fs::set_permissions(&path, perm);
                        }
                        let _ = std::fs::remove_file(path);
                    }
                }
            }
            let _ = std::fs::remove_dir(p);
        }
        clean(&self.0);
    }
}
fn register(state: &ProductState, path: PathBuf) -> String {
    state.register(path.to_str().unwrap()).unwrap()["id"]
        .as_str()
        .unwrap()
        .into()
}
async fn raw(
    app: &Router,
    method: &str,
    url: &str,
    body: Vec<u8>,
    content_type: &str,
) -> (StatusCode, Vec<u8>) {
    let response = app
        .clone()
        .oneshot(
            Request::builder()
                .method(method)
                .uri(url)
                .header("Content-Type", content_type)
                .body(Body::from(body))
                .unwrap(),
        )
        .await
        .unwrap();
    (
        response.status(),
        response
            .into_body()
            .collect()
            .await
            .unwrap()
            .to_bytes()
            .to_vec(),
    )
}
async fn json_call(app: &Router, method: &str, url: &str, value: Value) -> (StatusCode, Value) {
    let (s, b) = raw(
        app,
        method,
        url,
        value.to_string().into_bytes(),
        "application/json",
    )
    .await;
    (
        s,
        serde_json::from_slice(&b).unwrap_or_else(|_| panic!("{}", String::from_utf8_lossy(&b))),
    )
}
fn digest(bytes: &[u8]) -> String {
    format!("{:x}", Sha256::digest(bytes))
}
async fn upload(
    app: &Router,
    endpoint: &str,
    w: &str,
    zip: &[u8],
    hash: Option<&str>,
    write: Option<&str>,
) -> (StatusCode, Value) {
    let mut b = Vec::new();
    for (k, v) in [
        ("workspace", Some(w)),
        ("expected_sha256", hash),
        ("allow_workspace_write", write),
    ] {
        if let Some(v) = v {
            write!(
                &mut b,
                "--handoff-boundary\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n"
            )
            .unwrap();
        }
    }
    b.extend_from_slice(b"--handoff-boundary\r\nContent-Disposition: form-data; name=\"file\"; filename=\"handoff.zip\"\r\nContent-Type: application/zip\r\n\r\n");
    b.extend_from_slice(zip);
    b.extend_from_slice(b"\r\n--handoff-boundary--\r\n");
    let (s, b) = raw(
        app,
        "POST",
        endpoint,
        b,
        "multipart/form-data; boundary=handoff-boundary",
    )
    .await;
    (
        s,
        serde_json::from_slice(&b).unwrap_or_else(|_| json!({"raw":String::from_utf8_lossy(&b)})),
    )
}
async fn export(app: &Router, w: &str) -> Vec<u8> {
    let (s, b) = raw(
        app,
        "GET",
        &format!("/api/project-control/package?workspace={w}"),
        Vec::new(),
        "application/json",
    )
    .await;
    assert_eq!(s, StatusCode::OK, "{}", String::from_utf8_lossy(&b));
    b
}
fn unpack(bytes: &[u8]) -> BTreeMap<String, Vec<u8>> {
    let mut z = zip::ZipArchive::new(Cursor::new(bytes)).unwrap();
    (0..z.len())
        .map(|i| {
            let mut f = z.by_index(i).unwrap();
            let name = f.name().to_owned();
            let mut b = Vec::new();
            f.read_to_end(&mut b).unwrap();
            (name, b)
        })
        .collect()
}
fn pack(files: BTreeMap<String, Vec<u8>>) -> Vec<u8> {
    let mut z = zip::ZipWriter::new(Cursor::new(Vec::new()));
    for (name, b) in files {
        z.start_file(name, zip::write::SimpleFileOptions::default())
            .unwrap();
        z.write_all(&b).unwrap();
    }
    z.finish().unwrap().into_inner()
}
fn mutate(bytes: &[u8], f: impl FnOnce(&mut Value)) -> Vec<u8> {
    let mut files = unpack(bytes);
    let mut m: Value = serde_json::from_slice(&files["manifest.json"]).unwrap();
    f(&mut m);
    files.insert("manifest.json".into(), serde_json::to_vec(&m).unwrap());
    pack(files)
}
async fn seeded(f: &Fixture, app: &Router, w: &str) -> (Value, Value) {
    std::fs::write(
        f.0.join("source/detail.md"),
        "Original revision A: inspect the restraint detail",
    )
    .unwrap();
    let (s,doc)=json_call(app,"POST","/api/project-control/documents",json!({"workspace":w,"path":"detail.md","title":"Facade detail 原始图","discipline":"Facade","revision_label":"A"})).await;
    assert_eq!(s, StatusCode::OK, "{doc}");
    let (s,issue)=json_call(app,"POST","/api/project-control/issues",json!({"workspace":w,"document_id":doc["id"],"document_revision":1,"kind":"rfi","title":"Resolve detail","description":"Check installation arrangement","assignee":"Engineer B","due_date":"2026-10-30","status":"open","resolution":""})).await;
    assert_eq!(s, StatusCode::OK, "{issue}");
    (doc, issue)
}
async fn overview(app: &Router, w: &str) -> Value {
    let (s, v) = json_call(
        app,
        "GET",
        &format!("/api/project-control?workspace={w}&complete=true"),
        Value::Null,
    )
    .await;
    assert_eq!(s, StatusCode::OK);
    v
}

#[tokio::test]
async fn preview_is_readonly_commit_uses_new_ids_actor_and_workspace_bound_replay() {
    let f = Fixture::new();
    let source = f.source();
    let target = f.target();
    let sw = register(&source, f.0.join("source"));
    let tw = register(&target, f.0.join("target"));
    let third = register(&target, f.0.join("third"));
    let sa = router(source);
    let ta = router(target);
    let (doc, issue) = seeded(&f, &sa, &sw).await;
    let bytes = export(&sa, &sw).await;
    let (s, p) = upload(
        &ta,
        "/api/project-control/package/preview",
        &tw,
        &bytes,
        None,
        None,
    )
    .await;
    assert_eq!(s, StatusCode::OK, "{p}");
    assert_eq!(p["source"]["workspace"], sw);
    assert_eq!(p["review_reset"], true);
    assert_eq!(p["document_count"], 1);
    assert_eq!(p["issue_count"], 1);
    assert!(!f.0.join("target/.civil-buddy").exists());
    assert!(overview(&ta, &tw).await["documents"]
        .as_array()
        .unwrap()
        .is_empty());
    let hash = p["package_sha256"].as_str().unwrap();
    let (s, r) = upload(
        &ta,
        "/api/project-control/package/import",
        &tw,
        &bytes,
        Some(hash),
        Some("true"),
    )
    .await;
    assert_eq!(s, StatusCode::OK, "{r}");
    assert_eq!(r["replayed"], false);
    let v = overview(&ta, &tw).await;
    let d = &v["documents"][0];
    let i = &v["issues"][0];
    assert_ne!(d["id"], doc["id"]);
    assert_ne!(i["id"], issue["id"]);
    assert_eq!(d["workspace"], tw);
    assert_eq!(d["revision"], 1);
    assert_eq!(d["status"], "work_in_progress");
    assert!(d.get("review").is_none());
    assert_eq!(d["actor_id"], "local-owner");
    assert_eq!(i["actor_id"], "local-owner");
    assert_eq!(i["document_id"], d["id"]);
    assert_eq!(i["document_revision"], 1);
    assert_eq!(i["evidence_status"], "current");
    assert_eq!(d["source_status"], "current");
    assert!(v["events"]
        .as_array()
        .unwrap()
        .iter()
        .all(|e| e["actor_id"] == "local-owner"));
    assert_eq!(v["events"].as_array().unwrap().len(), 3);
    let local = f.0.join("target").join(d["path"].as_str().unwrap());
    assert_eq!(
        std::fs::read(local).unwrap(),
        std::fs::read(f.0.join("source/detail.md")).unwrap()
    );
    let (s, replay) = upload(
        &ta,
        "/api/project-control/package/import",
        &tw,
        &bytes,
        Some(hash),
        Some("true"),
    )
    .await;
    assert_eq!(s, StatusCode::OK);
    assert_eq!(replay["import_id"], r["import_id"]);
    assert_eq!(replay["replayed"], true);
    assert_eq!(
        overview(&ta, &tw).await["documents"]
            .as_array()
            .unwrap()
            .len(),
        1
    );
    let (s, r2) = upload(
        &ta,
        "/api/project-control/package/import",
        &third,
        &bytes,
        Some(hash),
        Some("true"),
    )
    .await;
    assert_eq!(s, StatusCode::OK, "{r2}");
    assert_ne!(r2["import_id"], r["import_id"]);
    assert_eq!(
        overview(&ta, &third).await["documents"]
            .as_array()
            .unwrap()
            .len(),
        1
    );
    let db = f.db();
    let stored: String = db
        .query_row(
            "SELECT record FROM project_imports WHERE workspace=?1 AND package_sha256=?2",
            params![tw, hash],
            |r| r.get(0),
        )
        .unwrap();
    let stored: Value = serde_json::from_str(&stored).unwrap();
    assert_eq!(
        stored["document_mapping"][0]["source_document_id"],
        doc["id"]
    );
    assert_eq!(stored["issue_mapping"][0]["source_issue_id"], issue["id"]);
}

#[tokio::test]
async fn reviews_reset_and_old_evidence_survives_second_export_import_then_explicit_rebase() {
    let f = Fixture::new();
    let source = f.source();
    let target = f.target();
    let sw = register(&source, f.0.join("source"));
    let tw = register(&target, f.0.join("target"));
    let third = register(&target, f.0.join("third"));
    let sa = router(source);
    let ta = router(target);
    let (doc, issue) = seeded(&f, &sa, &sw).await;
    let(s,resolved)=json_call(&sa,"POST","/api/project-control/issues",json!({"workspace":sw,"id":issue["id"],"expected_version":1,"document_id":doc["id"],"document_revision":1,"kind":"rfi","title":"Resolve detail","description":"Check installation arrangement","assignee":"Engineer B","due_date":"2026-10-30","status":"resolved","resolution":"Engineer checked original detail"})).await;
    assert_eq!(s, StatusCode::OK, "{resolved}");
    let current = overview(&sa, &sw).await;
    let(s,review)=json_call(&sa,"POST",&format!("/api/project-control/documents/{}/review",doc["id"].as_str().unwrap()),json!({"workspace":sw,"expected_revision":1,"expected_review_version":current["documents"][0]["review_version"],"notes":"Checked original detail"})).await;
    assert_eq!(s, StatusCode::OK, "{review}");
    let reviewed_bytes = export(&sa, &sw).await;
    let (s, r) = upload(
        &ta,
        "/api/project-control/package/import",
        &third,
        &reviewed_bytes,
        Some(&digest(&reviewed_bytes)),
        Some("true"),
    )
    .await;
    assert_eq!(s, StatusCode::OK, "{r}");
    let reviewed_target = overview(&ta, &third).await;
    assert_eq!(
        reviewed_target["documents"][0]["status"],
        "work_in_progress"
    );
    assert!(reviewed_target["documents"][0].get("review").is_none());
    assert_eq!(reviewed_target["issues"][0]["status"], "review_required");
    std::fs::write(
        f.0.join("source/detail.md"),
        "Revision B changes the restraint detail",
    )
    .unwrap();
    let(s,v)=json_call(&sa,"POST","/api/project-control/documents",json!({"workspace":sw,"document_id":doc["id"],"expected_revision":1,"path":"detail.md","title":"Facade detail 原始图","discipline":"Facade","revision_label":"B"})).await;
    assert_eq!(s, StatusCode::OK, "{v}");
    let bytes = export(&sa, &sw).await;
    let (s, p) = upload(
        &ta,
        "/api/project-control/package/preview",
        &tw,
        &bytes,
        None,
        None,
    )
    .await;
    assert_eq!(s, StatusCode::OK, "{p}");
    assert_eq!(p["missing_evidence_count"], 1);
    let (s, r) = upload(
        &ta,
        "/api/project-control/package/import",
        &tw,
        &bytes,
        Some(&digest(&bytes)),
        Some("true"),
    )
    .await;
    assert_eq!(s, StatusCode::OK, "{r}");
    let v = overview(&ta, &tw).await;
    let i = &v["issues"][0];
    let d = &v["documents"][0];
    assert_eq!(i["document_revision"], 0);
    assert_eq!(i["evidence_status"], "unavailable");
    assert_eq!(i["source_sha256"], issue["source_sha256"]);
    assert_eq!(i["provenance"]["source_document_revision"], 1);
    assert_ne!(i["source_sha256"], d["sha256"]);
    let again = export(&ta, &tw).await;
    let (s, p) = upload(
        &ta,
        "/api/project-control/package/preview",
        &third,
        &again,
        None,
        None,
    )
    .await;
    assert_eq!(s, StatusCode::OK, "{p}");
    assert_eq!(p["missing_evidence_count"], 1);
    let (s, r2) = upload(
        &ta,
        "/api/project-control/package/import",
        &third,
        &again,
        Some(&digest(&again)),
        Some("true"),
    )
    .await;
    assert_eq!(s, StatusCode::OK, "{r2}");
    assert_eq!(r2["missing_evidence_count"], 1);
    let(s,edited)=json_call(&ta,"POST","/api/project-control/issues",json!({"workspace":tw,"id":i["id"],"expected_version":1,"document_id":d["id"],"document_revision":1,"kind":"rfi","title":"Resolve detail","description":"Check installation arrangement","assignee":"Engineer B","due_date":"2026-10-30","status":"in_progress","resolution":"Explicitly checking imported current source"})).await;
    assert_eq!(s, StatusCode::OK, "{edited}");
    assert_eq!(edited["evidence_status"], "current");
    assert_eq!(edited["document_revision"], 1);
    assert_eq!(edited["source_sha256"], d["sha256"]);
    assert_eq!(edited["provenance"], i["provenance"]);
    let path = f.0.join("target").join(d["path"].as_str().unwrap());
    std::fs::write(&path, "Local revision after explicit edit").unwrap();
    let(s,next)=json_call(&ta,"POST","/api/project-control/documents",json!({"workspace":tw,"document_id":d["id"],"expected_revision":1,"path":d["path"],"title":d["title"],"discipline":d["discipline"],"revision_label":"Local C"})).await;
    assert_eq!(s, StatusCode::OK, "{next}");
    assert_eq!(next["provenance"], d["provenance"]);
}

#[tokio::test]
async fn corruption_unknown_members_duplicate_json_and_metadata_are_rejected_without_writes() {
    let f = Fixture::new();
    let source = f.source();
    let target = f.target();
    let sw = register(&source, f.0.join("source"));
    let tw = register(&target, f.0.join("target"));
    let sa = router(source);
    let ta = router(target);
    seeded(&f, &sa, &sw).await;
    let bytes = export(&sa, &sw).await;
    let mut cases = Vec::new();
    for change in [0, 1, 2, 3, 4, 5, 6, 7, 8] {
        cases.push(mutate(&bytes,|m|match change {0=>m["documents"][0]["sha256"]=json!("0".repeat(64)),1=>m["audit"]["event_count"]=json!(999),2=>m["issues"][0]["document_id"]=json!("f".repeat(32)),3=>m["issue_count"]=json!(2),4=>m["documents"][0]["snapshot"]["archive_path"]=json!("../detail.md"),5=>m["issues"][0]["due_date"]=json!("2026-02-30"),6=>m["documents"][0]["path"]=json!("credentials.json"),7=>m["issues"][0]["source_sha256"]=json!("1".repeat(64)),_=>m["documents"][0]["review"]=json!({"actor_id":"fake","at":"2026-10-01T00:00:00Z","notes":"Fake statutory approval","sha256":"0".repeat(64),"basis_version":1,"statutory_signoff":true})}));
    }
    for name in [
        "extra.json",
        "../escape.md",
        ".env",
        "sources/bad/credentials.json",
    ] {
        let mut fs = unpack(&bytes);
        fs.insert(name.into(), b"do not extract".to_vec());
        cases.push(pack(fs));
    }
    let mut fs = unpack(&bytes);
    let old = fs["manifest.json"].clone();
    let text = String::from_utf8(old).unwrap();
    fs.insert(
        "manifest.json".into(),
        text.replacen('{', "{\"schema_version\":1,", 1).into_bytes(),
    );
    cases.push(pack(fs));
    for b in cases {
        let (s, v) = upload(
            &ta,
            "/api/project-control/package/preview",
            &tw,
            &b,
            None,
            None,
        )
        .await;
        assert_ne!(s, StatusCode::OK, "{v}");
        let (s, v) = upload(
            &ta,
            "/api/project-control/package/import",
            &tw,
            &b,
            Some(&digest(&b)),
            Some("true"),
        )
        .await;
        assert_ne!(s, StatusCode::OK, "{v}");
    }
    assert!(!f.0.join("target/.civil-buddy").exists());
    assert_eq!(overview(&ta, &tw).await["documents"], json!([]));
    assert!(!f.0.join("escape.md").exists());
}

#[tokio::test]
async fn import_permission_hash_unknown_workspace_and_global_json_limit_remain_enforced() {
    let f = Fixture::new();
    let source = f.source();
    let target = f.target();
    let sw = register(&source, f.0.join("source"));
    let tw = register(&target, f.0.join("target"));
    let sa = router(source);
    let ta = router(target);
    seeded(&f, &sa, &sw).await;
    let bytes = export(&sa, &sw).await;
    let hash = digest(&bytes);
    for write in [None, Some("false"), Some("TRUE")] {
        let (s, _) = upload(
            &ta,
            "/api/project-control/package/import",
            &tw,
            &bytes,
            Some(&hash),
            write,
        )
        .await;
        assert_eq!(s, StatusCode::FORBIDDEN);
    }
    let (s, _) = upload(
        &ta,
        "/api/project-control/package/import",
        &tw,
        &bytes,
        Some(&"0".repeat(64)),
        Some("true"),
    )
    .await;
    assert_eq!(s, StatusCode::CONFLICT);
    let (s, _) = upload(
        &ta,
        "/api/project-control/package/import",
        &"f".repeat(32),
        &bytes,
        Some(&hash),
        Some("true"),
    )
    .await;
    assert_ne!(s, StatusCode::OK);
    let (s, _) = raw(
        &ta,
        "POST",
        "/api/project-control/documents",
        vec![b' '; 65 * 1024],
        "application/json",
    )
    .await;
    assert_eq!(s, StatusCode::PAYLOAD_TOO_LARGE);
    assert!(!f.0.join("target/.civil-buddy").exists());
    // Upload over 64KiB succeeds on only the dedicated multipart routes.
    std::fs::write(f.0.join("source/detail.md"), "x".repeat(96 * 1024)).unwrap();
    let v = overview(&sa, &sw).await;
    let d = &v["documents"][0];
    let(s,_)=json_call(&sa,"POST","/api/project-control/documents",json!({"workspace":sw,"document_id":d["id"],"expected_revision":1,"path":"detail.md","title":"Large source","discipline":"Facade","revision_label":"B"})).await;
    assert_eq!(s, StatusCode::OK);
    let larger = export(&sa, &sw).await;
    assert!(larger.len() > 64 * 1024);
    let (s, v) = upload(
        &ta,
        "/api/project-control/package/preview",
        &tw,
        &larger,
        None,
        None,
    )
    .await;
    assert_eq!(s, StatusCode::OK, "{v}");
}

#[tokio::test]
async fn capacity_and_transaction_failure_leave_no_visible_records_or_owned_files() {
    let f = Fixture::new();
    let source = f.source();
    let target = f.target();
    let sw = register(&source, f.0.join("source"));
    let tw = register(&target, f.0.join("target"));
    let sa = router(source);
    let ta = router(target);
    seeded(&f, &sa, &sw).await;
    let bytes = export(&sa, &sw).await;
    let hash = digest(&bytes);
    let db = f.db();
    for n in 0..100 {
        db.execute(
            "INSERT INTO project_documents VALUES(?1,?2,'{}')",
            params![tw, format!("{n:032x}")],
        )
        .unwrap();
    }
    let (s, _) = upload(
        &ta,
        "/api/project-control/package/preview",
        &tw,
        &bytes,
        None,
        None,
    )
    .await;
    assert_eq!(s, StatusCode::CONFLICT);
    let (s, _) = upload(
        &ta,
        "/api/project-control/package/import",
        &tw,
        &bytes,
        Some(&hash),
        Some("true"),
    )
    .await;
    assert_eq!(s, StatusCode::CONFLICT);
    assert!(!f.0.join("target/.civil-buddy").exists());
    db.execute("DELETE FROM project_documents", []).unwrap();
    db.execute_batch("CREATE TRIGGER deny_import_issue BEFORE INSERT ON project_events WHEN json_extract(NEW.record,'$.kind')='project_handoff_imported' BEGIN SELECT RAISE(ABORT,'fixture atomic rollback'); END;").unwrap();
    let (s, _) = upload(
        &ta,
        "/api/project-control/package/import",
        &tw,
        &bytes,
        Some(&hash),
        Some("true"),
    )
    .await;
    assert_eq!(s, StatusCode::INTERNAL_SERVER_ERROR);
    for table in [
        "project_documents",
        "project_revisions",
        "project_issues",
        "project_events",
        "project_imports",
    ] {
        let count: i64 = db
            .query_row(
                &format!("SELECT count(*) FROM {table} WHERE workspace=?1"),
                [&tw],
                |r| r.get(0),
            )
            .unwrap();
        assert_eq!(count, 0, "{table}");
    }
    let folder = f.0.join("target/.civil-buddy/out/handoffs");
    assert!(folder.is_dir());
    assert_eq!(std::fs::read_dir(&folder).unwrap().count(), 0);
    db.execute_batch("DROP TRIGGER deny_import_issue").unwrap();
    let (s, r) = upload(
        &ta,
        "/api/project-control/package/import",
        &tw,
        &bytes,
        Some(&hash),
        Some("true"),
    )
    .await;
    assert_eq!(s, StatusCode::OK, "{r}");
}

#[tokio::test]
async fn source_audit_is_inert_and_hash_order_and_count_are_verified() {
    let f = Fixture::new();
    let source = f.source();
    let target = f.target();
    let sw = register(&source, f.0.join("source"));
    let tw = register(&target, f.0.join("target"));
    let sa = router(source);
    let ta = router(target);
    seeded(&f, &sa, &sw).await;
    let bytes = export(&sa, &sw).await;
    fn audit_package(bytes: &[u8], audit: &[u8], count: u64) -> Vec<u8> {
        let mut files = unpack(bytes);
        let mut m: Value = serde_json::from_slice(&files["manifest.json"]).unwrap();
        m["audit"]["sha256"] = json!(digest(audit));
        m["audit"]["event_count"] = json!(count);
        files.insert("audit.jsonl".into(), audit.to_vec());
        files.insert("manifest.json".into(), serde_json::to_vec(&m).unwrap());
        pack(files)
    }
    let valid = audit_package(
        &bytes,
        b"{\"sequence\":42,\"kind\":\"synthetic_test_event\",\"number\":1}\n",
        1,
    );
    let (s, r) = upload(
        &ta,
        "/api/project-control/package/import",
        &tw,
        &valid,
        Some(&digest(&valid)),
        Some("true"),
    )
    .await;
    assert_eq!(s, StatusCode::OK, "{r}");
    assert!(overview(&ta, &tw).await["events"]
        .as_array()
        .unwrap()
        .iter()
        .all(|e| e["kind"] != "synthetic_test_event"));
    for (audit, count) in [
        (b"{\"sequence\":2}\n{\"sequence\":1}\n".as_slice(), 2),
        (b"{\"sequence\":2}\n\n".as_slice(), 1),
        (b"{\"sequence\":2,\"sequence\":3}\n".as_slice(), 1),
        (b"{\"sequence\":2}".as_slice(), 1),
        (b"{\"sequence\":2}\n".as_slice(), 2),
    ] {
        let b = audit_package(&bytes, audit, count);
        let (s, v) = upload(
            &ta,
            "/api/project-control/package/preview",
            &tw,
            &b,
            None,
            None,
        )
        .await;
        assert_ne!(s, StatusCode::OK, "{v}");
    }
}

#[tokio::test]
async fn two_thousand_issue_boundary_reexports_and_metadata_is_in_cumulative_quota() {
    let f = Fixture::new();
    let source = f.source();
    let target = f.target();
    let sw = register(&source, f.0.join("source"));
    let tw = register(&target, f.0.join("target"));
    let third = register(&target, f.0.join("third"));
    let sa = router(source);
    let ta = router(target);
    seeded(&f, &sa, &sw).await;
    let original = export(&sa, &sw).await;
    let bytes = mutate(&original, |m| {
        let template = m["issues"][0].clone();
        m["issues"] = Value::Array(
            (0..2000)
                .map(|n| {
                    let mut i = template.clone();
                    i["id"] = json!(format!("{n:032x}"));
                    i
                })
                .collect(),
        );
        m["issue_count"] = json!(2000);
        m["unresolved_issues"] = json!(2000);
    });
    let (s, r) = upload(
        &ta,
        "/api/project-control/package/import",
        &tw,
        &bytes,
        Some(&digest(&bytes)),
        Some("true"),
    )
    .await;
    assert_eq!(s, StatusCode::OK, "{r}");
    assert_eq!(r["issue_count"], 2000);
    assert_eq!(r["issue_mapping"].as_array().unwrap().len(), 2000);
    let (s, same_preview) = upload(
        &ta,
        "/api/project-control/package/preview",
        &tw,
        &bytes,
        None,
        None,
    )
    .await;
    assert_eq!(s, StatusCode::OK, "{same_preview}");
    assert_eq!(same_preview["issue_count"], 2000);
    let files = unpack(&bytes);
    assert_eq!(
        r["source_metadata_bytes"],
        (files["manifest.json"].len() + files["audit.jsonl"].len()) as u64
    );
    let db = f.db();
    let max_event: i64 = db
        .query_row(
            "SELECT max(length(CAST(record AS BLOB))) FROM project_events WHERE workspace=?1",
            [&tw],
            |r| r.get(0),
        )
        .unwrap();
    assert!(max_event < 256 * 1024);
    let exported = export(&ta, &tw).await;
    let (s, p) = upload(
        &ta,
        "/api/project-control/package/preview",
        &third,
        &exported,
        None,
        None,
    )
    .await;
    assert_eq!(s, StatusCode::OK, "{p}");
    assert_eq!(p["issue_count"], 2000);
    let changed = mutate(&bytes, |m| {
        m["documents"][0]["title"] = json!("Different package same issue count")
    });
    let (s, _) = upload(
        &ta,
        "/api/project-control/package/preview",
        &tw,
        &changed,
        None,
        None,
    )
    .await;
    assert_eq!(s, StatusCode::CONFLICT);
    // Seed the durable metadata ledger at its explicit byte boundary without
    // allocating half a gigabyte. Capacity considers it for both import and
    // ordinary revision registration, then replay remains non-mutating.
    db.execute(
        "INSERT INTO project_imports(workspace,package_sha256,record) VALUES(?1,?2,?3)",
        params![
            third,
            "f".repeat(64),
            json!({"source_metadata_bytes":512*1024*1024u64}).to_string()
        ],
    )
    .unwrap();
    let (s, _) = upload(
        &ta,
        "/api/project-control/package/preview",
        &third,
        &original,
        None,
        None,
    )
    .await;
    assert_eq!(s, StatusCode::CONFLICT);
    assert!(!f.0.join("third/.civil-buddy").exists());
    std::fs::write(f.0.join("third/check.md"), "Explicit new revision").unwrap();
    let(s,_)=json_call(&ta,"POST","/api/project-control/documents",json!({"workspace":third,"path":"check.md","title":"Check","discipline":"Site","revision_label":"A"})).await;
    assert_eq!(s, StatusCode::BAD_REQUEST);
    let (s, replay) = upload(
        &ta,
        "/api/project-control/package/import",
        &tw,
        &bytes,
        Some(&digest(&bytes)),
        Some("true"),
    )
    .await;
    assert_eq!(s, StatusCode::OK);
    assert_eq!(replay["replayed"], true);
}

#[tokio::test]
async fn duplicate_zip_names_and_nonregular_members_are_rejected() {
    let f = Fixture::new();
    let source = f.source();
    let target = f.target();
    let sw = register(&source, f.0.join("source"));
    let tw = register(&target, f.0.join("target"));
    let sa = router(source);
    let ta = router(target);
    seeded(&f, &sa, &sw).await;
    let bytes = export(&sa, &sw).await;
    let (s, baseline) = upload(
        &ta,
        "/api/project-control/package/preview",
        &tw,
        &bytes,
        None,
        None,
    )
    .await;
    assert_eq!(s, StatusCode::OK, "{baseline}");
    let mut fs = unpack(&bytes);
    let manifest: Value = serde_json::from_slice(&fs["manifest.json"]).unwrap();
    let source_name = manifest["documents"][0]["snapshot"]["archive_path"]
        .as_str()
        .unwrap()
        .to_owned();
    let (prefix, basename) = source_name.rsplit_once('/').unwrap();
    let alias_name = format!("{prefix}/z{}", &basename[1..]);
    assert_ne!(alias_name, source_name);
    let original_payload = fs[&source_name].clone();
    fs.insert(alias_name.clone(), original_payload);
    let mut duplicate = pack(fs);
    let from = alias_name.as_bytes();
    let to = source_name.as_bytes();
    assert_eq!(from.len(), to.len());
    let mut offset = 0;
    while let Some(pos) = duplicate[offset..]
        .windows(from.len())
        .position(|b| b == from)
    {
        let at = offset + pos;
        duplicate[at..at + from.len()].copy_from_slice(to);
        offset = at + from.len();
    }
    let inspected = zip::ZipArchive::new(Cursor::new(&duplicate)).unwrap();
    let mut duplicate_count = 0;
    let mut at = inspected.central_directory_start() as usize;
    // zip 2 folds identical names into its public index. Inspect the raw
    // directory so this assertion establishes the actual adversarial sample.
    while duplicate[at..].starts_with(b"PK\x01\x02") {
        let n = u16::from_le_bytes([duplicate[at + 28], duplicate[at + 29]]) as usize;
        let e = u16::from_le_bytes([duplicate[at + 30], duplicate[at + 31]]) as usize;
        let c = u16::from_le_bytes([duplicate[at + 32], duplicate[at + 33]]) as usize;
        if &duplicate[at + 46..at + 46 + n] == source_name.as_bytes() {
            duplicate_count += 1;
        }
        at += 46 + n + e + c;
    }
    assert_eq!(
        duplicate_count, 2,
        "Attack must duplicate a genuinely referenced snapshot"
    );
    let (s, rejected) = upload(
        &ta,
        "/api/project-control/package/preview",
        &tw,
        &duplicate,
        None,
        None,
    )
    .await;
    assert_eq!(s, StatusCode::BAD_REQUEST);
    assert_eq!(
        rejected["detail"],
        "Duplicate, linked or unsafe handoff member"
    );
    let mut linked = pack(unpack(&bytes));
    // Set the central-directory Unix file type directly: SimpleFileOptions
    // deliberately masks file-type bits, whereas an untrusted ZIP need not.
    let mut offset = 0;
    while offset + 46 < linked.len() {
        if linked[offset..].starts_with(b"PK\x01\x02") {
            let n = u16::from_le_bytes([linked[offset + 28], linked[offset + 29]]) as usize;
            let e = u16::from_le_bytes([linked[offset + 30], linked[offset + 31]]) as usize;
            let c = u16::from_le_bytes([linked[offset + 32], linked[offset + 33]]) as usize;
            if &linked[offset + 46..offset + 46 + n] == source_name.as_bytes() {
                linked[offset + 5] = 3;
                linked[offset + 38..offset + 42]
                    .copy_from_slice(&(0o120777u32 << 16).to_le_bytes());
            }
            offset += 46 + n + e + c;
        } else {
            offset += 1;
        }
    }
    let mut inspected = zip::ZipArchive::new(Cursor::new(&linked)).unwrap();
    assert_eq!(
        inspected
            .by_name(&source_name)
            .unwrap()
            .unix_mode()
            .unwrap()
            & 0o170000,
        0o120000,
        "Attack must change the referenced snapshot's file type"
    );
    let (s, rejected) = upload(
        &ta,
        "/api/project-control/package/preview",
        &tw,
        &linked,
        None,
        None,
    )
    .await;
    assert_eq!(s, StatusCode::BAD_REQUEST);
    assert_eq!(
        rejected["detail"],
        "Duplicate, linked or unsafe handoff member"
    );
    // A local header must name the same referenced source, even though common
    // ZIP readers trust only the central-directory spelling.
    let mut inspected = zip::ZipArchive::new(Cursor::new(&bytes)).unwrap();
    let source = inspected.by_name(&source_name).unwrap();
    let local = source.header_start() as usize;
    let central = source.central_header_start() as usize;
    drop(source);
    let mut mismatched = bytes.clone();
    mismatched[local + 30] = b'x';
    let (s, v) = upload(
        &ta,
        "/api/project-control/package/preview",
        &tw,
        &mismatched,
        None,
        None,
    )
    .await;
    assert_eq!(s, StatusCode::BAD_REQUEST);
    assert_eq!(
        v["detail"],
        "Handoff ZIP local and central headers disagree"
    );
    // Refuse even a well-formed central timestamp extension before asking the
    // ZIP library to parse metadata. Native Civil Buddy exports have no extra.
    let mut extended = bytes.clone();
    let n = u16::from_le_bytes([extended[central + 28], extended[central + 29]]) as usize;
    assert_eq!(
        u16::from_le_bytes([extended[central + 30], extended[central + 31]]),
        0
    );
    let original_footer = extended.len() - 22;
    assert_eq!(
        &extended[original_footer..original_footer + 4],
        b"PK\x05\x06"
    );
    let original_size = u32::from_le_bytes(
        extended[original_footer + 12..original_footer + 16]
            .try_into()
            .unwrap(),
    );
    extended[central + 30..central + 32].copy_from_slice(&5u16.to_le_bytes());
    extended.splice(central + 46 + n..central + 46 + n, [0x55, 0x54, 1, 0, 0]);
    let footer = original_footer + 5;
    extended[footer + 12..footer + 16].copy_from_slice(&(original_size + 5).to_le_bytes());
    let (s, v) = upload(
        &ta,
        "/api/project-control/package/preview",
        &tw,
        &extended,
        None,
        None,
    )
    .await;
    assert_eq!(s, StatusCode::BAD_REQUEST);
    assert!(v["detail"]
        .as_str()
        .unwrap()
        .starts_with("Unsupported extended or encrypted ZIP metadata"));
    // A fake later footer inside a comment must not steer the library toward
    // another directory or a huge allocation before validation.
    let mut fake_footer = vec![0; 22];
    fake_footer[..4].copy_from_slice(b"PK\x05\x06");
    fake_footer[8..12].copy_from_slice(&[255, 255, 255, 255]);
    fake_footer[16..20].copy_from_slice(&u32::MAX.to_le_bytes());
    fake_footer.extend_from_slice(b"trailing comment");
    let mut ambiguous = bytes.clone();
    ambiguous[original_footer + 20..original_footer + 22]
        .copy_from_slice(&(fake_footer.len() as u16).to_le_bytes());
    ambiguous.extend_from_slice(&fake_footer);
    let (s, v) = upload(
        &ta,
        "/api/project-control/package/preview",
        &tw,
        &ambiguous,
        None,
        None,
    )
    .await;
    assert_eq!(s, StatusCode::BAD_REQUEST);
    assert_eq!(v["detail"], "Ambiguous handoff ZIP footer comment");
    assert!(!f.0.join("target/.civil-buddy").exists());
}

#[cfg(unix)]
#[tokio::test]
async fn linked_output_root_is_denied_without_touching_external_files() {
    use std::os::unix::fs::symlink;
    let f = Fixture::new();
    let source = f.source();
    let target = f.target();
    let sw = register(&source, f.0.join("source"));
    let tw = register(&target, f.0.join("target"));
    let sa = router(source);
    let ta = router(target);
    seeded(&f, &sa, &sw).await;
    let bytes = export(&sa, &sw).await;
    let outside = f.0.join("external");
    std::fs::create_dir(&outside).unwrap();
    std::fs::write(outside.join("original.md"), "Preserve original").unwrap();
    symlink(&outside, f.0.join("target/.civil-buddy")).unwrap();
    let (s, _) = upload(
        &ta,
        "/api/project-control/package/import",
        &tw,
        &bytes,
        Some(&digest(&bytes)),
        Some("true"),
    )
    .await;
    assert_ne!(s, StatusCode::OK);
    assert_eq!(
        std::fs::read_to_string(outside.join("original.md")).unwrap(),
        "Preserve original"
    );
    assert_eq!(std::fs::read_dir(&outside).unwrap().count(), 1);
    std::fs::remove_file(f.0.join("target/.civil-buddy")).unwrap();
}
