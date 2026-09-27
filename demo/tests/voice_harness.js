// Runs demo/static/voice.js in a fake page and drives it like a person would.
// The fake page only hands out the five elements voice.js owns; touching anything
// else (the send button, the form, other events on the textarea) is a violation.
// Usage: node voice_harness.js <path to voice.js>  -> prints JSON results per scenario.
"use strict";
const fs = require("fs");
const vm = require("vm");

const SOURCE = fs.readFileSync(process.argv[2], "utf8");
const ALLOWED_IDS = new Set(["btnVoice", "input", "voiceStatus", "voiceInterim", "voiceLabel"]);

function makePage(opts) {
  const log = { violations: [], events: [], fetches: [], order: [], gum: 0, recorders: 0, trackStops: 0, confirms: 0, recStarts: 0, stopAt: [], recordAt: [] };
  let now = 0;
  let nextId = 1;
  const timers = new Map();
  const setTimeout_ = (fn, ms) => { const id = nextId++; timers.set(id, { at: now + (ms || 0), fn, every: 0 }); return id; };
  const setInterval_ = (fn, ms) => { const id = nextId++; timers.set(id, { at: now + ms, fn, every: ms }); return id; };
  const clear = (id) => { timers.delete(id); };
  const flush = async () => { for (let i = 0; i < 50; i++) await Promise.resolve(); };
  async function advance(ms) {
    const end = now + ms;
    for (;;) {
      await flush(); // let pending promises register their timers before looking for due ones
      let next = null;
      for (const [id, t] of timers) if (t.at <= end && (!next || t.at < next[1].at)) next = [id, t];
      if (!next) break;
      const [id, t] = next;
      now = t.at;
      if (t.every) t.at += t.every; else timers.delete(id);
      t.fn();
      await flush();
    }
    now = end;
    await flush();
  }

  class El {
    constructor(id) {
      this.id = id; this.textContent = ""; this.value = ""; this.attrs = {}; this.dataset = {};
      this.disabled = false; this.title = ""; this.listeners = {};
      const set = new Set();
      this.classList = { toggle: (c, on) => (on ? set.add(c) : set.delete(c)), contains: (c) => set.has(c) };
    }
    setAttribute(k, v) { this.attrs[k] = String(v); }
    getAttribute(k) { return k in this.attrs ? this.attrs[k] : null; }
    removeAttribute(k) { delete this.attrs[k]; }
    addEventListener(type, fn) { (this.listeners[type] = this.listeners[type] || []).push(fn); }
    dispatchEvent(ev) {
      log.events.push(this.id + ":" + ev.type);
      if (this.id !== "input" || ev.type !== "input" || ev.constructor.name !== "Event") {
        log.violations.push("dispatched " + ev.constructor.name + " " + ev.type + " on " + this.id);
      }
      return true;
    }
    focus() { log.focus = this.id; }
    setSelectionRange() {}
    click() {
      if (this.id !== "btnVoice") log.violations.push("click() on " + this.id);
      return Promise.all((this.listeners.click || []).map((fn) => fn({ type: "click" })));
    }
    get form() { log.violations.push("reached the form through " + this.id); return null; }
    closest() { log.violations.push("closest() on " + this.id); return null; }
    requestSubmit() { log.violations.push("requestSubmit on " + this.id); }
    submit() { log.violations.push("submit on " + this.id); }
  }
  const els = {};
  for (const id of ALLOWED_IDS) els[id] = new El(id);
  els.input.value = opts.initialText || "";

  const document = {
    getElementById(id) {
      if (!ALLOWED_IDS.has(id)) { log.violations.push("getElementById(" + id + ")"); return new El(id); }
      return els[id];
    },
    querySelector(sel) { log.violations.push("querySelector(" + sel + ")"); return null; },
    querySelectorAll(sel) { log.violations.push("querySelectorAll(" + sel + ")"); return []; },
    get forms() { log.violations.push("document.forms"); return []; },
  };

  const respond = (status, body) => ({ ok: status >= 200 && status < 300, status, json: async () => body });
  async function fetch_(url, init) {
    log.fetches.push(String(url));
    log.order.push("fetch " + url);
    const reply = await opts.fetch(String(url), init || {}, { now: () => now, wait: (ms) => new Promise((r) => setTimeout_(r, ms)) });
    if (reply === "network-error") throw new TypeError("Failed to fetch");
    return respond(reply[0], reply[1]);
  }

  class FakeRecorder {
    static isTypeSupported(t) { return t === "audio/webm;codecs=opus"; }
    constructor(stream, options) { this.mimeType = (options && options.mimeType) || "audio/webm"; this.state = "inactive"; log.recorders++; FakeRecorder.last = this; }
    start() { this.state = "recording"; log.order.push("record"); log.recordAt.push(now); }
    stop() {
      if (this.state === "inactive") return;
      this.state = "inactive";
      log.stopAt.push(now);
      if (this.ondataavailable) this.ondataavailable({ data: { size: 4096 } });
      if (this.onstop) this.onstop();
    }
  }
  class FakeBlob { constructor(parts, o) { this.size = parts.reduce((a, p) => a + (p.size || 0), 0); this.type = (o && o.type) || ""; } }
  class FakeRecognition {
    constructor() { FakeRecognition.last = this; }
    start() { log.recStarts++; log.order.push("recognition"); }
    stop() { setTimeout_(() => this.onend && this.onend(), 10); }
  }
  const store = new Map();
  const window = {
    fetch: fetch_,
    confirm: (msg) => { log.confirms++; log.order.push("confirm"); log.confirmText = msg; return opts.consent !== false; },
    localStorage: { getItem: (k) => (store.has(k) ? store.get(k) : null), setItem: (k, v) => store.set(k, String(v)) },
    MediaRecorder: opts.noRecorder ? undefined : FakeRecorder,
    SpeechRecognition: opts.speech ? FakeRecognition : undefined,
  };
  const navigator = {
    mediaDevices: {
      getUserMedia: async () => {
        log.gum++;
        log.order.push("getUserMedia");
        await new Promise((r) => setTimeout_(r, opts.micDelay || 0));
        return { getTracks: () => [{ stop: () => { log.trackStops++; } }] };
      },
    },
  };
  const context = {
    window, document, navigator, fetch: fetch_, localStorage: window.localStorage,
    MediaRecorder: window.MediaRecorder, Blob: FakeBlob, Event: class Event { constructor(type) { this.type = type; } },
    AbortController, setTimeout: setTimeout_, setInterval: setInterval_, clearTimeout: clear, clearInterval: clear,
    console, Promise, Date: { now: () => now },
  };
  Object.assign(window, { document, navigator, setTimeout: setTimeout_, clearTimeout: clear });
  vm.createContext(context);
  vm.runInContext(SOURCE, context, { filename: "voice.js" });
  const snap = () => ({
    btn: els.voiceLabel.textContent, label: els.btnVoice.getAttribute("aria-label"), pressed: els.btnVoice.getAttribute("aria-pressed"),
    ariaDisabled: els.btnVoice.getAttribute("aria-disabled"), disabled: els.btnVoice.disabled,
    status: els.voiceStatus.textContent, kind: els.voiceStatus.dataset.kind, interim: els.voiceInterim.textContent,
    input: els.input.value, focus: log.focus,
  });
  const click = () => { els.btnVoice.click(); };
  const tap = async (ms) => { click(); await advance(ms || 1); };
  return { log, els, advance, flush, snap, click, tap, cancel: () => window.CivilBuddyVoice.cancel(), rec: () => FakeRecorder.last, speech: () => FakeRecognition.last, now: () => now };
}

const READY = { available: true, state: "ready", load_error: "" };
const scenarios = {
  async cancelled_server_result_is_ignored() {
    let requestId = "";
    const p = makePage({ fetch: (u, i, t) => {
      if (u.includes("status")) return [200, { ...READY, supports_cancel: true }];
      if (u.endsWith("/cancel")) return [200, { status: "cancelled" }];
      requestId = i.headers["X-Civil-ASR-ID"];
      return t.wait(1000).then(() => [200, { text: "迟到结果", elapsed_seconds: 1 }]);
    } });
    await p.tap(); await p.tap(); await p.tap(); await p.advance(2000);
    return { requestId, after: p.snap(), log: p.log };
  },
  async cancelled_mic_start_releases_late_stream() {
    const p = makePage({ micDelay: 500, fetch: () => [200, READY] });
    p.click(); await p.flush(); p.cancel(); await p.advance(1000);
    return { after: p.snap(), log: p.log };
  },
  async cancelled_browser_result_is_ignored() {
    const p = makePage({ speech: true, fetch: () => [404, {}] });
    await p.tap(); const lateResult = p.speech().onresult, lateEnd = p.speech().onend;
    p.cancel(); lateResult({ resultIndex: 0, results: [Object.assign([{ transcript: "旧会话" }], { isFinal: true })] }); lateEnd();
    await p.advance(100); return { after: p.snap(), log: p.log };
  },
  async server_fill_never_send() {
    const p = makePage({ initialText: "先写的", fetch: (u) => (u.includes("status") ? [200, READY] : [200, { text: "脚手架的连墙件", elapsed_seconds: 1.2 }]) });
    await p.tap();
    const during = p.snap();
    await p.advance(3000); await p.tap();
    return { during, after: p.snap(), log: p.log };
  },
  async double_click_while_mic_starts() {
    const p = makePage({ micDelay: 200, fetch: (u, i, t) => (u.includes("status") ? t.wait(50).then(() => [200, READY]) : [200, { text: "好", elapsed_seconds: 1 }]) });
    p.click(); p.click(); await p.flush(); p.click(); await p.flush();
    await p.advance(500);
    return { after: p.snap(), log: p.log };
  },
  async auto_stop_one_second_early() {
    const p = makePage({ fetch: (u) => (u.includes("status") ? [200, READY] : [200, { text: "好", elapsed_seconds: 1 }]) });
    await p.tap();
    const t0 = p.now();
    await p.advance(25000);
    return { started: t0, after: p.snap(), log: p.log };
  },
  async no_stale_timer_after_self_stop() {
    const p = makePage({ fetch: (u) => (u.includes("status") ? [200, READY] : [200, { text: "一", elapsed_seconds: 1 }]) });
    await p.tap();
    await p.advance(1000);
    p.rec().stop(); await p.flush(); // the mic went away: the recorder stopped on its own
    await p.advance(8000);
    await p.tap();
    const second = p.now();
    await p.advance(18000); // the old 19 s timer would have fired at t=19000 and cut this one short
    return { secondStarted: second, stillRecording: p.snap(), log: p.log };
  },
  async prepares_before_recording() {
    let polls = 0;
    const p = makePage({
      fetch: (u) => {
        if (u.includes("prepare")) return [200, { ok: true, state: "loading" }];
        if (u.includes("status")) { polls++; return [200, { available: true, state: polls === 1 ? "missing" : polls < 4 ? "loading" : "ready" }]; }
        return [200, { text: "好", elapsed_seconds: 1 }];
      },
    });
    await p.tap();
    const preparing = p.snap();
    const gumWhilePreparing = p.log.gum;
    await p.advance(8000);
    return { preparing, gumWhilePreparing, after: p.snap(), log: p.log };
  },
  async cancel_while_preparing() {
    const p = makePage({ fetch: (u) => (u.includes("prepare") ? [200, { state: "loading" }] : u.includes("status") ? [200, { available: true, state: "missing" }] : [200, {}]) });
    await p.tap();
    await p.tap();
    await p.advance(10000);
    return { after: p.snap(), log: p.log };
  },
  async server_error_falls_back_to_browser_after_consent() {
    const p = makePage({ speech: true, fetch: (u) => (u.includes("status") ? [200, READY] : [503, { detail: "本机识别运行失败：RuntimeError" }]) });
    await p.tap(); await p.tap();
    const afterError = p.snap();
    await p.tap();
    p.speech().onresult({ resultIndex: 0, results: [Object.assign([{ transcript: "基坑支护" }], { isFinal: true })] });
    await p.tap(50);
    return { afterError, after: p.snap(), log: p.log };
  },
  async browser_consent_declined_then_given() {
    const declined = makePage({ speech: true, consent: false, fetch: () => [404, { detail: "Not Found" }] });
    await declined.tap();
    const p = makePage({ speech: true, fetch: () => [404, { detail: "Not Found" }] });
    await p.tap();
    p.speech().onresult({ resultIndex: 0, results: [Object.assign([{ transcript: "张拉" }], { isFinal: false })] });
    const listening = p.snap();
    p.speech().onresult({ resultIndex: 0, results: [Object.assign([{ transcript: "预应力张拉" }], { isFinal: true })] });
    await p.tap(50);
    return { declined: { snap: declined.snap(), log: declined.log }, listening, after: p.snap(), log: p.log };
  },
  async browser_ends_without_text() {
    const p = makePage({ speech: true, fetch: () => [404, {}] });
    await p.tap(); await p.tap(50);
    return { after: p.snap(), log: p.log };
  },
  async nothing_available() {
    const p = makePage({ fetch: () => "network-error" });
    await p.tap();
    return { after: p.snap(), log: p.log };
  },
};

const out = {};
process.on("beforeExit", () => {
  if (!out.__done) { out.__incomplete = true; process.stdout.write(JSON.stringify(out)); out.__done = true; }
});
(async () => {
  for (const [name, fn] of Object.entries(scenarios)) {
    try { out[name] = await fn(); } catch (err) { out[name] = { error: String(err && err.stack || err) }; }
  }
  out.__done = true;
  process.stdout.write(JSON.stringify(out));
})();
