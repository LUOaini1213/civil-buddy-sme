// UI language changes must preserve unsaved material values and approval gates.
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const {JSDOM}=require('jsdom');
const root=path.join(__dirname,'..');
const read=p=>fs.readFileSync(path.join(root,p),'utf8');

test('logistics language switch keeps input, pending edits, sources and confirmation state',async()=>{
  const dom=new JSDOM(read('demo/static/logistics.html'),{url:'http://localhost/logistics',runScripts:'outside-only'});
  const {window}=dom;
  window.eval(read('demo/static/i18n.js'));
  window.eval(read('demo/static/i18n-logistics.js'));
  window.document.dispatchEvent(new window.Event('DOMContentLoaded'));
  globalThis.CBI18n=window.CBI18n;
  try{
    const source=read('demo/static/logistics.js');
    const {startLogisticsApp}=await import('data:text/javascript;base64,'+Buffer.from(source).toString('base64'));
    const app=startLogisticsApp(window.document,{window,initialize:false});
    const project={id:'a'.repeat(32),name:'施工计划',revision:1,confirmed:true,versions:[],document:{source:{filename:'原件.xlsx'},rows:[{id:'R00001',name:'柜型',quantity:2,unit:'原始单位',package_count:1,dimension_scope:'package',weight_scope:'package',evidence:{}}],totals:[]},audit:{issues:[]},summary:{totals:{package_count:1},quantities_by_unit:{原始单位:2}}};
    app.acceptProject(project);
    const $=id=>window.document.getElementById(id);
    $('projectName').value='未保存的项目名';$('chatInput').value='保留我的输入';$('confirmation').value='I understand; a licensed person will sign this off.';
    app.edit('R00001','quantity','12.5');
    window.CBI18n.setLocale('en');
    assert.match($('ledgerHead').textContent,/Package count/);
    assert.equal(window.document.querySelector('[data-row-id="R00001"][data-ledger-field="quantity"]').value,'12.5');
    assert.equal(window.document.querySelector('[data-row-id="R00001"][data-ledger-field="name"]').value,'柜型');
    assert.equal($('projectName').value,'未保存的项目名');assert.equal($('chatInput').value,'保留我的输入');
    assert.equal($('confirmation').value,'I understand; a licensed person will sign this off.');
    assert.equal(app.state.project.document.rows[0].quantity,2);assert.equal(app.state.pending.size,1);
    assert.equal($('calculate').disabled,true);assert.match($('projectState').textContent,/施工计划 · version 1/);
    assert.equal($('sourceName').textContent,'原件.xlsx');
    window.CBI18n.setLocale('zh-CN');
    assert.match($('ledgerHead').textContent,/包装数/);assert.equal(app.state.pending.size,1);
    assert.equal($('projectName').value,'未保存的项目名');assert.equal($('calculate').disabled,true);
  }finally{delete globalThis.CBI18n;dom.window.close();}
});

test('packing Vue template compiles and language updates keep user input and source records',async()=>{
  const source=read('frontend/workbench.html');
  const dom=new JSDOM(source,{url:'http://localhost/packing',runScripts:'outside-only',pretendToBeVisual:true});
  const {window}=dom;const warnings=[];
  window.console.error=(...args)=>warnings.push(args.join(' '));window.console.warn=(...args)=>warnings.push(args.join(' '));
  window.fetch=async()=>({ok:true,status:200,json:async()=>({ok:true,items:[],runs:[],checkpoints:[],experts:[],scenarios:[],profiles:[]})});
  window.WebSocket=class {close(){}};
  window.eval(read('demo/static/i18n.js'));window.eval(read('demo/static/i18n-logistics.js'));window.CBI18n.setLocale('en');
  window.eval(read('frontend/vendor/vue.min.js'));
  for(const script of window.document.scripts){
    const src=script.getAttribute('src');
    if(src?.startsWith('/static/vendor/') && !src.endsWith('vue.min.js'))window.eval(read('frontend/vendor/'+src.split('/').pop()));
    else if(!src && script.textContent.trim())window.eval(script.textContent);
  }
  window.document.dispatchEvent(new window.Event('DOMContentLoaded'));
  await new Promise(resolve=>setTimeout(resolve,30));
  const vm=window.document.querySelector('#app').__vue__;
  assert.ok(vm,'Vue mounted');assert.deepEqual(warnings.filter(s=>/Error compiling|invalid expression|not defined on the instance/.test(s)),[]);
  const untranslated=[],walker=window.document.createTreeWalker(vm.$el,window.NodeFilter.SHOW_TEXT);
  for(let node=walker.nextNode();node;node=walker.nextNode()){
    if(!/[\u4e00-\u9fff]/.test(node.nodeValue) || node.parentElement.closest('[data-cb-language-toggle]'))continue;
    let visible=true;for(let parent=node.parentElement;parent;parent=parent.parentElement)if(window.getComputedStyle(parent).display==='none'){visible=false;break;}
    if(visible)untranslated.push(node.nodeValue.trim());
  }
  assert.deepEqual(untranslated,[],'initial English packing chrome');
  vm.userInput='保留原始任务';vm.tableMaterials=[{id:'材料甲',name:'柜型'}];
  window.CBI18n.setLocale('zh-CN');await vm.$nextTick();
  assert.equal(vm.userInput,'保留原始任务');assert.equal(vm.tableMaterials[0].name,'柜型');
  window.CBI18n.setLocale('en');await vm.$nextTick();
  assert.equal(vm.userInput,'保留原始任务');assert.equal(vm.tableMaterials[0].name,'柜型');
  assert.match(window.document.querySelector('.topbar').textContent,/Packing/);
  assert.match(window.document.querySelector('.topbar').textContent,/Back to Workbench/);
  // Imported/backend trace prose can coincide with a UI key; it is still source data.
  vm.demoSimpleMode=false;vm.mainTab='agents';vm.selectedAgent='ALL';
  vm.agentSteps=[{node:'source-record',title:'施工计划',message:'柜型',status:'done'}];
  await vm.$nextTick();
  assert.equal(window.document.querySelector('.agent-card .title').textContent,'施工计划');
  assert.equal(window.document.querySelector('.agent-card .body-text').textContent,'柜型');
  window.CBI18n.setLocale('zh-CN');await vm.$nextTick();
  window.CBI18n.setLocale('en');await vm.$nextTick();
  assert.equal(window.document.querySelector('.agent-card .title').textContent,'施工计划');
  assert.equal(window.document.querySelector('.agent-card .body-text').textContent,'柜型');
  vm.$destroy();dom.window.close();
});

test('handling fields and actionable calculation refusal survive a language switch verbatim',async()=>{
  const dom=new JSDOM(read('demo/static/logistics.html'),{url:'http://localhost/logistics',runScripts:'outside-only'});
  const {window}=dom;
  window.eval(read('demo/static/i18n.js'));window.eval(read('demo/static/i18n-logistics.js'));
  window.document.dispatchEvent(new window.Event('DOMContentLoaded'));
  globalThis.CBI18n=window.CBI18n;globalThis.CBLogisticsI18n=window.CBLogisticsI18n;
  try{
    const {startLogisticsApp}=await import('data:text/javascript;base64,'+Buffer.from(read('demo/static/logistics.js')).toString('base64'));
    const failure={ok:false,revision:1,mode:'packaged',result:{ok:false,needs_human:[{row_id:'R00001',field:'handling_requirements',code:'unsupported_handling',message:'柜型'}]}};
    const app=startLogisticsApp(window.document,{window,initialize:false,fetch:async()=>new Response(JSON.stringify(failure),{headers:{'Content-Type':'application/json'}})});
    const row={id:'R00001',name:'原始货物',orientation:'upright',stacking:'no_stack',handling_requirements:'柜型',evidence:{handling_requirements:{raw:'柜型',header:'handling',source:{row:2,column:4}}}};
    app.acceptProject({id:'a'.repeat(32),name:'Synthetic',revision:1,confirmed:true,versions:[],document:{source:{filename:'原件.csv'},rows:[row],totals:[]},audit:{issues:[]},summary:{}});
    const $=id=>window.document.getElementById(id);
    $('confirmation').value='我明白，将由持证人员签认';await app.calculate();
    assert.equal(app.state.packing,null);assert.match($('packingResult').textContent,/R00001/);
    window.CBI18n.setLocale('en');
    assert.match($('packingResult').textContent,/unsupported or conflicts/);
    assert.match($('packingResult').textContent,/柜型/);
    assert.equal(window.document.querySelector('[data-ledger-field="handling_requirements"]').value,'柜型');
    assert.equal(window.document.querySelector('[data-ledger-field="orientation"]').value,'upright');
    assert.match(window.document.querySelector('[data-ledger-field="stacking"]').textContent,/Do not stack/);
    assert.match($('handlingHelp').textContent,/Stability, securing, lifting/);
    $('packingResult').querySelector('button').click();
    assert.match($('sourceFacts').textContent,/柜型/);
    window.CBI18n.setLocale('zh-CN');
    assert.match($('packingResult').textContent,/R00001/);assert.equal(app.state.project.document.rows[0].handling_requirements,'柜型');
  }finally{delete globalThis.CBI18n;delete globalThis.CBLogisticsI18n;dom.window.close();}
});
