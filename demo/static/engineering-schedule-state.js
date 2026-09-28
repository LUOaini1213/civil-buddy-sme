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
const clone = (value) => JSON.parse(JSON.stringify(value));

/** A mouse gesture is one edit, even when the chart emits several callbacks. */
export class GanttGesture {
  constructor({read, write, validate, record}) {
    Object.assign(this, {read, write, validate, record});
    this.active = null;
    this.sequence = 0;
  }
  begin(id) {
    if (this.active) return this.active.token;
    const before = clone(this.read());
    if (!before.tasks.some((task) => task.id === id)) throw new Error(trEngineering("拖动的任务已不存在，请重新打开计划。"));
    this.active = {id, before, token: ++this.sequence};
    return this.active.token;
  }
  update(id, patch) {
    if (!this.active || this.active.id !== id) throw new Error(trEngineering("拖动任务与当前手势不一致。"));
    const next = clone(this.read());
    next.tasks = next.tasks.map((task) => task.id === id ? {...task,...patch} : task);
    this.validate(next.tasks);
    this.write(next);
  }
  commit(token = this.active?.token) {
    if (!this.active || this.active.token !== token) return null;
    const {id, before} = this.active;
    const after = clone(this.read());
    this.active = null;
    if (JSON.stringify(before) === JSON.stringify(after)) return null;
    this.record(clone(before));
    return {id, before: before.tasks.find((task) => task.id === id), after: after.tasks.find((task) => task.id === id)};
  }
  cancel() {
    if (!this.active) return false;
    this.write(clone(this.active.before));
    this.active = null;
    return true;
  }
}
