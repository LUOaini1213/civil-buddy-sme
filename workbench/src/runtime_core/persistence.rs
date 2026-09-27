use super::{CancellationToken, Result, RuntimeError, SessionId, TaskId, TurnId, WorkspaceContext};
use rusqlite::{params, Connection, OptionalExtension, Transaction, TransactionBehavior};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::{
    collections::HashMap,
    path::Path,
    sync::{Arc, Mutex},
    time::Duration,
};

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum TurnStatus {
    Running,
    Cancelling,
    Completed,
    Failed,
    Cancelled,
    Interrupted,
}
impl TurnStatus {
    pub fn is_terminal(self) -> bool {
        !matches!(self, Self::Running | Self::Cancelling)
    }
    fn as_str(self) -> &'static str {
        match self {
            Self::Running => "running",
            Self::Cancelling => "cancelling",
            Self::Completed => "completed",
            Self::Failed => "failed",
            Self::Cancelled => "cancelled",
            Self::Interrupted => "interrupted",
        }
    }
    fn parse(value: &str) -> Result<Self> {
        match value {
            "running" => Ok(Self::Running),
            "cancelling" => Ok(Self::Cancelling),
            "completed" => Ok(Self::Completed),
            "failed" => Ok(Self::Failed),
            "cancelled" => Ok(Self::Cancelled),
            "interrupted" => Ok(Self::Interrupted),
            _ => Err(RuntimeError::InvalidState(format!(
                "unknown persisted status {value}"
            ))),
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct TurnRecord {
    #[serde(default)]
    pub actor_id: Option<String>,
    pub workspace_id: String,
    pub session_id: SessionId,
    pub turn_id: TurnId,
    pub task_id: TaskId,
    pub status: TurnStatus,
    pub request: Value,
    pub result: Option<Value>,
    pub created_at: String,
    pub updated_at: String,
    pub last_seq: u64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RuntimeEvent {
    #[serde(default)]
    pub actor_id: Option<String>,
    pub workspace_id: String,
    pub session_id: SessionId,
    pub turn_id: TurnId,
    pub task_id: TaskId,
    /// Monotonically increasing within a turn, starting at 1.
    pub seq: u64,
    pub event: String,
    pub data: Value,
    pub ts: String,
}

struct Inner {
    db: Mutex<Connection>,
    cancellations: Mutex<HashMap<TurnId, CancellationToken>>,
}

/// One cloneable runtime for the existing product sessions. Database uniqueness
/// enforces exclusion across runtime instances too; the in-memory cancellation
/// tokens are cooperative and local to clones of this instance.
#[derive(Clone)]
pub struct RuntimeCore(Arc<Inner>);

impl RuntimeCore {
    /// Opening storage does not recover turns: call recover_interrupted once at
    /// application startup, before accepting requests or starting any workers.
    pub fn open(path: impl AsRef<Path>) -> Result<Self> {
        let path = path.as_ref();
        if let Some(parent) = path.parent().filter(|p| !p.as_os_str().is_empty()) {
            std::fs::create_dir_all(parent)?;
        }
        let db = Connection::open(path)?;
        db.busy_timeout(Duration::from_secs(5))?;
        db.execute_batch("PRAGMA journal_mode=WAL; PRAGMA foreign_keys=ON;
            CREATE TABLE IF NOT EXISTS runtime_workspaces (
                workspace_id TEXT PRIMARY KEY, root TEXT NOT NULL, output_root TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS runtime_turns (
                turn_id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL REFERENCES runtime_workspaces(workspace_id),
                session_id TEXT NOT NULL, task_id TEXT NOT NULL, status TEXT NOT NULL
                    CHECK(status IN ('running','cancelling','completed','failed','cancelled','interrupted')),
                request_json TEXT NOT NULL, result_json TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                last_seq INTEGER NOT NULL DEFAULT 0 CHECK(last_seq >= 0)
            );
            CREATE UNIQUE INDEX IF NOT EXISTS runtime_one_active_session
                ON runtime_turns(workspace_id, session_id) WHERE status IN ('running','cancelling');
            CREATE INDEX IF NOT EXISTS runtime_session_turns ON runtime_turns(workspace_id,session_id,created_at);
            CREATE TABLE IF NOT EXISTS runtime_events (
                turn_id TEXT NOT NULL REFERENCES runtime_turns(turn_id), seq INTEGER NOT NULL CHECK(seq > 0),
                task_id TEXT NOT NULL, event TEXT NOT NULL, data_json TEXT NOT NULL, ts TEXT NOT NULL,
                PRIMARY KEY(turn_id, seq)
            );")?;
        Ok(Self(Arc::new(Inner {
            db: Mutex::new(db),
            cancellations: Mutex::new(HashMap::new()),
        })))
    }

    pub fn begin_turn(
        &self,
        workspace: &WorkspaceContext,
        session: &SessionId,
        request: Value,
    ) -> Result<TurnLease> {
        let mut db = self.0.db.lock().map_err(|_| RuntimeError::Poisoned)?;
        let tx = db.transaction_with_behavior(TransactionBehavior::Immediate)?;
        let active: bool = tx.query_row("SELECT EXISTS(SELECT 1 FROM runtime_turns WHERE workspace_id=?1 AND session_id=?2 AND status IN ('running','cancelling'))", params![workspace.id(), session.as_str()], |r| r.get(0))?;
        if active {
            return Err(RuntimeError::SessionBusy);
        }
        tx.execute("INSERT INTO runtime_workspaces(workspace_id,root,output_root) VALUES(?1,?2,?3)
            ON CONFLICT(workspace_id) DO UPDATE SET root=excluded.root,output_root=excluded.output_root",
            params![workspace.id(), workspace.root().to_string_lossy(), workspace.output_root().to_string_lossy()])?;
        let turn_id = TurnId::new();
        let task_id = TaskId::new();
        let ts = now();
        tx.execute("INSERT INTO runtime_turns(turn_id,workspace_id,session_id,task_id,status,request_json,created_at,updated_at)
            VALUES(?1,?2,?3,?4,'running',?5,?6,?6)", params![turn_id.as_str(), workspace.id(), session.as_str(), task_id.as_str(), serde_json::to_string(&request)?, ts])?;
        append_event(
            &tx,
            workspace.id(),
            session,
            &turn_id,
            &task_id,
            "turn.started",
            json!({"status":"running"}),
        )?;
        // Lock order is always database -> cancellation registry.
        let mut tokens = self
            .0
            .cancellations
            .lock()
            .map_err(|_| RuntimeError::Poisoned)?;
        tx.commit()?;
        let cancellation = CancellationToken::new();
        tokens.insert(turn_id.clone(), cancellation.clone());
        Ok(TurnLease {
            runtime: self.clone(),
            workspace: workspace.clone(),
            session: session.clone(),
            turn_id,
            task_id,
            cancellation,
            finished: false,
        })
    }

    pub fn turn(
        &self,
        workspace: &WorkspaceContext,
        session: &SessionId,
        turn: &TurnId,
    ) -> Result<TurnRecord> {
        let db = self.0.db.lock().map_err(|_| RuntimeError::Poisoned)?;
        read_turn(&db, workspace.id(), session, turn)
    }

    pub fn list_turns(
        &self,
        workspace: &WorkspaceContext,
        session: Option<&SessionId>,
        limit: usize,
    ) -> Result<Vec<TurnRecord>> {
        let db = self.0.db.lock().map_err(|_| RuntimeError::Poisoned)?;
        let mut statement = db.prepare("SELECT turn_id,session_id FROM runtime_turns WHERE workspace_id=?1 AND (?2 IS NULL OR session_id=?2) ORDER BY created_at DESC, rowid DESC LIMIT ?3")?;
        let rows = statement.query_map(
            params![
                workspace.id(),
                session.map(SessionId::as_str),
                limit.min(1000) as i64
            ],
            |r| Ok((r.get::<_, String>(0)?, r.get::<_, String>(1)?)),
        )?;
        rows.map(|row| {
            let (turn, session) = row?;
            read_turn(
                &db,
                workspace.id(),
                &SessionId::parse(session)?,
                &TurnId::parse(turn)?,
            )
        })
        .collect()
    }

    pub fn replay(
        &self,
        workspace: &WorkspaceContext,
        session: &SessionId,
        turn: &TurnId,
        after_seq: u64,
        limit: usize,
    ) -> Result<Vec<RuntimeEvent>> {
        let db = self.0.db.lock().map_err(|_| RuntimeError::Poisoned)?;
        let record = read_turn(&db, workspace.id(), session, turn)?;
        if after_seq >= i64::MAX as u64 {
            return Ok(Vec::new());
        }
        let mut statement = db.prepare("SELECT seq,task_id,event,data_json,ts FROM runtime_events WHERE turn_id=?1 AND seq>?2 ORDER BY seq LIMIT ?3")?;
        let rows = statement.query_map(
            params![turn.as_str(), after_seq as i64, limit.min(1000) as i64],
            |r| {
                Ok((
                    r.get::<_, i64>(0)?,
                    r.get::<_, String>(1)?,
                    r.get::<_, String>(2)?,
                    r.get::<_, String>(3)?,
                    r.get::<_, String>(4)?,
                ))
            },
        )?;
        rows.map(|row| {
            let (seq, task, event, data, ts) = row?;
            Ok(RuntimeEvent {
                actor_id: record.actor_id.clone(),
                workspace_id: workspace.id().to_owned(),
                session_id: session.clone(),
                turn_id: turn.clone(),
                task_id: TaskId::parse(task)?,
                seq: seq as u64,
                event,
                data: serde_json::from_str(&data)?,
                ts,
            })
        })
        .collect()
    }

    /// Cancellation does not release the session until the executing worker
    /// settles it as cancelled/interrupted; another turn cannot race that worker.
    pub fn cancel_turn(
        &self,
        workspace: &WorkspaceContext,
        session: &SessionId,
        turn: &TurnId,
    ) -> Result<bool> {
        let mut db = self.0.db.lock().map_err(|_| RuntimeError::Poisoned)?;
        let tx = db.transaction_with_behavior(TransactionBehavior::Immediate)?;
        let record = read_turn(&tx, workspace.id(), session, turn)?;
        if record.status.is_terminal() {
            return Ok(false);
        }
        if record.status != TurnStatus::Cancelling {
            tx.execute(
                "UPDATE runtime_turns SET status='cancelling',updated_at=?1 WHERE turn_id=?2",
                params![now(), turn.as_str()],
            )?;
            append_event(
                &tx,
                workspace.id(),
                session,
                turn,
                &record.task_id,
                "turn.cancelling",
                json!({"status":"cancelling"}),
            )?;
        }
        let tokens = self
            .0
            .cancellations
            .lock()
            .map_err(|_| RuntimeError::Poisoned)?;
        tx.commit()?;
        if let Some(token) = tokens.get(turn) {
            token.cancel();
        }
        Ok(true)
    }

    /// Explicit crash recovery, only before execution begins in this process.
    /// No tools or LLM requests are replayed. Multi-process hosting must elect a
    /// single startup owner; this is not a distributed heartbeat/lease protocol.
    pub fn recover_interrupted(&self) -> Result<usize> {
        let mut db = self.0.db.lock().map_err(|_| RuntimeError::Poisoned)?;
        let tx = db.transaction_with_behavior(TransactionBehavior::Immediate)?;
        let active: Vec<(String, String, String, String)> = {
            let mut statement = tx.prepare("SELECT workspace_id,session_id,turn_id,task_id FROM runtime_turns WHERE status IN ('running','cancelling')")?;
            let rows =
                statement.query_map([], |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?, r.get(3)?)))?;
            rows.collect::<std::result::Result<_, _>>()?
        };
        for (workspace, session, turn, task) in &active {
            tx.execute("UPDATE runtime_turns SET status='interrupted',updated_at=?1,result_json=?2 WHERE turn_id=?3", params![now(), r#"{"reason":"runtime_restart"}"#, turn])?;
            append_event(
                &tx,
                workspace,
                &SessionId::parse(session)?,
                &TurnId::parse(turn)?,
                &TaskId::parse(task)?,
                "turn.interrupted",
                json!({"status":"interrupted","reason":"runtime_restart"}),
            )?;
        }
        let mut tokens = self
            .0
            .cancellations
            .lock()
            .map_err(|_| RuntimeError::Poisoned)?;
        tx.commit()?;
        for (_, _, turn, _) in &active {
            if let Some(token) = tokens.remove(&TurnId::parse(turn)?) {
                token.cancel();
            }
        }
        Ok(active.len())
    }

    fn emit(
        &self,
        lease: &TurnLease,
        task: &TaskId,
        event: &str,
        data: Value,
    ) -> Result<RuntimeEvent> {
        if event.is_empty() || event.len() > 128 || event.chars().any(char::is_control) {
            return Err(RuntimeError::InvalidState("invalid event name".into()));
        }
        let mut db = self.0.db.lock().map_err(|_| RuntimeError::Poisoned)?;
        let tx = db.transaction_with_behavior(TransactionBehavior::Immediate)?;
        let record = read_turn(&tx, lease.workspace.id(), &lease.session, &lease.turn_id)?;
        if record.status.is_terminal() {
            return Err(RuntimeError::InvalidState(
                "cannot append to a finished turn".into(),
            ));
        }
        let result = append_event(
            &tx,
            lease.workspace.id(),
            &lease.session,
            &lease.turn_id,
            task,
            event,
            data,
        )?;
        tx.commit()?; // The caller can deliver the event only after durability.
        Ok(result)
    }

    fn finish(&self, lease: &TurnLease, status: TurnStatus, result: Value) -> Result<TurnRecord> {
        self.finish_transition(lease, status, result, false)
    }

    fn finish_transition(
        &self,
        lease: &TurnLease,
        mut status: TurnStatus,
        mut result: Value,
        resolve_cancel: bool,
    ) -> Result<TurnRecord> {
        if !status.is_terminal() {
            return Err(RuntimeError::InvalidState(
                "finish requires a terminal status".into(),
            ));
        }
        let mut db = self.0.db.lock().map_err(|_| RuntimeError::Poisoned)?;
        let tx = db.transaction_with_behavior(TransactionBehavior::Immediate)?;
        let record = read_turn(&tx, lease.workspace.id(), &lease.session, &lease.turn_id)?;
        if record.status.is_terminal() {
            return Err(RuntimeError::InvalidState(
                "turn is already finished".into(),
            ));
        }
        if status == TurnStatus::Completed
            && (record.status == TurnStatus::Cancelling || lease.cancellation.is_cancelled())
        {
            if !resolve_cancel {
                return Err(RuntimeError::InvalidState(
                    "cancelled turn cannot complete successfully".into(),
                ));
            }
            status = TurnStatus::Cancelled;
            if !result.is_object() {
                result = json!({"result":result});
            }
            result["partial"] = json!(true);
        }
        tx.execute(
            "UPDATE runtime_turns SET status=?1,result_json=?2,updated_at=?3 WHERE turn_id=?4",
            params![
                status.as_str(),
                serde_json::to_string(&result)?,
                now(),
                lease.turn_id.as_str()
            ],
        )?;
        append_event(
            &tx,
            lease.workspace.id(),
            &lease.session,
            &lease.turn_id,
            &lease.task_id,
            &format!("turn.{}", status.as_str()),
            json!({"status":status,"result":result}),
        )?;
        let record = read_turn(&tx, lease.workspace.id(), &lease.session, &lease.turn_id)?;
        let mut tokens = self
            .0
            .cancellations
            .lock()
            .map_err(|_| RuntimeError::Poisoned)?;
        tx.commit()?;
        tokens.remove(&lease.turn_id);
        Ok(record)
    }
}

pub struct TurnLease {
    runtime: RuntimeCore,
    workspace: WorkspaceContext,
    session: SessionId,
    turn_id: TurnId,
    task_id: TaskId,
    cancellation: CancellationToken,
    finished: bool,
}
impl TurnLease {
    pub fn turn_id(&self) -> &TurnId {
        &self.turn_id
    }
    pub fn task_id(&self) -> &TaskId {
        &self.task_id
    }
    pub fn session_id(&self) -> &SessionId {
        &self.session
    }
    pub fn workspace(&self) -> &WorkspaceContext {
        &self.workspace
    }
    pub fn cancellation(&self) -> CancellationToken {
        self.cancellation.clone()
    }
    pub fn emit(&self, event: &str, data: Value) -> Result<RuntimeEvent> {
        self.runtime.emit(self, &self.task_id, event, data)
    }
    pub fn emit_for_task(&self, task: &TaskId, event: &str, data: Value) -> Result<RuntimeEvent> {
        self.runtime.emit(self, task, event, data)
    }
    /// Also sees cancellation/recovery performed by a separately opened runtime.
    pub fn check_cancelled(&self) -> Result<()> {
        self.cancellation.check()?;
        let record = self
            .runtime
            .turn(&self.workspace, &self.session, &self.turn_id)?;
        if record.status != TurnStatus::Running {
            self.cancellation.cancel();
            return Err(RuntimeError::Cancelled);
        }
        Ok(())
    }
    pub fn finish(mut self, status: TurnStatus, result: Value) -> Result<TurnRecord> {
        let record = self.runtime.finish(&self, status, result)?;
        self.finished = true;
        Ok(record)
    }

    /// Serializes completion against persisted cancellation. If cancellation
    /// won the transaction ordering, preserve partial output and commit exactly
    /// one `turn.cancelled` event instead of reporting successful completion.
    /// A cancellation requested after the completed transaction returns false.
    pub fn finish_resolving_cancel(
        mut self,
        status: TurnStatus,
        result: Value,
    ) -> Result<TurnRecord> {
        let record = self
            .runtime
            .finish_transition(&self, status, result, true)?;
        self.finished = true;
        Ok(record)
    }
}
impl Drop for TurnLease {
    fn drop(&mut self) {
        if !self.finished {
            self.cancellation.cancel();
            let _ = self.runtime.finish(
                self,
                TurnStatus::Interrupted,
                json!({"reason":"turn_lease_dropped"}),
            );
        }
    }
}

fn now() -> String {
    chrono::Utc::now().to_rfc3339_opts(chrono::SecondsFormat::Millis, true)
}

fn read_turn(
    db: &Connection,
    workspace: &str,
    session: &SessionId,
    turn: &TurnId,
) -> Result<TurnRecord> {
    let row = db.query_row("SELECT task_id,status,request_json,result_json,created_at,updated_at,last_seq FROM runtime_turns WHERE workspace_id=?1 AND session_id=?2 AND turn_id=?3",
        params![workspace,session.as_str(),turn.as_str()], |r| Ok((r.get::<_, String>(0)?,r.get::<_, String>(1)?,r.get::<_, String>(2)?,r.get::<_, Option<String>>(3)?,r.get::<_, String>(4)?,r.get::<_, String>(5)?,r.get::<_, i64>(6)?))).optional()?.ok_or(RuntimeError::NotFound)?;
    let request: Value = serde_json::from_str(&row.2)?;
    Ok(TurnRecord {
        actor_id: request["actor_id"].as_str().map(str::to_owned),
        workspace_id: workspace.to_owned(),
        session_id: session.clone(),
        turn_id: turn.clone(),
        task_id: TaskId::parse(row.0)?,
        status: TurnStatus::parse(&row.1)?,
        request,
        result: row.3.as_deref().map(serde_json::from_str).transpose()?,
        created_at: row.4,
        updated_at: row.5,
        last_seq: row.6 as u64,
    })
}

fn append_event(
    tx: &Transaction<'_>,
    workspace: &str,
    session: &SessionId,
    turn: &TurnId,
    task: &TaskId,
    event: &str,
    data: Value,
) -> Result<RuntimeEvent> {
    let ts = now();
    let changed = tx.execute(
        "UPDATE runtime_turns SET last_seq=last_seq+1,updated_at=?1 WHERE turn_id=?2 AND last_seq<9223372036854775807",
        params![ts, turn.as_str()],
    )?;
    if changed != 1 {
        return Err(RuntimeError::InvalidState(
            "event sequence exhausted".into(),
        ));
    }
    let seq: i64 = tx.query_row(
        "SELECT last_seq FROM runtime_turns WHERE turn_id=?1",
        [turn.as_str()],
        |r| r.get(0),
    )?;
    let request: String = tx.query_row("SELECT request_json FROM runtime_turns WHERE turn_id=?1", [turn.as_str()], |r| r.get(0))?;
    let actor_id = serde_json::from_str::<Value>(&request)?["actor_id"].as_str().map(str::to_owned);
    tx.execute("INSERT INTO runtime_events(turn_id,seq,task_id,event,data_json,ts) VALUES(?1,?2,?3,?4,?5,?6)", params![turn.as_str(),seq,task.as_str(),event,serde_json::to_string(&data)?,ts])?;
    Ok(RuntimeEvent {
        actor_id,
        workspace_id: workspace.to_owned(),
        session_id: session.clone(),
        turn_id: turn.clone(),
        task_id: task.clone(),
        seq: seq as u64,
        event: event.to_owned(),
        data,
        ts,
    })
}
