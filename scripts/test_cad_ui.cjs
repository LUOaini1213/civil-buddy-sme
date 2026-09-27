#!/usr/bin/env node
'use strict';

// No browser, model endpoint or network is used. These exercise request ownership,
// download gating, undo, and the actual UI handlers with a deliberately tiny DOM.
const assert = require('node:assert/strict');
const { test } = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../demo/static/cad.js'), 'utf8');
const ui = import('data:text/javascript;base64,' + Buffer.from(source).toString('base64'));
const html = fs.readFileSync(path.join(__dirname, '../demo/static/cad.html'), 'utf8');
const copy = (value) => JSON.parse(JSON.stringify(value));
const drawing = {
  filename: 'test.dxf', sha256: 'fixture', units: { name: 'Millimeters', meters_per_unit: .001 },
  layers: [{ name: 'WALL', suggested_role: 'wall', entity_count: 2 }],
  entities: [
    { id: 'A', layer: 'WALL', type: 'LWPOLYLINE', status: 'ready', points: [[0, 0], [4000, 0], [4000, 3000], [0, 3000]] },
    { id: 'B', layer: 'WALL', type: 'LWPOLYLINE', status: 'ready', points: [[200, 200], [3800, 200], [3800, 2800], [200, 2800]] },
  ],
};
const model = { objects: [{ id: 'solid-A', layer: 'WALL', role: 'wall', source_entity_ids: ['A', 'B'], parameters: { height_m: 3, base_m: 0 }, vertices: [], faces: [], volume_m3: 7.92 }], report: [{ id: 'A', layer: 'WALL', status: 'modeled', reason: 'solid' }, { id: 'B', layer: 'WALL', status: 'modeled', reason: 'hole boundary' }] };
const documentResponse = { ok: true, document_id: 'drawing-one', document: drawing };
const modelResponse = { ok: true, model_id: 'model-one', model };

test('unit selector has valid option tags for every server-supported coordinate unit', () => {
  const select = /<select id="drawingUnit">([\s\S]*?)<\/select>/.exec(html)[1];
  const options = [...select.matchAll(/<option value="([^"]*)">([^<]+)<\/option>/g)];
  assert.deepEqual(options.map((option) => option[1]), ['', 'mm', 'cm', 'm', 'in', 'ft']);
  assert.equal(select.replace(/<option value="[^"]*">[^<]+<\/option>/g, '').trim(), '');
});

test('real drawings require explicit units, solid confirmation and user dimensions', async () => {
  const { defaultConfig, validateConfig } = await ui;
  const config = defaultConfig(drawing);
  assert.equal(config.unit, '');
  assert.equal(config.parameters.wall.height_m, null);
  assert.match(validateConfig(config), /单位/);
  config.unit = 'mm'; assert.match(validateConfig(config), /确认/);
  config.confirmed_solid = true; assert.match(validateConfig(config), /墙体/);
  config.parameters.wall.height_m = 3; assert.equal(validateConfig(config), '');
  config.overrides.A = { height_m: null }; assert.match(validateConfig(config), /实体 A/);
});

test('defaults never turn unknown or incompatible layers into solid geometry', async () => {
  const { defaultConfig } = await ui;
  const source = { layers: [...drawing.layers, { name: '<script>alert(1)</script>', suggested_role: 'unknown' }, { name: '__proto__', suggested_role: 'section' }] };
  const config = defaultConfig(source, 'section', true);
  assert.equal(config.layers.WALL, 'ignore');
  assert.equal(config.layers['<script>alert(1)</script>'], 'ignore');
  assert.equal(config.layers.__proto__, 'section');
  assert.equal(config.parameters.section.height_m, 6);
  assert.equal(config.confirmed_solid, false);
});

test('switching or clearing a file rejects all late import/build replies', async () => {
  const { CadState, defaultConfig } = await ui;
  const state = new CadState(); const oldRequest = state.begin(); state.reset(); const current = state.begin();
  assert.equal(state.setDocument(oldRequest, documentResponse, defaultConfig(drawing)), false);
  assert.equal(state.setModel(oldRequest, modelResponse, {}), false);
  state.finish(oldRequest); assert.equal(state.busy, true);
  assert.equal(state.setDocument(current, documentResponse, defaultConfig(drawing)), true);
  state.finish(current); assert.equal(state.busy, false); assert.equal(state.model, null);
});

test('a failed rebuild retains geometry but edited or pending parameters block export', async () => {
  const { CadState, defaultConfig } = await ui;
  const state = new CadState(); const config = defaultConfig(drawing, 'building', true); config.confirmed_solid = true;
  const token = state.begin(); state.setDocument(token, documentResponse, config); state.setModel(token, modelResponse, config); state.finish(token);
  assert.equal(state.canExport, true);
  state.config.parameters.wall.height_m = 3.6;
  const failed = state.begin(); assert.equal(state.canExport, false); state.finish(failed);
  assert.equal(state.modelId, 'model-one'); assert.equal(state.model.objects[0].parameters.height_m, 3);
  assert.equal(state.dirty, true); assert.equal(state.canExport, false); assert.equal(state.history.length, 0);
});

test('undo history changes only after a successful model response', async () => {
  const { CadState, defaultConfig } = await ui;
  const state = new CadState(); const first = defaultConfig(drawing, 'building', true); first.confirmed_solid = true;
  let token = state.begin(); state.setModel(token, modelResponse, first); state.finish(token);
  const next = copy(first); next.parameters.wall.height_m = 3.6;
  token = state.begin(); state.setModel(token, { ...modelResponse, model_id: 'model-two' }, next); state.finish(token);
  assert.equal(state.history.length, 1); assert.equal(state.history[0].parameters.wall.height_m, 3);
  state.config = copy(state.history[0]); token = state.begin(); state.finish(token);
  assert.equal(state.history.length, 1); assert.equal(state.modelId, 'model-two'); assert.equal(state.canExport, false);
  token = state.begin(); state.setModel(token, modelResponse, first, true); state.finish(token);
  assert.equal(state.history.length, 0); assert.equal(state.canExport, true);
});

test('unit or layer changes cannot masquerade as an unchanged model', async () => {
  const { sameConfig, defaultConfig } = await ui;
  const first = defaultConfig(drawing, 'building', true); const next = copy(first);
  assert.equal(sameConfig(first, next), true); next.unit = 'm'; assert.equal(sameConfig(first, next), false);
  next.unit = 'mm'; next.layers.WALL = 'ignore'; assert.equal(sameConfig(first, next), false);
});

test('preview retains source coordinates, flips only screen Y and does not close invalid paths', async () => {
  const { drawingGeometry } = await ui;
  const output = drawingGeometry([{ ...drawing.entities[0], points: [[100000, 200000], [100020, 200000], [100020, 200010]] }, { id: 'C', status: 'invalid', points: [[0, 0], [1, 1], [NaN, 2]] }]);
  assert.match(output.paths[0].path, /^M100000 -200000 L100020 -200000 L100020 -200010 Z$/);
  assert.equal(output.paths[1].path, 'M0 0 L1 -1');
  assert.equal(output.viewBox.split(' ').map(Number).every(Number.isFinite), true);
});

test('structured geometry failures retain their per-entity report', async () => {
  const { responseError } = await ui;
  const report = [{ id: 'A', status: 'failed', reason: 'intersecting boundaries' }];
  const error = responseError({ detail: { message: '没有可生成的实体', report } }, 422);
  assert.match(error.message, /没有可生成/); assert.deepEqual(error.report, report);
  assert.equal(responseError({ detail: '图纸已过期' }, 410).message, '图纸已过期');
});

class Element {
  constructor(tag = 'div') { this.tagName = tag; this.children = []; this.listeners = {}; this.dataset = {}; this.value = ''; this.checked = false; this.disabled = false; this.hidden = false; this.textContent = ''; this.style = { setProperty() {} }; this.attributes = {}; const classes = new Set(); this.classList = { add: (...names) => names.forEach((name) => classes.add(name)), remove: (...names) => names.forEach((name) => classes.delete(name)), toggle: (name, on) => on ? classes.add(name) : classes.delete(name) }; }
  set value(value) { this._value = String(value); }
  get value() { return this._value; }
  append(...items) { this.children.push(...items); for (const item of items) if (typeof item === 'object') item.parent = this; }
  prepend(...items) { this.children.unshift(...items); }
  replaceChildren(...items) { this.children = items; }
  setAttribute(name, value) { this.attributes[name] = value; }
  getAttribute(name) { return this.attributes[name]; }
  getBoundingClientRect() { return { left: 0, top: 0, width: 100, height: 100 }; }
  addEventListener(name, listener) { (this.listeners[name] ||= []).push(listener); }
  emit(name, data = {}) { for (const listener of this.listeners[name] || []) listener({ target: this, preventDefault() {}, ...data }); }
  remove() { if (this.parent) this.parent.children = this.parent.children.filter((item) => item !== this); }
  click() { this.emit('click'); }
}

function dom() {
  const ids = {}, groups = { example: [], mode: [], export: [] };
  for (const match of html.matchAll(/<([a-z]+)\b([^>]*?)>/g)) {
    const id = /\bid="([^"]+)"/.exec(match[2]);
    const element = new Element(match[1]); if (id) ids[id[1]] = element;
    for (const key of Object.keys(groups)) { const data = new RegExp(`data-${key}="([^"]+)"`).exec(match[2]); if (data) { element.dataset[key] = data[1]; groups[key].push(element); } }
  }
  return { ids, groups, body: new Element('body'), createElement: (tag) => new Element(tag), createElementNS: (_, tag) => new Element(tag), getElementById: (id) => ids[id], querySelectorAll: (selector) => groups[/\[data-(\w+)\]/.exec(selector)?.[1]] || [] };
}

const json = (payload, status = 200) => new Response(JSON.stringify(payload), { status, headers: { 'Content-Type': 'application/json' } });
async function waitFor(predicate) { for (let i = 0; i < 100; i++) { if (predicate()) return; await new Promise((resolve) => setImmediate(resolve)); } assert.fail('UI operation did not settle'); }

async function withApp(fetcher, action, capabilities = () => json({ ok: true, available: true }), options = {}) {
  const previousFetch = global.fetch, previousWindow = global.window;
  const document = dom(); global.window = { addEventListener() {}, devicePixelRatio: 1, location: { search: options.search || '' } };
  global.fetch = (url, request) => url === '/api/cad/capabilities' ? Promise.resolve().then(() => capabilities(request)) : url === '/api/cad/projects' && !request?.method ? Promise.resolve().then(() => options.projectList ? options.projectList() : json({ ok: true, projects: [] })) : url === '/api/cad/analyze' && !options.analyze ? Promise.resolve(json({ ok: true, contours: [], diagnostics: [], selected_ids: [], preview_entities: drawing.entities, buildable: true })) : fetcher(url, request);
  let state;
  try { const { startCadApp } = await ui; state = await startCadApp(document); await action({ document, state }); }
  finally { state?.dispose(); global.fetch = previousFetch; global.window = previousWindow; }
}

test('actual UI requires confirmation, builds sample and suspends download on a parameter edit', async () => {
  let builds = 0;
  await withApp(async (url) => {
    if (url.includes('/examples/')) return new Response('fixture');
    if (url.endsWith('/import')) return json(documentResponse);
    if (url.endsWith('/build')) { builds++; return json(modelResponse); }
    throw Error('unexpected URL ' + url);
  }, async ({ document: doc, state }) => {
    doc.groups.example[0].click(); await waitFor(() => state.document && !state.busy);
    doc.ids.buildModel.click(); await waitFor(() => !state.busy); assert.equal(builds, 0); assert.match(doc.ids.notice.textContent, /确认/);
    doc.ids.solidConfirmed.checked = true; doc.ids.solidConfirmed.emit('change'); doc.ids.buildModel.click(); await waitFor(() => !!state.model && !state.busy);
    assert.equal(builds, 1); assert.equal(doc.ids.reportSummary.textContent, '2 已处理 · 0 忽略 · 0 失败');
    doc.ids.exportConfirmation.value = '我明白，将由持证人员签认'; doc.ids.exportConfirmation.emit('input'); assert.equal(doc.groups.export[0].disabled, false);
    const wallHeight = doc.ids.parameterRows.children[0].children[1]; wallHeight.value = '3.6'; wallHeight.emit('input');
    assert.equal(doc.groups.export[0].disabled, true); assert.equal(state.modelId, 'model-one'); assert.match(doc.ids.modelBadge.textContent, /旧模型/);
  });
});

test('actual UI displays a failed build report and keeps the prior model', async () => {
  let builds = 0;
  await withApp(async (url) => {
    if (url.includes('/examples/')) return new Response('fixture');
    if (url.endsWith('/import')) return json(documentResponse);
    if (url.endsWith('/build')) return ++builds === 1 ? json(modelResponse) : json({ detail: { message: '轮廓相交', report: [{ id: 'A', layer: 'WALL', status: 'failed', reason: 'self intersection' }] } }, 422);
    throw Error('unexpected URL ' + url);
  }, async ({ document: doc, state }) => {
    doc.groups.example[0].click(); await waitFor(() => state.document && !state.busy);
    doc.ids.solidConfirmed.checked = true; doc.ids.solidConfirmed.emit('change'); doc.ids.buildModel.click(); await waitFor(() => state.model && !state.busy);
    const input = doc.ids.parameterRows.children[0].children[1]; input.value = '4'; input.emit('input'); doc.ids.buildModel.click(); await waitFor(() => builds === 2 && !state.busy);
    assert.match(doc.ids.notice.textContent, /轮廓相交/); assert.doesNotMatch(doc.ids.notice.textContent, /object Object/);
    assert.equal(doc.ids.reportSummary.textContent, '0 已处理 · 0 忽略 · 1 失败'); assert.equal(state.modelId, 'model-one'); assert.equal(state.canExport, false);
  });
});

test('actual UI rejects an unknown natural-language instruction without changing parameters', async () => {
  await withApp(async (url) => {
    if (url.includes('/examples/')) return new Response('fixture');
    if (url.endsWith('/import')) return json(documentResponse);
    if (url.endsWith('/command')) return json({ detail: '无法识别完整指令' }, 422);
    throw Error('unexpected URL ' + url);
  }, async ({ document: doc, state }) => {
    doc.groups.example[0].click(); await waitFor(() => state.document && !state.busy);
    const before = copy(state.config); doc.ids.commandInput.value = '删除全部坐标'; doc.ids.commandForm.emit('submit'); await waitFor(() => !state.busy);
    assert.deepEqual(state.config, before); assert.match(doc.ids.notice.textContent, /无法识别完整指令/);
  });
});

test('ordinary file upload keeps dimensions blank until supplied, with no inactive role requirement', async () => {
  let sentConfig = null;
  await withApp(async (url, options) => {
    if (url.endsWith('/import')) return json(documentResponse);
    if (url.endsWith('/build')) { sentConfig = JSON.parse(options.body).config; return json(modelResponse); }
    throw Error('unexpected URL ' + url);
  }, async ({ document: doc, state }) => {
    doc.ids.cadFile.files = [new File(['fixture'], 'user-drawing.dxf')]; doc.ids.cadFile.emit('change');
    await waitFor(() => state.document && !state.busy);
    assert.equal(doc.ids.drawingUnit.value, ''); assert.equal(doc.ids.parameterRows.children[0].children[1].value, '');
    doc.ids.drawingUnit.value = 'mm'; doc.ids.drawingUnit.emit('change');
    doc.ids.solidConfirmed.checked = true; doc.ids.solidConfirmed.emit('change');
    doc.ids.buildModel.click(); await waitFor(() => !state.busy); assert.equal(sentConfig, null); assert.match(doc.ids.notice.textContent, /墙体/);
    const height = doc.ids.parameterRows.children[0].children[1]; height.value = '3'; height.emit('input');
    doc.ids.buildModel.click(); await waitFor(() => state.model && !state.busy);
    assert.equal(sentConfig.parameters.wall.height_m, 3); assert.equal(sentConfig.parameters.section.height_m, null);
    assert.equal(sentConfig.layers.WALL, 'wall'); assert.equal(doc.ids.exampleHint.hidden, true);
  });
});

test('clearing the UI during a pending request discards even a server response that ignores abort', async () => {
  let finishBuild;
  await withApp(async (url) => {
    if (url.includes('/examples/')) return new Response('fixture');
    if (url.endsWith('/import')) return json(documentResponse);
    if (url.endsWith('/build')) return new Promise((resolve) => { finishBuild = resolve; });
    throw Error('unexpected URL ' + url);
  }, async ({ document: doc, state }) => {
    doc.groups.example[0].click(); await waitFor(() => state.document && !state.busy);
    doc.ids.solidConfirmed.checked = true; doc.ids.solidConfirmed.emit('change'); doc.ids.buildModel.click(); await waitFor(() => !!finishBuild);
    doc.ids.clearFile.click(); finishBuild(json(modelResponse)); await new Promise((resolve) => setImmediate(resolve));
    assert.equal(state.document, null); assert.equal(state.model, null); assert.equal(doc.ids.configFields.disabled, true);
    assert.equal(doc.ids.modelBadge.hidden, true); assert.equal(state.canExport, false); assert.match(doc.ids.notice.textContent, /先上传/);
  });
});

test('natural-language parameters rebuild geometry and undo rebuilds the saved configuration', async () => {
  const heights = [];
  await withApp(async (url, options) => {
    if (url.includes('/examples/')) return new Response('fixture');
    if (url.endsWith('/import')) return json(documentResponse);
    if (url.endsWith('/command')) { const next = JSON.parse(options.body).config; next.parameters.wall.height_m = 3.6; return json({ ok: true, config: next, changes: ['墙高改为 3.6 m'] }); }
    if (url.endsWith('/build')) {
      const height = JSON.parse(options.body).config.parameters.wall.height_m; heights.push(height);
      const nextModel = copy(model); nextModel.objects[0].parameters.height_m = height;
      return json({ ...modelResponse, model_id: 'model-' + heights.length, model: nextModel });
    }
    throw Error('unexpected URL ' + url);
  }, async ({ document: doc, state }) => {
    doc.groups.example[0].click(); await waitFor(() => state.document && !state.busy);
    doc.ids.solidConfirmed.checked = true; doc.ids.solidConfirmed.emit('change'); doc.ids.buildModel.click(); await waitFor(() => state.model && !state.busy);
    doc.ids.commandInput.value = '把墙高改成3.6米'; doc.ids.commandForm.emit('submit'); await waitFor(() => heights.length === 2 && !state.busy);
    assert.equal(state.model.objects[0].parameters.height_m, 3.6); assert.equal(doc.ids.undoChange.disabled, false);
    assert.equal(doc.ids.commandDiffRows.children[0].children[1].textContent, '3 m');
    assert.equal(doc.ids.commandDiffRows.children[0].children[3].textContent, '3.6 m');
    assert.match(doc.ids.commandDiffStatus.textContent, /已应用/);
    doc.ids.undoChange.click(); await waitFor(() => heights.length === 3 && !state.busy);
    assert.deepEqual(heights, [3, 3.6, 3]); assert.equal(state.model.objects[0].parameters.height_m, 3); assert.equal(state.history.length, 0);
    assert.equal(doc.ids.commandResult.textContent, '已撤销，恢复上一次建模参数。');
    assert.equal(doc.ids.commandDiff.hidden, true);
  });
});

test('clicking a modeled hole targets its owning solid for subsequent parameter commands', async () => {
  let selected;
  await withApp(async (url, options) => {
    if (url.includes('/examples/')) return new Response('fixture');
    if (url.endsWith('/import')) return json(documentResponse);
    if (url.endsWith('/build')) return json(modelResponse);
    if (url.endsWith('/command')) {
      const body = JSON.parse(options.body); selected = body.selected_id;
      return json({ ok: true, config: body.config, changes: ['构件高度已更新'] });
    }
    throw Error('unexpected URL ' + url);
  }, async ({ document: doc, state }) => {
    doc.groups.example[0].click(); await waitFor(() => state.document && !state.busy);
    doc.ids.solidConfirmed.checked = true; doc.ids.solidConfirmed.emit('change'); doc.ids.buildModel.click(); await waitFor(() => state.model && !state.busy);
    // Second source entity B is the inner boundary of solid A.
    doc.ids.drawingSvg.children[1].click(); assert.equal(state.selectedId, 'A');
    const numericForm = doc.ids.objectInspector.children.find((item) => item.tagName === 'form');
    assert.ok(numericForm, 'the selected owner exposes its numeric parameter form');
    doc.ids.commandInput.value = '把选中构件高度改为4米'; doc.ids.commandForm.emit('submit'); await waitFor(() => selected !== undefined && !state.busy);
    assert.equal(selected, 'A'); assert.equal(Object.hasOwn(state.config.overrides, 'B'), false);
  });
});

test('direct CAD access recovers from HTTP 401 through an explicit inline token form', async () => {
  let authenticated = false;
  await withApp(() => assert.fail('unavailable tools must not send requests'), async ({ document: doc }) => {
    assert.equal(doc.ids.accessPanel.hidden, false); assert.equal(doc.ids.cadFile.disabled, true);
    assert.equal(doc.groups.example[0].disabled, true); assert.match(doc.ids.notice.textContent, /访问口令/);
    doc.groups.example[0].click();
    authenticated = true; doc.ids.accessToken.value = '测试 % secret'; doc.ids.accessForm.emit('submit');
    await waitFor(() => doc.ids.accessPanel.hidden);
    assert.match(doc.cookie, /^cb_token=%E6%B5%8B%E8%AF%95%20%25%20secret;/);
    assert.equal(doc.ids.accessToken.value, ''); assert.equal(doc.ids.cadFile.disabled, false);
    assert.equal(doc.groups.example[0].disabled, false);
  }, () => authenticated ? json({ ok: true, available: true }) : json({ detail: '访问口令缺失' }, 401));
});

test('unavailable dependencies disable imports and a successful recheck restores controls', async () => {
  let installed = false;
  await withApp(() => assert.fail('missing dependencies must not send import requests'), async ({ document: doc }) => {
    assert.equal(doc.ids.cadFile.disabled, true); assert.equal(doc.ids.retryService.hidden, false);
    assert.match(doc.ids.notice.textContent, /ezdxf/); doc.groups.example[0].click();
    installed = true; doc.ids.retryService.click(); await waitFor(() => !doc.ids.cadFile.disabled);
    assert.equal(doc.ids.retryService.hidden, true); assert.match(doc.ids.notice.textContent, /连接已恢复/);
  }, () => json({ ok: true, available: installed, missing_dependencies: installed ? [] : ['ezdxf'] }));
});

test('capability network failures offer retry rather than leaving a permanently disabled page', async () => {
  let online = false;
  await withApp(() => assert.fail('offline tools must not send requests'), async ({ document: doc }) => {
    assert.equal(doc.ids.cadFile.disabled, true); assert.equal(doc.ids.retryService.hidden, false);
    online = true; doc.ids.retryService.click(); await waitFor(() => !doc.ids.cadFile.disabled);
    assert.match(doc.ids.serviceStatus.textContent, /已就绪/);
  }, () => { if (!online) throw new TypeError('Network offline'); return json({ ok: true, available: true }); });
});

test('expired authentication preserves selected geometry and parameter drafts through login', async () => {
  let authenticated = true, builds = 0;
  await withApp(async (url) => {
    if (url.includes('/examples/')) return new Response('fixture');
    if (url.endsWith('/import')) return json(documentResponse);
    if (url.endsWith('/build')) { builds++; return authenticated ? json(modelResponse) : json({ detail: '口令无效' }, 401); }
    throw Error('unexpected URL ' + url);
  }, async ({ document: doc, state }) => {
    doc.groups.example[0].click(); await waitFor(() => state.document && !state.busy);
    doc.ids.solidConfirmed.checked = true; doc.ids.solidConfirmed.emit('change'); doc.ids.buildModel.click(); await waitFor(() => state.model && !state.busy);
    doc.ids.drawingSvg.children[0].click();
    const input = doc.ids.parameterRows.children[0].children[1]; input.value = '4'; input.emit('input');
    authenticated = false; doc.ids.buildModel.click(); await waitFor(() => builds === 2 && !state.busy);
    assert.equal(doc.ids.accessPanel.hidden, false); assert.equal(state.modelId, 'model-one'); assert.equal(state.selectedId, 'A');
    assert.equal(state.config.parameters.wall.height_m, 4); assert.equal(doc.ids.buildModel.disabled, true);
    authenticated = true; doc.ids.accessToken.value = 'renewed'; doc.ids.accessForm.emit('submit'); await waitFor(() => !doc.ids.buildModel.disabled);
    assert.equal(state.modelId, 'model-one'); assert.equal(state.selectedId, 'A'); assert.equal(state.config.parameters.wall.height_m, 4);
    assert.equal(state.dirty, true); assert.equal(builds, 2, 'authentication never repeats a mutation on its own');
  }, () => authenticated ? json({ ok: true, available: true }) : json({ detail: '需要口令' }, 401));
});

test('object editors show draft role parameters and cannot alter state while a build is pending', async () => {
  let builds = 0, finishBuild;
  await withApp(async (url) => {
    if (url.includes('/examples/')) return new Response('fixture');
    if (url.endsWith('/import')) return json(documentResponse);
    if (url.endsWith('/build')) return ++builds === 1 ? json(modelResponse) : new Promise((resolve) => { finishBuild = resolve; });
    throw Error('unexpected URL ' + url);
  }, async ({ document: doc, state }) => {
    doc.groups.example[0].click(); await waitFor(() => state.document && !state.busy);
    doc.ids.solidConfirmed.checked = true; doc.ids.solidConfirmed.emit('change'); doc.ids.buildModel.click(); await waitFor(() => state.model && !state.busy);
    const roleHeight = doc.ids.parameterRows.children[0].children[1]; roleHeight.value = '4'; roleHeight.emit('input');
    doc.ids.drawingSvg.children[0].click();
    let editor = doc.ids.objectInspector.children.find((item) => item.tagName === 'form');
    assert.equal(editor.children[0].children[0].value, '4');
    doc.ids.buildModel.click(); await waitFor(() => !!finishBuild);
    editor = doc.ids.objectInspector.children.find((item) => item.tagName === 'form');
    const height = editor.children[0].children[0]; assert.equal(height.disabled, true);
    height.value = '9'; height.emit('input'); assert.equal(state.config.parameters.wall.height_m, 4); assert.equal(state.config.overrides.A, undefined);
    const next = copy(modelResponse); next.model.objects[0].parameters.height_m = 4; finishBuild(json(next)); await waitFor(() => !state.busy);
    assert.equal(state.model.objects[0].parameters.height_m, 4);
  });
});

test('cleared object parameter stays blank when reselected and undo restores the current model locally', async () => {
  let builds = 0;
  await withApp(async (url) => {
    if (url.includes('/examples/')) return new Response('fixture');
    if (url.endsWith('/import')) return json(documentResponse);
    if (url.endsWith('/build')) { builds++; return json(modelResponse); }
    throw Error('unexpected URL ' + url);
  }, async ({ document: doc, state }) => {
    doc.groups.example[0].click(); await waitFor(() => state.document && !state.busy);
    doc.ids.solidConfirmed.checked = true; doc.ids.solidConfirmed.emit('change'); doc.ids.buildModel.click(); await waitFor(() => state.model && !state.busy);
    doc.ids.drawingSvg.children[0].click();
    let editor = doc.ids.objectInspector.children.find((item) => item.tagName === 'form');
    editor.children[0].children[0].value = ''; editor.children[0].children[0].emit('input');
    doc.ids.drawingSvg.children[0].click(); editor = doc.ids.objectInspector.children.find((item) => item.tagName === 'form');
    assert.equal(editor.children[0].children[0].value, ''); assert.equal(state.canExport, false);
    assert.equal(doc.ids.undoChange.disabled, false); doc.ids.undoChange.click();
    assert.equal(state.dirty, false); assert.equal(state.config.overrides.A, undefined); assert.equal(builds, 1);
    assert.match(doc.ids.commandResult.textContent, /放弃未应用/); assert.equal(state.modelId, 'model-one');
  });
});

test('an export failure unlocks the selected object editor and leaves the model intact', async () => {
  let exports = 0;
  await withApp(async (url) => {
    if (url.includes('/examples/')) return new Response('fixture');
    if (url.endsWith('/import')) return json(documentResponse);
    if (url.endsWith('/build')) return json(modelResponse);
    if (url.endsWith('/export')) { exports++; return json({ detail: '临时导出错误，请重试' }, 500); }
    throw Error('unexpected URL ' + url);
  }, async ({ document: doc, state }) => {
    doc.groups.example[0].click(); await waitFor(() => state.document && !state.busy);
    doc.ids.solidConfirmed.checked = true; doc.ids.solidConfirmed.emit('change'); doc.ids.buildModel.click(); await waitFor(() => state.model && !state.busy);
    doc.ids.drawingSvg.children[0].click();
    doc.ids.exportConfirmation.value = '我明白，将由持证人员签认'; doc.ids.exportConfirmation.emit('input');
    doc.groups.export[0].click(); await waitFor(() => exports === 1 && !state.busy);
    const editor = doc.ids.objectInspector.children.find((item) => item.tagName === 'form');
    assert.equal(editor.children[0].children[0].disabled, false); assert.equal(editor.children[2].disabled, false);
    assert.equal(state.modelId, 'model-one'); assert.match(doc.ids.notice.textContent, /临时导出错误/);
  });
});

const project = { id: 'a'.repeat(32), name: '测试项目', revision: 2, updated_at: '2026-09-21T12:00:00Z', versions: [{ version: 1, created_at: '2026-09-21T12:00:00Z', objects: 1 }] };
async function restoredProject(draftHeight = 3) {
  const { defaultConfig } = await ui;
  const config = defaultConfig(drawing, 'building', true); config.confirmed_solid = true;
  const draft = copy(config); draft.parameters.wall.height_m = draftHeight;
  return { ...documentResponse, ...modelResponse, project: copy(project), draft_config: draft, applied_config: config };
}

test('curve precision defaults to 0.1 mm, rejects missing/out-of-range values and compares visibly', async () => {
  const { defaultConfig, validateConfig, configChanges } = await ui;
  const config = defaultConfig(drawing, 'building', true); config.confirmed_solid = true;
  assert.equal(config.curve_tolerance_mm, .1);
  for (const tolerance of [null, 0, .0009, 10.1, Infinity]) { config.curve_tolerance_mm = tolerance; assert.match(validateConfig(config), /曲线/); }
  config.curve_tolerance_mm = .001; assert.equal(validateConfig(config), '');
  const next = copy(config); next.curve_tolerance_mm = .2;
  assert.deepEqual(configChanges(config, next), [{ label: '曲线误差', before: '0.001 mm', after: '0.2 mm' }]);
});

test('selected-object command diffs use the previous effective layer dimensions', async () => {
  const { defaultConfig, configChanges } = await ui;
  const config = defaultConfig(drawing, 'building', true), next = copy(config);
  next.overrides.A = { height_m: 4 };
  assert.deepEqual(configChanges(config, next, drawing), [{ label: '实体 A 高度 / 长度', before: '3 m', after: '4 m' }]);
});

test('saving a draft never archives an unrequested model, and a clean model version does', async () => {
  const saves = [];
  await withApp(async (url, options) => {
    if (url.includes('/examples/')) return new Response('fixture');
    if (url.endsWith('/import')) return json(documentResponse);
    if (url.endsWith('/build')) return json(modelResponse);
    if (url === '/api/cad/projects') { saves.push(JSON.parse(options.body)); return json({ ok: true, project: { ...project, revision: saves.length, versions: saves.length > 1 ? project.versions : [] } }); }
    throw Error('unexpected URL ' + url);
  }, async ({ document: doc, state }) => {
    doc.groups.example[0].click(); await waitFor(() => state.document && !state.busy);
    doc.ids.solidConfirmed.checked = true; doc.ids.solidConfirmed.emit('change'); doc.ids.buildModel.click(); await waitFor(() => state.model && !state.busy);
    doc.ids.projectName.value = '测试项目'; doc.ids.projectName.emit('input');
    doc.ids.saveDraft.click(); await waitFor(() => saves.length === 1 && !state.busy);
    assert.equal(saves[0].model_id, undefined); assert.equal(saves[0].document_id, 'drawing-one'); assert.equal(state.projectDirty, true);
    assert.match(doc.ids.notice.textContent, /模型尚未归档/); assert.equal(doc.ids.exportProject.disabled, true);
    doc.ids.saveVersion.click(); await waitFor(() => saves.length === 2 && !state.busy);
    assert.equal(saves[1].model_id, 'model-one'); assert.equal(saves[1].expected_revision, 1); assert.equal(saves[1].project_id, project.id);
    assert.equal(state.projectDirty, false); assert.equal(doc.ids.agentProjectLink.href, '/static/index.html?cad_project_id=' + project.id);
    doc.ids.exportConfirmation.value = '我明白，将由持证人员签认'; doc.ids.exportConfirmation.emit('input'); assert.equal(doc.ids.exportProject.disabled, false);
    doc.ids.parameterRows.children[0].children[1].value = '4'; doc.ids.parameterRows.children[0].children[1].emit('input');
    assert.equal(doc.ids.exportProject.disabled, true); assert.equal(doc.ids.agentProjectLink.attributes['aria-disabled'], 'true');
  });
});

test('reopening a saved project restores its draft separately from the last good model and clears signing', async () => {
  const payload = await restoredProject(4);
  await withApp(async (url) => { assert.equal(url, '/api/cad/projects/' + project.id); return json(payload); }, async ({ document: doc, state }) => {
    doc.ids.exportConfirmation.value = '我明白，将由持证人员签认';
    doc.ids.recentProjects.value = project.id; doc.ids.recentProjects.emit('change'); doc.ids.openProject.click();
    await waitFor(() => state.project && !state.busy);
    assert.equal(state.config.parameters.wall.height_m, 4); assert.equal(state.model.objects[0].parameters.height_m, 3);
    assert.equal(state.dirty, true); assert.equal(state.projectDirty, false); assert.equal(state.history.length, 0);
    assert.equal(doc.ids.exportConfirmation.value, ''); assert.equal(doc.ids.exportProject.disabled, true);
    assert.equal(doc.ids.projectVersions.children[1].value, '1'); assert.match(doc.ids.notice.textContent, /草稿与上次生成模型/);
  }, undefined, { projectList: () => json({ ok: true, projects: [project] }) });
});

test('the project URL restores a saved model only after capabilities are ready', async () => {
  const payload = await restoredProject();
  await withApp(async (url) => { assert.equal(url, '/api/cad/projects/' + project.id); return json(payload); }, async ({ state }) => {
    assert.equal(state.project.id, project.id); assert.equal(state.canExport, true); assert.equal(state.projectDirty, false);
  }, undefined, { search: '?project_id=' + project.id });
});

test('loading a historical version is a local unsaved change with the latest revision retained', async () => {
  const payload = await restoredProject(); const requests = [];
  await withApp(async (url) => { requests.push(url); return json(payload); }, async ({ document: doc, state }) => {
    doc.ids.recentProjects.value = project.id; doc.ids.openProject.click(); await waitFor(() => state.project && !state.busy);
    doc.ids.projectVersions.value = '1'; doc.ids.projectVersions.emit('change'); doc.ids.openVersion.click(); await waitFor(() => requests.length === 2 && !state.busy);
    assert.equal(requests[1], '/api/cad/projects/' + project.id + '?version=1'); assert.equal(state.project.revision, 2);
    assert.equal(state.projectDirty, true); assert.equal(doc.ids.exportProject.disabled, true); assert.match(doc.ids.notice.textContent, /保存后才会写入/);
  });
});

test('revision conflicts preserve drafts and allow saving an independent copy without a stale lock', async () => {
  const payload = await restoredProject(); const saves = [];
  await withApp(async (url, options) => {
    if (url === '/api/cad/projects') { const body = JSON.parse(options.body); saves.push(body); return saves.length === 1 ? json({ detail: 'revision conflict' }, 409) : json({ ok: true, project: { ...project, id: 'b'.repeat(32), revision: 1, versions: [] } }); }
    return json(payload);
  }, async ({ document: doc, state }) => {
    doc.ids.recentProjects.value = project.id; doc.ids.openProject.click(); await waitFor(() => state.project && !state.busy);
    const height = doc.ids.parameterRows.children[0].children[1]; height.value = '4'; height.emit('input');
    doc.ids.saveDraft.click(); await waitFor(() => saves.length === 1 && !state.busy);
    assert.equal(state.project.revision, 2); assert.equal(state.config.parameters.wall.height_m, 4); assert.match(doc.ids.notice.textContent, /本次未覆盖/);
    doc.ids.saveCopy.click(); await waitFor(() => saves.length === 2 && !state.busy);
    assert.equal(saves[1].project_id, undefined); assert.equal(saves[1].expected_revision, undefined); assert.equal(state.project.id, 'b'.repeat(32));
  });
});

test('clearing during a project reopen discards a late response even if the server ignores abort', async () => {
  const payload = await restoredProject(); let finish;
  await withApp(async () => new Promise((resolve) => { finish = resolve; }), async ({ document: doc, state }) => {
    doc.ids.recentProjects.value = project.id; doc.ids.openProject.click(); await waitFor(() => !!finish);
    doc.ids.clearFile.click(); finish(json(payload)); await new Promise((resolve) => setImmediate(resolve));
    assert.equal(state.project, null); assert.equal(state.document, null); assert.equal(state.model, null);
  });
});

test('project packages import into a new project with no remembered signing phrase', async () => {
  const payload = await restoredProject(); payload.project.id = 'c'.repeat(32);
  await withApp(async (url, options) => {
    assert.equal(url, '/api/cad/projects/import'); assert.equal(options.body.get('file').name, 'shared.zip'); return json(payload);
  }, async ({ document: doc, state }) => {
    doc.ids.exportConfirmation.value = '我明白，将由持证人员签认'; doc.ids.projectPackage.files = [new File(['fixture'], 'shared.zip')]; doc.ids.projectPackage.emit('change');
    await waitFor(() => state.project && !state.busy);
    assert.equal(state.project.id, 'c'.repeat(32)); assert.equal(doc.ids.exportConfirmation.value, ''); assert.equal(doc.ids.exportProject.disabled, true);
    assert.match(doc.ids.notice.textContent, /新项目/);
  });
});

test('a damaged package cannot clear an already open model or its pending draft', async () => {
  const payload = await restoredProject(4);
  await withApp(async (url) => url.endsWith('/import') ? json({ detail: '项目包损坏' }, 422) : json(payload), async ({ document: doc, state }) => {
    doc.ids.recentProjects.value = project.id; doc.ids.openProject.click(); await waitFor(() => state.project && !state.busy);
    doc.ids.projectPackage.files = [new File(['bad'], 'damaged.zip')]; doc.ids.projectPackage.emit('change'); await waitFor(() => !state.busy);
    assert.equal(state.project.id, project.id); assert.equal(state.modelId, 'model-one'); assert.equal(state.config.parameters.wall.height_m, 4);
    assert.match(doc.ids.notice.textContent, /项目包损坏/);
  });
});

test('failed natural-language rebuild shows the proposed diff as unapplied', async () => {
  let builds = 0;
  await withApp(async (url, options) => {
    if (url.includes('/examples/')) return new Response('fixture');
    if (url.endsWith('/import')) return json(documentResponse);
    if (url.endsWith('/command')) { const cfg = JSON.parse(options.body).config; cfg.parameters.wall.height_m = 4; return json({ ok: true, config: cfg, changes: ['墙高改为4米'] }); }
    if (url.endsWith('/build')) return ++builds === 1 ? json(modelResponse) : json({ detail: '无法计算几何' }, 422);
    throw Error('unexpected URL ' + url);
  }, async ({ document: doc, state }) => {
    doc.groups.example[0].click(); await waitFor(() => state.document && !state.busy);
    doc.ids.solidConfirmed.checked = true; doc.ids.solidConfirmed.emit('change'); doc.ids.buildModel.click(); await waitFor(() => state.model && !state.busy);
    doc.ids.commandInput.value = '墙高改为4米'; doc.ids.commandForm.emit('submit'); await waitFor(() => builds === 2 && !state.busy);
    assert.match(doc.ids.commandDiffStatus.textContent, /未应用/); assert.equal(state.model.objects[0].parameters.height_m, 3);
    assert.equal(state.canExport, false); assert.equal(doc.ids.commandDiffRows.children[0].children[3].textContent, '4 m');
  });
});

test('a successful curve build redraws the source preview with the actual model discretization', async () => {
  const result = copy(modelResponse); result.model.preview_entities = [{ ...drawing.entities[0], points: [[0, 0], [4000, 0], [5000, 1000], [4000, 3000], [0, 3000]] }];
  let config;
  await withApp(async (url, options) => {
    if (url.includes('/examples/')) return new Response('fixture');
    if (url.endsWith('/import')) return json(documentResponse);
    if (url.endsWith('/build')) { config = JSON.parse(options.body).config; return json(result); }
    throw Error('unexpected URL ' + url);
  }, async ({ document: doc, state }) => {
    doc.groups.example[0].click(); await waitFor(() => state.document && !state.busy);
    doc.ids.curveTolerance.value = '.05'; doc.ids.curveTolerance.emit('input'); doc.ids.solidConfirmed.checked = true; doc.ids.solidConfirmed.emit('change');
    doc.ids.buildModel.click(); await waitFor(() => state.model && !state.busy);
    assert.equal(config.curve_tolerance_mm, .05); assert.match(doc.ids.drawingSvg.children[0].attributes.d, /L5000 -1000/);
  });
});

test('project exports carry the shown revision and reject concurrent updates without replacing the view', async () => {
  const payload = await restoredProject(); let exportBody;
  await withApp(async (url, options) => {
    if (url.endsWith('/export')) { exportBody = JSON.parse(options.body); return json({ detail: '项目修订冲突' }, 409); }
    return json(payload);
  }, async ({ document: doc, state }) => {
    doc.ids.recentProjects.value = project.id; doc.ids.openProject.click(); await waitFor(() => state.project && !state.busy);
    doc.ids.exportProject.click(); assert.equal(exportBody, undefined);
    doc.ids.exportConfirmation.value = '我明白，将由持证人员签认'; doc.ids.exportConfirmation.emit('input');
    doc.ids.exportProject.click(); await waitFor(() => exportBody && !state.busy);
    assert.equal(exportBody.expected_revision, 2); assert.equal(exportBody.confirmation, '我明白，将由持证人员签认');
    assert.equal(state.project.revision, 2); assert.equal(state.modelId, 'model-one'); assert.match(doc.ids.notice.textContent, /项目已有更新/);
  });
});

test('saving a historical draft does not claim the historical preview was saved as the latest model', async () => {
  const payload = await restoredProject(); let saves = 0;
  await withApp(async (url) => url === '/api/cad/projects' ? (saves++, json({ ok: true, project: { ...project, revision: 3 } })) : json(payload), async ({ document: doc, state }) => {
    doc.ids.recentProjects.value = project.id; doc.ids.openProject.click(); await waitFor(() => state.project && !state.busy);
    doc.ids.projectVersions.value = '1'; doc.ids.openVersion.click(); await waitFor(() => state.projectDirty && !state.busy);
    doc.ids.saveDraft.click(); await waitFor(() => saves === 1 && !state.busy);
    assert.equal(state.projectDirty, true); assert.equal(doc.ids.exportProject.disabled, true); assert.match(doc.ids.notice.textContent, /尚未归档/);
  });
});

const entityPath = (doc, id) => doc.ids.drawingSvg.children.find((item) => item.attributes['aria-label']?.startsWith(`实体 ${id}，`));
const projectOptions = { search: '?project_id=' + project.id };

test('entity exclusion is passed to geometry and project persistence, while hole picking retains its own selection ID', async () => {
  const payload = await restoredProject(); const requests = [];
  await withApp(async (url, options) => {
    if (url.endsWith('/build')) { requests.push(JSON.parse(options.body)); return json({ detail: '不能遗漏孔洞' }, 422); }
    if (url === '/api/cad/projects') { requests.push(JSON.parse(options.body)); return json({ ok: true, project: { ...project, revision: 3 } }); }
    return json(payload);
  }, async ({ document: doc, state }) => {
    entityPath(doc, 'B').click(); assert.deepEqual(state.pickedIds, ['B']); assert.equal(state.selectedId, 'A');
    doc.ids.excludePicked.click(); assert.deepEqual(state.config.selection.exclude_ids, ['B']); assert.equal(state.canExport, false);
    doc.ids.buildModel.click(); await waitFor(() => requests.length === 1 && !state.busy);
    assert.deepEqual(requests[0].config.selection.exclude_ids, ['B']); assert.equal(state.modelId, 'model-one');
    doc.ids.saveDraft.click(); await waitFor(() => requests.length === 2 && !state.busy);
    assert.deepEqual(requests[1].config.selection.exclude_ids, ['B']);
    doc.ids.undoSelection.click(); assert.deepEqual(state.config.selection.exclude_ids, []); assert.equal(state.dirty, false);
  }, undefined, projectOptions);
});

test('Shift picking, view-only groups and explicit group modeling remain distinct', async () => {
  const payload = await restoredProject();
  await withApp(async () => json(payload), async ({ document: doc, state }) => {
    entityPath(doc, 'A').click(); entityPath(doc, 'B').emit('click', { shiftKey: true }); assert.deepEqual(state.pickedIds, ['A', 'B']);
    entityPath(doc, 'A').emit('click', { shiftKey: true }); assert.deepEqual(state.pickedIds, ['B']);
    doc.ids.selectionGroupName.value = '内圈检查'; doc.ids.saveSelectionGroup.click();
    const before = copy(state.config); doc.ids.showSelectionGroup.click();
    assert.equal(entityPath(doc, 'A'), undefined); assert.ok(entityPath(doc, 'B')); assert.deepEqual(state.config, before);
    doc.ids.useSelectionGroup.click(); assert.deepEqual(state.config.selection.include_ids, ['B']);
    doc.ids.showAllEntities.click(); assert.ok(entityPath(doc, 'A')); assert.deepEqual(state.config.selection.include_ids, ['B']);
    doc.ids.undoSelection.click(); assert.equal(state.config.selection.include_ids, null); assert.equal(state.config.selection.groups[0].name, '内圈检查');
  }, undefined, projectOptions);
});

test('box picking selects complete contours and viewport selection is a draft only after explicit include', async () => {
  const payload = await restoredProject();
  await withApp(async () => json(payload), async ({ document: doc, state }) => {
    doc.ids.drawingInteraction.value = 'box';
    const svg = doc.ids.drawingSvg; svg.setAttribute('viewBox', '0 -3000 4000 4000');
    svg.emit('pointerdown', { clientX: 4, clientY: 4, button: 0 }); svg.emit('pointermove', { clientX: 96, clientY: 71 }); svg.emit('pointerup', { clientX: 96, clientY: 71 });
    assert.deepEqual(state.pickedIds, ['B']); assert.equal(state.config.selection.include_ids, null);
    doc.ids.includePicked.click(); assert.deepEqual(state.config.selection.include_ids, ['B']);
    doc.ids.fitDrawing.click(); doc.ids.pickVisible.click(); assert.deepEqual(state.pickedIds, ['A', 'B']);
    doc.ids.addPicked.click(); assert.deepEqual(state.config.selection.include_ids, ['A', 'B']);
  }, undefined, projectOptions);
});

test('material preview uses even-odd hole geometry, and diagnosis clicking focuses its source points', async () => {
  const payload = await restoredProject();
  const analysis = { ok: true, contours: [{ outer_id: 'A', hole_ids: ['B'], layer: 'WALL', points: drawing.entities[0].points, holes: [drawing.entities[1].points] }], preview_entities: drawing.entities, diagnostics: [{ code: 'open', message: '轮廓有开口', severity: 'error', entity_ids: ['B'], points: [[200, 200], [201, 200]], gap_mm: 1 }], buildable: false };
  await withApp(async (url) => json(url.endsWith('/analyze') ? analysis : payload), async ({ document: doc, state }) => {
    doc.ids.analyzeDrawing.click(); await waitFor(() => !!state.analysis);
    const fill = doc.ids.drawingSvg.children.find((item) => item.attributes.class === 'material-fill');
    assert.equal(fill.attributes['fill-rule'], 'evenodd'); assert.equal((fill.attributes.d.match(/ Z/g) || []).length, 2);
    assert.match(doc.ids.analysisStatus.textContent, /不可建模/);
    doc.ids.diagnosticList.children[0].click(); assert.deepEqual(state.pickedIds, ['B']);
    assert.equal(doc.ids.drawingSvg.children.filter((item) => item.tagName === 'circle').length, 2);
    assert.ok(Number(doc.ids.drawingSvg.attributes.viewBox.split(' ')[2]) < 10);
  }, undefined, { ...projectOptions, analyze: true });
});

test('a stale analysis cannot repaint a newer entity selection even when abort is ignored', async () => {
  const payload = await restoredProject(); let finish;
  await withApp(async (url) => url.endsWith('/analyze') ? new Promise((resolve) => { finish = resolve; }) : json(payload), async ({ document: doc, state }) => {
    doc.ids.analyzeDrawing.click(); await waitFor(() => !!finish);
    entityPath(doc, 'A').click(); doc.ids.excludePicked.click();
    finish(json({ ok: true, contours: [{ outer_id: 'A', layer: 'WALL', points: drawing.entities[0].points, holes: [] }], diagnostics: [], preview_entities: drawing.entities }));
    await new Promise((resolve) => setImmediate(resolve)); assert.equal(state.analysis, null);
    assert.deepEqual(state.config.selection.exclude_ids, ['A']); assert.equal(doc.ids.drawingSvg.children.some((item) => item.attributes.class === 'material-fill'), false);
  }, undefined, { ...projectOptions, analyze: true });
});

test('dimension binding requires an explicit purpose, keeps annotation separate, and manual edits detach provenance', async () => {
  const payload = await restoredProject(); payload.document = copy(drawing);
  payload.document.dimensions = [{ id: 'D1', layer: 'DIMS', kind: 'DIMENSION', text: '3600 mm', annotation_value: 3600, annotation_unit: 'mm', measurement: 3500, measurement_unit: 'drawing', points: [[0, 0], [3600, 0]], bindable: true, reference_only: true }];
  let bindingRequest;
  await withApp(async (url, options) => {
    if (url.endsWith('/bind-dimension')) { bindingRequest = JSON.parse(options.body); const config = copy(bindingRequest.config); config.parameters.wall.height_m = 3.6; config.dimension_bindings = [bindingRequest.binding]; return json({ ok: true, config }); }
    return json(payload);
  }, async ({ document: doc, state }) => {
    assert.equal(state.config.parameters.wall.height_m, 3);
    doc.ids.dimensionList.children[0].click(); assert.match(doc.ids.dimensionDetails.textContent, /3600/); assert.match(doc.ids.dimensionDetails.textContent, /3500/);
    doc.ids.bindDimension.click(); assert.equal(bindingRequest, undefined); assert.match(doc.ids.notice.textContent, /明确选择/);
    doc.ids.dimensionTarget.value = 'role:wall'; doc.ids.dimensionValueSource.value = 'annotation'; doc.ids.dimensionParameter.value = 'height_m'; doc.ids.bindDimension.click();
    await waitFor(() => !!bindingRequest && !state.busy);
    assert.deepEqual(bindingRequest.binding, { dimension_id: 'D1', role: 'wall', parameter: 'height_m', value_source: 'annotation' });
    assert.equal(state.config.parameters.wall.height_m, 3.6); assert.equal(state.model.objects[0].parameters.height_m, 3); assert.equal(state.config.dimension_bindings.length, 1);
    const input = doc.ids.parameterRows.children[0].children[1]; input.value = '4'; input.emit('input'); assert.deepEqual(state.config.dimension_bindings, []);
  }, undefined, projectOptions);
});

test('unit changes detach all dimension bindings and entity parameter changes detach only their binding', async () => {
  const { detachDimensionBindings, defaultConfig } = await ui;
  const config = defaultConfig(drawing, 'building', true); config.overrides.A = { height_m: 3 };
  config.dimension_bindings = [{ dimension_id: 'D1', role: 'wall', parameter: 'height_m', value_source: 'annotation' }, { dimension_id: 'D2', target_id: 'A', parameter: 'height_m', value_source: 'measurement' }];
  const change = copy(config); change.overrides.A.height_m = 4;
  assert.equal(detachDimensionBindings(change, config).dimension_bindings.length, 1);
  const unit = copy(config); unit.unit = 'm'; assert.deepEqual(detachDimensionBindings(unit, config).dimension_bindings, []);
});

test('excluding a dimension-bound entity detaches that evidence and selection undo restores it', async () => {
  const payload = await restoredProject(); payload.draft_config.overrides.A = { height_m: 3 };
  const binding = { dimension_id: 'D1', target_id: 'A', parameter: 'height_m', value_source: 'measurement' };
  payload.draft_config.dimension_bindings = [binding]; payload.applied_config = copy(payload.draft_config);
  await withApp(async () => json(payload), async ({ document: doc, state }) => {
    entityPath(doc, 'A').click(); doc.ids.excludePicked.click(); assert.deepEqual(state.config.dimension_bindings, []);
    doc.ids.undoSelection.click(); assert.deepEqual(state.config.dimension_bindings, [binding]); assert.equal(state.dirty, false);
    entityPath(doc, 'A').click(); doc.ids.excludePicked.click(); doc.ids.drawingUnit.value = 'm'; doc.ids.drawingUnit.emit('change');
    doc.ids.undoSelection.click(); assert.deepEqual(state.config.dimension_bindings, []); assert.equal(state.config.unit, 'm');
  }, undefined, projectOptions);
});

const scanResponse = { ok: true, import_id: 'd'.repeat(32), index: { filename: 'large.dxf', sha256: 'source', layers: [{ name: 'WALL', entity_count: 2, bounds: { min: [0, 0], max: [4000, 3000] } }, { name: 'TITLE', entity_count: 1, bounds: { min: [-1000, -1000], max: [5000, 4000] } }], bounds: { min: [-1000, -1000], max: [5000, 4000] }, preview: [{ id: 'I1', layer: '0', layers: ['WALL'], block_overview: true, points: [], bounds: { min: [0, 0], max: [4000, 3000] } }], counts: { modelspace: 3, expanded: 300000, previewed: 1 }, preview_sampled: true, warnings: ['块引用以概览框显示'] } };

test('large files scan first, retain the previous model, and only selected layers and complete bounds are imported', async () => {
  const payload = await restoredProject(); let scanRequest, selectRequest;
  await withApp(async (url, options) => {
    if (url.endsWith('/scan')) { scanRequest = options; return json(scanResponse); }
    if (url.endsWith('/select')) { selectRequest = JSON.parse(options.body); return json({ ...documentResponse, document_id: 'new-source' }); }
    return json(payload);
  }, async ({ document: doc, state }) => {
    doc.ids.cadFile.files = [new File([new Uint8Array(10 * 1024 * 1024 + 1)], 'large.dxf')]; doc.ids.cadFile.emit('change'); await waitFor(() => !!state.scan && !state.busy);
    assert.ok(/^[a-f0-9]{32}$/.test(scanRequest.headers['X-CAD-Operation-ID'])); assert.equal(state.modelId, 'model-one'); assert.equal(doc.ids.importScanSelection.disabled, true);
    assert.match(doc.ids.scanSummary.textContent, /抽样/); assert.equal(doc.ids.scanSvg.children[0].attributes['stroke-dasharray'], '5 4');
    const beforeZoom = doc.ids.scanSvg.attributes.viewBox;
    doc.ids.scanSvg.emit('wheel', { deltaY: -200, clientX: 50, clientY: 50 }); const zoomed = doc.ids.scanSvg.attributes.viewBox;
    assert.notEqual(zoomed, beforeZoom);
    const checkbox = doc.ids.scanLayers.children[0].children[0]; checkbox.checked = true; checkbox.emit('change');
    assert.equal(doc.ids.scanSvg.attributes.viewBox, zoomed); doc.ids.fitScan.click(); assert.equal(doc.ids.scanSvg.attributes.viewBox, beforeZoom);
    assert.equal(doc.ids.importScanSelection.disabled, false);
    ['scanMinX', 'scanMinY', 'scanMaxX', 'scanMaxY'].forEach((id, index) => { doc.ids[id].value = [0, 0, 4000, 3000][index]; });
    doc.ids.importScanSelection.click(); await waitFor(() => state.documentId === 'new-source' && !state.busy);
    assert.deepEqual(selectRequest, { import_id: scanResponse.import_id, layers: ['WALL'], bounds: [0, 0, 4000, 3000] });
    assert.equal(state.model, null); assert.equal(state.config.parameters.wall.height_m, null); assert.equal(state.scan, null); assert.equal(doc.ids.exportConfirmation.value, '');
  }, undefined, projectOptions);
});

test('large import cancellation notifies its exact operation before abort and discards late results', async () => {
  const payload = await restoredProject(); let finish, operation, signal; const cancelled = [];
  await withApp(async (url, options) => {
    if (url.endsWith('/scan')) { operation = options.headers['X-CAD-Operation-ID']; signal = options.signal; return new Promise((resolve) => { finish = resolve; }); }
    if (url.endsWith('/cancel')) { cancelled.push({ url, wasAborted: signal.aborted }); return json({ ok: true }); }
    return json(payload);
  }, async ({ document: doc, state }) => {
    doc.ids.scanFirst.checked = true; doc.ids.cadFile.files = [new File(['fixture'], 'large.dxf')]; doc.ids.cadFile.emit('change'); await waitFor(() => !!finish);
    assert.equal(doc.ids.operationProgress.hidden, false); assert.match(doc.ids.operationStage.textContent, /第一阶段/);
    doc.ids.cancelOperation.click(); await waitFor(() => signal.aborted);
    assert.deepEqual(cancelled, [{ url: `/api/cad/import/${operation}/cancel`, wasAborted: false }]); assert.equal(state.modelId, 'model-one');
    finish(json(scanResponse)); await new Promise((resolve) => setImmediate(resolve)); assert.equal(state.scan, null); assert.equal(state.documentId, 'drawing-one');
  }, undefined, projectOptions);
});

test('invalid scan bounds and a failed selected import retain the old model and resumable scan', async () => {
  const payload = await restoredProject(); let selects = 0;
  await withApp(async (url) => {
    if (url.endsWith('/scan')) return json(scanResponse);
    if (url.endsWith('/select')) { selects++; return json({ detail: '所选区域太大' }, 422); }
    return json(payload);
  }, async ({ document: doc, state }) => {
    doc.ids.scanFirst.checked = true; doc.ids.cadFile.files = [new File(['fixture'], 'large.dxf')]; doc.ids.cadFile.emit('change'); await waitFor(() => !!state.scan && !state.busy);
    doc.ids.scanSelectAll.click(); doc.ids.scanMinX.value = '42'; doc.ids.importScanSelection.click();
    assert.equal(selects, 0); assert.match(doc.ids.notice.textContent, /完整有效/);
    doc.ids.resetScanBounds.click(); doc.ids.importScanSelection.click(); await waitFor(() => selects === 1 && !state.busy);
    assert.equal(state.modelId, 'model-one'); assert.equal(state.scan.import_id, scanResponse.import_id); assert.match(doc.ids.notice.textContent, /缩小范围/);
  }, undefined, projectOptions);
});

test('STEP availability and exact signing are both required before the export request', async () => {
  const payload = await restoredProject(); let exports = 0;
  await withApp(async (url) => { if (url.endsWith('/export')) exports++; return json(payload); }, async ({ document: doc }) => {
    const step = doc.groups.export.find((item) => item.dataset.export === 'step');
    doc.ids.exportConfirmation.value = '我明白，将由持证人员签认'; doc.ids.exportConfirmation.emit('input'); assert.equal(step.disabled, true); step.click(); assert.equal(exports, 0); assert.match(doc.ids.stepHint.textContent, /requirements-cad-step/);
  }, () => json({ ok: true, available: true, step_available: false, step_install_command: 'python -m pip install -r requirements-cad-step.txt' }), projectOptions);
  await withApp(async () => json(payload), async ({ document: doc }) => {
    const step = doc.groups.export.find((item) => item.dataset.export === 'step'); assert.equal(step.disabled, true);
    doc.ids.exportConfirmation.value = '我明白，将由持证人员签认'; doc.ids.exportConfirmation.emit('input'); assert.equal(step.disabled, false); assert.match(doc.ids.stepHint.textContent, /离散轮廓/);
  }, () => json({ ok: true, available: true, step_available: true }), projectOptions);
});

test('project package upload respects the advertised limit for preserved large sources', async () => {
  const payload = await restoredProject(); let imported = 0;
  await withApp(async (url) => { assert.equal(url, '/api/cad/projects/import'); imported++; return json(payload); }, async ({ document: doc, state }) => {
    doc.ids.projectPackage.files = [new File([new Uint8Array(17 * 1024 * 1024)], 'large-project.zip')]; doc.ids.projectPackage.emit('change');
    await waitFor(() => imported === 1 && !state.busy); assert.equal(state.project.id, project.id);
  }, () => json({ ok: true, available: true, max_project_bundle_bytes: 80 * 1024 * 1024 }));
});


test('section result returns when selection is undone and save carries a cancellable operation', async () => {
  const payload = await restoredProject();
  payload.draft_config.mode = 'section'; payload.draft_config.layers.WALL = 'section';
  payload.draft_config.parameters.section = { height_m: null, base_m: 0 };
  payload.applied_config = copy(payload.draft_config);
  const section = { run_id: 'c'.repeat(32), result: { unit: 'mm', engine: 'sectionproperties', engine_version: 'test', notes: [], regions: [{ layer: 'WALL', outer_id: 'A', hole_ids: ['B'], area_mm2: 2640000, centroid_source: [2000, 1500], Ixx_mm4: 1, Iyy_mm4: 2, Ixy_mm4: 0, I11_mm4: 2, I22_mm4: 1, principal_angle_deg: 0, rx_mm: 1, ry_mm: 2 }] } };
  const textOf = (node) => [node.textContent || '', ...node.children.map(textOf)].join(' ');
  let saveOptions;
  await withApp(async (url, options) => {
    if (url === '/api/engineering/section') { assert.equal(JSON.parse(options.body).config.parameters.section.height_m, null); return json(section); }
    if (url === '/api/engineering/projects') { saveOptions = options; return json({ project: { id: 'd'.repeat(32), revision: 1 }, version: 1 }); }
    return json(payload);
  }, async ({ document: doc, state }) => {
    doc.ids.calculateSection.click(); await waitFor(() => !state.busy && !doc.ids.saveSection.disabled);
    assert.match(textOf(doc.ids.sectionProperties), /2,640,000/);
    entityPath(doc, 'A').click(); doc.ids.excludePicked.click();
    assert.equal(doc.ids.saveSection.disabled, true); assert.match(textOf(doc.ids.sectionProperties), /重新计算/);
    doc.ids.undoSelection.click();
    assert.equal(doc.ids.saveSection.disabled, false); assert.match(textOf(doc.ids.sectionProperties), /2,640,000/);
    doc.ids.saveSection.click(); await waitFor(() => !!saveOptions && !state.busy);
    assert.match(saveOptions.headers['X-CAD-Operation-ID'], /^[0-9a-f]{32}$/);
    assert.equal(doc.ids.sectionRecord.hidden, false);
  }, undefined, projectOptions);
});
