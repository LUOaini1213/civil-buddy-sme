import { createAuth } from "./modules/auth.js";
import { renderAgentMarkdown } from "./modules/agent-markdown.js";

const STORE_KEY = "cb_agent_workspaces_v1";
const RISK_PHRASE = "我明白，将由持证人员签认";
const confirmedMessage = (text) => String(text || "").trim() === RISK_PHRASE;
const TERMINAL = new Set(["completed", "succeeded", "failed", "cancelled", "interrupted"]);
const STATUS = { queued: "等待执行", running: "执行中", waiting_approval: "等待确认", cancelling: "正在停止", completed: "已完成", succeeded: "已完成", failed: "未完成", cancelled: "已停止", interrupted: "服务重启时中断" };
const EVENT_NAMES = { status: "执行阶段", context: "上下文预算", model: "模型调用", tool_started: "工具开始", tool_finished: "工具结束", subtask_started: "子任务开始", subtask_finished: "子任务结束", artifact: "产物已保存", source_evidence: "来源引文核验", decision: "工程判断", error: "执行错误", done: "本轮结束" };
const CHILD_ROLES = { evidence: "证据检索", review: "独立复核" };
const TURN_EVENTS = { "turn.started": "running", "turn.cancelling": "cancelling", "turn.completed": "completed", "turn.failed": "failed", "turn.cancelled": "cancelled", "turn.interrupted": "interrupted" };
const idOf = (turn) => String(turn && (turn.turn_id || turn.id) || "");
const object = (value) => value && typeof value === "object" && !Array.isArray(value);
const printable = (value) => typeof value === "string" ? value : JSON.stringify(value, null, 2) || "";
const displayPath = (value) => value.startsWith("\\\\?\\UNC\\") ? "\\\\" + value.slice(8) : value.startsWith("\\\\?\\") ? value.slice(4) : value;
const engineeringKey = (value) => value.kind + ":" + value.project_id;
const snapshotKey = (value) => JSON.stringify([value.kind, value.project_id, value.revision, value.source_sha256, value.inputs_sha256]);
const artifactKey = (value) => String(value.id || value.url || value.name || "");
const artifactVersion = (value) => JSON.stringify([value.id, value.url, value.output_sha256, value.source, value.source_sha256]);
const HASH = /^[a-f0-9]{64}$/i;
const ID = /^[A-Za-z0-9_-]{1,128}$/;
const READINESS_LABELS = { source_hash: "副本哈希", structure: "文件结构", active_content: "活动内容", external_links: "外部链接",
  layout: "版式核对", document_fields: "修订与域", formula_cache: "公式缓存", spreadsheet_errors: "错误单元格",
  recalculation: "公式重算", external_data: "外部数据", engineering_review: "专业核对" };
const CHECK_STATUS = { pass: "已检查", review_required: "待核对", blocked: "需处理", not_applicable: "不适用" };
function engineeringSnapshot(data, expected) {
  const value = data?.selection;
  if (!object(value) || engineeringKey(value) !== engineeringKey(expected) || !Number.isSafeInteger(value.revision) || value.revision < 1
    || !/^[a-f0-9]{64}$/i.test(value.inputs_sha256 || "") || !(value.source_sha256 === null || /^[a-f0-9]{64}$/i.test(value.source_sha256 || ""))) throw new Error("工程快照缺少有效修订号或来源校验值");
  return { kind: value.kind, project_id: value.project_id, revision: value.revision,
    source_sha256: value.source_sha256, inputs_sha256: value.inputs_sha256, confirmed_solid: false };
}

export function artifactUrl(value, origin, workspace) {
  try {
    const url = new URL(value, origin);
    return url.origin === origin && /^\/api\/agent\/artifacts\/[A-Za-z0-9_-]+$/.test(url.pathname)
      && url.searchParams.get("workspace") === workspace ? url.href : "";
  } catch (_) { return ""; }
}

export function createAgentWorkbench(deps) {
  const doc = deps.document, win = deps.window;
  const t = (source, values = {}) => win.CBI18n?.t(source, values) ?? String(source || "").replace(/\{(\w+)\}/g, (match, key) => values[key] ?? match);
  const fetcher = deps.fetch || ((url, init) => win.fetch(url, init));
  const storage = deps.storage || win.localStorage;
  const later = deps.setTimeout || setTimeout, unschedule = deps.clearTimeout || clearTimeout;
  const newId = deps.makeSessionId || (() => globalThis.crypto && typeof globalThis.crypto.randomUUID === "function"
    ? globalThis.crypto.randomUUID().replaceAll("-", "") : Date.now().toString(36) + Math.random().toString(36).slice(2));
  const $ = (id) => doc.getElementById(id);
  const node = (tag, text, cls) => { const n = doc.createElement(tag); if (text !== undefined) n.textContent = text; if (cls) n.className = cls; return n; };
  const state = { capabilities: null, workspace: null, session: "", files: [], selected: new Set(), turn: null,
    seq: 0, emptyEventPages: 0, nextTurnCursor: null, turnsLoading: false, sessionListEpoch: 0, sessionsLoading: false, nextSessionCursor: null, sessionHistoryMessage: null, artifacts: new Map(), artifactChecks: new Map(), engineeringResults: new Map(), epoch: 0, workspaceEpoch: 0, fileEpoch: 0, listEpoch: 0,
    contextData: null, taskRows: [], eventRows: [], subtasks: new Map(), packingResults: new Map(), modelConfig: null, writeGateReason: null, sourceEvidence: null,
    engineeringEpoch: 0, engineeringRows: [], engineeringLoading: false, engineeringStatus: null, terminalNotice: false, experts: [],
    opening: false, submitting: false, cancelling: false, modelBusy: false, modelConfigured: false, disposed: false };
  let saved = { lastRoot: "", workspaces: {} }, timer = null, controller = null, started = false;
  try { const value = JSON.parse(storage.getItem(STORE_KEY) || "null"); if (object(value) && object(value.workspaces)) saved = value; } catch (_) { /* Storage is optional. */ }
  function persist() { try { storage.setItem(STORE_KEY, JSON.stringify(saved)); } catch (_) { /* Storage may be disabled. */ } }
  function record() { return state.workspace && saved.workspaces[state.workspace.id]; }
  function sessionRecord() { return record() && record().sessions.find((item) => item.id === state.session); }
  function notice(text, error = false, terminal = false) { state.terminalNotice = terminal; $("agentNotice").textContent = text; $("agentNotice").className = "notice" + (error ? " error" : ""); }
  function terminalNotice() {
    notice(t(STATUS[state.turn.status]) + (state.turn.result?.partial ? t("，部分工作尚未完成。") : (win.CBI18n?.locale === "en" ? "." : "。")), state.turn.status === "failed", true);
  }
  function engineeringStatus(key, values = {}, detail = "") {
    state.engineeringStatus = { key, values, detail };
    $("agentEngineeringStatus").textContent = t(key, values) + detail;
  }
  const scoped = () => new URLSearchParams({ workspace: state.workspace.id, session_id: state.session }).toString();
  const active = () => state.turn && !TERMINAL.has(state.turn.status);
  function current(epoch) { return !state.disposed && epoch === state.epoch; }
  function cancelVoice() { win.CivilBuddyVoice?.cancel(t("页面上下文已改变，旧语音草稿已取消。")); }
  async function request(url, init) {
    const response = await fetcher(url, init);
    let body;
    try { body = await response.json(); } catch (_) { throw new Error(t("服务返回了无法读取的数据（HTTP {status}）", { status: response.status })); }
    if (!response.ok) throw new Error(typeof body.detail === "string" ? body.detail : body.error || body.message || `HTTP ${response.status}`);
    return body;
  }
  const post = (body) => ({ method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  function stopPolling() { if (timer !== null) unschedule(timer); timer = null; if (controller) controller.abort(); controller = null; }
  function resetView({ keepHistory = false, keepPackingSelection = false } = {}) {
    state.subtasks.clear();
    state.packingResults.clear();
    if (!keepPackingSelection) $("agentPackingSource").value = "";
    stopPolling(); state.epoch += 1; state.listEpoch += 1; state.turn = null; state.seq = 0; state.emptyEventPages = 0; state.turnsLoading = false; if (!keepHistory) { state.nextTurnCursor = null; state.taskRows = []; } state.eventRows = []; state.contextData = null; state.artifacts.clear(); state.artifactChecks.clear(); state.engineeringResults.clear();
    state.submitting = false; state.cancelling = false;
    state.writeGateReason = null; paintWriteGate();
    state.sourceEvidence = null; paintSourceEvidence(null);
    $("agentEvents").replaceChildren(); $("agentArtifacts").replaceChildren(); $("agentReply").textContent = t("结果将显示在这里。");
    if ($("agentEngineeringResults")) { $("agentEngineeringResults").replaceChildren(); $("agentEngineeringResults").hidden = true; }
    if ($("agentPackingResults")) { $("agentPackingResults").replaceChildren(); $("agentPackingResults").hidden = true; }
    $("agentTurnStatus").textContent = t("尚未开始"); $("agentPartial").hidden = true; $("agentUsage").hidden = true;
    $("agentTurnActor").textContent = t("尚无任务发起者记录。");
    $("agentContextMeter").hidden = true; $("agentContextText").textContent = t("收到后端请求预算后显示；这不是任务完成进度。");
    controls();
  }
  function controls() {
    const caps = state.capabilities;
    const mode = $("agentMode").value;
    const supported = caps && caps.available === true && Array.isArray(caps.modes) && caps.modes.includes(mode)
      && Array.isArray(caps.sandbox) && caps.sandbox.includes($("agentSandbox").value);
    const highRiskWrite = (state.experts.find((item) => item.id === $("agentExpert").value)?.risk === "high" || !!$("agentPackingSource").value) && $("agentSandbox").value === "workspace-write";
    const confirmationPresent = $("agentRiskConfirmation").value.trim() === RISK_PHRASE || confirmedMessage($("agentMessage").value);
    $("agentRiskWrap").hidden = !highRiskWrite;
    $("agentRiskConfirmation").disabled = state.submitting;
    $("agentSend").disabled = !state.workspace || state.opening || state.submitting || !!active() || state.engineeringLoading || state.engineeringRows.some((row) => row.loading) || !supported || (mode === "model" && !state.modelConfigured) || (highRiskWrite && !confirmationPresent);
    $("agentCancel").hidden = !active() || !(caps && caps.features && caps.features.cancel);
    $("agentCancel").disabled = state.cancelling || state.turn && state.turn.status === "cancelling";
    for (const id of ["agentRefreshFiles", "agentRefreshTurns", "agentNewSession", "agentSession"]) $(id).disabled = !state.workspace || state.opening;
    $("agentWorkspaceOpen").disabled = state.opening;
    const history = state.capabilities?.features?.session_discovery === true;
    $("agentSessionHistory").hidden = !history;
    $("agentRefreshSessions").disabled = !state.workspace || state.opening || state.sessionsLoading;
    $("agentMoreSessions").hidden = !state.nextSessionCursor;
    $("agentMoreSessions").disabled = state.opening || state.sessionsLoading;
    $("agentMoreTurns").hidden = !state.nextTurnCursor;
    $("agentMoreTurns").disabled = state.opening || state.turnsLoading;
    $("agentModelSave").disabled = state.modelBusy;
    $("agentRefreshEngineering").disabled = !state.workspace || state.opening || state.submitting || state.engineeringLoading;
    $("agentExpert").disabled = state.submitting;
    $("agentPackingControl").hidden = caps?.features?.packing_replan !== true;
    $("agentPackingSource").disabled = !state.workspace || state.opening || state.submitting || !!active() || caps?.features?.packing_replan !== true;
    expertNote();
    paintEngineering();
  }
  function paintCapabilities(caps) {
    state.capabilities = caps;
    const identity = caps.identity || {};
    $("agentIdentity").textContent = identity.mode === "named_single_user_instance"
      ? t("当前身份：{user} · 独立用户与工程实例（非共享多人服务）", { user: identity.user_id })
      : identity.mode === "local_single_user" ? t("当前身份：本机单用户 · 未启用登录验证")
      : t("当前服务尚未声明用户身份与实例隔离方式。");
    if (caps.models && typeof caps.models.configured === "boolean") state.modelConfigured = caps.models.configured;
    $("agentCapability").textContent = caps.available === true
      ? t("Agent 已连接。先选资料；执行阶段、工具结果与产物均来自服务端。")
      : caps.unavailability_reason || t("当前服务未启用 Agent 工作台，可返回岗位工作台继续使用现有功能。");
    for (const [id, values] of [["agentMode", caps.modes], ["agentSandbox", caps.sandbox]]) {
      const select = $(id), options = Array.from(select.options);
      options.forEach((item) => { item.disabled = !Array.isArray(values) || !values.includes(item.value); });
      if (!options.some((item) => item.value === select.value && !item.disabled)) select.value = options.find((item) => !item.disabled)?.value || "";
    }
    const isolation = caps.sandbox_controls || {};
    $("agentSandboxNote").textContent = isolation.os_enforced === true
      ? t("服务端报告工具工作进程已启用操作系统写入隔离；文件写入仍受当前权限策略约束。") +
        (isolation.reads_confined === false ? t("不限制读取范围。") : "") + (isolation.network_confined === false ? t("不隔离网络访问。") : "")
      : isolation.policy === true ? t("当前启用应用权限策略；服务端未声明操作系统强制隔离。") : t("服务端尚未声明隔离能力。");
    controls();
  }
  async function loadCapabilities() {
    try { paintCapabilities(await request("/api/agent/capabilities")); }
    catch (error) { paintCapabilities({ available: false }); notice(t("Agent 能力读取失败：") + error.message, true); }
  }
  function paintSessions() {
    const select = $("agentSession"); select.replaceChildren();
    for (const item of record()?.sessions || []) {
      const suffix = Number.isSafeInteger(item.turn_count) && item.turn_count > 0
        ? t(" · {count} 条任务", { count: item.turn_count }) + " · " + (t(STATUS[item.status]) || item.status || "") : "";
      const option = node("option", t("会话 ") + item.id.slice(0, 10) + suffix); option.value = item.id; select.appendChild(option);
    }
    select.value = state.session;
  }
  function paintSessionHistory() {
    const message = state.sessionHistoryMessage;
    $("agentSessionHistoryStatus").textContent = message ? t(message.key, message.values || {}) : "";
    controls();
  }
  async function loadSessions({ more = false, preferServer = false } = {}) {
    if (!state.workspace || state.capabilities?.features?.session_discovery !== true) return;
    if (more && (!state.nextSessionCursor || state.sessionsLoading)) return;
    const workspace = state.workspace.id, workspaceEpoch = state.workspaceEpoch, selectedSession = state.session;
    const requestId = ++state.sessionListEpoch, cursor = more ? state.nextSessionCursor : null;
    state.sessionsLoading = true; state.sessionHistoryMessage = { key: "正在读取已保存的会话…" }; paintSessionHistory();
    const query = new URLSearchParams({ workspace, limit: "50" }); if (cursor) query.set("cursor", cursor);
    try {
      const data = await request("/api/agent/sessions?" + query);
      if (state.disposed || state.workspace?.id !== workspace || state.workspaceEpoch !== workspaceEpoch || requestId !== state.sessionListEpoch) return;
      const valid = Array.isArray(data.sessions) && data.sessions.length <= 100 && data.workspace === workspace
        && data.sessions.every(item => object(item) && ID.test(item.session_id || "") && ID.test(item.latest_turn_id || "")
          && Number.isSafeInteger(item.turn_count) && item.turn_count > 0 && typeof item.updated_at === "string" && item.updated_at.length <= 64
          && typeof item.status === "string" && Object.hasOwn(STATUS, item.status))
        && (data.next_cursor === null || (typeof data.next_cursor === "string" && data.next_cursor.length > 0 && data.next_cursor.length <= 2048));
      if (!valid || (cursor && data.next_cursor === cursor)) throw new Error(t("会话历史响应无法核对，请重新刷新。"));
      const item = record(), known = new Map(item.sessions.map(row => [row.id, row]));
      for (const row of data.sessions) {
        const previous = known.get(row.session_id);
        known.set(row.session_id, { ...previous, id: row.session_id, turn: previous?.turn || row.latest_turn_id,
          latest_turn_id: row.latest_turn_id, updated_at: row.updated_at, status: row.status, turn_count: row.turn_count });
      }
      // Keep unsent local drafts and a user's historical task selection.
      item.sessions = [...known.values()];
      if (preferServer && state.session === selectedSession && data.sessions.length) {
        state.session = data.sessions[0].session_id; item.current = state.session;
      }
      state.nextSessionCursor = data.next_cursor; persist(); paintSessions();
      state.sessionHistoryMessage = { key: state.nextSessionCursor ? "已恢复会话，可继续加载更早记录。" : "已读取已保存的会话。" };
    } catch (error) {
      if (state.workspace?.id === workspace && state.workspaceEpoch === workspaceEpoch && requestId === state.sessionListEpoch)
        state.sessionHistoryMessage = { key: "会话历史读取失败：{error}", values: { error: error.message } };
    } finally {
      if (state.workspace?.id === workspace && state.workspaceEpoch === workspaceEpoch && requestId === state.sessionListEpoch) {
        state.sessionsLoading = false; paintSessionHistory();
      }
    }
  }
  function paintFiles() {
    paintPackingSources();
    const host = $("agentFiles"); host.replaceChildren();
    if (!state.workspace) { host.appendChild(node("p", t("打开文件夹后显示可读资料。"), "muted")); return; }
    if (!state.files.length) { host.appendChild(node("p", t("这个文件夹暂未列出可读资料。"), "muted")); return; }
    for (const file of state.files) {
      const label = node("label", undefined, "file-row"), check = node("input"); check.type = "checkbox"; check.checked = state.selected.has(file.path);
      check.addEventListener("change", () => { check.checked ? state.selected.add(file.path) : state.selected.delete(file.path); record().files = [...state.selected]; persist(); paintPackingSources(); controls(); });
      const info = node("span", file.name || file.path); info.appendChild(node("small", file.path + (Number.isFinite(file.size) ? t(" · {size} 字节", { size: file.size.toLocaleString() }) : "")));
      label.append(check, info); host.appendChild(label);
    }
  }
  function paintPackingSources() {
    const select = $("agentPackingSource"), selected = select.value;
    select.replaceChildren();
    const empty = node("option", t("本轮不重排")); empty.value = ""; select.appendChild(empty);
    for (const file of state.files) {
      if (!state.selected.has(file.path) || !/\.json$/i.test(file.path)) continue;
      const option = node("option", file.path); option.value = file.path; select.appendChild(option);
    }
    select.value = [...select.options].some(option => option.value === selected) ? selected : "";
  }
  async function loadFiles() {
    if (!state.workspace) return;
    const id = state.workspace.id, version = ++state.fileEpoch;
    try {
      const data = await request("/api/agent/files?" + new URLSearchParams({ workspace: id }));
      if (state.disposed || state.workspace?.id !== id || version !== state.fileEpoch) return;
      state.files = Array.isArray(data.files) ? data.files.filter((file) => file && typeof file.path === "string") : [];
      const paths = new Set(state.files.map((file) => file.path)); state.selected = new Set([...state.selected].filter((path) => paths.has(path)));
      record().files = [...state.selected]; persist(); paintFiles();
    } catch (error) { if (state.workspace?.id === id && version === state.fileEpoch) notice(t("资料列表读取失败：") + error.message, true); }
  }
  function clearEngineering() {
    state.engineeringEpoch += 1; state.engineeringLoading = false;
    for (const row of state.engineeringRows) { row.selected = false; row.confirmed = false; row.loading = false; row.detail = null; row.snapshot = null; }
    engineeringStatus("工程选择已清空；请重新读取需要使用的项目快照。");
    paintEngineering();
  }
  function paintEngineering() {
    const host = $("agentEngineering"); host.replaceChildren();
    const busy = state.submitting || state.opening || state.engineeringLoading;
    for (const row of state.engineeringRows) {
      const card = node("article", undefined, "engineering-card"), title = node("strong", row.name);
      card.dataset.project = engineeringKey(row); card.append(title, node("p", (row.kind === "cad_section" ? t("CAD 截面") : t("已保存框架")) + t(" · 列表修订 {revision}", { revision: row.revision }), "muted small"));
      const inspect = node("button", row.loading ? t("正在读取…") : row.detail ? t("重新核对快照") : t("读取当前快照")); inspect.type = "button";
      inspect.disabled = busy || row.loading; inspect.addEventListener("click", () => inspectEngineering(row)); card.appendChild(inspect);
      if (row.error) card.appendChild(node("p", row.error, "notice error small"));
      if (row.detail) {
        const detail = row.detail, missing = Array.isArray(detail.missing_inputs) ? detail.missing_inputs : [];
        const summary = node("details"); summary.append(node("summary", t("当前修订 {revision} · 查看输入摘要", { revision: row.snapshot.revision })), node("pre", printable(detail.summary ?? {}))); card.appendChild(summary);
        if (missing.length || detail.status === "missing_inputs") card.appendChild(node("p", t("缺少输入：") + (missing.map(printable).join("；") || t("请返回工程页面补全")), "notice warning small"));
        const needsConfirm = row.kind === "cad_section" || detail.confirmation_required === true;
        if (needsConfirm && detail.status !== "missing_inputs") {
          const label = node("label", undefined, "engineering-check"), check = node("input"); check.type = "checkbox"; check.checked = row.confirmed; check.disabled = busy || row.loading;
          check.addEventListener("change", () => { row.confirmed = check.checked; if (!row.confirmed) row.selected = false; controls(); });
          label.append(check, node("span", t("我已核对当前截面的实体区域与孔洞"))); card.appendChild(label);
        }
        const label = node("label", undefined, "engineering-check"), check = node("input"); check.type = "checkbox"; check.checked = row.selected;
        check.disabled = busy || row.loading || missing.length > 0 || !["ready", "confirmation_required"].includes(detail.status) || (needsConfirm && !row.confirmed);
        check.addEventListener("change", () => {
          if (check.checked && state.engineeringRows.filter((item) => item.selected).length >= 4) { check.checked = false; engineeringStatus("最多选择 4 个工程项目。"); return; }
          row.selected = check.checked; engineeringStatus("已选 {count} / 4 个项目。", { count: state.engineeringRows.filter((item) => item.selected).length }); controls();
        });
        label.append(check, node("span", t("加入本次任务"))); card.appendChild(label);
      }
      host.appendChild(card);
    }
  }
  async function loadEngineering() {
    if (!state.workspace) return;
    clearEngineering(); const version = state.engineeringEpoch; state.engineeringLoading = true; state.engineeringRows = []; controls();
    engineeringStatus("正在读取已保存工程…");
    try {
      const data = await request(`/api/agent/engineering/projects?${scoped()}`);
      if (version !== state.engineeringEpoch || state.disposed) return;
      if (!Array.isArray(data.projects)) throw new Error(t("工程项目列表不完整"));
      state.engineeringRows = data.projects.filter((row) => row && ["cad_section", "saved_frame"].includes(row.kind) && /^[A-Za-z0-9_-]+$/.test(row.project_id || "")).map((row) => ({ ...row, name: String(row.name || row.project_id), selected: false, confirmed: false, loading: false, detail: null, snapshot: null }));
      engineeringStatus(state.engineeringRows.length ? "请先读取需要使用的项目快照；刷新会清除上次确认。" : "暂无可用的已保存截面或框架。");
    } catch (error) { if (version === state.engineeringEpoch) engineeringStatus("工程选集暂不可用：", {}, error.message); }
    finally { if (version === state.engineeringEpoch) { state.engineeringLoading = false; controls(); } }
  }
  async function fetchEngineering(row) {
    const detail = await request(`/api/agent/engineering/projects/${encodeURIComponent(row.kind)}/${encodeURIComponent(row.project_id)}?${scoped()}`);
    return { detail, snapshot: engineeringSnapshot(detail, row) };
  }
  async function inspectEngineering(row) {
    const version = state.engineeringEpoch;
    row.selected = false; row.confirmed = false; row.detail = null; row.snapshot = null; row.error = ""; row.loading = true; controls();
    engineeringStatus("已清除此项目的旧选择，请核对当前快照。");
    try { const value = await fetchEngineering(row); if (version === state.engineeringEpoch && state.engineeringRows.includes(row) && !state.disposed) Object.assign(row, value); }
    catch (error) { if (version === state.engineeringEpoch) row.error = t("快照读取失败：") + error.message; }
    finally { if (version === state.engineeringEpoch) { row.loading = false; controls(); } }
  }
  async function verifyEngineering(epoch) {
    const rows = state.engineeringRows.filter((row) => row.selected), version = state.engineeringEpoch;
    const results = await Promise.allSettled(rows.map(fetchEngineering));
    if (!current(epoch) || version !== state.engineeringEpoch) return false;
    let valid = true;
    results.forEach((result, index) => {
      const row = rows[index];
      if (result.status === "rejected") { row.error = t("提交前核对失败：") + result.reason.message; row.selected = false; row.confirmed = false; valid = false; return; }
      const value = result.value;
      if (snapshotKey(value.snapshot) !== snapshotKey(row.snapshot) || !["ready", "confirmation_required"].includes(value.detail.status) || (value.detail.missing_inputs || []).length || (value.detail.confirmation_required === true && !row.confirmed)) {
        Object.assign(row, value); row.selected = false; row.confirmed = false; row.error = t("工程修订或输入已变化，请重新核对后选择。"); valid = false;
      }
    });
    if (!valid) { engineeringStatus("部分工程选择已失效，请重新核对。"); notice(t("工程快照已变化或无法核对；任务尚未提交，请重新检查工程选集。"), true); controls(); }
    return valid;
  }
  function expertNote() {
    const expert = state.experts.find((item) => item.id === $("agentExpert").value);
    $("agentExpertNote").textContent = expert ? (t(expert.title) || t(expert.name)) + (expert.risk === "high" ? t("；该岗位涉及高风险工程内容，服务端会执行签认门禁。") : "")
      : $("agentSandbox").value === "workspace-write" ? t("保存副本前请手动选择岗位；仍可读取和预览。") : t("由任务内容选择岗位；服务端决定可用工具和签认要求。");
  }
  function paintWriteGate() {
    const panel = $("agentWriteGateNote"); panel.hidden = !state.writeGateReason;
    panel.textContent = state.writeGateReason === "post_selection_required"
      ? t("本次保存请求被拦截。请手动选择对应岗位后重新提交；旧轮确认不会沿用。")
      : state.writeGateReason === "current_turn_confirmation_required"
        ? t("本次保存请求被拦截。请手动选择对应高风险岗位，重新输入签认句后提交；旧轮确认不会沿用。") : "";
  }
  async function loadExperts() {
    try {
      const data = await request("/api/catalog");
      if (!Array.isArray(data.experts)) throw new Error(t("岗位目录不完整"));
      state.experts = data.experts.filter((item) => item && item.enabled !== false && typeof item.id === "string" && item.id);
      paintExperts();
      expertNote();
      controls();
    } catch (error) { $("agentExpertNote").textContent = t("岗位目录暂不可用，保留自动选择：") + error.message; }
  }
  function paintExperts() {
      const select = $("agentExpert"); const selected = select.value; select.replaceChildren(); const auto = node("option", t("自动选择")); auto.value = ""; select.appendChild(auto);
      for (const expert of state.experts) { const option = node("option", `${t(expert.category_name) || t("岗位")} · ${t(expert.name) || expert.id}`); option.value = expert.id; select.appendChild(option); }
      select.value = selected;
  }
  async function openWorkspace(path, { expectedId = "", restoreSelection = true, restoreTurn = true, manual = false } = {}) {
    if (!String(path || "").trim() || state.opening) return;
    cancelVoice();
    clearEngineering();
    resetView(); const epoch = state.epoch, requestId = ++state.workspaceEpoch;
    state.sessionListEpoch += 1; state.sessionsLoading = false; state.nextSessionCursor = null; state.sessionHistoryMessage = null;
    state.opening = true; controls(); paintSessionHistory(); notice(t("正在打开工程文件夹…"));
    try {
      const data = await request("/api/agent/workspaces", post({ path: path.trim() }));
      if (!current(epoch) || requestId !== state.workspaceEpoch) return;
      if (!data.workspace || typeof data.workspace.id !== "string" || typeof data.workspace.root !== "string") throw new Error(t("工作区响应缺少标识或路径"));
      if (expectedId && data.workspace.id !== expectedId) throw new Error(t("工程链接与服务器返回的工程不一致，请从项目页重新打开。"));
      state.workspace = data.workspace;
      $("agentMessage").value = "";
      $("agentRiskConfirmation").value = "";
      if (data.capabilities) paintCapabilities(data.capabilities);
      let item = saved.workspaces[state.workspace.id];
      if (object(item) && Array.isArray(item.sessions)) item.sessions = item.sessions.filter((s) => s && typeof s.id === "string" && /^[A-Za-z0-9_-]{1,128}$/.test(s.id));
      const hasLocalSessions = object(item) && Array.isArray(item.sessions) && item.sessions.length > 0;
      if (!hasLocalSessions) item = { sessions: [{ id: newId(), turn: "" }], files: [] };
      saved.workspaces[state.workspace.id] = item;
      state.session = item.sessions.some((s) => s.id === item.current) ? item.current : item.sessions[0].id;
      item.current = state.session;
      state.selected = new Set(restoreSelection && Array.isArray(item.files) ? item.files : []); saved.lastRoot = state.workspace.root; persist();
      if (manual) {
        // A prior project-page link must not override this explicit choice on reload.
        const url = new URL(win.location.href);
        if (url.searchParams.has("workspace")) {
          url.searchParams.delete("workspace");
          win.history.replaceState(win.history.state, "", url.pathname + url.search + url.hash);
        }
      }
      $("agentWorkspacePath").value = displayPath(state.workspace.root); $("agentWorkspaceStatus").textContent = displayPath(state.workspace.root);
      paintSessions(); notice(t("工程文件夹已打开。可勾选资料后开始任务。"));
      await Promise.all([loadFiles(), loadSessions({ preferServer: !hasLocalSessions }), loadEngineering()]);
      if (!current(epoch) || requestId !== state.workspaceEpoch) return;
      await loadTurns();
      if (restoreTurn && current(epoch) && sessionRecord()?.turn) await showTurn(sessionRecord().turn);
    } catch (error) { if (current(epoch)) notice(t("打开失败：") + error.message, true); }
    finally { if (requestId === state.workspaceEpoch) { state.opening = false; controls(); } }
  }
  function paintTurns() {
    const host = $("agentTurns"); host.replaceChildren();
    if (!state.taskRows.length) host.appendChild(node("p", t("本会话暂无执行记录。"), "muted"));
    for (const turn of state.taskRows) {
      const id = idOf(turn); if (!id) continue;
      const button = node("button", (turn.message || turn.title || id.slice(0, 12)) + " · " + (t(STATUS[turn.status]) || turn.status || t("状态待读取")) + t(" · 发起者：") + (turn.actor_id || t("历史记录未标记")));
      button.type = "button"; button.setAttribute("aria-current", String(id === idOf(state.turn)));
      button.addEventListener("click", () => showTurn(id)); host.appendChild(button);
    }
    controls();
  }
  async function loadTurns({ more = false, keepHistory = false } = {}) {
    if (!state.workspace || (more && (!state.nextTurnCursor || state.turnsLoading))) return;
    const epoch = state.epoch, requestId = ++state.listEpoch;
    const cursor = more ? state.nextTurnCursor : null;
    const query = new URLSearchParams({ workspace: state.workspace.id, session_id: state.session, limit: "50" });
    if (cursor) query.set("cursor", cursor);
    state.turnsLoading = true; controls();
    try {
      const data = await request("/api/agent/turns?" + query);
      if (!current(epoch) || requestId !== state.listEpoch) return;
      if ((data.workspace !== undefined && data.workspace !== state.workspace.id)
        || (data.session_id !== undefined && data.session_id !== state.session)
        || !Array.isArray(data.turns) || data.turns.length > 100
        || data.turns.some(row => !object(row) || !ID.test(idOf(row)) || (row.session_id && row.session_id !== state.session))
        || (data.next_cursor != null && (typeof data.next_cursor !== "string" || !data.next_cursor || data.next_cursor.length > 2048))
        || (cursor && data.next_cursor === cursor)) throw new Error(t("执行历史响应无法核对，请重新刷新。"));
      const keepCursor = keepHistory && state.taskRows.length > 0;
      const merged = new Map((more ? state.taskRows : data.turns).map(row => [idOf(row), row]));
      for (const row of (more ? data.turns : keepHistory ? state.taskRows : [])) if (more || !merged.has(idOf(row))) merged.set(idOf(row), row);
      state.taskRows = [...merged.values()]; if (!keepCursor) state.nextTurnCursor = data.next_cursor || null; paintTurns();
    } catch (error) { if (current(epoch) && requestId === state.listEpoch) notice(t("执行记录读取失败：") + error.message, true); }
    finally { if (current(epoch) && requestId === state.listEpoch) { state.turnsLoading = false; controls(); } }
  }
  async function changeSession(id, create = false) {
    if (!record()) return;
    if (create) { id = newId(); record().sessions.unshift({ id, turn: "" }); }
    if (!record().sessions.some((item) => item.id === id)) return;
    cancelVoice();
    clearEngineering();
    resetView(); const epoch = state.epoch; state.session = id; record().current = id; persist(); paintSessions(); $("agentMessage").value = ""; $("agentRiskConfirmation").value = ""; controls();
    notice(t("已切换会话；此前启动的任务继续由服务端管理。"));
    await loadTurns(); if (current(epoch) && sessionRecord()?.turn) await showTurn(sessionRecord().turn);
  }
  function paintContext(data) {
    state.contextData = data;
    const used = data.used, limit = data.limit, reserve = data.reserve;
    if (![used, limit, reserve].every((v) => typeof v === "number" && Number.isFinite(v)) || used < 0 || reserve < 0 || limit <= reserve) return;
    const meter = $("agentContextMeter"); meter.max = limit - reserve; meter.value = Math.min(used, meter.max); meter.hidden = false;
    const scope = data.scope === "request" ? t("本次请求") : t("后端报告");
    $("agentContextText").textContent = t("{scope}输入{kind} {used} / {budget}；输出预留 {reserve}，窗口 {limit}。", { scope, kind: data.estimated ? t("估算") : t("用量"), used: used.toLocaleString(), budget: (limit - reserve).toLocaleString(), reserve: reserve.toLocaleString(), limit: limit.toLocaleString() }) +
      (data.estimated ? t("估算不代表计费用量。") : "") + (data.omitted_history_messages > 0 ? t(" 本次省略 {count} 条历史消息。", { count: data.omitted_history_messages }) : "");
  }
  function paintArtifact(data) {
    if (!object(data)) return;
    const key = artifactKey(data); if (!key) return;
    const previous = state.artifacts.get(key);
    if (previous && artifactVersion(previous) !== artifactVersion(data)) state.artifactChecks.delete(key);
    state.artifacts.set(key, data); paintArtifacts();
  }
  function artifactEndpoint(item, operation) {
    const url = artifactUrl(item.url, win.location.origin, state.workspace?.id);
    if (!url || !/^[A-Za-z0-9_-]+$/.test(item.id || "")) return "";
    const parsed = new URL(url);
    if (parsed.pathname !== `/api/agent/artifacts/${item.id}`) return "";
    return parsed.pathname + "/" + operation + "?" + new URLSearchParams({ workspace: state.workspace.id });
  }
  function readinessCurrent(scope, key, record) {
    return current(scope.epoch) && scope.workspaceEpoch === state.workspaceEpoch && scope.workspace === state.workspace?.id
      && scope.session === state.session && scope.turn === idOf(state.turn) && state.artifactChecks.get(key) === record
      && state.artifacts.has(key) && artifactVersion(state.artifacts.get(key)) === scope.version;
  }
  function validReadiness(result, item) {
    const report = result?.readiness;
    return object(result) && HASH.test(item.output_sha256 || "") && result.source_sha256 === item.output_sha256 && result.artifact_id === item.id
      && ["current", "changed", "unavailable", "not_recorded"].includes(result.original_source_status)
      && ["docx", "xlsx", "pdf"].includes(result.format) && object(report) && report.schema_version === 1
      && ["review_required", "blocked"].includes(report.status) && report.automatic_acceptance === false
      && Array.isArray(report.checks) && report.checks.length <= 100
      && report.checks.every((check) => object(check) && typeof check.id === "string" && Object.hasOwn(CHECK_STATUS, check.status))
      && report.checks.some((check) => check.id === "source_hash" && check.status === "pass")
      && report.checks.some((check) => check.id === "structure" && check.status === "pass")
      && object(report.format_details) && object(report.preview);
  }
  async function inspectArtifact(key) {
    const item = state.artifacts.get(key), old = state.artifactChecks.get(key);
    if (!item || old?.loading || state.opening || state.disposed) return;
    const url = artifactEndpoint(item, "readiness"); if (!url) return;
    const record = { loading: true, result: null, error: null };
    const scope = { epoch: state.epoch, workspaceEpoch: state.workspaceEpoch, workspace: state.workspace.id,
      session: state.session, turn: idOf(state.turn), version: artifactVersion(item) };
    state.artifactChecks.set(key, record); paintArtifacts();
    try {
      const result = await request(url, post({}));
      if (!readinessCurrent(scope, key, record)) return;
      if (!validReadiness(result, item)) record.error = { kind: "contract" };
      else record.result = result;
    } catch (error) {
      if (readinessCurrent(scope, key, record)) record.error = { kind: "request", message: error.message };
    } finally {
      if (readinessCurrent(scope, key, record)) { record.loading = false; paintArtifacts(); }
    }
  }
  function readinessHint(check) {
    switch (check.id) {
      case "active_content": return check.status === "blocked" ? t("发现宏、脚本、嵌入对象或活动内容，需另行核对。") : t("未发现活动内容标记；未执行宏或脚本。");
      case "external_links": return check.status === "pass" ? t("未发现外部关系。") : t("存在外部链接，本次检查未打开这些链接。");
      case "document_fields": return t("存在修订记录或域，请核对最终显示内容。");
      case "external_data": return t("存在外部数据公式或连接，未打开或刷新其来源。");
      case "formula_cache": return check.status === "blocked" ? t("公式缓存缺失或无效，请用电子表格应用重算并另存副本。")
        : check.status === "not_applicable" ? t("未发现单元格公式。") : t("已保存的公式缓存可能过期，不能作为已重算的证明。");
      case "spreadsheet_errors": return check.status === "blocked" ? t("存在错误单元格，请处理后再交付。") : t("未发现标记为错误类型的单元格。");
      case "engineering_review": return t("请由负责人员核对来源、数字和结论；本次检查不代表人工验收。");
      default: return "";
    }
  }
  function paintReadiness(card, item, record) {
    const button = node("button", record?.loading ? t("正在检查交付…") : record?.error ? t("重试交付检查") : record?.result ? t("重新检查交付") : t("交付检查"));
    button.type = "button"; button.dataset.action = "inspect-readiness";
    button.disabled = !!record?.loading || !artifactEndpoint(item, "readiness");
    button.addEventListener("click", () => inspectArtifact(artifactKey(item))); card.appendChild(button);
    const panel = node("div", undefined, "artifact-readiness small wrap");
    panel.setAttribute("aria-live", "polite"); panel.setAttribute("aria-busy", String(!!record?.loading));
    if (record?.loading) panel.appendChild(node("p", t("正在核对当前副本及其来源…"), "muted"));
    if (record?.error) panel.appendChild(node("p", record.error.kind === "contract"
      ? t("检查响应与当前副本不匹配，未采用该结果。请重新检查。")
      : t("交付检查失败：") + record.error.message, "notice error"));
    if (!record?.result) { card.appendChild(panel); return; }
    const result = record.result, report = result.readiness, detail = report.format_details;
    const sourceStatus = result.original_source_status;
    panel.appendChild(node("p", sourceStatus === "changed" ? t("原始来源已变化，请从新版资料重新生成副本。")
      : report.status === "blocked" ? t("发现需处理项，暂不交付。") : t("结构检查已完成，待人工核对。"), "notice" + (report.status === "blocked" || sourceStatus === "changed" ? " warning" : "")));
    panel.appendChild(node("p", t("已检查副本 SHA-256：{hash}", { hash: result.source_sha256 }), "wrap"));
    panel.appendChild(node("p", sourceStatus === "current" ? t("原始来源：当前文件与生成时一致。")
      : sourceStatus === "changed" ? t("原始来源：生成后已变化，请从新版资料重新生成副本。")
      : sourceStatus === "not_recorded" ? t("原始来源：未记录生成时的版本，无法核对。")
      : t("原始来源：当前无法核对。"), sourceStatus === "current" ? "muted" : "notice warning"));
    if (typeof item.source === "string") panel.appendChild(node("p", item.source, "muted wrap"));
    if (typeof item.source_sha256 === "string") panel.appendChild(node("p", t("生成时来源 SHA-256：{hash}", { hash: item.source_sha256 }), "muted wrap"));
    panel.appendChild(node("p", t("版式尚未逐页核对。请在对应应用中检查分页、表格和显示内容。"), "muted"));
    if (report.preview.rendered !== true) panel.appendChild(node("p", t("尚未运行文档渲染器；预览入口不代表已完成版式检查。"), "muted"));
    if (result.format === "xlsx") {
      const count = (key) => Number.isSafeInteger(detail[key]) && detail[key] >= 0 ? detail[key] : t("未返回");
      panel.appendChild(node("p", t("公式 {total}；有缓存 {cached}；缺失缓存 {missing}；无效缓存 {invalid}；错误单元格 {errors}。", {
        total: count("formula_count"), cached: count("formula_cached_count"), missing: count("formula_missing_cache_count"),
        invalid: count("formula_invalid_cache_count"), errors: count("error_cell_count") })));
      panel.appendChild(node("p", detail.formula_count === 0 ? t("未发现单元格公式，无需公式重算。") : t("公式未实际重算；已有缓存仍未验证，自动计算标记也不是重算证明。"), "muted"));
    }
    const list = node("ul");
    for (const check of report.checks) {
      const entry = node("li"); entry.dataset.check = check.id;
      entry.appendChild(node("strong", (Object.hasOwn(READINESS_LABELS, check.id) ? t(READINESS_LABELS[check.id]) : check.id) + " · " + t(CHECK_STATUS[check.status])));
      const hint = readinessHint(check); if (hint) entry.appendChild(node("p", hint, "muted")); list.appendChild(entry);
    }
    panel.appendChild(list);
    if (result.format === "pdf" && report.preview.kind === "native_pdf" && report.preview.eligible === true
      && report.checks.some((check) => check.id === "active_content" && check.status === "pass")) {
      const url = artifactEndpoint(item, "preview");
      if (url) { const link = node("a", t("在新窗口核对 PDF")); link.href = url; link.target = "_blank"; link.rel = "noopener noreferrer"; link.dataset.action = "preview-pdf"; panel.appendChild(link); }
    }
    const raw = node("details"); raw.append(node("summary", t("完整交付检查记录")), node("pre", printable(result))); panel.appendChild(raw);
    card.appendChild(panel);
  }
  function paintArtifacts() {
    const host = $("agentArtifacts"); host.replaceChildren();
    for (const [key, item] of state.artifacts) {
      const card = node("article", undefined, "artifact"); card.dataset.artifactId = item.id || key;
      const url = artifactUrl(item.url, win.location.origin, state.workspace.id);
      const title = node(url ? "a" : "strong", item.name || t("文档产物")); if (url) { title.href = url; title.download = ""; }
      card.appendChild(title);
      card.appendChild(node("p", item.provenance === "model_proposed" ? t("模型提出修改，由文档工具应用") : item.provenance === "deterministic" ? t("确定性工具生成") : t("来源由服务端提供"), "muted"));
      if (!url) card.appendChild(node("p", t("当前没有可用的工作区下载链接。"), "muted"));
      const details = node("details"), summary = node("summary", t("来源与验证记录"));
      details.append(summary, node("pre", printable({ source: item.source, source_sha256: item.source_sha256, output_sha256: item.output_sha256, validation: item.validation })));
      card.appendChild(details); paintReadiness(card, item, state.artifactChecks.get(key)); host.appendChild(card);
    }
  }
  const engineeringNumber = (value) => typeof value === "number" && Number.isFinite(value)
    ? value.toLocaleString(win.CBI18n?.locale === "en" ? "en-GB" : "zh-CN", { maximumSignificantDigits: 12, useGrouping: false }) : t("未返回");
  function engineeringMetric(host, label, value) {
    const item = node("div"); item.append(node("dt", label), node("dd", value)); host.appendChild(item);
  }
  function engineeringRange(value) {
    return object(value) ? t("最小 {min} · 最大 {max}", { min: engineeringNumber(value.min), max: engineeringNumber(value.max) }) : t("工具未返回采样极值");
  }
  function paintEngineeringResult(value) {
    const data = value?.result, source = value?.provenance;
    if (value?.ok !== true || !object(data) || !object(source) || !["cad_section", "saved_frame"].includes(source.kind)) return;
    const section = source.kind === "cad_section" && data.kind === "section";
    const frame = source.kind === "saved_frame" && (data.kind === "frame" || data.analysis === "linear_elastic_frame");
    if (!section && !frame) return;
    const key = String(value.call_id || [source.kind, source.project_id, source.revision].join(":"));
    state.engineeringResults.set(key, value);
    let host = $("agentEngineeringResults");
    if (!host) { host = node("div", undefined, "engineering-results"); host.id = "agentEngineeringResults"; $("agentArtifacts").before(host); }
    host.hidden = false; host.replaceChildren();
    for (const [id, item] of state.engineeringResults) {
      const result = item.result, provenance = item.provenance, isSection = provenance.kind === "cad_section";
      const card = node("article", undefined, "engineering-result"); card.dataset.callId = id;
      card.appendChild(node("h3", isSection ? t("截面几何计算") : t("梁框架线弹性分析")));
      card.appendChild(node("p", t("来源项目 {project} · 修订 {revision}", { project: provenance.project_id || t("未返回"), revision: provenance.revision ?? t("未返回") }), "muted small wrap"));
      if (isSection) {
        card.appendChild(node("p", t("原图单位：{unit}；面积 mm²，惯性矩 mm⁴。", { unit: result.unit || t("未返回") }), "muted small"));
        for (const [index, region] of (Array.isArray(result.regions) ? result.regions : []).entries()) {
          if (!object(region)) continue;
          card.appendChild(node("h4", t("区域 {number}{layer}", { number: index + 1, layer: typeof region.layer === "string" ? " · " + region.layer : "" })));
          const metrics = node("dl", undefined, "engineering-metrics");
          engineeringMetric(metrics, t("面积（mm²）"), engineeringNumber(region.area_mm2));
          const center = Array.isArray(region.centroid_source) && region.centroid_source.length === 2 ? region.centroid_source.map(engineeringNumber).join(", ") : t("未返回");
          engineeringMetric(metrics, t("形心（原图 x, y；{unit}）", { unit: result.unit || t("单位未返回") }), center);
          engineeringMetric(metrics, "Ixx（mm⁴）", engineeringNumber(region.Ixx_mm4));
          engineeringMetric(metrics, "Iyy（mm⁴）", engineeringNumber(region.Iyy_mm4));
          card.appendChild(metrics);
        }
        card.appendChild(node("p", t("惯性矩关于区域形心轴；此结果未提供强度或规范合格结论。"), "muted small"));
      } else {
        const units = object(result.units) ? result.units : {};
        card.appendChild(node("p", t("单位：长度 {length} · 力 {force} · 弯矩 {moment}", { length: units.length || t("未返回"), force: units.force || t("未返回"), moment: units.moment || t("未返回") }), "muted small"));
        for (const combination of Array.isArray(result.combinations) ? result.combinations : []) {
          if (!object(combination)) continue;
          card.appendChild(node("h4", t("荷载组合 {id}", { id: combination.id ?? t("未返回") })));
          const reactions = node("dl", undefined, "engineering-metrics");
          for (const point of Array.isArray(combination.nodes) ? combination.nodes : []) {
            if (!object(point)) continue;
            const reaction = Array.isArray(point.reaction_N) ? point.reaction_N : [];
            engineeringMetric(reactions, t("节点 {id} 反力（{unit}）", { id: point.id ?? t("未返回"), unit: units.force || t("单位未返回") }), ["FX", "FY", "FZ"].map((axis, index) => `${axis} ${engineeringNumber(reaction[index])}`).join(" · "));
          }
          card.appendChild(reactions);
          for (const member of Array.isArray(combination.members) ? combination.members : []) {
            if (!object(member)) continue;
            card.appendChild(node("h4", t("杆件 {id} · 采样极值", { id: member.id ?? t("未返回") })));
            const extrema = object(member.sampled_extrema) ? member.sampled_extrema : {}, metrics = node("dl", undefined, "engineering-metrics");
            for (const [label, field, unit] of [[t("弯矩 My"), "moment_y_Nm", units.moment], [t("弯矩 Mz"), "moment_z_Nm", units.moment], [t("挠度 dy"), "dy_m", units.length], [t("挠度 dz"), "dz_m", units.length]]) engineeringMetric(metrics, `${label}（${unit || t("单位未返回")}）`, engineeringRange(extrema[field]));
            card.appendChild(metrics);
          }
        }
        card.appendChild(node("p", t("节点反力采用全局坐标；杆件弯矩和挠度采用局部坐标。最小/最大为工具返回的有符号采样极值，并非连续包络或规范判定。"), "muted small"));
      }
      card.appendChild(node("p", t("摘要保留至 12 位有效数字；完整精度与来源记录见下方执行事件。"), "muted small"));
      host.appendChild(card);
    }
  }
  function sourceLocation(locator) {
    const positive = (value) => Number.isSafeInteger(value) && value > 0;
    if (locator.kind === "pdf_page" && positive(locator.page)) return t("PDF 第 {page} 页", { page: locator.page });
    if (locator.kind === "docx_paragraph" && typeof locator.paragraph_id === "string" && locator.paragraph_id) return t("Word 段落 {id}", { id: locator.paragraph_id });
    if (typeof locator.sheet === "string" && locator.sheet) {
      if (locator.kind === "xlsx_cell" && /^[A-Z]{1,3}[1-9][0-9]*$/.test(locator.cell || "")) return `Excel ${locator.sheet}!${locator.cell}`;
      if (locator.kind === "xlsx_row" && positive(locator.row)) return t("Excel {sheet} 第 {row} 行", { sheet: locator.sheet, row: locator.row });
    }
    if (locator.kind === "text" && positive(locator.line_start) && positive(locator.line_end) && locator.line_end >= locator.line_start) return locator.line_start === locator.line_end
      ? t("文本第 {line} 行", { line: locator.line_start }) : t("文本第 {start}–{end} 行", { start: locator.line_start, end: locator.line_end });
    return JSON.stringify(locator);
  }
  function paintPackingResult(value) {
    const result = value?.result, source = value?.provenance;
    if (value?.ok !== true || result?.schema !== "packing_replan.result.v1" || result.kind !== "packing_replan"
      || !object(source) || typeof source.source !== "string" || !HASH.test(source.source_sha256 || "")
      || result.source?.path !== source.source || result.source?.sha256 !== source.source_sha256
      || source.shipping_release !== false || source.professional_signoff !== false || source.originals_unchanged !== true) return;
    state.packingResults.set(source.source + ":" + source.source_sha256, value);
    let host = $("agentPackingResults");
    if (!host) { host = node("section", undefined, "engineering-results"); host.id = "agentPackingResults"; $("agentArtifacts").before(host); }
    host.hidden = false; host.replaceChildren();
    const booleanLabel = (answer) => answer === true ? t("是") : answer === false ? t("否") : t("未返回");
    for (const item of state.packingResults.values()) {
      const report = item.result, provenance = item.provenance, card = node("article", undefined, "engineering-result");
      card.append(node("h3", t("箱单重排核对")), node("p", provenance.source, "wrap"));
      const outcome = { improved: "复算找到更优候选", unchanged: "复算未找到更优候选，保留基线", needs_human: "输入需人工补充，未执行重排" };
      card.appendChild(node("p", t(outcome[report.outcome] || "复算状态待核对"), report.status === "needs_human" ? "notice warning" : "notice"));
      if (report.status === "completed") {
        for (const [label, summary] of [["原始基线", report.baseline], ["保留结果", report.final]]) {
          if (!object(summary)) continue;
          card.appendChild(node("h4", t(label)));
          const metrics = node("dl", undefined, "engineering-metrics");
          engineeringMetric(metrics, t("计算能否装下"), booleanLabel(summary.can_fit));
          engineeringMetric(metrics, t("布局校验通过"), booleanLabel(summary.layout_verified));
          engineeringMetric(metrics, t("使用柜数"), engineeringNumber(summary.containers_used));
          engineeringMetric(metrics, t("包装箱数"), engineeringNumber(summary.n_boxes));
          card.appendChild(metrics);
        }
        card.appendChild(node("p", t("实际复算 {count} 轮", { count: Array.isArray(report.rounds) ? report.rounds.length : 0 }), "muted small"));
        const structure = report.final?.structure;
        if (object(structure)) {
          card.appendChild(node("h4", t("包装结构检查（不包含在能否装下中）")));
          const checks = node("dl", undefined, "engineering-metrics");
          for (const [label, field] of [["不通过", "fail"], ["需加强", "needs_reinforcement"], ["待详设", "pending_design"], ["通过", "pass"]]) engineeringMetric(checks, t(label), engineeringNumber(structure[field]));
          card.appendChild(checks);
          if (structure.fail > 0 || structure.needs_reinforcement > 0 || structure.pending_design > 0) card.appendChild(node("p", t("包装结构仍有未解决项，须补充详设或加固后重新核对。"), "notice warning"));
        } else card.appendChild(node("p", t("包装结构检查未返回，不能据此判断结构安全。"), "notice warning"));
      }
      if (Array.isArray(report.needs_human) && report.needs_human.length) {
        const list = node("ul");
        const reasons = { missing_weight: "缺少有效重量，请补充正数重量并核对单位。", missing_dimensions: "缺少有效长宽高，请补充毫米尺寸。",
          invalid_quantity: "数量需为实际正整数件数。", oversize_for_container: "源材料尺寸超出所选柜型，请人工复核。",
          packaging_not_cargo: "该行可能是包装器具，需区分器具和实际货物。", boxing_not_conserved: "成箱结果未通过数量或重量守恒核对。",
          unsupported_transport_requirements: "自动成箱无法执行这行运输要求。请保留原文，转到物流台账的已包装箱件模式，补充整体外尺寸、包装毛重、数量及所需承载资料。" };
        for (const entry of report.needs_human.slice(0, 50)) list.appendChild(node("li", typeof entry === "string" ? entry
          : [entry.id, entry.name, reasons[entry.reason] ? t(reasons[entry.reason]) : entry.ask || entry.reason || entry.code].filter(Boolean).join(" · ")));
        card.appendChild(list);
        if (report.needs_human.length > 50) card.appendChild(node("p", t("共 {count} 项需核对，完整原因见执行记录。", { count: report.needs_human.length }), "muted small"));
      }
      card.appendChild(node("p", t("仅核对所列箱单与计算约束；不代表装运放行或专业签认。"), "notice warning"));
      if (item.decision_proposal?.phase === "packing_replan_shadow_v1") card.appendChild(node("p", t(item.decision_proposal.mode === "off" ? "Jev 已关闭，使用确定性结果。" : "Jev 建议仅作记录，不改变本次计算结果。"), "muted small"));
      const details = node("details"); details.append(node("summary", t("来源与验证记录")), node("pre", printable({ provenance, hard_constraints: report.hard_constraints, needs_human: report.needs_human, rounds: report.rounds, decision: item.decision_proposal })));
      card.appendChild(details); host.appendChild(card);
    }
  }
  function paintSourceEvidence(receipt) {
    const host = $("agentSourceEvidence"); host.replaceChildren(); host.hidden = !receipt;
    state.sourceEvidence = receipt;
    if (!receipt) return;
    const valid = object(receipt) && receipt.schema_version === 1 && receipt.origin === "host"
      && receipt.scope === "this_turn_source_quotes" && receipt.model_claims_verified === false
      && receipt.engineering_truth === "not_verified" && Array.isArray(receipt.references) && receipt.references.length <= 12;
    if (!valid) { host.appendChild(node("p", t("来源回执格式无法核对，不能作为已验证证据。"), "notice warning")); return; }
    if (!receipt.attempted && !receipt.references.length) { host.hidden = true; return; }
    host.appendChild(node("h3", t("本轮来源引文")));
    host.appendChild(node("p", t("仅核对引文、定位和当时文件哈希；不代表模型所有结论已验证，也不代表工程签认。"), "notice"));
    if (receipt.checked_at) host.appendChild(node("p", t("核对时间：{time}", { time: receipt.checked_at }), "muted small"));
    if (receipt.truncated) host.appendChild(node("p", t("来源过多或内容超过回执限制；这里只列出部分引文，完整记录见执行过程。"), "notice warning"));
    if (receipt.collection_failed) host.appendChild(node("p", t("本轮部分检索或引用请求失败，请查看执行过程。"), "notice warning"));
    if (!receipt.references.length) host.appendChild(node("p", t("本轮没有可展示的来源引文，回答未获得引文一致性证明。"), "notice warning"));
    const labels = { valid: "引文与核对时的文件一致", changed: "来源已变化，请按新版重新核对", invalid: "引文未通过核验", unavailable: "来源暂时无法核对", unverified: "引文尚未完成核验" };
    const reasons = { source_not_allowed: "该来源未被本轮选择", version_mismatch: "文件内容与检索时的哈希不同", source_changed_during_verification: "来源在核对过程中发生变化", quote_mismatch: "引文与原文不一致", locator_mismatch: "原文位置不一致", chunk_mismatch: "引文块标识不一致", invalid_reference: "引用格式无效", invalid_locator_or_quote: "引用位置或引文无效", invalid_verification_response: "校验服务未返回可核对的结果", verification_timeout: "来源核对超时，请重新检查", verification_unavailable: "来源或校验服务不可用", cancelled: "来源核对已取消" };
    for (const reference of receipt.references) {
      const card = node("article", undefined, "artifact");
      const wellFormed = object(reference) && typeof reference.source === "string" && reference.source.length <= 1024
        && typeof reference.quote === "string" && [...reference.quote].length <= 1200 && object(reference.locator)
        && JSON.stringify(reference.locator).length <= 2048 && /^[a-f0-9]{64}$/i.test(reference.source_sha256 || "") && labels[reference.status]
        && (reference.status !== "valid" || reference.reason === "exact_quote_verified");
      if (!wellFormed) { card.appendChild(node("p", t("来源回执格式无法核对，不能作为已验证证据。"), "notice warning")); host.appendChild(card); continue; }
      card.appendChild(node("strong", reference.source));
      card.appendChild(node("p", t(labels[reference.status]), reference.status === "valid" ? "muted" : "notice warning"));
      if (reasons[reference.reason]) card.appendChild(node("p", t(reasons[reference.reason]), "small"));
      card.appendChild(node("p", t("原始位置：{location}", { location: sourceLocation(reference.locator) }), "small wrap"));
      card.appendChild(node("p", t(reference.status === "valid" ? "已核对引文" : "待核对引文"), "small"));
      card.appendChild(node("blockquote", reference.quote, "wrap"));
      card.appendChild(node("p", t("检索时 SHA-256：{hash}", { hash: reference.source_sha256 }), "small wrap"));
      if (/^[a-f0-9]{64}$/i.test(reference.current_source_sha256 || "")) card.appendChild(node("p", t("核对时 SHA-256：{hash}", { hash: reference.current_source_sha256 }), "small wrap"));
      host.appendChild(card);
    }
  }
  function paintResult(result) {
    if (!object(result)) return;
    if (typeof result.reply === "string") renderReply($("agentReply"), result.reply);
    if (Object.prototype.hasOwnProperty.call(result, "source_evidence")) paintSourceEvidence(result.source_evidence);
    $("agentPartial").hidden = result.partial !== true;
    for (const item of Array.isArray(result.artifacts) ? result.artifacts : []) paintArtifact(item);
    for (const item of Array.isArray(result.findings) ? result.findings : []) { paintEngineeringResult(item); paintPackingResult(item); }
    if (object(result.usage)) { $("agentUsageText").textContent = printable(result.usage); $("agentUsage").hidden = false; }
  }
  function paintTurn(turn) {
    if (!object(turn)) return;
    state.turn = { ...state.turn, ...turn };
    $("agentTurnStatus").textContent = t(STATUS[state.turn.status]) || state.turn.status || t("状态待读取");
    $("agentTurnActor").textContent = t("任务发起者：") + (state.turn.actor_id || t("历史记录未标记身份"));
    paintResult(state.turn.result); controls();
  }
  function renderReply(host, text) {
    renderAgentMarkdown(host, text, { lexer: typeof win.marked?.lexer === "function" ? win.marked.lexer.bind(win.marked) : null, t });
  }
  function subtaskEvent(li, kind, data) {
    // RuntimeEvent.task_id identifies the parent lease; the child ID belongs
    // to event data. It is a task label, never an actor/authorization identity.
    const id = typeof data.task_id === "string" && ID.test(data.task_id) ? data.task_id : "";
    const lifecycle = kind === "subtask_started" || kind === "subtask_finished";
    if (!id || (!lifecycle && !state.subtasks.has(id))) return;
    const previous = state.subtasks.get(id) || {};
    const role = typeof data.role === "string" ? data.role : previous.role || "";
    const status = kind === "subtask_started" ? "running"
      : kind === "subtask_finished" ? (TERMINAL.has(data.status) ? data.status : "unknown") : previous.status;
    state.subtasks.set(id, { role, status });
    const meta = node("div", undefined, "subtask-meta");
    meta.appendChild(node("strong", Object.hasOwn(CHILD_ROLES, role) ? t(CHILD_ROLES[role]) : role || t("子任务")));
    const identifier = node("span", t("子任务 {id}", { id: id.slice(0, 8) })); identifier.title = id; meta.appendChild(identifier);
    if (lifecycle) meta.appendChild(node("span", Object.hasOwn(STATUS, status) ? t(STATUS[status]) : t("状态待读取"), "status-tag"));
    li.appendChild(meta);
    if (kind === "subtask_started" && typeof data.goal === "string") li.appendChild(node("p", data.goal));
    if (kind === "subtask_finished" && typeof data.error === "string") li.appendChild(node("p", data.error, "notice error"));
    if (kind === "subtask_finished" && typeof data.findings === "string") {
      const details = node("details", undefined, "subtask-findings"), reply = node("div", undefined, "reply");
      details.appendChild(node("summary", t("查看子代理发现（模型自述）")));
      renderReply(reply, data.findings); details.appendChild(reply); li.appendChild(details);
    }
  }
  function eventFrame(event) {
    if (!event || !Number.isSafeInteger(event.seq) || event.seq <= state.seq) return;
    state.seq = event.seq; state.eventRows.push(event);
    const data = object(event.data) ? event.data : {}, kind = String(event.kind || "status");
    if (kind === "authorization" && data.document_write_allowed === false
      && ["post_selection_required", "current_turn_confirmation_required"].includes(data.reason)) {
      state.writeGateReason = data.reason; paintWriteGate();
    }
    if (kind === "context") paintContext(data);
    if (kind === "source_evidence") paintSourceEvidence(data);
    if (kind === "artifact") paintArtifact(data);
    if (kind === "tool_finished" && (data.name === "engineering_analyze" || data.tool === "engineering_analyze")) paintEngineeringResult(data.result);
    if (kind === "tool_finished" && (data.name === "packing_replan" || data.tool === "packing_replan")) paintPackingResult(data.result);
    if (kind === "done") paintResult(data.result || data);
    if (TURN_EVENTS[kind]) paintTurn({ status: TURN_EVENTS[kind], ...(data.result ? { result: data.result } : {}) });
    const li = node("li"), title = node("div", undefined, "event-title");
    title.append(node("strong", kind === "authorization" ? t("本轮权限记录") : t(EVENT_NAMES[kind]) || t(STATUS[TURN_EVENTS[kind]]) || kind), node("span", `#${event.seq}` + (event.actor_id ? t(" · 发起者：{actor}", { actor: event.actor_id }) : ""))); li.appendChild(title);
    subtaskEvent(li, kind, data);
    const text = data.message || data.text || data.summary || data.tool || data.name || data.phase || data.model;
    if (typeof text === "string") li.appendChild(node("p", text));
    if (Object.keys(data).length) { const details = node("details"); details.append(node("summary", t("查看事件数据")), node("pre", printable(data))); li.appendChild(details); }
    $("agentEvents").appendChild(li);
  }
  async function poll() {
    if (!state.workspace || !state.turn || state.disposed) return;
    const epoch = state.epoch, id = idOf(state.turn), query = scoped();
    controller = new AbortController(); let delay = 900, retry = false, draining = false;
    try {
      const data = await request(`/api/agent/turns/${encodeURIComponent(id)}/events?${query}&after_seq=${state.seq}`, { signal: controller.signal });
      if (!current(epoch) || id !== idOf(state.turn)) return;
      const previousSeq = state.seq;
      for (const event of (Array.isArray(data.events) ? data.events : []).slice().sort((a, b) => a.seq - b.seq)) eventFrame(event);
      paintTurn(data.turn);
      draining = Number.isSafeInteger(state.turn.last_seq) && state.turn.last_seq > state.seq;
      if (draining) {
        state.emptyEventPages = state.seq > previousSeq ? 0 : state.emptyEventPages + 1;
        if (state.emptyEventPages >= 3) {
          draining = false; notice(t("执行记录尚未完整恢复，请重新打开该任务重试。"), true);
        } else { delay = state.emptyEventPages ? 2500 : 0; notice(t("正在恢复剩余执行记录…")); }
      } else {
        state.emptyEventPages = 0;
        if (TERMINAL.has(state.turn.status)) { terminalNotice(); await loadTurns({ keepHistory: true }); }
      }
    } catch (error) {
      if (!current(epoch) || error.name === "AbortError") return;
      retry = true; delay = 2500; notice(t("连接暂时中断，将从已收到的事件继续恢复：") + error.message, true);
    } finally {
      if (current(epoch) && id === idOf(state.turn)) { controller = null; if (active() || retry || draining) timer = later(poll, delay); }
    }
  }
  async function showTurn(id) {
    if (!state.workspace || !id) return;
    resetView({ keepHistory: true }); const epoch = state.epoch;
    state.turn = { turn_id: id, status: "queued" }; paintTurns();
    try {
      const data = await request(`/api/agent/turns/${encodeURIComponent(id)}?${scoped()}`);
      if (!current(epoch)) return;
      paintTurn(data.turn || data); sessionRecord().turn = id; persist(); await poll();
    } catch (error) { if (current(epoch)) { state.turn = null; controls(); notice(t("执行记录恢复失败：") + error.message, true); } }
  }
  async function send() {
    if ($("agentSend").disabled || !$("agentMessage").value.trim()) return;
    cancelVoice();
    const packingSource = $("agentPackingSource").value;
    const messageDraft = $("agentMessage").value, message = messageDraft.trim();
    // Submission failures keep the draft and its explicit source available for
    // retry. Workspace/session changes still reset both in their own handlers.
    resetView({ keepHistory: true, keepPackingSelection: true }); state.submitting = true; controls();
    const epoch = state.epoch;
    notice(t("正在提交任务…"));
    try {
      if (!(await verifyEngineering(epoch))) return;
      const payload = { workspace: state.workspace.id, session_id: state.session, message, locale: win.CBI18n?.locale || "zh-CN", mode: $("agentMode").value, sandbox: $("agentSandbox").value, files: [...state.selected],
        ...(state.capabilities?.features?.packing_replan === true ? { packing_sources: packingSource && state.selected.has(packingSource) ? [{ source: packingSource }] : [] } : {}),
        expert_id: $("agentExpert").value, risk_confirmation: $("agentRiskConfirmation").value.trim(), engineering: state.engineeringRows.filter((row) => row.selected).map((row) => ({ ...row.snapshot, confirmed_solid: row.kind === "cad_section" && row.confirmed })) };
      const data = await request("/api/agent/turns", post(payload));
      if (!current(epoch)) return;
      if (!data.turn_id || data.session_id !== state.session) throw new Error(t("任务响应缺少本会话的执行标识"));
      sessionRecord().turn = data.turn_id; persist(); state.turn = { turn_id: data.turn_id, status: "queued" };
      // Consume only the accepted draft; a user can already be writing the next.
      if ($("agentMessage").value === messageDraft) $("agentMessage").value = "";
      if (payload.packing_sources?.some(selection => selection.source === $("agentPackingSource").value)) $("agentPackingSource").value = "";
      $("agentRiskConfirmation").value = ""; notice(t("任务已提交，正在接收执行事件。")); await poll();
    } catch (error) { if (current(epoch)) notice(t("任务提交失败：") + error.message, true); }
    finally { if (current(epoch)) { state.submitting = false; controls(); } }
  }
  async function cancel() {
    if (!active() || state.cancelling || $("agentCancel").hidden) return;
    const epoch = state.epoch, id = idOf(state.turn), query = scoped(); state.cancelling = true; controls();
    try {
      await request(`/api/agent/turns/${encodeURIComponent(id)}/cancel?${query}`, post({}));
      if (current(epoch) && id === idOf(state.turn) && active()) { paintTurn({ status: "cancelling" }); notice(t("已请求停止，等待服务端确认并保留已保存的结果。")); }
    } catch (error) { if (current(epoch)) notice(t("停止请求失败，可重试：") + error.message, true); }
    finally { if (current(epoch)) { state.cancelling = false; controls(); } }
  }
  async function loadModel() {
    const cfg = await request("/api/llm-config"); state.modelConfig = cfg;
    if (typeof cfg.configured !== "boolean") throw new Error(t("模型配置响应不完整"));
    state.modelConfigured = cfg.configured; $("agentModelBase").value = cfg.base_url || ""; $("agentModelName").value = cfg.model || ""; $("agentModelKey").value = "";
    $("agentModelLabel").textContent = cfg.configured ? cfg.model || t("已配置") : t("未配置，可先检查资料");
    $("agentModelStatus").textContent = cfg.configured ? t("Key {key}；来源：{source}。", { key: cfg.key_masked || t("已设置"), source: cfg.source === "runtime" ? t("本次运行") : t("启动配置") }) : t("尚未配置模型；检查资料无需 Key。");
    controls();
  }
  async function saveModel() {
    if (state.modelBusy) return;
    const key = $("agentModelKey").value.trim();
    const payload = { base_url: $("agentModelBase").value.trim(), model: $("agentModelName").value.trim(), api_key: key };
    state.modelBusy = true; $("agentModelKey").value = ""; controls(); let submitted = false;
    try { await request("/api/llm-config", post(payload)); submitted = true; await loadCapabilities(); await loadModel(); $("agentModelStatus").textContent += t(" 已保存并核对。"); }
    catch (error) { const message = key ? String(error.message).split(key).join(t("[已隐藏]")) : error.message; $("agentModelStatus").textContent = (submitted ? t("已提交，但当前配置未能核对：") : t("保存失败：")) + message; }
    finally { state.modelBusy = false; controls(); }
  }
  function languageChanged() {
    if (state.disposed) return;
    const displayedTurn = state.turn ? { ...state.turn } : null;
    // UI state is repainted; user text, raw events, file names and saved outputs stay unchanged.
    if (state.capabilities) paintCapabilities(state.capabilities);
    paintSessions(); paintFiles(); paintExperts(); expertNote(); paintEngineering(); paintWriteGate();
    if (state.engineeringStatus) { const { key, values, detail } = state.engineeringStatus; engineeringStatus(key, values, detail); }
    if (state.terminalNotice && state.turn && TERMINAL.has(state.turn.status)) terminalNotice();
    if (state.contextData) paintContext(state.contextData);
    else $("agentContextText").textContent = t("收到后端请求预算后显示；这不是任务完成进度。");
    if (state.sourceEvidence) paintSourceEvidence(state.sourceEvidence);
    if (state.turn) paintTurn(state.turn);
    else {
      $("agentReply").textContent = t("结果将显示在这里。");
      $("agentTurnStatus").textContent = t("尚未开始");
      $("agentTurnActor").textContent = t("尚无任务发起者记录。");
    }
    for (const item of state.artifacts.values()) { paintArtifact(item); break; }
    for (const item of state.engineeringResults.values()) { paintEngineeringResult(item); break; }
    for (const item of state.packingResults.values()) { paintPackingResult(item); break; }
    const events = state.eventRows.slice(); state.eventRows = []; state.seq = 0; state.subtasks.clear(); $("agentEvents").replaceChildren();
    for (const event of events) eventFrame(event);
    // A partially drained history can end with an old running event; keep the
    // latest server snapshot authoritative after repainting historical rows.
    if (displayedTurn) paintTurn(displayedTurn);
    paintTurns(); paintSessionHistory();
    if (state.modelConfig) {
      const cfg = state.modelConfig;
      $("agentModelLabel").textContent = cfg.configured ? cfg.model || t("已配置") : t("未配置，可先检查资料");
      $("agentModelStatus").textContent = cfg.configured ? t("Key {key}；来源：{source}。", { key: cfg.key_masked || t("已设置"), source: cfg.source === "runtime" ? t("本次运行") : t("启动配置") }) : t("尚未配置模型；检查资料无需 Key。");
    }
  }
  async function start() {
    if (started) return; started = true;
    const startupWorkspaceEpoch = state.workspaceEpoch;
    win.addEventListener("cb:languagechange", languageChanged);
    languageChanged();
    $("agentWorkspaceForm").addEventListener("submit", (event) => { event.preventDefault(); openWorkspace($("agentWorkspacePath").value, { manual: true }); });
    $("agentTaskForm").addEventListener("submit", (event) => { event.preventDefault(); send(); });
    $("agentModelForm").addEventListener("submit", (event) => { event.preventDefault(); saveModel(); });
    $("agentMode").addEventListener("change", controls); $("agentSandbox").addEventListener("change", controls);
    $("agentPackingSource").addEventListener("change", controls);
    $("agentExpert").addEventListener("change", () => { expertNote(); controls(); }); $("agentRefreshEngineering").addEventListener("click", loadEngineering);
    $("agentRiskConfirmation").addEventListener("input", controls); $("agentMessage").addEventListener("input", controls);
    $("agentCancel").addEventListener("click", cancel); $("agentRefreshFiles").addEventListener("click", loadFiles); $("agentRefreshTurns").addEventListener("click", () => loadTurns());
    $("agentMoreTurns").addEventListener("click", () => loadTurns({ more: true }));
    $("agentRefreshSessions").addEventListener("click", () => loadSessions());
    $("agentMoreSessions").addEventListener("click", () => loadSessions({ more: true }));
    $("agentNewSession").addEventListener("click", () => changeSession("", true)); $("agentSession").addEventListener("change", () => changeSession($("agentSession").value));
    await Promise.all([loadCapabilities(), loadExperts(), loadModel().catch((error) => { $("agentModelLabel").textContent = t("暂不可用"); $("agentModelStatus").textContent = t("模型设置暂不可用：") + error.message; })]);
    if (state.disposed || state.workspaceEpoch !== startupWorkspaceEpoch) return;
    const linked = new URLSearchParams(win.location.search).getAll("workspace");
    if (linked.length) {
      // URL input is only an opaque registered ID. Resolve its path through the
      // authorized server list; never accept a path, file selection or write
      // permission from the URL, and never fall back to another saved workspace.
      if (linked.length !== 1 || !/^[A-Za-z0-9_-]{1,128}$/.test(linked[0])) {
        notice(t("工程链接无效，请从项目页重新打开。"), true); return;
      }
      if (state.capabilities?.available !== true) {
        notice(t("当前服务不可用，无法打开链接指定的工程。"), true); return;
      }
      try {
        const data = await request("/api/agent/workspaces");
        if (state.disposed || state.workspaceEpoch !== startupWorkspaceEpoch) return;
        const matches = Array.isArray(data.workspaces) ? data.workspaces.filter(w => w?.id === linked[0] && typeof w.root === "string" && w.root.trim()) : [];
        if (matches.length !== 1) throw new Error(t("链接中的工程未登记或当前账号无权访问。请从项目页重新打开。"));
        await openWorkspace(matches[0].root, { expectedId: linked[0], restoreSelection: false, restoreTurn: false });
      } catch (error) {
        if (!state.disposed && state.workspaceEpoch === startupWorkspaceEpoch) notice(t("无法打开工程链接：") + error.message, true);
      }
      return;
    }
    if (saved.lastRoot && state.capabilities?.available === true) await openWorkspace(saved.lastRoot);
  }
  function dispose() { win.removeEventListener("cb:languagechange", languageChanged); cancelVoice(); state.disposed = true; state.epoch += 1; stopPolling(); }
  return { state, start, openWorkspace, loadFiles, loadTurns, loadSessions, loadEngineering, inspectEngineering, changeSession, showTurn, send, cancel, poll, saveModel, dispose };
}

if (typeof document !== "undefined" && document.getElementById("agentWorkbench")) {
  createAuth({ win: window, doc: document }).installFetchGuard();
  const app = createAgentWorkbench({ document, window });
  window.addEventListener("pagehide", () => app.dispose());
  app.start();
}
