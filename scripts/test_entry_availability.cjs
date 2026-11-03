"use strict";
const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { JSDOM } = require("jsdom");
const { applyEntryAvailability, probeEntryCapabilities } = require("../demo/static/modules/entry-availability.js");
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

const capabilityReply = (body, status = 200, type = "application/json") =>
  new Response(JSON.stringify(body), {status, headers: {"content-type": type}});
const packingReady = {unified_packing: {available: true, page: "/packing", agent_mode: "steps"}};
const logisticsReady = {ok: true, formats: ["xlsx", "csv"], packing_modes: ["packaged", "materials"]};
const agentReady = {available: true, modes: ["steps", "model"], sandbox: ["read-only", "workspace-write"], features: {documents: true}};

test("split proxy enables real packing/logistics routes without inventing an absent Rust Agent", async () => {
  const calls = [];
  const capabilities = await probeEntryCapabilities({fetcher: async (url, init) => {
    calls.push([url, init.method, init.body]);
    if (url === "/api/agent/capabilities") return capabilityReply({detail: "Not Found"}, 404);
    return capabilityReply(url === "/packing/api/health" ? packingReady : logisticsReady);
  }});
  assert.deepEqual(capabilities, {agent_workbench: false, packing_workbench: true, logistics: true});
  assert.equal(calls.length, 3);
  assert.ok(calls.every(([, method, body]) => method === "GET" && body === undefined));
  const dom = page(), doc = dom.window.document;
  applyEntryAvailability({doc, capabilities: {packing: false}, t});
  applyEntryAvailability({doc, capabilities, t});
  for (const link of doc.querySelectorAll("[data-cb-entry]"))
    assert.equal(link.hasAttribute("href"), link.dataset.cbCapability !== "agent_workbench");
  dom.window.close();
});

test("only explicit endpoint schemas enable entries; an HTML fallback or generic ok is insufficient", async () => {
  const noSchema = await probeEntryCapabilities({fetcher: async () => capabilityReply({ok: true, available: true})});
  assert.deepEqual(noSchema, {agent_workbench: false, packing_workbench: false, logistics: false});
  const html = await probeEntryCapabilities({fetcher: async () => capabilityReply(agentReady, 200, "text/html")});
  assert.ok(Object.values(html).every((value) => value === false));
  const ready = await probeEntryCapabilities({fetcher: async (url) => capabilityReply(
    url.includes("agent") ? agentReady : url.includes("packing") ? packingReady : logisticsReady)});
  assert.ok(Object.values(ready).every((value) => value === true));
});

test("401, forbidden responses, malformed JSON and network errors never enable links", async () => {
  for (const fetcher of [async () => capabilityReply(agentReady, 401), async () => capabilityReply(agentReady, 403),
    async () => new Response("not JSON", {headers: {"content-type": "application/json"}}),
    async () => { throw new Error("offline fixture"); }]) {
    const caps = await probeEntryCapabilities({fetcher});
    assert.ok(Object.values(caps).every((value) => value === false));
  }
});

test("stalled reads are bounded even when a fake transport ignores abort", async () => {
  let aborted = 0;
  const caps = await probeEntryCapabilities({timeoutMs: 10, fetcher: async (_url, init) => {
    init.signal.addEventListener("abort", () => { aborted += 1; });
    return new Promise(() => {});
  }});
  assert.equal(aborted, 3);
  assert.ok(Object.values(caps).every((value) => value === false));
});

test("an oversized declared metadata body is refused before JSON decoding", async () => {
  let decoded = 0;
  const caps = await probeEntryCapabilities({fetcher: async () => ({status: 200,
    headers: new Headers({"content-type": "application/json", "content-length": "70000"}),
    json: async () => { decoded += 1; return agentReady; }})});
  assert.equal(decoded, 0);
  assert.ok(Object.values(caps).every((value) => value === false));
});
