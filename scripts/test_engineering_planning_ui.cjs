#!/usr/bin/env node
// Offline acceptance of real handlers: no model endpoint, browser or solver mock claims.
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const html=fs.readFileSync(path.join(__dirname,'../demo/static/engineering-planning.html'),'utf8');
const source=fs.readFileSync(path.join(__dirname,'../demo/static/engineering-planning.js'),'utf8').replace(/^import Gantt[^\n]+\n/,'const Gantt=class {};\n');
const modulePromise=import('data:text/javascript;base64,'+Buffer.from(source).toString('base64'));
const copy=value=>JSON.parse(JSON.stringify(value));
const response=(data,status=200)=>new Response(JSON.stringify(data),{status,headers:{'Content-Type':'application/json'}});
const deferred=()=>{let resolve;const promise=new Promise(r=>resolve=r);return{promise,resolve};};
const plan={start_date:'2026-09-21',calendar:{weekdays:[0,1,2,3,4],holidays:[]},tasks:[{id:'A',name:'合成：准备',duration:2,progress:0,parent_id:null,dependencies:[],resources:{},actual_start:null,actual_finish:null}],resources:[]};
const result={tasks:[{id:'A',start:'2026-09-21',end:'2026-09-22',early_start:'2026-09-21',early_finish:'2026-09-22',late_start:'2026-09-21',late_finish:'2026-09-22',total_float:0,critical:true}],finish_date:'2026-09-22',duration_workdays:2,critical_task_ids:['A'],warnings:[]};
function run(method='cpm'){return{plan:copy(plan),result:copy(result),method,run_id:'a'.repeat(32)};}
function project(revision=1,extra={}){return{id:'b'.repeat(32),name:'教学计划',revision,plan:copy(plan),result:copy(result),weekly:[],baseline:null,method:'cpm',synthetic:true,versions:[],can_undo:false,...extra};}
class Node {
  constructor(tag,doc){this.tagName=tag;this.doc=doc;this.children=[];this.listeners={};this.style={};this.dataset={};this.attributes={};this.value='';this.checked=false;this.disabled=false;this.hidden=false;this.textContent='';this.files=[];doc.nodes.push(this);}
  append(...nodes){this.children.push(...nodes);} replaceChildren(...nodes){this.children=[...nodes];}
  setAttribute(key,value){this.attributes[key]=String(value);} getAttribute(key){return this.attributes[key];}
  addEventListener(name,fn){(this.listeners[name]||=[]).push(fn);}
  async emit(name,event={}){const ev={target:this,preventDefault(){},...event};const tasks=(this.listeners[name]||[]).map(fn=>fn(ev));if(this['on'+name])tasks.push(this['on'+name](ev));await Promise.all(tasks);}
  async click(){if(!this.disabled)await this.emit('click');}
  remove(){} focus(){this.doc.activeElement=this;} blur(){this.doc.activeElement=null;}
}
function dom(){const doc={nodes:[],ids:{},views:[],activeElement:null,cookie:'',createElement(tag){return new Node(tag,this);},createElementNS(ns,tag){return this.createElement(tag);},getElementById(id){return this.ids[id];},querySelectorAll(selector){if(selector==='[data-view]')return this.views;assert.equal(selector,'main button,main input,main select,main textarea');return this.nodes.filter(n=>['button','input','select','textarea'].includes(n.tagName));}};
  for(const m of html.matchAll(/<([a-z]+)\b([^>]*?)>/g)){const attrs=m[2],id=/\bid="([^"]+)"/.exec(attrs),view=/\bdata-view="([^"]+)"/.exec(attrs);if(!id&&!view)continue;const node=doc.createElement(m[1]);if(id)doc.ids[id[1]]=node;if(view){node.dataset.view=view[1];doc.views.push(node);}}
  doc.body=doc.createElement('body');doc.ids.viewMode.value='Week';doc.ids.exportFormat.value='json';return doc;
}
async function app(fetcher=async()=>response({projects:[]}),options={}){
  const doc=dom(),charts=[],win={location:{pathname:'/engineering/planning',search:''},history:{replaceState(){}},confirm:()=>true,addEventListener(){},removeEventListener(){}};
  const {startPlanningApp}=await modulePromise;
  const instance=startPlanningApp(doc,{initialize:false,fetch:fetcher,window:win,Gantt:class{constructor(node,tasks){charts.push(tasks);}},...options});
  instance.newLocal(plan,'教学计划',true);return{instance,doc,charts};
}
const text=(node)=>node.textContent+' '+node.children.map(text).join(' ');

test('dependencies accept four types and bounded negative lag; resource parser rejects unsafe or duplicate input',async()=>{
  const {parseDependencies,parseResources}=await modulePromise;
  assert.deepEqual(parseDependencies('A:FS:0,A:SS:-2,B:FF:3,C:SF:0').map(d=>d.type),['FS','SS','FF','SF']);
  assert.throws(()=>parseDependencies('A:FS:0,A:FS:2'));assert.throws(()=>parseDependencies('A:FS:99999'));assert.throws(()=>parseResources('__proto__:2'));assert.throws(()=>parseResources('A:1,A:2'));
});

test('saved plan handoff carries exact revision/hash and refuses unapplied or unsaved changes',async()=>{
  const binding={kind:'saved_planning',project_id:'b'.repeat(32),revision:3,inputs_sha256:'a'.repeat(64),source_sha256:null,confirmed_solid:false};
  const {instance,doc}=await app(async()=>response({project:project(3,{agent_selection:binding}),run_id:'a'.repeat(32)}),{agentPlanningSupported:true});
  await instance.open(binding.project_id);assert.equal(doc.ids.unifiedChat.hidden,false);
  const query=new URL(doc.ids.unifiedChat.href,'http://localhost').searchParams;assert.equal(query.get('planning_project_id'),binding.project_id);assert.equal(query.get('planning_revision'),'3');assert.equal(query.get('planning_inputs_sha256'),binding.inputs_sha256);
  const field=doc.nodes.filter(n=>n.attributes['aria-label']==='A 工期').at(-1);field.value='3';await field.emit('input');
  assert.equal(doc.ids.unifiedChat.href,'#');assert.equal(doc.ids.unifiedChat.getAttribute('aria-disabled'),'true');
  let prevented=false;await doc.ids.unifiedChat.emit('click',{preventDefault(){prevented=true;}});assert.equal(prevented,true);assert.match(doc.ids.notice.textContent,/先保存/);
  instance.undo();assert.equal(new URL(doc.ids.unifiedChat.href,'http://localhost').searchParams.get('planning_revision'),'3');
});

test('older Agent capability or missing bound snapshot does not advertise a working handoff',async()=>{
  const old=await app(async()=>response({project:project(),run_id:'a'.repeat(32)}));await old.instance.open('b'.repeat(32));assert.equal(old.doc.ids.unifiedChat.hidden,true);assert.match(old.doc.ids.unifiedChatStatus.textContent,/尚不支持/);
  const updated=await app(async()=>response({project:project(),run_id:'a'.repeat(32)}),{agentPlanningSupported:true});await updated.instance.open('b'.repeat(32));assert.equal(updated.doc.ids.unifiedChat.href,'#');assert.match(updated.doc.ids.unifiedChatStatus.textContent,/缺少版本摘要/);
});
test('PPC includes unclosed commitments in denominator and records no fabricated completion',async()=>{
  const {weeklyPpc}=await modulePromise;assert.deepEqual(weeklyPpc([{status:'done'},{status:'planned'},{status:'missed'}]),{total:3,done:1,planned:1,missed:1,percent:33.3});assert.equal(weeklyPpc([]).percent,null);
});
test('input then explicit apply works without relying on blur or change',async()=>{
  const {instance,doc}=await app();doc.ids.planName.value='仅触发 input 的名字';await doc.ids.planName.emit('input');assert.equal(instance.state.pending.size,1);assert.equal(doc.ids.applyInputs.disabled,false);await doc.ids.applyInputs.click();assert.equal(instance.state.name,'仅触发 input 的名字');assert.equal(instance.state.pending.size,0);
});
test('save commits valid pending fields first and retains synthetic provenance',async()=>{
  let submitted;const {instance,doc}=await app(async(url,init)=>{if(url.endsWith('/projects')&&init.method==='POST'){submitted=JSON.parse(init.body);return response({project:project(1,{name:submitted.name}),run_id:'c'.repeat(32)});}return response({projects:[]});});
  doc.ids.planName.value='自动应用名称';await doc.ids.planName.emit('input');assert.equal(doc.ids.savePlan.disabled,false);await instance.save();assert.equal(submitted.name,'自动应用名称');assert.equal(submitted.synthetic,true);assert.equal(instance.state.pending.size,0);assert.equal(instance.state.runId,'c'.repeat(32));
});
test('invalid pending duration cannot compute or save stale parameters and can be undone',async()=>{
  let calls=0;const {instance,doc}=await app(async()=>{calls++;return response(run());});const duration=doc.nodes.find(n=>n.attributes['aria-label']==='A 工期');duration.value='';await duration.emit('input');await instance.calculate();await instance.save();assert.equal(calls,0);assert.equal(instance.state.plan.tasks[0].duration,2);assert.equal(instance.state.pending.size,1);instance.undo();assert.equal(instance.state.pending.size,0);assert.equal(instance.state.plan.tasks[0].duration,2);
});
test('cancel isolates late successful calculations from newer input',async()=>{
  const old=deferred();const {instance}=await app((url)=>url.endsWith('/calculate')?old.promise:Promise.resolve(response({ok:true})));const pending=instance.calculate();await instance.cancel();instance.newLocal({...copy(plan),start_date:'2026-10-05'},'新输入');old.resolve(response(run()));await pending;assert.equal(instance.state.plan.start_date,'2026-10-05');assert.equal(instance.state.result,null);
});
test('409 conflict preserves edits and revision for an explicit retry or copy',async()=>{
  const {instance,doc}=await app(async()=>response({detail:'版本已改变'},409));instance.state.project=project();instance.state.plan.tasks[0].duration=3;await instance.save();assert.equal(instance.state.plan.tasks[0].duration,3);assert.equal(instance.state.project.revision,1);assert.match(doc.ids.notice.textContent,/版本冲突/);
});
test('failed calculation keeps the previous result and marks it stale after editing',async()=>{
  let count=0;const {instance,doc}=await app(async()=>++count===1?response(run()):response({detail:'循环依赖'},422));await instance.calculate();const field=doc.nodes.filter(n=>n.attributes['aria-label']==='A 工期').at(-1);field.value='3';await field.emit('input');await instance.calculate();assert.equal(instance.state.result.duration_workdays,2);assert.equal(instance.current(),false);assert.match(doc.ids.resultBadge.textContent,/已过期/);assert.equal(instance.state.plan.tasks[0].duration,3);
});
test('resource proposal is not applied until confirmed; null CPM fields remain absent and undo restores result',async()=>{
  const optimized=run('resource');optimized.result={...copy(result),duration_workdays:3,finish_date:'2026-09-23',critical_task_ids:[],solver_status:'FEASIBLE',proven_optimal:false,tasks:[{...result.tasks[0],end:'2026-09-23',early_start:null,early_finish:null,late_start:null,late_finish:null,total_float:null,critical:false}]};
  const {instance,doc}=await app(async url=>response(url.endsWith('/optimize')?optimized:run()));await instance.calculate();await instance.optimize();assert.equal(instance.state.method,'cpm');await doc.ids.applyOptimization.click();assert.equal(instance.state.method,'resource');assert.match(text(doc.ids.resultRows),/未评估/);assert.match(doc.ids.engineStatus.textContent,/未声明最优/);assert.equal(instance.state.result.tasks[0].total_float,null);instance.undo();assert.equal(instance.state.method,'cpm');assert.equal(instance.state.result.duration_workdays,2);
});
test('import requires report confirmation and then retains source metadata for saving',async()=>{
  const {instance,doc}=await app();const imported=copy(plan);imported.tasks[0].name='导入任务';instance.state.importDraft={plan:imported,source_id:'d'.repeat(32),source:{filename:'original.xml'},report:[],original_dates:{A:{start:'2026-09-21',end:'2026-09-22'}}};
  instance.applyImport();assert.equal(instance.state.plan.tasks[0].name,'合成：准备');doc.ids.confirmImport.checked=true;instance.applyImport();assert.equal(instance.state.plan.tasks[0].name,'导入任务');assert.equal(instance.state.sourceId,'d'.repeat(32));assert.equal(instance.state.synthetic,false);assert.equal(doc.ids.sourcePanel.hidden,false);
});
test('history restore keeps latest optimistic revision and uses historical import provenance',async()=>{
  const latest=project(5,{source_id:'e'.repeat(32),import_source:{source:{filename:'new.xml'}},synthetic:false}),historical=project(2,{source_id:'f'.repeat(32),import_source:{source:{filename:'old.xml'}},synthetic:true});
  const {instance,doc}=await app(async url=>response({project:url.includes('?version=')?historical:latest,run_id:'a'.repeat(32)}));instance.state.project=latest;doc.ids.revisions.value='2';await instance.restoreRevision();assert.equal(instance.state.project.revision,5);assert.equal(instance.state.sourceId,'f'.repeat(32));assert.equal(instance.state.importSource.source.filename,'old.xml');assert.equal(instance.state.synthetic,true);assert.equal(instance.dirty(),true);
});
test('untrusted task labels are escaped at the Frappe innerHTML boundary',async()=>{
  const payload=run();payload.plan.tasks[0].name='<img src=x onerror=alert(1)>';const {instance,charts}=await app(async()=>response(payload));await instance.calculate();assert.match(charts.at(-1)[0].name,/&lt;img/);assert.doesNotMatch(charts.at(-1)[0].name,/<img/);
});
test('editing resource-plan progress blocks save, save-copy and export instead of silently switching to CPM',async()=>{
  const optimized=run('resource');optimized.result.duration_workdays=11;optimized.result.finish_date='2026-10-05';const requests=[];
  const {instance,doc}=await app(async(url,init)=>{requests.push({url,body:init.body});return response(optimized);});
  await instance.optimize();await doc.ids.applyOptimization.click();assert.equal(instance.state.method,'resource');
  const progress=doc.nodes.filter(n=>n.attributes['aria-label']==='A 完成百分比').at(-1);progress.value='25';await progress.emit('input');
  await instance.save();assert.equal(instance.state.plan.tasks[0].progress,25);assert.equal(instance.state.runId,null);assert.match(doc.ids.notice.textContent,/预览资源调整并应用调整/);
  await instance.save(true);doc.ids.confirmation.value='我明白，将由持证人员签认';await instance.exportFile();
  assert.equal(requests.length,1,'only explicit optimization requested; no save or export request with a missing run');
  assert.equal(instance.state.method,'resource');assert.equal(instance.state.result.duration_workdays,11);assert.equal(instance.current(),false);assert.match(doc.ids.notice.textContent,/再导出/);
});
test('export refuses a never-calculated or edited CPM input rather than calculating implicitly',async()=>{
  let calls=0;const {instance,doc}=await app(async()=>{calls++;return response(run());});doc.ids.confirmation.value='我明白，将由持证人员签认';
  await instance.exportFile();assert.equal(calls,0);assert.match(doc.ids.notice.textContent,/先计算关键路径/);
  await instance.calculate();const duration=doc.nodes.filter(n=>n.attributes['aria-label']==='A 工期').at(-1);duration.value='3';await duration.emit('input');await instance.exportFile();
  assert.equal(calls,1);assert.equal(instance.state.plan.tasks[0].duration,3);assert.equal(instance.state.result.duration_workdays,2);assert.equal(instance.state.runId,null);
});
test('fresh resource save explicitly sends its method and run; subsequent export keeps the saved resource run',async()=>{
  const optimized=run('resource');optimized.result.duration_workdays=11;const savedRun='d'.repeat(32),requests=[];
  const {instance,doc}=await app(async(url,init)=>{
    if(url.endsWith('/optimize'))return response(optimized);
    if(url.endsWith('/projects')&&init.method==='POST'){const body=JSON.parse(init.body);requests.push({kind:'save',body});return response({project:project(2,{method:'resource',result:optimized.result}),run_id:savedRun});}
    if(url.endsWith('/export')){requests.push({kind:'export',body:JSON.parse(init.body)});return new Response('{}',{headers:{'Content-Type':'application/json'}});}
    return response({projects:[]});
  });
  await instance.optimize();await doc.ids.applyOptimization.click();await instance.save();doc.ids.confirmation.value='我明白，将由持证人员签认';await instance.exportFile();
  assert.equal(requests[0].body.method,'resource');assert.equal(requests[0].body.run_id,optimized.run_id);assert.equal(requests[1].kind,'export');assert.equal(requests[1].body.run_id,savedRun);assert.equal(instance.state.result.duration_workdays,11);assert.equal(instance.state.method,'resource');
});
test('import warning grouping preserves severity and exact message differences plus all entity IDs',async()=>{
  const {groupImportReports}=await modulePromise;
  const groups=groupImportReports([
    {code:'calendar',severity:'warning',message:'转换日历',entity_id:'A'},
    {code:'calendar',severity:'warning',message:'转换日历',entity_id:'B'},
    {code:'calendar',severity:'warning',message:'转换日历'},
    {code:'calendar',severity:'info',message:'转换日历',entity_id:'A'},
    {code:'calendar',severity:'warning',message:'保留日历',entity_id:'A'},
  ]);
  assert.equal(groups.length,3);assert.deepEqual(groups[0].entityIds,['A','B']);assert.equal(groups[0].count,3);assert.equal(groups[0].projectLevel,true);assert.equal(groups[1].severity,'info');assert.equal(groups[2].message,'保留日历');
});
test('pending import renders readable literal text, retains the full raw report, and still requires confirmation',async()=>{
  const payload={plan:copy(plan),source:{filename:'<img src=x>.mpp',format:'mpp',sha256:'fixture'},original_dates:{A:{start:'2026-09-21',end:'2026-09-22'}},report:[{code:'<svg>',severity:'warning',message:'<script>alert(1)</script>',entity_id:'A'},{code:'<svg>',severity:'warning',message:'<script>alert(1)</script>',entity_id:'B'}]};
  const {instance,doc}=await app(async()=>response(payload));doc.ids.importFile.files=[new Blob(['fixture'])];await instance.importFile();
  assert.equal(doc.ids.importPreview.hidden,false);assert.match(text(doc.ids.importSummary),/合并为 1 组/);assert.match(text(doc.ids.importSummary),/A、B/);assert.match(text(doc.ids.importSummary),/<script>alert\(1\)<\/script>/);assert.equal(doc.nodes.some(node=>node.tagName==='img'||node.tagName==='script'),false);assert.equal(JSON.parse(doc.ids.importReport.textContent).report.length,2);assert.equal(doc.ids.confirmImport.checked,false);assert.equal(doc.ids.applyImport.disabled,true);
  instance.applyImport();assert.equal(instance.state.project,null);assert.equal(instance.state.importSource,null);assert.equal(instance.state.plan.tasks[0].name,'合成：准备');
});
test('reopening a saved imported plan shows its source summary without a second import-apply workflow',async()=>{
  const source={source:{filename:'saved-original.mpp',format:'mpp',sha256:'kept-original-hash'},report:[{code:'source_dates',severity:'info',message:'核对源计划日期'}],original_dates:{A:{start:'2026-09-21',end:'2026-09-23'}}};
  const {instance,doc}=await app(async()=>response({project:project(3,{import_source:source,source_id:'f'.repeat(32),synthetic:false}),run_id:'a'.repeat(32)}));await instance.open('b'.repeat(32));
  assert.equal(doc.ids.sourcePanel.hidden,false);assert.match(text(doc.ids.sourceSummary),/saved-original.mpp/);assert.match(text(doc.ids.sourceSummary),/核对源计划日期/);assert.equal(JSON.parse(doc.ids.sourceMetadata.textContent).source.sha256,'kept-original-hash');assert.match(text(doc.ids.sourceDateComparison),/2026-09-23/);assert.equal(doc.ids.importPreview.hidden,true);assert.equal(instance.state.importDraft,null);assert.equal(doc.ids.applyImport.disabled,true);
});

test('conversation proposes without editing, explicit apply computes and undo restores success',async()=>{
  const suggestion={ok:true,reply:'等待核对',proposal_id:'e'.repeat(32),changes:[{parameter:'A.duration',before:2,after:3}],action:null};const changed=run();changed.plan.tasks[0].duration=3;changed.result.duration_workdays=3;
  const {instance,doc}=await app(async url=>response(url.endsWith('/conversation')?suggestion:url.endsWith('/apply')?changed:run()));
  await instance.calculate();doc.ids.planningMessage.value='把 A 的工期改为 3 工作日';await instance.conversation();assert.equal(instance.state.plan.tasks[0].duration,2);assert.equal(doc.ids.planningProposal.hidden,false);
  await instance.applyProposal();assert.equal(instance.state.plan.tasks[0].duration,3);assert.equal(instance.state.result.duration_workdays,3);assert.equal(instance.state.proposal,null);instance.undo();assert.equal(instance.state.plan.tasks[0].duration,2);assert.equal(instance.state.result.duration_workdays,2);
});
test('cancelled conversation cannot publish its old proposal over new input',async()=>{
  const old=deferred();const {instance,doc}=await app(url=>url.endsWith('/conversation')?old.promise:Promise.resolve(response({ok:true})));doc.ids.planningMessage.value='把 A 的工期改为 3 工作日';const pending=instance.conversation();await instance.cancel();instance.newLocal({...copy(plan),start_date:'2026-10-05'},'新计划');old.resolve(response({ok:true,reply:'old',proposal_id:'e'.repeat(32)}));await pending;assert.equal(instance.state.proposal,null);assert.equal(instance.state.plan.start_date,'2026-10-05');
});
test('stale application preserves edited input, previous results and proposal for review',async()=>{
  const {instance,doc}=await app(async()=>response({detail:'建议已过期'},409));instance.state.proposal={proposal_id:'e'.repeat(32),changes:[],action:null};instance.state.result=copy(result);instance.state.plan.tasks[0].duration=4;await instance.applyProposal();assert.equal(instance.state.plan.tasks[0].duration,4);assert.equal(instance.state.result.duration_workdays,2);assert.match(doc.ids.notice.textContent,/版本冲突/);
});
test('bundle import opens new copy, restores baseline and weekly, resets confirmation',async()=>{
  const restored=project(7,{baseline:{result:copy(result)},weekly:[{id:'W1',task_id:'A',week_start:'2026-09-21',status:'done',reason:'',constraints:''}],bundle_status:{complete:true,source_file_count:1,missing_sources:[]}});
  const {instance,doc}=await app(async url=>response(url.endsWith('/projects/import')?{project:restored,run_id:'a'.repeat(32),confirmation_reset:true}:{projects:[]}));doc.ids.bundleFile.files=[new Blob(['zip'])];doc.ids.confirmation.value='old';await instance.importBundle();assert.equal(instance.state.project.revision,7);assert.equal(instance.state.weekly.length,1);assert.deepEqual(instance.state.baseline,restored.baseline);assert.equal(doc.ids.confirmation.value,'');assert.equal(instance.dirty(),false);assert.match(doc.ids.bundleStatus.textContent,/原件 1/);
});
test('bundle export requires clean saved version and explicit confirmation',async()=>{
  let calls=0;const {instance,doc}=await app(async()=>{calls++;return response({});});instance.state.project=project();await instance.exportBundle();assert.equal(calls,0);assert.match(doc.ids.notice.textContent,/先保存/);instance.state.saved=JSON.stringify({plan:copy(plan),name:instance.state.name,weekly:[],synthetic:true,result:null,resultPlan:'',method:'cpm',sourceId:null,importSource:null});await instance.exportBundle();assert.equal(calls,0);assert.match(doc.ids.notice.textContent,/签认/);
});

test('confirmed saved undo is a non-cancellable commit and applies its final revision',async()=>{
  const commit=deferred();let request;const {instance,doc}=await app(async(url,init)=>{if(url.includes('/apply')){request=init;return commit.promise;}return response(url.endsWith('/projects')?{projects:[]}:{project:project(2,{can_undo:true}),run_id:'a'.repeat(32)});});
  await instance.open('b'.repeat(32));instance.state.proposal={proposal_id:'e'.repeat(32),action:'undo',changes:[]};const pending=instance.applyProposal();assert.equal(instance.state.busy,'save');assert.equal(doc.ids.cancelRequest.hidden,true);assert.equal(request.headers['X-CAD-Operation-ID'],undefined);commit.resolve(response({project:project(3),run_id:'a'.repeat(32)}));await pending;assert.equal(instance.state.project.revision,3);
});

test('imported optimality remains explicitly attributed to the stored solver record',async()=>{
 const imported=project(4,{method:'resource',result:{...copy(result),proven_optimal:true,solver_status:'OPTIMAL',engine:{name:'OR-Tools'}},bundle_origin:{optimality_reverified:false}});
 const {instance,doc}=await app(async()=>response({project:imported,run_id:'a'.repeat(32)}));await instance.open(imported.id);assert.match(doc.ids.engineStatus.textContent,/记录标注最优/);assert.match(doc.ids.engineStatus.textContent,/导入未重新证明/);assert.doesNotMatch(doc.ids.engineStatus.textContent,/已证明最优/);
});
