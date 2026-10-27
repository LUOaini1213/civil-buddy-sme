//! Read-only comparison of persisted tool byte-version receipts with current sources.
//! A matching hash is not semantic validation, model comprehension or professional approval.
use crate::runtime_core::{RuntimeEvent, TurnRecord, WorkspaceContext};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::{
    collections::{BTreeMap, BTreeSet},
    io::Read,
    time::{Duration, Instant},
};

const MAX_FILE_BYTES: u64 = 25_000_000;
const MAX_TOTAL_BYTES: u64 = 100_000_000;
const MAX_CHECK_TIME: Duration = Duration::from_secs(10);

fn normalized_path(value: &str) -> String {
    let value = value.replace('\\', "/");
    let value = if let Some(unc) = value.strip_prefix("//?/UNC/") {
        format!("//{unc}")
    } else {
        value.strip_prefix("//?/").unwrap_or(&value).to_owned()
    };
    if cfg!(windows) {
        value.to_lowercase()
    } else {
        value
    }
}

fn valid_hash(value: &str) -> bool {
    value.len() == 64 && value.bytes().all(|b| b.is_ascii_hexdigit())
}

fn record_receipt(
    value: &Value,
    aliases: &BTreeMap<String, String>,
    hashes: &mut BTreeMap<String, BTreeSet<String>>,
) {
    let (Some(source), Some(hash)) = (value["source"].as_str(), value["source_sha256"].as_str())
    else {
        return;
    };
    if !valid_hash(hash) {
        return;
    }
    if let Some(selected) = aliases.get(&normalized_path(source)) {
        hashes
            .entry(selected.clone())
            .or_default()
            .insert(hash.to_ascii_lowercase());
    }
}

fn successful_tool_receipts(
    value: &Value,
    aliases: &BTreeMap<String, String>,
    hashes: &mut BTreeMap<String, BTreeSet<String>>,
) {
    if value["ok"] != true {
        return;
    }
    let result = &value["result"];
    record_receipt(result, aliases, hashes);
    // Retrieval workers return a host-created source manifest. Do not consume
    // model-provided reference hashes, arbitrary nested JSON or quoted source text.
    for source in result["sources"].as_array().into_iter().flatten() {
        record_receipt(source, aliases, hashes);
    }
}

fn current_hash(
    ws: &WorkspaceContext,
    source: &str,
    remaining: &mut u64,
    started: Instant,
) -> Result<String, &'static str> {
    if started.elapsed() >= MAX_CHECK_TIME {
        return Err("check_timeout");
    }
    let path = ws.resolve_read(source).map_err(|error| match error {
        crate::runtime_core::RuntimeError::Io(error)
            if error.kind() == std::io::ErrorKind::NotFound =>
        {
            "source_missing"
        }
        _ => "source_unavailable",
    })?;
    let mut file = std::fs::File::open(&path).map_err(|_| "source_unavailable")?;
    let before = file.metadata().map_err(|_| "source_unavailable")?;
    if !before.is_file() {
        return Err("source_unavailable");
    }
    if before.len() > MAX_FILE_BYTES {
        return Err("source_too_large");
    }
    if before.len() > *remaining {
        return Err("check_byte_budget");
    }
    let mut digest = Sha256::new();
    let mut bytes = 0_u64;
    let mut buffer = [0_u8; 64 * 1024];
    loop {
        if started.elapsed() >= MAX_CHECK_TIME {
            return Err("check_timeout");
        }
        let read = file.read(&mut buffer).map_err(|_| "source_unavailable")?;
        if read == 0 {
            break;
        }
        let read = read as u64;
        bytes += read;
        if read > *remaining {
            *remaining = 0;
            return Err("check_byte_budget");
        }
        *remaining -= read;
        if bytes > MAX_FILE_BYTES {
            return Err("source_too_large");
        }
        digest.update(&buffer[..read as usize]);
    }
    let after = file.metadata().map_err(|_| "source_unavailable")?;
    if bytes != before.len()
        || after.len() != before.len()
        || before.modified().ok() != after.modified().ok()
        || ws.resolve_read(source).map_err(|_| "source_unavailable")? != path
    {
        return Err("source_changed_during_check");
    }
    Ok(format!("{:x}", digest.finalize()))
}

pub(super) fn check(
    ws: &WorkspaceContext,
    record: &TurnRecord,
    events: &[RuntimeEvent],
    receipts_complete: bool,
) -> Value {
    let selection_complete = record.request["files"]
        .as_array()
        .is_some_and(|files| files.len() <= 24 && files.iter().all(|file| file.as_str().is_some()));
    let selected: BTreeSet<String> = record.request["files"]
        .as_array()
        .into_iter()
        .flatten()
        .filter_map(Value::as_str)
        .take(24)
        .map(str::to_owned)
        .collect();
    let mut aliases = BTreeMap::new();
    for source in &selected {
        aliases.insert(normalized_path(source), source.clone());
        aliases.insert(
            normalized_path(&ws.root().join(source).to_string_lossy()),
            source.clone(),
        );
    }
    let mut hashes = BTreeMap::new();
    for event in events.iter().filter(|event| event.seq <= record.last_seq) {
        if event.event == "tool_finished"
            && matches!(
                event.data["name"].as_str(),
                Some(
                    "read_file"
                        | "preview_document"
                        | "apply_document"
                        | "search_sources"
                        | "verify_sources"
                )
            )
        {
            successful_tool_receipts(&event.data["result"], &aliases, &mut hashes);
        }
        if event.event == "artifact" {
            record_receipt(&event.data, &aliases, &mut hashes);
        }
    }
    // Steps-mode findings and registered artifacts are host-owned records, not a model reply.
    if let Some(result) = &record.result {
        for finding in result["findings"].as_array().into_iter().flatten() {
            successful_tool_receipts(finding, &aliases, &mut hashes);
        }
        for artifact in result["artifacts"].as_array().into_iter().flatten() {
            record_receipt(artifact, &aliases, &mut hashes);
        }
    }
    let started = Instant::now();
    let mut remaining = MAX_TOTAL_BYTES;
    let mut sources = Vec::new();
    for source in selected {
        let versions = hashes.remove(&source).unwrap_or_default();
        let (status, reason, current) = if !selection_complete {
            ("unknown", Some("selection_out_of_bounds"), None)
        } else if !receipts_complete {
            ("unknown", Some("receipt_history_incomplete"), None)
        } else if versions.is_empty() {
            ("not_read", Some("no_successful_tool_version_receipt"), None)
        } else {
            match current_hash(ws, &source, &mut remaining, started) {
                Ok(current) => {
                    let status = if versions.len() > 1 {
                        "mixed_versions"
                    } else if versions.contains(&current) {
                        "unchanged"
                    } else {
                        "changed"
                    };
                    (status, None, Some(current))
                }
                Err("source_missing") => ("missing", Some("source_missing"), None),
                Err(reason) => ("unknown", Some(reason), None),
            }
        };
        sources.push(
            json!({"path":source,"recorded_sha256":versions,"current_sha256":current,
            "status":status,"reason":reason}),
        );
    }
    let all_unchanged =
        !sources.is_empty() && sources.iter().all(|source| source["status"] == "unchanged");
    let needs_review = !selection_complete
        || !receipts_complete
        || sources.iter().any(|source| source["status"] != "unchanged");
    json!({"workspace":record.request["workspace"],"turn_id":record.turn_id,"checked_at":chrono::Utc::now().to_rfc3339(),
        "scope":"tool_source_byte_versions","receipts_complete":receipts_complete,"selection_complete":selection_complete,"sources":sources,
        "all_recorded_sources_unchanged":all_unchanged,"needs_review":needs_review,
        "automatic_recompute":false,"semantic_validation":false,"originals_written":false})
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::runtime_core::{SessionId, TaskId, TurnId, TurnStatus};

    struct Job {
        root: std::path::PathBuf,
        ws: WorkspaceContext,
    }
    impl Job {
        fn new() -> Self {
            let root =
                std::env::temp_dir().join(format!("civil-source-unit-{}", uuid::Uuid::new_v4()));
            std::fs::create_dir(&root).unwrap();
            std::fs::write(root.join("brief.txt"), b"fixture bytes").unwrap();
            Self {
                ws: WorkspaceContext::new(&root).unwrap(),
                root,
            }
        }
    }
    impl Drop for Job {
        fn drop(&mut self) {
            let _ = std::fs::remove_dir_all(&self.root);
        }
    }

    #[test]
    fn byte_budget_and_elapsed_deadline_do_not_return_a_hash() {
        let job = Job::new();
        assert_eq!(
            current_hash(&job.ws, "brief.txt", &mut 1, Instant::now()),
            Err("check_byte_budget")
        );
        assert_eq!(
            current_hash(
                &job.ws,
                "brief.txt",
                &mut 100,
                Instant::now() - Duration::from_secs(11)
            ),
            Err("check_timeout")
        );
    }

    #[test]
    fn partial_history_or_oversized_selection_never_claims_current() {
        let job = Job::new();
        let hash = super::super::tools::sha256(b"fixture bytes");
        let mut record = TurnRecord {
            actor_id: None,
            workspace_id: job.ws.id().into(),
            session_id: SessionId::parse("unit").unwrap(),
            turn_id: TurnId::new(),
            task_id: TaskId::new(),
            status: TurnStatus::Completed,
            request: json!({"workspace":"fixture","files":["brief.txt"]}),
            result: None,
            created_at: "fixture".into(),
            updated_at: "fixture".into(),
            last_seq: 1,
        };
        let event = RuntimeEvent {
            actor_id: None,
            workspace_id: record.workspace_id.clone(),
            session_id: record.session_id.clone(),
            turn_id: record.turn_id.clone(),
            task_id: record.task_id.clone(),
            seq: 1,
            event: "tool_finished".into(),
            data: json!({"name":"read_file","result":{"ok":true,
                "result":{"source":"brief.txt","source_sha256":hash}}}),
            ts: "fixture".into(),
        };
        let partial = check(&job.ws, &record, std::slice::from_ref(&event), false);
        assert_eq!(partial["sources"][0]["status"], "unknown");
        assert_eq!(
            partial["sources"][0]["reason"],
            "receipt_history_incomplete"
        );
        assert_eq!(partial["all_recorded_sources_unchanged"], false);
        record.request["files"] = json!(vec!["brief.txt"; 25]);
        let oversized = check(&job.ws, &record, &[event], true);
        assert_eq!(oversized["selection_complete"], false);
        assert_eq!(oversized["sources"][0]["reason"], "selection_out_of_bounds");
        assert_eq!(oversized["all_recorded_sources_unchanged"], false);
    }
}
