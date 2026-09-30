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
fn reference_schema() -> Value {
    // Keep complete search hits valid (including score/chunk_id/trust), while
    // spelling out the four fields that the verifier actually consumes.
    json!({"type":"object","properties":{
        "source":{"type":"string","description":"Exact selected relative source from search_sources"},
        "source_sha256":{"type":"string","pattern":"^[a-fA-F0-9]{64}$"},
        "locator":{"type":"object","description":"Copy the complete hit locator unchanged, including start/end"},
        "quote":{"type":"string","description":"Copy the complete exact hit quote, including newlines"}},
        "required":["source","source_sha256","locator","quote"],"additionalProperties":true})
}
fn patch_schema() -> Value {
    let typed = json!({"type":"object","properties":{"type":{"type":"string","enum":["blank","text","number","boolean","formula"]},
        "value":{"type":["string","number","boolean","null"]}},"required":["type","value"],"additionalProperties":false});
    let matrix = json!({"type":"array","items":{"type":"array","items":typed}});
    json!({"type":"array","minItems":1,"maxItems":200,"description":"At most 100 evidence references across all patches; split larger previews.","items":{"type":"object","properties":{
        "op":{"type":"string","enum":["replace_paragraph","replace_cell","set_cell","set_range","annotate","reorder_pages","fill_fields"]},
        "evidence":{"type":"array","maxItems":50,"items":reference_schema(),"description":"References belong INSIDE each patch. Required for new source-derived numbers; a prior verify_sources call does not attach them."},
        "paragraph_id":{"type":"string"},"expected_text":{"type":"string"},"text":{"type":"string"},
        "table":{"type":"integer","minimum":0},"row":{"type":"integer","minimum":0},"column":{"type":"integer","minimum":0},
        "sheet":{"type":"string"},"cell":{"type":"string"},"range":{"type":"string"},
        "expected":{"anyOf":[typed,matrix]},"value":typed,"values":matrix,
        "page":{"type":"integer","minimum":1},"rect":{"type":"array","items":{"type":"number"},"minItems":4,"maxItems":4},
        "pages":{"type":"array","items":{"type":"integer","minimum":1}},
        "fields":{"type":"object","additionalProperties":{"type":"string"}}},
        "required":["op"],"additionalProperties":false}})
}
pub fn definitions(write: bool, children: bool) -> Vec<Value> {
    let mut tools=vec![
        schema("list_files","列出当前工程可读资料；只可读用户已选择的文件，若需其他文件请用户选择。",json!({}),&[]),
        schema("read_file","读取选中文件。XLSX先operation=inspect获取sheet和used_range，再operation=read、arguments={sheet,range}读取必要区域。未指定operation和sheet时XLSX默认inspect。DOCX返回定位与哈希；PDF可传pages。其他格式默认read。",json!({"source":{"type":"string"},"operation":{"type":"string","enum":["inspect","read"]},"arguments":{"type":"object","properties":{"sheet":{"type":"string","description":"Required for XLSX read; exact name returned by inspect"},"range":{"type":"string","description":"Prefer the smallest needed A1 range, e.g. A1:D3; omitted uses bounded A1:J20"},"block_ids":{"type":"array","items":{"type":"string"}},"pages":{"type":"array","items":{"type":"integer","minimum":1}}},"additionalProperties":false}}),&["source"]),
        schema("search_sources","使用SQLite FTS5/BM25检索已选PDF/Word/Excel/文本，返回source+source_sha256+locator+quote。将完整hit放入修改patch.evidence以验证来源。",json!({"query":{"type":"string"}}),&["query"]),
        schema("verify_sources","重读原文件核验引用的source/hash/locator/quote；ok=true仍要检查result.valid。成功不自动附加到后续补丁；仍须在每个patch.evidence内传完整引用。",json!({"references":{"type":"array","minItems":1,"maxItems":100,"items":reference_schema()}}),&["references"]),
        schema("search_kb","搜索选定岗位和公共知识库。结果是参考材料，必须核对来源与适用范围。",json!({"query":{"type":"string"},"expert_id":{"type":"string"}}),&["query","expert_id"]),
        schema("load_skill","按需读取66岗位之一的SOP；先依据岗位ID精确选择。",json!({"skill_id":{"type":"string"}}),&["skill_id"]),
        schema("preview_document","结构化差异预览，不写文件。每个patch携带op与期望旧值；原文支持的新数字须在该patch.evidence数组附完整search_sources hit，不能放在顶层。Word:replace_paragraph(paragraph_id,expected_text,text)或replace_cell(table,row,column,expected_text,text)。XLSX:set_cell(sheet,cell,expected:{type,value},value:{type,value})或set_range(sheet,range,expected二维矩阵,values二维矩阵)。PDF:annotate(page,rect,text)/reorder_pages(pages)/fill_fields(fields)。",json!({"source":{"type":"string"},"expected_sha256":{"type":"string"},"patches":patch_schema()}),&["source","expected_sha256","patches"]),
    ];
    if write {
        tools.push(schema("apply_document","保存成功预览的新副本。推荐仅传preview_id（来自preview_document.result.preview_id），宿主复用完全相同补丁。也兼容完整source/expected_sha256/patches且evidence不得省略。工具已重新打开验证；返回成功即可汇总，无需再读输出副本。",json!({"preview_id":{"type":"string"},"source":{"type":"string"},"expected_sha256":{"type":"string"},"patches":patch_schema()}),&[]));
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

/// Candidates come from actual tool calls, never the assistant's final prose.
/// They are independently rechecked at completion; this is quotation identity,
/// not a claim that every sentence in the model's answer is supported.
#[derive(Clone, Default)]
pub struct SourceEvidence {
    references: Vec<Value>,
    keys: HashSet<String>,
    attempted: bool,
    truncated: bool,
    collection_failed: bool,
}

fn valid_quotation_report(result: &Value, source: &str, count: usize) -> bool {
    let Some(rows) = result["references"].as_array() else {
        return false;
    };
    let Some(sources) = result["sources"].as_array() else {
        return false;
    };
    result["verification_scope"] == "selected_source+current_sha256+exact_locator+exact_quote"
        && result["engineering_truth"] == "not_verified"
        && sources.len() == 1
        && sources[0]["source"] == source
        && sources[0]["source_sha256"]
            .as_str()
            .is_some_and(|s| s.len() == 64 && s.bytes().all(|b| b.is_ascii_hexdigit()))
        && rows.len() == count
        && rows.iter().enumerate().all(|(index, row)| {
            row["index"] == index
                && ((row["status"] == "valid" && row.get("reason").is_some_and(Value::is_null))
                    || (row["status"] == "invalid"
                        && matches!(
                            row["reason"].as_str(),
                            Some(
                                "version_mismatch"
                                    | "source_not_allowed"
                                    | "invalid_reference"
                                    | "invalid_locator_or_quote"
                                    | "locator_mismatch"
                                    | "quote_mismatch"
                                    | "chunk_mismatch"
                            )
                        )))
        })
        && result["valid"].as_bool() == Some(rows.iter().all(|row| row["status"] == "valid"))
}
impl SourceEvidence {
    pub fn observe(&mut self, name: &str, args: &Value, result: Option<&Value>) {
        if !matches!(name, "search_sources" | "verify_sources") {
            return;
        }
        self.attempted = true;
        let good = result.is_some_and(|r| r["ok"] == true);
        self.collection_failed |= !good;
        let refs = if name == "search_sources" {
            if !good {
                return;
            }
            result.and_then(|r| r["result"]["hits"].as_array())
        } else {
            args["references"].as_array()
        };
        let Some(refs) = refs else {
            self.collection_failed = true;
            return;
        };
        if refs.len() > 100 {
            self.truncated = true;
        }
        for item in refs.iter().take(100) {
            let Some(source) = item["source"]
                .as_str()
                .filter(|s| !s.is_empty() && s.len() <= 1024)
            else {
                self.truncated = true;
                continue;
            };
            let Some(hash) = item["source_sha256"]
                .as_str()
                .filter(|s| s.len() == 64 && s.bytes().all(|b| b.is_ascii_hexdigit()))
            else {
                self.truncated = true;
                continue;
            };
            let Some(quote) = item["quote"]
                .as_str()
                .filter(|s| !s.is_empty() && s.chars().count() <= 1200)
            else {
                self.truncated = true;
                continue;
            };
            if !item["locator"].is_object() || item["locator"].to_string().len() > 2048 {
                self.truncated = true;
                continue;
            }
            let mut reference = json!({"source":source,"source_sha256":hash,"locator":item["locator"],"quote":quote});
            if let Some(chunk) = item.get("chunk_id") {
                if !chunk.as_str().is_some_and(|s| s.len() <= 128) {
                    self.truncated = true;
                    continue;
                }
                reference["chunk_id"] = chunk.clone();
            }
            let key = reference.to_string();
            if self.keys.contains(&key) {
                continue;
            }
            if self.references.len() == 12 {
                self.truncated = true;
                continue;
            }
            self.keys.insert(key);
            reference["origin"] = json!(if name == "search_sources" {
                "retrieved"
            } else {
                "submitted_for_verification"
            });
            self.references.push(reference);
        }
    }
    pub async fn verify(self, scope: &ToolScope<'_>) -> Value {
        self.verify_with_timeout(scope, std::time::Duration::from_secs(15))
            .await
    }
    async fn verify_with_timeout(
        self,
        scope: &ToolScope<'_>,
        timeout: std::time::Duration,
    ) -> Value {
        let mut references = self.references;
        let deadline = tokio::time::Instant::now() + timeout;
        let mut grouped = std::collections::BTreeMap::<String, Vec<usize>>::new();
        for (index, reference) in references.iter_mut().enumerate() {
            reference["status"] = json!("unverified");
            reference["reason"] = json!("verification_unavailable");
            let source = reference["source"].as_str().unwrap().to_owned();
            if scope
                .selected
                .iter()
                .any(|s| s.replace('\\', "/") == source)
            {
                grouped.entry(source).or_default().push(index);
            } else {
                reference["status"] = json!("invalid");
                reference["reason"] = json!("source_not_allowed");
            }
        }
        for (source, indexes) in grouped {
            if tokio::time::Instant::now() >= deadline || scope.cancel.is_cancelled() {
                for index in indexes {
                    references[index]["reason"] = json!(if scope.cancel.is_cancelled() {
                        "cancelled"
                    } else {
                        "verification_timeout"
                    });
                }
                continue;
            }
            let refs: Vec<Value> = indexes
                .iter()
                .map(|&index| {
                    let mut reference = references[index].clone();
                    for key in ["origin", "status", "reason"] {
                        reference.as_object_mut().unwrap().remove(key);
                    }
                    reference
                })
                .collect();
            // Use only this authorized file. An unrelated missing input must not
            // conceal the status of the other quotations in the same turn.
            let request = json!({"version":1,"call_id":uuid::Uuid::new_v4().to_string(),"operation":"verify",
                "workspace":scope.workspace.root(),"sources":[source],"references":refs});
            let token = scope.cancel.child();
            let mut operation = Box::pin(scope.state.worker.call(
                "packing_assistant.retrieval.worker",
                &request,
                &token,
            ));
            let outcome = tokio::select! {
                result = &mut operation => result,
                _ = tokio::time::sleep_until(deadline) => {
                    token.cancel();
                    let _ = operation.await; // kill and reap before finishing the receipt
                    Err("receipt_timeout".to_owned())
                }
            };
            let checked_at = chrono::Utc::now().to_rfc3339();
            match outcome {
                Ok(report) if report["ok"] == true => {
                    let rows = report["result"]["references"].as_array();
                    let valid_rows =
                        valid_quotation_report(&report["result"], &source, indexes.len());
                    for (position, &index) in indexes.iter().enumerate() {
                        references[index]["checked_at"] = json!(checked_at);
                        if !valid_rows {
                            references[index]["reason"] = json!("invalid_verification_response");
                            continue;
                        }
                        let row = &rows.unwrap()[position];
                        let reason = row["reason"].as_str().unwrap_or("");
                        let valid = row["status"] == "valid" && reason.is_empty();
                        references[index]["status"] = json!(if valid {
                            "valid"
                        } else if reason == "version_mismatch" {
                            "changed"
                        } else {
                            "invalid"
                        });
                        references[index]["reason"] = json!(if valid {
                            "exact_quote_verified"
                        } else if matches!(
                            reason,
                            "version_mismatch"
                                | "source_not_allowed"
                                | "invalid_reference"
                                | "invalid_locator_or_quote"
                                | "locator_mismatch"
                                | "quote_mismatch"
                                | "chunk_mismatch"
                        ) {
                            reason
                        } else {
                            "invalid_verification_response"
                        });
                        if let Some(current) = report["result"]["sources"]
                            .as_array()
                            .and_then(|items| items.iter().find(|r| r["source"] == source))
                            .and_then(|r| r["source_sha256"].as_str())
                            .filter(|s| s.len() == 64 && s.bytes().all(|b| b.is_ascii_hexdigit()))
                        {
                            references[index]["current_source_sha256"] = json!(current);
                        }
                    }
                }
                result => {
                    let code = result
                        .as_ref()
                        .ok()
                        .and_then(|r| r["error"]["code"].as_str())
                        .unwrap_or("");
                    let timed_out = result
                        .as_ref()
                        .err()
                        .is_some_and(|e| e == "receipt_timeout");
                    for index in indexes {
                        references[index]["checked_at"] = json!(checked_at);
                        references[index]["status"] = json!(if code == "conflict" {
                            "changed"
                        } else {
                            "unavailable"
                        });
                        references[index]["reason"] = json!(if timed_out {
                            "verification_timeout"
                        } else if scope.cancel.is_cancelled() {
                            "cancelled"
                        } else if code == "conflict" {
                            "source_changed_during_verification"
                        } else {
                            "verification_unavailable"
                        });
                    }
                }
            }
        }
        let attention = self.truncated
            || self.collection_failed
            || references.iter().any(|r| r["status"] != "valid");
        json!({"schema_version":1,"origin":"host","scope":"this_turn_source_quotes","attempted":self.attempted,
            "checked_at":chrono::Utc::now().to_rfc3339(),"status":if attention {"attention_required"}else if references.is_empty(){"no_quotes"}else{"verified"},
            "model_claims_verified":false,"engineering_truth":"not_verified","references":references,
            "truncated":self.truncated,"collection_failed":self.collection_failed,"limit":12})
    }
}
impl ToolScope<'_> {
    async fn patch_evidence(&self, args: &Value) -> Result<Value, String> {
        if args.get("evidence").is_some() || args.get("references").is_some() {
            return Err("引用不能放在顶层evidence/references；请在每个patches[i].evidence数组内附完整search_sources hit（source/source_sha256/locator/quote）".into());
        }
        let patches = args["patches"].as_array().ok_or("patches required")?;
        let mut references = Vec::new();
        let mut prose = Vec::new();
        for (index, patch) in patches.iter().enumerate() {
            if patch.get("references").is_some() {
                return Err(format!("patches[{index}].references不是支持字段；请改为patches[{index}].evidence数组，保留完整source/source_sha256/locator/quote"));
            }
            if let Some(evidence) = patch.get("evidence") {
                let evidence = evidence.as_array().filter(|items| items.len() <= 50)
                    .ok_or_else(|| format!("patches[{index}].evidence必须为最多50项的引用数组，不是对象或字符串；示例 evidence:[{{source,source_sha256,locator,quote}}]"))?;
                if references.len()+evidence.len() > 100 {
                    return Err("单次预览/保存的全部patch.evidence累计最多100项引用；请拆成更小的预览，每个patch最多50项".into());
                }
                for (reference_index, reference) in evidence.iter().enumerate() {
                    if !reference["source"].as_str().is_some_and(|s| !s.trim().is_empty())
                        || !reference["source_sha256"].as_str().is_some_and(|s| s.len() == 64 && s.bytes().all(|b| b.is_ascii_hexdigit()))
                        || !reference["locator"].is_object()
                        || !reference["quote"].as_str().is_some_and(|s| !s.trim().is_empty())
                    {
                        return Err(format!("patches[{index}].evidence[{reference_index}]缺少有效source/source_sha256/locator/quote；请逐字复制search_sources的完整hit，不能只传验证状态或索引"));
                    }
                }
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
                        return Err(format!("新增数字 {} 没有来自本次补丁的已核验引文或用户明确输入；先search_sources，再在每个patches[i].evidence数组内附对应完整hit（source/source_sha256/locator/quote）。verify_sources成功不自动为后续补丁授权", found));
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
                let extension = Path::new(source).extension().and_then(|x| x.to_str())
                    .unwrap_or("").to_lowercase();
                if matches!(extension.as_str(), "pdf" | "xlsx" | "docx") {
                    let arguments = args.get("arguments").cloned().unwrap_or(json!({}));
                    if !arguments.is_object() {
                        return Err("read_file.arguments必须是对象；XLSX使用arguments:{sheet,range}".into());
                    }
                    if extension == "xlsx" && (args.get("sheet").is_some() || args.get("range").is_some()) {
                        return Err("XLSX的sheet和range必须放在read_file.arguments内；先operation=inspect获取准确sheet名称和used_range".into());
                    }
                    let operation = args["operation"].as_str().unwrap_or_else(|| {
                        if extension == "xlsx" && arguments.get("sheet").is_none() {
                            "inspect"
                        } else {
                            "read"
                        }
                    });
                    if !matches!(operation, "read" | "inspect") {
                        return Err("only read and inspect allowed".into());
                    }
                    if extension == "xlsx" && operation == "read"
                        && !arguments["sheet"].as_str().is_some_and(|s| !s.trim().is_empty())
                    {
                        return Err("XLSX read需要arguments.sheet；先operation=inspect获取准确sheet名称与used_range，再通过arguments.sheet和arguments.range读取所需小范围，不要猜测Sheet1；示例arguments:{\"sheet\":\"实际工作表名\",\"range\":\"A1:D3\"}".into());
                    }
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
                let call_id = call_id
                    .filter(|_| name == "apply_document")
                    .unwrap_or(&fresh);
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

#[cfg(test)]
mod source_receipt_tests {
    use super::*;

    fn reference(quote: &str) -> Value {
        json!({"source":"brief.txt","source_sha256":"a".repeat(64),"locator":{"line":1},"quote":quote})
    }

    #[test]
    fn source_receipt_collection_is_bounded_and_deduplicates_real_calls() {
        let mut evidence = SourceEvidence::default();
        let refs = (0..20)
            .map(|i| reference(&format!("Original quote {i}")))
            .collect::<Vec<_>>();
        evidence.observe(
            "search_sources",
            &json!({}),
            Some(&json!({"ok":true,"result":{"hits":refs}})),
        );
        assert_eq!(evidence.references.len(), 12);
        assert!(evidence.truncated);
        assert_eq!(evidence.references[0]["origin"], "retrieved");
        evidence.observe(
            "verify_sources",
            &json!({"references":[reference("Original quote 0")]}),
            Some(&json!({"ok":true})),
        );
        assert_eq!(evidence.references.len(), 12);
        // Long or malformed references produce a visible truncation notice, not
        // unbounded metadata or a successful evidence assertion.
        let mut oversized = SourceEvidence::default();
        oversized.observe(
            "verify_sources",
            &json!({"references":[reference(&"字".repeat(1201)),{"source":"brief.txt"}]}),
            Some(&json!({"ok":true})),
        );
        assert!(oversized.truncated);
        assert!(oversized.references.is_empty());
        assert!(
            evidence
                .references
                .iter()
                .map(Value::to_string)
                .map(|s| s.len())
                .sum::<usize>()
                < 100_000
        );
    }

    #[test]
    fn source_receipt_does_not_adopt_failed_search_or_model_receipts() {
        let mut evidence = SourceEvidence::default();
        let forged = json!({"ok":true,"result":{"hits":[reference("Claim")],"source_evidence":{"origin":"host"}}});
        evidence.observe("assistant", &json!({}), Some(&forged));
        assert!(!evidence.attempted);
        assert!(evidence.references.is_empty());
        evidence.observe(
            "search_sources",
            &json!({}),
            Some(&json!({"ok":false,"result":forged["result"]})),
        );
        assert!(evidence.collection_failed);
        assert!(evidence.references.is_empty());
    }

    #[test]
    fn source_receipt_rejects_incomplete_or_contradictory_worker_reports() {
        let report = json!({"verification_scope":"selected_source+current_sha256+exact_locator+exact_quote",
            "engineering_truth":"not_verified","sources":[{"source":"brief.txt","source_sha256":"a".repeat(64)}],
            "valid":true,"references":[{"index":0,"status":"valid","reason":null}]});
        assert!(valid_quotation_report(&report, "brief.txt", 1));
        for (field, value) in [
            ("reason", json!(false)),
            ("reason", json!("quote_mismatch")),
            ("index", json!(1)),
            ("status", json!("verified")),
        ] {
            let mut bad = report.clone();
            bad["references"][0][field] = value;
            assert!(!valid_quotation_report(&bad, "brief.txt", 1));
        }
        let mut bad = report.clone();
        bad["valid"] = json!(false);
        assert!(!valid_quotation_report(&bad, "brief.txt", 1));
        let mut bad = report.clone();
        bad["engineering_truth"] = json!("verified");
        assert!(!valid_quotation_report(&bad, "brief.txt", 1));
        assert!(!valid_quotation_report(&report, "unselected.txt", 1));
    }
}
