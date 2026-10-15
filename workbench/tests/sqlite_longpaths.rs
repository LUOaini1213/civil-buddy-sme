//! Actual Windows disk I/O; no model, service, private input or environment edits.
#![cfg(windows)]

use civil_workbench::{
    config::Paths,
    product::{api::ProductState, auth::InstanceAuth},
    runtime_core::{SessionId, TurnStatus},
    sqlite,
};
use rusqlite::{ErrorCode, OpenFlags, TransactionBehavior};
use serde_json::json;
use sha2::{Digest, Sha256};
use std::{
    path::{Path, PathBuf},
    sync::Arc,
    time::Duration,
};

struct Temp(PathBuf);
impl Temp {
    fn new() -> Self {
        let path = std::env::temp_dir().join(format!("civil-sqlite-{}", uuid::Uuid::new_v4()));
        assert!(path.is_absolute());
        std::fs::create_dir_all(&path).unwrap();
        Self(path)
    }
    fn unicode_dir(&self, name: &str) -> PathBuf {
        let mut path = self.0.join(name);
        for i in 0..10 {
            path.push(format!("{i}-{}", "工程资料".repeat(10)));
        }
        std::fs::create_dir_all(&path).unwrap();
        assert!(
            path.to_string_lossy().len() > 1040,
            "must exceed default VFS byte limit"
        );
        assert!(path.to_string_lossy().encode_utf16().count() > 260);
        path
    }
}
impl Drop for Temp {
    fn drop(&mut self) {
        // Only this test's UUID directory below the actual temporary root.
        if let (Ok(path), Ok(parent)) = (self.0.canonicalize(), std::env::temp_dir().canonicalize())
        {
            if path.parent() == Some(parent.as_path())
                && path
                    .file_name()
                    .is_some_and(|n| n.to_string_lossy().starts_with("civil-sqlite-"))
            {
                let _ = std::fs::remove_dir_all(path);
            }
        }
    }
}
fn integrity(db: &rusqlite::Connection) {
    assert_eq!(
        db.query_row("PRAGMA integrity_check", [], |r| r.get::<_, String>(0))
            .unwrap(),
        "ok"
    );
}

#[test]
fn sqlite_journal_crosses_260_and_reopens_from_an_unprefixed_path() {
    let temp = Temp::new();
    let name = "identity.sqlite";
    let remaining = 255 - temp.0.to_string_lossy().encode_utf16().count() - 2 - name.len();
    assert!((1..=240).contains(&remaining));
    let folder = temp.0.join("j".repeat(remaining));
    std::fs::create_dir_all(&folder).unwrap();
    let path = folder.join(name);
    assert_eq!(path.to_string_lossy().encode_utf16().count(), 255);
    assert!(!path.to_string_lossy().starts_with(r"\\?\"));
    let mut db = sqlite::open(&path).unwrap();
    assert!(db.path().unwrap().starts_with(r"\\?\"));
    db.execute_batch("PRAGMA journal_mode=DELETE; CREATE TABLE sample(value INTEGER NOT NULL);")
        .unwrap();
    let tx = db.transaction().unwrap();
    tx.execute("INSERT INTO sample VALUES(17)", []).unwrap();
    let journal = PathBuf::from(format!("{}-journal", path.display()));
    assert!(journal.to_string_lossy().encode_utf16().count() > 260);
    assert!(std::fs::metadata(&journal).unwrap().len() > 0);
    tx.commit().unwrap();
    integrity(&db);
    drop(db);
    let reopened = sqlite::open(&path).unwrap();
    assert_eq!(
        reopened
            .query_row("SELECT value FROM sample", [], |r| r.get::<_, i64>(0))
            .unwrap(),
        17
    );
    integrity(&reopened);
}

#[test]
fn sqlite_wal_unicode_paths_keep_real_locks_rollback_and_read_only_permissions() {
    let temp = Temp::new();
    let path = temp.unicode_dir("wal").join("runtime.sqlite");
    let mut first = sqlite::open(&path).unwrap();
    first.busy_timeout(Duration::ZERO).unwrap();
    first.execute_batch("PRAGMA journal_mode=WAL; CREATE TABLE sample(value INTEGER PRIMARY KEY); INSERT INTO sample VALUES(1);").unwrap();
    let second = sqlite::open(&path).unwrap();
    second.busy_timeout(Duration::ZERO).unwrap();
    let tx = first
        .transaction_with_behavior(TransactionBehavior::Immediate)
        .unwrap();
    tx.execute("INSERT INTO sample VALUES(2)", []).unwrap();
    assert_eq!(
        second
            .query_row("SELECT COUNT(*) FROM sample", [], |r| r.get::<_, i64>(0))
            .unwrap(),
        1
    );
    let error = second
        .execute("INSERT INTO sample VALUES(3)", [])
        .unwrap_err();
    assert!(
        matches!(
            error.sqlite_error_code(),
            Some(ErrorCode::DatabaseBusy | ErrorCode::DatabaseLocked)
        ),
        "{error}"
    );
    tx.rollback().unwrap();
    second.execute("INSERT INTO sample VALUES(3)", []).unwrap();
    for suffix in ["-wal", "-shm"] {
        assert!(
            std::fs::metadata(PathBuf::from(format!("{}{suffix}", path.display())))
                .unwrap()
                .len()
                > 0
        );
    }
    integrity(&second);
    drop(second);
    drop(first);
    let read_only = sqlite::open_with_flags(&path, OpenFlags::SQLITE_OPEN_READ_ONLY).unwrap();
    let values: Vec<i64> = read_only
        .prepare("SELECT value FROM sample ORDER BY value")
        .unwrap()
        .query_map([], |r| r.get(0))
        .unwrap()
        .map(Result::unwrap)
        .collect();
    assert_eq!(values, [1, 3], "rolled-back row must not survive reopening");
    assert_eq!(
        read_only
            .execute("INSERT INTO sample VALUES(4)", [])
            .unwrap_err()
            .sqlite_error_code(),
        Some(ErrorCode::ReadOnly)
    );
    integrity(&read_only);
    let missing = path.with_file_name("missing.sqlite");
    assert!(sqlite::open_with_flags(&missing, OpenFlags::SQLITE_OPEN_READ_ONLY).is_err());
    assert!(
        !missing.exists(),
        "read-only open must not create a database"
    );
}

fn identity(user: &str, root: &Path) -> Arc<InstanceAuth> {
    InstanceAuth::named(
        user,
        &format!(
            "{:x}",
            Sha256::digest(b"synthetic-long-path-token-with-32-bytes")
        ),
        vec![root.to_owned()],
        None,
    )
    .unwrap()
}

#[test]
fn sqlite_product_state_all_databases_reopen_without_losing_identity_or_turns() {
    assert!(
        std::env::var_os("CIVIL_STATE_ROOT").is_none(),
        "test requires isolated state"
    );
    let temp = Temp::new();
    let demo = temp.unicode_dir("state");
    let workspace = temp.unicode_dir("job");
    let paths = Paths::from_demo(demo.clone());
    let state =
        ProductState::open_with_auth(paths.clone(), identity("long-path-owner", &workspace))
            .unwrap();
    let id = state.register(workspace.to_str().unwrap()).unwrap()["id"]
        .as_str()
        .unwrap()
        .to_owned();
    let ws = state.workspace(&id).unwrap();
    let session = SessionId::parse("long-path-session").unwrap();
    let turn = state
        .runtime
        .begin_turn(
            &ws,
            &session,
            json!({"actor_id":"long-path-owner","message":"synthetic disk test"}),
        )
        .unwrap();
    let turn_id = turn.turn_id().clone();
    turn.emit("synthetic_marker", json!({"value":29})).unwrap();
    turn.finish(TurnStatus::Completed, json!({"value":29}))
        .unwrap();
    drop(state);
    let reopened =
        ProductState::open_with_auth(paths.clone(), identity("long-path-owner", &workspace))
            .unwrap();
    assert_eq!(
        reopened.register(workspace.to_str().unwrap()).unwrap()["id"],
        id
    );
    let record = reopened.runtime.turn(&ws, &session, &turn_id).unwrap();
    assert_eq!(record.status, TurnStatus::Completed);
    assert_eq!(record.actor_id.as_deref(), Some("long-path-owner"));
    assert_eq!(record.result.unwrap()["value"], 29);
    assert!(reopened
        .runtime
        .replay(&ws, &session, &turn_id, 0, 100)
        .unwrap()
        .iter()
        .any(|e| e.event == "synthetic_marker" && e.data["value"] == 29));
    for path in [
        demo.join("data/unified/identity.sqlite"),
        demo.join("data/unified/index.sqlite"),
        demo.join("data/unified/runtime.sqlite"),
        workspace.join(".civil-buddy/instance-owner.sqlite"),
    ] {
        let db = sqlite::open_with_flags(path, OpenFlags::SQLITE_OPEN_READ_ONLY).unwrap();
        integrity(&db);
    }
    drop(reopened);
    let rejected =
        ProductState::open_with_auth(paths.clone(), identity("different-owner", &workspace));
    assert!(rejected.is_err());
    assert!(rejected
        .err()
        .unwrap()
        .contains("different user or workspace"));
    let other = Paths::from_demo(temp.unicode_dir("other-state"));
    let rejected = ProductState::open_with_auth(other, identity("different-owner", &workspace));
    assert!(rejected.is_err());
    assert!(rejected
        .err()
        .unwrap()
        .contains("workspace already belongs"));
    // Failed identity claims must not corrupt the previous owner's state.
    let reopened =
        ProductState::open_with_auth(paths, identity("long-path-owner", &workspace)).unwrap();
    assert_eq!(
        reopened
            .runtime
            .turn(&ws, &session, &turn_id)
            .unwrap()
            .status,
        TurnStatus::Completed
    );
}
