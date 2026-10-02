#!/usr/bin/env node
/* Agent page against the HTTP contract, with real DOM events and no model or server. */
"use strict";
const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { JSDOM } = require("jsdom");
const { createAgentWorkbench, artifactUrl } = require("../demo/static/agent.js");
const { renderAgentMarkdown } = require("../demo/static/modules/agent-markdown.js");
const markedSource = fs.readFileSync(path.join(__dirname, "../demo/static/vendor/marked.min.js"), "utf8");
const html = fs.readFileSync(path.join(__dirname, "../demo/static/agent.html"), "utf8");
const caps = { available: true, models: { configured: false }, modes: ["steps", "model"], sandbox: ["read-only", "workspace-write"],
  features: { cancel: true, context: true, subagents: true, documents: true }, sandbox_controls: { policy: true, os_enforced: false } };
const response = (value, status = 200) => new Response(JSON.stringify(value), { status, headers: { "Content-Type": "application/json" } });
const storage = () => { const values = new Map(); return { getItem: (key) => values.get(key) || null, setItem: (key, value) => values.set(key, String(value)), values }; };
function installLanguage(h, locale = "zh-CN") {
  h.win.localStorage.setItem("cb_locale_v1", locale);
  for (const file of ["i18n.js", "i18n-agent.js", "i18n-home.js"]) h.win.eval(fs.readFileSync(path.join(__dirname, "../demo/static", file), "utf8"));
  h.doc.dispatchEvent(new h.win.Event("DOMContentLoaded"));
}
const event = (seq, kind, data = {}) => ({ seq, kind, data });
const settle = () => new Promise((resolve) => setImmediate(resolve));
const deliveryArtifact = (format = "xlsx") => ({ id: "deliverable-1", name: `原始工程副本.${format}`,
  url: "/api/agent/artifacts/deliverable-1?workspace=workspace-1", source: `原始工程资料.${format}`,
  source_sha256: "a".repeat(64), output_sha256: "b".repeat(64), provenance: "model_proposed" });
function readinessResult(artifact, format = "xlsx", sourceStatus = "current") {
  return { artifact_id: artifact.id, source: `C:/engineering/.civil-buddy/out/${artifact.name}`,
    source_sha256: artifact.output_sha256, format, original_source_status: sourceStatus,
    inspected_by: "reviewer-account", inspected_at: 1790720000,
    validation: { structure: "pass", source_hash: "pass", render: "not_checked", engineering_facts: "not_checked" },
    readiness: { schema_version: 1, status: "review_required", automatic_acceptance: false,
      checks: [{ id: "source_hash", status: "pass" }, { id: "structure", status: "pass" },
        { id: "active_content", status: "pass" }, { id: "layout", status: "review_required" },
        { id: "engineering_review", status: "review_required" }],
      format_details: { formula_count: 2, formula_cached_count: 1, formula_missing_cache_count: 1,
        formula_invalid_cache_count: 0, error_cell_count: 0, cached_values_status: "stored_unverified", recalculation: "not_performed" },
      preview: { kind: format === "pdf" ? "native_pdf" : "unavailable", eligible: format === "pdf", rendered: false } } };
}
function deliveryRoute(artifact, route) {
  let turns = 0;
  return (url, init) => {
    const custom = route?.(url, init); if (custom !== undefined) return custom;
    if (url === "/api/agent/turns" && init) return response({ turn_id: `delivery-turn-${++turns}`, session_id: JSON.parse(init.body).session_id });
    if (/\/delivery-turn-\d+\/events\?/.test(url)) return response({ events: [event(1, "artifact", artifact)],
      turn: { status: "completed", result: { reply: "已保留来源文字", artifacts: [artifact] } } });
  };
}
async function showDelivery(h) { await h.ready(); h.$("agentMessage").value = "查看副本"; await h.app.send(); }
const checkButton = (h) => h.doc.querySelector('[data-action="inspect-readiness"]');
const previewLink = (h) => h.doc.querySelector('[data-action="preview-pdf"]');

function sourceReceipt(references = []) {
  return { schema_version: 1, origin: "host", scope: "this_turn_source_quotes", attempted: true,
    checked_at: "2026-09-30T08:00:00Z", model_claims_verified: false, engineering_truth: "not_verified",
    status: "verified", references, truncated: false, collection_failed: false, limit: 12 };
}
function sourceReference(overrides = {}) {
  return { source: "原始资料<em>.txt", quote: '工期为 60 天。<img src=x onerror="bad()">', locator: { line: 3 },
    source_sha256: "a".repeat(64), current_source_sha256: "a".repeat(64),
    status: "valid", reason: "exact_quote_verified", origin: "retrieved", ...overrides };
}
function receiptRoute(receipt, reply = "模型解释：请核对原文。", events = []) {
  return (url, init) => {
    if (url === "/api/agent/turns" && init) return response({ turn_id: "source-turn", session_id: JSON.parse(init.body).session_id });
    if (url.includes("/source-turn/events?")) return response({ events, turn: { status: "completed", result: { reply, ...(receipt === undefined ? {} : { source_evidence: receipt }) } } });
  };
}

function harness(route = () => undefined, saved = storage()) {
  const dom = new JSDOM(html, { url: "http://localhost/static/agent.html", runScripts: "outside-only" });
  dom.window.eval(markedSource);
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

function packingDraftHarness(route = () => undefined) {
  const packingCaps = { ...caps, features: { ...caps.features, packing_replan: true } };
  return harness((url, init) => {
    const custom = route(url, init); if (custom !== undefined) return custom;
    if (url === "/api/agent/capabilities") return response(packingCaps);
    if (url === "/api/agent/workspaces" && init) {
      const root = JSON.parse(init.body).path;
      return response({ workspace: { id: root === "C:/new" ? "workspace-2" : "workspace-1", root }, capabilities: packingCaps });
    }
    if (url.startsWith("/api/agent/files?")) return response({ files: [{ path: "箱单.json" }, { path: "新箱单.json" }] });
  });
}

test("Agent submission: failed packing requests retain the exact draft and source for retry", async (t) => {
  let attempts = 0;
  const h = packingDraftHarness((url, init) => {
    if (url === "/api/agent/turns" && init) return ++attempts === 1
      ? response({ detail: "Temporarily unavailable" }, 503)
      : response({ turn_id: "packing-retry", session_id: JSON.parse(init.body).session_id });
    if (url.includes("/packing-retry/events?")) return response({ events: [], turn: { status: "completed", result: { reply: "Done" } } });
  });
  t.after(h.close); await h.ready(); h.selectFirst();
  h.$("agentPackingSource").value = "箱单.json";
  const draft = "  请重排箱单\n保留原始约束  "; h.$("agentMessage").value = draft;
  await h.app.send();
  assert.equal(h.$("agentPackingSource").value, "箱单.json");
  assert.equal(h.$("agentMessage").value, draft);
  assert.equal(h.$("agentSend").disabled, false);
  assert.match(h.$("agentNotice").textContent, /Temporarily unavailable/);
  await h.app.send();
  const sent = h.calls.filter(call => call.url === "/api/agent/turns" && call.init).map(call => call.body);
  assert.equal(sent.length, 2);
  assert.deepEqual(sent.map(body => body.packing_sources), [[{ source: "箱单.json" }], [{ source: "箱单.json" }]]);
  assert.deepEqual(sent.map(body => body.message), [draft.trim(), draft.trim()]);
  assert.equal(h.$("agentPackingSource").value, ""); assert.equal(h.$("agentMessage").value, "");
});

test("Agent submission: accepting a request preserves a newer unsent message", async (t) => {
  let finish;
  const h = packingDraftHarness((url, init) => {
    if (url === "/api/agent/turns" && init) return new Promise(resolve => { finish = resolve; });
    if (url.includes("/accepted-draft/events?")) return response({ events: [], turn: { status: "completed", result: { reply: "Done" } } });
  });
  t.after(h.close); await h.ready(); h.selectFirst();
  h.$("agentPackingSource").value = "箱单.json"; h.$("agentMessage").value = "已提交任务";
  const sending = h.app.send(); await settle();
  assert.equal(h.$("agentMessage").disabled, false);
  assert.equal(h.$("agentPackingSource").value, "箱单.json");
  const nextDraft = "  下一项任务\n尚未提交  "; h.$("agentMessage").value = nextDraft;
  h.$("agentMessage").dispatchEvent(new h.win.Event("input"));
  finish(response({ turn_id: "accepted-draft", session_id: h.app.state.session })); await sending;
  assert.equal(h.$("agentMessage").value, nextDraft);
  assert.equal(h.$("agentPackingSource").value, "");
  const requests = h.calls.filter(call => call.url === "/api/agent/turns" && call.init);
  assert.equal(requests.length, 1); assert.equal(requests[0].body.message, "已提交任务");
  assert.deepEqual(requests[0].body.packing_sources, [{ source: "箱单.json" }]);
});

test("Agent submission: late responses cannot restore or consume drafts after changing context", async (t) => {
  for (const context of ["session", "workspace"]) for (const accepted of [false, true]) {
    await t.test(`${context}: ${accepted ? "accepted" : "failed"}`, async () => {
      let finish;
      const h = packingDraftHarness((url, init) => {
        if (url === "/api/agent/turns" && init) return new Promise(resolve => { finish = resolve; });
      });
      try {
        await h.ready(); h.selectFirst(); h.$("agentPackingSource").value = "箱单.json"; h.$("agentMessage").value = "旧上下文任务";
        const previousSession = h.app.state.session, sending = h.app.send(); await settle();
        if (context === "session") await h.app.changeSession("", true);
        else await h.app.openWorkspace("C:/new", { manual: true });
        assert.equal(h.$("agentPackingSource").value, ""); assert.equal(h.$("agentMessage").value, "");
        const nextFile = h.doc.querySelectorAll("#agentFiles input")[1]; nextFile.checked = true; nextFile.dispatchEvent(new h.win.Event("change"));
        h.$("agentPackingSource").value = "新箱单.json"; h.$("agentMessage").value = "新上下文草稿";
        finish(accepted ? response({ turn_id: "old-context-turn", session_id: previousSession }) : response({ detail: "Old request failed" }, 503));
        await sending;
        assert.equal(h.$("agentPackingSource").value, "新箱单.json"); assert.equal(h.$("agentMessage").value, "新上下文草稿");
        assert.equal(h.app.state.turn, null); assert.equal(h.app.state.submitting, false);
        assert.doesNotMatch(h.$("agentNotice").textContent, /Old request failed/);
        assert.equal(h.calls.some(call => call.url.includes("/old-context-turn/")), false);
      } finally { h.close(); }
    });
  }
});

test("Agent packing: only explicitly selected JSON is authorized for one turn", async (t) => {
  const h = harness((url, init) => {
    if (url === "/api/agent/capabilities" || url === "/api/agent/workspaces") return response(url.endsWith("workspaces")
      ? { workspace: { id: "workspace-1", root: "C:/engineering" }, capabilities: { ...caps, features: { ...caps.features, packing_replan: true } } }
      : { ...caps, features: { ...caps.features, packing_replan: true } });
    if (url.startsWith("/api/agent/files?")) return response({ files: [{ path: "箱单.json" }, { path: "约束.JSON" }, { path: "资料.xlsx" }] });
    if (url === "/api/agent/turns" && init) return response({ turn_id: "packing-turn", session_id: JSON.parse(init.body).session_id });
    if (url.includes("/packing-turn/events?")) return response({ events: [], turn: { status: "completed", result: { reply: "仅做核对" } } });
  });
  t.after(h.close); installLanguage(h); await h.ready();
  const select = h.$("agentPackingSource");
  assert.equal(select.options.length, 1);
  h.selectFirst(); assert.equal(select.options.length, 2); assert.equal(select.value, "");
  select.value = "箱单.json";
  h.win.CBI18n.setLocale("en"); assert.equal(select.value, "箱单.json");
  assert.equal(select.options[0].textContent, "No packing replan this turn");
  h.$("agentMessage").value = "核对原件的装载搜索结果"; await h.app.send();
  assert.deepEqual(h.calls.find(call => call.url === "/api/agent/turns" && call.init).body.packing_sources, [{ source: "箱单.json" }]);
  assert.equal(select.value, "");
  h.$("agentMessage").value = "只读原文"; await h.app.send();
  assert.deepEqual(h.calls.filter(call => call.url === "/api/agent/turns" && call.init).at(-1).body.packing_sources, []);
  select.value = "箱单.json";
  const check = h.doc.querySelector("#agentFiles input"); check.checked = false; check.dispatchEvent(new h.win.Event("change"));
  assert.equal(select.value, ""); assert.equal(select.options.length, 1);
  check.checked = true; check.dispatchEvent(new h.win.Event("change")); select.value = "箱单.json";
  await h.app.changeSession("", true); assert.equal(select.value, "");
});

test("Agent packing: host results replay in both languages without converting fit into release", async (t) => {
  const summary = { can_fit: true, layout_verified: true, containers_used: 2, n_boxes: 8, plan_sha256: "b".repeat(64), structure: { fail: 1, needs_reinforcement: 0, pending_design: 7, pass: 0 } };
  const calculation = { ok: true, result: { schema: "packing_replan.result.v1", kind: "packing_replan", status: "completed", outcome: "unchanged",
    source: { path: "原箱单<img>.json", sha256: "a".repeat(64) }, baseline: summary, final: summary, rounds: [{ round: 1, candidate_id: "fixed_order", accepted: false, summary }], hard_constraints: { max_rounds: 2 } },
    provenance: { source: "原箱单<img>.json", source_sha256: "a".repeat(64), originals_unchanged: true, shipping_release: false, professional_signoff: false },
    decision_proposal: { phase: "packing_replan_shadow_v1", mode: "off", applied: false } };
  const h = harness(receiptRoute(undefined, "原文回复保留", [event(1, "tool_finished", { name: "packing_replan", result: calculation })]));
  t.after(h.close); installLanguage(h); await showDelivery(h);
  const host = h.$("agentPackingResults");
  assert.equal(host.querySelectorAll("article").length, 1);
  assert.equal(host.querySelector("img"), null);
  assert.match(host.textContent, /未找到更优候选，保留基线/); assert.match(host.textContent, /不代表装运放行/);
  assert.match(host.textContent, /实际复算 1 轮/);
  assert.match(host.textContent, /包装结构仍有未解决项/);
  h.win.CBI18n.setLocale("en");
  assert.match(host.textContent, /No better candidate found/); assert.match(host.textContent, /do not authorize shipment/);
  assert.equal(h.$("agentContextText").textContent, h.win.CBI18n.t("收到后端请求预算后显示；这不是任务完成进度。"));
  assert.doesNotMatch(h.$("agentContextText").textContent, /收到后端/);
  assert.match(host.textContent, /原箱单<img>.json/); assert.equal(host.querySelectorAll("article").length, 1);
  assert.equal(h.$("agentReply").textContent, "原文回复保留");
  await h.app.changeSession("", true); assert.equal(host.hidden, true);
});

test("Agent packing: mismatched source receipts cannot create a calculation card", async (t) => {
  const calculation = { ok: true, result: { schema: "packing_replan.result.v1", kind: "packing_replan", source: { path: "other.json", sha256: "a".repeat(64) } },
    provenance: { source: "selected.json", source_sha256: "a".repeat(64), originals_unchanged: true, shipping_release: false, professional_signoff: false } };
  const h = harness(receiptRoute(undefined, JSON.stringify(calculation), [event(1, "tool_finished", { name: "packing_replan", result: calculation })]));
  t.after(h.close); await showDelivery(h); assert.equal(h.$("agentPackingResults"), null);
});

test("Agent packing: missing inputs are bilingual while cargo names and raw requirements stay original", async (t) => {
  const calculation = { ok: true, result: { schema: "packing_replan.result.v1", kind: "packing_replan", status: "needs_human", outcome: "needs_human",
    source: { path: "箱单.json", sha256: "a".repeat(64) }, needs_human: [{ id: "A1", name: "铝板原名", reason: "unsupported_transport_requirements", requirements: { note: "保持原文：竖放" }, ask: "A fixed worker detail" }] },
    provenance: { source: "箱单.json", source_sha256: "a".repeat(64), originals_unchanged: true, shipping_release: false, professional_signoff: false } };
  const h = harness(receiptRoute(undefined, "待补资料", [event(1, "tool_finished", { name: "packing_replan", result: calculation })]));
  t.after(h.close); installLanguage(h); await showDelivery(h);
  const host = h.$("agentPackingResults"); assert.match(host.querySelector("li").textContent, /自动成箱无法执行/);
  assert.match(host.textContent, /未执行重排/); assert.match(host.querySelector("pre").textContent, /保持原文：竖放/);
  h.win.CBI18n.setLocale("en"); assert.match(host.querySelector("li").textContent, /Automatic boxing cannot enforce/);
  assert.match(host.querySelector("li").textContent, /铝板原名/); assert.match(host.querySelector("pre").textContent, /保持原文：竖放/);
});

test("Agent bilingual UI: persisted English initializes empty states before opening a folder", async () => {
  const h = harness(); installLanguage(h, "en");
  try {
    await h.app.start();
    assert.equal(h.doc.documentElement.lang, "en");
    assert.match(h.$("agentFiles").textContent, /Open a folder/);
    assert.match(h.$("agentTurns").textContent, /No tasks/);
    assert.equal(h.$("agentReply").textContent, "Results will appear here.");
    assert.equal(h.$("agentExpert").options[0].textContent, "Automatic selection");
    assert.equal(h.$("agentRiskConfirmation").placeholder, "我明白，将由持证人员签认");
  } finally { h.close(); }
});

test("Agent sources: host receipts preserve literal source identity across language changes and reset", async (t) => {
  const reference = sourceReference(), receipt = sourceReceipt([reference]);
  const h = harness(receiptRoute(receipt)); t.after(h.close); installLanguage(h);
  await showDelivery(h);
  const host = h.$("agentSourceEvidence");
  assert.equal(host.hidden, false);
  assert.match(host.textContent, /引文与核对时的文件一致/);
  assert.match(host.textContent, /不代表模型所有结论已验证/);
  assert.equal(host.querySelector("blockquote").textContent, reference.quote);
  assert.equal(host.querySelector("strong").textContent, reference.source);
  assert.equal(host.querySelector("img, em"), null);
  assert.match(host.textContent, /"line":3/);
  h.$("agentMessage").value = "保留未发出的中文问题";
  h.win.CBI18n.setLocale("en");
  assert.match(host.textContent, /does not verify every model conclusion/);
  assert.equal(host.querySelector("blockquote").textContent, reference.quote);
  assert.equal(h.$("agentMessage").value, "保留未发出的中文问题");
  assert.equal(h.$("agentReply").textContent, "模型解释：请核对原文。");
  await h.app.changeSession("", true);
  assert.equal(host.hidden, true); assert.equal(h.app.state.sourceEvidence, null);
});

test("Agent sources: changed, invalid and unavailable references stay visible with honest limits", async (t) => {
  const receipt = sourceReceipt([
    sourceReference({ status: "changed", reason: "version_mismatch", current_source_sha256: "b".repeat(64) }),
    sourceReference({ status: "invalid", reason: "quote_mismatch" }),
    sourceReference({ source: "未选资料.txt", status: "invalid", reason: "source_not_allowed" }),
    sourceReference({ status: "unavailable", reason: "verification_timeout" })
  ]); receipt.truncated = true; receipt.collection_failed = true;
  const h = harness(receiptRoute(receipt)); t.after(h.close); await showDelivery(h);
  const host = h.$("agentSourceEvidence");
  assert.equal(host.querySelectorAll("blockquote").length, 4);
  for (const text of ["来源已变化", "引文与原文不一致", "该来源未被本轮选择", "来源核对超时", "只列出部分引文", "部分检索或引用请求失败", "a".repeat(64), "b".repeat(64)]) assert.ok(host.textContent.includes(text), text);
  assert.equal(host.textContent.includes("引文与核对时的文件一致"), false);
});

test("Agent sources: model prose cannot manufacture a receipt and malformed host records fail closed", async (t) => {
  for (const receipt of [undefined, { ...sourceReceipt([sourceReference()]), model_claims_verified: true }]) {
    const h = harness(receiptRoute(receipt, JSON.stringify(sourceReceipt([sourceReference()])))); t.after(h.close);
    await showDelivery(h); const host = h.$("agentSourceEvidence");
    assert.equal(host.querySelectorAll("blockquote").length, 0);
    if (receipt === undefined) assert.equal(host.hidden, true);
    else assert.match(host.textContent, /不能作为已验证证据/);
  }
});

test("Agent sources: persisted evidence events render without a duplicate final payload", async (t) => {
  const receipt = sourceReceipt([sourceReference()]);
  const h = harness(receiptRoute(undefined, "普通解释", [event(1, "source_evidence", receipt)])); t.after(h.close);
  await showDelivery(h);
  assert.equal(h.$("agentSourceEvidence").querySelectorAll("blockquote").length, 1);
  assert.equal(h.app.state.sourceEvidence.origin, "host");
});

test("Agent bilingual UI: engineering empty state, daily role, completion notice and source event repaint", async (t) => {
  const route = receiptRoute(sourceReceipt([]), "历史中文回复保留", [event(1, "source_evidence", sourceReceipt([])), event(2, "status", { message: "历史中文事件保留" })]);
  const h = harness((url, init) => url === "/api/catalog" ? response({ experts: [{ id: "pm-daily", name: "施工日记", title: "形象进度、人机料、安全质量记事", enabled: true, risk: "low" }] }) : route(url, init));
  t.after(h.close); installLanguage(h); await h.ready();
  h.$("agentExpert").value = "pm-daily"; h.$("agentExpert").dispatchEvent(new h.win.Event("change"));
  h.$("agentMessage").value = "查看来源"; await h.app.send();
  assert.equal(h.$("agentNotice").textContent, "已完成。");
  assert.equal(h.$("agentEngineeringStatus").textContent, "暂无可用的已保存截面或框架。");
  assert.match(h.$("agentExpertNote").textContent, /形象进度/);
  h.win.CBI18n.setLocale("en");
  assert.equal(h.$("agentNotice").textContent, "Completed.");
  assert.equal(h.$("agentEngineeringStatus").textContent, "No saved sections or frames are available.");
  assert.match(h.$("agentExpertNote").textContent, /Physical progress, workforce, equipment/);
  assert.match(h.$("agentEvents").textContent, /Source quotation verification/);
  assert.match(h.$("agentEvents").textContent, /历史中文事件保留/);
  assert.equal(h.$("agentReply").textContent, "历史中文回复保留");
  h.win.CBI18n.setLocale("zh-CN");
  assert.equal(h.$("agentNotice").textContent, "已完成。");
  assert.match(h.$("agentEvents").textContent, /来源引文核验/);
});

test("Agent sources: readable locations preserve exact IDs, sheet names and unknown locator fields", async (t) => {
  const locators = [
    { kind: "pdf_page", page: 1, start: 0, end: 35 },
    { kind: "docx_paragraph", paragraph_id: "p:0", start: 0, end: 20 },
    { kind: "xlsx_cell", sheet: "数量表", cell: "A2" },
    { kind: "xlsx_row", sheet: "Counts", row: 2 },
    { kind: "text", line_start: 3, line_end: 7 },
    { kind: "text", line_start: 4, line_end: 4 },
    { kind: "future_source", custom: "原始位置" },
    { kind: "pdf_page", page: 0 }
  ];
  const receipt = sourceReceipt(locators.map(locator => sourceReference({ locator })));
  const h = harness(receiptRoute(receipt)); t.after(h.close); installLanguage(h); await showDelivery(h);
  const host = h.$("agentSourceEvidence");
  for (const text of ["PDF 第 1 页", "Word 段落 p:0", "Excel 数量表!A2", "Excel Counts 第 2 行", "文本第 3–7 行", "文本第 4 行", JSON.stringify(locators[6]), JSON.stringify(locators[7])]) assert.ok(host.textContent.includes(text), text);
  h.win.CBI18n.setLocale("en");
  for (const text of ["PDF page 1", "Word paragraph p:0", "Excel 数量表!A2", "Excel Counts, row 2", "Text lines 3–7", "Text line 4", JSON.stringify(locators[6]), JSON.stringify(locators[7])]) assert.ok(host.textContent.includes(text), text);
  assert.deepEqual(h.app.state.sourceEvidence.references.map(reference => reference.locator), locators);
});

test("Agent bilingual UI: switches preserve drafts and source identities and send reply locale", async () => {
  let payload;
  const h = harness((url, init) => {
    if (url === "/api/agent/turns") {
      payload = JSON.parse(init.body);
      return response({ turn_id: "turn-language", session_id: payload.session_id });
    }
    if (url.includes("/turn-language/events?")) return response({
      turn: { turn_id: "turn-language", status: "completed", result: { reply: "已完成", partial: false } },
      events: [event(1, "context", { used: 100, limit: 1000, reserve: 200, estimated: true }), event(2, "tool_started", { message: "已完成", name: "source_read" })]
    });
  });
  installLanguage(h);
  try {
    await h.ready(); h.selectFirst();
    h.$("agentMessage").value = "检查原文中的已完成，保留中文引用";
    h.$("agentModelKey").value = "unsaved-local-key";
    const selected = [...h.app.state.selected];
    h.win.CBI18n.setLocale("en");
    assert.equal(h.$("agentMessage").value, "检查原文中的已完成，保留中文引用");
    assert.equal(h.$("agentModelKey").value, "unsaved-local-key");
    assert.deepEqual([...h.app.state.selected], selected);
    assert.match(h.$("agentFiles").textContent, /方案.docx/);
    await h.app.send();
    assert.equal(payload.locale, "en"); assert.equal(payload.message, "检查原文中的已完成，保留中文引用");
    assert.equal(h.$("agentReply").textContent, "已完成");
    assert.match(h.$("agentEvents").textContent, /Tool started/);
    assert.match(h.$("agentEvents").textContent, /已完成/);
    h.$("agentMessage").value = "Keep this unsent draft";
    h.win.CBI18n.setLocale("zh-CN");
    assert.equal(h.$("agentMessage").value, "Keep this unsent draft");
    assert.equal(h.$("agentReply").textContent, "已完成");
    assert.match(h.$("agentEvents").textContent, /工具开始/);
    assert.equal(h.$("agentEvents").children.length, 2);
    assert.equal(h.app.state.seq, 2);
  } finally { h.close(); }
});

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

function savedWorkspacePair() {
  const saved=storage();saved.setItem("cb_agent_workspaces_v1",JSON.stringify({lastRoot:"C:/first project",workspaces:{
    "workspace-1":{current:"old-one",sessions:[{id:"old-one",turn:"previous-one"}],files:["原工程资料.docx"]},
    "workspace-2":{current:"old-two",sessions:[{id:"old-two",turn:"previous-two"}],files:["方案.docx"]}
  }}));return saved;
}

test("Project link resolves an authorized ID, preserves other workspace state, and starts with no selected files or task",async(t)=>{
  const saved=savedWorkspacePair();
  const h=harness((url,init)=>{
    if(url==="/api/agent/workspaces")return !init?response({workspaces:[{id:"workspace-1",root:"C:/first project"},{id:"workspace-2",root:"C:/工程 乙"}]}):response({workspace:{id:"workspace-2",root:"C:/工程 乙"},capabilities:caps});
    if(url==="/api/agent/turns"&&init)return response({turn_id:"explicit-linked-turn",session_id:JSON.parse(init.body).session_id});
    if(url.includes("/explicit-linked-turn/events?"))return response({events:[],turn:{status:"completed",result:{reply:"Recorded"}}});
  },saved);t.after(h.close);
  h.win.history.replaceState(null,"","/static/agent.html?workspace=workspace-2&sandbox=workspace-write&files=方案.docx");
  await h.app.start();
  assert.equal(h.app.state.workspace.id,"workspace-2");assert.equal(h.$("agentWorkspacePath").value,"C:/工程 乙");
  assert.deepEqual([...h.app.state.selected],[]);assert.equal(h.$("agentSandbox").value,"read-only");assert.equal(h.$("agentRiskConfirmation").value,"");
  assert.equal(h.calls.filter(c=>c.url==="/api/agent/turns"&&c.init).length,0);assert.equal(h.calls.some(c=>c.url.includes("/previous-two")),false);
  const opens=h.calls.filter(c=>c.url==="/api/agent/workspaces"&&c.init);assert.equal(opens.length,1);assert.equal(opens[0].body.path,"C:/工程 乙");
  const persisted=JSON.parse(saved.getItem("cb_agent_workspaces_v1"));assert.equal(persisted.lastRoot,"C:/工程 乙");assert.deepEqual(persisted.workspaces["workspace-1"].files,["原工程资料.docx"]);
  h.selectFirst();h.$("agentMessage").value="明确点击后核对乙工程资料";await h.app.send();
  const sent=h.calls.find(c=>c.url==="/api/agent/turns"&&c.init).body;assert.equal(sent.workspace,"workspace-2");assert.deepEqual(sent.files,["方案.docx"]);assert.equal(sent.sandbox,"read-only");
});

test("Invalid or inaccessible project links never fall back to a cached project",async()=>{
  for(const query of ["workspace=","workspace=unknown-id","workspace=workspace-2&workspace=workspace-1","workspace=C%3A%2Fother"]){
    const h=harness((url,init)=>url==="/api/agent/workspaces"&&!init?response({workspaces:[{id:"workspace-1",root:"C:/first project"}]}):undefined,savedWorkspacePair());
    try{h.win.history.replaceState(null,"","/static/agent.html?"+query);installLanguage(h,"en");await h.app.start();
      assert.equal(h.app.state.workspace,null,query);assert.equal(h.calls.some(c=>c.url==="/api/agent/workspaces"&&c.init),false,query);
      assert.equal(h.calls.some(c=>c.url==="/api/agent/turns"&&c.init),false);assert.match(h.$("agentNotice").textContent,/link|linked project/i);
      assert.equal(JSON.parse(h.saved.getItem("cb_agent_workspaces_v1")).lastRoot,"C:/first project");
    }finally{h.close();}
  }
});

test("Project-link list failures and mismatched open responses do not open another workspace",async()=>{
  for(const fail of ["list","mismatch"]){
    const h=harness((url,init)=>{
      if(url==="/api/agent/workspaces"&&!init)return fail==="list"?response({detail:"Access denied"},403):response({workspaces:[{id:"workspace-2",root:"C:/second"}]});
      if(url==="/api/agent/workspaces"&&init)return response({workspace:{id:"wrong-workspace",root:"C:/wrong"},capabilities:caps});
    },savedWorkspacePair());
    try{h.win.history.replaceState(null,"","/static/agent.html?workspace=workspace-2");await h.app.start();
      assert.equal(h.app.state.workspace,null);assert.equal(h.app.state.opening,false);assert.match(h.$("agentNotice").className,/error/);
      assert.equal(h.calls.some(c=>c.url.startsWith("/api/agent/files?")),false);assert.equal(h.calls.some(c=>c.url==="/api/agent/turns"&&c.init),false);
    }finally{h.close();}
  }
});

test("A delayed project-link lookup cannot replace a manually opened workspace",async(t)=>{
  let resolve;const waiting=new Promise(r=>{resolve=r;});
  const h=harness((url,init)=>{
    if(url==="/api/agent/workspaces"&&!init)return waiting;
    if(url==="/api/agent/workspaces"&&init)return response({workspace:{id:"manual-project",root:JSON.parse(init.body).path},capabilities:caps});
  },savedWorkspacePair());t.after(h.close);h.win.history.replaceState(null,"","/static/agent.html?workspace=workspace-2");
  const starting=h.app.start();await settle();await h.app.openWorkspace("C:/manual project");resolve(response({workspaces:[{id:"workspace-2",root:"C:/second"}]}));await starting;
  assert.equal(h.app.state.workspace.id,"manual-project");assert.equal(h.calls.filter(c=>c.url==="/api/agent/workspaces"&&c.init).length,1);
});

test("Manual workspace selection removes an old deep link only after success and reload restores the new workspace", async (t) => {
  const saved = storage();
  const route = (url, init) => {
    if (url !== "/api/agent/workspaces") return;
    if (!init) return response({ workspaces: [{ id: "old-project", root: "C:/old" }] });
    const root = JSON.parse(init.body).path;
    return response({ workspace: { id: root === "C:/old" ? "old-project" : "new-project", root }, capabilities: caps });
  };
  const first = harness(route, saved); t.after(first.close);
  first.win.history.replaceState({ page: "preserved" }, "", "/static/agent.html?workspace=old-project&lang=en#answer");
  await first.app.start();
  assert.match(first.win.location.search, /workspace=old-project/);
  assert.equal(first.app.state.selected.size, 0); assert.equal(first.app.state.turn, null);
  first.$("agentWorkspacePath").value = "C:/new";
  first.$("agentWorkspaceForm").dispatchEvent(new first.win.Event("submit", { cancelable: true }));
  await settle(); await settle();
  assert.equal(first.app.state.workspace.id, "new-project");
  assert.equal(first.win.location.search, "?lang=en"); assert.equal(first.win.location.hash, "#answer");
  assert.equal(first.win.history.state.page, "preserved");
  first.selectFirst();
  const second = harness(route, saved); t.after(second.close);
  second.win.history.replaceState(null, "", first.win.location.href);
  await second.app.start();
  assert.equal(second.app.state.workspace.id, "new-project");
  assert.deepEqual([...second.app.state.selected], ["方案.docx"]);
  assert.equal(second.calls.some(call => call.url === "/api/agent/workspaces" && !call.init), false);
  assert.equal(second.calls.some(call => call.url === "/api/agent/turns" && call.init), false);
});

test("A failed or stale manual workspace open preserves the previous deep link", async (t) => {
  for (const outcome of ["failure", "stale"]) {
    await t.test(outcome, async () => {
      let finish;
      const h = harness((url, init) => {
        if (url === "/api/agent/workspaces" && init && JSON.parse(init.body).path === "C:/new") {
          return outcome === "failure" ? response({ detail: "Unavailable folder" }, 403) : new Promise(resolve => { finish = resolve; });
        }
      });
      try {
        await h.ready();
        h.win.history.replaceState(null, "", "/static/agent.html?workspace=workspace-1&lang=en#answer");
        const opening = h.app.openWorkspace("C:/new", { manual: true });
        await settle();
        if (outcome === "stale") {
          await h.app.changeSession("", true);
          finish(response({ workspace: { id: "new-project", root: "C:/new" }, capabilities: caps }));
        }
        await opening;
        assert.equal(h.win.location.search, "?workspace=workspace-1&lang=en");
        assert.equal(h.win.location.hash, "#answer");
        assert.equal(h.app.state.workspace.id, "workspace-1");
      } finally { h.close(); }
    });
  }
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
  const voiceCalls = []; let finish, consumed = 0;
  const nextTurn = () => new Promise((resolve) => setImmediate(resolve));
  async function waitFor(predicate, description) {
    const deadline = Date.now() + 2000;
    while (!predicate()) {
      assert.ok(Date.now() < deadline, description);
      await nextTurn();
    }
  }
  h.win.fetch = async (url, init) => {
    voiceCalls.push({ url, init });
    if (url === "/api/asr/status") {
      await nextTurn(); // A real fetch is not guaranteed to finish in microtasks.
      return response({ available: true, state: "ready", supports_cancel: true });
    }
    if (url.endsWith("/cancel")) return response({ ok: true, status: "cancelled" });
    if (url === "/api/asr") return new Promise((resolve) => {
      finish = (text) => {
        const reply = response({ text, elapsed_seconds: 1 });
        const json = reply.json.bind(reply);
        reply.json = async () => { const data = await json(); consumed += 1; return data; };
        resolve(reply);
      };
    });
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
  async function transcribing() {
    finish = null;
    h.$("btnVoice").click();
    await waitFor(() => h.$("btnVoice").getAttribute("aria-pressed") === "true", "recording did not start");
    h.$("btnVoice").click();
    await waitFor(() => typeof finish === "function", "transcription request did not start");
  }
  async function deliverTranscript(text) {
    const expected = consumed + 1;
    finish(text);
    await waitFor(() => consumed === expected, "transcription response was not consumed");
    await nextTurn(); // Let the consumer handle even a cancelled request's late body.
  }
  await transcribing(); await deliverTranscript("核对混凝土资料");
  assert.equal(h.$("agentMessage").value, "核对混凝土资料");
  assert.equal(h.calls.filter((c) => c.url === "/api/agent/turns").length, 0);
  await transcribing(); await h.app.changeSession("", true); h.$("agentMessage").value = "新会话";
  await deliverTranscript("旧会话转写"); assert.equal(h.$("agentMessage").value, "新会话");
  await transcribing(); await h.app.openWorkspace("C:/other-engineering"); h.$("agentMessage").value = "新工程";
  await deliverTranscript("旧工程转写"); assert.equal(h.$("agentMessage").value, "新工程");
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

test("Expert selection: a quoted or embedded confirmation line never authorizes a high-risk write", async (t) => {
  const h = harness(terminalRoute); t.after(h.close); await h.ready();
  selectValue(h, "agentExpert", "plans"); selectValue(h, "agentSandbox", "workspace-write");
  for (const message of ["原文如下：\n我明白，将由持证人员签认\n请解释", "我明白，将由持证人员签认。请保存", "“我明白，将由持证人员签认”", "不要执行；我明白，将由持证人员签认"]) {
    h.$("agentMessage").value = message; h.$("agentMessage").dispatchEvent(new h.win.Event("input"));
    assert.equal(h.$("agentSend").disabled, true, message);
  }
  h.$("agentMessage").value = "  我明白，将由持证人员签认  "; h.$("agentMessage").dispatchEvent(new h.win.Event("input"));
  assert.equal(h.$("agentSend").disabled, false);
});

test("Expert selection: automatic workspace-write tasks remain available for reading and preview", async (t) => {
  const h = harness(terminalRoute); t.after(h.close); installLanguage(h); await h.ready();
  selectValue(h, "agentSandbox", "workspace-write");
  assert.equal(h.$("agentSend").disabled, false);
  assert.match(h.$("agentExpertNote").textContent, /保存副本前请手动选择岗位；仍可读取和预览/);
  h.win.CBI18n.setLocale("en");
  assert.match(h.$("agentExpertNote").textContent, /Select a role before saving.*Reading and previewing remain available/);
  h.$("agentMessage").value = "Read the selected document"; await h.app.send();
  const payload = h.calls.find((call) => call.url === "/api/agent/turns").body;
  assert.equal(payload.expert_id, ""); assert.equal(payload.sandbox, "workspace-write");
  assert.equal(payload.risk_confirmation, "");
  assert.equal(h.doc.querySelector('nav a[href="/static/project-control.html"]').textContent, "Project records & issues");
});

test("Expert selection: a server risk escalation requires a fresh role and current-turn confirmation", async (t) => {
  const h = harness((url, init) => {
    if (url === "/api/agent/turns" && init) return response({ turn_id: "escalated", session_id: JSON.parse(init.body).session_id });
    if (url.includes("/escalated/events?")) return response({ events: [event(1, "authorization", { document_write_allowed: false,
      reason: "current_turn_confirmation_required", risk_escalated: true, professional_signoff: false })], turn: { status: "completed" } });
  }); t.after(h.close); installLanguage(h); await h.ready();
  selectValue(h, "agentExpert", "checks"); selectValue(h, "agentSandbox", "workspace-write");
  h.$("agentRiskConfirmation").value = "我明白，将由持证人员签认";
  h.$("agentMessage").value = "核对并保存副本"; await h.app.send();
  assert.equal(h.$("agentRiskConfirmation").value, "");
  assert.equal(h.$("agentWriteGateNote").hidden, false);
  assert.match(h.$("agentWriteGateNote").textContent, /高风险岗位.*重新输入签认句.*旧轮确认不会沿用/);
  h.win.CBI18n.setLocale("en"); assert.match(h.$("agentWriteGateNote").textContent, /high-risk role.*confirmation phrase again/);
  selectValue(h, "agentExpert", "plans"); assert.equal(h.$("agentSend").disabled, true);
  await h.app.changeSession("", true); assert.equal(h.$("agentWriteGateNote").hidden, true);
});

test("Deliverable checks: failure is actionable, duplicate clicks do not duplicate requests, and retry succeeds", async (t) => {
  const artifact = deliveryArtifact(); let reads = 0, finish;
  const h = harness(deliveryRoute(artifact, (url) => {
    if (url.includes("/readiness?")) { reads++; return reads === 1 ? new Promise((resolve) => { finish = resolve; }) : response(readinessResult(artifact)); }
  })); t.after(h.close); await showDelivery(h);
  checkButton(h).click(); checkButton(h).click(); await settle();
  assert.equal(reads, 1); assert.equal(checkButton(h).disabled, true);
  finish(response({ detail: "Artifact changed after registration" }, 409)); await settle();
  assert.match(h.$("agentArtifacts").textContent, /交付检查失败：Artifact changed after registration/);
  assert.equal(checkButton(h).textContent, "重试交付检查"); assert.equal(previewLink(h), null);
  checkButton(h).click(); await settle();
  assert.equal(reads, 2); assert.equal(checkButton(h).textContent, "重新检查交付");
  const request = h.calls.find((call) => call.url.includes("/readiness?"));
  assert.equal(request.url, "/api/agent/artifacts/deliverable-1/readiness?workspace=workspace-1");
  assert.equal(request.init.method, "POST"); assert.deepEqual(request.body, {});
});

test("Deliverable checks: source names remain literal while bilingual layout and formula findings repaint", async (t) => {
  const artifact = deliveryArtifact(), report = readinessResult(artifact);
  report.readiness.status = "blocked";
  report.readiness.checks.push({ id: "formula_cache", status: "blocked" }, { id: "recalculation", status: "review_required" });
  const h = harness(deliveryRoute(artifact, (url) => url.includes("/readiness?") ? response(report) : undefined));
  t.after(h.close); installLanguage(h); await showDelivery(h); checkButton(h).click(); await settle();
  assert.match(h.$("agentArtifacts").textContent, /公式 2；有缓存 1；缺失缓存 1/);
  assert.match(h.$("agentArtifacts").textContent, /公式未实际重算/);
  assert.match(h.$("agentArtifacts").textContent, /版式尚未逐页核对/);
  assert.match(h.$("agentArtifacts").textContent, /不代表人工验收/);
  assert.match(h.$("agentArtifacts").textContent, new RegExp(artifact.output_sha256));
  h.win.CBI18n.setLocale("en");
  assert.equal(checkButton(h).textContent, "Recheck deliverable");
  assert.match(h.$("agentArtifacts").textContent, /Formulas: 2; cached: 1; missing caches: 1/);
  assert.match(h.$("agentArtifacts").textContent, /Formulas have not been recalculated/);
  assert.match(h.$("agentArtifacts").textContent, /Layout has not been reviewed page by page/);
  assert.match(h.$("agentArtifacts").textContent, /does not constitute human acceptance/);
  assert.equal(h.$("agentArtifacts").querySelector("a").textContent, artifact.name);
  assert.match(h.$("agentArtifacts").textContent, /原始工程资料.xlsx/);
  h.win.CBI18n.setLocale("zh-CN"); assert.equal(checkButton(h).textContent, "重新检查交付");
  assert.equal(h.calls.filter((call) => call.url.includes("/readiness?")).length, 1);
});

test("Deliverable checks: changed, unavailable and unrecorded original sources never appear current", async (t) => {
  const artifact = deliveryArtifact("docx"); let sourceStatus = "changed";
  const h = harness(deliveryRoute(artifact, (url) => url.includes("/readiness?") ? response(readinessResult(artifact, "docx", sourceStatus)) : undefined));
  t.after(h.close); installLanguage(h); await showDelivery(h); checkButton(h).click(); await settle();
  assert.match(h.$("agentArtifacts").textContent, /原始来源已变化，请从新版资料重新生成副本/);
  assert.doesNotMatch(h.$("agentArtifacts").textContent, /当前文件与生成时一致/);
  h.win.CBI18n.setLocale("en"); assert.match(h.$("agentArtifacts").textContent, /Generate a new copy from the updated source/);
  sourceStatus = "unavailable"; checkButton(h).click(); await settle();
  assert.match(h.$("agentArtifacts").textContent, /current version could not be verified/);
  sourceStatus = "not_recorded"; checkButton(h).click(); await settle();
  assert.match(h.$("agentArtifacts").textContent, /version used at generation was not recorded/);
  sourceStatus = "current"; checkButton(h).click(); await settle();
  assert.match(h.$("agentArtifacts").textContent, /current file matches the version used/);
});

test("Deliverable checks: native PDF link requires a completed eligible hash-bound PDF inspection", async (t) => {
  const artifact = deliveryArtifact("pdf"); let result = readinessResult(artifact, "pdf"), finish;
  const h = harness(deliveryRoute(artifact, (url) => url.includes("/readiness?") ? new Promise((resolve) => { finish = () => resolve(response(result)); }) : undefined));
  t.after(h.close); await showDelivery(h); assert.equal(previewLink(h), null);
  checkButton(h).click(); await settle(); assert.equal(previewLink(h), null);
  finish(); await settle();
  assert.equal(previewLink(h).href, "http://localhost/api/agent/artifacts/deliverable-1/preview?workspace=workspace-1");
  assert.equal(previewLink(h).target, "_blank"); assert.equal(previewLink(h).rel, "noopener noreferrer");
  result = readinessResult(artifact, "pdf"); result.readiness.preview.eligible = false;
  checkButton(h).click(); await settle(); assert.equal(previewLink(h), null, "a recheck removes stale eligibility immediately");
  finish(); await settle(); assert.equal(previewLink(h), null);
  result = readinessResult(artifact, "docx"); result.readiness.preview = { kind: "native_pdf", eligible: true, rendered: false };
  checkButton(h).click(); await settle(); finish(); await settle(); assert.equal(previewLink(h), null, "a DOCX cannot become a PDF preview");
  result = readinessResult(artifact, "pdf"); result.readiness.checks.find((check) => check.id === "active_content").status = "blocked";
  checkButton(h).click(); await settle(); finish(); await settle(); assert.equal(previewLink(h), null, "active-content findings override an inconsistent eligibility flag");
  result = readinessResult(artifact, "pdf"); result.source_sha256 = "c".repeat(64);
  checkButton(h).click(); await settle(); finish(); await settle();
  assert.equal(previewLink(h), null); assert.match(h.$("agentArtifacts").textContent, /检查响应与当前副本不匹配/);
});

test("Deliverable checks: late results are discarded after changing session, workspace or turn", async (t) => {
  for (const change of ["session", "workspace", "turn"]) await t.test(change, async (t) => {
    const artifact = deliveryArtifact("pdf"); let finish;
    const h = harness(deliveryRoute(artifact, (url, init) => {
      if (url.includes("/readiness?")) return new Promise((resolve) => { finish = () => resolve(response(readinessResult(artifact, "pdf"))); });
      if (url === "/api/agent/workspaces" && JSON.parse(init.body).path === "C:/second") return response({ workspace: { id: "workspace-2", root: "C:/second" }, capabilities: caps });
    })); t.after(h.close); await showDelivery(h);
    checkButton(h).click(); await settle();
    if (change === "session") await h.app.changeSession("", true);
    else if (change === "workspace") await h.app.openWorkspace("C:/second");
    else { h.$("agentMessage").value = "下一轮任务"; await h.app.send(); }
    finish(); await settle();
    assert.equal(previewLink(h), null); assert.equal(h.app.state.artifactChecks.size, 0);
    assert.doesNotMatch(h.$("agentArtifacts").textContent, /已检查副本 SHA-256/);
  });
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

const historyCaps = { ...caps, features: { ...caps.features, session_discovery: true } };
const savedSession = (id, turn = `turn-${id}`) => ({ session_id: id, latest_turn_id: turn, status: "completed", turn_count: 1, updated_at: "2026-09-30T10:00:00Z" });
function historyRoute(route = () => undefined) {
  return (url, init) => {
    const custom = route(url, init); if (custom !== undefined) return custom;
    if (url === "/api/agent/capabilities") return response(historyCaps);
    if (url === "/api/agent/workspaces") return response({ workspace: { id: "workspace-1", root: "C:/engineering" }, capabilities: historyCaps });
    if (url.startsWith("/api/agent/sessions?")) return response({ workspace: "workspace-1", sessions: [], next_cursor: null });
  };
}

test("History recovery: fresh browser discovers persisted sessions and reopens outputs without starting a task", async (t) => {
  const artifact = deliveryArtifact(), turn = { turn_id: "persisted-turn", session_id: "server-session", status: "completed", last_seq: 1, result: { reply: "已保存的原文", artifacts: [artifact] } };
  const h = harness(historyRoute((url) => {
    if (url.startsWith("/api/agent/sessions?")) return response({ workspace: "workspace-1", sessions: [savedSession("server-session", turn.turn_id)], next_cursor: null });
    if (url.startsWith("/api/agent/turns?")) { assert.equal(new URL(url, "http://local").searchParams.get("session_id"), "server-session"); return response({ turns: [turn], next_cursor: null }); }
    if (url.includes("/persisted-turn/events?")) return response({ turn, events: [event(1, "artifact", artifact)] });
    if (url.startsWith("/api/agent/turns/persisted-turn?")) return response({ turn });
  })); t.after(h.close); installLanguage(h); await h.ready();
  assert.equal(h.app.state.session, "server-session");
  assert.equal(h.$("agentReply").textContent, "已保存的原文");
  assert.equal(h.doc.querySelectorAll("#agentArtifacts .artifact").length, 1);
  assert.equal(h.calls.filter(call => call.url === "/api/agent/turns" && call.init).length, 0);
  h.win.CBI18n.setLocale("en");
  assert.match(h.$("agentSession").textContent, /Tasks: 1/);
  assert.equal(h.$("agentReply").textContent, "已保存的原文");
});

test("History recovery: session pages merge while refresh preserves unsent local drafts and permissions", async (t) => {
  const h = harness(historyRoute((url) => {
    if (url.startsWith("/api/agent/sessions?")) {
      const cursor = new URL(url, "http://local").searchParams.get("cursor");
      return response({ workspace: "workspace-1", sessions: cursor ? [savedSession("remote-2")] : [savedSession("remote-1")], next_cursor: cursor ? null : "page-two" });
    }
    if (/\/turns\/turn-remote-1/.test(url)) return response({ turn: { turn_id: "turn-remote-1", status: "completed", last_seq: 0, result: {} }, events: [] });
  })); t.after(h.close); await h.ready();
  await h.app.changeSession("", true); const draft = h.app.state.session;
  h.$("agentMessage").value = "不要覆盖这个未发送草稿"; h.$("agentSandbox").value = "read-only"; h.selectFirst();
  await h.app.loadSessions({ more: true }); await h.app.loadSessions();
  assert.equal(h.app.state.session, draft); assert.equal(h.$("agentMessage").value, "不要覆盖这个未发送草稿");
  assert.equal(h.$("agentSandbox").value, "read-only"); assert.equal(h.app.state.selected.size, 1);
  const options = [...h.$("agentSession").options].map(option => option.value);
  assert.ok(options.includes(draft)); assert.ok(options.includes("remote-2")); assert.equal(options.filter(id => id === "remote-1").length, 1);
  assert.equal(h.calls.filter(call => call.url === "/api/agent/turns" && call.init).length, 0);
});

test("History recovery: a stale session page cannot cross a workspace change", async (t) => {
  let delayed = false, release;
  const h = harness(historyRoute((url, init) => {
    if (url === "/api/agent/workspaces" && JSON.parse(init.body).path === "C:/other") return response({ workspace: { id: "workspace-2", root: "C:/other" }, capabilities: historyCaps });
    if (url.startsWith("/api/agent/sessions?")) {
      const workspace = new URL(url, "http://local").searchParams.get("workspace");
      if (workspace === "workspace-1" && delayed) return new Promise(resolve => { release = resolve; });
      return response({ workspace, sessions: [], next_cursor: null });
    }
  })); t.after(h.close); await h.ready(); delayed = true;
  const pending = h.app.loadSessions(); await settle(); await h.app.openWorkspace("C:/other");
  release(response({ workspace: "workspace-1", sessions: [savedSession("old-private")], next_cursor: "old-cursor" })); await pending;
  assert.equal(h.app.state.workspace.id, "workspace-2"); assert.equal(h.app.state.nextSessionCursor, null);
  assert.equal([...h.$("agentSession").options].some(option => option.value === "old-private"), false);
});

test("History recovery: invalid scope and repeated cursors retain drafts and provide retry feedback", async (t) => {
  let mode = "normal";
  const h = harness(historyRoute((url) => {
    if (url.startsWith("/api/agent/sessions?")) return response({ workspace: mode === "wrong" ? "other" : "workspace-1", sessions: [], next_cursor: "same-cursor" });
  })); t.after(h.close); await h.ready();
  const current = h.app.state.session; h.$("agentMessage").value = "保留";
  await h.app.loadSessions({ more: true }); assert.match(h.$("agentSessionHistoryStatus").textContent, /无法核对/);
  mode = "wrong"; await h.app.loadSessions(); assert.match(h.$("agentSessionHistoryStatus").textContent, /无法核对/);
  assert.equal(h.app.state.session, current); assert.equal(h.$("agentMessage").value, "保留");
});

test("History recovery: earlier task pages expose old artifacts and stale pages cannot cross sessions", async (t) => {
  let hold = false, release;
  const artifact = deliveryArtifact();
  const h = harness((url) => {
    if (url.startsWith("/api/agent/turns?")) {
      const query = new URL(url, "http://local").searchParams;
      if (query.get("cursor") && hold) return new Promise(resolve => { release = resolve; });
      const turns = query.get("cursor") ? [{ turn_id: "old-task", session_id: query.get("session_id"), status: "completed" }] : Array.from({ length: 50 }, (_, i) => ({ turn_id: `recent-${i}`, session_id: query.get("session_id"), status: "completed" }));
      return response({ turns, next_cursor: query.get("cursor") ? null : "older-tasks" });
    }
    if (url.includes("/old-task/events?")) return response({ turn: { turn_id: "old-task", status: "completed", last_seq: 0, result: { artifacts: [artifact] } }, events: [] });
    if (url.startsWith("/api/agent/turns/old-task?")) return response({ turn: { turn_id: "old-task", status: "completed", last_seq: 0, result: { artifacts: [artifact] } } });
  }); t.after(h.close); await h.ready(); await h.app.loadTurns({ more: true });
  assert.equal(h.$("agentTurns").querySelectorAll("button").length, 51); await h.app.showTurn("old-task");
  assert.equal(h.app.state.artifacts.size, 1); assert.equal(h.calls.filter(call => call.url === "/api/agent/turns" && call.init).length, 0);
  assert.equal(h.app.state.taskRows.length, 51); assert.equal(h.app.state.nextTurnCursor, null);
  assert.match(h.$("agentTurns").querySelector('[aria-current="true"]').textContent, /old-task/);
  await h.app.loadTurns();
  hold = true; const pending = h.app.loadTurns({ more: true }); await settle(); await h.app.changeSession("", true);
  release(response({ turns: [{ turn_id: "stale-private", status: "completed" }], next_cursor: null })); await pending;
  assert.equal(h.app.state.taskRows.some(row => row.turn_id === "stale-private"), false);
});

test("History recovery: interrupted terminal tasks drain more than 200 events and recover late artifacts", async (t) => {
  const artifact = deliveryArtifact(); let pages = 0;
  const turn = { turn_id: "restart-turn", status: "interrupted", last_seq: 202, result: { reason: "runtime_restart" } };
  const h = harness((url) => {
    if (url.startsWith("/api/agent/turns/restart-turn?")) return response({ turn });
    if (url.includes("/restart-turn/events?")) {
      pages += 1; const after = Number(new URL(url, "http://local").searchParams.get("after_seq"));
      return response({ turn, events: after === 0 ? Array.from({ length: 200 }, (_, i) => event(i + 1, "status", { message: "原始记录" })) : [event(201, "artifact", artifact), event(202, "turn.interrupted", { result: turn.result })] });
    }
  }); t.after(h.close); await h.ready(); await h.app.showTurn("restart-turn");
  assert.equal(h.app.state.seq, 200); assert.equal(h.timers.size, 1); assert.match(h.$("agentNotice").textContent, /恢复剩余/);
  await h.tick(); assert.equal(pages, 2); assert.equal(h.app.state.seq, 202); assert.equal(h.timers.size, 0);
  assert.equal(h.app.state.artifacts.size, 1); assert.match(h.$("agentNotice").textContent, /重启时中断/);
  assert.equal(h.calls.filter(call => call.url === "/api/agent/turns" && call.init).length, 0);
});

test("History recovery: a terminal event cursor that makes no progress stops bounded automatic retries", async (t) => {
  const turn = { turn_id: "stuck-turn", status: "completed", last_seq: 202, result: {} }; let pages = 0;
  const h = harness((url) => {
    if (url.startsWith("/api/agent/turns/stuck-turn?")) return response({ turn });
    if (url.includes("/stuck-turn/events?")) { pages += 1; return response({ turn, events: [] }); }
  }); t.after(h.close); await h.ready(); await h.app.showTurn("stuck-turn");
  await h.tick(); await h.tick(); assert.equal(pages, 3); assert.equal(h.timers.size, 0);
  assert.match(h.$("agentNotice").textContent, /尚未完整恢复/);
});

test("Agent answers: local lexer renders headings, tables, lists and exact code using DOM nodes", (t) => {
  const h = harness(); t.after(h.close);
  const source = '# 核对结果\n\n**原文件.xlsx**、*原文*与~~旧值~~。\n\n| 项目 | 数值 |\n|---|---:|\n| sample_count | 6 |\n\n> 仅核对原文。\n\n3. 读取文件\n4. 核对来源\n\n- [x] 已读取\n- [ ] 待复核\n\n```xml\n<source sha="' + 'a'.repeat(64) + '">\n  sample_count=6\n</source>\n```\n\n`Counts!B2`';
  const host = h.$("agentReply");
  assert.equal(renderAgentMarkdown(host, source, { lexer: h.win.marked.lexer }), true);
  assert.equal(host.querySelector("h3").textContent, "核对结果");
  assert.equal(host.querySelector("strong").textContent, "原文件.xlsx");
  assert.equal(host.querySelector("em").textContent, "原文");
  assert.equal(host.querySelector("del").textContent, "旧值");
  assert.equal(host.querySelectorAll("table").length, 1);
  assert.equal(host.querySelector("tbody td:last-child").textContent, "6");
  assert.equal(host.querySelector(".agent-table-scroll").tabIndex, 0);
  assert.equal(host.querySelector("ol").start, 3);
  assert.match(host.querySelector("ul").textContent, /☑ 已读取.*☐ 待复核/s);
  assert.equal(host.querySelector("pre code").textContent, '<source sha="' + 'a'.repeat(64) + '">\n  sample_count=6\n</source>');
  assert.equal(host.querySelectorAll("source,input").length, 0);
  assert.match(html, /src="\/static\/vendor\/marked.min.js"/);
});

test("Agent answers: HTML stays literal, image URLs never load and dangerous links are inactive", (t) => {
  const h = harness(); t.after(h.close);
  const host = h.$("agentReply");
  const source = '[good](https://example.invalid/reference) [js](javascript:alert%281%29) [data](data:text/html,evil) [file](file:///private) [relative](//evil.invalid) [credentials](https://user:secret@example.invalid)\n\n![箱单图片](https://evil.invalid/track.png)\n\n<script>window.bad=1</script>\n\n<img src="https://evil.invalid/x" onerror="bad()">\n\n<svg onload="bad()"><foreignObject><iframe src="https://evil.invalid"></iframe></foreignObject></svg>\n\n<style>@import "https://evil.invalid/a.css";</style>';
  renderAgentMarkdown(host, source, { lexer: h.win.marked.lexer });
  assert.equal(host.querySelectorAll("script,img,iframe,svg,style,object,embed,form,input,video,audio,link").length, 0);
  assert.equal(host.querySelectorAll("a").length, 1);
  const link = host.querySelector("a");
  assert.equal(link.href, "https://example.invalid/reference");
  assert.equal(link.rel, "noopener noreferrer"); assert.equal(link.referrerPolicy, "no-referrer");
  assert.match(host.textContent, /箱单图片/); assert.match(host.textContent, /<script>window.bad=1<\/script>/);
  assert.equal(h.win.bad, undefined);
  assert.equal(host.querySelector("[src],[srcset],[onerror],[onload],[style]"), null);
});

test("Agent answers: lexer escaping is undone once while source entities, quotes and exact code remain intact", (t) => {
  const h = harness(); t.after(h.close); const host = h.$("agentReply");
  const code = '{"kind":"text","literal":"&amp; &quot; &#34; <tag>"}';
  const prose = '原文 "quote" < 6 & literal &amp; &quot; &#34;';
  const source = prose + '\n\n`' + code + '`\n\n```json\n' + code + '\n```\n\n\\<escaped\\>\n\n[query](https://example.invalid/?a=1&amp;b=2)';
  renderAgentMarkdown(host, source, { lexer: h.win.marked.lexer });
  assert.equal(host.querySelector("p").textContent, prose);
  assert.equal(host.querySelector("p code").textContent, code);
  assert.equal(host.querySelector("pre code").textContent, code);
  assert.match(host.textContent, /<escaped>/);
  assert.equal(host.querySelector("tag,escaped"), null);
  assert.equal(new URL(host.querySelector("a").href).searchParams.get("b"), "2");
  assert.equal(new URL(host.querySelector("a").href).searchParams.has("amp;b"), false);
});

test("Agent answers: unavailable, failing or excessive lexer output preserves complete plain text", (t) => {
  const h = harness(); t.after(h.close); const host = h.$("agentReply"), source = "# 原文\n<script>仍是原文</script>";
  for (const lexer of [undefined, () => { throw new Error("parser unavailable"); },
    () => Array.from({ length: 7000 }, () => ({ type: "text", text: "ignored" })),
    () => { let tokens = [{ type: "text", text: "deep" }]; for (let i = 0; i < 34; i++) tokens = [{ type: "blockquote", tokens }]; return tokens; }]) {
    assert.equal(renderAgentMarkdown(host, source, { lexer }), false);
    assert.equal(host.textContent, source); assert.equal(host.children.length, 0);
  }
  const large = "原".repeat(128001); let called = false;
  assert.equal(renderAgentMarkdown(host, large, { lexer: () => { called = true; return []; } }), false);
  assert.equal(called, false); assert.equal(host.textContent, large);
});

test("Agent subagents: roles, child IDs, outcomes and Markdown survive history reopening and language changes", async (t) => {
  const saved = storage(), reply = '## 核对结果\n\n| 文件 | 数量 |\n|---|---|\n| 原文件.xlsx | 6 |\n\n```text\nsample_count=6\n' + 'a'.repeat(64) + '\n```';
  const evidence = "a1234567-child-evidence", review = "b7654321-child-review";
  const turn = { turn_id: "child-history", task_id: "parent-lease", status: "completed", last_seq: 8, result: { reply } };
  const events = [event(1, "turn.started"),
    event(2, "subtask_started", { task_id: evidence, role: "evidence", goal: "读取原文件.xlsx" }),
    event(3, "model", { task_id: evidence, model: "synthetic-model" }),
    event(4, "tool_finished", { task_id: evidence, name: "search_sources", result: {} }),
    event(5, "subtask_finished", { task_id: evidence, role: "evidence", status: "completed", findings: "### 来源\n\n`sample_count=6`", trust: "assistant_claimed" }),
    event(6, "subtask_started", { task_id: review, role: "review", goal: "复核来源" }),
    event(7, "subtask_finished", { task_id: review, role: "review", status: "failed", error: "<img src=x>来源不可读" }),
    event(8, "turn.completed")].map(row => ({ ...row, task_id: "parent-lease", actor_id: "test-owner" }));
  const route = (url) => {
    if (url.includes("/child-history/events?")) return response({ turn, events });
    if (url.startsWith("/api/agent/turns/child-history?")) return response({ turn });
  };
  const first = harness(route, saved); installLanguage(first); await first.ready(); await first.app.showTurn(turn.turn_id);
  const before = first.$("agentReply").textContent;
  assert.equal(first.$("agentReply").querySelectorAll("table").length, 1);
  for (const index of [1, 2, 3, 4]) {
    assert.match(first.$("agentEvents").children[index].querySelector(".subtask-meta").textContent, /证据检索.*a1234567/);
    assert.doesNotMatch(first.$("agentEvents").children[index].querySelector(".subtask-meta").textContent, /parent-lease|test-owner/);
  }
  assert.match(first.$("agentEvents").children[4].querySelector(".subtask-meta").textContent, /已完成/);
  assert.match(first.$("agentEvents").children[6].querySelector(".subtask-meta").textContent, /独立复核.*b7654321.*未完成/);
  assert.equal(first.doc.querySelector(".subtask-findings code").textContent, "sample_count=6");
  assert.equal(first.doc.querySelector("#agentEvents img"), null);
  first.win.CBI18n.setLocale("en");
  assert.equal(first.$("agentReply").textContent, before); assert.equal(first.app.state.turn.result.reply, reply);
  assert.match(first.$("agentEvents").children[6].querySelector(".subtask-meta").textContent, /Independent review.*b7654321.*Incomplete/);
  assert.match(first.doc.querySelector(".subtask-findings summary").textContent, /model claims/);
  assert.equal(first.$("agentEvents").children.length, 8);
  first.close();
  const second = harness(route, saved); t.after(second.close); installLanguage(second); await second.app.start();
  assert.equal(second.$("agentReply").textContent, before); assert.equal(second.doc.querySelectorAll("#agentReply table").length, 1);
  assert.equal(second.$("agentEvents").children.length, 8); assert.equal(second.app.state.subtasks.size, 2);
  assert.equal(second.calls.filter(call => call.url === "/api/agent/turns" && call.init).length, 0);
  await second.app.changeSession("", true); assert.equal(second.app.state.subtasks.size, 0);
});

test("Agent subagents: switching language during event draining retains the terminal snapshot and child role", async (t) => {
  const turn = { turn_id: "child-drain", status: "interrupted", last_seq: 3, result: { reason: "runtime_restart" } };
  const h = harness((url) => {
    if (url.startsWith("/api/agent/turns/child-drain?")) return response({ turn });
    if (url.includes("/child-drain/events?")) {
      const after = Number(new URL(url, "http://local").searchParams.get("after_seq"));
      return response({ turn, events: after ? [event(3, "subtask_finished", { task_id: "c1234567", status: "cancelled" })]
        : [event(1, "turn.started"), event(2, "subtask_started", { task_id: "c1234567", role: "review" })] });
    }
  }); t.after(h.close); installLanguage(h); await h.ready(); await h.app.showTurn(turn.turn_id);
  h.win.CBI18n.setLocale("en"); assert.equal(h.app.state.turn.status, "interrupted");
  await h.tick();
  assert.match(h.$("agentEvents").lastChild.querySelector(".subtask-meta").textContent, /Independent review.*c1234567.*Stopped/);
  assert.equal(h.timers.size, 0); assert.equal(h.app.state.turn.status, "interrupted");
});


test("History recovery: task pages reject wrong outer workspace or session without changing history", async (t) => {
  let wrong = null;
  const h = harness((url) => {
    if (url.startsWith("/api/agent/turns?")) {
      const query = new URL(url, "http://local").searchParams;
      return response({ workspace: wrong === "workspace" ? "other" : query.get("workspace"),
        session_id: wrong === "session" ? "other" : query.get("session_id"),
        turns: [{ turn_id: wrong ? "foreign-turn" : "own-turn", status: "completed" }], next_cursor: wrong ? "foreign-cursor" : "own-cursor" });
    }
  }); t.after(h.close); await h.ready();
  for (wrong of ["workspace", "session"]) {
    await h.app.loadTurns();
    assert.match(h.$("agentNotice").textContent, /无法核对/);
    assert.deepEqual(h.app.state.taskRows.map(row => row.turn_id), ["own-turn"]);
    assert.equal(h.app.state.nextTurnCursor, "own-cursor");
  }
});
