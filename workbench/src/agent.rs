use crate::catalog::Expert;
use crate::config::{max_agent_steps, Paths};
use crate::context;
use crate::harness::{self, Run, Ticket};
use crate::llm::{self, LlmError};
use crate::packs::{self, ToolCtx};
use serde_json::{json, Value};
use std::fs;
use std::path::{Path, PathBuf};
use std::sync::OnceLock;

// 意图契约唯一真源：contract/intents.v1.json（与 packing_assistant/understand.py、
// packing_assistant/runtime/expert_skills.py 同读一份；改词表先改契约，见 contract/README.md）。
// include_str 编译期内联：JSON 缺失/损坏在编译期或首次加载即失败（fail-fast，不静默回退）。
const INTENT_CONTRACT_JSON: &str = include_str!("../../contract/intents.v1.json");

struct IntentContract {
    pack_action_zh: Vec<String>,
    packish: Vec<String>,
    phrase_write: Vec<String>,
    write_nouns: Vec<String>,
    ask: Vec<String>,
    tender: Vec<String>,
    // (phrase 小写, expert_id)，保序 = 契约数组顺序（最长优先，首个命中语义）
    strong_match: Vec<(String, String)>,
}

fn contract() -> &'static IntentContract {
    static CELL: OnceLock<IntentContract> = OnceLock::new();
    CELL.get_or_init(|| {
        let v: Value = serde_json::from_str(INTENT_CONTRACT_JSON)
            .expect("contract/intents.v1.json 非法 JSON（fail-fast，禁止静默回退内联词表）");
        let str_list = |key: &str| -> Vec<String> {
            v.get(key)
                .and_then(|x| x.as_array())
                .filter(|a| !a.is_empty())
                .map(|a| {
                    a.iter()
                        .map(|x| {
                            x.as_str()
                                .filter(|s| !s.is_empty())
                                .expect("contract 词表元素必须是非空字符串")
                                .to_string()
                        })
                        .collect()
                })
                .unwrap_or_else(|| panic!("contract 缺字段或非字符串数组: {key}"))
        };
        let strong = v
            .get("strong_match")
            .and_then(|x| x.as_array())
            .filter(|a| !a.is_empty())
            .unwrap_or_else(|| panic!("contract 缺字段 strong_match 或为空"));
        let strong_match = strong
            .iter()
            .map(|item| {
                let pair = item
                    .as_array()
                    .filter(|p| p.len() == 2)
                    .expect("contract strong_match 元素必须是 [phrase, expert_id]");
                let phrase = pair[0]
                    .as_str()
                    .filter(|s| !s.is_empty())
                    .expect("contract strong_match phrase 必须是非空字符串");
                let eid = pair[1]
                    .as_str()
                    .filter(|s| !s.is_empty())
                    .expect("contract strong_match expert_id 必须是非空字符串");
                (phrase.to_lowercase(), eid.to_string())
            })
            .collect();
        IntentContract {
            pack_action_zh: str_list("pack_action_zh"),
            packish: str_list("packish"),
            phrase_write: str_list("phrase_write"),
            write_nouns: str_list("write_nouns"),
            ask: str_list("ask"),
            tender: str_list("tender"),
            strong_match,
        }
    })
}

const READ_ONLY_TOOLS: &[&str] = &[
    "search_kb",
    "read_kb",
    "list_kb",
    "web_search",
    "web_open",
    "list_attachments",
    "read_attachment",
];

fn skill_id_ok(id: &str) -> bool {
    let b = id.as_bytes();
    if b.is_empty() || b.len() > 64 || b[0] == b'-' || b[b.len() - 1] == b'-' {
        return false;
    }
    if id.contains("--") {
        return false;
    }
    id.chars()
        .all(|c| c.is_ascii_lowercase() || c.is_ascii_digit() || c == '-')
}

fn strip_skill_frontmatter(raw: &str) -> &str {
    let t = raw.trim_start();
    if !t.starts_with("---") {
        return raw;
    }
    let rest = &t[3..];
    if let Some(end) = rest.find("\n---") {
        rest[end + 4..].trim_start()
    } else {
        raw
    }
}

fn load_expert_skill_body(expert_id: &str) -> String {
    if !skill_id_ok(expert_id) {
        return String::new();
    }
    let mut cands: Vec<PathBuf> = Vec::new();
    if let Ok(m) = std::env::var("CARGO_MANIFEST_DIR") {
        cands.push(
            PathBuf::from(m)
                .join("..")
                .join(".agents")
                .join("skills")
                .join(expert_id)
                .join("SKILL.md"),
        );
    }
    if let Ok(cwd) = std::env::current_dir() {
        cands.push(
            cwd.join(".agents")
                .join("skills")
                .join(expert_id)
                .join("SKILL.md"),
        );
        cands.push(
            cwd.join("..")
                .join(".agents")
                .join("skills")
                .join(expert_id)
                .join("SKILL.md"),
        );
    }
    for p in cands {
        let p = Path::new(&p);
        if let Ok(raw) = fs::read_to_string(p) {
            let body = strip_skill_frontmatter(&raw).trim();
            if !body.is_empty() {
                return body.to_string();
            }
        }
    }
    String::new()
}

pub fn build_expert_prompt(expert: &Expert, confirm_ok: bool) -> String {
    let pack = packs::pack_help(&expert.id, &expert.category);
    let skill = load_expert_skill_body(&expert.id);
    let skill_block = if skill.is_empty() {
        String::new()
    } else {
        format!("\n本岗 Skill（程序记忆，选用本岗才加载全文）：\n{skill}\n")
    };
    format!(
        r#"你是土木企业工作台里的【{name}】专家（大类：{cat}）。

提问权：全企业任何人都可以向你提问，不限于本部门。施工员可以问财务，商务可以问试验室，工人可以问造价。用户召唤了你，就用你的知识库答。

两类任务同等重要：
A. 问 / 不懂 / 解释 / 科普：用白话讲清楚本专业概念、流程、边界。可以只聊天，不必 write_deliverable。先 search_kb 再答。答完注明依据来自本岗知识、大类共享还是网上检索。
B. 成稿 / 出文件 / 写方案表：按工序独立成稿，写完调用本大类专用工具或 write_deliverable。高风险且未确认则不要写盘。

问其他人：不要读取其他专家的私库。问题明显属于别的专业时，先答你能答的边界，并请用户在左侧改召唤那位专家。

职责：{title}
默认交付：{delivers}
风险：{risk}
标准工序：{pipeline}
{pack}
知识分层（必须用工具，不许假装读过）：
1. 用户上传：有附件先 list_attachments / read_attachment。招标/图纸/表格以用户文件为准，不要用库里的虚构例子顶替。
2. 你的本岗知识 kb/{category}/{id}/ —— search_kb / read_kb，先读「联网核对要点」（文件 web-knowledge.md，2026-08-14 过，只认官方标题）。
3. 大类共享库 kb/{category}/_shared/
4. 公司规则 kb/company/；先认「官方门户与现行口径」（文件 web-portals.md，含 APPBCA-2026-12：2026-10-01 CORENET X 仅 GFA≥5,000 m² 强制）。
5. 现场联网：search_kb 不够、用户要现行网页、或日期可能过期时，必须 web_search；命中后再 web_open 官方页。搜索摘要不是条文。没打开原文的条款号标 unverified。禁止编造条款。PDF 官网请用户上传，不要 web_open PDF。

硬规则（摘要，细节用 read_kb company/hard-rules.md）：
- 不编条款号、强度、岩土参数、综合单价。
- 引用写 全名+年份+条款；没抽到原文就 unverified / UNSPECIFIED。
- 无来源数字写 [A001] 待填。
- 禁止断言：可交差、可提交专家论证、请监理审核后开工、可以开工、报审通过。
- 产出是内部讨论 AI 草稿，不是法定签认件。
- 辖区 CN/SG/EU/DUAL 禁止静默混用。
- 高风险写盘前确认门。当前 confirm_ok={confirm_ok}。
  若用户要成稿且 risk=high 且 confirm_ok 不为 true：只问用户打出「我明白，将由持证人员签认」，不要 write_deliverable。纯提问（A）不受确认门阻挡。

先理解用户再说。可以只聊天，也可以出稿。用户没要求成稿时不要落盘。
成稿走本岗 harness（steps 独占工具），不要用聊天当计算器，不要编 xyz / 单价 / 条款号。
现行口径（税率、门户、通告年份）必须 web_search，命中后再 web_open 官方页；搜索摘要不是条文。

先看上传，再 search_kb；要现行网页就 web_search。中文回答。
{skill_block}"#,
        name = expert.name,
        cat = expert.category_name,
        title = expert.title,
        delivers = expert.delivers,
        risk = expert.risk,
        pipeline = expert.pipeline,
        category = expert.category,
        id = expert.id,
        confirm_ok = confirm_ok,
        pack = pack,
        skill_block = skill_block,
    )
}

fn user_blob(history: &[Value]) -> String {
    history
        .iter()
        .filter(|m| m.get("role").and_then(|v| v.as_str()) == Some("user"))
        .filter_map(|m| m.get("content").and_then(|v| v.as_str()))
        .collect::<Vec<_>>()
        .join("\n")
}

pub fn is_packish(blob: &str) -> bool {
    contract()
        .packish
        .iter()
        .any(|k| blob.contains(k.as_str()))
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Intent {
    Chat,
    Run,
    Both,
}

impl Intent {
    pub fn as_str(self) -> &'static str {
        match self {
            Intent::Chat => "chat",
            Intent::Run => "run",
            Intent::Both => "both",
        }
    }
}

fn has_any(s: &str, keys: &[String]) -> bool {
    keys.iter().any(|k| s.contains(k.as_str()))
}

/// pack 一句话动作（契约 pack_action_zh / pack_action_en）——与 packing_assistant/understand.py 保持一致。
pub fn is_pack_action(blob: &str) -> bool {
    let t = blob.trim();
    if t.is_empty() {
        return false;
    }
    if is_packish(t) {
        return true;
    }
    if has_any(t, &contract().pack_action_zh) {
        return true;
    }
    // parity:pack-action-en — 英文 pack 判定按非字母数字切词后 eq_ignore_ascii_case("pack")，
    // 与 understand.py 的 \bpack\b 正则语义等价；两侧机制不同，契约 pack_action_en 只记录不互译，
    // 锚点由 scripts/test_stack_parity.py 成对校验。
    t.split(|c: char| !c.is_ascii_alphanumeric())
        .any(|w| w.eq_ignore_ascii_case("pack"))
}

/// Understand the user first. Default is chat. Write only when they ask for a draft.
/// 词表全部来自 contract/intents.v1.json（单源）。
pub fn understand(blob: &str) -> Intent {
    let t = blob.trim();
    if t.is_empty() {
        return Intent::Chat;
    }
    let c = contract();
    let pack_action = is_pack_action(t);
    if is_packish(t) {
        return Intent::Run;
    }
    let phrase_write = has_any(t, &c.phrase_write);
    let write = phrase_write || (t.contains('写') && has_any(t, &c.write_nouns));
    let ask = has_any(t, &c.ask);
    let qmark = t.contains('？') || t.contains('?') || t.ends_with('吗');
    let tender = has_any(t, &c.tender);
    if (write || pack_action) && (ask || qmark) {
        return Intent::Both;
    }
    if (ask || qmark) && !write && !pack_action {
        return Intent::Chat;
    }
    if pack_action || write || tender {
        return Intent::Run;
    }
    Intent::Chat
}

/// Implicit skill routing — full `strong_match` table from contract/intents.v1.json
/// (single source, same table as packing_assistant/runtime/expert_skills.py `_STRONG`).
/// 保序遍历取首个命中，与 Python match_skill 的 first-hit 语义对齐。
pub fn match_skill_implicit(text: &str) -> Option<String> {
    let t = text.to_lowercase();
    contract()
        .strong_match
        .iter()
        .find(|(phrase, _)| t.contains(phrase.as_str()))
        .map(|(_, eid)| eid.clone())
}

pub fn is_explain_only(blob: &str) -> bool {
    understand(blob) == Intent::Chat
}

/// Tax evidence is explicit input. Historical KB rates are not current advice.
pub fn offline_explain(_paths: &Paths, blob: &str) -> Option<String> {
    let gst = blob.to_ascii_uppercase().contains("GST") || blob.contains("消费税") || blob.contains("税率");
    if !gst {
        return None;
    }
    let jurisdiction = tax_jurisdiction(blob);
    let rate = tax_rate_input(blob, jurisdiction);
    Some(format!(
        "辖区：{jurisdiction}。GST 税率：{rate}（用户输入待核，非现行适用结论）。\n请补主体、税种、申报期间及可定位的资料原文；IRAS 等历史 KB 条目不证明当前税率。本次未联网核验、未计算税额。内部讨论 AI 草稿，不是税务意见书。submit_blocked=true。"
    ))
}

pub(crate) fn tax_jurisdiction(blob: &str) -> &'static str {
    let words = regex::Regex::new(r"(?i)\b(CN|SG|EU|DUAL|CHINA|SINGAPORE)\b").unwrap();
    let mut cn = blob.contains("中国") || blob.contains("大陆");
    let mut sg = blob.contains("新加坡");
    let mut eu = blob.contains("欧盟");
    let mut dual = blob.contains("双辖区") || blob.contains("多辖区");
    for cap in words.captures_iter(blob) {
        match cap[1].to_ascii_uppercase().as_str() {
            "CN" | "CHINA" => cn = true,
            "SG" | "SINGAPORE" => sg = true,
            "EU" => eu = true,
            "DUAL" => dual = true,
            _ => {},
        }
    }
    if dual || [cn, sg, eu].into_iter().filter(|v| *v).count() > 1 { "DUAL" }
    else if cn { "CN" } else if sg { "SG" } else if eu { "EU" } else { "UNSPECIFIED" }
}

pub(crate) fn tax_rate_input(blob: &str, jurisdiction: &str) -> String {
    if !matches!(jurisdiction, "CN" | "SG" | "EU") {
        return "UNSPECIFIED".into();
    }
    let explicit = regex::Regex::new(r"(?i)(?:用户税率|税率|\bGST(?:\s+rate)?)\s*[:：=]?\s*(\d+(?:\.\d+)?\s*[%％])").unwrap();
    let all_rates = regex::Regex::new(r"\d+(?:\.\d+)?\s*[%％]").unwrap();
    let rates: Vec<_> = explicit.captures_iter(blob).collect();
    if rates.len() == 1 && all_rates.find_iter(blob).count() == 1 {
        rates[0][1].to_string()
    } else {
        "UNSPECIFIED".into()
    }
}

pub fn detect_jurisdiction(blob: &str) -> &'static str {
    let u = blob.to_ascii_uppercase();
    if blob.contains("DUAL") || blob.contains("双辖") {
        "DUAL"
    } else if blob.contains("Eurocode") || u.contains(" EN ") || blob.contains("欧盟") {
        "EU"
    } else if blob.contains("BCA")
        || blob.contains("GeBIZ")
        || blob.contains("SCDF")
        || blob.contains("LTA")
        || u.contains("SS EN")
    {
        "SG"
    } else if blob.contains("JGJ") || blob.contains("住建") || blob.contains("国标") {
        "CN"
    } else {
        "SG"
    }
}

pub fn ticket_from_chat(session: &str, expert: &Expert, history: &[Value], confirm_ok: bool) -> Ticket {
    let brief = user_blob(history);
    let project = brief
        .lines()
        .map(str::trim)
        .find(|l| !l.is_empty())
        .map(|l| l.chars().take(40).collect::<String>())
        .filter(|s| !s.is_empty())
        .unwrap_or_else(|| expert.name.clone());
    Ticket {
        session: session.to_string(),
        project,
        jurisdiction: detect_jurisdiction(&brief).into(),
        brief,
        path: String::new(),
        confirm_ok,
    }
}

pub fn format_run_text(label: &str, run: &Run) -> String {
    let mut out = format!(
        "【{label}】Harness mode=`{}` · run_id={}\n内部讨论 AI 草稿，不是法定签认件。缺的数字 [A001] / UNSPECIFIED。\n",
        run.mode, run.run_id
    );
    if let Some(e) = &run.error {
        out.push_str(e);
        out.push('\n');
        return out;
    }
    if run.hitl.pending {
        out.push_str("HITL：高风险写盘未确认。请勾选「我明白，将由持证人员签认」后再发一次。本岗未出稿。\n");
    }
    out.push_str("\n## Runtime 步骤\n");
    for s in &run.steps {
        out.push_str(&format!(
            "- {} · {} / {} · legal={} ok={}\n  {}\n",
            s.name, s.expert, s.tool, s.legal, s.ok, s.note
        ));
    }
    if !run.files.is_empty() {
        out.push_str("\n## 已出稿\n");
        for f in &run.files {
            let name = f.get("name").and_then(|v| v.as_str()).unwrap_or("?");
            let path = f.get("path").and_then(|v| v.as_str()).unwrap_or("");
            out.push_str(&format!("- {name}\n  `{path}`\n"));
        }
    }
    if !run.notes.is_empty() {
        out.push_str("\n## 记录\n");
        for n in &run.notes {
            out.push_str(&format!("- {n}\n"));
        }
    }
    out.push_str(&format!(
        "\nillegal_tool_calls={}\n",
        run.illegal_count()
    ));
    out
}

pub fn events_from_run(label: &str, expert_id: &str, run: &Run) -> Vec<EventOut> {
    let text = format_run_text(label, run);
    let ctx = context::inspect(
        &[json!({"role": "assistant", "content": text})],
        &[&text],
    );
    let mut events = vec![(
        "status".into(),
        json!({
            "phase": "harness",
            "text": format!("{label} · harness steps · run {}", run.run_id),
            "expert": expert_id,
            "run_id": run.run_id,
        }),
    )];
    for s in &run.steps {
        events.push((
            "status".into(),
            json!({
                "phase": s.name,
                "text": format!("{} · {} legal={} ok={}", s.tool, s.note, s.legal, s.ok),
                "expert": expert_id,
            }),
        ));
    }
    events.push(("context".into(), ctx.to_value()));
    events
}

fn done_from_run(label: &str, expert_id: &str, run: &Run) -> EventOut {
    let text = format_run_text(label, run);
    let ctx = context::inspect(
        &[json!({"role": "assistant", "content": text})],
        &[&text],
    );
    (
        "done".into(),
        json!({
            "mode": "expert",
            "harness": true,
            "runtime": run.mode,
            "intent": "run",
            "expert": expert_id,
            "text": text,
            "citations": [],
            "deliverables": run.files.clone(),
            "run_id": run.run_id,
            "hitl": {
                "required": run.hitl.required,
                "confirmed": run.hitl.confirmed,
                "pending": run.hitl.pending,
                "gate": run.hitl.gate,
            },
            "illegal_tool_calls": run.illegal_count(),
            "intent": "run",
            "wrote": !run.files.is_empty(),
            "submit_blocked": true,
            "stamp": chrono::Local::now().format("%Y-%m-%dT%H-%M-%S").to_string(),
            "context": ctx.to_value(),
        }),
    )
}

#[allow(dead_code)]
fn fill_empty_source_args(expert: &Expert, name: &str, args: &mut Value, history: &[Value]) {
    let blob = user_blob(history);
    if blob.trim().is_empty() {
        return;
    }
    let empty_key = |args: &Value, k: &str| {
        args.get(k)
            .and_then(|v| v.as_str())
            .map(|s| s.trim().is_empty())
            .unwrap_or(true)
    };
    if name == "bid-parse__extract" && empty_key(args, "tender_text") && empty_key(args, "text") {
        args["tender_text"] = json!(blob);
    }
    if name == "construction__scheme_draft"
        && empty_key(args, "work_scope")
        && empty_key(args, "known_facts")
    {
        args["work_scope"] = json!(blob);
    }
    if (name.ends_with("__extract") || name.ends_with("__scheme_draft"))
        && empty_key(args, "project_name")
    {
        args["project_name"] = json!(expert.name.clone());
    }
}

pub fn finish_if_no_deliverable(
    expert: &Expert,
    history: &[Value],
    ctx: &mut ToolCtx,
) -> Option<String> {
    let blob = user_blob(history);
    if blob.chars().count() < 8 {
        return None;
    }
    let packish = is_packish(&blob);
    if packish {
        let v = crate::firm::run_bid_job(
            &ctx.paths,
            &ctx.session_id,
            &json!({
                "brief": blob,
                "project_name": expert.name,
                "confirm_ok": ctx.confirm_ok,
            }),
        );
        if v.get("ok").and_then(|x| x.as_bool()) == Some(true) {
            if let Some(arr) = v.get("files").and_then(|x| x.as_array()) {
                for f in arr {
                    ctx.deliverables.push(f.clone());
                }
            }
            return Some(v.to_string());
        }
    }
    let (tool, args) = match expert.id.as_str() {
        "bid-parse" => (
            "bid-parse__extract",
            json!({"tender_text": blob, "project_name": expert.name}),
        ),
        "construction" => (
            "construction__scheme_draft",
            json!({
                "work_scope": blob,
                "project_name": expert.name,
                "known_facts": blob
            }),
        ),
        _ => return None,
    };
    let out = packs::execute(ctx, tool, &args);
    if out.contains("已写入") {
        Some(out)
    } else {
        None
    }
}

pub fn plain_system() -> &'static str {
    "你是 DeepSeek，在土木工作台里以「未召唤专家」模式回答。没有岗位知识库，没有出稿工具。可以科普、讨论、列提纲，但必须声明：这不是专家稿，条款和数字需要用户自行核对。不要假装引用了企业规范库。用户若要可核验成稿，请他们在左侧召唤专家。"
}

#[derive(Clone, Debug)]
pub enum LlmMode {
    Live,
    FakePlain { text: String },
    /// Test-only: the turn stays open until cancel drops this future.
    Hold,
}

pub type EventOut = (String, Value);

pub async fn run_plain(history: Vec<Value>, mode: &LlmMode) -> Result<Vec<EventOut>, LlmError> {
    let mut events = vec![(
        "status".into(),
        json!({"phase": "plain", "text": "未召唤专家 · 普通 DeepSeek"}),
    )];
    match mode {
        LlmMode::FakePlain { text } => {
            let ctx = context::inspect(&history, &[plain_system(), text]);
            events.push(("context".into(), ctx.to_value()));
            events.push(("token".into(), json!({"text": text})));
            events.push((
                "done".into(),
                json!({"mode": "plain", "text": text, "citations": [], "deliverables": [], "context": ctx.to_value()}),
            ));
        }
        LlmMode::Hold => {
            std::future::pending::<()>().await;
            unreachable!("hold ends only when the turn is cancelled");
        }
        LlmMode::Live => {
            let mut messages = vec![json!({"role": "system", "content": plain_system()})];
            messages.extend(history);
            let mut buf = String::new();
            llm::stream_plain(&messages, 0.6, |piece| {
                buf.push_str(piece);
                events.push(("token".into(), json!({"text": piece})));
            })
            .await?;
            let ctx = context::inspect(&messages, &[&buf]);
            events.push(("context".into(), ctx.to_value()));
            events.push((
                "done".into(),
                json!({"mode": "plain", "text": buf, "citations": [], "deliverables": [], "context": ctx.to_value()}),
            ));
        }
    }
    Ok(events)
}

pub async fn run_expert(
    paths: &Paths,
    expert: &Expert,
    history: Vec<Value>,
    confirm_ok: bool,
    session_id: &str,
    mode: &LlmMode,
) -> Result<Vec<EventOut>, LlmError> {
    let mut events = vec![(
        "status".into(),
        json!({
            "phase": "summon",
            "text": format!("已召唤 {} / {} · 独立收工", expert.category_name, expert.name),
            "expert": expert.id,
        }),
    )];

    if matches!(mode, LlmMode::Hold) {
        std::future::pending::<()>().await;
        unreachable!("hold ends only when the turn is cancelled");
    }
    if let LlmMode::FakePlain { text } = mode {
        let ctx = context::inspect(&history, &[text]);
        events.push(("context".into(), ctx.to_value()));
        events.push(("token".into(), json!({"text": text})));
        events.push((
            "done".into(),
            json!({
                "mode": "expert",
                "expert": expert.id,
                "text": text,
                "citations": [],
                "deliverables": [],
                "context": ctx.to_value(),
            }),
        ));
        return Ok(events);
    }

    let blob = user_blob(&history);
    let intent = understand(&blob);
    events.push((
        "status".into(),
        json!({
            "phase": "understand",
            "text": format!("{} · 听懂为 {}（能聊能跑）", expert.name, intent.as_str()),
            "expert": expert.id,
            "intent": intent.as_str(),
        }),
    ));
    match intent {
        Intent::Chat => {
            if let Some(text) = offline_explain(paths, &blob) {
                events.push(("token".into(), json!({"text": text})));
                events.push((
                    "done".into(),
                    json!({
                        "mode": "expert",
                        "intent": "chat",
                        "wrote": false,
                        "submit_blocked": true,
                        "expert": expert.id,
                        "text": text,
                        "deliverables": [],
                    }),
                ));
            } else {
                events.extend(
                    run_expert_explain(paths, expert, history, confirm_ok, session_id).await?,
                );
            }
        }
        Intent::Run | Intent::Both => {
            let ticket = ticket_from_chat(session_id, expert, &history, confirm_ok);
            // ux(round3) 自测修复：harness steps 内含 reqwest::blocking（pack_ship_health
            // → packing_bridge）与文件 IO，必须在阻塞线程池执行；否则 blocking client
            // 在 tokio worker 上 drop 时 panic，SSE 流被无声截断（只收到 context 事件）。
            let paths_owned = paths.clone();
            let expert_owned = expert.clone();
            let run = match crate::turns::blocking(session_id, move || {
                harness::run_expert_steps(&paths_owned, &expert_owned, ticket)
            })
            .await
            {
                Ok(run) => run,
                Err(e) => {
                    events.push((
                        "error".into(),
                        json!({"text": format!("harness join error: {e}")}),
                    ));
                    return Ok(events);
                }
            };
            events.extend(events_from_run(&expert.name, &expert.id, &run));
            match talk_after_run(paths, expert, &history, confirm_ok, session_id, &run).await {
                Ok(talk) => events.extend(talk),
                Err(_) => {
                    let text = format_run_text(&expert.name, &run);
                    events.push(("token".into(), json!({"text": text})));
                    events.push(done_from_run(&expert.name, &expert.id, &run));
                }
            }
        }
    }
    Ok(events)
}

async fn run_expert_explain(
    paths: &Paths,
    expert: &Expert,
    history: Vec<Value>,
    confirm_ok: bool,
    session_id: &str,
) -> Result<Vec<EventOut>, LlmError> {
    let mut events = Vec::new();
    let mut ctx = ToolCtx::new(
        paths.clone(),
        &expert.id,
        &expert.category,
        &expert.risk,
        confirm_ok,
        session_id,
    );
    let tools: Vec<Value> = packs::tools_for_expert(&expert.id)
        .iter()
        .filter(|t| READ_ONLY_TOOLS.contains(&t.name))
        .map(|t| t.openai_tool())
        .collect();
    let mut messages = vec![json!({"role": "system", "content": build_expert_prompt(expert, confirm_ok)})];
    messages.extend(history);
    events.push((
        "status".into(),
        json!({
            "phase": "chat",
            "text": format!("{} · 在跟你说话（可联网，不成稿）", expert.name),
            "expert": expert.id,
        }),
    ));
    events.push((
        "context".into(),
        context::inspect(&messages, &[]).to_value(),
    ));

    let max_steps = max_agent_steps();
    let mut final_text = String::new();
    for step in 0..max_steps {
        events.push((
            "status".into(),
            json!({"phase": "think", "text": format!("{} 解释 {}/{}", expert.name, step + 1, max_steps)}),
        ));
        let msg = llm::chat(&messages, Some(&tools), 0.3).await?;
        let tool_calls = msg.get("tool_calls").and_then(|v| v.as_array()).cloned().unwrap_or_default();
        if tool_calls.is_empty() {
            final_text = msg.get("content").and_then(|v| v.as_str()).unwrap_or("").to_string();
            break;
        }
        messages.push(msg);
        for call in tool_calls {
            let fnx = call.get("function").cloned().unwrap_or(json!({}));
            let name = fnx.get("name").and_then(|v| v.as_str()).unwrap_or("").to_string();
            let raw = fnx.get("arguments").cloned().unwrap_or(json!("{}"));
            let args = match raw {
                Value::String(s) => serde_json::from_str(&s).unwrap_or(json!({})),
                other => other,
            };
            if !READ_ONLY_TOOLS.contains(&name.as_str()) {
                messages.push(json!({
                    "role": "tool",
                    "tool_call_id": call.get("id").and_then(|v| v.as_str()).unwrap_or(&name),
                    "content": "拒绝：解释模式只允许只读工具。成稿请直接下达作业，走 harness steps。",
                }));
                continue;
            }
            let hint = args
                .get("query")
                .or_else(|| args.get("path"))
                .and_then(|v| v.as_str())
                .unwrap_or("");
            events.push((
                "status".into(),
                json!({"phase": name, "text": format!("{} · {} {}", expert.name, name, hint)}),
            ));
            let result = packs::execute(&mut ctx, &name, &args);
            let cut: String = result.chars().take(12000).collect();
            messages.push(json!({
                "role": "tool",
                "tool_call_id": call.get("id").and_then(|v| v.as_str()).unwrap_or(&name),
                "content": cut,
            }));
        }
    }
    if final_text.is_empty() {
        final_text = "（达到步数上限，请把任务拆小或再发一次）".into();
    }

    for chunk in final_text.as_bytes().chunks(40) {
        let text = String::from_utf8_lossy(chunk).to_string();
        events.push(("token".into(), json!({"text": text})));
    }

    let mut uniq = Vec::new();
    let mut seen = std::collections::HashSet::new();
    for c in ctx.citations {
        if let Some(p) = c.get("path").and_then(|v| v.as_str()) {
            if seen.insert(p.to_string()) {
                uniq.push(c);
            }
        }
    }

    let ctx_end = context::inspect(&messages, &[&final_text]);
    events.push(("context".into(), ctx_end.to_value()));
    events.push((
        "done".into(),
        json!({
            "mode": "expert",
            "harness": true,
            "runtime": "chat",
            "intent": "chat",
            "expert": expert.id,
            "text": final_text,
            "citations": uniq,
            "deliverables": ctx.deliverables,
            "stamp": chrono::Local::now().format("%Y-%m-%dT%H-%M-%S").to_string(),
            "context": ctx_end.to_value(),
        }),
    ));
    Ok(events)
}

fn read_run_grounding(run: &Run) -> String {
    let mut g = format_run_text("本岗", run);
    for f in &run.files {
        let Some(path) = f.get("path").and_then(|v| v.as_str()) else {
            continue;
        };
        if let Ok(body) = std::fs::read_to_string(path) {
            let name = f.get("name").and_then(|v| v.as_str()).unwrap_or(path);
            let cut: String = body.chars().take(4000).collect();
            g.push_str(&format!("\n\n## 文件 {name}\n{cut}\n"));
        }
    }
    g
}

async fn talk_after_run(
    paths: &Paths,
    expert: &Expert,
    history: &[Value],
    confirm_ok: bool,
    session_id: &str,
    run: &Run,
) -> Result<Vec<EventOut>, LlmError> {
    let grounding = read_run_grounding(run);
    let extra = format!(
        "\n\n你已经按 harness steps 出稿（或 HITL 未出稿）。现在用白话跟用户说话：\n\
         1. 先复述你听懂的任务；\n\
         2. 说明出了什么、缺什么（[A001]/UNSPECIFIED）；\n\
         3. 现行门户只引用草稿或 web_search/web_open 看到的官方标题，条款没打开就 unverified；\n\
         4. 不要再调用写盘工具，不要编 xyz / 单价 / 条款号。\n\
         5. 用户若还想聊，直接答。\n\n--- 草稿与步骤 ---\n{grounding}\n"
    );
    let mut messages = vec![json!({
        "role": "system",
        "content": format!("{}{extra}", build_expert_prompt(expert, confirm_ok))
    })];
    messages.extend(history.iter().cloned());
    let tools: Vec<Value> = packs::tools_for_expert(&expert.id)
        .iter()
        .filter(|t| READ_ONLY_TOOLS.contains(&t.name))
        .map(|t| t.openai_tool())
        .collect();
    let mut events = vec![(
        "status".into(),
        json!({
            "phase": "talk",
            "text": format!("{} · 出稿后用白话说明", expert.name),
            "expert": expert.id,
        }),
    )];
    let mut ctx = ToolCtx::new(
        paths.clone(),
        &expert.id,
        &expert.category,
        &expert.risk,
        confirm_ok,
        session_id,
    );
    let max_steps = max_agent_steps();
    let mut final_text = String::new();
    for step in 0..max_steps {
        events.push((
            "status".into(),
            json!({"phase": "think", "text": format!("{} 说明 {}/{}", expert.name, step + 1, max_steps)}),
        ));
        let msg = llm::chat(&messages, Some(&tools), 0.3).await?;
        let tool_calls = msg
            .get("tool_calls")
            .and_then(|v| v.as_array())
            .cloned()
            .unwrap_or_default();
        if tool_calls.is_empty() {
            final_text = msg
                .get("content")
                .and_then(|v| v.as_str())
                .unwrap_or("")
                .to_string();
            break;
        }
        messages.push(msg);
        for call in tool_calls {
            let fnx = call.get("function").cloned().unwrap_or(json!({}));
            let name = fnx
                .get("name")
                .and_then(|v| v.as_str())
                .unwrap_or("")
                .to_string();
            let raw = fnx.get("arguments").cloned().unwrap_or(json!("{}"));
            let args = match raw {
                Value::String(s) => serde_json::from_str(&s).unwrap_or(json!({})),
                other => other,
            };
            if !READ_ONLY_TOOLS.contains(&name.as_str()) {
                messages.push(json!({
                    "role": "tool",
                    "tool_call_id": call.get("id").and_then(|v| v.as_str()).unwrap_or(&name),
                    "content": "拒绝：说明阶段只读。写盘已由 harness steps 完成。",
                }));
                continue;
            }
            let hint = args
                .get("query")
                .or_else(|| args.get("path"))
                .and_then(|v| v.as_str())
                .unwrap_or("");
            events.push((
                "status".into(),
                json!({"phase": name, "text": format!("{} · {} {}", expert.name, name, hint)}),
            ));
            let result = packs::execute(&mut ctx, &name, &args);
            let cut: String = result.chars().take(12000).collect();
            messages.push(json!({
                "role": "tool",
                "tool_call_id": call.get("id").and_then(|v| v.as_str()).unwrap_or(&name),
                "content": cut,
            }));
        }
    }
    if final_text.trim().is_empty() {
        final_text = grounding;
    }
    events.push(("token".into(), json!({"text": final_text})));
    let ctx_end = context::inspect(&messages, &[&final_text]);
    events.push(("context".into(), ctx_end.to_value()));
    events.push((
        "done".into(),
        json!({
            "mode": "expert",
            "harness": true,
            "runtime": "steps+talk",
            "intent": "run",
            "expert": expert.id,
            "text": final_text,
            "citations": ctx.citations,
            "deliverables": run.files.clone(),
            "run_id": run.run_id,
            "hitl": {
                "required": run.hitl.required,
                "confirmed": run.hitl.confirmed,
                "pending": run.hitl.pending,
                "gate": run.hitl.gate,
            },
            "illegal_tool_calls": run.illegal_count(),
            "stamp": chrono::Local::now().format("%Y-%m-%dT%H-%M-%S").to_string(),
            "context": ctx_end.to_value(),
        }),
    ));
    Ok(events)
}
