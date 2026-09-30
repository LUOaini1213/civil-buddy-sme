//! Concurrent updates must preserve the evidence/version observed by the loser.
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
use tower::ServiceExt;

struct Temp(std::path::PathBuf);
impl Drop for Temp {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.0);
    }
}
async fn call(app: &Router, method: &str, url: &str, body: Value) -> (StatusCode, Value) {
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

#[tokio::test]
async fn racing_revisions_and_issue_updates_have_one_winner_and_keep_workspace_boundaries() {
    let temp =
        Temp(std::env::temp_dir().join(format!("civil-project-races-{}", uuid::Uuid::new_v4())));
    for dir in ["demo", "job", "other"] {
        std::fs::create_dir_all(temp.0.join(dir)).unwrap();
    }
    std::fs::write(temp.0.join("job/evidence.md"), "Explicit original evidence").unwrap();
    let state =
        ProductState::open_with_auth(Paths::from_demo(temp.0.join("demo")), InstanceAuth::local())
            .unwrap();
    let w = state
        .register(temp.0.join("job").to_str().unwrap())
        .unwrap()["id"]
        .as_str()
        .unwrap()
        .to_owned();
    let other = state
        .register(temp.0.join("other").to_str().unwrap())
        .unwrap()["id"]
        .as_str()
        .unwrap()
        .to_owned();
    let app = router(state);
    let request = json!({"workspace":w,"path":"evidence.md","title":"Evidence","discipline":"Site","revision_label":"A"});
    let (status, doc) = call(
        &app,
        "POST",
        "/api/project-control/documents",
        request.clone(),
    )
    .await;
    assert_eq!(status, StatusCode::OK, "{doc}");
    let id = doc["id"].as_str().unwrap();
    let mut a = request.clone();
    a["document_id"] = json!(id);
    a["expected_revision"] = json!(1);
    a["revision_label"] = json!("B");
    let mut b = a.clone();
    b["revision_label"] = json!("C");
    let (a, b) = tokio::join!(
        call(&app, "POST", "/api/project-control/documents", a),
        call(&app, "POST", "/api/project-control/documents", b)
    );
    let statuses = [a.0, b.0];
    assert!(
        statuses.contains(&StatusCode::OK) && statuses.contains(&StatusCode::CONFLICT),
        "{a:?} {b:?}"
    );
    let (_, revisions) = call(
        &app,
        "GET",
        &format!("/api/project-control/documents/{id}/revisions?workspace={w}"),
        Value::Null,
    )
    .await;
    assert_eq!(revisions["revisions"].as_array().unwrap().len(), 2);
    assert_eq!(revisions["revisions"][0]["revision"], 2);
    let issue_req = json!({"workspace":w,"document_id":id,"document_revision":2,"kind":"rfi","title":"Clarify detail",
        "description":"Source requires a human check","assignee":"Reviewer","due_date":"","status":"open","resolution":""});
    let (status, issue) = call(
        &app,
        "POST",
        "/api/project-control/issues",
        issue_req.clone(),
    )
    .await;
    assert_eq!(status, StatusCode::OK, "{issue}");
    let mut a = issue_req.clone();
    a["id"] = issue["id"].clone();
    a["expected_version"] = json!(1);
    a["status"] = json!("resolved");
    a["resolution"] = json!("Checked original evidence");
    let mut b = a.clone();
    b["status"] = json!("in_progress");
    b["resolution"] = json!("");
    let (a, b) = tokio::join!(
        call(&app, "POST", "/api/project-control/issues", a),
        call(&app, "POST", "/api/project-control/issues", b)
    );
    let statuses = [a.0, b.0];
    assert!(
        statuses.contains(&StatusCode::OK) && statuses.contains(&StatusCode::CONFLICT),
        "{a:?} {b:?}"
    );
    let winner = if a.0 == StatusCode::OK { &a.1 } else { &b.1 };
    let (_, overview) = call(
        &app,
        "GET",
        &format!("/api/project-control?workspace={w}"),
        Value::Null,
    )
    .await;
    assert_eq!(overview["issues"][0]["version"], 2);
    assert_eq!(overview["issues"][0]["status"], winner["status"]);
    assert_eq!(overview["issues"][0]["resolution"], winner["resolution"]);
    assert_eq!(overview["internal_review_complete"], false);
    let mut wrong = issue_req;
    wrong["workspace"] = json!(other);
    assert_eq!(
        call(&app, "POST", "/api/project-control/issues", wrong)
            .await
            .0,
        StatusCode::NOT_FOUND
    );
    assert_eq!(
        call(
            &app,
            "POST",
            &format!("/api/project-control/documents/{id}/review"),
            json!({"workspace":other,"expected_revision":2,"expected_review_version":0,"notes":"Wrong workspace"})
        )
        .await
        .0,
        StatusCode::NOT_FOUND
    );
    assert!(call(
        &app,
        "GET",
        &format!("/api/project-control?workspace={other}"),
        Value::Null
    )
    .await
    .1["issues"]
        .as_array()
        .unwrap()
        .is_empty());
    assert_eq!(
        std::fs::read_to_string(temp.0.join("job/evidence.md")).unwrap(),
        "Explicit original evidence"
    );
}

#[tokio::test]
async fn empty_sources_cannot_bypass_the_revision_count_limit() {
    let temp =
        Temp(std::env::temp_dir().join(format!("civil-project-limits-{}", uuid::Uuid::new_v4())));
    std::fs::create_dir_all(temp.0.join("demo")).unwrap();
    std::fs::create_dir_all(temp.0.join("job")).unwrap();
    std::fs::write(temp.0.join("job/empty.md"), b"").unwrap();
    let state =
        ProductState::open_with_auth(Paths::from_demo(temp.0.join("demo")), InstanceAuth::local())
            .unwrap();
    let workspace = state
        .register(temp.0.join("job").to_str().unwrap())
        .unwrap()["id"]
        .as_str()
        .unwrap()
        .to_owned();
    let app = router(state);
    let mut body = json!({"workspace":workspace,"path":"empty.md","title":"Empty source draft","discipline":"Site","revision_label":"1"});
    let (status, first) = call(&app, "POST", "/api/project-control/documents", body.clone()).await;
    assert_eq!(status, StatusCode::OK, "{first}");
    body["document_id"] = first["id"].clone();
    for revision in 2..=256 {
        body["expected_revision"] = json!(revision - 1);
        body["revision_label"] = json!(revision.to_string());
        let (status, result) =
            call(&app, "POST", "/api/project-control/documents", body.clone()).await;
        assert_eq!(status, StatusCode::OK, "revision {revision}: {result}");
        assert_eq!(result["revision"], revision);
    }
    body["expected_revision"] = json!(256);
    body["revision_label"] = json!("257");
    let (status, result) = call(&app, "POST", "/api/project-control/documents", body).await;
    assert_eq!(status, StatusCode::BAD_REQUEST, "{result}");
    assert!(result["detail"].as_str().unwrap().contains("256"));
    let (_, history) = call(
        &app,
        "GET",
        &format!(
            "/api/project-control/documents/{}/revisions?workspace={workspace}",
            first["id"].as_str().unwrap()
        ),
        Value::Null,
    )
    .await;
    assert_eq!(history["revisions"].as_array().unwrap().len(), 256);
    assert_eq!(history["revisions"][0]["revision"], 256);
    let mut large = std::fs::File::create(temp.0.join("job/too-large.md")).unwrap();
    large.set_len(16 * 1024 * 1024 + 1).unwrap();
    use std::io::Write;
    large.flush().unwrap();
    drop(large);
    assert_eq!(
        call(
            &app,
            "POST",
            "/api/project-control/documents",
            json!({"workspace":workspace,"path":"too-large.md",
        "title":"Too large","discipline":"Site","revision_label":"1"})
        )
        .await
        .0,
        StatusCode::BAD_REQUEST
    );
}
