//! Explicit, current imported sources. Never a recursive hidden-directory scan.
use super::{
    api::ProductState,
    file_listing::{self, FileListPurpose, FileListing, ListedFile},
    tools::sha256,
};
use crate::runtime_core::WorkspaceContext;
use rusqlite::{params, OptionalExtension};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::{
    collections::{BTreeMap, HashSet},
    io::Read,
};

const PREFIX: &str = ".civil-buddy/out/handoffs/";
const MAX_SOURCE: u64 = 16 * 1024 * 1024;
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct SourceOrigin {
    pub kind: String,
    pub import_id: String,
    pub document_id: String,
    pub revision: u64,
    pub registered_sha256: String,
}
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct FileBinding {
    pub path: String,
    pub origin: SourceOrigin,
}
fn hex(s: &str, n: usize) -> bool {
    s.len() == n
        && s.bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
}
pub fn is_handoff(path: &str) -> bool {
    path.replace('\\', "/")
        .split('/')
        .filter(|p| !p.is_empty() && *p != ".")
        .collect::<Vec<_>>()
        .join("/")
        .to_ascii_lowercase()
        .starts_with(PREFIX)
}
pub fn requires_binding(ws: &WorkspaceContext, path: &str) -> bool {
    is_handoff(path)
        || ws
            .resolve_read(path)
            .ok()
            .and_then(|p| {
                p.strip_prefix(ws.root())
                    .ok()
                    .and_then(|p| p.to_str())
                    .map(is_handoff)
            })
            .unwrap_or(false)
}
fn supported(ext: &str) -> bool {
    matches!(ext, "pdf" | "docx" | "xlsx" | "txt" | "md" | "csv" | "json")
}
fn shape(binding: &FileBinding) -> bool {
    let o = &binding.origin;
    let Some((_, ext)) = binding.path.rsplit_once('.') else {
        return false;
    };
    o.kind == "project_handoff"
        && hex(&o.import_id, 32)
        && hex(&o.document_id, 32)
        && (1..=256).contains(&o.revision)
        && hex(&o.registered_sha256, 64)
        && supported(ext)
        && binding.path == format!("{PREFIX}{}/{}.{}", o.import_id, o.document_id, ext)
}
fn error() -> String {
    "Imported source registration could not be verified; refresh and explicitly reselect its current revision".into()
}
struct Current {
    origin: SourceOrigin,
    name: String,
    size: u64,
}
fn current(
    st: &ProductState,
    ws: &WorkspaceContext,
    only: Option<&HashSet<&str>>,
) -> Result<BTreeMap<String, Current>, String> {
    st.auth.authorize_workspace(ws.root())?;
    let db = st.index.lock().map_err(|_| error())?;
    let workspace: String = db
        .query_row(
            "SELECT id FROM workspaces WHERE root=?1",
            [ws.root().to_string_lossy().as_ref()],
            |r| r.get(0),
        )
        .map_err(|_| error())?;
    let (count,largest):(i64,i64)=db.query_row("SELECT count(*),coalesce(max(length(CAST(record AS BLOB))),0) FROM project_documents WHERE workspace=?1",[&workspace],|r|Ok((r.get(0)?,r.get(1)?))).map_err(|_|error())?;
    if count > 100 || largest > 65536 {
        return Err(error());
    }
    let mut statement=db.prepare("SELECT d.id,d.record,r.record,length(r.payload) FROM project_documents d LEFT JOIN project_revisions r ON r.workspace=d.workspace AND r.document_id=d.id AND r.revision=json_extract(d.record,'$.revision') AND length(CAST(r.record AS BLOB))<=65536 AND length(r.payload)<=16777216 WHERE d.workspace=?1 ORDER BY d.id").map_err(|_|error())?;
    let rows = statement
        .query_map([&workspace], |r| {
            Ok((
                r.get::<_, String>(0)?,
                r.get::<_, String>(1)?,
                r.get::<_, Option<String>>(2)?,
                r.get::<_, Option<i64>>(3)?,
            ))
        })
        .map_err(|_| error())?;
    let mut result = BTreeMap::new();
    for row in rows {
        let (key, raw, revision_raw, size) = row.map_err(|_| error())?;
        let d: Value = serde_json::from_str(&raw).map_err(|_| error())?;
        let path = d["path"].as_str().ok_or_else(error)?;
        if !is_handoff(path) || only.is_some_and(|set| !set.contains(path)) {
            continue;
        }
        let ext = path.rsplit('.').next().unwrap_or("");
        if !supported(ext) {
            continue;
        }
        let revision_raw = revision_raw
            .filter(|v| v.len() <= 65536)
            .ok_or_else(error)?;
        let r: Value = serde_json::from_str(&revision_raw).map_err(|_| error())?;
        let size = size
            .filter(|n| *n >= 0 && *n as u64 <= MAX_SOURCE)
            .ok_or_else(error)? as u64;
        let origin = SourceOrigin {
            kind: "project_handoff".into(),
            import_id: d["provenance"]["import_id"]
                .as_str()
                .ok_or_else(error)?
                .into(),
            document_id: key.clone(),
            revision: d["revision"].as_u64().ok_or_else(error)?,
            registered_sha256: d["sha256"].as_str().ok_or_else(error)?.into(),
        };
        if !shape(&FileBinding {
            path: path.into(),
            origin: origin.clone(),
        }) || d["id"] != key
            || d["workspace"] != workspace
            || ["id", "workspace", "path", "revision", "sha256"]
                .iter()
                .any(|k| d[*k] != r[*k])
        {
            return Err(error());
        }
        let package = d["provenance"]["package_sha256"]
            .as_str()
            .filter(|s| hex(s, 64))
            .ok_or_else(error)?;
        let receipt_raw:Option<String>=db.query_row("SELECT record FROM project_imports WHERE workspace=?1 AND package_sha256=?2 AND length(CAST(record AS BLOB))<=2097152",params![workspace,package],|r|r.get(0)).optional().map_err(|_|error())?;
        let receipt: Value =
            serde_json::from_str(&receipt_raw.ok_or_else(error)?).map_err(|_| error())?;
        let mappings = receipt["document_mapping"]
            .as_array()
            .filter(|m| m.len() <= 100)
            .ok_or_else(error)?;
        let initial_raw: String = db.query_row(
            "SELECT record FROM project_revisions WHERE workspace=?1 AND document_id=?2 AND revision=1 AND length(CAST(record AS BLOB))<=65536",
            params![workspace,key], |r|r.get(0)).map_err(|_|error())?;
        let initial: Value = serde_json::from_str(&initial_raw).map_err(|_| error())?;
        if receipt["schema_version"] != 1
            || receipt["workspace"] != workspace
            || receipt["import_id"] != origin.import_id
            || receipt["package_sha256"] != package
            || mappings
                .iter()
                .filter(|m| m["local_document_id"] == key && m["local_revision"] == 1)
                .count()
                != 1
            || initial["workspace"] != workspace
            || initial["id"] != key
            || initial["revision"] != 1
            || initial["path"] != path
            || !initial["sha256"].as_str().is_some_and(|s| hex(s, 64))
            || !mappings
                .iter()
                .any(|m| m["local_document_id"] == key && m["source_sha256"] == initial["sha256"])
        {
            return Err(error());
        }
        let verified = ws.resolve_read(path).map_err(|_| error())?;
        let actual = std::fs::metadata(&verified).map_err(|_| error())?;
        if !actual.is_file() || actual.len() != size {
            return Err(error());
        }
        let name = d["title"]
            .as_str()
            .filter(|s| !s.trim().is_empty() && s.chars().count() <= 160)
            .ok_or_else(error)?
            .to_owned();
        if result
            .insert(path.into(), Current { origin, name, size })
            .is_some()
        {
            return Err(error());
        }
    }
    Ok(result)
}
pub fn inventory(st: &ProductState, ws: &WorkspaceContext) -> Result<FileListing, String> {
    let mut report = file_listing::list_files(ws, FileListPurpose::Agent)?;
    for (path, source) in current(st, ws, None)? {
        report.files.push(ListedFile {
            path,
            name: source.name,
            size: source.size,
            origin: Some(source.origin),
        });
    }
    report
        .files
        .sort_by(|a, b| a.path.as_bytes().cmp(b.path.as_bytes()));
    report.files.dedup_by(|a, b| a.path == b.path);
    report.file_limit_reached = report.files.len() >= file_listing::MAX_FILES;
    report.files.truncate(file_listing::MAX_FILES);
    report.returned_count = report.files.len();
    report.incomplete_reasons.clear();
    if report.file_limit_reached {
        report.incomplete_reasons.push("file_limit");
    }
    if report.depth_limit_reached {
        report.incomplete_reasons.push("depth_limit");
    }
    if report.scan_limit_reached {
        report.incomplete_reasons.push("scan_limit");
    }
    report.complete = report.incomplete_reasons.is_empty();
    Ok(report)
}
pub fn validate_selection(
    st: &ProductState,
    ws: &WorkspaceContext,
    files: &[String],
    bindings: &[FileBinding],
) -> Result<(), String> {
    if bindings.len() > 24 {
        return Err(error());
    }
    let selected: HashSet<&str> = files
        .iter()
        .filter(|s| requires_binding(ws, s))
        .map(String::as_str)
        .collect();
    let mut seen = HashSet::new();
    for b in bindings {
        if !shape(b) || !selected.contains(b.path.as_str()) || !seen.insert(b.path.as_str()) {
            return Err(error());
        }
    }
    if seen != selected {
        return Err(error());
    }
    if selected.is_empty() {
        return Ok(());
    }
    let sources = current(st, ws, Some(&selected))?;
    for b in bindings {
        if sources.get(&b.path).is_none_or(|c| c.origin != b.origin) {
            return Err(error());
        }
        verify_bytes(ws, &b.path, &b.origin.registered_sha256)?;
    }
    Ok(())
}
/// A missing proof is not evidence that a known registered revision changed.
/// Publication callers first compare the artifact's recorded source bytes, so
/// this classifies the additional immutable registration identity separately.
pub fn binding_status(
    st: &ProductState,
    ws: &WorkspaceContext,
    binding: &FileBinding,
) -> &'static str {
    if !shape(binding) {
        return "unavailable";
    }
    let selected = HashSet::from([binding.path.as_str()]);
    let Ok(sources) = current(st, ws, Some(&selected)) else {
        return "unavailable";
    };
    let Some(source) = sources.get(&binding.path) else {
        return "unavailable";
    };
    if source.origin != binding.origin {
        return "changed";
    }
    if verify_bytes(ws, &binding.path, &binding.origin.registered_sha256).is_ok() {
        "current"
    } else {
        "unavailable"
    }
}
pub fn verify_one(
    st: &ProductState,
    ws: &WorkspaceContext,
    files: &[String],
    bindings: &[FileBinding],
    source: &str,
) -> Result<Option<String>, String> {
    if !files.iter().any(|f| f == source) {
        return Err("Source was not selected for this task".into());
    }
    if !requires_binding(ws, source) {
        return Ok(None);
    }
    let b = bindings
        .iter()
        .find(|b| b.path == source)
        .ok_or_else(error)?;
    validate_selection(st, ws, &[source.into()], std::slice::from_ref(b))?;
    Ok(Some(b.origin.registered_sha256.clone()))
}
pub fn verify_bytes(ws: &WorkspaceContext, source: &str, expected: &str) -> Result<(), String> {
    let file =
        std::fs::File::open(ws.resolve_read(source).map_err(|_| error())?).map_err(|_| error())?;
    if file.metadata().map_err(|_| error())?.len() > MAX_SOURCE {
        return Err(error());
    }
    let mut bytes = Vec::new();
    file.take(MAX_SOURCE + 1)
        .read_to_end(&mut bytes)
        .map_err(|_| error())?;
    if bytes.len() as u64 > MAX_SOURCE || sha256(&bytes) != expected {
        return Err(error());
    }
    ws.resolve_read(source).map_err(|_| error())?;
    Ok(())
}
pub fn expected_versions(bindings: &[FileBinding], sources: &[String]) -> Value {
    let mut map = serde_json::Map::new();
    for b in bindings {
        if sources.contains(&b.path) {
            map.insert(b.path.clone(), json!(b.origin.registered_sha256));
        }
    }
    Value::Object(map)
}
