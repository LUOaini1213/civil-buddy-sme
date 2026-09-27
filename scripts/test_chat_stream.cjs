#!/usr/bin/env node
/* Page-logic tests: the whole page (demo/static/app.js + demo/static/modules/*.js, loaded as
   classic scripts) runs in a Node vm with a fake DOM and a fake fetch. boot() is not run and a
   few observation stubs (addMsg / addStatus / cbAnnounce / …) replace the page's own so tests
   can watch what happened; a test that wants the page's version says `x = __real.x`.
   No source slicing: this file does not depend on comment markers in app.js.
   Real page + real backend lives in scripts/e2e/ui_dom.cjs; pure modules in scripts/test_modules.cjs. */
"use strict";

// Offline behavioral regressions for the shipped transport and conversation UI.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { test } = require("node:test");
const transport = require("../demo/static/chat-stream.js");
const encoder = new TextEncoder();
const app = fs.readFileSync(path.join(__dirname, "../demo/static/app.js"), "utf8");

function bytesStream(bytes, chunkSize = 1) {
  return new ReadableStream({
    start(controller) {
      for (let i = 0; i < bytes.length; i += chunkSize) controller.enqueue(bytes.slice(i, i + chunkSize));
      controller.close();
    },
  });
}

const frame = (name, data) => "event: " + name + "\ndata: " + JSON.stringify(data) + "\n\n";

test("SSE handles UTF-8, CRLF boundaries, multiline data, comments and event reset", async () => {
  const text = ': heartbeat\r\nevent:token\r\ndata:{\r\ndata: "text":"中文回答"}\r\n\r\n' +
    'event: ignored\r\n\r\ndata:default\n\nevent:done\rdata:ok\r\r';
  for (const chunkSize of [1, 2, 7, 1024]) {
    const events = [];
    const body = bytesStream(encoder.encode(text), chunkSize);
    await transport.read(body, (name, data) => events.push([name, data]));
    assert.deepEqual(events, [["token", '{\n"text":"中文回答"}'], ["message", "default"], ["done", "ok"]]);
    assert.equal(body.locked, false);
  }
});

test("SSE does not invent a completed event from an unterminated final frame", async () => {
  const events = [];
  await transport.read(bytesStream(encoder.encode('event: done\ndata: {"text":"partial"}\n')), (...ev) => events.push(ev));
  assert.deepEqual(events, []);
});

test("SSE cancels and releases its reader when a handler fails", async () => {
  let cancelled = 0;
  const body = new ReadableStream({
    start(controller) { controller.enqueue(encoder.encode(frame("error", { text: "failure" }))); },
    cancel() { cancelled += 1; },
  });
  await assert.rejects(transport.read(body, () => { throw new Error("render failure"); }), /render failure/);
  assert.equal(cancelled, 1);
  assert.equal(body.locked, false);
});

test("SSE abort interrupts an idle read and never dispatches queued events", async () => {
  const controller = new AbortController();
  let cancelled = 0;
  const body = new ReadableStream({ cancel() { cancelled += 1; } });
  const reading = transport.read(body, () => assert.fail("aborted event"), { signal: controller.signal });
  controller.abort();
  await assert.rejects(reading, { name: "AbortError" });
  assert.equal(cancelled, 1);
  assert.equal(body.locked, false);
});

test("SSE checks a pre-aborted signal and absent response body", async () => {
  const controller = new AbortController();
  controller.abort();
  const body = bytesStream(encoder.encode(frame("done", {})));
  await assert.rejects(transport.read(body, () => assert.fail("aborted event"), { signal: controller.signal }), { name: "AbortError" });
  assert.equal(body.locked, false);
  await assert.rejects(transport.read(null, () => {}), /回答流/);
});


/* The whole page is loaded, not slices of it: the ES modules and app.js are turned into
   classic scripts (export/import lines dropped) and evaluated in the same context, so every
   top-level function is a global the tests can call, exactly as the old sections were —
   without depending on comment markers in the source. boot() is not run; the harness's
   observation stubs (addMsg / addStatus / cbAnnounce / …) are re-applied after the load. */
const MODULE_FILES = ["auth", "toast", "drafts", "uploads", "turn-stream", "deliverables", "session-watch", "session-nav"];
const classic = (src) => src.replace(/^export /mg, "").replace(/^import .*$/mg, "");
const moduleSources = MODULE_FILES.map((name) => classic(fs.readFileSync(path.join(__dirname, "..", "demo", "static", "modules", name + ".js"), "utf8")));
const appSource = classic(app).replace(/^boot\(\);\s*$/m, "/* boot() is not run in the harness */");
assert.ok(!/^boot\(\);/m.test(appSource), "boot() call removed for the harness");


function element(tag) {
  const classes = new Set();
  const el = {
    tagName: String(tag || "div").toUpperCase(), value: "", textContent: "", children: [], dataset: {}, style: {}, hidden: false,
    listeners: {}, selectors: {}, attributes: {}, isConnected: true, disabled: false, scrollTop: 0, scrollHeight: 0,
    classList: { add(...n) { n.forEach((c) => classes.add(c)); }, remove(...n) { n.forEach((c) => classes.delete(c)); },
      contains: (c) => classes.has(c), toggle(c, force) { const on = force === undefined ? !classes.has(c) : !!force; on ? classes.add(c) : classes.delete(c); return on; } },
    get className() { return [...classes].join(" "); }, set className(v) { classes.clear(); String(v || "").split(/\s+/).filter(Boolean).forEach((c) => classes.add(c)); },
    setAttribute(name, value) { this[name] = value; this.attributes[name] = String(value); },
    getAttribute(name) { return name in this.attributes ? this.attributes[name] : (this[name] === undefined ? null : String(this[name])); },
    hasAttribute(name) { return name in this.attributes; },
    removeAttribute(name) { delete this[name]; delete this.attributes[name]; },
    focus() { this.focused = true; }, blur() {}, click() { if (this.listeners.click) return this.listeners.click({ preventDefault() {} }); },
    setSelectionRange(start, end) { this.selectionStart = start; this.selectionEnd = end; },
    appendChild(child) { this.children.push(child); child.parentElement = this; return child; },
    append(...nodes) { nodes.forEach((n) => { if (n && typeof n === "object") this.appendChild(n); }); },
    prepend(...nodes) { nodes.forEach((n) => { if (n && typeof n === "object") { this.children.unshift(n); n.parentElement = this; } }); },
    insertBefore(child, ref) { const i = this.children.indexOf(ref); if (i < 0) this.children.push(child); else this.children.splice(i, 0, child); child.parentElement = this; return child; },
    removeChild(child) { this.children = this.children.filter((c) => c !== child); child.parentElement = null; return child; },
    remove() { if (this.parentElement) this.parentElement.removeChild(this); },
    replaceChildren(...children) { this.children.forEach(child => { child.parentElement = null; }); this.children = []; children.forEach(child => this.appendChild(child)); },
    get firstChild() { return this.children[0] || null; }, get lastChild() { return this.children[this.children.length - 1] || null; },
    get firstElementChild() { return this.children[0] || null; },
    querySelector(selector) { return this.selectors[selector] || (this.selectors[selector] = element()); },
    querySelectorAll() { return []; }, closest() { return null; }, contains(n) { return n === this || this.children.includes(n); },
    getBoundingClientRect() { return { top: 0, left: 0, width: 0, height: 0, bottom: 0, right: 0 }; },
    scrollIntoView() {}, dispatchEvent() { return true; },
    addEventListener(name, callback) { this.listeners[name] = callback; },
    removeEventListener(name) { delete this.listeners[name]; },
  };
  let html = "";
  Object.defineProperty(el, "innerHTML", { get() { return html; }, set(v) { html = String(v); if (html === "") { this.children.forEach((c) => { c.parentElement = null; }); this.children = []; } } });
  return el;
}

function ui(fetcher) {
  const elements = Object.fromEntries(["input", "form", "send", "stop", "confirmOk", "log", "btnNewThread",
    "keyBadge", "cbAvailability", "cbCadEntry", "btnAttach", "cbPackSample", "cbEmptyModel", "cbLlmOpen", "cbLlm",
    "cbLlmVendor", "cbLlmBase", "cbLlmModel", "cbLlmKey", "cbLlmSave", "cbLlmReset", "cbLlmModels", "cbLlmStatus",
    "cbBackupExport", "cbBackupImport", "cbBackupFile",
    "ctxMemory", "ctxMemoryStatus", "ctxRebuild", "ctxQuery", "ctxResults", "ctxSearchStatus", "ctxSearch", "ctxOpen", "ctxRefresh", "ctxBar", "ctxFill", "ctxText",
    "cbLlmContext", "cbLlmReserve", "cbLlmSemantic",
    "cbCapabilityPost", "cbCapabilityBody", "cbExploreCapability", "attaches",
  ].map((id) => [id, element()]));
  const messages = [];
  const errors = [];
  const announcements = [];
  const onboarding = [];
  const stored = new Map();
  const stubs = {
    cbCmd: {}, cbCmdUpdate() {},
    cbDockSet(open) { elements.ctxOpen.expanded = open; },
    escapeHtml: value => String(value || "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])),
    layerName: layer => layer,
    CB_APR_WAITING: new Set(),
    cbSyncSend: () => { elements.send.disabled = !elements.input.value.trim(); },
    cbRecentPush() {}, cbAutosize() {}, cbAtClose() {}, cbCmdClose() {}, cbHideWelcome() {},
    cbAttachRender() {}, renderSummon() {}, cbResetToEmpty() {}, cbProjRender() {}, async loadThreads() {},
    cbSlashExpandMessage: () => null, cbDirectMatch: () => null, namesOrPlain: () => "岗位",
    estimateLocalContext: () => ({}), paintContext() {}, cbObStep(step) { onboarding.push(step); }, cbAnnounce(text) { announcements.push(text); },
    renderCites() {}, appendDocCards() {}, refreshAuditSoon() {}, cbFixMount() {},
    apiError: async (res) => res.statusText,
    skillWho: (id) => id,
    cbTlCreate: () => ({ status() {}, finish() {}, error(message) { errors.push(message); } }),
    addStatus(message) { errors.push(message); },
    addMsg(role, who, text) {
      const parent = element();
      const body = element();
      body.textContent = text;
      parent.appendChild(body);
      messages.push({ role, body });
      return body;
    },
  };
  const context = vm.createContext({
    ...stubs,
    AbortController, TextDecoder, FormData, URL, setTimeout, clearTimeout, CB_CHAT_STREAM: transport, fetch: fetcher,
    window: {}, navigator: {}, location: { href: "http://localhost/", origin: "http://localhost", search: "", hash: "", pathname: "/" },
    history: { replaceState() {}, pushState() {} }, console,
    getComputedStyle: () => ({ getPropertyValue: () => "" }), requestAnimationFrame: (fn) => setTimeout(fn, 0), cancelAnimationFrame: (t) => clearTimeout(t),
    matchMedia: () => ({ matches: false, addEventListener() {}, removeEventListener() {}, addListener() {}, removeListener() {} }),
    alert() {}, confirm: () => true, prompt: () => null, queueMicrotask, structuredClone, atob, btoa, TextEncoder, Blob, File, Headers, Response, Request, ReadableStream,
    MutationObserver: class { observe() {} disconnect() {} }, ResizeObserver: class { observe() {} disconnect() {} }, IntersectionObserver: class { observe() {} disconnect() {} },
    Event: class { constructor(type) { this.type = type; } preventDefault() {} }, CustomEvent: class { constructor(type, init) { this.type = type; this.detail = init && init.detail; } },
    performance: { now: () => Date.now() },
    localStorage: { getItem: (key) => stored.get(key) || null, setItem: (key, value) => stored.set(key, value), removeItem: (key) => stored.delete(key) },
    document: { getElementById: (id) => elements[id] || (elements[id] = element()), createElement: element, addEventListener() {},
      querySelector: () => null, querySelectorAll: () => [], body: element(), documentElement: element(), visibilityState: "visible" },
  });
  for (const code of moduleSources) vm.runInContext(code, context);
  vm.runInContext(appSource, context);
  /* The page defined its own versions of the observation points; put the harness's back so the
     tests keep seeing messages, status lines and announcements the way they always did. */
  context.__stubs = stubs;
  vm.runInContext(`var __real = {}; for (const name of Object.keys(__stubs)) { try { __real[name] = eval(name); eval(name + " = __stubs[name]"); } catch (e) { /* const in the page: keep the page's */ } }`, context);
  return {
    elements, messages, errors, stored, announcements, onboarding,
    evaluate(code) { return vm.runInContext(code, context); },
    submit(text) {
      elements.input.value = text;
      return elements.form.listeners.submit({ preventDefault() {} });
    },
  };
}

test("chat rejects truncated responses, keeps partial output and unlocks composer", async () => {
  const h = ui(async () => ({ ok: true, body: bytesStream(encoder.encode(frame("token", { text: "已生成的一部分" }))) }));
  await h.submit("测试任务");
  const answer = h.messages.find((m) => m.role === "assistant").body;
  assert.equal(answer.textContent, "已生成的一部分");
  assert.match(answer.parentElement.children[1].textContent, /连接已中断/);
  assert.equal(h.evaluate("state.history.length"), 1);
  assert.equal(h.elements.form["aria-busy"], "false");
  assert.equal(h.elements.stop.hidden, true);
  assert.match(h.errors[0], /连接已中断/);
});

const idFrame = (id, name, data) => "id: " + id + "\n" + frame(name, data);

test("a dropped stream resumes from the last id via /events, skips repeats and completes", async () => {
  const calls = [];
  const h = ui(async (url) => {
    calls.push(String(url));
    if (String(url).startsWith("/api/chat")) {
      return { ok: true, body: bytesStream(encoder.encode(idFrame(1, "context", {}) + idFrame(2, "token", { text: "已生成" }) + idFrame(3, "token", { text: "的一部分" }))) };
    }
    if (String(url).startsWith("/api/sessions/") && String(url).includes("/events?after=3")) {
      // The server replays from after=3; a repeated frame 3 must be ignored, then the rest and done.
      return { ok: true, body: bytesStream(encoder.encode(idFrame(3, "token", { text: "的一部分" }) + idFrame(4, "token", { text: "，剩下的" }) + idFrame(5, "done", { text: "已生成的一部分，剩下的" })), 4) };
    }
    return { ok: false, status: 500, statusText: "unexpected " + url };
  });
  h.evaluate('cbApplyHealth({ has_key: true, capabilities: { chat: true, event_log: true } })');
  await h.submit("测试任务");
  const answer = h.messages.find((m) => m.role === "assistant").body;
  assert.equal(answer.textContent, "已生成的一部分，剩下的");
  assert.equal(h.evaluate("state.history.length"), 2);
  assert.equal(h.evaluate("state.history[1].content"), "已生成的一部分，剩下的");
  assert.ok(calls.some((u) => u.includes("/events?after=3")), calls.join("\n"));
  assert.equal(h.errors.length, 0, h.errors.join("\n"));
  assert.equal(h.elements.form["aria-busy"], "false");
});

test("without the event_log capability a dropped stream still falls back to polling recovery", async () => {
  const h = ui(async () => ({ ok: true, body: bytesStream(encoder.encode(idFrame(1, "token", { text: "已生成的一部分" }))) }));
  h.evaluate('cbApplyHealth({ has_key: true, capabilities: { chat: true, event_log: false } })');
  await h.submit("测试任务");
  const answer = h.messages.find((m) => m.role === "assistant").body;
  assert.equal(answer.textContent, "已生成的一部分");
  assert.match(answer.parentElement.children[1].textContent, /连接已中断/);
});

test("stop during a resume aborts it and keeps the partial answer", async () => {
  let resumeStarted;
  const started = new Promise((resolve) => { resumeStarted = resolve; });
  const h = ui(async (url, options) => {
    if (String(url).startsWith("/api/chat")) return { ok: true, body: bytesStream(encoder.encode(idFrame(1, "token", { text: "一半" }))) };
    if (String(url).includes("/events?after=1")) {
      resumeStarted();
      return new Promise((_, reject) => { options.signal.addEventListener("abort", () => { const e = new Error("aborted"); e.name = "AbortError"; reject(e); }); });
    }
    return { ok: false, status: 500, statusText: "unexpected " + url };
  });
  h.evaluate('cbApplyHealth({ has_key: true, capabilities: { chat: true, cancel: true, event_log: true } })');
  const running = h.submit("测试任务");
  await started;
  assert.equal(h.elements.form["aria-busy"], "true");
  h.evaluate("runState.active.controller.abort()");
  await running;
  const answer = h.messages.find((m) => m.role === "assistant").body;
  assert.equal(answer.textContent, "一半");
  assert.match(answer.parentElement.children[1].textContent, /已停止/);
  assert.equal(h.elements.form["aria-busy"], "false");
});

test("chat records each expert answer once and identifies an unfinished second expert", async () => {
  const response = frame("done", { text: "第一岗完成" }) +
    frame("status", { phase: "summon", expert: "quality" }) + frame("token", { text: "第二岗未完成" });
  const h = ui(async () => ({ ok: true, body: bytesStream(encoder.encode(response), 8) }));
  await h.submit("多岗任务");
  assert.equal(h.evaluate('state.history.filter((m) => m.content === "第一岗完成").length'), 1);
  assert.equal(h.evaluate("state.history.length"), 2);
  assert.match(h.messages[2].body.parentElement.children[1].textContent, /连接已中断/);
});

test("chat ignores unknown events and reports malformed known events", async () => {
  const good = ui(async () => ({ ok: true, body: bytesStream(encoder.encode("event: future\ndata: not-json\n\n" + frame("done", { text: "完成" }))) }));
  await good.submit("测试任务");
  assert.equal(good.errors.length, 0);
  assert.equal(good.evaluate("state.history.length"), 2);
  for (const data of ["not-json", "null", "[]"]) {
    const bad = ui(async () => ({ ok: true, body: bytesStream(encoder.encode("event: done\ndata: " + data + "\n\n")) }));
    await bad.submit("测试任务");
    assert.match(bad.errors[0], /回答事件/);
  }
});

test("duplicate submits preserve draft; stopping leaves history without a fabricated answer", async () => {
  let calls = 0;
  const h = ui(async () => { calls += 1; return { ok: true, body: new ReadableStream({}) }; });
  const first = h.submit("第一次任务");
  await h.submit("发送中写的新草稿");
  assert.equal(calls, 1);
  assert.equal(h.elements.input.value, "发送中写的新草稿");
  h.elements.stop.listeners.click();
  await first;
  assert.equal(h.evaluate("state.history.length"), 1);
  assert.equal(h.elements.send.disabled, false);
  assert.match(h.messages[1].body.parentElement.children[1].textContent, /已停止接收/);
});

test("stop requests backend cancellation and keeps the stream open for actual saved results", async () => {
  let stream, requested, signal;
  const h = ui(async (url, options) => {
    if (url.endsWith("/cancel")) {
      requested = url;
      return { ok: true, json: async () => ({ cancel_requested: true, cancelled: false, state: "cancelling" }) };
    }
    signal = options.signal;
    return { ok: true, body: new ReadableStream({ start(controller) { stream = controller; } }) };
  });
  h.evaluate('cbApplyHealth({capabilities:{chat:true,cancel:true}})');
  const session = h.evaluate("state.session");
  const pending = h.submit("多岗起草");
  await h.elements.stop.listeners.click();
  assert.equal(requested, `/api/sessions/${session}/cancel`);
  assert.equal(signal.aborted, false);
  assert.equal(h.elements.stop.disabled, true);
  stream.enqueue(encoder.encode(frame("status", { phase: "cancelling", text: "等待已开始的文件操作结束" })));
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(h.elements.form["aria-busy"], "true");
  assert.match(h.announcements.at(-1), /已请求停止/);
  assert.equal(h.errors.length, 0);
  stream.enqueue(encoder.encode(frame("done", { text: "已停止，已有文书保留", cancelled: true, ok: false,
    deliverables: [{ name: "first.docx", path: "saved.docx" }] })));
  stream.close();
  await pending;
  assert.equal(h.evaluate("cbLastDeliverables[0].name"), "first.docx");
  assert.match(h.announcements.at(-1), /任务已停止/);
  assert.equal(h.elements.stop.hidden, true);
});

test("a rejected cancel can be retried without discarding the active response", async () => {
  let stream, attempts = 0;
  const h = ui(async (url) => {
    if (url.endsWith("/cancel")) {
      attempts++;
      return attempts === 1 ? { ok: false, statusText: "temporary failure" } : { ok: true, json: async () => ({cancel_requested:true}) };
    }
    return { ok: true, body: new ReadableStream({ start(controller) { stream = controller; } }) };
  });
  h.evaluate('cbApplyHealth({capabilities:{chat:true,cancel:true}})');
  const pending = h.submit("写一份草稿");
  await h.elements.stop.listeners.click();
  assert.equal(h.elements.stop.disabled, false);
  assert.equal(h.evaluate("runState.active.cancelRequested"), false);
  await h.elements.stop.listeners.click();
  assert.equal(attempts, 2);
  stream.enqueue(encoder.encode(frame("done", {text:"已停止",cancelled:true,ok:false})));
  stream.close();
  await pending;
});

test("leaving an active task detaches it: nothing is cancelled and the task is remembered as running", async () => {
  const requested = [];
  const h = ui(async (url) => {
    if (url.endsWith("/cancel")) {
      requested.push(url);
      return { ok:true, json:async () => ({cancel_requested:true}) };
    }
    return { ok:true, body:new ReadableStream({}) };
  });
  h.evaluate('cbApplyHealth({capabilities:{chat:true,cancel:true}})');
  const old = h.evaluate("state.session");
  const pending = h.submit("旧任务");
  h.evaluate("cbNewLocalSession()");
  await pending;
  assert.notEqual(h.evaluate("state.session"), old);
  assert.deepEqual(requested, [], "switching tasks is not the stop button");
  assert.equal(h.evaluate(`runState.background.has(${JSON.stringify(old)})`), true);
  assert.equal(h.evaluate(`runState.background.has(state.session)`), false);
  assert.equal(h.evaluate("runState.active"), null);
  assert.match(h.announcements.at(-1), /后台/);
});

test("the stop button still cancels the session it was pressed in, and only that one", async () => {
  const requested = [];
  let stream;
  const h = ui(async (url) => {
    if (url.endsWith("/cancel")) {
      requested.push(url);
      return { ok:true, json:async () => ({cancel_requested:true}) };
    }
    return { ok:true, body:new ReadableStream({ start(controller) { stream = controller; } }) };
  });
  h.evaluate('cbApplyHealth({capabilities:{chat:true,cancel:true}})');
  const session = h.evaluate("state.session");
  const pending = h.submit("要停的任务");
  await h.elements.stop.listeners.click();
  assert.deepEqual(requested, [`/api/sessions/${session}/cancel`]);
  assert.equal(h.evaluate(`runState.background.has(${JSON.stringify(session)})`), false);
  stream.enqueue(encoder.encode(frame("done", {text:"已停止",cancelled:true,ok:false})));
  stream.close();
  await pending;
});

// A turn the page is only watching: no stream here, but the server is still running it.
function watchedUi(capabilities) {
  const server = { active: true, state: "running", cancels: [], chats: 0 };
  const h = ui(async (url, init) => {
    if (url.endsWith("/cancel")) {
      server.cancels.push(url);
      return { ok: true, json: async () => ({ cancel_requested: true }) };
    }
    if (init && init.method === "POST") { server.chats++; return { ok: false, statusText: "409" }; }
    return { ok: true, json: async () => ({ session_id: h.evaluate("state.session"), transcript: [],
      turn_state: { active: server.active, state: server.state } }) };
  });
  h.evaluate(`cbApplyHealth(${JSON.stringify({ capabilities })})`);
  // Polls are scheduled 1.5 s apart; keep them in hand instead of waiting for them.
  h.evaluate("var cbTestPolls = []; setTimeout = (fn, ms) => { if (ms === 1500) cbTestPolls.push(fn); return 0; }");
  const settle = () => new Promise((resolve) => setImmediate(resolve));
  return { h, server, settle,
    async watch(options) { h.evaluate(`cbWatchSession(state.session, ${JSON.stringify(options || {})})`); await settle(); },
    async nextPoll() { await h.evaluate("cbTestPolls.shift()()"); await settle(); } };
}

test("a task found running in the background is shown as running, refuses a new message, and can be stopped", async () => {
  const { h, server, watch, nextPoll } = watchedUi({ chat: true, cancel: true });
  const session = h.evaluate("state.session");
  await watch({ reason: "回到前台；" });
  assert.equal(h.elements.stop.hidden, false);
  assert.equal(h.elements.form["aria-busy"], "true");
  assert.equal(h.elements.send.disabled, true);

  await h.submit("新消息");
  assert.equal(server.chats, 0, "the session is busy on the server: sending would only earn a 409");
  assert.match(h.errors.at(-1), /还在后台运行/);
  assert.equal(h.elements.input.value, "新消息");

  await h.elements.stop.listeners.click();
  assert.deepEqual(server.cancels, [`/api/sessions/${session}/cancel`]);
  assert.match(h.announcements.at(-1), /已请求停止/);

  server.active = false;
  server.state = "cancelled";
  await nextPoll();
  assert.equal(h.elements.stop.hidden, true);
  assert.equal(h.elements.form["aria-busy"], "false");
  assert.equal(h.evaluate("runState.watched"), null);
  assert.match(h.errors.at(-1), /回到前台；该任务已被停止/);
});

test("a watched task that finishes is reported as finished, one that failed is not", async () => {
  for (const [state, expected] of [["done", /已在后台完成/], ["failed", /没有跑完/]]) {
    const { h, server, watch, nextPoll } = watchedUi({ chat: true, cancel: true });
    await watch();
    server.active = false;
    server.state = state;
    await nextPoll();
    assert.match(h.errors.at(-1), expected);
    assert.equal(h.elements.stop.hidden, true);
  }
});

test("leaving a watched task takes the stop button away without cancelling anything", async () => {
  const { h, server, watch } = watchedUi({ chat: true, cancel: true });
  await watch();
  assert.equal(h.elements.stop.hidden, false);
  h.evaluate("cbNewLocalSession()");
  assert.equal(h.elements.stop.hidden, true);
  assert.equal(h.elements.form["aria-busy"], "false");
  assert.equal(h.evaluate("runState.watched"), null);
  assert.deepEqual(server.cancels, []);
  assert.equal(h.evaluate("cbTestPolls.length"), 1, "the poll that was pending");
  await h.evaluate("cbTestPolls.shift()()");
  assert.equal(h.evaluate("cbTestPolls.length"), 0, "and it does not reschedule itself for a task we left");
});

test("a backend that cannot cancel gets no dead stop button on a watched task", async () => {
  const { h, watch } = watchedUi({ chat: true, cancel: false });
  await watch();
  assert.equal(h.elements.form["aria-busy"], "true");
  assert.equal(h.elements.stop.hidden, true);
});

test("a task still running when the page stops waiting is not called finished", async () => {
  const { h, watch, nextPoll } = watchedUi({ chat: true, cancel: true });
  await watch();
  assert.equal(h.elements.stop.hidden, false);
  h.evaluate("{ const real = Date.now; Date.now = () => real() + 11 * 60 * 1000; }");
  await nextPoll();
  assert.match(h.errors.at(-1), /仍在后台运行/);
  assert.doesNotMatch(h.errors.join("\n"), /已在后台完成|已被停止/);
  assert.equal(h.elements.stop.hidden, true);
  assert.equal(h.evaluate("cbTestPolls.length"), 0);
});

test("oversize backup import and busy-task export do not send requests", async () => {
  let requests = 0;
  const h = ui(async () => { requests++; return {ok:true,body:new ReadableStream({})}; });
  h.elements.cbBackupFile.files = [{size:129 * 1024 * 1024}];
  await h.elements.cbBackupFile.listeners.change();
  assert.equal(requests, 0);
  assert.match(h.errors.at(-1), /128 MB/);
  const pending = h.submit("执行中");
  await h.elements.cbBackupExport.listeners.click();
  assert.equal(requests, 1);
  assert.match(h.errors.at(-1), /完成后备份/);
  h.elements.stop.listeners.click();
  await pending;
});

test("new local session invalidates old responses without resetting a newer running task", async () => {
  const pending = [];
  const h = ui(() => new Promise((resolve) => pending.push(resolve)));
  const old = h.submit("旧任务");
  h.evaluate("cbNewLocalSession()");
  const fresh = h.submit("新任务");
  pending[0]({ ok: true, body: bytesStream(encoder.encode(frame("done", { text: "旧回答" }))) });
  await old;
  assert.equal(h.elements.form["aria-busy"], "true");
  assert.equal(h.evaluate("state.history.length"), 1);
  pending[1]({ ok: true, body: bytesStream(encoder.encode(frame("done", { text: "新回答" }))) });
  await fresh;
  assert.equal(h.evaluate('state.history[1].content'), "新回答");
});

test("session loads and remote thread registration cannot overwrite later navigation or messages", async () => {
  const pending = [];
  const h = ui(() => new Promise((resolve) => pending.push(resolve)));
  const first = h.evaluate('cbProjOpenSession({ session_id: "first" })');
  const second = h.evaluate('cbProjOpenSession({ session_id: "second" })');
  pending[1]({ ok: true, json: async () => ({ session_id: "second", transcript: [] }) });
  await second;
  pending[0]({ ok: true, json: async () => ({ session_id: "first", transcript: [] }) });
  await first;
  assert.equal(h.evaluate("state.session"), "second");
  h.elements.btnNewThread.listeners.click();
  const local = h.evaluate("state.session");
  h.evaluate('state.history.push({ role: "user", content: "already sent" })');
  assert.equal(h.evaluate("state.session"), local);
  assert.equal(pending.length, 2, "新建任务只在本地清屏，不再向服务端登记线程");
});

test("typed confirmation requires the exact phrase and is cleared on new sessions", () => {
  const h = ui(() => assert.fail("must not request network"));
  for (const value of ["", "true", "我明白", "我明白，将由持证人员签认 "]) {
    h.elements.confirmOk.value = value;
    assert.equal(h.evaluate("cbConfirmed()"), false);
  }
  h.elements.confirmOk.value = "我明白，将由持证人员签认";
  assert.equal(h.evaluate("cbConfirmed()"), true);
  h.evaluate("cbNewLocalSession()");
  assert.equal(h.evaluate("cbConfirmed()"), false);
});

test("a message sent while session loading keeps its original session and answer", async () => {
  const pending = [];
  const h = ui(() => new Promise((resolve) => pending.push(resolve)));
  const local = h.evaluate("state.session");
  const navigation = h.evaluate('cbProjOpenSession({ session_id: "other" })');
  const running = h.submit("刚发送的任务");
  pending[0]({ ok: true, json: async () => ({ session_id: "other", transcript: [] }) });
  await navigation;
  assert.equal(h.evaluate("state.session"), local);
  pending[1]({ ok: true, body: bytesStream(encoder.encode(frame("done", { text: "本轮回答" }))) });
  await running;
  assert.equal(h.evaluate("state.history[1].content"), "本轮回答");
});

test("an upload finishing after navigation cannot attach files to the new session", async () => {
  let finish;
  const h = ui(() => new Promise((resolve) => { finish = resolve; }));
  const uploading = h.evaluate('cbAttachUpload([{ name: "plan.csv" }])');
  h.evaluate("cbNewLocalSession()");
  finish({ ok: true, json: async () => ({ files: [{ id: "old-file", name: "plan.csv" }] }) });
  await uploading;
  assert.equal(h.evaluate("state.attachments.length"), 0);
  assert.equal(h.errors.length, 0);
});

test("chat keeps the backend confirmation protocol and does not accept a checked property", async () => {
  for (const confirmed of [false, true]) {
    let payload;
    const h = ui(async (_url, request) => {
      payload = JSON.parse(request.body);
      return { ok: true, body: bytesStream(encoder.encode(frame("done", { text: "回答" }))) };
    });
    h.elements.confirmOk.checked = true;
    h.elements.confirmOk.value = confirmed ? "我明白，将由持证人员签认" : "";
    await h.submit("测试任务");
    assert.equal(payload.confirm_ok, confirmed);
    assert.equal(payload.confirm_text, confirmed ? "我明白，将由持证人员签认" : "");
  }
});

test("offline capability status enables usable tools without claiming a model is configured", () => {
  const h = ui(() => assert.fail("status paint must not request network"));
  h.evaluate('cbApplyHealth({ has_key: false, mode: "offline", capabilities: { chat: true, drafts: true, model_settings: true, attachments: false, packing: false } })');
  assert.equal(h.elements.keyBadge.textContent, "离线工作台可用");
  assert.match(h.elements.cbAvailability.textContent, /无需 API Key/);
  assert.equal(h.elements.cbLlmOpen.disabled, false);
  assert.equal(h.elements.btnAttach.disabled, true);
  assert.equal(h.elements.cbPackSample.disabled, true);
  h.evaluate('cbApplyHealth({ has_key: true, mode: "configured", capabilities: { attachments: true } })');
  assert.equal(h.elements.keyBadge.textContent, "模型已配置");
  assert.equal(h.elements.btnAttach.disabled, false);
});

test("explicit CAD project URL binds chat without restoring an unrelated remembered task", async () => {
  let payload;
  const h = ui(async (_url, options) => {
    payload = JSON.parse(options.body);
    return { ok: true, body: bytesStream(encoder.encode(frame("done", { text: "已检查" }))) };
  });
  const id = "a".repeat(32);
  h.evaluate(`globalThis.location = { href: "http://localhost/?cad_project_id=${id}" }; state.cadProjectId = cbCadProjectFromUrl(); cbRememberSession("old-task");`);
  assert.equal(h.evaluate("cbRememberedSession()"), "");
  await h.submit("检查图纸");
  assert.equal(payload.cad_project_id, id);
  assert.equal(payload.confirm_ok, false);
  h.evaluate('globalThis.location.href = "http://localhost/?cad_project_id=../../other"');
  assert.equal(h.evaluate("cbCadProjectFromUrl()"), "");
  h.evaluate("cbNewLocalSession()");
  assert.equal(h.evaluate("state.cadProjectId"), "");
});

test("saved task restores only its server-bound CAD selection and clears it for ordinary tasks", async () => {
  const id = "b".repeat(32);
  const h = ui(async (url) => response({ session_id: String(url).includes("cad-task") ? "cad-task" : "ordinary-task",
    transcript: [], cad_project_id: String(url).includes("cad-task") ? id : "" }));
  await h.evaluate('cbProjOpenSession({ session_id: "cad-task" })');
  assert.equal(h.evaluate("state.cadProjectId"), id);
  await h.evaluate('cbProjOpenSession({ session_id: "ordinary-task" })');
  assert.equal(h.evaluate("state.cadProjectId"), "");
});

test("shared home exposes CAD only when the host explicitly advertises its routes", () => {
  const h = ui(() => assert.fail("painting capabilities must not request network"));
  h.evaluate('cbApplyHealth({ capabilities: { chat: true } })');
  assert.equal(h.elements.cbCadEntry.hidden, true);
  h.evaluate('cbApplyHealth({ capabilities: { cad: true } })');
  assert.equal(h.elements.cbCadEntry.hidden, false);
  h.evaluate('cbApplyHealth({ capabilities: { cad: false } })');
  assert.equal(h.elements.cbCadEntry.hidden, true);
  const home = fs.readFileSync(path.join(__dirname, "../demo/static/index.html"), "utf8");
  assert.match(home, /<a\b[^>]*id="cbCadEntry"[^>]*\bhidden\b[^>]*>/);
  const styles = fs.readFileSync(path.join(__dirname, "../demo/static/styles.css"), "utf8");
  assert.match(styles, /\.cb-empty-card\[hidden\]\s*\{\s*display:\s*none;/);
});

test("explicitly unsupported task and upload capabilities do not send network requests", async () => {
  const h = ui(() => assert.fail("unsupported action must not request network"));
  h.evaluate('cbApplyHealth({ capabilities: { chat: false, attachments: false } })');
  await h.submit("测试任务");
  await h.evaluate('cbAttachUpload([{ name: "plan.csv" }])');
  assert.equal(h.errors.length, 2);
});

test("offline samples prefill real posts without submitting or granting confirmation", () => {
  const h = ui(() => assert.fail("prefill must not submit"));
  for (const [id, prefix] of [["skills", "@施工方案"], ["daily", "@项目日报"], ["bid-guide", "@招标解析"]]) {
    h.evaluate('cbEmptyPrefill("' + id + '")');
    assert.ok(h.elements.input.value.startsWith(prefix));
    assert.equal(h.evaluate("cbConfirmed()"), false);
  }
  h.elements.input.value = "已有的任务说明";
  h.elements.input.selectionStart = h.elements.input.value.length;
  h.evaluate("cbBrowsePosts()");
  assert.equal(h.elements.input.value, "已有的任务说明 /");
  assert.equal(h.evaluate("cbCmd.mode"), "cats");
});

const config = (configured = true) => ({ configured, model: "demo-model", base_url: "http://localhost:9999/v1", key_masked: "test****demo", source: "runtime" });
const response = (value) => ({ ok: true, status: 200, json: async () => value });

test("model settings load only masked configuration and reject HTTP or malformed responses", async () => {
  const h = ui(async () => response({ ...config(), api_key: "plaintext-never-display", key_masked: "plaintext-never-display",
    base_url: "http://username:password@localhost:9999/v1?api_key=hidden#fragment" }));
  await h.evaluate("cbLlmLoad()");
  assert.equal(h.elements.cbLlmKey.value, "");
  assert.equal(h.elements.cbLlmBase.value, "http://localhost:9999/v1");
  assert.doesNotMatch(h.elements.cbLlmStatus.textContent, /plaintext|password|api_key=/);
  for (const fetcher of [async () => ({ ok: false, status: 404 }), async () => response({ detail: "wrong format" })]) {
    const broken = ui(fetcher);
    assert.equal(await broken.evaluate("cbLlmLoad()"), null);
    assert.match(broken.elements.cbLlmStatus.textContent, /读取失败/);
    assert.equal(broken.elements.cbLlmSave.disabled, false);
  }
});

test("saving model settings prevents duplicate writes and refreshes configuration once", async () => {
  const calls = [];
  let complete;
  const h = ui((_url, options) => {
    calls.push(options);
    if (options.method === "POST") return new Promise((resolve) => { complete = resolve; });
    return Promise.resolve(response(config()));
  });
  h.elements.cbLlmKey.value = "local-test-secret";
  const saving = h.evaluate('cbLlmSubmit({ api_key: "local-test-secret", model: "demo-model" }, "模型已保存")');
  await h.evaluate('cbLlmSubmit({ clear: true }, "恢复启动配置")');
  assert.equal(calls.length, 1);
  assert.equal(h.elements.cbLlmSave.disabled, true);
  assert.equal(h.elements.cbLlmKey.value, "");
  complete(response(config()));
  await saving;
  assert.equal(calls.length, 2);
  assert.equal(calls[1].method, undefined);
  assert.equal(h.elements.cbLlmSave.disabled, false);
  assert.match(h.elements.cbLlmStatus.textContent, /模型已保存/);
  assert.equal(h.elements.keyBadge.textContent, "模型已配置");
});

test("model errors redact submitted keys and distinguish refresh failures from rejected writes", async () => {
  const failed = ui(async () => ({ ok: false, status: 400, statusText: "invalid local-test-secret" }));
  await failed.evaluate('cbLlmSubmit({ api_key: "local-test-secret" }, "保存")');
  assert.match(failed.elements.cbLlmStatus.textContent, /保存失败/);
  assert.doesNotMatch(failed.elements.cbLlmStatus.textContent, /local-test-secret/);
  let count = 0;
  const unconfirmed = ui(async () => ++count === 1 ? response(config()) : { ok: false, status: 503, statusText: "unavailable" });
  await unconfirmed.evaluate('cbLlmSubmit({ clear: true }, "已恢复启动配置")');
  assert.equal(count, 2);
  assert.match(unconfirmed.elements.cbLlmStatus.textContent, /设置已提交.*未能核对/);
  assert.equal(unconfirmed.elements.cbLlmReset.disabled, false);
});

test("restoring startup configuration returns to offline availability", async () => {
  const calls = [];
  const h = ui(async (_url, options) => { calls.push(options); return response(config(false)); });
  h.evaluate('cbApplyHealth({ has_key: true, capabilities: { chat: true, drafts: true } })');
  await h.evaluate('cbLlmSubmit({ clear: true }, "已恢复启动配置")');
  assert.deepEqual(JSON.parse(calls[0].body), { clear: true });
  assert.equal(h.elements.keyBadge.textContent, "离线工作台可用");
  assert.match(h.elements.cbLlmStatus.textContent, /已恢复启动配置/);
});

test("restored sessions recover their selected attachments, project and deliverables", async () => {
  const detail = { session_id: "saved", project_id: "project-one", transcript: [{ role: "assistant", text: "已完成" }],
    attachments: [{ id: "selected", name: "材料.md" }, { id: "job:generated" }, null],
    deliverables: [{ path: "/draft.md", name: "日报草稿.md" }] };
  const payloads = [];
  const h = ui(async (_url, options) => {
    if (!options) return response(detail);
    payloads.push(JSON.parse(options.body));
    return { ok: true, body: bytesStream(encoder.encode(frame("done", { text: "续答" }))) };
  });
  await h.evaluate('cbProjOpenSession({ session_id: "saved" })');
  assert.equal(h.evaluate("cbLastDeliverables.length"), 1);
  assert.equal(h.evaluate("state.attachments.length"), 1);
  await h.submit("继续完善");
  assert.equal(payloads[0].project_id, "project-one");
  assert.deepEqual(payloads[0].attachments, ["selected"]);
});

test("both Python and Rust pending responses mount the typed confirmation card", () => {
  for (const data of [{ hitl_pending: true, skill: "construction", run_ids: ["run-one"] },
    { hitl: { pending: true, gate: "exclusive_write" }, expert: "construction", run_id: "run-one" }]) {
    const h = ui(() => assert.fail("must not request network"));
    h.evaluate("cbTlCreate = __real.cbTlCreate");
    const pending = h.evaluate(`cbHitlPending(${JSON.stringify(data)})`);
    assert.equal(pending, true, "both response shapes count as a pending gate");
    h.evaluate(`var __body = addMsg("assistant", "岗位", ""); var __tl = cbTlCreate(__body, "写一份方案"); __tl.finish(${JSON.stringify(data)});`);
    const root = h.evaluate("__body.parentElement.children.find((c) => c.className === 'cb-tl')");
    assert.ok(root, "the timeline is mounted next to the bubble");
    assert.equal(root.selectors[".tl-hitl"].hidden, false, "the HITL slot is shown while the gate waits");
  }
});

test("current low-risk server HITL enables confirmation and shows the approval path even when ok is false", async () => {
  for (const pending of [{ ok: false, hitl_pending: true, skill: "bid-parse,bid-tech,bid-compliance" },
    { ok: false, hitl: { pending: true, gate: "untrusted_write" }, expert: "pm-daily" }]) {
    const payload = frame("status", { phase: "hitl_gate", confirmed: false }) + frame("done", { text: "等待本轮确认", ...pending });
    const h = ui(async () => ({ ok: true, body: bytesStream(encoder.encode(payload)) }));
    h.elements.confirmOk.disabled = true;
    h.elements.confirmOk.placeholder = "普通岗位无需填写";
    h.evaluate('state.experts = [{id:"pm-daily",risk:"low"}]; state.summoned.add("pm-daily");' +
      'var timelineFinished = null; cbTlCreate = () => ({status(){},finish(data){timelineFinished=data;},error(){throw Error("HITL is not a tool failure");}});');
    await h.submit("整理日报");
    assert.equal(h.elements.confirmOk.disabled, false);
    assert.match(h.elements.confirmOk.placeholder, /本轮确认/);
    assert.equal(h.evaluate("timelineFinished.ok"), false);
    assert.match(h.announcements.at(-1), /等待签认/);
    assert.equal(h.onboarding.includes(2), false);
    assert.deepEqual(h.errors, []);
  }
});

test("server gate confirmation is sent once and ordinary responses do not enable a disabled input", async () => {
  const payloads = [];
  const h = ui(async (_url, options) => {
    payloads.push(JSON.parse(options.body));
    const done = payloads.length === 1 ? { text: "等待确认", ok: false, hitl_pending: true } : { text: "完成", ok: true };
    return { ok: true, body: bytesStream(encoder.encode(frame("done", done))) };
  });
  h.elements.confirmOk.disabled = true;
  h.elements.confirmOk.placeholder = "普通岗位无需填写";
  await h.submit("整理日报");
  h.elements.confirmOk.value = "我明白，将由持证人员签认";
  await h.submit("整理日报");
  assert.equal(payloads[1].confirm_ok, true);
  assert.equal(payloads[1].confirm_text, "我明白，将由持证人员签认");
  assert.equal(h.elements.confirmOk.disabled, true);
  assert.equal(h.elements.confirmOk.placeholder, "普通岗位无需填写");
  assert.equal(h.elements.confirmOk.value, "");
  await h.submit("整理另一天日报");
  assert.equal(payloads[2].confirm_ok, false);
  assert.equal(payloads[2].confirm_text, "");
  assert.equal(h.elements.confirmOk.disabled, true);
  h.evaluate('cbEnableServerHitl({hitl_pending:"true"}); cbEnableServerHitl({phase:"routing"});');
  assert.equal(h.elements.confirmOk.disabled, true);
});

test("collaboration waiting for an untrusted write uses a readable confirmation state", () => {
  const h = ui(() => assert.fail("must not request network"));
  h.evaluate('var gateBody = addMsg("assistant", "协作", ""); cbCollaborationPaint({state:"waiting_hitl",children:[]}, gateBody);');
  const card = h.messages[0].body.parentElement.cbCollaborationCard;
  assert.match(card.children[0].textContent, /等待本轮确认/);
});

test("new and restored tasks discard server gate UI and cannot inherit confirmation from history", async () => {
  const h = ui(async () => ({ ok: true, json: async () => ({ session_id: "restored", transcript: [],
    history: [], expert_ids: ["pm-daily"], hitl_pending: true, turn_state: { state: "waiting_hitl" } }) }));
  h.elements.confirmOk.disabled = true;
  h.evaluate('cbEnableServerHitl({hitl_pending:true});');
  h.elements.confirmOk.value = "我明白，将由持证人员签认";
  h.evaluate('cbNewLocalSession()');
  assert.equal(h.elements.confirmOk.disabled, true);
  assert.equal(h.elements.confirmOk.value, "");
  h.evaluate('cbEnableServerHitl({hitl_pending:true});');
  h.elements.confirmOk.value = "我明白，将由持证人员签认";
  await h.evaluate('cbProjOpenSession({session_id:"restored"})');
  assert.equal(h.elements.confirmOk.disabled, true);
  assert.equal(h.elements.confirmOk.value, "");
  assert.equal(h.evaluate("cbServerHitlInput"), null);
});

test("upload errors show actionable API details and do not select a failed file", async () => {
  const h = ui(async () => ({ ok: false, status: 400, text: async () => JSON.stringify({ detail: "扫描件 PDF 需要先 OCR" }) }));
  h.evaluate("apiError = __real.apiError");
  await h.evaluate('cbAttachUpload([{ name: "scan.pdf" }])');
  assert.match(h.errors[0], /scan\.pdf.*需要先 OCR/);
  assert.equal(h.evaluate("state.attachments.length"), 0);
});

test("generic and packing timelines describe the selected workflow", () => {
  const h = ui(() => assert.fail("must not request network"));
  assert.equal(h.evaluate('cbTlStageDefinitions(false).map((item) => item[1]).join("/")'), "理解任务/召唤岗位/读取资料/起草/人工确认/检查/保存/完成");
  assert.match(h.evaluate('cbTlStageDefinitions(true).map((item) => item[1]).join("/")'), /成箱.*拼柜/);
  assert.equal(h.evaluate('cbTlStageDefinitions(false).find((item) => item[0] === cbTlPhase("deliver", false))[1]'), "起草");
  assert.equal(h.evaluate('cbTlStageDefinitions(true).find((item) => item[0] === cbTlPhase("deliver", true))[1]'), "落盘");
});

test("summon events show the named expert before any tokens arrive", async () => {
  const h = ui(async () => ({ ok: true, body: bytesStream(encoder.encode(frame("status", { phase: "summon", expert: "pm-daily" }))) }));
  h.evaluate("skillWho = __real.skillWho; namesOrPlain = __real.namesOrPlain");
  h.evaluate('state.experts = [{ id: "pm-daily", name: "项目日报" }]');
  await h.submit("@项目日报 写一份日报");
  const who = h.messages[1].body.parentElement.querySelector(".who").textContent;
  assert.match(who, /项目日报/);
  assert.doesNotMatch(who, /未点名/);
});

test("recent-session storage contains only an id and is cleared for a new empty task", async () => {
  const h = ui(async () => ({ ok: true, body: bytesStream(encoder.encode(frame("done", { text: "私密回答正文" }))) }));
  assert.equal(h.evaluate("cbRememberedSession()"), "");
  await h.submit("私密任务正文");
  assert.deepEqual([...h.stored.values()], [h.evaluate("state.session")]);
  h.evaluate("cbNewLocalSession()");
  assert.equal(h.stored.size, 0);
  h.stored.set("cb_active_session_v1", "../invalid");
  assert.equal(h.evaluate("cbRememberedSession()"), "");
});

test("startup restores a listed session but never overrides a later user operation", async () => {
  let requests = 0;
  const h = ui(async () => {
    requests += 1;
    return response({ session_id: "remembered", transcript: [{ role: "assistant", text: "已恢复" }] });
  });
  h.evaluate('cbProj.sessions = [{ session_id: "remembered" }]');
  assert.equal(await h.evaluate('cbResumeSession("remembered", cbSessionRequest)'), true);
  assert.equal(requests, 1);
  assert.equal(h.evaluate("cbRememberedSession()"), "remembered");
  const stale = h.evaluate("cbSessionRequest");
  h.evaluate("cbNewLocalSession()");
  assert.equal(await h.evaluate('cbResumeSession("remembered", ' + stale + ')'), false);
  assert.equal(await h.evaluate('cbResumeSession("missing", cbSessionRequest)'), false);
  assert.equal(requests, 1);
});

test("browsing categories and posts does not silently truncate the catalog to nine entries", () => {
  const h = ui(() => assert.fail("must not request network"));
  h.evaluate("cbCmdUpdate = __real.cbCmdUpdate");
  h.evaluate('function cbCmdSubItems() { return Array.from({ length: 16 }, (_, i) => ({ id: "item-" + i, name: "岗位" + i })); }');
  assert.equal(h.evaluate('cbCmdFiltered("cats", "").length'), 16);
  assert.equal(h.evaluate('cbCmdFiltered("posts", "").length'), 16);
  assert.equal(h.evaluate('cbCmdFiltered("cmds", "").length'), 9);
});

test("a failed done result retains output and deliverables without declaring success", async () => {
  const data = { ok: false, text: "工具未完成本轮任务", deliverables: [{ path: "/partial.md", name: "已生成草稿.md" }] };
  const h = ui(async () => ({ ok: true, body: bytesStream(encoder.encode(frame("done", data))) }));
  await h.submit("生成任务");
  assert.equal(h.messages[1].body.textContent, data.text);
  assert.equal(h.evaluate("cbLastDeliverables.length"), 1);
  assert.equal(h.errors[0], data.text);
  assert.match(h.announcements.at(-1), /本轮未完成/);
  assert.equal(h.onboarding.includes(2), false);
});

test("restored expert selections filter the live catalog and preserve follow-up routing without confirmation", async () => {
  let payload;
  const h = ui(async (_url, options) => {
    if (!options) return response({ session_id: "saved-experts", transcript: [],
      expert_ids: ["pm-daily", "construction", "unknown", null, "pm-daily", "quality"] });
    payload = JSON.parse(options.body);
    return { ok: true, body: bytesStream(encoder.encode(frame("done", { text: "继续完成" }))) };
  });
  h.evaluate('state.experts = [{ id: "pm-daily", enabled: true }, { id: "construction", enabled: false }, { id: "quality" }];' +
    'var summonRenders = 0; function renderSummon() { summonRenders += 1; }');
  h.elements.confirmOk.value = "我明白，将由持证人员签认";
  await h.evaluate('cbProjOpenSession({ session_id: "saved-experts" })');
  assert.equal(h.evaluate("summonRenders"), 1);
  assert.equal(h.evaluate("cbConfirmed()"), false);
  await h.submit("继续完善");
  assert.deepEqual(payload.expert_ids, ["pm-daily", "quality"]);
  assert.equal(payload.confirm_ok, false);
});

function contextUi(fetcher) {
  const h = ui(fetcher);
  /* the context panel tests want the page's own painters, not the harness stubs */
  h.evaluate("renderCites = __real.renderCites; paintContext = __real.paintContext; estimateLocalContext = __real.estimateLocalContext");
  return h;
}

test("a restored long conversation sends only the latest 80 fallback turns without deleting local history", async () => {
  const transcript = Array.from({ length: 240 }, (_, i) => ({ role: i % 2 ? "assistant" : "user", text: "原文-" + i }));
  let payload;
  const h = contextUi(async (_url, options) => {
    if (!options) return response({ session_id: "long-history", transcript, context: {} });
    payload = JSON.parse(options.body);
    return { ok: true, body: bytesStream(encoder.encode(frame("done", { text: "继续回答" }))) };
  });
  await h.evaluate('cbProjOpenSession({ session_id: "long-history" })');
  await h.submit("现在继续");
  assert.equal(payload.history.length, 80);
  assert.equal(payload.history[0].content, "原文-160");
  assert.equal(payload.history.at(-1).content, "原文-239");
  assert.equal(payload.message, "现在继续");
  assert.equal(h.evaluate("state.history.length"), 242);
});

test("new task clears report, remembered text and search state while preserving context policy", () => {
  const h = contextUi(() => assert.fail("new local task does not fetch"));
  h.evaluate('state.context = { limit: 65536, reserve: 8192, lastReport: { note: "旧任务", components: {} } }; state.history = [{ role: "user", content: "旧正文" }];');
  h.elements.ctxMemory.textContent = "旧任务记忆";
  h.elements.ctxQuery.value = "旧查询";
  h.elements.ctxResults.appendChild(element());
  h.elements.ctxSearchStatus.textContent = "旧结果";
  h.elements.ctxSearch.disabled = true;
  h.evaluate("cbNewLocalSession()");
  assert.equal(h.evaluate("state.context.lastReport"), null);
  assert.equal(h.evaluate("state.context.limit"), 65536);
  assert.equal(h.elements.ctxResults.children.length, 0);
  assert.equal(h.elements.ctxQuery.value, "");
  assert.equal(h.elements.ctxSearchStatus.textContent, "");
  assert.equal(h.elements.ctxSearch.disabled, false);
  assert.doesNotMatch(h.elements.ctxMemory.textContent, /旧任务/);
  assert.match(h.elements.ctxText.textContent, /65,536/);
});

test("session restore with empty context estimates the current policy instead of displaying zero of zero", async () => {
  const h = contextUi(async () => response({ session_id: "restored", transcript: [{ role: "user", text: "已有正文" }], context: {} }));
  h.evaluate('state.context.lastReport = { components: {}, note: "另一个任务的上下文", limit: 999 };');
  h.elements.ctxMemory.textContent = "另一个任务的记忆";
  await h.evaluate('cbProjOpenSession({ session_id: "restored" })');
  assert.equal(h.evaluate("state.context.lastReport"), null);
  assert.doesNotMatch(h.elements.ctxText.textContent, /0 \/ 0|另一个任务/);
  assert.match(h.elements.ctxText.textContent, /32,768/);
  assert.doesNotMatch(h.elements.ctxMemory.textContent, /另一个任务/);
});

test("memory fetch is literal text and cannot overwrite a new session or a later refresh", async () => {
  const pending = [];
  const h = contextUi(() => new Promise(resolve => pending.push(resolve)));
  const old = h.evaluate("cbContextLoad()");
  h.evaluate("cbNewLocalSession()");
  const current = h.evaluate("cbContextLoad()");
  pending[0](response({ memory_text: "旧任务内容", context: { note: "旧报告", components: {} } }));
  await old;
  assert.equal(h.elements.ctxMemory.textContent, "正在读取任务记忆…");
  pending[1](response({ memory_text: "<script>历史文本</script>", context: {} }));
  await current;
  assert.equal(h.elements.ctxMemory.textContent, "<script>历史文本</script>");
  assert.equal(h.elements.ctxMemory["aria-busy"], "false");
  const first = h.evaluate("cbContextLoad()");
  const second = h.evaluate("cbContextLoad()");
  pending[3](response({ memory_text: "最新刷新" }));
  await second;
  pending[2](response({ memory_text: "迟到的旧刷新" }));
  await first;
  assert.equal(h.elements.ctxMemory.textContent, "最新刷新");
});

test("memory request started before navigation cannot write during the pending session load", async () => {
  const pending = [];
  const h = contextUi(() => new Promise(resolve => pending.push(resolve)));
  const memory = h.evaluate("cbContextLoad()");
  const navigation = h.evaluate('cbProjOpenSession({ session_id: "next-task" })');
  pending[0](response({ memory_text: "旧任务迟到内容" }));
  await memory;
  assert.doesNotMatch(h.elements.ctxMemory.textContent, /旧任务迟到内容/);
  pending[1](response({ session_id: "next-task", transcript: [] }));
  await navigation;
  assert.equal(h.evaluate("state.session"), "next-task");
});

test("local search sends only selected task attachments and a source button reads same-origin text", async () => {
  const requests = [];
  const h = contextUi(async (url, options) => {
    requests.push({ url, options });
    if (options) return response({ citations: [{ layer: "history", title: "历史目标", start: 10, end: 30,
      snippet: "<img src=x onerror=bad()>", url: "/api/context/source?session_id=search-task&id=message-1" }] });
    return response({ text: "<script>作为原文显示</script>" });
  });
  h.evaluate('state.session = "search-task"; state.attachments = [{ id: "selected" }, { id: "job:generated" }];');
  h.elements.ctxQuery.value = "  交付日期  ";
  await h.evaluate("cbContextSearch()");
  assert.deepEqual(JSON.parse(requests[0].options.body), { session_id: "search-task", query: "交付日期", attachments: ["selected"] });
  assert.match(h.elements.ctxSearchStatus.textContent, /找到 1/);
  const card = h.elements.ctxResults.children[1];
  const item = card.children[1].children[0];
  assert.match(item.innerHTML, /&lt;img/);
  const button = item.children[0];
  assert.equal(button.type, "button");
  assert.match(button.textContent, /查看原文.*10–30/);
  await button.listeners.click();
  assert.equal(requests[1].url, "/api/context/source?session_id=search-task&id=message-1");
  assert.equal(item.children[1].textContent, "<script>作为原文显示</script>");
  assert.equal(button.hidden, true);
});

test("older searches cannot clear a newer pending search or insert results into another task", async () => {
  const pending = [];
  const h = contextUi(() => new Promise(resolve => pending.push(resolve)));
  h.elements.ctxQuery.value = "第一次";
  const first = h.evaluate("cbContextSearch()");
  h.elements.ctxQuery.value = "第二次";
  const second = h.evaluate("cbContextSearch()");
  pending[0](response({ citations: [{ title: "旧结果" }] }));
  await first;
  assert.equal(h.elements.ctxSearch.disabled, true);
  assert.equal(h.elements.ctxResults.children.length, 0);
  h.evaluate("cbNewLocalSession()");
  pending[1](response({ citations: [{ title: "另一个任务" }] }));
  await second;
  assert.equal(h.elements.ctxResults.children.length, 0);
  assert.equal(h.elements.ctxSearchStatus.textContent, "");
  assert.equal(h.elements.ctxSearch.disabled, false);
});

test("memory/search failures recover controls and keyboard search respects IME composition", async () => {
  const requests = [];
  const h = contextUi(async (url) => { requests.push(url); return { ok: false, statusText: "服务暂不可用" }; });
  await h.evaluate("cbContextLoad()");
  assert.match(h.elements.ctxMemory.textContent, /读取失败.*服务暂不可用/);
  assert.equal(h.elements.ctxMemory["aria-busy"], "false");
  h.elements.ctxQuery.value = "查询";
  h.elements.ctxQuery.listeners.keydown({ key: "Enter", isComposing: true, preventDefault() { assert.fail("IME"); } });
  assert.equal(requests.length, 1);
  let prevented = false;
  h.elements.ctxQuery.listeners.keydown({ key: "Enter", isComposing: false, preventDefault() { prevented = true; } });
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(prevented, true);
  assert.equal(requests.length, 2);
  assert.match(h.elements.ctxSearchStatus.textContent, /检索失败.*服务暂不可用/);
  assert.equal(h.elements.ctxSearch.disabled, false);
});

test("context opener opens the dock, focuses the labelled search input, and reads memory", async () => {
  const h = contextUi(async () => response({ memory_text: "已有任务记忆" }));
  h.elements.ctxOpen.listeners.click();
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(h.elements.ctxOpen.expanded, true);
  assert.equal(h.elements.ctxQuery.focused, true);
  assert.equal(h.elements.ctxMemory.textContent, "已有任务记忆");
  const html = fs.readFileSync(path.join(__dirname, "../demo/static/index.html"), "utf8");
  assert.match(html, /<label for="ctxQuery">/);
  assert.match(html, /id="ctxSearchStatus" role="status"/);
  assert.match(html, /id="ctxRebuild"[^>]*aria-describedby="ctxRebuildHint"/);
  assert.match(html, /id="ctxMemoryStatus" role="status"/);
  assert.match(html, /保留对话、附件与交付物，不调用模型/);
});

test("memory status explains disabled or unavailable caches as literal text and supports older servers", async () => {
  let payload = { memory_text: "规则记忆", memory_status: { state: "unavailable", note: "规则记忆暂不可用，可重新整理。" },
    semantic_status: { state: "disabled", note: "<script>摘要开关已关闭</script>" } };
  const h = contextUi(async () => response(payload));
  await h.evaluate("cbContextLoad()");
  assert.match(h.elements.ctxMemoryStatus.textContent, /规则记忆暂不可用/);
  assert.match(h.elements.ctxMemoryStatus.textContent, /<script>摘要开关已关闭<\/script>/);
  assert.equal(h.elements.ctxMemoryStatus.children.length, 0);
  assert.doesNotMatch(h.elements.ctxMemoryStatus.textContent, /unavailable|disabled/);
  payload = { memory_text: "旧版服务器记忆" };
  await h.evaluate("cbContextLoad()");
  assert.equal(h.elements.ctxMemoryStatus.textContent, "");
  assert.equal(h.elements.ctxMemory.textContent, "旧版服务器记忆");
});

test("rebuild posts only the task id and replaces derived memory without changing original task choices", async () => {
  const requests = [];
  const h = contextUi(async (url, options) => {
    requests.push({url, options});
    return response({ ok: true, note: "已从原文重新整理；原始资料已保留。", memory_text: "重新提取的规则记忆", semantic_memory_text: "",
      semantic_status: { enabled: true, state: "missing", note: "旧模型摘要已撤回，开启后可按需要重新生成。" },
      context: { mode: "local", limit: 32768, used: 0, note: "尚无新的模型请求。", semantic: { status: "reset", note: "旧摘要已撤回。", model_calls: 0 } },
      rebuild: { semantic_cleared: true, model_calls: 0 } });
  });
  h.evaluate('state.session="rebuild-task"; state.summoned=new Set(["steel"]); state.history=[{role:"user",content:"原始目标"}];' +
    'state.attachments=[{id:"attachment-1"}]; state.attachmentRoles={"attachment-1":"reference"}; state.context.semantic_summary=true;' +
    'state.context.lastReport={note:"旧模型报告", components:{}};');
  h.elements.ctxMemory.textContent = "旧语义摘要";
  h.elements.ctxQuery.value = "用户自己的搜索词";
  h.elements.cbCapabilityBody.textContent = "已选择岗位的工具说明";
  h.elements.ctxResults.appendChild(element());
  await h.elements.ctxRebuild.listeners.click();
  assert.equal(requests.length, 1);
  assert.equal(requests[0].url, "/api/context/rebuild");
  assert.equal(requests[0].options.method, "POST");
  assert.deepEqual(JSON.parse(requests[0].options.body), {session_id: "rebuild-task"});
  assert.equal(h.elements.ctxMemory.textContent, "重新提取的规则记忆");
  assert.match(h.elements.ctxMemoryStatus.textContent, /原始资料已保留.*旧模型摘要已撤回/s);
  assert.equal(h.elements.ctxResults.children.length, 0);
  assert.equal(h.elements.ctxQuery.value, "用户自己的搜索词");
  assert.equal(h.elements.cbCapabilityBody.textContent, "已选择岗位的工具说明");
  assert.equal(h.evaluate("state.session"), "rebuild-task");
  assert.equal(h.evaluate("JSON.stringify([...state.summoned])"), '["steel"]');
  assert.equal(h.evaluate("state.history[0].content"), "原始目标");
  assert.equal(h.evaluate("state.attachments[0].id"), "attachment-1");
  assert.equal(h.evaluate("state.attachmentRoles['attachment-1']"), "reference");
  assert.equal(h.evaluate("state.context.semantic_summary"), true);
  assert.match(h.evaluate("state.context.lastReport.note"), /尚无新的模型请求/);
  assert.doesNotMatch(h.elements.ctxText.textContent, /旧模型报告/);
  assert.equal(h.elements.ctxRebuild.textContent, "重新整理记忆");
  assert.equal(h.elements.ctxMemory["aria-busy"], "false");
  assert.equal(h.announcements.length, 1);
});

test("rebuild blocks duplicate actions and late memory, search, and source responses cannot restore discarded context", async () => {
  const pending = [];
  const h = contextUi((url, options) => new Promise(resolve => pending.push({url, options, resolve})));
  const reading = h.evaluate("cbContextLoad()");
  h.elements.ctxQuery.value = "旧摘要字段";
  const searching = h.evaluate("cbContextSearch()");
  h.evaluate('var citeBody=addMsg("assistant","资料",""); renderCites([{url:"/api/context/source?session_id=test&id=source-1",start:0,end:4,title:"原文"}],citeBody);');
  const item = h.messages[0].body.parentElement.children[1].children[1].children[0];
  const sourceButton = item.children[0];
  const sourcing = sourceButton.listeners.click();
  const rebuilding = h.evaluate("cbContextRebuild()");
  assert.equal(pending.length, 4);
  for (const id of ["ctxRebuild", "ctxRefresh", "ctxSearch"]) assert.equal(h.elements[id].disabled, true);
  await h.evaluate("cbContextRebuild(); cbContextLoad(); cbContextSearch();");
  await sourceButton.listeners.click();
  assert.equal(pending.length, 4);
  pending[3].resolve(response({ok:true, memory_text:"重建后的规则记忆", semantic_memory_text:""}));
  await rebuilding;
  pending[0].resolve(response({memory_text:"迟到的旧摘要", context:{mode:"local", note:"旧报告"}}));
  pending[1].resolve(response({citations:[{title:"旧索引片段"}]}));
  pending[2].resolve(response({text:"旧来源响应"}));
  await Promise.all([reading, searching, sourcing]);
  assert.equal(h.elements.ctxMemory.textContent, "重建后的规则记忆");
  assert.equal(h.elements.ctxResults.children.length, 0);
  assert.equal(item.children.length, 1);
  assert.equal(sourceButton.hidden, false);
  assert.equal(sourceButton.disabled, false);
  assert.doesNotMatch(h.elements.ctxText.textContent, /旧报告/);
  for (const id of ["ctxRebuild", "ctxRefresh", "ctxSearch"]) assert.equal(h.elements[id].disabled, false);
});

test("rebuild failures keep displayed memory, surface server reasons, and restore controls for retry", async () => {
  for (const [status, message] of [[409,"任务运行中，请稍后重试"], [403,"当前为只读模式"], [404,"请先保存一个任务"], [400,"缓存不是普通文件"], [500,"重建失败，原文未改"]]) {
    const h = contextUi(async () => ({ok:false, status, statusText:message}));
    h.elements.ctxMemory.textContent = "之前查看的记忆";
    h.elements.ctxResults.appendChild(element());
    h.elements.ctxQuery.value = "保留的搜索词";
    await h.evaluate("cbContextRebuild()");
    assert.equal(h.elements.ctxMemory.textContent, "之前查看的记忆");
    assert.equal(h.elements.ctxMemoryStatus.textContent, "重新整理失败：" + message);
    assert.equal(h.elements.ctxResults.children.length, 1);
    assert.equal(h.elements.ctxQuery.value, "保留的搜索词");
    assert.equal(h.elements.ctxRebuild.textContent, "重新整理记忆");
    for (const id of ["ctxRebuild", "ctxRefresh", "ctxSearch"]) assert.equal(h.elements[id].disabled, false);
    assert.equal(h.elements.ctxMemory["aria-busy"], "false");
    assert.equal(h.announcements.length, 0);
  }
});

test("an unconfirmed rebuild response never clears existing memory or reports success", async () => {
  const h = contextUi(async () => response({ok:false, note:"未执行"}));
  h.elements.ctxMemory.textContent = "已有记忆";
  await h.evaluate("cbContextRebuild()");
  assert.equal(h.elements.ctxMemory.textContent, "已有记忆");
  assert.match(h.elements.ctxMemoryStatus.textContent, /未返回有效的重建结果/);
  assert.equal(h.announcements.length, 0);
  assert.equal(h.elements.ctxRebuild.disabled, false);
});

test("a rebuild from a previous task cannot overwrite or unlock the current task rebuild", async () => {
  for (const firstResult of [response({ok:true,memory_text:"旧任务重建结果"}), {ok:false,statusText:"旧任务失败"}]) {
    const pending = [];
    const h = contextUi(() => new Promise(resolve => pending.push(resolve)));
    const first = h.evaluate("cbContextRebuild()");
    h.evaluate("cbNewLocalSession()");
    assert.equal(h.elements.ctxRebuild.disabled, false);
    assert.equal(h.elements.ctxMemoryStatus.textContent, "");
    const next = h.evaluate("cbContextRebuild()");
    pending[0](firstResult);
    await first;
    assert.equal(h.elements.ctxRebuild.disabled, true);
    assert.equal(h.elements.ctxMemory["aria-busy"], "true");
    assert.doesNotMatch(h.elements.ctxMemory.textContent + h.elements.ctxMemoryStatus.textContent, /旧任务/);
    pending[1](response({ok:true,memory_text:"当前任务记忆"}));
    await next;
    assert.equal(h.elements.ctxMemory.textContent, "当前任务记忆");
    assert.equal(h.elements.ctxRebuild.disabled, false);
  }
});

test("a running local task explains why rebuilding must wait without sending a mutation", async () => {
  const h = contextUi(() => assert.fail("do not rebuild while the current local turn is active"));
  h.evaluate("runState.active={session:state.session};");
  h.elements.ctxMemory.textContent = "当前记忆";
  await h.evaluate("cbContextRebuild()");
  assert.match(h.elements.ctxMemoryStatus.textContent, /任务正在处理中/);
  assert.equal(h.elements.ctxMemory.textContent, "当前记忆");
  assert.equal(h.evaluate("cbContextRebuildRun"), null);
});

test("sending during rebuild retains the draft and cannot leave the context controls locked", async () => {
  let finish;
  const requests = [];
  const h = contextUi((url) => {
    requests.push(url);
    if (url === "/api/context/rebuild") return new Promise(resolve => { finish = resolve; });
    return Promise.resolve({ok:true,body:bytesStream(encoder.encode(frame("done",{text:"新回答"})))});
  });
  const rebuilding = h.evaluate("cbContextRebuild()");
  const navigation = h.evaluate("cbSessionRequest");
  await h.submit("待发送的新要求");
  assert.deepEqual(requests, ["/api/context/rebuild"]);
  assert.equal(h.elements.input.value, "待发送的新要求");
  assert.equal(h.evaluate("cbSessionRequest"), navigation);
  assert.equal(h.evaluate("state.history.length"), 0);
  assert.match(h.elements.ctxMemoryStatus.textContent, /输入内容已保留/);
  finish(response({ok:true,memory_text:"重建记忆"}));
  await rebuilding;
  assert.equal(h.elements.ctxRebuild.disabled, false);
  await h.submit(h.elements.input.value);
  assert.deepEqual(requests, ["/api/context/rebuild", "/api/chat"]);
  assert.equal(h.evaluate("state.history.length"), 2);
});

test("task memory turns semantic JSON into classified unverified text with exact literal citations", async () => {
  const items = ["goal", "decision", "constraint", "unresolved", "result"].map((kind, index) => ({
    kind, text: "<script>模型参考" + index + "</script>", verified: false,
    trust: kind === "result" ? "assistant_unverified" : "user_stated_unverified",
    evidence: [{ message_id: "message-" + index, start: 3, end: 8, quote: "<b>依据</b>" }],
  }));
  let payload = { memory_text: "规则记忆", semantic_memory_text: JSON.stringify({ coverage_complete: true, covered_messages: 12, items }),
    semantic_memory: { path: "private-cache.json", raw: "do-not-display" } };
  const h = contextUi(async () => response(payload));
  await h.evaluate("cbContextLoad()");
  const text = h.elements.ctxMemory.textContent;
  assert.ok(text.startsWith("规则记忆\n\n模型语义摘要（未核验）\n"));
  for (const label of ["目标", "决定", "约束", "待办", "结果"]) assert.ok(text.includes(label + "（未核验）"));
  assert.match(text, /来源消息 message-0 · 字符 3–8（左闭右开）/);
  assert.match(text, /原文：“<b>依据<\/b>”/);
  assert.match(text, /助手历史陈述，未视为已完成或已验证事实/);
  assert.match(text, /已处理历史 12 条/);
  assert.doesNotMatch(text, /coverage_complete|message_id|user_stated_unverified|private-cache|do-not-display/);
  assert.equal(h.elements.ctxMemory.children.length, 0);
  for (const text of [undefined, "", "   ", { path: "private-cache.json" }]) {
    payload = { memory_text: "规则记忆", semantic_memory_text: text };
    await h.evaluate("cbContextLoad()");
    assert.equal(h.elements.ctxMemory.textContent, "规则记忆");
  }
});

test("semantic text explains partial coverage and omitted items without claiming full retained history", () => {
  const h = contextUi(() => assert.fail("no network"));
  const data = { covered_messages: 50, remaining_messages: 8, omitted_items: 2, evicted_segments: 3,
    skipped_messages: 1, skipped_chars: 9, coverage_complete: false, items: [] };
  const text = h.evaluate("cbSemanticMemoryText(" + JSON.stringify(JSON.stringify(data)) + ")");
  assert.match(text, /仅展示部分历史摘要/);
  assert.match(text, /尚有 8 条较早历史待处理/);
  assert.match(text, /省略 2 条摘要/);
  assert.match(text, /移出 3 段旧摘要，原文仍保留/);
  assert.match(text, /1 条历史及 9 字符未纳入/);
  assert.doesNotMatch(text, /evicted_segments|omitted_items|coverage_complete/);
});

test("malformed semantic JSON or item data yields a short unavailable message and preserves rule memory", async () => {
  for (const raw of ["<script>bad JSON</script>", "null", "[]", "{}", JSON.stringify({items:[{kind:"goal",text:"无来源"}]}),
    JSON.stringify({items:[],covered_messages:"50"}), JSON.stringify({items:[{kind:"__proto__",text:"bad",evidence:[]}]}),
    "x".repeat(65537)]) {
    const h = contextUi(async () => response({memory_text:"保留的规则记忆",semantic_memory_text:raw}));
    await h.evaluate("cbContextLoad()");
    assert.match(h.elements.ctxMemory.textContent, /^保留的规则记忆\n\n模型语义摘要（未核验）\n模型语义摘要暂不可用/);
    assert.equal(h.elements.ctxMemory["aria-busy"], "false");
    assert.ok(h.elements.ctxMemory.textContent.length < 160);
  }
});

test("semantic memory from an old task cannot appear after navigation", async () => {
  let finish;
  const h = contextUi(() => new Promise(resolve => { finish = resolve; }));
  const loading = h.evaluate("cbContextLoad()");
  h.evaluate("cbNewLocalSession()");
  finish(response({ memory_text: "旧规则记忆", semantic_memory_text: "旧模型摘要" }));
  await loading;
  assert.doesNotMatch(h.elements.ctxMemory.textContent, /旧规则记忆|旧模型摘要|模型语义摘要/);
});

test("context settings load, submit numeric limits, clear stale reports, and restore startup limits", async () => {
  const calls = [];
  let current = { ...config(), context: { limit: 65536, reserve: 8192 } };
  const h = contextUi(async (_url, options) => {
    if (options.method === "POST") {
      const body = JSON.parse(options.body);
      calls.push(body);
      current = { ...config(), context: body.clear ? { limit: 32768, reserve: 4096 } : { limit: body.context_limit, reserve: body.output_reserve } };
    }
    return response(current);
  });
  await h.evaluate("cbLlmLoad()");
  assert.equal(h.elements.cbLlmContext.value, 65536);
  assert.equal(h.elements.cbLlmReserve.value, 8192);
  h.evaluate('state.context.lastReport = { note: "旧窗口" };');
  h.elements.cbLlmContext.value = "131072";
  h.elements.cbLlmReserve.value = "16384";
  h.elements.cbLlmSave.listeners.click();
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(calls[0].context_limit, 131072);
  assert.equal(calls[0].output_reserve, 16384);
  assert.equal(h.evaluate("state.context.lastReport"), null);
  assert.equal(h.elements.cbLlmContext.disabled, false);
  h.elements.cbLlmReset.listeners.click();
  await new Promise(resolve => setImmediate(resolve));
  assert.deepEqual(calls[1], { clear: true });
  assert.equal(h.elements.cbLlmContext.value, 32768);
  assert.equal(h.elements.cbLlmReserve.value, 4096);
  assert.match(h.elements.ctxText.textContent, /32,768/);
});

test("semantic summary checkbox loads, saves strict booleans, and resets without generating a summary", async () => {
  const requests = [];
  let current = { ...config(), semantic_summary: true, context: { limit: 32768, reserve: 4096, semantic_summary: true } };
  const h = contextUi(async (url, options) => {
    requests.push([url, options.method || "GET", options.body && JSON.parse(options.body)]);
    if (options.method === "POST") {
      const payload = JSON.parse(options.body);
      current = { ...current, semantic_summary: payload.clear ? false : payload.semantic_summary };
      current.context.semantic_summary = current.semantic_summary;
    }
    return response(current);
  });
  await h.evaluate("cbLlmLoad()");
  assert.equal(h.elements.cbLlmSemantic.checked, true);
  assert.equal(requests.length, 1);
  h.elements.cbLlmSemantic.checked = false;
  h.elements.cbLlmSave.listeners.click();
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(requests[1][2].semantic_summary, false);
  assert.equal(typeof requests[1][2].semantic_summary, "boolean");
  assert.equal(h.elements.cbLlmSemantic.checked, false);
  assert.equal(h.elements.cbLlmSemantic.disabled, false);
  h.elements.cbLlmSemantic.checked = true;
  h.elements.cbLlmReset.listeners.click();
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(h.elements.cbLlmSemantic.checked, false);
  assert.equal(h.evaluate("state.context.semantic_summary"), false);
  assert.ok(requests.every(row => row[0] === "/api/llm-config"));
  const html = fs.readFileSync(path.join(__dirname, "../demo/static/index.html"), "utf8");
  assert.match(html, /for="cbLlmSemantic"/);
  assert.match(html, /id="cbLlmSemantic" aria-describedby="cbLlmSemanticNote"/);
  assert.match(html, /每轮最多增加 1 次同模型调用/);
  assert.match(html, /失败时沿用规则记忆与检索，完整原文保留/);
});

test("semantic summary setting rejects malformed server booleans and remains backwards compatible", async () => {
  for (const semantic_summary of ["true", "false", 1, null]) {
    const h = ui(async () => response({ ...config(), semantic_summary }));
    await h.evaluate("cbLlmLoad()");
    assert.match(h.elements.cbLlmStatus.textContent, /语义摘要设置无效/);
  }
  const legacy = ui(async () => response(config()));
  await legacy.evaluate("cbLlmLoad()");
  assert.equal(legacy.elements.cbLlmSemantic.checked, false);
  const nested = ui(async () => response({ ...config(), context: { limit: 32768, reserve: 4096, semantic_summary: true } }));
  await nested.evaluate("cbLlmLoad()");
  assert.equal(nested.elements.cbLlmSemantic.checked, true);
});

test("semantic context reports show backend notes and measured fields without inventing fees or retaining another task", async () => {
  for (const [status, note, calls] of [["generated", "已生成语义摘要", 1], ["reused", "复用已有语义摘要", 0],
    ["failed", "语义摘要失败，沿用规则记忆与检索", 1], ["disabled", "语义摘要未启用", 0]]) {
    const report = { components: {}, limit: 32768, used: 8192, pct: 25, note: "本轮上下文",
      semantic: { status, note: note + " <b>原文</b>", model_calls: calls, input_tokens: 700,
        output_reserve: 512, covered_messages: 12 } };
    const h = contextUi(async () => ({ ok: true, body: bytesStream(encoder.encode(frame("context", report) + frame("done", { text: "回答", context: report }))) }));
    await h.submit("问答测试");
    const shown = h.elements.ctxText.textContent;
    assert.ok(shown.includes(note + " <b>原文</b>"));
    assert.ok(shown.includes("摘要调用 " + calls + " 次"));
    assert.match(shown, /摘要输入估算 700 token.*摘要输出预留 512 token.*已处理历史 12 条/);
    assert.doesNotMatch(shown, /费用|无损|人民币/);
    assert.equal(h.elements.ctxText.children.length, 0);
    h.evaluate("cbNewLocalSession()");
    assert.ok(!h.elements.ctxText.textContent.includes(note));
  }
  const invalid = contextUi(() => assert.fail("no network"));
  invalid.evaluate('paintContext({limit:32768, note:"仅后端说明", semantic:{note:"尚未生成",model_calls:"2",input_tokens:-1,output_reserve:1.5}})');
  assert.equal(invalid.elements.ctxText.textContent, "仅后端说明 · 尚未生成");
});

function shownText(element) {
  return [element.textContent, ...element.children.map(shownText)].join("\n");
}

test("new task cards prefill routable tasks and clear old selections and confirmation without sending", () => {
  const h = ui(() => assert.fail("task card must not submit"));
  for (const [id, phrase] of [["tender-review", "综合检查投标响应"], ["backup-plan", "制定备份策略"], ["steel-note", "写钢构说明"]]) {
    h.evaluate('state.summoned.add("unrelated");');
    h.elements.confirmOk.value = "我明白，将由持证人员签认";
    h.evaluate('cbEmptyPrefill("' + id + '")');
    assert.ok(h.elements.input.value.includes(phrase));
    assert.equal(h.evaluate("state.summoned.size"), 0);
    assert.equal(h.evaluate("cbConfirmed()"), false);
  }
});

test("routing candidates render once and selecting one prepares an explicit retry without sending", async () => {
  const route = { ambiguous: true, reason: "设计变更需要区分交付", candidates: [
    { expert_ids: ["variation"], label: "变更签证", reason: "事实与工程量" },
    { expert_ids: ["design-coord"], label: "设计统筹", reason: "专业接口" }], steps: [] };
  let calls = 0;
  const h = ui(async () => {
    calls++;
    return { ok: true, body: bytesStream(encoder.encode(frame("status", { phase: "routing", route }) + frame("done", { text: "请明确交付", route }))) };
  });
  await h.submit("整理设计变更");
  const host = h.messages[1].body.parentElement;
  assert.equal(host.children.filter(child => child.className === "cb-route-card").length, 1);
  const choice = host.cbRouteCard.children.find(child => child.type === "button");
  assert.match(choice.textContent, /变更签证.*工程量/);
  choice.listeners.click();
  assert.equal(calls, 1);
  assert.equal(h.evaluate('[...state.summoned].join(",")'), "variation");
  assert.equal(h.elements.input.value, "整理设计变更");
  assert.equal(h.elements.input.focused, true);
});

test("route steps show actual parallel dependencies and keep text inert", () => {
  const h = ui(() => assert.fail("rendering is local"));
  h.evaluate('var body = addMsg("assistant", "岗位", ""); cbTaskRoutePaint({ reason: "<script>原文</script>", steps: [' +
    '{ id: "parse", label: "解析", depends_on: [] }, { id: "tech", label: "技术响应", depends_on: ["parse"] },' +
    '{ id: "compliance", label: "响应检查", depends_on: ["parse"] }] }, body, "任务");');
  const text = shownText(h.messages[0].body.parentElement.cbRouteCard);
  assert.match(text, /技术响应 · 前置步骤：“解析”/);
  assert.match(text, /响应检查 · 前置步骤：“解析”/);
  assert.match(text, /<script>原文<\/script>/);
});

test("worker completion does not mark the whole collaboration complete and final evidence and budget remain visible", () => {
  const h = contextUi(() => assert.fail("rendering is local"));
  h.evaluate('var body = addMsg("assistant", "岗位", ""); cbCollaborationPaint({ kind: "workflow", state: "running" }, body);' +
    'cbCollaborationPaint({ kind: "worker", state: "done", task_id: "worker-bid-tech", skill: "bid-tech" }, body);');
  const card = h.messages[0].body.parentElement.cbCollaborationCard;
  assert.match(card.children[0].textContent, /处理中/);
  assert.match(shownText(card), /bid-tech · 已完成/);
  h.evaluate('cbCollaborationPaint({ state: "failed", children: [{ skill: "bid-compliance", status: "failed", ' +
    'conclusions: [{ text: "可能缺少响应", origin: "model_analysis" }], evidence: [{ source_id: "requirement-1", title: "原文要求", quote: "须提交人员清单" }], ' +
    'unresolved: ["缺少人员清单"] }], review: { gaps: ["响应待补"], conflicts: [{ note: "工期不一致" }] }, ' +
    'metrics: { input_tokens: 300, output_estimated: 100, limit: 1000, reserved_tokens: 200, estimated: true, counter: "utf8-bytes" } }, body);');
  const text = shownText(card);
  assert.match(text, /协作进度 · 未完成/);
  assert.match(text, /模型分析，未核实/);
  assert.match(text, /requirement-1.*须提交人员清单/);
  assert.match(text, /缺少人员清单/);
  assert.match(text, /工期不一致/);
  assert.match(text, /汇总预算（估算）.*300.*100.*1,000/);
  assert.match(text, /总预留 200/);
  assert.doesNotMatch(text, /回答预留/);
  const meter = card.children.at(-1).children[1];
  assert.equal(meter.value, 400);
  assert.equal(meter["aria-label"], "协作汇总预算使用量");
});

test("collaboration SSE events display progress and preserve the final structured result", async () => {
  const h = contextUi(async () => ({ ok: true, body: bytesStream(encoder.encode(
    frame("collaboration", { kind: "workflow", state: "running" }) +
    frame("done", { text: "汇总完成", collaboration: { state: "done", children: [{ skill: "bid-tech", status: "done" }] } }))) }));
  await h.submit("综合检查投标响应");
  assert.match(shownText(h.messages[1].body.parentElement.cbCollaborationCard), /协作进度 · 已完成/);
});

test("capability browser keeps all 66 posts and displays inputs, tool availability and acceptance", async () => {
  const capability = { expert_id: "steel", name: "钢结构", risk: "high", inputs: ["荷载来源"],
    tools: [{ name: "steel__memo", label: "说明起草", available: true }, { name: "solver", label: "计算", available: false }],
    steps: [{ action: "核对来源" }], output_sections: ["连接与稳定"], acceptance: ["未知截面保持待填"], limitations: ["不替代结构计算"] };
  const h = ui(async url => {
    assert.equal(url, "/api/experts/steel/capability");
    return response(capability);
  });
  h.evaluate('state.experts = Array.from({ length: 66 }, (_, i) => ({ id: "post-" + i, name: "岗位" + i })); cbCapabilityCatalog();');
  assert.equal(h.elements.cbCapabilityPost.children.length, 67);
  await h.evaluate('cbExpertCapability("steel")');
  const text = shownText(h.elements.cbCapabilityBody);
  assert.match(text, /成稿需人工确认/);
  assert.match(text, /荷载来源/);
  assert.match(text, /steel__memo.*可调用/);
  assert.match(text, /solver.*未接通/);
  assert.match(text, /未知截面保持待填/);
  assert.match(text, /不替代结构计算/);
  assert.equal(h.elements.cbCapabilityBody["aria-busy"], "false");
});

test("capability responses cannot overwrite later selections or new tasks and failures are actionable", async () => {
  const pending = [];
  const h = ui(() => new Promise(resolve => pending.push(resolve)));
  const first = h.evaluate('cbExpertCapability("steel")');
  const second = h.evaluate('cbExpertCapability("it-data")');
  pending[1](response({ expert_id: "it-data", name: "备份策略", inputs: ["系统名"] }));
  await second;
  pending[0](response({ expert_id: "steel", name: "旧资料" }));
  await first;
  assert.doesNotMatch(shownText(h.elements.cbCapabilityBody), /旧资料/);
  const third = h.evaluate('cbExpertCapability("steel")');
  h.evaluate("cbNewLocalSession()");
  pending[2](response({ expert_id: "steel", name: "迟到资料" }));
  await third;
  assert.doesNotMatch(shownText(h.elements.cbCapabilityBody), /迟到资料/);
  assert.equal(h.elements.cbCapabilityBody["aria-busy"], "false");
  const broken = ui(async () => ({ ok: false, statusText: "接口暂不可用" }));
  await broken.evaluate('cbExpertCapability("steel")');
  assert.match(broken.elements.cbCapabilityBody.textContent, /读取岗位资料失败.*接口暂不可用/);
});

test("attachment purpose controls are labelled, optional and sent only for selected files", async () => {
  let payload;
  const h = ui(async (_url, options) => {
    payload = JSON.parse(options.body);
    return { ok: true, body: bytesStream(encoder.encode(frame("done", { text: "已处理" }))) };
  });
  h.evaluate("cbAttachRender = __real.cbAttachRender");
  h.evaluate('state.attachments = [{ id: "tender-file", name: "招标.pdf" }, { id: "response-file", name: "响应.docx" }]; cbAttachRender();');
  const role = h.elements.attaches.children[0].children[1];
  assert.equal(role["aria-label"], "资料用途：招标.pdf");
  assert.equal(role.value, "");
  role.value = "tender";
  role.listeners.change();
  h.evaluate('state.attachmentRoles["removed-file"] = "response";');
  await h.submit("检查投标响应");
  assert.deepEqual(payload.attachment_roles, { "tender-file": "tender" });
  assert.deepEqual(payload.attachments, ["tender-file", "response-file"]);
  role.value = "";
  role.listeners.change();
  assert.equal(h.evaluate('state.attachmentRoles["tender-file"]'), undefined);
});

test("restored attachment purposes accept only selected files and new sessions clear them", async () => {
  const h = ui(async () => response({ session_id: "purpose-session", transcript: [], attachments: [{ id: "a" }, { id: "b" }],
    attachment_roles: { a: "response", b: "invalid", missing: "tender" } }));
  await h.evaluate('cbProjOpenSession({ session_id: "purpose-session" })');
  assert.equal(h.evaluate('JSON.stringify(state.attachmentRoles)'), '{"a":"response"}');
  h.evaluate("cbNewLocalSession()");
  assert.equal(h.evaluate('JSON.stringify(state.attachmentRoles)'), '{}');
});

test("tender response comparison distinguishes evidence candidates from verified compliance", () => {
  const h = ui(() => assert.fail("rendering is local"));
  h.evaluate('var body = addMsg("assistant", "岗位", ""); cbCollaborationPaint({ state: "done", submit_blocked: true, review: {' +
    'response_comparison: [{ requirement: "提供人员清单", status: "candidate_requires_review", response_evidence: [{ source_id: "response-1", quote: "人员名单附件" }] },' +
    '{ requirement: "承诺工期", status: "not_matched", response_evidence: [] }, { requirement: "提供业绩", status: "not_provided" }] } }, body);');
  const text = shownText(h.messages[0].body.parentElement.cbCollaborationCard);
  assert.match(text, /找到候选，待人工核验/);
  assert.match(text, /response-1.*人员名单附件/);
  assert.match(text, /未匹配到响应/);
  assert.match(text, /未提供响应资料/);
  assert.match(text, /内部讨论草稿/);
  assert.doesNotMatch(text, /核验通过|响应合格/);
});

test("tender response comparison shows a numeric mismatch as a review item, not a verdict", () => {
  const h = ui(() => assert.fail("rendering is local"));
  h.evaluate('var body = addMsg("assistant", "岗位", ""); cbCollaborationPaint({ state: "done", submit_blocked: true, review: {' +
    'response_comparison: [{ requirement: "工期60日历天。", status: "conflict_requires_review",' +
    ' response_evidence: [{ source_id: "response-1", quote: "供应商自述工期999日历天。" }],' +
    ' conflicts: [{ label: "工期", note: "工期：响应 999日历天 超过招标 60日历天，待人工核验" }] }] } }, body);');
  const text = shownText(h.messages[0].body.parentElement.cbCollaborationCard);
  assert.match(text, /数值与招标不一致，待人工核验/);
  assert.match(text, /响应 999日历天 超过招标 60日历天/);
  assert.match(text, /response-1.*供应商自述工期999日历天/);
  assert.doesNotMatch(text, /不合格|废标|核验通过|响应合格/);
});

test("restoring a collaboration shows saved routing, evidence and unresolved work without re-executing", async () => {
  let calls = 0;
  const h = ui(async () => {
    calls++;
    return response({ session_id: "saved-team", transcript: [{ role: "user", text: "检查投标响应" }, { role: "assistant", text: "已留存草稿" }],
      route: { reason: "综合检查分工", steps: [] }, collaboration: { state: "done", children: [{ skill: "bid-compliance", status: "done", unresolved: ["资料仍待补"] }] } });
  });
  await h.evaluate('cbProjOpenSession({ session_id: "saved-team" })');
  const host = h.messages[1].body.parentElement;
  assert.match(shownText(host.cbRouteCard), /综合检查分工/);
  assert.match(shownText(host.cbCollaborationCard), /资料仍待补/);
  assert.equal(calls, 1);
});

test("custom-post capability absence explains the user SOP boundary without borrowing a built-in contract", async () => {
  const h = ui(async () => ({ ok: false, status: 404 }));
  await h.evaluate('cbExpertCapability("custom-expert")');
  assert.match(h.elements.cbCapabilityBody.textContent, /用户 SOP.*不借用其他岗位/);
});
