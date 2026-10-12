"use strict";
const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { JSDOM } = require("jsdom");
const { applyEntryAvailability } = require("../demo/static/modules/entry-availability.js");
const source = fs.readFileSync(path.join(__dirname, "../demo/static/index.html"), "utf8");
const t = (s, values = {}) => s.replace(/\{(\w+)\}/g, (_, k) => values[k] ?? "");
function page() { return new JSDOM(source, {url: "http://localhost/"}); }

test("optional entries are not advertised as working before health arrives", () => {
  const dom = page();
  for (const el of dom.window.document.querySelectorAll("[data-cb-entry]")) {
    assert.equal(el.hasAttribute("href"), false);
    assert.equal(el.getAttribute("aria-disabled"), "true");
  }
  dom.window.close();
});
test("legacy public host cannot send users into missing or forbidden entries", () => {
  const dom = page(), doc = dom.window.document;
  applyEntryAvailability({doc, capabilities: {packing: true, agent_workbench: false, packing_workbench: false, logistics: false}, t});
  for (const el of doc.querySelectorAll("[data-cb-entry]")) assert.equal(el.hasAttribute("href"), false);
  assert.match(doc.getElementById("cbEntryAvailability").textContent, /Agent.*装箱.*物流/);
  dom.window.close();
});
test("explicit route capabilities restore real links and later missing flags close them again", () => {
  const dom = page(), doc = dom.window.document;
  applyEntryAvailability({doc, capabilities: {agent_workbench: true, packing_workbench: true, logistics: true}, t});
  for (const el of doc.querySelectorAll("[data-cb-entry]")) {
    assert.equal(el.getAttribute("href"), el.dataset.cbEntry);
    assert.equal(el.hasAttribute("aria-disabled"), false);
  }
  assert.equal(doc.getElementById("cbEntryAvailability").hidden, true);
  applyEntryAvailability({doc, capabilities: {}, t});
  for (const el of doc.querySelectorAll("[data-cb-entry]")) assert.equal(el.hasAttribute("href"), false);
  dom.window.close();
});
test("a translated unavailability message is literal text, not HTML", () => {
  const dom = page(), doc = dom.window.document;
  applyEntryAvailability({doc, capabilities: {}, t: () => "<img src=x onerror=alert(1)>"});
  const status = doc.getElementById("cbEntryAvailability");
  assert.equal(status.querySelector("img"), null);
  assert.match(status.textContent, /<img/);
  dom.window.close();
});
