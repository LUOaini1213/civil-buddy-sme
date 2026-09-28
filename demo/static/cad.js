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
const CONFIRMATION = '我明白，将由持证人员签认';
const CONFIRMATIONS = [CONFIRMATION, 'I understand; a licensed person will sign this off.'];
const ROLES = { get wall() { return trEngineering('墙体'); }, get column() { return trEngineering('柱子'); }, get slab() { return trEngineering('楼板'); }, get section() { return trEngineering('截面'); }, get ignore() { return trEngineering('忽略'); } };
const COLORS = { wall: '#8dcad8', column: '#52dbc3', slab: '#8aa4c5', section: '#52dbc3', ignore: '#708296' };
const clone = (value) => JSON.parse(JSON.stringify(value));
const selectionOf = (config) => config?.selection || { include_ids: null, exclude_ids: [], groups: [] };
export function selectedEntityIds(document, config) {
  const selection = selectionOf(config), included = selection.include_ids === null ? null : new Set(selection.include_ids), excluded = new Set(selection.exclude_ids || []);
  return (document?.entities || []).filter((entity) => (!included || included.has(entity.id)) && !excluded.has(entity.id) && ['wall', 'column', 'slab', 'section'].includes(config?.layers?.[entity.layer])).map((entity) => entity.id);
}
export function editSelection(config, ids, action) {
  const next = clone(config), selection = clone(selectionOf(config)), picked = [...new Set(ids)];
  if (action === 'only') { selection.include_ids = picked; selection.exclude_ids = []; }
  if (action === 'add') { if (selection.include_ids !== null) selection.include_ids = [...new Set([...selection.include_ids, ...picked])]; selection.exclude_ids = selection.exclude_ids.filter((id) => !picked.includes(id)); }
  if (action === 'exclude') selection.exclude_ids = [...new Set([...selection.exclude_ids, ...picked])];
  if (action === 'reset') { selection.include_ids = null; selection.exclude_ids = []; }
  next.selection = selection; return next;
}
export function entityBounds(entity) {
  if (entity.bounds?.min && entity.bounds?.max) return [...entity.bounds.min.slice(0, 2), ...entity.bounds.max.slice(0, 2)];
  const points = (entity.points || []).filter((p) => Number.isFinite(p[0]) && Number.isFinite(p[1]));
  if (!points.length) return null;
  return points.reduce(([x0, y0, x1, y1], [x, y]) => [Math.min(x0, x), Math.min(y0, y), Math.max(x1, x), Math.max(y1, y)], [Infinity, Infinity, -Infinity, -Infinity]);
}
export function idsInBounds(entities, bounds) {
  return (entities || []).filter((entity) => { const box = entityBounds(entity); return box && box[0] >= bounds[0] && box[1] >= bounds[1] && box[2] <= bounds[2] && box[3] <= bounds[3]; }).map((entity) => entity.id);
}
export function materialPath(contour) {
  return [contour.points || [], ...(contour.holes || [])].filter((points) => points.length > 2).map((points) => points.map(([x, y], index) => `${index ? 'L' : 'M'}${x} ${-y}`).join(' ') + ' Z').join(' ');
}
export function detachDimensionBindings(config, previous) {
  const next = clone(config);
  if (!next.dimension_bindings?.length) return next;
  if (next.unit !== previous.unit) { next.dimension_bindings = []; return next; }
  next.dimension_bindings = next.dimension_bindings.filter((binding) => {
    if (!sameConfig(next.layers, previous.layers) && binding.target_id) return false;
    if (binding.role && !Object.values(next.layers || {}).includes(binding.role)) return false;
    const selection = selectionOf(next);
    if (binding.target_id && (selection.exclude_ids.includes(binding.target_id) || (selection.include_ids !== null && !selection.include_ids.includes(binding.target_id)))) return false;
    const value = (cfg) => binding.target_id ? cfg.overrides?.[binding.target_id]?.[binding.parameter] : cfg.parameters?.[binding.role]?.[binding.parameter];
    return Object.is(value(next), value(previous));
  });
  return next;
}

export function sameConfig(a, b) {
  const ordered = (value) => Array.isArray(value) ? value.map(ordered) : value && typeof value === 'object' ? Object.fromEntries(Object.keys(value).sort().map((key) => [key, ordered(value[key])])) : value;
  return JSON.stringify(ordered(a)) === JSON.stringify(ordered(b));
}

/** Requests are tied to a document generation. A late response may never replace a new file. */
export class CadState {
  constructor() { this.sequence = 0; this.reset(); }
  reset() { this.sequence += 1; this.document = null; this.documentId = null; this.model = null; this.modelId = null; this.appliedConfig = null; this.config = null; this.history = []; this.busy = false; this.selectedId = null; this.project = null; this.savedConfig = null; this.savedModelId = null; this.projectName = ''; this.savedName = ''; this.pickedIds = []; this.selectionHistory = []; this.analysis = null; this.analysisConfig = null; this.scan = null; }
  begin() { this.busy = true; return ++this.sequence; }
  isCurrent(token) { return token === this.sequence; }
  finish(token) { if (this.isCurrent(token)) this.busy = false; }
  setDocument(token, payload, config) {
    if (!this.isCurrent(token)) return false;
    this.document = payload.document; this.documentId = payload.document_id; this.config = clone(config);
    return true;
  }
  setModel(token, payload, config, undo = false) {
    if (!this.isCurrent(token)) return false;
    if (undo) this.history.pop();
    else if (this.appliedConfig && !sameConfig(this.appliedConfig, config)) this.history.push(clone(this.appliedConfig));
    this.model = payload.model; this.modelId = payload.model_id;
    this.appliedConfig = clone(config); this.config = clone(config);
    return true;
  }
  get dirty() { return !this.appliedConfig || !sameConfig(this.config, this.appliedConfig); }
  get canExport() { return !!(this.modelId && this.model?.objects?.length && !this.dirty && !this.busy); }
  get projectDirty() { return !this.project || !sameConfig(this.config, this.savedConfig) || this.modelId !== this.savedModelId || this.projectName.trim() !== this.savedName; }
  setProject(token, payload, historical = false) {
    if (!this.isCurrent(token)) return false;
    this.document = payload.document; this.documentId = payload.document_id;
    this.model = payload.model || null; this.modelId = payload.model_id || null;
    this.config = clone(payload.draft_config); this.appliedConfig = payload.applied_config ? clone(payload.applied_config) : null;
    this.project = clone(payload.project); this.projectName = payload.project.name; this.savedName = payload.project.name;
    this.savedConfig = historical ? null : clone(payload.draft_config); this.savedModelId = historical ? null : this.modelId;
    this.history = []; this.selectedId = null; this.pickedIds = []; this.selectionHistory = []; this.analysis = null; this.analysisConfig = null; this.scan = null;
    return true;
  }
}

export function validateConfig(config) {
  if (!['mm', 'cm', 'm', 'in', 'ft'].includes(config.unit)) return trEngineering("请先确认图纸坐标单位。");
  if (!config.confirmed_solid) return trEngineering("请确认所选闭合轮廓是实体区域，同层内圈表示孔洞。");
  const tolerance = config.curve_tolerance_mm === undefined ? .1 : config.curve_tolerance_mm;
  if (!Number.isFinite(tolerance) || tolerance < .001 || tolerance > 10) return trEngineering("曲线弦高误差必须为 0.001 至 10 毫米。");
  const roles = [...new Set(Object.values(config.layers).filter((role) => role !== 'ignore'))];
  if (!roles.length) return trEngineering("请至少选择一个需要建模的图层。");
  for (const role of roles) {
    const param = config.parameters[role];
    if (!param || !Number.isFinite(param.height_m) || param.height_m <= 0) return trEngineering("请填写{p0}的有效{p1}（大于 0 米）。", {p0: ROLES[role] || role, p1: role === 'section' ? trEngineering("拉伸长度") : role === 'slab' ? trEngineering("厚度") : trEngineering("高度")});
    if (!Number.isFinite(param.base_m)) return trEngineering("请填写{p0}的有效底部标高。", {p0: ROLES[role] || role});
  }
  for (const [id, values] of Object.entries(config.overrides || {})) {
    if (Object.hasOwn(values, 'height_m') && (!Number.isFinite(values.height_m) || values.height_m <= 0)) return trEngineering("请填写实体 {p0} 的有效高度 / 长度。", {p0: id});
    if (Object.hasOwn(values, 'base_m') && !Number.isFinite(values.base_m)) return trEngineering("请填写实体 {p0} 的有效底部标高。", {p0: id});
  }
  return '';
}

export function responseError(payload, status) {
  const detail = payload.detail;
  const message = typeof payload.error === 'string' ? payload.error : payload.error?.message || (typeof detail === 'string' ? detail : detail?.message) || payload.message || trEngineering("请求失败（HTTP {p0}）。", {p0: status});
  const error = new Error(message);
  error.status = status;
  error.report = Array.isArray(detail?.report) ? detail.report : null;
  return error;
}

export function defaultConfig(document, mode = 'building', example = false) {
  const roles = mode === 'building' ? ['wall', 'column', 'slab'] : ['section'];
  const layers = Object.create(null);
  for (const layer of document.layers || []) layers[layer.name] = roles.includes(layer.suggested_role) ? layer.suggested_role : 'ignore';
  const parameters = Object.create(null);
  for (const role of ['wall', 'column', 'slab', 'section']) parameters[role] = { height_m: example ? ({ wall: 3, column: 3, slab: .12, section: 6 })[role] : null, base_m: 0 };
  return { mode, unit: example ? 'mm' : '', layers, parameters, overrides: {}, confirmed_solid: false, curve_tolerance_mm: .1, selection: { include_ids: null, exclude_ids: [], groups: [] }, dimension_bindings: [] };
}

export function configChanges(before, after, document = { entities: [] }) {
  const changes = [];
  const add = (label, oldValue, newValue, suffix = '') => {
    if (Object.is(oldValue, newValue)) return;
    const display = (value) => value === null || value === undefined || value === '' ? trEngineering("待填写") : `${value}${suffix}`;
    changes.push({ label, before: display(oldValue), after: display(newValue) });
  };
  add(trEngineering("图纸单位"), before.unit, after.unit);
  add(trEngineering("曲线误差"), before.curve_tolerance_mm ?? .1, after.curve_tolerance_mm ?? .1, ' mm');
  if (!sameConfig(selectionOf(before), selectionOf(after))) add(trEngineering("参与建模的实体"), selectedEntityIds(document, before).length, selectedEntityIds(document, after).length, trEngineering(" 个"));
  for (const layer of new Set([...Object.keys(before.layers || {}), ...Object.keys(after.layers || {})])) add(trEngineering("图层 {p0}", {p0: layer}), ROLES[before.layers?.[layer]] || trEngineering("忽略"), ROLES[after.layers?.[layer]] || trEngineering("忽略"));
  for (const role of ['wall', 'column', 'slab', 'section']) {
    add(`${ROLES[role]}${role === 'section' ? trEngineering("长度") : role === 'slab' ? trEngineering("厚度") : trEngineering("高度")}`, before.parameters?.[role]?.height_m, after.parameters?.[role]?.height_m, ' m');
    add(trEngineering("{p0}标高", {p0: ROLES[role]}), before.parameters?.[role]?.base_m, after.parameters?.[role]?.base_m, ' m');
  }
  for (const id of new Set([...Object.keys(before.overrides || {}), ...Object.keys(after.overrides || {})])) {
    if (sameConfig(before.overrides?.[id], after.overrides?.[id])) continue;
    const entity = document.entities?.find((item) => item.id === id);
    for (const [key, label] of [['height_m', trEngineering("高度 / 长度")], ['base_m', trEngineering("标高")]]) {
      const value = (cfg) => Object.hasOwn(cfg.overrides?.[id] || {}, key) ? cfg.overrides[id][key] : cfg.parameters?.[cfg.layers?.[entity?.layer]]?.[key];
      add(trEngineering("实体 {p0} {p1}", {p0: id, p1: label}), value(before), value(after), ' m');
    }
  }
  return changes;
}

export function drawingGeometry(entities) {
  const coordinates = [];
  const paths = [];
  for (const entity of entities || []) {
    const points = (entity.points || []).filter((point) => Array.isArray(point) && Number.isFinite(point[0]) && Number.isFinite(point[1]));
    if (points.length < 2) continue;
    for (const point of points) coordinates.push(point);
    const path = points.map(([x, y], index) => `${index ? 'L' : 'M'}${x} ${-y}`).join(' ') + (entity.status === 'ready' ? ' Z' : '');
    paths.push({ entity, path });
  }
  if (!coordinates.length) return { paths: [], viewBox: '0 0 100 100' };
  let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
  for (const [x, y] of coordinates) { minX = Math.min(minX, x); maxX = Math.max(maxX, x); minY = Math.min(minY, -y); maxY = Math.max(maxY, -y); }
  const span = Math.max(maxX - minX, maxY - minY, 1);
  const padding = span * .09;
  return { paths, viewBox: `${minX - padding} ${minY - padding} ${Math.max(maxX - minX, span * .01) + padding * 2} ${Math.max(maxY - minY, span * .01) + padding * 2}` };
}

export async function startCadApp(doc = document) {
  const $ = (id) => doc.getElementById(id);
  const all = (selector) => [...doc.querySelectorAll(selector)];
  const state = new CadState();
  const namespace = 'http://www.w3.org/2000/svg';
  let requestController = null;
  let viewer = null;
  let capability = null;
  let capabilityChecking = false;
  let modelViewError = false;
  let edgesVisible = true;
  let projectListSequence = 0;
  let projectsLoading = false;
  let projectListError = '';
  let projects = [];
  let analysisController = null, analysisSequence = 0, analysisTimer = null, analysisBusy = false;
  let drawingView = null, scanView = null, visibleGroup = null, diagnosticPoints = [], selectedDimensionId = null;
  let scanLayersSelected = new Set(), activeImportOperation = null, requestPhase = '';
  let sectionRun = null, sectionKey = null, sectionDocument = null, sectionProject = null;
  const queryProjectId = new URLSearchParams(window.location?.search || '').get('project_id');
  let pendingProjectId = /^[a-f0-9]{32}$/i.test(queryProjectId || '') ? queryProjectId : null;
  const parameterInputs = new Map();
  const layerInputs = new Map();
  const svgPaths = new Map();
  const node = (tag, text, className) => { const item = doc.createElement(tag); item.setAttribute('data-i18n-dynamic', ''); if (text !== undefined) item.textContent = text; if (className) item.className = className; return item; };
  const notice = (message, kind = '') => { $('notice').textContent = message; $('notice').className = `notice ${kind}`; };
  const numberValue = (input) => input.value.trim() === '' ? null : Number(input.value);
  const errorMessage = (error) => error.name === 'AbortError' ? trEngineering("操作已取消。") : error.message || trEngineering("连接失败，请检查服务是否运行后重试。");
  const serviceReady = () => capability?.available === true && !capabilityChecking;

  for (const role of ['wall', 'column', 'slab', 'section']) {
    const row = node('div', undefined, 'parameter-row');
    row.dataset.role = role;
    row.append(node('span', ROLES[role]));
    const height = node('input'); height.type = 'number'; height.step = 'any'; height.min = '0.000001'; height.placeholder = trEngineering("待填写"); height.setAttribute('aria-label', trEngineering("{p0}{p1}，米", {p0: ROLES[role], p1: role === 'section' ? trEngineering("拉伸长度") : role === 'slab' ? trEngineering("厚度") : trEngineering("高度")}));
    const base = node('input'); base.type = 'number'; base.step = 'any'; base.value = '0'; base.setAttribute('aria-label', trEngineering("{p0}底部标高，米", {p0: ROLES[role]}));
    row.append(height, base); $('parameterRows').append(row); parameterInputs.set(role, { row, height, base });
    for (const input of [height, base]) input.addEventListener('input', edited);
  }

  function readConfig() {
    const config = state.config ? clone(state.config) : defaultConfig({ layers: [] });
    config.unit = $('drawingUnit').value;
    config.curve_tolerance_mm = numberValue($('curveTolerance'));
    config.confirmed_solid = $('solidConfirmed').checked;
    config.layers = Object.fromEntries([...layerInputs].map(([name, input]) => [name, input.value]));
    config.parameters = Object.fromEntries([...parameterInputs].map(([role, inputs]) => [role, { height_m: numberValue(inputs.height), base_m: numberValue(inputs.base) }]));
    return state.config ? detachDimensionBindings(config, state.config) : config;
  }

  function setForm(config) {
    state.config = clone(config);
    $('drawingUnit').value = config.unit || '';
    $('curveTolerance').value = config.curve_tolerance_mm ?? .1;
    $('solidConfirmed').checked = !!config.confirmed_solid;
    for (const [role, fields] of parameterInputs) {
      fields.height.value = config.parameters?.[role]?.height_m ?? '';
      fields.base.value = config.parameters?.[role]?.base_m ?? 0;
    }
    $('modeBuilding').setAttribute('aria-pressed', String(config.mode === 'building'));
    $('modeSection').setAttribute('aria-pressed', String(config.mode === 'section'));
    $('modeHint').textContent = config.mode === 'building' ? trEngineering("墙、柱、板分别拉伸；请选实体占据的区域，不要把房间边界当成实心墙。") : trEngineering("截面沿长度方向拉伸，同层内圈保留为贯通孔；长度由你指定。");
    $('heightHeading').textContent = config.mode === 'section' ? trEngineering("拉伸长度") : trEngineering("高度 / 厚度");
    $('commandHelp').textContent = config.mode === 'section' ? trEngineering("例如：把拉伸长度改为 6 米；把选中构件长度改成 2 米。") : trEngineering("例如：把墙高改成 3.6 米；把柱子标高改为 0.2 米。");
    $('commandInput').placeholder = config.mode === 'section' ? trEngineering("把拉伸长度改为 6 米") : trEngineering("把墙高改成 3.6 米");
    renderLayers(); updateParameterRows(); renderDrawing(); renderSelectionGroups(); renderDimensions(); refresh(); scheduleAnalysis();
  }

  function renderLayers() {
    layerInputs.clear(); $('layerList').replaceChildren();
    for (const layer of state.document?.layers || []) {
      const row = node('div', undefined, 'layer-row');
      const role = state.config.layers[layer.name] || 'ignore';
      const name = node('span', layer.name, 'layer-name'); name.style.setProperty('--layer-color', COLORS[role] || COLORS.ignore);
      row.append(name, node('span', String(layer.entity_count), 'layer-count'));
      const input = node('select'); input.setAttribute('aria-label', trEngineering("{p0} 图层用途", {p0: layer.name}));
      for (const key of state.config.mode === 'section' ? ['ignore', 'section'] : ['ignore', 'wall', 'column', 'slab']) {
        const option = node('option', ROLES[key]); option.value = key; input.append(option);
      }
      input.value = role;
      input.addEventListener('change', () => { edited(); updateParameterRows(); name.style.setProperty('--layer-color', COLORS[input.value]); renderDrawing(); });
      row.append(input); $('layerList').append(row); layerInputs.set(layer.name, input);
    }
    if (!layerInputs.size) $('layerList').append(node('p', trEngineering("这份图纸没有可映射的图层。"), 'hint'));
  }

  function updateParameterRows() {
    const mode = state.config?.mode || 'building';
    for (const [role, { row }] of parameterInputs) row.hidden = mode === 'section' ? role !== 'section' : role === 'section';
  }

  function edited() {
    if (!state.document) return;
    state.config = readConfig();
    if (state.model && state.dirty) notice(trEngineering("参数已修改，当前仍显示上次生成的模型。请生成或应用修改后再导出。"), 'warn');
    refresh(); renderInspector(); renderDimensions(); scheduleAnalysis();
  }

  function refresh() {
    const hasDoc = !!state.document;
    const ready = serviceReady();
    $('cadFile').disabled = !ready;
    $('dropzone').setAttribute('aria-disabled', String(!ready));
    for (const button of all('[data-example]')) button.disabled = !ready;
    $('retryService').disabled = capabilityChecking || state.busy;
    $('accessSubmit').disabled = capabilityChecking || state.busy;
    $('configFields').disabled = !hasDoc || state.busy;
    $('buildModel').disabled = !hasDoc || state.busy || !ready;
    $('buildModel').textContent = state.busy ? trEngineering("处理中…") : state.model ? trEngineering("更新三维模型 ↗") : trEngineering("生成三维模型 ↗");
    $('commandInput').disabled = !hasDoc || state.busy || !ready;
    $('sendCommand').disabled = !hasDoc || state.busy || !ready;
    const canDiscardDraft = !!state.model && state.dirty;
    $('undoChange').disabled = !(canDiscardDraft || state.history.length) || state.busy || (!ready && !canDiscardDraft);
    $('undoChange').title = canDiscardDraft ? trEngineering("放弃未应用参数，恢复当前模型的参数") : trEngineering("撤销上一次已生成的修改");
    $('fitView').disabled = !viewer || !state.model?.objects?.length;
    $('toggleEdges').disabled = $('fitView').disabled;
    $('exportConfirmation').disabled = (!state.model && !state.project) || state.busy;
    const confirmed = CONFIRMATIONS.includes($('exportConfirmation').value);
    for (const button of all('[data-export]')) button.disabled = !state.canExport || !confirmed || !ready || (button.dataset.export === 'step' && !capability?.step_available);
    $('stepHint').textContent = capability?.step_available ? trEngineering("STEP 使用与预览相同的离散轮廓，不包含完整参数历史。") : trEngineering("STEP 导出需安装可选内核：{p0}。采用与预览相同的离散轮廓，不包含完整参数历史。", {p0: capability?.step_install_command || trEngineering("安装项目 CAD STEP 依赖后重新检测")});
    $('exportHint').textContent = !state.model ? trEngineering("生成模型后可导出") : !ready ? trEngineering("连接恢复后可导出") : state.dirty ? trEngineering("参数尚未应用，导出已暂停") : !state.model.objects?.length ? trEngineering("没有可导出的有效构件") : !confirmed ? trEngineering("完整输入签认提示后可下载") : trEngineering("模型及参数与当前预览一致");
    $('modelBadge').hidden = !state.model;
    $('modelBadge').className = `model-badge${state.dirty ? ' stale' : ''}`;
    $('modelBadge').textContent = state.dirty ? trEngineering("旧模型 · 参数未应用") : trEngineering("{p0} 个几何构件 · 米", {p0: state.model?.objects?.length || 0});
    $('modelEmpty').hidden = !!state.model?.objects?.length || modelViewError;
    $('fileMeta').hidden = !hasDoc;
    const picked = state.pickedIds.length > 0;
    for (const id of ['includePicked', 'addPicked', 'excludePicked', 'saveSelectionGroup']) $(id).disabled = !hasDoc || !picked || state.busy;
    $('resetSelection').disabled = !hasDoc || state.busy;
    $('undoSelection').disabled = !state.selectionHistory.length || state.busy;
    $('analyzeDrawing').disabled = !hasDoc || state.busy || analysisBusy || !ready;
    if (sectionDocument !== state.documentId) { sectionRun = null; sectionProject = null; sectionKey = null; sectionDocument = state.documentId; $('sectionRecord').hidden = true; }
    const sectionCurrent = !!sectionRun && sameConfig(sectionKey, analysisKey(state.config));
    $('calculateSection').disabled = !hasDoc || state.busy || !ready || state.config?.mode !== 'section' || !state.config?.unit || !state.config?.confirmed_solid;
    $('saveSection').disabled = !sectionCurrent || state.busy;
    if (sectionCurrent) renderSectionResult(sectionRun);
    else $('sectionProperties').replaceChildren(node('p', sectionRun ? trEngineering("选集或单位已修改，请重新计算截面性质。已保存的历史记录保留。") : trEngineering("确认截面模式、单位和实体材料后，可计算面积、形心和惯性矩；无需拉伸长度。"), 'hint'));
    $('pickVisible').disabled = !hasDoc || state.busy; $('fitDrawing').disabled = !hasDoc;
    $('drawingInteraction').disabled = !hasDoc || state.busy;
    for (const id of ['showSelectionGroup', 'useSelectionGroup', 'deleteSelectionGroup']) $(id).disabled = !$('selectionGroups').value || state.busy;
    $('bindDimension').disabled = !selectedDimensionId || state.busy || !ready || !state.document?.dimensions?.find((item) => item.id === selectedDimensionId)?.bindable;
    for (const id of ['dimensionValueSource', 'dimensionTarget', 'dimensionParameter']) $(id).disabled = !hasDoc || state.busy;
    $('selectionSummary').textContent = hasDoc ? trEngineering("参与建模 {p0} / {p1} 个轮廓或实体；排除 {p2} 个。", {p0: selectedEntityIds(state.document, state.config).length, p1: state.document.entities.length, p2: selectionOf(state.config).exclude_ids.length}) : trEngineering("图层映射决定用途，实体选集决定范围。");
    $('pickedSummary').textContent = picked ? trEngineering("已点选 {p0} 个：{p1}{p2}", {p0: state.pickedIds.length, p1: state.pickedIds.slice(0, 5).join('、'), p2: state.pickedIds.length > 5 ? '…' : ''}) : trEngineering("尚未点选实体");
    $('operationProgress').hidden = !state.busy;
    $('operationStage').textContent = requestPhase || trEngineering("正在处理，请稍候");
    $('scanPanel').hidden = !state.scan;
    $('importScanSelection').disabled = !state.scan || !scanLayersSelected.size || state.busy || !ready;
    for (const id of ['scanSelectAll', 'scanSelectNone', 'scanLayerFilter', 'resetScanBounds', 'scanMinX', 'scanMinY', 'scanMaxX', 'scanMaxY', 'scanInteraction', 'fitScan']) $(id).disabled = state.busy;
    $('projectName').disabled = !hasDoc || state.busy;
    $('saveDraft').disabled = !hasDoc || state.busy || !ready;
    $('saveCopy').disabled = !hasDoc || state.busy || !ready;
    $('saveVersion').disabled = !state.canExport || !ready;
    $('projectPackage').disabled = !ready || state.busy;
    $('recentProjects').disabled = !ready || state.busy || projectsLoading;
    $('refreshProjects').disabled = !ready || state.busy || projectsLoading;
    $('openProject').disabled = !ready || state.busy || !$('recentProjects').value;
    $('projectVersions').disabled = !ready || state.busy || !state.project?.versions?.length;
    $('openVersion').disabled = !ready || state.busy || !$('projectVersions').value;
    $('exportProject').disabled = !ready || state.busy || !state.project || state.projectDirty || !confirmed;
    const agentReady = !!state.project && !state.projectDirty && !state.busy;
    $('agentProjectLink').setAttribute('aria-disabled', String(!agentReady));
    $('agentProjectLink').href = agentReady ? `/static/index.html?cad_project_id=${encodeURIComponent(state.project.id)}` : '#';
    $('projectStatus').textContent = state.project ? trEngineering("{p0} · 修订 {p1}{p2}", {p0: state.project.name, p1: state.project.revision, p2: state.projectDirty ? trEngineering(" · 有未保存更改") : trEngineering(" · 已保存")}) : hasDoc ? trEngineering("新项目 · 尚未保存") : projectListError || trEngineering("尚未保存项目");
    doc.body.setAttribute('aria-busy', String(state.busy));
  }

  function renderDrawing() {
    svgPaths.clear(); $('drawingSvg').replaceChildren();
    const entities = state.analysis?.preview_entities || state.model?.preview_entities || state.document?.entities;
    const geometry = drawingGeometry(entities?.filter((entity) => !visibleGroup || visibleGroup.has(entity.id)));
    if (!drawingView) drawingView = geometry.viewBox.split(' ').map(Number);
    $('drawingSvg').setAttribute('viewBox', drawingView.join(' '));
    $('drawingEmpty').hidden = !!geometry.paths.length;
    $('entityCount').textContent = state.document ? trEngineering("{p0} 个轮廓 / 实体", {p0: state.document.entities?.length || 0}) : trEngineering("等待图纸");
    for (const contour of state.analysis?.contours || []) {
      if (visibleGroup && !visibleGroup.has(contour.outer_id)) continue;
      const fill = doc.createElementNS(namespace, 'path'); fill.setAttribute('d', materialPath(contour)); fill.setAttribute('fill-rule', 'evenodd'); fill.setAttribute('class', 'material-fill'); fill.setAttribute('aria-hidden', 'true'); fill.setAttribute('fill', COLORS[state.config.layers[contour.layer]] || COLORS.section); $('drawingSvg').append(fill);
    }
    const included = new Set(selectedEntityIds(state.document, state.config));
    for (const { entity, path } of geometry.paths) {
      const element = doc.createElementNS(namespace, 'path');
      element.setAttribute('d', path);
      const color = entity.status === 'ready' ? COLORS[state.config.layers[entity.layer] || 'ignore'] : '#ff9299';
      element.setAttribute('stroke', color); element.setAttribute('fill', color); element.setAttribute('tabindex', '0');
      element.setAttribute('role', 'button'); element.setAttribute('aria-label', trEngineering("实体 {p0}，图层 {p1}，{p2}", {p0: entity.id, p1: entity.layer, p2: entity.status === 'ready' ? trEngineering("可建模") : entity.reason || trEngineering("未支持")}));
      const title = doc.createElementNS(namespace, 'title'); title.textContent = `${entity.layer} · ${entity.id}${entity.reason ? ` · ${entity.reason}` : ''}`; element.append(title);
      element.classList.toggle('build-excluded', !included.has(entity.id));
      element.setAttribute('fill-opacity', '0');
      const choose = (event) => { if (state.busy || $('drawingInteraction').value === 'box' || $('drawingInteraction').value === 'pan') return; state.pickedIds = event?.shiftKey ? (state.pickedIds.includes(entity.id) ? state.pickedIds.filter((id) => id !== entity.id) : [...state.pickedIds, entity.id]) : [entity.id]; selectEntity(entity.id); };
      element.addEventListener('click', choose); element.addEventListener('keydown', (event) => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); choose(event); } });
      $('drawingSvg').append(element); svgPaths.set(entity.id, element);
    }
    for (const [x, y] of diagnosticPoints) { const marker = doc.createElementNS(namespace, 'circle'); marker.setAttribute('cx', x); marker.setAttribute('cy', -y); marker.setAttribute('r', Math.max(drawingView[2], drawingView[3]) * .007); marker.setAttribute('class', 'diagnostic-mark'); $('drawingSvg').append(marker); }
    highlightSelection();
  }

  function highlightSelection() {
    for (const [id, path] of svgPaths) { path.classList.toggle('selected', id === state.selectedId); path.classList.toggle('picked', state.pickedIds.includes(id)); }
  }

  function selectEntity(id) {
    const object = state.model?.objects?.find((item) => item.source_entity_ids?.includes(id));
    // A hole is a boundary of its owning solid, never an independently extrudable
    // component. Both numeric and language edits must address the shell handle.
    state.selectedId = object?.source_entity_ids?.[0] || id;
    viewer?.select(object?.id || null); highlightSelection(); renderInspector(); renderDimensionTargets(); refresh();
  }

  function setSelection(config) {
    if (!state.document || state.busy) return;
    state.selectionHistory.push({ selection: clone(selectionOf(state.config)), bindingConfig: clone(state.config) }); if (state.selectionHistory.length > 30) state.selectionHistory.shift();
    const orderedIds = (ids) => { const set = new Set(ids); return state.document.entities.filter((entity) => set.has(entity.id)).map((entity) => entity.id); };
    if (config.selection.include_ids !== null) config.selection.include_ids = orderedIds(config.selection.include_ids);
    config.selection.exclude_ids = orderedIds(config.selection.exclude_ids);
    config.selection.groups.forEach((group) => { group.entity_ids = orderedIds(group.entity_ids); });
    state.config = detachDimensionBindings(config, state.config); diagnosticPoints = []; renderDrawing(); renderSelectionGroups(); renderDimensions(); refresh(); renderInspector(); scheduleAnalysis();
    notice(trEngineering("建模选集已修改。先检查实体材料和孔洞，再生成更新后的模型。"), 'warn');
  }

  function renderSelectionGroups() {
    const value = $('selectionGroups').value; $('selectionGroups').replaceChildren();
    const placeholder = node('option', trEngineering("选择分组")); placeholder.value = ''; $('selectionGroups').append(placeholder);
    for (const [index, group] of (selectionOf(state.config).groups || []).entries()) { const option = node('option', trEngineering("{p0} · {p1} 个", {p0: group.name, p1: group.entity_ids.length})); option.value = String(index + 1); $('selectionGroups').append(option); }
    $('selectionGroups').value = Number(value) <= (selectionOf(state.config).groups || []).length ? value : '';
  }

  function currentGroup() { return selectionOf(state.config).groups?.[Number($('selectionGroups').value) - 1]; }

  function analysisKey(config) { return { unit: config?.unit, layers: config?.layers, selection: selectionOf(config), curve_tolerance_mm: config?.curve_tolerance_mm ?? .1, confirmed_solid: config?.confirmed_solid }; }

  function scheduleAnalysis() {
    clearTimeout(analysisTimer); analysisController?.abort(); analysisSequence += 1; analysisBusy = false;
    if (!state.document) return;
    if (state.analysisConfig && sameConfig(state.analysisConfig, analysisKey(state.config))) return;
    state.analysis = null; state.analysisConfig = null;
    $('analysisStatus').textContent = state.config.unit ? trEngineering("选集已变化，等待检查") : trEngineering("请先确认图纸单位");
    $('diagnosticList').replaceChildren(node('p', trEngineering("待检查当前选集。材料填充和诊断以本次检查结果为准。"), 'hint'));
    renderDrawing();
    const run = () => { analysisTimer = null; if (!state.document || !state.config.unit) return; if (state.busy) analysisTimer = setTimeout(run, 350); else analyze(); };
    if (state.config.unit) analysisTimer = setTimeout(run, 350);
  }

  async function analyze() {
    if (!state.document || state.busy || !serviceReady()) return;
    if (!state.config.unit) { $('analysisStatus').textContent = trEngineering("请先确认图纸单位"); return; }
    analysisController?.abort(); const controller = new AbortController(); analysisController = controller;
    const sequence = ++analysisSequence, documentId = state.documentId, key = analysisKey(state.config);
    analysisBusy = true; $('analysisStatus').textContent = trEngineering("正在检查连通性、重复边界和孔洞…"); refresh();
    try {
      const result = await jsonRequest('/api/cad/analyze', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ document_id: documentId, config: state.config }), signal: controller.signal });
      if (sequence !== analysisSequence || documentId !== state.documentId || !sameConfig(key, analysisKey(state.config))) return;
      state.analysis = result; state.analysisConfig = clone(key); diagnosticPoints = [];
      renderDiagnostics(); renderDrawing();
    } catch (error) { if (sequence === analysisSequence && error.name !== 'AbortError') { $('analysisStatus').textContent = trEngineering("检查未完成"); $('diagnosticList').replaceChildren(node('p', errorMessage(error), 'hint')); } }
    finally { if (sequence === analysisSequence) { analysisBusy = false; refresh(); } }
  }

  function renderDiagnostics() {
    const result = state.analysis; $('diagnosticList').replaceChildren();
    if (!result) { $('analysisStatus').textContent = trEngineering("确认单位与图层后检查"); return; }
    $('analysisStatus').textContent = trEngineering("{p0} 个材料区域 · {p1} 条诊断{p2}", {p0: result.contours?.length || 0, p1: result.diagnostics?.length || 0, p2: result.buildable === false ? trEngineering(" · 当前不可建模") : ''});
    for (const diagnostic of result.diagnostics || []) {
      const button = node('button', undefined, 'diagnostic-row'); button.type = 'button';
      const detail = node('span', diagnostic.message); detail.append(node('small', `${(diagnostic.entity_ids || []).join('、')}${Number.isFinite(diagnostic.gap_mm) ? trEngineering(" · 缺口 {p0} mm", {p0: diagnostic.gap_mm}) : ''}`));
      button.append(node('strong', diagnostic.severity === 'error' ? trEngineering("需处理") : trEngineering("注意"), diagnostic.severity || 'warning'), detail);
      button.addEventListener('click', () => { diagnosticPoints = diagnostic.points || []; state.pickedIds = diagnostic.entity_ids || []; visibleGroup = null; focusPoints(diagnosticPoints.length ? diagnosticPoints : (state.document.entities || []).filter((item) => state.pickedIds.includes(item.id)).flatMap((item) => item.points || [])); if (state.pickedIds.length) selectEntity(state.pickedIds[0]); renderDrawing(); refresh(); });
      $('diagnosticList').append(button);
    }
    if (!result.diagnostics?.length) $('diagnosticList').append(node('p', result.contours?.length ? trEngineering("轮廓检查通过。着色区域代表材料，内圈留白代表孔洞。高度 / 长度仍须单独确认。") : trEngineering("当前选集没有可生成的材料区域。请检查图层用途及实体选择。"), 'hint'));
  }

  function renderSectionResult(payload) {
      const host = $('sectionProperties'); host.replaceChildren();
      const n = (value) => Number.isFinite(value) ? Number(value.toPrecision(7)).toLocaleString(uiLocale(), { maximumSignificantDigits: 7 }) : trEngineering("无唯一方向");
      for (const row of payload.result.regions) {
        const card = node('div', undefined, 'section-property-card'); card.append(node('strong', trEngineering("{p0} · {p1} · {p2} 个孔洞", {p0: row.layer, p1: row.outer_id, p2: row.hole_ids.length})));
        const list = node('dl');
        for (const [label, value] of [[trEngineering("面积 A"), `${n(row.area_mm2)} mm²`], [trEngineering("形心（原图坐标）"), `${row.centroid_source.map(n).join('，')} ${payload.result.unit}`], [trEngineering("形心惯性矩 Ixx / Iyy"), `${n(row.Ixx_mm4)} / ${n(row.Iyy_mm4)} mm⁴`], [trEngineering("惯性积 Ixy"), `${n(row.Ixy_mm4)} mm⁴`], [trEngineering("主惯性矩 I11 / I22"), `${n(row.I11_mm4)} / ${n(row.I22_mm4)} mm⁴`], [trEngineering("主轴角"), row.principal_angle_deg === null ? trEngineering("无唯一方向") : `${n(row.principal_angle_deg)}°`], [trEngineering("回转半径 rx / ry"), `${n(row.rx_mm)} / ${n(row.ry_mm)} mm`]]) list.append(node('dt', label), node('dd', value));
        card.append(list); host.append(card);
      }
      host.append(node('p', `${payload.result.engine} ${payload.result.engine_version} · ${payload.result.notes.join(' ')}`, 'hint'));
  }

  async function calculateSection() {
    if (state.busy || !state.documentId) return;
    const config = readConfig(), request = startRequest(trEngineering("正在计算截面面积、形心与惯性矩")), documentId = state.documentId;
    const id = operationId(); activeImportOperation = id;
    try {
      const payload = await jsonRequest('/api/engineering/section', { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-CAD-Operation-ID': id }, body: JSON.stringify({ document_id: documentId, config }), signal: request.signal });
      if (!state.isCurrent(request.token) || documentId !== state.documentId) return;
      sectionRun = payload; sectionKey = analysisKey(config); sectionDocument = documentId;
      notice(trEngineering("截面性质计算完成。所有孔洞按当前选集保留；该计算不使用拉伸长度，也不推断构件尺寸。"));
    } catch (error) { if (state.isCurrent(request.token)) notice(errorMessage(error), 'error'); }
    finally { if (activeImportOperation === id) activeImportOperation = null; state.finish(request.token); if (state.isCurrent(request.token)) refresh(); }
  }

  async function saveSection() {
    if (!sectionRun || state.busy || !sameConfig(sectionKey, analysisKey(state.config))) return;
    const request = startRequest(trEngineering("正在保存截面计算记录"));
    const id = operationId(); activeImportOperation = id;
    const body = { run_id: sectionRun.run_id, name: trEngineering("{p0} · 截面性质", {p0: (state.projectName || state.document.filename || 'CAD').slice(0, 80)}) };
    if (sectionProject) { body.id = sectionProject.id; body.expected_revision = sectionProject.revision; }
    try {
      const payload = await jsonRequest('/api/engineering/projects', { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-CAD-Operation-ID': id }, body: JSON.stringify(body), signal: request.signal });
      if (!state.isCurrent(request.token)) return;
      sectionProject = payload.project; $('sectionRecord').href = `/engineering?project_id=${payload.project.id}`; $('sectionRecord').hidden = false;
      notice(trEngineering("截面计算记录已保存为版本 {p0}，可在工程计算页重开。", {p0: payload.version}));
    } catch (error) { if (state.isCurrent(request.token)) notice(errorMessage(error), 'error'); }
    finally { if (activeImportOperation === id) activeImportOperation = null; state.finish(request.token); if (state.isCurrent(request.token)) refresh(); }
  }

  function focusPoints(points) {
    const geometry = drawingGeometry([{ points, status: 'invalid' }]); drawingView = geometry.viewBox.split(' ').map(Number);
    if (points.length === 1) { const [x, y] = points[0]; const span = Math.max(drawingView[2], 10); drawingView = [x - span / 2, -y - span / 2, span, span]; }
    renderDrawing();
  }

  function pointInSvg(svg, event) {
    if (svg.createSVGPoint && svg.getScreenCTM?.()) { const p = svg.createSVGPoint(); p.x = event.clientX; p.y = event.clientY; const q = p.matrixTransform(svg.getScreenCTM().inverse()); return [q.x, q.y]; }
    const box = (svg.getAttribute?.('viewBox') || svg.attributes?.viewBox || '0 0 100 100').split(' ').map(Number), rect = svg.getBoundingClientRect?.() || { left: 0, top: 0, width: 100, height: 100 };
    const scale = Math.min(rect.width / box[2], rect.height / box[3]);
    return [box[0] + (event.clientX - rect.left - (rect.width - box[2] * scale) / 2) / scale, box[1] + (event.clientY - rect.top - (rect.height - box[3] * scale) / 2) / scale];
  }

  function wireRectangle(svg, mode, completed) {
    let start = null, rect = null, initialView = null;
    svg.addEventListener('pointerdown', (event) => {
      if (state.busy || event.button > 0 || mode() === 'pick') return;
      start = pointInSvg(svg, event); initialView = (svg.getAttribute?.('viewBox') || '0 0 100 100').split(' ').map(Number); svg.setPointerCapture?.(event.pointerId); event.preventDefault();
      if (mode() !== 'pan') { rect = doc.createElementNS(namespace, 'rect'); rect.setAttribute('class', 'selection-rectangle'); svg.append(rect); }
    });
    svg.addEventListener('pointermove', (event) => {
      if (!start) return; const point = pointInSvg(svg, event);
      if (mode() === 'pan' && initialView) { const current = (svg.getAttribute('viewBox')).split(' ').map(Number); const next = [current[0] + start[0] - point[0], current[1] + start[1] - point[1], initialView[2], initialView[3]]; if (svg === $('drawingSvg')) drawingView = next; else scanView = next; svg.setAttribute('viewBox', next.join(' ')); return; }
      rect?.setAttribute('x', Math.min(start[0], point[0])); rect?.setAttribute('y', Math.min(start[1], point[1])); rect?.setAttribute('width', Math.abs(point[0] - start[0])); rect?.setAttribute('height', Math.abs(point[1] - start[1]));
    });
    const finish = (event, cancelled = false) => { if (!start) return; const point = pointInSvg(svg, event), from = start; start = null; rect?.remove(); rect = null; if (!cancelled && mode() !== 'pan' && Math.abs(point[0] - from[0]) + Math.abs(point[1] - from[1]) > 0) completed([Math.min(from[0], point[0]), -Math.max(from[1], point[1]), Math.max(from[0], point[0]), -Math.min(from[1], point[1])], event.shiftKey); };
    svg.addEventListener('pointerup', (event) => finish(event)); svg.addEventListener('pointercancel', (event) => finish(event, true));
  }

  function renderDimensionTargets() {
    const previous = $('dimensionTarget').value; $('dimensionTarget').replaceChildren();
    const prompt = node('option', trEngineering("请选择用途对象")); prompt.value = ''; $('dimensionTarget').append(prompt);
    if (state.selectedId && state.document?.entities?.some((entity) => entity.id === state.selectedId && state.config.layers[entity.layer] !== 'ignore')) {
      const option = node('option', trEngineering("所选实体 {p0}", {p0: state.selectedId})); option.value = `entity:${state.selectedId}`; $('dimensionTarget').append(option);
    }
    for (const role of [...new Set(Object.values(state.config?.layers || {}))].filter((value) => value !== 'ignore')) {
      const option = node('option', trEngineering("所有{p0}", {p0: ROLES[role] || role})); option.value = `role:${role}`; $('dimensionTarget').append(option);
    }
    $('dimensionTarget').value = $('dimensionTarget').children && [...$('dimensionTarget').children].some((option) => option.value === previous) ? previous : '';
  }

  function renderDimensions() {
    const dimensions = state.document?.dimensions || []; $('dimensionList').replaceChildren();
    $('dimensionCount').textContent = trEngineering("{p0} 条原图标注", {p0: dimensions.length});
    if (!dimensions.some((item) => item.id === selectedDimensionId)) selectedDimensionId = null;
    for (const dimension of dimensions) {
      const button = node('button', `${dimension.id} · ${dimension.text || trEngineering("自动测量标注")} · ${dimension.layer}`, 'dimension-item'); button.type = 'button'; button.setAttribute('aria-pressed', String(dimension.id === selectedDimensionId));
      button.addEventListener('click', () => { if (state.busy) return; selectedDimensionId = dimension.id; diagnosticPoints = dimension.points || []; if (diagnosticPoints.length) focusPoints(diagnosticPoints); renderDimensions(); refresh(); });
      $('dimensionList').append(button);
    }
    if (!dimensions.length) $('dimensionList').append(node('p', trEngineering("未读取到 DIMENSION 标注。请在参数面板手工填写已确认尺寸。"), 'hint'));
    const dimension = dimensions.find((item) => item.id === selectedDimensionId);
    $('dimensionDetails').textContent = dimension ? trEngineering("原文：{p0}；标注数值：{p1} {p2}；几何测量：{p3} 图纸单位{p4}。{p5}", {p0: dimension.text || trEngineering("无覆盖文字"), p1: dimension.annotation_value ?? trEngineering("不可读取"), p2: dimension.annotation_unit || trEngineering("图纸单位"), p3: dimension.measurement ?? trEngineering("不可读取"), p4: dimension.axis ? trEngineering("；方向：{p0}", {p0: dimension.axis}) : '', p5: dimension.bindable ? trEngineering("请核对数值，并明确指定三维用途。") : trEngineering("该标注仅供参考，不能绑定。")}) : trEngineering("选择一条原图标注，核对原文和几何测量，再明确指定用途。");
    renderDimensionTargets(); $('dimensionBindings').replaceChildren();
    for (const binding of state.config?.dimension_bindings || []) {
      const row = node('div', undefined, 'dimension-binding');
      const value = binding.target_id ? state.config.overrides?.[binding.target_id]?.[binding.parameter] : state.config.parameters?.[binding.role]?.[binding.parameter];
      row.append(node('span', `${binding.dimension_id} → ${binding.target_id || ROLES[binding.role]} · ${binding.parameter === 'base_m' ? trEngineering("标高") : trEngineering("高度 / 长度")} ${value ?? trEngineering("未确认")} m（${binding.value_source === 'annotation' ? trEngineering("标注文字") : trEngineering("几何测量")}）`));
      const remove = node('button', trEngineering("解除绑定")); remove.type = 'button'; remove.disabled = state.busy;
      remove.addEventListener('click', () => { if (state.busy) return; state.config.dimension_bindings = state.config.dimension_bindings.filter((item) => item !== binding); renderDimensions(); refresh(); notice(trEngineering("已解除尺寸来源绑定；已填写参数保留，可继续手工修改。")); });
      row.append(remove); $('dimensionBindings').append(row);
    }
  }

  async function bindDimension() {
    if (!selectedDimensionId || state.busy || !serviceReady()) return;
    const target = $('dimensionTarget').value;
    if (!target) { notice(trEngineering("请明确选择尺寸要应用的实体或用途。"), 'warn'); return; }
    const binding = { dimension_id: selectedDimensionId, parameter: $('dimensionParameter').value, value_source: $('dimensionValueSource').value };
    binding[target.startsWith('entity:') ? 'target_id' : 'role'] = target.slice(target.indexOf(':') + 1);
    const before = readConfig(), { token, signal } = startRequest(trEngineering("正在核对标注来源并绑定指定参数"));
    try {
      const payload = await jsonRequest('/api/cad/bind-dimension', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ document_id: state.documentId, config: before, binding }), signal });
      if (!state.isCurrent(token)) return;
      setForm(payload.config); renderChanges(configChanges(before, payload.config, state.document), trEngineering("已绑定为草稿参数，生成模型后应用"));
      notice(trEngineering("已核对并绑定尺寸来源。请检查参数，再生成模型。"));
    } catch (error) { if (state.isCurrent(token)) notice(errorMessage(error), 'error'); }
    finally { state.finish(token); if (state.isCurrent(token)) { renderDimensions(); refresh(); } }
  }

  const scanBoundInputs = () => ['scanMinX', 'scanMinY', 'scanMaxX', 'scanMaxY'].map($);
  function scanBounds() {
    const fields = scanBoundInputs();
    if (fields.every((input) => input.value.trim() === '')) return null;
    const values = fields.map(numberValue);
    if (values.some((value) => !Number.isFinite(value)) || values[0] >= values[2] || values[1] >= values[3]) throw new Error(trEngineering("请填写完整有效的范围坐标，最小值须小于最大值；也可点击“不限范围”。"));
    return values;
  }
  function renderScanLayers() {
    $('scanLayers').replaceChildren(); const filter = $('scanLayerFilter').value.toLowerCase();
    for (const layer of state.scan?.index.layers || []) {
      if (!layer.name.toLowerCase().includes(filter)) continue;
      const row = node('label', undefined, 'scan-layer-row'); const input = node('input'); input.type = 'checkbox'; input.checked = scanLayersSelected.has(layer.name); input.disabled = state.busy;
      input.addEventListener('change', () => { if (state.busy) return; if (input.checked) scanLayersSelected.add(layer.name); else scanLayersSelected.delete(layer.name); renderScanPreview(); refresh(); });
      row.append(input, node('span', layer.name), node('small', String(layer.entity_count))); $('scanLayers').append(row);
    }
  }
  function renderScanPreview() {
    const svg = $('scanSvg'); svg.replaceChildren();
    const index = state.scan?.index; if (!index) return;
    const bounds = index.bounds;
    const geometry = drawingGeometry((index.preview || []).map((entity) => ({ ...entity, points: entity.points?.length ? entity.points : entity.bounds ? [[...entity.bounds.min], [entity.bounds.max[0], entity.bounds.min[1]], [...entity.bounds.max], [entity.bounds.min[0], entity.bounds.max[1]], [...entity.bounds.min]] : [], status: 'scan' })));
    const overall = bounds ? drawingGeometry([{ points: [bounds.min, bounds.max] }]).viewBox : geometry.viewBox;
    if (!scanView) scanView = overall.split(' ').map(Number); svg.setAttribute('viewBox', scanView.join(' '));
    for (const { entity, path } of geometry.paths) { const element = doc.createElementNS(namespace, 'path'); const selected = scanLayersSelected.has(entity.layer) || entity.layers?.some((layer) => scanLayersSelected.has(layer)); element.setAttribute('d', path); element.setAttribute('fill', 'none'); element.setAttribute('stroke', selected ? COLORS.section : COLORS.ignore); element.setAttribute('opacity', selected ? '.9' : '.25'); if (entity.block_overview) element.setAttribute('stroke-dasharray', '5 4'); element.setAttribute('vector-effect', 'non-scaling-stroke'); svg.append(element); }
    try { const region = scanBounds(); if (region) { const rectangle = doc.createElementNS(namespace, 'rect'); rectangle.setAttribute('class', 'selection-rectangle'); rectangle.setAttribute('x', region[0]); rectangle.setAttribute('y', -region[3]); rectangle.setAttribute('width', region[2] - region[0]); rectangle.setAttribute('height', region[3] - region[1]); svg.append(rectangle); } } catch { /* Incomplete bounds remain editable. */ }
    $('scanSummary').textContent = trEngineering("{p0} · {p1} 个图层 · {p2} 个实体 · {p3}", {p0: index.filename, p1: index.layers.length, p2: index.counts?.expanded ?? index.counts?.modelspace ?? 0, p3: index.preview_sampled ? trEngineering("抽样预览") : trEngineering("范围预览")});
    $('scanWarnings').textContent = (index.warnings || []).map((item) => typeof item === 'string' ? item : item.message || JSON.stringify(item)).join('；');
  }
  function operationId() { return globalThis.crypto?.randomUUID?.().replaceAll('-', '') || Array.from({ length: 32 }, () => Math.floor(Math.random() * 16).toString(16)).join(''); }
  async function scanFile(file) {
    const request = startRequest(trEngineering("第一阶段：上传并扫描图层、块引用与范围")); const id = operationId(); activeImportOperation = id;
    notice(trEngineering("正在扫描 {p0}。完成后可先选图层和范围，当前模型保留。", {p0: file.name}));
    try {
      const form = new FormData(); form.append('file', file);
      const payload = await jsonRequest('/api/cad/import/scan', { method: 'POST', headers: { 'X-CAD-Operation-ID': id }, body: form, signal: request.signal });
      if (!state.isCurrent(request.token)) return;
      state.scan = payload; scanView = null; scanLayersSelected = new Set(); $('scanLayerFilter').value = ''; scanBoundInputs().forEach((input) => { input.value = ''; });
      renderScanLayers(); renderScanPreview(); notice(trEngineering("扫描完成。请先勾选需要的图层，并按需框选完整实体所在范围。"));
    } catch (error) { if (state.isCurrent(request.token)) notice(trEngineering("{p0} 当前图纸与模型保留。", {p0: errorMessage(error)}), 'error'); }
    finally { if (activeImportOperation === id) activeImportOperation = null; state.finish(request.token); if (state.isCurrent(request.token)) { renderScanLayers(); refresh(); } }
  }
  async function importScanSelection() {
    if (!state.scan || !scanLayersSelected.size || state.busy || !serviceReady()) return;
    let bounds; try { bounds = scanBounds(); } catch (error) { notice(error.message, 'warn'); return; }
    const body = { import_id: state.scan.import_id, layers: [...scanLayersSelected] }; if (bounds) body.bounds = bounds;
    const request = startRequest(trEngineering("第二阶段：读取所选实体并检查建模轮廓")); const id = operationId(); activeImportOperation = id;
    try {
      const payload = await jsonRequest('/api/cad/import/select', { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-CAD-Operation-ID': id }, body: JSON.stringify(body), signal: request.signal });
      acceptDocument(request.token, payload);
    } catch (error) { if (state.isCurrent(request.token)) notice(trEngineering("{p0} 当前图纸与模型保留，可缩小范围后重试。", {p0: errorMessage(error)}), 'error'); }
    finally { if (activeImportOperation === id) activeImportOperation = null; state.finish(request.token); if (state.isCurrent(request.token)) { renderScanLayers(); refresh(); } }
  }

  async function cancelOperation() {
    if (!state.busy) return;
    const operation = activeImportOperation, controller = requestController;
    state.sequence += 1; state.busy = false; activeImportOperation = null;
    refresh(); renderInspector(); notice(trEngineering("操作已取消，当前图纸、草稿和已生成模型保留。"));
    // Publish cancellation to the server before aborting the upload/selection request.
    if (operation) {
      const timeout = new AbortController(), timer = setTimeout(() => timeout.abort(), 3000);
      try { await serviceFetch(`/api/cad/import/${operation}/cancel`, { method: 'POST', signal: timeout.signal }); } catch { /* Local ownership is already invalidated. */ } finally { clearTimeout(timer); controller?.abort(); }
    } else controller?.abort();
  }

  function renderInspector() {
    const host = $('objectInspector'); host.replaceChildren();
    const source = state.document?.entities?.find((entity) => entity.id === state.selectedId);
    if (!source) { host.append(node('p', trEngineering("选择一个二维轮廓或三维构件，查看原始图层、实体编号与建模尺寸。"), 'hint')); return; }
    const object = state.model?.objects?.find((item) => item.source_entity_ids?.includes(source.id));
    const list = node('dl');
    const add = (label, value) => list.append(node('dt', label), node('dd', value));
    add(trEngineering("原图实体"), source.id); add(trEngineering("来源图层"), source.layer); add(trEngineering("实体类型"), source.type);
    const blockPaths = [...new Set((source.source_entities || []).filter((item) => item.insert_path?.length).map((item) => item.insert_path.map((block) => `${block.block} (${block.handle})`).join(' → ')))];
    if (blockPaths.length) add(trEngineering("来源块引用"), blockPaths.join('；'));
    add(trEngineering("用途"), ROLES[state.config.layers[source.layer]] || trEngineering("忽略"));
    if (object) {
      add(trEngineering("模型编号"), object.id); add(trEngineering("关联图元"), object.source_entity_ids.join(', '));
      add(object.role === 'section' ? trEngineering("拉伸长度") : object.role === 'slab' ? trEngineering("厚度") : trEngineering("高度"), `${object.parameters.height_m} m`);
      add(trEngineering("底部标高"), `${object.parameters.base_m} m`);
      if (Number.isFinite(object.volume_m3)) add(trEngineering("几何体积"), `${Number(object.volume_m3.toPrecision(7))} m³`);
      if (state.dirty) add(trEngineering("应用状态"), trEngineering("显示上次生成值，当前参数尚未应用"));
    } else add(trEngineering("状态"), source.reason || trEngineering("尚未生成此构件"));
    host.append(list);
    // An inner loop belongs to its shell. Only the shell can receive an object override.
    if (object && object.source_entity_ids[0] === source.id && state.config.layers[source.layer] === object.role) {
      const form = node('form', undefined, 'object-edit');
      const draftParameters = { ...state.config.parameters[object.role], ...state.config.overrides?.[source.id] };
      const heightLabel = node('label', object.role === 'section' ? trEngineering("长度（米）") : trEngineering("高度 / 厚度（米）"));
      const height = node('input'); height.type = 'number'; height.step = 'any'; height.min = '.000001'; height.required = true; height.value = draftParameters.height_m ?? ''; height.disabled = state.busy || !serviceReady(); heightLabel.append(height);
      const baseLabel = node('label', trEngineering("标高（米）")); const base = node('input'); base.type = 'number'; base.step = 'any'; base.required = true; base.value = draftParameters.base_m ?? ''; base.disabled = height.disabled; baseLabel.append(base);
      const apply = node('button', trEngineering("应用")); apply.type = 'submit'; apply.disabled = height.disabled;
      form.append(heightLabel, baseLabel, apply);
      for (const input of [height, base]) input.addEventListener('input', () => {
        if (state.busy || !serviceReady()) return;
        const next = readConfig(); next.overrides = { ...next.overrides, [source.id]: { height_m: numberValue(height), base_m: numberValue(base) } }; state.config = detachDimensionBindings(next, state.config);
        notice(trEngineering("构件参数已修改，点击应用后更新模型。当前模型仍是上次生成值。"), 'warn'); renderDimensions(); refresh();
      });
      form.addEventListener('submit', (event) => { event.preventDefault(); if (state.busy || !serviceReady()) return; const next = readConfig(); next.overrides = { ...next.overrides, [source.id]: { height_m: numberValue(height), base_m: numberValue(base) } }; setForm(next); build(next); });
      host.append(form);
    }
  }

  function renderReport(latestReport = null) {
    $('reportList').replaceChildren();
    const report = latestReport || state.model?.report || [];
    renderReport.last = report;
    const counts = { modeled: 0, ignored: 0, failed: 0 };
    for (const item of report) {
      const status = Object.hasOwn(counts, item.status) ? item.status : 'failed'; counts[status] += 1;
      const row = node('div', undefined, 'report-row'); row.append(node('span', ({ modeled: trEngineering("已处理"), ignored: trEngineering("已忽略"), failed: trEngineering("失败") })[status], `status-${status}`));
      const details = node('div'); details.append(node('strong', `${item.layer || '—'} · ${item.id || '—'}`), node('p', item.reason || (status === 'modeled' ? trEngineering("已进入几何模型") : ''))); row.append(details); $('reportList').append(row);
    }
    $('reportSummary').textContent = report.length ? trEngineering("{p0} 已处理 · {p1} 忽略 · {p2} 失败", {p0: counts.modeled, p1: counts.ignored, p2: counts.failed}) : trEngineering("尚未建模");
    if (!report.length) $('reportList').append(node('p', trEngineering("成功、忽略和失败的实体会分别列出，孔洞也会保留处理记录。"), 'hint'));
    return counts;
  }

  async function serviceFetch(url, options = {}) {
    const response = await fetch(url, options);
    if (response.status === 401) {
      capability = null;
      $('accessPanel').hidden = false;
      $('retryService').hidden = false;
      $('serviceStatus').textContent = trEngineering("需要访问口令");
      $('serviceStatus').className = 'service-status offline';
      refresh(); renderInspector();
    }
    return response;
  }

  async function jsonRequest(url, options = {}) {
    const response = await serviceFetch(url, options);
    const contentType = response.headers.get('content-type') || '';
    if (!contentType.includes('json')) throw new Error(response.ok ? trEngineering("服务返回了无法识别的数据。") : trEngineering("建模服务请求失败（HTTP {p0}）。", {p0: response.status}));
    const payload = await response.json();
    if (!response.ok || payload.ok === false) throw responseError(payload, response.status);
    return payload;
  }

  function startRequest(phase = trEngineering("正在处理，请稍候")) {
    clearTimeout(analysisTimer); analysisController?.abort(); analysisSequence += 1; analysisBusy = false; requestPhase = phase;
    requestController?.abort(); requestController = new AbortController();
    const token = state.begin(); refresh(); renderInspector();
    return { token, signal: requestController.signal };
  }

  function renderVersions() {
    const select = $('projectVersions'); select.replaceChildren();
    const placeholder = node('option', state.project?.versions?.length ? trEngineering("选择已保存的模型版本") : trEngineering("尚无已保存版本")); placeholder.value = ''; select.append(placeholder); select.value = '';
    for (const version of [...(state.project?.versions || [])].reverse()) {
      const option = node('option', trEngineering("版本 {p0} · {p1} 个构件 · {p2}", {p0: version.version, p1: version.objects, p2: version.created_at})); option.value = String(version.version); select.append(option);
    }
  }

  async function refreshProjects() {
    if (!serviceReady()) return;
    const sequence = ++projectListSequence; projectsLoading = true; projectListError = ''; refresh();
    try {
      const payload = await jsonRequest('/api/cad/projects', { cache: 'no-store' });
      if (sequence !== projectListSequence) return;
      projects = payload.projects || [];
      const selected = $('recentProjects').value;
      $('recentProjects').replaceChildren();
      const placeholder = node('option', projects.length ? trEngineering("选择一个已保存项目") : trEngineering("还没有已保存项目")); placeholder.value = ''; $('recentProjects').append(placeholder);
      for (const project of projects) { const option = node('option', trEngineering("{p0} · 修订 {p1}", {p0: project.name, p1: project.revision})); option.value = project.id; $('recentProjects').append(option); }
      $('recentProjects').value = projects.some((project) => project.id === selected) ? selected : '';
    } catch (error) {
      if (sequence === projectListSequence) { projectListError = trEngineering("项目列表读取失败：{p0}", {p0: errorMessage(error)}); const option = node('option', projectListError); option.value = ''; $('recentProjects').replaceChildren(option); $('recentProjects').value = ''; }
    } finally { if (sequence === projectListSequence) { projectsLoading = false; refresh(); } }
  }

  function showDocumentDetails() {
    $('filename').textContent = state.document.filename;
    const sourceCount = state.document.layers.reduce((sum, layer) => sum + layer.entity_count, 0);
    $('fileDetails').textContent = trEngineering("{p0} 个图层 · {p1} 个来源实体", {p0: state.document.layers.length, p1: sourceCount});
    const unit = state.document.units;
    $('unitHint').textContent = unit?.meters_per_unit ? trEngineering("原图单位标注：{p0}。请选择并确认，避免尺寸缩放错误。", {p0: unit.name}) : trEngineering("原图未提供可确认的单位，请根据实际图纸选择。");
  }

  function showProject(token, payload, historical = false) {
    if (!state.setProject(token, payload, historical)) return false;
    drawingView = null; visibleGroup = null; diagnosticPoints = []; selectedDimensionId = null;
    $('cadFile').value = ''; $('projectPackage').value = ''; $('projectName').value = state.project.name;
    $('exportConfirmation').value = ''; $('commandInput').value = ''; $('exampleHint').hidden = true;
    $('commandResult').textContent = trEngineering("项目已载入。修改只作用于图层选择和建模参数，原图保持不变。");
    renderChanges([], ''); showDocumentDetails(); setForm(state.config); renderVersions();
    try { if (state.model) viewer?.setModel(state.model, true); else viewer?.clear(); }
    catch (error) { showViewerError(trEngineering("三维显示失败：{p0}。模型数据仍可导出。", {p0: errorMessage(error)})); }
    renderReport(); renderInspector(); refresh();
    return true;
  }

  async function openProject(projectId, version = null) {
    if (!projectId || state.busy || !serviceReady()) return;
    const { token, signal } = startRequest(); notice(version ? trEngineering("正在载入模型版本 {p0}…", {p0: version}) : trEngineering("正在恢复项目原图、草稿与模型…"));
    try {
      const query = version ? `?version=${encodeURIComponent(version)}` : '';
      const payload = await jsonRequest(`/api/cad/projects/${encodeURIComponent(projectId)}${query}`, { signal, cache: 'no-store' });
      if (!showProject(token, payload, !!version)) return;
      notice(version ? trEngineering("已载入版本 {p0}。保存后才会写入项目，现有历史版本保持不变。", {p0: version}) : trEngineering("已恢复项目“{p0}”。{p1}导出时需要重新填写签认提示。", {p0: payload.project.name, p1: state.dirty && state.model ? trEngineering("草稿与上次生成模型分别保留，请应用参数后再导出模型。") : trEngineering("可以继续修改参数。")}));
    } catch (error) { if (state.isCurrent(token)) notice(trEngineering("{p0} 当前图纸与模型保持不变。", {p0: errorMessage(error)}), 'error'); }
    finally { state.finish(token); if (state.isCurrent(token)) { refresh(); renderInspector(); } }
  }

  async function saveProject(includeModel, newCopy = false) {
    if (!state.document || state.busy || !serviceReady() || (includeModel && !state.canExport)) return;
    const name = $('projectName').value.trim();
    if (!name) { notice(trEngineering("请填写项目名称后保存。"), 'warn'); return; }
    const config = readConfig(); state.config = clone(config); state.projectName = name;
    const body = { name, document_id: state.documentId, config };
    if (state.project && !newCopy) { body.project_id = state.project.id; body.expected_revision = state.project.revision; }
    if (includeModel) body.model_id = state.modelId;
    const { token, signal } = startRequest(); notice(includeModel ? trEngineering("正在保存模型版本与原图…") : trEngineering("正在保存项目草稿…"));
    try {
      const payload = await jsonRequest('/api/cad/projects', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body), signal });
      if (!state.isCurrent(token)) return;
      state.project = clone(payload.project); state.savedConfig = clone(config); state.savedName = name;
      if (newCopy) state.savedModelId = null;
      if (includeModel) state.savedModelId = state.modelId;
      renderVersions();
      notice(includeModel ? trEngineering("模型版本已保存。原图、参数和处理报告可在重启后恢复。") : state.modelId !== state.savedModelId ? trEngineering("草稿已保存。当前模型尚未归档，请点击“保存模型版本”保留本次几何结果。") : trEngineering("草稿已保存，上次生成的模型版本保持不变。"));
      await refreshProjects();
    } catch (error) {
      if (state.isCurrent(token)) notice(error.status === 409 ? trEngineering("项目已被其他页面修改，本次未覆盖。当前草稿仍在，请另存为新项目，或打开最新项目后重新修改。") : trEngineering("{p0} 当前草稿仍保留，可重试保存。", {p0: errorMessage(error)}), 'error');
    } finally { state.finish(token); if (state.isCurrent(token)) { refresh(); renderInspector(); scheduleAnalysis(); } }
  }

  async function importProject(file) {
    if (!file || state.busy || !serviceReady()) return;
    if (!/\.zip$/i.test(file.name)) { notice(trEngineering("请选择 CAD 项目 ZIP 包。"), 'warn'); return; }
    const maxBundle = capability?.max_project_bundle_bytes || 16 * 1024 * 1024;
    if (file.size > maxBundle) { notice(trEngineering("项目包超出 {p0} MiB 上传上限。", {p0: Math.round(maxBundle / 1024 / 1024)}), 'warn'); return; }
    const { token, signal } = startRequest(); notice(trEngineering("正在检查项目包并创建独立副本…"));
    try {
      const form = new FormData(); form.append('file', file);
      const payload = await jsonRequest('/api/cad/projects/import', { method: 'POST', body: form, signal });
      if (!showProject(token, payload)) return;
      notice(trEngineering("已导入为新项目“{p0}”。原项目未覆盖；导出签认需要重新输入。", {p0: payload.project.name}));
      await refreshProjects();
    } catch (error) { if (state.isCurrent(token)) notice(trEngineering("{p0} 当前图纸与模型保持不变。", {p0: errorMessage(error)}), 'error'); }
    finally { state.finish(token); if (state.isCurrent(token)) { $('projectPackage').value = ''; refresh(); renderInspector(); } }
  }

  function download(blob, filename) {
    const url = URL.createObjectURL(blob); const link = node('a'); link.href = url; link.download = filename;
    doc.body.append(link); link.click(); link.remove(); setTimeout(() => URL.revokeObjectURL(url), 10000);
  }

  async function exportProject() {
    if (!state.project || state.projectDirty || state.busy || !serviceReady() || !CONFIRMATIONS.includes($('exportConfirmation').value)) return;
    const { token, signal } = startRequest(); notice(trEngineering("正在打包原图、草稿和模型版本…"));
    try {
      const response = await serviceFetch(`/api/cad/projects/${encodeURIComponent(state.project.id)}/export`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ confirmation: $('exportConfirmation').value, expected_revision: state.project.revision }), signal });
      if (!response.ok) throw responseError(await response.json().catch(() => ({})), response.status);
      const blob = await response.blob(); if (!state.isCurrent(token)) return;
      download(blob, `${state.project.name.replace(/[\\/:*?"<>|]/g, '_')}.cad-project.zip`);
      notice(trEngineering("项目包已准备下载。同事导入后会创建独立副本，并重新确认导出签认。"));
    } catch (error) { if (state.isCurrent(token)) notice(error.status === 409 ? trEngineering("项目已有更新，本次未下载旧画面之外的版本。请打开最新项目，核对后重新导出。") : errorMessage(error), 'error'); }
    finally { state.finish(token); if (state.isCurrent(token)) { refresh(); renderInspector(); } }
  }

  function renderChanges(changes, status) {
    $('commandDiff').hidden = !changes.length; $('commandDiffRows').replaceChildren(); $('commandDiffStatus').textContent = status;
    for (const change of changes) { const row = node('div', undefined, 'command-diff-row'); row.append(node('span', change.label), node('span', change.before, 'before'), node('span', '→'), node('span', change.after, 'after')); $('commandDiffRows').append(row); }
  }

  function clear() {
    if (activeImportOperation) cancelOperation();
    clearTimeout(analysisTimer); analysisController?.abort(); analysisSequence += 1; analysisBusy = false;
    requestController?.abort(); state.reset(); viewer?.clear();
    drawingView = null; visibleGroup = null; diagnosticPoints = []; selectedDimensionId = null;
    $('cadFile').value = ''; $('drawingSvg').replaceChildren(); $('drawingEmpty').hidden = false; $('entityCount').textContent = trEngineering("等待图纸");
    $('exampleHint').hidden = true; $('exportConfirmation').value = ''; $('commandInput').value = '';
    $('projectName').value = ''; $('projectPackage').value = ''; renderVersions(); renderChanges([], '');
    $('commandResult').textContent = trEngineering("修改只作用于图层选择、高度、长度和标高；平面轮廓来自原图。");
    layerInputs.clear(); $('layerList').replaceChildren(node('p', trEngineering("上传后显示图层"), 'hint')); svgPaths.clear();
    for (const fields of parameterInputs.values()) { fields.height.value = ''; fields.base.value = '0'; }
    $('drawingUnit').value = ''; $('curveTolerance').value = '.1'; $('solidConfirmed').checked = false;
    renderReport(); renderInspector(); renderDimensions(); renderSelectionGroups(); renderDiagnostics(); refresh(); notice(trEngineering("先上传 DXF，或选择一个合成样例体验完整流程。"));
  }

  function acceptDocument(token, payload, exampleMode = null) {
    if (!state.isCurrent(token)) return false;
    const config = defaultConfig(payload.document, exampleMode || 'building', !!exampleMode);
    // Replace the old document only after the new source has passed import.
    state.model = null; state.modelId = null; state.appliedConfig = null; state.history = []; state.project = null; state.savedConfig = null; state.savedModelId = null; state.savedName = '';
    state.selectedId = null; state.pickedIds = []; state.selectionHistory = []; state.analysis = null; state.analysisConfig = null; state.scan = null;
    drawingView = null; visibleGroup = null; diagnosticPoints = []; selectedDimensionId = null;
    state.setDocument(token, payload, config); viewer?.clear(); $('exportConfirmation').value = ''; renderVersions(); renderChanges([], '');
    state.projectName = (payload.document.filename || trEngineering("CAD 项目")).replace(/\.dxf$/i, '').slice(0, 100); $('projectName').value = state.projectName;
    showDocumentDetails(); $('exampleHint').hidden = !exampleMode; setForm(config); renderInspector(); renderDiagnostics();
    const rejected = payload.document.entities.filter((entity) => entity.status !== 'ready');
    renderReport(rejected.map((entity) => ({ id: entity.id, layer: entity.layer, status: 'failed', reason: entity.reason || trEngineering("无效或暂未支持的原图实体") })));
    if (rejected.length) $('reportSummary').textContent = trEngineering("导入检查 · {p0} 个未支持 / 无效实体", {p0: rejected.length});
    notice(trEngineering("已读取图纸。{p0}请确认单位、图层映射、实体区域及尺寸。", {p0: rejected.length ? trEngineering("其中 {p0} 个实体无效或暂未支持；", {p0: rejected.length}) : ''}), rejected.length ? 'warn' : '');
    return true;
  }

  async function importFile(file, exampleMode = null) {
    if (!serviceReady()) return;
    if (!file || !/\.dxf$/i.test(file.name)) { notice(trEngineering("请选择 DXF 文件。DWG 请先使用 CAD 软件另存为 DXF。"), 'error'); return; }
    const maxBytes = capability?.max_upload_bytes || 64 * 1024 * 1024;
    if (file.size > maxBytes) { notice(trEngineering("文件超出上传上限 {p0} MiB。", {p0: Math.round(maxBytes / 1024 / 1024)}), 'error'); return; }
    if (activeImportOperation) await cancelOperation();
    if (!exampleMode && ($('scanFirst').checked || file.size > (capability?.direct_import_max_bytes || 10 * 1024 * 1024))) return scanFile(file);
    const { token, signal } = startRequest(trEngineering("正在读取图纸实体与图层")); notice(trEngineering("正在读取 {p0} 的实体和图层…", {p0: file.name}));
    try {
      const form = new FormData(); form.append('file', file);
      const payload = await jsonRequest('/api/cad/import', { method: 'POST', body: form, signal });
      acceptDocument(token, payload, exampleMode);
    } catch (error) { if (state.isCurrent(token)) notice(errorMessage(error), 'error'); }
    finally { state.finish(token); if (state.isCurrent(token)) refresh(); }
  }

  async function build(config = readConfig(), undo = false, request = null) {
    if (!serviceReady() || (state.busy && !request)) return false;
    const issue = validateConfig(config);
    state.config = clone(config); refresh();
    if (issue) { notice(issue, 'warn'); if (request) { state.finish(request.token); refresh(); } return false; }
    const { token, signal } = request || startRequest(trEngineering("正在检查轮廓并生成三维模型"));
    notice(trEngineering("正在检查轮廓并计算三维几何…"));
    const firstModel = !state.model;
    try {
      const payload = await jsonRequest('/api/cad/build', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ document_id: state.documentId, config }), signal });
      if (!state.setModel(token, payload, config, undo)) return false;
      if (undo) { $('commandResult').textContent = trEngineering("已撤销，恢复上一次建模参数。"); renderChanges([], ''); }
      try {
        viewer?.setModel(payload.model, firstModel);
        const selected = payload.model.objects?.find((item) => item.source_entity_ids?.includes(state.selectedId));
        viewer?.select(selected?.id || null);
      } catch (error) { showViewerError(trEngineering("三维显示失败：{p0}。模型数据仍可导出。", {p0: errorMessage(error)})); }
      renderDrawing(); const counts = renderReport(); renderInspector();
      if (!payload.model.objects?.length) notice(trEngineering("未生成有效构件，请查看处理记录，修正图层或轮廓后重试。"), 'warn');
      else if (counts.failed) notice(trEngineering("已生成 {p0} 个构件，但有 {p1} 个实体处理失败。请查看记录；当前为部分结果。", {p0: payload.model.objects.length, p1: counts.failed}), 'warn');
      else notice(trEngineering("已生成 {p0} 个几何构件。可以旋转查看、选择来源或继续修改尺寸。", {p0: payload.model.objects.length}));
      return true;
    } catch (error) {
      if (state.isCurrent(token)) {
        if (error.report) renderReport(error.report);
        notice(`${errorMessage(error)}${state.model ? trEngineering(" 当前保留上次生成的模型，本次参数尚未应用。") : ''}`, 'error');
      }
      return false;
    } finally { state.finish(token); if (state.isCurrent(token)) { refresh(); renderInspector(); scheduleAnalysis(); } }
  }

  async function runCommand(message) {
    if (!message.trim() || state.busy || !state.document || !serviceReady()) return;
    const config = readConfig(); state.config = clone(config);
    renderChanges([], '');
    const request = startRequest(); notice(trEngineering("正在解析参数修改…"));
    try {
      const payload = await jsonRequest('/api/cad/command', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ document_id: state.documentId, config, message, selected_id: state.selectedId }), signal: request.signal });
      if (!state.isCurrent(request.token)) return;
      setForm(payload.config);
      $('commandResult').textContent = payload.changes?.length ? payload.changes.join('；') : payload.message || trEngineering("已解析参数。");
      const changes = configChanges(config, payload.config, state.document); renderChanges(changes, trEngineering("待应用：正在重新生成几何模型"));
      const applied = await build(payload.config, false, request);
      if (state.isCurrent(request.token)) { renderChanges(changes, applied ? trEngineering("已应用：原值 → 新值，可撤销") : trEngineering("未应用：当前保留上次生成的模型")); if (applied) $('commandInput').value = ''; }
    } catch (error) { if (state.isCurrent(request.token)) notice(trEngineering("{p0} 现有模型保持不变。", {p0: errorMessage(error)}), 'error'); }
    finally { state.finish(request.token); if (state.isCurrent(request.token)) { refresh(); renderInspector(); } }
  }

  async function loadExample(mode) {
    if (!serviceReady()) return;
    clear(); const { token, signal } = startRequest(); notice(trEngineering("正在载入合成演示图纸…"));
    try {
      const response = await serviceFetch(`/api/cad/examples/${mode}`, { signal });
      if (!response.ok) throw new Error(trEngineering("样例读取失败（HTTP {p0}）。", {p0: response.status}));
      const blob = await response.blob();
      if (!state.isCurrent(token)) return;
      await importFile(new File([blob], `demo-${mode}.dxf`, { type: 'application/dxf' }), mode);
    } catch (error) { if (state.isCurrent(token)) notice(errorMessage(error), 'error'); }
    finally { state.finish(token); if (state.isCurrent(token)) refresh(); }
  }

  async function exportModel(format) {
    if (!state.canExport || !serviceReady() || !CONFIRMATIONS.includes($('exportConfirmation').value) || (format === 'step' && !capability?.step_available)) return;
    const modelId = state.modelId;
    const { token, signal } = startRequest(); notice(trEngineering("正在准备模型与参数下载…"));
    try {
      const response = await serviceFetch('/api/cad/export', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ model_id: modelId, confirmation: $('exportConfirmation').value, format }), signal });
      if (!response.ok) {
        const payload = await response.json().catch(() => ({}));
        throw responseError(payload, response.status);
      }
      const blob = await response.blob(); if (!state.isCurrent(token)) return;
      download(blob, `${(state.document.filename || 'cad-model').replace(/\.dxf$/i, '')}.${format}`);
      notice(trEngineering("已准备下载。参数记录包含原图来源、单位和坐标变换。"));
    } catch (error) { if (state.isCurrent(token)) notice(errorMessage(error), 'error'); }
    finally { state.finish(token); if (state.isCurrent(token)) { refresh(); renderInspector(); } }
  }

  function showViewerError(message) { modelViewError = true; $('webglError').textContent = message; $('webglError').hidden = false; $('modelEmpty').hidden = true; }

  $('cadFile').addEventListener('change', (event) => { const file = event.target.files[0]; if (file) importFile(file); });
  const dropzone = $('dropzone');
  for (const type of ['dragenter', 'dragover']) dropzone.addEventListener(type, (event) => { event.preventDefault(); dropzone.classList.add('dragging'); });
  for (const type of ['dragleave', 'drop']) dropzone.addEventListener(type, (event) => { event.preventDefault(); dropzone.classList.remove('dragging'); });
  dropzone.addEventListener('drop', (event) => { const file = event.dataTransfer.files[0]; if (file) importFile(file); });
  $('clearFile').addEventListener('click', clear);
  $('cancelOperation').addEventListener('click', cancelOperation);
  $('closeScan').addEventListener('click', async () => { if (activeImportOperation) await cancelOperation(); state.scan = null; refresh(); });
  $('scanLayerFilter').addEventListener('input', renderScanLayers);
  $('scanSelectAll').addEventListener('click', () => { if (state.busy) return; scanLayersSelected = new Set((state.scan?.index.layers || []).map((layer) => layer.name)); renderScanLayers(); renderScanPreview(); refresh(); });
  $('scanSelectNone').addEventListener('click', () => { if (state.busy) return; scanLayersSelected.clear(); renderScanLayers(); renderScanPreview(); refresh(); });
  $('resetScanBounds').addEventListener('click', () => { if (state.busy) return; scanBoundInputs().forEach((input) => { input.value = ''; }); renderScanPreview(); });
  for (const input of scanBoundInputs()) input.addEventListener('input', renderScanPreview);
  $('importScanSelection').addEventListener('click', importScanSelection);
  wireRectangle($('scanSvg'), () => $('scanInteraction').value || 'box', (bounds) => { scanBoundInputs().forEach((input, index) => { input.value = Number(bounds[index].toPrecision(12)); }); renderScanPreview(); });
  $('fitScan').addEventListener('click', () => { scanView = null; renderScanPreview(); });
  $('scanSvg').addEventListener('wheel', (event) => {
    if (!state.scan || !scanView || state.busy) return; event.preventDefault();
    const point = pointInSvg($('scanSvg'), event), factor = Math.exp(Math.max(-1, Math.min(1, event.deltaY * .0015)));
    if (scanView[2] * factor < 1e-9 || scanView[2] * factor > 1e15) return;
    scanView = [point[0] + (scanView[0] - point[0]) * factor, point[1] + (scanView[1] - point[1]) * factor, scanView[2] * factor, scanView[3] * factor]; $('scanSvg').setAttribute('viewBox', scanView.join(' '));
  }, { passive: false });
  for (const [id, action] of [['includePicked', 'only'], ['addPicked', 'add'], ['excludePicked', 'exclude'], ['resetSelection', 'reset']]) $(id).addEventListener('click', () => { if (!state.document || state.busy || (action !== 'reset' && !state.pickedIds.length)) return; setSelection(editSelection(readConfig(), state.pickedIds, action)); });
  $('undoSelection').addEventListener('click', () => { if (state.busy || !state.selectionHistory.length) return; const previous = state.selectionHistory.pop(); state.config = detachDimensionBindings({ ...readConfig(), selection: previous.selection, dimension_bindings: previous.bindingConfig.dimension_bindings || [] }, previous.bindingConfig); visibleGroup = null; renderSelectionGroups(); renderDimensions(); renderDrawing(); scheduleAnalysis(); refresh(); notice(trEngineering("已恢复上一次实体选集、分组和仍适用的尺寸绑定。")); });
  $('saveSelectionGroup').addEventListener('click', () => {
    if (state.busy || !state.pickedIds.length) return;
    const name = $('selectionGroupName').value.trim(); if (!name) { notice(trEngineering("请先填写实体分组名称。"), 'warn'); return; }
    const config = readConfig(); config.selection = clone(selectionOf(config));
    const groups = config.selection.groups, group = { name, entity_ids: [...state.pickedIds] }, index = groups.findIndex((item) => item.name === name);
    if (index < 0 && groups.length >= 64) { notice(trEngineering("每个项目最多保存 64 个分组，请删除不再需要的分组。"), 'warn'); return; }
    if (index < 0) groups.push(group); else groups[index] = group;
    setSelection(config); $('selectionGroups').value = String(index < 0 ? groups.length : index + 1); refresh(); notice(trEngineering("已保存分组“{p0}”，请保存项目以便下次恢复。", {p0: name}));
  });
  $('selectionGroups').addEventListener('change', refresh);
  $('showSelectionGroup').addEventListener('click', () => { if (state.busy || !currentGroup()) return; visibleGroup = new Set(currentGroup().entity_ids); drawingView = null; renderDrawing(); notice(trEngineering("当前只显示所选分组；建模范围保持不变。")); });
  $('useSelectionGroup').addEventListener('click', () => { if (state.busy || !currentGroup()) return; setSelection(editSelection(readConfig(), currentGroup().entity_ids, 'only')); });
  $('deleteSelectionGroup').addEventListener('click', () => { if (state.busy || !currentGroup()) return; const config = readConfig(); config.selection = clone(selectionOf(config)); config.selection.groups.splice(Number($('selectionGroups').value) - 1, 1); visibleGroup = null; setSelection(config); });
  $('showAllEntities').addEventListener('click', () => { visibleGroup = null; drawingView = null; renderDrawing(); });
  $('fitDrawing').addEventListener('click', () => { drawingView = null; renderDrawing(); });
  function pickRegion(bounds, additive = false) {
    const entities = state.analysis?.preview_entities || state.document?.entities || [];
    const picked = idsInBounds(entities.filter((entity) => !visibleGroup || visibleGroup.has(entity.id)), bounds);
    state.pickedIds = additive ? [...new Set([...state.pickedIds, ...picked])] : picked;
    if (state.pickedIds.length) selectEntity(state.pickedIds[0]); else { state.selectedId = null; highlightSelection(); renderInspector(); refresh(); }
  }
  $('pickVisible').addEventListener('click', () => { if (state.busy || !drawingView) return; const [x, y, width, height] = drawingView; pickRegion([x, -(y + height), x + width, -y]); });
  wireRectangle($('drawingSvg'), () => $('drawingInteraction').value || 'pick', pickRegion);
  $('drawingSvg').addEventListener('wheel', (event) => {
    if (!state.document || !drawingView || state.busy) return; event.preventDefault();
    const point = pointInSvg($('drawingSvg'), event), factor = Math.exp(Math.max(-1, Math.min(1, event.deltaY * .0015)));
    if (drawingView[2] * factor < 1e-9 || drawingView[2] * factor > 1e15) return;
    drawingView = [point[0] + (drawingView[0] - point[0]) * factor, point[1] + (drawingView[1] - point[1]) * factor, drawingView[2] * factor, drawingView[3] * factor]; $('drawingSvg').setAttribute('viewBox', drawingView.join(' '));
  }, { passive: false });
  $('analyzeDrawing').addEventListener('click', () => { clearTimeout(analysisTimer); analyze(); });
  $('calculateSection').addEventListener('click', calculateSection);
  $('saveSection').addEventListener('click', saveSection);
  $('bindDimension').addEventListener('click', bindDimension);
  $('projectName').addEventListener('input', () => { state.projectName = $('projectName').value; refresh(); });
  $('saveDraft').addEventListener('click', () => saveProject(false));
  $('saveVersion').addEventListener('click', () => saveProject(true));
  $('saveCopy').addEventListener('click', () => saveProject(state.canExport, true));
  $('recentProjects').addEventListener('change', refresh);
  $('projectVersions').addEventListener('change', refresh);
  $('refreshProjects').addEventListener('click', refreshProjects);
  $('openProject').addEventListener('click', () => openProject($('recentProjects').value));
  $('openVersion').addEventListener('click', () => openProject(state.project?.id, $('projectVersions').value));
  $('projectPackage').addEventListener('change', (event) => { const file = event.target.files[0]; if (file) importProject(file); });
  $('exportProject').addEventListener('click', exportProject);
  $('agentProjectLink').addEventListener('click', (event) => { if (!state.project || state.projectDirty || state.busy) event.preventDefault(); });
  for (const button of all('[data-example]')) button.addEventListener('click', () => loadExample(button.dataset.example));
  for (const button of all('[data-mode]')) button.addEventListener('click', () => {
    if (!state.document || state.busy || state.config.mode === button.dataset.mode) return;
    const next = readConfig(); next.mode = button.dataset.mode;
    for (const layer of state.document.layers) {
      const current = next.layers[layer.name];
      next.layers[layer.name] = current === 'ignore' ? 'ignore' : next.mode === 'section' ? 'section' : ['wall', 'column', 'slab'].includes(layer.suggested_role) ? layer.suggested_role : 'ignore';
    }
    next.overrides = {}; next.dimension_bindings = []; next.confirmed_solid = false; setForm(next); edited();
  });
  $('drawingUnit').addEventListener('change', edited); $('curveTolerance').addEventListener('input', edited); $('solidConfirmed').addEventListener('change', edited);
  $('buildModel').addEventListener('click', () => build());
  $('commandForm').addEventListener('submit', (event) => { event.preventDefault(); runCommand($('commandInput').value); });
  $('undoChange').addEventListener('click', () => {
    if (state.busy) return;
    if (state.model && state.dirty) {
      setForm(clone(state.appliedConfig)); renderInspector(); renderReport();
      $('commandResult').textContent = trEngineering("已放弃未应用的修改，恢复当前模型的参数。");
      renderChanges([], '');
      notice(trEngineering("已恢复当前模型的参数。"));
      return;
    }
    if (!state.history.length || !serviceReady()) return;
    const previous = clone(state.history[state.history.length - 1]); setForm(previous); build(previous, true);
  });
  $('retryService').addEventListener('click', () => loadCapabilities(true));
  $('accessForm').addEventListener('submit', (event) => {
    event.preventDefault();
    if (capabilityChecking || state.busy) return;
    const token = $('accessToken').value.trim();
    if (!token) { notice(trEngineering("请输入访问口令。"), 'warn'); return; }
    doc.cookie = `cb_token=${encodeURIComponent(token)}; path=/; max-age=${60 * 60 * 24 * 30}; SameSite=Lax`;
    $('accessToken').value = '';
    loadCapabilities(true);
  });
  $('exportConfirmation').addEventListener('input', refresh);
  for (const button of all('[data-export]')) button.addEventListener('click', () => exportModel(button.dataset.export));
  $('fitView').addEventListener('click', () => viewer?.fit());
  $('toggleEdges').addEventListener('click', () => { edgesVisible = !edgesVisible; viewer?.setEdges(edgesVisible); $('toggleEdges').setAttribute('aria-pressed', String(edgesVisible)); });
  state.dispose = () => { clearTimeout(analysisTimer); analysisController?.abort(); analysisSequence += 1; requestController?.abort(); state.sequence += 1; viewer?.dispose(); doc.defaultView?.removeEventListener?.('cb:languagechange', onLanguageChange); };
  window.addEventListener('beforeunload', state.dispose);
  const onLanguageChange = () => {
    // Refresh presentation without applying drafts, invoking tools or changing source data.
    const inspectorInputs = [...$('objectInspector').querySelectorAll('input')].map(input => input.value);
    renderLayers(); renderDrawing(); renderSelectionGroups(); renderDiagnostics(); renderDimensions(); renderInspector(); renderReport(renderReport.last);
    [...$('objectInspector').querySelectorAll('input')].forEach((input, i) => { if (inspectorInputs[i] !== undefined) input.value = inspectorInputs[i]; });
    for (const [role, {row, height, base}] of parameterInputs) {
      row.querySelector('span').textContent = ROLES[role];
      height.placeholder = trEngineering('待填写');
      height.setAttribute('aria-label', trEngineering('{p0}{p1}，米', {p0: ROLES[role], p1: role === 'section' ? trEngineering('拉伸长度') : role === 'slab' ? trEngineering('厚度') : trEngineering('高度')}));
      base.setAttribute('aria-label', trEngineering('{p0}底部标高，米', {p0: ROLES[role]}));
    }
    if (state.scan) { renderScanLayers(); renderScanPreview(); }
    refresh();
    relocalizeOwned(doc, ['notice','serviceStatus','fileDetails','unitHint','modeHint','heightHeading','commandHelp','commandResult','commandDiffStatus','projectStatus','recentProjects','projectVersions','scanSummary','operationStage']);
    const commandPlaceholder = translatedUI.get($('commandInput').placeholder);
    if (commandPlaceholder) $('commandInput').placeholder = trEngineering(commandPlaceholder.source, commandPlaceholder.values);
  };
  doc.defaultView?.addEventListener?.('cb:languagechange', onLanguageChange);
  updateParameterRows(); refresh();

  // A missing graphics driver never blocks source inspection, parameter entry, or export.
  import('./cad-viewer.js').then(({ CadViewer }) => {
    viewer = new CadViewer($('modelStage'), (object) => { state.selectedId = object?.source_entity_ids?.[0] || null; highlightSelection(); renderInspector(); }, showViewerError);
    if (state.model) viewer.setModel(state.model);
    refresh();
  }).catch(() => showViewerError(trEngineering("当前浏览器无法启动三维查看器（WebGL 或本地显示组件不可用）。仍可检查二维轮廓、生成模型并导出 GLB。")));

  async function loadCapabilities(retry = false) {
    if (capabilityChecking || state.busy) return;
    capabilityChecking = true; refresh(); renderInspector();
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 15000);
    try {
      capability = await jsonRequest('/api/cad/capabilities', { signal: controller.signal, cache: 'no-store' });
      const available = capability.available === true;
      $('serviceStatus').textContent = available ? trEngineering("本地几何工具已就绪") : trEngineering("建模工具暂不可用");
      $('serviceStatus').className = `service-status ${available ? 'online' : 'offline'}`;
      $('accessPanel').hidden = true;
      $('retryService').hidden = available;
      if (!available) notice(trEngineering("建模工具缺少依赖：{p0}。安装后可点击重新检测。", {p0: (capability.missing_dependencies || []).join('、') || trEngineering("请检查服务配置")}), 'error');
      else if (retry) notice(state.document ? trEngineering("连接已恢复，图纸和参数已保留。请继续操作。") : trEngineering("连接已恢复，可以上传 DXF 或选择演示样例。"));
    } catch (error) {
      capability = null;
      $('retryService').hidden = false;
      $('serviceStatus').textContent = error.status === 401 ? trEngineering("需要访问口令") : trEngineering("建模服务连接失败");
      $('serviceStatus').className = 'service-status offline';
      notice(error.status === 401 ? trEngineering("请输入工作台访问口令，再继续使用建模工具。") : error.status === 404 ? trEngineering("当前服务未提供 CAD 建模工具，请返回工作台。") : error.name === 'AbortError' ? trEngineering("连接超时，请检查服务后点击重新检测。") : errorMessage(error), 'error');
    } finally { clearTimeout(timer); capabilityChecking = false; refresh(); renderInspector(); }
    if (serviceReady()) {
      await refreshProjects();
      if (pendingProjectId && serviceReady() && !state.busy) { const projectId = pendingProjectId; pendingProjectId = null; if (!state.document) await openProject(projectId); }
    }
  }
  await loadCapabilities();
  return state;
}

if (typeof document !== 'undefined' && document.getElementById('cadFile')) startCadApp();
