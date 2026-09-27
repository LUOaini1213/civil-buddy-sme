//! Minimal MCP stdio (newline-delimited JSON-RPC, plus Content-Length framing).

use crate::catalog::seed;
use crate::config::Paths;
use crate::packs::{self, ToolCtx};
use crate::store;
use serde_json::{json, Value};
use std::io::{self, BufRead, Write};

#[derive(Clone)]
pub struct McpFilter {
    pub pack: Option<String>,
    pub expert: Option<String>,
}

pub fn serve_stdio(paths: Paths, filter: McpFilter) -> io::Result<()> {
    if let Some(p) = filter.pack.as_deref() {
        if !packs::valid_pack(p) {
            eprintln!("unknown pack {p}; valid: {:?}", crate::catalog::pack_ids());
            std::process::exit(2);
        }
    }
    let stdin = io::stdin();
    let mut lock = stdin.lock();
    let mut stdout = io::stdout();
    loop {
        let Some(msg) = read_message(&mut lock)? else {
            break;
        };
        if let Some(resp) = handle_rpc(&paths, &filter, msg) {
            write_message(&mut stdout, &resp)?;
        }
    }
    Ok(())
}

fn read_message(r: &mut impl BufRead) -> io::Result<Option<Value>> {
    let mut first = String::new();
    let n = r.read_line(&mut first)?;
    if n == 0 {
        return Ok(None);
    }
    let trimmed = first.trim();
    if trimmed.is_empty() {
        return read_message(r);
    }
    if trimmed.to_ascii_lowercase().starts_with("content-length:") {
        let len: usize = trimmed
            .split(':')
            .nth(1)
            .and_then(|s| s.trim().parse().ok())
            .unwrap_or(0);
        loop {
            let mut line = String::new();
            r.read_line(&mut line)?;
            if line.trim().is_empty() {
                break;
            }
        }
        let mut buf = vec![0u8; len];
        io::Read::read_exact(r, &mut buf)?;
        let v = serde_json::from_slice(&buf).unwrap_or(json!({}));
        return Ok(Some(v));
    }
    let v = serde_json::from_str(trimmed).unwrap_or(json!({}));
    Ok(Some(v))
}

fn write_message(w: &mut impl Write, v: &Value) -> io::Result<()> {
    let s = serde_json::to_string(v).unwrap_or_else(|_| "{}".into());
    writeln!(w, "{s}")?;
    w.flush()
}

/// JSON-RPC handler (stdio + tests).
pub fn handle_rpc(paths: &Paths, filter: &McpFilter, msg: Value) -> Option<Value> {
    let method = msg.get("method").and_then(|v| v.as_str()).unwrap_or("");
    let id = msg.get("id").cloned();
    if method.is_empty() || method.starts_with("notifications/") {
        return None;
    }
    let result = match method {
        "initialize" => {
            let ver = msg
                .pointer("/params/protocolVersion")
                .and_then(|v| v.as_str())
                .unwrap_or("2024-11-05");
            let name = filter
                .expert
                .as_deref()
                .map(|e| format!("civil-{e}"))
                .or_else(|| filter.pack.as_deref().map(|p| format!("civil-{p}")))
                .unwrap_or_else(|| "civil-buddy".into());
            json!({
                "protocolVersion": ver,
                "capabilities": {
                    "tools": {},
                    "resources": {},
                    "prompts": {}
                },
                "serverInfo": { "name": name, "version": "0.1.0" }
            })
        }
        "ping" => json!({}),
        "tools/list" => {
            let tools: Vec<Value> = list_tools(filter)
                .iter()
                .map(|t| {
                    let mut tool = t.mcp_tool();
                    // A model-facing argument is never proof of a person's approval.
                    if let Some(properties) = tool
                        .pointer_mut("/inputSchema/properties")
                        .and_then(Value::as_object_mut)
                    {
                        for key in ["confirm_ok", "confirm_text", "p0_confirmed"] {
                            properties.remove(key);
                        }
                    }
                    tool
                })
                .collect();
            json!({ "tools": tools })
        }
        "resources/list" => json!({ "resources": list_resources(paths, filter) }),
        "resources/read" => read_resource(paths, filter, &msg),
        "prompts/list" => json!({ "prompts": list_prompts(filter) }),
        "prompts/get" => get_prompt(filter, &msg),
        "tools/call" => {
            let name = msg
                .pointer("/params/name")
                .and_then(|v| v.as_str())
                .unwrap_or("");
            let args = msg
                .pointer("/params/arguments")
                .cloned()
                .unwrap_or(json!({}));
            let (text, is_err) = call_tool(paths, filter, name, &args);
            json!({
                "content": [{ "type": "text", "text": text }],
                "isError": is_err
            })
        }
        _ => {
            return Some(json!({
                "jsonrpc": "2.0",
                "id": id,
                "error": { "code": -32601, "message": format!("Method not found: {method}") }
            }));
        }
    };
    Some(json!({"jsonrpc": "2.0", "id": id, "result": result}))
}

fn list_tools(filter: &McpFilter) -> Vec<packs::ToolDef> {
    let tools = if let Some(eid) = filter.expert.as_deref() {
        packs::tools_for_expert(eid)
    } else {
        packs::all_tools_filtered(filter.pack.as_deref())
    };
    // Stdio has no interactive file-selection/approval channel. These wrappers
    // import arbitrary local paths and must remain on the interactive surface.
    tools
        .into_iter()
        .filter(|tool| !matches!(tool.name, "import_local" | "firm__bid_pack"))
        .collect()
}

fn call_tool(paths: &Paths, filter: &McpFilter, name: &str, args: &Value) -> (String, bool) {
    if !args.is_object() {
        return ("拒绝：arguments must be an object".into(), true);
    }
    if ["confirm_ok", "confirm_text", "p0_confirmed"]
        .iter()
        .any(|key| args.get(key).is_some())
    {
        return (
            "拒绝：approval_required; MCP cannot accept human approval through tool arguments"
                .into(),
            true,
        );
    }
    if !list_tools(filter).iter().any(|tool| tool.name == name) {
        return ("拒绝：tool is outside this MCP launch scope".into(), true);
    }
    let expert_id = args
        .get("expert_id")
        .and_then(|v| v.as_str())
        .map(|s| s.to_string())
        .or_else(|| filter.expert.clone())
        .or_else(|| {
            filter
                .pack
                .as_deref()
                .map(|p| packs::default_expert(p).to_string())
        })
        .unwrap_or_else(|| infer_expert(name).to_string());
    if filter
        .expert
        .as_deref()
        .is_some_and(|scoped| scoped != expert_id)
    {
        return ("拒绝：expert is outside this MCP launch scope".into(), true);
    }
    let exp = seed()
        .experts
        .iter()
        .find(|e| e.id == expert_id)
        .cloned()
        .or_else(|| store::get_expert(paths, &expert_id));
    let (category, risk) = match &exp {
        Some(e) => (e.category.clone(), e.risk.clone()),
        None => return ("拒绝：unknown expert".into(), true),
    };
    if filter
        .pack
        .as_deref()
        .is_some_and(|scoped| scoped != category)
    {
        return ("拒绝：expert is outside this MCP launch scope".into(), true);
    }
    if risk == "high" {
        return (
            "拒绝：approval_required; use the interactive workbench for this high-risk post".into(),
            true,
        );
    }
    if name == "read_kb" {
        let path = args.get("path").and_then(Value::as_str).unwrap_or("");
        if !crate::rag::list_kb(paths, &expert_id, &category)
            .iter()
            .any(|entry| entry.get("path").and_then(Value::as_str) == Some(path))
        {
            return (
                "拒绝：knowledge file is outside this expert's scope".into(),
                true,
            );
        }
    }
    if name == "pack-ship__plan" {
        let root = std::env::var_os("CIVIL_JOB_ROOT")
            .map(std::path::PathBuf::from)
            .or_else(|| std::env::current_dir().ok())
            .and_then(|p| p.canonicalize().ok());
        for field in ["materials", "notes", "project_name"] {
            if let Some(raw) = args
                .get(field)
                .and_then(Value::as_str)
                .and_then(packs::find_table_path)
            {
                let file = std::path::Path::new(&raw);
                let allowed = root.as_ref().is_some_and(|root| {
                    file.canonicalize()
                        .ok()
                        .is_some_and(|target| target.starts_with(root))
                });
                if !allowed || reject_output_links(file).is_err() {
                    return (
                        "拒绝：table path must be an unlinked file inside the job workspace".into(),
                        true,
                    );
                }
            }
        }
    }
    let session = args
        .get("session_id")
        .and_then(|v| v.as_str())
        .unwrap_or("mcp");
    if session.is_empty()
        || session.len() > 128
        || !session
            .bytes()
            .all(|c| c.is_ascii_alphanumeric() || c == b'-' || c == b'_')
    {
        return ("拒绝：invalid session_id".into(), true);
    }
    if reject_output_links(&paths.out_root.join(session).join(&expert_id)).is_err() {
        return (
            "拒绝：output paths must not contain links or reparse points".into(),
            true,
        );
    }
    let mut ctx = ToolCtx::new(paths.clone(), &expert_id, &category, &risk, false, session);
    let text = packs::execute(&mut ctx, name, args);
    let err = text.starts_with("拒绝")
        || text.starts_with("未知工具")
        || text.starts_with("找不到")
        || text.starts_with("缺少")
        || text.starts_with("文件不存在");
    (text, err)
}

fn reject_output_links(path: &std::path::Path) -> std::io::Result<()> {
    fn linked(path: &std::path::Path) -> std::io::Result<bool> {
        let metadata = match std::fs::symlink_metadata(path) {
            Ok(metadata) => metadata,
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok(false),
            Err(error) => return Err(error),
        };
        let mut link = metadata.file_type().is_symlink();
        #[cfg(windows)]
        {
            use std::os::windows::fs::MetadataExt;
            link |= metadata.file_attributes() & 0x400 != 0;
        }
        Ok(link)
    }
    let denied = || std::io::Error::new(std::io::ErrorKind::PermissionDenied, "linked output path");
    for parent in path.ancestors() {
        if linked(parent)? {
            return Err(denied());
        }
    }
    let mut pending = vec![path.to_path_buf()];
    let mut visited = 0;
    while let Some(directory) = pending.pop() {
        if !directory.is_dir() {
            continue;
        }
        for entry in std::fs::read_dir(directory)? {
            let entry = entry?;
            visited += 1;
            if visited > 10_000 || linked(&entry.path())? {
                return Err(denied());
            }
            if entry.file_type()?.is_dir() {
                pending.push(entry.path());
            }
        }
    }
    Ok(())
}

fn infer_expert(tool: &str) -> &'static str {
    if let Some(owner) = crate::tier_map::exclusive_owner(tool) {
        return owner;
    }
    let pack = tool.split("__").next().unwrap_or("");
    if packs::valid_pack(pack) {
        packs::default_expert(pack)
    } else {
        "construction"
    }
}

fn scoped_expert(paths: &Paths, filter: &McpFilter) -> (String, String) {
    if let Some(eid) = filter.expert.as_deref() {
        if let Some(e) = store::get_expert(paths, eid) {
            return (e.id, e.category);
        }
        let cat = seed()
            .experts
            .iter()
            .find(|e| e.id == eid)
            .map(|e| e.category.clone())
            .unwrap_or_else(|| "bid".into());
        return (eid.to_string(), cat);
    }
    if let Some(p) = filter.pack.as_deref() {
        let eid = packs::default_expert(p);
        return (eid.to_string(), p.to_string());
    }
    ("bid-parse".into(), "bid".into())
}

fn list_resources(paths: &Paths, filter: &McpFilter) -> Vec<Value> {
    let (eid, cat) = scoped_expert(paths, filter);
    crate::rag::list_kb(paths, &eid, &cat)
        .into_iter()
        .filter_map(|row| {
            let rel = row.get("path").and_then(|v| v.as_str())?;
            let title = row.get("title").and_then(|v| v.as_str()).unwrap_or(rel);
            let layer = row.get("layer").and_then(|v| v.as_str()).unwrap_or("");
            Some(json!({
                "uri": format!("kb://{rel}"),
                "name": title,
                "description": format!("{layer} · {rel}"),
                "mimeType": "text/markdown"
            }))
        })
        .collect()
}

fn read_resource(paths: &Paths, filter: &McpFilter, msg: &Value) -> Value {
    let uri = msg
        .pointer("/params/uri")
        .and_then(|v| v.as_str())
        .unwrap_or("");
    let rel = uri.strip_prefix("kb://").unwrap_or("");
    let (eid, cat) = scoped_expert(paths, filter);
    let allowed: Vec<String> = crate::rag::list_kb(paths, &eid, &cat)
        .iter()
        .filter_map(|r| {
            r.get("path")
                .and_then(|v| v.as_str())
                .map(|s| s.to_string())
        })
        .collect();
    if !allowed.iter().any(|p| p == rel) {
        return json!({
            "contents": [{
                "uri": uri,
                "mimeType": "text/plain",
                "text": "拒绝：该知识不在当前专家可见层（私库 / 大类共享 / 公司）。"
            }]
        });
    }
    match crate::rag::read_kb(paths, rel) {
        Some((_, text)) => json!({
            "contents": [{
                "uri": uri,
                "mimeType": "text/markdown",
                "text": text
            }]
        }),
        None => json!({
            "contents": [{
                "uri": uri,
                "mimeType": "text/plain",
                "text": "拒绝：文件不存在或越权。"
            }]
        }),
    }
}

fn list_prompts(filter: &McpFilter) -> Vec<Value> {
    let mut all = vec![
        json!({
            "name": "civil.bid.parse",
            "description": "招标解析：只抄原文成表，评分点交 bid-tech，★/废标交 bid-compliance。不判定可投标。",
            "arguments": [
                {"name": "tender_text", "description": "招标/ITT 节选", "required": true},
                {"name": "jurisdiction", "description": "CN / SG / EU / DUAL", "required": false}
            ]
        }),
        json!({
            "name": "civil.bid.compliance",
            "description": "P0 废标/资格/★ 扫描。三态：已响应 / 未响应 / 招标未提供正文。",
            "arguments": [
                {"name": "tender_text", "description": "招标正文", "required": true}
            ]
        }),
        json!({
            "name": "civil.pack-ship.plan",
            "description": "装箱作业单。柜数/xyz/N0 只抄 pack-ship__plan；未接通写 UNSPECIFIED。",
            "arguments": [
                {"name": "materials", "description": "物料表或自然语言", "required": true}
            ]
        }),
        json!({
            "name": "civil.pack-ship.list",
            "description": "列出 pack-ship list / plan / export。",
            "arguments": []
        }),
        json!({
            "name": "civil.pack-ship.export",
            "description": "导出装柜证据。utilization/can_fit/mid50/系固待办只抄 solver。",
            "arguments": [
                {"name": "solver", "description": "本仓 solver 快照", "required": false}
            ]
        }),
        json!({
            "name": "civil.construction.scheme",
            "description": "专项方案讨论提纲十一章。须确认句。禁止可以开工。不是法定专项。",
            "arguments": [
                {"name": "task", "description": "工作范围 / 临边部位", "required": true}
            ]
        }),
    ];
    if let Some(eid) = filter.expert.as_deref() {
        all.retain(|p| {
            let n = p.get("name").and_then(|v| v.as_str()).unwrap_or("");
            match eid {
                "bid-parse" => n == "civil.bid.parse",
                "bid-compliance" => n == "civil.bid.compliance",
                "pack-ship" => n.starts_with("civil.pack-ship."),
                "construction" => n == "civil.construction.scheme",
                _ => false,
            }
        });
    } else if let Some(pack) = filter.pack.as_deref() {
        all.retain(|p| {
            let n = p.get("name").and_then(|v| v.as_str()).unwrap_or("");
            match pack {
                "bid" => n.starts_with("civil.bid."),
                "plant" => n.starts_with("civil.pack-ship."),
                "construction" => n == "civil.construction.scheme",
                _ => false,
            }
        });
    }
    all
}

fn get_prompt(filter: &McpFilter, msg: &Value) -> Value {
    let name = msg
        .pointer("/params/name")
        .and_then(|v| v.as_str())
        .unwrap_or("");
    let allowed: Vec<String> = list_prompts(filter)
        .iter()
        .filter_map(|p| {
            p.get("name")
                .and_then(|v| v.as_str())
                .map(|s| s.to_string())
        })
        .collect();
    if !allowed.iter().any(|n| n == name) {
        return json!({
            "description": "拒绝：当前 --expert/--pack 看不见该 prompt",
            "messages": []
        });
    }
    let args = msg
        .pointer("/params/arguments")
        .cloned()
        .unwrap_or(json!({}));
    let text = match name {
        "civil.bid.parse" => format!(
            "你是 Civil Buddy 招标解析岗。用 bid-parse__extract 抽表。天数/分值/workhead 只抄用户正文。\
无正文则拒绝。不要判定可投标。评分点交给 bid-tech，★/废标交给 bid-compliance。辖区={}。正文：\n{}",
            args.get("jurisdiction").and_then(|v| v.as_str()).unwrap_or("SG"),
            args.get("tender_text").and_then(|v| v.as_str()).unwrap_or("（未提供）")
        ),
        "civil.bid.compliance" => format!(
            "你是废标检查岗。用 bid-compliance__gaps。只打 已响应/未响应/招标未提供正文。\
不要编造否决依据。正文：\n{}",
            args.get("tender_text").and_then(|v| v.as_str()).unwrap_or("（未提供）")
        ),
        "civil.pack-ship.plan" => format!(
            "你是装箱拼柜岗。先 pack-ship__list，再 pack-ship__plan，再 pack-ship__export。\
柜数/N0/xyz 只抄工具；未接通写 UNSPECIFIED。禁止编 CTU 条款号。物料：\n{}",
            args.get("materials").and_then(|v| v.as_str()).unwrap_or("（未提供）")
        ),
        "civil.pack-ship.list" => "列出 pack-ship__list / pack-ship__plan / pack-ship__export。不要编数字。".into(),
        "civil.pack-ship.export" => "导出装柜证据。utilization / can_fit / mid50 / 系固待办只抄 solver；未接通写 UNSPECIFIED。".into(),
        "civil.construction.scheme" => format!(
            "你是施工方案岗。出十一章讨论提纲，不是法定专项。写盘前须确认句「我明白，将由持证人员签认」。禁止断言可以开工。任务：\n{}",
            args.get("task").or_else(|| args.get("text")).and_then(|v| v.as_str()).unwrap_or("（未提供）")
        ),
        _ => "未知 prompt".into(),
    };
    json!({
        "description": name,
        "messages": [{
            "role": "user",
            "content": { "type": "text", "text": text }
        }]
    })
}
