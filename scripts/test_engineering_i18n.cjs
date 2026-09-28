/* Offline language-switch acceptance using the actual page controllers. */
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {JSDOM} = require('jsdom');
const root = path.join(__dirname, '../demo/static');
const read = file => fs.readFileSync(path.join(root,file),'utf8');
const response = data => Promise.resolve({ok:true,status:200,headers:{get:()=> 'application/json'},json:async()=>data});
async function page(name, locale='zh-CN') {
  const dom = new JSDOM(read(`${name}.html`),{url:`http://localhost/${name}`,runScripts:'outside-only',pretendToBeVisual:true});
  const win=dom.window;
  win.localStorage.setItem('cb_locale_v1',locale);
  win.fetch=()=>response({projects:[],available:false,missing_dependencies:[]});
  win.eval(read('i18n.js'));win.eval(read('i18n-engineering.js'));
  await new Promise(resolve=>win.document.addEventListener('DOMContentLoaded',resolve,{once:true}));
  return {dom,win,doc:win.document};
}
function controller(win,file,name) {
  const source=read(file).replace(/^import Gantt[^\n]+\n/,'const Gantt=class {};\n').replace(/^export /gm,'').replace(/^if \(typeof (?:document|window).*$/gm,'').replace(/^if\(typeof document.*$/gm,'');
  win.eval(source+`\nwindow.__start = ${name};`);return win.__start;
}
test('all five engineering pages translate headings, labels and attributes and switch back',async()=>{
  for(const name of ['cad','engineering','engineering-planning','engineering-schedule','engineering-routing']){
    const {dom,win,doc}=await page(name);
    const heading=doc.querySelector('h1').textContent;
    win.CBI18n.setLocale('en');assert.equal(doc.documentElement.lang,'en');
    assert.doesNotMatch(doc.querySelector('h1').textContent,/[\u3400-\u9fff]/);
    assert.equal(doc.querySelector('[data-cb-language-toggle]').textContent,'中文');
    for(const node of doc.querySelectorAll('[placeholder],[title],[aria-label]'))for(const attr of ['placeholder','title','aria-label']){
      const value=node.getAttribute(attr);if(value&&!node.hasAttribute('data-cb-language-toggle'))assert.doesNotMatch(value,/[\u3400-\u9fff]/,`${name} ${attr}: ${value}`);
    }
    win.CBI18n.setLocale('zh-CN');assert.equal(doc.querySelector('h1').textContent,heading);dom.window.close();
  }
});
test('catalog preserves every named placeholder and has no untranslated values',()=>{
  let catalog;vm.runInNewContext(read('i18n-engineering.js'),{window:{CBI18n:{add:value=>catalog=value}}});
  assert.ok(Object.keys(catalog).length>=1100);
  const tokens=s=>[...s.matchAll(/\{p\d+\}/g)].map(m=>m[0]).sort();
  for(const [source,english]of Object.entries(catalog)){assert.deepEqual(tokens(source),tokens(english),source);assert.doesNotMatch(english,/[\u3400-\u9fff]/,source);}
});
test('route switching preserves graph data and unapplied JSON while updating dynamic controls',async()=>{
  const {dom,win,doc}=await page('engineering-routing');const app=controller(win,'engineering-routing.js','startRoutingApp')(doc);
  doc.getElementById('example').click();app.state.model.nodes[0].name='工作台';app.state.model.edges[0].source='原文：施工记录';
  const model=JSON.stringify(app.state.model);doc.getElementById('modelJson').value='{"unfinished":';doc.getElementById('modelJson').dispatchEvent(new win.Event('input'));
  const revision=app.state.revision,history=JSON.stringify(app.state.history);
  win.CBI18n.setLocale('en');assert.equal(JSON.stringify(app.state.model),model);assert.equal(doc.getElementById('modelJson').value,'{"unfinished":');assert.equal(app.state.revision,revision);assert.equal(JSON.stringify(app.state.history),history);
  assert.match(doc.getElementById('inputState').textContent,/JSON edits are unapplied/);assert.equal(doc.querySelector('#nodes input').value,'工作台');
  assert.equal(doc.querySelector('#nodes button').textContent,'Delete');win.CBI18n.setLocale('zh-CN');assert.equal(doc.querySelector('#nodes button').textContent,'删除');app.dispose();dom.window.close();
});
test('planning language switching preserves pending inputs, project names and result values',async()=>{
  const {dom,win,doc}=await page('engineering-planning');const app=controller(win,'engineering-planning.js','startPlanningApp')(doc,{initialize:false,window:win,fetch:win.fetch});
  app.newLocal({start_date:'2026-09-21',calendar:{weekdays:[0,1,2,3,4],holidays:[]},tasks:[{id:'A',name:'工作台',duration:2,progress:0,parent_id:null,dependencies:[],resources:{},actual_start:null,actual_finish:null}],resources:[]},'原项目名称',false);
  const input=doc.querySelector('#taskRows input[aria-label="A 工期"]');input.value='';input.dispatchEvent(new win.Event('input'));
  const plan=JSON.stringify(app.state.plan),history=JSON.stringify(app.state.history);
  win.CBI18n.setLocale('en');assert.equal(JSON.stringify(app.state.plan),plan);assert.equal(JSON.stringify(app.state.history),history);assert.equal(doc.getElementById('planName').value,'原项目名称');
  assert.equal(doc.querySelector('#taskRows input[aria-label="A duration"]').value,'');assert.equal(doc.querySelector('#taskRows input[aria-label="A name"]').value,'工作台');assert.equal(app.state.pending.size,1);
  assert.equal(doc.querySelector('#weekdays label:last-child span').textContent,'Sun');win.CBI18n.setLocale('zh-CN');assert.equal(doc.querySelector('#taskRows input[aria-label="A 工期"]').value,'');app.dispose();dom.window.close();
});
test('CAD role controls update in both directions without applying numeric drafts',async()=>{
  const {dom,win,doc}=await page('cad','en');const app=await controller(win,'cad.js','startCadApp')(doc);
  const height=doc.querySelector('[data-role="wall"] input');height.value='3.75';doc.getElementById('projectName').value='保留项目';
  win.CBI18n.setLocale('zh-CN');assert.equal(doc.querySelector('[data-role="wall"] span').textContent,'墙体');assert.equal(height.value,'3.75');
  win.CBI18n.setLocale('en');assert.equal(doc.querySelector('[data-role="wall"] span').textContent,'Walls');assert.equal(height.value,'3.75');assert.equal(doc.getElementById('projectName').value,'保留项目');assert.equal(app.model,null);app.dispose();dom.window.close();
});
