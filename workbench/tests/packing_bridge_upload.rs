//! A real loopback receiver checks the table boundary without a model or solver.
use axum::{extract::Multipart, routing::post, Json, Router};
use civil_workbench::packing_bridge;
use serde_json::{json, Value};
use std::{
    ffi::OsString,
    sync::{
        atomic::{AtomicBool, Ordering},
        Arc, Mutex,
    },
};

struct PackingEnv(Option<OsString>);
impl Drop for PackingEnv {
    fn drop(&mut self) {
        if let Some(old) = &self.0 {
            std::env::set_var("PACKING_AGENT_URL", old);
        } else {
            std::env::remove_var("PACKING_AGENT_URL");
        }
    }
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn selected_table_upload_is_bounded_and_incomplete_rows_never_reach_solver() {
    let uploads = Arc::new(Mutex::new(Vec::<(String, Vec<u8>)>::new()));
    let pipelines = Arc::new(Mutex::new(Vec::<Value>::new()));
    let incomplete = Arc::new(AtomicBool::new(false));
    let seen_uploads = uploads.clone();
    let seen_pipelines = pipelines.clone();
    let reject_rows = incomplete.clone();
    let app = Router::new()
        .route(
            "/packing/api/table/parse",
            post(move |mut form: Multipart| {
                let uploads = seen_uploads.clone();
                let incomplete = reject_rows.clone();
                async move {
                    let field = form.next_field().await.unwrap().unwrap();
                    assert_eq!(field.name(), Some("file"));
                    let name = field.file_name().unwrap().to_owned();
                    let data = field.bytes().await.unwrap();
                    uploads.lock().unwrap().push((name, data.to_vec()));
                    assert!(
                        form.next_field().await.unwrap().is_none(),
                        "path was sent to the worker"
                    );
                    Json(json!({
                        "ok": !incomplete.load(Ordering::Relaxed),
                        "errors": ["synthetic missing weight"],
                        "materials": [{"id":"fixture-1","name":"synthetic panel","quantity":1,
                            "length_mm":1000,"width_mm":500,"height_mm":20,"weight_kg":10}]
                    }))
                }
            }),
        )
        .route(
            "/packing/api/pipeline",
            post(move |Json(body): Json<Value>| {
                let pipelines = seen_pipelines.clone();
                async move {
                    pipelines.lock().unwrap().push(body);
                    Json(json!({"ok":true,"summary":{"boxes":1,"can_fit":true,"phase":"done"}}))
                }
            }),
        );
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let _env = PackingEnv(std::env::var_os("PACKING_AGENT_URL"));
    std::env::set_var(
        "PACKING_AGENT_URL",
        format!("http://{}/packing", listener.local_addr().unwrap()),
    );
    let server = tokio::spawn(async move {
        axum::serve(listener, app).await.unwrap();
    });
    let dir = std::env::temp_dir().join(format!("civil-table-boundary-{}", uuid::Uuid::new_v4()));
    std::fs::create_dir(&dir).unwrap();
    let table = dir.join("private-project-name.csv");
    let data =
        b"name,quantity,length_mm,width_mm,height_mm,weight_kg\nsynthetic panel,1,1000,500,20,10\n";
    std::fs::write(&table, data).unwrap();
    let selected = table.clone();
    let result =
        tokio::task::spawn_blocking(move || packing_bridge::run_table(&selected, "fixture only"))
            .await
            .unwrap()
            .unwrap();
    assert!(result.ok);
    assert_eq!(
        uploads.lock().unwrap()[0],
        ("upload.csv".into(), data.to_vec())
    );
    let body = pipelines.lock().unwrap()[0].clone();
    assert_eq!(body["agent_mode"], "steps");
    assert_eq!(body["materials"][0]["id"], "fixture-1");
    assert!(!body.to_string().contains("private-project-name"));

    incomplete.store(true, Ordering::Relaxed);
    let selected = table.clone();
    let result =
        tokio::task::spawn_blocking(move || packing_bridge::run_table(&selected, "fixture only"))
            .await
            .unwrap();
    assert!(result.unwrap_err().contains("需要补全"));
    assert_eq!(
        pipelines.lock().unwrap().len(),
        1,
        "incomplete materials reached solver"
    );

    std::fs::File::create(&table)
        .unwrap()
        .set_len(20 * 1024 * 1024 + 1)
        .unwrap();
    let selected = table.clone();
    let result =
        tokio::task::spawn_blocking(move || packing_bridge::run_table(&selected, "fixture only"))
            .await
            .unwrap();
    assert!(result.unwrap_err().contains("20MiB"));
    assert_eq!(
        uploads.lock().unwrap().len(),
        2,
        "oversized file reached worker"
    );
    server.abort();
    std::fs::remove_file(table).unwrap();
    std::fs::remove_dir(dir).unwrap();
}
