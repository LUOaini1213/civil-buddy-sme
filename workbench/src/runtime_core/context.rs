use super::{Result, RuntimeError};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::collections::HashSet;

#[derive(Debug, Clone)]
pub struct ContextRequest {
    pub system: Vec<Value>,
    pub history: Vec<Value>,
    /// The current user message followed by complete assistant tool calls/results.
    /// This entire slice is immutable: it is never shortened to fit a window.
    pub current: Vec<Value>,
    pub tools: Vec<Value>,
    pub output_reserve: u64,
    pub window: u64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ContextReport {
    pub estimated: bool,
    pub counter: String,
    pub input_estimate: u64,
    pub output_reserve: u64,
    pub window: u64,
    pub system_bytes: u64,
    pub history_bytes: u64,
    pub current_bytes: u64,
    pub tools_bytes: u64,
    pub omitted_history_messages: usize,
    pub omitted_history_groups: usize,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PreparedContext {
    pub messages: Vec<Value>,
    pub tools: Vec<Value>,
    pub report: ContextReport,
}

fn bytes<T: Serialize + ?Sized>(value: &T) -> Result<u64> {
    Ok(serde_json::to_vec(value)?.len() as u64)
}

/// Validate the final adapter payload too, after adding model/provider fields.
/// UTF-8 serialized bytes plus framing margin is a deliberately conservative
/// estimate, not the provider's tokenizer or billed usage.
pub fn check_request_budget(payload: &Value, output_reserve: u64, window: u64) -> Result<u64> {
    let estimate = bytes(payload)?
        .checked_add(64)
        .ok_or_else(|| RuntimeError::InvalidContext("request size overflow".into()))?;
    if output_reserve == 0
        || estimate
            .checked_add(output_reserve)
            .is_none_or(|n| n > window)
    {
        return Err(RuntimeError::ContextOverflow {
            input_estimate: estimate,
            output_reserve,
            window,
        });
    }
    Ok(estimate)
}

fn role(message: &Value) -> Result<&str> {
    message
        .get("role")
        .and_then(Value::as_str)
        .ok_or_else(|| RuntimeError::InvalidContext("message is missing role".into()))
}

fn validate_dialogue(messages: &[Value]) -> Result<()> {
    let mut pending = HashSet::new();
    let mut seen = HashSet::new();
    for message in messages {
        let r = role(message)?;
        if r == "tool" {
            let id = message
                .get("tool_call_id")
                .and_then(Value::as_str)
                .unwrap_or("");
            if id.is_empty() || !pending.remove(id) {
                return Err(RuntimeError::InvalidContext(
                    "orphan or duplicate tool result".into(),
                ));
            }
            continue;
        }
        if !pending.is_empty() {
            return Err(RuntimeError::InvalidContext(
                "tool calls must be followed by all their results".into(),
            ));
        }
        if !matches!(r, "user" | "assistant") {
            return Err(RuntimeError::InvalidContext(
                "system/developer messages belong in system, not history/current".into(),
            ));
        }
        if let Some(calls) = message.get("tool_calls").filter(|v| !v.is_null()) {
            if r != "assistant" {
                return Err(RuntimeError::InvalidContext(
                    "only assistant can call tools".into(),
                ));
            }
            let calls = calls.as_array().ok_or_else(|| {
                RuntimeError::InvalidContext("tool_calls must be an array".into())
            })?;
            for call in calls {
                let id = call.get("id").and_then(Value::as_str).unwrap_or("");
                if id.is_empty() || !seen.insert(id.to_owned()) {
                    return Err(RuntimeError::InvalidContext(
                        "tool call IDs must be nonempty and unique".into(),
                    ));
                }
                pending.insert(id.to_owned());
            }
        }
    }
    if !pending.is_empty() {
        return Err(RuntimeError::InvalidContext(
            "missing tool results; do not send an incomplete interaction".into(),
        ));
    }
    Ok(())
}

fn payload(
    system: &[Value],
    history: &[Value],
    current: &[Value],
    tools: &[Value],
    reserve: u64,
) -> Value {
    let messages: Vec<_> = system
        .iter()
        .chain(history)
        .chain(current)
        .cloned()
        .collect();
    json!({"messages": messages, "tools": tools, "max_tokens": reserve})
}

pub fn prepare_context(request: ContextRequest) -> Result<PreparedContext> {
    if request.current.is_empty() || role(&request.current[0])? != "user" {
        return Err(RuntimeError::InvalidContext(
            "current must begin with the current user message".into(),
        ));
    }
    for message in &request.system {
        if !matches!(role(message)?, "system" | "developer") {
            return Err(RuntimeError::InvalidContext(
                "system contains a non-system message".into(),
            ));
        }
    }
    validate_dialogue(&request.history)?;
    validate_dialogue(&request.current)?;
    // Reject oversized required content before considering optional history.
    let required = payload(
        &request.system,
        &[],
        &request.current,
        &request.tools,
        request.output_reserve,
    );
    check_request_budget(&required, request.output_reserve, request.window)?;

    // Only discard whole oldest user-turn groups. A call/result pair, all
    // parallel results, reasoning metadata and tool text stay together.
    let mut starts = vec![0];
    for (index, message) in request.history.iter().enumerate().skip(1) {
        if role(message)? == "user" {
            starts.push(index);
        }
    }
    starts.push(request.history.len());
    starts.dedup();
    let mut removed_groups = 0;
    let mut start = 0;
    let (messages, input_estimate) = loop {
        let proposed = payload(
            &request.system,
            &request.history[start..],
            &request.current,
            &request.tools,
            request.output_reserve,
        );
        match check_request_budget(&proposed, request.output_reserve, request.window) {
            Ok(used) => break (proposed["messages"].as_array().unwrap().clone(), used),
            Err(RuntimeError::ContextOverflow { .. }) if start < request.history.len() => {
                removed_groups += 1;
                start = starts[removed_groups];
            }
            Err(err) => return Err(err),
        }
    };
    Ok(PreparedContext {
        messages,
        report: ContextReport {
            estimated: true,
            counter: "serialized-utf8-bytes-plus-framing".into(),
            input_estimate,
            output_reserve: request.output_reserve,
            window: request.window,
            system_bytes: bytes(&request.system)?,
            history_bytes: bytes(&request.history[start..])?,
            current_bytes: bytes(&request.current)?,
            tools_bytes: bytes(&request.tools)?,
            omitted_history_messages: start,
            omitted_history_groups: removed_groups,
        },
        tools: request.tools,
    })
}
