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
export function startRoutingApp(document) {
  const $ = (id) => document.getElementById(id);
  const copy = (value) => JSON.parse(JSON.stringify(value));
  const element = (tag, text, cls) => { const node = document.createElement(tag); node.setAttribute('data-i18n-dynamic', ''); if (text !== undefined) node.textContent = text; if (cls) node.className = cls; return node; };
  const format = (value) => Number(value.toPrecision(8)).toLocaleString(uiLocale(), { maximumSignificantDigits: 8 });
  const state = { model: { source: 'user', metric: 'distance_m', origin: '', destination: '', nodes: [], edges: [] }, result: null, history: [], dirty: true, jsonPending: false, busy: false, sequence: 0, revision: 0 };
  let controller, operation;
  function notify(message, error = false) { $('notice').textContent = message; $('notice').className = `notice${error ? ' error' : ''}`; }
  function controls() {
    for (const node of document.querySelectorAll('.editor input,.editor select,#nodes button,#edges button')) node.disabled = state.busy || state.jsonPending;
    for (const id of ['example', 'addNode', 'addEdge', 'undo', 'calculate']) $(id).disabled = state.busy || state.jsonPending;
    $('addNode').disabled ||= state.model.nodes.length >= 200;
    $('addEdge').disabled ||= state.model.nodes.length < 2 || state.model.edges.length >= 500;
    $('undo').disabled ||= !state.history.length;
    $('modelJson').disabled = state.busy;
    $('applyJson').disabled = $('discardJson').disabled = state.busy || !state.jsonPending;
    $('cancel').hidden = !state.busy;
    $('sample').hidden = state.model.source !== 'synthetic';
    $('synthetic').checked = state.model.source === 'synthetic';
    $('inputState').textContent = state.busy ? trEngineering("正在计算…") : state.jsonPending ? trEngineering("JSON 编辑未应用，请先应用或撤回。") : state.dirty && state.result ? trEngineering("输入已改变，请重新计算。") : state.result ? trEngineering("结果与当前输入一致") : trEngineering("尚未计算");
    $('resultState').hidden = !state.result;
    $('resultState').textContent = state.result ? state.dirty ? trEngineering("上次计算记录 · 当前输入尚未产生新结果，图中不高亮旧路线。") : trEngineering("当前路网的计算结果") : '';
  }
  function syncJson() { $('modelJson').value = JSON.stringify(state.model, null, 2); }
  function checkpoint() { state.history.push(copy(state.model)); if (state.history.length > 20) state.history.shift(); }
  function changed(mutator, rerender = false) {
    if (state.busy || state.jsonPending) return;
    checkpoint(); mutator(); state.dirty = true; state.revision++; syncJson();
    if (rerender) renderInputs();
    draw(); controls();
  }
  function selectNodes(select, value) {
    select.replaceChildren(new Option(trEngineering("请选择节点"), ''));
    for (const node of state.model.nodes) select.append(new Option(`${node.id} · ${node.name || trEngineering("未命名")}`, node.id));
    select.value = value;
  }
  function renderSelectors() {
    selectNodes($('origin'), state.model.origin); selectNodes($('destination'), state.model.destination);
    for (const select of document.querySelectorAll('[data-road-node]')) {
      const edge = state.model.edges[Number(select.dataset.index)];
      if (edge) selectNodes(select, edge[select.dataset.roadNode]);
    }
  }
  function inputCell(row, value, label, handler, type = 'text') {
    const td = element('td'), input = element('input'); input.type = type;
    input.setAttribute('aria-label', label);
    if (type === 'checkbox') input.checked = value; else input.value = value ?? '';
    input.addEventListener(type === 'checkbox' ? 'change' : 'input', () => handler(type === 'checkbox' ? input.checked : input.value));
    td.append(input); row.append(td); return input;
  }
  function renderInputs() {
    $('metric').value = state.model.metric;
    $('weightTitle').textContent = state.model.metric === 'distance_m' ? trEngineering("距离 m") : trEngineering("时间 min");
    $('nodes').replaceChildren();
    state.model.nodes.forEach((node, index) => {
      const row = element('tr'); row.append(element('td', node.id));
      const input = inputCell(row, node.name, trEngineering("{p0} 位置名称", {p0: node.id}), (value) => { changed(() => { state.model.nodes[index].name = value; }); renderSelectors(); }); input.maxLength = 100;
      const td = element('td'), remove = element('button', trEngineering("删除")); remove.type = 'button';
      remove.addEventListener('click', () => {
        const count = state.model.edges.filter((edge) => edge.from === node.id || edge.to === node.id).length;
        changed(() => {
          state.model.nodes.splice(index, 1); state.model.edges = state.model.edges.filter((edge) => edge.from !== node.id && edge.to !== node.id);
          if (state.model.origin === node.id) state.model.origin = ''; if (state.model.destination === node.id) state.model.destination = '';
        }, true); notify(trEngineering("已删除节点及 {p0} 条相连道路；可撤销。", {p0: count}));
      }); td.append(remove); row.append(td); $('nodes').append(row);
    });
    $('edges').replaceChildren();
    state.model.edges.forEach((edge, index) => {
      const row = element('tr'); row.append(element('td', edge.id));
      for (const key of ['from', 'to']) {
        const td = element('td'), select = element('select'); select.dataset.roadNode = key; select.dataset.index = String(index); select.setAttribute('aria-label', `${edge.id} ${key === 'from' ? trEngineering("从") : trEngineering("到")}`);
        select.addEventListener('change', () => changed(() => { state.model.edges[index][key] = select.value; })); td.append(select); row.append(td);
      }
      const weight = inputCell(row, edge.weight, `${edge.id} ${state.model.metric === 'distance_m' ? trEngineering("距离米") : trEngineering("通行时间分钟")}`, (value) => changed(() => { state.model.edges[index].weight = value === '' ? null : Number(value); }), 'number');
      weight.min = '0'; weight.max = '1000000000'; weight.step = 'any';
      inputCell(row, edge.bidirectional, trEngineering("{p0} 双向", {p0: edge.id}), (value) => changed(() => { state.model.edges[index].bidirectional = value; }), 'checkbox');
      inputCell(row, edge.closed, trEngineering("{p0} 封路", {p0: edge.id}), (value) => changed(() => { state.model.edges[index].closed = value; }), 'checkbox');
      const source = inputCell(row, edge.source, trEngineering("{p0} 数值来源", {p0: edge.id}), (value) => changed(() => { state.model.edges[index].source = value; })); source.maxLength = 200; source.placeholder = trEngineering("如：测量记录第 3 行");
      const td = element('td'), remove = element('button', trEngineering("删除")); remove.type = 'button'; remove.addEventListener('click', () => changed(() => { state.model.edges.splice(index, 1); }, true)); td.append(remove); row.append(td); $('edges').append(row);
    });
    renderSelectors(); controls();
  }
  function svg(tag, attributes, text) {
    const node = document.createElementNS('http://www.w3.org/2000/svg', tag); node.setAttribute('data-i18n-dynamic', '');
    for (const [key, value] of Object.entries(attributes)) node.setAttribute(key, String(value));
    if (text !== undefined) node.textContent = text; return node;
  }
  function draw() {
    const target = $('network'); target.replaceChildren();
    const defs = svg('defs', {});
    for (const [id, color] of [['road-arrow', '#819e8b'], ['route-arrow', '#137655'], ['closed-arrow', '#b55f58']]) {
      const marker = svg('marker', { id, viewBox: '0 0 10 10', refX: 9, refY: 5, markerWidth: 6, markerHeight: 6, orient: 'auto-start-reverse', markerUnits: 'userSpaceOnUse' });
      marker.append(svg('path', { d: 'M 0 0 L 10 5 L 0 10 z', fill: color })); defs.append(marker);
    } target.append(defs);
    if (!state.model.nodes.length) { target.append(svg('text', { x: 400, y: 245, 'text-anchor': 'middle', fill: '#657b7b' }, trEngineering("添加节点和道路后显示示意图"))); return; }
    const positions = new Map(), count = state.model.nodes.length;
    state.model.nodes.forEach((node, index) => {
      const angle = -Math.PI / 2 + index * 2 * Math.PI / count;
      positions.set(node.id, { x: count === 1 ? 400 : 400 + Math.cos(angle) * 290, y: count === 1 ? 245 : 235 + Math.sin(angle) * 165 });
    });
    const pathEdges = new Set(!state.dirty && state.result?.route_found ? state.result.segments.map((row) => row.edge_id) : []);
    const pathNodes = new Set(!state.dirty && state.result?.route_found ? state.result.path_node_ids : []), pairs = new Map();
    for (const edge of state.model.edges) {
      if (!positions.has(edge.from) || !positions.has(edge.to) || edge.from === edge.to) continue;
      const a = positions.get(edge.from), b = positions.get(edge.to), dx = b.x - a.x, dy = b.y - a.y, length = Math.hypot(dx, dy);
      const pair = [edge.from, edge.to].sort().join('|'), index = pairs.get(pair) || 0; pairs.set(pair, index + 1);
      const offset = index ? (index % 2 ? 1 : -1) * Math.ceil(index / 2) * 32 : 0;
      const start = { x: a.x + dx / length * 23, y: a.y + dy / length * 23 }, end = { x: b.x - dx / length * 23, y: b.y - dy / length * 23 };
      const control = { x: (a.x + b.x) / 2 - dy / length * offset, y: (a.y + b.y) / 2 + dx / length * offset };
      const highlighted = pathEdges.has(edge.id), marker = edge.closed ? 'closed-arrow' : highlighted ? 'route-arrow' : 'road-arrow';
      const attributes = { d: `M ${start.x} ${start.y} Q ${control.x} ${control.y} ${end.x} ${end.y}`, class: `road${edge.closed ? ' closed' : highlighted ? ' route' : ''}`, 'marker-end': `url(#${marker})` };
      if (edge.bidirectional) attributes['marker-start'] = `url(#${marker})`;
      const line = svg('path', attributes); line.append(svg('title', {}, `${edge.id} · ${edge.source || trEngineering("来源待填")}${edge.closed ? trEngineering(" · 封路") : ''}`)); target.append(line);
      target.append(svg('text', { x: .25 * start.x + .5 * control.x + .25 * end.x, y: .25 * start.y + .5 * control.y + .25 * end.y - 6, class: 'road-label', 'text-anchor': 'middle' }, `${edge.id} · ${Number.isFinite(edge.weight) ? format(edge.weight) : trEngineering("待填")} ${state.model.metric === 'distance_m' ? 'm' : 'min'}`));
    }
    for (const node of state.model.nodes) {
      const point = positions.get(node.id);
      target.append(svg('circle', { cx: point.x, cy: point.y, r: 22, class: `junction${pathNodes.has(node.id) ? ' route' : ''}` }));
      target.append(svg('text', { x: point.x, y: point.y + 4, class: 'node-id' }, node.id));
      const label = svg('text', { x: point.x, y: point.y + 40, class: 'node-name' }, node.name.length > 16 ? `${node.name.slice(0, 16)}…` : node.name || trEngineering("待命名")); label.append(svg('title', {}, node.name)); target.append(label);
    }
  }
  function renderResult(value) {
    $('result').replaceChildren(); $('engine').textContent = `${value.engine.name} ${value.engine.version}`;
    $('raw').hidden = false; $('resultJson').textContent = JSON.stringify(value, null, 2);
    if (!value.route_found) $('result').append(element('p', value.message, 'notice error'));
    else {
      const total = element('div', undefined, 'total'); total.append(element('span', value.metric === 'distance_m' ? trEngineering("总距离") : trEngineering("总通行时间")), element('strong', format(value.total_weight)), element('span', value.unit)); $('result').append(total);
      const names = new Map(value.model.nodes.map((row) => [row.id, row.name]));
      $('result').append(element('p', value.path_node_ids.map((id) => `${names.get(id)}（${id}）`).join(' → '), 'path'));
      if (!value.segments.length) $('result').append(element('p', trEngineering("起点与终点相同，无需经过道路。"), 'muted'));
      else {
        const wrap = element('div', undefined, 'table-scroll'), table = element('table'), head = element('tr');
        for (const label of [trEngineering("原道路编号"), trEngineering("行进方向"), trEngineering("分段 {p0}", {p0: value.unit}), trEngineering("数值来源")]) head.append(element('th', label));
        const thead = element('thead'); thead.append(head); table.append(thead); const body = element('tbody');
        for (const segment of value.segments) { const row = element('tr'); for (const text of [segment.edge_id, `${segment.from} → ${segment.to}`, format(segment.weight), segment.source]) row.append(element('td', text)); body.append(row); }
        table.append(body); wrap.append(table); $('result').append(wrap);
      }
    }
    $('result').append(element('p', value.excluded_edge_ids.length ? trEngineering("已排除封路：{p0}", {p0: value.excluded_edge_ids.join('、')}) : trEngineering("本次没有封路道路。"), 'muted'));
    const notes = element('ul'); for (const text of value.notes) notes.append(element('li', text)); $('result').append(notes);
  }
  function checkShape(model) {
    const exact = (row, keys) => row && typeof row === 'object' && !Array.isArray(row) && Object.keys(row).sort().join('|') === [...keys].sort().join('|');
    const id = (value) => typeof value === 'string' && /^[A-Za-z][A-Za-z0-9_-]{0,63}$/.test(value);
    if (!exact(model, ['source','metric','origin','destination','nodes','edges']) || !['user','synthetic'].includes(model.source) || !['distance_m','travel_time_min'].includes(model.metric) || !Array.isArray(model.nodes) || !Array.isArray(model.edges) || !model.nodes.length || model.nodes.length > 200 || model.edges.length > 500) throw new Error(trEngineering("请提交完整路网对象：明确单位，1–200 个节点，最多 500 条道路。"));
    const nodes = new Set(), edges = new Set();
    for (const node of model.nodes) {
      if (!exact(node, ['id','name']) || !id(node.id) || nodes.has(node.id) || typeof node.name !== 'string' || !node.name.trim() || node.name.length > 100) throw new Error(trEngineering("节点编号须唯一，名称不能为空；完整编号规则见接口提示。"));
      nodes.add(node.id);
    }
    if (!nodes.has(model.origin) || !nodes.has(model.destination)) throw new Error(trEngineering("请选择已有节点作为起点和终点。"));
    for (const edge of model.edges) {
      if (!exact(edge, ['id','from','to','weight','bidirectional','closed','source']) || !id(edge.id) || edges.has(edge.id) || !nodes.has(edge.from) || !nodes.has(edge.to) || edge.from === edge.to || !Number.isFinite(edge.weight) || edge.weight <= 0 || edge.weight > 1e9 || typeof edge.bidirectional !== 'boolean' || typeof edge.closed !== 'boolean' || typeof edge.source !== 'string' || !edge.source.trim() || edge.source.length > 200) throw new Error(trEngineering("道路 {p0}：核对唯一编号、两端节点、正数边权、方向、封路状态及数值来源。", {p0: edge?.id || trEngineering("未编号")}));
      edges.add(edge.id);
    }
  }
  async function request(url, options) {
    const response = await fetch(url, { credentials: 'same-origin', ...options });
    if (response.status === 401) { $('auth').hidden = false; throw new Error(trEngineering("请输入访问口令，输入已保留。")); }
    const data = await response.json(); if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : trEngineering("请求失败（{p0}）", {p0: response.status})); return data;
  }
  const post = (data) => ({ method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data) });
  async function calculate() {
    if (state.busy || state.jsonPending) return;
    const payload = copy(state.model);
    try { checkShape(payload); } catch (error) { notify(error.message, true); return; }
    state.busy = true; state.dirty = true; const sequence = ++state.sequence, revision = state.revision;
    controller = new AbortController(); operation = crypto.randomUUID().replaceAll('-', ''); controls(); draw(); notify(trEngineering("正在按已输入路网计算…"));
    try {
      const value = await request('/api/engineering/routes/calculate', { ...post({ model: payload }), headers: { 'Content-Type': 'application/json', 'X-CAD-Operation-ID': operation }, signal: controller.signal });
      if (sequence !== state.sequence || revision !== state.revision) return;
      state.result = value; state.dirty = false; renderResult(value); draw(); notify(value.message);
    } catch (error) { if (sequence === state.sequence && error.name !== 'AbortError') notify(trEngineering("计算未完成：{p0}", {p0: error.message}), true); }
    finally { if (sequence === state.sequence) { state.busy = false; operation = null; controller = null; controls(); } }
  }
  async function cancelOperation() {
    if (!state.busy) return;
    const identifier = operation, sequence = ++state.sequence; controller?.abort(); state.busy = false; operation = null; controller = null; state.dirty = true; controls(); draw(); notify(trEngineering("已停止等待本次路线计算。"));
    try { await request(`/api/engineering/routes/operations/${identifier}/cancel`, post({})); if (sequence === state.sequence) notify(trEngineering("已取消本次路线计算；旧结果保留为历史显示。")); }
    catch (error) { if (sequence === state.sequence) notify(trEngineering("已停止等待，取消确认失败：{p0}；未更新路线。", {p0: error.message}), true); }
  }
  function syntheticExample() {
    const metric = state.model.metric, time = metric === 'travel_time_min';
    return { source: 'synthetic', metric, origin: 'Gate', destination: 'Site',
      nodes: [{id:'Gate',name:trEngineering("入口")},{id:'Store',name:trEngineering("材料库")},{id:'Yard',name:trEngineering("堆场")},{id:'Site',name:trEngineering("作业区")},{id:'Exit',name:trEngineering("出口")}],
      edges: [['R1','Gate','Store',80,3,true],['R2','Store','Site',100,4,true],['R3','Gate','Yard',50,2,true],['R4','Yard','Site',100,4,true],['R5','Store','Yard',30,2,true],['R6','Site','Exit',60,3,false]].map(([id,from,to,distance,minutes,bidirectional]) => ({id,from,to,weight:time?minutes:distance,bidirectional,closed:false,source:trEngineering("合成教学样例，非现场测量")})) };
  }
  function nextId(prefix, rows) { let index = 1; const known = new Set(rows.map((row) => row.id)); while (known.has(prefix + index)) index++; return prefix + index; }
  $('example').addEventListener('click', () => { changed(() => { state.model = syntheticExample(); }, true); notify(trEngineering("已载入合成路网。可计算后封闭 R4，观察改道；全部数值为演示输入。")); });
  $('addNode').addEventListener('click', () => changed(() => { state.model.nodes.push({ id: nextId('N', state.model.nodes), name: '' }); }, true));
  $('addEdge').addEventListener('click', () => changed(() => { state.model.edges.push({ id: nextId('R', state.model.edges), from: '', to: '', weight: null, bidirectional: false, closed: false, source: '' }); }, true));
  $('undo').addEventListener('click', () => { if (state.busy || state.jsonPending || !state.history.length) return; state.model = state.history.pop(); state.dirty = true; state.revision++; syncJson(); renderInputs(); draw(); controls(); notify(trEngineering("已撤销输入修改，请重新计算。")); });
  $('metric').addEventListener('change', () => { const metric = $('metric').value; changed(() => { state.model.metric = metric; for (const edge of state.model.edges) edge.weight = null; }, true); notify(trEngineering("单位已切换，原数值已清空；请按新单位填写，也可撤销恢复。")); });
  for (const id of ['origin', 'destination']) $(id).addEventListener('change', () => changed(() => { state.model[id] = $(id).value; }));
  $('synthetic').addEventListener('change', () => changed(() => { state.model.source = $('synthetic').checked ? 'synthetic' : 'user'; }));
  $('modelJson').addEventListener('input', () => { if (state.busy) return; state.jsonPending = true; state.dirty = true; state.revision++; controls(); draw(); });
  $('applyJson').addEventListener('click', () => {
    if (state.busy || !state.jsonPending) return;
    try { const model = JSON.parse($('modelJson').value); checkShape(model); checkpoint(); state.model = model; state.jsonPending = false; state.dirty = true; state.revision++; syncJson(); renderInputs(); draw(); controls(); notify(trEngineering("JSON 已应用，请核对图与来源后计算。")); }
    catch (error) { notify(trEngineering("JSON 未应用：{p0}", {p0: error.message}), true); }
  });
  $('discardJson').addEventListener('click', () => { if (state.busy) return; state.jsonPending = false; syncJson(); controls(); notify(trEngineering("已撤回未应用的 JSON 编辑。")); });
  $('calculate').addEventListener('click', calculate); $('cancel').addEventListener('click', cancelOperation);
  $('auth').addEventListener('submit', (event) => { event.preventDefault(); document.cookie = `cb_token=${encodeURIComponent($('token').value.trim())}; path=/; max-age=2592000; SameSite=Lax`; $('token').value = ''; $('auth').hidden = true; notify(trEngineering("访问口令已保存，请再次计算。")); });
  const localeWindow = document.defaultView || globalThis.window;
  const onLanguageChange = () => { renderInputs(); draw(); if (state.result) renderResult(state.result); controls(); relocalizeOwned(document, ['notice']); };
  localeWindow?.addEventListener?.('cb:languagechange', onLanguageChange);
  syncJson(); renderInputs(); draw(); controls();
  return { state, calculate, cancelOperation, checkShape, dispose() { state.sequence++; controller?.abort(); localeWindow?.removeEventListener?.("cb:languagechange", onLanguageChange); } };
}

if (typeof window !== 'undefined' && typeof document !== 'undefined') startRoutingApp(document);
