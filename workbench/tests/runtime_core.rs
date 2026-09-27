use civil_workbench::runtime_core::*;
use serde_json::{json, Value};
use std::{
    path::{Path, PathBuf},
    sync::{Arc, Barrier},
    thread,
    time::Duration,
};

struct Temp(PathBuf);
impl Temp {
    fn new() -> Self {
        let path =
            std::env::temp_dir().join(format!("civil-runtime-core-{}", uuid::Uuid::new_v4()));
        std::fs::create_dir_all(&path).unwrap();
        Self(path)
    }
    fn path(&self) -> &Path {
        &self.0
    }
    fn workspace(&self, name: &str) -> WorkspaceContext {
        let path = self.0.join(name);
        std::fs::create_dir_all(&path).unwrap();
        WorkspaceContext::new(path).unwrap()
    }
}
impl Drop for Temp {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.0);
    }
}
fn limits() -> BudgetLimits {
    BudgetLimits {
        total_tokens: 100,
        task_tokens: 100,
        max_model_calls: 20,
        max_tasks: 20,
        max_depth: 2,
        timeout_ms: 60_000,
    }
}
fn usage(input: u64, output: u64) -> Usage {
    Usage {
        input_tokens: input,
        output_tokens: output,
        model_calls: 1,
        estimated: false,
    }
}

#[test]
fn ids_and_runtime_are_thread_safe() {
    fn thread_safe<T: Send + Sync>() {}
    thread_safe::<RuntimeCore>();
    thread_safe::<BudgetTree>();
    thread_safe::<TurnLease>();
    for value in ["", "../other", "space name", "a/b", "中文"] {
        assert!(SessionId::parse(value).is_err());
    }
    let id = SessionId::parse("existing-session_1").unwrap();
    assert_eq!(
        serde_json::from_value::<SessionId>(json!(id.to_string())).unwrap(),
        id
    );
    assert!(serde_json::from_value::<SessionId>(json!("../injected")).is_err());
}

#[test]
fn workspace_scope_normalizes_and_denies_traversal_and_secrets() {
    let tmp = Temp::new();
    let workspace = tmp.workspace("project");
    std::fs::write(workspace.root().join("drawing.json"), "{}").unwrap();
    assert_eq!(
        workspace.resolve_read("drawing.json").unwrap(),
        workspace.root().join("drawing.json")
    );
    assert_eq!(
        WorkspaceContext::new(workspace.root().join("."))
            .unwrap()
            .id(),
        workspace.id()
    );
    assert_eq!(
        workspace.resolve_write("reports/summary.json").unwrap(),
        workspace.output_root().join("reports/summary.json")
    );
    for relative in [
        "../drawing.json",
        ".env",
        "config/.env.local",
        ".git/config",
        "keys/private.pem",
        "file:stream",
        ".env ",
        "nul.txt",
        "reports/con.xlsx",
        "",
    ] {
        assert!(
            workspace.resolve_write(relative).is_err(),
            "accepted {relative}"
        );
        assert!(
            workspace.resolve_read(relative).is_err(),
            "accepted {relative}"
        );
    }
    assert!(workspace.resolve_write(workspace.root()).is_err());
    assert!(
        !workspace.output_root().exists(),
        "scope resolution must not mutate files"
    );
}

#[cfg(unix)]
#[test]
fn workspace_denies_existing_symlink_ancestors() {
    use std::os::unix::fs::symlink;
    let tmp = Temp::new();
    let workspace = tmp.workspace("project");
    let outside = tmp.workspace("outside");
    std::fs::write(outside.root().join("file"), "data").unwrap();
    symlink(outside.root(), workspace.root().join("linked")).unwrap();
    assert!(workspace.resolve_read("linked/file").is_err());
    std::fs::create_dir_all(workspace.output_root()).unwrap();
    symlink(outside.root(), workspace.output_root().join("linked")).unwrap();
    assert!(workspace.resolve_write("linked/new").is_err());
}

#[test]
fn one_active_turn_per_workspace_session_even_across_database_connections() {
    let tmp = Temp::new();
    let workspace = tmp.workspace("project");
    let session = SessionId::new();
    let path = tmp.path().join("runtime.sqlite");
    let runtime = RuntimeCore::open(&path).unwrap();
    let runtimes: Vec<_> = (0..8).map(|_| RuntimeCore::open(&path).unwrap()).collect();
    let barrier = Arc::new(Barrier::new(runtimes.len()));
    let count: usize = thread::scope(|scope| {
        let handles: Vec<_> = runtimes
            .into_iter()
            .map(|runtime| {
                let barrier = barrier.clone();
                let workspace = &workspace;
                let session = &session;
                scope.spawn(move || {
                    barrier.wait();
                    let result =
                        runtime.begin_turn(workspace, session, json!({"prompt":"compete"}));
                    // Keep the winning lease active until every contender has checked.
                    barrier.wait();
                    match result {
                        Ok(lease) => {
                            lease.finish(TurnStatus::Completed, json!({})).unwrap();
                            1
                        }
                        Err(RuntimeError::SessionBusy) => 0,
                        Err(error) => panic!("unexpected concurrent error: {error}"),
                    }
                })
            })
            .collect();
        handles
            .into_iter()
            .map(|handle| handle.join().unwrap())
            .sum()
    });
    assert_eq!(count, 1);
    let first = runtime.begin_turn(&workspace, &session, json!({})).unwrap();
    let other_workspace = tmp.workspace("other");
    let second = runtime
        .begin_turn(&other_workspace, &session, json!({}))
        .unwrap();
    assert_ne!(first.turn_id(), second.turn_id());
    assert!(matches!(
        runtime.turn(&other_workspace, &session, first.turn_id()),
        Err(RuntimeError::NotFound)
    ));
    first.finish(TurnStatus::Completed, json!({})).unwrap();
    second.finish(TurnStatus::Completed, json!({})).unwrap();
}

#[test]
fn durable_events_have_contiguous_sequence_and_scoped_cursor_replay() {
    let tmp = Temp::new();
    let workspace = tmp.workspace("project");
    let session = SessionId::new();
    let path = tmp.path().join("runtime.sqlite");
    let runtime = RuntimeCore::open(&path).unwrap();
    let lease = runtime
        .begin_turn(&workspace, &session, json!({"user":"check quantities"}))
        .unwrap();
    let turn = lease.turn_id().clone();
    thread::scope(|scope| {
        for producer in 0..4 {
            let lease = &lease;
            scope.spawn(move || {
                for item in 0..20 {
                    lease
                        .emit("tool", json!({"producer":producer,"item":item}))
                        .unwrap();
                }
            });
        }
    });
    let record = lease
        .finish(TurnStatus::Completed, json!({"answer":"ready"}))
        .unwrap();
    assert_eq!(record.last_seq, 82);
    drop(runtime);
    let reopened = RuntimeCore::open(&path).unwrap();
    assert_eq!(
        reopened.turn(&workspace, &session, &turn).unwrap().result,
        Some(json!({"answer":"ready"}))
    );
    let first = reopened.replay(&workspace, &session, &turn, 0, 37).unwrap();
    let remaining = reopened
        .replay(&workspace, &session, &turn, first.last().unwrap().seq, 1000)
        .unwrap();
    let events: Vec<_> = first.into_iter().chain(remaining).collect();
    assert_eq!(events.len(), 82);
    for (index, event) in events.iter().enumerate() {
        assert_eq!(event.seq, index as u64 + 1);
        assert_eq!(&event.turn_id, &turn);
    }
    assert_eq!(events.last().unwrap().event, "turn.completed");
    assert!(matches!(
        reopened.replay(&workspace, &SessionId::new(), &turn, 0, 10),
        Err(RuntimeError::NotFound)
    ));
    assert!(reopened
        .replay(&workspace, &session, &turn, u64::MAX, 10)
        .unwrap()
        .is_empty());
    let listed = reopened.list_turns(&workspace, Some(&session), 10).unwrap();
    assert_eq!(listed.len(), 1);
    assert_eq!(listed[0].turn_id, turn);
    assert!(reopened
        .list_turns(&workspace, Some(&SessionId::new()), 10)
        .unwrap()
        .is_empty());
    let raw = rusqlite::Connection::open(&path).unwrap();
    let persisted_root: String = raw
        .query_row(
            "SELECT root FROM runtime_workspaces WHERE workspace_id=?1",
            [workspace.id()],
            |row| row.get(0),
        )
        .unwrap();
    assert_eq!(persisted_root, workspace.root().to_string_lossy());
}

#[test]
fn cancellation_holds_session_until_worker_finishes_and_propagates_downward() {
    let tmp = Temp::new();
    let workspace = tmp.workspace("project");
    let session = SessionId::new();
    let runtime = RuntimeCore::open(tmp.path().join("runtime.sqlite")).unwrap();
    let lease = runtime.begin_turn(&workspace, &session, json!({})).unwrap();
    let turn = lease.turn_id().clone();
    let token = lease.cancellation();
    let child = token.child();
    let sibling = token.child();
    child.cancel();
    assert!(!token.is_cancelled());
    assert!(!sibling.is_cancelled());
    assert!(runtime.cancel_turn(&workspace, &session, &turn).unwrap());
    assert!(
        runtime.cancel_turn(&workspace, &session, &turn).unwrap(),
        "duplicate cancellation is idempotent"
    );
    assert!(sibling.is_cancelled());
    assert!(matches!(
        lease.check_cancelled(),
        Err(RuntimeError::Cancelled)
    ));
    assert!(matches!(
        runtime.begin_turn(&workspace, &session, json!({})),
        Err(RuntimeError::SessionBusy)
    ));
    assert_eq!(
        runtime
            .replay(&workspace, &session, &turn, 0, 10)
            .unwrap()
            .len(),
        2
    );
    lease
        .finish(TurnStatus::Cancelled, json!({"partial":true}))
        .unwrap();
    assert!(!runtime.cancel_turn(&workspace, &session, &turn).unwrap());
    let next = runtime.begin_turn(&workspace, &session, json!({})).unwrap();
    drop(next);
}

#[test]
fn reopening_does_not_implicitly_recover_and_explicit_recovery_never_reexecutes() {
    let tmp = Temp::new();
    let workspace = tmp.workspace("project");
    let session = SessionId::new();
    let path = tmp.path().join("runtime.sqlite");
    let first = RuntimeCore::open(&path).unwrap();
    let lease = first
        .begin_turn(&workspace, &session, json!({"write":"proposed.xlsx"}))
        .unwrap();
    let turn = lease.turn_id().clone();
    lease.emit("tool", json!({"status":"started"})).unwrap();
    // A separate connection observes the same persisted running state. Startup
    // recovery is deliberately explicit, and does not execute the stored request.
    let second = RuntimeCore::open(&path).unwrap();
    assert_eq!(
        second.turn(&workspace, &session, &turn).unwrap().status,
        TurnStatus::Running
    );
    assert_eq!(second.recover_interrupted().unwrap(), 1);
    assert_eq!(second.recover_interrupted().unwrap(), 0);
    assert_eq!(
        second.turn(&workspace, &session, &turn).unwrap().status,
        TurnStatus::Interrupted
    );
    assert!(lease.check_cancelled().is_err());
    assert!(lease.emit("tool", json!({"status":"complete"})).is_err());
    assert!(!workspace.output_root().exists());
    drop(lease);
    assert_eq!(
        second
            .replay(&workspace, &session, &turn, 0, 10)
            .unwrap()
            .len(),
        3
    );
    let next = second.begin_turn(&workspace, &session, json!({})).unwrap();
    let next_id = next.turn_id().clone();
    drop(next);
    assert_eq!(
        second.turn(&workspace, &session, &next_id).unwrap().status,
        TurnStatus::Interrupted
    );
}

#[test]
fn completion_and_cancellation_race_commits_one_consistent_terminal_event() {
    let tmp = Temp::new();
    let workspace = tmp.workspace("project");
    let session = SessionId::new();
    let path = tmp.path().join("runtime.sqlite");
    let runtime = RuntimeCore::open(&path).unwrap();
    let other = RuntimeCore::open(&path).unwrap();
    for attempt in 0..24 {
        let lease = runtime
            .begin_turn(&workspace, &session, json!({"attempt":attempt}))
            .unwrap();
        let turn = lease.turn_id().clone();
        let barrier = Arc::new(Barrier::new(2));
        let (record, cancellation_won) = thread::scope(|scope| {
            let finish_barrier = barrier.clone();
            let finishing = scope.spawn(move || {
                finish_barrier.wait();
                lease
                    .finish_resolving_cancel(
                        TurnStatus::Completed,
                        json!({"artifacts":["preserved"],"partial":false}),
                    )
                    .unwrap()
            });
            let cancelling = scope.spawn(|| {
                barrier.wait();
                other.cancel_turn(&workspace, &session, &turn).unwrap()
            });
            (finishing.join().unwrap(), cancelling.join().unwrap())
        });
        let expected = if cancellation_won {
            TurnStatus::Cancelled
        } else {
            TurnStatus::Completed
        };
        assert_eq!(record.status, expected);
        assert_eq!(
            runtime.turn(&workspace, &session, &turn).unwrap().status,
            expected
        );
        let events = runtime.replay(&workspace, &session, &turn, 0, 100).unwrap();
        let terminal: Vec<_> = events
            .iter()
            .filter(|event| {
                matches!(
                    event.event.as_str(),
                    "turn.completed" | "turn.cancelled" | "turn.interrupted" | "turn.failed"
                )
            })
            .collect();
        assert_eq!(terminal.len(), 1);
        assert_eq!(
            terminal[0].data["status"],
            serde_json::to_value(expected).unwrap()
        );
        if cancellation_won {
            assert_eq!(record.result.as_ref().unwrap()["partial"], true);
        }
        assert_eq!(
            record.result.as_ref().unwrap()["artifacts"],
            json!(["preserved"])
        );
    }
    // Local deadline cancellation uses a token, without a separate API request.
    let lease = runtime.begin_turn(&workspace, &session, json!({})).unwrap();
    lease.cancellation().cancel();
    let record = lease
        .finish_resolving_cancel(TurnStatus::Completed, json!("partial text"))
        .unwrap();
    assert_eq!(record.status, TurnStatus::Cancelled);
    assert_eq!(record.result.unwrap()["partial"], true);
}

#[test]
fn concurrent_children_cannot_oversubscribe_parent_budget() {
    let root = TaskId::new();
    let tree = BudgetTree::new(root.clone(), limits()).unwrap();
    let children: Vec<_> = (0..16)
        .map(|_| {
            let child = TaskId::new();
            tree.register_task(&root, &child).unwrap();
            child
        })
        .collect();
    let barrier = Arc::new(Barrier::new(children.len()));
    let handles: Vec<_> = children
        .into_iter()
        .map(|child| {
            let tree = tree.clone();
            let barrier = barrier.clone();
            thread::spawn(move || {
                barrier.wait();
                let result = tree.reserve(&child, 50, 40, 1);
                barrier.wait();
                result.ok()
            })
        })
        .collect();
    let reservations: Vec<_> = handles
        .into_iter()
        .filter_map(|h| h.join().unwrap())
        .collect();
    assert_eq!(reservations.len(), 1);
    assert_eq!(tree.snapshot().unwrap().reserved_tokens, 90);
    drop(reservations);
    let snapshot = tree.snapshot().unwrap();
    assert_eq!(snapshot.reserved_tokens, 0);
    assert_eq!(snapshot.spent_tokens, 0);
}

#[test]
fn reservations_settle_actual_usage_and_started_failures_stay_charged() {
    let root = TaskId::new();
    let tree = BudgetTree::new(root.clone(), limits()).unwrap();
    let mut reservation = tree.reserve(&root, 40, 40, 1).unwrap();
    reservation.start().unwrap();
    reservation.settle(usage(30, 10)).unwrap();
    assert_eq!(tree.snapshot().unwrap().spent_tokens, 40);
    let mut retry = tree.reserve(&root, 20, 20, 1).unwrap();
    retry.start().unwrap();
    drop(retry);
    let snapshot = tree.snapshot().unwrap();
    assert_eq!(snapshot.spent_tokens, 80);
    assert_eq!(snapshot.model_calls, 2);
    assert!(snapshot.settlements[1].estimated);
    assert!(tree.reserve(&root, 20, 1, 1).is_err());
    tree.reserve(&root, 10, 10, 1).unwrap().release().unwrap();
    assert_eq!(tree.snapshot().unwrap().spent_tokens, 80);
    let mut excess = tree.reserve(&root, 5, 5, 1).unwrap();
    excess.start().unwrap();
    assert!(matches!(
        excess.settle(usage(30, 5)),
        Err(RuntimeError::BudgetExceeded(_))
    ));
    let snapshot = tree.snapshot().unwrap();
    assert_eq!(snapshot.spent_tokens, 115);
    assert_eq!(snapshot.reserved_tokens, 0);
    assert!(snapshot.exhausted);
    assert!(tree.reserve(&root, 0, 1, 1).is_err());
}

#[test]
fn task_depth_width_calls_individual_limit_and_deadline_are_enforced() {
    let root = TaskId::new();
    let child = TaskId::new();
    let tree = BudgetTree::new(
        root.clone(),
        BudgetLimits {
            total_tokens: 100,
            task_tokens: 40,
            max_tasks: 2,
            max_depth: 1,
            max_model_calls: 1,
            ..limits()
        },
    )
    .unwrap();
    tree.register_task(&root, &child).unwrap();
    assert!(tree.register_task(&root, &TaskId::new()).is_err());
    assert!(tree.register_task(&child, &TaskId::new()).is_err());
    assert!(tree.reserve(&child, 30, 11, 1).is_err());
    let mut reservation = tree.reserve(&child, 20, 20, 1).unwrap();
    reservation.start().unwrap();
    reservation.settle(usage(10, 10)).unwrap();
    assert!(tree.reserve(&root, 1, 1, 1).is_err());
    let deadline = BudgetTree::new(
        root.clone(),
        BudgetLimits {
            timeout_ms: 1,
            ..limits()
        },
    )
    .unwrap();
    thread::sleep(Duration::from_millis(15));
    assert!(deadline.reserve(&root, 1, 1, 1).is_err());
}

fn interaction() -> Vec<Value> {
    vec![
        json!({"role":"user","content":"calculate"}),
        json!({"role":"assistant","content":null,"reasoning_content":"reasoning metadata","tool_calls":[
            {"id":"call_a","type":"function","function":{"name":"a","arguments":"{}"}},
            {"id":"call_b","type":"function","function":{"name":"b","arguments":"{}"}}]}),
        json!({"role":"tool","tool_call_id":"call_b","content":"result b"}),
        json!({"role":"tool","tool_call_id":"call_a","content":"result a"}),
    ]
}
fn context_request() -> ContextRequest {
    ContextRequest {
        system: vec![json!({"role":"system","content":"ground answers in tool results"})],
        history: vec![],
        current: interaction(),
        tools: vec![
            json!({"type":"function","function":{"name":"a","parameters":{"type":"object"}}}),
        ],
        output_reserve: 128,
        window: 100_000,
    }
}

#[test]
fn context_trims_whole_old_turns_preserving_parallel_tool_pairs_and_metadata() {
    let mut request = context_request();
    let recent = vec![
        json!({"role":"user","content":"recent"}),
        json!({"role":"assistant","content":"retained answer"}),
    ];
    request.history = recent.clone();
    let minimal = prepare_context(request.clone()).unwrap();
    request.window = minimal.report.input_estimate + request.output_reserve;
    let mut old = interaction();
    old[0]["content"] = json!("old ".repeat(1000));
    request.history = old.iter().cloned().chain(recent.clone()).collect();
    let result = prepare_context(request.clone()).unwrap();
    assert_eq!(result.report.omitted_history_messages, old.len());
    assert_eq!(result.report.omitted_history_groups, 1);
    let expected: Vec<_> = request
        .system
        .into_iter()
        .chain(recent)
        .chain(request.current.clone())
        .collect();
    assert_eq!(result.messages, expected);
    assert_eq!(result.tools, request.tools);
    assert!(result.report.estimated);
    assert_eq!(
        &result.messages[result.messages.len() - request.current.len()..],
        &request.current
    );
}

#[test]
fn context_counts_system_tools_output_and_rejects_required_overflow_or_broken_pairs() {
    let base = context_request();
    let prepared = prepare_context(base.clone()).unwrap();
    let boundary = prepared.report.input_estimate + base.output_reserve;
    let mut too_small = base.clone();
    too_small.window = boundary - 1;
    assert!(matches!(
        prepare_context(too_small),
        Err(RuntimeError::ContextOverflow { .. })
    ));
    let mut large_tools = base.clone();
    large_tools.window = boundary + 100;
    large_tools.tools[0]["description"] = json!("schema ".repeat(1000));
    assert!(matches!(
        prepare_context(large_tools),
        Err(RuntimeError::ContextOverflow { .. })
    ));
    let mut large_system = base.clone();
    large_system.window = boundary + 100;
    large_system.system[0]["content"] = json!("policy ".repeat(1000));
    assert!(matches!(
        prepare_context(large_system),
        Err(RuntimeError::ContextOverflow { .. })
    ));
    let mut large_result = base.clone();
    large_result.window = boundary + 100;
    large_result.current[3]["content"] = json!("result ".repeat(1000));
    assert!(matches!(
        prepare_context(large_result),
        Err(RuntimeError::ContextOverflow { .. })
    ));
    let mut missing_result = base.clone();
    missing_result.current.pop();
    assert!(matches!(
        prepare_context(missing_result),
        Err(RuntimeError::InvalidContext(_))
    ));
    let mut duplicate = base.clone();
    duplicate.current[3]["tool_call_id"] = json!("call_b");
    assert!(matches!(
        prepare_context(duplicate),
        Err(RuntimeError::InvalidContext(_))
    ));
    let mut zero_output = base;
    zero_output.output_reserve = 0;
    assert!(prepare_context(zero_output).is_err());
    assert!(check_request_budget(
        &json!({"messages":[],"provider":{"extra":"x".repeat(5000)}}),
        10,
        1000
    )
    .is_err());
}
