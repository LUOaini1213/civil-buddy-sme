//! Explicit source-bound packing calculations. Neither model can submit cargo,
//! solver options or a new state; Jev observes host-built solved candidates only.
use super::{providers, tools::sha256, worker::WorkerHost};
use crate::runtime_core::{BudgetTree, CancellationToken, TaskId, WorkspaceContext};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::{
    collections::{BTreeMap, HashMap, HashSet},
    io::Read,
};

pub const MAX_SOURCES: usize = 2;
const MAX_SOURCE_BYTES: u64 = 2 * 1024 * 1024;
type Result<T> = std::result::Result<T, String>;

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct PackingSelection {
    pub source: String,
    /// An omitted digest is captured by the host before the turn is created.
    /// Stored requests and worker calls always carry the resulting digest.
    #[serde(default)]
    pub source_sha256: Option<String>,
}

fn valid_hash(value: &str) -> bool {
    value.len() == 64 && value.bytes().all(|b| b.is_ascii_hexdigit())
}

fn source_hash(workspace: &WorkspaceContext, source: &str) -> Result<String> {
    let path = workspace.resolve_read(source).map_err(|e| e.to_string())?;
    if !path
        .extension()
        .and_then(|s| s.to_str())
        .is_some_and(|s| s.eq_ignore_ascii_case("json"))
    {
        return Err("装箱重排仅接受显式 packing_replan.v1 JSON 原件；柜型和约束不得推测".into());
    }
    let file = std::fs::File::open(path).map_err(|_| "无法读取所选装箱原件")?;
    let mut bytes = Vec::new();
    file.take(MAX_SOURCE_BYTES + 1)
        .read_to_end(&mut bytes)
        .map_err(|_| "装箱原件读取失败")?;
    if bytes.is_empty() || bytes.len() as u64 > MAX_SOURCE_BYTES {
        return Err("装箱原件必须非空且不超过2MiB".into());
    }
    Ok(sha256(&bytes))
}

pub fn bind_selections(
    workspace: &WorkspaceContext,
    files: &[String],
    selections: &mut [PackingSelection],
) -> Result<()> {
    if selections.len() > MAX_SOURCES {
        return Err("每轮最多选择两份装箱原件".into());
    }
    let mut seen = HashSet::new();
    for selection in selections {
        if !files.contains(&selection.source) || !seen.insert(selection.source.clone()) {
            return Err("装箱原件必须是本轮已选文件，且不得重复".into());
        }
        let hash = source_hash(workspace, &selection.source)?;
        if selection
            .source_sha256
            .as_deref()
            .is_some_and(|expected| !valid_hash(expected) || !expected.eq_ignore_ascii_case(&hash))
        {
            return Err("装箱原件摘要已变化，请重新选择并核对原件".into());
        }
        selection.source_sha256 = Some(hash);
    }
    Ok(())
}

pub fn verify_selection(workspace: &WorkspaceContext, selection: &PackingSelection) -> Result<()> {
    let expected = selection
        .source_sha256
        .as_deref()
        .filter(|hash| valid_hash(hash))
        .ok_or("装箱选择未绑定原件摘要")?;
    if source_hash(workspace, &selection.source)? != expected {
        return Err("装箱原件在本轮期间已变化，拒绝把旧结果当作当前结果".into());
    }
    Ok(())
}

pub fn selection_index(args: &Value, selections: &[PackingSelection]) -> Option<usize> {
    args.as_object().filter(|object| object.len() == 1)?;
    let index = usize::try_from(args["selection_index"].as_u64()?).ok()?;
    (index < selections.len()).then_some(index)
}

pub fn definition(count: usize) -> Value {
    json!({"type":"function","function":{"name":"packing_replan",
        "description":"核对用户显式选定并绑定哈希的装箱JSON，执行确定性基线及最多两轮合法顺序重排复算。唯一参数是选集索引。柜型、尺寸、数量、重量和运输要求只读原件，不能由模型补写。结果不构成装运放行。",
        "parameters":{"type":"object","properties":{"selection_index":{"type":"integer","minimum":0,"maximum":count.saturating_sub(1)}},"required":["selection_index"],"additionalProperties":false}}})
}

#[derive(Default)]
pub struct PackingTurn {
    results: HashMap<String, Value>,
    attempted: HashSet<String>,
    // Independent of document-evidence decisions; one bounded phase/hash entry
    // for each of the at-most-two explicitly selected immutable sources.
    decisions: HashMap<String, Value>,
}

impl PackingTurn {
    pub async fn calculate(
        &mut self,
        worker: &WorkerHost,
        workspace: &WorkspaceContext,
        selection: &PackingSelection,
        cfg: &providers::JevConfig,
        budget: &BudgetTree,
        task: &TaskId,
        cancel: &CancellationToken,
    ) -> Result<Value> {
        cancel.check().map_err(|e| e.to_string())?;
        verify_selection(workspace, selection)?;
        let key = format!(
            "{}:{}",
            selection.source,
            selection.source_sha256.as_deref().unwrap()
        );
        if let Some(previous) = self.results.get(&key) {
            let mut previous = previous.clone();
            previous["provenance"]["replayed_in_turn"] = json!(true);
            return Ok(previous);
        }
        if self.attempted.contains(&key) {
            return Err("本轮已经尝试此装箱原件；查看先前失败记录，不重复消耗复算轮数".into());
        }
        if self.attempted.len() >= MAX_SOURCES {
            return Err("装箱重排达到本轮来源上限".into());
        }
        self.attempted.insert(key.clone());
        let mut response = worker
            .engineering(
                workspace,
                "packing_replan",
                json!({
            "source":selection.source,"expected_sha256":selection.source_sha256}),
                cancel,
            )
            .await?;
        verify_selection(workspace, selection)?;
        if response["ok"] == true {
            validate_result(&response["result"], selection)?;
            let (context, questions) = decision_context(&response["result"], selection)?;
            let context_hash = sha256(context.to_string().as_bytes());
            let decision = if let Some(previous) = self.decisions.get(&context_hash) {
                previous.clone()
            } else {
                let decision =
                    shadow_decision(cfg, context, questions, &context_hash, budget, task, cancel)
                        .await?;
                self.decisions.insert(context_hash, decision.clone());
                decision
            };
            // A delayed decision must never bless a stale source revision.
            verify_selection(workspace, selection)?;
            response["decision_proposal"] = decision;
        }
        response["provenance"] = json!({"source":selection.source,"source_sha256":selection.source_sha256,
            "originals_unchanged":true,"selection_scope":"explicit_current_turn","writes":false,
            "shipping_release":false,"professional_signoff":false,"replayed_in_turn":false});
        self.results.insert(key, response.clone());
        Ok(response)
    }
}

fn validate_summary(summary: &Value) -> bool {
    summary.is_object()
        && summary["can_fit"].is_boolean()
        && summary["layout_verified"].is_boolean()
        && summary["n_boxes"].as_u64().is_some()
        && (summary["containers_used"].is_null() || summary["containers_used"].as_u64().is_some())
        && (summary["worst_mid50"].is_null()
            || summary["worst_mid50"].as_f64().is_some_and(f64::is_finite))
        && summary["plan_sha256"].as_str().is_some_and(valid_hash)
}

fn validate_result(result: &Value, selection: &PackingSelection) -> Result<()> {
    if result["kind"] != "packing_replan"
        || result["schema"] != "packing_replan.result.v1"
        || result["source"]["path"] != selection.source
        || result["source"]["sha256"] != json!(selection.source_sha256)
        || result["originals_unchanged"] != true
        || result["professional_signoff"] != false
        || result["hard_constraints"]["shipping_release"] != false
        || result["hard_constraints"]["originals_immutable"] != true
        || result["hard_constraints"]["container_upgrades"] != false
        || result["hard_constraints"]["max_rounds"] != 2
    {
        return Err("装箱结果未通过来源与约束协议校验".into());
    }
    let rounds = result["rounds"]
        .as_array()
        .filter(|r| r.len() <= 2)
        .ok_or("装箱复算轮数格式无效或超过两轮")?;
    if result["status"] == "needs_human" && result["outcome"] == "needs_human" {
        if result["needs_human"].as_array().is_none_or(Vec::is_empty) || !rounds.is_empty() {
            return Err("装箱待补资料结果缺少明确原因".into());
        }
    } else if result["status"] != "completed"
        || !matches!(result["outcome"].as_str(), Some("improved" | "unchanged"))
        || !validate_summary(&result["baseline"])
        || !validate_summary(&result["final"])
        || !result["constraints_sha256"]
            .as_str()
            .is_some_and(valid_hash)
    {
        return Err("装箱基线或最终复算结果格式无效".into());
    }
    for (index, round) in rounds.iter().enumerate() {
        if round["round"] != index + 1
            || !round["accepted"].is_boolean()
            || round["candidate_id"]
                .as_str()
                .is_none_or(|s| s.is_empty() || s.len() > 80)
            || !round["parameters_sha256"].as_str().is_some_and(valid_hash)
            || (!validate_summary(&round["summary"])
                && !(round["summary"].is_null()
                    && round["accepted"] == false
                    && round["reason"].as_str().is_some_and(|s| !s.is_empty())))
        {
            return Err("装箱候选未通过固定复算协议校验".into());
        }
    }
    if result["status"] == "completed"
        && result["final"]["plan_sha256"] != result["baseline"]["plan_sha256"]
        && !rounds.iter().any(|round| {
            round["accepted"] == true
                && round["summary"]["plan_sha256"] == result["final"]["plan_sha256"]
        })
    {
        return Err("最终装箱结果不属于基线或已接受的复算候选".into());
    }
    Ok(())
}

fn decision_context(
    result: &Value,
    selection: &PackingSelection,
) -> Result<(Value, BTreeMap<String, providers::DecisionQuestion>)> {
    let mut candidates = BTreeMap::new();
    candidates.insert(
        "keep_baseline".to_owned(),
        json!({"summary":summary_for_decision(&result["baseline"])}),
    );
    if let Some(rounds) = result["rounds"].as_array() {
        for (index, round) in rounds.iter().enumerate() {
            if round["summary"]["can_fit"] == true && round["summary"]["layout_verified"] == true {
                candidates.insert(
                    format!("round_{}", index + 1),
                    json!({"summary":summary_for_decision(&round["summary"])}),
                );
            }
        }
    }
    let criteria = candidates.keys().map(|id| (id.clone(), if id == "keep_baseline" {
        "Keep the original deterministic baseline; no approval or shipment release.".into()
    } else { "Consider this already-recomputed fixed-order candidate; no new solver inputs or approval.".into() })).collect();
    let questions = BTreeMap::from([("preferred_recomputed_candidate".into(), providers::DecisionQuestion::Choice {
        instructions:"Shadow-only comparison of the host-listed deterministic results. Do not invent dimensions, capacities, restraints or shipment approval. This observation cannot change the selected plan.".into(), criteria })]);
    Ok((
        json!({"phase":"packing_replan_shadow_v1","source_sha256":selection.source_sha256,
        "status":result["status"],"outcome":result["outcome"],"candidates":candidates,
        "hard_constraints":{"originals_immutable":true,"shipping_release":false,"professional_signoff":false,
            "max_rounds":2,"container_upgrades":false,"decision_applied":false}}),
        questions,
    ))
}

fn summary_for_decision(summary: &Value) -> Value {
    if summary.is_null() {
        return Value::Null;
    }
    json!({"can_fit":summary["can_fit"],"layout_verified":summary["layout_verified"],
        "containers_used":summary["containers_used"],"worst_mid50":summary["worst_mid50"],
        "n_boxes":summary["n_boxes"],"plan_sha256":summary["plan_sha256"],
        "structure":{"pass":summary["structure"]["pass"],"needs_reinforcement":summary["structure"]["needs_reinforcement"],
            "fail":summary["structure"]["fail"],"pending_design":summary["structure"]["pending_design"]}})
}

/// The event retains the complete solver audit. The language model receives
/// bounded receipts only, never thousands of repeated placements/raw plans.
pub fn model_result_summary(response: &Value) -> Value {
    if response["result"]["kind"] != "packing_replan" {
        return response.clone();
    }
    let result = &response["result"];
    let compact = |summary: &Value| {
        if summary.is_null() {
            return Value::Null;
        }
        let mut item = summary_for_decision(summary);
        item["engine"] = summary["engine"].clone();
        item["structure"] = json!({"pass":summary["structure"]["pass"],"fail":summary["structure"]["fail"],
            "needs_reinforcement":summary["structure"]["needs_reinforcement"],"pending_design":summary["structure"]["pending_design"],"n_boxes":summary["structure"]["n_boxes"],
            "failing":bounded_items(&summary["structure"]["failing"],5,2000)});
        item
    };
    let rounds: Vec<Value> = result["rounds"].as_array().into_iter().flatten().take(2).map(|round| json!({
        "round":round["round"],"candidate_id":round["candidate_id"],"parameters_sha256":round["parameters_sha256"],
        "accepted":round["accepted"],"reason":round["reason"],"error_type":round["error_type"],"summary":compact(&round["summary"])})).collect();
    json!({"ok":response["ok"],"result":{"kind":"packing_replan","schema":result["schema"],"status":result["status"],
        "source":result["source"],"originals_unchanged":result["originals_unchanged"],"baseline":compact(&result["baseline"]),
        "final":compact(&result["final"]),"rounds":rounds,"outcome":result["outcome"],"constraints_sha256":result["constraints_sha256"],
        "requires_human_review":result["requires_human_review"],"stop_reason":result["stop_reason"],
        "needs_human":bounded_items(&result["needs_human"],8,6000),"needs_human_count":result["needs_human"].as_array().map_or(0,Vec::len),
        "limitations":bounded_items(&result["limitations"],8,4000),"professional_signoff":false,"shipping_release":false},
        "provenance":response["provenance"],"decision_proposal":compact_decision(&response["decision_proposal"]),
        "detail_scope":"bounded_summary_from_fixed_worker","layout_and_boxes_omitted":true,"complete_audit":"tool_finished_event"})
}

/// A human-readable receipt from validated fixed-tool results. Never inspect
/// model prose here, and never turn a missing count into a numeric zero.
pub fn receipt_report(
    selections: &[PackingSelection],
    receipts: &HashMap<usize, Value>,
    completed: &HashSet<usize>,
    successful_tools: &[String],
    tool_errors: usize,
    locale: &str,
) -> String {
    let en = locale == "en";
    let unknown = if en { "not returned" } else { "未返回" };
    let count = |value: &Value| value.as_u64().map(|n| n.to_string()).unwrap_or_else(|| unknown.into());
    let yes_no = |value: &Value| match value.as_bool() {
        Some(true) => if en { "yes" } else { "是" },
        Some(false) => if en { "no" } else { "否" },
        None => unknown,
    };
    let mut lines = vec![if en { "## Packing execution receipt" } else { "## 装箱执行回执" }.to_owned()];
    for (index, selection) in selections.iter().enumerate() {
        lines.push(format!("\n### {} {}", if en { "Selected source" } else { "所选来源" }, index + 1));
        lines.push(format!("- {}: {}", if en { "Source" } else { "文件" }, receipt_literal(&selection.source)));
        lines.push(format!("- {}: {}", if en { "Selected source SHA-256" } else { "所选原件 SHA-256" },
            receipt_literal(selection.source_sha256.as_deref().unwrap_or(unknown))));
        let Some(response) = receipts.get(&index) else {
            lines.push(if en { "- Status: not calculated; no packing tool receipt was recorded for this source." }
                else { "- 状态：未计算；本轮没有这份来源的装箱工具回执。" }.into());
            continue;
        };
        if response["ok"] != true {
            lines.push(if en { "- Status: calculation failed; no usable result is confirmed. See the tool record for the error." }
                else { "- 状态：计算失败；未确认可用结果。错误原因见工具记录。" }.into());
            continue;
        }
        let result = &response["result"];
        if result["status"] == "needs_human" {
            lines.push(if en { "- Status: more input or human clarification is required; no completed packing result. See the tool record for the required information." }
                else { "- 状态：待补资料或人工澄清；没有已完成的装箱结果。所缺信息见工具记录。" }.into());
            continue;
        }
        let status = if result["final"]["can_fit"] == false {
            if en { "does not fit under the recorded constraints; the packing task remains incomplete." }
                else { "在已记录约束下装不下，装箱任务仍未完成。" }
        } else if completed.contains(&index) {
            if en { "calculation recorded; geometric fit does not establish packaging structural suitability." }
                else { "已有计算回执；几何装下不代表包装结构适用。" }
        } else {
            if en { "no completed calculation receipt for this source." } else { "这份来源没有已完成的计算回执。" }
        };
        lines.push(format!("- {}: {status}", if en { "Status" } else { "状态" }));
        for (key, label) in [("baseline", if en { "Baseline" } else { "基线" }), ("final", if en { "Final" } else { "最终" })] {
            let summary = &result[key];
            lines.push(if en {
                format!("- {label}: containers = {}; packaging boxes = {}; geometric fit = {}.",
                    count(&summary["containers_used"]), count(&summary["n_boxes"]), yes_no(&summary["can_fit"]))
            } else {
                format!("- {label}：柜数 = {}；包装箱数 = {} 箱；几何可装下 = {}。",
                    count(&summary["containers_used"]), count(&summary["n_boxes"]), yes_no(&summary["can_fit"]))
            });
        }
        let rounds = result["rounds"].as_array().map(|rows| rows.len().to_string()).unwrap_or_else(|| unknown.into());
        let outcome = match result["outcome"].as_str() {
            Some("improved") => if en { "improved according to the fixed tool comparison" } else { "按固定工具比较结果有所改善" },
            Some("unchanged") => if en { "no improvement" } else { "无改善" },
            _ => unknown,
        };
        lines.push(if en { format!("- Recorded replan rounds: {rounds}; outcome: {outcome}.") }
            else { format!("- 已记录重排轮数：{rounds}；结果：{outcome}。") });
        let structure = &result["final"]["structure"];
        lines.push(if en {
            format!("- Packaging structure counts (unit: boxes): pass = {}; fail = {}; needs reinforcement = {}; pending design = {}.",
                count(&structure["pass"]), count(&structure["fail"]), count(&structure["needs_reinforcement"]), count(&structure["pending_design"]))
        } else {
            format!("- 包装结构状态（单位：箱）：通过 = {}；不通过 = {}；需加固 = {}；待设计 = {}。",
                count(&structure["pass"]), count(&structure["fail"]), count(&structure["needs_reinforcement"]), count(&structure["pending_design"]))
        });
    }
    let tools = if successful_tools.is_empty() { if en { "none recorded" } else { "无成功回执" }.into() }
        else { successful_tools.iter().map(|name| receipt_literal(name)).collect::<Vec<_>>().join(", ") };
    lines.push(format!("\n- {}: {tools}", if en { "Successful main-agent tools this turn" } else { "本轮主代理成功工具" }));
    lines.push(format!("- {}: {tool_errors}", if en { "Failed main-agent tool calls this turn" } else { "本轮主代理工具失败次数" }));
    lines.push(if en { "\nThese receipts do not grant shipping release, engineering approval or professional signoff, and do not certify that every requested action is complete. The AI interpretation is retained separately." }
        else { "\n以上回执不构成装运放行、工程批准或专业签认，也不证明用户要求的所有事项均已完成。AI 解读单独保留。" }.into());
    lines.join("\n")
}

fn receipt_literal(value: &str) -> String {
    // Preserve names while preventing Markdown control characters in a file
    // name from becoming headings, links or an early end to an inline span.
    let value = value.replace('\r', "\\r").replace('\n', "\\n");
    let fence = "`".repeat(value.split(|c| c != '`').map(str::len).max().unwrap_or(0) + 1);
    format!("{fence} {value} {fence}")
}

fn compact_decision(value: &Value) -> Value {
    json!({"phase":value["phase"],"context_hash":value["context_hash"],"mode":value["mode"],"requested_mode":value["requested_mode"],
        "applied":false,"status":value["status"],"reason":value["reason"],"engineering_assist_calibrated":false,
        "choice":value["answers"]["preferred_recomputed_candidate"]["choice"],
        "confidence":value["answers"]["preferred_recomputed_candidate"]["confidence"]})
}

fn bounded_items(value: &Value, max_items: usize, max_bytes: usize) -> Vec<Value> {
    let mut used = 0;
    value
        .as_array()
        .into_iter()
        .flatten()
        .take(max_items)
        .enumerate()
        .filter_map(|(index, item)| {
            let bytes = item.to_string().len();
            if used + bytes > max_bytes {
                Some(json!({"index":index,"omitted":"See complete tool event for this detail"}))
            } else {
                used += bytes;
                Some(item.clone())
            }
        })
        .collect()
}

async fn shadow_decision(
    cfg: &providers::JevConfig,
    context: Value,
    questions: BTreeMap<String, providers::DecisionQuestion>,
    hash: &str,
    budget: &BudgetTree,
    task: &TaskId,
    cancel: &CancellationToken,
) -> Result<Value> {
    let reason = if context["status"] == "needs_human" {
        Some("source_requires_human_input")
    } else if cfg.mode == providers::JevMode::Off {
        Some("mode_off")
    } else if cfg.api_key.is_empty() {
        Some("key_not_configured")
    } else {
        None
    };
    let mut decision = if let Some(reason) = reason {
        json!({"status":"deterministic_fallback","reason":reason})
    } else {
        let mut effective = cfg.clone();
        effective.mode = providers::JevMode::Shadow;
        match providers::decide(&effective, context, questions, budget, task, cancel).await {
            Ok(value) => value,
            Err(error) => {
                cancel.check().map_err(|e| e.to_string())?;
                json!({"status":"deterministic_fallback","reason":error.to_string()})
            }
        }
    };
    decision["phase"] = json!("packing_replan_shadow_v1");
    decision["context_hash"] = json!(hash);
    decision["requested_mode"] = json!(cfg.mode);
    decision["mode"] = json!(if cfg.mode == providers::JevMode::Off {
        "off"
    } else {
        "shadow"
    });
    decision["applied"] = json!(false);
    decision["engineering_assist_calibrated"] = json!(false);
    decision["fallback"] = json!("original_deterministic_path");
    Ok(decision)
}
