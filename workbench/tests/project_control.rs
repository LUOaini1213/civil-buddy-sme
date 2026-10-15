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
use serde_json::{json, Value};
use std::path::PathBuf;
use tower::ServiceExt;

struct Fixture(PathBuf);
impl Fixture {
    fn new() -> Self {
        let path =
            std::env::temp_dir().join(format!("civil-project-control-{}", uuid::Uuid::new_v4()));
        std::fs::create_dir_all(path.join("demo")).unwrap();
        std::fs::create_dir_all(path.join("project")).unwrap();
        std::fs::create_dir_all(path.join("other")).unwrap();
        Self(path)
    }
    fn open(&self) -> std::sync::Arc<ProductState> {
        ProductState::open_with_auth(Paths::from_demo(self.0.join("demo")), InstanceAuth::local())
            .unwrap()
    }
}
impl Drop for Fixture {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.0);
    }
}
async fn raw(app: &Router, method: &str, url: &str, body: Value) -> (StatusCode, Vec<u8>) {
    let r = app
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
    (
        r.status(),
        r.into_body().collect().await.unwrap().to_bytes().to_vec(),
    )
}
async fn call(app: &Router, method: &str, url: &str, body: Value) -> (StatusCode, Value) {
    let (s, b) = raw(app, method, url, body).await;
    (
        s,
        serde_json::from_slice(&b)
            .unwrap_or_else(|_| panic!("Not JSON: {}", String::from_utf8_lossy(&b))),
    )
}

#[tokio::test]
async fn revisions_issues_reviews_and_restart_preserve_evidence() {
    let f = Fixture::new();
    let state = f.open();
    let w = state
        .register(f.0.join("project").to_str().unwrap())
        .unwrap()["id"]
        .as_str()
        .unwrap()
        .to_owned();
    let other = state.register(f.0.join("other").to_str().unwrap()).unwrap()["id"]
        .as_str()
        .unwrap()
        .to_owned();
    let path = f.0.join("project/原始方案.md");
    std::fs::write(&path, "Rev A\nA-frame: verify securing plan").unwrap();
    let app = router(state.clone());
    let registration = json!({"workspace":w,"path":"原始方案.md","title":"Facade installation","discipline":"Facade","revision_label":"Rev A"});
    let (s, doc) = call(
        &app,
        "POST",
        "/api/project-control/documents",
        registration.clone(),
    )
    .await;
    assert_eq!(s, StatusCode::OK, "{doc}");
    let doc_id = doc["id"].as_str().unwrap();
    assert_eq!(doc["revision"], 1);
    assert_eq!(doc["status"], "work_in_progress");
    assert_eq!(
        call(
            &app,
            "POST",
            "/api/project-control/documents",
            registration.clone()
        )
        .await
        .0,
        StatusCode::CONFLICT
    );
    let review_url = format!("/api/project-control/documents/{doc_id}/review");
    let mut review = json!({"workspace":w,"expected_revision":1,"expected_review_version":1,"notes":"Checked stated requirements against the source; professional review remains separate."});
    let issue_req = json!({"workspace":w,"document_id":doc_id,"document_revision":1,"kind":"rfi","title":"Missing restraint detail","description":"Confirm the rack restraint drawing, section 2.","assignee":"Engineer A","due_date":"2026-10-15","status":"open","resolution":""});
    let (s, issue) = call(
        &app,
        "POST",
        "/api/project-control/issues",
        issue_req.clone(),
    )
    .await;
    assert_eq!(s, StatusCode::OK, "{issue}");
    assert_eq!(issue["notification_sent"], false);
    assert_eq!(issue["actor_id"], "local-owner");
    assert_eq!(
        call(&app, "POST", &review_url, review.clone()).await.0,
        StatusCode::CONFLICT
    );
    let mut resolved = issue_req.clone();
    resolved["id"] = issue["id"].clone();
    resolved["expected_version"] = json!(1);
    resolved["status"] = json!("resolved");
    assert_eq!(
        call(
            &app,
            "POST",
            "/api/project-control/issues",
            resolved.clone()
        )
        .await
        .0,
        StatusCode::BAD_REQUEST
    );
    resolved["resolution"] = json!("Referenced drawing R-1 received and checked.");
    assert_eq!(
        call(
            &app,
            "POST",
            "/api/project-control/issues",
            resolved.clone()
        )
        .await
        .0,
        StatusCode::OK
    );
    assert_eq!(
        call(
            &app,
            "POST",
            "/api/project-control/issues",
            resolved.clone()
        )
        .await
        .0,
        StatusCode::CONFLICT,
        "lost update must not overwrite resolution"
    );
    review["expected_review_version"] = json!(3);
    let (s, reviewed) = call(&app, "POST", &review_url, review.clone()).await;
    assert_eq!(s, StatusCode::OK, "{reviewed}");
    assert_eq!(reviewed["review"]["statutory_signoff"], false);
    let overview_url = format!("/api/project-control?workspace={w}");
    assert_eq!(
        call(&app, "GET", &overview_url, json!(null)).await.1["internal_review_complete"],
        true
    );
    std::fs::write(
        &path,
        "Rev B\nRestraint detail updated, verify new drawing R-2.",
    )
    .unwrap();
    let changed = call(&app, "GET", &overview_url, json!(null)).await.1;
    assert_eq!(changed["documents"][0]["source_status"], "changed");
    assert_eq!(changed["internal_review_complete"], false);
    assert_eq!(
        call(&app, "POST", &review_url, review.clone()).await.0,
        StatusCode::CONFLICT
    );
    let mut revision = registration.clone();
    revision["document_id"] = json!(doc_id);
    revision["expected_revision"] = json!(1);
    revision["revision_label"] = json!("Rev B");
    let (s, newdoc) = call(
        &app,
        "POST",
        "/api/project-control/documents",
        revision.clone(),
    )
    .await;
    assert_eq!(s, StatusCode::OK, "{newdoc}");
    assert_eq!(newdoc["revision"], 2);
    assert_eq!(
        call(&app, "POST", "/api/project-control/documents", revision)
            .await
            .0,
        StatusCode::CONFLICT
    );
    let newer = call(&app, "GET", &overview_url, json!(null)).await.1;
    assert_eq!(newer["issues"][0]["status"], "review_required");
    assert_eq!(newer["issues"][0]["document_revision"], 1);
    assert_eq!(newer["issues"][0]["version"], 3);
    assert_eq!(
        call(
            &app,
            "POST",
            &review_url,
            json!({"workspace":w,"expected_revision":2,"expected_review_version":newdoc["review_version"],"notes":"Reviewed B"})
        )
        .await
        .0,
        StatusCode::CONFLICT
    );
    let snapshot_url = format!("/api/project-control/documents/{doc_id}/revisions/1?workspace={w}");
    let (s, original) = raw(&app, "GET", &snapshot_url, json!(null)).await;
    assert_eq!(s, StatusCode::OK);
    assert_eq!(
        String::from_utf8(original).unwrap(),
        "Rev A\nA-frame: verify securing plan"
    );
    assert_eq!(
        raw(
            &app,
            "GET",
            &format!("/api/project-control/documents/{doc_id}/revisions/1?workspace={other}"),
            json!(null)
        )
        .await
        .0,
        StatusCode::NOT_FOUND
    );
    let mut closed = resolved.clone();
    closed["expected_version"] = json!(3);
    closed["document_revision"] = json!(2);
    closed["resolution"] = json!("Rechecked drawing R-2 against Rev B.");
    assert_eq!(
        call(&app, "POST", "/api/project-control/issues", closed)
            .await
            .0,
        StatusCode::OK
    );
    assert_eq!(call(&app,"POST",&review_url,json!({"workspace":w,"expected_revision":2,"expected_review_version":5,"notes":"Internal coordination review for Rev B."})).await.0,StatusCode::OK);
    let handoff = call(
        &app,
        "GET",
        &format!("/api/project-control/handoff?workspace={w}"),
        json!(null),
    )
    .await
    .1;
    assert_eq!(handoff["internal_review_complete"], true);
    assert_eq!(handoff["statutory_signoff"], false);
    assert_eq!(handoff["export_kind"], "internal_project_review_record");
    assert!(handoff["events"]
        .as_array()
        .unwrap()
        .iter()
        .any(|e| e["kind"] == "issue_reopened"));
    drop(app);
    drop(state);
    let reopened = f.open();
    let app = router(reopened);
    let persisted = call(&app, "GET", &overview_url, json!(null)).await.1;
    assert_eq!(persisted["internal_review_complete"], true);
    assert_eq!(
        persisted["issues"][0]["resolution"],
        "Rechecked drawing R-2 against Rev B."
    );
    assert_eq!(
        raw(&app, "GET", &snapshot_url, json!(null)).await.0,
        StatusCode::OK
    );
    assert!(
        std::fs::read_to_string(path).unwrap().contains("Rev B"),
        "source must never be edited by the ledger"
    );
}

#[tokio::test]
async fn secrets_traversal_wrong_identity_and_unavailable_sources_fail_closed() {
    let f = Fixture::new();
    let state = f.open();
    let w = state
        .register(f.0.join("project").to_str().unwrap())
        .unwrap()["id"]
        .as_str()
        .unwrap()
        .to_owned();
    std::fs::write(f.0.join("project/input.md"), "source").unwrap();
    std::fs::write(f.0.join("project/.env"), "KEY=secret").unwrap();
    let app = router(state);
    let base = json!({"workspace":w,"path":"input.md","title":"Task","discipline":"Site","revision_label":"A"});
    for path in [
        "../input.md",
        ".env",
        "C:/outside.md",
        ".git/config",
        "secret.key",
    ] {
        let mut req = base.clone();
        req["path"] = json!(path);
        assert_eq!(
            call(&app, "POST", "/api/project-control/documents", req)
                .await
                .0,
            StatusCode::BAD_REQUEST,
            "{path}"
        );
    }
    let mut forged = base.clone();
    forged["actor_id"] = json!("engineer-approved");
    assert_eq!(
        raw(&app, "POST", "/api/project-control/documents", forged)
            .await
            .0,
        StatusCode::UNPROCESSABLE_ENTITY
    );
    let (_, doc) = call(&app, "POST", "/api/project-control/documents", base).await;
    std::fs::remove_file(f.0.join("project/input.md")).unwrap();
    let result = call(
        &app,
        "GET",
        &format!("/api/project-control?workspace={w}"),
        json!(null),
    )
    .await
    .1;
    assert_eq!(result["documents"][0]["source_status"], "unavailable");
    assert_eq!(
        call(
            &app,
            "POST",
            &format!(
                "/api/project-control/documents/{}/review",
                doc["id"].as_str().unwrap()
            ),
            json!({"workspace":w,"expected_revision":1,"expected_review_version":doc["review_version"],"notes":"Cannot inspect"})
        )
        .await
        .0,
        StatusCode::CONFLICT
    );
    let (s, _) = raw(
        &app,
        "GET",
        &format!(
            "/api/project-control/documents/{}/revisions/18446744073709551615?workspace={w}",
            doc["id"].as_str().unwrap()
        ),
        json!(null),
    )
    .await;
    assert_eq!(s, StatusCode::BAD_REQUEST);
}

#[tokio::test]
async fn review_requires_the_observed_issue_basis_even_when_source_revision_is_unchanged() {
    let f = Fixture::new();
    let state = f.open();
    let w = state
        .register(f.0.join("project").to_str().unwrap())
        .unwrap()["id"]
        .as_str()
        .unwrap()
        .to_owned();
    std::fs::write(f.0.join("project/source.md"), "Unchanged original evidence").unwrap();
    let app = router(state);
    let registration = json!({"workspace":w,"path":"source.md","title":"Evidence","discipline":"Site","revision_label":"A"});
    let (_, doc) = call(
        &app,
        "POST",
        "/api/project-control/documents",
        registration.clone(),
    )
    .await;
    let doc_id = doc["id"].as_str().unwrap();
    let url = format!("/api/project-control/documents/{doc_id}/review");
    let overview_url = format!("/api/project-control?workspace={w}");
    assert_eq!(doc["review_version"], 1);
    let mut review = json!({"workspace":w,"expected_revision":1,"expected_review_version":doc["review_version"],"notes":"Reviewed the evidence currently shown."});
    // Reviewing unchanged evidence does not manufacture a new invalidation.
    for _ in 0..2 {
        let (status, reviewed) = call(&app, "POST", &url, review.clone()).await;
        assert_eq!(status, StatusCode::OK, "{reviewed}");
        assert_eq!(reviewed["review_version"], 1);
        assert_eq!(reviewed["review"]["basis_version"], 1);
    }
    let mut issue = json!({"workspace":w,"document_id":doc_id,"document_revision":1,"kind":"rfi","title":"New information","description":"Check the original source against a newly received clarification.","assignee":"Reviewer","due_date":"","status":"open","resolution":""});
    let (status, saved) = call(&app, "POST", "/api/project-control/issues", issue.clone()).await;
    assert_eq!(status, StatusCode::OK, "{saved}");
    issue["id"] = saved["id"].clone();
    issue["expected_version"] = json!(1);
    issue["status"] = json!("resolved");
    issue["resolution"] = json!("Clarification A checked.");
    assert_eq!(
        call(&app, "POST", "/api/project-control/issues", issue.clone())
            .await
            .0,
        StatusCode::OK
    );
    // An old review remains stale after the new issue has already been closed.
    assert_eq!(
        call(&app, "POST", &url, review.clone()).await.0,
        StatusCode::CONFLICT
    );
    let current = call(&app, "GET", &overview_url, json!(null)).await.1;
    assert_eq!(current["documents"][0]["revision"], 1);
    assert_eq!(current["documents"][0]["review_version"], 3);
    assert_eq!(current["documents"][0]["status"], "work_in_progress");
    review["expected_review_version"] = current["documents"][0]["review_version"].clone();
    assert_eq!(
        call(&app, "POST", &url, review.clone()).await.0,
        StatusCode::OK
    );
    issue["expected_version"] = json!(2);
    issue["resolution"] = json!("Clarification B supersedes the first answer.");
    assert_eq!(
        call(&app, "POST", "/api/project-control/issues", issue.clone())
            .await
            .0,
        StatusCode::OK
    );
    assert_eq!(
        call(&app, "POST", &url, review.clone()).await.0,
        StatusCode::CONFLICT
    );
    let mut incomplete = review.clone();
    incomplete
        .as_object_mut()
        .unwrap()
        .remove("expected_review_version");
    assert_eq!(
        raw(&app, "POST", &url, incomplete).await.0,
        StatusCode::UNPROCESSABLE_ENTITY
    );
    review["expected_review_version"] = json!(4);
    assert_eq!(
        call(&app, "POST", &url, review.clone()).await.0,
        StatusCode::OK
    );
    let mut revision = registration;
    revision["document_id"] = json!(doc_id);
    revision["expected_revision"] = json!(1);
    revision["revision_label"] = json!("B");
    let (status, revised) = call(&app, "POST", "/api/project-control/documents", revision).await;
    assert_eq!(status, StatusCode::OK, "{revised}");
    assert_eq!(revised["review_version"], 5);
    assert_eq!(
        call(&app, "POST", &url, review.clone()).await.0,
        StatusCode::CONFLICT
    );
    review["expected_revision"] = json!(2);
    assert_eq!(
        call(&app, "POST", &url, review.clone()).await.0,
        StatusCode::CONFLICT
    );
    issue["expected_version"] = json!(4);
    issue["document_revision"] = json!(2);
    assert_eq!(
        call(&app, "POST", "/api/project-control/issues", issue)
            .await
            .0,
        StatusCode::OK
    );
    review["expected_review_version"] = json!(6);
    assert_eq!(call(&app, "POST", &url, review).await.0, StatusCode::OK);
    assert_eq!(
        std::fs::read_to_string(f.0.join("project/source.md")).unwrap(),
        "Unchanged original evidence"
    );
}

#[tokio::test]
async fn old_project_records_receive_a_stable_review_basis_on_reopen() {
    let f = Fixture::new();
    let state = f.open();
    let w = state
        .register(f.0.join("project").to_str().unwrap())
        .unwrap()["id"]
        .as_str()
        .unwrap()
        .to_owned();
    std::fs::write(f.0.join("project/source.md"), "original").unwrap();
    let app = router(state.clone());
    let (_,doc)=call(&app,"POST","/api/project-control/documents",json!({"workspace":w,"path":"source.md","title":"Old record","discipline":"Site","revision_label":"A"})).await;
    let db = rusqlite::Connection::open(f.0.join("demo/data/unified/index.sqlite")).unwrap();
    db.execute(
        "UPDATE project_documents SET record=json_remove(record,'$.review_version')",
        [],
    )
    .unwrap();
    drop(db);
    drop(app);
    drop(state);
    let state = f.open();
    let app = router(state);
    let current = call(
        &app,
        "GET",
        &format!("/api/project-control?workspace={w}"),
        json!(null),
    )
    .await
    .1;
    assert_eq!(current["documents"][0]["review_version"], 0);
    let (status,reviewed)=call(&app,"POST",&format!("/api/project-control/documents/{}/review",doc["id"].as_str().unwrap()),json!({"workspace":w,"expected_revision":1,"expected_review_version":0,"notes":"Reviewed migrated record."})).await;
    assert_eq!(status, StatusCode::OK, "{reviewed}");
    assert_eq!(reviewed["review_version"], 0);
}
