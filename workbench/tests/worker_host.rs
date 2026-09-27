use civil_workbench::{
    product::worker::WorkerHost,
    runtime_core::{CancellationToken, WorkspaceContext},
};
use serde_json::json;
use std::{path::PathBuf, time::Duration};

struct Temp(PathBuf);
impl Temp {
    fn new() -> Self {
        let path = std::env::temp_dir().join(format!("civil-worker-host-{}", uuid::Uuid::new_v4()));
        std::fs::create_dir_all(&path).unwrap();
        Self(path)
    }
    fn workspace(&self) -> WorkspaceContext {
        WorkspaceContext::new(&self.0).unwrap()
    }
}
impl Drop for Temp {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.0);
    }
}
fn host() -> WorkerHost {
    WorkerHost::detect(
        PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .parent()
            .unwrap()
            .to_path_buf(),
    )
}

#[tokio::test]
async fn registered_modules_and_paths_are_checked_before_spawn() {
    let temp = Temp::new();
    let worker = host();
    let token = CancellationToken::new();
    assert!(worker.call("subprocess", &json!({}), &token).await.is_err());
    assert!(worker
        .call(
            "packing_assistant.documents.worker",
            &json!({"workspace":temp.0,"operation":"shell"}),
            &token
        )
        .await
        .is_err());
    assert!(worker
        .document(
            &temp.workspace(),
            "read",
            "../other.txt",
            None,
            json!({}),
            &token
        )
        .await
        .is_err());
    token.cancel();
    assert!(worker
        .capabilities(&temp.workspace(), &token)
        .await
        .is_err());
    assert!(!temp.0.join(".civil-buddy").exists());
}

#[tokio::test]
async fn actual_worker_reports_observed_capabilities() {
    let temp = Temp::new();
    let response = host()
        .capabilities(&temp.workspace(), &CancellationToken::new())
        .await
        .unwrap();
    assert_eq!(response["ok"], true, "{response}");
    assert_eq!(response["sandbox"]["enforces"]["read"], false);
    #[cfg(windows)]
    assert_eq!(response["sandbox"]["enforces"]["network"], false);
    assert_eq!(response["result"]["documents"]["docx"]["available"], true);
}

#[tokio::test]
async fn cancellation_kills_worker_before_it_can_publish_late_output() {
    // A trusted test bootstrap blocks after consuming stdin. It writes a marker
    // only if still alive after cancellation, reproducing a long parser/solver.
    let temp = Temp::new();
    let repository = Temp::new();
    let script_dir = repository.0.join("packing_assistant");
    std::fs::create_dir_all(&script_dir).unwrap();
    std::fs::write(
        script_dir.join("host_worker.py"),
        r#"
import json, os, pathlib, sys, time
request = json.loads(sys.stdin.readline())
root = pathlib.Path(request['workspace'])
(root / 'ready').write_text(str(os.getpid()))
time.sleep(0.7)
(root / 'late-output').write_text('should never publish')
time.sleep(30)
"#,
    )
    .unwrap();
    let mut worker = host();
    worker.repo_root = repository.0.clone();
    let cancel = CancellationToken::new();
    let worker_cancel = cancel.clone();
    let workspace = temp.workspace();
    let running =
        tokio::spawn(async move { worker.capabilities(&workspace, &worker_cancel).await });
    for _ in 0..200 {
        if temp.0.join("ready").exists() {
            break;
        }
        tokio::time::sleep(Duration::from_millis(10)).await;
    }
    assert!(temp.0.join("ready").exists(), "test worker never started");
    cancel.cancel();
    let result = tokio::time::timeout(Duration::from_secs(3), running)
        .await
        .unwrap()
        .unwrap();
    assert!(result.unwrap_err().contains("cancelled"));
    tokio::time::sleep(Duration::from_millis(800)).await;
    assert!(
        !temp.0.join("late-output").exists(),
        "cancelled process continued writing"
    );
}

#[tokio::test]
async fn output_limit_terminates_a_worker_that_would_otherwise_keep_running() {
    let temp = Temp::new();
    let repository = Temp::new();
    let script_dir = repository.0.join("packing_assistant");
    std::fs::create_dir_all(&script_dir).unwrap();
    std::fs::write(
        script_dir.join("host_worker.py"),
        r#"
import os, sys, time
sys.stdin.readline()
try:
    for _ in range(14):
        os.write(1, b'x' * 1024 * 1024)
except OSError:
    pass
time.sleep(30)
"#,
    )
    .unwrap();
    let mut worker = host();
    worker.repo_root = repository.0.clone();
    let result = tokio::time::timeout(
        Duration::from_secs(3),
        worker.capabilities(&temp.workspace(), &CancellationToken::new()),
    )
    .await;
    assert!(
        result.is_ok(),
        "oversized worker waited for its general 120 second deadline"
    );
    assert!(result.unwrap().unwrap_err().contains("output exceeds"));
}

#[tokio::test]
async fn provider_credentials_and_ambient_python_paths_are_not_inherited() {
    let temp = Temp::new();
    let repository = Temp::new();
    let script_dir = repository.0.join("packing_assistant");
    std::fs::create_dir_all(&script_dir).unwrap();
    std::fs::write(
        script_dir.join("host_worker.py"),
        r#"
import json, os, sys
request = json.loads(sys.stdin.readline())
print(json.dumps({'version': 1, 'ok': True, 'call_id': request['call_id'],
    'result': {'sentinel': os.environ.get('CIVIL_TEST_WORKER_API_KEY'),
               'pythonpath': os.environ.get('PYTHONPATH'),
               'provider_credentials': any(k.endswith('API_KEY') for k in os.environ),
               'isolated': sys.flags.isolated},
    'sandbox': {'enforces': {'write': True, 'spawn': True}}}))
"#,
    )
    .unwrap();
    // This test-only name cannot configure any provider. Restore its prior value
    // even if the assertion fails, without touching a real configured API key.
    struct Environment(Option<std::ffi::OsString>);
    impl Drop for Environment {
        fn drop(&mut self) {
            match self.0.take() {
                Some(value) => std::env::set_var("CIVIL_TEST_WORKER_API_KEY", value),
                None => std::env::remove_var("CIVIL_TEST_WORKER_API_KEY"),
            }
        }
    }
    let _restore = Environment(std::env::var_os("CIVIL_TEST_WORKER_API_KEY"));
    std::env::set_var("CIVIL_TEST_WORKER_API_KEY", "sentinel-must-not-cross");
    let mut worker = host();
    worker.repo_root = repository.0.clone();
    let response = worker
        .capabilities(&temp.workspace(), &CancellationToken::new())
        .await
        .unwrap();
    assert_eq!(response["result"]["sentinel"], serde_json::Value::Null);
    assert_eq!(response["result"]["pythonpath"], serde_json::Value::Null);
    assert_eq!(response["result"]["provider_credentials"], false);
    assert_eq!(response["result"]["isolated"], 1);
}
