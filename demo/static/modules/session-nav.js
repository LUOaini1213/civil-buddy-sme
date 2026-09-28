const cbSessionText = (source, values = {}) => globalThis.CBI18n?.t(source, values) ?? String(source || "").replace(/\{(\w+)\}/g, (match, key) => values[key] ?? match);
/* Session navigation: which session the page is on, remembering it across reloads, the
 * project / session list and its rendering, and opening a session (restoring its transcript,
 * attachments, deliverables and a still-running turn).
 *
 * Page-owned state comes in and stays the page's: state, runState, proj (the project list
 * object) and request (the navigation counter: current() / bump() — every open or new
 * session bumps it, and late responses compare against it).
 *
 *   storage, fetch, doc, el(id), location, history, relTime, addStatus, addMsg
 *                                                               environment and log lines
 *   reset   { toEmpty, contextReset, clearServerHitl, hideWelcome, detachActiveRun, uploadAbortAll,
 *             attachRender, draftRestore, paintContext, estimateLocalContext, renderSummon }
 *   apiError(res)
 *   paint   { appendDocCards, routePaint, collaborationPaint, setLastDeliverables }
 *   hooks   { render, loadThreads, openSession, attachToTurn, bgObserve }   cross-calls through
 *           the page, so a test can stub any of them
 */
export const ACTIVE_SESSION_KEY = "cb_active_session_v1";
export const PROJ_OPEN_KEY = "cb_proj_open_v1";

function validCadProjectId(value) {
  return typeof value === "string" && /^[0-9a-f]{32}$/.test(value) ? value : "";
}

/* An explicit CAD handoff selects a project for a new conversation, without restoring
   whichever unrelated conversation was most recently open. */
export function cadProjectFromUrl(href) {
  try { return validCadProjectId(new URL(href).searchParams.get("cad_project_id")); }
  catch (_) { return ""; }
}

/* A new local session id. crypto.randomUUID exists only in secure contexts (https / localhost);
   a phone on http://<LAN-ip> falls back to time + random. Pure, so the page can call it while
   building its state, before the nav factory exists. */
export function sessionId() {
  return (globalThis.crypto && typeof globalThis.crypto.randomUUID === "function"
    ? globalThis.crypto.randomUUID()
    : Date.now().toString(36) + Math.random().toString(36).slice(2)).slice(0, 12);
}

export function createSessionNav(deps) {
  const { state, runState, proj, request: navRequest, storage, doc, el, relTime, addStatus, addMsg, apiError, reset, paint, hooks } = deps; // `request` is a local in several functions
  const doFetch = deps.fetch;

  function renderCadProject() {
    let banner = el("cadProjectContext");
    // A handoff URL must not reselect an old project after navigation or clearing it.
    try {
      const url = new URL(deps.location.href);
      if (url.searchParams.has("cad_project_id") && url.searchParams.get("cad_project_id") !== state.cadProjectId) {
        url.searchParams.delete("cad_project_id");
        deps.history.replaceState(null, "", url);
      }
    } catch (_) { /* Non-browser tests do not own navigation. */ }
    if (!state.cadProjectId) {
      if (banner) banner.remove();
      return;
    }
    const composer = el("input") && el("input").parentElement;
    if (!composer) return;
    if (!banner) {
      banner = doc.createElement("div");
      banner.id = "cadProjectContext";
      banner.className = "status-line";
      composer.prepend(banner);
    }
    banner.replaceChildren();
    const link = doc.createElement("a");
    link.href = "/cad?project_id=" + state.cadProjectId;
    link.textContent = cbSessionText("当前 CAD 项目 · 返回三维模型");
    banner.appendChild(link);
    const clear = doc.createElement("button");
    clear.type = "button";
    clear.textContent = cbSessionText("取消选择");
    clear.addEventListener("click", () => { reset.cancelVoice?.(); state.cadProjectId = ""; renderCadProject(); });
    banner.appendChild(clear);
  }

  function rememberSession(id) {
    try {
      if (id) storage.setItem(ACTIVE_SESSION_KEY, id);
      else storage.removeItem(ACTIVE_SESSION_KEY);
    } catch (_) { /* Session restoration is optional when storage is unavailable. */ }
  }

  function rememberedSession() {
    if (state.cadProjectId || state.planningProjectId || state.logisticsProjectId) return "";
    try {
      const id = storage.getItem(ACTIVE_SESSION_KEY) || "";
      return /^[A-Za-z0-9][A-Za-z0-9_-]{3,31}$/.test(id) ? id : "";
    } catch (_) { return ""; }
  }

  async function resumeSession(id, request) {
    if (!id || request !== navRequest.current() || runState.active || state.history.length) return false;
    const session = proj.sessions.find((item) => item.session_id === id);
    if (!session) return false;
    await hooks.openSession(session);
    return state.session === id;
  }

  function newLocalSession() {
    reset.cancelVoice?.();
    reset.clearServerHitl();
    navRequest.bump();
    reset.detachActiveRun();
    rememberSession("");
    if (el("confirmOk")) el("confirmOk").value = "";
    state.attachments = [];
    state.attachmentRoles = {};
    state.cadProjectId = "";
    state.planningProjectId = "";
    state.logisticsProjectId = "";
    if (reset.renderToolProjects) reset.renderToolProjects();
    else renderCadProject();
    state.session = sessionId();
    reset.uploadAbortAll(state.session);
    reset.attachRender();
    reset.draftRestore();
    state.history = [];
    reset.contextReset();
    state.summoned.clear();
    reset.renderSummon();
    reset.toEmpty();
    reset.paintContext(reset.estimateLocalContext());
  }

  function openLoad() {
    try {
      const v = JSON.parse(storage.getItem(PROJ_OPEN_KEY) || "[]");
      if (Array.isArray(v)) proj.open = new Set(v.map(String));
    } catch (e) { /* 存储不可用：全折叠 */ }
  }

  function openSave() {
    try { storage.setItem(PROJ_OPEN_KEY, JSON.stringify([...proj.open])); } catch (e) { /* 忽略 */ }
  }

  function fallback(msg) {
    const box = el("projTree");
    if (!box) return;
    box.setAttribute("aria-label", cbSessionText("工程项目与会话"));
    box.innerHTML = "";
    const none = doc.createElement("div");
    none.className = "thread-none";
    none.textContent = msg;
    box.appendChild(none);
  }

  async function loadThreads() {
    const box = el("projTree");
    if (!box) return;
    try {
      const [pj, ss] = await Promise.all([
        doFetch("/api/projects").then((r) => (r.ok ? r.json() : null)),
        doFetch("/api/sessions?limit=100").then((r) => (r.ok ? r.json() : null)),
      ]);
      if (!pj || !ss) throw new Error("no-projects-api");
      hooks.bgObserve(ss.sessions);
      proj.projects = pj.projects || [];
      proj.inbox = pj.inbox || null;
      proj.sessions = ss.sessions || [];
      hooks.render();
    } catch (e) {
      /* 降级：该后端没有项目接口。静默留一行弱文本，不写对话流。 */
      fallback(cbSessionText("本后端不提供项目列表"));
    }
  }

  function sessionsOf(pid) {
    return proj.sessions.filter((s) => s.project_id === pid);
  }

  function renderProjects() {
    const box = el("projTree");
    if (!box) return;
    box.setAttribute("aria-label", cbSessionText("工程项目与会话"));
    box.innerHTML = "";
    const groups = proj.projects.slice();
    if (proj.inbox) groups.push(proj.inbox); /* 未归类恒在最后 */
    if (!groups.length) {
      fallback(cbSessionText("还没有项目；跑一次任务后自动归入未归类"));
      return;
    }
    for (const p of groups) {
      const displayName = p.id === "p-inbox" && p.builtin ? cbSessionText("未归类") : p.name;
      const kids = sessionsOf(p.id);
      const open = proj.open.has(p.id);
      const wrap = doc.createElement("div");
      wrap.className = "proj" + (open ? " open" : "");
      wrap.dataset.pid = p.id;
      wrap.setAttribute("role", "treeitem");
      wrap.setAttribute("aria-expanded", open ? "true" : "false");

      const row = doc.createElement("div");
      row.className = "proj-row";
      const tw = doc.createElement("button");
      tw.type = "button";
      tw.className = "proj-tw"; /* CSS 三角，不用字符（符号纪律） */
      tw.setAttribute("aria-label", (open ? cbSessionText("折叠 ") : cbSessionText("展开 ")) + displayName);
      tw.addEventListener("click", () => {
        if (proj.open.has(p.id)) proj.open.delete(p.id);
        else proj.open.add(p.id);
        openSave();
        hooks.render();
      });
      const name = doc.createElement("button");
      name.type = "button";
      name.className = "proj-name";
      name.textContent = displayName;
      name.title = displayName;
      name.addEventListener("click", () => {
        proj.cur = p.id;
        proj.open.add(p.id);
        openSave();
        hooks.render();
      });
      const n = doc.createElement("span");
      n.className = "proj-n";
      n.textContent = String(kids.length);
      row.append(tw, name, n);

      /* 内置「未归类」不可改名 —— 服务端也会 400，这里不给入口 */
      if (!p.builtin) {
        const more = doc.createElement("button");
        more.type = "button";
        more.className = "proj-more";
        more.textContent = cbSessionText("改名");
        more.setAttribute("aria-label", cbSessionText("重命名项目 ") + p.name);
        more.addEventListener("click", () => renameProject(p));
        row.appendChild(more);
      }
      wrap.appendChild(row);

      const kidBox = doc.createElement("div");
      kidBox.className = "proj-kids";
      if (!open) kidBox.hidden = true;
      for (const s of kids) {
        const b = doc.createElement("button");
        b.type = "button";
        b.className = "sess-row" + (s.session_id === state.session ? " on" : "");
        b.title = s.session_id;
        const t1 = doc.createElement("span");
        t1.className = "t-name";
        t1.textContent = s.title || s.session_id;
        const t2 = doc.createElement("span");
        t2.className = "t-time";
        const running = s.running === true || runState.background.has(s.session_id);
        const stale = !running && s.turn_state === "stale";
        t2.textContent = running ? cbSessionText("运行中") : stale ? cbSessionText("已中断") : relTime(s.updated_at);
        if (running) t2.classList.add("t-running");
        if (stale) { t2.classList.add("t-stale"); t2.title = cbSessionText("上一轮在服务重启时被中断"); }
        b.append(t1, t2);
        b.addEventListener("click", () => hooks.openSession(s));
        kidBox.appendChild(b);
      }
      if (!kids.length) {
        const none = doc.createElement("div");
        none.className = "sess-none";
        none.textContent = cbSessionText("这个项目还没有会话");
        kidBox.appendChild(none);
      }
      wrap.appendChild(kidBox);
      box.appendChild(wrap);
    }
  }

  function renameProject(p) {
    const wrap = doc.querySelector('.proj[data-pid="' + p.id + '"]');
    const row = wrap && wrap.querySelector(".proj-row");
    if (!row) return;
    const old = row.querySelector(".proj-name");
    if (!old) return;
    const inp = doc.createElement("input");
    inp.type = "text";
    inp.className = "rail-rename";
    inp.value = p.name;
    let settled = false;
    const commit = async (save) => {
      if (settled) return;
      settled = true;
      const v = inp.value.trim();
      inp.replaceWith(old);
      if (!save || !v || v === p.name) return;
      try {
        const r = await doFetch("/api/projects/" + encodeURIComponent(p.id), {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ name: v }),
        });
        if (!r.ok) throw new Error("HTTP " + r.status);
        await hooks.loadThreads();
      } catch (e) {
        addStatus(cbSessionText("改名失败：") + ((e && e.message) || e));
      }
    };
    inp.addEventListener("keydown", (ev) => {
      if (ev.key === "Enter") { ev.preventDefault(); commit(true); }
      if (ev.key === "Escape") { ev.preventDefault(); commit(false); }
    });
    inp.addEventListener("blur", () => commit(true));
    old.replaceWith(inp);
    inp.focus();
    inp.select();
  }

  async function openSession(s) {
    reset.cancelVoice?.();
    const request = navRequest.bump();
    reset.detachActiveRun();
    reset.contextReset();
    try {
      const response = await doFetch("/api/sessions/" + encodeURIComponent(s.session_id));
      if (!response.ok) throw new Error(await apiError(response));
      const d = await response.json();
      if (request !== navRequest.current()) return;
      if (!d || !d.session_id || !Array.isArray(d.transcript)) throw new Error(cbSessionText("会话数据格式不完整"));
      state.session = d.session_id;
      rememberSession(d.session_id);
      reset.clearServerHitl();
      if (el("confirmOk")) el("confirmOk").value = "";
      state.attachments = Array.isArray(d.attachments) ? d.attachments.filter((file) =>
        file && typeof file.id === "string" && file.id && !file.id.startsWith("job:")) : [];
      state.attachmentRoles = Object.fromEntries(state.attachments.filter(file =>
        d.attachment_roles && ["tender", "response", "reference"].includes(d.attachment_roles[file.id]))
        .map(file => [file.id, d.attachment_roles[file.id]]));
      reset.uploadAbortAll(d.session_id);
      reset.attachRender();
      reset.draftRestore();
      proj.cur = d.project_id || s.project_id || "";
      if (reset.restoreToolProjects) reset.restoreToolProjects(d);
      else {
        state.cadProjectId = validCadProjectId(d.cad_project_id);
        state.planningProjectId = validCadProjectId(d.planning_project_id);
        state.logisticsProjectId = validCadProjectId(d.logistics_project_id);
        renderCadProject();
      }
      state.summoned.clear();
      const enabledExperts = new Set(state.experts.filter((expert) => expert && expert.enabled !== false).map((expert) => expert.id));
      for (const id of Array.isArray(d.expert_ids) ? d.expert_ids : []) {
        if (typeof id === "string" && enabledExperts.has(id)) state.summoned.add(id);
      }
      reset.renderSummon();
      state.history = (d.transcript || [])
        .filter((t) => t && (t.role === "user" || t.role === "assistant"))
        .map((t) => ({ role: t.role, content: t.text || "" }));
      reset.toEmpty();
      const log = el("log");
      let restoredBody = null;
      let restoredMessage = "";
      if (state.history.length) {
        reset.hideWelcome();
        for (const t of state.history) {
          const body = addMsg(t.role === "user" ? "user" : "assistant", t.role === "user" ? cbSessionText("你") : "岗位", t.content);
          if (t.role !== "user" && typeof paint.markdown === "function") paint.markdown(body, t.content);
          if (t.role === "assistant") restoredBody = body;
          else restoredMessage = t.content;
        }
      } else {
        /* 诚实：没有留存正文就明说，不假装接上了 */
        addStatus(cbSessionText("这条会话没有留存对话正文；上文从此刻重新开始。"));
      }
      if (d.collaboration || d.route && (d.route.reason || d.route.ambiguous)) {
        if (!restoredBody) restoredBody = addMsg("assistant", cbSessionText("本会话任务安排"), cbSessionText("已恢复留存的任务状态。"));
        if (d.route && (d.route.reason || d.route.ambiguous)) paint.routePaint(d.route, restoredBody, restoredMessage);
        if (d.collaboration) paint.collaborationPaint(d.collaboration, restoredBody);
      }
      const files = Array.isArray(d.deliverables) ? d.deliverables.filter((file) => file && typeof file.path === "string" && file.path) : [];
      if (files.length) {
        paint.setLastDeliverables(files);
        reset.hideWelcome();
        const runs = Array.isArray(d.deliverable_runs) ? d.deliverable_runs : [];
        const intro = runs.length > 1 ? `已恢复 ${runs.length} 轮留存的草稿（最近的在前），可继续预览或下载。` : cbSessionText("已恢复留存的草稿，可继续预览或下载。");
        paint.appendDocCards(files, addMsg("assistant", cbSessionText("本会话交付物"), intro), { runs });
      }
      if (d.truncated) addStatus(cbSessionText("列表只展示近期对话节选。可在「任务记忆与本地搜索」找回已保留的历史原文。"));
      if (d.turn_state && d.turn_state.active) {
        addStatus(cbSessionText("这个任务仍在后台运行，完成后会自动显示结果。"));
        hooks.attachToTurn(d.session_id, "");
      } else if (d.turn_state && d.turn_state.state === "stale") {
        /* 服务重启时这一轮还在跑：它不会再有结果了，别让人以为还在等 */
        addStatus(cbSessionText("上一轮在服务重启时被中断，已有内容已保留；需要的话重新发送一次。"));
      }
      if (d.context && (d.context.note || Number(d.context.limit) > 0)) reset.paintContext(d.context);
      else reset.paintContext(reset.estimateLocalContext());
      if (log) log.scrollTop = log.scrollHeight;
      hooks.render();
    } catch (e) {
      if (request !== navRequest.current()) return;
      addStatus(cbSessionText("载入会话失败：") + ((e && e.message) || e));
    }
  }

  return { sessionId, rememberSession, rememberedSession, resumeSession, newLocalSession, openLoad, openSave,
    loadThreads, renderProjects, renameProject, openSession, sessionsOf, renderCadProject };
}
