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
