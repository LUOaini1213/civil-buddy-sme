#!/usr/bin/env node
/* The real page against the real backend.
 *
 * Loads demo/static/index.html into jsdom exactly as a browser would (scripts fetched from
 * the server, classic globals, no source slicing), talks to a real uvicorn with a fake model
 * upstream, and drives the UI through the DOM: send, stop, a dropped stream that resumes
 * from the last id, a background task, uploads with progress, drafts and stale turns.
 *
 * Unlike scripts/test_chat_stream.cjs this harness does not depend on comment markers in
 * app.js, so app.js can be split into modules without rewriting the tests.
 *
 *   node scripts/e2e/ui_dom.cjs        (also registered in scripts/check_project.py as ui-dom)
 */
"use strict";

const { test, before, after } = require("node:test");
const assert = require("node:assert/strict");
const { spawn } = require("node:child_process");
const fs = require("node:fs");
const path = require("node:path");
const net = require("node:net");
const { JSDOM, VirtualConsole, ResourceLoader } = require("jsdom");
const esbuild = require("esbuild");

const ROOT = path.resolve(__dirname, "..", "..");
const PYTHON = process.env.PYTHON || process.env.PY || (process.platform === "win32" ? "python" : "python3");
const OUT_ROOT = path.join(ROOT, "output", `e2e-ui-${process.pid}`);

function freePort() {
  return new Promise((resolve) => {
    const s = net.createServer();
    s.listen(0, "127.0.0.1", () => { const p = s.address().port; s.close(() => resolve(p)); });
  });
}

function sleep(ms) { return new Promise((r) => setTimeout(r, ms)); }

async function until(fn, { timeout = 15000, every = 50, what = "condition" } = {}) {
  const t0 = Date.now();
  for (;;) {
    let v;
    try { v = await fn(); } catch (e) { v = null; }
    if (v) return v;
    if (Date.now() - t0 > timeout) throw new Error("timed out waiting for " + what);
    await sleep(every);
  }
}

let upstream, server, base, dom, window, document;

before(async () => {
  fs.mkdirSync(OUT_ROOT, { recursive: true });
  const upPort = await freePort();
  upstream = spawn(PYTHON, [path.join(ROOT, "scripts", "e2e", "fake_upstream.py"), String(upPort)], { stdio: ["ignore", "pipe", "inherit"] });
  await new Promise((resolve, reject) => {
    upstream.stdout.on("data", (d) => { if (String(d).startsWith("READY")) resolve(); });
    upstream.on("exit", (c) => reject(new Error("fake upstream exited " + c)));
  });
  const port = await freePort();
  base = `http://127.0.0.1:${port}`;
  server = spawn(PYTHON, ["-m", "uvicorn", "app:app", "--host", "127.0.0.1", "--port", String(port), "--log-level", "warning"], {
    cwd: path.join(ROOT, "demo"),
    stdio: ["ignore", "inherit", "inherit"],
    env: {
      ...process.env,
      PYTHONPATH: ROOT,
      CIVIL_API_KEY: "e2e-fake-key",
      CIVIL_API_BASE: `http://127.0.0.1:${upPort}/v1`,
      CIVIL_MODEL: "fake-model",
      CIVIL_OUT_ROOT: OUT_ROOT,
      CIVIL_SANDBOX_ROOTS: ROOT,
      CIVIL_TOKEN: "",
    },
  });
  await until(() => fetch(base + "/api/health").then((r) => r.ok), { timeout: 30000, what: "workbench health" });
  const health = await fetch(base + "/api/health").then((r) => r.json());
  assert.equal(health.capabilities.event_log, true);
  assert.equal(health.capabilities.background_turns, true);
});

after(async () => {
  try { if (dom) dom.window.close(); } catch (e) { /* ignore */ }
  for (const p of [server, upstream]) { try { p && p.kill(); } catch (e) { /* ignore */ } }
  await sleep(200);
  try { fs.rmSync(OUT_ROOT, { recursive: true, force: true }); } catch (e) { /* ignore */ }
});

/* The page's fetch is Node's fetch; the streaming bodies are wrapped so a test can cut the
   cord the way a phone's OS does — the reader sees a network error, not an abort. */
const liveStreams = new Set();
const entryMetadataRequests = new Set();
function makeFetch(win) {
  return async function pageFetch(input, init) {
    const url = new URL(typeof input === "string" ? input : input.url, base).href;
    const options = { ...(init || {}) };
    if (["/api/agent/capabilities", "/packing/api/health", "/api/logistics/capabilities"].includes(new URL(url).pathname)) {
      assert.equal(options.method, "GET");
      assert.equal(options.credentials, "same-origin");
      assert.equal(options.redirect, "error");
      assert.equal(options.body, undefined);
      entryMetadataRequests.add(new URL(url).pathname);
    }
    if (options.body && typeof options.body === "object" && typeof options.body.entries === "function" && !(options.body instanceof globalThis.FormData)) {
      // jsdom FormData (from a fetch fallback) → Node FormData
      const fd = new globalThis.FormData();
      for (const [k, v] of options.body.entries()) {
        if (v && typeof v.arrayBuffer === "function") fd.append(k, new globalThis.Blob([await v.arrayBuffer()]), v.name || "file");
        else fd.append(k, String(v));
      }
      options.body = fd;
    }
    const res = await globalThis.fetch(url, options);
    if (new URL(url).pathname === "/api/health" && res.ok) {
      // Reproduce an older aggregate health contract without optional route flags.
      // The entry-specific metadata still comes from the actual backend endpoints.
      const health = await res.json();
      for (const key of ["agent_workbench", "packing_workbench", "logistics"]) delete health.capabilities[key];
      return new globalThis.Response(JSON.stringify(health), {
        status: res.status, headers: { "content-type": "application/json" },
      });
    }
    if (!res.body || !(res.headers.get("content-type") || "").includes("text/event-stream")) return res;
    let cut = null;
    const reader = res.body.getReader();
    const wrapped = new globalThis.ReadableStream({
      async pull(controller) {
        if (cut) { controller.error(cut); return; }
        const { done, value } = await reader.read();
        if (cut) { controller.error(cut); return; }
        if (done) { controller.close(); liveStreams.delete(handle); return; }
        controller.enqueue(value);
      },
      cancel() { liveStreams.delete(handle); return reader.cancel().catch(() => {}); },
    });
    const handle = { url, kill() { cut = new TypeError("network connection was lost"); reader.cancel().catch(() => {}); } };
    liveStreams.add(handle);
    return new globalThis.Response(wrapped, { status: res.status, statusText: res.statusText, headers: res.headers });
  };
}

/* jsdom cannot run <script type="module">. The page ships app.js as an ES module (no build
   step in production); here the same sources are bundled in memory with esbuild into one
   classic script and served in place of /static/app.js. Everything else comes from the real
   server. */
let bundle = null;
function bundledApp() {
  if (bundle) return bundle;
  const r = esbuild.buildSync({ entryPoints: [path.join(ROOT, "demo", "static", "app.js")], bundle: true, format: "iife",
    platform: "browser", target: "es2020", write: false, logLevel: "silent" });
  bundle = Buffer.from(r.outputFiles[0].text, "utf8");
  return bundle;
}

class PageResources extends ResourceLoader {
  fetch(url, options) {
    if (/\/static\/app\.js(\?|$)/.test(url)) return Promise.resolve(bundledApp());
    return super.fetch(url, options);
  }
}

async function loadPage() {
  const vc = new VirtualConsole();
  vc.on("jsdomError", (e) => { console.error("[page]", e && e.message ? e.message : e, e && e.detail && e.detail.stack ? e.detail.stack : ""); });
  vc.on("error", (...a) => console.error("[page:console]", ...a));
  const html = (await fetch(base + "/").then((r) => r.text()))
    .replace(/<script type="module" src="(\/static\/app\.js[^"]*)"><\/script>/, '<script src="$1"></script>');
  assert.ok(html.includes('src="/static/app.js'), "index.html loads app.js");
  dom = new JSDOM(html, {
    url: base + "/",
    virtualConsole: vc,
    runScripts: "dangerously",
    resources: new PageResources(),
    pretendToBeVisual: true,
    beforeParse(win) {
      win.fetch = makeFetch(win);
      for (const name of ["AbortController", "AbortSignal", "ReadableStream", "TextDecoder", "TextEncoder", "Headers", "Request", "Response"]) {
        win[name] = globalThis[name];
      }
      win.matchMedia = () => ({ matches: false, addEventListener() {}, removeEventListener() {}, addListener() {}, removeListener() {} });
      win.scrollTo = () => {};
      win.prompt = () => null;
      win.HTMLElement.prototype.scrollIntoView = function scrollIntoView() {};
    },
  });
  window = dom.window;
  document = window.document;
  await until(() => window.__cb && typeof window.__cb.cbAttachUpload === "function", { what: "app.js window surface (__cb)" });
  await until(() => window.__cb.cbCapability("chat") === true, { what: "boot()" });
  return window;
}

/* app.js is a module: its state and entry points are reachable only through window.__cb. */
const state = () => window.__cb.state;
const cb = () => window.__cb;

const $ = (id) => document.getElementById(id);
const logText = () => $("log").textContent;
const lastAssistant = () => { const all = document.querySelectorAll("#log .msg.assistant .body, #log .assistant .body"); return all[all.length - 1] || null; };

async function send(text) {
  $("input").value = text;
  $("input").dispatchEvent(new window.Event("input", { bubbles: true }));
  $("form").dispatchEvent(new window.Event("submit", { bubbles: true, cancelable: true }));
}

test("boot: the real page probes actual entry endpoints despite legacy aggregate health metadata", async () => {
  await loadPage();
  await until(() => ["agent_workbench", "packing_workbench", "logistics"].every(
    (name) => typeof cb().cbCapability(name) === "boolean") && entryMetadataRequests.size === 3
      && cb().cbCapability("logistics") === true,
    { what: "read-only entry metadata probes" });
  assert.ok(state().session, "a local session id exists after boot");
  assert.equal(cb().cbCapability("event_log"), true);
  assert.equal(cb().cbCapability("file_ref"), true);
  assert.equal($("send").disabled, true, "send is disabled while the composer is empty");
  assert.equal($("stop").hidden, true);
  assert.equal($("cbAgentEntry").hasAttribute("href"), false);
  assert.equal($("cbPackingEntry").hasAttribute("href"), false);
  assert.equal($("cbLogisticsEntry").getAttribute("href"), "/logistics");
  assert.match($("cbEntryAvailability").textContent, /Agent/);
  assert.equal(cb().cbCapability("agent_workbench"), false);
  assert.equal(cb().cbCapability("packing_workbench"), false);
  assert.equal(cb().cbCapability("logistics"), true);
});

test("send: tokens stream into the bubble and the turn ends with 回答完毕", async () => {
  await send("你好，请自我介绍");
  await until(() => lastAssistant() && lastAssistant().textContent.includes("片段2"), { what: "streamed tokens" });
  await until(() => $("form").getAttribute("aria-busy") === "false", { what: "turn end" });
  assert.ok(lastAssistant().textContent.includes("片段11"), lastAssistant().textContent);
  assert.equal(state().history.length, 2);
  assert.equal(state().history[1].content.trim(), lastAssistant().textContent.trim());
});

test("stop: the server turn is cancelled and the partial answer stays", async () => {
  await send("慢一点回答");
  await until(() => lastAssistant() && lastAssistant().textContent.includes("片段1"), { what: "second token" });
  assert.equal($("stop").hidden, false);
  $("stop").click();
  await until(() => $("form").getAttribute("aria-busy") === "false", { what: "stop to settle" });
  const detail = await fetch(base + "/api/sessions/" + state().session).then((r) => r.json());
  assert.equal(detail.turn_state.state, "cancelled");
  assert.ok(lastAssistant().textContent.includes("片段"), "partial text kept");
});

test("dropped stream: the page resumes from the last id via /events and finishes", async () => {
  await send("慢慢说一个长故事");
  await until(() => lastAssistant() && lastAssistant().textContent.includes("片段1 "), { what: "tokens before the cut" });
  const chatStream = [...liveStreams].find((h) => h.url.includes("/api/chat"));
  assert.ok(chatStream, "the chat stream is live");
  chatStream.kill(); // the OS drops the socket; the server turn keeps running
  await until(() => [...liveStreams].some((h) => h.url.includes("/events?after=")), { what: "resume request" });
  await until(() => $("form").getAttribute("aria-busy") === "false", { timeout: 40000, what: "resumed turn to finish" });
  assert.ok(lastAssistant().textContent.includes("片段39"), lastAssistant().textContent);
  assert.ok(!logText().includes("连接已中断"), "no dropped-stream error was shown");
  const detail = await fetch(base + "/api/sessions/" + state().session).then((r) => r.json());
  assert.equal(detail.turn_state.state, "done");
});

test("background task: /bg starts a session with no reader; its completion is announced with a link", async () => {
  const before = state().session;
  await send("/bg 并行算一下工期");
  await until(() => logText().includes("并行任务已开始"), { what: "background start" });
  assert.equal(state().session, before, "the current session is untouched");
  await until(() => $("cbToast") && !$("cbToast").hidden && $("cbToast").textContent.includes("已在后台完成"), { timeout: 30000, what: "completion toast" });
  $("cbToast").querySelector("button").click();
  await until(() => state().session !== before && lastAssistant() && lastAssistant().textContent.includes("片段11"), { what: "opened background session with its answer" });
});

test("upload: progress chip, then a chip whose name downloads the original by ref; wrong types are refused before sending", async () => {
  const sid = state().session;
  const big = "第一章 总则：本项目位于某市，工期 180 天。\n".repeat(400);
  const file = new window.File([big], "投标说明.txt", { type: "text/plain" });
  const bad = new window.File(["x"], "幻灯片.pptx");
  const run = cb().cbAttachUpload([file, bad]);
  await until(() => document.querySelector("#attaches .cb-att-chip.pending"), { what: "pending chip" });
  await run;
  assert.ok(logText().includes("未上传：幻灯片.pptx"), logText());
  const link = await until(() => document.querySelector('#attaches a.cb-att-name[href*="upload="]'), { what: "attachment link" });
  assert.equal(link.textContent, "投标说明.txt");
  assert.ok(link.getAttribute("href").includes("session=" + sid));
  const res = await fetch(base + link.getAttribute("href"));
  assert.equal(res.status, 200);
  assert.ok((await res.text()).startsWith("第一章 总则"));
  const listed = await fetch(base + "/api/attachments?session_id=" + sid).then((r) => r.json());
  assert.deepEqual(listed.files.map((f) => f.name), ["投标说明.txt"]);
});

test("drafts: a half-typed message survives switching sessions", async () => {
  const here = state().session;
  $("input").value = "写到一半的话";
  $("input").dispatchEvent(new window.Event("input", { bubbles: true }));
  $("btnNewThread").click();
  assert.notEqual(state().session, here);
  assert.equal($("input").value, "");
  await cb().cbProjOpenSession({ session_id: here });
  assert.equal(state().session, here);
  assert.equal($("input").value, "写到一半的话");
});

test("stale: a turn left running by a dead process is shown as 已中断 when the session is opened", async () => {
  const sid = state().session;
  const dir = path.join(OUT_ROOT, sid, "events");
  fs.mkdirSync(dir, { recursive: true });
  fs.writeFileSync(path.join(dir, "latest"), "deadbeef0001");
  fs.writeFileSync(path.join(dir, "deadbeef0001.jsonl"), JSON.stringify({ seq: 1, event: "token", data: { text: "写到一半" } }) + "\n");
  fs.writeFileSync(path.join(dir, "deadbeef0001.state.json"), JSON.stringify({ turn_id: "deadbeef0001", session_id: sid, state: "running", pid: 999999, seq: 1, started_at: "", heartbeat_at: "", finished_at: "" }));
  const detail = await fetch(base + "/api/sessions/" + sid).then((r) => r.json());
  assert.equal(detail.turn_state.state, "stale");
  await cb().cbProjOpenSession({ session_id: sid });
  await until(() => logText().includes("服务重启时被中断"), { what: "stale notice (log tail: " + logText().slice(-200).replace(/\s+/g, " ") + ")" });
});
