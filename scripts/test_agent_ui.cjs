#!/usr/bin/env node
/* Agent page against the HTTP contract, with real DOM events and no model or server. */
"use strict";
const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { JSDOM } = require("jsdom");
const { createAgentWorkbench, artifactUrl } = require("../demo/static/agent.js");
const html = fs.readFileSync(path.join(__dirname, "../demo/static/agent.html"), "utf8");
const caps = { available: true, models: { configured: false }, modes: ["steps", "model"], sandbox: ["read-only", "workspace-write"],
  features: { cancel: true, context: true, subagents: true, documents: true }, sandbox_controls: { policy: true, os_enforced: false } };
const response = (value, status = 200) => new Response(JSON.stringify(value), { status, headers: { "Content-Type": "application/json" } });
const storage = () => { const values = new Map(); return { getItem: (key) => values.get(key) || null, setItem: (key, value) => values.set(key, String(value)), values }; };
const event = (seq, kind, data = {}) => ({ seq, kind, data });

function harness(route = () => undefined, saved = storage()) {
  const dom = new JSDOM(html, { url: "http://localhost/static/agent.html", runScripts: "outside-only" });
  const doc = dom.window.document, calls = [], timers = new Map();
  let timerId = 0, sid = 0;
  const fetch = async (url, init) => {
    calls.push({ url, init, body: init?.body ? JSON.parse(init.body) : undefined });
    const custom = await route(url, init);
    if (custom !== undefined) return custom;
    if (url === "/api/agent/capabilities") return response(caps);
    if (url === "/api/catalog") return response({ experts: [{ id: "plans", name: "施工方案", category_name: "施工", enabled: true, risk: "high" }, { id: "checks", name: "资料检查", category_name: "资料", enabled: true, risk: "low" }] });
    if (url.startsWith("/api/agent/engineering/projects?")) return response({ projects: [] });
    if (url === "/api/llm-config" && !init) return response({ configured: false, model: "", base_url: "https://example.invalid/v1" });
    if (url === "/api/agent/workspaces") return response({ workspace: { id: "workspace-1", root: "C:/engineering" }, capabilities: caps });
    if (url.startsWith("/api/agent/files?")) return response({ files: [{ path: "方案.docx", name: "方案.docx", size: 120 }, { path: "计划.xlsx", name: "计划.xlsx", size: 240 }] });
    if (url.startsWith("/api/agent/turns?")) return response({ turns: [] });
    throw new Error("unexpected request: " + url);
  };
  const app = createAgentWorkbench({ document: doc, window: dom.window, fetch, storage: saved,
    makeSessionId: () => `session-${++sid}`, setTimeout: (fn) => { const id = ++timerId; timers.set(id, fn); return id; }, clearTimeout: (id) => timers.delete(id) });
  const h = { app, doc, win: dom.window, calls, timers, saved, $: (id) => doc.getElementById(id),
    async ready() { await app.start(); if (!app.state.workspace) await app.openWorkspace("C:/engineering"); },
    selectFirst() { const input = doc.querySelector("#agentFiles input"); input.checked = true; input.dispatchEvent(new dom.window.Event("change")); },
    async tick() { const [id, fn] = timers.entries().next().value || []; assert.ok(fn, "a poll was scheduled"); timers.delete(id); await fn(); },
    close() { app.dispose(); dom.window.close(); } };
  return h;
}

test("Agent UI: unavailable capability is honest and no task can be submitted", async (t) => {
  const h = harness((url) => ["/api/agent/capabilities", "/api/llm-config"].includes(url) ? response({ detail: "Not Found" }, 404) : undefined); t.after(h.close);
  await h.app.start();
  assert.match(h.$("agentCapability").textContent, /未启用/);
  assert.equal(h.$("agentSend").disabled, true);
  assert.equal(h.$("agentModelLabel").textContent, "暂不可用");
  h.$("agentMessage").value = "请改稿"; await h.app.send();
  assert.equal(h.calls.some((call) => call.url === "/api/agent/turns"), false);
});

test("Agent UI: identity and audit actors are displayed as text without claiming multi-tenant hosting", async (t) => {
  const identity = { mode: "named_single_user_instance", user_id: "alice", multi_tenant: false };
  const h = harness((url, init) => {
    if (url === "/api/agent/capabilities") return response({ ...caps, identity });
    if (url === "/api/agent/workspaces") return response({ workspace: { id: "workspace-1", root: "C:/engineering" }, capabilities: { ...caps, identity } });
    if (url.startsWith("/api/agent/turns?")) return response({ turns: [{ turn_id: "audit-turn", status: "completed", actor_id: "alice" }] });
    if (url === "/api/agent/turns" && init) return response({ turn_id: "audit-turn", session_id: JSON.parse(init.body).session_id });
    if (url.includes("/audit-turn/events?")) return response({ events: [{ ...event(1, "authorization", { professional_signoff: false }), actor_id: "<b>alice</b>" }], turn: { status: "completed", actor_id: "alice" } });
  }); t.after(h.close);
  await h.ready(); assert.match(h.$("agentIdentity").textContent, /alice.*非共享多人服务/);
  assert.match(h.$("agentTurns").textContent, /发起者：alice/);
  h.$("agentMessage").value = "检查资料"; await h.app.send();
  assert.match(h.$("agentTurnActor").textContent, /alice/); assert.match(h.$("agentEvents").textContent, /本轮权限记录.*发起者：<b>alice<\/b>/);
  assert.equal(h.$("agentEvents").querySelector("b"), null);
});

test("Agent UI: worker write confinement does not imply read or network confinement", async (t) => {
  const h = harness((url) => url === "/api/agent/capabilities" ? response({ ...caps, sandbox_controls: { policy: true, os_enforced: true, reads_confined: false, network_confined: false } }) : undefined); t.after(h.close);
  await h.app.start();
  assert.match(h.$("agentSandboxNote").textContent, /工作进程.*写入隔离/);
  assert.match(h.$("agentSandboxNote").textContent, /不限制读取范围.*不隔离网络访问/);
  assert.equal(h.doc.querySelector('nav a').getAttribute('href'), '/static/index.html');
});

test("Agent UI: canonical Windows paths retain identity without leaking verbatim prefixes into the form", async (t) => {
  const root = "\\\\?\\C:\\engineering";
  const h = harness((url) => url === "/api/agent/workspaces" ? response({ workspace: { id: "workspace-1", root }, capabilities: caps }) : undefined); t.after(h.close);
  await h.ready();
  assert.equal(h.app.state.workspace.root, root);
  assert.equal(h.$("agentWorkspacePath").value, "C:\\engineering");
  assert.equal(h.$("agentWorkspaceStatus").textContent, "C:\\engineering");
});

test("Agent UI: selected inputs, real context, tools, subtasks and saved artifacts follow the event contract", async (t) => {
  const artifact = { id: "artifact-1", name: '<img src=x onerror="bad()">.docx', url: "/api/agent/artifacts/artifact-1?workspace=workspace-1",
    source: "方案.docx", source_sha256: "source-hash", output_sha256: "output-hash", provenance: "model_proposed", validation: { schema: "passed", render: "not_available" } };
  const h = harness((url, init) => {
    if (url === "/api/agent/turns" && init) return response({ turn_id: "turn-1", session_id: JSON.parse(init.body).session_id });
    if (url.includes("/turn-1/events?")) return response({ events: [event(1, "turn.started"), event(2, "context", { used: 1000, limit: 8000, reserve: 2000, estimated: true, scope: "request", omitted_history_messages: 2 }),
      event(3, "tool_started", { tool: "docx.inspect" }), event(3, "tool_started", { tool: "must-not-repeat" }), event(4, "subtask_started", { name: "核对资料缺项" }),
      event(5, "subtask_finished", { summary: "未提供试验记录" }), event(6, "tool_finished", { tool: "docx.inspect" }), event(7, "artifact", artifact), event(8, "turn.completed")],
      turn: { status: "completed", result: { reply: "<script>bad()</script>待核对原文。", partial: true, artifacts: [artifact], usage: { model_calls: 0 } } } });
  }); t.after(h.close);
  await h.ready(); h.selectFirst(); h.$("agentMessage").value = "读取方案"; await h.app.send();
  const sent = h.calls.find((call) => call.url === "/api/agent/turns").body;
  assert.deepEqual(sent.files, ["方案.docx"]); assert.equal(sent.mode, "steps"); assert.equal(sent.sandbox, "read-only");
  assert.equal(h.$("agentTurnStatus").textContent, "已完成"); assert.equal(h.timers.size, 0);
  assert.equal(h.$("agentEvents").children.length, 8); assert.doesNotMatch(h.$("agentEvents").textContent, /must-not-repeat/);
  assert.match(h.$("agentContextText").textContent, /输入估算 1,000 \/ 6,000/); assert.match(h.$("agentContextText").textContent, /省略 2 条/);
  assert.equal(h.$("agentContextMeter").value, 1000); assert.equal(h.$("agentContextMeter").max, 6000);
  assert.equal(h.$("agentPartial").hidden, false); assert.equal(h.$("agentReply").querySelector("script"), null);
  assert.equal(h.$("agentArtifacts").children.length, 1); assert.equal(h.$("agentArtifacts").querySelector("img"), null);
  assert.match(h.$("agentArtifacts").querySelector("a").href, /\/api\/agent\/artifacts\/artifact-1\?workspace=workspace-1$/);
  assert.match(h.$("agentArtifacts").textContent, /source-hash/); assert.match(h.$("agentArtifacts").textContent, /not_available/);
  assert.match(h.$("agentSandboxNote").textContent, /未声明操作系统强制隔离/);
});

test("Agent UI: a failed poll resumes after the last sequence without inventing completion", async (t) => {
  let reads = 0;
  const h = harness((url, init) => {
    if (url === "/api/agent/turns" && init) return response({ turn_id: "turn-retry", session_id: JSON.parse(init.body).session_id });
    if (url.includes("/turn-retry/events?")) {
      reads++;
      if (reads === 1) return response({ events: [event(1, "turn.started"), event(2, "model", { model: "configured-model" })], turn: { status: "running" } });
      assert.ok(url.endsWith("after_seq=2"));
      if (reads === 2) throw new TypeError("connection lost");
      return response({ events: [event(2, "model", { model: "duplicate" }), event(3, "done", { reply: "完成" })], turn: { status: "completed", result: { reply: "完成" } } });
    }
  }); t.after(h.close);
  await h.ready(); h.$("agentMessage").value = "检查"; await h.app.send(); await h.tick();
  assert.equal(h.$("agentTurnStatus").textContent, "执行中"); assert.match(h.$("agentNotice").textContent, /恢复/);
  await h.tick(); assert.equal(h.$("agentEvents").children.length, 3); assert.equal(h.$("agentReply").textContent, "完成"); assert.equal(h.timers.size, 0);
});

test("Agent UI: changing session detaches a pending response and cancellation remains scoped to its original task", async (t) => {
  let finishCancel, finishPoll, reads = 0;
  const h = harness((url, init) => {
    if (url === "/api/agent/turns" && init) return response({ turn_id: "turn-old", session_id: JSON.parse(init.body).session_id });
    if (url.includes("/turn-old/events?")) {
      if (++reads === 1) return response({ events: [event(1, "turn.started")], turn: { status: "running" } });
      return new Promise((resolve) => { finishPoll = resolve; });
    }
    if (url.includes("/turn-old/cancel?")) return new Promise((resolve) => { finishCancel = resolve; });
  }); t.after(h.close);
  await h.ready(); h.$("agentMessage").value = "原任务"; await h.app.send();
  const oldSession = h.app.state.session, polling = h.tick(), cancelling = h.app.cancel();
  await new Promise((resolve) => setImmediate(resolve));
  await h.app.changeSession("", true);
  const newSession = h.app.state.session;
  finishCancel(response({ ok: true })); await cancelling;
  finishPoll(response({ events: [event(2, "done", { reply: "不应出现" })], turn: { status: "cancelled" } })); await polling;
  assert.notEqual(oldSession, newSession); assert.equal(h.$("agentTurnStatus").textContent, "尚未开始"); assert.equal(h.$("agentEvents").children.length, 0);
  const cancel = h.calls.find((call) => call.url.includes("/cancel?")); assert.match(cancel.url, new RegExp(`workspace=workspace-1&session_id=${oldSession}$`));
  assert.doesNotMatch(h.$("agentReply").textContent, /不应出现/); assert.equal(h.timers.size, 0);
});

test("Agent UI: refresh restores the workspace, selected files and completed turn without starting it again", async (t) => {
  const saved = storage(); let starts = 0;
  const route = (url, init) => {
    if (url === "/api/agent/turns" && init) { starts++; return response({ turn_id: "saved-turn", session_id: JSON.parse(init.body).session_id }); }
    if (url.includes("/saved-turn/events?")) return response({ events: [event(1, "turn.interrupted")], turn: { status: "interrupted", result: { reply: "已保留原文", partial: true } } });
    if (url.includes("/saved-turn?")) return response({ turn: { turn_id: "saved-turn", status: "interrupted", result: { reply: "已保留原文", partial: true } } });
  };
  const first = harness(route, saved); await first.ready(); first.selectFirst(); first.$("agentMessage").value = "读取"; await first.app.send(); const session = first.app.state.session; first.close();
  const second = harness(route, saved); t.after(second.close); await second.app.start();
  assert.equal(second.app.state.session, session); assert.equal(second.doc.querySelector("#agentFiles input").checked, true);
  assert.equal(second.$("agentTurnStatus").textContent, "服务重启时中断"); assert.equal(second.$("agentPartial").hidden, false);
  assert.equal(second.$("agentReply").textContent, "已保留原文"); assert.equal(starts, 1); assert.equal(second.timers.size, 0);
});

test("Agent UI: cancelling is a request until the server confirms, and partial artifacts remain downloadable", async (t) => {
  let stopped = false;
  const h = harness((url, init) => {
    if (url === "/api/agent/turns" && init) return response({ turn_id: "cancel-turn", session_id: JSON.parse(init.body).session_id });
    if (url.includes("/cancel-turn/cancel?")) { stopped = true; return response({ ok: true }); }
    if (url.includes("/cancel-turn/events?")) return response({ events: [], turn: { status: stopped ? "cancelled" : "running",
      result: stopped ? { reply: "已停止", partial: true, artifacts: [{ id: "partial", name: "已保存.docx", url: "/api/agent/artifacts/partial?workspace=workspace-1" }] } : null } });
  }); t.after(h.close);
  await h.ready(); h.$("agentMessage").value = "生成"; await h.app.send(); await h.app.cancel();
  assert.equal(h.$("agentTurnStatus").textContent, "正在停止"); assert.equal(h.$("agentCancel").disabled, true);
  await h.tick(); assert.equal(h.$("agentTurnStatus").textContent, "已停止"); assert.equal(h.$("agentCancel").hidden, true);
  assert.ok(h.$("agentArtifacts").querySelector("a")); assert.equal(h.$("agentPartial").hidden, false);
});

test("Agent UI: model mode needs a configured model; saving does not retain or echo its key", async (t) => {
  let configured = false, posted;
  const h = harness((url, init) => {
    if (url === "/api/llm-config" && init) { posted = JSON.parse(init.body); configured = true; return response({ ok: true }); }
    if (url === "/api/llm-config") return response({ configured, model: configured ? "configured-flash" : "", base_url: "https://provider.invalid/v1", key_masked: configured ? "****" : "" });
  }); t.after(h.close);
  await h.ready(); h.$("agentMode").value = "model"; h.$("agentMode").dispatchEvent(new h.doc.defaultView.Event("change")); assert.equal(h.$("agentSend").disabled, true);
  h.$("agentModelName").value = "configured-flash"; h.$("agentModelKey").value = "private-fixture-key"; await h.app.saveModel();
  assert.equal(posted.api_key, "private-fixture-key"); assert.equal(h.$("agentModelKey").value, ""); assert.equal(h.$("agentSend").disabled, false);
  assert.doesNotMatch([...h.saved.values.values()].join(""), /private-fixture-key/); assert.doesNotMatch(h.doc.body.textContent, /private-fixture-key/);
});

test("Agent UI: artifact downloads must stay on this host and in this workspace", () => {
  assert.equal(artifactUrl("https://evil.invalid/api/agent/artifacts/a?workspace=w", "http://localhost", "w"), "");
  assert.equal(artifactUrl("javascript:alert(1)", "http://localhost", "w"), "");
  assert.equal(artifactUrl("/api/agent/artifacts/a?workspace=another", "http://localhost", "w"), "");
  assert.equal(artifactUrl("/api/file?path=secret", "http://localhost", "w"), "");
  assert.equal(artifactUrl("/api/agent/artifacts/a?workspace=w", "http://localhost", "w"), "http://localhost/api/agent/artifacts/a?workspace=w");
});

test("Agent voice: a transcript is only a draft; late results cannot cross session or workspace", async (t) => {
  const h = harness(); t.after(h.close); await h.ready();
  const voiceCalls = []; let finish;
  h.win.fetch = async (url, init) => {
    voiceCalls.push({ url, init });
    if (url === "/api/asr/status") return response({ available: true, state: "ready", supports_cancel: true });
    if (url.endsWith("/cancel")) return response({ ok: true, status: "cancelled" });
    if (url === "/api/asr") return new Promise((resolve) => { finish = (text) => resolve(response({ text, elapsed_seconds: 1 })); });
    throw new Error("unexpected voice request " + url);
  };
  Object.defineProperty(h.win.navigator, "mediaDevices", { value: { getUserMedia: async () => ({ getTracks: () => [{ stop() {} }] }) } });
  h.win.MediaRecorder = class {
    static isTypeSupported() { return true; }
    constructor() { this.mimeType = "audio/webm"; this.state = "inactive"; }
    start() { this.state = "recording"; }
    stop() { this.state = "inactive"; this.ondataavailable?.({ data: new h.win.Blob(["audio"]) }); this.onstop?.(); }
  };
  h.win.eval(fs.readFileSync(path.join(__dirname, "../demo/static/voice.js"), "utf8"));
  await h.app.changeSession("", true);
  assert.equal(h.$("voiceStatus").textContent, "", "idle navigation should not claim voice was cancelled");
  const flush = async () => { for (let i = 0; i < 25; i++) await Promise.resolve(); };
  async function transcribing() { h.$("btnVoice").click(); await flush(); h.$("btnVoice").click(); await flush(); assert.ok(finish); }
  await transcribing(); finish("核对混凝土资料"); await flush();
  assert.equal(h.$("agentMessage").value, "核对混凝土资料");
  assert.equal(h.calls.filter((c) => c.url === "/api/agent/turns").length, 0);
  await transcribing(); await h.app.changeSession("", true); h.$("agentMessage").value = "新会话";
  finish("旧会话转写"); await flush(); assert.equal(h.$("agentMessage").value, "新会话");
  await transcribing(); await h.app.openWorkspace("C:/other-engineering"); h.$("agentMessage").value = "新工程";
  finish("旧工程转写"); await flush(); assert.equal(h.$("agentMessage").value, "新工程");
  const requests = voiceCalls.filter((c) => c.url === "/api/asr");
  assert.equal(requests.length, 3);
  for (const request of requests.slice(1)) {
    assert.ok(request.init.signal.aborted);
    assert.ok(voiceCalls.some((c) => c.url === "/api/asr/" + request.init.headers["X-Civil-ASR-ID"] + "/cancel"));
  }
  assert.equal(h.calls.filter((c) => c.url === "/api/agent/turns").length, 0);
});

const project = (kind, id, status = "ready") => ({ kind, project_id: id, name: `<工程 ${id}>`, revision: 1, status, confirmation_required: kind === "cad_section" });
const inspection = (row, revision = 1) => ({ selection: { kind: row.kind, project_id: row.project_id, revision, source_sha256: row.kind === "cad_section" ? "a".repeat(64) : null, inputs_sha256: String(revision).repeat(64), confirmed_solid: false },
  summary: { note: "工程原始输入；不允许模型修改几何" }, status: row.status === "missing_inputs" ? "missing_inputs" : row.kind === "cad_section" ? "confirmation_required" : "ready",
  missing_inputs: row.status === "missing_inputs" ? ["elastic_modulus"] : [], confirmation_required: row.kind === "cad_section" });
function engineeringInput(h, key, label) {
  const card = [...h.doc.querySelectorAll("#agentEngineering article")].find((node) => node.dataset.project === key);
  return [...card.querySelectorAll("label")].find((node) => node.textContent === label).querySelector("input");
}
function change(h, input, value) { input.checked = value; input.dispatchEvent(new h.win.Event("change")); }
function selectValue(h, id, value) { h.$(id).value = value; h.$(id).dispatchEvent(new h.win.Event("change")); }
function terminalRoute(url, init) {
  if (url === "/api/agent/turns" && init) return response({ turn_id: "engineering-turn", session_id: JSON.parse(init.body).session_id });
  if (url.includes("/engineering-turn/events?")) return response({ events: [event(1, "turn.completed")], turn: { status: "completed", result: { reply: "已核对" } } });
}

test("Engineering selection: inspect before selection, explicit CAD snapshot confirmation, missing-input block and maximum four", async (t) => {
  const rows = [project("cad_section", "cad-1"), project("saved_frame", "missing", "missing_inputs"), ...[1, 2, 3, 4].map((n) => project("saved_frame", "frame-" + n))];
  const h = harness((url, init) => {
    if (url.startsWith("/api/agent/engineering/projects?")) return response({ projects: rows });
    if (url.startsWith("/api/agent/engineering/projects/")) return response(inspection(rows.find((row) => new URL(url, "http://localhost").pathname.endsWith("/" + row.project_id))));
    return terminalRoute(url, init);
  }); t.after(h.close); await h.ready();
  assert.equal(h.doc.querySelectorAll("#agentEngineering input").length, 0);
  for (const row of h.app.state.engineeringRows) await h.app.inspectEngineering(row);
  assert.equal(engineeringInput(h, "cad_section:cad-1", "加入本次任务").disabled, true);
  assert.equal(engineeringInput(h, "saved_frame:missing", "加入本次任务").disabled, true);
  assert.match(h.$("agentEngineering").textContent, /elastic_modulus/);
  change(h, engineeringInput(h, "cad_section:cad-1", "我已核对当前截面的实体区域与孔洞"), true);
  change(h, engineeringInput(h, "cad_section:cad-1", "加入本次任务"), true);
  for (const n of [1, 2, 3, 4]) change(h, engineeringInput(h, `saved_frame:frame-${n}`, "加入本次任务"), true);
  assert.equal(h.app.state.engineeringRows.filter((row) => row.selected).length, 4);
  assert.match(h.$("agentEngineeringStatus").textContent, /最多选择 4/);
  selectValue(h, "agentExpert", "checks"); h.$("agentMessage").value = "核对截面与框架"; await h.app.send();
  const payload = h.calls.find((call) => call.url === "/api/agent/turns").body;
  assert.equal(payload.engineering.length, 4); assert.equal(payload.engineering[0].confirmed_solid, true);
  assert.equal(payload.engineering[0].inputs_sha256, "1".repeat(64)); assert.equal(payload.expert_id, "checks");
  assert.deepEqual(Object.keys(payload.engineering[0]).sort(), ["confirmed_solid", "inputs_sha256", "kind", "project_id", "revision", "source_sha256"]);
  await h.app.inspectEngineering(h.app.state.engineeringRows[0]);
  assert.equal(engineeringInput(h, "cad_section:cad-1", "我已核对当前截面的实体区域与孔洞").checked, false);
  assert.equal(engineeringInput(h, "cad_section:cad-1", "加入本次任务").disabled, true);
});

test("Engineering selection: changed revision before submission invalidates confirmation and submits nothing", async (t) => {
  const row = project("cad_section", "cad-1"); let revision = 1;
  const h = harness((url) => {
    if (url.startsWith("/api/agent/engineering/projects?")) return response({ projects: [row] });
    if (new URL(url, "http://localhost").pathname.endsWith("/cad_section/cad-1")) return response(inspection(row, revision));
  }); t.after(h.close); await h.ready(); await h.app.inspectEngineering(h.app.state.engineeringRows[0]);
  change(h, engineeringInput(h, "cad_section:cad-1", "我已核对当前截面的实体区域与孔洞"), true);
  change(h, engineeringInput(h, "cad_section:cad-1", "加入本次任务"), true);
  revision = 2; h.$("agentMessage").value = "计算已核对截面"; await h.app.send();
  assert.equal(h.calls.filter((call) => call.url === "/api/agent/turns").length, 0);
  assert.equal(h.$("agentMessage").value, "计算已核对截面"); assert.match(h.$("agentNotice").textContent, /任务尚未提交/);
  assert.equal(h.app.state.engineeringRows[0].snapshot.revision, 2);
  assert.equal(engineeringInput(h, "cad_section:cad-1", "我已核对当前截面的实体区域与孔洞").checked, false);
  assert.equal(engineeringInput(h, "cad_section:cad-1", "加入本次任务").disabled, true);
});

test("Engineering selection: list, inspect and pre-submit verification use the active workspace scope", async (t) => {
  const row = project("cad_section", "cad-1");
  const h = harness((url, init) => {
    if (url === "/api/agent/workspaces") {
      const root = JSON.parse(init.body).path;
      return response({ workspace: { id: root === "C:/second job" ? "workspace-2" : "workspace-1", root }, capabilities: caps });
    }
    if (url.startsWith("/api/agent/engineering/projects?")) return response({ projects: [row] });
    if (url.startsWith("/api/agent/engineering/projects/cad_section/")) return response(inspection(row));
    return terminalRoute(url, init);
  }); t.after(h.close); await h.ready();
  await h.app.inspectEngineering(h.app.state.engineeringRows[0]);
  const firstCalls = h.calls.filter((call) => call.url.startsWith("/api/agent/engineering/projects"));
  assert.equal(firstCalls.length, 2);
  for (const call of firstCalls) assert.equal(new URL(call.url, "http://localhost").searchParams.get("workspace"), "workspace-1");
  const beforeSwitch = h.calls.length;
  await h.app.openWorkspace("C:/second job");
  await h.app.inspectEngineering(h.app.state.engineeringRows[0]);
  change(h, engineeringInput(h, "cad_section:cad-1", "我已核对当前截面的实体区域与孔洞"), true);
  change(h, engineeringInput(h, "cad_section:cad-1", "加入本次任务"), true);
  h.$("agentMessage").value = "核对选中工程"; await h.app.send();
  const secondCalls = h.calls.slice(beforeSwitch).filter((call) => call.url.startsWith("/api/agent/engineering/projects"));
  assert.equal(secondCalls.length, 3, "list, explicit inspect and pre-submit verification");
  for (const call of secondCalls) {
    const query = new URL(call.url, "http://localhost").searchParams;
    assert.equal(query.get("workspace"), "workspace-2");
    assert.equal(query.get("session_id"), h.app.state.session);
  }
  assert.equal(h.calls.find((call) => call.url === "/api/agent/turns").body.workspace, "workspace-2");
});

test("Engineering selection: delayed inspection cannot restore authorization after a new session", async (t) => {
  const row = project("cad_section", "cad-1"); let finish;
  const h = harness((url) => {
    if (url.startsWith("/api/agent/engineering/projects?")) return response({ projects: [row] });
    if (new URL(url, "http://localhost").pathname.endsWith("/cad_section/cad-1")) return new Promise((resolve) => { finish = () => resolve(response(inspection(row))); });
  }); t.after(h.close); await h.ready();
  const pending = h.app.inspectEngineering(h.app.state.engineeringRows[0]);
  await h.app.changeSession("", true); finish(); await pending;
  assert.equal(h.app.state.engineeringRows[0].detail, null);
  assert.equal(h.doc.querySelectorAll("#agentEngineering input").length, 0);
  assert.equal(h.$("agentSend").disabled, false);
});

test("Expert selection: high-risk writes require explicit phrase for each turn; read-only and ordinary work do not", async (t) => {
  const h = harness(terminalRoute); t.after(h.close); await h.ready();
  selectValue(h, "agentExpert", "plans"); h.$("agentMessage").value = "检查方案";
  assert.equal(h.$("agentRiskWrap").hidden, true); assert.equal(h.$("agentSend").disabled, false);
  selectValue(h, "agentSandbox", "workspace-write");
  assert.equal(h.$("agentRiskWrap").hidden, false); assert.equal(h.$("agentSend").disabled, true);
  h.$("agentRiskConfirmation").value = "我明白"; h.$("agentRiskConfirmation").dispatchEvent(new h.win.Event("input"));
  assert.equal(h.$("agentSend").disabled, true);
  h.$("agentRiskConfirmation").value = "我明白，将由持证人员签认"; h.$("agentRiskConfirmation").dispatchEvent(new h.win.Event("input"));
  assert.equal(h.$("agentSend").disabled, false); await h.app.send();
  const payload = h.calls.find((call) => call.url === "/api/agent/turns").body;
  assert.equal(payload.expert_id, "plans"); assert.equal(payload.risk_confirmation, "我明白，将由持证人员签认");
  assert.equal(h.$("agentRiskConfirmation").value, ""); assert.equal(h.$("agentRiskWrap").hidden, false); assert.equal(h.$("agentSend").disabled, true);
  await h.app.changeSession("", true); assert.equal(h.$("agentRiskWrap").hidden, false); assert.equal(h.$("agentSend").disabled, true);
  selectValue(h, "agentExpert", "checks"); assert.equal(h.$("agentRiskWrap").hidden, true); assert.equal(h.$("agentSend").disabled, false);
});

// Captured numeric fields from the real synthetic 400 x 600 mm rectangle and
// saved 4 m beam acceptance fixtures; rendering tests never run a solver.
const engineeringRenderFixtures = () => [
  { call_id: "rectangle-result", ok: true, provenance: { kind: "cad_section", project_id: "16ad9512626b4613a38dc2e11617bea8", revision: 1 }, result: {
    kind: "section", unit: "mm", regions: [{ layer: "UI_SECTION", area_mm2: 240000.00000000015, centroid_source: [200.00000000000003, 300.00000000000006], Ixx_mm4: 7200000000.000008, Iyy_mm4: 3200000000, hole_ids: [] }] } },
  { call_id: "frame-result", ok: true, provenance: { kind: "saved_frame", project_id: "681fedaa6ce6471d8c9b94abfc5e5bc3", revision: 1 }, result: {
    kind: "frame", analysis: "linear_elastic_frame", units: { length: "m", force: "N", moment: "N*m" }, combinations: [{ id: "C1",
      nodes: [{ id: "N1", reaction_N: [0, 2000.0000000000002, 0] }, { id: "N2", reaction_N: [0, 1999.9999999999998, 0] }],
      members: [{ id: "B1", sampled_extrema: { moment_y_Nm: { min: 0, max: 0 }, moment_z_Nm: { min: -2000, max: 2.2737367544323206e-13 }, dy_m: { min: -0.0020833333333333333, max: 0 }, dz_m: { min: 0, max: 0 } } }] }] } },
];

test("Engineering results: real rectangle and beam values become concise cards and replay does not duplicate them", async (t) => {
  const findings = engineeringRenderFixtures();
  const h = harness((url, init) => {
    if (url === "/api/agent/turns" && init) return response({ turn_id: "render-turn", session_id: JSON.parse(init.body).session_id });
    if (url.includes("/render-turn/events?")) return response({ events: findings.map((result, index) => event(index + 1, "tool_finished", { name: "engineering_analyze", result })), turn: { status: "completed", result: { reply: "已完成确定性计算", findings } } });
  }); t.after(h.close); await h.ready(); h.$("agentMessage").value = "展示已保存结果"; await h.app.send();
  const cards = [...h.doc.querySelectorAll(".engineering-result")];
  assert.equal(cards.length, 2, "tool events and terminal findings must share the same cards");
  const metrics = (card) => Object.fromEntries([...card.querySelectorAll("dl>div")].map((row) => [row.querySelector("dt").textContent, row.querySelector("dd").textContent]));
  assert.match(cards[0].textContent, /截面几何计算.*16ad9512626b4613a38dc2e11617bea8.*修订 1/);
  assert.deepEqual(metrics(cards[0]), { "面积（mm²）": "240000", "形心（原图 x, y；mm）": "200, 300", "Ixx（mm⁴）": "7200000000", "Iyy（mm⁴）": "3200000000" });
  const beam = metrics(cards[1]);
  assert.equal(beam["节点 N1 反力（N）"], "FX 0 · FY 2000 · FZ 0");
  assert.equal(beam["节点 N2 反力（N）"], "FX 0 · FY 2000 · FZ 0");
  assert.equal(beam["弯矩 Mz（N*m）"], "最小 -2000 · 最大 0.000000000000227373675443");
  assert.equal(beam["挠度 dy（m）"], "最小 -0.00208333333333 · 最大 0");
  assert.match(cards[1].textContent, /有符号采样极值/);
  assert.equal(h.doc.querySelectorAll(".engineering-result pre").length, 0);
  assert.equal(h.doc.querySelectorAll("#agentEvents details pre").length, 2, "full precision remains in expandable events");
  await h.app.changeSession("", true);
  assert.equal(h.$("agentEngineeringResults").hidden, true);
  assert.equal(h.doc.querySelectorAll(".engineering-result").length, 0);
});

test("Engineering results: missing values are not calculated from curves and returned strings remain plain text", async (t) => {
  const [section, frame] = engineeringRenderFixtures();
  section.result.regions[0].layer = '<img src=x onerror="bad()">';
  delete section.result.regions[0].centroid_source;
  delete frame.result.combinations[0].members[0].sampled_extrema;
  frame.result.combinations[0].members[0].curves = { moment_z_Nm: [-999999, 0], dy_m: [-123456, 0] };
  const h = harness((url, init) => {
    if (url === "/api/agent/turns" && init) return response({ turn_id: "missing-turn", session_id: JSON.parse(init.body).session_id });
    if (url.includes("/missing-turn/events?")) return response({ events: [], turn: { status: "completed", result: { findings: [section, frame, { ...section, call_id: "failed-result", ok: false }] } } });
  }); t.after(h.close); await h.ready(); h.$("agentMessage").value = "检查返回字段"; await h.app.send();
  const host = h.$("agentEngineeringResults");
  assert.equal(host.querySelectorAll("article").length, 2);
  assert.equal(host.querySelectorAll("img,script").length, 0);
  assert.match(host.textContent, /<img src=x onerror="bad\(\)">/);
  assert.match(host.textContent, /形心（原图 x, y；mm）未返回/);
  assert.match(host.textContent, /工具未返回采样极值/);
  assert.doesNotMatch(host.textContent, /999999|123456/);
});
