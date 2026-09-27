use axum::{
    body::Body,
    http::{Request, StatusCode},
    response::IntoResponse,
    routing::get,
    Router,
};
use civil_workbench::{
    api::{self, AppState},
    config::Paths,
    product::auth::{self, InstanceAuth},
};
use http_body_util::BodyExt;
use serde_json::json;
use sha2::{Digest, Sha256};
use std::{
    path::PathBuf,
    sync::{Arc, Mutex},
};
use tower::ServiceExt;

const TOKEN: &str = "synthetic-named-token-with-at-least-32-characters";
struct Temp(PathBuf);
impl Temp {
    fn new() -> Self {
        let p = std::env::temp_dir().join(format!("civil-file-boundary-{}", uuid::Uuid::new_v4()));
        std::fs::create_dir_all(&p).unwrap();
        Self(p)
    }
    fn dir(&self, name: &str) -> PathBuf {
        let p = self.0.join(name);
        std::fs::create_dir_all(&p).unwrap();
        p
    }
}
impl Drop for Temp {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.0);
    }
}
fn encoded_path(path: &std::path::Path) -> String {
    reqwest::Url::parse_with_params(
        "http://localhost/api/file",
        &[("path", path.to_string_lossy().as_ref())],
    )
    .unwrap()
    .query()
    .unwrap()
    .to_owned()
}

#[tokio::test]
async fn named_file_download_preserves_relative_links_bytes_and_filename_but_denies_other_instance_paths(
) {
    let temp = Temp::new();
    let seen = Arc::new(Mutex::new(Vec::<String>::new()));
    let captured = seen.clone();
    let sidecar = Router::new()
        .route(
            "/api/sessions",
            get(|uri: axum::http::Uri| async move {
                assert_eq!(uri.query(), Some("q=import%20copy&limit=12"));
                axum::Json(
                    json!({"sessions":[{"session_id":"import-copy","title":"Restored task"}]}),
                )
            }),
        )
        .route(
            "/api/file",
            get(
                move |uri: axum::http::Uri, headers: axum::http::HeaderMap| {
                    let seen = captured.clone();
                    async move {
                        assert_eq!(headers["authorization"], format!("Bearer {TOKEN}"));
                        seen.lock().unwrap().push(uri.to_string());
                        (
                            [
                                ("content-type", "application/octet-stream"),
                                (
                                    "content-disposition",
                                    "attachment; filename*=UTF-8''source%20drawing.dxf",
                                ),
                            ],
                            vec![0, 255, 128, 80, 75],
                        )
                            .into_response()
                    }
                },
            ),
        );
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let base = format!("http://{}", listener.local_addr().unwrap());
    let server = tokio::spawn(async move { axum::serve(listener, sidecar).await.unwrap() });
    let paths = Paths::from_demo(temp.dir("alice-demo"));
    std::fs::create_dir_all(&paths.out_root).unwrap();
    let own = paths.out_root.join("native.txt");
    std::fs::write(&own, b"native bytes").unwrap();
    let other = temp.dir("bob-state").join("private.txt");
    std::fs::write(&other, b"private-bob-data").unwrap();
    let mut state = AppState::live(paths.clone());
    state.engine = Some(Arc::new(
        civil_workbench::py_engine::PyEngine::attach(&base, TOKEN).unwrap(),
    ));
    let identity = InstanceAuth::named(
        "alice",
        &format!("{:x}", Sha256::digest(TOKEN.as_bytes())),
        vec![temp.dir("alice-job")],
        None,
    )
    .unwrap();
    let app = auth::protect(api::app(state), identity);
    let links = [
        "/api/file?session=flow01&upload=u123&name=source%20drawing.dxf".to_owned(),
        "/api/file?session=flow01&run=run01&file=report.docx&name=final%20report.docx".to_owned(),
        format!("/api/file?{}", encoded_path(&own)),
    ];
    for link in &links {
        let response = app
            .clone()
            .oneshot(
                Request::builder()
                    .uri(link)
                    .header("host", "127.0.0.1:8765")
                    .header("authorization", format!("Bearer {TOKEN}"))
                    .body(Body::empty())
                    .unwrap(),
            )
            .await
            .unwrap();
        assert_eq!(response.status(), StatusCode::OK);
        assert_eq!(
            response.headers()["content-disposition"],
            "attachment; filename*=UTF-8''source%20drawing.dxf"
        );
        assert_eq!(
            &response.into_body().collect().await.unwrap().to_bytes()[..],
            &[0, 255, 128, 80, 75]
        );
    }
    assert_eq!(*seen.lock().unwrap(), links);
    let listing = app
        .clone()
        .oneshot(
            Request::builder()
                .uri("/api/sessions?q=import%20copy&limit=12")
                .header("host", "127.0.0.1:8765")
                .header("authorization", format!("Bearer {TOKEN}"))
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(listing.status(), StatusCode::OK);
    let listed: serde_json::Value =
        serde_json::from_slice(&listing.into_body().collect().await.unwrap().to_bytes()).unwrap();
    assert_eq!(listed["sessions"][0]["session_id"], "import-copy");
    let foreign = format!("/api/file?{}", encoded_path(&other));
    let denied = app
        .clone()
        .oneshot(
            Request::builder()
                .uri(foreign)
                .header("host", "127.0.0.1:8765")
                .header("authorization", format!("Bearer {TOKEN}"))
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(denied.status(), StatusCode::FORBIDDEN);
    assert_eq!(seen.lock().unwrap().len(), 3);
    let denied = app
        .oneshot(
            Request::builder()
                .uri(&links[0])
                .header("host", "127.0.0.1:8765")
                .header("authorization", "Bearer wrong-instance-token")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(denied.status(), StatusCode::UNAUTHORIZED);
    assert_eq!(seen.lock().unwrap().len(), 3);
    let native = api::app(AppState::live(paths))
        .oneshot(
            Request::builder()
                .uri(&links[2])
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(native.status(), StatusCode::OK);
    assert_eq!(
        &native.into_body().collect().await.unwrap().to_bytes()[..],
        b"native bytes"
    );
    server.abort();
}

struct Env(Vec<(String, Option<std::ffi::OsString>)>);
impl Env {
    fn named(root: &std::path::Path) -> Self {
        let values = [
            ("CIVIL_INSTANCE_USER", "alice".to_owned()),
            (
                "CIVIL_TOKEN_SHA256",
                format!("{:x}", Sha256::digest(TOKEN.as_bytes())),
            ),
            (
                "CIVIL_ALLOWED_WORKSPACES",
                serde_json::to_string(&vec![root]).unwrap(),
            ),
        ];
        let saved = values
            .iter()
            .map(|(k, _)| ((*k).to_owned(), std::env::var_os(k)))
            .collect();
        for (key, value) in values {
            std::env::set_var(key, value);
        }
        Self(saved)
    }
}
impl Drop for Env {
    fn drop(&mut self) {
        for (key, value) in &self.0 {
            if let Some(value) = value {
                std::env::set_var(key, value);
            } else {
                std::env::remove_var(key);
            }
        }
    }
}

#[test]
fn named_legacy_import_tool_harness_and_packing_share_the_workspace_read_boundary() {
    let temp = Temp::new();
    let job = temp.dir("alice-job");
    let external = temp.dir("bob-job").join("private.csv");
    std::fs::write(&external, b"BOB_PRIVATE_SENTINEL,42").unwrap();
    let own = job.join("source.txt");
    std::fs::write(&own, b"allowed source").unwrap();
    let secret = job.join(".env");
    std::fs::write(&secret, b"synthetic-secret-never-read").unwrap();
    let _env = Env::named(&job);
    let paths = Paths::from_demo(temp.dir("demo"));
    assert!(
        civil_workbench::attach::allow_local_path(&paths, &external.to_string_lossy())
            .unwrap_err()
            .contains("outside")
    );
    assert!(
        civil_workbench::attach::allow_local_path(&paths, &secret.to_string_lossy())
            .unwrap_err()
            .contains("secret")
    );
    assert!(
        civil_workbench::attach::import_local(&paths, "import01", &external.to_string_lossy())
            .unwrap_err()
            .contains("outside")
    );
    let uploaded =
        civil_workbench::attach::import_local(&paths, "import02", &own.to_string_lossy()).unwrap();
    assert_eq!(uploaded.len(), 1);
    let expert = civil_workbench::catalog::seed()
        .experts
        .iter()
        .find(|e| e.id == "pm-daily")
        .unwrap()
        .clone();
    let run = civil_workbench::harness::run_expert_steps(
        &paths,
        &expert,
        civil_workbench::harness::Ticket::from_args(
            "harness01",
            &json!({"brief":"生成项目日报","path":external,"confirm_ok":true}),
        ),
    );
    assert!(run.error.unwrap().contains("outside"));
    assert!(run.files.is_empty());
    let mut ctx = civil_workbench::packs::ToolCtx::new(
        paths.clone(),
        "pack-ship",
        "material",
        "high",
        true,
        "tool01",
    );
    let response =
        civil_workbench::packs::execute(&mut ctx, "import_local", &json!({"path":external}));
    assert!(response.contains("outside"));
    assert!(!response.contains("BOB_PRIVATE_SENTINEL"));
    let response = civil_workbench::packs::execute(
        &mut ctx,
        "pack-ship__plan",
        &json!({"materials":external.to_string_lossy(),"connected":false}),
    );
    assert!(response.contains("已写入"), "{response}");
    let report =
        std::fs::read_to_string(ctx.deliverables.last().unwrap()["path"].as_str().unwrap())
            .unwrap();
    assert!(report.contains("outside"), "{report}");
    assert!(!report.contains("BOB_PRIVATE_SENTINEL"));
    #[cfg(unix)]
    {
        let linked = job.join("external.txt");
        std::os::unix::fs::symlink(&external, &linked).unwrap();
        assert!(
            civil_workbench::attach::import_local(&paths, "import03", &job.to_string_lossy())
                .unwrap_err()
                .contains("links")
        );
    }
    #[cfg(windows)]
    {
        let linked = job.join("external.txt");
        if std::os::windows::fs::symlink_file(&external, &linked).is_ok() {
            assert!(civil_workbench::attach::import_local(
                &paths,
                "import03",
                &job.to_string_lossy()
            )
            .unwrap_err()
            .contains("links"));
        }
    }
}

#[tokio::test]
async fn legacy_native_task_endpoints_reject_path_shaped_session_ids_before_writing() {
    let temp = Temp::new();
    let paths = Paths::from_demo(temp.dir("demo"));
    let out = paths.out_root.clone();
    let app = api::app(AppState::live(paths));
    for route in [
        "/api/harness/expert",
        "/api/firm/bid",
        "/api/eval/shadow",
        "/api/eval/shadow-expert",
    ] {
        let response=app.clone().oneshot(Request::builder().method("POST").uri(route).header("content-type","application/json")
            .body(Body::from(json!({"session_id":"../../other-user","expert_id":"pm-daily","brief":"生成项目日报"}).to_string())).unwrap()).await.unwrap();
        assert_eq!(response.status(), StatusCode::BAD_REQUEST, "{route}");
    }
    assert!(!out.exists());
    assert!(!temp.0.join("other-user").exists());
}
