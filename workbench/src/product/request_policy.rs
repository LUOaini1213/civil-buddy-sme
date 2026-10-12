//! Pure current-turn document policy; model prose, sources and history grant no authority.
//! ToolScope and the worker independently enforce sandbox and document integrity.
use serde_json::{json, Value};

pub(super) const CONFIRMATION_TEXT: &str = "我明白，将由持证人员签认";

pub(super) fn acknowledgement_present(field: &str) -> bool {
    field == CONFIRMATION_TEXT
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum RiskScope {
    Low,
    High,
    Unclassified,
}

impl RiskScope {
    fn as_str(self) -> &'static str {
        match self {
            Self::Low => "low",
            Self::High => "high",
            Self::Unclassified => "unclassified",
        }
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(super) struct RequestIntent {
    pub read_only: bool,
    pub publication_requested: bool,
}

impl RequestIntent {
    pub fn classify(message: &str, selected_documents: bool) -> Self {
        // Preserve the bounded recogniser. Do not transform the message sent to tools/model.
        Self {
            read_only: read_only_document_request(message, selected_documents),
            publication_requested: requests_document_publication(message, selected_documents),
        }
    }
}

pub(super) struct WritePolicy {
    pub intent: RequestIntent,
    risk: RiskScope,
    acknowledged: bool,
    selected_by_user: bool,
}

impl WritePolicy {
    pub fn new(
        message: &str,
        selected_documents: bool,
        selected_risk: Option<&str>,
        selected_by_user: bool,
        confirmation: &str,
    ) -> Self {
        Self {
            intent: RequestIntent::classify(message, selected_documents),
            // Only the user's selection may establish low-risk scope.
            risk: match (selected_by_user, selected_risk) {
                (true, Some("low")) => RiskScope::Low,
                (true, Some("high")) => RiskScope::High,
                _ => RiskScope::Unclassified,
            },
            acknowledged: acknowledgement_present(confirmation),
            selected_by_user,
        }
    }

    /// Availability is not permission. Reads/previews may precede acknowledgement,
    /// but publication_rejection still gates every apply, including forged calls.
    pub fn advertises_document_writes(&self, sandbox_write: bool) -> bool {
        sandbox_write && !self.intent.read_only
    }

    /// Check the entire batch before its first tool, even apply-before-load ordering.
    pub fn observe_batch(&mut self, calls: &[Value]) -> bool {
        batch_loads_high_risk(calls) && self.raise_high_risk()
    }

    pub fn observe_skill_result(&mut self, result: &Value) -> bool {
        result["risk"] == "high" && self.raise_high_risk()
    }

    fn raise_high_risk(&mut self) -> bool {
        let changed = self.risk != RiskScope::High;
        self.risk = RiskScope::High;
        changed
    }

    /// Request veto wins over acknowledgement. ToolScope separately enforces sandbox.
    pub fn publication_rejection(&self, locale: &str) -> Option<Value> {
        if self.intent.read_only {
            return Some(
                json!({"ok":false,"code":"document_write_forbidden_by_request",
                "error":if locale == "en" {
                    "This turn requests read-only work or explicitly forbids file creation. No document was saved. Ask explicitly to save a new copy in a new turn; sandbox permission and acknowledgement do not override this request."
                } else {
                    "本轮请求只读或明确禁止生成文件，未保存文档。需要副本时请在新一轮明确要求保存；工作区写入权限和签认不能覆盖本轮只读要求。"
                }, "scope":"current_turn"}),
            );
        }
        write_confirmation_error(
            self.risk == RiskScope::High,
            self.risk == RiskScope::Unclassified,
            self.acknowledged,
            locale,
        )
        .map(|message| {
            json!({"ok":false,"error":message,"code":"risk_confirmation_required",
                "risk":self.risk.as_str(),"confirmation_source":"risk_confirmation",
                "confirmation_scope":"current_turn"})
        })
    }

    pub fn audit(&self) -> Value {
        json!({"scope":"current_turn","risk":self.risk.as_str(),
            "selected_by":if self.selected_by_user {"user"} else {"automatic"},
            "confirmation_source":"risk_confirmation","confirmation_present":self.acknowledged,
            "request_read_only":self.intent.read_only,"model_can_lower_risk":false})
    }
}

pub(super) fn capabilities() -> Value {
    json!({"source":"risk_confirmation","scope":"current_turn","high_risk_required":true,
        "automatic_unclassified_required":true,"model_can_lower_risk":false,
        "read_only_request_enforced":true})
}

fn batch_loads_high_risk(calls: &[Value]) -> bool {
    calls.iter().any(|call| {
        call["function"]["name"] == "load_skill"
            && call["function"]["arguments"]
                .as_str()
                .and_then(|args| serde_json::from_str::<Value>(args).ok())
                .is_some_and(|args| {
                    crate::catalog::seed()
                        .experts
                        .iter()
                        .any(|expert| args["skill_id"] == expert.id && expert.risk == "high")
                })
    })
}

fn write_confirmation_error(
    high_risk: bool,
    unclassified: bool,
    signed: bool,
    locale: &str,
) -> Option<&'static str> {
    if signed {
        return None;
    }
    match (high_risk, unclassified, locale == "en") {
        (true, _, true) => Some("This turn includes high-risk work. Type the exact acknowledgement in this turn's dedicated field: 我明白，将由持证人员签认"),
        (true, _, false) => Some("本轮涉及高风险岗位；请在本轮签认框完整输入：我明白，将由持证人员签认"),
        (false, true, true) => Some("Automatic-mode document writes have no user-selected risk scope. Select a low-risk specialist, or type the exact acknowledgement in this turn's dedicated field: 我明白，将由持证人员签认. Loading a specialist via the model does not grant permission."),
        (false, true, false) => Some("自动模式的文档写入尚无用户选定的风险范围；请明确选择低风险岗位，或在本轮签认框完整输入：我明白，将由持证人员签认。模型加载岗位不能代替用户授权。"),
        _ => None,
    }
}

/// Source quotes describe material, not permission to publish a document.
fn unquoted_request(request: &str) -> String {
    let mut fence: Option<(char, usize)> = None;
    let mut lines = Vec::new();
    for line in request.lines() {
        let head = line.trim_start();
        let marker = head.chars().next().filter(|c| matches!(c, '`' | '~'));
        let count = marker.map_or(0, |marker| {
            head.chars().take_while(|c| *c == marker).count()
        });
        if let Some((open, width)) = fence {
            if marker == Some(open) && count >= width {
                fence = None;
            }
            lines.push("〔引用〕");
        } else if count >= 3 {
            fence = Some((marker.unwrap(), count));
            lines.push("〔引用〕");
        } else if head.starts_with('>') {
            lines.push("〔引用〕");
        } else {
            lines.push(line);
        }
    }
    let quoted = regex::Regex::new(
        r#"`[^`\n]*`|“[^”]*”|‘[^’]*’|「[^」]*」|『[^』]*』|"[^"\n]*"|(?:^|[\s(])'[^'\n]*'"#,
    )
    .unwrap();
    quoted
        .replace_all(&lines.join("\n"), "〔引用〕")
        .into_owned()
}

fn document_write_veto(request: &str) -> bool {
    let directive = regex::Regex::new(r"(?i)(?:^|[.!?;，,。；：:\n])\s*(?:please\s+)?(?:no\s+(?:files?|deliverables?|writing)|read[-\s]+only(?:\s+mode)?|preview\s+only|只读|仅预览|只预览|只(?:看|检查|核对|解释|展示)不(?:改|写)|不生成文件)(?:\s+please)?\s*(?:$|[.!?;，,。；：:\n])").unwrap();
    let prohibition = regex::Regex::new(r"(?i)(?:不要|不用|不需要|不必|先别|暂不|不得|禁止|不)(?:实际|真正|直接|继续|再|先)?\s*(?:保存|另存|写盘|写入|导出|生成|创建|制作|修改|更改|修复|修正|调整|替换|删除|新增|添加|应用)\s*|\b(?:do\s+not|don['’]t|never|without|no\s+need\s+to)\s+(?:(?:actually|really|directly|yet|now)\s+)?(?:save|saving|write|writing|export|exporting|publish|publishing|create|creating|generate|generating|produce|producing|modify|modifying|edit|editing|change|changing|fix|fixing|correct|correcting|apply|applying|delete|deleting)\b\s*").unwrap();
    let original_target = regex::Regex::new(r"(?i)^\s*(?:(?:changes\s+to|to|into|in|the|my|our|any)\s+)*(?:原件|原文件|源文件|(?:original|source)(?:\s+(?:file|document|workbook))?s?\b)").unwrap();
    let additional_target = regex::Regex::new(r"(?i)^\s*(?:and|or|nor|和|与|及|或|、)\s*(?:(?:the|any|new|draft)\s+)*(?:copies|copy|files?|documents?|副本|文件|文档)").unwrap();
    let file_target = regex::Regex::new(r"(?i)^\s*(?:(?:any|a|an|the|these|those|new|draft|my|our|your|all|more|to|into)\s+)*(?:files?|documents?|copies|copy|reports?|deliverables?|outputs?|artifacts?|anything|anywhere|[\w.-]+\.(?:docx|xlsx|xlsm|pdf|md|txt|csv)\b|(?:任何|任何新|新的?|其他|这个|这些|全部|任何一个)?(?:文件|文档|副本|报告|草稿|交付物|输出文件|任何东西)|〔引用〕)|^\s*(?:yet|now|anymore)?\s*(?:$|[.!?;，,。；\n])").unwrap();
    directive.is_match(request)
        || prohibition.find_iter(request).any(|found| {
            // Only the prohibition's actual object can be the original. Merely
            // mentioning 'original' elsewhere cannot cancel a no-files directive.
            let object = &request[found.end()..];
            match original_target.find(object) {
                Some(original) => additional_target.is_match(&object[original.end()..]),
                None => file_target.is_match(object),
            }
        })
}

fn explanatory_request_head(clause: &str) -> bool {
    regex::Regex::new(r"(?i)^\s*(?:(?:please|just|only|hi|hey|so|also|can\s+you|could\s+you|would\s+you)\s+)*(?:what|how|why|when|where|who|which|should|is|are|was|were|does|did|has|have|can\s+(?:i|we)|do\s+(?:i|we|you|they)|explain|describe|tell\s+me|walk\s+me\s+through|summari[sz]e|review|inspect|check|read|preview)\b|^\s*(?:请|帮我)?(?:只|仅|仅仅)?(?:解释|讲解|如何|怎么|为什么|为何|什么|哪些|是否|是不是|有没有|总结|汇总|预览|检查|核对|查看|读取|只读)").unwrap().is_match(clause)
}

fn read_only_document_request(request: &str, selected: bool) -> bool {
    let request = unquoted_request(request);
    document_write_veto(&request)
        || (!requests_document_publication(&request, selected)
            && request
                .split(['。', '！', '？', '，', ',', ';', '；', '\n'])
                .any(explanatory_request_head))
}

/// Classify explicit operations on selected documents before looking at any
/// model reply, excluding quoted instructions and vetoed operations. A
/// preview/no-write instruction wins over a request to change content, while
/// "do not overwrite the original" does not prohibit saving a new copy.
fn requests_document_publication(request: &str, selected: bool) -> bool {
    let request = unquoted_request(request).replace("能不能", "能否");
    let clauses: Vec<_> = request
        .split(['。', '！', '？', '，', ',', ';', '；', '\n'])
        .collect();
    if document_write_veto(&request) {
        return false;
    }
    let publication =
        regex::Regex::new(r"(?i)保存|另存|导出|写入|写盘|\b(?:save|export|apply|publish)\b")
            .unwrap();
    let edit = regex::Regex::new(r"(?i)修改|更改|改成|改为|改好|改一下|修复|修正|换成|设为|设置为|替换|调整|删除|插入|新增|添加|更新|重排|填写|填入|批注|\b(?:edit|modify|change|replace|update|revise|rewrite|reorder|fill|annotate|add|remove|delete|insert)\b").unwrap();
    // "correct" can describe a value, and "fix" can name a proposed solution.
    // These new verbs require an imperative or an explicit request prefix.
    let repair_command = regex::Regex::new(r"(?i)(?:^\s*(?:please\s+)?|\b(?:please|can\s+you|could\s+you|would\s+you|will\s+you|help\s+(?:me|us)(?:\s+to)?)\s+)(?:fix|correct)\b|\b(?:and|then)\s+(?:fix|correct)\s+(?:the|this|that|these|those|a|an|my|our|your|its|all)\b").unwrap();
    let create = regex::Regex::new(
        r"(?i)生成|创建|制作|新建|编写|写一|写份|\b(?:create|generate|produce|make|write|draft)\b",
    )
    .unwrap();
    let document = regex::Regex::new(r"(?i)报告|文档|文件|附件|表格|台账|模型|\.docx\b|\.xlsx\b|\.pdf\b|\.glb\b|\b(?:report|document|file|attachment|spreadsheet|workbook|model|pdf|docx|xlsx)s?\b").unwrap();
    let explanation = regex::Regex::new(r"(?i)如何|怎么|为什么|为何|解释|讲解|是什么|什么意思|哪些|是否|是不是|区别|原理|示例|总结|汇总|不要|不用|不需要|不必|先别|暂不|禁止|不能|不(?:修改|更改|改动|改好|改一下|修复|修正|替换|更新|调整|删除|插入|写入|保存|生成)|\b(?:how|why|explain|describe|example|summarize|without|not|don't|never)\b").unwrap();
    let read_then_write = regex::Regex::new(r"(?i)^\s*(?:please\s+)?(?:(?:can|could|would|will)\s+you\s+(?:please\s+)?)?(?:summari[sz]e|review|inspect|check|read|translate|compare|analy[sz]e)\b[^;\n]{0,300}\b(?:and|then)\s+(?:please\s+)?(?:save|export|apply|publish|edit|modify|update|revise|rewrite|annotate|fix|create|generate|write|draft|correct\s+(?:(?:the|this|that|these|those|a|an|my|our|your|its|all)\s+|it\b|them\b|typos?\b|files?\b|documents?\b|spreadsheets?\b))\b|^\s*(?:请|帮我)?(?:总结|汇总|检查|核对|查看|读取|翻译|比较|分析)[^；\n]{0,300}(?:并|然后|再)(?:请|帮我)?(?:保存|另存|导出|写入|修改|更改|修复|修正|更新|调整|批注|生成|创建)").unwrap();
    let read_instructions = regex::Regex::new(r"(?i)^\s*(?:please\s+)?(?:review|inspect|check|read)\s+(?:how|why|what)\b|^\s*(?:请|帮我)?(?:查看|检查|核对|读取)(?:如何|怎么|为什么)").unwrap();
    clauses.iter().any(|clause| {
        (read_then_write.is_match(clause) && !read_instructions.is_match(clause))
            || (!explanatory_request_head(clause)
                && !explanation.is_match(clause)
                && (publication.is_match(clause)
                    || ((selected || document.is_match(clause))
                        && (edit.is_match(clause) || repair_command.is_match(clause)))
                    || (create.is_match(clause) && document.is_match(clause))))
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn user_risk_scope_cannot_be_lowered_by_batch_order_or_model_skill_selection() {
        for locale in ["zh-CN", "en"] {
            assert!(write_confirmation_error(false, false, false, locale).is_none());
            assert!(write_confirmation_error(false, true, false, locale).is_some());
            assert!(write_confirmation_error(true, false, false, locale).is_some());
            assert!(write_confirmation_error(true, true, true, locale).is_none());
        }
        let high = crate::catalog::seed()
            .experts
            .iter()
            .find(|expert| expert.risk == "high")
            .unwrap()
            .id
            .clone();
        let low = crate::catalog::seed()
            .experts
            .iter()
            .find(|expert| expert.risk == "low")
            .unwrap()
            .id
            .clone();
        let call = |id: &str| json!({"function":{"name":"load_skill","arguments":json!({"skill_id":id}).to_string()}});
        let apply = json!({"function":{"name":"apply_document","arguments":"{}"}});
        assert!(batch_loads_high_risk(&[
            apply.clone(),
            call(&high),
            call(&low)
        ]));
        assert!(!batch_loads_high_risk(&[call(&low), apply.clone()]));
        assert!(!batch_loads_high_risk(&[call("doc-word"), apply]));
        assert!(!batch_loads_high_risk(&[
            json!({"function":{"name":"load_skill","arguments":"broken JSON"}})
        ]));
    }

    #[test]
    fn read_only_requests_override_sandbox_and_acknowledgement_not_original_preservation() {
        for request in [
            "只预览，不保存文件。",
            "请修改标题，但不要生成文件。",
            "Update the report; preview only.",
            "Update the report; no files.",
            "Do not write any files because the original must remain intact.",
            "Do not save copies of the original document.",
            "Do not save the original and any copies.",
            "不要保存原件和副本。",
            "只读检查原件，不生成文件。",
            "请只读检查 report.docx。",
            "What changes would you make to report.docx?",
            "Could you explain how to save report.docx?",
            "Is this spreadsheet correct?",
            "Does report.docx need a new title?",
            "Can I save a copy of report.docx?",
            "Please only preview the proposed changes.",
            "Review the selected report.",
            "Review how to edit and save the report.",
            "Check that the totals are complete and correct.",
            "解释如何修改报告。",
            "Explain this example:\n```\nSave a new copy of report.docx.\n```",
            "Explain the note:\n~~~~\nCreate a report.docx\n~~~~",
            "Explain the unfinished source:\n```\nSave a new copy of report.docx.",
            "Explain the quoted note:\n> Save a new copy of report.docx.",
        ] {
            assert!(
                read_only_document_request(request, true),
                "Read-only task not enforced: {request}"
            );
            assert!(
                !requests_document_publication(request, true),
                "Read-only task reported as a write: {request}"
            );
        }
        for request in [
            "修改标题，不要覆盖原件，另存新副本。",
            "Do not modify the original; update the draft and save a new copy.",
            "Update the report, do not save changes to the original; save a new copy.",
            "修改标题为‘不要保存文件’，另存副本。",
            "Update the title to \"No files\"; save a copy.",
            "Explain the discrepancies, then update the report and save a copy.",
            "Summarize the source and save a new copy.",
            "Please review and annotate report.docx.",
            "Can you review the file and correct the typo?",
            "总结附件并保存新副本。",
            "Update the report; do not generate invented facts.",
            "修改标题，不要生成编造的数据。",
        ] {
            assert!(
                !read_only_document_request(request, true),
                "Authorised new copy blocked: {request}"
            );
            assert!(
                requests_document_publication(request, true),
                "Authorised new copy omitted: {request}"
            );
        }
    }

    #[test]
    fn selected_document_execution_is_classified_before_model_wording() {
        for request in [
            "请把 report.docx 的标题改成 X。",
            "Can you update quantities.xlsx A1 from 10 to 12?",
            "修改标题，不要覆盖原件，另存新副本",
            "Update the draft, do not overwrite the source; save a copy",
        ] {
            assert!(requests_document_publication(request, true), "{request}");
        }
        for request in [
            "帮我生成一份报告",
            "Create a report.docx",
            "能不能制作一份表格",
            "把 report.docx 的标题改成 X。",
        ] {
            assert!(requests_document_publication(request, false), "{request}");
        }
        for request in [
            "解释附件中的‘已修改’是什么意思",
            "总结附件改动，不修改任何文件",
            "哪些单元格已经修改，汇总给我看",
            "Please summarize the edits without changing the file",
            "不要保存，先展示差异",
            "请改成 X，但先不要写盘。",
            "为什么没有保存成功？",
            "为何保存失败？",
        ] {
            assert!(!requests_document_publication(request, true), "{request}");
        }
    }

    #[test]
    fn policy_matrix_keeps_request_veto_and_risk_separate_from_sandbox() {
        for locale in ["zh-CN", "en"] {
            for risk in [None, Some("low"), Some("high"), Some("unexpected")] {
                for signed in [false, true] {
                    for read_only in [false, true] {
                        let policy = WritePolicy::new(
                            if read_only {
                                "Update the report; no files."
                            } else {
                                "Update the report and save a new copy."
                            },
                            true,
                            risk,
                            risk.is_some(),
                            if signed { CONFIRMATION_TEXT } else { "" },
                        );
                        assert_eq!(policy.intent.read_only, read_only);
                        assert_eq!(policy.intent.publication_requested, !read_only);
                        assert!(!policy.advertises_document_writes(false));
                        assert_eq!(policy.advertises_document_writes(true), !read_only);
                        let rejected = policy.publication_rejection(locale);
                        assert_eq!(
                            rejected.is_some(),
                            read_only || (!signed && risk != Some("low"))
                        );
                        if let Some(rejected) = rejected {
                            assert_eq!(
                                rejected["code"],
                                if read_only {
                                    "document_write_forbidden_by_request"
                                } else {
                                    "risk_confirmation_required"
                                }
                            );
                        }
                        assert_eq!(policy.audit()["confirmation_present"], signed);
                    }
                }
            }
        }
    }

    #[test]
    fn model_results_raise_risk_stickily_but_never_supply_user_low_risk_scope() {
        let mut automatic = WritePolicy::new("Save report.docx", true, Some("low"), false, "");
        assert_eq!(automatic.audit()["risk"], "unclassified");
        assert!(!automatic.observe_skill_result(&json!({"risk":"low"})));
        assert_eq!(
            automatic.publication_rejection("en").unwrap()["risk"],
            "unclassified"
        );
        assert!(automatic.observe_skill_result(&json!({"risk":"high"})));
        assert!(!automatic.observe_skill_result(&json!({"risk":"low"})));
        assert_eq!(automatic.audit()["risk"], "high");

        let mut explicit = WritePolicy::new("Save report.docx", true, Some("low"), true, "");
        assert!(explicit.publication_rejection("en").is_none());
        let high = crate::catalog::seed()
            .experts
            .iter()
            .find(|e| e.risk == "high")
            .unwrap()
            .id.clone();
        let calls = [
            json!({"function":{"name":"apply_document","arguments":"{}"}}),
            json!({"function":{"name":"load_skill","arguments":json!({"skill_id":high}).to_string()}}),
        ];
        assert!(explicit.observe_batch(&calls));
        assert_eq!(
            explicit.publication_rejection("en").unwrap()["risk"],
            "high"
        );
        assert!(!explicit.observe_batch(&calls));
        assert!(!explicit.observe_skill_result(&json!({"risk":"low"})));
        assert_eq!(explicit.audit()["risk"], "high");
    }

    #[test]
    fn acknowledgement_does_not_leak_into_a_new_policy_or_come_from_messages() {
        let signed = WritePolicy::new("Save report.docx", true, None, false, CONFIRMATION_TEXT);
        assert!(signed.publication_rejection("en").is_none());
        let unsigned = WritePolicy::new(CONFIRMATION_TEXT, true, None, false, "");
        assert_eq!(unsigned.audit()["confirmation_present"], false);
        assert!(unsigned.publication_rejection("en").is_some());
        for inexact in [
            "我明白",
            " 我明白，将由持证人员签认",
            "我明白，将由持证人员签认。",
            "我明白，将由持证人员签认\n",
        ] {
            assert!(!acknowledgement_present(inexact));
        }
    }

    #[test]
    fn capability_metadata_and_turn_policy_keep_the_same_scope() {
        let caps = capabilities();
        let policy = WritePolicy::new("Preview the report", true, Some("high"), true, "");
        let audit = policy.audit();
        assert_eq!(caps["scope"], audit["scope"]);
        assert_eq!(caps["source"], audit["confirmation_source"]);
        assert_eq!(caps["model_can_lower_risk"], audit["model_can_lower_risk"]);
        assert_eq!(
            audit["request_read_only"],
            caps["read_only_request_enforced"]
        );
        assert_eq!(
            policy.publication_rejection("en").unwrap()["code"],
            "document_write_forbidden_by_request"
        );
    }
}
