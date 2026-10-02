use crate::runtime_core::{CancellationToken, WorkspaceContext};
use serde_json::{json, Value};
use std::path::PathBuf;
use std::process::Stdio;
use std::time::Duration;
use tokio::io::{AsyncReadExt, AsyncWriteExt};

#[derive(Clone)]
pub struct WorkerHost {
    pub python: PathBuf,
    pub repo_root: PathBuf,
}

impl WorkerHost {
    pub fn detect(repo_root: PathBuf) -> Self {
        let local = repo_root.join(if cfg!(windows) {
            ".venv/Scripts/python.exe"
        } else {
            ".venv/bin/python"
        });
        let python = std::env::var_os("CIVIL_PYTHON")
            .map(PathBuf::from)
            .unwrap_or_else(|| {
                if local.is_file() {
                    local
                } else {
                    PathBuf::from("python")
                }
            });
        Self { python, repo_root }
    }

    /// Fixed deterministic services only. The trusted bootstrap establishes OS
    /// confinement before parsing stdin and never invokes a Python agent loop.
    /// `CIVIL_WORKER_SANDBOX=app` is an explicit host setting, never model input.
    pub async fn call(
        &self,
        module: &str,
        request: &Value,
        cancel: &CancellationToken,
    ) -> Result<Value, String> {
        if !matches!(
            module,
            "packing_assistant.documents.worker"
                | "packing_assistant.engineering.worker"
                | "packing_assistant.retrieval.worker"
                | "packing_assistant.review_worker"
                | "packing_assistant.host_worker"
        ) {
            return Err("worker module is not registered".into());
        }
        let root = request
            .get("workspace")
            .and_then(Value::as_str)
            .ok_or("worker requires an explicit workspace")?;
        let workspace = WorkspaceContext::new(root).map_err(|e| e.to_string())?;
        let operation = request
            .get("operation")
            .and_then(Value::as_str)
            .ok_or("worker operation is required")?;
        let allowed = match module {
            "packing_assistant.documents.worker" => matches!(
                operation,
                "capabilities" | "inspect" | "read" | "preview" | "apply" | "validate" | "inspect_readiness"
            ),
            "packing_assistant.engineering.worker" => {
                matches!(operation, "frame" | "section" | "ifc_check" | "ifc_diff" | "packing_replan")
            }
            "packing_assistant.retrieval.worker" => {
                matches!(operation, "index" | "search" | "verify")
            }
            "packing_assistant.review_worker" => operation == "verdicts",
            _ => operation == "capabilities",
        };
        if !allowed {
            return Err("worker operation is not registered".into());
        }
        if module == "packing_assistant.documents.worker" && operation != "capabilities" {
            let source = request
                .get("source")
                .and_then(Value::as_str)
                .ok_or("document source is required")?;
            workspace.resolve_read(source).map_err(|e| e.to_string())?;
        }
        if module == "packing_assistant.retrieval.worker" {
            let sources = request
                .get("sources")
                .and_then(Value::as_array)
                .ok_or("retrieval sources are required")?;
            for source in sources {
                let source = source.as_str().ok_or("retrieval source must be a path")?;
                workspace.resolve_read(source).map_err(|e| e.to_string())?;
            }
        }
        if module == "packing_assistant.engineering.worker" && operation == "packing_replan" {
            let source = request["payload"]["source"].as_str().ok_or("packing source is required")?;
            workspace.resolve_read(source).map_err(|e| e.to_string())?;
        }
        workspace
            .resolve_write(".tmp/worker-scope-check")
            .map_err(|e| e.to_string())?;
        let mut request = request.clone();
        request["workspace"] = json!(workspace.root());
        request["output_dir"] = json!(workspace.output_root());
        let input = serde_json::to_vec(&request).map_err(|e| e.to_string())?;
        if input.len() > 32 * 1024 * 1024 {
            return Err("worker input exceeds 32 MiB".into());
        }
        cancel.check().map_err(|e| e.to_string())?;
        let backend = std::env::var("CIVIL_WORKER_SANDBOX").unwrap_or_else(|_| "os".into());
        if !matches!(backend.as_str(), "os" | "app") {
            return Err("CIVIL_WORKER_SANDBOX must be os or app".into());
        }
        let script = self.repo_root.join("packing_assistant/host_worker.py");
        if !script.is_file() {
            return Err("fixed Python host worker is missing".into());
        }
        let mut command = tokio::process::Command::new(&self.python);
        // -I ignores Python paths and user site packages; the fixed script adds
        // only its repository root before loading registered code.
        command
            .arg("-I")
            .arg("-B")
            .arg(&script)
            .current_dir(&self.repo_root)
            .env_clear()
            .env("PYTHONUTF8", "1")
            .env("PYTHONIOENCODING", "utf-8")
            .env("PYTHONDONTWRITEBYTECODE", "1")
            .env("PYTHON_DOTENV_DISABLED", "1")
            .env("MPLBACKEND", "Agg")
            .env("NUMBA_NUM_THREADS", "1")
            .env("OPENBLAS_NUM_THREADS", "1")
            .env("OMP_NUM_THREADS", "1")
            .env("CIVIL_HOST_WORKSPACE", workspace.root())
            .env("CIVIL_HOST_MODULE", module)
            .env("CIVIL_HOST_SANDBOX", &backend)
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .kill_on_drop(true);
        for name in [
            "SystemRoot",
            "WINDIR",
            "PATH",
            "PATHEXT",
            "TEMP",
            "TMP",
            "LANG",
            "USERPROFILE",
            "HOMEDRIVE",
            "HOMEPATH",
            "HOME",
        ] {
            if let Some(value) = std::env::var_os(name) {
                command.env(name, value);
            }
        }
        #[cfg(windows)]
        command.creation_flags(0x08000000);
        let mut child = command
            .spawn()
            .map_err(|_| "cannot start Python worker; configure CIVIL_PYTHON".to_string())?;
        let mut stdin = child.stdin.take().ok_or("missing worker stdin")?;
        let stdout = child.stdout.take().ok_or("missing worker stdout")?;
        let stderr = child.stderr.take().ok_or("missing worker stderr")?;
        let writer = tokio::spawn(async move {
            stdin.write_all(&input).await?;
            stdin.write_all(b"\n").await?;
            stdin.shutdown().await
        });
        let (limit_signal, mut limits) = tokio::sync::mpsc::channel::<&'static str>(2);
        let output_signal = limit_signal.clone();
        let error_signal = limit_signal.clone();
        let output = tokio::spawn(async move {
            let mut bytes = Vec::new();
            stdout
                .take(12 * 1024 * 1024 + 1)
                .read_to_end(&mut bytes)
                .await?;
            if bytes.len() > 12 * 1024 * 1024 {
                let _ = output_signal.try_send("worker output exceeds 12 MiB");
            }
            Ok::<_, std::io::Error>(bytes)
        });
        let errors = tokio::spawn(async move {
            let mut bytes = Vec::new();
            stderr.take(64 * 1024 + 1).read_to_end(&mut bytes).await?;
            if bytes.len() > 64 * 1024 {
                let _ = error_signal.try_send("worker diagnostics exceed 64 KiB");
            }
            Ok::<_, std::io::Error>(bytes)
        });
        let status = tokio::select! {
            status = child.wait() => status.map_err(|_| "worker wait failed".to_string()),
            _ = tokio::time::sleep(Duration::from_secs(120)) => { let _ = child.kill().await; let _ = child.wait().await; Err("worker timed out".into()) },
            _ = super::providers::cancelled(cancel) => { let _ = child.kill().await; let _ = child.wait().await; Err("worker cancelled".into()) },
            Some(reason) = limits.recv() => { let _ = child.kill().await; let _ = child.wait().await; Err(reason.into()) },
        };
        drop(limit_signal);
        let _ = writer.await;
        let bytes = output
            .await
            .map_err(|_| "worker output task failed")?
            .map_err(|_| "worker output failed")?;
        let diagnostics = errors
            .await
            .map_err(|_| "worker diagnostics task failed")?
            .map_err(|_| "worker diagnostics failed")?;
        let status = status?;
        cancel.check().map_err(|e| e.to_string())?;
        if bytes.len() > 12 * 1024 * 1024 {
            return Err("worker output exceeds 12 MiB".into());
        }
        if diagnostics.len() > 64 * 1024 {
            return Err("worker diagnostics exceed 64 KiB".into());
        }
        if !status.success() {
            return Err(format!(
                "worker exited with {}",
                status.code().unwrap_or(-1)
            ));
        }
        let response: Value =
            serde_json::from_slice(&bytes).map_err(|_| "worker returned invalid JSON")?;
        if response.get("version") != Some(&json!(1))
            || !response.get("ok").is_some_and(Value::is_boolean)
        {
            return Err("worker returned an invalid protocol envelope".into());
        }
        if response["ok"] == true
            && backend == "os"
            && (response["sandbox"]["enforces"]["write"] != true
                || response["sandbox"]["enforces"]["spawn"] != true)
        {
            return Err("worker did not prove the required OS confinement".into());
        }
        Ok(response)
    }

    /// Executes a real child-process probe, not a platform-name assumption.
    pub async fn capabilities(
        &self,
        workspace: &WorkspaceContext,
        cancel: &CancellationToken,
    ) -> Result<Value, String> {
        self.call(
            "packing_assistant.host_worker",
            &json!({"version":1,"call_id":uuid::Uuid::new_v4().to_string(),
            "operation":"capabilities","workspace":workspace.root()}),
            cancel,
        )
        .await
    }

    pub async fn engineering(
        &self,
        workspace: &WorkspaceContext,
        operation: &str,
        payload: Value,
        cancel: &CancellationToken,
    ) -> Result<Value, String> {
        self.call(
            "packing_assistant.engineering.worker",
            &json!({"version":1,"call_id":uuid::Uuid::new_v4().to_string(),
            "operation":operation,"workspace":workspace.root(),"payload":payload}),
            cancel,
        )
        .await
    }

    pub async fn document(
        &self,
        workspace: &WorkspaceContext,
        operation: &str,
        source: &str,
        expected: Option<&str>,
        arguments: Value,
        cancel: &CancellationToken,
    ) -> Result<Value, String> {
        let call_id = uuid::Uuid::new_v4().to_string();
        self.document_as(&call_id, workspace, operation, source, expected, arguments, cancel)
            .await
    }

    /// `call_id` is the document worker's replay key: an apply delivered again
    /// with the same call_id and request returns the first draft
    /// (`replayed: true`) instead of writing another one.
    #[allow(clippy::too_many_arguments)]
    pub async fn document_as(
        &self,
        call_id: &str,
        workspace: &WorkspaceContext,
        operation: &str,
        source: &str,
        expected: Option<&str>,
        arguments: Value,
        cancel: &CancellationToken,
    ) -> Result<Value, String> {
        self.call("packing_assistant.documents.worker", &json!({"version":1,"call_id":call_id,
            "operation":operation,"workspace":workspace.root(),"source":source,"expected_sha256":expected,
            "arguments":arguments,"output_dir":workspace.output_root()}), cancel).await
    }
}

/// Deterministic apply call_id: the same turn delivering the same previewed
/// patch (the preview's sha256) maps to one worker call, so a repeat replays.
/// A later turn gets a new id and may deliberately write a new draft.
pub fn document_call_id(turn: &str, preview_sha256: &str) -> String {
    format!(
        "apply-{}",
        super::tools::sha256(format!("{turn}\n{preview_sha256}").as_bytes())
    )
}
