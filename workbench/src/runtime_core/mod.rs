//! Shared execution infrastructure. Sessions are the existing product sessions;
//! this module does not introduce another user-visible thread system.
mod budget;
mod context;
mod persistence;
mod workspace;

pub use budget::{BudgetLimits, BudgetReservation, BudgetSnapshot, BudgetTree, Usage};
pub use context::{
    check_request_budget, prepare_context, ContextReport, ContextRequest, PreparedContext,
};
pub use persistence::{RuntimeCore, RuntimeEvent, SessionSummary, TurnLease, TurnRecord, TurnStart, TurnStatus};
pub use workspace::WorkspaceContext;

use serde::{Deserialize, Serialize};
use std::sync::{
    atomic::{AtomicBool, Ordering},
    Arc,
};

#[derive(Debug, thiserror::Error)]
pub enum RuntimeError {
    #[error("invalid identifier: {0}")]
    InvalidId(String),
    #[error("workspace scope denied: {0}")]
    ScopeDenied(String),
    #[error("session already has an active turn")]
    SessionBusy,
    #[error("idempotency key was already used for a different request")]
    IdempotencyConflict,
    #[error("record not found in this workspace/session")]
    NotFound,
    #[error("invalid state transition: {0}")]
    InvalidState(String),
    #[error("task was cancelled")]
    Cancelled,
    #[error("budget exceeded: {0}")]
    BudgetExceeded(String),
    #[error("context_overflow: required input {input_estimate} + output {output_reserve} exceeds window {window}")]
    ContextOverflow {
        input_estimate: u64,
        output_reserve: u64,
        window: u64,
    },
    #[error("invalid model message sequence: {0}")]
    InvalidContext(String),
    #[error("runtime lock poisoned")]
    Poisoned,
    #[error(transparent)]
    Io(#[from] std::io::Error),
    #[error(transparent)]
    Sqlite(#[from] rusqlite::Error),
    #[error(transparent)]
    Json(#[from] serde_json::Error),
}

pub type Result<T> = std::result::Result<T, RuntimeError>;

macro_rules! identifier {
    ($name:ident) => {
        #[derive(Debug, Clone, PartialEq, Eq, Hash, Serialize, Deserialize)]
        #[serde(try_from = "String", into = "String")]
        pub struct $name(String);
        impl $name {
            pub fn new() -> Self {
                Self(uuid::Uuid::new_v4().simple().to_string())
            }
            pub fn parse(value: impl AsRef<str>) -> Result<Self> {
                let value = value.as_ref();
                if value.is_empty()
                    || value.len() > 128
                    || !value
                        .bytes()
                        .all(|b| b.is_ascii_alphanumeric() || b == b'-' || b == b'_')
                {
                    return Err(RuntimeError::InvalidId(value.chars().take(128).collect()));
                }
                Ok(Self(value.to_owned()))
            }
            pub fn as_str(&self) -> &str {
                &self.0
            }
        }
        impl Default for $name {
            fn default() -> Self {
                Self::new()
            }
        }
        impl TryFrom<String> for $name {
            type Error = RuntimeError;
            fn try_from(v: String) -> Result<Self> {
                Self::parse(v)
            }
        }
        impl From<$name> for String {
            fn from(v: $name) -> String {
                v.0
            }
        }
        impl std::fmt::Display for $name {
            fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
                f.write_str(&self.0)
            }
        }
    };
}
identifier!(SessionId);
identifier!(TurnId);
identifier!(TaskId);

/// Cooperative cancellation. Cancelling an ancestor cancels its descendants;
/// cancelling a child never cancels its siblings or parent.
#[derive(Debug, Clone, Default)]
pub struct CancellationToken(Arc<CancelState>);
#[derive(Debug, Default)]
struct CancelState {
    cancelled: AtomicBool,
    parent: Option<CancellationToken>,
}
impl CancellationToken {
    pub fn new() -> Self {
        Self::default()
    }
    pub fn child(&self) -> Self {
        Self(Arc::new(CancelState {
            cancelled: AtomicBool::new(false),
            parent: Some(self.clone()),
        }))
    }
    pub fn cancel(&self) {
        self.0.cancelled.store(true, Ordering::Release);
    }
    pub fn is_cancelled(&self) -> bool {
        self.0.cancelled.load(Ordering::Acquire)
            || self.0.parent.as_ref().is_some_and(Self::is_cancelled)
    }
    pub fn check(&self) -> Result<()> {
        if self.is_cancelled() {
            Err(RuntimeError::Cancelled)
        } else {
            Ok(())
        }
    }
}
