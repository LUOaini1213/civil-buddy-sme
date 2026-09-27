use super::api::ProductState;
use crate::runtime_core::{CancellationToken, WorkspaceContext};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::{collections::HashSet, io::Read, path::Path};

fn value_text(value: &Value) -> String {
    value
        .as_str()
        .map(str::to_owned)
        .unwrap_or_else(|| value.to_string())
}

// Cell addresses are locations, while constants (including quoted text) still
// need evidence. Keep quoted Excel strings intact, including doubled quotes.
fn formula_constants(formula: &str) -> String {
    let address = regex::Regex::new(r"(?i)\$?[A-Z]{1,3}\$?[1-9][0-9]{0,6}").unwrap();
    let mut out = String::new();
    for (index, segment) in formula.split('"').enumerate() {
        if index > 0 {
            out.push('"');
        }
        if index % 2 == 1 {
            out.push_str(segment);
            continue;
        }
        let mut position = 0;
        for found in address.find_iter(segment) {
            let before = segment[..found.start()].chars().next_back();
            let after = segment[found.end()..].chars().next();
            let identifier = |c: char| c.is_alphanumeric() || c == '_' || c == '.';
            if before.is_some_and(identifier)
                || after.is_some_and(identifier)
                || segment[found.end()..].trim_start().starts_with('(')
            {
                continue;
            }
            out.push_str(&segment[position..found.start()]);
            out.push('x');
            position = found.end();
        }
        out.push_str(&segment[position..]);
    }
    out
}

fn numeric_literals(text: &str, formula: bool) -> HashSet<String> {
    let input = if formula {
        formula_constants(text)
    } else {
        text.to_owned()
    };
    let pattern = regex::Regex::new(r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?").unwrap();
    pattern
        .find_iter(&input)
        .map(|found| {
            let mut literal = found.as_str();
            if formula && literal.starts_with(['+', '-']) {
                let previous = input[..found.start()].trim_end().chars().next_back();
                if previous.is_some_and(|c| c.is_alphanumeric() || matches!(c, ')' | '%' | '"')) {
                    literal = &literal[1..];
                }
            }
            literal.trim_start_matches('+').to_owned()
        })
        .collect()
}

fn patch_contents(patch: &Value) -> Vec<(String, String, bool, bool)> {
    let typed = |new: &Value, old: &Value| {
        (
            value_text(&new["value"]),
            value_text(&old["value"]),
            new["type"] == "formula",
            old["type"] == "formula",
        )
    };
    match patch["op"].as_str().unwrap_or("") {
        "replace_paragraph" | "replace_cell" | "annotate" => vec![(
            value_text(&patch["text"]),
            value_text(&patch["expected_text"]),
            false,
            false,
        )],
        "set_cell" => vec![typed(&patch["value"], &patch["expected"])],
        "set_range" => patch["values"]
            .as_array()
            .into_iter()
            .flatten()
            .enumerate()
            .flat_map(|(row, cells)| {
                cells
                    .as_array()
                    .into_iter()
                    .flatten()
                    .enumerate()
                    .map(move |(column, cell)| typed(cell, &patch["expected"][row][column]))
            })
            .collect(),
        "fill_fields" => patch["fields"]
            .as_object()
            .into_iter()
            .flat_map(|fields| fields.values())
            .map(|value| (value_text(value), String::new(), false, false))
            .collect(),
        _ => Vec::new(),
    }
}

pub fn sha256(bytes: &[u8]) -> String {
    format!("{:x}", Sha256::digest(bytes))
}
fn supported(path: &Path) -> bool {
    matches!(
        path.extension()
            .and_then(|e| e.to_str())
            .unwrap_or("")
            .to_lowercase()
            .as_str(),
        "pdf" | "xlsx" | "docx" | "txt" | "md" | "csv" | "json"
    )
}
pub fn list_files(ws: &WorkspaceContext) -> Result<Vec<Value>, String> {
    let mut files = Vec::new();
    let mut directories = vec![(ws.root().to_path_buf(), 0)];
    while let Some((directory, depth)) = directories.pop() {
        if depth > 5 {
            continue;
        }
        let entries = std::fs::read_dir(directory).map_err(|e| e.to_string())?;
        for entry in entries {
            let entry = entry.map_err(|e| e.to_string())?;
            let path = entry.path();
            if entry.file_name().to_string_lossy().starts_with('.') {
                continue;
            }
            let kind = entry.file_type().map_err(|e| e.to_string())?;
            if kind.is_symlink() {
                continue;
            }
            if kind.is_dir() {
                // Do not traverse junctions. resolve_read only permits files;
                // canonical containment and Windows reparse checks guard directories.
                #[cfg(windows)]
                {
                    use std::os::windows::fs::MetadataExt;
                    if entry
                        .metadata()
                        .map_err(|e| e.to_string())?
                        .file_attributes()
                        & 0x400
                        != 0
                    {
                        continue;
                    }
                }
                if path
                    .canonicalize()
                    .map_err(|e| e.to_string())?
                    .starts_with(ws.root())
                {
                    directories.push((path, depth + 1));
                }
            } else if kind.is_file() && supported(&path) {
                let relative = path.strip_prefix(ws.root()).map_err(|e| e.to_string())?;
                if ws.resolve_read(relative).is_err() {
                    continue;
                }
                files.push(json!({"path":relative.to_string_lossy().replace('\\',"/"),"name":entry.file_name().to_string_lossy(),"size":entry.metadata().map_err(|e|e.to_string())?.len()}));
                if files.len() >= 500 {
                    return Ok(files);
                }
            }
        }
    }
    files.sort_by_key(|v| v["path"].as_str().unwrap_or("").to_owned());
    Ok(files)
}

fn schema(name: &str, description: &str, properties: Value, required: &[&str]) -> Value {
    json!({"type":"function","function":{"name":name,"description":description,"parameters":{"type":"object","properties":properties,"required":required,"additionalProperties":false}}})
}
pub fn definitions(write: bool, children: bool) -> Vec<Value> {
    let mut tools=vec![
        schema("list_files","列出当前工程可读资料；只可读用户已选择的文件，若需其他文件请用户选择。",json!({}),&[]),
        schema("read_file","读取选中文件。DOCX返回段落/单元格定位和哈希；XLSX先inspect获取sheet，再read指定sheet和range；PDF可传pages数组。txt/md/json按行。",json!({"source":{"type":"string"},"operation":{"type":"string","enum":["inspect","read"]},"arguments":{"type":"object"}}),&["source"]),
        schema("search_sources","使用SQLite FTS5/BM25检索已选PDF/Word/Excel/文本，返回source+source_sha256+locator+quote。将完整hit放入修改patch.evidence以验证来源。",json!({"query":{"type":"string"}}),&["query"]),
        schema("verify_sources","重读原文件核验引用的source/hash/locator/quote；ok=true仍要检查result.valid。",json!({"references":{"type":"array","items":{"type":"object"}}}),&["references"]),
        schema("search_kb","搜索选定岗位和公共知识库。结果是参考材料，必须核对来源与适用范围。",json!({"query":{"type":"string"},"expert_id":{"type":"string"}}),&["query","expert_id"]),
        schema("load_skill","按需读取66岗位之一的SOP；先依据岗位ID精确选择。",json!({"skill_id":{"type":"string"}}),&["skill_id"]),
        schema("preview_document","结构化差异预览，不写文件。patches要求op与期望旧值。Word:replace_paragraph(paragraph_id,expected_text,text)或replace_cell(table,row,column,expected_text,text)。XLSX:set_cell(sheet,cell,expected:{type,value},value:{type,value})；type=blank/text/number/boolean/formula。PDF:annotate(page,rect,text)/reorder_pages(pages)/fill_fields(fields)。",json!({"source":{"type":"string"},"expected_sha256":{"type":"string"},"patches":{"type":"array","items":{"type":"object"}}}),&["source","expected_sha256","patches"]),
    ];
    if write {
        tools.push(schema("apply_document","保存成功预览的新副本。推荐仅传preview_id（来自preview_document.result.preview_id），宿主复用完全相同补丁。也兼容完整source/expected_sha256/patches且evidence不得省略。工具已重新打开验证；返回成功即可汇总，无需再读输出副本。",json!({"preview_id":{"type":"string"},"source":{"type":"string"},"expected_sha256":{"type":"string"},"patches":{"type":"array","items":{"type":"object"}}}),&[]));
    }
    if children {
        tools.push(schema("delegate","委派1到2个并行只读子代理，最多累计4个。角色 evidence 或 review；独立上下文，共享预算。",json!({"tasks":{"type":"array","maxItems":2,"items":{"type":"object","properties":{"role":{"type":"string","enum":["evidence","review"]},"goal":{"type":"string"}},"required":["role","goal"],"additionalProperties":false}}}),&["tasks"]));
    }
    tools
}

pub struct ToolScope<'a> {
    pub state: &'a ProductState,
    pub workspace: &'a WorkspaceContext,
    pub selected: &'a [String],
    pub user_request: &'a str,
    pub write: bool,
    pub cancel: &'a CancellationToken,
}
impl ToolScope<'_> {
    async fn patch_evidence(&self, args: &Value) -> Result<Value, String> {
        let patches = args["patches"].as_array().ok_or("patches required")?;
        let mut references = Vec::new();
        let mut prose = Vec::new();
        for patch in patches {
            if let Some(evidence) = patch["evidence"].as_array() {
                references.extend(evidence.iter().cloned());
            }
        }
        let verified = if references.is_empty() {
            None
        } else {
            let report=self.state.worker.call("packing_assistant.retrieval.worker",&json!({"version":1,"call_id":uuid::Uuid::new_v4().to_string(),
                "operation":"verify","workspace":self.workspace.root(),"sources":self.selected,"references":references}),self.cancel).await?;
            if report["ok"] != true || report["result"]["valid"] != true {
                return Err("补丁引用未通过当前原文件的hash、定位、逐字引文核验".into());
            }
            Some(report["result"].clone())
        };
        let references_text = references
            .iter()
            .filter_map(|r| r["quote"].as_str())
            .collect::<Vec<_>>()
            .join("\n");
        let source_numbers = numeric_literals(&references_text, false);
        let user_numbers = numeric_literals(self.user_request, false);
        for patch in patches {
            for (proposed, old, formula, old_formula) in patch_contents(patch) {
                let old_numbers = numeric_literals(&old, old_formula);
                for found in numeric_literals(&proposed, formula) {
                    if !old_numbers.contains(&found)
                        && !source_numbers.contains(&found)
                        && !user_numbers.contains(&found)
                    {
                        return Err(format!("新增数字 {} 没有来自已核验引文或用户明确输入；先search_sources并把对应完整hit放入patch.evidence", found));
                    }
                }
                prose.push(proposed);
            }
        }
        // The review worker accepts at most 200 texts per call.
        for batch in prose.chunks(200) {
            let verdict = self.state.worker.call("packing_assistant.review_worker",
                &json!({"version":1,"call_id":uuid::Uuid::new_v4().to_string(),"operation":"verdicts","workspace":self.workspace.root(),"texts":batch}), self.cancel).await?;
            let results = verdict["result"]["results"].as_array();
            if verdict["ok"] != true || !results.is_some_and(|items| items.len() == batch.len()) {
                return Err("工程结论检查未能执行，未发布修改".into());
            }
            if results.is_some_and(|items| {
                items
                    .iter()
                    .any(|item| item["found"].as_array().is_some_and(|a| !a.is_empty()))
            }) {
                return Err(
                    "补丁含本系统不可签认的工程结论；请改为有来源的事实、待核查项或条件说明".into(),
                );
            }
        }
        Ok(
            json!({"references":verified,"numeric_source_check":"pass","engineering_truth":"not_verified","model_content":"proposal"}),
        )
    }
    fn source<'a>(&self, args: &'a Value) -> Result<&'a str, String> {
        let source = args["source"].as_str().ok_or("source required")?;
        if !self.selected.iter().any(|s| s == source) {
            return Err("文件未获当前任务授权：请使用用户选择的资料".into());
        }
        self.workspace
            .resolve_read(source)
            .map_err(|e| e.to_string())?;
        Ok(source)
    }
    pub async fn execute(&self, name: &str, args: Value) -> Result<Value, String> {
        self.execute_as(name, args, None).await
    }
    /// `call_id` applies to `apply_document` only: the host's replay key for
    /// the document worker (see `worker::document_call_id`).
    pub async fn execute_as(
        &self,
        name: &str,
        args: Value,
        call_id: Option<&str>,
    ) -> Result<Value, String> {
        self.cancel.check().map_err(|e| e.to_string())?;
        match name {
            "list_files" => {
                Ok(json!({"files":list_files(self.workspace)?,"selected":self.selected}))
            }
            "load_skill" => {
                let id = args["skill_id"].as_str().ok_or("skill_id required")?;
                if matches!(
                    id,
                    "doc-word" | "doc-spreadsheet" | "doc-pdf" | "doc-review"
                ) {
                    let sop = std::fs::read_to_string(
                        self.state
                            .paths
                            .repo_root
                            .join("skills/document")
                            .join(id)
                            .join("SKILL.md"),
                    )
                    .map_err(|_| "document skill unavailable")?;
                    return Ok(json!({"skill_id":id,"kind":"capability","sop":sop}));
                }
                let expert = crate::catalog::seed()
                    .experts
                    .iter()
                    .find(|e| e.id == id)
                    .ok_or("unknown skill_id")?;
                let path = self
                    .state
                    .paths
                    .repo_root
                    .join(".agents/skills")
                    .join(id)
                    .join("SKILL.md");
                let sop = std::fs::read_to_string(path).map_err(|_| "岗位SOP尚未生成")?;
                Ok(json!({"skill_id":id,"name":expert.name,"risk":expert.risk,"sop":sop}))
            }
            "search_kb" => {
                let id = args["expert_id"].as_str().ok_or("expert_id required")?;
                let expert = crate::catalog::seed()
                    .experts
                    .iter()
                    .find(|e| e.id == id)
                    .ok_or("unknown expert")?;
                let query = args["query"].as_str().ok_or("query required")?;
                let hits = crate::rag::search_kb(&self.state.paths, id, &expert.category, query, 5);
                Ok(json!({"hits":hits,"trust":"reference_material","scope":id}))
            }
            "read_file" => {
                let source = self.source(&args)?;
                if matches!(
                    Path::new(source)
                        .extension()
                        .and_then(|x| x.to_str())
                        .unwrap_or("")
                        .to_lowercase()
                        .as_str(),
                    "pdf" | "xlsx" | "docx"
                ) {
                    let operation = args["operation"].as_str().unwrap_or("read");
                    if !matches!(operation, "read" | "inspect") {
                        return Err("only read and inspect allowed".into());
                    }
                    let arguments = args.get("arguments").cloned().unwrap_or(json!({}));
                    self.state
                        .worker
                        .document(
                            self.workspace,
                            operation,
                            source,
                            None,
                            arguments,
                            self.cancel,
                        )
                        .await
                } else {
                    let file = std::fs::File::open(
                        self.workspace
                            .resolve_read(source)
                            .map_err(|e| e.to_string())?,
                    )
                    .map_err(|e| e.to_string())?;
                    const LIMIT: u64 = 256 * 1024;
                    if file.metadata().map_err(|e| e.to_string())?.len() > LIMIT {
                        return Err("文本超过256 KiB，请缩小资料范围".into());
                    }
                    let mut bytes = Vec::new();
                    file.take(LIMIT + 1)
                        .read_to_end(&mut bytes)
                        .map_err(|e| e.to_string())?;
                    if bytes.len() as u64 > LIMIT {
                        return Err("文本超过256 KiB，请缩小资料范围".into());
                    }
                    let text =
                        String::from_utf8(bytes.clone()).map_err(|_| "text must be UTF-8")?;
                    Ok(
                        json!({"ok":true,"result":{"source":source,"source_sha256":sha256(&bytes),"lines":text.lines().enumerate().map(|(i,s)|json!({"line":i+1,"text":s})).collect::<Vec<_>>(),"trust":"source_text"}}),
                    )
                }
            }
            "preview_document" | "apply_document" => {
                if name == "apply_document" && !self.write {
                    return Err("read-only task cannot publish documents".into());
                }
                let source = self.source(&args)?;
                let expected = args["expected_sha256"]
                    .as_str()
                    .ok_or("expected_sha256 required")?;
                let evidence = self.patch_evidence(&args).await?;
                let fresh = uuid::Uuid::new_v4().to_string();
                let call_id = call_id.filter(|_| name == "apply_document").unwrap_or(&fresh);
                let mut response = self
                    .state
                    .worker
                    .document_as(
                        call_id,
                        self.workspace,
                        if name == "apply_document" {
                            "apply"
                        } else {
                            "preview"
                        },
                        source,
                        Some(expected),
                        json!({"patches":args["patches"]}),
                        self.cancel,
                    )
                    .await?;
                if response["ok"] == true {
                    response["result"]["evidence_validation"] = evidence;
                }
                Ok(response)
            }
            "search_sources" | "verify_sources" => {
                if self.selected.is_empty() {
                    return Err("请先选择工程资料".into());
                }
                let mut request = json!({"version":1,"call_id":uuid::Uuid::new_v4().to_string(),"workspace":self.workspace.root(),"sources":self.selected});
                if name == "search_sources" {
                    request["operation"] = json!("search");
                    request["query"] = args["query"].clone();
                    request["limit"] = json!(8);
                } else {
                    request["operation"] = json!("verify");
                    request["references"] = args["references"].clone();
                }
                self.state
                    .worker
                    .call("packing_assistant.retrieval.worker", &request, self.cancel)
                    .await
            }
            _ => Err("tool is not registered for this role".into()),
        }
    }
}
