const translatedUI = new Map();
const trEngineering = (source, values) => {
  const result = (globalThis.CBI18n || globalThis.window?.CBI18n)?.t(source, values) ?? source.replace(/\{([A-Za-z][A-Za-z0-9_]*)\}/g, (match, key) => values && Object.hasOwn(values, key) ? String(values[key]) : match);
  translatedUI.set(result, { source, values });
  if (translatedUI.size > 2500) translatedUI.delete(translatedUI.keys().next().value);
  return result;
};
// Only explicitly named app-owned status nodes are refreshed here. Source text and inputs are excluded.
const relocalizeOwned = (doc, ids) => { for (const id of ids) { const node = doc.getElementById(id); if (!node) continue; const nodes = node.tagName === 'SELECT' ? [...node.options] : [node]; for (const target of nodes) { const item = translatedUI.get(target.textContent); if (item) target.textContent = trEngineering(item.source, item.values); } } };
const uiLocale = () => (globalThis.CBI18n || globalThis.window?.CBI18n)?.locale || 'zh-CN';
export function startEngineeringApp(document, options = {}) {
const $ = (id) => document.getElementById(id);
const copy = (value) => JSON.parse(JSON.stringify(value));
const el = (tag, text, cls) => { const node = document.createElement(tag); node.setAttribute('data-i18n-dynamic', ''); if (text !== undefined) node.textContent = text; if (cls) node.className = cls; return node; };
const number = (value) => Number.isFinite(value) ? Number(value.toPrecision(6)).toLocaleString(uiLocale(), { maximumSignificantDigits: 6 }) : '—';
const state = { mode: 'frame', run: null, project: null, dirty: true, busy: false, sequence: 0, inputRevision: 0, projectListSequence: 0, inputs: {}, history: [], formPending: false, editingText: false, lastText: '', initialProjectHandled: false };
let controller, operation;
function notify(text, error = false) { $('notice').textContent = text; $('notice').className = `notice${error ? ' error' : ''}`; }
async function request(url, options = {}) {
  const response = await fetch(url, { credentials: 'same-origin', ...options });
  if (response.status === 401) { $('auth').hidden = false; throw new Error(trEngineering("请输入访问口令，当前编辑已保留。")); }
  if (!response.ok) { let data; try { data = await response.json(); } catch { data = {}; } throw new Error(typeof data.detail === 'string' ? data.detail : trEngineering("请求失败（{p0}）", {p0: response.status})); }
  return response;
}
async function json(url, options) { return (await request(url, options)).json(); }
const post = (value) => ({ method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(value) });
function controls() {
  for (const input of document.querySelectorAll('.inputs input,.inputs textarea,.inputs select,.inputs form button,.tabs button')) input.disabled = !!state.busy;
  $('save').disabled = $('saveCopy').disabled = $('export').disabled = !state.run || state.dirty || !!state.busy;
  $('run').disabled = !!state.busy || state.formPending;
  $('cancel').hidden = !['compute', 'load'].includes(state.busy);
  $('undoInput').disabled = !state.history.length || !!state.busy || state.formPending;
  for (const id of ['beamExample', 'frameExample', 'checkExample', 'diffExample', 'open', 'restore']) $(id).disabled = !!state.busy;
  $('restore').disabled = !!state.busy || !state.project;
  for (const id of ['projectName', 'projects', 'versions', 'confirmation']) $(id).disabled = !!state.busy;
  $('frameModel').disabled = $('importFrame').disabled = !!state.busy || state.formPending;
  $('sourceSynthetic').disabled = !!state.busy || state.formPending || !parsedFrame();
  $('discardForm').disabled = !!state.busy || !state.formPending;
  $('formPending').hidden = !state.formPending;
  const pending = state.formPending ? trEngineering("表单修改尚未应用。请应用到模型，或撤回表单修改，再开始计算。") : '';
  $('inputState').textContent = state.busy ? ({ compute: trEngineering("正在本地计算…"), load: trEngineering("正在载入输入或项目…"), save: trEngineering("正在保存当前计算版本…"), export: trEngineering("正在导出当前计算记录…") }[state.busy]) : pending || (state.dirty && state.run ? trEngineering("当前编辑尚未产生新结果；保留上一次成功结果，请重新计算。") : state.run ? trEngineering("结果与当前分析模型一致") : trEngineering("尚未计算"));
  $('resultState').hidden = !state.run;
  $('resultState').textContent = state.run ? state.dirty || state.busy === 'compute' ? trEngineering("上一次成功结果 · 当前编辑 / 本次计算尚未生成可用结果") : trEngineering("当前分析模型的成功结果") : '';
  $('projectState').textContent = state.project ? trEngineering("{p0} · 修订 {p1}{p2}", {p0: state.project.name, p1: state.project.revision, p2: state.dirty ? trEngineering(" · 未保存编辑") : ''}) : trEngineering("未保存");
}
function edited() { state.inputRevision++; state.dirty = true; controls(); }
function parsedFrame() { try { const value = JSON.parse($('frameModel').value); return value && typeof value === 'object' && !Array.isArray(value) ? value : null; } catch { return null; } }
function remember() { const value = $('frameModel').value; if (state.history.at(-1) !== value) state.history.push(value); if (state.history.length > 20) state.history.shift(); }
function setModelText(text, rememberOld = true) {
  if (rememberOld) remember();
  $('frameModel').value = text; state.lastText = text; state.editingText = false; state.formPending = false;
  const model = parsedFrame();
  $('sourceSynthetic').checked = model?.source === 'synthetic'; $('sampleLabel').hidden = model?.source !== 'synthetic';
  syncBeam(model || {}); edited();
}
function setModel(model, rememberOld = true) {
  setModelText(JSON.stringify(model, null, 2), rememberOld);
}
function syncBeam(model) {
  const rows = (key) => Array.isArray(model[key]) ? model[key].filter((row) => row && typeof row === 'object') : [];
  const member = rows('members')[0], material = rows('materials').find((row) => row.id === member?.material_id), section = rows('sections').find((row) => row.id === member?.section_id);
  const nodes = rows('nodes'), a = nodes.find((row) => row.id === member?.i), b = nodes.find((row) => row.id === member?.j);
  const load = rows('member_loads')[0];
  const values = { length: a && b ? Math.hypot(b.x_m-a.x_m,b.y_m-a.y_m,b.z_m-a.z_m) : '', load: load ? -load.w1_N_m : '', E: material?.E_Pa ?? '', nu: material?.nu ?? '', density: material?.density_kg_m3 ?? '', A: section?.A_m2 ?? '', Iy: section?.Iy_m4 ?? '', Iz: section?.Iz_m4 ?? '', J: section?.J_m4 ?? '' };
  for (const [key, value] of Object.entries(values)) $('beamForm').elements.namedItem(key).value = value;
  $('beamForm').elements.namedItem('supports').checked = false;
  $('beamTemplateNotice').textContent = rows('members').length > 1 ? trEngineering("当前是多杆件模型。下列表单会新建一根直梁并替换完整模型；如需保留框架，请编辑下方 JSON。") : trEngineering("直梁表单只有应用后才成为计算输入；完整 JSON 是实际分析模型。");
}
function switchMode(mode, reset = true) {
  if (state.busy && reset) return;
  state.mode = mode;
  $('frameFields').hidden = mode !== 'frame'; $('checkFields').hidden = mode !== 'ifc_check'; $('diffFields').hidden = mode !== 'ifc_diff';
  $('sectionFields').hidden = mode !== 'section';
  document.querySelectorAll('[data-mode]').forEach((node) => node.setAttribute('aria-pressed', String(node.dataset.mode === mode)));
  if (reset) { state.sequence++; state.run = null; state.project = null; state.dirty = true; state.formPending = false; state.history = []; state.editingText = false; syncBeam(parsedFrame() || {}); $('confirmation').value = ''; $('result').replaceChildren(el('div', trEngineering("当前工具尚未计算。"), 'empty')); $('engine').textContent = ''; $('rawResult').hidden = true; $('versions').replaceChildren(new Option(trEngineering("当前版本"), '')); }
  controls();
}
function begin(kind) {
  if (state.busy) return null;
  state.busy = kind; controller = new AbortController(); operation = kind === 'compute' ? crypto.randomUUID().replaceAll('-', '') : null;
  const token = { sequence: ++state.sequence, revision: state.inputRevision, mode: state.mode, signal: controller.signal, operation };
  controls(); return token;
}
function owns(token) { return token.sequence === state.sequence; }
function finish(token) { if (owns(token)) { state.busy = false; operation = null; controller = null; controls(); } }
async function perform(kind, action) {
  const token = begin(kind); if (!token) return;
  try { return await action(token); }
  catch (error) { if (owns(token) && error.name !== 'AbortError') notify(error.message, true); }
  finally { finish(token); }
}
function table(headers, rows) {
  const wrap = el('div', undefined, 'table-scroll'), node = el('table'), head = el('thead'), tr = el('tr');
  headers.forEach((text) => tr.append(el('th', text))); head.append(tr); node.append(head);
  const body = el('tbody'); rows.slice(0, 500).forEach((values) => { const row = el('tr'); values.forEach((value) => row.append(el('td', String(value ?? '—')))); body.append(row); }); node.append(body); wrap.append(node);
  if (rows.length > 500) wrap.append(el('p', trEngineering("显示前 500 / {p0} 行，完整结果在下方 JSON 记录。", {p0: rows.length}), 'muted'));
  return wrap;
}
function metrics(items) { const box = el('div', undefined, 'metrics'); for (const [label, value] of items) { const cell = el('div', undefined, 'metric'); cell.append(el('strong', String(value)), el('small', label)); box.append(cell); } return box; }
function chart(title, x, y, unit) {
  const ns = 'http://www.w3.org/2000/svg', svg = document.createElementNS(ns, 'svg'); svg.setAttribute('viewBox', '0 0 620 210'); svg.setAttribute('class', 'chart'); svg.setAttribute('role', 'img'); svg.setAttribute('aria-label', title);
  const put = (tag, attributes, text) => { const node = document.createElementNS(ns, tag); Object.entries(attributes).forEach(([key, value]) => node.setAttribute(key, value)); if (text !== undefined) node.textContent = text; svg.append(node); };
  const xmin = Math.min(...x), xmax = Math.max(...x), ymax = Math.max(...y.map(Math.abs), 1e-15);
  put('text', { x: 15, y: 22 }, `${title}（${unit}）`); put('line', { x1: 55, x2: 595, y1: 112, y2: 112, class: 'axis' });
  put('text', { x: 8, y: 48 }, number(ymax)); put('text', { x: 8, y: 184 }, number(-ymax)); put('text', { x: 55, y: 201 }, `${number(xmin)} m`); put('text', { x: 555, y: 201 }, `${number(xmax)} m`);
  put('polyline', { points: x.map((value, index) => `${55+(value-xmin)/(xmax-xmin||1)*530},${112-y[index]/ymax*70}`).join(' ') });
  return svg;
}
function renderFrame(result) {
  const box = $('result'), choices = el('div', undefined, 'row'), combo = el('select'), member = el('select'), content = el('div');
  combo.setAttribute('aria-label', trEngineering("荷载组合")); member.setAttribute('aria-label', trEngineering("查看杆件"));
  for (const row of result.combinations) combo.append(new Option(row.id, row.id));
  choices.append(el('span', trEngineering("组合 / 杆件")), combo, member); box.append(choices, content);
  const render = () => {
    content.replaceChildren(); const current = result.combinations.find((row) => row.id === combo.value); const beam = current.members.find((row) => row.id === member.value) || current.members[0];
    const c = beam.curves;
    content.append(metrics([[trEngineering("竖向反力合计 N"), number(current.nodes.reduce((sum, row) => sum+row.reaction_N[1], 0))], [trEngineering("本杆采样最大 |Mz| N·m"), number(Math.max(...c.moment_z_Nm.map(Math.abs)))], [trEngineering("本杆采样最大 |dy| mm"), number(Math.max(...c.dy_m.map(Math.abs))*1000)]]));
    content.append(chart(trEngineering("局部 y 剪力"), c.x_m, c.shear_y_N, 'N'), chart(trEngineering("局部 z 弯矩"), c.x_m, c.moment_z_Nm, 'N·m'), chart(trEngineering("局部 y 位移"), c.x_m, c.dy_m.map((v) => v*1000), 'mm'));
    content.append(table([trEngineering("节点"), 'Rx N', 'Ry N', 'Rz N', 'Uy mm'], current.nodes.map((row) => [row.id, ...row.reaction_N.map(number), number(row.displacement_m[1]*1000)])));
    content.append(el('p', trEngineering("杆件 {p0}：{p1} → {p2}，长度 {p3} m。图示为局部轴量；完整三向结果见计算记录。", {p0: beam.id, p1: beam.i, p2: beam.j, p3: number(beam.length_m)}), 'muted'));
  };
  const selectCombo = () => { member.replaceChildren(); result.combinations.find((row) => row.id === combo.value).members.forEach((row) => member.append(new Option(row.id, row.id))); render(); };
  combo.addEventListener('change', selectCombo); member.addEventListener('change', render); selectCombo();
}
function renderResult(result) {
  $('result').replaceChildren();
  $('engine').textContent = typeof result.engine === 'object' ? `${result.engine.name} ${result.engine.version}` : `${result.engine} ${result.engine_version || ''}`;
  $('rawResult').hidden = false; $('resultJson').textContent = JSON.stringify(result, null, 2);
  if (state.mode === 'frame') renderFrame(result);
  else if (state.mode === 'ifc_check') {
    const report = result.report;
    $('result').append(metrics([[trEngineering("规则检查项"), report.total_checks], [trEngineering("满足要求"), report.total_checks_pass], [trEngineering("未满足要求"), report.total_checks_fail]]));
    for (const spec of report.specifications) {
      $('result').append(el('h3', `${spec.name} · ${spec.is_skipped ? trEngineering("跳过") : spec.status ? trEngineering("满足本条要求") : trEngineering("未满足本条要求")}`));
      $('result').append(el('p', trEngineering("适用对象 {p0} · IFC 版本{p1} · 要求出现次数 {p2}", {p0: spec.total_applicable, p1: spec.is_ifc_version ? trEngineering("匹配") : trEngineering("不匹配"), p2: spec.cardinality}), 'muted'));
      const rows = spec.requirements.flatMap((req) => (req.failed_entities || []).map((entity) => [entity.global_id || entity.id, entity.class, req.description, entity.reason]));
      if (rows.length) $('result').append(table([trEngineering("原构件编号"), trEngineering("类型"), trEngineering("要求"), trEngineering("原因")], rows));
    }
    $('result').append(el('p', `${result.source.name} · SHA-256 ${result.source.sha256}`, 'source'));
  } else if (state.mode === 'ifc_diff') {
    $('result').append(metrics([[trEngineering("新增"), result.added.length], [trEngineering("删除"), result.deleted.length], [trEngineering("改变"), result.changed.length], [trEngineering("未改变"), result.unchanged_count]]));
    $('result').append(table([trEngineering("变化"), 'GlobalId', trEngineering("类型 / 名称"), trEngineering("说明")], [
      ...result.added.map((row) => [trEngineering("新增"), row.global_id, `${row.type} / ${row.name}`, trEngineering("新版存在")]), ...result.deleted.map((row) => [trEngineering("删除"), row.global_id, `${row.type} / ${row.name}`, trEngineering("原版存在")]),
      ...result.changed.map((row) => [trEngineering("改变"), row.global_id, `${row.type} / ${row.name}`, JSON.stringify(row.changes)]),
    ]));
    for (const source of [result.old_source, result.new_source]) $('result').append(el('p', `${source.name} · SHA-256 ${source.sha256}`, 'source'));
  } else if (state.mode === 'section') {
    $('result').append(table([trEngineering("轮廓"), trEngineering("面积 mm²"), trEngineering("形心原图坐标"), 'Ixx mm⁴', 'Iyy mm⁴', 'Ixy mm⁴'], result.regions.map((row) => [row.outer_id, number(row.area_mm2), row.centroid_source.map(number).join(', '), number(row.Ixx_mm4), number(row.Iyy_mm4), number(row.Ixy_mm4)])));
  }
  const notes = el('ul', undefined, 'notes'); for (const note of result.notes || result.assumptions || []) notes.append(el('li', note)); $('result').append(notes);
}
function setVersions(project) { $('versions').replaceChildren(new Option(trEngineering("当前版本"), '')); for (const row of project.versions || []) $('versions').append(new Option(trEngineering("版本 {p0} · {p1}", {p0: row.version, p1: row.created_at}), row.version)); }
async function refreshProjects(preferredId) {
  const listToken = ++state.projectListSequence, sequence = state.sequence;
  const data = await json('/api/engineering/projects');
  if (listToken !== state.projectListSequence || sequence !== state.sequence) return;
  const selected = preferredId || $('projects').value || state.project?.id || '';
  $('projects').replaceChildren(new Option(trEngineering("选择项目"), ''));
  for (const project of data.projects) $('projects').append(new Option(`${project.name}${project.error ? trEngineering(" · 无法读取") : ''}`, project.id));
  $('projects').value = selected;
}
function sourceLabels() { for (const [mode, id] of [['ifc_check', 'checkSources'], ['ifc_diff', 'diffSources']]) { const sources = state.inputs[mode]; $(id).textContent = sources ? Object.values(sources).map((row) => row.name).join(' / ') : trEngineering("尚未选择文件"); } }
async function fileValue(file, maxBytes) { if (!file) throw new Error(trEngineering("请选择文件。")); if (!file.size || file.size > maxBytes) throw new Error(trEngineering("文件为空或超过页面标注上限。")); const data = new Uint8Array(await file.arrayBuffer()); let binary = ''; for (let i=0; i<data.length; i+=32768) binary += String.fromCharCode(...data.subarray(i,i+32768)); return { name: file.name, data_b64: btoa(binary) }; }
async function loadExample(kind) {
  return perform('load', async (token) => {
    if (kind === 'beam' || kind === 'frame') {
      const value = await json(`/api/engineering/examples/frame?kind=${kind}`, { signal: token.signal });
      if (!owns(token)) return;
      setModel(value.model); if (kind === 'frame') $('modelDetails').open = true;
    } else {
      const names = kind === 'ifc_check' ? [['ifc','old.ifc'],['ids','requirements.ids']] : [['old','old.ifc'],['new','new.ifc']];
      const payload = {};
      for (const [key,name] of names) {
        const response = await request(`/api/engineering/examples/ifc/${name}`, { signal: token.signal });
        payload[key] = await fileValue(new File([await response.blob()], `synthetic-${name}`), 8*1024*1024);
        if (!owns(token)) return;
      }
      state.inputs[kind] = payload; sourceLabels(); edited();
    }
    notify(trEngineering("已载入合成演示输入。点击开始计算；这些参数不是实际项目资料。"));
  });
}
async function execute() {
  if (state.busy) return;
  if (state.formPending) { notify(trEngineering("表单修改尚未应用，请先应用到分析模型或撤回表单修改。"), true); return; }
  let payload; const mode = state.mode;
  try { payload = mode === 'frame' ? JSON.parse($('frameModel').value) : copy(state.inputs[mode]); if (!payload || typeof payload !== 'object' || Array.isArray(payload)) throw new Error(trEngineering("请先补全输入对象。")); } catch (error) { notify(trEngineering("输入未就绪：{p0}", {p0: error.message}), true); return; }
  state.dirty = true;
  return perform('compute', async (token) => {
    notify(trEngineering("正在执行本地工具计算…"));
    const url = mode === 'frame' ? '/api/engineering/frame' : mode === 'section' ? '/api/engineering/section' : `/api/engineering/ifc/${mode === 'ifc_check' ? 'check' : 'diff'}`;
    let data;
    try { data = await json(url, { ...post(mode === 'frame' ? { model: payload } : payload), headers: { 'Content-Type':'application/json','X-CAD-Operation-ID':token.operation }, signal: token.signal }); }
    catch (error) { throw new Error(trEngineering("计算未完成：{p0} 上次成功结果仍保留。", {p0: error.message})); }
    if (!owns(token) || token.revision !== state.inputRevision) return;
    state.run = data; state.dirty = false; renderResult(data.result); notify(trEngineering("计算完成。可保存输入与结果，或修改参数后比较。"));
  });
}
async function openProject(version, identifier = $('projects').value) {
  if (state.busy) return;
  if (!identifier) { notify(trEngineering("请先选择一个项目。"), true); return; }
  return perform('load', async (token) => {
    const data = await json(`/api/engineering/projects/${encodeURIComponent(identifier)}${version ? `?version=${encodeURIComponent(version)}` : ''}`, { signal: token.signal });
    if (!owns(token)) return;
    state.project = data.project; switchMode(data.snapshot.kind, false); state.run = { run_id: data.run_id, result: data.snapshot.result };
    state.history = []; state.formPending = false; state.editingText = false;
    if (state.mode === 'frame') setModel(data.snapshot.inputs, false);
    else if (state.mode === 'section') state.inputs.section = { document_id: data.document_id, config: data.snapshot.inputs.config };
    else { state.inputs[state.mode] = data.snapshot.inputs; sourceLabels(); }
    state.dirty = false; $('confirmation').value = ''; $('projects').value = data.project.id; $('projectName').value = data.project.name; setVersions(data.project); $('versions').value = version || ''; renderResult(data.snapshot.result); controls(); notify(trEngineering("已打开 {p0} 的版本 {p1}。原始输入与结果已恢复。", {p0: data.project.name, p1: data.version}));
  });
}
async function cancelOperation() {
  if (!['load', 'compute'].includes(state.busy)) return;
  const id = operation, wasCompute = state.busy === 'compute';
  const sequence = ++state.sequence; controller?.abort(); state.busy = false; operation = null; controller = null;
  if (wasCompute) state.dirty = true;
  controls(); notify(wasCompute ? trEngineering("已停止等待本次计算，正在确认取消；上次成功结果保留。") : trEngineering("已取消载入，原输入与结果保留。"));
  if (id) {
    try { await json(`/api/engineering/operations/${id}/cancel`, post({})); if (sequence === state.sequence) notify(trEngineering("已取消本次计算，上次成功结果保留。")); }
    catch (error) { if (sequence === state.sequence) notify(trEngineering("已停止等待，取消确认失败：{p0}；没有更新计算结果。", {p0: error.message}), true); }
  }
}
async function saveProject(asCopy = false) {
  if (!state.run || state.dirty || state.busy || state.formPending) return;
  const runId = state.run.run_id, projectId = state.project?.id;
  const body = { run_id: runId, name: $('projectName').value };
  if (!asCopy && state.project) Object.assign(body, { id: projectId, expected_revision: state.project.revision });
  return perform('save', async (token) => {
    // Persistence refers only to a server-held successful run. The browser
    // never uploads a trusted result or pairs it with an edited input model.
    const data = await json('/api/engineering/projects', post(body));
    if (!owns(token) || state.run?.run_id !== runId || state.project?.id !== projectId) return;
    state.project = data.project; setVersions(data.project); controls();
    try { await refreshProjects(data.project.id); } catch (error) { if (owns(token)) notify(trEngineering("保存已成功，但最近项目列表刷新失败：{p0}", {p0: error.message}), true); return; }
    if (owns(token)) notify(trEngineering("已保存 {p0} · 版本 {p1}。", {p0: data.project.name, p1: data.version}));
  });
}
async function importFrame(file) {
  if (state.busy || state.formPending) return;
  return perform('load', async (token) => {
    if (!file || !file.size || file.size > 512*1024) throw new Error(trEngineering("参数 JSON 必须为非空文件，最大 512 KiB。"));
    const model = JSON.parse(await file.text());
    if (!owns(token)) return;
    setModel(model); $('modelDetails').open = true; notify(trEngineering("已载入参数 JSON，请核对后重新计算。"));
  });
}
function guard(fn) { return async (...args) => { try { await fn(...args); } catch (error) { notify(error.message, true); } }; }
document.querySelectorAll('[data-mode]').forEach((node) => node.addEventListener('click', () => switchMode(node.dataset.mode)));
$('beamExample').addEventListener('click', guard(() => loadExample('beam'))); $('frameExample').addEventListener('click', guard(() => loadExample('frame')));
$('checkExample').addEventListener('click', guard(() => loadExample('ifc_check'))); $('diffExample').addEventListener('click', guard(() => loadExample('ifc_diff')));
$('frameModel').addEventListener('focus', () => { state.editingText = false; state.lastText = $('frameModel').value; });
$('frameModel').addEventListener('input', () => {
  if (state.busy || state.formPending) return;
  if (!state.editingText) { if (state.history.at(-1) !== state.lastText) state.history.push(state.lastText); if (state.history.length > 20) state.history.shift(); state.editingText = true; }
  state.lastText = $('frameModel').value; const model = parsedFrame(); $('sourceSynthetic').checked = model?.source === 'synthetic'; $('sampleLabel').hidden = model?.source !== 'synthetic'; edited();
});
$('frameModel').addEventListener('change', () => { if (state.busy || state.formPending) return; state.editingText = false; syncBeam(parsedFrame() || {}); controls(); });
$('sourceSynthetic').addEventListener('change', guard(() => { if (state.busy || state.formPending) return; const model = parsedFrame(); if (!model) throw new Error(trEngineering("请先修复完整模型 JSON。")); model.source = $('sourceSynthetic').checked ? 'synthetic' : 'user'; setModel(model); }));
$('undoInput').addEventListener('click', () => { if (!state.history.length || state.busy || state.formPending) return; setModelText(state.history.pop(), false); notify(parsedFrame() ? trEngineering("已撤销一次模型参数修改，请重新计算。") : trEngineering("已恢复上一份原始编辑文本；JSON 尚未完整，请修复后计算。")); });
$('importFrame').addEventListener('change', () => { const file = $('importFrame').files[0]; $('importFrame').value = ''; return importFrame(file); });
$('beamForm').addEventListener('input', () => { if (state.busy) return; state.formPending = true; edited(); });
$('discardForm').addEventListener('click', () => { if (state.busy) return; syncBeam(parsedFrame() || {}); state.formPending = false; controls(); notify(trEngineering("已撤回未应用的表单修改，完整分析模型保持原值。")); });
$('beamForm').addEventListener('submit', (event) => {
  event.preventDefault(); if (state.busy) return; const form = $('beamForm'); if (!form.reportValidity()) return;
  const v = (key) => Number(form.elements.namedItem(key).value), length=v('length'), E=v('E'), nu=v('nu');
  setModel({ schema_version:1, units:'SI', source:$('sourceSynthetic').checked?'synthetic':'user', samples:41,
    nodes:[{id:'A',x_m:0,y_m:0,z_m:0,support:[true,true,true,true,false,false]},{id:'B',x_m:length,y_m:0,z_m:0,support:[false,true,true,false,false,false]}],
    materials:[{id:'MAT',E_Pa:E,G_Pa:E/(2*(1+nu)),nu,density_kg_m3:v('density')}], sections:[{id:'SEC',A_m2:v('A'),Iy_m4:v('Iy'),Iz_m4:v('Iz'),J_m4:v('J')}],
    members:[{id:'BEAM',i:'A',j:'B',material_id:'MAT',section_id:'SEC',rotation_deg:0}], load_cases:[{id:'Q'}], combinations:[{id:'Q',factors:{Q:1}}],
    nodal_loads:[],member_loads:[{member_id:'BEAM',case_id:'Q',direction:'FY',w1_N_m:-v('load'),w2_N_m:-v('load'),x1_m:0,x2_m:length}]
  }); notify(trEngineering("已应用明确的直梁参数。剪切模量按 G=E/[2(1+ν)] 计算；荷载组合系数为 1。"));
});
for (const [id,mode,key,limit] of [['checkIfc','ifc_check','ifc',8*1024*1024],['checkIds','ifc_check','ids',512*1024],['oldIfc','ifc_diff','old',8*1024*1024],['newIfc','ifc_diff','new',8*1024*1024]]) {
  $(id).addEventListener('change', () => {
    if (state.busy) return; const file = $(id).files[0]; $(id).value = ''; state.dirty = true;
    return perform('load', async (token) => { const source = await fileValue(file,limit); if (!owns(token)) return; state.inputs[mode] ||= {}; state.inputs[mode][key] = source; sourceLabels(); edited(); notify(trEngineering("文件已载入，请重新计算。")); });
  });
}
$('run').addEventListener('click', execute);
$('cancel').addEventListener('click', cancelOperation);
for (const [id,asCopy] of [['save',false],['saveCopy',true]]) $(id).addEventListener('click',()=>saveProject(asCopy));
$('open').addEventListener('click',guard(()=>openProject())); $('restore').addEventListener('click',guard(()=>state.project && openProject($('versions').value,state.project.id)));
$('export').addEventListener('click',()=>{
  if (!state.run || state.dirty || state.busy || state.formPending) return;
  const body = {run_id:state.run.run_id,confirmation:$('confirmation').value};
  return perform('export',async(token)=>{ const response=await request('/api/engineering/export',post(body)); const blob=await response.blob(); if(!owns(token))return; const url=URL.createObjectURL(blob); const anchor=el('a'); anchor.href=url; anchor.download='analysis-record.json'; anchor.click(); setTimeout(()=>URL.revokeObjectURL(url),1000); notify(trEngineering("计算记录已导出。")); });
});
$('auth').addEventListener('submit',guard(async(event)=>{event.preventDefault(); document.cookie=`cb_token=${encodeURIComponent($('token').value.trim())}; path=/; max-age=2592000; SameSite=Lax`; await initialize(); $('auth').hidden=true;}));
async function initialize() {
  const sequence=state.sequence, value=await json('/api/engineering/capabilities'); const ready=Object.values(value.tools).filter((row)=>row.available).length;
  $('capability').textContent=trEngineering("{p0} / 4 个计算工具就绪", {p0: ready}); await refreshProjects();
  const id=new URL(options.location || globalThis.location?.href || 'http://localhost/engineering').searchParams.get('project_id');
  if(!state.initialProjectHandled && sequence===state.sequence && !state.inputRevision && !state.busy) { state.initialProjectHandled=true; if(id) await openProject(undefined,id); }
  if(ready<4 && sequence===state.sequence) notify(trEngineering("部分工具尚未安装：{p0}", {p0: value.install_command})); controls();
}
const localeWindow = options.window || document.defaultView || globalThis.window;
const onLanguageChange = () => {
  controls(); sourceLabels();
  if (state.run) {
    const selections = [...document.querySelectorAll('#result select')].map(node => node.value);
    renderResult(state.run.result);
    [...document.querySelectorAll('#result select')].forEach((node, index) => {
      if (selections[index] !== undefined) { node.value = selections[index]; node.dispatchEvent(new localeWindow.Event('change')); }
    });
  }
  relocalizeOwned(document, ['notice','capability','projects','versions','beamTemplateNotice']);
};
localeWindow?.addEventListener?.('cb:languagechange', onLanguageChange);
controls();
const ready = options.initialize === false ? Promise.resolve() : guard(initialize)();
return { state, ready, execute, loadExample, openProject, saveProject, cancelOperation, importFrame, setModel, switchMode, refreshProjects, dispose() { state.sequence++; controller?.abort(); localeWindow?.removeEventListener?.("cb:languagechange", onLanguageChange); } };
}

if (typeof window !== 'undefined' && typeof document !== 'undefined') startEngineeringApp(document);
