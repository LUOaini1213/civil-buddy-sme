//! Recompute only user-selected saved engineering inputs. Model tool arguments
//! never contain coordinates, dimensions, materials, supports or load values.
use super::{providers::cancelled, tools::sha256, worker::WorkerHost};
use crate::runtime_core::{CancellationToken, WorkspaceContext};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::time::Duration;

type Result<T> = std::result::Result<T, String>;
const MAX_SNAPSHOT: usize = 24 * 1024 * 1024;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum EngineeringKind {
    CadSection,
    SavedFrame,
    SavedPlanning,
}

impl EngineeringKind {
    pub fn parse(value: &str) -> Result<Self> {
        match value {
            "cad_section" => Ok(Self::CadSection),
            "saved_frame" => Ok(Self::SavedFrame),
            "saved_planning" => Ok(Self::SavedPlanning),
            _ => Err("工程项目类型无效".into()),
        }
    }
}

/// This value belongs in the host's user-authorized turn request. A model may
/// choose its index, but may not create/modify it through a tool argument.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct EngineeringSelection {
    pub kind: EngineeringKind,
    pub project_id: String,
    pub revision: u64,
    pub source_sha256: Option<String>,
    pub inputs_sha256: String,
    /// Reopened CAD projects reset authorization. Only a current user gesture
    /// can set this, regardless of any saved config.confirmed_solid value.
    #[serde(default)]
    pub confirmed_solid: bool,
}

impl EngineeringSelection {
    /// Validate the user's binding before a turn exists. The authoritative
    /// snapshot is checked again immediately before and after computation.
    pub fn validate(&self) -> Result<()> {
        if !valid_id(&self.project_id) || self.revision == 0 || !valid_hash(&self.inputs_sha256) {
            return Err("工程选择缺少有效项目ID、版本或输入摘要，请重新选择项目".into());
        }
        match self.kind {
            EngineeringKind::CadSection => {
                if !self.source_sha256.as_deref().is_some_and(valid_hash) {
                    return Err("CAD截面选择必须绑定原图SHA-256摘要".into());
                }
            }
            EngineeringKind::SavedFrame => {
                if self.source_sha256.is_some() || self.confirmed_solid {
                    return Err("已保存框架选择不得附带CAD原图或实体确认".into());
                }
            }
            EngineeringKind::SavedPlanning => {
                if self.confirmed_solid
                    || self
                        .source_sha256
                        .as_deref()
                        .is_some_and(|v| !valid_hash(v))
                {
                    return Err("已保存计划不得附带CAD实体确认或无效来源摘要".into());
                }
            }
        }
        Ok(())
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ProjectInspection {
    pub selection: EngineeringSelection,
    pub summary: Value,
    pub status: String,
    pub missing_inputs: Vec<String>,
    pub confirmation_required: bool,
}

#[derive(Clone)]
pub struct EngineeringHost {
    base: String,
    worker: WorkerHost,
    client: reqwest::Client,
    token: Option<String>,
}

struct BoundInputs {
    selection: EngineeringSelection,
    payload: Value,
    summary: Value,
    missing: Vec<String>,
}

impl EngineeringHost {
    pub fn from_env(worker: WorkerHost) -> Result<Self> {
        let base = std::env::var("CIVIL_DOMAIN_URL").map_err(|_| "工程领域服务未启动")?;
        let mut host = Self::new(worker, &base)?;
        host.token = Some(
            std::env::var("CIVIL_DOMAIN_TOKEN")
                .ok()
                .filter(|v| v.len() >= 32)
                .ok_or("领域服务认证未配置")?,
        );
        Ok(host)
    }

    pub fn new(worker: WorkerHost, base: &str) -> Result<Self> {
        let url = reqwest::Url::parse(base).map_err(|_| "无效领域服务地址")?;
        if url.scheme() != "http"
            || !matches!(url.host_str(), Some("127.0.0.1" | "[::1]"))
            || url.path() != "/"
            || url.query().is_some()
            || url.fragment().is_some()
            || !url.username().is_empty()
            || url.password().is_some()
        {
            return Err("工程服务只能使用宿主配置的固定回环地址".into());
        }
        let client = reqwest::Client::builder()
            .no_proxy()
            .timeout(Duration::from_secs(60))
            .redirect(reqwest::redirect::Policy::none())
            .build()
            .map_err(|_| "无法初始化工程服务连接")?;
        Ok(Self {
            base: base.trim_end_matches('/').to_owned(),
            worker,
            client,
            token: None,
        })
    }

    async fn get(&self, path: &str, cancel: &CancellationToken) -> Result<Value> {
        cancel.check().map_err(|e| e.to_string())?;
        let request = async {
            let mut request = self.client.get(format!("{}{path}", self.base));
            if let Some(token) = &self.token {
                request = request.bearer_auth(token);
            }
            let mut response = request.send().await.map_err(|_| "领域项目读取失败")?;
            if !response.status().is_success() {
                return Err(format!(
                    "领域项目读取失败（HTTP {}）",
                    response.status().as_u16()
                ));
            }
            if response
                .content_length()
                .is_some_and(|n| n > MAX_SNAPSHOT as u64)
            {
                return Err("领域项目超过24MiB桥接上限".into());
            }
            let mut bytes = Vec::new();
            while let Some(chunk) = response.chunk().await.map_err(|_| "领域项目读取中断")?
            {
                if bytes.len() + chunk.len() > MAX_SNAPSHOT {
                    return Err("领域项目超过24MiB桥接上限".into());
                }
                bytes.extend_from_slice(&chunk);
            }
            serde_json::from_slice(&bytes).map_err(|_| "领域项目不是有效JSON".into())
        };
        tokio::select! { _ = cancelled(cancel) => Err("工程项目读取已取消".into()), result = request => result }
    }

    pub async fn list_projects(&self, cancel: &CancellationToken) -> Result<Value> {
        let cad = self.get("/api/cad/projects", cancel).await?;
        let engineering = self.get("/api/engineering/projects", cancel).await?;
        let planning = self
            .get("/api/engineering/planning/projects", cancel)
            .await?;
        let mut projects = Vec::new();
        for (kind, list) in [
            (EngineeringKind::CadSection, &cad),
            (EngineeringKind::SavedFrame, &engineering),
            (EngineeringKind::SavedPlanning, &planning),
        ] {
            for row in list["projects"]
                .as_array()
                .ok_or("工程项目目录格式无效")?
                .iter()
                .take(100)
            {
                if kind == EngineeringKind::SavedFrame && row["kind"] != "frame" {
                    continue;
                }
                let Some(id) = row["id"].as_str().filter(|id| valid_id(id)) else {
                    continue;
                };
                if row["revision"].as_u64().is_none_or(|n| n == 0) {
                    continue;
                }
                projects.push(json!({"kind":kind,"project_id":id,"name":row["name"],"revision":row["revision"],
                    "status":"inspection_required","confirmation_required":kind == EngineeringKind::CadSection}));
            }
        }
        Ok(json!({"projects":projects,"scope":"explicit_user_selection_required"}))
    }

    async fn snapshot(
        &self,
        kind: EngineeringKind,
        project_id: &str,
        cancel: &CancellationToken,
    ) -> Result<BoundInputs> {
        if !valid_id(project_id) {
            return Err("工程项目ID无效".into());
        }
        let collection = match kind {
            EngineeringKind::CadSection => "cad",
            EngineeringKind::SavedFrame => "engineering",
            EngineeringKind::SavedPlanning => "engineering/planning",
        };
        let record = self
            .get(&format!("/api/{collection}/projects/{project_id}"), cancel)
            .await?;
        bind_inputs(kind, project_id, record)
    }

    pub async fn inspect_project(
        &self,
        kind: EngineeringKind,
        project_id: &str,
        cancel: &CancellationToken,
    ) -> Result<ProjectInspection> {
        let bound = self.snapshot(kind, project_id, cancel).await?;
        let confirmation_required = kind == EngineeringKind::CadSection;
        Ok(ProjectInspection {
            selection: bound.selection,
            summary: bound.summary,
            status: if !bound.missing.is_empty() {
                "missing_inputs"
            } else if confirmation_required {
                "confirmation_required"
            } else {
                "ready"
            }
            .into(),
            missing_inputs: bound.missing,
            confirmation_required,
        })
    }

    pub async fn calculate(
        &self,
        workspace: &WorkspaceContext,
        authorized: &EngineeringSelection,
        cancel: &CancellationToken,
    ) -> Result<Value> {
        authorized.validate()?;
        let bound = self
            .snapshot(authorized.kind, &authorized.project_id, cancel)
            .await?;
        verify_selection(authorized, &bound.selection)?;
        if !bound.missing.is_empty() {
            return Err(format!(
                "工程输入不完整，拒绝计算：{}",
                bound.missing.join("；")
            ));
        }
        let mut payload = bound.payload;
        let operation = match authorized.kind {
            EngineeringKind::CadSection => {
                if !authorized.confirmed_solid {
                    return Err(
                        "本轮尚未由用户确认实体区域与孔洞；重新打开项目不会继承旧确认".into(),
                    );
                }
                // The only input change is the user's explicit current-turn
                // confirmation. Preserve every saved entity and evidence link.
                payload["config"]["confirmed_solid"] = json!(true);
                "section"
            }
            EngineeringKind::SavedFrame => "frame",
            EngineeringKind::SavedPlanning => "planning_calculate",
        };
        let executed_inputs_sha256 =
            sha256(&serde_json::to_vec(&payload).map_err(|e| e.to_string())?);
        let mut response = self
            .worker
            .engineering(workspace, operation, payload, cancel)
            .await?;
        cancel.check().map_err(|e| e.to_string())?;
        if response["ok"] != true {
            return Ok(response);
        }
        match authorized.kind {
            EngineeringKind::CadSection => {
                if response["result"]["kind"] != "section" {
                    return Err("计算结果类型与授权操作不一致".into());
                }
                if response["result"]["source_sha256"] != json!(authorized.source_sha256) {
                    return Err("截面结果未绑定授权原图摘要".into());
                }
            }
            EngineeringKind::SavedFrame => {
                // analyze_frame returns its native analysis schema, without a
                // kind field. Validate that contract before adding a host tag;
                // no solver values are synthesized, converted or recomputed.
                if !valid_frame_result(&response["result"]) {
                    return Err("梁框架计算结果格式与授权分析不一致".into());
                }
                response["result"]["kind"] = json!("frame");
            }
            EngineeringKind::SavedPlanning => {
                if !valid_planning_result(&response["result"], &bound.selection, &bound.summary) {
                    return Err("计划计算结果未绑定授权的日历、资源、方式与任务快照".into());
                }
            }
        }
        // A result belongs to the selected revision, never a concurrently saved
        // new design. Fail before publishing it as the current project result.
        let after = self
            .snapshot(authorized.kind, &authorized.project_id, cancel)
            .await?;
        verify_selection(authorized, &after.selection)?;
        response["provenance"] = json!({"kind":authorized.kind,"project_id":authorized.project_id,
            "revision":authorized.revision,"source_sha256":authorized.source_sha256,
            "saved_inputs_sha256":authorized.inputs_sha256,"executed_inputs_sha256":executed_inputs_sha256,
            "geometry_origin":"validated_saved_project","source_project_modified":false,
            "current_user_confirmed_solid":authorized.confirmed_solid,"engineering_verdict":"not_provided"});
        if authorized.kind == EngineeringKind::SavedPlanning {
            response["result"]["synthetic"] = bound.summary["synthetic"].clone();
            response["provenance"]["result_origin"] = json!("fixed_solver_recomputed");
            response["provenance"]["calculation_method"] = bound.summary["method"].clone();
            response["provenance"]["geometry_origin"] = Value::Null;
        }
        Ok(response)
    }

    /// Read only the explicitly selected saved plan. Recorded historical
    /// results are labelled as such; this is not a new solver execution.
    pub async fn inspect_selected_plan(
        &self,
        authorized: &EngineeringSelection,
        cancel: &CancellationToken,
    ) -> Result<Value> {
        authorized.validate()?;
        if authorized.kind != EngineeringKind::SavedPlanning {
            return Err("planning_inspect只接受用户选定的已保存计划".into());
        }
        let bound = self
            .snapshot(authorized.kind, &authorized.project_id, cancel)
            .await?;
        verify_selection(authorized, &bound.selection)?;
        let after = self
            .snapshot(authorized.kind, &authorized.project_id, cancel)
            .await?;
        verify_selection(authorized, &after.selection)?;
        Ok(
            json!({"ok":true,"result":{"kind":"planning_inspection","saved_plan":bound.summary},
            "provenance":{"kind":authorized.kind,"project_id":authorized.project_id,"revision":authorized.revision,
                "source_sha256":authorized.source_sha256,"saved_inputs_sha256":authorized.inputs_sha256,
                "result_origin":"saved_record_not_recomputed","source_project_modified":false,"engineering_verdict":"not_provided"}}),
        )
    }
}

fn valid_planning_result(result: &Value, selected: &EngineeringSelection, summary: &Value) -> bool {
    result["kind"] == "planning"
        && result["method"] == summary["method"]
        && result["plan"] == summary["plan"]
        && result["inputs_sha256"] == selected.inputs_sha256
        && valid_saved_planning_result(
            &result["result"],
            &result["plan"],
            result["method"].as_str().unwrap_or(""),
        )
}

fn valid_saved_planning_result(result: &Value, plan: &Value, method: &str) -> bool {
    let engine = if method == "cpm" {
        "civil-buddy-cpm"
    } else {
        "OR-Tools CP-SAT"
    };
    let Some(tasks) = plan["tasks"].as_array() else {
        return false;
    };
    let Some(rows) = result["tasks"].as_array() else {
        return false;
    };
    result["kind"] == method && result["engine"]["name"] == engine
        && result["engine"]["version"].as_str().is_some_and(|v| !v.is_empty())
        && result["duration_workdays"].as_u64().is_some_and(|v| v <= 3654)
        && result["finish_date"].as_str().is_some_and(|v| v.len() == 10)
        && rows.len() == tasks.len() && !rows.is_empty()
        && rows.iter().enumerate().all(|(i, row)| {
            row["id"] == tasks[i]["id"]
                && row["start"].as_str().is_some_and(|v| v.len() == 10)
                && row["end"].as_str().is_some_and(|v| v.len() == 10)
                && matches!((row["start_offset"].as_u64(), row["finish_offset"].as_u64()), (Some(a),Some(b)) if a <= b && b <= 3654)
        })
        && (method != "resource" || (result["resource_optimized"] == true
            && result["resource_feasible"] == true
            && matches!(result["solver_status"].as_str(), Some("OPTIMAL" | "FEASIBLE"))
            && result["proven_optimal"].is_boolean()))
}

fn valid_frame_result(result: &Value) -> bool {
    fn named(value: &Value) -> bool {
        value.as_str().is_some_and(|name| !name.is_empty())
    }
    fn vector3(value: &Value) -> bool {
        value.as_array().is_some_and(|values| {
            values.len() == 3
                && values
                    .iter()
                    .all(|v| v.as_f64().is_some_and(f64::is_finite))
        })
    }
    if result["ok"] != true
        || result["schema_version"].as_u64() != Some(1)
        || result["analysis"] != "linear_elastic_frame"
        || result["engine"]["name"] != "PyniteFEA"
        || !named(&result["engine"]["version"])
        || result.get("kind").is_some_and(|kind| kind != "frame")
    {
        return false;
    }
    result["combinations"]
        .as_array()
        .is_some_and(|combinations| {
            !combinations.is_empty()
                && combinations.iter().all(|combination| {
                    named(&combination["id"])
                        && combination["factors"].as_object().is_some_and(|factors| {
                            !factors.is_empty()
                                && factors.iter().all(|(name, factor)| {
                                    !name.is_empty() && factor.as_f64().is_some_and(f64::is_finite)
                                })
                        })
                        && combination["nodes"].as_array().is_some_and(|nodes| {
                            !nodes.is_empty()
                                && nodes.iter().all(|node| {
                                    named(&node["id"])
                                        && [
                                            "displacement_m",
                                            "rotation_rad",
                                            "reaction_N",
                                            "reaction_Nm",
                                        ]
                                        .iter()
                                        .all(|key| vector3(&node[*key]))
                                })
                        })
                        && combination["members"].as_array().is_some_and(|members| {
                            !members.is_empty()
                                && members.iter().all(|member| {
                                    named(&member["id"])
                                        && named(&member["i"])
                                        && named(&member["j"])
                                        && member["length_m"].as_f64().is_some_and(|length| {
                                            length.is_finite() && length > 0.0
                                        })
                                        && member["curves"].is_object()
                                        && member["sampled_extrema"].is_object()
                                })
                        })
                })
        })
}

fn valid_id(value: &str) -> bool {
    value.len() == 32
        && value
            .bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
}
fn valid_hash(value: &str) -> bool {
    value.len() == 64
        && value
            .bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
}
fn verify_selection(
    authorized: &EngineeringSelection,
    current: &EngineeringSelection,
) -> Result<()> {
    if authorized.kind != current.kind
        || authorized.project_id != current.project_id
        || authorized.revision != current.revision
        || authorized.source_sha256 != current.source_sha256
        || authorized.inputs_sha256 != current.inputs_sha256
    {
        return Err(
            "工程项目或输入已变化，请重新检查并选择当前修订版；未发布旧输入计算结果".into(),
        );
    }
    Ok(())
}

fn bind_inputs(kind: EngineeringKind, project_id: &str, record: Value) -> Result<BoundInputs> {
    if kind == EngineeringKind::SavedPlanning {
        return bind_planning_inputs(project_id, record);
    }
    if record["ok"] != true || record["project"]["id"] != project_id {
        return Err("领域项目身份不匹配".into());
    }
    let revision = record["project"]["revision"]
        .as_u64()
        .filter(|n| *n > 0)
        .ok_or("领域项目修订号缺失")?;
    let mut missing = Vec::new();
    let (payload, source_sha256, summary) = match kind {
        EngineeringKind::CadSection => {
            let document = &record["document"];
            let config = &record["draft_config"];
            if !document.is_object() || !config.is_object() {
                return Err("CAD项目缺少源图或参数快照".into());
            }
            let source = document["sha256"]
                .as_str()
                .filter(|hash| valid_hash(hash))
                .ok_or("CAD原图缺少SHA-256证据")?
                .to_owned();
            if document["entities"]
                .as_array()
                .is_none_or(|entities| entities.is_empty())
            {
                missing.push("原图没有可分析实体".into());
            }
            if config["mode"] != "section" {
                missing.push("需在CAD工作台保存section模式，不能从建筑模型猜测截面".into());
            }
            if !matches!(
                config["unit"].as_str(),
                Some("mm" | "cm" | "m" | "in" | "ft")
            ) {
                missing.push("原图单位未明确".into());
            }
            if config["layers"]
                .as_object()
                .is_none_or(|layers| !layers.values().any(|role| role == "section"))
            {
                missing.push("尚未指定截面图层".into());
            }
            let summary = json!({"name":record["project"]["name"],"filename":document["filename"],"unit":config["unit"],
                "entity_count":document["entities"].as_array().map(Vec::len),"layers":config["layers"],"selection":config["selection"],
                "dimension_bindings":config["dimension_bindings"],"confirmation_reset":true});
            (
                json!({"document":document,"config":config}),
                Some(source),
                summary,
            )
        }
        EngineeringKind::SavedFrame => {
            if record["project"]["kind"] != "frame" || record["snapshot"]["kind"] != "frame" {
                return Err("该项目不是已保存的梁框架输入".into());
            }
            let inputs = record["snapshot"]["inputs"].clone();
            if !inputs.is_object() {
                return Err("梁框架项目缺少输入快照".into());
            }
            if inputs["schema_version"] != 1 || inputs["units"] != "SI" {
                missing.push("缺少明确SI单位的v1框架输入".into());
            }
            for key in [
                "nodes",
                "members",
                "materials",
                "sections",
                "load_cases",
                "combinations",
            ] {
                if inputs[key].as_array().is_none_or(|rows| rows.is_empty()) {
                    missing.push(format!("缺少已保存的{key}"));
                }
            }
            for key in ["nodal_loads", "member_loads"] {
                if !inputs[key].is_array() {
                    missing.push(format!("必须明确{key}（无荷载也须为空数组）"));
                }
            }
            let summary = json!({"name":record["project"]["name"],"units":inputs["units"],"source":inputs["source"],
                "node_count":inputs["nodes"].as_array().map(Vec::len),"member_count":inputs["members"].as_array().map(Vec::len),
                "origin":"saved_explicit_frame_inputs","cad_geometry_inference":false});
            (inputs, None, summary)
        }
        EngineeringKind::SavedPlanning => unreachable!(),
    };
    let inputs_sha256 = sha256(&serde_json::to_vec(&payload).map_err(|e| e.to_string())?);
    Ok(BoundInputs {
        selection: EngineeringSelection {
            kind,
            project_id: project_id.into(),
            revision,
            source_sha256,
            inputs_sha256,
            confirmed_solid: false,
        },
        payload,
        summary,
        missing,
    })
}

fn bind_planning_inputs(project_id: &str, record: Value) -> Result<BoundInputs> {
    let project = &record["project"];
    if project["id"] != project_id
        || project["schema"] != "civil-buddy.planning.v1"
        || project["historical"] == true
    {
        return Err("领域计划身份不匹配或不是当前修订版".into());
    }
    let selection: EngineeringSelection =
        serde_json::from_value(project["agent_selection"].clone())
            .map_err(|_| "计划服务尚不支持版本绑定，请更新领域服务")?;
    selection.validate()?;
    if selection.kind != EngineeringKind::SavedPlanning
        || selection.project_id != project_id
        || project["revision"].as_u64() != Some(selection.revision)
        || selection.confirmed_solid
    {
        return Err("计划选择与当前项目身份不匹配".into());
    }
    let method = project["method"]
        .as_str()
        .filter(|v| matches!(*v, "cpm" | "resource"))
        .ok_or("已保存计划的计算方式无效")?;
    let source = project["import_source"]["source"]["sha256"]
        .as_str()
        .map(str::to_owned);
    if selection.source_sha256 != source {
        return Err("计划原文件摘要不匹配".into());
    }
    let canonical = project["agent_inputs_json"]
        .as_str()
        .ok_or("计划缺少规范化输入摘要")?;
    let payload: Value = serde_json::from_str(canonical).map_err(|_| "计划输入快照无效")?;
    if sha256(canonical.as_bytes()) != selection.inputs_sha256
        || payload != json!({"plan":project["plan"],"method":method})
        || !valid_saved_planning_result(&project["result"], &project["plan"], method)
    {
        return Err("计划日历、资源、方式或结果与授权摘要不一致".into());
    }
    let summary = json!({"name":project["name"],"method":method,"plan":project["plan"],
        "recorded_result":project["result"],"result_origin":"saved_record_not_recomputed",
        "synthetic":project["synthetic"],"import_source":project["import_source"],
        "origin":"saved_explicit_planning_inputs","plan_editing":"dedicated_planning_page_only"});
    Ok(BoundInputs {
        selection,
        payload,
        summary,
        missing: Vec::new(),
    })
}
