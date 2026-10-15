//! Explicit local-desktop Excel jobs. This channel is never a model tool and is
//! separate from the ordinary process-confined document workers.
use super::{
    api::{ProductState, Scope},
    project_control,
    tools::sha256,
};
use crate::runtime_core::{CancellationToken, WorkspaceContext};
use axum::{
    extract::{DefaultBodyLimit, Path, Query, State},
    http::StatusCode,
    routing::{get, post},
    Json, Router,
};
use rusqlite::{params, Connection, OptionalExtension};
use serde::Deserialize;
use serde_json::{json, Value};
use std::{
    collections::HashMap,
    io::Write,
    path::{Path as FilePath, PathBuf},
    process::Stdio,
    sync::{Arc, Mutex},
    time::Duration,
};
use tokio::io::{AsyncBufReadExt, AsyncReadExt, AsyncWriteExt, BufReader};

const ACK: &str = "我明白，将由持证人员签认";
const PROFILE: &str = "civil-xlsx-engine-v1";
const MAX_BYTES: u64 = 16 * 1024 * 1024;
const MAX_PROTOCOL: u64 = 12 * 1024 * 1024;
const JOB_TIMEOUT: u64 = 180;
const BROKER: &[u8] = include_bytes!("../../../scripts/excel-desktop-broker.ps1");
type Error = (StatusCode, Json<Value>);

fn error(status: StatusCode, code: &str, message: impl ToString) -> Error {
    (
        status,
        Json(json!({"code":code,"detail":message.to_string()})),
    )
}
fn bad(code: &str, message: impl ToString) -> Error {
    error(StatusCode::BAD_REQUEST, code, message)
}
fn now() -> String {
    chrono::Utc::now().to_rfc3339()
}
fn hash(value: &str) -> bool {
    value.len() == 64
        && value
            .bytes()
            .all(|c| c.is_ascii_hexdigit() && !c.is_ascii_uppercase())
}
fn terminal(job: &Value) -> bool {
    matches!(
        job["status"].as_str(),
        Some("completed" | "failed" | "cancelled" | "interrupted")
    )
}

pub struct ExcelController {
    enabled: bool,
    local: bool,
    script: PathBuf,
    serial: tokio::sync::Mutex<()>,
    active: Mutex<HashMap<String, CancellationToken>>,
}
impl ExcelController {
    pub fn detect(repo: &FilePath, auth: &super::auth::InstanceAuth) -> Self {
        Self {
            enabled: std::env::var("CIVIL_EXCEL_ENGINE").ok().as_deref() == Some("windows-desktop"),
            local: !auth.public_origin_configured(),
            script: repo.join("scripts/excel-desktop-broker.ps1"),
            serial: tokio::sync::Mutex::new(()),
            active: Mutex::new(HashMap::new()),
        }
    }
    fn reason(&self) -> Option<&'static str> {
        if !self.enabled {
            Some("engine_not_enabled")
        } else if !self.local {
            Some("local_desktop_only")
        } else if !cfg!(windows) {
            Some("windows_required")
        } else if checked_script(&self.script).is_err() {
            Some("broker_missing_or_changed")
        } else {
            None
        }
    }
    pub fn capabilities(&self) -> Value {
        let reason = self.reason();
        json!({"enabled":self.enabled,"available":reason.is_none(),"reason":reason,
            "mode":if self.enabled{Some("windows-desktop")}else{None},
            "probe":"configuration_and_fixed_broker_only","engine_verified":false,
            "scope":"local_desktop_explicit_action","automatic_acceptance":false})
    }
}

pub fn initialize(db: &Connection) -> Result<(), String> {
    db.execute_batch("CREATE TABLE IF NOT EXISTS excel_jobs(id TEXT PRIMARY KEY,workspace TEXT NOT NULL,actor TEXT NOT NULL,client_key TEXT NOT NULL,request_sha256 TEXT NOT NULL,record TEXT NOT NULL,UNIQUE(workspace,actor,client_key));
        CREATE INDEX IF NOT EXISTS excel_jobs_workspace ON excel_jobs(workspace);
        UPDATE excel_jobs SET record=json_set(record,'$.status','interrupted','$.updated_at',strftime('%Y-%m-%dT%H:%M:%fZ','now'),'$.error',json('{\"code\":\"interrupted\",\"message\":\"Host stopped before this calculation finished; no successful delivery is inferred\"}')) WHERE json_extract(record,'$.status') IN ('queued','running');")
        .map_err(|e|e.to_string())
}
pub fn router(state: Arc<ProductState>) -> Router {
    Router::new()
        .route("/api/agent/artifacts/{id}/excel-recalculate", post(start))
        .route("/api/agent/artifacts/{id}/calculations", get(parent_jobs))
        .route("/api/agent/excel-jobs", get(jobs))
        .route("/api/agent/excel-jobs/{id}", get(job))
        .route("/api/agent/excel-jobs/{id}/cancel", post(cancel))
        .layer(DefaultBodyLimit::max(4096))
        .with_state(state)
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Request {
    expected_sha256: String,
    idempotency_key: String,
    sandbox: String,
    risk_confirmation: String,
}
fn request_digest(parent: &str, req: &Request) -> String {
    sha256(
        format!(
            "excel-recalculate-v1\n{parent}\n{}\n{}\n{}",
            req.expected_sha256, req.sandbox, req.risk_confirmation
        )
        .as_bytes(),
    )
}
fn load(st: &ProductState, workspace: &str, id: &str) -> Result<Value, Error> {
    st.workspace(workspace)
        .map_err(|e| bad("workspace_denied", e))?;
    let db = st
        .index
        .lock()
        .map_err(|_| bad("storage_failed", "Index lock failed"))?;
    let raw: String = db
        .query_row(
            "SELECT record FROM excel_jobs WHERE id=?1 AND workspace=?2 AND actor=?3",
            params![id, workspace, st.auth.owner()],
            |row| row.get(0),
        )
        .map_err(|_| {
            error(
                StatusCode::NOT_FOUND,
                "job_not_found",
                "Excel job not found",
            )
        })?;
    serde_json::from_str(&raw).map_err(|_| bad("storage_failed", "Invalid saved Excel job"))
}
fn update(
    st: &ProductState,
    workspace: &str,
    id: &str,
    change: impl FnOnce(&mut Value),
) -> Result<Value, String> {
    let mut db = st.index.lock().map_err(|_| "Index lock failed")?;
    let tx = db.transaction().map_err(|e| e.to_string())?;
    let raw: String = tx
        .query_row(
            "SELECT record FROM excel_jobs WHERE id=?1 AND workspace=?2 AND actor=?3",
            params![id, workspace, st.auth.owner()],
            |row| row.get(0),
        )
        .map_err(|e| e.to_string())?;
    let mut result: Value = serde_json::from_str(&raw).map_err(|e| e.to_string())?;
    change(&mut result);
    result["updated_at"] = json!(now());
    tx.execute(
        "UPDATE excel_jobs SET record=?1 WHERE id=?2 AND workspace=?3",
        params![result.to_string(), id, workspace],
    )
    .map_err(|e| e.to_string())?;
    tx.commit().map_err(|e| e.to_string())?;
    Ok(result)
}
async fn start(
    State(st): State<Arc<ProductState>>,
    Path(parent): Path<String>,
    Query(scope): Query<Scope>,
    Json(req): Json<Request>,
) -> Result<(StatusCode, Json<Value>), Error> {
    st.workspace(&scope.workspace)
        .map_err(|e| bad("workspace_denied", e))?;
    if req.sandbox != "workspace-write" || req.risk_confirmation != ACK {
        return Err(error(StatusCode::FORBIDDEN,"permission_denied","Excel creates a new copy; choose workspace-write and enter the exact current-operation confirmation"));
    }
    if !hash(&req.expected_sha256)
        || uuid::Uuid::parse_str(&req.idempotency_key).is_err()
        || req.idempotency_key.len() > 36
    {
        return Err(bad(
            "invalid_request",
            "A lowercase SHA-256 and UUID idempotency_key are required",
        ));
    }
    let key = uuid::Uuid::parse_str(&req.idempotency_key)
        .map_err(|_| bad("invalid_request", "Invalid UUID"))?
        .simple()
        .to_string();
    let digest = request_digest(&parent, &req);
    // Replays authorize the same scoped request but do not restart a completed,
    // failed or interrupted task. Changed delivered bytes are never redelivered.
    {
        let db = st
            .index
            .lock()
            .map_err(|_| bad("storage_failed", "Index lock failed"))?;
        let old:Option<(String,String)>=db.query_row("SELECT request_sha256,record FROM excel_jobs WHERE workspace=?1 AND actor=?2 AND client_key=?3",
            params![scope.workspace,st.auth.owner(),key],|r|Ok((r.get(0)?,r.get(1)?))).optional().map_err(|_|bad("storage_failed","Cannot query Excel jobs"))?;
        if let Some((expected, raw)) = old {
            if expected != digest {
                return Err(error(
                    StatusCode::CONFLICT,
                    "idempotency_conflict",
                    "This key was used for another calculation request",
                ));
            }
            let old: Value = serde_json::from_str(&raw)
                .map_err(|_| bad("storage_failed", "Invalid saved Excel job"))?;
            drop(db);
            check_delivered(&st, &scope.workspace, &old)?;
            return Ok((
                StatusCode::ACCEPTED,
                Json(json!({"job":old,"replayed":true})),
            ));
        }
    }
    let (ws, path, record) = project_control::artifact_info(&st, &scope.workspace, &parent)?;
    if record["output_sha256"] != req.expected_sha256 {
        return Err(error(
            StatusCode::CONFLICT,
            "source_conflict",
            "Expected artifact hash is stale",
        ));
    }
    if !path.to_ascii_lowercase().ends_with(".xlsx") {
        return Err(bad(
            "unsupported",
            "Only registered XLSX artifacts can be recalculated",
        ));
    }
    if project_control::artifact_source_status(&st, &ws, &record) != "current" {
        return Err(error(
            StatusCode::CONFLICT,
            "source_conflict",
            "Original source changed or cannot be verified; generate a current copy first",
        ));
    }
    if let Some(reason) = st.excel.reason() {
        return Err(error(StatusCode::FORBIDDEN, "engine_disabled", reason));
    }
    let id = uuid::Uuid::new_v4().simple().to_string();
    let job = json!({"id":id,"workspace":scope.workspace,"actor_id":st.auth.owner(),"parent_artifact_id":parent,
        "input_sha256":req.expected_sha256,"status":"queued","stage":"queued","created_at":now(),"updated_at":now(),
        "sandbox":"workspace-write","confirmation_scope":"current_operation","risk_confirmation_present":true,
        "artifact":null,"receipt":null,"error":null});
    {
        let mut db = st
            .index
            .lock()
            .map_err(|_| bad("storage_failed", "Index lock failed"))?;
        let tx = db
            .transaction()
            .map_err(|_| bad("storage_failed", "Cannot begin Excel job"))?;
        let count:i64=tx.query_row("SELECT count(*) FROM excel_jobs WHERE json_extract(record,'$.status') IN ('queued','running')",[],|r|r.get(0)).map_err(|_|bad("storage_failed","Cannot count Excel jobs"))?;
        if count >= 16 {
            return Err(error(
                StatusCode::TOO_MANY_REQUESTS,
                "busy",
                "Too many pending Excel jobs",
            ));
        }
        let inserted=tx.execute("INSERT OR IGNORE INTO excel_jobs(id,workspace,actor,client_key,request_sha256,record) VALUES(?1,?2,?3,?4,?5,?6)",
            params![id,scope.workspace,st.auth.owner(),key,digest,job.to_string()]).map_err(|_|bad("storage_failed","Cannot save Excel job"))?;
        if inserted == 0 {
            return Err(error(
                StatusCode::CONFLICT,
                "request_busy",
                "Concurrent retry; query the existing job",
            ));
        }
        tx.commit()
            .map_err(|_| bad("storage_failed", "Cannot commit Excel job"))?;
    }
    let token = CancellationToken::new();
    st.excel
        .active
        .lock()
        .map_err(|_| bad("storage_failed", "Job lock failed"))?
        .insert(id.clone(), token.clone());
    let state = st.clone();
    let workspace = scope.workspace.clone();
    tokio::spawn(async move {
        let result = execute(
            &state,
            &workspace,
            &id,
            &parent,
            &req.expected_sha256,
            &token,
        )
        .await;
        if let Err((code, message)) = result {
            let cancelled = token.is_cancelled() && code != "engine_cleanup_unverified";
            let _ = update(&state, &workspace, &id, |job| {
                if !terminal(job) {
                    job["status"] = json!(if cancelled { "cancelled" } else { "failed" });
                    job["error"] =
                        json!({"code":if cancelled{"cancelled"}else{code},"message":message});
                }
            });
        }
        if let Ok(mut active) = state.excel.active.lock() {
            active.remove(&id);
        }
    });
    Ok((StatusCode::ACCEPTED, Json(json!({"job":job}))))
}

fn check_delivered(st: &ProductState, workspace: &str, job: &Value) -> Result<(), Error> {
    if job["status"] == "completed" {
        let id = job["artifact"]["id"]
            .as_str()
            .ok_or_else(|| bad("storage_failed", "Completed job has no artifact"))?;
        let (_, _, record) = project_control::artifact_info(st, workspace, id)?;
        if record["output_sha256"] != job["artifact"]["output_sha256"] {
            return Err(error(
                StatusCode::CONFLICT,
                "artifact_changed",
                "Excel output changed after registration",
            ));
        }
    }
    Ok(())
}
fn list(st: &ProductState, workspace: &str, parent: Option<&str>) -> Result<Json<Value>, Error> {
    st.workspace(workspace)
        .map_err(|e| bad("workspace_denied", e))?;
    let db = st
        .index
        .lock()
        .map_err(|_| bad("storage_failed", "Index lock failed"))?;
    let mut statement=db.prepare("SELECT record FROM excel_jobs WHERE workspace=?1 AND actor=?2 AND (?3 IS NULL OR json_extract(record,'$.parent_artifact_id')=?3) ORDER BY rowid DESC LIMIT 101")
        .map_err(|_|bad("storage_failed","Cannot query Excel jobs"))?;
    let rows = statement
        .query_map(params![workspace, st.auth.owner(), parent], |r| {
            r.get::<_, String>(0)
        })
        .map_err(|_| bad("storage_failed", "Cannot read Excel jobs"))?;
    let mut jobs = Vec::new();
    for row in rows {
        jobs.push(
            serde_json::from_str::<Value>(
                &row.map_err(|_| bad("storage_failed", "Cannot read Excel job"))?,
            )
            .map_err(|_| bad("storage_failed", "Invalid saved Excel job"))?,
        );
    }
    let truncated = jobs.len() > 100;
    jobs.truncate(100);
    // The status/history is retained even if a later external edit invalidates
    // download. Readiness and downloads recheck current registered bytes.
    Ok(Json(json!({"jobs":jobs,"limit":100,"truncated":truncated})))
}
async fn jobs(
    State(st): State<Arc<ProductState>>,
    Query(scope): Query<Scope>,
) -> Result<Json<Value>, Error> {
    list(&st, &scope.workspace, None)
}
async fn parent_jobs(
    State(st): State<Arc<ProductState>>,
    Path(parent): Path<String>,
    Query(scope): Query<Scope>,
) -> Result<Json<Value>, Error> {
    project_control::artifact_info(&st, &scope.workspace, &parent)?;
    list(&st, &scope.workspace, Some(&parent))
}
async fn job(
    State(st): State<Arc<ProductState>>,
    Path(id): Path<String>,
    Query(scope): Query<Scope>,
) -> Result<Json<Value>, Error> {
    Ok(Json(json!({"job":load(&st,&scope.workspace,&id)?})))
}
async fn cancel(
    State(st): State<Arc<ProductState>>,
    Path(id): Path<String>,
    Query(scope): Query<Scope>,
) -> Result<Json<Value>, Error> {
    let old = load(&st, &scope.workspace, &id)?;
    if !terminal(&old) {
        if let Some(token) = st
            .excel
            .active
            .lock()
            .map_err(|_| bad("storage_failed", "Job lock failed"))?
            .get(&id)
        {
            token.cancel();
        }
        // A running task only becomes cancelled after its broker is stopped.
        let value = update(&st, &scope.workspace, &id, |job| {
            if !terminal(job) {
                job["cancellation_requested"] = json!(true);
            }
        })
        .map_err(|e| bad("storage_failed", e))?;
        return Ok(Json(json!({"job":value})));
    }
    Ok(Json(json!({"job":old})))
}

type Failure = (&'static str, String);
fn failure(code: &'static str, reason: impl ToString) -> Failure {
    (code, reason.to_string())
}
fn cancelled(token: &CancellationToken) -> Result<(), Failure> {
    token
        .check()
        .map_err(|_| failure("cancelled", "Calculation cancelled"))
}
fn worker_result(response: Value) -> Result<Value, Failure> {
    if response["ok"] != true {
        return Err(failure(
            "admission_rejected",
            response["error"]["message"]
                .as_str()
                .unwrap_or("Document input rejected"),
        ));
    }
    Ok(response["result"].clone())
}
async fn execute(
    st: &Arc<ProductState>,
    workspace: &str,
    id: &str,
    parent: &str,
    input_hash: &str,
    token: &CancellationToken,
) -> Result<(), Failure> {
    let _serial = tokio::select! { lock=st.excel.serial.lock()=>lock, _=super::providers::cancelled(token)=>return Err(failure("cancelled","Queued calculation cancelled")) };
    cancelled(token)?;
    let phase = |stage: &str| {
        update(st, workspace, id, |job| {
            job["status"] = json!("running");
            job["stage"] = json!(stage);
        })
        .map_err(|e| failure("storage_failed", e))
    };
    phase("admission")?;
    let (ws, path, parent_record) =
        project_control::artifact_info(st, workspace, parent).map_err(|_| {
            failure(
                "source_conflict",
                "Registered artifact no longer matches its hash",
            )
        })?;
    if parent_record["output_sha256"] != input_hash
        || project_control::artifact_source_status(st, &ws, &parent_record) != "current"
    {
        return Err(failure(
            "source_conflict",
            "Input artifact or original source changed before calculation",
        ));
    }
    let source = ws
        .resolve_read(&path)
        .map_err(|e| failure("source_conflict", e))?;
    let before = bounded_bytes(&source).map_err(|e| failure("source_conflict", e))?;
    if sha256(&before) != input_hash {
        return Err(failure(
            "source_conflict",
            "Artifact changed before admission",
        ));
    }
    let admitted = worker_result(
        st.worker
            .document(
                &ws,
                "inspect_excel_engine_input",
                &path,
                Some(input_hash),
                json!({}),
                token,
            )
            .await
            .map_err(|e| failure("admission_rejected", e))?,
    )?;
    let admission = admitted.get("admission").unwrap_or(&admitted);
    if admitted["source_sha256"] != input_hash
        || admission["eligible"] != true
        || admission["policy_version"] != PROFILE
        || !admission["formula_cells"].is_array()
        || admission["formula_count"].as_u64()
            != admission["formula_cells"]
                .as_array()
                .map(|v| v.len() as u64)
        || admission["sheet_count"].as_u64().is_none()
    {
        return Err(failure(
            "admission_rejected",
            "Admission result is not complete or bound to this artifact",
        ));
    }
    cancelled(token)?;
    let folder = ws
        .resolve_write(format!(".tmp/excel/{id}"))
        .map_err(|e| failure("source_conflict", e))?;
    std::fs::create_dir_all(&folder).map_err(|e| failure("publication_failed", e))?;
    let staging = ws
        .resolve_write(format!(".tmp/excel/{id}/input.xlsx"))
        .map_err(|e| failure("source_conflict", e))?;
    write_new(&staging, &before).map_err(|e| failure("publication_failed", e))?;
    let candidate = ws
        .resolve_write(format!(".tmp/excel/{id}/output.xlsx"))
        .map_err(|e| failure("source_conflict", e))?;
    if candidate.exists() {
        return Err(failure(
            "publication_failed",
            "Candidate destination already exists",
        ));
    }
    phase("ownership")?;
    let request = json!({"schema_version":1,"job_id":id,"source":staging,"output":candidate,
        "expected_sha256":input_hash,"formula_cells":admission["formula_cells"],"sheet_count":admission["sheet_count"],"profile":PROFILE});
    let mut receipt = run_broker(&st.excel.script, &request, token, || {
        phase("calculation").map(|_| ())
    })
    .await?;
    phase("verification")?;
    cancelled(token)?;
    if sha256(&bounded_bytes(&staging).map_err(|e| failure("verification_failed", e))?)
        != input_hash
        || sha256(&bounded_bytes(&source).map_err(|e| failure("source_conflict", e))?) != input_hash
        || project_control::artifact_source_status(st, &ws, &parent_record) != "current"
    {
        return Err(failure(
            "source_conflict",
            "Source or parent artifact changed during calculation",
        ));
    }
    let candidate = ws
        .resolve_read(
            FilePath::new(".civil-buddy/out").join(format!(".tmp/excel/{id}/output.xlsx")),
        )
        .map_err(|e| failure("verification_failed", e))?;
    let output = bounded_bytes(&candidate).map_err(|e| failure("verification_failed", e))?;
    let output_hash = sha256(&output);
    validate_receipt(&receipt, id, input_hash, &output_hash)?;
    let calculation_input = if receipt["calculation_control_normalization"]["performed"] == true {
        ws.resolve_read(
            FilePath::new(".civil-buddy/out")
                .join(format!(".tmp/excel/{id}/calculation-input.xlsx")),
        )
        .map_err(|e| failure("verification_failed", e))?
    } else {
        staging.clone()
    };
    if sha256(&bounded_bytes(&calculation_input).map_err(|e| failure("verification_failed", e))?)
        != receipt["calculation_input_sha256"].as_str().unwrap_or("")
    {
        return Err(failure(
            "verification_failed",
            "The native calculation input is not bound to this receipt",
        ));
    }
    let candidate_relative = candidate
        .strip_prefix(ws.root())
        .map_err(|e| failure("verification_failed", e))?
        .to_string_lossy()
        .into_owned();
    let checked=worker_result(st.worker.document(&ws,"verify_excel_engine_output",&path,Some(input_hash),
        json!({"candidate_source":candidate_relative,"expected_candidate_sha256":output_hash,"engine_receipt":receipt}),token)
        .await.map_err(|e|failure("verification_failed",e))?).map_err(|(_,message)|failure("verification_failed",message))?;
    let verified = &checked["verification"];
    if checked["source_sha256"] != input_hash
        || checked["output_sha256"] != output_hash
        || verified["status"] != "pass"
        || verified["semantic_preserved"] != true
        || verified["persisted_formula_caches_verified"] != true
        || verified["formula_count"] != admission["formula_count"]
    {
        return Err(failure(
            "verification_failed",
            "Output verification did not cover every admitted formula and semantic input",
        ));
    }
    receipt["verification"] = verified.clone();
    receipt["automatic_acceptance"] = json!(false);
    receipt["professional_review"] = json!("required");
    cancelled(token)?;
    phase("publication")?;
    project_control::artifact_info(st, workspace, parent).map_err(|_| {
        failure(
            "source_conflict",
            "Input artifact changed before publication",
        )
    })?;
    if project_control::artifact_source_status(st, &ws, &parent_record) != "current"
        || sha256(&bounded_bytes(&candidate).map_err(|e| failure("verification_failed", e))?)
            != output_hash
        || sha256(
            &bounded_bytes(&calculation_input).map_err(|e| failure("verification_failed", e))?,
        ) != receipt["calculation_input_sha256"].as_str().unwrap_or("")
    {
        return Err(failure(
            "source_conflict",
            "Source or output changed before publication",
        ));
    }
    publish(
        st,
        &ws,
        workspace,
        id,
        &parent_record,
        &receipt,
        &output,
        token,
    )?;
    Ok(())
}

fn bounded_bytes(path: &FilePath) -> Result<Vec<u8>, String> {
    use std::io::Read;
    let mut file = std::fs::File::open(path).map_err(|e| e.to_string())?;
    if file.metadata().map_err(|e| e.to_string())?.len() > MAX_BYTES {
        return Err("Excel input/output exceeds 16 MiB".into());
    }
    let mut bytes = Vec::new();
    std::io::Read::by_ref(&mut file)
        .take(MAX_BYTES + 1)
        .read_to_end(&mut bytes)
        .map_err(|e| e.to_string())?;
    if bytes.len() as u64 > MAX_BYTES {
        return Err("Excel input/output exceeds 16 MiB".into());
    }
    Ok(bytes)
}
fn write_new(path: &FilePath, bytes: &[u8]) -> Result<(), String> {
    let mut file = std::fs::OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(path)
        .map_err(|e| e.to_string())?;
    file.write_all(bytes).map_err(|e| e.to_string())?;
    file.sync_all().map_err(|e| e.to_string())
}
fn publish(
    st: &ProductState,
    ws: &WorkspaceContext,
    workspace: &str,
    id: &str,
    parent: &Value,
    receipt: &Value,
    bytes: &[u8],
    token: &CancellationToken,
) -> Result<Value, Failure> {
    let target = ws
        .resolve_write(format!("documents/excel-recalculated-{id}.xlsx"))
        .map_err(|e| failure("publication_failed", e))?;
    std::fs::create_dir_all(
        target
            .parent()
            .ok_or_else(|| failure("publication_failed", "Missing output directory"))?,
    )
    .map_err(|e| failure("publication_failed", e))?;
    let target = ws
        .resolve_write(format!("documents/excel-recalculated-{id}.xlsx"))
        .map_err(|e| failure("publication_failed", e))?;
    cancelled(token)?;
    write_new(&target, bytes).map_err(|e| failure("publication_failed", e))?;
    let relative = target
        .strip_prefix(ws.root())
        .map_err(|e| failure("publication_failed", e))?;
    if sha256(
        &bounded_bytes(
            &ws.resolve_read(relative)
                .map_err(|e| failure("publication_failed", e))?,
        )
        .map_err(|e| failure("publication_failed", e))?,
    ) != receipt["output_sha256"]
    {
        return Err(failure(
            "publication_failed",
            "Published file differs from verified candidate",
        ));
    }
    project_control::artifact_info(st, workspace, parent["id"].as_str().unwrap_or("")).map_err(
        |_| {
            failure(
                "source_conflict",
                "Parent artifact changed before registration",
            )
        },
    )?;
    if project_control::artifact_source_status(st, ws, parent) != "current" {
        return Err(failure(
            "source_conflict",
            "Original source changed before registration",
        ));
    }
    // Complete the artifact and job in one transaction. Orphan candidates are
    // never treated as registered deliveries after storage/crash failures.
    let artifact_id = uuid::Uuid::new_v4().simple().to_string();
    let mut artifact = json!({"id":artifact_id,"actor_id":st.auth.owner(),"name":target.file_name().unwrap_or_default().to_string_lossy(),
        "url":format!("/api/agent/artifacts/{artifact_id}?workspace={workspace}"),"source":parent["source"],"source_sha256":parent["source_sha256"],
        "output_sha256":receipt["output_sha256"],"provenance":"excel_recalculated","parent_artifact_id":parent["id"],"parent_output_sha256":parent["output_sha256"],
        "excel_job_id":id,"evidence_validation":parent["evidence_validation"],
        "validation":{"source_unchanged":"pass","parent_unchanged":"pass","reopened":"pass","recalculation":"performed_by_excel",
            "cached_values":"engine_refreshed_and_saved_verified","semantic_preserved":true,"professional_review":"required","automatic_acceptance":false}});
    if let Some(origin) = parent.get("source_origin") {
        artifact["source_origin"] = origin.clone();
    }
    let mut db = st
        .index
        .lock()
        .map_err(|_| failure("publication_failed", "Index lock failed"))?;
    let tx = db
        .transaction()
        .map_err(|e| failure("publication_failed", e))?;
    let raw: String = tx
        .query_row(
            "SELECT record FROM excel_jobs WHERE id=?1 AND workspace=?2 AND actor=?3",
            params![id, workspace, st.auth.owner()],
            |r| r.get(0),
        )
        .map_err(|e| failure("publication_failed", e))?;
    let mut job: Value =
        serde_json::from_str(&raw).map_err(|e| failure("publication_failed", e))?;
    cancelled(token)?;
    job["status"] = json!("completed");
    job["stage"] = json!("completed");
    job["updated_at"] = json!(now());
    job["artifact"] = artifact.clone();
    job["receipt"] = receipt.clone();
    tx.execute(
        "INSERT INTO artifacts(id,workspace,path,record) VALUES(?1,?2,?3,?4)",
        params![
            artifact_id,
            workspace,
            relative.to_string_lossy(),
            artifact.to_string()
        ],
    )
    .map_err(|e| failure("publication_failed", e))?;
    tx.execute(
        "UPDATE excel_jobs SET record=?1 WHERE id=?2 AND workspace=?3",
        params![job.to_string(), id, workspace],
    )
    .map_err(|e| failure("publication_failed", e))?;
    let event = json!({"at":now(),"actor_id":st.auth.owner(),"kind":"excel_copy_recalculated","entity_id":artifact_id,
        "details":{"job_id":id,"parent_artifact_id":parent["id"],"input_sha256":parent["output_sha256"],"output_sha256":receipt["output_sha256"],"statutory_signoff":false}});
    tx.execute(
        "INSERT INTO project_events(workspace,record) VALUES(?1,?2)",
        params![workspace, event.to_string()],
    )
    .map_err(|e| failure("publication_failed", e))?;
    tx.commit().map_err(|e| failure("publication_failed", e))?;
    Ok(artifact)
}

fn validate_receipt(receipt: &Value, id: &str, input: &str, output: &str) -> Result<(), Failure> {
    if receipt["schema_version"] != 1
        || receipt["engine"] != "Microsoft Excel"
        || receipt["calculation_method"] != "CalculateFullRebuild"
        || receipt["calculation_state"] != "done"
        || receipt["owned_instance"] != true
        || receipt["workbook_count"] != 1
        || receipt["cleanup_verified"] != true
        || receipt["job_id"] != id
        || receipt["source_sha256"] != input
        || receipt["output_sha256"] != output
        || !receipt["engine_version"]
            .as_str()
            .is_some_and(|s| !s.is_empty() && s.len() <= 100)
        || !receipt["engine_build"]
            .as_str()
            .is_some_and(|s| !s.is_empty() && s.len() <= 100)
        || receipt["calculation_version"].as_u64().is_none()
        || !receipt["owned_pid"]
            .as_u64()
            .is_some_and(|pid| pid > 0 && pid <= u32::MAX as u64)
        || !receipt["owned_process_creation_ticks"]
            .as_u64()
            .is_some_and(|ticks| ticks > 0)
        || !receipt["formula_results"].is_array()
    {
        return Err(failure(
            "verification_failed",
            "Excel receipt does not prove this exact owned-instance calculation and cleanup",
        ));
    }
    validate_normalization(receipt, input)?;
    Ok(())
}

fn validate_normalization(receipt: &Value, input: &str) -> Result<(), Failure> {
    let normalized = &receipt["calculation_control_normalization"];
    let Some(fields) = normalized.as_object() else {
        return Err(failure(
            "verification_failed",
            "Calculation control normalization receipt is missing",
        ));
    };
    let Some(changes) = normalized["changed_attributes"].as_object() else {
        return Err(failure(
            "verification_failed",
            "Calculation control normalization changes are invalid",
        ));
    };
    let native_hash = receipt["calculation_input_sha256"].as_str().unwrap_or("");
    let admitted_changes = changes.iter().all(|(name, value)| {
        matches!(name.as_str(), "forceFullCalc" | "fullCalcOnLoad")
            && matches!(value.as_str(), Some("1" | "true"))
    });
    let normalized_shape = match normalized["performed"].as_bool() {
        Some(true) => !changes.is_empty() && native_hash != input,
        Some(false) => changes.is_empty() && native_hash == input,
        None => false,
    };
    if fields.len() != 3
        || !fields.keys().all(|name| {
            matches!(
                name.as_str(),
                "performed" | "changed_attributes" | "retained_input_sha256"
            )
        })
        || normalized["retained_input_sha256"] != input
        || !valid_hash(native_hash)
        || !admitted_changes
        || !normalized_shape
    {
        return Err(failure("verification_failed", "The calculation copy may normalize only the two explicit recalc-control flags; the retained input hash must remain exact"));
    }
    Ok(())
}

fn valid_hash(value: &str) -> bool {
    value.len() == 64
        && value
            .bytes()
            .all(|ch| ch.is_ascii_digit() || (b'a'..=b'f').contains(&ch))
}

fn checked_script(path: &FilePath) -> Result<(), String> {
    for current in path.ancestors() {
        let meta =
            std::fs::symlink_metadata(current).map_err(|_| "Fixed Excel broker is missing")?;
        let mut link = meta.file_type().is_symlink();
        #[cfg(windows)]
        {
            use std::os::windows::fs::MetadataExt;
            link |= meta.file_attributes() & 0x400 != 0;
        }
        if link {
            return Err("Fixed Excel broker path traverses a link".into());
        }
    }
    if std::fs::read(path).map_err(|_| "Cannot read fixed Excel broker")? != BROKER {
        return Err("Fixed Excel broker differs from compiled source".into());
    }
    Ok(())
}

async fn run_broker(
    script: &FilePath,
    request: &Value,
    token: &CancellationToken,
    on_owned: impl FnOnce() -> Result<(), Failure>,
) -> Result<Value, Failure> {
    checked_script(script).map_err(|e| failure("engine_failed", e))?;
    #[cfg(not(windows))]
    {
        let _ = (request, token, on_owned);
        Err(failure("engine_disabled", "Windows desktop required"))
    }
    #[cfg(windows)]
    {
        let previous = native::excel_processes().map_err(|e| failure("ownership_unverified", e))?;
        let root = std::env::var_os("SystemRoot")
            .map(PathBuf::from)
            .ok_or_else(|| failure("engine_failed", "SystemRoot is unavailable"))?;
        if !root.is_absolute() {
            return Err(failure("engine_failed", "SystemRoot must be absolute"));
        }
        let executable = root.join("System32/WindowsPowerShell/v1.0/powershell.exe");
        let mut command = tokio::process::Command::new(executable);
        command
            .args([
                "-NoProfile",
                "-NonInteractive",
                "-STA",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
            ])
            .arg(script)
            .current_dir(
                script
                    .parent()
                    .ok_or_else(|| failure("engine_failed", "Missing broker directory"))?,
            )
            .env_clear()
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .kill_on_drop(true)
            .creation_flags(0x08000000);
        for name in [
            "SystemRoot",
            "WINDIR",
            "TEMP",
            "TMP",
            "APPDATA",
            "LOCALAPPDATA",
            "USERPROFILE",
            "HOMEDRIVE",
            "HOMEPATH",
            "PATH",
        ] {
            if let Some(value) = std::env::var_os(name) {
                command.env(name, value);
            }
        }
        let mut child = command
            .spawn()
            .map_err(|_| failure("engine_failed", "Cannot start fixed Windows Excel broker"))?;
        let stdin = child
            .stdin
            .take()
            .ok_or_else(|| failure("engine_failed", "Missing broker stdin"))?;
        let stdout = child
            .stdout
            .take()
            .ok_or_else(|| failure("engine_failed", "Missing broker stdout"))?;
        let stderr = child
            .stderr
            .take()
            .ok_or_else(|| failure("engine_failed", "Missing broker stderr"))?;
        let errors = tokio::spawn(async move {
            let mut b = Vec::new();
            stderr.take(65537).read_to_end(&mut b).await.map(|_| b)
        });
        let id = request["job_id"]
            .as_str()
            .ok_or_else(|| failure("engine_failed", "Missing host job ID"))?;
        let mut owned = None;
        let exchange = exchange(stdin, stdout, request, id, |message| {
            let identity = native::verify_new_excel(message, &previous)?;
            owned = Some(identity);
            on_owned()
        });
        let result = tokio::select! {
            result=exchange=>result,
            _=super::providers::cancelled(token)=>Err(failure("cancelled","Calculation cancelled; stopping owned broker")),
            _=tokio::time::sleep(Duration::from_secs(JOB_TIMEOUT))=>Err(failure("engine_timeout","Excel exceeded its bounded runtime"))
        };
        if result.is_err() {
            let _ = child.kill().await;
        }
        let status = match tokio::time::timeout(Duration::from_secs(10), child.wait()).await {
            Ok(Ok(status)) => Some(status),
            _ => {
                let _ = child.kill().await;
                let _ = child.wait().await;
                None
            }
        };
        let diagnostics = match tokio::time::timeout(Duration::from_secs(5), errors).await {
            Ok(Ok(Ok(bytes))) => bytes,
            _ => Vec::new(),
        };
        if owned.is_none()
            && (token.is_cancelled()
                || result
                    .as_ref()
                    .err()
                    .is_some_and(|(code, _)| *code == "engine_timeout"))
        {
            let reason = result
                .as_ref()
                .err()
                .map(|(code, reason)| format!("{code}: {reason}"))
                .unwrap_or_else(|| "Interrupted before ownership confirmation".into());
            return Err(failure("engine_cleanup_unverified", format!("{reason}; the supervisor has no validated owned Excel identity, so cleanup cannot be confirmed. No new copy is approved.")));
        }
        if let Some(identity) = owned.as_ref() {
            identity
                .wait_stopped()
                .await
                .map_err(|e| failure("engine_cleanup_unverified", e))?;
        }
        cancelled(token)?;
        let receipt = result?;
        if !status.is_some_and(|s| s.success()) || diagnostics.len() > 65536 {
            return Err(failure(
                "engine_failed",
                "Excel broker failed or exceeded diagnostics limit",
            ));
        }
        let identity = owned.ok_or_else(|| {
            failure(
                "ownership_unverified",
                "Broker did not establish Excel ownership",
            )
        })?;
        if receipt["owned_pid"] != identity.pid
            || receipt["owned_process_creation_ticks"] != identity.ticks
        {
            return Err(failure(
                "ownership_unverified",
                "Final Excel receipt changed process identity",
            ));
        }
        Ok(receipt)
    }
}

async fn exchange<R: tokio::io::AsyncRead + Unpin, W: tokio::io::AsyncWrite + Unpin>(
    mut input: W,
    output: R,
    request: &Value,
    id: &str,
    validate: impl FnOnce(&Value) -> Result<(), Failure>,
) -> Result<Value, Failure> {
    let bytes = serde_json::to_vec(request).map_err(|e| failure("engine_failed", e))?;
    if bytes.len() as u64 > MAX_PROTOCOL {
        return Err(failure("engine_failed", "Broker request exceeds limit"));
    }
    input
        .write_all(&bytes)
        .await
        .map_err(|e| failure("engine_failed", e))?;
    input
        .write_all(b"\n")
        .await
        .map_err(|e| failure("engine_failed", e))?;
    input
        .flush()
        .await
        .map_err(|e| failure("engine_failed", e))?;
    let mut output = BufReader::new(output.take(MAX_PROTOCOL + 1));
    let mut total = 0_u64;
    async fn line<R: tokio::io::AsyncRead + Unpin>(
        reader: &mut BufReader<R>,
        total: &mut u64,
    ) -> Result<Value, Failure> {
        let mut bytes = Vec::new();
        let count = reader
            .read_until(b'\n', &mut bytes)
            .await
            .map_err(|e| failure("engine_failed", e))?;
        *total += count as u64;
        if count == 0 || *total > MAX_PROTOCOL {
            return Err(failure(
                "engine_failed",
                "Missing or oversized Excel broker response",
            ));
        }
        serde_json::from_slice(&bytes)
            .map_err(|_| failure("engine_failed", "Excel broker returned malformed JSON"))
    }
    let owned = line(&mut output, &mut total).await?;
    if owned["stage"] == "failed" && owned["job_id"] == id {
        return Err(broker_failure(&owned));
    }
    if owned["stage"] != "owned" || owned["job_id"] != id {
        return Err(failure(
            "ownership_unverified",
            "Broker did not establish ownership before opening",
        ));
    }
    validate(&owned)?;
    let acknowledgement = serde_json::to_vec(&json!({"job_id":id,"continue":true}))
        .map_err(|e| failure("engine_failed", e))?;
    input
        .write_all(&acknowledgement)
        .await
        .map_err(|e| failure("engine_failed", e))?;
    input
        .write_all(b"\n")
        .await
        .map_err(|e| failure("engine_failed", e))?;
    input
        .shutdown()
        .await
        .map_err(|e| failure("engine_failed", e))?;
    let done = line(&mut output, &mut total).await?;
    if done["stage"] != "completed" || done["job_id"] != id {
        return Err(broker_failure(&done));
    }
    let mut extra = Vec::new();
    output
        .read_to_end(&mut extra)
        .await
        .map_err(|e| failure("engine_failed", e))?;
    total += extra.len() as u64;
    if total > MAX_PROTOCOL || extra.iter().any(|b| !b.is_ascii_whitespace()) {
        return Err(failure(
            "engine_failed",
            "Excel broker returned extra protocol output",
        ));
    }
    Ok(done["receipt"].clone())
}

fn broker_failure(message: &Value) -> Failure {
    // Fixed broker codes and stage labels are useful support diagnostics; raw
    // COM messages, paths and workbook contents are deliberately not retained.
    let code = message["error"]["code"]
        .as_str()
        .filter(|code| {
            !code.is_empty()
                && code.len() <= 64
                && code
                    .bytes()
                    .all(|ch| ch.is_ascii_alphanumeric() || ch == b'_')
        })
        .unwrap_or("ExcelDesktopEngineFailed");
    let stage = message["error"]["operation"]
        .as_str()
        .filter(|stage| {
            matches!(
                *stage,
                "request"
                    | "prepare_calculation_input"
                    | "activation"
                    | "open"
                    | "calculation"
                    | "capture"
                    | "save"
            )
        })
        .unwrap_or("unknown");
    let hresult = message["error"]["hresult"]
        .as_i64()
        .map(|value| value.to_string())
        .unwrap_or_else(|| "unavailable".into());
    failure(if message["cleanup_verified"] == true { "engine_failed" } else { "engine_cleanup_unverified" }, format!("Excel desktop failed: {code}; operation={stage}; hresult={hresult}. No new copy is approved."))
}

#[cfg(test)]
mod tests {
    use super::*;
    use axum::{body::Body, http::Request as HttpRequest};
    use http_body_util::BodyExt;
    use tower::ServiceExt;
    struct Fixture {
        state: Arc<ProductState>,
        workspace: String,
        other: String,
        parent: Value,
        root: PathBuf,
        original: Vec<u8>,
    }
    impl Fixture {
        fn new() -> Self {
            let repo = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
                .parent()
                .unwrap()
                .to_path_buf();
            let root =
                std::env::temp_dir().join(format!("civil-excel-product-{}", uuid::Uuid::new_v4()));
            let job = root.join("job");
            let out = job.join(".civil-buddy/out");
            std::fs::create_dir_all(&out).unwrap();
            std::fs::create_dir_all(root.join("other")).unwrap();
            let original = b"no Office opens this permission fixture".to_vec();
            std::fs::write(job.join("original.xlsx"), &original).unwrap();
            std::fs::write(out.join("copy.xlsx"), &original).unwrap();
            let mut paths = crate::config::Paths::from_demo(repo.join("demo"));
            paths.data_dir = root.join("state");
            let state =
                ProductState::open_with_auth(paths, super::super::auth::InstanceAuth::local())
                    .unwrap();
            let workspace = state.register(job.to_str().unwrap()).unwrap()["id"]
                .as_str()
                .unwrap()
                .to_owned();
            let other = state
                .register(root.join("other").to_str().unwrap())
                .unwrap()["id"]
                .as_str()
                .unwrap()
                .to_owned();
            let parent=state.register_artifact(&workspace,&json!({"source":"original.xlsx","source_sha256":sha256(&original),
                "output_path":out.join("copy.xlsx").canonicalize().unwrap(),"output_sha256":sha256(&original),"validation":{},"evidence_validation":"not_checked"})).unwrap();
            Self {
                state,
                workspace,
                other,
                parent,
                root,
                original,
            }
        }
        fn request(&self) -> Value {
            json!({"expected_sha256":self.parent["output_sha256"],"idempotency_key":uuid::Uuid::new_v4().to_string(),"sandbox":"workspace-write","risk_confirmation":ACK})
        }
        fn endpoint(&self) -> String {
            format!(
                "/api/agent/artifacts/{}/excel-recalculate?workspace={}",
                self.parent["id"].as_str().unwrap(),
                self.workspace
            )
        }
        fn unchanged(&self) {
            assert_eq!(
                std::fs::read(self.root.join("job/original.xlsx")).unwrap(),
                self.original
            );
            assert_eq!(
                std::fs::read(self.root.join("job/.civil-buddy/out/copy.xlsx")).unwrap(),
                self.original
            );
        }
    }
    async fn call(
        st: &Arc<ProductState>,
        method: &str,
        url: &str,
        body: Value,
    ) -> (StatusCode, Value) {
        let response = super::super::api::router(st.clone())
            .oneshot(
                HttpRequest::builder()
                    .method(method)
                    .uri(url)
                    .header("content-type", "application/json")
                    .body(Body::from(body.to_string()))
                    .unwrap(),
            )
            .await
            .unwrap();
        let status = response.status();
        let bytes = response.into_body().collect().await.unwrap().to_bytes();
        (
            status,
            serde_json::from_slice(&bytes)
                .unwrap_or_else(|_| json!({"raw":String::from_utf8_lossy(&bytes)})),
        )
    }
    #[tokio::test]
    async fn explicit_confirmation_and_write_mode_are_required_before_any_job_or_office() {
        let fixture = Fixture::new();
        for (field, value) in [
            ("sandbox", "read-only"),
            ("risk_confirmation", "我明白，将由持证人员签认 "),
            ("risk_confirmation", ""),
        ] {
            let mut request = fixture.request();
            request[field] = json!(value);
            let (status, body) = call(&fixture.state, "POST", &fixture.endpoint(), request).await;
            assert_eq!(status, StatusCode::FORBIDDEN, "{body}");
            assert_eq!(body["code"], "permission_denied");
        }
        let (_, history) = call(
            &fixture.state,
            "GET",
            &format!("/api/agent/excel-jobs?workspace={}", fixture.workspace),
            json!({}),
        )
        .await;
        assert!(history["jobs"].as_array().unwrap().is_empty());
        fixture.unchanged();
    }
    #[tokio::test]
    async fn stale_artifact_original_change_and_caller_paths_cannot_trigger_office() {
        let fixture = Fixture::new();
        let mut request = fixture.request();
        request["expected_sha256"] = json!("0".repeat(64));
        let (status, body) = call(&fixture.state, "POST", &fixture.endpoint(), request).await;
        assert_eq!(status, StatusCode::CONFLICT, "{body}");
        let mut request = fixture.request();
        request["output_path"] = json!("C:/outside/overwrite.xlsx");
        assert_eq!(
            call(&fixture.state, "POST", &fixture.endpoint(), request)
                .await
                .0,
            StatusCode::UNPROCESSABLE_ENTITY
        );
        std::fs::write(fixture.root.join("job/original.xlsx"), b"later original").unwrap();
        let (status, body) = call(
            &fixture.state,
            "POST",
            &fixture.endpoint(),
            fixture.request(),
        )
        .await;
        assert_eq!(status, StatusCode::CONFLICT, "{body}");
        assert_eq!(body["code"], "source_conflict");
        assert_eq!(
            std::fs::read(fixture.root.join("job/.civil-buddy/out/copy.xlsx")).unwrap(),
            fixture.original
        );
    }
    #[tokio::test]
    async fn disabled_engine_and_cross_workspace_are_failclosed_without_output() {
        assert!(
            std::env::var_os("CIVIL_EXCEL_ENGINE").is_none(),
            "Offline tests must not enable the desktop engine"
        );
        let fixture = Fixture::new();
        let (status, body) = call(
            &fixture.state,
            "POST",
            &fixture.endpoint(),
            fixture.request(),
        )
        .await;
        assert_eq!(status, StatusCode::FORBIDDEN, "{body}");
        assert_eq!(body["code"], "engine_disabled");
        let url = format!(
            "/api/agent/artifacts/{}/excel-recalculate?workspace={}",
            fixture.parent["id"].as_str().unwrap(),
            fixture.other
        );
        assert_eq!(
            call(&fixture.state, "POST", &url, fixture.request())
                .await
                .0,
            StatusCode::NOT_FOUND
        );
        assert!(!fixture
            .root
            .join("job/.civil-buddy/out/.tmp/excel")
            .exists());
        fixture.unchanged();
        let mut controller =
            ExcelController::detect(&fixture.state.paths.repo_root, &fixture.state.auth);
        controller.enabled = true;
        controller.local = false;
        assert_eq!(controller.reason(), Some("local_desktop_only"));
        assert_eq!(controller.capabilities()["available"], false);
    }
    fn saved(st: &ProductState, workspace: &str, id: &str, status: &str, parent: &Value) -> Value {
        let job = json!({"id":id,"workspace":workspace,"actor_id":st.auth.owner(),"parent_artifact_id":parent["id"],"input_sha256":parent["output_sha256"],
            "status":status,"stage":"calculation","created_at":now(),"updated_at":now(),"error":null,"artifact":null,"receipt":null});
        st.index.lock().unwrap().execute("INSERT INTO excel_jobs(id,workspace,actor,client_key,request_sha256,record) VALUES(?1,?2,?3,?1,'test-record',?4)",
            params![id,workspace,st.auth.owner(),job.to_string()]).unwrap();
        job
    }
    #[tokio::test]
    async fn history_is_persistent_scoped_and_recovery_never_infers_success() {
        let fixture = Fixture::new();
        saved(
            &fixture.state,
            &fixture.workspace,
            "running-job",
            "running",
            &fixture.parent,
        );
        saved(
            &fixture.state,
            &fixture.workspace,
            "failed-job",
            "failed",
            &fixture.parent,
        );
        initialize(&fixture.state.index.lock().unwrap()).unwrap();
        let recovered = load(&fixture.state, &fixture.workspace, "running-job").unwrap();
        assert_eq!(recovered["status"], "interrupted");
        assert_eq!(recovered["error"]["code"], "interrupted");
        assert!(recovered["artifact"].is_null());
        assert_eq!(
            load(&fixture.state, &fixture.workspace, "failed-job").unwrap()["status"],
            "failed"
        );
        let (_, history) = call(
            &fixture.state,
            "GET",
            &format!("/api/agent/excel-jobs?workspace={}", fixture.workspace),
            json!({}),
        )
        .await;
        assert_eq!(history["jobs"].as_array().unwrap().len(), 2);
        assert_eq!(history["jobs"][0]["id"], "failed-job");
        let (status, other) = call(
            &fixture.state,
            "GET",
            &format!("/api/agent/excel-jobs?workspace={}", fixture.other),
            json!({}),
        )
        .await;
        assert_eq!(status, StatusCode::OK);
        assert!(other["jobs"].as_array().unwrap().is_empty());
        assert_eq!(
            call(
                &fixture.state,
                "GET",
                &format!(
                    "/api/agent/excel-jobs/running-job?workspace={}",
                    fixture.other
                ),
                json!({})
            )
            .await
            .0,
            StatusCode::NOT_FOUND
        );
        assert_eq!(
            call(
                &fixture.state,
                "POST",
                &format!(
                    "/api/agent/excel-jobs/running-job/cancel?workspace={}",
                    fixture.other
                ),
                json!({})
            )
            .await
            .0,
            StatusCode::NOT_FOUND
        );
        fixture.unchanged();
    }
    #[tokio::test]
    async fn concurrent_retry_keys_and_injected_fields_do_not_create_other_operations() {
        let fixture = Fixture::new();
        let request: Request = serde_json::from_value(fixture.request()).unwrap();
        let key = uuid::Uuid::parse_str(&request.idempotency_key)
            .unwrap()
            .simple()
            .to_string();
        let id = "retry-job";
        let original = saved(
            &fixture.state,
            &fixture.workspace,
            id,
            "failed",
            &fixture.parent,
        );
        fixture
            .state
            .index
            .lock()
            .unwrap()
            .execute(
                "UPDATE excel_jobs SET client_key=?1,request_sha256=?2 WHERE id=?3",
                params![
                    key,
                    request_digest(fixture.parent["id"].as_str().unwrap(), &request),
                    id
                ],
            )
            .unwrap();
        let body = json!({"expected_sha256":request.expected_sha256,"idempotency_key":request.idempotency_key,"sandbox":request.sandbox,"risk_confirmation":request.risk_confirmation});
        let (status, replay) =
            call(&fixture.state, "POST", &fixture.endpoint(), body.clone()).await;
        assert_eq!(status, StatusCode::ACCEPTED, "{replay}");
        assert_eq!(replay["job"], original);
        assert_eq!(replay["replayed"], true);
        let mut different = body.clone();
        different["expected_sha256"] = json!("0".repeat(64));
        assert_eq!(
            call(&fixture.state, "POST", &fixture.endpoint(), different)
                .await
                .0,
            StatusCode::CONFLICT
        );
        for field in ["source", "output", "engine_receipt", "script", "operation"] {
            let mut injected = body.clone();
            injected[field] = json!("untrusted");
            assert_eq!(
                call(&fixture.state, "POST", &fixture.endpoint(), injected)
                    .await
                    .0,
                StatusCode::UNPROCESSABLE_ENTITY
            );
        }
        fixture.unchanged();
    }
    #[tokio::test]
    async fn broker_cannot_open_before_host_ownership_ack_or_report_extra_output() {
        let (host_input, broker_read) = tokio::io::duplex(65536);
        let (broker_write, host_output) = tokio::io::duplex(65536);
        let broker = tokio::spawn(async move {
            let mut input = BufReader::new(broker_read);
            let mut line = String::new();
            input.read_line(&mut line).await.unwrap();
            assert_eq!(
                serde_json::from_str::<Value>(&line).unwrap()["job_id"],
                "test-job"
            );
            let mut output = broker_write;
            output
                .write_all(b"{\"stage\":\"owned\",\"job_id\":\"test-job\",\"owned_pid\":1}\n")
                .await
                .unwrap();
            line.clear();
            input.read_line(&mut line).await.unwrap();
            assert_eq!(
                serde_json::from_str::<Value>(&line).unwrap(),
                json!({"job_id":"test-job","continue":true})
            );
            output.write_all(b"{\"stage\":\"completed\",\"job_id\":\"test-job\",\"receipt\":{\"proof\":true}}\n{\"untrusted_extra\":true}\n").await.unwrap();
        });
        let result = exchange(
            host_input,
            host_output,
            &json!({"job_id":"test-job"}),
            "test-job",
            |owned| {
                assert_eq!(owned["owned_pid"], 1);
                Ok(())
            },
        )
        .await;
        assert!(result.unwrap_err().1.contains("extra protocol"));
        broker.await.unwrap();
    }
    #[tokio::test]
    async fn invalid_ownership_receives_no_continue_and_cleanup_receipt_is_mandatory() {
        let (host_input, mut broker_read) = tokio::io::duplex(65536);
        let (mut broker_write, host_output) = tokio::io::duplex(65536);
        let broker = tokio::spawn(async move {
            let mut bytes = [0_u8; 4096];
            let _ = broker_read.read(&mut bytes).await.unwrap();
            broker_write
                .write_all(b"{\"stage\":\"owned\",\"job_id\":\"test-job\"}\n")
                .await
                .unwrap();
            assert_eq!(
                broker_read.read(&mut bytes).await.unwrap(),
                0,
                "No continue may be sent to an unowned process"
            );
        });
        assert_eq!(
            exchange(
                host_input,
                host_output,
                &json!({"job_id":"test-job"}),
                "test-job",
                |_| Err(failure("ownership_unverified", "Existing user Excel"))
            )
            .await
            .unwrap_err()
            .0,
            "ownership_unverified"
        );
        broker.await.unwrap();
        let receipt = json!({"schema_version":1,"engine":"Microsoft Excel","engine_version":"16","calculation_method":"CalculateFullRebuild","calculation_state":"done",
            "owned_instance":true,"workbook_count":1,"cleanup_verified":false,"job_id":"j","source_sha256":"input","output_sha256":"output","formula_results":[]});
        assert!(validate_receipt(&receipt, "j", "input", "output").is_err());
    }
    #[test]
    fn calculation_control_evidence_cannot_hide_native_input_or_other_workbook_changes() {
        let input = "a".repeat(64);
        let mut receipt = json!({"calculation_input_sha256":input,"calculation_control_normalization":{"performed":false,"changed_attributes":{},"retained_input_sha256":input}});
        validate_normalization(&receipt, &input).unwrap();
        receipt["calculation_input_sha256"] = json!("b".repeat(64));
        assert!(validate_normalization(&receipt, &input).is_err());
        receipt["calculation_control_normalization"]["performed"] = json!(true);
        assert!(validate_normalization(&receipt, &input).is_err());
        receipt["calculation_control_normalization"]["changed_attributes"] =
            json!({"forceFullCalc":"1","fullCalcOnLoad":"true"});
        validate_normalization(&receipt, &input).unwrap();
        receipt["calculation_control_normalization"]["changed_attributes"]["fullCalcOnLoad"] =
            json!("0");
        assert!(validate_normalization(&receipt, &input).is_err());
        receipt["calculation_control_normalization"]["changed_attributes"] = json!({"formula":"1"});
        assert!(validate_normalization(&receipt, &input).is_err());
        receipt["calculation_control_normalization"]["changed_attributes"] =
            json!({"forceFullCalc":"1"});
        receipt["calculation_control_normalization"]["retained_input_sha256"] =
            json!("b".repeat(64));
        assert!(validate_normalization(&receipt, &input).is_err());
    }
    #[test]
    fn broker_support_diagnostics_exclude_raw_com_messages_and_document_contents() {
        let (code, reason) = broker_failure(
            &json!({"cleanup_verified":true,"error":{"code":"ExcelDesktopEngineFailed","operation":"open","hresult":-2146827284,"message":"private document contents and C:\\private.xlsx"}}),
        );
        assert_eq!(code, "engine_failed");
        assert!(reason.contains("operation=open"));
        assert!(reason.contains("hresult=-2146827284"));
        assert!(!reason.contains("private"));
        let (code, reason) = broker_failure(
            &json!({"cleanup_verified":false,"error":{"code":"C:\\private.xlsx","operation":"private contents","hresult":"private"}}),
        );
        assert_eq!(code, "engine_cleanup_unverified");
        assert!(reason.contains("ExcelDesktopEngineFailed"));
        assert!(reason.contains("operation=unknown"));
        assert!(!reason.contains("private"));
    }
    #[test]
    fn worker_claims_alone_never_upgrade_readiness_and_receipt_replay_requires_exact_hash() {
        let fixture = Fixture::new();
        let mut fake = json!({"engine_receipt":{"calculation_state":"done"},"readiness":{"format_details":{"cached_values_status":"stored_unverified"}}});
        merge_readiness(
            &fixture.state,
            &fixture.workspace,
            &fixture.parent,
            &mut fake,
        )
        .unwrap();
        assert_eq!(
            fake["readiness"]["format_details"]["cached_values_status"],
            "stored_unverified"
        );
        let mut derivative = fixture.parent.clone();
        derivative["excel_job_id"] = json!("missing-job");
        assert!(
            merge_readiness(&fixture.state, &fixture.workspace, &derivative, &mut fake).is_err()
        );
        fixture.unchanged();
    }
    #[cfg(windows)]
    #[test]
    fn already_running_or_wrong_creation_identity_is_never_accepted_as_owned_excel() {
        let pid = std::process::id();
        let existing = std::collections::HashSet::from([pid]);
        assert!(native::verify_new_excel(
            &json!({"owned_pid":pid,"owned_process_creation_ticks":1}),
            &existing
        )
        .is_err());
        assert!(native::verify_new_excel(
            &json!({"owned_pid":pid,"owned_process_creation_ticks":1}),
            &std::collections::HashSet::new()
        )
        .is_err());
    }
}

/// A worker can only describe OOXML bytes. Trusted calculation evidence is
/// adopted from a completed host-owned job, never from caller/worker claims.
pub(super) fn merge_readiness(
    st: &ProductState,
    workspace: &str,
    artifact: &Value,
    result: &mut Value,
) -> Result<(), String> {
    let Some(job_id) = artifact["excel_job_id"].as_str() else {
        return Ok(());
    };
    let job = load(st, workspace, job_id)
        .map_err(|_| "Registered Excel calculation receipt is unavailable")?;
    let receipt = &job["receipt"];
    if job["status"] != "completed"
        || job["artifact"]["id"] != artifact["id"]
        || job["artifact"]["output_sha256"] != artifact["output_sha256"]
        || job["input_sha256"] != artifact["parent_output_sha256"]
        || receipt["output_sha256"] != artifact["output_sha256"]
        || result["source_sha256"] != artifact["output_sha256"]
        || result["format"] != "xlsx"
    {
        return Err("Excel calculation receipt is not bound to this artifact".into());
    }
    validate_receipt(
        receipt,
        job_id,
        job["input_sha256"].as_str().unwrap_or(""),
        artifact["output_sha256"].as_str().unwrap_or(""),
    )
    .map_err(|(_, e)| e)?;
    let verification = &receipt["verification"];
    if verification["status"] != "pass"
        || verification["semantic_preserved"] != true
        || verification["persisted_formula_caches_verified"] != true
        || verification["formula_count"] != result["readiness"]["format_details"]["formula_count"]
    {
        return Err("Excel calculation cache verification is incomplete".into());
    }
    let detail = &result["readiness"]["format_details"];
    let caches_good = detail["formula_missing_cache_count"] == 0
        && detail["formula_invalid_cache_count"] == 0
        && detail["error_cell_count"] == 0;
    result["engine_receipt"] = receipt.clone();
    result["validation"]["recalculation"] = json!("performed_by_excel");
    result["validation"]["cached_values"] = json!("engine_refreshed_and_saved_verified");
    result["readiness"]["format_details"]["recalculation"] = json!("performed_by_excel");
    result["readiness"]["format_details"]["cached_values_status"] =
        json!("engine_refreshed_and_saved_verified");
    if let Some(checks) = result["readiness"]["checks"].as_array_mut() {
        for check in checks {
            if check["id"] == "recalculation" {
                check["status"] = json!("pass");
                check["detail"]=json!("A dedicated owned Microsoft Excel instance completed CalculateFullRebuild; professional review remains required.");
            }
            if check["id"] == "formula_cache" && caches_good {
                check["status"] = json!("pass");
                check["detail"]=json!("Every saved formula cache matches the Excel calculation result; this does not establish engineering correctness.");
            }
        }
    }
    if result["original_source_status"] != "current" {
        result["readiness"]["status"] = json!("blocked");
    }
    result["readiness"]["automatic_acceptance"] = json!(false);
    Ok(())
}

#[cfg(windows)]
mod native {
    use super::*;
    use std::{collections::HashSet, ffi::c_void};
    type Handle = *mut c_void;
    #[repr(C)]
    struct FileTime {
        low: u32,
        high: u32,
    }
    #[repr(C)]
    struct ProcessEntry {
        size: u32,
        usage: u32,
        pid: u32,
        heap: usize,
        module: u32,
        threads: u32,
        parent: u32,
        priority: i32,
        flags: u32,
        name: [u16; 260],
    }
    #[link(name = "kernel32")]
    extern "system" {
        fn CreateToolhelp32Snapshot(flags: u32, pid: u32) -> Handle;
        fn GetLastError() -> u32;
        fn Process32FirstW(snapshot: Handle, entry: *mut ProcessEntry) -> i32;
        fn Process32NextW(snapshot: Handle, entry: *mut ProcessEntry) -> i32;
        fn OpenProcess(access: u32, inherit: i32, pid: u32) -> Handle;
        fn GetProcessTimes(
            process: Handle,
            creation: *mut FileTime,
            exit: *mut FileTime,
            kernel: *mut FileTime,
            user: *mut FileTime,
        ) -> i32;
        fn QueryFullProcessImageNameW(
            process: Handle,
            flags: u32,
            name: *mut u16,
            size: *mut u32,
        ) -> i32;
        fn WaitForSingleObject(handle: Handle, millis: u32) -> u32;
        fn CloseHandle(handle: Handle) -> i32;
    }
    pub struct Identity {
        handle: Handle,
        pub pid: u32,
        pub ticks: u64,
    }
    // Windows process handles may be queried/waited/closed from another thread.
    unsafe impl Send for Identity {}
    unsafe impl Sync for Identity {}
    impl Drop for Identity {
        fn drop(&mut self) {
            unsafe {
                CloseHandle(self.handle);
            }
        }
    }
    impl Identity {
        pub async fn wait_stopped(&self) -> Result<(), String> {
            for _ in 0..100 {
                if unsafe { WaitForSingleObject(self.handle, 0) } == 0 {
                    return Ok(());
                }
                tokio::time::sleep(Duration::from_millis(100)).await;
            }
            Err(
                "Owned Excel process did not stop after broker cleanup; no delivery registered"
                    .into(),
            )
        }
    }
    struct Snapshot(Handle);
    impl Drop for Snapshot {
        fn drop(&mut self) {
            unsafe {
                CloseHandle(self.0);
            }
        }
    }
    fn collect_excel_processes(
        mut next: impl FnMut(bool) -> Result<Option<(u32, String)>, u32>,
    ) -> Result<HashSet<u32>, String> {
        let mut first = true;
        let mut found = HashSet::new();
        loop {
            match next(first) {
                Ok(Some((pid, name))) => {
                    if name.eq_ignore_ascii_case("EXCEL.EXE") {
                        found.insert(pid);
                    }
                }
                // A live host guarantees a nonempty process snapshot. An empty
                // first entry is not evidence that no Excel processes exist.
                Ok(None) if first => {
                    return Err("Prior process snapshot contains no process information".into())
                }
                Ok(None) => return Ok(found),
                Err(code) => {
                    return Err(format!(
                        "Cannot completely enumerate prior processes (Windows error {code})"
                    ))
                }
            }
            first = false;
        }
    }
    pub fn excel_processes() -> Result<HashSet<u32>, String> {
        unsafe {
            let snapshot = CreateToolhelp32Snapshot(2, 0);
            if snapshot as isize == -1 {
                return Err("Cannot enumerate prior Excel processes".into());
            }
            let snapshot = Snapshot(snapshot);
            let mut entry: ProcessEntry = std::mem::zeroed();
            entry.size = std::mem::size_of::<ProcessEntry>() as u32;
            collect_excel_processes(|first| {
                let ok = if first {
                    Process32FirstW(snapshot.0, &mut entry)
                } else {
                    Process32NextW(snapshot.0, &mut entry)
                };
                if ok == 0 {
                    // Capture immediately: closing the snapshot or another API
                    // call may overwrite the thread's Win32 last-error value.
                    let code = GetLastError();
                    return if code == 18 { Ok(None) } else { Err(code) };
                }
                let end = entry.name.iter().position(|c| *c == 0).unwrap_or(260);
                Ok(Some((
                    entry.pid,
                    String::from_utf16_lossy(&entry.name[..end]),
                )))
            })
        }
    }
    #[cfg(test)]
    mod inventory_tests {
        use super::*;
        #[test]
        fn failed_enumeration_never_becomes_an_empty_or_partial_exclusion_inventory() {
            assert!(collect_excel_processes(|_| Err(5)).is_err());
            assert!(collect_excel_processes(|_| Ok(None)).is_err());
            let mut entries = vec![Ok(Some((41, "EXCEL.EXE".into()))), Err(5)].into_iter();
            assert!(collect_excel_processes(|_| entries.next().unwrap()).is_err());
            let mut entries = vec![Ok(Some((41, "EXCEL.EXE".into()))), Err(0)].into_iter();
            assert!(collect_excel_processes(|_| entries.next().unwrap()).is_err());
        }
        #[test]
        fn complete_process_snapshot_preserves_every_prior_excel_identity() {
            let mut entries = vec![
                Ok(Some((41, "EXCEL.EXE".into()))),
                Ok(Some((42, "civil-workbench.exe".into()))),
                Ok(Some((43, "excel.exe".into()))),
                Ok(None),
            ]
            .into_iter();
            let mut calls = Vec::new();
            let found = collect_excel_processes(|first| {
                calls.push(first);
                entries.next().unwrap()
            })
            .unwrap();
            assert_eq!(found, HashSet::from([41, 43]));
            assert_eq!(calls, vec![true, false, false, false]);
        }
    }
    pub fn verify_new_excel(message: &Value, previous: &HashSet<u32>) -> Result<Identity, Failure> {
        let pid = message["owned_pid"]
            .as_u64()
            .filter(|pid| *pid > 0 && *pid <= u32::MAX as u64)
            .ok_or_else(|| failure("ownership_unverified", "Invalid owned Excel PID"))?
            as u32;
        let ticks = message["owned_process_creation_ticks"]
            .as_u64()
            .ok_or_else(|| {
                failure(
                    "ownership_unverified",
                    "Missing Excel process creation identity",
                )
            })?;
        if previous.contains(&pid) {
            return Err(failure(
                "ownership_unverified",
                "Broker attached to an existing Excel process",
            ));
        }
        unsafe {
            let handle = OpenProcess(0x1000 | 0x100000, 0, pid);
            if handle.is_null() {
                return Err(failure(
                    "ownership_unverified",
                    "Cannot acquire owned Excel process handle",
                ));
            }
            let identity = Identity { handle, pid, ticks };
            let mut times: [FileTime; 4] = std::mem::zeroed();
            let ptr = times.as_mut_ptr();
            if GetProcessTimes(handle, ptr, ptr.add(1), ptr.add(2), ptr.add(3)) == 0 {
                return Err(failure(
                    "ownership_unverified",
                    "Cannot verify Excel creation time",
                ));
            }
            let actual = ((times[0].high as u64) << 32) | times[0].low as u64;
            if actual != ticks {
                return Err(failure(
                    "ownership_unverified",
                    "Excel process creation identity differs",
                ));
            }
            let mut image = [0_u16; 32768];
            let mut length = image.len() as u32;
            if QueryFullProcessImageNameW(handle, 0, image.as_mut_ptr(), &mut length) == 0 {
                return Err(failure(
                    "ownership_unverified",
                    "Cannot verify Excel process image",
                ));
            }
            let image = String::from_utf16_lossy(&image[..length as usize]);
            if !FilePath::new(&image)
                .file_name()
                .is_some_and(|name| name.to_string_lossy().eq_ignore_ascii_case("EXCEL.EXE"))
            {
                return Err(failure(
                    "ownership_unverified",
                    "Owned process is not Excel",
                ));
            }
            Ok(identity)
        }
    }
}
