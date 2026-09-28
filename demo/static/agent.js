import { createAuth } from "./modules/auth.js";

const STORE_KEY = "cb_agent_workspaces_v1";
const RISK_PHRASE = "我明白，将由持证人员签认";
const confirmedMessage = (text) => String(text || "").split(/[\n。;；]/).some((part) => part.trim() === RISK_PHRASE);
const TERMINAL = new Set(["completed", "succeeded", "failed", "cancelled", "interrupted"]);
const STATUS = { queued: "等待执行", running: "执行中", waiting_approval: "等待确认", cancelling: "正在停止", completed: "已完成", succeeded: "已完成", failed: "未完成", cancelled: "已停止", interrupted: "服务重启时中断" };
const EVENT_NAMES = { status: "执行阶段", context: "上下文预算", model: "模型调用", tool_started: "工具开始", tool_finished: "工具结束", subtask_started: "子任务开始", subtask_finished: "子任务结束", artifact: "产物已保存", decision: "工程判断", error: "执行错误", done: "本轮结束" };
const TURN_EVENTS = { "turn.started": "running", "turn.cancelling": "cancelling", "turn.completed": "completed", "turn.failed": "failed", "turn.cancelled": "cancelled", "turn.interrupted": "interrupted" };
const idOf = (turn) => String(turn && (turn.turn_id || turn.id) || "");
const object = (value) => value && typeof value === "object" && !Array.isArray(value);
const printable = (value) => typeof value === "string" ? value : JSON.stringify(value, null, 2) || "";
const displayPath = (value) => value.startsWith("\\\\?\\UNC\\") ? "\\\\" + value.slice(8) : value.startsWith("\\\\?\\") ? value.slice(4) : value;
const engineeringKey = (value) => value.kind + ":" + value.project_id;
const snapshotKey = (value) => JSON.stringify([value.kind, value.project_id, value.revision, value.source_sha256, value.inputs_sha256]);
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
    seq: 0, artifacts: new Map(), engineeringResults: new Map(), epoch: 0, workspaceEpoch: 0, fileEpoch: 0, listEpoch: 0,
    contextData: null, taskRows: [], eventRows: [], modelConfig: null,
    engineeringEpoch: 0, engineeringRows: [], engineeringLoading: false, experts: [],
    opening: false, submitting: false, cancelling: false, modelBusy: false, modelConfigured: false, disposed: false };
  let saved = { lastRoot: "", workspaces: {} }, timer = null, controller = null, started = false;
  try { const value = JSON.parse(storage.getItem(STORE_KEY) || "null"); if (object(value) && object(value.workspaces)) saved = value; } catch (_) { /* Storage is optional. */ }
  function persist() { try { storage.setItem(STORE_KEY, JSON.stringify(saved)); } catch (_) { /* Storage may be disabled. */ } }
  function record() { return state.workspace && saved.workspaces[state.workspace.id]; }
  function sessionRecord() { return record() && record().sessions.find((item) => item.id === state.session); }
  function notice(text, error = false) { $("agentNotice").textContent = text; $("agentNotice").className = "notice" + (error ? " error" : ""); }
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
  function resetView() {
    stopPolling(); state.epoch += 1; state.listEpoch += 1; state.turn = null; state.seq = 0; state.eventRows = []; state.contextData = null; state.artifacts.clear(); state.engineeringResults.clear();
    state.submitting = false; state.cancelling = false;
    $("agentEvents").replaceChildren(); $("agentArtifacts").replaceChildren(); $("agentReply").textContent = t("结果将显示在这里。");
    if ($("agentEngineeringResults")) { $("agentEngineeringResults").replaceChildren(); $("agentEngineeringResults").hidden = true; }
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
    const highRiskWrite = state.experts.find((item) => item.id === $("agentExpert").value)?.risk === "high" && $("agentSandbox").value === "workspace-write";
    const confirmationPresent = $("agentRiskConfirmation").value.trim() === RISK_PHRASE || confirmedMessage($("agentMessage").value);
    $("agentRiskWrap").hidden = !highRiskWrite;
    $("agentRiskConfirmation").disabled = state.submitting;
    $("agentSend").disabled = !state.workspace || state.opening || state.submitting || !!active() || state.engineeringLoading || state.engineeringRows.some((row) => row.loading) || !supported || (mode === "model" && !state.modelConfigured) || (highRiskWrite && !confirmationPresent);
    $("agentCancel").hidden = !active() || !(caps && caps.features && caps.features.cancel);
    $("agentCancel").disabled = state.cancelling || state.turn && state.turn.status === "cancelling";
    for (const id of ["agentRefreshFiles", "agentRefreshTurns", "agentNewSession", "agentSession"]) $(id).disabled = !state.workspace || state.opening;
    $("agentWorkspaceOpen").disabled = state.opening;
    $("agentModelSave").disabled = state.modelBusy;
    $("agentRefreshEngineering").disabled = !state.workspace || state.opening || state.submitting || state.engineeringLoading;
    $("agentExpert").disabled = state.submitting;
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
    for (const item of record()?.sessions || []) { const option = node("option", t("会话 ") + item.id.slice(0, 10)); option.value = item.id; select.appendChild(option); }
    select.value = state.session;
  }
  function paintFiles() {
    const host = $("agentFiles"); host.replaceChildren();
    if (!state.workspace) { host.appendChild(node("p", t("打开文件夹后显示可读资料。"), "muted")); return; }
    if (!state.files.length) { host.appendChild(node("p", t("这个文件夹暂未列出可读资料。"), "muted")); return; }
    for (const file of state.files) {
      const label = node("label", undefined, "file-row"), check = node("input"); check.type = "checkbox"; check.checked = state.selected.has(file.path);
      check.addEventListener("change", () => { check.checked ? state.selected.add(file.path) : state.selected.delete(file.path); record().files = [...state.selected]; persist(); });
      const info = node("span", file.name || file.path); info.appendChild(node("small", file.path + (Number.isFinite(file.size) ? t(" · {size} 字节", { size: file.size.toLocaleString() }) : "")));
      label.append(check, info); host.appendChild(label);
    }
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
    $("agentEngineeringStatus").textContent = t("工程选择已清空；请重新读取需要使用的项目快照。");
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
          if (check.checked && state.engineeringRows.filter((item) => item.selected).length >= 4) { check.checked = false; $("agentEngineeringStatus").textContent = t("最多选择 4 个工程项目。"); return; }
          row.selected = check.checked; $("agentEngineeringStatus").textContent = t("已选 {count} / 4 个项目。", { count: state.engineeringRows.filter((item) => item.selected).length }); controls();
        });
        label.append(check, node("span", t("加入本次任务"))); card.appendChild(label);
      }
      host.appendChild(card);
    }
  }
  async function loadEngineering() {
    if (!state.workspace) return;
    clearEngineering(); const version = state.engineeringEpoch; state.engineeringLoading = true; state.engineeringRows = []; controls();
    $("agentEngineeringStatus").textContent = t("正在读取已保存工程…");
    try {
      const data = await request(`/api/agent/engineering/projects?${scoped()}`);
      if (version !== state.engineeringEpoch || state.disposed) return;
      if (!Array.isArray(data.projects)) throw new Error(t("工程项目列表不完整"));
      state.engineeringRows = data.projects.filter((row) => row && ["cad_section", "saved_frame"].includes(row.kind) && /^[A-Za-z0-9_-]+$/.test(row.project_id || "")).map((row) => ({ ...row, name: String(row.name || row.project_id), selected: false, confirmed: false, loading: false, detail: null, snapshot: null }));
      $("agentEngineeringStatus").textContent = state.engineeringRows.length ? t("请先读取需要使用的项目快照；刷新会清除上次确认。") : t("暂无可用的已保存截面或框架。");
    } catch (error) { if (version === state.engineeringEpoch) $("agentEngineeringStatus").textContent = t("工程选集暂不可用：") + error.message; }
    finally { if (version === state.engineeringEpoch) { state.engineeringLoading = false; controls(); } }
  }
  async function fetchEngineering(row) {
    const detail = await request(`/api/agent/engineering/projects/${encodeURIComponent(row.kind)}/${encodeURIComponent(row.project_id)}?${scoped()}`);
    return { detail, snapshot: engineeringSnapshot(detail, row) };
  }
  async function inspectEngineering(row) {
    const version = state.engineeringEpoch;
    row.selected = false; row.confirmed = false; row.detail = null; row.snapshot = null; row.error = ""; row.loading = true; controls();
    $("agentEngineeringStatus").textContent = t("已清除此项目的旧选择，请核对当前快照。");
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
    if (!valid) { $("agentEngineeringStatus").textContent = t("部分工程选择已失效，请重新核对。"); notice(t("工程快照已变化或无法核对；任务尚未提交，请重新检查工程选集。"), true); controls(); }
    return valid;
  }
  function expertNote() {
    const expert = state.experts.find((item) => item.id === $("agentExpert").value);
    $("agentExpertNote").textContent = expert ? (t(expert.title) || t(expert.name)) + (expert.risk === "high" ? t("；该岗位涉及高风险工程内容，服务端会执行签认门禁。") : "") : t("由任务内容选择岗位；服务端决定可用工具和签认要求。");
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
  async function openWorkspace(path) {
    if (!String(path || "").trim() || state.opening) return;
    cancelVoice();
    clearEngineering();
    resetView(); const epoch = state.epoch, requestId = ++state.workspaceEpoch;
    state.opening = true; controls(); notice(t("正在打开工程文件夹…"));
    try {
      const data = await request("/api/agent/workspaces", post({ path: path.trim() }));
      if (!current(epoch) || requestId !== state.workspaceEpoch) return;
      if (!data.workspace || typeof data.workspace.id !== "string" || typeof data.workspace.root !== "string") throw new Error(t("工作区响应缺少标识或路径"));
      state.workspace = data.workspace;
      $("agentMessage").value = "";
      $("agentRiskConfirmation").value = "";
      if (data.capabilities) paintCapabilities(data.capabilities);
      let item = saved.workspaces[state.workspace.id];
      if (object(item) && Array.isArray(item.sessions)) item.sessions = item.sessions.filter((s) => s && typeof s.id === "string" && /^[A-Za-z0-9_-]{1,128}$/.test(s.id));
      if (!object(item) || !Array.isArray(item.sessions) || !item.sessions.length) item = { sessions: [{ id: newId(), turn: "" }], files: [] };
      saved.workspaces[state.workspace.id] = item;
      state.session = item.sessions.some((s) => s.id === item.current) ? item.current : item.sessions[0].id;
      item.current = state.session;
      state.selected = new Set(Array.isArray(item.files) ? item.files : []); saved.lastRoot = state.workspace.root; persist();
      $("agentWorkspacePath").value = displayPath(state.workspace.root); $("agentWorkspaceStatus").textContent = displayPath(state.workspace.root);
      paintSessions(); notice(t("工程文件夹已打开。可勾选资料后开始任务。"));
      await Promise.all([loadFiles(), loadTurns(), loadEngineering()]);
      if (current(epoch) && sessionRecord()?.turn) await showTurn(sessionRecord().turn);
    } catch (error) { if (current(epoch)) notice(t("打开失败：") + error.message, true); }
    finally { if (requestId === state.workspaceEpoch) { state.opening = false; controls(); } }
  }
  async function loadTurns() {
    if (!state.workspace) return;
    const epoch = state.epoch, requestId = ++state.listEpoch;
    try {
      const data = await request("/api/agent/turns?" + scoped());
      if (!current(epoch) || requestId !== state.listEpoch) return;
      const host = $("agentTurns"); host.replaceChildren();
      const turns = Array.isArray(data.turns) ? data.turns : []; state.taskRows = turns;
      if (!turns.length) host.appendChild(node("p", t("本会话暂无执行记录。"), "muted"));
      for (const turn of turns) {
        const id = idOf(turn); if (!id) continue;
        const button = node("button", (turn.message || turn.title || id.slice(0, 12)) + " · " + (t(STATUS[turn.status]) || turn.status || t("状态待读取")) + t(" · 发起者：") + (turn.actor_id || t("历史记录未标记")));
        button.type = "button"; button.setAttribute("aria-current", String(id === idOf(state.turn)));
        button.addEventListener("click", () => showTurn(id)); host.appendChild(button);
      }
    } catch (error) { if (current(epoch)) notice(t("执行记录读取失败：") + error.message, true); }
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
    const key = String(data.id || data.url || data.name || ""); if (!key) return;
    state.artifacts.set(key, data); const host = $("agentArtifacts"); host.replaceChildren();
    for (const item of state.artifacts.values()) {
      const card = node("article", undefined, "artifact");
      const url = artifactUrl(item.url, win.location.origin, state.workspace.id);
      const title = node(url ? "a" : "strong", item.name || t("文档产物")); if (url) { title.href = url; title.download = ""; }
      card.appendChild(title);
      card.appendChild(node("p", item.provenance === "model_proposed" ? t("模型提出修改，由文档工具应用") : item.provenance === "deterministic" ? t("确定性工具生成") : t("来源由服务端提供"), "muted"));
      if (!url) card.appendChild(node("p", t("当前没有可用的工作区下载链接。"), "muted"));
      const details = node("details"), summary = node("summary", t("来源与验证记录"));
      details.append(summary, node("pre", printable({ source: item.source, source_sha256: item.source_sha256, output_sha256: item.output_sha256, validation: item.validation })));
      card.appendChild(details); host.appendChild(card);
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
  function paintResult(result) {
    if (!object(result)) return;
    if (typeof result.reply === "string") $("agentReply").textContent = result.reply;
    $("agentPartial").hidden = result.partial !== true;
    for (const item of Array.isArray(result.artifacts) ? result.artifacts : []) paintArtifact(item);
    for (const item of Array.isArray(result.findings) ? result.findings : []) paintEngineeringResult(item);
    if (object(result.usage)) { $("agentUsageText").textContent = printable(result.usage); $("agentUsage").hidden = false; }
  }
  function paintTurn(turn) {
    if (!object(turn)) return;
    state.turn = { ...state.turn, ...turn };
    $("agentTurnStatus").textContent = t(STATUS[state.turn.status]) || state.turn.status || t("状态待读取");
    $("agentTurnActor").textContent = t("任务发起者：") + (state.turn.actor_id || t("历史记录未标记身份"));
    paintResult(state.turn.result); controls();
  }
  function eventFrame(event) {
    if (!event || !Number.isSafeInteger(event.seq) || event.seq <= state.seq) return;
    state.seq = event.seq; state.eventRows.push(event);
    const data = object(event.data) ? event.data : {}, kind = String(event.kind || "status");
    if (kind === "context") paintContext(data);
    if (kind === "artifact") paintArtifact(data);
    if (kind === "tool_finished" && (data.name === "engineering_analyze" || data.tool === "engineering_analyze")) paintEngineeringResult(data.result);
    if (kind === "done") paintResult(data.result || data);
    if (TURN_EVENTS[kind]) paintTurn({ status: TURN_EVENTS[kind], ...(data.result ? { result: data.result } : {}) });
    const li = node("li"), title = node("div", undefined, "event-title");
    title.append(node("strong", kind === "authorization" ? t("本轮权限记录") : t(EVENT_NAMES[kind]) || t(STATUS[TURN_EVENTS[kind]]) || kind), node("span", `#${event.seq}` + (event.actor_id ? t(" · 发起者：{actor}", { actor: event.actor_id }) : ""))); li.appendChild(title);
    const text = data.message || data.text || data.summary || data.tool || data.name || data.phase || data.model;
    if (typeof text === "string") li.appendChild(node("p", text));
    if (Object.keys(data).length) { const details = node("details"); details.append(node("summary", t("查看事件数据")), node("pre", printable(data))); li.appendChild(details); }
    $("agentEvents").appendChild(li);
  }
  async function poll() {
    if (!state.workspace || !state.turn || state.disposed) return;
    const epoch = state.epoch, id = idOf(state.turn), query = scoped();
    controller = new AbortController(); let delay = 900, retry = false;
    try {
      const data = await request(`/api/agent/turns/${encodeURIComponent(id)}/events?${query}&after_seq=${state.seq}`, { signal: controller.signal });
      if (!current(epoch) || id !== idOf(state.turn)) return;
      for (const event of (Array.isArray(data.events) ? data.events : []).slice().sort((a, b) => a.seq - b.seq)) eventFrame(event);
      paintTurn(data.turn);
      if (TERMINAL.has(state.turn.status)) { notice(t(STATUS[state.turn.status]) + (state.turn.result?.partial ? t("，部分工作尚未完成。") : (win.CBI18n?.locale === "en" ? "." : "。")), state.turn.status === "failed"); await loadTurns(); }
    } catch (error) {
      if (!current(epoch) || error.name === "AbortError") return;
      retry = true; delay = 2500; notice(t("连接暂时中断，将从已收到的事件继续恢复：") + error.message, true);
    } finally {
      if (current(epoch) && id === idOf(state.turn)) { controller = null; if (active() || retry) timer = later(poll, delay); }
    }
  }
  async function showTurn(id) {
    if (!state.workspace || !id) return;
    resetView(); const epoch = state.epoch;
    state.turn = { turn_id: id, status: "queued" }; controls();
    try {
      const data = await request(`/api/agent/turns/${encodeURIComponent(id)}?${scoped()}`);
      if (!current(epoch)) return;
      paintTurn(data.turn || data); sessionRecord().turn = id; persist(); await poll();
    } catch (error) { if (current(epoch)) { state.turn = null; controls(); notice(t("执行记录恢复失败：") + error.message, true); } }
  }
  async function send() {
    if ($("agentSend").disabled || !$("agentMessage").value.trim()) return;
    cancelVoice();
    const message = $("agentMessage").value.trim(); resetView(); state.submitting = true; controls();
    const epoch = state.epoch;
    notice(t("正在提交任务…"));
    try {
      if (!(await verifyEngineering(epoch))) return;
      const payload = { workspace: state.workspace.id, session_id: state.session, message, locale: win.CBI18n?.locale || "zh-CN", mode: $("agentMode").value, sandbox: $("agentSandbox").value, files: [...state.selected],
        expert_id: $("agentExpert").value, risk_confirmation: $("agentRiskConfirmation").value.trim(), engineering: state.engineeringRows.filter((row) => row.selected).map((row) => ({ ...row.snapshot, confirmed_solid: row.kind === "cad_section" && row.confirmed })) };
      const data = await request("/api/agent/turns", post(payload));
      if (!current(epoch)) return;
      if (!data.turn_id || data.session_id !== state.session) throw new Error(t("任务响应缺少本会话的执行标识"));
      sessionRecord().turn = data.turn_id; persist(); state.turn = { turn_id: data.turn_id, status: "queued" };
      $("agentMessage").value = ""; $("agentRiskConfirmation").value = ""; notice(t("任务已提交，正在接收执行事件。")); await poll();
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
    // UI state is repainted; user text, raw events, file names and saved outputs stay unchanged.
    if (state.capabilities) paintCapabilities(state.capabilities);
    paintSessions(); paintFiles(); paintExperts(); expertNote(); paintEngineering();
    if (state.contextData) paintContext(state.contextData);
    if (state.turn) paintTurn(state.turn);
    else {
      $("agentReply").textContent = t("结果将显示在这里。");
      $("agentTurnStatus").textContent = t("尚未开始");
      $("agentTurnActor").textContent = t("尚无任务发起者记录。");
      if (!state.contextData) $("agentContextText").textContent = t("收到后端请求预算后显示；这不是任务完成进度。");
    }
    for (const item of state.artifacts.values()) { paintArtifact(item); break; }
    for (const item of state.engineeringResults.values()) { paintEngineeringResult(item); break; }
    const events = state.eventRows.slice(); state.eventRows = []; state.seq = 0; $("agentEvents").replaceChildren();
    for (const event of events) eventFrame(event);
    const host = $("agentTurns"); host.replaceChildren();
    if (!state.taskRows.length) host.appendChild(node("p", t("本会话暂无执行记录。"), "muted"));
    for (const turn of state.taskRows) {
      const id = idOf(turn); if (!id) continue;
      const button = node("button", (turn.message || turn.title || id.slice(0, 12)) + " · " + (t(STATUS[turn.status]) || turn.status || t("状态待读取")) + t(" · 发起者：") + (turn.actor_id || t("历史记录未标记")));
      button.type = "button"; button.setAttribute("aria-current", String(id === idOf(state.turn)));
      button.addEventListener("click", () => showTurn(id)); host.appendChild(button);
    }
    if (state.modelConfig) {
      const cfg = state.modelConfig;
      $("agentModelLabel").textContent = cfg.configured ? cfg.model || t("已配置") : t("未配置，可先检查资料");
      $("agentModelStatus").textContent = cfg.configured ? t("Key {key}；来源：{source}。", { key: cfg.key_masked || t("已设置"), source: cfg.source === "runtime" ? t("本次运行") : t("启动配置") }) : t("尚未配置模型；检查资料无需 Key。");
    }
  }
  async function start() {
    if (started) return; started = true;
    win.addEventListener("cb:languagechange", languageChanged);
    languageChanged();
    $("agentWorkspaceForm").addEventListener("submit", (event) => { event.preventDefault(); openWorkspace($("agentWorkspacePath").value); });
    $("agentTaskForm").addEventListener("submit", (event) => { event.preventDefault(); send(); });
    $("agentModelForm").addEventListener("submit", (event) => { event.preventDefault(); saveModel(); });
    $("agentMode").addEventListener("change", controls); $("agentSandbox").addEventListener("change", controls);
    $("agentExpert").addEventListener("change", () => { expertNote(); controls(); }); $("agentRefreshEngineering").addEventListener("click", loadEngineering);
    $("agentRiskConfirmation").addEventListener("input", controls); $("agentMessage").addEventListener("input", controls);
    $("agentCancel").addEventListener("click", cancel); $("agentRefreshFiles").addEventListener("click", loadFiles); $("agentRefreshTurns").addEventListener("click", loadTurns);
    $("agentNewSession").addEventListener("click", () => changeSession("", true)); $("agentSession").addEventListener("change", () => changeSession($("agentSession").value));
    await Promise.all([loadCapabilities(), loadExperts(), loadModel().catch((error) => { $("agentModelLabel").textContent = t("暂不可用"); $("agentModelStatus").textContent = t("模型设置暂不可用：") + error.message; })]);
    if (saved.lastRoot && state.capabilities?.available === true) await openWorkspace(saved.lastRoot);
  }
  function dispose() { win.removeEventListener("cb:languagechange", languageChanged); cancelVoice(); state.disposed = true; state.epoch += 1; stopPolling(); }
  return { state, start, openWorkspace, loadFiles, loadTurns, loadEngineering, inspectEngineering, changeSession, showTurn, send, cancel, poll, saveModel, dispose };
}

if (typeof document !== "undefined" && document.getElementById("agentWorkbench")) {
  createAuth({ win: window, doc: document }).installFetchGuard();
  const app = createAgentWorkbench({ document, window });
  window.addEventListener("pagehide", () => app.dispose());
  app.start();
}
