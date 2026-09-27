use super::{
    agent,
    engineering::{EngineeringHost, EngineeringKind, EngineeringSelection},
    worker::WorkerHost,
};
use crate::{
    config::Paths,
    runtime_core::{CancellationToken, RuntimeCore, SessionId, TurnId, WorkspaceContext},
};
use axum::{
    extract::{DefaultBodyLimit, Path, Query, State},
    http::{header, StatusCode},
    response::IntoResponse,
    routing::{get, post},
    Json, Router,
};
use rusqlite::{params, Connection};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::{
    path::PathBuf,
    sync::{Arc, Mutex},
};

pub struct ProductState {
    pub paths: Paths,
    pub runtime: RuntimeCore,
    pub worker: WorkerHost,
    pub auth: Arc<super::auth::InstanceAuth>,
    index: Mutex<Connection>,
    _owner: std::fs::File,
}
type HttpError = (StatusCode, Json<Value>);
fn error(code: StatusCode, message: impl ToString) -> HttpError {
    (code, Json(json!({"detail":message.to_string()})))
}
fn bad(message: impl ToString) -> HttpError {
    error(StatusCode::BAD_REQUEST, message)
}

impl ProductState {
    pub fn open(paths: Paths) -> Result<Arc<Self>, String> {
        Self::open_with_auth(paths, super::auth::InstanceAuth::from_env()?)
    }
    pub fn open_with_auth(paths: Paths, auth: Arc<super::auth::InstanceAuth>) -> Result<Arc<Self>, String> {
        let folder = std::env::var_os("CIVIL_STATE_ROOT")
            .map(PathBuf::from)
            .unwrap_or_else(|| paths.data_dir.join("unified"));
        std::fs::create_dir_all(&folder).map_err(|e| e.to_string())?;
        let owner = std::fs::OpenOptions::new()
            .create(true)
            .truncate(false)
            .read(true)
            .write(true)
            .open(folder.join("runtime-owner.lock"))
            .map_err(|e| e.to_string())?;
        owner
            .try_lock()
            .map_err(|_| "this state directory already has a running Rust host".to_string())?;
        auth.claim_state(&folder)?;
        let runtime =
            RuntimeCore::open(folder.join("runtime.sqlite")).map_err(|e| e.to_string())?;
        runtime.recover_interrupted().map_err(|e| e.to_string())?;
        let index = Connection::open(folder.join("index.sqlite")).map_err(|e| e.to_string())?;
        index.execute_batch("PRAGMA journal_mode=WAL; CREATE TABLE IF NOT EXISTS workspaces(id TEXT PRIMARY KEY,root TEXT UNIQUE NOT NULL); CREATE TABLE IF NOT EXISTS artifacts(id TEXT PRIMARY KEY,workspace TEXT NOT NULL,path TEXT NOT NULL,record TEXT NOT NULL);").map_err(|e|e.to_string())?;
        Ok(Arc::new(Self {
            worker: WorkerHost::detect(paths.repo_root.clone()),
            paths,
            runtime,
            index: Mutex::new(index),
            auth,
            _owner: owner,
        }))
    }
    pub fn register(&self, path: &str) -> Result<Value, String> {
        self.auth.authorize_workspace(std::path::Path::new(path))?;
        let ws = WorkspaceContext::new(path).map_err(|e| e.to_string())?;
        let db = self.index.lock().map_err(|_| "index lock failed")?;
        let id = uuid::Uuid::new_v4().simple().to_string();
        let root = ws.root().to_string_lossy().into_owned();
        db.execute(
            "INSERT OR IGNORE INTO workspaces(id,root) VALUES(?1,?2)",
            params![id, root],
        )
        .map_err(|e| e.to_string())?;
        let id: String = db
            .query_row("SELECT id FROM workspaces WHERE root=?1", [&root], |r| {
                r.get(0)
            })
            .map_err(|e| e.to_string())?;
        Ok(json!({"id":id,"root":root}))
    }
    pub fn workspace(&self, id: &str) -> Result<WorkspaceContext, String> {
        let db = self.index.lock().map_err(|_| "index lock failed")?;
        let root: String = db
            .query_row("SELECT root FROM workspaces WHERE id=?1", [id], |r| {
                r.get(0)
            })
            .map_err(|_| "unknown workspace")?;
        self.auth.authorize_workspace(std::path::Path::new(&root))?;
        WorkspaceContext::new(root).map_err(|e| e.to_string())
    }
    pub fn register_artifact(&self, workspace_id: &str, result: &Value) -> Result<Value, String> {
        let ws = self.workspace(workspace_id)?;
        let path = PathBuf::from(
            result["output_path"]
                .as_str()
                .ok_or("missing output path")?,
        );
        let relative = path
            .strip_prefix(ws.root())
            .map_err(|_| "output outside workspace")?;
        let verified = ws.resolve_read(relative).map_err(|e| e.to_string())?;
        if !verified.starts_with(ws.output_root()) {
            return Err("output outside publication directory".into());
        }
        let id = uuid::Uuid::new_v4().simple().to_string();
        let record = json!({"id":id,"actor_id":self.auth.owner(),"name":path.file_name().unwrap_or_default().to_string_lossy(),
            "url":format!("/api/agent/artifacts/{id}?workspace={workspace_id}"),
            "source":result["source"],"source_sha256":result["source_sha256"],"output_sha256":result["output_sha256"],
            "validation":result["validation"],"provenance":"model_proposed","evidence_validation":result["evidence_validation"]});
        self.index
            .lock()
            .map_err(|_| "index lock failed")?
            .execute(
                "INSERT INTO artifacts(id,workspace,path,record) VALUES(?1,?2,?3,?4)",
                params![
                    id,
                    workspace_id,
                    relative.to_string_lossy(),
                    record.to_string()
                ],
            )
            .map_err(|e| e.to_string())?;
        Ok(record)
    }
}

pub fn router(state: Arc<ProductState>) -> Router {
    Router::new()
        .route("/api/agent/capabilities", get(capabilities))
        .route("/api/agent/workspaces", get(workspaces).post(register))
        .route("/api/agent/files", get(files))
        .route("/api/agent/engineering/projects", get(engineering_projects))
        .route(
            "/api/agent/engineering/projects/{kind}/{id}",
            get(engineering_project),
        )
        .route("/api/agent/turns", post(start).get(turns))
        .route("/api/agent/turns/{turn}", get(turn))
        .route("/api/agent/turns/{turn}/events", get(events))
        .route("/api/agent/turns/{turn}/cancel", post(cancel))
        .route("/api/agent/artifacts/{id}", get(artifact))
        .layer(DefaultBodyLimit::max(256 * 1024))
        .with_state(state)
}

async fn capabilities(State(st): State<Arc<ProductState>>) -> Json<Value> {
    let cfg = crate::config::llm_config();
    Json(
        json!({"available":true,"models":{"configured":!cfg.api_key.is_empty(),"model":cfg.model},
        "modes":["steps","model"],"sandbox":["read-only","workspace-write"],
        "sandbox_controls":{"policy":true,"os_enforced":null,"network_confined":null,"reads_confined":null,"probe":"workspace_required"},
        "features":{"context":true,"subagents":true,"cancel":true,"documents":true,"retrieval":true,"voice":false},
        "identity":st.auth.capabilities(),"unavailability_reason":null}),
    )
}
#[derive(Deserialize)]
struct Register {
    path: String,
}
async fn register(
    State(st): State<Arc<ProductState>>,
    Json(req): Json<Register>,
) -> Result<Json<Value>, HttpError> {
    let workspace = st.register(&req.path).map_err(bad)?;
    let ws = st
        .workspace(workspace["id"].as_str().unwrap_or(""))
        .map_err(bad)?;
    let mut caps = capabilities(State(st.clone())).await.0;
    match st
        .worker
        .capabilities(&ws, &crate::runtime_core::CancellationToken::new())
        .await
    {
        Ok(report) => {
            caps["worker"] = report.clone();
            caps["sandbox_controls"] = json!({"policy":true,"os_enforced":report["sandbox"]["enforces"]["write"],"network_confined":report["sandbox"]["enforces"]["network"],"reads_confined":report["sandbox"]["enforces"]["read"],"backend":report["sandbox"]["backend"],"limitations":report["sandbox"]["limitations"]});
            if report["ok"] != true {
                caps["features"]["documents"] = json!(false);
                caps["features"]["retrieval"] = json!(false);
                caps["unavailability_reason"] = report["error"].clone();
            }
        }
        Err(reason) => {
            caps["features"]["documents"] = json!(false);
            caps["features"]["retrieval"] = json!(false);
            caps["unavailability_reason"] = json!(reason);
        }
    }
    Ok(Json(json!({"workspace":workspace,"capabilities":caps})))
}
async fn workspaces(State(st): State<Arc<ProductState>>) -> Result<Json<Value>, HttpError> {
    let db = st.index.lock().map_err(|_| bad("index lock failed"))?;
    let mut statement = db
        .prepare("SELECT id,root FROM workspaces ORDER BY rowid DESC")
        .map_err(bad)?;
    let rows = statement
        .query_map([], |r| {
            Ok(json!({"id":r.get::<_,String>(0)?,"root":r.get::<_,String>(1)?}))
        })
        .map_err(bad)?;
    let workspaces: Vec<Value> = rows.collect::<Result<Vec<_>, _>>().map_err(bad)?.into_iter()
        .filter(|row| row["root"].as_str().is_some_and(|root| st.auth.authorize_workspace(std::path::Path::new(root)).is_ok())).collect();
    Ok(Json(json!({"workspaces":workspaces})))
}
#[derive(Deserialize)]
pub struct Scope {
    pub workspace: String,
    #[serde(default)]
    pub session_id: Option<String>,
    #[serde(default)]
    pub after_seq: Option<u64>,
}
impl Scope {
    fn session(&self) -> Result<SessionId, HttpError> {
        SessionId::parse(
            self.session_id
                .as_deref()
                .ok_or_else(|| bad("session_id required"))?,
        )
        .map_err(bad)
    }
}
async fn files(
    State(st): State<Arc<ProductState>>,
    Query(q): Query<Scope>,
) -> Result<Json<Value>, HttpError> {
    let ws = st.workspace(&q.workspace).map_err(bad)?;
    Ok(Json(
        json!({"workspace":q.workspace,"files":super::tools::list_files(&ws).map_err(bad)?}),
    ))
}

async fn engineering_projects(
    State(st): State<Arc<ProductState>>,
    Query(q): Query<Scope>,
) -> Result<Json<Value>, HttpError> {
    st.workspace(&q.workspace).map_err(bad)?;
    let host = EngineeringHost::from_env(st.worker.clone())
        .map_err(|e| error(StatusCode::SERVICE_UNAVAILABLE, e))?;
    host.list_projects(&CancellationToken::new())
        .await
        .map(Json)
        .map_err(|e| error(StatusCode::BAD_GATEWAY, e))
}

async fn engineering_project(
    State(st): State<Arc<ProductState>>,
    Path((kind, id)): Path<(String, String)>,
    Query(q): Query<Scope>,
) -> Result<Json<Value>, HttpError> {
    st.workspace(&q.workspace).map_err(bad)?;
    let kind = EngineeringKind::parse(&kind).map_err(bad)?;
    if id.len() != 32
        || !id
            .bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
    {
        return Err(bad("工程项目ID无效"));
    }
    let host = EngineeringHost::from_env(st.worker.clone())
        .map_err(|e| error(StatusCode::SERVICE_UNAVAILABLE, e))?;
    let inspected = host
        .inspect_project(kind, &id, &CancellationToken::new())
        .await
        .map_err(|e| error(StatusCode::BAD_GATEWAY, e))?;
    Ok(Json(serde_json::to_value(inspected).map_err(bad)?))
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct TurnRequest {
    pub workspace: String,
    pub session_id: String,
    pub message: String,
    #[serde(default = "default_locale")]
    pub locale: String,
    #[serde(default = "default_mode")]
    pub mode: String,
    #[serde(default = "default_sandbox")]
    pub sandbox: String,
    #[serde(default)]
    pub files: Vec<String>,
    #[serde(default)]
    pub engineering: Vec<EngineeringSelection>,
    #[serde(default)]
    pub expert_id: String,
    #[serde(default)]
    pub risk_confirmation: String,
}
fn default_locale() -> String {
    "zh-CN".into()
}
fn default_mode() -> String {
    "model".into()
}
fn default_sandbox() -> String {
    "read-only".into()
}
async fn start(
    State(st): State<Arc<ProductState>>,
    Json(req): Json<TurnRequest>,
) -> Result<(StatusCode, Json<Value>), HttpError> {
    if req.message.trim().is_empty()
        || req.message.len() > 48_000
        || req.files.len() > 24
        || req.engineering.len() > 4
        || req.risk_confirmation.chars().count() > 80
        || !matches!(req.mode.as_str(), "model" | "steps")
        || !matches!(req.locale.as_str(), "zh-CN" | "en")
        || !matches!(req.sandbox.as_str(), "read-only" | "workspace-write")
    {
        return Err(bad(
            "invalid message, mode, sandbox, file or engineering selection",
        ));
    }
    if !req.expert_id.is_empty()
        && !crate::catalog::seed()
            .experts
            .iter()
            .any(|expert| expert.id == req.expert_id)
    {
        return Err(bad("unknown expert_id"));
    }
    for selection in &req.engineering {
        selection.validate().map_err(bad)?;
    }
    let ws = st.workspace(&req.workspace).map_err(bad)?;
    let session = SessionId::parse(&req.session_id).map_err(bad)?;
    for file in &req.files {
        ws.resolve_read(file).map_err(bad)?;
    }
    if req.mode == "model" && crate::config::llm_config().api_key.is_empty() {
        return Err(bad("请先配置模型 API Key，或使用资料检查模式"));
    }
    let mut stored_request = serde_json::to_value(&req).map_err(bad)?;
    stored_request["actor_id"] = json!(st.auth.owner());
    stored_request["identity_mode"] = st.auth.capabilities()["mode"].clone();
    stored_request["risk_confirmation_present"] = json!(agent::current_turn_confirmation(&req));
    let lease = st
        .runtime
        .begin_turn(&ws, &session, stored_request)
        .map_err(|e| {
            if matches!(e, crate::runtime_core::RuntimeError::SessionBusy) {
                error(StatusCode::CONFLICT, e)
            } else {
                bad(e)
            }
        })?;
    lease.emit("authorization", json!({"actor_id":st.auth.owner(),"sandbox":req.sandbox,
        "risk_confirmation_present":agent::current_turn_confirmation(&req),"confirmation_scope":"current_turn",
        "professional_signoff":false})).map_err(bad)?;
    let response = json!({"turn_id":lease.turn_id(),"session_id":session});
    tokio::spawn(agent::run(st, ws, req, lease));
    Ok((StatusCode::ACCEPTED, Json(response)))
}
async fn turns(
    State(st): State<Arc<ProductState>>,
    Query(q): Query<Scope>,
) -> Result<Json<Value>, HttpError> {
    let ws = st.workspace(&q.workspace).map_err(bad)?;
    let session = q
        .session_id
        .as_deref()
        .map(SessionId::parse)
        .transpose()
        .map_err(bad)?;
    Ok(Json(
        json!({"turns":st.runtime.list_turns(&ws,session.as_ref(),100).map_err(bad)?}),
    ))
}
async fn turn(
    State(st): State<Arc<ProductState>>,
    Path(id): Path<String>,
    Query(q): Query<Scope>,
) -> Result<Json<Value>, HttpError> {
    let ws = st.workspace(&q.workspace).map_err(bad)?;
    Ok(Json(
        json!({"turn":st.runtime.turn(&ws,&q.session()?,&TurnId::parse(id).map_err(bad)?).map_err(bad)?}),
    ))
}
async fn events(
    State(st): State<Arc<ProductState>>,
    Path(id): Path<String>,
    Query(q): Query<Scope>,
) -> Result<Json<Value>, HttpError> {
    let ws = st.workspace(&q.workspace).map_err(bad)?;
    let session = q.session()?;
    let id = TurnId::parse(id).map_err(bad)?;
    let events = st
        .runtime
        .replay(&ws, &session, &id, q.after_seq.unwrap_or(0), 200)
        .map_err(bad)?;
    // Keep the external event vocabulary stable even if storage uses `event`.
    let events: Vec<Value> = events
        .into_iter()
        .map(|e| {
            let mut v = serde_json::to_value(e).unwrap_or(Value::Null);
            if v.get("kind").is_none() {
                v["kind"] = v["event"].clone();
            }
            v
        })
        .collect();
    Ok(Json(
        json!({"events":events,"turn":st.runtime.turn(&ws,&session,&id).map_err(bad)?}),
    ))
}
async fn cancel(
    State(st): State<Arc<ProductState>>,
    Path(id): Path<String>,
    Query(q): Query<Scope>,
) -> Result<Json<Value>, HttpError> {
    let ws = st.workspace(&q.workspace).map_err(bad)?;
    st.runtime
        .cancel_turn(&ws, &q.session()?, &TurnId::parse(id).map_err(bad)?)
        .map_err(bad)?;
    Ok(Json(json!({"ok":true})))
}
async fn artifact(
    State(st): State<Arc<ProductState>>,
    Path(id): Path<String>,
    Query(q): Query<Scope>,
) -> Result<axum::response::Response, HttpError> {
    let ws = st.workspace(&q.workspace).map_err(bad)?;
    let (relative, record): (String, String) = st
        .index
        .lock()
        .map_err(|_| bad("index lock failed"))?
        .query_row(
            "SELECT path,record FROM artifacts WHERE id=?1 AND workspace=?2",
            params![id, q.workspace],
            |r| Ok((r.get(0)?, r.get(1)?)),
        )
        .map_err(|_| error(StatusCode::NOT_FOUND, "artifact not found"))?;
    let path = ws.resolve_read(relative).map_err(bad)?;
    if !path.starts_with(ws.output_root()) {
        return Err(bad("invalid artifact path"));
    }
    let bytes = tokio::fs::read(&path).await.map_err(bad)?;
    let record: Value = serde_json::from_str(&record).map_err(bad)?;
    if super::tools::sha256(&bytes) != record["output_sha256"].as_str().unwrap_or("") {
        return Err(error(
            StatusCode::CONFLICT,
            "artifact changed after validation",
        ));
    }
    Ok((
        [
            (header::CONTENT_TYPE, "application/octet-stream"),
            (header::CONTENT_DISPOSITION, "attachment"),
            (header::X_CONTENT_TYPE_OPTIONS, "nosniff"),
        ],
        bytes,
    )
        .into_response())
}
