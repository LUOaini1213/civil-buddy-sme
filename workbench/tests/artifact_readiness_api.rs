//! Real Python document-worker integration, with generated local PDF fixtures.
//! No model/provider is configured and no PDF action is executed.
use axum::{
    body::Body,
    http::{header, HeaderMap, Request, StatusCode},
    Router,
};
use civil_workbench::{
    config::Paths,
    product::{
        api::{router, ProductState},
        auth::InstanceAuth,
        tools::sha256,
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
async fn call(app: &Router, method: &str, url: &str) -> (StatusCode, HeaderMap, Vec<u8>) {
    let response = app
        .clone()
        .oneshot(
            Request::builder()
                .method(method)
                .uri(url)
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    let status = response.status();
    let headers = response.headers().clone();
    let body = response
        .into_body()
        .collect()
        .await
        .unwrap()
        .to_bytes()
        .to_vec();
    (status, headers, body)
}
fn parsed(bytes: &[u8]) -> Value {
    serde_json::from_slice(bytes)
        .unwrap_or_else(|_| panic!("Not JSON: {}", String::from_utf8_lossy(bytes)))
}

#[tokio::test]
async fn readiness_and_native_pdf_preview_bind_real_worker_checks_to_registered_bytes() {
    assert!(
        std::env::var_os("CIVIL_STATE_ROOT").is_none(),
        "Use isolated test state"
    );
    let repo = std::path::PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .unwrap()
        .to_path_buf();
    let temp = Temp(
        repo.parent()
            .unwrap()
            .join(format!("artifact-readiness-{}", uuid::Uuid::new_v4())),
    );
    let job = temp.0.join("job");
    let out = job.join(".civil-buddy/out");
    std::fs::create_dir_all(&out).unwrap();
    std::fs::create_dir_all(temp.0.join("other")).unwrap();
    let python = std::env::var_os("CIVIL_PYTHON").unwrap_or_else(|| "python".into());
    let made=std::process::Command::new(python).args(["-B","-c",
        "from pathlib import Path; import sys; from scripts.test_document_readiness import pdf; p=Path(sys.argv[1]); (p/'plain.pdf').write_bytes(pdf()); (p/'active.pdf').write_bytes(pdf(javascript=True)); (p/'attached.pdf').write_bytes(pdf(attachment=True))"])
        .arg(&out).current_dir(&repo).env("PYTHONUTF8","1").output().unwrap();
    assert!(
        made.status.success(),
        "fixture: {}",
        String::from_utf8_lossy(&made.stderr)
    );
    let original = std::fs::read(out.join("plain.pdf")).unwrap();
    std::fs::write(job.join("original.pdf"), &original).unwrap();
    let mut paths = Paths::from_demo(repo.join("demo"));
    paths.data_dir = temp.0.join("state");
    let state = ProductState::open_with_auth(paths, InstanceAuth::local()).unwrap();
    let workspace = state.register(job.to_str().unwrap()).unwrap()["id"]
        .as_str()
        .unwrap()
        .to_owned();
    let other = state
        .register(temp.0.join("other").to_str().unwrap())
        .unwrap()["id"]
        .as_str()
        .unwrap()
        .to_owned();
    let mut artifacts = Vec::new();
    for name in ["plain.pdf", "active.pdf", "attached.pdf"] {
        let bytes = std::fs::read(out.join(name)).unwrap();
        let record=state.register_artifact(&workspace,&json!({"output_path":out.join(name).canonicalize().unwrap(),"output_sha256":sha256(&bytes),
            "source":"original.pdf","source_sha256":sha256(&original),"validation":{},"evidence_validation":"not_checked"})).unwrap();
        artifacts.push(record["id"].as_str().unwrap().to_owned());
    }
    let app = router(state);
    let plain = &artifacts[0];
    let inspection = format!("/api/agent/artifacts/{plain}/readiness?workspace={workspace}");
    let preview = format!("/api/agent/artifacts/{plain}/preview?workspace={workspace}");
    assert_eq!(
        call(&app, "GET", &preview).await.0,
        StatusCode::BAD_REQUEST,
        "a registered artifact alone cannot enable preview"
    );
    let (status, _, body) = call(&app, "POST", &inspection).await;
    let report = parsed(&body);
    assert_eq!(status, StatusCode::OK, "{report}");
    assert_eq!(report["format"], "pdf");
    assert_eq!(report["source_sha256"], sha256(&original));
    assert_eq!(report["readiness"]["preview"]["eligible"], true);
    assert_eq!(report["readiness"]["automatic_acceptance"], false);
    assert_eq!(report["writes"], false);
    assert_eq!(report["original_source_status"], "current");
    assert_eq!(report["inspected_by"], "local-owner");
    let (status, headers, bytes) = call(&app, "GET", &preview).await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(headers[header::CONTENT_TYPE], "application/pdf");
    assert_eq!(headers[header::CACHE_CONTROL], "no-store");
    assert!(headers[header::CONTENT_SECURITY_POLICY]
        .to_str()
        .unwrap()
        .contains("sandbox"));
    assert_eq!(bytes, original);
    for id in &artifacts[1..] {
        let (status, _, body) = call(
            &app,
            "POST",
            &format!("/api/agent/artifacts/{id}/readiness?workspace={workspace}"),
        )
        .await;
        let report = parsed(&body);
        assert_eq!(status, StatusCode::OK, "{report}");
        assert_eq!(report["readiness"]["status"], "blocked");
        assert_eq!(report["readiness"]["preview"]["eligible"], false);
        assert!(
            !report["readiness"]["format_details"]["active_content_markers"]
                .as_array()
                .unwrap()
                .is_empty()
        );
        assert_eq!(
            call(
                &app,
                "GET",
                &format!("/api/agent/artifacts/{id}/preview?workspace={workspace}")
            )
            .await
            .0,
            StatusCode::BAD_REQUEST
        );
    }
    for action in ["readiness", "preview"] {
        assert_eq!(
            call(
                &app,
                if action == "readiness" { "POST" } else { "GET" },
                &format!("/api/agent/artifacts/{plain}/{action}?workspace={other}")
            )
            .await
            .0,
            StatusCode::NOT_FOUND
        );
    }
    std::fs::write(job.join("original.pdf"), b"source changed outside the app").unwrap();
    let (status, _, body) = call(&app, "POST", &inspection).await;
    assert_eq!(status, StatusCode::OK, "{}", parsed(&body));
    assert_eq!(parsed(&body)["original_source_status"], "changed");
    let active = std::fs::read(out.join("active.pdf")).unwrap();
    std::fs::write(out.join("plain.pdf"), active).unwrap();
    assert_eq!(
        call(&app, "GET", &preview).await.0,
        StatusCode::CONFLICT,
        "old inspection cannot authorize changed PDF bytes"
    );
    assert_eq!(
        call(&app, "POST", &inspection).await.0,
        StatusCode::CONFLICT,
        "changed registered artifact must not be silently re-inspected as its old hash"
    );
}
