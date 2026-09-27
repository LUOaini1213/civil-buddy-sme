//! The Rust workbench is the process the user starts. CAD, construction
//! planning, the packing-list ledger, and tender comparison stay the tested
//! Python tools. This module runs that app on a loopback port and forwards
//! only those routes, so `civil-workbench` can open the same pages.

use crate::config::Paths;
use axum::body::Body;
use axum::response::Response;
use reqwest::Client;
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;
use std::time::{Duration, Instant};

pub struct PyEngine {
    port: u16,
    child: Mutex<Option<Child>>,
    client: Client,
    token: Option<String>,
}

impl PyEngine {
    /// Attach only to the fixed, authenticated sidecar owned by the launcher.
    pub fn attach(base: &str, token: &str) -> Result<Self, String> {
        let url = reqwest::Url::parse(base).map_err(|_| "invalid domain address")?;
        if url.scheme() != "http"
            || url.host_str() != Some("127.0.0.1")
            || url.path() != "/"
            || url.query().is_some()
            || url.fragment().is_some()
            || !url.username().is_empty()
            || url.password().is_some()
            || token.len() < 32
        {
            return Err(
                "domain service requires a fixed loopback address and internal credential".into(),
            );
        }
        let port = url
            .port()
            .ok_or("domain service requires an explicit port")?;
        let client = Client::builder()
            .timeout(Duration::from_secs(300))
            .no_proxy()
            .redirect(reqwest::redirect::Policy::none())
            .build()
            .map_err(|e| e.to_string())?;
        Ok(Self {
            port,
            child: Mutex::new(None),
            client,
            token: Some(token.to_owned()),
        })
    }

    pub fn start(paths: &Paths) -> Result<Self, String> {
        let python = std::env::var("PYTHON").unwrap_or_else(|_| {
            if cfg!(windows) {
                "python".into()
            } else {
                "python3".into()
            }
        });
        let port = free_port()?;
        let child = Command::new(&python)
            .args([
                "-m",
                "uvicorn",
                "app:app",
                "--host",
                "127.0.0.1",
                "--port",
                &port.to_string(),
                "--no-access-log",
            ])
            .current_dir(&paths.demo_root)
            .env("PYTHONPATH", &paths.repo_root)
            .env("PYTHONUTF8", "1")
            .env("CIVIL_ENGINE_CHILD", "1")
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .spawn()
            .map_err(|e| format!("无法启动 Python 工具引擎（{python}）：{e}"))?;
        let client = Client::builder()
            .timeout(Duration::from_secs(120))
            .no_proxy()
            .build()
            .map_err(|e| e.to_string())?;
        let engine = Self {
            port,
            child: Mutex::new(Some(child)),
            client,
            token: None,
        };
        let deadline = Instant::now() + Duration::from_secs(25);
        let health = format!("http://127.0.0.1:{port}/api/health");
        let probe = reqwest::blocking::Client::builder()
            .timeout(Duration::from_secs(1))
            .no_proxy()
            .build()
            .map_err(|e| e.to_string())?;
        while Instant::now() < deadline {
            if engine
                .child
                .lock()
                .map(|mut c| {
                    c.as_mut()
                        .is_some_and(|c| c.try_wait().ok().flatten().is_some())
                })
                .unwrap_or(false)
            {
                return Err(
                    "Python 工具引擎启动后立即退出。请确认已安装 requirements.txt。".into(),
                );
            }
            if let Ok(resp) = probe.get(&health).send() {
                if resp.status().is_success() {
                    if let Ok(body) = resp.json::<serde_json::Value>() {
                        if body.get("product").and_then(|v| v.as_str()) == Some("civil-codex") {
                            return Ok(engine);
                        }
                    }
                }
            }
            std::thread::sleep(Duration::from_millis(150));
        }
        if let Ok(mut guard) = engine.child.lock() {
            if let Some(child) = guard.as_mut() {
                let _ = child.kill();
            }
        }
        Err("Python 工具引擎在 25 秒内没有通过健康检查。".into())
    }

    pub fn base(&self) -> String {
        format!("http://127.0.0.1:{}", self.port)
    }

    pub async fn forward(
        &self,
        method: reqwest::Method,
        path_and_query: &str,
        content_type: Option<&str>,
        body: Vec<u8>,
    ) -> Response {
        let url = format!("{}{}", self.base(), path_and_query);
        let mut req = self.client.request(method, &url).body(body);
        if let Some(token) = &self.token {
            req = req.bearer_auth(token);
        }
        if let Some(ct) = content_type {
            req = req.header(reqwest::header::CONTENT_TYPE, ct);
        }
        match req.send().await {
            Ok(resp) => {
                let status = resp.status();
                let ct = resp.headers().get(reqwest::header::CONTENT_TYPE).cloned();
                match resp.bytes().await {
                    Ok(bytes) => {
                        let mut out = Response::builder().status(status.as_u16());
                        if let Some(ct) = ct {
                            if let Ok(v) = ct.to_str() {
                                out = out.header(axum::http::header::CONTENT_TYPE, v);
                            }
                        }
                        out.body(Body::from(bytes))
                            .unwrap_or_else(|_| Response::new(Body::from("engine response")))
                    }
                    Err(e) => text_status(502, format!("工具引擎读取失败：{e}")),
                }
            }
            Err(e) => text_status(502, format!("工具引擎不可用：{e}")),
        }
    }
}

impl Drop for PyEngine {
    fn drop(&mut self) {
        if let Ok(mut guard) = self.child.lock() {
            if let Some(child) = guard.as_mut() {
                let _ = child.kill();
                let _ = child.wait();
            }
        }
    }
}

fn text_status(code: u16, msg: String) -> Response {
    Response::builder()
        .status(code)
        .body(Body::from(msg))
        .unwrap_or_else(|_| Response::new(Body::empty()))
}

fn free_port() -> Result<u16, String> {
    let listener = std::net::TcpListener::bind("127.0.0.1:0").map_err(|e| e.to_string())?;
    listener
        .local_addr()
        .map(|a| a.port())
        .map_err(|e| e.to_string())
}

/// Pages and APIs that belong to the Python tool engine, not the Rust chat host.
pub fn tool_route(path: &str) -> bool {
    let path = path.split('?').next().unwrap_or(path);
    path == "/cad"
        || path.starts_with("/cad/")
        || path == "/logistics"
        || path.starts_with("/logistics/")
        || path == "/engineering"
        || path.starts_with("/engineering/")
        || path.starts_with("/api/cad/")
        || path == "/api/cad"
        || path.starts_with("/api/logistics/")
        || path == "/api/logistics"
        || path.starts_with("/api/engineering/")
        || path == "/api/engineering"
        || path == "/api/task-route"
        || path.starts_with("/api/workflows/")
        || path.starts_with("/api/experts/")
}

pub fn chat_needs_python_tools(
    message: &str,
    cad: &str,
    planning: &str,
    logistics: &str,
    roles: usize,
) -> bool {
    !cad.is_empty()
        || !planning.is_empty()
        || !logistics.is_empty()
        || roles > 0
        || message.contains("招标")
        || message.contains("投标响应")
        || message.contains("全面检查")
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn tool_pages_are_forwarded_and_rust_chat_is_not() {
        assert!(tool_route("/cad"));
        assert!(tool_route("/engineering/planning"));
        assert!(tool_route("/logistics"));
        assert!(tool_route("/api/cad/import"));
        assert!(tool_route("/api/logistics/projects"));
        assert!(tool_route("/api/engineering/planning/calculate"));
        assert!(!tool_route("/api/chat"));
        assert!(!tool_route("/api/health"));
        assert!(!tool_route("/"));
    }

    #[test]
    fn tender_and_tool_context_use_the_python_turn() {
        assert!(chat_needs_python_tools("全面检查投标响应", "", "", "", 0));
        assert!(chat_needs_python_tools("你好", "abc", "", "", 0));
        assert!(!chat_needs_python_tools("出一份项目日报", "", "", "", 0));
    }
}
