//! Internal project records: immutable source revisions, accountable issues and
//! hash-bound review. This is not statutory sign-off or a multi-user CDE.
use super::api::{ProductState, Scope};
use crate::runtime_core::{CancellationToken, WorkspaceContext};
use axum::{
    body::Body,
    extract::{DefaultBodyLimit, Path, Query, State},
    http::{header, StatusCode},
    response::{IntoResponse, Response},
    routing::{get, post},
    Json, Router,
};
use rusqlite::{params, Connection, OptionalExtension};
use serde::Deserialize;
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::{
    io::{Read, Seek, SeekFrom, Write},
    sync::Arc,
};

const MAX_SOURCE: usize = 16 * 1024 * 1024;
const MAX_PACKAGE: u64 = 128 * 1024 * 1024;
type Error = (StatusCode, Json<Value>);
fn err(code: StatusCode, text: impl ToString) -> Error {
    (code, Json(json!({"detail":text.to_string()})))
}
fn bad(text: impl ToString) -> Error {
    err(StatusCode::BAD_REQUEST, text)
}
fn conflict(text: impl ToString) -> Error {
    err(StatusCode::CONFLICT, text)
}
fn internal(_: impl ToString) -> Error {
    err(
        StatusCode::INTERNAL_SERVER_ERROR,
        "Project record storage failed",
    )
}
fn now() -> String {
    chrono::Utc::now().to_rfc3339()
}
fn id() -> String {
    uuid::Uuid::new_v4().simple().to_string()
}
fn text(value: &str, max: usize) -> Result<String, Error> {
    let v = value.trim();
    if v.is_empty()
        || v.chars().count() > max
        || v.chars().any(|c| c.is_control() && c != '\n' && c != '\t')
    {
        return Err(bad(
            "A required field is empty, too long or contains control characters",
        ));
    }
    Ok(v.into())
}

pub fn initialize(db: &Connection) -> Result<(), String> {
    db.execute_batch("CREATE TABLE IF NOT EXISTS project_documents(workspace TEXT NOT NULL,id TEXT NOT NULL,record TEXT NOT NULL,PRIMARY KEY(workspace,id));
      CREATE TABLE IF NOT EXISTS project_revisions(workspace TEXT NOT NULL,document_id TEXT NOT NULL,revision INTEGER NOT NULL,record TEXT NOT NULL,payload BLOB NOT NULL,PRIMARY KEY(workspace,document_id,revision));
      CREATE TABLE IF NOT EXISTS project_issues(workspace TEXT NOT NULL,id TEXT NOT NULL,record TEXT NOT NULL,PRIMARY KEY(workspace,id));
      CREATE TABLE IF NOT EXISTS project_events(seq INTEGER PRIMARY KEY AUTOINCREMENT,workspace TEXT NOT NULL,record TEXT NOT NULL);
      CREATE INDEX IF NOT EXISTS project_events_scope ON project_events(workspace,seq);
      CREATE TABLE IF NOT EXISTS artifact_readiness(workspace TEXT NOT NULL,id TEXT NOT NULL,record TEXT NOT NULL,PRIMARY KEY(workspace,id));
      UPDATE project_documents SET record=json_set(record,'$.review_version',0) WHERE json_type(record,'$.review_version') IS NULL;")
      .map_err(|e| e.to_string())
}

pub fn router(state: Arc<ProductState>) -> Router {
    Router::new()
        .route("/api/project-control", get(overview))
        .route("/api/project-control/documents", post(register_document))
        .route(
            "/api/project-control/documents/{id}/review",
            post(review_document),
        )
        .route(
            "/api/project-control/documents/{id}/revisions",
            get(revisions),
        )
        .route(
            "/api/project-control/documents/{id}/revisions/{revision}",
            get(snapshot),
        )
        .route("/api/project-control/issues", post(save_issue))
        .route("/api/project-control/handoff", get(handoff))
        .route("/api/project-control/package", get(package))
        .route(
            "/api/agent/artifacts/{id}/readiness",
            post(inspect_artifact),
        )
        .route("/api/agent/artifacts/{id}/preview", get(preview_artifact))
        .layer(DefaultBodyLimit::max(64 * 1024))
        .with_state(state)
}

fn read_source(ws: &WorkspaceContext, relative: &str) -> Result<Vec<u8>, Error> {
    let path = ws.resolve_read(relative).map_err(bad)?;
    let file = std::fs::File::open(path).map_err(|_| bad("Source file is not readable"))?;
    if file.metadata().map_err(bad)?.len() > MAX_SOURCE as u64 {
        return Err(bad("Source exceeds 16 MiB"));
    }
    let mut bytes = Vec::new();
    file.take((MAX_SOURCE + 1) as u64)
        .read_to_end(&mut bytes)
        .map_err(bad)?;
    if bytes.len() > MAX_SOURCE {
        return Err(bad("Source exceeds 16 MiB"));
    }
    Ok(bytes)
}
fn document(db: &Connection, workspace: &str, doc_id: &str) -> Result<Value, Error> {
    let raw: Option<String> = db
        .query_row(
            "SELECT record FROM project_documents WHERE workspace=?1 AND id=?2",
            params![workspace, doc_id],
            |r| r.get(0),
        )
        .optional()
        .map_err(internal)?;
    serde_json::from_str(&raw.ok_or_else(|| {
        err(
            StatusCode::NOT_FOUND,
            "Document not found in this workspace",
        )
    })?)
    .map_err(internal)
}
fn records(db: &Connection, sql: &str, workspace: &str) -> Result<Vec<Value>, Error> {
    let mut stmt = db.prepare(sql).map_err(internal)?;
    let rows = stmt
        .query_map([workspace], |r| r.get::<_, String>(0))
        .map_err(internal)?;
    rows.map(|r| serde_json::from_str(&r.map_err(internal)?).map_err(internal))
        .collect()
}
fn audit(
    db: &Connection,
    workspace: &str,
    actor: &str,
    kind: &str,
    entity: &str,
    details: Value,
) -> Result<(), Error> {
    let event =
        json!({"at":now(),"actor_id":actor,"kind":kind,"entity_id":entity,"details":details});
    db.execute(
        "INSERT INTO project_events(workspace,record) VALUES(?1,?2)",
        params![workspace, event.to_string()],
    )
    .map_err(internal)?;
    Ok(())
}
fn integrity(ws: &WorkspaceContext, doc: &Value) -> &'static str {
    match read_source(ws, doc["path"].as_str().unwrap_or("")) {
        Ok(bytes) if super::tools::sha256(&bytes) == doc["sha256"] => "current",
        Ok(_) => "changed",
        Err(_) => "unavailable",
    }
}
fn require_current(ws: &WorkspaceContext, doc: &Value) -> Result<(), Error> {
    if integrity(ws, doc) != "current" {
        return Err(conflict(
            "Source changed or is unavailable; register its current revision before review",
        ));
    }
    Ok(())
}
fn next_review_version(doc: &Value) -> Result<u64, Error> {
    doc["review_version"]
        .as_u64()
        .unwrap_or(0)
        .checked_add(1)
        .ok_or_else(|| conflict("Document review version is exhausted"))
}
async fn blocking<T: Send + 'static>(
    f: impl FnOnce() -> Result<T, Error> + Send + 'static,
) -> Result<T, Error> {
    tokio::task::spawn_blocking(f).await.map_err(internal)?
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct RegisterDocument {
    workspace: String,
    document_id: Option<String>,
    expected_revision: Option<u64>,
    path: String,
    title: String,
    discipline: String,
    revision_label: String,
}
async fn register_document(
    State(st): State<Arc<ProductState>>,
    Json(req): Json<RegisterDocument>,
) -> Result<Json<Value>, Error> {
    blocking(move || {
        let ws = st.workspace(&req.workspace).map_err(bad)?;
        let title = text(&req.title, 160)?;
        let discipline = text(&req.discipline, 80)?;
        let label = text(&req.revision_label, 80)?;
        let source = text(&req.path, 1024)?;
        // Persist one portable spelling. Windows treats backslashes as path
        // separators, and Path::components also normalizes repeated separators;
        // neither alias may bypass sensitive-name or duplicate-path checks.
        if source.split('/').any(invalid_package_part) {
            return Err(bad("Project source paths must be relative, use '/' separators and contain no empty or dot components"));
        }
        if sensitive_source_name(&source) { return Err(bad("Instance credentials and state files cannot be registered as project documents")); }
        let ext = std::path::Path::new(&source).extension().and_then(|e| e.to_str()).unwrap_or("").to_ascii_lowercase();
        if !matches!(ext.as_str(), "pdf"|"docx"|"xlsx"|"csv"|"txt"|"md"|"json"|"ifc"|"dxf") { return Err(bad("Unsupported project source format")); }
        let bytes = read_source(&ws, &source)?;
        let hash = super::tools::sha256(&bytes);
        let mut db = st.index.lock().map_err(internal)?;
        let tx = db.transaction().map_err(internal)?;
        let stored_bytes: i64 = tx.query_row("SELECT coalesce(sum(length(payload)),0) FROM project_revisions WHERE workspace=?1",[&req.workspace],|r|r.get(0)).map_err(internal)?;
        if stored_bytes.saturating_add(bytes.len() as i64)>512*1024*1024 { return Err(bad("Project revision snapshots exceed the 512 MiB workspace limit")); }
        let registered = records(&tx,"SELECT record FROM project_documents WHERE workspace=?1",&req.workspace)?;
        if registered.iter().any(|d|d["path"]==source && d["id"].as_str()!=req.document_id.as_deref()) { return Err(conflict("This path is already registered to another document")); }
        let (doc_id, revision, review_version) = if let Some(ref doc_id) = req.document_id {
            let old = document(&tx, &req.workspace, doc_id)?;
            if old["revision"].as_u64().unwrap_or(0)>=256 { return Err(bad("A controlled document supports up to 256 revisions; preserve its history before starting a new document")); }
            if req.expected_revision != old["revision"].as_u64() { return Err(conflict("Document revision changed; reload before saving")); }
            if old["sha256"] == hash && old["path"] == source && old["revision_label"] == label { return Err(conflict("This exact revision is already registered")); }
            (doc_id.clone(), old["revision"].as_u64().unwrap_or(0) + 1, next_review_version(&old)?)
        } else {
            if req.expected_revision.is_some() { return Err(bad("A new document cannot have an expected revision")); }
            let count: i64 = tx.query_row("SELECT count(*) FROM project_documents WHERE workspace=?1", [&req.workspace], |r| r.get(0)).map_err(internal)?;
            if count >= 100 { return Err(bad("This workspace supports up to 100 controlled documents")); }
            (id(), 1, 1)
        };
        let revision = i64::try_from(revision).map_err(|_|bad("Revision is out of range"))?;
        let doc = json!({"id":doc_id,"workspace":req.workspace,"title":title,"discipline":discipline,"path":source,
            "revision":revision,"review_version":review_version,"revision_label":label,"sha256":hash,"status":"work_in_progress","updated_at":now(),"actor_id":st.auth.owner()});
        tx.execute("INSERT INTO project_revisions(workspace,document_id,revision,record,payload) VALUES(?1,?2,?3,?4,?5)", params![req.workspace,doc_id,revision,doc.to_string(),bytes]).map_err(internal)?;
        tx.execute("INSERT OR REPLACE INTO project_documents(workspace,id,record) VALUES(?1,?2,?3)", params![req.workspace,doc_id,doc.to_string()]).map_err(internal)?;
        // Retain the original evidence revision. A person must explicitly rebase
        // and resolve the issue after examining the new source.
        let issues = records(&tx, "SELECT record FROM project_issues WHERE workspace=?1", &req.workspace)?;
        for mut issue in issues {
            if issue["document_id"] == doc_id && issue["status"] == "resolved" {
                issue["status"] = json!("review_required");
                issue["version"] = json!(issue["version"].as_u64().unwrap_or(0) + 1);
                issue["updated_at"] = json!(now());
                tx.execute("UPDATE project_issues SET record=?1 WHERE workspace=?2 AND id=?3", params![issue.to_string(),req.workspace,issue["id"].as_str()]).map_err(internal)?;
                audit(&tx,&req.workspace,st.auth.owner(),"issue_reopened",issue["id"].as_str().unwrap_or(""),json!({"new_revision":revision,"previous_evidence_revision":issue["document_revision"]}))?;
            }
        }
        audit(&tx,&req.workspace,st.auth.owner(),"document_revision_registered",&doc_id,doc.clone())?;
        tx.commit().map_err(internal)?;
        Ok(Json(doc))
    }).await
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Review {
    workspace: String,
    expected_revision: u64,
    expected_review_version: u64,
    notes: String,
}
async fn review_document(
    State(st): State<Arc<ProductState>>,
    Path(doc_id): Path<String>,
    Json(req): Json<Review>,
) -> Result<Json<Value>, Error> {
    blocking(move || {
        let ws = st.workspace(&req.workspace).map_err(bad)?;
        let notes = text(&req.notes, 4000)?;
        let mut db = st.index.lock().map_err(internal)?;
        let tx = db.transaction().map_err(internal)?;
        let mut doc = document(&tx, &req.workspace, &doc_id)?;
        if doc["revision"] != req.expected_revision { return Err(conflict("Document revision changed; reload before review")); }
        if doc["review_version"] != req.expected_review_version { return Err(conflict("Document issues or review evidence changed; reload and review the current record before saving")); }
        require_current(&ws, &doc)?;
        if records(&tx, "SELECT record FROM project_issues WHERE workspace=?1", &req.workspace)?.iter().any(|i| i["document_id"] == doc_id && i["status"] != "resolved") {
            return Err(conflict("Resolve the document's outstanding issues before internal review"));
        }
        doc["status"] = json!("internally_reviewed");
        doc["review"] = json!({"actor_id":st.auth.owner(),"at":now(),"notes":notes,"sha256":doc["sha256"],"basis_version":doc["review_version"],"statutory_signoff":false});
        tx.execute("UPDATE project_documents SET record=?1 WHERE workspace=?2 AND id=?3", params![doc.to_string(),req.workspace,doc_id]).map_err(internal)?;
        audit(&tx,&req.workspace,st.auth.owner(),"document_internal_review",&doc_id,doc["review"].clone())?;
        tx.commit().map_err(internal)?;
        Ok(Json(doc))
    }).await
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct IssueInput {
    workspace: String,
    id: Option<String>,
    expected_version: Option<u64>,
    document_id: String,
    document_revision: u64,
    kind: String,
    title: String,
    description: String,
    assignee: String,
    due_date: String,
    status: String,
    resolution: String,
}
async fn save_issue(
    State(st): State<Arc<ProductState>>,
    Json(req): Json<IssueInput>,
) -> Result<Json<Value>, Error> {
    blocking(move || {
        let ws = st.workspace(&req.workspace).map_err(bad)?;
        if !matches!(req.kind.as_str(),"rfi"|"quality"|"change"|"task") || !matches!(req.status.as_str(),"open"|"in_progress"|"resolved") { return Err(bad("Unsupported issue kind or status")); }
        let title = text(&req.title,160)?;
        let description = text(&req.description,8000)?;
        let assignee = text(&req.assignee,100)?;
        if !req.due_date.is_empty() {
            let date = chrono::NaiveDate::parse_from_str(&req.due_date,"%Y-%m-%d").map_err(|_|bad("Due date must use YYYY-MM-DD"))?;
            if date.format("%Y-%m-%d").to_string() != req.due_date { return Err(bad("Due date must use YYYY-MM-DD")); }
        }
        let resolution = if req.status == "resolved" { text(&req.resolution,8000)? } else { req.resolution.trim().to_owned() };
        if resolution.chars().count()>8000 { return Err(bad("Resolution is too long")); }
        let mut db = st.index.lock().map_err(internal)?;
        let tx = db.transaction().map_err(internal)?;
        let mut doc = document(&tx,&req.workspace,&req.document_id)?;
        if doc["revision"] != req.document_revision { return Err(conflict("Issue evidence revision changed; reload and review the current source")); }
        require_current(&ws, &doc)?;
        let (issue_id, version, created_at) = if let Some(ref issue_id) = req.id {
            let raw: Option<String> = tx.query_row("SELECT record FROM project_issues WHERE workspace=?1 AND id=?2",params![req.workspace,issue_id],|r|r.get(0)).optional().map_err(internal)?;
            let old: Value = serde_json::from_str(&raw.ok_or_else(||err(StatusCode::NOT_FOUND,"Issue not found"))?).map_err(internal)?;
            if req.expected_version != old["version"].as_u64() { return Err(conflict("Issue was changed; reload before saving")); }
            if old["document_id"] != req.document_id { return Err(bad("An issue's document cannot be replaced")); }
            (issue_id.clone(),old["version"].as_u64().unwrap_or(0)+1,old["created_at"].clone())
        } else {
            if req.expected_version.is_some() || req.status != "open" { return Err(bad("New issues must start open")); }
            let count: i64 = tx.query_row("SELECT count(*) FROM project_issues WHERE workspace=?1",[&req.workspace],|r|r.get(0)).map_err(internal)?;
            if count>=2000 { return Err(bad("This workspace supports up to 2000 issues")); }
            (id(),1,json!(now()))
        };
        let issue = json!({"id":issue_id,"version":version,"workspace":req.workspace,"document_id":req.document_id,
            "document_revision":req.document_revision,"source_sha256":doc["sha256"],"kind":req.kind,"title":title,
            "description":description,"assignee":assignee,"due_date":req.due_date,"status":req.status,"resolution":resolution,
            "created_at":created_at,"updated_at":now(),"actor_id":st.auth.owner(),"notification_sent":false});
        tx.execute("INSERT OR REPLACE INTO project_issues(workspace,id,record) VALUES(?1,?2,?3)",params![req.workspace,issue_id,issue.to_string()]).map_err(internal)?;
        // Any new issue or changed resolution invalidates the document's prior
        // review. Closing an issue is not itself a document acceptance.
        doc["status"] = json!("work_in_progress");
        doc["review_version"] = json!(next_review_version(&doc)?);
        tx.execute("UPDATE project_documents SET record=?1 WHERE workspace=?2 AND id=?3",params![doc.to_string(),req.workspace,req.document_id]).map_err(internal)?;
        audit(&tx,&req.workspace,st.auth.owner(),"issue_saved",&issue_id,issue.clone())?;
        tx.commit().map_err(internal)?;
        Ok(Json(issue))
    }).await
}

fn overview_sync(st: &ProductState, workspace: &str, complete: bool) -> Result<Value, Error> {
    let ws = st.workspace(workspace).map_err(bad)?;
    let (mut docs, issues, events) = {
        let db = st.index.lock().map_err(internal)?;
        (
            records(
                &db,
                "SELECT record FROM project_documents WHERE workspace=?1 ORDER BY rowid DESC",
                workspace,
            )?,
            records(
                &db,
                "SELECT record FROM project_issues WHERE workspace=?1 ORDER BY rowid DESC",
                workspace,
            )?,
            records(
                &db,
                "SELECT record FROM project_events WHERE workspace=?1 ORDER BY seq DESC LIMIT 200",
                workspace,
            )?,
        )
    };
    // The explicit full check covers the entire snapshot quota. Background
    // callers may request a smaller refresh; skipped files never imply current.
    let mut remaining = if complete { 512 } else { 64 } * 1024 * 1024u64;
    for doc in &mut docs {
        let size = ws
            .resolve_read(doc["path"].as_str().unwrap_or(""))
            .ok()
            .and_then(|p| std::fs::metadata(p).ok())
            .map(|m| m.len())
            .unwrap_or(0);
        let status = if size > remaining {
            "not_checked"
        } else {
            remaining = remaining.saturating_sub(size);
            integrity(&ws, doc)
        };
        doc["source_status"] = json!(status);
        doc["effective_status"] = if status == "current" {
            doc["status"].clone()
        } else {
            json!("review_required")
        };
    }
    let unresolved = issues.iter().filter(|i| i["status"] != "resolved").count();
    let ready = !docs.is_empty()
        && unresolved == 0
        && docs
            .iter()
            .all(|d| d["source_status"] == "current" && d["status"] == "internally_reviewed");
    Ok(
        json!({"workspace":workspace,"documents":docs,"issues":issues,"events":events,"events_limit":200,
        "internal_review_complete":ready,"unresolved_issues":unresolved,"statutory_signoff":false,
        "checked_at":now(),"complete_source_check":complete,"scope":"single_user_instance","notifications_enabled":false}),
    )
}
#[derive(Deserialize)]
struct OverviewScope {
    workspace: String,
    #[serde(default)]
    complete: bool,
}
async fn overview(
    State(st): State<Arc<ProductState>>,
    Query(q): Query<OverviewScope>,
) -> Result<Json<Value>, Error> {
    blocking(move || overview_sync(&st, &q.workspace, q.complete).map(Json)).await
}
async fn handoff(
    State(st): State<Arc<ProductState>>,
    Query(q): Query<Scope>,
) -> Result<Response, Error> {
    blocking(move || {
        let mut report = overview_sync(&st,&q.workspace,true)?;
        report["schema_version"] = json!(1);
        report["export_kind"] = json!("internal_project_review_record");
        report["exported_by"] = json!(st.auth.owner());
        report["disclaimer"] = json!("Internal review record only. It does not authorize tender submission, construction or shipment. Source snapshots remain separately downloadable by revision.");
        let bytes = serde_json::to_vec_pretty(&report).map_err(internal)?;
        Ok(([(header::CONTENT_TYPE,"application/json"),(header::CONTENT_DISPOSITION,"attachment; filename=project-review.json"),(header::CACHE_CONTROL,"no-store"),(header::X_CONTENT_TYPE_OPTIONS,"nosniff")],bytes).into_response())
    }).await
}

fn package_limit() -> Error {
    err(StatusCode::PAYLOAD_TOO_LARGE,
        "Project handoff exceeds the 128 MiB export limit. Download the registered revisions separately with the JSON handoff record; history has not been deleted.")
}

// Spool a bounded archive instead of retaining all source BLOBs and the ZIP in
// memory together. Dropping the HTTP body also drops and removes this file.
struct PackageFile {
    file: Option<std::fs::File>,
    path: std::path::PathBuf,
    position: u64,
    length: u64,
}
impl PackageFile {
    fn create() -> Result<Self, Error> {
        let path = std::env::temp_dir().join(format!("civil-project-handoff-{}.zip", id()));
        let mut options = std::fs::OpenOptions::new();
        options.read(true).write(true).create_new(true);
        #[cfg(unix)]
        {
            use std::os::unix::fs::OpenOptionsExt;
            options.mode(0o600);
        }
        let file = options.open(&path).map_err(internal)?;
        Ok(Self {
            file: Some(file),
            path,
            position: 0,
            length: 0,
        })
    }
}
impl Drop for PackageFile {
    fn drop(&mut self) {
        drop(self.file.take());
        let _ = std::fs::remove_file(&self.path);
    }
}
impl Read for PackageFile {
    fn read(&mut self, bytes: &mut [u8]) -> std::io::Result<usize> {
        let n = self.file.as_mut().unwrap().read(bytes)?;
        self.position += n as u64;
        Ok(n)
    }
}
impl Write for PackageFile {
    fn write(&mut self, bytes: &[u8]) -> std::io::Result<usize> {
        if self.position.saturating_add(bytes.len() as u64) > MAX_PACKAGE {
            return Err(std::io::Error::other("project_package_size_limit"));
        }
        let n = self.file.as_mut().unwrap().write(bytes)?;
        self.position += n as u64;
        self.length = self.length.max(self.position);
        Ok(n)
    }
    fn flush(&mut self) -> std::io::Result<()> {
        self.file.as_mut().unwrap().flush()
    }
}
impl Seek for PackageFile {
    fn seek(&mut self, from: SeekFrom) -> std::io::Result<u64> {
        let target = match from {
            SeekFrom::Start(n) => n as i128,
            SeekFrom::End(n) => self.length as i128 + n as i128,
            SeekFrom::Current(n) => self.position as i128 + n as i128,
        };
        if target < 0 || target > MAX_PACKAGE as i128 {
            return Err(std::io::Error::other("project_package_size_limit"));
        }
        self.position = self
            .file
            .as_mut()
            .unwrap()
            .seek(SeekFrom::Start(target as u64))?;
        Ok(self.position)
    }
}
fn package_io(error: impl ToString) -> Error {
    if error.to_string().contains("project_package_size_limit") {
        package_limit()
    } else {
        internal(error)
    }
}
fn sensitive_source_name(source: &str) -> bool {
    // Historical records may predate the canonical '/' registration rule.
    source.split(['/', '\\']).any(|part| {
        let name = part.to_ascii_lowercase();
        matches!(
            name.as_str(),
            ".git"
                | ".ssh"
                | ".env"
                | "login-token.txt"
                | "token.txt"
                | "credentials.json"
                | "secrets.json"
                | "instance-owner.sqlite"
                | "index.sqlite"
                | "runtime.sqlite"
                | "runtime-owner.lock"
        ) || name.starts_with(".env.")
    })
}
fn invalid_package_part(part: &str) -> bool {
    let stem = part.split('.').next().unwrap_or("").to_ascii_lowercase();
    let device = matches!(stem.as_str(), "con" | "prn" | "aux" | "nul")
        || (stem.len() == 4
            && (stem.starts_with("com") || stem.starts_with("lpt"))
            && matches!(stem.as_bytes()[3], b'1'..=b'9'));
    part.is_empty()
        || part == "."
        || part == ".."
        || part.ends_with(['.', ' '])
        || device
        || part
            .chars()
            .any(|c| c.is_control() || "\\:<>\"|?*".contains(c))
}
fn package_path(doc: &Value) -> Result<String, Error> {
    let key = doc["id"].as_str().unwrap_or("");
    let revision = doc["revision"]
        .as_u64()
        .filter(|n| *n > 0 && *n <= 256)
        .ok_or_else(|| {
            conflict("Invalid registered revision; repair the project record before exporting")
        })?;
    let source = doc["path"].as_str().unwrap_or("");
    if key.len() != 32
        || !key.bytes().all(|c| c.is_ascii_hexdigit())
        || source.is_empty()
        || source.len() > 4096
        || source.split('/').any(invalid_package_part)
    {
        return Err(conflict(
            "Invalid registered source path; repair the project record before exporting",
        ));
    }
    let name = source.rsplit('/').next().unwrap();
    let lower = name.to_ascii_lowercase();
    let extension = lower.rsplit('.').next().unwrap_or("");
    if sensitive_source_name(source)
        || !matches!(
            extension,
            "pdf" | "docx" | "xlsx" | "csv" | "txt" | "md" | "json" | "ifc" | "dxf"
        )
    {
        return Err(conflict("A registered source has a credential or instance-state filename and cannot be included in a handoff. Register a sanitized project document revision before exporting."));
    }
    Ok(format!("sources/{key}/revision-{revision}/{name}"))
}

// Bound metadata before materializing it. Issue and audit rows are processed
// one at a time; only the small, quota-limited document list stays in memory.
fn package_preflight(db: &Connection, workspace: &str) -> Result<(), Error> {
    let mut total = 0u64;
    for (table, max_row, max_total, max_count) in [
        ("project_documents", 64 * 1024, 2 * 1024 * 1024, 100),
        ("project_issues", 128 * 1024, MAX_PACKAGE, 2000),
        ("project_events", 256 * 1024, MAX_PACKAGE, i64::MAX),
    ] {
        let sql = format!("SELECT count(*),coalesce(sum(length(CAST(record AS BLOB))),0),coalesce(max(length(CAST(record AS BLOB))),0) FROM {table} WHERE workspace=?1");
        let (count, size, largest): (i64, i64, i64) = db
            .query_row(&sql, [workspace], |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?)))
            .map_err(internal)?;
        let size = u64::try_from(size).map_err(internal)?;
        let largest = u64::try_from(largest).map_err(internal)?;
        if count > max_count || largest > max_row || size > max_total {
            return Err(package_limit());
        }
        total = total.saturating_add(size);
    }
    let (size, largest): (i64,i64) = db.query_row(
        "SELECT coalesce(sum(length(r.payload)),0),coalesce(max(length(r.payload)),0) FROM project_documents d JOIN project_revisions r ON r.workspace=d.workspace AND r.document_id=d.id AND r.revision=json_extract(d.record,'$.revision') WHERE d.workspace=?1",
        [workspace],|r|Ok((r.get(0)?,r.get(1)?))).map_err(internal)?;
    let size = u64::try_from(size).map_err(internal)?;
    let largest = u64::try_from(largest).map_err(internal)?;
    if largest > MAX_SOURCE as u64 || total.saturating_add(size) > MAX_PACKAGE {
        return Err(package_limit());
    }
    Ok(())
}

fn package_sync(st: &ProductState, workspace: &str) -> Result<PackageFile, Error> {
    let ws = st.workspace(workspace).map_err(bad)?;
    let mut db = st.index.lock().map_err(internal)?;
    let tx = db.transaction().map_err(internal)?;
    // The first SELECT establishes the read snapshot. No source content is
    // reread for payload: the package always uses the exact registered BLOB.
    package_preflight(&tx, workspace)?;
    let cutoff = now();
    let mut docs = records(
        &tx,
        "SELECT record FROM project_documents WHERE workspace=?1 ORDER BY id",
        workspace,
    )?;
    let options = zip::write::SimpleFileOptions::default()
        .compression_method(zip::CompressionMethod::Stored)
        .unix_permissions(0o444);
    let mut archive = zip::ZipWriter::new(PackageFile::create()?);
    for doc in &mut docs {
        let path = package_path(doc)?;
        let row: Option<(String,Vec<u8>)> = tx.query_row(
            "SELECT record,payload FROM project_revisions WHERE workspace=?1 AND document_id=?2 AND revision=?3 AND length(CAST(record AS BLOB))<=65536 AND length(payload)<=?4",
            params![workspace,doc["id"].as_str(),doc["revision"].as_i64(),MAX_SOURCE as i64], |r|Ok((r.get(0)?,r.get(1)?))).optional().map_err(internal)?;
        let (raw,bytes) = row.ok_or_else(||conflict("Registered snapshot is missing or invalid; repair the project record before exporting"))?;
        let stored: Value = serde_json::from_str(&raw).map_err(internal)?;
        if doc["workspace"] != workspace
            || [
                "id",
                "workspace",
                "path",
                "revision",
                "revision_label",
                "title",
                "discipline",
                "sha256",
            ]
            .iter()
            .any(|field| doc[*field] != stored[*field])
            || super::tools::sha256(&bytes) != doc["sha256"]
        {
            return Err(conflict("Stored revision failed its integrity check; restore a verified snapshot before exporting"));
        }
        archive.start_file(&path, options).map_err(package_io)?;
        archive.write_all(&bytes).map_err(package_io)?;
        doc["snapshot"] = json!({"archive_path":path,"bytes":bytes.len(),"sha256":doc["sha256"],"revision":doc["revision"],"read_only":true});
        drop(bytes);
        let source_status = integrity(&ws, doc);
        doc["source_status"] = json!(source_status);
        doc["source_checked_at"] = json!(now());
        let reviewed = doc["status"] == "internally_reviewed"
            && doc["review"]["sha256"] == doc["sha256"]
            && doc["review"]["statutory_signoff"] == false;
        doc["effective_status"] = if source_status == "current" && reviewed {
            json!("internally_reviewed")
        } else {
            json!("review_required")
        };
    }
    let mut unresolved = 0usize;
    let mut issue_count = 0usize;
    {
        let mut stmt = tx
            .prepare("SELECT record FROM project_issues WHERE workspace=?1 ORDER BY id")
            .map_err(internal)?;
        let mut rows = stmt.query([workspace]).map_err(internal)?;
        while let Some(row) = rows.next().map_err(internal)? {
            let raw: String = row.get(0).map_err(internal)?;
            let issue: Value = serde_json::from_str(&raw).map_err(internal)?;
            if issue["workspace"] != workspace {
                return Err(conflict("An issue has inconsistent workspace metadata"));
            }
            issue_count += 1;
            if issue["status"] != "resolved" {
                unresolved += 1;
            }
        }
    }
    archive
        .start_file("audit.jsonl", options)
        .map_err(package_io)?;
    let mut audit_hash = Sha256::new();
    let mut event_count = 0usize;
    {
        let mut stmt = tx
            .prepare("SELECT seq,record FROM project_events WHERE workspace=?1 ORDER BY seq")
            .map_err(internal)?;
        let mut rows = stmt.query([workspace]).map_err(internal)?;
        while let Some(row) = rows.next().map_err(internal)? {
            let raw: String = row.get(1).map_err(internal)?;
            let mut event: Value = serde_json::from_str(&raw).map_err(internal)?;
            if !event.is_object() {
                return Err(conflict("Invalid project audit record"));
            }
            event["sequence"] = json!(row.get::<_, i64>(0).map_err(internal)?);
            let line = serde_json::to_vec(&event).map_err(internal)?;
            archive.write_all(&line).map_err(package_io)?;
            archive.write_all(b"\n").map_err(package_io)?;
            audit_hash.update(&line);
            audit_hash.update(b"\n");
            event_count += 1;
        }
    }
    let ready = !docs.is_empty()
        && unresolved == 0
        && docs
            .iter()
            .all(|d| d["effective_status"] == "internally_reviewed");
    let mut manifest = json!({"schema_version":1,"export_kind":"internal_project_handoff","workspace":workspace,
        "cutoff_at":cutoff,"source_checks_completed_at":now(),"exported_by":st.auth.owner(),"scope":"single_user_instance",
        "issue_count":issue_count,"unresolved_issues":unresolved,
        "internal_review_complete":ready,"draft":!ready,"status":if ready {"internally_reviewed"} else {"draft_not_ready"},
        "statutory_signoff":false,"notifications_enabled":false,
        "audit":{"archive_path":"audit.jsonl","event_count":event_count,"sha256":format!("{:x}",audit_hash.finalize()),"complete":true},
        "exclusions":["unregistered files","previous revision payloads","instance databases","credentials","model configuration","runtime sessions"],
        "disclaimer":"Internal coordination handoff only. Source status was checked at each recorded time and can change later. Internal review does not authorize tender submission, construction or shipment. This package is not an instance backup."});
    manifest["documents"] = Value::Array(docs);
    archive
        .start_file("manifest.json", options)
        .map_err(package_io)?;
    // Stream potentially numerous issue descriptions without collecting them.
    archive.write_all(b"{").map_err(package_io)?;
    for (key, value) in manifest.as_object().unwrap() {
        serde_json::to_writer(&mut archive, key).map_err(package_io)?;
        archive.write_all(b":").map_err(package_io)?;
        serde_json::to_writer(&mut archive, value).map_err(package_io)?;
        archive.write_all(b",").map_err(package_io)?;
    }
    archive.write_all(b"\"issues\":[").map_err(package_io)?;
    {
        let mut stmt = tx
            .prepare("SELECT record FROM project_issues WHERE workspace=?1 ORDER BY id")
            .map_err(internal)?;
        let mut rows = stmt.query([workspace]).map_err(internal)?;
        let mut first = true;
        while let Some(row) = rows.next().map_err(internal)? {
            if !first {
                archive.write_all(b",").map_err(package_io)?;
            }
            first = false;
            let raw: String = row.get(0).map_err(internal)?;
            let issue: Value = serde_json::from_str(&raw).map_err(internal)?;
            serde_json::to_writer(&mut archive, &issue).map_err(package_io)?;
        }
    }
    archive.write_all(b"]}").map_err(package_io)?;
    let mut file = archive.finish().map_err(package_io)?;
    file.seek(SeekFrom::Start(0)).map_err(internal)?;
    Ok(file)
}
async fn package(
    State(st): State<Arc<ProductState>>,
    Query(q): Query<Scope>,
) -> Result<Response, Error> {
    let mut file = blocking(move || package_sync(&st, &q.workspace)).await?;
    let length = file.length;
    let (sender, receiver) = tokio::sync::mpsc::channel::<Result<Vec<u8>, std::io::Error>>(2);
    tokio::task::spawn_blocking(move || {
        let mut buffer = vec![0; 64 * 1024];
        loop {
            match file.read(&mut buffer) {
                Ok(0) => break,
                Ok(n) => {
                    if sender.blocking_send(Ok(buffer[..n].to_vec())).is_err() {
                        break;
                    }
                }
                Err(error) => {
                    let _ = sender.blocking_send(Err(error));
                    break;
                }
            }
        }
    });
    Ok((
        [
            (header::CONTENT_TYPE, "application/zip".to_string()),
            (
                header::CONTENT_DISPOSITION,
                "attachment; filename=project-handoff.zip".to_string(),
            ),
            (header::CONTENT_LENGTH, length.to_string()),
            (header::CACHE_CONTROL, "no-store".to_string()),
            (header::X_CONTENT_TYPE_OPTIONS, "nosniff".to_string()),
        ],
        Body::from_stream(tokio_stream::wrappers::ReceiverStream::new(receiver)),
    )
        .into_response())
}
async fn revisions(
    State(st): State<Arc<ProductState>>,
    Path(doc_id): Path<String>,
    Query(q): Query<Scope>,
) -> Result<Json<Value>, Error> {
    st.workspace(&q.workspace).map_err(bad)?;
    let db = st.index.lock().map_err(internal)?;
    document(&db, &q.workspace, &doc_id)?;
    let mut stmt = db.prepare("SELECT record FROM project_revisions WHERE workspace=?1 AND document_id=?2 ORDER BY revision DESC").map_err(internal)?;
    let rows = stmt
        .query_map(params![q.workspace, doc_id], |r| r.get::<_, String>(0))
        .map_err(internal)?;
    let values = rows
        .map(|r| serde_json::from_str::<Value>(&r.map_err(internal)?).map_err(internal))
        .collect::<Result<Vec<_>, _>>()?;
    Ok(Json(json!({"revisions":values})))
}
async fn snapshot(
    State(st): State<Arc<ProductState>>,
    Path((doc_id, revision)): Path<(String, u64)>,
    Query(q): Query<Scope>,
) -> Result<Response, Error> {
    blocking(move || {
        let revision = i64::try_from(revision).map_err(|_|bad("Revision is out of range"))?;
        st.workspace(&q.workspace).map_err(bad)?;
        let db = st.index.lock().map_err(internal)?;
        let row: Option<(String,Vec<u8>)> = db.query_row("SELECT record,payload FROM project_revisions WHERE workspace=?1 AND document_id=?2 AND revision=?3",params![q.workspace,doc_id,revision],|r|Ok((r.get(0)?,r.get(1)?))).optional().map_err(internal)?;
        let (raw,bytes) = row.ok_or_else(||err(StatusCode::NOT_FOUND,"Revision not found"))?;
        let record: Value = serde_json::from_str(&raw).map_err(internal)?;
        if super::tools::sha256(&bytes)!=record["sha256"] { return Err(conflict("Stored revision failed its integrity check")); }
        let ext = std::path::Path::new(record["path"].as_str().unwrap_or("source")).extension().and_then(|e|e.to_str()).unwrap_or("bin");
        Ok(([(header::CONTENT_TYPE,"application/octet-stream".to_string()),(header::CONTENT_DISPOSITION,format!("attachment; filename=revision-{revision}.{ext}")),(header::CACHE_CONTROL,"no-store".to_string()),(header::X_CONTENT_TYPE_OPTIONS,"nosniff".to_string())],bytes).into_response())
    }).await
}

fn artifact_info(
    st: &ProductState,
    workspace: &str,
    artifact_id: &str,
) -> Result<(WorkspaceContext, String, Value), Error> {
    let ws = st.workspace(workspace).map_err(bad)?;
    let (path, raw): (String, String) = st
        .index
        .lock()
        .map_err(internal)?
        .query_row(
            "SELECT path,record FROM artifacts WHERE workspace=?1 AND id=?2",
            params![workspace, artifact_id],
            |r| Ok((r.get(0)?, r.get(1)?)),
        )
        .map_err(|_| err(StatusCode::NOT_FOUND, "Artifact not found"))?;
    if !ws
        .resolve_read(&path)
        .map_err(bad)?
        .starts_with(ws.output_root())
    {
        return Err(bad("Invalid artifact path"));
    }
    let record: Value = serde_json::from_str(&raw).map_err(internal)?;
    if super::tools::sha256(&read_source(&ws, &path)?) != record["output_sha256"] {
        return Err(conflict("Artifact changed after registration"));
    }
    Ok((ws, path, record))
}
async fn inspect_artifact(
    State(st): State<Arc<ProductState>>,
    Path(artifact_id): Path<String>,
    Query(q): Query<Scope>,
) -> Result<Json<Value>, Error> {
    let (ws, path, record) = artifact_info(&st, &q.workspace, &artifact_id)?;
    let response = st
        .worker
        .document(
            &ws,
            "inspect_readiness",
            &path,
            record["output_sha256"].as_str(),
            json!({}),
            &CancellationToken::new(),
        )
        .await
        .map_err(bad)?;
    if response["ok"] != true {
        return Err(bad(response["error"].to_string()));
    }
    let mut result = response["result"].clone();
    if result["source_sha256"] != record["output_sha256"] {
        return Err(conflict("Readiness result is not bound to this artifact"));
    }
    // Recheck after the worker. A report may not authorize preview of later bytes.
    artifact_info(&st, &q.workspace, &artifact_id)?;
    result["inspected_by"] = json!(st.auth.owner());
    result["inspected_at"] = json!(now());
    result["artifact_id"] = json!(artifact_id);
    result["original_source_status"] =
        match (record["source"].as_str(), record["source_sha256"].as_str()) {
            (Some(source), Some(expected)) => json!(match read_source(&ws, source) {
                Ok(bytes) if super::tools::sha256(&bytes) == expected => "current",
                Ok(_) => "changed",
                Err(_) => "unavailable",
            }),
            _ => json!("not_recorded"),
        };
    st.index
        .lock()
        .map_err(internal)?
        .execute(
            "INSERT OR REPLACE INTO artifact_readiness(workspace,id,record) VALUES(?1,?2,?3)",
            params![q.workspace, artifact_id, result.to_string()],
        )
        .map_err(internal)?;
    Ok(Json(result))
}
async fn preview_artifact(
    State(st): State<Arc<ProductState>>,
    Path(artifact_id): Path<String>,
    Query(q): Query<Scope>,
) -> Result<Response, Error> {
    blocking(move || {
        let (ws, path, record) = artifact_info(&st, &q.workspace, &artifact_id)?;
        let raw: String = st
            .index
            .lock()
            .map_err(internal)?
            .query_row(
                "SELECT record FROM artifact_readiness WHERE workspace=?1 AND id=?2",
                params![q.workspace, artifact_id],
                |r| r.get(0),
            )
            .map_err(|_| bad("Run readiness inspection before PDF preview"))?;
        let report: Value = serde_json::from_str(&raw).map_err(internal)?;
        if report["source_sha256"] != record["output_sha256"]
            || report["format"] != "pdf"
            || report["readiness"]["preview"]["eligible"] != true
        {
            return Err(bad(
                "This artifact is not eligible for a native PDF preview",
            ));
        }
        let bytes = read_source(&ws, &path)?;
        if super::tools::sha256(&bytes) != record["output_sha256"] {
            return Err(conflict("Artifact changed after inspection"));
        }
        Ok((
            [
                (header::CONTENT_TYPE, "application/pdf"),
                (header::CONTENT_DISPOSITION, "inline; filename=review.pdf"),
                (header::CACHE_CONTROL, "no-store"),
                (header::X_CONTENT_TYPE_OPTIONS, "nosniff"),
                (
                    header::CONTENT_SECURITY_POLICY,
                    "sandbox; default-src 'none'",
                ),
            ],
            bytes,
        )
            .into_response())
    })
    .await
}
