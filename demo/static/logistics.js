const UNKNOWN = 'UNSPECIFIED';
const SIGNOFF = '我明白，将由持证人员签认';
const SIGNOFFS = [SIGNOFF, 'I understand; a licensed person will sign this off.'];
export const FIELDS = [
  ['container_id','集装箱号'],['package_id','箱号'],['package_type','包装类型'],['material_id','物料编号'],['name','品名'],['spec','规格'],
  ['package_count','包装数','integer'],['quantity','数量（原单位）','integer'],['units_per_package','每包装数量','integer'],['unit','原始单位'],
  ['length_mm','长度 mm','number'],['width_mm','宽度 mm','number'],['height_mm','高度 mm','number'],
  ['dimension_scope','尺寸口径','dimension'],['net_kg','净重 kg','number'],['gross_kg','毛重 kg','number'],['weight_scope','重量口径','weight'],
];
const labels = Object.fromEntries(FIELDS.map(([key,label])=>[key,label]));
const scopes = {dimension:[[UNKNOWN,'待确认'],['package','每包装外尺寸'],['item','单件材料尺寸']],weight:[[UNKNOWN,'待确认'],['package','每包装重量'],['item','单件重量'],['row','整行总重量']]};
const copy = value => JSON.parse(JSON.stringify(value));
export function displayValue(value, field) {
  if (value === UNKNOWN || value === null || value === undefined || value === '') return '待确认';
  const options = field === 'dimension_scope' ? scopes.dimension : field === 'weight_scope' ? scopes.weight : null;
  return options?.find(([key])=>key===value)?.[1] || String(value);
}
export function parseEdit(field, raw) {
  const definition = FIELDS.find(([key])=>key===field);
  if (!definition) throw new Error('该字段不可修改。');
  const value = String(raw).trim(), kind = definition[2];
  if (!value || value === UNKNOWN) return UNKNOWN;
  if (kind === 'number' || kind === 'integer') {
    if (!/^(?:\d+(?:\.\d*)?|\.\d+)$/.test(value)) throw new Error(`${definition[1]}须为正有限数；未知请留空。`);
    const parsed = Number(value);
    if (!Number.isFinite(parsed) || parsed<=0 || parsed > 1e12 || (kind === 'integer' && !Number.isInteger(parsed))) throw new Error(`${definition[1]}须为正数且在范围内，包装数与数量须为整数。`);
    return parsed;
  }
  if (scopes[kind] && !scopes[kind].some(([key])=>key===value)) throw new Error('请选择明确口径。');
  if (value.length > 500) throw new Error('文字修订不能超过 500 字。');
  return value;
}
export function sourceLocation(source={}, compact=false) {
  const parts=[];
  if (source.sheet) parts.push(String(source.sheet));
  if (source.row || source.column) {
    let column='', value=source.column;
    if (Number.isInteger(value) && value>0 && value<=100000) while(value>0){value--;column=String.fromCharCode(65+value%26)+column;value=Math.floor(value/26);}
    parts.push(column && source.row ? `${column}${source.row}` : `行 ${source.row || '待确认'} / 列 ${source.column || '待确认'}`);
  }
  if (source.page) parts.push(`第 ${source.page} 页`);
  if (!compact && Array.isArray(source.bbox)) parts.push(`框 [${source.bbox.map(value=>typeof value==='number'?Number(value.toFixed(2)):value).join(', ')}]`);
  return parts.join(' · ') || '未提供位置';
}

export function startLogisticsApp(document, options={}) {
  const $=id=>document.getElementById(id), win=options.window || globalThis.window;
  const fetcher=options.fetch || globalThis.fetch;
  const element=(tag,text,cls)=>{const node=document.createElement(tag);if(text!==undefined)node.textContent=String(text);if(cls)node.className=cls;return node;};
  const state={project:null,draft:null,latestRevision:null,historical:false,pending:new Map(),proposal:null,sequence:0,busy:null,page:0,packing:null,calculationAttempt:null,chats:[],selectedSource:null,recentProjects:[],comparison:null};
  let controller=null, operation=null, recentSequence=0, previewSequence=0;
  const doc=()=>state.project?.document || state.draft?.document;
  const projectId=()=>state.project?.id;
  const writable=()=>!!state.project && !state.historical;
  const sourceURL=()=>state.project ? `/api/logistics/projects/${encodeURIComponent(state.project.id)}/source${state.historical?`?version=${state.project.revision}`:''}` : state.draft ? `/api/logistics/documents/${encodeURIComponent(state.draft.document_id)}/source` : null;
  function notify(message,error=false){$('notice').textContent=message;$('notice').className=`notice${error?' error':''}`;}
  function currentPacking(){return !!state.packing && state.packing.projectId===projectId() && state.packing.revision===state.project?.revision && !state.pending.size && state.packing.mode===$('packingMode').value && state.packing.container===$('containerType').value && state.packing.max===Number($('maxContainers').value);}
  function controls(){
    const busy=!!state.busy, write=writable(), pending=state.pending.size>0;
    for(const node of document.querySelectorAll('main button,main input,main select,main textarea')) node.disabled=busy;
    $('cancel').hidden=!busy || state.busy.write; $('cancel').disabled=false;
    $('saveProject').disabled=busy || !state.draft;
    $('projectName').disabled=busy || !!state.project;
    $('openProject').disabled=busy || !$('recentProjects').value;
    $('openVersion').disabled=busy || !state.project || !$('versions').value;
    $('latestVersion').hidden=!state.historical;
    $('undoProject').disabled=busy || !write || !state.project?.can_undo || pending;
    for(const node of document.querySelectorAll('[data-ledger-field]')) node.disabled=busy || !write || node.dataset.shared==='true';
    for(const id of ['reviewEdits','discardEdits']) $(id).disabled=busy || !write || !pending;
    $('applyProposal').disabled=busy || !write || !state.proposal || !$('confirmProposal').checked;
    $('confirmProposal').disabled=busy || !write || !state.proposal;
    $('checkedLedger').disabled=busy || !write || pending || !!state.proposal;
    $('confirmLedger').disabled=busy || !write || pending || !!state.proposal || !$('checkedLedger').checked || state.project?.confirmed===true;
    for(const id of ['sendChat','askSummary','askIssues','chatInput']) $(id).disabled=busy || !write || pending;
    $('compareProjects').disabled=busy || !write || pending;
    $('compareProject').disabled=busy || !write || pending || !$('compareProjects').value;
    $('calculate').disabled=busy || !write || pending || !!state.proposal || !state.project?.confirmed;
    $('exportProject').disabled=busy || !write || pending || !!state.proposal;
    $('previousPage').disabled=busy || state.page===0;
    $('nextPage').disabled=busy || (state.page+1)*30>=(doc()?.rows.length || 0);
    $('pageState').textContent=doc()?.rows.length ? `${state.page+1} / ${Math.ceil(doc().rows.length/30)} 页 · 共 ${doc().rows.length} 行` : '暂无物料行';
    $('projectState').textContent=state.project ? `${state.project.name} · 版本 ${state.project.revision}${state.historical?' · 历史只读':''}${busy && state.busy.write?' · 正在保存，请等待返回':''}` : state.draft ? '文件已读取，尚未保存；保存后可编辑、对话和确认。' : '尚未保存';
    $('ledgerState').textContent=state.historical?'历史版本':state.project?.confirmed && !pending?'当前版本已确认':pending?'有未应用编辑':doc()?'待核对':'等待文件';
    $('ledgerState').className=`badge${state.project?.confirmed && !pending?' confirmed':''}`;
    $('editState').textContent=pending?`${state.pending.size} 个字段尚未应用；汇总仍为已保存台账。`:'';
    $('confirmationState').textContent=state.project?.confirmed && !pending?'当前已保存版本已确认。应用修改会清除旧确认。':'核对原件、处理差异后确认；未应用编辑不能用于计算。';
    $('calculationState').textContent=state.calculationAttempt?.status==='running'?'正在计算…':state.calculationAttempt && state.calculationAttempt.status!=='success'?'本次计算未完成；已有结果仅作上次记录':state.packing ? currentPacking()?'结果对应当前版本与计算选项':'保留上次结果 · 当前版本或选项已改变' : '尚未计算';
    $('sampleBadge').hidden=!(state.project?.synthetic || state.draft?.synthetic || doc()?.synthetic || doc()?.extraction?.synthetic);
    $('historyBadge').hidden=!state.historical;
    $('historyBadge').textContent=state.historical?`正在只读查看版本 ${state.project.revision}，不允许用历史版本覆盖最新台账。回到最新版本后可撤销上次修改。`:'';
    $('agentLink').hidden=!writable();if(writable())$('agentLink').href=`/?logistics_project_id=${projectId()}`;
    $('modeHelp').textContent=$('packingMode').value==='materials'?'裸材料模式需要单件材料尺寸、单件净重及明确数量，不能带有已包装箱号或包装数。包装采用引擎规则，须另行复核。':'已包装模式需要每包装外尺寸、每包装毛重、包装数及数量。共享合并值不能拆成单行重量。缺项由服务报告，不猜测。';
  }
  function optionsIn(select,values,placeholder){const previous=select.value;select.replaceChildren(element('option',placeholder));select.children[0].value='';for(const [value,label] of values){const item=element('option',label);item.value=String(value);select.append(item);}select.value=values.some(([value])=>String(value)===previous)?previous:'';}
  function renderLedger(){
    $('ledgerHead').replaceChildren();const head=element('tr');head.append(element('th','行号'));for(const [,label] of FIELDS)head.append(element('th',label));$('ledgerHead').append(head);
    $('ledgerRows').replaceChildren();const rows=doc()?.rows || [];state.page=Math.min(state.page,Math.max(0,Math.ceil(rows.length/30)-1));
    for(const row of rows.slice(state.page*30,(state.page+1)*30)){
      const tr=element('tr');tr.append(element('td',row.id));
      for(const [field,label,kind] of FIELDS){
        const td=element('td'),key=`${row.id}:${field}`,raw=state.pending.has(key)?state.pending.get(key).raw:row[field],evidence=row.evidence?.[field],group=evidence?.group;
        const input=element(scopes[kind]?'select':'input');input.dataset.ledgerField=field;input.dataset.rowId=row.id;input.setAttribute('aria-label',`${row.id} ${label}`);
        if(scopes[kind])for(const [value,text]of scopes[kind]){const opt=element('option',text);opt.value=value;input.append(opt);}
        else{input.type='text';input.maxLength=500;if(kind)input.inputMode=kind==='integer'?'numeric':'decimal';input.placeholder='待确认';}
        input.value=raw===UNKNOWN || raw==null ? scopes[kind]?UNKNOWN:'' : String(raw);
        if(group){input.dataset.shared='true';input.readOnly=true;input.setAttribute('aria-readonly','true');input.title='原图合并单元格跨多材料行，不能按单行修改。';if(scopes[kind])input.children[0].textContent='共享合并值';else input.placeholder='共享合并值';td.className='shared-field';}
        input.addEventListener(scopes[kind]?'change':'input',()=>edit(row.id,field,input.value,td));
        if(state.pending.has(key))td.className='pending';if(field==='name')td.className+=' name-field';td.append(input);
        const location=group?.source || evidence?.source,source=element('button',group?'共享合并值 · 来源':location?sourceLocation(location,true):'来源待补',`source-link${evidence?'':' missing'}`);source.type='button';source.setAttribute('aria-label',`${row.id} ${label} 来源`);source.addEventListener('click',()=>showSource(row.id,field));td.append(source);tr.append(td);
      }$('ledgerRows').append(tr);
    }$('ledgerEmpty').hidden=rows.length>0;controls();
  }
  function edit(rowId,field,raw,cell){
    if(state.busy || !writable())return;
    const row=doc().rows.find(item=>item.id===rowId);if(!row)return;
    if(row.evidence?.[field]?.group){notify('该字段来自跨材料行的共享合并单元格，不能按单行修改。请查看来源与覆盖范围。',true);return;}
    const key=`${rowId}:${field}`;let same=false;try{same=parseEdit(field,raw)===row[field];}catch{ /* Invalid draft remains visible for correction. */ }
    if(same)state.pending.delete(key);else state.pending.set(key,{row_id:rowId,field,raw});
    if(cell)cell.className=state.pending.has(key)?'pending':'';
    state.proposal=null;$('proposalPanel').hidden=true;$('confirmProposal').checked=false;$('checkedLedger').checked=false;controls();renderPacking();renderComparison();
  }
  function renderSummary(){
    const summary=state.project?.summary || state.draft?.summary;
    $('totals').replaceChildren();for(const [field,label,unit]of [['package_count','包装数','包装单位'],['net_kg','净重合计','kg'],['gross_kg','毛重合计','kg']]){const card=element('div',undefined,'metric');card.append(element('span',label),element('strong',displayValue(summary?.totals?.[field])),element('small',unit));$('totals').append(card);}
    const quantities=summary?.quantities_by_unit;
    if(quantities && Object.keys(quantities).length){for(const [unit,quantity]of Object.entries(quantities)){const card=element('div',undefined,'metric');card.append(element('span','数量分单位汇总'),element('strong',displayValue(quantity)),element('small',unit===UNKNOWN?'单位待确认':unit));$('totals').append(card);}}
    else{const card=element('div',undefined,'metric');card.append(element('span','数量'),element('strong','待确认'),element('small','等待分单位汇总'));$('totals').append(card);}
    $('sourceTotalsRows').replaceChildren();const originalTotals=doc()?.totals || [];$('sourceTotals').hidden=!originalTotals.length;
    for(const total of originalTotals.slice(0,50)){const card=element('div',undefined,'source-total');card.append(element('strong',sourceLocation(total.source || {})));for(const [field,value]of Object.entries(total.values || {}))card.append(element('p',`${labels[field] || field}：${displayValue(value)}${field==='quantity'?'（原表合计，单位与适用范围须核对）':''}`));if(total.row_ids?.length)card.append(element('p',`对应材料行：${total.row_ids.join('、')}`,'muted'));$('sourceTotalsRows').append(card);}
    $('issues').replaceChildren();const audit=state.project?.audit || state.draft?.audit;
    $('sourceTotals').open=!!audit?.issues?.some(issue=>['mixed_quantity_units','total_unverifiable'].includes(issue.code));
    if(!audit){$('issues').append(element('p','读取文件后查看缺项与不一致。','empty'));return;}
    if(!audit.issues?.length)$('issues').append(element('p','当前规则未发现问题；仍需核对原件并确认台账。','muted'));
    for(const issue of (audit.issues || []).slice(0,100)){const card=element('div',undefined,`issue${issue.severity==='error'?' error':''}`);card.append(element('strong',issue.message),element('span',[issue.row_id,labels[issue.field] || issue.field,issue.code].filter(Boolean).join(' · '),'muted'));if(issue.row_id && issue.field){const button=element('button','查看来源');button.addEventListener('click',()=>showSource(issue.row_id,issue.field));card.append(button);}$('issues').append(card);}
    if((audit.issues?.length || 0)>100)$('issues').append(element('p',`共 ${audit.issues.length} 条，当前显示前 100 条；导出 JSON 可查看完整记录。`,'muted'));
  }
  function renderSource(){
    const source=doc()?.source,url=sourceURL();$('sourceName').textContent=source?.filename || '尚未上传原件';$('sourceDownload').hidden=!url;if(url)$('sourceDownload').href=url;
    const ticket=++previewSequence;$('sourcePreview').replaceChildren();$('sourceDetail').hidden=true;state.selectedSource=null;
    const ext=source?.filename?.toLowerCase().split('.').pop();
    if(url && ext==='pdf'){const frame=element('iframe');frame.title='原始 PDF 预览';frame.src=`${url}#page=1`;frame.referrerPolicy='same-origin';$('sourcePreview').append(frame);}
    else if(url && ['png','jpg','jpeg'].includes(ext)){const wrap=element('div',undefined,'image-wrap'),img=element('img');img.alt='原始箱单图片';img.src=url;img.addEventListener('load',()=>{if(ticket===previewSequence && state.selectedSource)showSource(...state.selectedSource);});wrap.append(img);$('sourcePreview').append(wrap);}
    else $('sourcePreview').append(element('p',url?'表格证据见下方；点击台账来源查看准确单元格，完整原表可下载。':'上传文件后显示原件。','empty'));
    $('sourceCells').replaceChildren();const seen=new Set();let count=0;
    for(const row of doc()?.rows || [])for(const [field,evidence]of Object.entries(row.evidence || {})){
      const originalSource=evidence.group?.source || evidence.source;if(!originalSource?.sheet || count>=200)continue;
      const location=sourceLocation(originalSource),key=`${location}:${evidence.raw}`;if(seen.has(key))continue;seen.add(key);count++;
      const tr=element('tr'),td=element('td'),button=element('button',location);button.addEventListener('click',()=>showSource(row.id,field));td.append(button);tr.append(td,element('td',evidence.raw ?? ''));$('sourceCells').append(tr);
    }$('sourceTableDetails').hidden=count===0;
  }
  function showSource(rowId,field){
    const row=doc()?.rows.find(item=>item.id===rowId);if(!row)return;
    const ev=row.evidence?.[field],group=ev?.group,location=group?.source || ev?.source || {};state.selectedSource=[rowId,field];$('sourceDetail').hidden=false;$('sourceTitle').textContent=`${rowId} · ${labels[field] || field}`;$('sourceFacts').replaceChildren();
    const shared=group && (row[field]===UNKNOWN || row[field]==null);
    const anchor=group ? doc().rows.find(item=>item.id===group.anchor_row_id && item.evidence?.[field]?.group?.id===group.id) : null;
    for(const [label,value]of [['当前值',shared?'共享合并值，未分摊到本行':displayValue(row[field],field)],['原始值',ev?.raw ?? '未提供原始证据'],['共享原文',group?(anchor?.evidence?.[field]?.raw || '待确认'):null],['共享值（不分摊）',group?displayValue(anchor?.[field],field):null],['共享组',group?.id],['覆盖材料行',group?.row_ids?.join('、')],['唯一记值行',group?.anchor_row_id],['共享口径',group?'原图合并值仅记录一次，不代表各行分别具有此值；该字段只读。':null],['位置',sourceLocation(location)],['坐标单位',location.coordinate_system],['原表头',ev?.header],['核对说明',ev?.reason],['人工修订',ev?.corrections?JSON.stringify(ev.corrections):ev?.correction?typeof ev.correction==='string'?ev.correction:JSON.stringify(ev.correction):null]])if(value!==undefined && value!==null){$('sourceFacts').append(element('dt',label),element('dd',value));}
    const ext=doc()?.source.filename.toLowerCase().split('.').pop(),url=sourceURL();
    if(ext==='pdf' && Number.isInteger(location.page) && $('sourcePreview').children[0])$('sourcePreview').children[0].src=`${url}#page=${location.page}`;
    // Bounding boxes are shown numerically unless the parser provides explicit coordinate dimensions.
    // This avoids drawing PDF points or scaled OCR coordinates as native image pixels.
    $('sourceDetail').scrollIntoView?.({block:'nearest',behavior:'smooth'});
  }
  function renderProposal(){
    const draft=state.proposal;$('proposalPanel').hidden=!draft;$('proposalRows').replaceChildren();$('confirmProposal').checked=false;
    if(!draft){controls();return;}
    $('proposalReason').textContent=draft.action==='undo'?'将恢复上一份保留台账；当前记录仍保留在历史中。':draft.proposal?.source_text || draft.proposal?.reason || '核对修改前后的差异；只有确认应用才会写入新版本。';
    for(const change of draft.changes || draft.proposal?.changes || []){const row=element('tr');for(const value of [`${change.row_id || '项目'} / ${labels[change.field] || (change.field==='revision'?'版本':change.field)}`,displayValue(change.before,change.field),displayValue(change.after,change.field)])row.append(element('td',value));$('proposalRows').append(row);}controls();
  }
  function renderPacking(){
    $('packingResult').replaceChildren();if(!state.packing){$('packingResult').append(element('p','计算成功后展示引擎返回的结果；失败不会标记为已完成。','empty'));return;}
    const {payload}=state.packing,result=payload.result;
    if(state.calculationAttempt && state.calculationAttempt.status!=='success')$('packingResult').append(element('p','本次计算尚未成功，以下保留的是上次成功记录。','stale'));
    if(!currentPacking())$('packingResult').append(element('p',`上次计算记录（版本 ${state.packing.revision}）。当前台账、模式或柜型选项已改变，请重新计算。`,'stale'));
    if(payload.ok!==true || result?.can_fit===false)$('packingResult').append(element('p','本次未得到可用装载方案，请处理引擎报告的问题。','notice error'));
    else{$('packingResult').append(element('h3',`版本 ${state.packing.revision} · ${state.packing.mode==='packaged'?'已包装拼柜':'材料装箱与拼柜'}结果`));const cards=element('div',undefined,'result-grid');for(const [label,value]of [['使用柜数',result?.containers_used],['包装箱数',result?.n_boxes],['柜型',result?.container_type || state.packing.container]]){if(value===undefined)continue;const card=element('div',undefined,'metric');card.append(element('span',label),element('strong',displayValue(value)));cards.append(card);}$('packingResult').append(cards);}
    const detail=element('details'),summary=element('summary','引擎完整计算记录'),pre=element('pre',JSON.stringify(payload,null,2));detail.append(summary,pre);$('packingResult').append(detail);
  }
  function renderChat(){ $('chatLog').replaceChildren();if(!state.chats.length)$('chatLog').append(element('p','先保存项目，再提问或提出明确修改。提案须单独确认。','muted'));for(const item of state.chats){const node=element('div',undefined,`message ${item.role}${item.error?' error':''}`);node.append(element('small',item.role==='user'?'你':`Civil Buddy · 版本 ${item.revision}`),element('span',item.text));$('chatLog').append(node);} }
  function renderComparison(){
    const value=state.comparison,target=$('comparisonResult');target.replaceChildren();target.hidden=!value;if(!value)return;
    target.append(element('p',`当前项目版本 ${value.revision} ↔ ${value.otherName} · 版本 ${value.otherRevision}`,'muted'));
    if(value.projectId!==projectId() || value.revision!==state.project?.revision || state.pending.size)target.append(element('p','上次对照记录；当前台账有变化或未应用编辑，请保存修改后重新对照。','stale'));
    const wrap=element('div',undefined,'table-scroll'),table=element('table'),thead=element('thead'),head=element('tr');for(const title of ['材料编号 / 对照状态','字段','当前台账','对照台账'])head.append(element('th',title));thead.append(head);table.append(thead);const body=element('tbody');
    const statuses={same:'一致',different:'有差异',only_current:'仅当前台账有',only_comparison:'仅对照台账有',ambiguous_duplicate:'编号重复，未自动匹配'};
    for(const row of (value.comparison.rows || []).slice(0,200)){
      const differences=Object.entries(row.differences || {});
      for(const [field,difference]of differences.length?differences:[['',null]]){const tr=element('tr');for(const text of [`${row.material_id} · ${statuses[row.status] || row.status}`,labels[field] || field || '—',difference?displayValue(difference.current,field):row.current_row || '—',difference?displayValue(difference.comparison,field):row.comparison_row || '—'])tr.append(element('td',text));body.append(tr);}
    }table.append(body);wrap.append(table);target.append(wrap);
    if((value.comparison.rows?.length || 0)>200)target.append(element('p',`共 ${value.comparison.rows.length} 个材料编号，展示前 200 个；完整结果见下方。`,'muted'));
    for(const [label,key]of [['当前台账无明确材料编号','unmatched_current'],['对照台账无明确材料编号','unmatched_comparison']])if(value.comparison[key]?.length)target.append(element('p',`${label}：${value.comparison[key].join('、')}`,'muted'));
    target.append(element('p',value.comparison.note || '只按明确且唯一的材料编号匹配；未改动两份台账。','muted'));
    const detail=element('details');detail.append(element('summary','完整对照记录'),element('pre',JSON.stringify(value.comparison,null,2)));target.append(detail);
  }
  function render(){
    $('projectName').value=state.project?.name || state.draft?.suggestedName || '';
    optionsIn($('versions'),(state.project?.versions || []).map(item=>[item.revision,`版本 ${item.revision}`]),'选择版本');
    optionsIn($('compareProjects'),state.recentProjects.filter(item=>item.id!==projectId()).map(item=>[item.id,`${item.name} · v${item.revision}`]),'选择另一项目');
    renderLedger();renderSummary();renderSource();renderProposal();renderPacking();renderChat();renderComparison();controls();
  }
  function acceptProject(project,{historical=false,latestRevision=null,keepChat=false}={}){
    if(!project || !/^[a-f0-9]{32}$/.test(project.id) || !Number.isInteger(project.revision) || !Array.isArray(project.document?.rows))throw new Error('服务返回的项目结构不完整。');
    const same=projectId()===project.id;state.project=copy(project);state.draft=null;state.pending.clear();state.proposal=null;state.page=0;state.historical=historical;state.latestRevision=latestRevision || project.revision;$('checkedLedger').checked=false;$('confirmProposal').checked=false;$('confirmation').value='';
    if(!same){state.packing=null;state.calculationAttempt=null;state.comparison=null;}if(!same || !keepChat)state.chats=[];
    win?.history?.replaceState?.({},'',`/logistics?project_id=${project.id}`);render();
  }
  function acceptDraft(data){
    if(!data || !/^[a-f0-9]{32}$/.test(data.document_id) || !Array.isArray(data.document?.rows))throw new Error('服务没有返回可用的文件草稿。');
    state.draft={...copy(data),suggestedName:data.document.source.filename.replace(/\.[^.]+$/,'').slice(0,100)};state.project=null;state.historical=false;state.latestRevision=null;state.pending.clear();state.proposal=null;state.packing=null;state.calculationAttempt=null;state.comparison=null;state.chats=[];state.page=0;$('checkedLedger').checked=false;$('confirmation').value='';win?.history?.replaceState?.({},'','/logistics');render();
  }
  function json(body){return {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)};}
  async function request(url,init={}){
    const response=await fetcher(url,{credentials:'same-origin',...init});
    if(response.status===401){$('auth').hidden=false;throw new Error('请输入访问口令；当前台账与编辑已保留。');}
    if(!response.ok){let data;try{data=await response.json();}catch{data={};}const err=new Error(typeof data.detail==='string'?data.detail:typeof data.message==='string'?data.message:`请求失败（${response.status}）`);err.status=response.status;throw err;}
    return response;
  }
  async function perform(label,write,work){
    if(state.busy?.write)return false;
    if(state.busy){controller?.abort();const previous=operation;if(previous)void request(`/api/engineering/operations/${previous}/cancel`,json({})).catch(()=>{});}
    const sequence=++state.sequence;controller=new AbortController();operation=globalThis.crypto.randomUUID().replaceAll('-','');const signal=controller.signal,operationId=operation;state.busy={write,label};controls();notify(label);
    const guarded=async(url,init={},binary=false)=>{const response=await request(url,{...init,signal,headers:{...init.headers,'X-CAD-Operation-ID':operationId}});const data=binary?response:await response.json();if(sequence!==state.sequence)throw Object.assign(new Error('请求已被新操作替代'),{name:'AbortError'});return data;};
    try{await work(guarded,()=>sequence===state.sequence);return sequence===state.sequence;}
    catch(error){if(sequence===state.sequence && error.name!=='AbortError')notify(`${error.status===409?'版本冲突，请重新打开最新项目后核对编辑：':error.status===499?'操作已取消：':'操作未完成：'}${error.message}`,true);return false;}
    finally{if(sequence===state.sequence){state.busy=null;controller=null;operation=null;controls();}}
  }
  async function cancel(){
    if(!state.busy || state.busy.write)return;const id=operation,ticket=++state.sequence;controller?.abort();controller=null;operation=null;state.busy=null;if(state.calculationAttempt?.status==='running')state.calculationAttempt.status='cancelled';controls();renderPacking();notify('已停止等待本次操作；台账未改变。');
    try{await request(`/api/engineering/operations/${id}/cancel`,json({}));if(ticket===state.sequence)notify('已取消本次操作；现有台账与历史结果保留。');}catch{if(ticket===state.sequence)notify('已停止等待；取消确认未返回。重新打开项目可核对已保存状态。',true);}
  }
  function clean(){if(state.pending.size || state.proposal){notify('请先确认应用或撤回当前修改提案与未应用编辑。',true);return false;}return true;}
  function requireProject(){if(!writable()){notify(state.historical?'历史版本仅供查看，请先回到最新版本。':'请先保存文件为项目。',true);return false;}return true;}
  async function refreshProjects(){const ticket=++recentSequence;try{const response=await request('/api/logistics/projects');const data=await response.json();if(ticket!==recentSequence)return;state.recentProjects=data.projects || [];optionsIn($('recentProjects'),state.recentProjects.map(p=>[p.id,`${p.name} · v${p.revision}`]),'选择已保存项目');optionsIn($('compareProjects'),state.recentProjects.filter(item=>item.id!==projectId()).map(item=>[item.id,`${item.name} · v${item.revision}`]),'选择另一项目');controls();}catch(error){if(ticket===recentSequence)notify(error.message,true);}}
  async function openProject(id=$('recentProjects').value,version=null){
    if(!id || !/^[a-f0-9]{32}$/.test(id) || !clean())return false;
    const latest=state.latestRevision;
    return perform('正在读取项目…',false,async requestData=>{const data=await requestData(`/api/logistics/projects/${id}${version?`?version=${version}`:''}`);acceptProject(data.project,{historical:!!version,latestRevision:latest});notify(version?`已打开历史版本 ${version}（只读）。`:'已重新打开保存的台账。');});
  }
  async function upload(){if(!clean())return;const file=$('uploadFile').files?.[0];if(!file){notify('请先选择文件。',true);return;}if(!/\.(xlsx|xlsm|csv|tsv|pdf|png|jpe?g)$/i.test(file.name)){notify('支持 xlsx、xlsm、csv、tsv、pdf、png、jpg 文件。',true);return;}if(file.size>8*1024*1024){notify('文件超过 8 MB。',true);return;}const body=new FormData();body.append('file',file);return perform('正在读取文件；识别不清的字段会保留待确认…',false,async requestData=>{const data=await requestData('/api/logistics/upload?ocr_backend=auto',{method:'POST',body});acceptDraft(data);notify('文件已读取为草稿。核对原件后保存项目，继续编辑与对话。');});}
  async function example(){if(!clean())return;return perform('正在读取合成演示样例…',false,async requestData=>{const data=await requestData('/api/logistics/example');acceptDraft(data);notify('已载入合成演示箱单；请先保存为项目。');});}
  async function save(){if(!state.draft || state.busy)return;const name=$('projectName').value.trim();if(!name){notify('请填写项目名称。',true);return;}const document_id=state.draft.document_id;return perform('正在保存项目与原件…',true,async requestData=>{const data=await requestData('/api/logistics/projects',json({document_id,name}));acceptProject(data.project);notify('项目与原件已保存，可以编辑台账。');void refreshProjects();});}
  async function reviewEdits(){
    if(!requireProject() || !state.pending.size || state.busy)return;if(state.pending.size>100){notify('每次最多修订 100 个字段，请分批核对。',true);return;}let changes;try{changes=[...state.pending.values()].map(({row_id,field,raw})=>({row_id,field,value:parseEdit(field,raw)}));}catch(error){notify(error.message,true);return;}
    const id=projectId(),revision=state.project.revision;
    return perform('正在校验修改差异…',false,async requestData=>{const data=await requestData(`/api/logistics/projects/${id}/propose`,json({expected_revision:revision,changes,reason:'用户在箱单台账中明确编辑'}));state.proposal={...data,projectId:id,revision};renderProposal();notify('修改差异已生成，尚未写入台账。请核对后确认应用。');});
  }
  function discardEdits(){if(state.busy)return;state.pending.clear();state.proposal=null;renderLedger();renderProposal();renderPacking();renderComparison();notify('已撤回未应用编辑，已保存台账保持原样。');}
  async function applyProposal(){
    if(state.busy || !requireProject() || !state.proposal || !$('confirmProposal').checked)return;
    const proposal=state.proposal;if(proposal.projectId!==projectId() || proposal.revision!==state.project.revision){notify('提案不属于当前项目版本，请重新生成。',true);return;}
    return perform('正在应用已确认的修改…',true,async requestData=>{const data=await requestData(`/api/logistics/proposals/${encodeURIComponent(proposal.proposal_id)}/apply`,json({expected_revision:state.project.revision}));acceptProject(data.project,{keepChat:true});notify('修改已保存为新版本，请重新核对并确认台账。');void refreshProjects();});
  }
  async function confirmLedger(){if(state.busy || !requireProject() || !clean() || !$('checkedLedger').checked)return;return perform('正在确认当前版本…',true,async requestData=>{const data=await requestData(`/api/logistics/projects/${projectId()}/confirm`,json({expected_revision:state.project.revision}));acceptProject(data.project,{keepChat:true});notify(data.project.confirmed?'当前版本台账已确认，可选择模式计算。':'台账未能确认，请处理问题列表。',!data.project.confirmed);void refreshProjects();});}
  async function undo(){if(state.busy || !requireProject() || !clean() || !state.project.can_undo)return;return perform('正在撤销上次修改并保留历史…',true,async requestData=>{const data=await requestData(`/api/logistics/projects/${projectId()}/undo`,json({expected_revision:state.project.revision}));acceptProject(data.project,{keepChat:true});notify('已恢复上一份台账并保存新版本；请重新确认。');void refreshProjects();});}
  async function chat(message=$('chatInput').value.trim()){
    if(state.busy || !message || !requireProject() || !clean())return;const id=projectId(),revision=state.project.revision;state.chats.push({role:'user',text:message,revision});$('chatInput').value='';renderChat();
    return perform('正在核对项目并回复…',false,async requestData=>{const data=await requestData(`/api/logistics/projects/${id}/conversation`,json({message,expected_revision:revision}));state.chats.push({role:'assistant',text:data.reply || '服务未提供文字回复。',revision,error:data.ok===false});renderChat();if(data.proposal_id && data.ok!==false){state.proposal={...data,projectId:id,revision};renderProposal();}notify(data.ok===false?'未完成所请求的操作，请查看对话说明。':data.proposal_id?'已生成修改提案，等待你核对并确认。':'对话已完成；台账未被修改。',data.ok===false);});
  }
  async function compareProject(){
    if(state.busy || !requireProject() || !clean())return;
    const otherId=$('compareProjects').value,other=state.recentProjects.find(item=>item.id===otherId);
    if(!other || otherId===projectId() || !/^[a-f0-9]{32}$/.test(otherId)){notify('请选择另一份已保存台账。',true);return;}
    const id=projectId(),revision=state.project.revision;
    return perform('正在按明确材料编号对照两份台账…',false,async requestData=>{const data=await requestData(`/api/logistics/projects/${id}/conversation`,json({message:'对比这两份已保存台账的明确材料编号与字段',expected_revision:revision,compare_project_id:otherId}));if(data.ok!==true || !Array.isArray(data.comparison?.rows) || !Number.isInteger(data.comparison_revision))throw new Error(data.reply || '对照未返回有效结果。');state.comparison={projectId:id,revision,otherId,otherName:other.name,otherRevision:data.comparison_revision,comparison:copy(data.comparison)};renderComparison();notify('对照已完成；仅比较明确材料编号，两份台账均未修改。');});
  }
  async function calculate(){
    if(state.busy || !requireProject() || !clean())return;if(!state.project.confirmed){notify('请先核对并确认当前版本的台账。',true);return;}
    if(!SIGNOFFS.includes($('confirmation').value)){notify(`计算前请填写：${SIGNOFF}`,true);return;}
    const max=Number($('maxContainers').value),mode=$('packingMode').value,container=$('containerType').value;
    if(!Number.isInteger(max) || max<1 || max>40){notify('最多柜数必须是 1–40 的整数。',true);return;}
    const id=projectId(),revision=state.project.revision;
    const attempt={status:'running'};state.calculationAttempt=attempt;
    const completed=await perform('正在按已确认台账计算…',false,async requestData=>{const data=await requestData(`/api/logistics/projects/${id}/pack`,json({expected_revision:revision,mode,container_type:container,max_containers:max,confirmation:$('confirmation').value}));
      if(data.revision!==revision || data.mode!==mode)throw new Error('计算响应与请求版本或模式不一致，未采用结果。');
      if(data.ok!==true || data.result?.ok!==true || data.result?.can_fit!==true){notify('未生成可用装载方案，请检查引擎报告。',true);const pre=element('pre',JSON.stringify(data,null,2));renderPacking();$('packingResult').append(element('p','本次计算未成功，以下为失败报告。','notice error'),pre);return;}
      attempt.status='success';state.packing={projectId:id,revision,mode,container,max,payload:copy(data)};renderPacking();notify('计算已完成；结果绑定本次台账版本与选项。');});
    if(state.calculationAttempt===attempt){if(attempt.status==='running')attempt.status='failed';if(!completed)renderPacking();controls();}return completed;
  }
  function download(response,blob,format){
    const header=response.headers.get('content-disposition') || '',match=/filename="?([^";]+)"?/i.exec(header);const name=(match?.[1] || `logistics.${format}`).split(/[\\/]/).pop().replace(/[\x00-\x1f]/g,'');
    if(options.download){options.download(blob,name);return;}const url=URL.createObjectURL(blob),anchor=element('a');anchor.href=url;anchor.download=name;document.body.append(anchor);anchor.click();anchor.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);
  }
  async function exportProject(){if(state.busy || !requireProject() || !clean())return;if(!SIGNOFFS.includes($('confirmation').value)){notify(`导出前请填写：${SIGNOFF}`,true);return;}const format=$('exportFormat').value;return perform('正在校验并导出当前版本…',false,async(requestData,current)=>{const response=await requestData(`/api/logistics/projects/${projectId()}/export`,json({expected_revision:state.project.revision,format,confirmation:$('confirmation').value}),true);const blob=await response.blob();if(!current())return;download(response,blob,format);notify('已生成并开始下载当前版本文件。');});}
  async function importProject(){if(state.busy || !clean())return;const file=$('importFile').files?.[0];if(!file || !/\.zip$/i.test(file.name)){notify('请选择项目 ZIP 文件。',true);return;}const body=new FormData();body.append('file',file);return perform('正在校验项目包并创建新副本…',true,async requestData=>{const data=await requestData('/api/logistics/import',{method:'POST',body});acceptProject(data.project);notify('项目包已导入为新副本，请重新核对并确认台账。');void refreshProjects();});}
  async function loadLinkedProposal(id){if(!/^[a-f0-9]{32}$/.test(id) || !state.project)return;const project=projectId(),revision=state.project.revision;return perform('正在读取待确认修改提案…',false,async requestData=>{const data=await requestData(`/api/logistics/proposals/${id}`);if(data.project?.id!==project)throw new Error('提案属于其他项目。');if(data.project.revision!==revision)throw new Error('提案已过期，请重新提出修改。');state.proposal={...data,proposal_id:data.proposal_id || id,projectId:project,revision};renderProposal();notify('已载入对话提案，核对后才能应用。');});}
  async function initialize(){
    const initialSequence=state.sequence,params=new URLSearchParams(win?.location?.search || '');
    void refreshProjects();
    try{const response=await request('/api/logistics/capabilities'),data=await response.json();const ocr=data.ocr || {};const status=ocr.ready===true && ocr.available===true?`${ocr.engine || '本地 OCR'} 模型已就绪。`:ocr.environment_found?`${ocr.engine || 'OCR'} 环境已安装，模型尚未就绪。`:'本地 OCR 环境尚未就绪。';$('capabilities').textContent=`本页支持 xlsx / xlsm / csv / tsv / pdf / png / jpg，单文件最多 ${Math.floor((data.limits?.source_bytes || 8388608)/1048576)} MB。${status}${ocr.reason || ''} 扫描件识别不会自动下载模型，未知字段保持待确认。`;}catch(error){$('capabilities').textContent=`能力信息未读取：${error.message}`;}
    if(state.sequence!==initialSequence || state.project || state.draft)return;
    const id=params.get('project_id');if(id){await openProject(id);const proposal=params.get('proposal_id');if(proposal && projectId()===id)await loadLinkedProposal(proposal);}
  }
  $('upload').addEventListener('click',upload);$('example').addEventListener('click',example);$('saveProject').addEventListener('click',save);$('refreshProjects').addEventListener('click',refreshProjects);$('openProject').addEventListener('click',()=>openProject());$('recentProjects').addEventListener('change',controls);
  $('openVersion').addEventListener('click',()=>openProject(projectId(),Number($('versions').value)));$('versions').addEventListener('change',controls);$('latestVersion').addEventListener('click',()=>openProject(projectId()));$('undoProject').addEventListener('click',undo);$('cancel').addEventListener('click',cancel);
  $('reviewEdits').addEventListener('click',reviewEdits);$('discardEdits').addEventListener('click',discardEdits);$('applyProposal').addEventListener('click',applyProposal);$('confirmProposal').addEventListener('change',controls);$('dismissProposal').addEventListener('click',()=>{if(state.busy)return;state.proposal=null;renderProposal();notify('提案已放弃；未应用的表格编辑仍可继续核对。');});
  $('confirmLedger').addEventListener('click',confirmLedger);$('checkedLedger').addEventListener('change',controls);$('chatForm').addEventListener('submit',event=>{event.preventDefault();return chat();});$('askSummary').addEventListener('click',()=>chat('汇总箱单'));$('askIssues').addEventListener('click',()=>chat('检查异常'));
  $('compareProjects').addEventListener('change',controls);$('compareProject').addEventListener('click',compareProject);
  $('calculate').addEventListener('click',calculate);$('exportProject').addEventListener('click',exportProject);$('importProject').addEventListener('click',importProject);
  for(const id of ['packingMode','containerType','maxContainers'])$(id).addEventListener('change',()=>{controls();renderPacking();});
  $('maxContainers').addEventListener('input',()=>{controls();renderPacking();});
  $('previousPage').addEventListener('click',()=>{if(!state.busy && state.page>0){state.page--;renderLedger();}});$('nextPage').addEventListener('click',()=>{if(!state.busy){state.page++;renderLedger();}});
  $('auth').addEventListener('submit',event=>{event.preventDefault();document.cookie=`cb_token=${encodeURIComponent($('token').value)}; path=/; max-age=2592000; SameSite=Lax`;$('token').value='';$('auth').hidden=true;notify('访问口令已保存，请重试刚才的操作。');});
  win?.addEventListener?.('beforeunload',event=>{if(state.pending.size || state.draft || state.busy?.write){event.preventDefault();event.returnValue='';}});
  render();if(options.initialize!==false)void initialize();
  return {state,initialize,acceptDraft,acceptProject,openProject,upload,example,save,edit,reviewEdits,discardEdits,applyProposal,confirmLedger,undo,chat,compareProject,calculate,exportProject,importProject,cancel,refreshProjects,showSource,loadLinkedProposal,currentPacking,controls};
}
if(typeof document!=='undefined')startLogisticsApp(document);
