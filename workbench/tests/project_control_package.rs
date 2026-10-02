//! Real ZIP responses, independent payload digests and workspace boundaries.
use axum::{
    body::Body,
    http::{HeaderMap, Request, StatusCode},
    Router,
};
use civil_workbench::{
    config::Paths,
    product::{
        api::{router, ProductState},
        auth::{self, InstanceAuth},
    },
};
use http_body_util::BodyExt;
use rusqlite::{params, Connection};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::{
    collections::BTreeMap,
    io::{Cursor, Read},
    path::PathBuf,
    sync::Arc,
};
use tower::ServiceExt;

struct Fixture(PathBuf);
impl Fixture {
    fn new() -> Self {
        let path =
            std::env::temp_dir().join(format!("civil-project-package-{}", uuid::Uuid::new_v4()));
        for dir in ["demo", "project", "other"] {
            std::fs::create_dir_all(path.join(dir)).unwrap();
        }
        Self(path)
    }
    fn open(&self) -> Arc<ProductState> {
        ProductState::open_with_auth(Paths::from_demo(self.0.join("demo")), InstanceAuth::local())
            .unwrap()
    }
    fn database(&self) -> Connection {
        Connection::open(self.0.join("demo/data/unified/index.sqlite")).unwrap()
    }
}
impl Drop for Fixture {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.0);
    }
}
fn workspace(state: &ProductState, path: PathBuf) -> String {
    state.register(path.to_str().unwrap()).unwrap()["id"]
        .as_str()
        .unwrap()
        .to_owned()
}
async fn request(
    app: &Router,
    method: &str,
    url: &str,
    body: Value,
    token: Option<&str>,
) -> (StatusCode, HeaderMap, Vec<u8>) {
    let mut request = Request::builder()
        .method(method)
        .uri(url)
        .header("Content-Type", "application/json")
        .header("Host", "127.0.0.1:8765");
    if let Some(token) = token {
        request = request.header("Authorization", format!("Bearer {token}"));
    }
    let response = app
        .clone()
        .oneshot(request.body(Body::from(body.to_string())).unwrap())
        .await
        .unwrap();
    let status = response.status();
    let headers = response.headers().clone();
    let bytes = response
        .into_body()
        .collect()
        .await
        .unwrap()
        .to_bytes()
        .to_vec();
    (status, headers, bytes)
}
async fn call(app: &Router, method: &str, url: &str, body: Value) -> (StatusCode, Value) {
    let (status, _, bytes) = request(app, method, url, body, None).await;
    (status, serde_json::from_slice(&bytes).unwrap())
}
async fn register(app: &Router, workspace: &str, path: &str) -> Value {
    let (status, doc) = call(app, "POST", "/api/project-control/documents", json!({"workspace":workspace,"path":path,"title":"Original title 原始标题","discipline":"Site","revision_label":"Rev A"})).await;
    assert_eq!(status, StatusCode::OK, "{doc}");
    doc
}
struct Package {
    manifest: Value,
    files: BTreeMap<String, Vec<u8>>,
}
async fn package(app: &Router, workspace: &str) -> Package {
    let (status, headers, bytes) = request(
        app,
        "GET",
        &format!("/api/project-control/package?workspace={workspace}"),
        json!(null),
        None,
    )
    .await;
    assert_eq!(
        status,
        StatusCode::OK,
        "{}",
        String::from_utf8_lossy(&bytes)
    );
    assert_eq!(headers["content-type"], "application/zip");
    assert_eq!(headers["cache-control"], "no-store");
    assert_eq!(headers["x-content-type-options"], "nosniff");
    assert_eq!(
        headers["content-length"]
            .to_str()
            .unwrap()
            .parse::<usize>()
            .unwrap(),
        bytes.len()
    );
    let mut zip = zip::ZipArchive::new(Cursor::new(bytes)).unwrap();
    let mut files = BTreeMap::new();
    for index in 0..zip.len() {
        let mut entry = zip.by_index(index).unwrap();
        let name = entry.name().to_owned();
        assert!(entry.enclosed_name().is_some());
        assert!(!name.contains('\\'));
        assert!(!name.contains(".."));
        assert_eq!(
            entry.unix_mode().unwrap() & 0o222,
            0,
            "snapshot members must have read-only modes"
        );
        let mut content = Vec::new();
        entry.read_to_end(&mut content).unwrap();
        assert!(
            files.insert(name, content).is_none(),
            "ZIP paths must not collide"
        );
    }
    let manifest: Value = serde_json::from_slice(files.get("manifest.json").unwrap()).unwrap();
    let audit = files.get("audit.jsonl").unwrap();
    assert_eq!(
        manifest["audit"]["sha256"],
        format!("{:x}", Sha256::digest(audit))
    );
    let lines = std::str::from_utf8(audit)
        .unwrap()
        .lines()
        .map(|line| serde_json::from_str::<Value>(line).unwrap())
        .collect::<Vec<_>>();
    assert_eq!(manifest["audit"]["event_count"], lines.len());
    assert!(lines
        .windows(2)
        .all(|pair| pair[0]["sequence"].as_i64() < pair[1]["sequence"].as_i64()));
    assert_eq!(manifest["audit"]["complete"], true);
    assert_eq!(manifest["workspace"], workspace);
    assert_eq!(manifest["statutory_signoff"], false);
    assert!(chrono::DateTime::parse_from_rfc3339(manifest["cutoff_at"].as_str().unwrap()).is_ok());
    for doc in manifest["documents"].as_array().unwrap() {
        let content = files
            .get(doc["snapshot"]["archive_path"].as_str().unwrap())
            .unwrap();
        assert_eq!(doc["sha256"], format!("{:x}", Sha256::digest(content)));
        assert_eq!(doc["snapshot"]["sha256"], doc["sha256"]);
        assert_eq!(doc["snapshot"]["revision"], doc["revision"]);
        assert_eq!(doc["snapshot"]["bytes"], content.len());
        assert_eq!(doc["snapshot"]["read_only"], true);
    }
    assert_eq!(
        files.len(),
        manifest["documents"].as_array().unwrap().len() + 2,
        "no unregistered or instance files"
    );
    Package { manifest, files }
}

#[tokio::test]
async fn package_retains_registered_bytes_and_draft_state_when_live_source_changes() {
    let fixture = Fixture::new();
    let state = fixture.open();
    let w = workspace(&state, fixture.0.join("project"));
    let app = router(state);
    let source = fixture.0.join("project/原始方案.md");
    let original = b"Rev A: source dimensions and restraint questions";
    std::fs::write(&source, original).unwrap();
    let doc = register(&app, &w, "原始方案.md").await;
    let doc_id = doc["id"].as_str().unwrap();
    let mut issue_request = json!({"workspace":w,"document_id":doc_id,"document_revision":1,"kind":"rfi","title":"Review original detail","description":"Confirm the restraint plan.","assignee":"Engineer A","due_date":"2026-10-15","status":"open","resolution":""});
    let (status, issue) = call(
        &app,
        "POST",
        "/api/project-control/issues",
        issue_request.clone(),
    )
    .await;
    assert_eq!(status, StatusCode::OK);
    let initial = package(&app, &w).await;
    assert_eq!(initial.manifest["draft"], true);
    assert_eq!(initial.manifest["internal_review_complete"], false);
    assert_eq!(initial.manifest["issues"][0]["due_date"], "2026-10-15");
    issue_request["id"] = issue["id"].clone();
    issue_request["expected_version"] = json!(1);
    issue_request["status"] = json!("resolved");
    issue_request["resolution"] = json!("Detail R1 received and checked.");
    assert_eq!(
        call(&app, "POST", "/api/project-control/issues", issue_request)
            .await
            .0,
        StatusCode::OK
    );
    assert_eq!(call(&app,"POST",&format!("/api/project-control/documents/{doc_id}/review"),json!({"workspace":w,"expected_revision":1,"expected_review_version":3,"notes":"Internal review of original source."})).await.0, StatusCode::OK);
    let reviewed = package(&app, &w).await;
    assert_eq!(reviewed.manifest["internal_review_complete"], true);
    assert_eq!(reviewed.manifest["draft"], false);
    assert_eq!(
        reviewed.manifest["documents"][0]["review"]["sha256"],
        doc["sha256"]
    );
    let new_bytes = b"Rev B: changed source without a new registered revision";
    std::fs::write(&source, new_bytes).unwrap();
    let changed = package(&app, &w).await;
    let entry = &changed.manifest["documents"][0];
    assert_eq!(entry["revision"], 1);
    assert_eq!(entry["source_status"], "changed");
    assert_eq!(entry["effective_status"], "review_required");
    assert_eq!(changed.manifest["status"], "draft_not_ready");
    assert_eq!(
        changed.files[entry["snapshot"]["archive_path"].as_str().unwrap()],
        original
    );
    std::fs::remove_file(&source).unwrap();
    let missing = package(&app, &w).await;
    assert_eq!(
        missing.manifest["documents"][0]["source_status"],
        "unavailable"
    );
    assert_eq!(missing.manifest["draft"], true);
    std::fs::write(&source, new_bytes).unwrap();
    assert_eq!(call(&app,"POST","/api/project-control/documents",json!({"workspace":w,"document_id":doc_id,"expected_revision":1,"path":"原始方案.md","title":"Original title 原始标题","discipline":"Site","revision_label":"Rev B"})).await.0, StatusCode::OK);
    let latest = package(&app, &w).await;
    let entry = &latest.manifest["documents"][0];
    assert_eq!(entry["revision"], 2);
    assert_eq!(entry["source_status"], "current");
    assert_eq!(
        latest.files[entry["snapshot"]["archive_path"].as_str().unwrap()],
        new_bytes
    );
    assert_eq!(latest.manifest["issues"][0]["document_revision"], 1);
    assert_eq!(latest.manifest["issues"][0]["status"], "review_required");
    assert_eq!(latest.manifest["issues"][0]["due_date"], "2026-10-15");
    assert_eq!(latest.manifest["draft"], true);
}

#[tokio::test]
async fn package_keeps_duplicate_basenames_separate_and_exports_all_scoped_audit() {
    let fixture = Fixture::new();
    let state = fixture.open();
    let w = workspace(&state, fixture.0.join("project"));
    let other = workspace(&state, fixture.0.join("other"));
    let app = router(state);
    for (path, bytes) in [
        ("project/a/资料.md", "Original A"),
        ("project/b/资料.md", "Original B"),
        ("other/资料.md", "Other workspace private"),
    ] {
        let path = fixture.0.join(path);
        std::fs::create_dir_all(path.parent().unwrap()).unwrap();
        std::fs::write(path, bytes).unwrap();
    }
    std::fs::write(
        fixture.0.join("project/login-token.txt"),
        "synthetic-private-token",
    )
    .unwrap();
    std::fs::write(
        fixture.0.join("project/unregistered.md"),
        "private unregistered file",
    )
    .unwrap();
    register(&app, &w, "a/资料.md").await;
    register(&app, &w, "b/资料.md").await;
    let other_doc = register(&app, &other, "资料.md").await;
    let mut db = fixture.database();
    let tx = db.transaction().unwrap();
    for n in 0..215 {
        tx.execute(
            "INSERT INTO project_events(workspace,record) VALUES(?1,?2)",
            params![
                w,
                json!({"kind":"synthetic_test_event","number":n}).to_string()
            ],
        )
        .unwrap();
    }
    tx.commit().unwrap();
    let result = package(&app, &w).await;
    assert_eq!(result.manifest["documents"].as_array().unwrap().len(), 2);
    assert_eq!(result.manifest["audit"]["event_count"], 217);
    assert_eq!(
        result
            .files
            .keys()
            .filter(|path| path.ends_with("/资料.md"))
            .count(),
        2
    );
    for bytes in result.files.values() {
        let text = String::from_utf8_lossy(bytes);
        assert!(!text.contains("synthetic-private-token"));
        assert!(!text.contains("private unregistered file"));
        assert!(!text.contains("Other workspace private"));
        assert!(!text.contains(other_doc["id"].as_str().unwrap()));
    }
    let isolated = package(&app, &other).await;
    assert_eq!(isolated.manifest["documents"].as_array().unwrap().len(), 1);
    assert_eq!(isolated.manifest["documents"][0]["id"], other_doc["id"]);
    assert_eq!(isolated.manifest["audit"]["event_count"], 1);
    assert_eq!(
        request(
            &app,
            "GET",
            "/api/project-control/package?workspace=unknown",
            json!(null),
            None
        )
        .await
        .0,
        StatusCode::BAD_REQUEST
    );
}

#[tokio::test]
async fn package_rejects_corrupt_snapshot_without_returning_a_partial_zip() {
    let fixture = Fixture::new();
    let state = fixture.open();
    let w = workspace(&state, fixture.0.join("project"));
    let app = router(state);
    std::fs::write(fixture.0.join("project/source.md"), "original payload").unwrap();
    let doc = register(&app, &w, "source.md").await;
    fixture
        .database()
        .execute(
            "UPDATE project_revisions SET payload=?1 WHERE workspace=?2 AND document_id=?3",
            params![b"tampered payload".as_slice(), w, doc["id"].as_str()],
        )
        .unwrap();
    let (status, headers, bytes) = request(
        &app,
        "GET",
        &format!("/api/project-control/package?workspace={w}"),
        json!(null),
        None,
    )
    .await;
    assert_eq!(status, StatusCode::CONFLICT);
    assert_eq!(headers["content-type"], "application/json");
    assert!(serde_json::from_slice::<Value>(&bytes).unwrap()["detail"]
        .as_str()
        .unwrap()
        .contains("integrity"));
    let db = fixture.database();
    db.execute(
        "UPDATE project_revisions SET payload=?1 WHERE workspace=?2",
        params![b"original payload".as_slice(), w],
    )
    .unwrap();
    for path in ["../escape.md", "CON.md", "login-token.txt"] {
        let mut old_record = doc.clone();
        old_record["path"] = json!(path);
        // Old/corrupt registrations must not bypass archive path or credential
        // validation simply because their stored payload hash still matches.
        db.execute(
            "UPDATE project_documents SET record=?1 WHERE workspace=?2",
            params![old_record.to_string(), w],
        )
        .unwrap();
        db.execute(
            "UPDATE project_revisions SET record=?1 WHERE workspace=?2",
            params![old_record.to_string(), w],
        )
        .unwrap();
        assert_eq!(
            request(
                &app,
                "GET",
                &format!("/api/project-control/package?workspace={w}"),
                json!(null),
                None
            )
            .await
            .0,
            StatusCode::CONFLICT,
            "{path}"
        );
    }
}

#[tokio::test]
async fn package_limit_does_not_delete_registered_history() {
    let fixture = Fixture::new();
    let state = fixture.open();
    let w = workspace(&state, fixture.0.join("project"));
    let app = router(state);
    std::fs::write(fixture.0.join("project/source.md"), "original payload").unwrap();
    let doc = register(&app, &w, "source.md").await;
    let mut db = fixture.database();
    let tx = db.transaction().unwrap();
    // Allocate on disk in SQLite, not nine 16 MiB Rust buffers. Preflight must
    // reject before materializing these oversized aggregate source contents.
    for n in 0..8 {
        let key = uuid::Uuid::new_v4().simple().to_string();
        let mut seeded = doc.clone();
        seeded["id"] = json!(key);
        seeded["path"] = json!(format!("large-{n}.md"));
        tx.execute(
            "INSERT INTO project_documents(workspace,id,record) VALUES(?1,?2,?3)",
            params![w, key, seeded.to_string()],
        )
        .unwrap();
        tx.execute("INSERT INTO project_revisions(workspace,document_id,revision,record,payload) VALUES(?1,?2,1,?3,zeroblob(16777216))",params![w,key,seeded.to_string()]).unwrap();
    }
    tx.commit().unwrap();
    let (status, error) = call(
        &app,
        "GET",
        &format!("/api/project-control/package?workspace={w}"),
        json!(null),
    )
    .await;
    assert_eq!(status, StatusCode::PAYLOAD_TOO_LARGE, "{error}");
    assert!(error["detail"].as_str().unwrap().contains("separately"));
    assert_eq!(
        db.query_row(
            "SELECT count(*) FROM project_revisions WHERE workspace=?1",
            [&w],
            |r| r.get::<_, i64>(0)
        )
        .unwrap(),
        9
    );
    let (status, _, bytes) = request(
        &app,
        "GET",
        &format!(
            "/api/project-control/documents/{}/revisions/1?workspace={w}",
            doc["id"].as_str().unwrap()
        ),
        json!(null),
        None,
    )
    .await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(bytes, b"original payload");
}

#[tokio::test]
async fn package_auth_and_registered_credential_names_fail_closed() {
    let fixture = Fixture::new();
    let state = fixture.open();
    let w = workspace(&state, fixture.0.join("project"));
    let app = router(state.clone());
    for path in [
        "login-token.txt",
        "token.txt",
        "credentials.json",
        "secrets.json",
        "instance-owner.sqlite",
    ] {
        std::fs::write(fixture.0.join("project").join(path), "synthetic credential").unwrap();
        let (status,_)=call(&app,"POST","/api/project-control/documents",json!({"workspace":w,"path":path,"title":"Do not export","discipline":"IT","revision_label":"A"})).await;
        assert_eq!(status, StatusCode::BAD_REQUEST, "{path}");
    }
    std::fs::write(
        fixture.0.join("project/auth.md"),
        "Document about authentication, not a credential",
    )
    .unwrap();
    register(&app, &w, "auth.md").await;
    package(&app, &w).await;
    let named_fixture = Fixture::new();
    let token = "synthetic-zip-owner-token-with-more-than-32-bytes";
    let auth = InstanceAuth::named(
        "zip-owner",
        &format!("{:x}", Sha256::digest(token.as_bytes())),
        vec![named_fixture.0.join("project")],
        None,
    )
    .unwrap();
    let named_state =
        ProductState::open_with_auth(Paths::from_demo(named_fixture.0.join("demo")), auth.clone())
            .unwrap();
    let named_workspace = workspace(&named_state, named_fixture.0.join("project"));
    assert!(named_state
        .register(named_fixture.0.join("other").to_str().unwrap())
        .is_err());
    named_fixture
        .database()
        .execute(
            "INSERT INTO workspaces(id,root) VALUES(?1,?2)",
            params![
                "previously-registered-other-project",
                named_fixture.0.join("other").to_str()
            ],
        )
        .unwrap();
    let protected = auth::protect(router(named_state), auth);
    let url = format!("/api/project-control/package?workspace={named_workspace}");
    assert_eq!(
        request(&protected, "GET", &url, json!(null), None).await.0,
        StatusCode::UNAUTHORIZED
    );
    assert_eq!(
        request(&protected, "GET", &url, json!(null), Some("wrong-token"))
            .await
            .0,
        StatusCode::UNAUTHORIZED
    );
    assert_eq!(
        request(&protected, "GET", &url, json!(null), Some(token))
            .await
            .0,
        StatusCode::OK
    );
    assert_eq!(
        request(
            &protected,
            "GET",
            "/api/project-control/package?workspace=previously-registered-other-project",
            json!(null),
            Some(token)
        )
        .await
        .0,
        StatusCode::BAD_REQUEST
    );
}

#[tokio::test]
async fn registration_rejects_windows_separator_credentials_before_storing_snapshots() {
    let fixture = Fixture::new();
    let state = fixture.open();
    let w = workspace(&state, fixture.0.join("project"));
    let app = router(state);
    std::fs::create_dir_all(fixture.0.join("project/subdir")).unwrap();
    for name in [
        "credentials.json",
        "secrets.json",
        "login-token.txt",
        "token.txt",
    ] {
        std::fs::write(
            fixture.0.join("project/subdir").join(name),
            b"synthetic-private-value",
        )
        .unwrap();
        for path in [format!("subdir/{name}"), format!("subdir\\{name}")] {
            let (status, _) = call(&app, "POST", "/api/project-control/documents", json!({
                "workspace":w,"path":path,"title":"Sensitive input", "discipline":"Site","revision_label":"A"
            })).await;
            assert_eq!(status, StatusCode::BAD_REQUEST, "{path}");
        }
    }
    std::fs::write(
        fixture.0.join("project/subdir/source.md"),
        b"Original project evidence",
    )
    .unwrap();
    for path in [
        "subdir\\source.md",
        "subdir//source.md",
        "subdir/./source.md",
    ] {
        let (status, _) = call(&app, "POST", "/api/project-control/documents", json!({
            "workspace":w,"path":path,"title":"Aliased source", "discipline":"Site","revision_label":"A"
        })).await;
        assert_eq!(status, StatusCode::BAD_REQUEST, "{path}");
    }
    let db = fixture.database();
    for table in ["project_documents", "project_revisions", "project_events"] {
        let count: i64 = db
            .query_row(&format!("SELECT count(*) FROM {table}"), [], |row| {
                row.get(0)
            })
            .unwrap();
        assert_eq!(count, 0, "rejected paths must not leave {table} records");
    }
    let doc = register(&app, &w, "subdir/source.md").await;
    assert_eq!(doc["path"], "subdir/source.md");
    let output = package(&app, &w).await;
    let entry = &output.manifest["documents"][0];
    assert_eq!(
        output.files[entry["snapshot"]["archive_path"].as_str().unwrap()],
        b"Original project evidence"
    );
}
