import Gantt from './vendor/frappe-gantt-1.2.2/frappe-gantt.es.js';

const clone = (value) => JSON.parse(JSON.stringify(value));
const idPattern = /^[A-Za-z][A-Za-z0-9_-]{0,63}$/;
export function parseDependencies(text) {
  if (!text.trim()) return [];
  const seen = new Set();
  return text.split(/[,，\n]+/).map((part) => {
    const match = /^\s*([A-Za-z][A-Za-z0-9_-]{0,63})\s*:\s*(FS|SS|FF|SF)\s*:\s*([+-]?\d+)\s*$/i.exec(part);
    if (!match) throw new Error('依赖格式应为 T1:FS:0；多个依赖用逗号分隔。');
    const key=`${match[1]}:${match[2].toUpperCase()}`;
    if (seen.has(key)) throw new Error(`前置关系 ${key} 重复。`);
    if(Math.abs(Number(match[3]))>3653)throw new Error('依赖时距须在 -3653–3653 工作日之间。');
    seen.add(key); return {task_id: match[1], type: match[2].toUpperCase(), lag: Number(match[3])};
  });
}
export function parseResources(text) {
  const values = Object.create(null);
  if (!text.trim()) return values;
  for (const part of text.split(/[,，\n]+/)) {
    const match = /^\s*([A-Za-z][A-Za-z0-9_-]{0,63})\s*:\s*([1-9]\d*)\s*$/.exec(part);
    if (!match) throw new Error('资源格式应为 R1:2；用量必须是正整数。');
    if (Object.hasOwn(values, match[1])) throw new Error(`资源 ${match[1]} 重复。`);
    if(Number(match[2])>10000)throw new Error('单个资源用量不能超过 10000。');values[match[1]] = Number(match[2]);
  }
  return values;
}
export function weeklyPpc(weekly) {
  const done = weekly.filter((row) => row.status === 'done').length;
  return {total: weekly.length, done, planned: weekly.filter((row) => row.status === 'planned').length, missed: weekly.filter((row) => row.status === 'missed').length, percent: weekly.length ? Math.round(done / weekly.length * 1000) / 10 : null};
}
export function groupImportReports(report) {
  const grouped=new Map();
  for(const item of Array.isArray(report)?report:[]){
    const severity=String(item.severity??'info'),code=String(item.code??'未分类'),message=String(item.message??'');
    const key=JSON.stringify([severity,code,message]);
    if(!grouped.has(key))grouped.set(key,{severity,code,message,count:0,entityIds:[],projectLevel:false});
    const group=grouped.get(key);group.count++;
    if(item.entity_id!==undefined&&item.entity_id!==null&&String(item.entity_id)!==''){
      const id=String(item.entity_id);if(!group.entityIds.includes(id))group.entityIds.push(id);
    }else group.projectLevel=true;
  }
  return[...grouped.values()];
}
export function validDate(value) {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value || '')) return false;
  const date = new Date(`${value}T12:00:00Z`);
  return Number.isFinite(date.getTime()) && date.toISOString().slice(0,10) === value && value >= '1900-01-01' && value <= '2100-12-31';
}
const escapeHtml = (value) => String(value).replace(/[&<>"']/g, (char) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
const blankPlan = () => ({start_date:'',calendar:{weekdays:[0,1,2,3,4],holidays:[]},tasks:[],resources:[]});
const dependencyText = (rows) => (rows || []).map((row) => `${row.task_id}:${row.type}:${row.lag}`).join(', ');
const resourceText = (rows) => Object.entries(rows || {}).map(([id, units]) => `${id}:${units}`).join(', ');
const dateText = (value) => typeof value === 'string' ? value.slice(0,10) : value === 0 || value ? String(value) : '—';
const deltaDays = (a,b) => validDate(a) && validDate(b) ? Math.round((Date.parse(`${a}T12:00:00Z`)-Date.parse(`${b}T12:00:00Z`))/86400000) : null;

export function startPlanningApp(document, options = {}) {
  const $ = (id) => document.getElementById(id), win = options.window || globalThis.window;
  const fetcher = options.fetch || globalThis.fetch.bind(globalThis), Chart = options.Gantt || Gantt;
  const state = {plan:blankPlan(),name:'',weekly:[],project:null,baseline:null,result:null,resultPlan:'',resultStale:false,runId:null,method:'cpm',saved:'',history:[],pending:new Map(),busy:null,sequence:0,view:'gantt',optimization:null,importDraft:null,sourceId:null,importSource:null,synthetic:false,proposal:null};
  let controller = null, operationId = null;const committers=new Map();
  const el = (tag, text, cls) => {const node=document.createElement(tag); if(text!==undefined)node.textContent=String(text); if(cls)node.className=cls; return node;};
  const option = (label, value) => {const node=el('option',label);node.value=value;return node;};
  const notify = (text, error=false) => {$('notice').textContent=text;$('notice').className=`notice${error?' error':''}`;};
  const snapshot = () => ({plan:clone(state.plan),name:state.name,weekly:clone(state.weekly),synthetic:state.synthetic,result:clone(state.result),resultPlan:state.resultPlan,method:state.method,sourceId:state.sourceId,importSource:clone(state.importSource)});
  const dirty = () => state.pending.size>0 || JSON.stringify(snapshot())!==state.saved;
  const current = () => !!state.result && !state.resultStale && !state.pending.size && state.resultPlan===JSON.stringify(state.plan);
  function remember() {state.history.push({...snapshot(),runId:state.runId,resultStale:state.resultStale});if(state.history.length>60)state.history.shift();}
  function edit(change, planChanged=true) {
    if(state.busy)return;
    remember();change();
    if(planChanged){state.runId=null;state.resultStale=true;state.optimization=null;}
    render();
  }
  function discardAllowed() {return !dirty() || !state.plan.tasks.length && !state.name && !state.pending.size || win.confirm('当前有未保存修改。继续将替换这些修改，是否继续？');}
  function settleEdits() {
    if(!state.pending.size)return;
    const changes=[...state.pending].map(([key,pending])=>{const handler=committers.get(key);if(!handler)throw new Error('此字段已经变动，请撤回未应用输入后重试。');return{apply:handler.parse(pending.value),planChanged:handler.planChanged};});
    remember();for(const change of changes)change.apply();state.pending.clear();if(changes.some((change)=>change.planChanged)){state.runId=null;state.resultStale=true;state.optimization=null;}render();
  }
  function assertReady() {
    if(state.pending.size)throw new Error('还有未应用或格式无效的输入，请修正标记字段，或点击撤销编辑。');
    if(!validDate(state.plan.start_date))throw new Error('请填写有效的计划开始日期。');
    if(!state.plan.calendar.weekdays.length)throw new Error('请至少选择一个工作日。');
    if(!state.plan.tasks.length)throw new Error('请先添加任务或载入合成示例。');
    for(const task of state.plan.tasks){
      if(!task.name.trim())throw new Error(`${task.id} 尚未填写任务名称。`);
      if(!Number.isInteger(task.duration)||task.duration<0)throw new Error(`${task.id} 尚未填写非负整数工期。`);
    }
  }
  function controls() {
    for(const node of document.querySelectorAll('main button,main input,main select,main textarea'))node.disabled=!!state.busy;
    $('applyPlanningProposal').disabled=!!state.busy||!state.proposal; $('exportBundle').disabled=!!state.busy||!state.project||dirty(); $('attachSource').disabled=!!state.busy||!state.project||dirty(); $('cancelRequest').disabled=false;$('cancelRequest').hidden=!['calculate','optimize','load','import','conversation','applyProposal'].includes(state.busy);
    $('undoEdit').disabled=!!state.busy||!state.history.length&&!state.pending.size;$('applyInputs').disabled=!!state.busy||!state.pending.size;
    $('calculate').disabled=$('optimize').disabled=$('savePlan').disabled=$('saveCopy').disabled=$('exportPlan').disabled=!!state.busy;
    $('optimize').disabled ||= !state.plan.resources.length;
    $('restoreRevision').disabled=!!state.busy||!state.project||!$('revisions').value;
    $('undoSaved').disabled=!!state.busy||!state.project?.can_undo||dirty();
    $('captureBaseline').disabled=!!state.busy||!state.project||dirty()||!current();
    $('applyOptimization').disabled=!!state.busy||!state.optimization||!!state.pending.size;
    $('applyImport').disabled=!!state.busy||!state.importDraft||!$('confirmImport').checked;
    $('projectStatus').textContent=state.project?`修订 ${state.project.revision} · ${dirty()?'有未保存修改':'已保存'}`:'新计划 · 尚未保存';
    $('projectStatus').className=`status${dirty()?' stale':''}`;
    $('calculationState').textContent=state.busy?({calculate:'正在计算关键路径…',optimize:'正在分析资源容量…',save:'正在保存计划版本…',load:'正在读取项目…',import:'正在检查文件…',export:'正在生成导出文件…'}[state.busy]||'正在处理…'):state.pending.size?'离开字段或点击计算 / 保存时应用输入；无效格式会阻止操作。':state.result?(current()?'结果与当前输入一致。':'输入已修改，请重新计算；下方保留上次成功结果。'):'尚未计算';
    $('resultBadge').textContent=state.result?(current()?(state.method==='resource'?'资源调整结果':'当前关键路径结果'):'上次结果 · 已过期'):'等待输入';
    $('resultBadge').className=`status${state.result&&!current()?' stale':''}`;
    $('sampleNotice').hidden=!state.synthetic;
    for(const node of document.querySelectorAll('[data-view]'))node.setAttribute('aria-pressed',String(node.dataset.view===state.view));
  }
  async function request(path, body, token, method) {
    const headers={},init={method:method||(body===undefined?'GET':'POST'),credentials:'same-origin',headers};
    if(body!==undefined){if(body instanceof FormData)init.body=body;else{headers['Content-Type']='application/json';init.body=JSON.stringify(body);}}
    if(token){init.signal=token.signal;if(token.operation)headers['X-CAD-Operation-ID']=token.operation;}
    const response=await fetcher(`/api/engineering/planning${path}`,init);
    if(response.status===401){$('accessForm').hidden=false;throw new Error('请输入工作台访问口令；当前编辑已保留。');}
    if(!response.ok){const payload=await response.json().catch(()=>({}));const detail=typeof payload.detail==='string'?payload.detail:`请求失败（${response.status}）`;throw new Error(response.status===409?`版本冲突：${detail}。本地编辑已保留；可另存副本，或重新打开最新版本。`:detail);}
    return response;
  }
  const api = async (...args) => (await request(...args)).json();
  const owns = (token) => token.sequence===state.sequence;
  async function action(kind, fn) {
    if(state.busy)return;
    state.busy=kind;controller=new AbortController();operationId=['calculate','optimize','conversation','applyProposal'].includes(kind)?globalThis.crypto.randomUUID().replaceAll('-',''):null;
    const token={sequence:++state.sequence,signal:controller.signal,operation:operationId};controls();
    try{return await fn(token);}catch(error){if(owns(token)&&error.name!=='AbortError')notify(error.message||'操作未完成，当前编辑已保留。',true);}finally{if(owns(token)){state.busy=null;controller=null;operationId=null;controls();}}
  }
  async function cancel() {
    const operation=operationId;state.sequence++;controller?.abort();state.busy=null;controller=null;operationId=null;controls();notify('已取消本次请求，当前编辑与上次成功结果均保留。');
    if(operation)try{await fetcher(`/api/engineering/operations/${operation}/cancel`,{method:'POST',credentials:'same-origin'});}catch{/* Stale responses are still rejected by the sequence guard. */}
  }
  function input(value,key,label,setter,{type='text',min,max,step,maxLength,placeholder,planChanged=true}={}) {
    committers.set(key,{parse:setter,planChanged});
    const node=el('input');node.type=type;node.value=state.pending.get(key)?.value??value??'';node.setAttribute('aria-label',label);
    for(const [name,v]of Object.entries({min,max,step,maxLength,placeholder}))if(v!==undefined)node[name]=v;
    if(state.pending.get(key)?.error)node.setAttribute('aria-invalid','true');
    node.addEventListener('input',()=>{state.pending.set(key,{value:node.value});controls();});
    node.addEventListener('change',()=>{
      if(state.busy)return;
      try{const change=setter(node.value);state.pending.delete(key);edit(change,planChanged);}
      catch(error){state.pending.set(key,{value:node.value,error:error.message});node.setAttribute('aria-invalid','true');notify(error.message,true);controls();}
    });return node;
  }
  function integer(value,label,min=0,max=100000) {if(value===''||!Number.isInteger(Number(value))||Number(value)<min||Number(value)>max)throw new Error(`${label}须为 ${min}–${max} 的整数。`);return Number(value);}
  function optionalDate(value,label) {if(value&&!validDate(value))throw new Error(`${label}日期无效。`);return value||null;}
  function button(label,fn,cls) {const node=el('button',label,cls);node.type='button';node.onclick=()=>{if(!state.busy)fn();};return node;}
  function cell(row,node){const td=el('td');td.append(node);row.append(td);return td;}
  function taskDepth(task) {let depth=0,cursor=task,seen=new Set();while(cursor?.parent_id&&!seen.has(cursor.parent_id)&&depth<8){seen.add(cursor.parent_id);cursor=state.plan.tasks.find((t)=>t.id===cursor.parent_id);depth++;}return depth;}
  function renderTasks() {
    $('taskRows').replaceChildren();$('taskCount').textContent=`${state.plan.tasks.length} 项任务 · 工期 0 为里程碑`;
    const critical=new Set(current()?state.result?.critical_task_ids:[]),parents=new Set(state.plan.tasks.map((t)=>t.parent_id).filter(Boolean));
    for(const task of state.plan.tasks){
      const row=el('tr');row.className=[critical.has(task.id)?'is-critical':'',parents.has(task.id)?'summary-row':''].join(' ');
      cell(row,input(task.id,`task:${task.id}:id`,`${task.id} 编号`,(value)=>{
        if([...state.pending.keys()].some((key)=>key.startsWith('task:'+task.id+':')&&key!=='task:'+task.id+':id'))throw new Error('请先应用该任务的其他字段，再改编号。');
        if(!idPattern.test(value)||state.plan.tasks.some((t)=>t!==task&&t.id===value))throw new Error('任务编号须唯一且以英文字母开头，使用 1–64 位字母、数字、下划线或短横线。');
        return()=>{const old=task.id;task.id=value;for(const t of state.plan.tasks){if(t.parent_id===old)t.parent_id=value;for(const d of t.dependencies)if(d.task_id===old)d.task_id=value;}for(const w of state.weekly)if(w.task_id===old)w.task_id=value;};
      },{maxLength:64}));
      const name=input(task.name,`task:${task.id}:name`,`${task.id} 名称`,(value)=>{if(!value.trim())throw new Error('任务名称不能为空。');return()=>{task.name=value.trim();};},{maxLength:100,placeholder:'填写任务名称'});name.style.paddingLeft=`${8+taskDepth(task)*12}px`;cell(row,name);
      const parent=el('select');parent.setAttribute('aria-label',`${task.id} 父任务`);parent.append(option('无父任务',''));for(const t of state.plan.tasks)if(t.id!==task.id)parent.append(option(`${t.id} · ${t.name}`,t.id));parent.value=task.parent_id||'';parent.onchange=()=>edit(()=>{task.parent_id=parent.value||null;});cell(row,parent);
      cell(row,input(task.duration,`task:${task.id}:duration`,`${task.id} 工期`,(v)=>{const value=integer(v,'工期',0,3653);return()=>{task.duration=value;};},{type:'number',min:0,max:3653,step:1,placeholder:'必填'}));
      cell(row,input(task.progress,`task:${task.id}:progress`,`${task.id} 完成百分比`,(v)=>{if(v===''||!Number.isFinite(Number(v))||Number(v)<0||Number(v)>100)throw new Error('完成百分比须在 0–100 之间。');return()=>{task.progress=Number(v);};},{type:'number',min:0,max:100,step:'any'}));
      cell(row,input(dependencyText(task.dependencies),`task:${task.id}:dependencies`,`${task.id} 依赖`,(v)=>{const deps=parseDependencies(v);if(deps.some((d)=>d.task_id===task.id||!state.plan.tasks.some((t)=>t.id===d.task_id)))throw new Error('前置任务须为其他已有任务。');return()=>{task.dependencies=deps;};},{placeholder:'T1:FS:0'}));
      cell(row,input(resourceText(task.resources),`task:${task.id}:resources`,`${task.id} 资源用量`,(v)=>{const resources=parseResources(v);if(Object.keys(resources).some((id)=>!state.plan.resources.some((r)=>r.id===id)))throw new Error('请先在资源容量表添加对应资源。');return()=>{task.resources=resources;};},{placeholder:'R1:2'}));
      for(const [field,label]of [['actual_start','实际开始'],['actual_finish','实际完成']])cell(row,input(task[field],`task:${task.id}:${field}`,`${task.id} ${label}`,(v)=>{const date=optionalDate(v,label);return()=>{task[field]=date;};},{type:'date',min:'1900-01-01',max:'2100-12-31'}));
      cell(row,button('删除',()=>{
        if(state.plan.tasks.some((t)=>t.parent_id===task.id||t.dependencies.some((d)=>d.task_id===task.id))||state.weekly.some((w)=>w.task_id===task.id)){notify(`${task.id} 仍被依赖、子任务或周承诺引用，请先调整这些引用。`,true);return;}
        edit(()=>{state.plan.tasks=state.plan.tasks.filter((t)=>t.id!==task.id);for(const key of state.pending.keys())if(key.startsWith(`task:${task.id}:`))state.pending.delete(key);});
      },'danger'));$('taskRows').append(row);
    }
    if(!state.plan.tasks.length){const row=el('tr'),td=el('td','还没有任务。添加后填写明确工期，或者载入合成演示。','muted');td.colSpan=10;row.append(td);$('taskRows').append(row);}
    const selected=$('promiseTask').value;$('promiseTask').replaceChildren(option('选择已有任务',''));for(const task of state.plan.tasks)$('promiseTask').append(option(`${task.id} · ${task.name}`,task.id));$('promiseTask').value=selected;
  }
  function renderResources() {
    $('resourceRows').replaceChildren();
    for(const resource of state.plan.resources){const row=el('tr');
      cell(row,input(resource.id,`resource:${resource.id}:id`,`${resource.id} 资源编号`,(v)=>{if(!idPattern.test(v)||state.plan.resources.some((r)=>r!==resource&&r.id===v))throw new Error('资源编号无效或重复。');return()=>{const old=resource.id;resource.id=v;for(const task of state.plan.tasks)if(Object.hasOwn(task.resources,old)){const units=task.resources[old];delete task.resources[old];task.resources[v]=units;}};},{maxLength:64}));
      cell(row,input(resource.name,`resource:${resource.id}:name`,`${resource.id} 资源名称`,(v)=>{if(!v.trim())throw new Error('资源名称不能为空。');return()=>{resource.name=v.trim();};},{maxLength:100,placeholder:'如：木工班组'}));
      cell(row,input(resource.capacity,`resource:${resource.id}:capacity`,`${resource.id} 每日容量`,(v)=>{const capacity=integer(v,'每日容量',1,10000);return()=>{resource.capacity=capacity;};},{type:'number',min:1,max:10000,step:1,placeholder:'必填'}));
      cell(row,button('删除',()=>{if(state.plan.tasks.some((t)=>Object.hasOwn(t.resources,resource.id))){notify('此资源仍被任务占用，请先修改任务用量。',true);return;}edit(()=>{state.plan.resources=state.plan.resources.filter((r)=>r!==resource);});},'danger'));$('resourceRows').append(row);
    }
  }
  function renderCalendar() {
    $('planName').value=state.pending.get('name')?.value??state.name;$('startDate').value=state.pending.get('start')?.value??state.plan.start_date;
    $('holidays').value=state.pending.get('holidays')?.value??state.plan.calendar.holidays.join('\n');
    $('weekdays').replaceChildren();['一','二','三','四','五','六','日'].forEach((day,index)=>{const label=el('label'),check=el('input');check.type='checkbox';check.checked=state.plan.calendar.weekdays.includes(index);check.setAttribute('aria-label',`周${day}为工作日`);check.onchange=()=>edit(()=>{const days=new Set(state.plan.calendar.weekdays);check.checked?days.add(index):days.delete(index);state.plan.calendar.weekdays=[...days].sort();});label.append(check,el('span',day));$('weekdays').append(label);});
  }
  function resultTable(headers, rows) {const table=el('table'),thead=el('thead'),hr=el('tr');headers.forEach((h)=>hr.append(el('th',h)));thead.append(hr);table.append(thead);const body=el('tbody');for(const values of rows){const row=el('tr');values.forEach((value)=>row.append(el('td',value??'—')));body.append(row);}table.append(body);return table;}
  function renderBaseline() {
    $('baselineRows').replaceChildren();const baseline=state.baseline;
    $('baselineState').textContent=baseline?`基线：保存修订 ${baseline.revision} · ${baseline.created_at||''}。替换基线须明确确认；旧保存版本仍保留。`:'先计算并保存计划，再锁定基线。实际日期在任务表中填写。';
    const previous=new Map((baseline?.result?.tasks||[]).map((r)=>[r.id,r])),computed=new Map((state.result?.tasks||[]).map((r)=>[r.id,r]));
    for(const task of state.plan.tasks){const before=previous.get(task.id)?.end,now=current()?computed.get(task.id)?.end:null,delta=deltaDays(now,before);const row=el('tr');[`${task.id} · ${task.name}`,before||'—',now||'待重算',task.actual_finish||'—',delta===null?'—':`${delta>0?'+':''}${delta}`].forEach((value)=>row.append(el('td',value)));$('baselineRows').append(row);}
  }
  function renderWeekly() {
    const selected=$('weeklyFilter').value,weeks=[...new Set(state.weekly.map((w)=>w.week_start))].sort();$('weeklyFilter').replaceChildren(option('全部周',''));weeks.forEach((week)=>$('weeklyFilter').append(option(week,week)));$('weeklyFilter').value=weeks.includes(selected)?selected:'';
    const rows=state.weekly.filter((w)=>!$('weeklyFilter').value||w.week_start===$('weeklyFilter').value),stats=weeklyPpc(rows);
    $('ppc').textContent=stats.percent===null?'—':`${stats.percent}%`;$('ppcDetail').textContent=stats.total?`${stats.done} / ${stats.total} 项完成 · ${stats.missed} 项未履约 · ${stats.planned} 项未结算${stats.planned?'（进行中，未全部结算）':'（已结算）'}${!$('weeklyFilter').value?' · 当前为全部周合计':''}`:'尚无承诺';
    $('promiseRows').replaceChildren();for(const promise of rows){const row=el('tr');row.append(el('td',promise.week_start),el('td',`${promise.task_id} · ${state.plan.tasks.find((t)=>t.id===promise.task_id)?.name||'任务不存在'}`));
      const status=el('select');status.setAttribute('aria-label',`${promise.task_id} ${promise.week_start} 承诺状态`);for(const [value,label]of [['planned','进行中 / 未结算'],['done','本周已完成'],['missed','本周未完成']])status.append(option(label,value));status.value=promise.status;status.onchange=()=>edit(()=>{promise.status=status.value;},false);cell(row,status);
      for(const [field,label]of [['reason','原因 / 备注'],['constraints','待解决制约']])cell(row,input(promise[field]||'',`promise:${promise.id}:${field}`,`${promise.task_id} ${label}`,(value)=>()=>{promise[field]=value;},{maxLength:1000,planChanged:false}));
      cell(row,button('删除',()=>edit(()=>{state.weekly=state.weekly.filter((w)=>w.id!==promise.id);},false),'danger'));$('promiseRows').append(row);
    }
  }
  function drawNetwork() {
    const tasks=state.result?.tasks||[],plan=JSON.parse(state.resultPlan||'{}'),byId=new Map((plan.tasks||[]).map((t)=>[t.id,t])),ids=new Set(tasks.map((t)=>t.id));
    const levels=new Map(),active=new Set();function rank(id){if(levels.has(id))return levels.get(id);if(active.has(id))return 0;active.add(id);let level=0;for(const dep of byId.get(id)?.dependencies||[])if(ids.has(dep.task_id))level=Math.max(level,rank(dep.task_id)+1);active.delete(id);levels.set(id,level);return level;}
    const slots=new Map(),positions=new Map();for(const task of tasks){const level=rank(task.id),slot=slots.get(level)||0;slots.set(level,slot+1);positions.set(task.id,{x:26+level*270,y:35+slot*98});}
    const width=Math.max(680,50+(Math.max(...levels.values(),0)+1)*270),height=Math.max(210,70+Math.max(...slots.values(),1)*98),ns='http://www.w3.org/2000/svg',svg=document.createElementNS(ns,'svg');
    svg.setAttribute('class','network-svg');svg.setAttribute('width',width);svg.setAttribute('height',height);svg.setAttribute('viewBox',`0 0 ${width} ${height}`);svg.setAttribute('role','img');svg.setAttribute('aria-label','全部依赖类型与关键任务，箭头指向后续任务');
    const append=(tag,attrs,text,parent=svg)=>{const node=document.createElementNS(ns,tag);for(const[key,value]of Object.entries(attrs))node.setAttribute(key,value);if(text!==undefined)node.textContent=text;parent.append(node);return node;};
    const defs=append('defs',{}),marker=append('marker',{id:'planning-arrow',viewBox:'0 0 10 10',refX:9,refY:5,markerWidth:6,markerHeight:6,orient:'auto-start-reverse'},undefined,defs);append('path',{d:'M 0 0 L 10 5 L 0 10 z',fill:'#779eae'},undefined,marker);
    for(const task of tasks){const target=positions.get(task.id);for(const dep of byId.get(task.id)?.dependencies||[]){const source=positions.get(dep.task_id);if(!source)continue;const x1=source.x+212,y1=source.y+30,x2=target.x,y2=target.y+30,mid=(x1+x2)/2;append('path',{d:`M${x1},${y1} C${mid},${y1} ${mid},${y2} ${x2},${y2}`,class:'edge','marker-end':'url(#planning-arrow)'});append('text',{x:mid,y:(y1+y2)/2-7,class:'edge-label','text-anchor':'middle'},`${dep.type} ${dep.lag>=0?'+':''}${dep.lag}`);}}
    for(const task of tasks){const pos=positions.get(task.id),name=byId.get(task.id)?.name||task.id;append('rect',{x:pos.x,y:pos.y,width:212,height:61,rx:7,class:`node${task.critical?' critical':''}`});const label=append('text',{x:pos.x+11,y:pos.y+22},`${task.id} · ${name.slice(0,17)}${name.length>17?'…':''}`);append('title',{},`${task.id} · ${name}`,label);append('text',{x:pos.x+11,y:pos.y+43,class:'subtitle'},`${dateText(task.start)} → ${dateText(task.end)}`);}
    $('network').replaceChildren(svg);
  }
  function drawGantt() {
    $('gantt').replaceChildren();if(!state.result?.tasks?.length)return;
    const plan=JSON.parse(state.resultPlan),inputs=new Map(plan.tasks.map((t)=>[t.id,t])),parents=new Set(plan.tasks.map((t)=>t.parent_id).filter(Boolean)),resultIds=new Set(state.result.tasks.map((t)=>t.id));
    const tasks=state.result.tasks.map((row)=>{const input=inputs.get(row.id)||{};return{id:row.id,name:escapeHtml(`${row.id} · ${input.name||row.id}${input.duration===0?' ◆':''}`),start:row.start,end:row.end,progress:input.progress||0,dependencies:(input.dependencies||[]).filter((d)=>d.type==='FS'&&resultIds.has(d.task_id)).map((d)=>d.task_id),custom_class:row.critical?'critical':input.duration===0?'milestone':parents.has(row.id)?'summary':''};});
    try{new Chart($('gantt'),tasks,{readonly:true,popup:false,language:'zh',view_mode:$('viewMode').value,container_height:Math.min(520,95+tasks.length*39),infinite_padding:false,scroll_to:tasks.reduce((first,t)=>t.start<first?t.start:first,tasks[0].start)});}
    catch{notify('甘特显示暂时不可用；日期与时差表仍可查看，计划数据已保留。',true);}
  }
  function renderResult() {
    $('resultEmpty').hidden=!!state.result;$('resultContent').hidden=!state.result;if(!state.result)return;
    $('metrics').replaceChildren();for(const[label,value]of [['计划完成',dateText(state.result.finish_date)],['总工期 / 工作日',state.result.duration_workdays],state.method==='resource'?['资源冲突区间',(state.result.resource_conflicts||[]).length]:['关键任务',(state.result.critical_task_ids||[]).length],['计算方式',state.method==='resource'?'资源调整':'CPM']]){const node=el('div',undefined,'metric');node.append(el('strong',value??'—'),el('span',label));$('metrics').append(node);}
    const engine=state.result.engine;$('engineStatus').textContent=`${engine?.name||''} ${engine?.version||''}${state.method==='resource'?` · ${state.result.solver_status||''} · ${state.result.proven_optimal?(state.project?.bundle_origin?.optimality_reverified===false?'记录标注最优（项目包导入未重新证明）':'已证明最优'):'可行方案，未声明最优'}。资源方案不报告 CPM 时差或关键路径。`:' · 实际日期仅作记录，不自动锁定或重排剩余任务。'}`;
    $('resourceDiagnostics').hidden=!(state.result.resources?.length||state.result.resource_conflicts?.length);$('resourceReport').replaceChildren(resultTable(['资源','每日容量','峰值用量','是否超配'],(state.result.resources||[]).map((r)=>[`${r.id} · ${r.name}`,r.capacity,r.peak_demand,r.overallocated?'是':'否'])));
    if(state.result.resource_conflicts?.length)$('resourceReport').append(resultTable(['资源','冲突日期','用量 / 容量','任务'],state.result.resource_conflicts.map((r)=>[r.resource_id,`${r.start} → ${r.end}`,`${r.demand} / ${r.capacity}`,r.task_ids.join(', ')])));
    $('warnings').replaceChildren();for(const warning of state.result.warnings||[])$('warnings').append(el('li',typeof warning==='string'?warning:JSON.stringify(warning)));
    $('resultRows').replaceChildren();for(const task of state.result.tasks||[]){const row=el('tr');[task.id,dateText(task.start),dateText(task.end),`${dateText(task.early_start)} / ${dateText(task.early_finish)}`,`${dateText(task.late_start)} / ${dateText(task.late_finish)}`,task.total_float,state.method==='resource'?'未评估':task.critical?'是':'—'].forEach((v)=>row.append(el('td',v??'—')));$('resultRows').append(row);}
    $('ganttView').hidden=state.view!=='gantt';$('networkView').hidden=state.view!=='network';$('tableView').hidden=state.view!=='table';$('scaleControl').hidden=state.view!=='gantt';
    const basis=state.method==='resource'?'当前为资源方案，不标记 CPM 关键任务。':'红色为关键任务。';
    $('chartExplanation').textContent=basis+(state.view==='network'?'箭头表示前后置关系，标签为关系类型及工作日 lag；横向排布表示依赖层级，不是时间刻度。':state.view==='table'?'日期按项目工作日历计算；横向滚动可查看完整时差列。':'时间条来自计算结果；修改输入后重新计算。甘特图只画 FS 箭头，全部关系见依赖网络。');
    if(state.view==='gantt')drawGantt();if(state.view==='network')drawNetwork();
  }
  function renderOptimization() {
    $('optimization').hidden=!state.optimization;if(!state.optimization)return;
    const preview=state.optimization,old=new Map((state.result?.tasks||[]).map((t)=>[t.id,t]));
    $('optimizationSummary').textContent=`建议完成日期 ${dateText(preview.result.finish_date)}；应用后保留原输入来源，可撤销。${preview.result.warnings?.length?'请同时核对下列提醒。':''}`;
    $('optimizationChanges').replaceChildren(resultTable(['任务','调整前','建议日期'],preview.result.tasks.filter((t)=>!old.has(t.id)||old.get(t.id).start!==t.start||old.get(t.id).end!==t.end).map((t)=>[t.id,old.has(t.id)?`${old.get(t.id).start} → ${old.get(t.id).end}`:'尚无计算结果',`${t.start} → ${t.end}`])));
    for(const warning of preview.result.warnings||[])$('optimizationChanges').append(el('p',typeof warning==='string'?warning:JSON.stringify(warning),'muted small'));
  }
  function renderRevisions() {
    const previous=$('revisions').value;$('revisions').replaceChildren(option('选择保存版本',''));
    for(const version of state.project?.versions||[]){const n=typeof version==='number'?version:version.revision??version.version;$('revisions').append(option(`修订 ${n}${version.created_at||version.updated_at?' · '+(version.created_at||version.updated_at):''}`,String(n)));}$('revisions').value=previous;
  }
  function renderImportSummary(container,data,taskCount,{saved=false}={}) {
    const source=data.source||{},groups=groupImportReports(data.report),facts=el('dl',undefined,'import-facts');container.replaceChildren();
    for(const[label,value]of [['文件',source.filename||'未提供文件名'],['格式',source.format?String(source.format).toUpperCase():'未记录'],[saved?'当前任务数':'导入任务数',taskCount],['有原日期记录的任务',Object.keys(data.original_dates||{}).length]]){const field=el('div');field.append(el('dt',label),el('dd',value));facts.append(field);}container.append(facts);
    if(!groups.length){container.append(el('p','报告没有列出转换提示；仍需核对工作日历、工期与依赖。','muted small'));return;}
    container.append(el('p',`${Array.isArray(data.report)?data.report.length:0} 条报告记录，按级别、代码与完整内容合并为 ${groups.length} 组；所有不同提示均保留。`,'muted small'));
    const list=el('div',undefined,'import-report-groups');
    for(const group of groups){const card=el('article',undefined,'import-report-group'),heading=el('div',undefined,'report-heading'),label=({error:'错误',warning:'需核对',info:'说明'})[group.severity]||group.severity;
      const badge=el('span',label,`report-severity${group.severity==='error'?' report-error':group.severity==='warning'?' report-warning':''}`);heading.append(badge,el('code',group.code),el('span',`${group.count} 条`,'report-count'));
      card.append(heading,el('p',group.message));if(group.entityIds.length)card.append(el('p',`任务 / 对象：${group.entityIds.join('、')}${group.projectLevel?'；另有项目级提示':''}`,'report-entities'));else card.append(el('p','项目级提示','report-entities'));list.append(card);
    }container.append(list);
  }
  function renderSource() {const source=state.importSource;$('sourcePanel').hidden=!source;if(!source)return;$('sourceState').textContent=state.project?'随保存版本恢复的来源记录':'本地导入草稿 · 尚未保存';renderImportSummary($('sourceSummary'),source,state.plan.tasks.length,{saved:true});$('sourceMetadata').textContent=JSON.stringify(source,null,2);const rows=new Map((current()?state.result.tasks:[]).map((r)=>[r.id,r]));$('sourceDateComparison').replaceChildren(resultTable(['任务','原文件开始 / 完成','当前计算开始 / 完成'],Object.entries(source.original_dates||{}).map(([id,dates])=>[id,`${dates.start||'—'} / ${dates.end||'—'}`,rows.has(id)?`${rows.get(id).start} / ${rows.get(id).end}`:'待计算'])));}
  function render() {renderConversation();renderBundle();renderCalendar();renderTasks();renderResources();renderResult();renderBaseline();renderWeekly();renderOptimization();renderSource();controls();}
  function acceptRun(payload) {if(!payload.result||!Array.isArray(payload.result.tasks))throw new Error('服务器未返回有效排程结果，原计划已保留。');state.plan=clone(payload.plan||state.plan);state.result=clone(payload.result);state.resultPlan=JSON.stringify(state.plan);state.resultStale=false;state.runId=payload.run_id||null;state.method=payload.method||'cpm';}
  async function calculate() {if(state.busy)return;try{settleEdits();assertReady();}catch(error){notify(error.message,true);return;}return action('calculate',async(token)=>{const data=await api('/calculate',{plan:state.plan},token);if(!owns(token))return;acceptRun(data);render();notify(`计算完成：${data.result.duration_workdays} 个工作日，计划完成 ${data.result.finish_date}。`);});}
  async function optimize() {if(state.busy)return;try{settleEdits();assertReady();}catch(error){notify(error.message,true);return;}return action('optimize',async(token)=>{const data=await api('/optimize',{plan:state.plan},token);if(!owns(token))return;if(!data.result?.tasks)throw new Error('资源调整未返回有效结果。');state.optimization=data;renderOptimization();notify('资源调整已计算，请先核对日期变化，再点击应用。当前计划尚未改变。');});}
  function showProject(project,runId) {state.proposal=null;$('planningReply').textContent='';$('confirmation').value='';state.project=clone(project);state.plan=clone(project.plan);state.name=project.name;state.weekly=clone(project.weekly||[]);state.baseline=clone(project.baseline||null);state.pending.clear();state.history=[];state.optimization=null;state.importDraft=null;$('importPreview').hidden=true;state.synthetic=project.synthetic===true;state.sourceId=project.source_id||project.import_source?.source_id||null;state.importSource=clone(project.import_source||null);state.result=clone(project.result||null);state.resultPlan=state.result?JSON.stringify(state.plan):'';state.resultStale=false;state.runId=runId||null;state.method=project.method||'cpm';state.saved=JSON.stringify(snapshot());renderRevisions();render();}
  async function refreshProjects(token) {const data=await api('/projects',undefined,token);if(token&&!owns(token))return;const selected=state.project?.id||$('recentPlans').value;$('recentPlans').replaceChildren(option('选择已保存计划',''));for(const project of data.projects||[])$('recentPlans').append(option(`${project.name} · 修订 ${project.revision}${project.error?' · 无法读取':''}`,project.id));$('recentPlans').value=selected;}
  async function save(copy=false) {if(state.busy)return;try{settleEdits();assertReady();if(state.method==='resource'&&(!current()||!state.runId))throw new Error('资源方案已修改或缺少有效计算记录。请先预览资源调整并应用调整，再保存；当前编辑与上次结果已保留。');if(!state.name.trim())throw new Error('请先填写计划名称。');}catch(error){notify(error.message,true);return;}return action('save',async(token)=>{const payload={name:state.name.trim(),plan:state.plan,weekly:state.weekly,synthetic:state.synthetic,method:state.method};if(state.project&&!copy){payload.id=state.project.id;payload.expected_revision=state.project.revision;}if(state.sourceId)payload.source_id=state.sourceId;if(current()&&state.runId)payload.run_id=state.runId;const data=await api('/projects',payload,token);if(!owns(token))return;showProject(data.project,data.run_id);notify(`已保存修订 ${data.project.revision}，可重新打开继续编辑。`);await refreshProjects(token);if(win?.history)win.history.replaceState(null,'',`?project_id=${encodeURIComponent(data.project.id)}`);});}
  async function open(id) {if(!id||state.busy||!discardAllowed())return;return action('load',async(token)=>{const data=await api(`/projects/${encodeURIComponent(id)}`,undefined,token);if(!owns(token))return;showProject(data.project,data.run_id);if(win?.history)win.history.replaceState(null,'',`?project_id=${encodeURIComponent(id)}`);notify(`已打开 ${data.project.name} · 修订 ${data.project.revision}。`);});}
  async function restoreRevision() {if(!state.project||!$('revisions').value||state.busy||!discardAllowed())return;const revision=$('revisions').value,id=state.project.id;return action('load',async(token)=>{const latest=await api(`/projects/${encodeURIComponent(id)}`,undefined,token);if(!owns(token))return;const historical=await api(`/projects/${encodeURIComponent(id)}?version=${encodeURIComponent(revision)}`,undefined,token);if(!owns(token))return;showProject(latest.project,latest.run_id);remember();state.plan=clone(historical.project.plan);state.weekly=clone(historical.project.weekly||[]);state.name=historical.project.name;state.synthetic=historical.project.synthetic===true;state.sourceId=historical.project.source_id||null;state.importSource=clone(historical.project.import_source||null);state.result=clone(historical.project.result);state.resultPlan=JSON.stringify(state.plan);state.method=historical.project.method||'cpm';state.runId=historical.run_id||null;render();notify(`已载入修订 ${revision} 为草稿；保存会在当前最新修订 ${latest.project.revision} 后创建新版本。`);});}
  async function undoSaved() {if(!state.project||dirty()||state.busy)return;return action('save',async(token)=>{const data=await api(`/projects/${encodeURIComponent(state.project.id)}/undo`,{expected_revision:state.project.revision},token);if(!owns(token))return;showProject(data.project,data.run_id);notify(`已恢复上次保存内容，当前为修订 ${data.project.revision}。`);await refreshProjects(token);});}
  async function captureBaseline() {if(!state.project||dirty()||!current()||state.busy)return;if(state.baseline&&!win.confirm('已有基线。是否用当前保存结果替换？旧版本仍可在历史中查看。'))return;return action('save',async(token)=>{const data=await api(`/projects/${encodeURIComponent(state.project.id)}/baseline`,{expected_revision:state.project.revision},token);if(!owns(token))return;showProject(data.project,data.run_id);notify('已锁定当前保存结果作为基线。后续输入修改不会自动覆盖基线。');await refreshProjects(token);});}
  async function example() {if(state.busy||!discardAllowed())return;return action('load',async(token)=>{const data=await api('/example',undefined,token);if(!owns(token))return;newLocal(data.plan,'合成教学施工计划',true);notify('已载入合成演示。参数是教学样例，先计算即可查看关键路径。');});}
  function newLocal(plan=blankPlan(),name='',synthetic=false) {state.proposal=null;$('planningReply').textContent='';state.plan=clone(plan);state.name=name;state.weekly=[];state.project=null;state.baseline=null;state.result=null;state.resultPlan='';state.resultStale=false;state.runId=null;state.method='cpm';state.history=[];state.pending.clear();state.optimization=null;state.importDraft=null;state.sourceId=null;state.importSource=null;state.synthetic=synthetic;state.saved=plan.tasks.length?'':JSON.stringify(snapshot());$('importPreview').hidden=true;$('confirmation').value='';renderRevisions();render();if(win?.history)win.history.replaceState(null,'',win.location.pathname);}
  function undo() {if(state.busy)return;if(state.pending.size){state.pending.clear();render();notify('已撤回尚未应用的字段输入。');return;}const old=state.history.pop();if(!old)return;Object.assign(state,old);state.optimization=null;render();notify(current()?'已撤销一次编辑，并恢复对应的成功计算结果。':'已撤销一次编辑；请重新计算以更新结果。');}
  async function importFile() {const file=$('importFile').files?.[0];if(!file||state.busy){if(!file)notify('请先选择计划文件。',true);return;}return action('import',async(token)=>{const body=new FormData();body.append('file',file);const data=await api('/import',body,token);if(!owns(token))return;state.importDraft=data;renderImportSummary($('importSummary'),data,data.plan?.tasks?.length||0);$('importReport').textContent=JSON.stringify({source:data.source,report:data.report,original_dates:data.original_dates},null,2);$('importPreview').hidden=false;$('confirmImport').checked=false;notify('文件已解析。请核对导入报告，确认后才会应用为新计划。');});}
  function applyImport() {if(!state.importDraft||!$('confirmImport').checked||state.busy||!discardAllowed())return;const imported=state.importDraft;newLocal(imported.plan,$('importFile').files?.[0]?.name.replace(/\.[^.]+$/,'')||'导入计划',false);state.sourceId=imported.source_id||null;state.importSource={source:imported.source,report:imported.report,original_dates:imported.original_dates};renderSource();controls();notify('导入内容已应用为新计划草稿；尚未保存。请检查工作日历、关系类型与工期，再计算。');}
  async function exportFile() {if(state.busy)return;try{settleEdits();assertReady();if(!current()||!state.runId)throw new Error(state.method==='resource'?'资源方案已修改或缺少有效计算记录。请先预览资源调整并应用调整，再导出；当前编辑与上次结果已保留。':'请先计算关键路径，取得与当前输入一致的结果后再导出。');if(!['我明白，将由持证人员签认','I understand; a licensed person will sign this off.'].includes($('confirmation').value))throw new Error('请完整输入签认提示后导出。');}catch(error){notify(error.message,true);return;}return action('export',async(token)=>{const format=$('exportFormat').value,payload={plan:state.plan,format,confirmation:$('confirmation').value,run_id:state.runId};const response=await request('/export',payload,token),blob=await response.blob();if(!owns(token))return;const url=URL.createObjectURL(blob),link=el('a');link.href=url;link.download=`${state.name.replace(/[<>:"/\\|?*\x00-\x1f]/g,'_')||'planning'}.${format}`;document.body.append(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);notify('导出文件已生成，包含当前明确输入。请重新打开并核对交接结果。');});}
  const chatContext = () => ({plan:state.plan,method:state.method,...(state.project?{project_id:state.project.id,expected_revision:state.project.revision}:{})});
  function renderConversation() {
    const proposal=state.proposal;$('planningProposal').hidden=!proposal;
    $('unifiedChat').hidden=!state.project;$('unifiedChat').href=state.project?`/?planning_project_id=${state.project.id}`:'/';
    if(!proposal)return;
    $('planningChanges').replaceChildren(resultTable(['参数','原值','新值'],(proposal.changes||[]).map(row=>[row.parameter,JSON.stringify(row.before),JSON.stringify(row.after)])));
    $('applyPlanningProposal').textContent=proposal.action==='undo'?'确认撤销上次保存':'确认应用并计算';
    $('proposalHint').textContent=proposal.action==='undo'?'将恢复上次保存内容，并创建新修订；当前未保存编辑必须先处理。':'确认后使用现有计算器更新草稿；失败保留原计划。随后可撤销编辑或保存新版本。';
  }
  function renderBundle() {
    const status=state.project?.bundle_status;
    $('missingSourcePanel').hidden=!status?.missing_sources?.length;
    $('bundleStatus').textContent=!state.project?'请先保存计划，再导出完整项目包。':!status?'此项目尚未返回原件完整性记录，请重新打开。':status.complete?`已保存修订 ${state.project.revision}：原件 ${status.source_file_count} 份，基线、周承诺及保留历史可随包交接。`:`原件尚缺 ${status.missing_sources.length} 份：${status.missing_sources.map(item=>item.filename).join('、')}。可导出保留缺项说明的项目包，或先补齐原件。`;
  }
  async function conversation() {
    if(state.busy)return;
    try{settleEdits();assertReady();if(!$('planningMessage').value.trim())throw new Error('请输入排程指令。');}catch(error){notify(error.message,true);return;}
    return action('conversation',async(token)=>{
      const body={...chatContext(),message:$('planningMessage').value.trim()};if(current()&&state.runId)body.run_id=state.runId;
      const data=await api('/conversation',body,token);if(!owns(token))return;
      $('planningReply').textContent=data.reply;state.proposal=data.proposal_id?data:null;renderConversation();notify(data.ok?'对话已完成；参数建议等待你核对。':'本次指令未应用，见对话说明。',!data.ok);
    });
  }
  async function applyProposal() {
    if(state.busy||!state.proposal)return;
    try{settleEdits();assertReady();if(state.proposal.action==='undo'&&dirty())throw new Error('请先保存或撤回未保存编辑，再撤销上次保存。');}catch(error){notify(error.message,true);return;}
    return action(state.proposal.action==='undo'?'save':'applyProposal',async(token)=>{
      const data=await api(`/proposals/${state.proposal.proposal_id}/apply`,{...chatContext(),confirmed:true},token);if(!owns(token))return;
      if(data.project){showProject(data.project,data.run_id);await refreshProjects(token);if(!owns(token))return;notify('已撤销上次保存并创建新修订。');}
      else{remember();acceptRun(data);state.proposal=null;state.optimization=null;render();notify(`已按确认参数计算：${data.result.duration_workdays} 工作日。当前为草稿，可撤销编辑；保存后才会保留新修订。`);}
    });
  }
  async function downloadResponse(response,name,token){const blob=await response.blob();if(!owns(token))return;const url=URL.createObjectURL(blob),link=el('a');link.href=url;link.download=name;document.body.append(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);}
  async function exportBundle() {
    if(state.busy)return;
    try{settleEdits();if(!state.project||dirty())throw new Error('请先保存当前修改，再导出项目包。');if(!['我明白，将由持证人员签认','I understand; a licensed person will sign this off.'].includes($('confirmation').value))throw new Error('请完整输入签认提示后导出。');}catch(error){notify(error.message,true);return;}
    return action('export',async(token)=>{const response=await request(`/projects/${state.project.id}/export`,{expected_revision:state.project.revision,confirmation:$('confirmation').value},token);await downloadResponse(response,'civil-planning-project.zip',token);if(owns(token))notify(state.project.bundle_status?.complete?'项目包已生成，包含已保存计划、基线、周承诺、保留历史及原件。':'项目包已生成，并保留缺失原件说明；当前包尚不含全部原件。');});
  }
  async function importBundle() {
    const file=$('bundleFile').files?.[0];if(state.busy||!file){if(!file)notify('请选择 ZIP 项目包。',true);return;}if(!discardAllowed())return;
    // Import commits a new copy atomically; do not offer cancellation after it commits.
    return action('save',async(token)=>{const body=new FormData();body.append('file',file);const data=await api('/projects/import',body,token);if(!owns(token))return;showProject(data.project,data.run_id);await refreshProjects(token);if(!owns(token))return;win?.history?.replaceState(null,'',`?project_id=${data.project.id}`);notify('项目包已导入为新副本，版本、基线及周承诺已恢复；签认确认已重置。资源方案保留原记录，未重新求解最优性。');});
  }
  async function attachSource() {
    const file=$('missingSourceFile').files?.[0];if(state.busy||!state.project||dirty()||!file){if(!file)notify('请选择缺失的原文件。',true);return;}
    return action('save',async(token)=>{const body=new FormData();body.append('file',file);const data=await api(`/projects/${state.project.id}/source?expected_revision=${state.project.revision}`,body,token);if(!owns(token))return;showProject(data.project,data.run_id);notify('原件摘要匹配，已补齐并保存为新修订；原排程结果保留。');await refreshProjects(token);});
  }
  function bindField(id,key,read,assign,planChanged=true){const node=$(id);committers.set(key,{parse:(text)=>{const value=read(text);return()=>assign(value);},planChanged});node.addEventListener('input',()=>{state.pending.set(key,{value:node.value});controls();});node.addEventListener('change',()=>{try{const value=read(node.value);state.pending.delete(key);edit(()=>assign(value),planChanged);}catch(error){state.pending.set(key,{value:node.value,error:error.message});notify(error.message,true);controls();}});}
  bindField('planName','name',(v)=>v,(v)=>{state.name=v;},false);
  bindField('startDate','start',(v)=>{if(!validDate(v))throw new Error('计划开始日期无效。');return v;},(v)=>{state.plan.start_date=v;});
  bindField('holidays','holidays',(v)=>{const dates=v.split(/[\s,，;；]+/).filter(Boolean);if(dates.some((d)=>!validDate(d)))throw new Error('停工日请用 YYYY-MM-DD，每行一个日期。');return[...new Set(dates)].sort();},(v)=>{state.plan.calendar.holidays=v;});
  $('calculate').onclick=calculate;$('optimize').onclick=optimize;$('savePlan').onclick=()=>save();$('saveCopy').onclick=()=>save(true);$('cancelRequest').onclick=cancel;$('undoEdit').onclick=undo;
  $('applyInputs').onclick=()=>{if(state.busy)return;try{settleEdits();notify('输入已应用。修改任务参数后请重新计算，再保存版本。');}catch(error){notify(error.message,true);controls();}};
  $('newPlan').onclick=()=>{if(!state.busy&&discardAllowed()){newLocal();notify('已新建空计划。请填写真实任务与工作日历。');}};$('loadExample').onclick=example;
  $('openPlan').onclick=()=>open($('recentPlans').value);$('refreshPlans').onclick=()=>action('load',refreshProjects);$('restoreRevision').onclick=restoreRevision;$('revisions').onchange=controls;$('undoSaved').onclick=undoSaved;$('captureBaseline').onclick=captureBaseline;
  $('addTask').onclick=()=>{if(state.plan.tasks.length>=250){notify('一个计划最多 250 项任务。',true);return;}edit(()=>{let i=1;while(state.plan.tasks.some((t)=>t.id===`T${i}`))i++;state.plan.tasks.push({id:`T${i}`,name:'',duration:null,progress:0,parent_id:null,dependencies:[],resources:{},actual_start:null,actual_finish:null});});};
  $('addResource').onclick=()=>{if(state.plan.resources.length>=32){notify('一个计划最多 32 种资源。',true);return;}edit(()=>{let i=1;while(state.plan.resources.some((r)=>r.id===`R${i}`))i++;state.plan.resources.push({id:`R${i}`,name:'',capacity:null});});};
  $('applyOptimization').onclick=()=>{if(!state.optimization||state.busy||state.pending.size)return;remember();const preview=state.optimization;state.optimization=null;acceptRun(preview);render();notify('已应用资源调整结果，尚未保存；可撤销或另存版本。');};$('discardOptimization').onclick=()=>{state.optimization=null;renderOptimization();controls();notify('已放弃资源调整预览。');};
  for(const node of document.querySelectorAll('[data-view]'))node.onclick=()=>{state.view=node.dataset.view;renderResult();controls();};$('viewMode').onchange=drawGantt;
  $('weeklyFilter').onchange=renderWeekly;$('promiseForm').onsubmit=(event)=>{event.preventDefault();if(state.busy)return;const week=$('promiseWeek').value,task=$('promiseTask').value;if(!validDate(week)||new Date(`${week}T12:00:00Z`).getUTCDay()!==1){notify('周承诺起始日必须是有效的周一日期。',true);return;}if(!state.plan.tasks.some((t)=>t.id===task)){notify('请选择已有任务。',true);return;}if(state.weekly.some((w)=>w.task_id===task&&w.week_start===week)){notify('同一任务本周已有承诺，请直接编辑状态。',true);return;}edit(()=>{state.weekly.push({id:`W${globalThis.crypto.randomUUID().replaceAll('-','')}`,task_id:task,week_start:week,status:'planned',reason:'',constraints:''});},false);$('weeklyFilter').value=week;renderWeekly();};
  $('importPlan').onclick=importFile;$('applyImport').onclick=applyImport;$('confirmImport').onchange=controls;$('discardImport').onclick=()=>{state.importDraft=null;$('importPreview').hidden=true;controls();};$('exportPlan').onclick=exportFile;
  $('accessForm').onsubmit=async(event)=>{event.preventDefault();document.cookie=`cb_token=${encodeURIComponent($('accessToken').value.trim())}; path=/; max-age=2592000; SameSite=Lax`;$('accessToken').value='';await action('load',async(token)=>{await refreshProjects(token);if(!owns(token))return;$('accessForm').hidden=true;notify('口令已验证，请重试刚才的操作。');});};
  const unload=(event)=>{if(dirty()){event.preventDefault();event.returnValue='';}};win?.addEventListener?.('beforeunload',unload);
  state.saved=JSON.stringify(snapshot());render();
  $('planningChatForm').onsubmit=(event)=>{event.preventDefault();return conversation();};$('applyPlanningProposal').onclick=applyProposal;$('discardPlanningProposal').onclick=()=>{state.proposal=null;renderConversation();controls();};$('exportBundle').onclick=exportBundle;$('importBundle').onclick=importBundle;$('attachSource').onclick=attachSource;
  const ready=options.initialize===false?Promise.resolve():action('load',async(token)=>{await refreshProjects(token);if(!owns(token))return;const capabilities=await api('/capabilities',undefined,token).catch(()=>null);if(!owns(token))return;if(capabilities?.formats?.mpxj?.available===false)$('importHint').textContent='可导入 JSON、CSV、XLSX、MS Project XML。当前服务器未启用 MPP / P6 转换；MPP、XER、PMXML 请先在源软件导出本工作台支持的 Project XML 再导入。';else if(capabilities?.formats?.mpxj?.available===true)$('importHint').textContent='可导入 JSON、CSV、XLSX、MS Project XML，以及 MPP / P6（XER、PMXML）。MPXJ 与 JVM 已就绪；转换差异会逐项列入导入报告。';const id=new URLSearchParams(win?.location?.search||'').get('project_id');if(id&&/^[a-f0-9]{32}$/.test(id)){const data=await api(`/projects/${id}`,undefined,token);if(owns(token)){showProject(data.project,data.run_id);notify(`已恢复 ${data.project.name} · 修订 ${data.project.revision}。`);const proposalId=new URLSearchParams(win?.location?.search||'').get('proposal_id');if(proposalId&&/^[a-f0-9]{32}$/.test(proposalId)){const proposal=await api(`/proposals/${proposalId}`,undefined,token);if(owns(token)){state.proposal=proposal;renderConversation();}}}}});
  return{state,ready,calculate,optimize,save,open,undo,cancel,example,conversation,applyProposal,exportBundle,importBundle,attachSource,importFile,applyImport,exportFile,restoreRevision,captureBaseline,newLocal,render,current,dirty,dispose(){controller?.abort();state.sequence++;win?.removeEventListener?.('beforeunload',unload);}};
}
if(typeof document!=='undefined'&&document.getElementById('taskRows'))startPlanningApp(document);
