"use strict";
const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { JSDOM } = require("jsdom");
const source = fs.readFileSync(path.join(__dirname, "../demo/static/i18n.js"), "utf8");
function page(saved) {
  const dom = new JSDOM(`<!doctype html><html lang="zh-CN"><head><title>工作台</title></head><body><header></header><h1>工作台</h1><label>访问令牌 <input value="工作台" placeholder="访问令牌"></label><textarea>工作台</textarea><div data-i18n-skip>工作台</div><div data-i18n-dynamic>工作台</div><pre>工作台</pre><p id="status">刷新</p></body></html>`, { url: "http://localhost/", runScripts: "outside-only" });
  if (saved) dom.window.localStorage.setItem("cb_locale_v1", saved);
  dom.window.eval(source);
  dom.window.document.dispatchEvent(new dom.window.Event("DOMContentLoaded"));
  return dom;
}
test("language switch persists, translates labels, preserves draft/source text, and restores Chinese", () => {
  const dom = page(), win = dom.window, doc = win.document;
  const button = doc.querySelector("[data-cb-language-toggle]");
  button.click();
  assert.equal(doc.documentElement.lang, "en");
  assert.equal(doc.title, "Workbench");
  assert.equal(doc.querySelector("h1").textContent, "Workbench");
  assert.equal(doc.querySelector("input").placeholder, "Access token");
  for (const selector of ["textarea", "[data-i18n-skip]", "[data-i18n-dynamic]", "pre"]) assert.equal(doc.querySelector(selector).textContent, "工作台");
  assert.equal(doc.querySelector("input").value, "工作台");
  assert.equal(win.localStorage.getItem("cb_locale_v1"), "en");
  button.click(); assert.equal(doc.querySelector("h1").textContent, "工作台");
  dom.window.close();
});
test("saved choice applies on another page, interpolates data as literal text and follows cross-tab changes", () => {
  const dom = page("en"), win = dom.window;
  win.CBI18n.add({"找到 {count} 份资料：{file}": "Found {count} files: {file}"});
  assert.equal(win.CBI18n.t("找到 {count} 份资料：{file}", {count: 2, file: "检查表.xlsx"}), "Found 2 files: 检查表.xlsx");
  let changes = 0; win.addEventListener("cb:languagechange", () => changes++);
  win.localStorage.setItem("cb_locale_v1", "zh-CN");
  win.dispatchEvent(new win.StorageEvent("storage", {key: "cb_locale_v1", newValue: "zh-CN"}));
  assert.equal(win.document.querySelector("h1").textContent, "工作台"); assert.equal(changes, 1);
  dom.window.close();
});
test("static translator does not overwrite content subsequently owned by a controller", () => {
  const dom = page(), win = dom.window;
  win.document.getElementById("status").textContent = "原始资料已更新";
  win.CBI18n.setLocale("en");
  assert.equal(win.document.getElementById("status").textContent, "原始资料已更新");
  dom.window.close();
});
test("language choice works when localStorage is blocked", () => {
  const dom = new JSDOM("<body><header></header><h1>工作台</h1></body>", {url: "http://localhost/", runScripts:"outside-only"});
  Object.defineProperty(dom.window, "localStorage", {get() {throw new Error("blocked");}});
  dom.window.eval(source); dom.window.document.dispatchEvent(new dom.window.Event("DOMContentLoaded"));
  dom.window.CBI18n.setLocale("en"); assert.equal(dom.window.document.querySelector("h1").textContent,"Workbench");
  dom.window.close();
});

let homeBundle;
function homeModelSettings(fetcher) {
  // Load the whole controller and its real imports. Suppress only automatic boot;
  // the settings form, event listeners and translations run in the real JSDOM.
  if (!homeBundle) {
    const file = path.join(__dirname, "../demo/static/app.js"), app = fs.readFileSync(file, "utf8");
    assert.equal([...app.matchAll(/^boot\(\);\s*$/mg)].length, 1);
    homeBundle = require("esbuild").buildSync({ stdin: { contents: app.replace(/^boot\(\);\s*$/m, "")
      + "\nwindow.__settingsView = () => cbLlmView;", resolveDir: path.dirname(file), sourcefile: "app.js" },
      bundle: true, write: false, format: "iife", platform: "browser" }).outputFiles[0].text;
  }
  const dom = new JSDOM(fs.readFileSync(path.join(__dirname, "../demo/static/index.html"), "utf8"),
    { url: "http://localhost/static/index.html", runScripts: "outside-only" }), win = dom.window;
  const calls = [];
  win.fetch = async (url, init) => { calls.push({ url, init }); return fetcher(url, init); };
  win.matchMedia = () => ({ matches: false, addEventListener() {}, addListener() {} });
  for (const name of ["i18n.js", "i18n-home.js"]) win.eval(fs.readFileSync(path.join(__dirname, "../demo/static", name), "utf8"));
  win.document.dispatchEvent(new win.Event("DOMContentLoaded")); win.eval(homeBundle);
  return { dom, win, calls, $: id => win.document.getElementById(id) };
}
async function waitSettings(predicate) {
  const deadline = Date.now() + 2500;
  while (!predicate() && Date.now() < deadline) await new Promise(resolve => setTimeout(resolve, 5));
  assert.ok(predicate(), "the real settings form reached its observable boundary");
}
const json = (value, status = 200) => new Response(JSON.stringify(value), { status, headers: { "Content-Type": "application/json" } });

test("home settings: process-only apply and restore retain feedback across actual language events", async (t) => {
  let cfg = { configured: false, model: "startup-model", base_url: "https://startup.invalid/v1", source: "env" };
  const h = homeModelSettings((url, init) => {
    assert.equal(url, "/api/llm-config", "neither configuration action nor translation calls a model");
    if (init?.method === "POST") cfg = JSON.parse(init.body).clear
      ? { configured: false, model: "startup-model", base_url: "https://startup.invalid/v1", source: "env" }
      : { configured: true, model: "temporary-model", base_url: "https://temporary.invalid/v1", source: "runtime", key_masked: "****" };
    return json(cfg);
  }); t.after(() => h.dom.window.close());
  h.$("cbLlmOpen").click(); await waitSettings(() => h.$("cbLlmSave").disabled === false && /尚未配置/.test(h.$("cbLlmStatus").textContent));
  assert.equal(h.$("cbLlmSave").textContent, "应用到本次运行");
  h.$("cbLlmKey").value = "synthetic-local-credential"; h.$("cbLlmBase").value = "https://temporary.invalid/v1"; h.$("cbLlmModel").value = "temporary-model";
  h.$("cbLlmSave").click(); await waitSettings(() => /已应用到本次运行并回读配置/.test(h.$("cbLlmStatus").textContent));
  assert.match(h.$("cbLlmStatus").textContent, /不代表鉴权通过或模型可用/);
  assert.match(h.$("keyBadge").title, /尚不代表鉴权通过或模型可用/);
  h.win.CBI18n.setLocale("en"); assert.match(h.$("cbLlmStatus").textContent, /Applied for this run.*Restarting restores startup settings/);
  assert.match(h.$("cbLlmStatus").textContent, /not that authentication or model availability has been verified/);
  h.$("cbLlmReset").click(); await waitSettings(() => /Startup settings restored/.test(h.$("cbLlmStatus").textContent));
  h.win.CBI18n.setLocale("zh-CN"); assert.match(h.$("cbLlmStatus").textContent, /本次覆盖已撤销，启动文件未修改/);
  const posts = h.calls.filter(call => call.init?.method === "POST");
  assert.equal(posts.length, 2); assert.deepEqual(JSON.parse(posts[1].init.body), { clear: true });
  assert.equal(h.$("cbLlmKey").value, "");
  assert.doesNotMatch(h.win.document.body.textContent + JSON.stringify(h.win.__settingsView()) + JSON.stringify(h.win.localStorage), /synthetic-local-credential/);
});

test("home settings: provider rejections stay explanatory and bilingual without retrying tasks", async (t) => {
  for (const code of ["provider_key_required", "invalid_provider_url"]) {
  const h = homeModelSettings((_url, init) => init?.method === "POST"
    ? json({ ok: false, code, error: code }, 400)
    : json({ configured: true, model: "startup-model", base_url: "https://startup.invalid/v1", source: "env", key_masked: "****" }));
  t.after(() => h.dom.window.close()); h.$("cbLlmOpen").click();
  await waitSettings(() => h.$("cbLlmSave").disabled === false && /startup-model/.test(h.$("cbLlmStatus").textContent));
  h.$("cbLlmBase").value = "https://different.invalid/v1"; h.$("cbLlmSave").click();
  await waitSettings(() => /应用失败/.test(h.$("cbLlmStatus").textContent));
  if (code === "invalid_provider_url") assert.match(h.$("cbLlmStatus").textContent, /HTTPS.*仅供本机测试的回环 HTTP/);
  h.win.CBI18n.setLocale("en"); assert.match(h.$("cbLlmStatus").textContent, /Could not apply settings/);
  assert.match(h.$("cbLlmStatus").textContent, code === "provider_key_required"
    ? /requires a key for that provider.*configuration is unchanged/ : /Use HTTPS, or loopback HTTP for local testing only/);
  h.win.CBI18n.setLocale("zh-CN"); assert.match(h.$("cbLlmStatus").textContent, /应用失败/);
  assert.equal(h.calls.filter(call => call.init?.method === "POST").length, 1);
  assert.ok(h.calls.every(call => call.url === "/api/llm-config"));
  }
});

test("home settings: Rust readback absence or untyped context never enables or submits optional settings", async (t) => {
  for (const extra of [{}, { context: { limit: "65536", reserve: 8192 } }, { context: false }]) {
    const cfg = { configured: true, model: "native-model", base_url: "https://native.invalid/v1", source: "env", key_masked: "****", ...extra };
    const h = homeModelSettings((url) => { assert.equal(url, "/api/llm-config"); return json(cfg); }); t.after(() => h.dom.window.close());
    h.$("cbLlmOpen").click(); await waitSettings(() => !h.$("cbLlmSave").disabled && !h.$("cbLlmOptionalNote").hidden);
    assert.equal(h.$("cbLlmContextSettings").hidden, true); assert.equal(h.$("cbLlmSemanticSettings").hidden, true);
    for (const id of ["cbLlmContext", "cbLlmReserve", "cbLlmSemantic"]) assert.equal(h.$(id).disabled, true);
    // Even stale values left by an older page cannot enter the actual POST.
    h.$("cbLlmContext").value = "131072"; h.$("cbLlmReserve").value = "16384"; h.$("cbLlmSemantic").checked = true;
    h.$("cbLlmSave").click(); await waitSettings(() => !h.$("cbLlmSave").disabled && /已应用到本次运行/.test(h.$("cbLlmStatus").textContent));
    const posts = h.calls.filter(call => call.init?.method === "POST"); assert.equal(posts.length, 1);
    assert.deepEqual(Object.keys(JSON.parse(posts[0].init.body)).sort(), ["api_key", "base_url", "model"]);
    assert.equal(h.win.__settingsView().cfg.supports_context, false); assert.equal(h.win.__settingsView().cfg.supports_semantic_summary, false);
    h.win.CBI18n.setLocale("en"); assert.match(h.$("cbLlmOptionalNote").textContent, /does not provide editable context-budget or semantic-summary/);
    assert.equal(h.$("cbLlmContext").disabled, true);
  }
});

test("home settings: typed supported readback enables options, preserves values and removes support on later absence", async (t) => {
  let cfg = { configured: true, model: "supported-model", base_url: "https://supported.invalid/v1", source: "env", key_masked: "****",
    context: { limit: 65536, reserve: 8192 }, semantic_summary: false };
  const h = homeModelSettings((url, init) => {
    assert.equal(url, "/api/llm-config");
    if (init?.method === "POST") { const body = JSON.parse(init.body); cfg = { ...cfg, context: { limit: body.context_limit, reserve: body.output_reserve }, semantic_summary: body.semantic_summary }; }
    return json(cfg);
  }); t.after(() => h.dom.window.close()); h.$("cbLlmOpen").click();
  await waitSettings(() => !h.$("cbLlmSave").disabled && !h.$("cbLlmContextSettings").hidden);
  assert.equal(h.$("cbLlmSemanticSettings").hidden, false); assert.equal(h.$("cbLlmOptionalNote").hidden, true);
  for (const id of ["cbLlmContext", "cbLlmReserve", "cbLlmSemantic"]) assert.equal(h.$(id).disabled, false);
  assert.equal(h.$("cbLlmContext").value, "65536"); assert.equal(h.$("cbLlmReserve").value, "8192"); assert.equal(h.$("cbLlmSemantic").checked, false);
  h.$("cbLlmContext").value = "131072"; h.$("cbLlmReserve").value = "16384"; h.$("cbLlmSemantic").checked = true;
  h.$("cbLlmSave").click(); await waitSettings(() => !h.$("cbLlmSave").disabled && /已应用到本次运行/.test(h.$("cbLlmStatus").textContent));
  const post = JSON.parse(h.calls.find(call => call.init?.method === "POST").init.body);
  assert.equal(post.context_limit, 131072); assert.equal(post.output_reserve, 16384); assert.equal(post.semantic_summary, true);
  h.win.CBI18n.setLocale("en"); assert.equal(h.$("cbLlmContext").value, "131072"); assert.equal(h.$("cbLlmSemantic").checked, true);
  delete cfg.context; delete cfg.semantic_summary; h.$("cbLlmClose").click(); h.$("cbLlmOpen").click();
  await waitSettings(() => !h.$("cbLlmSave").disabled && h.$("cbLlmContextSettings").hidden);
  assert.equal(h.$("cbLlmSemanticSettings").hidden, true); assert.equal(h.$("cbLlmSemantic").disabled, true);
  assert.equal(h.calls.filter(call => call.init?.method === "POST").length, 1);
});

test("home settings: lost POST response or malformed JSON stays uncertain until a fresh GET without retry", async (t) => {
  for (const failure of ["transport", "parse"]) {
    let cfg = { configured: true, model: "before", base_url: "https://provider.invalid/v1", source: "runtime", key_masked: "****" };
    const fetcher = (url, init) => {
      assert.equal(url, "/api/llm-config");
      if (init?.method === "POST") {
        cfg = { ...cfg, model: "applied-but-unconfirmed" };
        if (failure === "transport") throw new Error("synthetic connection lost after apply");
        return new Response("unreadable JSON", { status: 200, headers: { "Content-Type": "application/json" } });
      }
      return json(cfg);
    };
    const h = homeModelSettings(fetcher); t.after(() => h.dom.window.close()); h.$("cbLlmOpen").click();
    await waitSettings(() => !h.$("cbLlmSave").disabled && /before/.test(h.$("cbLlmStatus").textContent));
    h.$("cbLlmSave").click(); await waitSettings(() => /未能确认设置是否生效/.test(h.$("cbLlmStatus").textContent));
    h.win.CBI18n.setLocale("en"); assert.match(h.$("cbLlmStatus").textContent, /Could not confirm whether settings took effect.*Refresh the page/);
    assert.equal(h.calls.filter(call => call.init?.method === "POST").length, 1);
    const reload = homeModelSettings(fetcher); t.after(() => reload.dom.window.close()); reload.$("cbLlmOpen").click();
    await waitSettings(() => reload.$("cbLlmModel").value === "applied-but-unconfirmed");
    assert.equal(reload.calls.filter(call => call.init?.method === "POST").length, 0, "refresh only reads; it never retries a task or configuration write");
  }
});
