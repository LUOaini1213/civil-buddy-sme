/* Civil Buddy 工作台页面。作为 ES module 加载（index.html: type="module"）；
   可复用的部件在 ./modules/ 里，这里只做接线和页面逻辑。 */
import { createAuth } from "./modules/auth.js";
import { createToast } from "./modules/toast.js";
import { createDrafts } from "./modules/drafts.js";
import { createUploads } from "./modules/uploads.js";
import { createTurnStream } from "./modules/turn-stream.js";
import { createDeliverables } from "./modules/deliverables.js";
import { createSessionWatch } from "./modules/session-watch.js";
import { createSessionNav, sessionId, cadProjectFromUrl } from "./modules/session-nav.js";

const state = {
  experts: [],
  catalog: null,
  summoned: new Set(),
  history: [],
  session: cbSessionId(),
  modelName: "",
  health: { capabilities: {} },
  /* ux(round21)：改回 attachments —— round19 拆掉入口后它一度没有写入方，
     叫这个名字等于给拖拽上传留后门；现在回形针是**唯一且显式**的写入方，名副其实。 */
  attachments: [],
  attachmentRoles: {},
  jobRoot: "",
  cadProjectId: cbCadProjectFromUrl(),
  planningProjectId: cbPlanningProjectFromUrl(),
  logisticsProjectId: cbProjectFromUrl("logistics_project_id"),
  lastSend: "", /* ux(round7)：纠偏卡「重试」重放同 payload */
  policy: { sandbox: "workspace-write", approval: "on-request" },
  context: {
    limit: 32768,
    reserve: 4096,
    compress_pct: 70,
    warn_pct: 50,
    keep_recent: 4,
    compress_at: 20070,
  },
};

const $ = (id) => document.getElementById(id);
/* 页面上"正在跑"的一切，一个对象：
   active      —— 有流的那一轮（发送中 / 续流中 / 切回来跟着的），停止按钮停的就是它
   watched     —— 只是旁观的后台轮次（没有 event_log 能力的后端上用轮询看着）
   background  —— 切走后仍在服务端跑的会话 id，列表画「运行中」、完成弹提示 */
const runState = { active: null, watched: null, background: new Set() };
/* 左栏的项目树：项目列表、收件箱、会话行、展开集合、当前项目 id（页面持有，nav 模块借用） */
const cbProj = { projects: [], inbox: null, sessions: [], open: new Set(), cur: "" };
let cbSessionRequest = 0;
let cbContextRequest = 0;
let cbContextSearchRequest = 0;
let cbContextSourceEpoch = 0;
let cbContextRebuildRun = null;
let cbCapabilityRequest = 0;
let cbServerHitlInput = null;

function cbCadProjectFromUrl() {
  return cbProjectFromUrl("cad_project_id", "planning_project_id");
}

function cbPlanningProjectFromUrl() {
  return cbProjectFromUrl("planning_project_id", "cad_project_id");
}

function cbProjectFromUrl(key, other) {
  try {
    const params = new URL(globalThis.location.href).searchParams;
    if (["cad_project_id", "planning_project_id", "logistics_project_id"].some((name) => name !== key && params.get(name))) return "";
    const id = params.get(key) || "";
    return /^[0-9a-f]{32}$/.test(id) ? id : "";
  } catch (_) { return ""; }
}

function cbPlanningProjectRender() {
  let banner = $("planningProjectContext");
  if (!state.planningProjectId) {
    if (banner) banner.remove();
    try {
      const url = new URL(globalThis.location.href);
      if (url.searchParams.has("planning_project_id")) {
        url.searchParams.delete("planning_project_id");
        globalThis.history.replaceState(null, "", url);
      }
    } catch (_) { /* Non-browser render tests do not own navigation. */ }
    return;
  }
  const composer = $("input") && $("input").parentElement;
  if (!composer) return;
  if (!banner) {
    banner = document.createElement("div");
    banner.id = "planningProjectContext";
    banner.className = "status-line";
    composer.prepend(banner);
  }
  banner.replaceChildren();
  const link = document.createElement("a");
  link.href = "/engineering/planning?project_id=" + state.planningProjectId;
  link.textContent = "当前施工计划 · 返回排程核对";
  banner.appendChild(link);
  const notice = document.createElement("span");
  notice.textContent = "对话生成建议，应用前须确认。";
  banner.appendChild(notice);
  const clear = document.createElement("button");
  clear.type = "button";
  clear.textContent = "取消选择";
  clear.addEventListener("click", () => { state.planningProjectId = ""; cbPlanningProjectRender(); });
  banner.appendChild(clear);
}

function cbRestoreProjectBindings(data) {
  // The selected saved session owns its project, including after a refresh.
  try {
    const url = new URL(globalThis.location.href);
    for (const key of ["cad_project_id", "planning_project_id", "logistics_project_id"]) url.searchParams.delete(key);
    globalThis.history.replaceState(null, "", url);
  } catch (_) { /* Session restoration also runs in non-browser tests. */ }
  const cad = /^[0-9a-f]{32}$/.test(data.cad_project_id || "") ? data.cad_project_id : "";
  const planning = /^[0-9a-f]{32}$/.test(data.planning_project_id || "") ? data.planning_project_id : "";
  const logistics = /^[0-9a-f]{32}$/.test(data.logistics_project_id || "") ? data.logistics_project_id : "";
  const exclusive = [cad, planning, logistics].filter(Boolean).length === 1;
  state.cadProjectId = exclusive ? cad : "";
  state.planningProjectId = exclusive ? planning : "";
  state.logisticsProjectId = exclusive ? logistics : "";
  cbCadProjectRender();
  cbPlanningProjectRender();
  cbLogisticsProjectRender();
}

function cbLogisticsProjectRender() {
  let banner = $("logisticsProjectContext");
  if (!state.logisticsProjectId) {
    if (banner) banner.remove();
    try {
      const url = new URL(globalThis.location.href);
      if (url.searchParams.has("logistics_project_id")) {
        url.searchParams.delete("logistics_project_id");
        globalThis.history.replaceState(null, "", url);
      }
    } catch (_) { /* Rendering without navigation is supported. */ }
    return;
  }
  const composer = $("input") && $("input").parentElement;
  if (!composer) return;
  if (!banner) {
    banner = document.createElement("div");
    banner.id = "logisticsProjectContext";
    banner.className = "status-line";
    composer.prepend(banner);
  }
  banner.replaceChildren();
  const link = document.createElement("a");
  link.href = "/logistics?project_id=" + state.logisticsProjectId;
  link.textContent = "当前箱单 · 返回物流材料核对";
  banner.appendChild(link);
  const notice = document.createElement("span");
  notice.textContent = "对话只提出建议，修改须在箱单页确认。";
  banner.appendChild(notice);
  const clear = document.createElement("button");
  clear.type = "button";
  clear.textContent = "取消选择";
  clear.addEventListener("click", () => { state.logisticsProjectId = ""; cbLogisticsProjectRender(); });
  banner.appendChild(clear);
}

function cbCadProjectRender() {
  let banner = $("cadProjectContext");
  if (!state.cadProjectId) {
    if (banner) banner.remove();
    try {
      const url = new URL(globalThis.location.href);
      if (url.searchParams.has("cad_project_id")) {
        url.searchParams.delete("cad_project_id");
        globalThis.history.replaceState(null, "", url);
      }
    } catch (_) { /* Non-browser render tests do not own navigation. */ }
    return;
  }
  const composer = $("input") && $("input").parentElement;
  if (!composer) return;
  if (!banner) {
    banner = document.createElement("div");
    banner.id = "cadProjectContext";
    banner.className = "status-line";
    composer.prepend(banner);
  }
  banner.replaceChildren();
  const link = document.createElement("a");
  link.href = "/cad?project_id=" + state.cadProjectId;
  link.textContent = "当前 CAD 项目 · 返回三维模型";
  banner.appendChild(link);
  const clear = document.createElement("button");
  clear.type = "button";
  clear.textContent = "取消选择";
  clear.addEventListener("click", () => { state.cadProjectId = ""; cbCadProjectRender(); });
  banner.appendChild(clear);
}

function cbSessionId() { return sessionId(); }
function cbRememberSession(id) { return nav.rememberSession(id); }
function cbRememberedSession() { return nav.rememberedSession(); }
async function cbResumeSession(id, request) { return nav.resumeSession(id, request); }

// The two sign-off sentences (packing_assistant/runtime/civil_config.py CONFIRM_SENTENCES); the server decides.
const CB_SIGNOFF = ["我明白，将由持证人员签认", "I understand; a licensed person will sign this off."];

function cbConfirmText() {
  const input = $("confirmOk");
  return input && CB_SIGNOFF.includes(input.value) ? input.value : "";
}

function cbConfirmed() {
  return !!cbConfirmText();
}

function cbHitlPending(data) {
  return !!(data && (data.hitl_pending === true || data.hitl && data.hitl.pending === true));
}

function cbEnableServerHitl(data) {
  if (!cbHitlPending(data) && !(data && data.phase === "hitl_gate" && data.confirmed === false)) return;
  const input = $("confirmOk");
  if (!input) return;
  // Only a current, session-guarded server event may override a disabled input.
  // Keep this ephemeral: restored history and cached policy never enter here.
  if (!cbServerHitlInput) cbServerHitlInput = { disabled: !!input.disabled, placeholder: input.placeholder || "" };
  input.disabled = false;
  input.placeholder = "服务器要求本轮确认，请键入完整签认句";
  const disclosure = $("riskDisclosure");
  if (disclosure) disclosure.open = true;
}

function cbClearServerHitl() {
  const input = $("confirmOk");
  if (input && cbServerHitlInput) {
    input.disabled = cbServerHitlInput.disabled;
    input.placeholder = cbServerHitlInput.placeholder;
    input.value = "";
  }
  cbServerHitlInput = null;
}

function cbRunPaint(running) {
  const send = $("send");
  if (send) {
    send.textContent = "↑";
    send.setAttribute("aria-label", running ? "运行中…" : "发送");
    if (running) {
      send.dataset.running = "1";
      send.disabled = true;
    } else {
      delete send.dataset.running;
      cbSyncSend();
    }
  }
  const stop = $("stop");
  /* 没有取消能力的后端上，停止只会断开浏览器这一端，服务端那轮照跑：不要摆一个假按钮。 */
  if (stop) { stop.hidden = !running || cbCapability("cancel") === false; stop.disabled = false; stop.textContent = "停止"; }
  const form = $("form");
  if (form) form.setAttribute("aria-busy", String(running));
}

function cbCancelActiveRun() {
  if (!runState.active) return;
  const run = runState.active;
  if (cbCapability("cancel") === true) cbRequestCancellation(run).catch(() => {});
  run.controller.abort();
  runState.active = null;
  cbRunPaint(false);
}

/* 切换任务 / 新建任务时只断开浏览器这一端，不向服务端发取消：
   服务端的 turn 持有 lease 直到落盘，回到该任务时由 cbWatchSession 把结果拉回来。
   显式「停止」按钮仍走 cbCancelActiveRun。 */
function cbDetachActiveRun() { return watch.detachActiveRun(); }
function cbBgObserve(rows) { return watch.bgObserve(rows); }
function cbBgSchedule() { return watch.bgSchedule(); }
function cbReleaseWatch() { return watch.releaseWatch(); }
async function cbWatchSession(sid, opts) { return watch.watchSession(sid, opts); }
function cbPaintRecovered(d, options) { return watch.paintRecovered(d, options); }

/* 回答收口后按 Markdown 画（标题、列表、表格、代码），流式期间仍是纯文本。
   渲染器来自 docpreview.js（marked + 白名单清洗）；没有它就退回纯文本。history 里存的始终是原文。 */
function cbPaintMarkdown(bodyEl, text) {
  if (!bodyEl) return;
  const render = typeof window.cbDocRenderMarkdown === "function" ? window.cbDocRenderMarkdown : null;
  const raw = String(text || "");
  if (!render || !/(^|\n)\s*(#{1,6}\s|[-*+]\s|\d+\.\s|\|.*\||```|>\s)|`[^`]+`|\*\*[^*]+\*\*/.test(raw)) {
    bodyEl.textContent = raw;
    if (bodyEl.classList) bodyEl.classList.remove("md");
    return;
  }
  let el = null;
  try { el = render(raw); } catch (e) { el = null; }
  if (!el) { bodyEl.textContent = raw; return; }
  bodyEl.textContent = "";
  bodyEl.appendChild(el);
  if (bodyEl.classList) bodyEl.classList.add("md");
}

/* 页面上唯一的可见提示条（modules/toast.js）：一次一条，6 s 自己消失，可带一个动作按钮。 */
const toast = createToast({ doc: document, announce: (text) => cbAnnounce(text) });
function cbToast(text, opts) { return toast(text, opts); }

/* 旁观（modules/session-watch.js）：切走的任务继续跑、后台完成提示、无事件日志时的轮询恢复。 */
const watch = createSessionWatch({
  state,
  runState,
  sessionRequest: () => cbSessionRequest,
  runPaint: (on) => cbRunPaint(on),
  announce: (text) => cbAnnounce(text),
  addMsg: (role, who, text) => addMsg(role, who, text),
  addStatus: (text) => addStatus(text),
  toast: (text, opts) => cbToast(text, opts),
  openSession: (row) => cbProjOpenSession(row),
  loadThreads: () => loadThreads(),
  appendDocCards: (files, bodyEl, opts) => appendDocCards(files, bodyEl, opts),
  setLastDeliverables: (files) => { cbLastDeliverables = files; },
  refreshAuditSoon: () => refreshAuditSoon(),
  markdown: (bodyEl, text) => cbPaintMarkdown(bodyEl, text),
  capability: (name) => cbCapability(name),
  fetch: (url, init) => fetch(url, init),
  doc: document,
  log: () => $("log"),
  setTimeout: (fn, ms) => setTimeout(fn, ms),
  clearTimeout: (t) => clearTimeout(t),
  now: () => Date.now(),
});

/* 手机回到前台（iOS 后台会掐掉 fetch 流）、从 bfcache 回来（pageshow persisted）、断网恢复（online）：
   没有活动流时，检查当前任务是否还在服务端跑，在跑就接上。 */
function cbCheckForegroundTurn(reason) {
  if (runState.active || !state.session) return;
  fetch("/api/sessions/" + encodeURIComponent(state.session))
    .then((r) => (r.ok ? r.json() : null))
    .then((d) => {
      if (d && d.turn_state && d.turn_state.active && !runState.active && state.session === d.session_id) {
        cbAttachToTurn(d.session_id, reason);
      }
    })
    .catch(() => {});
}
document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "visible") cbCheckForegroundTurn("回到前台；");
});
if (typeof window.addEventListener === "function") {
  window.addEventListener("pageshow", (ev) => { if (ev && ev.persisted) cbCheckForegroundTurn("回到页面；"); });
  window.addEventListener("online", () => cbCheckForegroundTurn("网络恢复；"));
}

async function cbRequestCancellation(run) {
  if (!run || run.cancelRequested) return;
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 8000);
  run.cancelRequested = true;
  try {
    const response = await fetch(`/api/sessions/${encodeURIComponent(run.session)}/cancel`, {
      method: "POST", signal: controller.signal,
    });
    if (!response.ok) throw new Error(await apiError(response) || "停止请求失败");
    return await response.json();
  } catch (error) {
    run.cancelRequested = false;
    throw error;
  } finally { clearTimeout(timer); }
}

function cbCapability(name) {
  return state.health.capabilities && state.health.capabilities[name];
}

function cbApplyHealth(health) {
  const capabilities = health && health.capabilities;
  state.health = {
    ...state.health, ...health,
    capabilities: { ...state.health.capabilities, ...(capabilities && typeof capabilities === "object" ? capabilities : {}) },
  };
  const configured = !!(state.health.has_key || state.health.deepseek);
  const offline = state.health.mode === "offline" && cbCapability("chat") === true;
  const badge = $("keyBadge");
  if (badge) {
    badge.textContent = configured ? "模型已配置" : offline ? "离线工作台可用" : "未配置模型";
    badge.className = configured || offline ? "pill ok" : "pill warn";
    badge.title = configured ? "开放式问答使用当前配置；岗位工具按任务运行。" :
      offline ? "无需 API Key 即可使用当前离线岗位功能。" : "可浏览岗位与知识库，模型能力尚未配置。";
  }
  const byUrl = $("btnAttachUrl");
  if (byUrl) byUrl.hidden = cbCapability("upload_url") !== true;   /* health 到了才知道服务端能不能自己去取 */
  const availability = $("cbAvailability");
  if (availability) availability.textContent = offline
    ? "无需 API Key：先问岗位能力" + (cbCapability("drafts") === true ? "，或生成模板草稿。" : "。") + "开放式问答可在模型设置中配置。"
    : configured ? "模型已配置。选择岗位、添加材料，再描述你的任务。"
      : "先浏览岗位与知识库。可用任务以当前工作台能力为准，开放式问答需配置模型。";
  for (const [id, capability, title] of [
    ["cbLlmOpen", "model_settings", "当前工作台未提供模型设置"],
    ["cbEmptyModel", "model_settings", "当前工作台未提供模型设置"],
    ["btnAttach", "attachments", "当前工作台未提供附件上传"],
    ["cbPackSample", "packing", "装箱示例执行需连接装箱服务；可从上方装箱拼柜入口查看状态"],
    ["cbBackupExport", "session_backup", "当前服务不支持任务备份"],
    ["cbBackupImport", "session_backup", "当前服务不支持任务导入"],
  ]) {
    const element = $(id);
    if (!element) continue;
    element.disabled = cbCapability(capability) === false;
    if (element.disabled) element.title = title;
    else element.removeAttribute("title");
  }
  // Other hosts share this HTML without exposing the optional CAD routes.
  const cadEntry = $("cbCadEntry");
  if (cadEntry) cadEntry.hidden = cbCapability("cad") !== true;
  if (health.model) state.modelName = health.model;
  cbSyncSend();
}

/* 访问口令（modules/auth.js）：cookie 里的 CIVIL_TOKEN，首个 401 问一次并重试那一次请求。 */
const auth = createAuth({ win: window, doc: document });
const { hasToken, askToken } = auth;
auth.installFetchGuard();

async function boot() {
  const request = cbSessionRequest;
  const remembered = cbRememberedSession();
  /* ux(round10)：后端未起/不可达 → 网关兜底空态（附录 I），不再裸 unhandled rejection */
  try {
    const health = await fetch("/api/health").then(async (r) => {
      if (!r.ok) throw new Error(await apiError(r) || "HTTP " + r.status);
      return r.json();
    });
    if (health && health.capabilities && health.capabilities.auth && !hasToken()) {
      await askToken("这个工作台开了访问口令");
    }
    cbApplyHealth(health);
    if (health.context) state.context = { ...state.context, ...health.context };
    CB_PACK_TRIAL = cbPackTrialFrom(health);
    paintContext(estimateLocalContext());
    await reloadCatalog();
    await loadJobRoot();
    await loadPolicy();
    await loadThreads();
    await cbResumeSession(remembered, request);
    return true;
  } catch (err) {
    cbEmptyDownShow(err);
    return false;
  }
}

async function loadPolicy() {
  try {
    const cfg = await fetch("/api/config").then((r) => r.json());
    state.policy = cfg;
  } catch (e) {
    /* ux(round19)：Rust 工作台没有 /api/config（只有 Python 参考实现有）。
       原先这里 addStatus(String(e))，导致评委每次打开页面，对话流里都会多一行
       "SyntaxError: Unexpected end of JSON input" —— 「页面不干净」的字面来源。
       静默保留默认 state.policy 即可，策略徽章本就允许缺省。 */
  }
}

/* ===== ux(round21) 附件（回形针）=====
   round19 把本地文件入口整体拆了，界面是干净了，但用自己的表只剩「放进仓库 /
   给绝对路径」两条路，最自然的「拖进来」没有了。这里只补回一个入口：
   composer 里的回形针 + 一行可移除的 chip。**不恢复**原先那排杂项。

   拖拽也一并接回来 —— round19 删它是因为它当时**看不见**（删了按钮它还活着，
   拖个文件进来就静默 POST）。现在有可见的 chip 反馈，它不再是隐藏通道。

   服务端 /api/upload 一直都在（round19 只拆了界面），Python 参考实现没有该路由，
   届时静默提示而不是抛错。 */
function cbAttachRender() {
  const box = $("attaches");
  if (!box) return;
  box.innerHTML = "";
  const pending = uploads.pending.filter((u) => u.session === state.session);
  if (!state.attachments.length && !pending.length) {
    box.hidden = true;
    return;
  }
  box.hidden = false;
  for (const u of pending) {
    const chip = document.createElement("span");
    chip.className = "cb-att-chip pending" + (u.error ? " err" : "");
    chip.dataset.upload = u.key;
    const nm = document.createElement("span");
    nm.className = "cb-att-name";
    nm.textContent = u.name;
    nm.title = u.name;
    chip.appendChild(nm);
    if (u.error) {
      const er = document.createElement("span");
      er.className = "cb-att-size";
      er.textContent = "失败：" + u.error;
      er.title = u.error;
      chip.appendChild(er);
      const retry = document.createElement("button");
      retry.type = "button";
      retry.className = "cb-att-x";
      retry.textContent = "重试";
      retry.addEventListener("click", () => uploads.retry(u));
      chip.appendChild(retry);
    } else {
      const bar = document.createElement("span");
      bar.className = "cb-att-bar";
      bar.setAttribute("role", "progressbar");
      bar.setAttribute("aria-label", "上传 " + u.name);
      bar.appendChild(document.createElement("i"));
      chip.appendChild(bar);
      const pct = document.createElement("span");
      pct.className = "cb-att-pct";
      pct.textContent = u.xhr ? "0%" : "排队";
      chip.appendChild(pct);
    }
    const x = document.createElement("button");
    x.type = "button";
    x.className = "cb-att-x";
    x.textContent = "\u00d7";
    x.setAttribute("aria-label", "取消上传 " + u.name);
    x.addEventListener("click", () => uploads.cancel(u));
    chip.appendChild(x);
    box.appendChild(chip);
    uploads.paintProgress(u);
  }
  for (const f of state.attachments) {
    const chip = document.createElement("span");
    chip.className = "cb-att-chip";
    const byRef = cbCapability("file_ref") === true && f.id && !String(f.id).startsWith("job:") && state.session;
    const nm = document.createElement(byRef ? "a" : "span");
    nm.className = "cb-att-name";
    nm.textContent = f.name || f.id;
    nm.title = byRef ? "下载原件 " + (f.name || f.id) : (f.name || f.id);
    if (byRef) {
      nm.href = `/api/file?session=${encodeURIComponent(state.session)}&upload=${encodeURIComponent(f.id)}&name=${encodeURIComponent(f.name || f.id)}`;
      nm.setAttribute("download", f.name || f.id);
    }
    chip.appendChild(nm);
    const role = document.createElement("select");
    role.className = "cb-att-role";
    role.setAttribute("aria-label", "资料用途：" + (f.name || f.id));
    for (const [value, label] of [["", "自动判断"], ["tender", "招标原文"], ["response", "投标响应"], ["reference", "参考资料"]]) {
      const option = document.createElement("option");
      option.value = value;
      option.textContent = label;
      role.appendChild(option);
    }
    role.value = state.attachmentRoles[f.id] || "";
    role.addEventListener("change", () => {
      if (["tender", "response", "reference"].includes(role.value)) state.attachmentRoles[f.id] = role.value;
      else delete state.attachmentRoles[f.id];
    });
    chip.appendChild(role);
    if (f.bytes != null) {
      const sz = document.createElement("span");
      sz.className = "cb-att-size";
      sz.textContent = fmtBytes(f.bytes);
      chip.appendChild(sz);
    }
    const x = document.createElement("button");
    x.type = "button";
    x.className = "cb-att-x";
    x.textContent = "\u00d7"; /* U+00D7，符号纪律显式豁免 */
    x.setAttribute("aria-label", "移除附件 " + (f.name || f.id));
    x.addEventListener("click", () => {
      state.attachments = state.attachments.filter((a) => a.id !== f.id);
      delete state.attachmentRoles[f.id];
      cbAttachRender();
    });
    chip.appendChild(x);
    box.appendChild(chip);
  }
}

async function cbAttachUpload(fileList) { return uploads.upload(fileList); }
function cbUploadAbortAll(exceptSession) { uploads.abortAll(exceptSession); }

/* 上传队列（modules/uploads.js）：发前预检、并发 2、XHR 进度、失败留在原位可重试。 */
const uploads = createUploads({
  state,
  capability: (name) => cbCapability(name),
  addStatus: (text) => addStatus(text),
  render: () => cbAttachRender(),
  apiError: (res) => apiError(res),
  askToken: (reason) => (typeof askToken === "function" ? askToken(reason) : Promise.resolve(false)),
  fmtBytes: (n) => (typeof fmtBytes === "function" ? fmtBytes(n) : `${n} B`),
  fetch: (url, init) => fetch(url, init),
  XMLHttpRequest: typeof XMLHttpRequest === "function" ? XMLHttpRequest : null,
  doc: document,
});

/* 从网址取文件：服务端去取（demo/uploads.py fetch_upload / workbench attach::import_url），回来的形状与 /api/upload 相同。 */
async function cbAttachFromUrl() {
  const address = (window.prompt("招标文件或招标公告的网址（工作台去取；只取公网地址，20 MB 以内）：") || "").trim();
  if (!address) return;
  if (cbUploadSlotsLeft(state.session) <= 0) { addStatus(`同一会话最多 ${CB_UPLOAD_LIMITS.maxFiles} 个附件`); return; }
  addStatus("正在从网址取文件…");
  try {
    const res = await fetch("/api/upload-url", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: state.session, url: address }) });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) { addStatus("没有取到：" + (data.detail || data.error || res.status)); return; }
    const why = cbUploadAccept(null, data);
    if (why) { addStatus(why); return; }
    cbAttachRender();
    addStatus("已取回并作为附件：" + (data.files || []).map((f) => f.name).join("、"));
  } catch (err) {
    addStatus("没有取到：" + (err && err.message ? err.message : "网络错误"));
  }
}

function cbAttachInit() {
  const byUrl = $("btnAttachUrl");
  if (byUrl) {
    byUrl.hidden = cbCapability("upload_url") !== true;
    byUrl.addEventListener("click", cbAttachFromUrl);
  }
  const btn = $("btnAttach");
  const pick = $("filePick");
  if (btn && pick) {
    btn.addEventListener("click", () => pick.click());
    pick.addEventListener("change", async (ev) => {
      await cbAttachUpload(ev.target.files);
      ev.target.value = "";
    });
  }
  const composer = document.querySelector(".composer");
  if (composer) {
    composer.addEventListener("dragover", (ev) => {
      ev.preventDefault();
      composer.classList.add("drop");
    });
    composer.addEventListener("dragleave", () => composer.classList.remove("drop"));
    composer.addEventListener("paste", (ev) => {
      const items = ev.clipboardData && ev.clipboardData.files;
      if (items && items.length) { ev.preventDefault(); cbAttachUpload(items); }
    });
    composer.addEventListener("drop", async (ev) => {
      ev.preventDefault();
      composer.classList.remove("drop");
      if (ev.dataTransfer && ev.dataTransfer.files && ev.dataTransfer.files.length) {
        await cbAttachUpload(ev.dataTransfer.files);
      }
    });
  }
}

cbAttachInit();

/* ===== ux(round19) 审计抽屉 + 设置菜单（docs/ux 附录 P）=====
   审计从常驻右栏改为右侧抽屉。**≥1280 默认展开是硬约束不是审美**：
   test_offline_ui:150 与金线第 6 项都是 page.click("#loadAudit") 且**无前置展开动作**，
   Playwright 的 click 会等元素可见 —— 抽屉默认收起 = 两个 e2e 直接挂。
   用户手动收起后记住（cb_dock_v1），窄屏默认收起。 */
const CB_DOCK_KEY = "cb_dock_v1";
const CB_DOCK_WIDE = 1280;

function cbDockRead() {
  try {
    const v = localStorage.getItem(CB_DOCK_KEY);
    if (v === "open") return true;
    if (v === "closed") return false;
  } catch (e) { /* 存储不可用：按宽度默认 */ }
  return null;
}

function cbDockSet(open, remember) {
  const dock = $("cbDock");
  const btn = $("cbDockBtn");
  if (dock) dock.hidden = !open;
  document.body.dataset.dock = open ? "open" : "closed";
  if (btn) btn.setAttribute("aria-expanded", open ? "true" : "false");
  if (remember) {
    try { localStorage.setItem(CB_DOCK_KEY, open ? "open" : "closed"); } catch (e) { /* 忽略 */ }
  }
}

function cbDockInit() {
  const saved = cbDockRead();
  const wide = window.innerWidth >= CB_DOCK_WIDE;
  cbDockSet(saved === null ? wide : saved, false);
  const btn = $("cbDockBtn");
  if (btn) {
    btn.addEventListener("click", () => {
      cbDockSet(document.body.dataset.dock !== "open", true);
    });
  }
  const close = $("cbDockClose");
  if (close) close.addEventListener("click", () => cbDockSet(false, true));
}

/* 设置菜单：低频入口（模型 / 知识库）收纳。点外部或 Esc 关闭。 */
function cbMoreInit() {
  const btn = $("cbMoreBtn");
  const menu = $("cbMoreMenu");
  if (!btn || !menu) return;
  const setOpen = (open) => {
    menu.hidden = !open;
    btn.setAttribute("aria-expanded", open ? "true" : "false");
  };
  btn.addEventListener("click", (ev) => {
    ev.stopPropagation();
    setOpen(menu.hidden);
  });
  menu.addEventListener("click", () => setOpen(false));
  document.addEventListener("click", (ev) => {
    if (!menu.hidden && !menu.contains(ev.target) && ev.target !== btn) setOpen(false);
  });
  document.addEventListener("keydown", (ev) => {
    if (ev.key === "Escape" && !menu.hidden) setOpen(false);
  });
}

cbDockInit();
cbMoreInit();
if ($("btnNewProject")) {
  $("btnNewProject").addEventListener("click", () => {
    const box = $("projTree");
    if (!box || box.querySelector(".rail-rename")) return;
    const inp = document.createElement("input");
    inp.type = "text";
    inp.className = "rail-rename";
    inp.placeholder = "项目名，Enter 确认";
    let settled = false;
    const done = async (save) => {
      if (settled) return;
      settled = true;
      const v = inp.value.trim();
      inp.remove();
      if (!save || !v) return;
      try {
        const r = await fetch("/api/projects", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ name: v }),
        });
        if (!r.ok) throw new Error("HTTP " + r.status);
        await loadThreads();
      } catch (e) {
        addStatus("新建项目失败：" + ((e && e.message) || e));
      }
    };
    inp.addEventListener("keydown", (ev) => {
      if (ev.key === "Enter") { ev.preventDefault(); done(true); }
      if (ev.key === "Escape") { ev.preventDefault(); done(false); }
    });
    inp.addEventListener("blur", () => done(true));
    box.prepend(inp);
    inp.focus();
  });
}


/* ux(round19)：本地新会话 —— 不依赖任何后端接口，任何后端上都生效。 */
function cbNewLocalSession() { return nav.newLocalSession(); }

function cbContextReset() {
  cbContextRequest += 1;
  cbContextSearchRequest += 1;
  cbContextSourceEpoch += 1;
  cbContextRebuildRun = null;
  cbCapabilityRequest += 1;
  if ($("cbCapabilityBody")) {
    $("cbCapabilityBody").replaceChildren();
    $("cbCapabilityBody").textContent = "查看开始前需要的资料、可用工具与草稿检查标准。";
    $("cbCapabilityBody").setAttribute("aria-busy", "false");
  }
  state.context.lastReport = null;
  if ($("ctxMemory")) {
    $("ctxMemory").textContent = "查看本任务记忆；检索仅使用本任务历史和当前选择的附件。";
    $("ctxMemory").setAttribute("aria-busy", "false");
  }
  if ($("ctxQuery")) $("ctxQuery").value = "";
  if ($("ctxResults")) $("ctxResults").replaceChildren();
  if ($("ctxSearchStatus")) $("ctxSearchStatus").textContent = "";
  if ($("ctxMemoryStatus")) $("ctxMemoryStatus").textContent = "";
  cbContextRebuildControls(false);
}

/* 会话导航（modules/session-nav.js）：当前会话、记住/恢复、新建、项目与会话列表、打开会话。
   页面持有的状态原样交进去（state / runState / cbProj / 导航计数器），跨调用经页面绕一圈，测试才能替换。 */
const nav = createSessionNav({
  state,
  runState,
  proj: cbProj,
  request: { current: () => cbSessionRequest, bump: () => ++cbSessionRequest },
  storage: localStorage,
  location: globalThis.location,
  history: globalThis.history,
  fetch: (url, init) => fetch(url, init),
  doc: document,
  el: (id) => $(id),
  relTime: (ts) => cbRelTime(ts),
  addStatus: (text) => addStatus(text),
  addMsg: (role, who, text) => addMsg(role, who, text),
  reset: {
    cancelVoice: () => window.CivilBuddyVoice?.cancel("已切换项目或会话，旧语音草稿已取消。"),
    toEmpty: () => cbResetToEmpty(),
    contextReset: () => cbContextReset(),
    clearServerHitl: () => cbClearServerHitl(),
    hideWelcome: () => cbHideWelcome(),
    detachActiveRun: () => cbDetachActiveRun(),
    uploadAbortAll: (sid) => cbUploadAbortAll(sid),
    attachRender: () => cbAttachRender(),
    draftRestore: () => cbDraftRestore(),
    paintContext: (d) => paintContext(d),
    estimateLocalContext: () => estimateLocalContext(),
    renderSummon: () => renderSummon(),
    renderToolProjects: () => { cbCadProjectRender(); cbPlanningProjectRender(); cbLogisticsProjectRender(); },
    restoreToolProjects: (data) => cbRestoreProjectBindings(data),
  },
  apiError: (res) => apiError(res),
  paint: {
    appendDocCards: (files, bodyEl, opts) => appendDocCards(files, bodyEl, opts),
    routePaint: (route, bodyEl, message) => cbTaskRoutePaint(route, bodyEl, message),
    collaborationPaint: (data, bodyEl) => cbCollaborationPaint(data, bodyEl),
    setLastDeliverables: (files) => { cbLastDeliverables = files; },
    markdown: (bodyEl, text) => cbPaintMarkdown(bodyEl, text),
  },
  hooks: {
    render: () => cbProjRender(),
    loadThreads: () => loadThreads(),
    openSession: (s) => cbProjOpenSession(s),
    attachToTurn: (sid, reason) => cbAttachToTurn(sid, reason),
    bgObserve: (rows) => cbBgObserve(rows),
  },
});

nav.openLoad(); // 项目树的展开状态：nav 接好之后再读

/* 草稿（modules/drafts.js）：按会话存 localStorage，切回来填回去，发送即清。 */
const drafts = createDrafts({
  storage: localStorage,
  input: () => $("input"),
  session: () => state.session,
  afterRestore: (ta) => { cbAutosize(ta); cbSyncSend(); },
});
function cbDraftSave() { drafts.save(); }
function cbDraftClear(sid) { drafts.clear(sid); }
function cbDraftRestore() { drafts.restore(); }
if ($("input")) $("input").addEventListener("input", cbDraftSave);

/* ux(round14)：相对时间（参考图会话列表「名称 + 相对时间」；只抄线程 updated_at 字段） */
function cbRelTime(ts) {
  const t = Number(ts);
  if (!t || !isFinite(t)) return "";
  const diff = Math.max(0, Date.now() / 1000 - t);
  if (diff < 60) return "刚刚";
  if (diff < 3600) return Math.floor(diff / 60) + " 分钟";
  if (diff < 86400) return Math.floor(diff / 3600) + " 小时";
  if (diff < 86400 * 30) return Math.floor(diff / 86400) + " 天";
  return new Date(t * 1000).toISOString().slice(0, 10);
}

/* ux(round14)：「新建任务」=清空当前会话回空态卡（Codex 历史流第 0 号 cell 复位） */
/* ux(round19)：空态卡改「隐藏」而非「移除」。
   原先发首条消息时 welcome.remove() 把 #cbEmpty 整个删掉，而 cbResetToEmpty()
   只会把它 hidden=false —— 于是「+ 新建任务」按钮 title 写着「回到空态卡」，
   实际一旦发过消息就永远回不去（既有缺陷，本轮修 exe 死键时暴露）。 */
function cbHideWelcome() {
  const el = $("cbEmpty") || document.querySelector(".welcome");
  if (!el) return;
  el.classList.remove("welcome");
  el.hidden = true;
}

function cbResetToEmpty() {
  CB_APR_WAITING.clear();
  const log = $("log");
  if (log) {
    for (const el of Array.from(log.children)) {
      if (el.id !== "cbOnboard" && el.id !== "cbEmpty") el.remove();
    }
    const empty = $("cbEmpty");
    if (empty) {
      empty.hidden = false;
      empty.classList.add("welcome"); /* 发送首条消息时沿用既有移除逻辑 */
    }
  }
  cbLastDeliverables = [];
}

/* ===== ux(round19) 左栏项目树（两级：工程项目 > 该项目下历次会话）=====
   数据源是 Rust 的 /api/projects 与 /api/sessions（P3-P5）。没有这两个接口的后端
   （如 Python 参考实现，P8 才补镜像）静默降级：不报错、不留空白、不写对话流。
   展开态存 localStorage，与 cb_theme_v1 / cb_dock_v1 同一套 try/catch 容错写法。 */
function cbProjOpenLoad() { return nav.openLoad(); }
async function loadThreads() { return nav.loadThreads(); }
function cbProjRender() { return nav.renderProjects(); }
function cbProjRename(p) { return nav.renameProject(p); }

/* 行内改名：不用 prompt()（嵌入视图会被拦，且与现有 vanilla 风格不搭） */
/* 点会话：拉详情并**整体替换** state.history（不 merge，避免与浏览器内存分叉） */
async function cbProjOpenSession(s) { return nav.openSession(s); }

if ($("btnNewThread")) {
  /* 新建任务只在本地清屏：会话在第一条消息发出时由 /api/chat 建立，不再向服务端登记「线程」。
     （ux(round19) 的教训仍然成立：任何后端上这个键都不能是死键。） */
  $("btnNewThread").addEventListener("click", () => { cbNewLocalSession(); });
}
/* ux(round19)：并行任务逻辑从按钮里抽出来。原先 /bg 命令的实现是
   $("btnBg").click()，委托给按钮 —— 一旦按钮从界面移除，/bg 会变成静默空操作
   （有 && 守卫不报错、也不干活）。抽成函数后两条入口共用一份实现。 */
async function cbRunBackground(text) {
  const body = String(text || "").trim();
  if (!body) {
    addStatus("/bg 先写任务内容");
    return;
  }
  if (cbCapability("background_turns") === false) {
    addStatus("当前后端不支持并行任务（需要 background_turns 能力）");
    return;
  }
  const sid = cbSessionId();
  try {
    const r = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        message: body,
        background: true,
        session_id: sid,
        project_id: cbProj.cur || "",
        cad_project_id: state.cadProjectId || "",
        planning_project_id: state.planningProjectId || "",
        logistics_project_id: state.logisticsProjectId || "",
        expert_ids: [...state.summoned],
        confirm_ok: cbConfirmed(),
        confirm_text: cbConfirmText(),
      }),
    });
    if (!r.ok) throw new Error(await apiError(r) || "HTTP " + r.status);
    const data = await r.json();
    const started = data.session_id || sid;
    runState.background.add(started);
    addStatus(`并行任务已开始（会话 ${started.slice(0, 8)}），完成后会提示；随时可在左栏打开查看进度。`);
    await loadThreads();
    cbBgSchedule();
  } catch (e) {
    addStatus("并行任务未能开始：" + ((e && e.message) || e));
  }
}

async function handleSlash(message) {
  const parts = message.slice(1).split(/\s+/);
  const cmd = (parts[0] || "").toLowerCase();
  const arg = parts.slice(1).join(" ");
  if (cmd === "skills") {
    const data = await fetch("/api/skills").then((r) => r.json());
    const q = arg.toLowerCase();
    const rows = (data.skills || []).filter((s) => !q || `${s.name} ${s.description}`.toLowerCase().includes(q));
    addStatus(`${rows.length} skills`);
    addMsg("assistant", "skills", rows.slice(0, 20).map((s) => `$${s.name}  ${s.description}`).join("\n"));
    return true;
  }
  if (cmd === "new") {
    if ($("btnNewThread")) $("btnNewThread").click();
    else cbNewLocalSession();
    return true;
  }
  if (cmd === "bg") {
    const ta = $("input");
    if (ta) ta.value = "";
    await cbRunBackground(arg);
    return true;
  }
  if (cmd === "sandbox" || cmd === "approvals" || cmd === "approval") {
    const body = cmd === "sandbox" ? { sandbox: arg } : { approval: arg };
    if (arg) {
      await fetch("/api/config", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    }
    await loadPolicy();
    addStatus(`${cmd} ${arg || (cmd === "sandbox" ? state.policy.sandbox : state.policy.approval)}`);
    return true;
  }
  if (cmd === "threads") {
    await loadThreads();
    addStatus("threads 已刷新");
    return true;
  }
  if (cmd === "help") {
    addMsg("assistant", "help", "输入 / 打开快捷指令面板：/pack /bid /safety /audit /doc /eval\n客户端命令：/skills /new /bg /threads /sandbox /approvals\n全企业可问任意专家。确认句：我明白，将由持证人员签认");
    return true;
  }
  return false;
}

async function loadJobRoot() {
  try {
    const job = await fetch("/api/job").then((r) => r.json());
    /* ux(round19)：只记录授权状态，不再往界面写附件 chip、不再写对话流。
       原先这里两条 addStatus 是常驻噪音；且它 push 的 job: 前缀占位对象
       在 streamChat 里本来就被 filter 掉，从未真正传给模型。 */
    state.jobRoot = job && job.granted ? String(job.root || "") : "";
  } catch (e) {
    /* 没有 /api/job 的后端：静默 */
  }
}

async function reloadCatalog() {
  /* ux(round19)：66 岗名册不再常驻渲染（renderWall 已删），只留数据供召唤面板与
     @ 补全使用。catalog 拿不到时不阻断 —— cbPostsAll() 会回退 window.CB_POSTS。 */
  try {
    const cat = await fetch("/api/catalog").then((r) => r.json());
    state.catalog = cat;
    state.experts = cat.experts || [];
    if (window.studioOnCatalog) window.studioOnCatalog(cat);
  } catch (e) {
    /* 静默：离线兜底由 posts.js 承担 */
  }
  renderSummon();
  cbCapabilityCatalog();
}

window.reloadCatalog = reloadCatalog;

function toggle(id) {
  if (state.summoned.has(id)) state.summoned.delete(id);
  else state.summoned.add(id);
  renderSummon();
  refreshKb();
}

/* ux(round19) 按需召唤：只加不减。
   必须是 add 而不是 toggle —— @招标解析 打两次不能把自己取消掉。 */
function cbSummonAdd(id) {
  if (!id || state.summoned.has(id)) return;
  state.summoned.add(id);
  renderSummon();
  refreshKb();
}

function renderSummon() {
  document.querySelectorAll("[data-cb-post]").forEach((el) => {
    el.classList.toggle("on", state.summoned.has(el.dataset.cbPost));
  });
  const names = [...state.summoned].map((id) => {
    const e = state.experts.find((x) => x.id === id);
    return e ? `${e.category_name}/${e.name}` : id;
  });
  const bar = $("summonBar");
  if (!bar) return;
  /* ux(round19)：改为可 × 移除的岗位 chip；一个都没点名时整条隐藏 ——
     「当前：未点名岗位 · 直接下任务即可」这句常驻横幅消失，首屏干净一整条，
     该信息已由空态卡的 .cb-empty-desc「在下面输入任务」承担。
     × 是 U+00D7，test_ux_no_emoji 的 ALLOWED 显式豁免。 */
  bar.innerHTML = "";
  const ids = [...state.summoned];
  const clearBtn = $("clearExperts");
  if (!ids.length) {
    bar.hidden = true;
    if (clearBtn) clearBtn.hidden = true;
    return;
  }
  bar.hidden = false;
  if (clearBtn) clearBtn.hidden = false;
  for (const id of ids) {
    const e = state.experts.find((x) => x.id === id);
    const chip = document.createElement("span");
    chip.className = "cb-sum-chip";
    const details = document.createElement("button");
    details.type = "button";
    details.className = "cb-post-details";
    details.textContent = e ? `${e.category_name}/${e.name}` : id;
    details.setAttribute("aria-label", "查看 " + (e ? e.name : id) + " 的资料与工具");
    details.addEventListener("click", () => cbExpertCapability(id));
    chip.appendChild(details);
    const x = document.createElement("button");
    x.type = "button";
    x.className = "cb-sum-x";
    x.textContent = "×";
    x.setAttribute("aria-label", "取消点名 " + (e ? e.name : id));
    x.addEventListener("click", () => {
      state.summoned.delete(id);
      renderSummon();
      refreshKb();
    });
    chip.appendChild(x);
    bar.appendChild(chip);
  }
  if (clearBtn) bar.appendChild(clearBtn);
}

if ($("clearExperts")) {
  $("clearExperts").addEventListener("click", () => {
    state.summoned.clear();
    renderSummon();
    const kb = $("kblist");
    if (kb) kb.innerHTML = "";
  });
}

async function refreshKb() {
  const box = $("kblist");
  if (!box) return;
  box.innerHTML = "";
  for (const id of state.summoned) {
    const data = await fetch(`/api/kb/${id}`).then((r) => r.json());
    for (const f of data.files) {
      const li = document.createElement("li");
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "kb-link";
      const label = f.display || f.title || (f.path || "").split("/").pop();
      const layer = f.layer_label || layerName(f.layer);
      const sz = f.bytes != null ? ` · ${fmtBytes(f.bytes)}` : "";
      btn.innerHTML = `<span class="layer ${escapeHtml(f.layer)}">${escapeHtml(layer)}</span>${escapeHtml(label)}<span class="kb-size">${sz}</span>`;
      btn.title = f.path || "";
      btn.addEventListener("click", () => window.openStudio && window.openStudio(f.path, f.layer === "expert" ? id : null));
      li.appendChild(btn);
      box.appendChild(li);
    }
  }
}

function layerName(layer) {
  if (layer === "history") return "任务历史";
  if (layer === "expert") return "本岗知识";
  if (layer === "category") return "大类共享";
  if (layer === "web") return "网上检索";
  if (layer === "upload") return "用户上传";
  if (layer === "job") return "作业根";
  return "公司规则";
}

function fmtBytes(n) {
  const x = Number(n) || 0;
  if (x < 1024) return `${x} B`;
  if (x < 1024 * 1024) return `${(x / 1024).toFixed(1)} KB`;
  return `${(x / (1024 * 1024)).toFixed(2)} MB`;
}

function fmtNum(n) {
  return Number(n || 0).toLocaleString("zh-CN");
}

function isCjk(ch) {
  const c = ch.codePointAt(0);
  return (c >= 0x4e00 && c <= 0x9fff) || (c >= 0x3400 && c <= 0x4dbf) || (c >= 0xf900 && c <= 0xfaff);
}

function estimateTokens(text) {
  let cjk = 0;
  let other = 0;
  for (const ch of String(text || "")) {
    if (/\s/.test(ch)) continue;
    if (isCjk(ch)) cjk += 1;
    else other += 1;
  }
  return cjk + Math.ceil(other / 4);
}

function estimateLocalContext() {
  if (state.context.lastReport) return state.context.lastReport;
  const policy = state.context;
  const limit = policy.limit || 32768;
  const reserve = policy.reserve || 4096;
  const usable = Math.max(1, limit - reserve);
  let used = 0;
  for (const m of state.history) {
    used += estimateTokens(m.role) + estimateTokens(m.content) + 4;
  }
  const draft = $("input") ? $("input").value : "";
  if (draft) used += estimateTokens(draft) + 8;
  const pct = Math.min(100, Math.round((Math.min(used, usable) * 100) / usable));
  const compressAt = policy.compress_at || Math.floor((usable * (policy.compress_pct || 70)) / 100);
  const keep = policy.keep_recent || 4;
  let zone = "room";
  if (pct >= 90) zone = "full";
  else if (pct >= (policy.compress_pct || 70)) zone = "compact";
  else if (pct >= (policy.warn_pct || 50)) zone = "warn";
  const note = `编辑中：仅对话文字约 ${fmtNum(used)} token。发送时按完整请求安排记忆和来源，上限 ${fmtNum(limit)}，回答预留 ${fmtNum(reserve)}。`;
  return { used, limit, usable, pct, zone, note, estimated: true, compress_at: compressAt, keep_recent: keep };
}

function paintContext(ctx) {
  if (!ctx) return;
  if (ctx.components || ctx.mode === "local") state.context.lastReport = ctx;
  const bar = $("ctxBar");
  const fill = $("ctxFill");
  const text = $("ctxText");
  if (!bar || !fill || !text) return;
  const pct = Math.max(0, Math.min(100, Number(ctx.pct) || 0));
  fill.style.width = `${Math.max(pct, pct > 0 ? 2 : 0)}%`;
  bar.dataset.zone = ctx.zone || "room";
  const meter = bar.querySelector('[role="meter"]');
  if (meter) meter.setAttribute("aria-valuenow", String(pct));
  if (ctx.note) {
    text.textContent = ctx.note + (ctx.history_count != null ? ` · 历史 ${ctx.history_count} 条 · 找回 ${ctx.retrieved || 0} 段` : "");
  } else {
    text.textContent = `上下文 ${fmtNum(ctx.used)} / ${fmtNum(ctx.limit)} · ${pct}%`;
  }
  const semantic = ctx.semantic;
  if (semantic && typeof semantic === "object" && !Array.isArray(semantic)) {
    const parts = [];
    if (typeof semantic.note === "string" && semantic.note) parts.push(semantic.note);
    else if (typeof semantic.status === "string") parts.push("语义摘要状态：" + semantic.status);
    for (const [key, label, unit] of [["model_calls", "摘要调用", " 次"], ["input_tokens", "摘要输入估算", " token"],
      ["output_reserve", "摘要输出预留", " token"], ["covered_messages", "已处理历史", " 条"]]) {
      if (Number.isSafeInteger(semantic[key]) && semantic[key] >= 0) parts.push(label + " " + fmtNum(semantic[key]) + unit);
    }
    if (parts.length) text.textContent += " · " + parts.join(" · ");
  }
}

function addMsg(role, who, text) {
  const div = document.createElement("div");
  div.className = `msg ${role}`;
  div.innerHTML = `<div class="who">${escapeHtml(who)}</div><div class="body"></div>`;
  div.querySelector(".body").textContent = text;
  $("log").appendChild(div);
  $("log").scrollTop = $("log").scrollHeight;
  return div.querySelector(".body");
}

function addStatus(text) {
  const p = document.createElement("p");
  p.className = "status-line";
  p.textContent = text;
  $("log").appendChild(p);
  $("log").scrollTop = $("log").scrollHeight;
}

/* ===== ux(round7) 纠偏卡片（docs/ux/ux-design-spec.md 附录 F）=====
   错误/拒绝/缺数 → 「发生了什么 + 为什么(code) + 现在能做什么(≤3 动作)」的可行动卡片。
   分类/渲染逻辑 canonical 在 /static/fixcard.js；此处只注入端侧动作句柄：
   prefill=预填输入框草稿（不自动发送）· retry=重放同 payload · newsession=新开会话。 */
function cbFixHandlers() {
  return {
    prefill(v) {
      const input = $("input");
      if (!input) return;
      input.value = v || "";
      cbAutosize(input);
      input.focus();
    },
    retry() {
      const msg = state.lastSend;
      if (!msg) return;
      $("input").value = msg;
      cbAutosize($("input"));
      $("form").dispatchEvent(new Event("submit", { cancelable: true }));
    },
    newsession() {
      const btn = $("btnNewThread");
      if (btn) btn.click();
    },
  };
}

function cbFixMount(anchor, desc) {
  if (!anchor || !desc || typeof CB_FIX === "undefined") return null;
  const el = CB_FIX.cardEl(desc, cbFixHandlers());
  anchor.appendChild(el);
  const log = $("log");
  if (log) log.scrollTop = log.scrollHeight;
  return el;
}

$("form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  if (runState.active) return;
  if (runState.watched) {
    /* 服务端这一轮还占着会话，现在发只会得到 409，还会把正在轮询的结果顶掉。 */
    addStatus("这个任务还在后台运行：等它完成，或先点「停止」。输入内容已保留。");
    return;
  }
  if (cbContextRebuilding()) {
    const note = "正在重新整理记忆，请完成后发送；输入内容已保留。";
    if ($("ctxMemoryStatus")) $("ctxMemoryStatus").textContent = note;
    cbAnnounce(note);
    return;
  }
  if (cbCapability("chat") === false) {
    addStatus("当前工作台暂未提供任务运行能力，请检查服务状态。");
    return;
  }
  let message = $("input").value.trim();
  if (!message) return;
  cbSessionRequest += 1; // A new message takes precedence over pending navigation.
  /* ux(round9)：最近任务（点击重填的来源）+ /命令直达展开 */
  cbRecentPush(message);
  const navCmd = message.match(/^\/(audit|doc|eval)\s*$/);
  if (navCmd) {
    $("input").value = "";
    cbAutosize($("input"));
    cbSyncSend();
    cbCmdNav(navCmd[1]);
    return;
  }
  const expanded = cbSlashExpandMessage(message);
  if (expanded) {
    message = expanded;
    $("input").value = message;
  } else if (/^\/(pack|bid|safety)\s*$/.test(message)) {
    /* ux(round9)：模板命令未带参 → 用法提示（不发送） */
    addStatus("用法：/pack <票名>（如 /pack small_one_container）、/bid <要点>、/safety <要点>；或输入 / 从面板选。");
    return;
  }
  state.lastSend = message; /* ux(round7)：重试=重放同 payload */
  $("input").value = "";
  cbDraftClear();
  cbAutosize($("input"));
  cbSyncSend();
  cbAtClose();
  cbCmdClose();
  if (message.startsWith("/")) {
    cbHideWelcome();
    addMsg("user", "你", message);
    try {
      const ok = await handleSlash(message);
      if (!ok) addStatus(`未知命令 ${message}。/help`);
    } catch (err) {
      addStatus(String(err.message || err));
    }
    return;
  }
  cbHideWelcome();
  addMsg("user", "你", message);
  const cbDirect = cbDirectMatch(message);
  if (cbDirect) {
    await cbDirectRun(cbDirect);
    return;
  }
  state.history.push({ role: "user", content: message });
  paintContext(estimateLocalContext());
  const bodyEl = addMsg("assistant", namesOrPlain(), "");
  const run = { controller: new AbortController(), session: state.session, bodyEl };
  cbRememberSession(state.session);
  runState.active = run;
  cbRunPaint(true);
  try {
    await streamChat(message, bodyEl, run);
  } catch (err) {
    if (runState.active !== run) return;
    const stopped = err.name === "AbortError";
    const dropped = err.name === "StreamDroppedError";
    const raw = stopped ? "已停止接收回答。已有内容已保留。" : String(err.message || err);
    if (dropped) {
      /* 断流（锁屏 / 切 App / 网络切换）：服务端这一轮不会被取消，轮询拿回结果。 */
      const note = document.createElement("p");
      note.className = "status-line";
      note.textContent = raw;
      run.bodyEl.parentElement.appendChild(note);
      cbAnnounce("连接中断，正在恢复结果");
      runState.active = null;
      cbRunPaint(false);
      cbWatchSession(run.session, { bodyEl: run.bodyEl, reason: "连接曾中断；" });
      return;
    }
    // Preserve partial output so an interrupted connection does not erase work.
    const note = document.createElement("p");
    note.className = stopped ? "status-line" : "status-line err";
    note.textContent = raw;
    run.bodyEl.parentElement.appendChild(note);
    if (!stopped) cbFixMount(run.bodyEl.parentElement, typeof CB_FIX !== "undefined" ? CB_FIX.classify(raw, { retryable: true }) : null);
    cbAnnounce(stopped ? "已停止接收回答" : "本轮失败：请看纠偏卡的建议动作");
  } finally {
    if (runState.active === run) {
      runState.active = null;
      cbRunPaint(false);
    }
  }
});

if ($("stop")) $("stop").addEventListener("click", async () => {
  /* 有流的轮次，或只是在旁观的后台轮次：停止的都是服务端那一轮。 */
  const run = runState.active || runState.watched;
  if (!run) return;
  const current = () => runState.active === run || runState.watched === run;
  if (cbCapability("cancel") !== true) { if (run.controller) run.controller.abort(); return; }
  $("stop").disabled = true;
  $("stop").textContent = "停止中…";
  try {
    const result = await cbRequestCancellation(run);
    if (current() && result && result.cancel_requested) cbAnnounce("已请求停止，正在保存已有结果");
  } catch (error) {
    if (current()) {
      $("stop").disabled = false;
      $("stop").textContent = "停止";
      addStatus("停止请求未完成，请重试。" + String(error.message || error));
    }
  }
});

let cbBackupBusy = false;
function cbBackupPaint(busy) {
  cbBackupBusy = busy;
  for (const id of ["cbBackupExport", "cbBackupImport"]) {
    if ($(id)) $(id).disabled = busy || cbCapability("session_backup") === false;
  }
}

if ($("cbBackupExport")) $("cbBackupExport").addEventListener("click", async () => {
  if (cbBackupBusy) return;
  if (runState.active) { addStatus("请停止或等待本轮完成后备份任务。"); return; }
  const session = state.session;
  cbBackupPaint(true);
  try {
    /* 先问一次服务端状态：后台可能还有本会话的轮次在跑（断流 / 切走后继续），导出会 409。 */
    const probe = await fetch(`/api/sessions/${encodeURIComponent(session)}`);
    const detail = probe.ok ? await probe.json() : null;
    if (detail && detail.turn_state && detail.turn_state.active) {
      addStatus("这个任务仍在后台运行，请等待完成后再备份。");
      return;
    }
    /* 不再 fetch→blob→createObjectURL：128 MB 的包会整个进内存，手机上直接崩；
       服务端已带 Content-Disposition: attachment，直接让浏览器下载即可。 */
    const url = `/api/sessions/${encodeURIComponent(session)}/export`;
    const link = document.createElement("a");
    link.href = url;
    link.download = `civil-task-${session}.zip`;
    link.rel = "noopener";
    document.body.appendChild(link);
    link.click();
    link.remove();
    if (state.session === session) addStatus("已发起任务备份下载；请确认浏览器已保存 ZIP 文件。备份包含对话、上传资料和生成的文书。");
  } catch (error) { addStatus(String(error.message || error)); }
  finally { cbBackupPaint(false); }
});

if ($("cbBackupImport")) $("cbBackupImport").addEventListener("click", () => {
  if (cbBackupBusy) return;
  if (runState.active) { addStatus("请停止或等待本轮完成后导入任务。"); return; }
  $("cbBackupFile").click();
});

if ($("cbBackupFile")) $("cbBackupFile").addEventListener("change", async () => {
  const file = $("cbBackupFile").files[0];
  if (!file || cbBackupBusy) return;
  if (file.size > 128 * 1024 * 1024) { addStatus("备份包不能超过 128 MB。"); $("cbBackupFile").value = ""; return; }
  const request = cbSessionRequest;
  cbBackupPaint(true);
  try {
    const response = await fetch("/api/session-import", {
      method: "POST", headers: { "Content-Type": "application/zip" }, body: file,
    });
    if (!response.ok) throw new Error(await apiError(response) || "导入失败");
    const result = await response.json();
    await loadThreads();
    if (request === cbSessionRequest && !runState.active) {
      await cbProjOpenSession({ session_id: result.session_id, title: result.title });
      addStatus("已导入为新任务，原任务保持不变。可在左侧将它移动到工程项目；后续高风险操作需重新确认。");
    }
  } catch (error) { addStatus(String(error.message || error)); }
  finally { $("cbBackupFile").value = ""; cbBackupPaint(false); }
});

function skillWho(id, source) {
  if (!id) return "未点名岗位";
  const names = String(id).split(",").map((value) => value.trim()).filter(Boolean).map((value) => {
    const name = (state.experts.find((e) => e.id === value) || {}).name || value;
    return `$${value} · ${name}`;
  });
  const how = source === "given" ? "已点名" : source === "matched" ? "规则选用" : "当前岗位";
  return names.join(" / ") + " · " + how;
}

function namesOrPlain() {
  if (!state.summoned.size) return "未点名岗位";
  return [...state.summoned].map((id) => skillWho(id, "given")).join(" / ");
}

async function streamChat(message, bodyEl, run) { return turns.streamChat(message, bodyEl, run); }
async function cbAttachToTurn(sid, reason) { return turns.attachToTurn(sid, reason); }

/* 一轮回答（modules/turn-stream.js）：首次流 / 断线续流 / 切回来的回放共用一个处理器。
   它需要页面的这些手：都以闭包交出去，模块本身不读全局。 */
const turns = createTurnStream({
  state,
  run: {
    active: () => runState.active,
    setActive: (r) => { runState.active = r; },
    paint: (on) => cbRunPaint(on),
    releaseWatch: () => cbReleaseWatch(),
    watch: (sid, opts) => cbWatchSession(sid, opts),
    background: runState.background,
  },
  ui: {
    log: () => $("log"),
    addMsg: (role, who, text) => addMsg(role, who, text),
    addStatus: (text) => addStatus(text),
    announce: (text) => cbAnnounce(text),
    doc: document,
  },
  hitl: {
    confirmed: () => cbConfirmed(),
    typed: () => cbConfirmText(),
    clear: () => cbClearServerHitl(),
    enable: (data) => cbEnableServerHitl(data),
    pending: (data) => cbHitlPending(data),
  },
  turnUi: {
    tlCreate: (bodyEl, message) => cbTlCreate(bodyEl, message),
    routePaint: (route, bodyEl, message) => cbTaskRoutePaint(route, bodyEl, message),
    collaborationPaint: (data, bodyEl) => cbCollaborationPaint(data, bodyEl),
    obStep: (n) => cbObStep(n),
    paintContext: (data) => paintContext(data),
    estimateLocalContext: () => estimateLocalContext(),
    renderCites: (cites, bodyEl) => renderCites(cites, bodyEl),
    appendDocCards: (files, bodyEl, opts) => appendDocCards(files, bodyEl, opts),
    fixMount: (host, card) => cbFixMount(host, card),
    classifyMissing: (text) => (typeof CB_FIX !== "undefined" ? CB_FIX.classifyMissing(text) : null),
    refreshAuditSoon: () => refreshAuditSoon(),
    skillWho: (id, source) => skillWho(id, source),
    namesOrPlain: () => namesOrPlain(),
    setLastDeliverables: (files) => { cbLastDeliverables = files; },
    markdown: (bodyEl, text) => cbPaintMarkdown(bodyEl, text),
  },
  projectId: () => cbProj.cur || "",
  loadThreads: () => loadThreads(),
  apiError: (res) => apiError(res),
  capability: (name) => cbCapability(name),
  stream: { read: (body, onEvent, opts) => CB_CHAT_STREAM.read(body, onEvent, opts) },
  fetch: (url, init) => fetch(url, init),
  AbortController,
});

/* ux(round19)：「依据」从常驻右栏改为**跟着那条回答走**的流内卡片（附录 P）。
   依据本来就是某一条回答的产物，堆在右栏等于把它和上下文剥离。
   renderFiles 一并删除——它与 appendDocCards 渲染的是同一份 deliverables，纯重复。 */
function renderCites(cites, hostEl) {
  const list = Array.isArray(cites) ? cites : [];
  if (!list.length) return;
  const host = hostEl && hostEl.parentElement ? hostEl.parentElement : null;
  if (!host) return;
  const card = document.createElement("div");
  card.className = "cb-cite-card";
  const h = document.createElement("div");
  h.className = "cb-cite-h";
  h.textContent = `依据 ${list.length} 条`;
  card.appendChild(h);
  const ul = document.createElement("ul");
  for (const c of list) {
    const li = document.createElement("li");
    const title = c.display || c.title || (c.path || "").split("/").pop();
    const layer = c.layer_label || layerName(c.layer);
    li.title = c.path || "";
    li.innerHTML = `<span class="layer ${escapeHtml(c.layer)}">${escapeHtml(layer)}</span><b>${escapeHtml(title)}</b><br>${escapeHtml(c.snippet || c.path || "")}`;
    if (typeof c.url === "string" && c.url.startsWith("/api/context/source?")) {
      const sid = state.session;
      const navigation = cbSessionRequest;
      let readRequest = 0;
      const button = document.createElement("button");
      button.type = "button";
      button.className = "top-btn";
      button.textContent = `查看原文 · 字符 ${c.start}–${c.end}`;
      button.addEventListener("click", async () => {
        if (state.session !== sid || navigation !== cbSessionRequest || cbContextRebuilding()) return;
        const epoch = cbContextSourceEpoch;
        const request = ++readRequest;
        const current = () => state.session === sid && navigation === cbSessionRequest &&
          epoch === cbContextSourceEpoch && request === readRequest;
        button.disabled = true;
        try {
          const response = await fetch(c.url);
          if (!response.ok) throw new Error(await apiError(response));
          const data = await response.json();
          if (!current()) return;
          const pre = document.createElement("pre");
          pre.className = "cb-source-text";
          pre.textContent = data.text;
          li.appendChild(pre);
          button.hidden = true;
        } catch (error) { if (current()) button.textContent = `读取失败，点击重试：${error.message}`; }
        finally { if (request === readRequest) button.disabled = false; }
      });
      li.appendChild(button);
    }
    ul.appendChild(li);
  }
  card.appendChild(ul);
  host.appendChild(card);
}

function cbTaskText(value) {
  if (typeof value === "string") return value;
  if (!value || typeof value !== "object") return "";
  return String(value.text || value.note || value.label || value.title || value.field || JSON.stringify(value));
}

function cbTaskList(host, title, values) {
  const items = Array.isArray(values) ? values : [];
  if (!items.length) return;
  const heading = document.createElement("h4");
  heading.textContent = title;
  host.appendChild(heading);
  const list = document.createElement("ul");
  for (const item of items) {
    const li = document.createElement("li");
    li.textContent = cbTaskText(item);
    list.appendChild(li);
  }
  host.appendChild(list);
}

function cbTaskRoutePaint(route, bodyEl, message) {
  const host = bodyEl && bodyEl.parentElement;
  if (!host || !route || typeof route !== "object") return;
  let card = host.cbRouteCard;
  if (!card) {
    card = document.createElement("section");
    card.className = "cb-route-card";
    card.setAttribute("aria-label", "任务选择与步骤");
    host.cbRouteCard = card;
    host.appendChild(card);
  }
  card.replaceChildren();
  const heading = document.createElement("h4");
  heading.textContent = route.ambiguous ? "选择本次要处理的事项" : "本次任务安排";
  card.appendChild(heading);
  const reason = document.createElement("p");
  reason.textContent = route.reason || "按本次明确选择的岗位处理。";
  card.appendChild(reason);
  const steps = Array.isArray(route.steps) ? route.steps : [];
  if (steps.length > 1) {
    const names = new Map(steps.map(step => [step.id, step.label]));
    cbTaskList(card, "步骤与依赖", steps.map(step => step.label + (
      Array.isArray(step.depends_on) && step.depends_on.length
        ? " · 前置步骤：“" + step.depends_on.map(id => names.get(id) || id).join("、") + "”"
        : " · 起始步骤")));
  }
  if (route.ambiguous) {
    const sid = state.session;
    for (const candidate of Array.isArray(route.candidates) ? route.candidates : []) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "top-btn cb-route-choice";
      button.textContent = candidate.label + " · " + (candidate.reason || "按此岗位处理");
      button.addEventListener("click", () => {
        if (state.session !== sid) return;
        state.summoned = new Set((candidate.expert_ids || []).filter(id => typeof id === "string"));
        renderSummon();
        if ($("confirmOk")) $("confirmOk").value = "";
        $("input").value = message || "";
        cbAutosize($("input"));
        cbSyncSend();
        $("input").focus();
        cbAnnounce("已选择岗位，检查任务内容后发送。");
      });
      card.appendChild(button);
    }
  }
}

function cbCollaborationPaint(data, bodyEl) {
  const host = bodyEl && bodyEl.parentElement;
  if (!host || !data || typeof data !== "object") return;
  let card = host.cbCollaborationCard;
  if (!card) {
    card = document.createElement("section");
    card.className = "cb-collaboration-card";
    card.setAttribute("aria-label", "协作进度与证据");
    host.cbCollaborationCard = card;
    host.appendChild(card);
  }
  const snapshot = card.cbSnapshot || {};
  card.cbSnapshot = { ...snapshot, ...data };
  if (data.kind === "worker" && data.task_id) {
    card.cbSnapshot.state = snapshot.state || "running";
    const children = Array.isArray(snapshot.children) ? snapshot.children.slice() : [];
    const previous = children.find(child => child.task_id === data.task_id);
    if (previous) Object.assign(previous, { status: data.state, skill: data.skill || previous.skill });
    else children.push({ task_id: data.task_id, skill: data.skill, status: data.state });
    card.cbSnapshot.children = children;
  }
  const result = card.cbSnapshot;
  card.replaceChildren();
  const states = { pending: "待开始", parsing: "解析原文中", running: "处理中", done: "已完成",
    failed: "未完成", cancelled: "已停止", timed_out: "已超时", interrupted: "已中断", restored: "已导入历史记录",
    waiting_hitl: "等待本轮确认" };
  const heading = document.createElement("h4");
  heading.textContent = "协作进度 · " + (states[result.state] || "等待更新");
  card.appendChild(heading);
  for (const child of Array.isArray(result.children) ? result.children : []) {
    const details = document.createElement("details");
    const summary = document.createElement("summary");
    const expert = state.experts.find(item => item.id === child.skill);
    summary.textContent = (expert ? expert.name : child.skill || child.task_id || "子任务") + " · " + (states[child.status] || "待更新");
    details.appendChild(summary);
    cbTaskList(details, "结论", (child.conclusions || []).map(item => cbTaskText(item) + (item.origin === "model_analysis" ? "（模型分析，未核实）" : "")));
    cbTaskList(details, "来源证据", (child.evidence || []).map(item =>
      [item.title || item.source_id, item.source_id, item.quote].filter(Boolean).join(" · ")));
    cbTaskList(details, "未解决事项", child.unresolved);
    card.appendChild(details);
  }
  const review = result.review || {};
  cbTaskList(card, "汇总待核项", [...(Array.isArray(review.gaps) ? review.gaps : []), ...(Array.isArray(review.conflicts) ? review.conflicts : [])]);
  cbTaskList(card, "需修正的表述", review.forbidden_hits);
  if (Array.isArray(review.response_comparison) && review.response_comparison.length) {
    const heading = document.createElement("h4");
    heading.textContent = "招标要求与投标响应对照（待核验）";
    card.appendChild(heading);
    const table = document.createElement("table");
    const header = document.createElement("tr");
    for (const label of ["招标要求", "响应原文", "状态"]) {
      const th = document.createElement("th");
      th.scope = "col";
      th.textContent = label;
      header.appendChild(th);
    }
    table.appendChild(header);
    const statuses = { candidate_requires_review: "找到候选，待人工核验", conflict_requires_review: "数值与招标不一致，待人工核验",
      not_matched: "未匹配到响应", not_provided: "未提供响应资料" };
    for (const comparison of review.response_comparison) {
      const row = document.createElement("tr");
      for (const value of [comparison.requirement || comparison.requirement_ref,
        (comparison.response_evidence || []).map(evidence => [evidence.source_id, evidence.quote].filter(Boolean).join(" · ")).join("\n") || "—",
        [statuses[comparison.status] || "待核验", ...(comparison.conflicts || []).map(conflict => conflict.note)].filter(Boolean).join("\n")]) {
        const cell = document.createElement("td");
        cell.textContent = value;
        row.appendChild(cell);
      }
      table.appendChild(row);
    }
    card.appendChild(table);
  }
  if (result.submit_blocked === true || result.state === "done") {
    const note = document.createElement("p");
    note.textContent = "协作产出是内部讨论草稿，仍需人工核验与正式签认。";
    card.appendChild(note);
  }
  const metrics = result.aggregate_metrics || result.metrics;
  if (metrics && typeof metrics === "object") {
    const box = document.createElement("div");
    box.className = "cb-collaboration-budget";
    const label = document.createElement("p");
    const input = Number(metrics.input_tokens) || 0;
    const output = Number(metrics.output_estimated) || 0;
    const limit = Number(metrics.limit) || 0;
    label.textContent = `汇总预算${metrics.estimated ? "（估算）" : ""}：输入 ${fmtNum(input)} · 输出 ${fmtNum(output)}` +
      (limit > 0 ? ` · 总额度 ${fmtNum(limit)} · 总预留 ${fmtNum(metrics.reserved_tokens)}` : "") +
      (metrics.counter === "utf8-bytes" ? " · 按 UTF-8 字节保守估算" : "");
    box.appendChild(label);
    if (limit > 0) {
      const meter = document.createElement("meter");
      meter.min = 0;
      meter.max = limit;
      meter.value = Math.min(limit, input + output);
      meter.setAttribute("aria-label", "协作汇总预算使用量");
      box.appendChild(meter);
    }
    card.appendChild(box);
  }
}

function cbCapabilityCatalog() {
  const select = $("cbCapabilityPost");
  if (!select) return;
  const selected = select.value;
  select.replaceChildren();
  const prompt = document.createElement("option");
  prompt.value = "";
  prompt.textContent = "请选择岗位";
  select.appendChild(prompt);
  for (const expert of state.experts) {
    const option = document.createElement("option");
    option.value = expert.id;
    option.textContent = (expert.category_name ? expert.category_name + " / " : "") + expert.name;
    select.appendChild(option);
  }
  select.value = selected;
}

async function cbExpertCapability(id) {
  const box = $("cbCapabilityBody");
  if (!box || !id) return;
  const request = ++cbCapabilityRequest;
  cbDockSet(true, true);
  if ($("cbCapabilityPost")) $("cbCapabilityPost").value = id;
  box.replaceChildren();
  box.textContent = "正在读取岗位资料…";
  box.setAttribute("aria-busy", "true");
  try {
    const response = await fetch("/api/experts/" + encodeURIComponent(id) + "/capability");
    if (response.status === 404) throw new Error("本岗没有内置专用工具契约；自定义岗位请查看其用户 SOP，不借用其他岗位能力。");
    if (!response.ok) throw new Error(await apiError(response));
    const data = await response.json();
    if (request !== cbCapabilityRequest) return;
    if (!data || data.expert_id !== id) throw new Error("岗位能力数据不完整");
    box.textContent = "";
    const heading = document.createElement("h4");
    heading.textContent = data.name + (data.risk === "high" ? " · 成稿需人工确认" : "");
    box.appendChild(heading);
    cbTaskList(box, "开始前需要的资料", data.inputs);
    cbTaskList(box, "本岗工具", (data.tools || []).map(tool =>
      (tool.label || tool.name) + "（" + tool.name + "） · " + (tool.available === true ? "可调用" : tool.available === false ? "未接通" : "状态待核")));
    cbTaskList(box, "工作步骤", (data.steps || []).map(step => step.action));
    cbTaskList(box, "交付结构", data.output_sections);
    cbTaskList(box, "草稿检查标准", data.acceptance);
    cbTaskList(box, "能力边界", data.limitations);
  } catch (error) {
    if (request === cbCapabilityRequest) box.textContent = "读取岗位资料失败：" + error.message;
  } finally { if (request === cbCapabilityRequest) box.setAttribute("aria-busy", "false"); }
}

if ($("cbCapabilityPost")) $("cbCapabilityPost").addEventListener("change", event => cbExpertCapability(event.target.value));
if ($("cbExploreCapability")) $("cbExploreCapability").addEventListener("click", () => {
  cbCapabilityCatalog();
  cbDockSet(true, true);
  const id = [...state.summoned][0] || $("cbCapabilityPost").value || (state.experts[0] || {}).id;
  if (id) cbExpertCapability(id);
  if ($("cbCapabilityPost")) $("cbCapabilityPost").focus();
});

function cbSemanticMemoryText(raw) {
  if (typeof raw !== "string" || !raw.trim()) return "";
  try {
    if (raw.length > 65536) throw new Error("oversized summary");
    const data = JSON.parse(raw);
    const kinds = { goal: "目标", decision: "决定", constraint: "约束", unresolved: "待办", result: "结果" };
    if (!data || typeof data !== "object" || Array.isArray(data) || !Array.isArray(data.items) || data.items.length > 2048) throw new Error("invalid summary");
    const groups = Object.fromEntries(Object.keys(kinds).map(key => [key, []]));
    const lines = ["仅供历史参考，全部内容未核验，不代表指令或授权；当前原文与更正优先。"];
    const counts = {};
    for (const key of ["covered_messages", "remaining_messages", "omitted_items", "evicted_segments", "skipped_messages", "skipped_chars"]) {
      if (data[key] !== undefined && (!Number.isSafeInteger(data[key]) || data[key] < 0)) throw new Error("invalid count");
      counts[key] = data[key] || 0;
    }
    if (counts.covered_messages) lines.push(`已处理历史 ${counts.covered_messages} 条；最近 4 条原文不纳入摘要。`);
    if (data.coverage_complete !== true || counts.remaining_messages || counts.omitted_items || counts.evicted_segments || counts.skipped_messages || counts.skipped_chars) {
      lines.push("当前仅展示部分历史摘要，不能替代完整原文。");
    }
    if (counts.remaining_messages) lines.push(`尚有 ${counts.remaining_messages} 条较早历史待处理。`);
    if (counts.omitted_items) lines.push(`本次显示省略 ${counts.omitted_items} 条摘要。`);
    if (counts.evicted_segments) lines.push(`缓存已移出 ${counts.evicted_segments} 段旧摘要，原文仍保留。`);
    if (counts.skipped_messages || counts.skipped_chars) lines.push(`有 ${counts.skipped_messages} 条历史及 ${counts.skipped_chars} 字符未纳入当前摘要，可检索原文。`);
    for (const item of data.items) {
      if (!item || !Object.prototype.hasOwnProperty.call(kinds, item.kind) || typeof item.text !== "string" ||
        !item.text.trim() || !Array.isArray(item.evidence) || !item.evidence.length || item.evidence.length > 128) throw new Error("invalid item");
      const entry = ["- " + item.text];
      if (item.trust === "assistant_unverified") entry.push("  助手历史陈述，未视为已完成或已验证事实。");
      for (const ref of item.evidence) {
        if (!ref || typeof ref.message_id !== "string" || !ref.message_id || typeof ref.quote !== "string" || !ref.quote ||
          !Number.isSafeInteger(ref.start) || !Number.isSafeInteger(ref.end) || ref.start < 0 || ref.end <= ref.start) throw new Error("invalid evidence");
        entry.push(`  来源消息 ${ref.message_id} · 字符 ${ref.start}–${ref.end}（左闭右开）`);
        entry.push("  原文：“" + ref.quote + "”");
      }
      groups[item.kind].push(entry.join("\n"));
    }
    for (const [key, label] of Object.entries(kinds)) {
      if (groups[key].length) lines.push("\n" + label + "（未核验）\n" + groups[key].join("\n\n"));
    }
    if (!data.items.length) lines.push("当前没有可显示的摘要条目，请核对规则记忆或搜索原文。");
    return lines.join("\n");
  } catch (_) {
    return "模型语义摘要暂不可用，请刷新后重试；规则记忆与原文仍可查看。";
  }
}

function cbContextRebuilding() {
  return cbContextRebuildRun && cbContextRebuildRun.session === state.session &&
    cbContextRebuildRun.navigation === cbSessionRequest;
}

function cbContextRebuildControls(busy) {
  for (const id of ["ctxRebuild", "ctxRefresh", "ctxSearch"]) {
    if ($(id)) $(id).disabled = busy;
  }
  if ($("ctxRebuild")) $("ctxRebuild").textContent = busy ? "正在整理…" : "重新整理记忆";
}

function cbContextDetailPaint(data) {
  const box = $("ctxMemory");
  if (box) {
    box.textContent = typeof data.memory_text === "string" ? data.memory_text : "当前任务尚无可显示的记忆。";
    const semanticText = cbSemanticMemoryText(data.semantic_memory_text);
    if (semanticText) box.textContent += "\n\n模型语义摘要（未核验）\n" + semanticText;
  }
  const notes = [data.note, data.memory_status && data.memory_status.note, data.semantic_status && data.semantic_status.note]
    .filter(note => typeof note === "string" && note.trim());
  if ($("ctxMemoryStatus")) $("ctxMemoryStatus").textContent = [...new Set(notes)].join("\n");
  if (data.context && (data.context.note || Number(data.context.limit) > 0)) paintContext(data.context);
}

async function cbContextLoad() {
  if (cbContextRebuilding()) return;
  const sid = state.session;
  const navigation = cbSessionRequest;
  const request = ++cbContextRequest;
  const current = () => state.session === sid && navigation === cbSessionRequest && request === cbContextRequest;
  const box = $("ctxMemory");
  if (!box) return;
  box.textContent = "正在读取任务记忆…";
  box.setAttribute("aria-busy", "true");
  try {
    const response = await fetch("/api/context?session_id=" + encodeURIComponent(sid));
    if (!response.ok) throw new Error(await apiError(response));
    const data = await response.json();
    if (!current()) return;
    cbContextDetailPaint(data);
  } catch (error) {
    if (current()) box.textContent = `读取失败：${error.message}`;
  } finally { if (current()) box.setAttribute("aria-busy", "false"); }
}

async function cbContextRebuild() {
  if (cbContextRebuilding()) return;
  const status = $("ctxMemoryStatus");
  const box = $("ctxMemory");
  if (!box || !status) return;
  if (runState.active && runState.active.session === state.session) {
    status.textContent = "任务正在处理中，请结束后再重新整理记忆。";
    return;
  }
  const run = { session: state.session, navigation: cbSessionRequest };
  cbContextRebuildRun = run;
  const current = () => cbContextRebuildRun === run && state.session === run.session && cbSessionRequest === run.navigation;
  cbContextRequest += 1;
  cbContextSearchRequest += 1;
  cbContextSourceEpoch += 1;
  cbContextRebuildControls(true);
  box.setAttribute("aria-busy", "true");
  status.textContent = "正在从原文重新整理记忆和本地搜索，不会调用模型…";
  if ($("ctxSearchStatus")) $("ctxSearchStatus").textContent = "";
  try {
    const response = await fetch("/api/context/rebuild", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: run.session }) });
    if (!response.ok) throw new Error(await apiError(response));
    const data = await response.json();
    if (!current()) return;
    if (!data || data.ok !== true || typeof data.memory_text !== "string") throw new Error("服务未返回有效的重建结果，请重新查看任务记忆。");
    if ($("ctxResults")) $("ctxResults").replaceChildren();
    state.context.lastReport = null;
    cbContextDetailPaint(data);
    if (!data.context || !(data.context.note || Number(data.context.limit) > 0)) paintContext(estimateLocalContext());
    if (!status.textContent) status.textContent = "任务记忆已重新整理；原始对话、附件和交付物已保留。";
    cbAnnounce("任务记忆已重新整理，原始资料已保留。");
  } catch (error) {
    if (current()) {
      status.textContent = "重新整理失败：" + error.message;
      if (box.textContent === "正在读取任务记忆…") box.textContent = "可重新查看任务记忆；原始对话、附件和交付物仍保留。";
    }
  } finally {
    if (current()) {
      cbContextRebuildRun = null;
      cbContextRebuildControls(false);
      box.setAttribute("aria-busy", "false");
    }
  }
}

async function cbContextSearch() {
  if (cbContextRebuilding()) return;
  const query = $("ctxQuery").value.trim();
  if (!query) return;
  const sid = state.session;
  const navigation = cbSessionRequest;
  const request = ++cbContextSearchRequest;
  const current = () => state.session === sid && navigation === cbSessionRequest && request === cbContextSearchRequest;
  const status = $("ctxSearchStatus");
  const result = $("ctxResults");
  const button = $("ctxSearch");
  result.replaceChildren();
  button.disabled = true;
  status.textContent = "正在本机检索…";
  try {
    const response = await fetch("/api/context/search", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: sid, query, attachments: state.attachments.filter(a => !String(a.id).startsWith("job:")).map(a => a.id) }) });
    if (!response.ok) throw new Error(await apiError(response));
    const data = await response.json();
    if (!current()) return;
    status.textContent = data.citations.length ? `找到 ${data.citations.length} 个原文片段。` : "没有找到匹配内容。可换用原文关键词，并检查附件是否已选择。";
    const body = document.createElement("div");
    result.appendChild(body);
    renderCites(data.citations, body);
  } catch (error) {
    if (current()) status.textContent = `检索失败：${error.message}`;
  } finally { if (current()) button.disabled = false; }
}

if ($("ctxOpen")) $("ctxOpen").addEventListener("click", () => {
  cbDockSet(true, true);
  cbContextLoad();
  $("ctxQuery").focus();
});
if ($("ctxRefresh")) $("ctxRefresh").addEventListener("click", cbContextLoad);
if ($("ctxRebuild")) $("ctxRebuild").addEventListener("click", cbContextRebuild);
if ($("ctxSearch")) $("ctxSearch").addEventListener("click", cbContextSearch);
if ($("ctxQuery")) $("ctxQuery").addEventListener("keydown", event => {
  if (event.key === "Enter" && !event.isComposing) { event.preventDefault(); cbContextSearch(); }
});

function fileUrl(f, nameOverride) { return deliverables.fileUrl(f, nameOverride); }
async function openDeliverable(f) { return deliverables.openDeliverable(f); }
function appendDocCards(files, bodyEl, opts) { return deliverables.appendDocCards(files, bodyEl, opts); }

/* 交付物（modules/deliverables.js）：按引用的下载链接、回答下方的文书卡片、预览。 */
const deliverables = createDeliverables({
  state,
  capability: (name) => cbCapability(name),
  obStep: (n) => cbObStep(n),
  addStatus: (text) => addStatus(text),
  openDoc: (spec) => window.cbDocOpenUrl(spec),
  relTime: (ts) => cbRelTime(ts),
  log: () => $("log"),
  doc: document,
});

async function apiError(res) {
  const t = await res.text();
  try {
    const j = JSON.parse(t);
    if (typeof j.detail === "string") return j.detail;
    if (Array.isArray(j.detail)) return j.detail.map((d) => d.msg || JSON.stringify(d)).join("; ");
  } catch (_) {}
  return t || res.statusText;
}

function escapeHtml(s) {
  return String(s)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

if ($("input")) {
  $("input").addEventListener("input", () => paintContext(estimateLocalContext()));
}

/* ux(round19)：本地文件入口整体拆除（附件 chip / 上传 / 本机路径导入 / 成套投标 /
   拖拽上传）。拖拽监听原先挂在 .composer 上、独立于「上传」按钮 —— 只删按钮不删它，
   界面上看不见但拖个文件进输入区照样静默 POST /api/upload，所以一并移除。
   服务端 /api/upload、/api/local、/api/firm/bid 路由保留，只是不再有界面入口。 */

/* === ux(round2) 输入体验（docs/ux/ux-design-spec.md R2：一个输入框） ============
   模式借鉴 pattern-only，不抄任何代码：
   - openai/codex codex-rs/tui/src/bottom_pane/chat_composer.rs（Apache-2.0）：
     单输入框按键先路由给活动浮层；补全=替换光标处 @token、尾部留一个空格；
     Esc 关闭浮层并记住被关闭的 token，编辑后才允许重开。
   - open-webui src/lib/components/chat/MessageInput.svelte（MIT）：
     compositionstart/end IME 守卫（中文输入法回车选词不误发，Safari 时序 200ms）；
     发送按钮随内容空态禁用；textarea autosize=height:auto→scrollHeight 钳制。
   - Discord/Slack @提及（pattern-only）：↑↓ 选择、Enter/Tab 确认、Esc 关闭、悬停即选中。 */
const CB_MAX_LINES = 8;

function cbAutosize(ta) {
  // 1→8 行伸缩，超长内部滚动
  if (!ta) return;
  ta.style.height = "auto";
  const cs = getComputedStyle(ta);
  const line = parseFloat(cs.lineHeight) || parseFloat(cs.fontSize) * 1.4 || 20;
  const pad = (parseFloat(cs.paddingTop) || 0) + (parseFloat(cs.paddingBottom) || 0);
  const max = line * CB_MAX_LINES + pad;
  ta.style.height = Math.min(ta.scrollHeight, max) + "px";
  ta.style.overflowY = ta.scrollHeight > max ? "auto" : "hidden";
}

function cbAtQuery(text, cursor) {
  // 光标左侧 @token：行首/空白起头，@ 后可跟中文/字母/数字/连字符/下划线
  const before = String(text || "").slice(0, cursor);
  const m = before.match(/(^|\s)@([\u4e00-\u9fa5A-Za-z0-9_-]*)$/);
  if (!m) return null;
  return { start: before.length - m[2].length - 1, query: m[2] };
}

function cbAtFilter(posts, query, limit) {
  // 66 岗按 name/aliases/id 过滤：前缀命中优先于包含命中，默认最多 8 条
  const q = String(query || "").trim().toLowerCase();
  const scored = [];
  for (const p of posts || []) {
    const names = [p.name].concat(p.aliases || [], [p.id]).filter(Boolean).map((n) => String(n).toLowerCase());
    let score = -1;
    if (!q) score = 2;
    else if (names.some((n) => n.indexOf(q) === 0)) score = 0;
    else if (names.some((n) => n.includes(q))) score = 1;
    if (score >= 0) scored.push([score, p]);
  }
  scored.sort((a, b) => a[0] - b[0]);
  return scored.slice(0, limit || 8).map((x) => x[1]);
}

function cbAtApply(ta, start, post) {
  // codex token 替换模式：@token → 「@岗位名 」，光标落在尾部空格之后
  const insert = "@" + post.name + " ";
  const tail = ta.value.slice(ta.selectionEnd);
  ta.value = ta.value.slice(0, start) + insert + tail;
  const pos = start + insert.length;
  ta.focus();
  ta.setSelectionRange(pos, pos);
}

function cbComposeGuard(el) {
  // IME 守卫：组合输入中的 Enter=选词不发送；结束后 200ms 内的 Enter 也忽略
  const st = { on: false, endedAt: 0 };
  el.addEventListener("compositionstart", () => { st.on = true; });
  el.addEventListener("compositionend", () => { st.on = false; st.endedAt = Date.now(); });
  st.block = function (ev) {
    return st.on || (ev && (ev.isComposing || ev.keyCode === 229)) || Date.now() - st.endedAt < 200;
  };
  return st;
}

/* === ux(round9) 快捷指令面板：/ 命令 + 常用任务模板（docs/ux/ux-design-spec.md 附录 H） ===
   借鉴 pattern-only，不抄任何代码：
   - openai/codex slash_command.rs / command_popup.rs（Apache-2.0）：输入 / 弹命令浮层、
     按名过滤、每条=命令名+一句描述、别名不在面板重复出现；Esc 关闭并记住被关的 token。
   - VS Code Command Palette（文档 pattern-only）：模糊过滤必须带排序——前缀命中 > 中文子串
     命中 > 子序列命中，相关度高的在上；全程键盘可达（↑↓/Enter/Tab/Esc）。
   - Raycast Arguments/Snippets（文档 pattern-only）：参数用 <待填> 占位提示；Esc 在子菜单=返回上一级。
   - GitHub Saved replies / Issue templates（文档 pattern-only）：选中=插入带占位的模板草稿，
     先改后发，绝不自动发送（承附录 F.3「预填不自动发送」红线）。 */
const CB_RECENT_KEY = "cb_recent_tasks_v1";

function cbRecentList() {
  try {
    const rows = JSON.parse(localStorage.getItem(CB_RECENT_KEY) || "[]");
    return Array.isArray(rows) ? rows.filter((r) => r && r.t) : [];
  } catch (e) {
    return [];
  }
}

function cbRecentPush(text) {
  const t = String(text || "").trim();
  if (!t || t.length > 200) return;
  const rows = cbRecentList().filter((r) => r.t !== t);
  rows.unshift({ t, ts: Date.now() });
  try {
    localStorage.setItem(CB_RECENT_KEY, JSON.stringify(rows.slice(0, 8)));
  } catch (e) { /* 隐私模式等存储不可用：最近任务静默缺席 */ }
}

/* === ux(round10) 空态卡 + 三步引导 checklist + 网关兜底（docs/ux/ux-design-spec.md 附录 I） ===
   借鉴 pattern-only，不抄任何代码：
   - shadcn/ui Empty / Tailwind UI empty state（MIT / 文档）：图标 + 一句话定位 + 主动作，不堆字。
   - PostHog / Appcues 首访 checklist（文档 pattern-only）：3 步、逐项打勾、全完成自动收起、右上角 ? 重开。
   - openai/codex 首启欢迎（Apache-2.0）：单一 composer 聚焦；示例卡=预填草稿不自动发送（承附录 F.3 红线）。 */
const CB_ONBOARD_KEY = "cb_onboarded_v1";
const CB_ONBOARD_STEPS = [
  { t: "输入任务，或点一张示例卡（预填，不自动发送）" },
  { t: "看时间线跑完：8 阶段收口" },
  { t: "键入完整签认句后确认 · 或预览 / 下载文书" },
];

function cbOnboardLoad() {
  try {
    const v = JSON.parse(localStorage.getItem(CB_ONBOARD_KEY) || "null");
    if (v && Array.isArray(v.s) && v.s.length === 3) {
      return { s: v.s.map(Boolean), done: !!v.done, dismissed: !!v.dismissed };
    }
  } catch (e) { /* 存储不可用：当作首访 */ }
  return { s: [false, false, false], done: false, dismissed: false };
}

function cbOnboardSave(st) {
  try {
    localStorage.setItem(CB_ONBOARD_KEY, JSON.stringify(st));
  } catch (e) { /* 存储不可用：本轮内存态即可 */ }
}

const cbOb = cbOnboardLoad();

function cbObStepsLeft() {
  return cbOb.s.filter((x) => !x).length;
}

function cbObStep(n) {
  if (n < 1 || n > 3 || cbOb.s[n - 1]) return;
  cbOb.s[n - 1] = true;
  if (!cbObStepsLeft()) cbOb.done = true;
  cbOnboardSave(cbOb);
  cbOnboardRender();
  if (cbOb.done) {
    /* 全部完成 → 自动收起（PostHog checklist 语义）；? 可随时重开 */
    setTimeout(() => { cbOb.dismissed = true; cbOnboardSave(cbOb); cbOnboardRender(); }, 1200);
  }
}

function cbOnboardRender() {
  const box = $("cbOnboard");
  if (!box) return;
  const show = !cbOb.dismissed && !(cbOb.done && cbOb.s.every(Boolean) && cbOb.dismissed);
  if (!show) { box.hidden = true; box.textContent = ""; return; }
  box.hidden = false;
  box.textContent = "";
  const head = document.createElement("div");
  head.className = "cb-ob-head";
  const title = document.createElement("strong");
  title.textContent = cbOb.done ? "三步引导 · 已完成" : "三步上手";
  const sub = document.createElement("span");
  sub.className = "cb-ob-sub";
  sub.textContent = cbOb.done ? "任何时候点右上角 ? 重看" : "第一次用？跟着走 30 秒";
  const x = document.createElement("button");
  x.type = "button";
  x.className = "cb-ob-x";
  x.setAttribute("aria-label", "收起新手引导");
  x.title = "收起（不再自动弹出）";
  x.textContent = "关闭";
  x.addEventListener("click", () => {
    cbOb.dismissed = true;
    cbOnboardSave(cbOb);
    cbOnboardRender();
  });
  head.append(title, sub, x);
  const ol = document.createElement("ol");
  ol.className = "cb-ob-steps";
  CB_ONBOARD_STEPS.forEach((s, i) => {
    const li = document.createElement("li");
    if (cbOb.s[i]) li.className = "on";
    else if (i === cbOb.s.indexOf(false)) li.className = "now";
    const dot = document.createElement("span");
    dot.className = "cb-ob-dot"; /* round14：完成态=CSS 实心绿点，不写字符 */
    dot.setAttribute("aria-hidden", "true");
    const t = document.createElement("span");
    t.className = "cb-ob-t";
    t.textContent = "①②③"[i] + " " + s.t;
    li.append(dot, t);
    ol.appendChild(li);
  });
  const foot = document.createElement("div");
  foot.className = "cb-ob-foot";
  foot.textContent = "产出永远是 AI 草稿，高风险岗需人工确认 · 全部完成自动收起";
  box.append(head, ol, foot);
}

function cbOnboardReopen() {
  cbOb.dismissed = false;
  cbOnboardRender();
  const box = $("cbOnboard");
  if (box && box.scrollIntoView) box.scrollIntoView({ block: "nearest" });
}

/* 示例任务卡：预填草稿，不自动发送（附录 F.3）；/pack 用真实小票 small_one_container */
function cbEmptyPrefill(id) {
  const ta = $("input");
  if (!ta) return;
  let text = "";
  const samples = {
    skills: "@施工方案 你能做什么？",
    daily: "@项目日报 写一份项目日报模板；项目、日期、人数与进度均未提供，缺失项保持待填，不编造完成情况。",
    "bid-guide": "@招标解析 说明你能帮我检查哪些招标响应项，以及开始前需要哪些材料。",
    "tender-review": "根据当前选择的附件，综合检查投标响应：先解析招标原文，再整理技术响应与响应缺口，汇总证据和未解决事项。未提供的内容保持待填，不判断可以投标。",
    "backup-plan": "帮我制定备份策略；按系统整理数据分级、备份周期、介质与恢复演练记录，未提供的 RPO、RTO 和实测结果保持待填。",
    "steel-note": "帮我写钢构说明草稿；项目地区、荷载、跨度与图号尚未提供，请列待补资料，不选择构件尺寸。",
  };
  if (samples[id]) {
    text = samples[id];
    state.summoned.clear();
    if ($("confirmOk")) $("confirmOk").value = "";
    renderSummon();
  } else if (id === "pack") {
    const t = (window.CB_TICKETS || []).find((x) => x.id === "small_one_container") || null;
    text = cbSlashTemplate("pack", "", t ? { xlsx: t.xlsx, story: t.story } : {});
  } else {
    text = cbSlashTemplate(id, "");
  }
  ta.value = text;
  cbAutosize(ta);
  cbSyncSend();
  ta.focus();
  ta.setSelectionRange(ta.value.length, ta.value.length);
  cbObStep(1);
}

function cbBrowsePosts() {
  const ta = $("input");
  if (!ta) return;
  if (!cbSlashQuery(ta.value, ta.selectionStart)) {
    if (ta.value && !/\s$/.test(ta.value)) ta.value += " ";
    ta.value += "/";
    ta.selectionStart = ta.selectionEnd = ta.value.length;
  }
  cbAtClose();
  cbCmd.mode = "cats";
  cbCmd.dismissed = null;
  cbCmd.dismissedAt = null;
  cbAutosize(ta);
  cbSyncSend();
  cbCmdUpdate();
  ta.focus();
}

/* 网关兜底空态：/api/health 不可达 → 纠偏卡（发生了什么 + 现在能做什么，命令一键复制） */
function cbEmptyDownShow(err) {
  const log = $("log");
  if (!log || $("cbDownCard")) return;
  const card = document.createElement("div");
  card.className = "cb-empty-down";
  card.id = "cbDownCard";
  card.setAttribute("role", "alert");
  const h = document.createElement("p");
  const kb = document.createElement("strong");
  kb.className = "cb-empty-k";
  kb.textContent = "工作台后端未启动或不可达";
  h.appendChild(kb);
  h.appendChild(document.createTextNode(" —— 界面在，但任务发不出去（" + String(err && err.message || err || "fetch failed") + "）。"));
  const p1 = document.createElement("p");
  p1.textContent = "现在能做什么：在仓库根目录启动工作台（或双击 zip 内 start-workbench.bat），然后点「重试检测」。";
  const code = document.createElement("code");
  code.textContent = "cargo run --release --bin civil-workbench";
  const acts = document.createElement("div");
  acts.className = "cb-empty-acts";
  const copyBtn = document.createElement("button");
  copyBtn.type = "button";
  copyBtn.textContent = "复制启动命令";
  copyBtn.addEventListener("click", async () => {
    try {
      if (navigator.clipboard && navigator.clipboard.writeText) await navigator.clipboard.writeText(code.textContent);
      else throw new Error("no clipboard");
      copyBtn.textContent = "已复制";
    } catch (e) {
      copyBtn.textContent = "复制失败，请手选";
    }
    setTimeout(() => { copyBtn.textContent = "复制启动命令"; }, 1600);
  });
  const retry = document.createElement("button");
  retry.type = "button";
  retry.textContent = "重试检测";
  retry.addEventListener("click", async () => {
    retry.textContent = "检测中…";
    try {
      if (await boot()) { card.remove(); addStatus("后端已恢复 · 工作台状态已更新"); return; }
      throw new Error("工作台尚未恢复");
    } catch (e) {
      retry.textContent = "仍未启动";
      setTimeout(() => { retry.textContent = "重试检测"; }, 1600);
    }
  });
  acts.append(copyBtn, retry);
  card.append(h, p1, code, acts);
  log.prepend(card);
}

function cbSlashQuery(text, cursor) {
  // 光标左侧 /token：行首/空白起头，/ 后可跟字母/数字/中文/连字符/下划线（与 @token 同构）
  const before = String(text || "").slice(0, cursor);
  const m = before.match(/(^|\s)\/([A-Za-z0-9\u4e00-\u9fa5_-]*)$/);
  if (!m) return null;
  return { start: before.length - m[2].length - 1, query: m[2] };
}

function cbSlashFilter(items, query, limit) {
  // 模糊过滤+排序：前缀命中(0) > 中文子串命中(1) > 子序列命中(3)，空查询=原序全量
  const q = String(query || "").trim().toLowerCase();
  const scored = [];
  for (const it of items || []) {
    const names = [it.id].concat(it.aliases || [], [it.name]).filter(Boolean).map((n) => String(n).toLowerCase());
    let score = -1;
    if (!q) score = 2;
    else if (names.some((n) => n.indexOf(q) === 0)) score = 0;
    else if (names.some((n) => n.includes(q))) score = 1;
    else if (cbSubsequence(q, names)) score = 3;
    if (score >= 0) scored.push([score, it]);
  }
  scored.sort((a, b) => a[0] - b[0]);
  return scored.slice(0, limit || 9).map((x) => x[1]);
}

function cbSubsequence(q, names) {
  // 任意 name 包含 q 的全部字符且按 q 的顺序出现（宽容模糊层，排在子串命中之后）
  return names.some((n) => {
    let i = 0;
    for (const ch of String(n)) {
      if (ch === q[i]) i += 1;
      if (i >= q.length) return true;
    }
    return false;
  });
}

/* ===== ux(round17) 模型设置面板 =====
   目的：评委/试用者用自己的 Key 就能跑，界面里切 DeepSeek / z.ai 等 OpenAI 兼容供应商，
   不必改 demo/.env 再重启进程。
   边界：Key 只 POST 给同源工作台，存进程内存（config.rs RUNTIME_LLM）——不写盘、不进
   localStorage、不进日志；GET 只回首尾各 4 位掩码，永不回明文。浏览器**从不**直连供应商，
   /chat/completions 由 Rust 侧 reqwest 发出，故不破 R12 断网红线（见该门禁 EXEMPT_URLS 理由）。 */
const CB_LLM_VENDORS = {
  deepseek: {
    base: "https://api.deepseek.com",
    models: ["deepseek-flash", "deepseek-v4-pro"],
  },
  zai: {
    base: "https://api.z.ai/api/paas/v4",
    models: ["glm-5.3", "glm-5.2", "glm-4.7", "GLM-4.7-Flash"],
  },
  openai: {
    base: "https://api.openai.com/v1",
    models: ["gpt-4o-mini", "gpt-4o"],
  },
  custom: { base: "", models: [] },
};

function cbLlmVendorOf(base) {
  const b = String(base || "").toLowerCase();
  if (b.indexOf("deepseek") !== -1) return "deepseek";
  if (b.indexOf("z.ai") !== -1) return "zai";
  if (b.indexOf("openai.com") !== -1) return "openai";
  return "custom";
}

function cbLlmFillModels(vendor) {
  const dl = $("cbLlmModels");
  if (!dl) return;
  dl.innerHTML = "";
  for (const m of (CB_LLM_VENDORS[vendor] || CB_LLM_VENDORS.custom).models) {
    const o = document.createElement("option");
    o.value = m;
    dl.appendChild(o);
  }
}

let cbLlmBusy = false;

function cbLlmSetBusy(busy) {
  cbLlmBusy = busy;
  for (const id of ["cbLlmVendor", "cbLlmBase", "cbLlmModel", "cbLlmKey", "cbLlmContext", "cbLlmReserve", "cbLlmSemantic", "cbLlmSave", "cbLlmReset"]) {
    if ($(id)) $(id).disabled = busy;
  }
  if ($("cbLlm")) $("cbLlm").setAttribute("aria-busy", String(busy));
}

function cbLlmNormalize(cfg) {
  if (!cfg || typeof cfg.configured !== "boolean" || typeof cfg.model !== "string" || typeof cfg.base_url !== "string") {
    throw new Error("工作台返回的模型配置不完整，请检查服务版本。");
  }
  let base = "";
  if (cfg.base_url) {
    const url = new URL(cfg.base_url);
    url.username = "";
    url.password = "";
    url.search = "";
    url.hash = "";
    base = url.toString().replace(/\/$/, "");
  }
  const masked = typeof cfg.key_masked === "string" && /^[^*\s]{0,4}\*+[^*\s]{0,4}$/.test(cfg.key_masked)
    ? cfg.key_masked : "已隐藏";
  const semantic = cfg.semantic_summary !== undefined ? cfg.semantic_summary : cfg.context && cfg.context.semantic_summary;
  if (semantic !== undefined && typeof semantic !== "boolean") throw new Error("工作台返回的语义摘要设置无效。");
  return { configured: cfg.configured, model: cfg.model, base_url: base, key_masked: masked, source: cfg.source, context: cfg.context,
    semantic_summary: semantic === true };
}

function cbLlmError(error, secret) {
  let text = String(error && error.message || error);
  if (secret) text = text.split(secret).join("[已隐藏]");
  return text.replace(/sk-[A-Za-z0-9_-]+/g, "[已隐藏]");
}

async function cbLlmRequest(payload) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 8000);
  try {
    const response = await fetch("/api/llm-config", {
      signal: controller.signal,
      ...(payload === undefined ? {} : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) }),
    });
    if (!response.ok) {
      if (response.status === 404) throw new Error("当前工作台未提供模型设置，可继续使用已支持的岗位功能。");
      throw new Error(await apiError(response) || "HTTP " + response.status);
    }
    try { return await response.json(); }
    catch (_) {
      const error = new Error("工作台返回了无法读取的模型配置。");
      error.submitted = payload !== undefined;
      throw error;
    }
  } catch (error) {
    if (error.name === "AbortError") throw new Error("请求超时，请检查本地工作台状态。");
    throw error;
  } finally { clearTimeout(timeout); }
}

function cbLlmPaint(cfg, note, tone) {
  const st = $("cbLlmStatus");
  if (!st) return;
  st.className = "cb-llm-status" + (tone ? " " + tone : "");
  if (!cfg) { st.textContent = note || "读取失败：工作台未响应。"; return; }
  const src = cfg.source === "runtime" ? "本次运行（界面设置）" : "启动配置";
  st.textContent = (note ? note + "。" : "") + (cfg.configured
    ? "当前：" + cfg.model + " · " + cfg.base_url + " · Key " + cfg.key_masked + " · 来源 " + src
    : "尚未配置开放式问答模型（来源 " + src + "）。填写后点「保存并生效」，无需重启。");
}

function cbLlmApply(cfg, note) {
    const vendor = cbLlmVendorOf(cfg.base_url);
    if ($("cbLlmVendor")) $("cbLlmVendor").value = vendor;
    cbLlmFillModels(vendor);
    if ($("cbLlmBase")) $("cbLlmBase").value = cfg.base_url || "";
    if ($("cbLlmModel")) $("cbLlmModel").value = cfg.model || "";
    if (cfg.context) {
      state.context = { ...state.context, ...cfg.context, lastReport: null };
      if ($("cbLlmContext")) $("cbLlmContext").value = cfg.context.limit;
      if ($("cbLlmReserve")) $("cbLlmReserve").value = cfg.context.reserve;
      paintContext(estimateLocalContext());
    }
    state.context.semantic_summary = cfg.semantic_summary === true;
    if ($("cbLlmSemantic")) $("cbLlmSemantic").checked = cfg.semantic_summary === true;
    if ($("cbLlmKey")) $("cbLlmKey").value = "";
    cbLlmPaint(cfg, note, note ? "ok" : "");
    cbApplyHealth({ has_key: cfg.configured, deepseek: cfg.configured, model: cfg.model,
      mode: cfg.configured ? "configured" : cbCapability("chat") === true ? "offline" : "" });
}

async function cbLlmLoad() {
  if (cbLlmBusy) return null;
  cbLlmSetBusy(true);
  cbLlmPaint(null, "正在读取模型配置…");
  if ($("cbLlmKey")) $("cbLlmKey").value = "";
  try {
    const cfg = cbLlmNormalize(await cbLlmRequest());
    cbLlmApply(cfg);
    return cfg;
  } catch (e) {
    cbLlmPaint(null, "读取失败：" + cbLlmError(e), "err");
    return null;
  } finally { cbLlmSetBusy(false); }
}

async function cbLlmSubmit(payload, okNote) {
  if (cbLlmBusy) return;
  cbLlmSetBusy(true);
  cbLlmPaint(null, payload.clear ? "正在恢复启动配置…" : "正在保存模型配置…");
  const secret = String(payload.api_key || "");
  if ($("cbLlmKey")) $("cbLlmKey").value = "";
  let submitted = false;
  try {
    await cbLlmRequest(payload);
    submitted = true;
    const cfg = cbLlmNormalize(await cbLlmRequest());
    cbLlmApply(cfg, okNote);
  } catch (e) {
    const prefix = submitted || e.submitted ? "设置已提交，但当前配置未能核对，请重新打开设置：" : "保存失败：";
    cbLlmPaint(null, prefix + cbLlmError(e, secret), "err");
  } finally { cbLlmSetBusy(false); }
}

function cbLlmOpen() {
  const box = $("cbLlm");
  if (!box) return;
  box.classList.remove("hidden");
  box.setAttribute("aria-hidden", "false");
  cbLlmLoad(null, null);
  const first = $("cbLlmVendor");
  if (first) first.focus();
}

function cbLlmClose() {
  const box = $("cbLlm");
  if (!box) return;
  box.classList.add("hidden");
  box.setAttribute("aria-hidden", "true");
  if ($("cbLlmKey")) $("cbLlmKey").value = "";
}

function cbLlmWire() {
  const open = $("cbLlmOpen");
  if (open) open.addEventListener("click", cbLlmOpen);
  if ($("cbEmptyModel")) $("cbEmptyModel").addEventListener("click", cbLlmOpen);
  const close = $("cbLlmClose");
  if (close) close.addEventListener("click", cbLlmClose);
  const box = $("cbLlm");
  if (box) {
    box.addEventListener("click", (e) => { if (e.target === box) cbLlmClose(); });
  }
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && box && !box.classList.contains("hidden")) cbLlmClose();
  });
  const vendor = $("cbLlmVendor");
  if (vendor) {
    vendor.addEventListener("change", () => {
      const v = vendor.value;
      cbLlmFillModels(v);
      const preset = CB_LLM_VENDORS[v] || CB_LLM_VENDORS.custom;
      if (preset.base) $("cbLlmBase").value = preset.base;
      if (preset.models.length) $("cbLlmModel").value = preset.models[0];
    });
  }
  const save = $("cbLlmSave");
  if (save) {
    save.addEventListener("click", () => {
      cbLlmSubmit({
        api_key: $("cbLlmKey").value,
        base_url: $("cbLlmBase").value,
        model: $("cbLlmModel").value,
        context_limit: Number($("cbLlmContext").value),
        output_reserve: Number($("cbLlmReserve").value),
        semantic_summary: !!$("cbLlmSemantic").checked,
      }, "模型已切换");
    });
  }
  const reset = $("cbLlmReset");
  if (reset) {
    reset.addEventListener("click", () => cbLlmSubmit({ clear: true }, "已恢复启动配置"));
  }
}

cbLlmWire();

/* ===== ux(round15) 装箱直达（docs/ux/ux-design-spec.md 附录 M）=====
   整条输入即"装箱"类词 → 打开装柜台 3D 工程台（成箱 → 人确认 → 拼柜/重心）。
   只在 trim 后整条等于触发词时接管；句中含"装箱"的正常提问（如"帮我装箱一下"）
   仍走 pack-ship 专家问答，不抢话。统一工作台通过同源 /packing 连接装箱引擎；
   不可达时保留入口与重试提示。 */
const CB_PACK_PAGE_ORIGIN = globalThis.location && globalThis.location.origin || "";
let CB_PACK_STUDIO_ORIGIN = CB_PACK_PAGE_ORIGIN;
let CB_PACK_STUDIO_PATH = "/packing";
const CB_PACK_STUDIO_CMD = "python scripts/start_unified_workbench.py";
const CB_PACK_LEGACY_ORIGIN = "http://127.0.0.1:8000";
const CB_PACK_OPEN_WORDS = ["装箱", "装柜", "拼柜", "装箱拼柜", "装箱作业单", "装柜台"];

/* ux(round16) 直达词泛化：不止装箱，CB_SLASH 每一项都能整条直达。
   词源 = 该项的 name + aliases（与 / 面板同一份口径，杜绝两处漂移）+ 少量口语补充。 */
const CB_DIRECT_EXTRA = {
  pack: ["装箱", "装柜台"],
  doc: ["交付物"],
  eval: ["记分卡"],
};

function cbDirectTable() {
  const map = new Map();
  for (const it of CB_SLASH) {
    const words = [it.name].concat(it.aliases || [], CB_DIRECT_EXTRA[it.id] || []);
    for (const w of words) if (w && !map.has(w)) map.set(w, it);
  }
  return map;
}

/* 精确匹配：trim + 去尾部中英标点后整条命中才算
   （"装箱。""交底 " 算；"帮我装箱""交底怎么写" 不算——不抢正常提问） */
function cbDirectNorm(text) {
  return String(text || "").trim().replace(/[\s。．.!！?？，,]+$/, "");
}

function cbDirectMatch(text) {
  return cbDirectTable().get(cbDirectNorm(text)) || null;
}

/* 保留：pack 专用谓词（round15 起对外语义不变，ci.yml ux_marks 亦断言此名） */
function cbIsPackOpenWord(text) {
  const it = cbDirectMatch(text);
  return !!it && it.id === "pack";
}

/* 直达执行：复用面板既有动作，不另起一套
   pack → 开装柜台；nav → 就地打开面板；tpl/client → 预填草稿不自动发送（附录 F.3 红线） */
async function cbDirectRun(it) {
  if (it.id === "pack") {
    await cbOpenPackStudio();
    return;
  }
  if (it.kind === "nav") {
    addStatus("直达：" + it.name);
    cbCmdNav(it.id);
    return;
  }
  if (it.sub === "cats") {
    cbBrowsePosts();
    return;
  }
  addStatus("直达：" + it.name + " —— 模板已填进输入框，改完再按发送（不自动发送）。");
  cbCmdApplyDraft(cbSlashTemplate(it.id, ""));
}

/* 使用统一工作台的同源装箱健康探针；超时当不可达，不阻塞 UI。 */
async function cbPackStudioUp(ms) {
  const ctl = new AbortController();
  const timer = setTimeout(() => ctl.abort(), ms || 2500);
  try {
    const r = await fetch("/packing/api/health", { signal: ctl.signal });
    if (r.status !== 404) {
      CB_PACK_STUDIO_ORIGIN = CB_PACK_PAGE_ORIGIN;
      CB_PACK_STUDIO_PATH = "/packing";
      return r.ok;
    }
    // Only a missing unified route permits the legacy standalone gateway fallback.
    CB_PACK_STUDIO_ORIGIN = CB_PACK_LEGACY_ORIGIN;
    CB_PACK_STUDIO_PATH = "/workbench";
    const legacy = await fetch(CB_PACK_LEGACY_ORIGIN + "/api/health", { signal: ctl.signal });
    return legacy.ok;
  } catch (e) {
    return false;
  } finally {
    clearTimeout(timer);
  }
}

/* 兼容旧服务的安装探针。缺少 python_root 只表示当前服务未发现装箱组件，
   不能据此断言发布包不包含装箱引擎。统一预览包已携带该组件。 */
let CB_PACK_TRIAL = null;

/* 旧探针判据：http.up=false 且 python_root 为空 = 当前服务未发现可用引擎。
   注意不能用 packing_agent.connected —— url_configured() 有默认值故它恒为 true。 */
function cbPackTrialFrom(h) {
  const pa = h && h.packing_agent;
  if (!pa) return null;
  return !(pa.http && pa.http.up) && !pa.python_root;
}

async function cbPackTrialProbe(ms) {
  const ctl = new AbortController();
  const timer = setTimeout(() => ctl.abort(), ms || 2000);
  try {
    const h = await fetch("/api/health", { signal: ctl.signal }).then((r) => r.json());
    return cbPackTrialFrom(h);
  } catch (e) {
    return null;
  } finally {
    clearTimeout(timer);
  }
}

function cbPackCardRender(up) {
  const url = CB_PACK_STUDIO_ORIGIN + CB_PACK_STUDIO_PATH;
  const card = document.createElement("div");
  card.className = up ? "cb-pack-ok" : "cb-empty-down";
  card.id = "cbPackCard";
  if (!up) card.setAttribute("role", "alert");

  const h = document.createElement("p");
  const k = document.createElement("strong");
  k.className = "cb-empty-k";
  const trial = !up && CB_PACK_TRIAL === true;
  k.textContent = up ? "装柜台已打开" : (trial ? "装箱组件未就绪" : "装柜台未启动");
  h.appendChild(k);
  h.appendChild(document.createTextNode(up
    ? " —— 成箱 → 人确认 → 拼柜 3D / 重心，都在这一页。"
    : (trial
      ? " —— 当前服务未检测到可用装箱组件，请检查是否完整解压了统一工作台。"
      : " —— 装箱引擎暂不可达，入口和已有材料仍保留。")));
  card.appendChild(h);

  const p1 = document.createElement("p");
  p1.textContent = up
    ? "装箱方案会先供你检查；确认后才继续拼柜。也可用下面的链接打开完整工作台。"
    : (trial
      ? "现在能做什么：完整解压预览包，按照包内 README 的「统一入口」启动，然后点「重试」。"
      : (CB_PACK_STUDIO_PATH === "/workbench"
        ? "现在能做什么：当前是旧版独立工作台。启动装箱网关后点「重试」，或按照预览包 README 使用统一入口。"
        : "现在能做什么：按预览包 README 的「统一入口」重新启动，它会一并启动装箱服务，然后点「重试」。"));
  card.appendChild(p1);

  const acts = document.createElement("div");
  acts.className = "cb-empty-acts";

  if (!up && trial) {
    const retry = document.createElement("button");
    retry.type = "button";
    retry.textContent = "重试";
    retry.addEventListener("click", () => { cbOpenPackStudio(); });
    acts.appendChild(retry);
    card.appendChild(acts);
    return card;
  }

  if (up) {
    const a = document.createElement("a");
    a.href = url;
    a.target = "_blank";
    a.rel = "noopener";
    a.textContent = "打开装柜台";
    acts.appendChild(a);
  } else {
    const code = document.createElement("code");
    const startCommand = CB_PACK_STUDIO_PATH === "/workbench"
      ? "uvicorn gateway.app:app --host 127.0.0.1 --port 8000" : CB_PACK_STUDIO_CMD;
    code.textContent = startCommand;
    card.appendChild(code);
    const copyBtn = document.createElement("button");
    copyBtn.type = "button";
    copyBtn.textContent = "复制启动命令";
    copyBtn.addEventListener("click", async () => {
      try {
        if (navigator.clipboard && navigator.clipboard.writeText) await navigator.clipboard.writeText(startCommand);
        else throw new Error("no clipboard");
        copyBtn.textContent = "已复制";
      } catch (e) {
        copyBtn.textContent = "复制失败，请手选";
      }
      setTimeout(() => { copyBtn.textContent = "复制启动命令"; }, 1600);
    });
    acts.appendChild(copyBtn);
    const retry = document.createElement("button");
    retry.type = "button";
    retry.textContent = "重试";
    retry.addEventListener("click", () => { cbOpenPackStudio(); });
    acts.appendChild(retry);
  }

  card.appendChild(acts);
  return card;
}

/* 打开装柜台：先探健康，通了才开新标签（避免弹出一个连接拒绝页） */
/* ux(round19)：把装柜台以面板形式嵌进对话流，不跳走。 */
function cbPackEmbed() {
  const log = $("log");
  if (!log) return;
  const old = document.getElementById("cbPackEmbed");
  if (old && old.parentElement) old.parentElement.removeChild(old);
  const box = document.createElement("div");
  box.id = "cbPackEmbed";
  box.className = "cb-pack-embed";

  const head = document.createElement("div");
  head.className = "cb-pack-embed-h";
  const t = document.createElement("strong");
  t.textContent = "装柜台";
  const sub = document.createElement("span");
  sub.textContent = "成箱 → 人确认 → 拼柜 3D / 重心";
  const acts = document.createElement("div");
  acts.className = "cb-pack-embed-acts";
  const max = document.createElement("button");
  max.type = "button";
  max.className = "top-btn";
  max.textContent = "放大";
  max.addEventListener("click", () => {
    const on = box.classList.toggle("is-max");
    max.textContent = on ? "还原" : "放大";
  });
  const hide = document.createElement("button");
  hide.type = "button";
  hide.className = "top-btn";
  hide.textContent = "收起";
  hide.addEventListener("click", () => box.remove());
  acts.append(max, hide);
  head.append(t, sub, acts);
  box.appendChild(head);

  const frame = document.createElement("iframe");
  frame.className = "cb-pack-frame";
  frame.title = "装柜台 3D 工程台";
  frame.loading = "lazy";
  frame.referrerPolicy = "no-referrer";
  frame.setAttribute(
    "sandbox",
    "allow-scripts allow-same-origin allow-downloads allow-forms allow-popups"
  );
  frame.src = CB_PACK_STUDIO_ORIGIN + CB_PACK_STUDIO_PATH;
  let loaded = false;
  frame.addEventListener("load", () => { loaded = true; });
  box.appendChild(frame);
  log.appendChild(box);
  log.scrollTop = log.scrollHeight;
  cbAnnounce("装柜台已在对话内打开");

  /* 探针通了但 iframe 仍白（被上级策略拦等）：4 秒兜底换成链接，不留白框 */
  setTimeout(() => {
    if (loaded || !box.isConnected) return;
    frame.remove();
    const p1 = document.createElement("p");
    p1.className = "cb-pack-embed-fb";
    p1.textContent = "内嵌视图未能加载。用下面的链接在新标签打开：";
    const a = document.createElement("a");
    a.href = CB_PACK_STUDIO_ORIGIN + CB_PACK_STUDIO_PATH;
    a.target = "_blank";
    a.rel = "noopener";
    a.textContent = "打开装柜台";
    box.append(p1, a);
  }, 4000);
}

async function cbOpenPackStudio() {
  const prev = document.getElementById("cbPackCard");
  if (prev && prev.parentElement) prev.parentElement.removeChild(prev);
  addStatus("正在连接装箱拼柜工作台…");
  const up = await cbPackStudioUp(2500);
  /* CB_PACK_TRIAL 已由 boot 的 health 填好；仅当那次没拿到（如 Python 参考实现无该字段，
     或 boot 时 health 失败）才现场补探一次。 */
  /* 先落卡片再开标签：部分浏览器/内嵌视图把 window.open 当原地跳转，
     后落卡片会随原页面一起被替换，回到本页什么痕迹都没有。 */
  const log = $("log");
  if (log) {
    log.appendChild(cbPackCardRender(up));
    log.scrollTop = log.scrollHeight;
  }
  if (up) {
    /* 装柜台以同源面板嵌入对话。allow-same-origin 让装箱页可以请求其代理 API；
       原有「打开装柜台」链接保留作完整页面入口。 */
    cbPackEmbed();
    return;
  }
  /* 试用判别放在落卡之后异步改写：真 exe 冷启动期 /api/health 要跑引擎探针（spawn_blocking），
     可能好几秒才回；若把它挡在渲染前面，用户按下回车会长时间无任何反馈（实测冷启动即复现）。
     先给通用「未启动」卡，探针回来再就地换成试用文案。 */
  if (CB_PACK_TRIAL !== null) return; /* boot 已判定，卡片文案已正确，无需改写 */
  const trial = await cbPackTrialProbe(2000);
  if (trial !== true) return;
  CB_PACK_TRIAL = true;
  const cur = document.getElementById("cbPackCard");
  if (cur && cur.parentElement) cur.parentElement.replaceChild(cbPackCardRender(false), cur);
}

/* 命令注册表（两端同构镜像；图标=--cb-* 色块+单字，不引图标库） */
const CB_SLASH = [
  /* ux(round19)：66 岗从左栏常驻名册改为按需召唤，入口收进这台已有的两级面板。
     cbDirectTable() 按 name+aliases 建表，于是整条输入「岗位」「召唤」也直达。 */
  { id: "at", ch: "岗", tone: "gray", name: "点名岗位", aliases: ["岗位", "召唤", "专家"],
    desc: "按大类挑岗（66 岗）", sub: "cats" },
  { id: "pack", ch: "装", tone: "blue", name: "装箱拼柜", aliases: ["装柜", "拼柜", "装箱作业单"],
    desc: "选 sim_materials 票，预填装柜任务", sub: "tickets" },
  { id: "bid", ch: "标", tone: "strong", name: "招标解析", aliases: ["招标", "解析招标"],
    desc: "预填招标解析模板（@招标解析）", kind: "tpl" },
  { id: "safety", ch: "安", tone: "orange", name: "安全交底", aliases: ["交底", "班前", "白话交底"],
    desc: "预填班前白话交底模板", kind: "tpl" },
  { id: "audit", ch: "审", tone: "red", name: "审计面板", aliases: ["审计", "时间线"],
    desc: "打开跨运行审计时间线", kind: "nav" },
  { id: "doc", ch: "文", tone: "green", name: "最近交付物", aliases: ["文书", "预览"],
    desc: "预览最近一份交付物文书", kind: "nav" },
  { id: "eval", ch: "评", tone: "gray", name: "记分卡摘要", aliases: ["评测", "自检"],
    desc: "竞赛记分卡 / 离线自检", kind: "nav" },
];

/* 模板填空：<待填> 占位提示（Raycast arguments placeholder 模式），数字一概不预编 */
function cbSlashTemplate(id, arg, ticket) {
  const a = String(arg || "").trim();
  if (id === "pack") {
    const t = ticket || {};
    const path = t.xlsx || "test/sim_materials/<票名>/materials.xlsx";
    return "pack " + path + (t.story ? "（" + t.story + "）" : "") +
      "\n要求：40HQ 高利用率装柜；柜数与坐标由 tools 计算，模型不摆箱子；出装柜单草稿，须人工确认后才拼柜。";
  }
  if (id === "bid") {
    return "@招标解析 解析这份招标文件：\n项目名称：<待填>\n关键条款：<待填：工期 / 资质 / 报价上限>" +
      (a ? "\n原文要点：" + a : "") +
      "\n请列出资格条件与废标项清单；P0 资格须人工确认，是否投、怎么投由人决定。";
  }
  if (id === "safety") {
    return "@安全交底 写一份班前白话交底：\n作业内容：<待填>\n主要风险与防护：<待填>" +
      (a ? "\n补充：" + a : "") +
      "\n给工友的白话版，一条一个动作；先讨论，说「写一份」才出草稿。";
  }
  return a;
}

/* 老手直达："/pack <票名>"、"/bid <要点>"、"/safety <要点>" 直接展开成任务文本；
   nav 类命令（/audit /doc /eval）与未带参的 /pack 返回 null，由调用方拦截处理 */
function cbSlashExpandMessage(message) {
  const m = String(message || "").match(/^\/(pack|bid|safety)(?:\s+([\s\S]+))?$/);
  if (!m) return null;
  const arg = (m[2] || "").trim();
  if (m[1] === "pack") {
    if (!arg) return null;
    const t = (typeof window !== "undefined" && window.CB_TICKETS || []).find((x) => x.id === arg);
    return cbSlashTemplate("pack", "", t ? { xlsx: t.xlsx, story: t.story } : { xlsx: arg });
  }
  return cbSlashTemplate(m[1], arg);
}

/* ---- :8765 实例接线：@岗位补全 + Enter 语义 + 空态禁用 ---- */
const cbAt = { open: false, items: [], idx: 0, start: -1, query: "", dismissed: "", dismissedAt: -1 };
let cbSyncSend = () => {};

function cbPostsSource() {
  return state.experts && state.experts.length ? state.experts : window.CB_POSTS || [];
}

function cbAtClose() {
  cbAt.open = false;
  const menu = $("atMenu");
  if (menu) menu.hidden = true;
}

function cbAtRender() {
  const menu = $("atMenu");
  if (!menu) return;
  menu.innerHTML = "";
  cbAt.items.forEach((p, i) => {
    const row = document.createElement("div");
    row.className = "cb-at-item" + (i === cbAt.idx ? " on" : "");
    row.setAttribute("role", "option");
    row.setAttribute("aria-selected", i === cbAt.idx ? "true" : "false");
    const name = document.createElement("span");
    name.className = "cb-at-name";
    name.textContent = "@" + p.name;
    const cat = document.createElement("span");
    cat.className = "cb-at-cat";
    cat.textContent = (p.category_name || "") + (p.id ? " · " + p.id : "");
    row.appendChild(name);
    row.appendChild(cat);
    row.addEventListener("mousedown", (ev) => {
      ev.preventDefault();
      cbAt.idx = i;
      cbAtConfirm();
    });
    row.addEventListener("mouseenter", () => {
      cbAt.idx = i;
      cbAtRender();
    });
    menu.appendChild(row);
  });
  menu.hidden = false;
}

function cbAtUpdate() {
  const ta = $("input");
  if (!ta) return cbAtClose();
  const tok = cbAtQuery(ta.value, ta.selectionStart);
  if (!tok || (cbAt.dismissed === tok.query && cbAt.dismissedAt === tok.start)) return cbAtClose();
  const items = cbAtFilter(cbPostsSource(), tok.query);
  if (!items.length) return cbAtClose();
  cbAt.open = true;
  cbAt.items = items;
  cbAt.idx = 0;
  cbAt.start = tok.start;
  cbAt.query = tok.query;
  cbAtRender();
}

/* ux(round19)：@ 补全确认时**顺带真正点名**。
   既有语义缺口：cbAtApply 只往 textarea 插文本、不写 state.summoned，于是 @ 完
   #summonBar 仍显示「未点名岗位」，真实路由靠服务端 match_skill_implicit 兜底。
   补上后 expert_ids 显式带出，前后端口径一致，行为不变（api.rs:704 给了就用）。 */
function cbAtConfirm() {
  const ta = $("input");
  const post = cbAt.items[cbAt.idx];
  cbAtClose();
  if (!ta || !post) return;
  cbAtApply(ta, cbAt.start, post);
  cbSummonAdd(post.id);
  cbAutosize(ta);
  cbSyncSend();
}

function cbAtMove(d) {
  if (!cbAt.items.length) return;
  cbAt.idx = (cbAt.idx + d + cbAt.items.length) % cbAt.items.length;
  cbAtRender();
}

function cbComposerInit() {
  const ta = $("input");
  const form = $("form");
  if (!ta || !form) return;
  const guard = cbComposeGuard(ta);
  cbSyncSend = () => {
    const btn = $("send");
    if (btn && !btn.dataset.running) btn.disabled = !ta.value.trim() || cbCapability("chat") === false;
  };
  ta.addEventListener("input", () => {
    cbAutosize(ta);
    cbSyncSend();
    cbAtUpdate();
    cbCmdUpdate();
  });
  /* ux(round14) 输入条左「+」按钮：展开 /快捷指令面板（复用 U-R9 浮层，不自动发送） */
  const plus = $("composerPlus");
  if (plus) {
    plus.addEventListener("click", () => {
      const tok = cbSlashQuery(ta.value, ta.selectionStart);
      if (!tok) {
        if (ta.value && !/\s$/.test(ta.value)) ta.value += " ";
        ta.value += "/";
        ta.selectionStart = ta.selectionEnd = ta.value.length;
        cbAutosize(ta);
      }
      cbCmd.dismissed = null;
      cbCmd.dismissedAt = null;
      cbCmdUpdate();
      ta.focus();
    });
  }
  ta.addEventListener("keydown", (ev) => {
    if (cbCmd.open) {
      if (ev.key === "ArrowDown") { ev.preventDefault(); cbCmdMove(1); return; }
      if (ev.key === "ArrowUp") { ev.preventDefault(); cbCmdMove(-1); return; }
      if (ev.key === "Enter" || ev.key === "Tab") { ev.preventDefault(); cbCmdConfirm(); return; }
      if (ev.key === "Escape") { ev.preventDefault(); cbCmdEsc(); return; }
    }
    if (cbAt.open) {
      if (ev.key === "ArrowDown") { ev.preventDefault(); cbAtMove(1); return; }
      if (ev.key === "ArrowUp") { ev.preventDefault(); cbAtMove(-1); return; }
      if (ev.key === "Enter" || ev.key === "Tab") { ev.preventDefault(); cbAtConfirm(); return; }
      if (ev.key === "Escape") {
        ev.preventDefault();
        cbAt.dismissed = cbAt.query;
        cbAt.dismissedAt = cbAt.start;
        cbAtClose();
        return;
      }
    }
    const send = ev.key === "Enter" && !ev.shiftKey && !ev.ctrlKey && !ev.metaKey && !ev.altKey && !guard.block(ev);
    if (send) {
      ev.preventDefault();
      if (form.requestSubmit) form.requestSubmit();
      else form.dispatchEvent(new Event("submit", { cancelable: true }));
    }
  });
  cbAutosize(ta);
  cbSyncSend();
}
cbComposerInit();

/* ---- :8765 实例接线：ux(round9) 快捷指令面板（与 cbAt 同一浮层体系） ---- */
const cbCmd = { open: false, mode: "cmds", cat: "", items: [], idx: 0, start: -1, query: "", dismissed: "", dismissedAt: -1 };
let cbLastDeliverables = []; /* ux(round9)：/doc 取最近一份交付物（done 事件落） */

function cbSlashCmds() {
  /* 既有客户端命令一并进面板（发现性），确认后预填命令文本，Enter 由既有 handleSlash 执行 */
  return CB_SLASH.concat([
    { id: "skills", ch: "技", tone: "gray", name: "技能列表", aliases: [], desc: "既有命令 · 列出 skills", kind: "client" },
    { id: "new", ch: "新", tone: "gray", name: "新会话", aliases: [], desc: "既有命令 · 开新线程", kind: "client" },
    { id: "threads", ch: "线", tone: "gray", name: "会话列表", aliases: [], desc: "既有命令 · 刷新 threads", kind: "client" },
    { id: "bg", ch: "后", tone: "gray", name: "后台任务", aliases: [], desc: "既有命令 · /bg <任务>", kind: "client" },
    { id: "help", ch: "帮", tone: "gray", name: "帮助", aliases: [], desc: "既有命令 · 命令清单", kind: "client" },
  ]);
}

function cbCmdTicketItems() {
  return (window.CB_TICKETS || []).map((t) => ({
    id: t.id,
    name: t.story || "",
    aliases: [],
    xlsx: t.xlsx,
    n_lines: t.n_lines,
    net_kg: t.net_kg,
  }));
}

/* ux(round19)：子模式数据源。cats/posts 优先用 /api/catalog 的 state.experts，
   离线或该接口不可用时从 window.CB_POSTS 推导（posts.js 由 gen_cb_posts.py 生成，
   与 workbench/seed.json 66 岗逐字段相等，禁止手改）。 */
function cbPostsAll() {
  if (Array.isArray(state.experts) && state.experts.length) return state.experts;
  return Array.isArray(window.CB_POSTS) ? window.CB_POSTS : [];
}

function cbCmdCatItems() {
  const seen = new Map();
  for (const p of cbPostsAll()) {
    const id = p.category || p.category_name || "";
    if (!id) continue;
    if (!seen.has(id)) seen.set(id, { id, name: p.category_name || id, n: 0 });
    seen.get(id).n += 1;
  }
  return [...seen.values()].map((c) => ({
    id: c.id, name: c.name, ch: "类", tone: "gray", desc: c.n + " 岗",
  }));
}

function cbCmdPostItems(catId) {
  return cbPostsAll()
    .filter((p) => (p.category || p.category_name) === catId)
    .map((p) => ({
      id: p.id, name: p.name, aliases: p.aliases || [],
      ch: "岗", tone: "blue",
      desc: [p.category_name || "", p.delivers || ""].filter(Boolean).join(" · "),
    }));
}

function cbCmdSubItems(mode) {
  if (mode === "tickets") return cbCmdTicketItems();
  if (mode === "cats") return cbCmdCatItems();
  if (mode === "posts") return cbCmdPostItems(cbCmd.cat);
  return cbSlashCmds();
}

function cbCmdFiltered(mode, query) {
  const items = cbCmdSubItems(mode);
  return cbSlashFilter(items, query, mode === "cats" || mode === "posts" ? items.length : 9);
}

function cbCmdClose() {
  cbCmd.open = false;
  cbCmd.mode = "cmds";
  const menu = $("cmdMenu");
  if (menu) menu.hidden = true;
}

function cbCmdRow(row, i) {
  const el = document.createElement("div");
  el.className = "cb-cmd-item" + (i === cbCmd.idx ? " on" : "");
  el.setAttribute("role", "option");
  el.setAttribute("aria-selected", i === cbCmd.idx ? "true" : "false");
  const ico = document.createElement("span");
  ico.className = "cb-cmd-ico tone-" + (row.tone || "gray");
  ico.textContent = row.ch || "·";
  const name = document.createElement("span");
  name.className = "cb-cmd-name";
  name.textContent = row.title || "";
  const desc = document.createElement("span");
  desc.className = "cb-cmd-desc";
  desc.textContent = row.desc || "";
  el.append(ico, name, desc);
  el.addEventListener("mousedown", (ev) => {
    ev.preventDefault();
    cbCmd.idx = i;
    cbCmdConfirm();
  });
  el.addEventListener("mouseenter", () => {
    cbCmd.idx = i;
    cbCmdRender();
  });
  return el;
}

function cbCmdRender() {
  const menu = $("cmdMenu");
  if (!menu) return;
  menu.innerHTML = "";
  if (cbCmd.mode === "cmds" && !cbCmd.query) {
    /* 面板顶部：最近 3 条任务（读 localStorage 会话历史），点击重填、不自动发送 */
    const rec = cbRecentList().slice(0, 3);
    if (rec.length) {
      const head = document.createElement("div");
      head.className = "cb-cmd-head";
      head.textContent = "最近任务 · 点击重填（不自动发送）";
      menu.appendChild(head);
      rec.forEach((r) => {
        const el = document.createElement("div");
        el.className = "cb-cmd-item cb-cmd-recent";
        const ico = document.createElement("span");
        ico.className = "cb-cmd-ico tone-gray";
        ico.textContent = "近"; /* round14：单字色块图标（与 装/标/安 同式），不用符号 */
        const name = document.createElement("span");
        name.className = "cb-cmd-name";
        name.textContent = String(r.t).slice(0, 46);
        const desc = document.createElement("span");
        desc.className = "cb-cmd-desc";
        desc.textContent = "重填";
        el.append(ico, name, desc);
        el.addEventListener("mousedown", (ev) => {
          ev.preventDefault();
          cbCmdClose();
          cbCmdApplyDraft(r.t);
        });
        menu.appendChild(el);
      });
      const sep = document.createElement("div");
      sep.className = "cb-cmd-head";
      sep.textContent = "命令 · ↑↓ 选择 / Enter 确认 / Esc 关闭";
      menu.appendChild(sep);
    }
  }
  cbCmd.items.forEach((it, i) => {
    if (cbCmd.mode === "cats" || cbCmd.mode === "posts") {
      /* ux(round19)：岗位行第二行终于露出 delivers —— 常驻名册时代它被
         .exp span{display:none} 藏了 18 轮，renderWall 白拼。 */
      menu.appendChild(cbCmdRow({ ch: it.ch, tone: it.tone, title: it.name, desc: it.desc }, i));
      if (cbCmd.mode === "posts") {
        const rows = menu.querySelectorAll(".cb-cmd-item");
        const last = rows[rows.length - 1];
        last.dataset.cbPost = it.id;
        if (state.summoned.has(it.id)) last.classList.add("on");
      }
    } else if (cbCmd.mode === "tickets") {
      menu.appendChild(cbCmdRow({
        ch: "票", tone: "blue",
        title: it.id + (it.name ? " · " + it.name : ""),
        desc: [it.n_lines != null ? it.n_lines + " 行" : "", it.net_kg != null ? Math.round(it.net_kg) + "kg" : ""]
          .filter(Boolean).join(" · "),
      }, i));
    } else {
      menu.appendChild(cbCmdRow({
        ch: it.ch, tone: it.tone,
        title: it.name + " ",
        desc: it.desc,
      }, i));
      /* 名称里补 mono /id（同 codex 弹层：命令名+描述） */
      const rows = menu.querySelectorAll(".cb-cmd-item");
      const last = rows[rows.length - 1];
      const nameEl = last.querySelector(".cb-cmd-name");
      const code = document.createElement("code");
      code.textContent = "/" + it.id;
      nameEl.appendChild(code);
    }
  });
  if (!cbCmd.items.length) {
    const none = document.createElement("div");
    none.className = "cb-cmd-head";
    none.textContent =
      cbCmd.mode === "tickets" ? "没有匹配的票 · Esc 返回"
      : cbCmd.mode === "cats" ? "没有大类 · Esc 返回"
      : cbCmd.mode === "posts" ? "该大类没有匹配岗位 · Esc 返回"
      : "没有匹配的命令";
    menu.appendChild(none);
  }
  menu.hidden = false;
}

function cbCmdUpdate() {
  const ta = $("input");
  if (!ta) return cbCmdClose();
  const tok = cbSlashQuery(ta.value, ta.selectionStart);
  if (!tok || (cbCmd.dismissed === tok.query && cbCmd.dismissedAt === tok.start)) return cbCmdClose();
  cbCmd.open = true;
  cbCmd.start = tok.start;
  cbCmd.query = tok.query;
  cbCmd.items = cbCmdFiltered(cbCmd.mode, tok.query);
  cbCmd.idx = 0;
  cbCmdRender();
}

function cbCmdApplyDraft(text) {
  const ta = $("input");
  if (!ta) return;
  ta.value = text;
  cbAutosize(ta);
  cbSyncSend();
  ta.focus();
  ta.setSelectionRange(ta.value.length, ta.value.length);
  if (text && String(text).trim()) cbObStep(1); /* ux(round10)：指令面板/最近任务预填 → 第 1 步打勾 */
}

function cbCmdMove(d) {
  if (!cbCmd.items.length) return;
  cbCmd.idx = (cbCmd.idx + d + cbCmd.items.length) % cbCmd.items.length;
  cbCmdRender();
}

function cbCmdEsc() {
  /* Raycast 子菜单语义：Esc=返回上一级。ux(round19) 由单分支改父级映射，支持三级。 */
  const parent = { tickets: "cmds", cats: "cmds", posts: "cats" };
  if (parent[cbCmd.mode]) {
    cbCmd.mode = parent[cbCmd.mode];
    cbCmd.items = cbCmdFiltered(cbCmd.mode, cbCmd.query);
    cbCmd.idx = 0;
    cbCmdRender();
    return;
  }
  cbCmd.dismissed = cbCmd.query;
  cbCmd.dismissedAt = cbCmd.start;
  cbCmdClose();
}

function cbCmdNav(id) {
  if (id === "audit") {
    const panel = $("auditPanel");
    if (panel && panel.scrollIntoView) panel.scrollIntoView({ block: "start" });
    loadAuditPanel().catch((e) => addStatus("审计加载失败：" + ((e && e.message) || e)));
    return true;
  }
  if (id === "doc") {
    const f = cbLastDeliverables.find(isDocMd) || cbLastDeliverables[0];
    if (f) {
      openDeliverable(f);
    } else {
      addStatus("本轮暂无交付物；说「写一份…」出稿后这里可直接预览。");
    }
    return true;
  }
  if (id === "eval") {
    fetch("/api/eval/live").then((r) => r.json()).then((j) => {
      const g = j.gates || {};
      const ks = Object.keys(g);
      const pass = ks.filter((k) => g[k]).length;
      addStatus("离线自检 " + (j.verdict || "—") + " · 闸门 " + pass + "/" + ks.length + "（只抄返回值）");
    }).catch((e) => addStatus("eval/live 失败：" + ((e && e.message) || e)));
    return true;
  }
  return false;
}

function cbCmdConfirm() {
  const it = cbCmd.items[cbCmd.idx];
  if (!it) return;
  if (cbCmd.mode === "cmds") {
    /* ux(round19)：sub 从写死 "tickets" 泛化为任意子模式（tickets / cats） */
    if (it.sub) {
      cbCmd.mode = it.sub;
      cbCmd.query = "";
      cbCmd.items = cbCmdFiltered(it.sub, "");
      cbCmd.idx = 0;
      cbCmdRender();
      return;
    }
    cbCmdClose();
    if (it.kind === "nav") {
      cbCmdNav(it.id);
      return;
    }
    /* tpl / client：预填草稿，不自动发送（附录 F.3 红线） */
    cbCmdApplyDraft(it.kind === "client" ? "/" + it.id + " " : cbSlashTemplate(it.id, ""));
    return;
  }
  if (cbCmd.mode === "cats") {
    /* 大类 → 该类岗位（第三级） */
    cbCmd.mode = "posts";
    cbCmd.cat = it.id;
    cbCmd.query = "";
    cbCmd.items = cbCmdFiltered("posts", "");
    cbCmd.idx = 0;
    cbCmdRender();
    return;
  }
  if (cbCmd.mode === "posts") {
    /* 选中岗位：真点名 + 把 /token 换成 @岗位名，文本与状态一致。绝不自动发送。 */
    const ta = $("input");
    cbCmdClose();
    cbSummonAdd(it.id);
    if (ta && cbCmd.start >= 0) {
      cbAtApply(ta, cbCmd.start, it);
      cbAutosize(ta);
      cbSyncSend();
    }
    return;
  }
  cbCmdClose();
  cbCmdApplyDraft(cbSlashTemplate("pack", "", it));
}

boot();

/* === ux(round11) 主题/大字开关接线 + 屏读播报（附录 J）===
   切换按钮统一放顶栏；持久化 cb_theme_v1 / cb_large_v1；未设置时跟随 prefers-color-scheme。 */
function cbAnnounce(text) {
  const el = document.getElementById("cbLive");
  if (!el) return;
  el.textContent = "";
  setTimeout(() => { el.textContent = String(text || ""); }, 30);
}

function cbThemeWire() {
  const themeBtn = document.getElementById("cbThemeBtn");
  const largeBtn = document.getElementById("cbLargeBtn");
  const isDark = () =>
    document.documentElement.getAttribute("data-theme") === "dark" ||
    (!document.documentElement.hasAttribute("data-theme") &&
      window.matchMedia && matchMedia("(prefers-color-scheme: dark)").matches);
  const sync = () => {
    const dark = isDark();
    if (themeBtn) {
      themeBtn.textContent = dark ? "暗" : "明"; /* 图标=文字：显示当前主题 */
      themeBtn.setAttribute("aria-pressed", dark ? "true" : "false");
      themeBtn.setAttribute("aria-label", dark ? "主题：当前深色，点击切换为浅色" : "主题：当前浅色，点击切换为深色");
    }
    if (largeBtn) {
      const on = document.documentElement.classList.contains("cb-large");
      largeBtn.setAttribute("aria-pressed", on ? "true" : "false");
      largeBtn.setAttribute("aria-label", (on ? "关闭" : "开启") + "大字模式（全站字号放大）");
    }
  };
  if (themeBtn) {
    themeBtn.addEventListener("click", () => {
      const next = isDark() ? "light" : "dark";
      try { localStorage.setItem("cb_theme_v1", next); } catch (e) {}
      if (window.cbApplyTheme) cbApplyTheme(next);
      sync();
    });
  }
  if (largeBtn) {
    largeBtn.addEventListener("click", () => {
      const on = document.documentElement.classList.toggle("cb-large");
      try { localStorage.setItem("cb_large_v1", on ? "1" : "0"); } catch (e) {}
      cbAnnounce(on ? "大字模式已开启" : "大字模式已关闭");
      sync();
    });
  }
  /* ux(round14)：暗色为默认主题——未显式选择时不再随系统翻转（浅色=显式切换，参考图深炭色基线） */
  if (window.matchMedia) {
    const mq = matchMedia("(prefers-color-scheme: dark)");
    const onSys = () => sync();
    mq.addEventListener ? mq.addEventListener("change", onSys) : mq.addListener && mq.addListener(onSys);
  }
  sync();
}
cbThemeWire();

/* === ux(round10) 空态卡/引导初始化：示例卡点击预填、首访 checklist 渲染、? 重开、step1 钩子 === */
document.querySelectorAll("[data-cb-sample]").forEach((btn) => {
  btn.addEventListener("click", () => cbEmptyPrefill(btn.dataset.cbSample));
});
if ($("cbBrowsePosts")) $("cbBrowsePosts").addEventListener("click", cbBrowsePosts);
cbOnboardRender();
if ($("onboardHelp")) {
  $("onboardHelp").addEventListener("click", cbOnboardReopen);
}
if ($("input")) {
  $("input").addEventListener("input", () => {
    if ($("input").value.trim()) cbObStep(1);
  });
}

/* === ux(round3) 阶段时间线 · 一条流水线（进度可见）· docs/ux/ux-design-spec.md 附录 B ===
   8 阶段轨道：理解任务→召唤岗位→成箱→人工确认→拼柜→合规校核→落盘→收口。
   事件→阶段映射（SSE event=status.phase，按 workbench/src/api.rs + agent.rs 实际事件名）：
   understand/compress/import→理解任务 · summon/queue/plain→召唤岗位 · harness/scheme_gate→成箱
   hitl_gate/confirm→人工确认 · plan_load_eval/pack→拼柜 · risk→合规校核 · price/deliver→落盘 · done→收口。
   未列出的工具名 phase（search_kb/read_kb/web_search 等）→ 附在当前阶段子行，不进「未知」桶。
   借鉴 pattern-only：aider waiting.Spinner（Apache-2.0）单行进行中+降级渲染；
   openai/codex history_cell（Apache-2.0）追加式会话流、完成后折叠为一行摘要；
   VS Code Tasks presentation（文档 pattern-only）长输出默认折叠、可展开。 */
const CB_TL_STAGES = [
  ["understand", "理解任务"],
  ["summon", "召唤岗位"],
  ["box", "读取资料"],
  ["pack", "起草"],
  ["hitl", "人工确认"],
  ["risk", "检查"],
  ["write", "保存"],
  ["finalize", "完成"],
];
const CB_TL_PACK_STAGES = [
  ["understand", "理解任务"],
  ["summon", "召唤岗位"],
  ["box", "成箱"],
  ["hitl", "人工确认"],
  ["pack", "拼柜"],
  ["risk", "合规校核"],
  ["write", "落盘"],
  ["finalize", "收口"],
];
/* ux(round5) 全局 Esc=驳回永不放行：等待中的审批卡登记于此（Codex approval_overlay 契约） */
const CB_APR_WAITING = new Set();
document.addEventListener("keydown", (ev) => {
  if (ev.key !== "Escape" || !CB_APR_WAITING.size) return;
  const cards = [...CB_APR_WAITING];
  const card = cards[cards.length - 1];
  if (card && typeof card.onEsc === "function") {
    ev.preventDefault();
    card.onEsc();
  }
});
const CB_PHASE_STAGE = {
  understand: "understand",
  compress: "understand",
  import: "understand",
  summon: "summon",
  queue: "summon",
  plain: "summon",
  search_kb: "box",
  read_kb: "box",
  retrieve: "box",
  harness: "box",
  scheme_gate: "box",
  scheme: "box",
  hitl_gate: "hitl",
  hitl: "hitl",
  confirm: "hitl",
  plan_load_eval: "pack",
  pack: "pack",
  risk: "risk",
  exclusive: "write",
  write: "write",
  price: "write",
  doc: "write",
  deliver: "write",
  done: "finalize",
};

function cbTlStageDefinitions(packing) {
  return packing ? CB_TL_PACK_STAGES : CB_TL_STAGES;
}

function cbTlPhase(phase, packing) {
  if (!packing && phase === "deliver") return "pack";
  return CB_PHASE_STAGE[phase] || "";
}

function cbTlCreate(bodyEl, sourceMessage) {
  let packing = state.summoned.has("pack-ship") || /^(?:pack\s|\$pack-ship\b|@装箱拼柜)/i.test(String(sourceMessage || "").trim());
  let definitions = cbTlStageDefinitions(packing);
  const stages = {};
  for (const [key, label] of definitions) stages[key] = { state: "idle", label, note: "" };
  let activeKey = "";
  let hitlWait = false;
  let doneFolded = false;

  const root = document.createElement("div");
  root.className = "cb-tl";
  root.setAttribute("data-cb-timeline", "true");
  root.innerHTML =
    '<div class="cb-tl-head"><span class="cb-tl-title">阶段时间线</span>' +
    '<span class="cb-tl-badge tl-badge"></span>' +
    '<button type="button" class="cb-tl-fold" hidden>展开时间线</button></div>' +
    '<div class="cb-tl-summary tl-summary" hidden></div>' +
    '<div class="cb-tl-track" role="list" aria-label="流水线阶段"></div>' +
    '<div class="cb-tl-hitl tl-hitl" data-r5-approval-slot="true" hidden></div>' +
    '<div class="cb-tl-lines tl-lines"></div>' +
    '<div class="cb-tl-audit tl-audit" data-cb-audit="true" hidden></div>';
  const badge = root.querySelector(".tl-badge");
  const foldBtn = root.querySelector(".cb-tl-fold");
  const summaryEl = root.querySelector(".tl-summary");
  const trackEl = root.querySelector(".cb-tl-track");
  const hitlEl = root.querySelector(".tl-hitl");
  const linesEl = root.querySelector(".tl-lines");
  const auditEl = root.querySelector(".tl-audit");

  function renderTrack() {
    trackEl.textContent = "";
    let count = 0;
    for (const [key, label] of definitions) {
      if (count++) {
        const arrow = document.createElement("span");
        arrow.className = "cb-tl-arrow";
        arrow.textContent = "→";
        arrow.setAttribute("aria-hidden", "true");
        trackEl.appendChild(arrow);
      }
      const chip = stages[key].el || document.createElement("span");
      chip.setAttribute("data-cb-stage", key);
      chip.setAttribute("role", "listitem");
      chip.title = label;
      trackEl.appendChild(chip);
      stages[key].el = chip;
      stages[key].label = label;
      paintStage(key);
    }
  }
  renderTrack();

  function paintStage(key) {
    const st = stages[key];
    if (!st || !st.el) return;
    st.el.className = "cb-tl-stage " + st.state;
    if (st.state === "run") {
      st.el.innerHTML = '<span class="cb-tl-spin" aria-hidden="true"></span>' + st.label;
    } else {
      /* ux(round14)：done/warn/err 状态 = 单色 CSS 圆点（.cb-tl-st），不再写字符（规则见 spec 附录 L） */
      st.el.innerHTML = '<span class="cb-tl-st" aria-hidden="true"></span>' + st.label;
    }
  }

  function setStage(key, stateName, note) {
    const st = stages[key];
    if (!st) return;
    if (st.state === "run" && stateName === "run") return;
    if (st.state === "done" && stateName === "run") st.note = "打回重做";
    st.state = stateName;
    if (note) st.note = String(note).slice(0, 200);
    paintStage(key);
  }

  function settleActive() {
    if (activeKey && stages[activeKey] && stages[activeKey].state === "run") {
      stages[activeKey].state = "done";
      paintStage(activeKey);
    }
  }

  function addLine(stageKey, tool, text, status) {
    const row = document.createElement("div");
    row.className = "cb-tl-line is-" + (status === "running" ? "running" : status === "error" ? "err" : status === "warn" ? "warn" : "ok");
    row.setAttribute("data-cb-stage", stageKey || activeKey || "understand");
    const t = String(text || "");
    const toolEl = document.createElement("span");
    toolEl.className = "tl-tool";
    toolEl.textContent = tool || "·";
    const textEl = document.createElement("span");
    textEl.className = "tl-text";
    textEl.textContent = t.length > 96 ? t.slice(0, 96) + "…" : t;
    row.appendChild(toolEl);
    row.appendChild(textEl);
    if (t.length > 96) {
      const more = document.createElement("button");
      more.type = "button";
      more.className = "cb-tl-more";
      more.textContent = "展开";
      let open = false;
      more.addEventListener("click", () => {
        open = !open;
        textEl.textContent = open ? t : t.slice(0, 96) + "…";
        more.textContent = open ? "收起" : "展开";
      });
      row.appendChild(more);
    }
    linesEl.appendChild(row);
    linesEl.scrollTop = linesEl.scrollHeight;
  }

  function setBadge(text, cls) {
    badge.textContent = text || "";
    badge.className = "cb-tl-badge tl-badge" + (cls ? " " + cls : "");
  }

  /* ===== ux(round5) HITL 审批卡（docs/ux/ux-design-spec.md 附录 D）=====
     显式决策事件 + Esc/关闭=驳回永不放行 + 决策写审计行。卡片只抄事件字段（gate/expert/run_id），不编数字。 */
  function aprNow() {
    const d = new Date();
    const p = (n) => String(n).padStart(2, "0");
    return d.getFullYear() + "-" + p(d.getMonth() + 1) + "-" + p(d.getDate()) + " " + p(d.getHours()) + ":" + p(d.getMinutes()) + ":" + p(d.getSeconds());
  }

  function aprAudit(decision, reason) {
    const rowEl = document.createElement("div");
    rowEl.className = "cb-tl-audit-row";
    rowEl.textContent = "审计 · " + decision + (reason ? "（" + reason + "）" : "") + " · " + aprNow() + " · 操作者=本地用户 · 未静默放行";
    auditEl.appendChild(rowEl);
    auditEl.hidden = false;
  }

  function addAuditRow(text) {
    aprAudit(text, "");
  }

  function gateLabel(gate) {
    if (gate === "scheme") return "成箱方案闸门（scheme）";
    if (gate === "exclusive_write") return "高风险写盘闸门（exclusive_write）";
    return gate ? "闸门 " + gate : "HITL 闸门";
  }

  function mountApproval(info) {
    if (!hitlEl) return;
    const state = { decided: "", open: true };
    hitlEl.hidden = false;
    hitlEl.textContent = "";
    const card = document.createElement("div");
    card.className = "cb-apr";
    card.setAttribute("role", "dialog");
    card.setAttribute("aria-label", "HITL 人工确认审批卡");
    card.setAttribute("data-cb-approval", "true");
    card.innerHTML =
      '<div class="cb-apr-bar" aria-hidden="true"></div>' +
      '<div class="cb-apr-head">' +
      '<span class="cb-apr-title">人工确认 · ' + escapeHtml(gateLabel(info.gate)) + "</span>" +
      '<span class="cb-apr-risk is-high">风险 · 高</span>' +
      '<span class="cb-apr-state apr-state" hidden></span>' +
      '<button type="button" class="cb-apr-x" title="关闭 = 驳回，永不放行" aria-label="关闭审批卡（等同驳回，不放行）">关闭</button>' +
      "</div>" +
      '<div class="cb-apr-body apr-waiting">' +
      '<div class="cb-apr-chips" data-cb-approval-summary="true">' +
      (info.expert ? '<span class="cb-apr-chip">岗位 <b>' + escapeHtml(String(info.expert)) + "</b></span>" : "") +
      (info.runId ? '<span class="cb-apr-chip">run <b>' + escapeHtml(String(info.runId)) + "</b></span>" : "") +
      '<span class="cb-apr-chip is-warn">签认 <b>未确认</b> · 本岗未出稿</span>' +
      "</div>" +
      '<div class="cb-apr-block" data-cb-approval-blockers="true">输入下方完整签认句后，才能重新提交这条任务并生成内部讨论草稿。</div>' +
      '<label class="cb-apr-ack">请键入：我明白，将由持证人员签认（或 / or: I understand; a licensed person will sign this off.）' +
      '<input class="cb-apr-ack-input" type="text" autocomplete="off" spellcheck="false" /></label>' +
      '<div class="cb-apr-actions" data-cb-approval-actions="true">' +
      '<button type="button" class="cb-apr-confirm" disabled aria-label="确认并重提（须完整键入签认句）">确认并重提</button>' +
      '<button type="button" class="cb-apr-reject" aria-label="驳回（显式决策：不放行，可修改输入后重跑）">驳回</button>' +
      '<button type="button" class="cb-apr-later" aria-label="稍后（折叠为等待条，不改变等待状态）">稍后</button>' +
      "</div>" +
      '<div class="cb-apr-foot">完整键入「我明白，将由持证人员签认」后，才能重新提交本条任务 · ' +
      "<b>Esc、关闭 = 驳回，永不放行</b> · 决策写入下方审计行</div>" +
      "</div>" +
      '<div class="cb-apr-body apr-decided" hidden></div>';
    const stateChip = card.querySelector(".apr-state");
    const waitingBody = card.querySelector(".apr-waiting");
    const decidedBody = card.querySelector(".apr-decided");
    const acknowledgment = card.querySelector(".cb-apr-ack-input");
    const approve = card.querySelector(".cb-apr-confirm");
    acknowledgment.addEventListener("input", () => {
      approve.disabled = !CB_SIGNOFF.includes(acknowledgment.value);
    });

    function settle(kind, reason) {
      if (state.decided) return;
      state.decided = kind;
      CB_APR_WAITING.delete(api);
      waitingBody.hidden = true;
      decidedBody.hidden = false;
      decidedBody.innerHTML = "";
      card.querySelector(".cb-apr-x").hidden = true;
      if (kind === "approved") {
        card.classList.add("is-approved");
        stateChip.hidden = false;
        stateChip.textContent = "已确认 · 已重新提交";
        setStage("hitl", "done", "已确认 · 已重新提交（续跑见新时间线）");
        addLine("hitl", "hitl.confirm", "用户确认 · 键入签认句并重新提交", "ok");
        decidedBody.textContent = "已确认 · 已重新提交本条任务（附本人键入的确认句）。本时间线定格为历史，续跑进度见新时间线。";
      } else {
        card.classList.add("is-rejected");
        stateChip.hidden = false;
        stateChip.classList.add("is-rejected");
        stateChip.textContent = "已驳回 · 未放行";
        setStage("hitl", "warn", "已驳回（未放行）· 请修改输入后重跑");
        addLine("hitl", "hitl.reject", "用户驳回（" + (reason || "") + "）· 未放行 · 未出稿", "warn");
        const note = document.createElement("div");
        note.className = "cb-apr-reject-note";
        note.textContent = "已驳回（" + (reason || "驳回") + "）· 未放行，未出任何稿 · 请修改输入后重跑（补齐数据 / 更换岗位 / 调整任务描述）";
        decidedBody.appendChild(note);
      }
      aprAudit(kind === "approved" ? "确认 · 已重新提交" : "驳回 · 未放行", reason);
    }

    card.querySelector(".cb-apr-confirm").addEventListener("click", () => {
      if (state.decided || !CB_SIGNOFF.includes(acknowledgment.value)) return;
      if (runState.active) {
        cbAnnounce("请等待当前回答结束后，再确认重提");
        return;
      }
      cbObStep(3); /* ux(round10)：审批卡显式确认 → 引导第 3 步打勾 */
      const ok = $("confirmOk");
      const input = $("input");
      const form = $("form");
      if (!ok || !input || !form || !sourceMessage) return;
      ok.value = acknowledgment.value;
      input.value = sourceMessage;
      if (form.requestSubmit) form.requestSubmit();
      else form.dispatchEvent(new Event("submit", { cancelable: true }));
      settle("approved", "");
    });
    card.querySelector(".cb-apr-reject").addEventListener("click", () => settle("rejected", "驳回按钮"));
    card.querySelector(".cb-apr-x").addEventListener("click", () => settle("rejected", "Esc/关闭"));
    card.querySelector(".cb-apr-later").addEventListener("click", () => {
      /* 稍后=折叠卡片继续等待；不做决策、不放行 */
      state.open = false;
      card.hidden = true;
      openSlot.hidden = false;
    });
    const api = { settle };
    api.onEsc = () => {
      if (state.open && !state.decided) settle("rejected", "Esc/关闭");
    };
    CB_APR_WAITING.delete(api);
    CB_APR_WAITING.add(api);

    /* 稍后折叠条：点击重新展开（仍等待，未放行） */
    const openSlot = document.createElement("div");
    openSlot.className = "cb-apr-open-slot";
    openSlot.setAttribute("role", "button");
    openSlot.tabIndex = 0;
    openSlot.hidden = true;
    openSlot.textContent = "等待人工确认 · 点此展开审批卡（确认 / 驳回须显式点击；Esc、关闭 = 驳回，永不放行）";
    const reopen = () => {
      if (state.decided) return;
      state.open = true;
      openSlot.hidden = true;
      card.hidden = false;
    };
    openSlot.addEventListener("click", reopen);
    openSlot.addEventListener("keydown", (ev) => {
      if (ev.key === "Enter") { ev.preventDefault(); reopen(); }
    });

    hitlEl.appendChild(card);
    hitlEl.appendChild(openSlot);
    const log = $("log");
    if (log) log.scrollTop = log.scrollHeight;
    return api;
  }

  function finish(data) {
    settleActive();
    const hitlPending = cbHitlPending(data);
    if (hitlPending) {
      /* HITL 闸门未过：运行已结束但未出稿——时间线不定格为「完成」，审批卡保持等待 */
      if (activeKey && stages[activeKey] && stages[activeKey].state === "run") {
        stages[activeKey].state = "done";
        paintStage(activeKey);
      }
      setStage("hitl", "warn", "等待人工确认（闸门未过，本岗未出稿）");
      setBadge("等待签认", "hitl");
      hitlEl.hidden = false;
      if (!hitlEl.querySelector(".cb-apr")) {
        mountApproval({ gate: (data.hitl || {}).gate || data.gate, expert: data.expert || data.skill || "",
          runId: data.run_id || (Array.isArray(data.run_ids) ? data.run_ids.join(", ") : "") });
      }
      return;
    }
    setStage("finalize", "done", "收口完成");
    if (hitlWait) hitlWait = false;
    hitlEl.hidden = true;
    setBadge("完成", "");
    doneFolded = true;
    /* 摘要数字只抄事件（deliverables / mode），不编造 */
    const files = Array.isArray(data && data.deliverables) ? data.deliverables : [];
    const head = document.createElement("span");
    head.textContent = "完成 · " + ((data && data.mode) === "firm" ? "一人公司成套" : (data && data.mode) === "expert" ? "岗位收工" : "回答完毕") + (files.length ? " · 文书 " + files.length + " 份" : "");
    summaryEl.textContent = "";
    summaryEl.appendChild(head);
    for (const f of files) {
      const chip = document.createElement("span");
      chip.className = "cb-tl-chip";
      chip.textContent = (f.name || f.title || String(f.path || "").split("/").pop() || "文书").slice(0, 24);
      summaryEl.appendChild(chip);
    }
    summaryEl.hidden = false;
    trackEl.hidden = true;
    linesEl.hidden = true;
    foldBtn.hidden = false;
    foldBtn.textContent = "展开时间线";
    foldBtn.onclick = () => {
      doneFolded = !doneFolded;
      trackEl.hidden = doneFolded;
      linesEl.hidden = doneFolded;
      summaryEl.hidden = !doneFolded;
      foldBtn.textContent = doneFolded ? "展开时间线" : "折叠为一行摘要";
    };
  }

  function status(data) {
    const phase = String((data && data.phase) || "");
    const text = String((data && data.text) || "");
    if (phase === "summon" && data.expert) {
      const nextPacking = data.expert === "pack-ship";
      if (nextPacking !== packing) {
        packing = nextPacking;
        definitions = cbTlStageDefinitions(packing);
        renderTrack();
      }
    }
    const stageKey = cbTlPhase(phase, packing);
    if (stageKey === "hitl") {
      settleActive();
      setStage("hitl", "run", text || "等待人工确认（HITL 闸门）");
      activeKey = "hitl";
      hitlWait = true;
      setBadge("等待签认", "hitl");
    } else if (stageKey) {
      settleActive();
      setStage(stageKey, "run", text || phase);
      activeKey = stageKey;
      setBadge(stageLabel(stageKey), "");
    } else {
      /* 工具名 phase / think 等未映射事件 → 当前阶段子行，不落「未知」
         （Rust 侧 status 事件=该动作已完成并带结果文本，故标 ok 不挂 running） */
      addLine(activeKey || "understand", phase, text || phase, "ok");
      return;
    }
    if (text) addLine(stageKey || activeKey, phase, text, "ok");
  }

  function stageLabel(key) {
    const found = definitions.find((s) => s[0] === key);
    return found ? found[1] : key;
  }

  function error(text) {
    const key = activeKey || "finalize";
    setStage(key, "warn", String(text).slice(0, 200));
    addLine(key, "error", text, "error");
    setBadge("未完成 · 见详情", "hitl");
  }

  /* 挂载：插到本条助手消息上方（追加式会话流中的一格，完成后折叠定格） */
  const msgBox = bodyEl && bodyEl.parentElement;
  if (msgBox && msgBox.parentElement) msgBox.parentElement.insertBefore(root, msgBox);
  else if (msgBox) msgBox.insertBefore(root, bodyEl);
  const log = $("log");
  if (log) log.scrollTop = log.scrollHeight;

  return { status, error, finish, root };
}

/* ===== ux(round6) 跨运行审计时间线（docs/ux 附录 E）=====
   数据源 GET /api/harness/audit/<session>（只读聚合 demo/out/<session>/runs/<run_id>/trace.json）。
   节点四色：工具执行=蓝 · 人工决策=合规红 · 错误/重试=橙 · 写盘=绿；决策节点永久置顶不可折叠。 */
let auditTimer = 0;
let auditLast = null;
const AUDIT_KIND_LABEL = { run: "运行", tool: "工具", decision: "决策", error: "错误", write: "写盘" };

function auditNodeEl(n, ts) {
  const row = document.createElement("div");
  row.className = "cb-audit-node is-" + (n.kind || "tool");
  const dot = document.createElement("span");
  dot.className = "cb-audit-dot";
  dot.setAttribute("aria-hidden", "true");
  const k = document.createElement("span");
  k.className = "cb-audit-k";
  k.textContent = AUDIT_KIND_LABEL[n.kind] || n.kind || "·";
  const t = document.createElement("span");
  t.className = "cb-audit-t";
  t.textContent = String(n.title || "") + (n.detail ? " · " + n.detail : "");
  row.append(dot, k, t);
  if (ts) {
    const time = document.createElement("span");
    time.className = "cb-audit-time";
    time.textContent = String(ts);
    row.append(time);
  }
  if (n.operator) {
    const op = document.createElement("span");
    op.className = "cb-audit-op";
    op.textContent = String(n.operator) + " · 未静默放行";
    row.append(op);
  }
  if (n.raw != null) {
    const det = document.createElement("details");
    det.className = "cb-audit-raw";
    const sum = document.createElement("summary");
    sum.textContent = "原始";
    const pre = document.createElement("pre");
    pre.textContent = JSON.stringify(n.raw, null, 2);
    det.append(sum, pre);
    row.append(det);
  }
  return row;
}

async function loadAuditPanel() {
  const body = $("auditBody");
  if (!body) return;
  body.innerHTML = '<p class="cb-audit-none">加载中…</p>';
  const res = await fetch(`/api/harness/audit/${encodeURIComponent(state.session)}`);
  let j;
  try {
    j = await res.json();
  } catch (_) {
    j = {};
  }
  if (!res.ok || j.ok === false) {
    throw new Error(j.detail || j.error || "HTTP " + res.status);
  }
  auditLast = j;
  body.textContent = "";
  const head = document.createElement("p");
  head.className = "cb-audit-none";
  const c = j.counts || {};
  head.textContent = `session ${j.session_id} · ${c.runs || 0} run · 决策 ${c.decisions || 0} · 工具 ${c.tools || 0} · 写盘 ${c.writes || 0} · 错误 ${c.errors || 0}`;
  body.append(head);
  /* 决策节点：永久置顶，不可折叠（confirm/reject/waiting 全留痕） */
  const pin = document.createElement("div");
  pin.className = "cb-audit-pinned";
  const pinH = document.createElement("div");
  pinH.className = "cb-audit-pinned-h";
  pinH.textContent = "人工决策（永久置顶 · 不可折叠 · 操作者=本地用户）";
  pin.append(pinH);
  const decisions = j.decisions || [];
  if (!decisions.length) {
    const none = document.createElement("p");
    none.className = "cb-audit-none";
    none.textContent = "本会话无人工决策记录（未触发 HITL 闸门或未走到审批）";
    pin.append(none);
  } else {
    for (const d of decisions) {
      pin.append(auditNodeEl({ kind: "decision", title: d.title, detail: d.detail, operator: d.operator }, d.ts));
    }
  }
  body.append(pin);
  for (const run of [...(j.runs || [])].reverse()) {
    const box = document.createElement("div");
    box.className = "cb-audit-run";
    const h = document.createElement("div");
    h.className = "cb-audit-run-h";
    const rid = document.createElement("span");
    rid.className = "cb-audit-runid";
    rid.textContent = run.run_id || "";
    const tm = document.createElement("span");
    tm.className = "cb-audit-time";
    tm.textContent = run.mtime || "";
    h.append(rid, tm);
    box.append(h);
    for (const n of run.nodes || []) box.append(auditNodeEl(n, ""));
    body.append(box);
  }
  if ($("auditCopy")) $("auditCopy").hidden = false;
  if ($("auditDownload")) $("auditDownload").hidden = false;
}

function refreshAuditSoon() {
  clearTimeout(auditTimer);
  auditTimer = setTimeout(() => {
    loadAuditPanel().catch(() => {});
  }, 800);
}

function auditExportPayload() {
  return {
    schema: (auditLast && auditLast.schema) || "civil.audit.v1",
    exported_at: new Date().toISOString(),
    product: "Civil Buddy · 人机协同履历（AI 做了什么、人批了什么）",
    session_id: (auditLast && auditLast.session_id) || state.session,
    counts: (auditLast && auditLast.counts) || {},
    decisions: (auditLast && auditLast.decisions) || [],
    runs: (auditLast && auditLast.runs) || [],
  };
}

if ($("loadAudit")) {
  $("loadAudit").addEventListener("click", () => loadAuditPanel().catch((e) => addStatus("审计加载失败：" + String(e.message || e))));
}
if ($("auditCopy")) {
  $("auditCopy").addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(JSON.stringify(auditExportPayload(), null, 2));
      addStatus("审计 JSON 已复制到剪贴板");
    } catch (_) {
      addStatus("复制失败（浏览器限制），请改用下载 JSON");
    }
  });
}
if ($("auditDownload")) {
  $("auditDownload").addEventListener("click", () => {
    const text = JSON.stringify(auditExportPayload(), null, 2);
    const fname = "audit-" + String((auditLast && auditLast.session_id) || state.session).replace(/[^A-Za-z0-9._-]+/g, "_") + ".json";
    const blob = new Blob([text], { type: "application/json;charset=utf-8" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = fname;
    document.body.appendChild(a);
    a.click();
    setTimeout(() => {
      URL.revokeObjectURL(a.href);
      a.remove();
    }, 400);
    addStatus("审计 JSON 已下载：" + fname);
  });
}

/* === ux(round8) 窄屏适配：汉堡键展开「会话/岗位」侧栏（docs/ux/ux-design-spec.md 附录 G） === */
if ($("railToggle")) {
  $("railToggle").addEventListener("click", () => {
    const rail = document.querySelector(".rail");
    if (!rail) return;
    const open = rail.classList.toggle("mobile-open");
    $("railToggle").setAttribute("aria-expanded", open ? "true" : "false");
  });
}

/* 手机键盘：iOS Safari 弹出键盘时 layout viewport 不变、visualViewport 变矮，输入框会被键盘盖住。
   窄屏且 visualViewport 明显低于窗口高度时把 body 压到 visualViewport 的高度并把对话滚到底；
   键盘收起后恢复 100dvh。Android Chrome 走 interactive-widget=resizes-content，这里等于不动。 */
if (window.visualViewport) {
  const vv = window.visualViewport;
  let raf = 0;
  const fit = () => {
    cancelAnimationFrame(raf);
    raf = requestAnimationFrame(() => {
      const narrow = window.matchMedia("(max-width: 768px)").matches;
      const keyboard = narrow && window.innerHeight - vv.height > 120;
      document.body.style.height = keyboard ? `${Math.round(vv.height)}px` : "";
      if (keyboard && $("log")) $("log").scrollTop = $("log").scrollHeight;
    });
  };
  vv.addEventListener("resize", fit);
  vv.addEventListener("scroll", fit);
}
cbCadProjectRender();
cbPlanningProjectRender();
cbLogisticsProjectRender();

/* 模块脚本没有全局：给旧的经典脚本（studio.js 调 reloadCatalog）和 e2e（scripts/e2e/ui_dom.cjs）
   一个明确的窗口面，而不是把几百个函数都挂到 window 上。 */
window.reloadCatalog = reloadCatalog;
window.__cb = Object.freeze({ state, runState, cbCapability, cbAttachUpload, cbProjOpenSession, cbNewLocalSession, uploads, drafts, turns, deliverables, watch, nav });

/* 离线壳：有 service worker 的浏览器把页面、脚本、样式留一份，断网也能打开上一次的界面；/api/ 不缓存。 */
if ("serviceWorker" in navigator && typeof navigator.serviceWorker.register === "function") {
  navigator.serviceWorker.register("/sw.js").catch(() => { /* 非安全上下文（http://LAN 地址）或被禁用：照常工作 */ });
}
