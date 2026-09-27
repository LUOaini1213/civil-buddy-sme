//! Sidecar to packing-agent. Numbers come from tools only; this crate never invents xyz.
//!
//! Connect:
//! - `PACKING_AGENT_URL` — e.g. http://127.0.0.1:8000  (POST /api/pipeline)
//! - `PACKING_AGENT_ROOT` — local checkout; runs `scripts/run_packing_sidecar.py`

use serde_json::{json, Value};
use std::env;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::time::Duration;

#[derive(Debug, Clone, Default, serde::Deserialize)]
#[serde(default)]
pub struct PackingSummary {
    pub ok: bool,
    pub n0: String,
    pub containers_used: String,
    pub boxes: String,
    pub can_fit: String,
    pub ship_ok: String,
    pub phase: String,
    pub source: String,
    pub utilization: String,
    pub error: Option<String>,
}

impl PackingSummary {
    pub fn markdown(&self) -> String {
        if let Some(e) = &self.error {
            return format!(
                "未接通或失败：{e}\n设 `PACKING_AGENT_URL`（http://127.0.0.1:8000）或 `PACKING_AGENT_ROOT`（packing-agent 仓库路径）后重跑。本岗**不编**柜数、xyz、重心。\n"
            );
        }
        format!(
            "- 来源：{}\n- ok: {}\n- n0: {}\n- containers_used: {}\n- boxes: {}\n- can_fit: {}\n- ship_ok: {}\n- phase: {}\n- 坐标/摆位：见 packing-agent 产物；本岗不重写 xyz。\n",
            self.source, self.ok, self.n0, self.containers_used, self.boxes, self.can_fit, self.ship_ok, self.phase
        )
    }

    pub fn n0_line(&self) -> String {
        if self.error.is_some() {
            "柜数 N0* = UNSPECIFIED；摆位 xyz = UNSPECIFIED。".into()
        } else {
            "柜数/N0* 以上文工具回传为准；未出现的数字标 UNSPECIFIED。".into()
        }
    }
}

pub fn url_configured() -> Option<String> {
    // 默认指向本仓 gateway 的默认端口；未起服务时 run() 的报错会给出启动指引。
    let base = env::var("PACKING_AGENT_URL")
        .unwrap_or_else(|_| "http://127.0.0.1:8000".to_string())
        .trim()
        .trim_end_matches('/')
        .to_string();
    if base.is_empty() {
        return None;
    }
    if !(base.starts_with("http://127.0.0.1")
        || base.starts_with("http://localhost")
        || base.starts_with("https://"))
    {
        return None;
    }
    Some(base)
}

fn looks_like_engine(p: &Path) -> bool {
    p.is_dir() && p.join("packing_assistant").is_dir()
}

pub fn root_configured() -> Option<PathBuf> {
    let p = env::var("PACKING_AGENT_ROOT").unwrap_or_default();
    let p = PathBuf::from(p.trim());
    if looks_like_engine(&p) {
        return Some(p);
    }
    if let Ok(m) = env::var("CARGO_MANIFEST_DIR") {
        let repo = Path::new(&m).parent().map(Path::to_path_buf);
        if let Some(repo) = repo {
            if looks_like_engine(&repo) {
                return Some(repo);
            }
        }
    }
    if let Ok(cwd) = env::current_dir() {
        if looks_like_engine(&cwd) {
            return Some(cwd);
        }
        if looks_like_engine(&cwd.join("..")) {
            return Some(cwd.join(".."));
        }
    }
    None
}

pub fn probe() -> Value {
    let url = url_configured();
    let root = root_configured();
    let mut http = json!({"configured": url.is_some(), "up": false});
    if let Some(base) = url.clone() {
        http["url"] = json!(base);
        if let Ok(h) = http_health(&base) {
            http["up"] = json!(true);
            http["health"] = h;
        }
    }
    json!({
        "http": http,
        "python_root": root.as_ref().map(|p| p.display().to_string()),
        "connected": url.is_some() || root.is_some(),
    })
}

/// Bid-parse extract via the same Python transform as packing tender-handoff.
pub fn tender_extract(tender_text: &str, project_name: &str) -> Result<Value, String> {
    let root = root_configured().ok_or_else(|| "no packing_assistant root".to_string())?;
    let script = {
        if let Ok(m) = env::var("CARGO_MANIFEST_DIR") {
            let p = Path::new(&m).join("scripts").join("run_tender_extract.py");
            if p.is_file() {
                p
            } else {
                root.join("workbench")
                    .join("scripts")
                    .join("run_tender_extract.py")
            }
        } else {
            root.join("workbench")
                .join("scripts")
                .join("run_tender_extract.py")
        }
    };
    if !script.is_file() {
        return Err("run_tender_extract.py missing".into());
    }
    let mut child = Command::new("python")
        .arg(&script)
        .env("PACKING_AGENT_ROOT", &root)
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .map_err(|e| format!("python: {e}"))?;
    let payload = json!({"tender_text": tender_text, "project_name": project_name});
    if let Some(stdin) = child.stdin.as_mut() {
        use std::io::Write;
        stdin
            .write_all(payload.to_string().as_bytes())
            .map_err(|e| e.to_string())?;
    }
    let out = child.wait_with_output().map_err(|e| e.to_string())?;
    if !out.status.success() {
        return Err(format!(
            "extract exit {:?} {}",
            out.status.code(),
            String::from_utf8_lossy(&out.stderr)
                .chars()
                .take(300)
                .collect::<String>()
        ));
    }
    serde_json::from_slice(&out.stdout).map_err(|e| e.to_string())
}

/// ux(round20)：**按本机路径装箱**。
///
/// 为什么不是把路径塞进 `user_input` 让网关去抠：网关的
/// `_load_materials_from_text`（gateway/app.py:1031）出于防目录穿越，**只认仓库内
/// 相对路径**，桌面路径会被丢弃并**静默回落到演示预设物料** —— 不报错、照样给一串
/// 很像样的柜数，演示时极易把样例数据当成用户的表。那个沙箱本身是对的，因为
/// `user_input` 里可能混着 LLM 生成或从文档粘来的内容，不能让它随便读盘。
///
/// 所以改走**显式通道**，职责各归各位：
///   1. exe 侧用 `attach::allow_local_path` 判断这个路径能不能读（该策略本来就归 exe，
///      与老「导入」按钮同一信任级别：路径是用户显式给的，不是从自由文本猜的）；
///   2. 网关 `/api/table/parse/json` 把表读成 materials（Python 的 table_mapper 能处理
///      脏表头与单位归一，见 test/generic_tables/G6_messy_headers；在 Rust 重写是倒退）；
///   3. materials **数组**直接喂 `/api/pipeline` —— 不传路径，沙箱问题根本不存在。
///
/// 两个端点都是现成的，网关侧零改动。
/// Windows 的 `canonicalize` 会返回 `\\?\C:\...` 这种扩展长度前缀，
/// 打进作业单里很难看。只用于显示，不改实际传给网关的路径语义。
pub fn pretty_path(p: &std::path::Path) -> String {
    let s = p.to_string_lossy().to_string();
    s.strip_prefix("\\\\?\\").map(|x| x.to_string()).unwrap_or(s)
}

pub fn run_table(path: &std::path::Path, notes: &str) -> Result<PackingSummary, String> {
    use std::io::Read;

    let base = url_configured().ok_or_else(|| "未配置装箱网关地址".to_string())?;
    let client = reqwest::blocking::Client::builder()
        .timeout(Duration::from_secs(240))
        .http1_only()
        .build()
        .map_err(|e| e.to_string())?;

    // 1) The host has already authorized this user-selected path. Upload the
    // bounded bytes so the domain worker does not need arbitrary file access.
    const MAX_TABLE_BYTES: u64 = 20 * 1024 * 1024;
    let extension = path
        .extension()
        .and_then(|s| s.to_str())
        .unwrap_or("")
        .to_ascii_lowercase();
    if !matches!(
        extension.as_str(),
        "csv" | "tsv" | "txt" | "xlsx" | "xlsm" | "json" | "pdf"
    ) {
        return Err("材料表格式不支持；请选择 CSV、TSV、TXT、XLSX、XLSM、JSON 或 PDF".into());
    }
    let file = std::fs::File::open(path).map_err(|e| format!("无法读取材料表：{e}"))?;
    if file.metadata().map_err(|e| e.to_string())?.len() > MAX_TABLE_BYTES {
        return Err("材料表超过20MiB，请拆分后上传".into());
    }
    let mut table = Vec::new();
    file.take(MAX_TABLE_BYTES + 1)
        .read_to_end(&mut table)
        .map_err(|e| format!("材料表读取失败：{e}"))?;
    if table.len() as u64 > MAX_TABLE_BYTES {
        return Err("材料表超过20MiB，请拆分后上传".into());
    }
    let boundary = format!("civil-table-{}", uuid::Uuid::new_v4());
    let mut upload = format!(
        "--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"upload.{extension}\"\r\nContent-Type: application/octet-stream\r\n\r\n"
    ).into_bytes();
    upload.extend_from_slice(&table);
    upload.extend_from_slice(format!("\r\n--{boundary}--\r\n").as_bytes());
    let parse_url = format!("{base}/api/table/parse");
    let resp = client
        .post(&parse_url)
        .header(
            reqwest::header::CONTENT_TYPE,
            format!("multipart/form-data; boundary={boundary}"),
        )
        .body(upload)
        .send()
        .map_err(|e| format!("表解析请求失败：{e}"))?;
    if !resp.status().is_success() {
        return Err(format!("表解析 HTTP {}", resp.status()));
    }
    let parsed: Value = resp.json().map_err(|e| e.to_string())?;
    if parsed.get("ok").and_then(Value::as_bool) == Some(false) {
        return Err(format!(
            "材料表需要补全后才能装箱：{}",
            parsed.get("errors").unwrap_or(&Value::Null)
        ));
    }
    let mats = parsed
        .get("materials")
        .and_then(|v| v.as_array())
        .cloned()
        .unwrap_or_default();
    if mats.is_empty() {
        return Err(format!(
            "表里没解析出物料：{}（检查表头是否含 名称/数量/长宽高/重量 等列）",
            pretty_path(path)
        ));
    }

    // 2) materials → 装箱
    let run_url = format!("{base}/api/pipeline");
    let payload = json!({
        "materials": mats,
        "user_input": notes,
        "mode": "steps",
        "agent_mode": "steps",
        "enable_auto_confirm": true,
        "save_artifacts": false,
        "session_id": format!("civil-buddy-tbl-{}", chrono::Local::now().format("%H%M%S")),
    });
    let resp = client
        .post(&run_url)
        .json(&payload)
        .send()
        .map_err(|e| format!("装箱请求失败：{e}"))?;
    if !resp.status().is_success() {
        return Err(format!("装箱 HTTP {}", resp.status()));
    }
    let v: Value = resp.json().map_err(|e| e.to_string())?;
    let mut sum = summarize_pipeline_json(&v, &format!("http {base} · 表 {}", pretty_path(path)));
    sum.source = format!("{} · {} 条物料", sum.source, mats.len());
    Ok(sum)
}

pub fn run(materials: &str, notes: &str) -> PackingSummary {
    let mut http_err: Option<String> = None;
    if let Some(base) = url_configured() {
        match http_pipeline(&base, materials, notes) {
            Ok(s) => return s,
            Err(e) => http_err = Some(format!("HTTP {base}: {e}")),
        }
    }
    if let Some(root) = root_configured() {
        if sidecar_script().is_some() {
            return match python_sidecar(&root, materials, notes) {
                Ok(s) => s,
                Err(e) => PackingSummary {
                    error: Some(format!("sidecar: {e}")),
                    ..Default::default()
                },
            };
        }
    }
    PackingSummary {
        error: Some(http_err.unwrap_or_else(|| {
            "未接通 packing 引擎：先启动 gateway（python -m uvicorn gateway.app:app --host 127.0.0.1 --port 8000），或设 PACKING_AGENT_URL / PACKING_AGENT_ROOT".into()
        })),
        ..Default::default()
    }
}

pub fn summarize_pipeline_json(v: &Value, source: &str) -> PackingSummary {
    let summary = v.get("summary").cloned().unwrap_or(json!({}));
    let pick = |keys: &[&str]| -> String {
        for k in keys {
            if let Some(x) = summary.get(*k).filter(|x| !x.is_null()) {
                return x.to_string();
            }
            if let Some(x) = v.get(*k).filter(|x| !x.is_null()) {
                return x.to_string();
            }
        }
        "UNSPECIFIED".into()
    };
    PackingSummary {
        ok: v
            .get("ok")
            .and_then(|x| x.as_bool())
            .or_else(|| summary.get("ok").and_then(|x| x.as_bool()))
            .unwrap_or(false),
        n0: pick(&["n0"]),
        containers_used: pick(&["containers_used"]),
        boxes: pick(&["boxes"]),
        can_fit: pick(&["can_fit"]),
        ship_ok: pick(&["ship_ok"]),
        phase: pick(&["phase"]),
        utilization: pick(&["booking_volume_utilization", "outer_space_utilization", "space_utilization"]),
        source: source.to_string(),
        error: None,
    }
}

fn http_health(base: &str) -> Result<Value, String> {
    let client = reqwest::blocking::Client::builder()
        .timeout(Duration::from_secs(4))
        .http1_only()
        .build()
        .map_err(|e| e.to_string())?;
    let resp = client
        .get(format!("{base}/api/health"))
        .send()
        .map_err(|e| e.to_string())?;
    if !resp.status().is_success() {
        return Err(format!("HTTP {}", resp.status()));
    }
    resp.json().map_err(|e| e.to_string())
}

fn http_pipeline(base: &str, materials: &str, notes: &str) -> Result<PackingSummary, String> {
    let url = format!("{base}/api/pipeline");
    let payload = json!({
        "user_input": format!("{materials}\n{notes}"),
        "mode": "steps",
        "agent_mode": "steps",
        "enable_auto_confirm": true,
        "save_artifacts": false,
        "session_id": format!("civil-buddy-{}", chrono::Local::now().format("%H%M%S")),
    });
    let client = reqwest::blocking::Client::builder()
        .timeout(Duration::from_secs(180))
        .http1_only()
        .build()
        .map_err(|e| e.to_string())?;
    let resp = client
        .post(url)
        .json(&payload)
        .send()
        .map_err(|e| e.to_string())?;
    if !resp.status().is_success() {
        return Err(format!("HTTP {}", resp.status()));
    }
    let v: Value = resp.json().map_err(|e| e.to_string())?;
    Ok(summarize_pipeline_json(&v, &format!("http {base}")))
}

fn sidecar_script() -> Option<PathBuf> {
    if let Some(root) = root_configured() {
        let p = root
            .join("workbench")
            .join("scripts")
            .join("run_packing_sidecar.py");
        if p.is_file() {
            return Some(p);
        }
    }
    if let Ok(m) = env::var("CARGO_MANIFEST_DIR") {
        let p = Path::new(&m).join("scripts").join("run_packing_sidecar.py");
        if p.is_file() {
            return Some(p);
        }
    }
    None
}

fn python_sidecar(root: &Path, materials: &str, notes: &str) -> Result<PackingSummary, String> {
    let script = sidecar_script().ok_or_else(|| "找不到 workbench/scripts/run_packing_sidecar.py".to_string())?;
    let mut child = Command::new("python")
        .arg(&script)
        .env("PACKING_AGENT_ROOT", root)
        .env("PACKING_SKIP_SKJOLBER", "1")
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .map_err(|e| format!("启动 python: {e}"))?;
    let payload = json!({"user_input": format!("{materials}\n{notes}")});
    if let Some(stdin) = child.stdin.as_mut() {
        use std::io::Write;
        stdin
            .write_all(payload.to_string().as_bytes())
            .map_err(|e| e.to_string())?;
    }
    let out = child.wait_with_output().map_err(|e| e.to_string())?;
    if !out.status.success() {
        return Err(format!(
            "exit {:?} stderr={}",
            out.status.code(),
            String::from_utf8_lossy(&out.stderr).chars().take(400).collect::<String>()
        ));
    }
    let v: Value = serde_json::from_slice(&out.stdout).map_err(|e| {
        format!(
            "sidecar JSON: {e} stdout={}",
            String::from_utf8_lossy(&out.stdout).chars().take(200).collect::<String>()
        )
    })?;
    if v.get("error").and_then(|x| x.as_str()).is_some() && v.get("ok") != Some(&json!(true)) {
        return Err(v["error"].as_str().unwrap_or("sidecar error").into());
    }
    Ok(summarize_pipeline_json(
        &v,
        &format!("python {}", root.display()),
    ))
}
