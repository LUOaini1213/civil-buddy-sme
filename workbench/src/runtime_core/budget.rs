use super::{Result, RuntimeError, TaskId};
use serde::{Deserialize, Serialize};
use std::{
    collections::HashMap,
    sync::{Arc, Mutex},
    time::{Duration, Instant},
};

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct BudgetLimits {
    pub total_tokens: u64,
    pub task_tokens: u64,
    pub max_model_calls: u32,
    /// Includes the root task. Tasks remain counted after completion.
    pub max_tasks: u32,
    pub max_depth: u32,
    pub timeout_ms: u64,
}
impl Default for BudgetLimits {
    fn default() -> Self {
        Self {
            total_tokens: 96_000,
            task_tokens: 32_768,
            max_model_calls: 8,
            max_tasks: 5,
            max_depth: 1,
            timeout_ms: 120_000,
        }
    }
}

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct Usage {
    pub input_tokens: u64,
    pub output_tokens: u64,
    pub model_calls: u32,
    pub estimated: bool,
}
impl Usage {
    fn total(&self) -> Result<u64> {
        self.input_tokens
            .checked_add(self.output_tokens)
            .ok_or_else(|| RuntimeError::BudgetExceeded("usage overflow".into()))
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct BudgetSnapshot {
    pub limits: BudgetLimits,
    pub spent_tokens: u64,
    pub reserved_tokens: u64,
    pub model_calls: u32,
    pub reserved_model_calls: u32,
    pub task_count: usize,
    pub elapsed_ms: u64,
    pub exhausted: bool,
    pub settlements: Vec<Usage>,
}

#[derive(Debug)]
struct TaskBudget {
    depth: u32,
    spent: u64,
}
#[derive(Debug)]
struct Allocation {
    task: TaskId,
    input: u64,
    output: u64,
    calls: u32,
    started: bool,
}
#[derive(Debug)]
struct State {
    limits: BudgetLimits,
    start: Instant,
    tasks: HashMap<TaskId, TaskBudget>,
    reservations: HashMap<u64, Allocation>,
    next: u64,
    spent: u64,
    calls: u32,
    settlements: Vec<Usage>,
}

#[derive(Debug, Clone)]
pub struct BudgetTree(Arc<Mutex<State>>);

impl BudgetTree {
    pub fn new(root: TaskId, limits: BudgetLimits) -> Result<Self> {
        if limits.total_tokens == 0
            || limits.task_tokens == 0
            || limits.task_tokens > limits.total_tokens
            || limits.max_tasks == 0
            || limits.max_tasks > 10_000
            || limits.max_depth > 32
            || limits.timeout_ms == 0
        {
            return Err(RuntimeError::InvalidState("invalid budget limits".into()));
        }
        Ok(Self(Arc::new(Mutex::new(State {
            limits,
            start: Instant::now(),
            tasks: HashMap::from([(root, TaskBudget { depth: 0, spent: 0 })]),
            reservations: HashMap::new(),
            next: 0,
            spent: 0,
            calls: 0,
            settlements: Vec::new(),
        }))))
    }

    pub fn register_task(&self, parent: &TaskId, child: &TaskId) -> Result<()> {
        let mut state = self.0.lock().map_err(|_| RuntimeError::Poisoned)?;
        check_time(&state)?;
        if state.tasks.contains_key(child) {
            return Err(RuntimeError::InvalidState("task already registered".into()));
        }
        let depth = state.tasks.get(parent).ok_or(RuntimeError::NotFound)?.depth + 1;
        if depth > state.limits.max_depth || state.tasks.len() >= state.limits.max_tasks as usize {
            return Err(RuntimeError::BudgetExceeded(
                "task tree width/depth limit".into(),
            ));
        }
        state
            .tasks
            .insert(child.clone(), TaskBudget { depth, spent: 0 });
        Ok(())
    }

    pub fn reserve(
        &self,
        task: &TaskId,
        input: u64,
        output: u64,
        model_calls: u32,
    ) -> Result<BudgetReservation> {
        let total = input
            .checked_add(output)
            .ok_or_else(|| RuntimeError::BudgetExceeded("reservation overflow".into()))?;
        let mut state = self.0.lock().map_err(|_| RuntimeError::Poisoned)?;
        check_time(&state)?;
        let task_state = state.tasks.get(task).ok_or(RuntimeError::NotFound)?;
        let held: u64 = state
            .reservations
            .values()
            .map(|a| a.input + a.output)
            .sum();
        let held_task: u64 = state
            .reservations
            .values()
            .filter(|a| &a.task == task)
            .map(|a| a.input + a.output)
            .sum();
        let calls: u64 = state.reservations.values().map(|a| a.calls as u64).sum();
        if total
            > state
                .limits
                .total_tokens
                .saturating_sub(state.spent)
                .saturating_sub(held)
            || state.spent > state.limits.total_tokens
            || total
                > state
                    .limits
                    .task_tokens
                    .saturating_sub(task_state.spent)
                    .saturating_sub(held_task)
            || task_state.spent > state.limits.task_tokens
            || state.calls as u64 + calls + model_calls as u64 > state.limits.max_model_calls as u64
        {
            return Err(RuntimeError::BudgetExceeded(
                "shared input/output/call budget".into(),
            ));
        }
        state.next += 1;
        let id = state.next;
        state.reservations.insert(
            id,
            Allocation {
                task: task.clone(),
                input,
                output,
                calls: model_calls,
                started: false,
            },
        );
        Ok(BudgetReservation {
            tree: self.clone(),
            id,
            finished: false,
        })
    }

    pub fn snapshot(&self) -> Result<BudgetSnapshot> {
        let state = self.0.lock().map_err(|_| RuntimeError::Poisoned)?;
        let held = state
            .reservations
            .values()
            .map(|a| a.input + a.output)
            .sum::<u64>();
        let calls = state.reservations.values().map(|a| a.calls).sum::<u32>();
        Ok(BudgetSnapshot {
            limits: state.limits.clone(),
            spent_tokens: state.spent,
            reserved_tokens: held,
            model_calls: state.calls,
            reserved_model_calls: calls,
            task_count: state.tasks.len(),
            elapsed_ms: state.start.elapsed().as_millis().min(u64::MAX as u128) as u64,
            exhausted: state.spent.saturating_add(held) >= state.limits.total_tokens
                || state.calls.saturating_add(calls) >= state.limits.max_model_calls
                || check_time(&state).is_err(),
            settlements: state.settlements.clone(),
        })
    }
}

fn check_time(state: &State) -> Result<()> {
    if state.start.elapsed() >= Duration::from_millis(state.limits.timeout_ms) {
        Err(RuntimeError::BudgetExceeded("task deadline elapsed".into()))
    } else {
        Ok(())
    }
}

/// An unstarted reservation is refunded on drop. Once started, a lost/failed
/// request is conservatively charged its reservation unless actual usage is
/// settled. Retries must make a fresh reservation and consume another call.
#[derive(Debug)]
pub struct BudgetReservation {
    tree: BudgetTree,
    id: u64,
    finished: bool,
}
impl BudgetReservation {
    pub fn start(&mut self) -> Result<()> {
        let mut state = self.tree.0.lock().map_err(|_| RuntimeError::Poisoned)?;
        check_time(&state)?;
        let allocation = state
            .reservations
            .get_mut(&self.id)
            .ok_or(RuntimeError::NotFound)?;
        if allocation.started {
            return Err(RuntimeError::InvalidState(
                "reservation already started".into(),
            ));
        }
        allocation.started = true;
        Ok(())
    }
    pub fn settle(mut self, usage: Usage) -> Result<()> {
        let result = self.settle_inner(Some(usage));
        // A budget excess is an accepted accounting fact, not an excuse to
        // erase real usage. settle_inner removes the reservation in that case.
        if let Ok(state) = self.tree.0.lock() {
            self.finished = !state.reservations.contains_key(&self.id);
        }
        result
    }
    pub fn release(mut self) -> Result<()> {
        let mut state = self.tree.0.lock().map_err(|_| RuntimeError::Poisoned)?;
        let allocation = state
            .reservations
            .get(&self.id)
            .ok_or(RuntimeError::NotFound)?;
        if allocation.started {
            return Err(RuntimeError::InvalidState(
                "cannot refund a dispatched request".into(),
            ));
        }
        state.reservations.remove(&self.id);
        self.finished = true;
        Ok(())
    }
    fn settle_inner(&self, usage: Option<Usage>) -> Result<()> {
        let mut state = self.tree.0.lock().map_err(|_| RuntimeError::Poisoned)?;
        let allocation = state
            .reservations
            .get(&self.id)
            .ok_or(RuntimeError::NotFound)?;
        if !allocation.started {
            if usage.is_some() {
                return Err(RuntimeError::InvalidState(
                    "request was never started".into(),
                ));
            }
            state.reservations.remove(&self.id);
            return Ok(());
        }
        let usage = usage.unwrap_or(Usage {
            input_tokens: allocation.input,
            output_tokens: allocation.output,
            model_calls: allocation.calls,
            estimated: true,
        });
        let total = usage.total()?;
        if usage.model_calls < allocation.calls {
            return Err(RuntimeError::InvalidState(
                "dispatched calls cannot be refunded".into(),
            ));
        }
        let allocation = state.reservations.remove(&self.id).unwrap();
        state.spent = state.spent.saturating_add(total);
        state.calls = state.calls.saturating_add(usage.model_calls);
        let task = state.tasks.get_mut(&allocation.task).unwrap();
        task.spent = task.spent.saturating_add(total);
        let task_spent = task.spent;
        state.settlements.push(usage);
        let held = state
            .reservations
            .values()
            .map(|a| a.input + a.output)
            .sum::<u64>();
        let held_task = state
            .reservations
            .values()
            .filter(|a| a.task == allocation.task)
            .map(|a| a.input + a.output)
            .sum::<u64>();
        let held_calls = state
            .reservations
            .values()
            .map(|a| a.calls as u64)
            .sum::<u64>();
        let over = state.spent.saturating_add(held) > state.limits.total_tokens
            || task_spent.saturating_add(held_task) > state.limits.task_tokens
            || state.calls as u64 + held_calls > state.limits.max_model_calls as u64;
        if over {
            Err(RuntimeError::BudgetExceeded(
                "actual usage recorded; remaining task budget exceeded".into(),
            ))
        } else {
            Ok(())
        }
    }
}
impl Drop for BudgetReservation {
    fn drop(&mut self) {
        if !self.finished {
            let _ = self.settle_inner(None);
        }
    }
}
