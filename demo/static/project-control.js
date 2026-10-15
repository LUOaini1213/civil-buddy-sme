/* User-authored project records. No source prose is translated or inserted as HTML. */
"use strict";
function createProjectControl(deps = {}) {
  const win = deps.window || window, doc = deps.document || document, fetcher = deps.fetch || win.fetch.bind(win);
  // UI vocabulary stays beside this feature; package metadata and user records
  // are rendered as plain text and never passed through translation.
  win.CBI18n?.add({
    "导入资料交接包":"Import document handoff package",
    "将另一实例导出的资料与事项创建为当前工程的副本。先核验 ZIP，再单独允许写入；不会恢复原复核、运行任务或覆盖原文件。":"Create copies of documents and issues exported from another instance in this project. Verify the ZIP first, then authorize writing separately. Import does not restore reviews, run tasks or overwrite source files.",
    "资料交接包（ZIP，最大 128 MiB）":"Document handoff package (ZIP, up to 128 MiB)",
    "核验所选交接包":"Verify selected package",
    "清空所选交接包":"Clear selected package",
    "包内仅含已登记资料的当前快照，不含旧修订原件。导入后内部复核全部重置；缺失旧证据的事项必须人工核对当前资料后再更新。":"The package contains current snapshots of registered documents, without earlier revision files. All internal reviews are reset on import. Issues with missing historical evidence require a manual check against current documents before updating.",
    "允许在当前工程创建资料副本和事项记录":"Allow document copies and issue records to be created in this project",
    "确认导入副本":"Confirm import of copies",
    "请选择工程文件夹和 ZIP，再核验交接包。":"Select a project folder and ZIP, then verify the package.",
    "请先选择工程文件夹。":"Select a project folder first.",
    "请选择一个不超过 128 MiB 的 ZIP 文件。":"Select a ZIP file no larger than 128 MiB.",
    "已选择交接包。请先核验来源与内容。":"Package selected. Verify its source and contents first.",
    "正在核验所选交接包；本次核验不写入资料。":"Verifying the selected package; this verification does not write documents.",
    "核验完成。请核对来源与数量，再勾选允许创建副本。":"Verification complete. Check the source and counts, then authorize creation of copies.",
    "请先核验当前选择的交接包。":"Verify the currently selected package first.",
    "请勾选允许在当前工程创建副本，再确认导入。":"Authorize creation of copies in this project before confirming import.",
    "正在导入副本，请等待服务器确认。":"Importing copies. Waiting for server confirmation.",
    "交接包响应无法核对，请重新核验当前文件。":"The package response could not be validated. Verify the current file again.",
    "交接包请求超时。结果可能尚未确认；可显式重试同一文件，不会自动重发。":"The package request timed out. Its outcome may be unconfirmed. You can explicitly retry the same file; no automatic resend will occur.",
    "交接包请求失败：{error}。请核对服务器记录；可显式重试同一文件，不会自动重发。":"Package request failed: {error}. Check server records. You can explicitly retry the same file; no automatic resend will occur.",
    "交接包已导入。内部复核已重置；请核对资料和缺失旧证据的事项。":"Package imported. Internal reviews were reset. Check the documents and issues with missing historical evidence.",
    "该交接包先前已导入，已读取原导入结果。内部复核仍须人工完成。":"This package was already imported. The original import result was loaded. Internal reviews still require manual completion.",
    "导入已获服务器确认，但台账刷新失败：{error}。请手动刷新核对，勿据此重复创建副本。":"The server confirmed the import, but refreshing records failed: {error}. Refresh manually to check; do not create another copy based on this refresh failure.",
    "来源工程：":"Source project:", "导出人：":"Exported by:", "导出截止时间：":"Export cutoff:",
    "资料：{documents}；事项：{issues}；缺少旧证据的事项：{missing}":"Documents: {documents}; issues: {issues}; issues missing historical evidence: {missing}",
    "交接包 SHA-256：":"Package SHA-256:", "已导入副本":"Imported copies", "已核验，尚未导入":"Verified; not imported yet",
    "{path}（已登记的交接来源）":"{path} (registered handoff source)",
    "已保留台账登记的交接来源。该受控副本不在普通文件列表中；保存修订时服务器仍会校验来源。":"The registered handoff source was retained. This controlled copy is excluded from ordinary file listings; the server will still validate it when saving a revision.",
    "交接包未包含该旧修订原件，核对当前资料后再更新。":"The handoff package did not include that earlier revision file. Check the current document before updating this issue.",
    "导入资料交接包记录":"Import document handoff records",
    "导入资料副本":"Import document copy", "导入交接事项":"Import handoff issue",
    "交接来源":"Handoff source", "原文件路径：":"Original file path:", "原修订：":"Original revision:", "来源人：":"Source exporter:",
    "来源证据修订：":"Source evidence revision:", "来源记录人：":"Original issue author:", "原事项 ID：":"Original issue ID:",
    "来源包记录的证据修订：{revision}。":"Evidence revision recorded by the source package: {revision}.",
    "来源包中该事项的证据本就缺失（修订 0），请重新核对当前资料。":"This issue's evidence was already missing in the source package (revision 0). Check the current document again.",
    "有导入请求的结果尚未确认（工程：{workspaces}）。清空或切换页面不会撤销服务器写入；请刷新相关工程台账，必要时用同一包显式重试。":"An import outcome is unconfirmed (project: {workspaces}). Clearing or leaving this page does not undo server writes. Refresh those project records and, if needed, explicitly retry the same package."
  });
  const $ = (id) => doc.getElementById(id), t = (s,p) => win.CBI18n?.t(s,p) || String(s).replace(/\{(\w+)\}/g,(_,k)=>p?.[k]??`{${k}}`);
  const fileListing = deps.fileListing || win.CBFileListing || globalThis.CBFileListing || (typeof require === "function" ? require("./modules/file-listing.js") : null);
  let listingI18n=null;
  const state = { workspace: "", workspaces: [], data: null, files: [], fileReport: null, fileFailure: false, sourceMissing: false, epoch: 0, refreshSeq: 0, historySeq: 0, workspaceSeq: 0, operationSeq: 0, busy: false, edit: null, issueEvidence: null, review: null, history: null, documentRevision: null, notice: null, packageFile: null, packagePreview: null, packageOutcome: null, packageSeq: 0, packageBusy: false, packageAbort: null, packageNotice: null, packageAction: null, packageTarget: null, packageUnconfirmed: [] };
  const statusNames = { open:"待处理", in_progress:"处理中", resolved:"已解决", review_required:"需要重新复核", work_in_progress:"待复核", internally_reviewed:"已内部复核", current:"来源一致", changed:"来源已变化", unavailable:"来源不可读", not_checked:"本次未检查" };
  const eventNames = { document_revision_registered:"登记资料修订", issue_saved:"保存事项", issue_reopened:"修订后重新打开事项", document_internal_review:"登记内部复核", project_handoff_imported:"导入资料交接包记录", document_handoff_imported:"导入资料副本", issue_handoff_imported:"导入交接事项" };
  const current = (epoch) => state.epoch === epoch;
  function node(tag, value, cls) { const n=doc.createElement(tag); if(value!==undefined)n.textContent=value; if(cls)n.className=cls; return n; }
  function button(text, fn) { const n=node("button",t(text)); n.type="button"; n.addEventListener("click",fn); return n; }
  function link(text,url) { const a=node("a",t(text),"pc-button"); a.href=url; return a; }
  function renderNotice(){if(state.notice){$("pcStatus").textContent=state.notice.translate?t(state.notice.message):state.notice.message;$("pcStatus").classList.toggle("pc-error",state.notice.error);}}
  function notice(message,error=false,translate=true) { state.notice={message,error,translate};renderNotice(); }
  function controls() {
    for(const form of ["pcWorkspaceForm","pcDocumentForm","pcIssueForm","pcReviewForm"]) for(const input of $(form).querySelectorAll("input,select,textarea,button")) input.disabled=state.busy || state.packageBusy || (form!=="pcWorkspaceForm" && !state.workspace);
    $("pcIssueDocument").disabled=state.busy || state.packageBusy || !state.workspace || !!state.edit;
    $("pcWorkspaceSelect").disabled=state.busy; $("pcRefresh").disabled=state.busy || state.packageBusy || !state.workspace;
    $("pcExport").hidden=!state.workspace; $("pcExport").href="/api/project-control/handoff?workspace="+encodeURIComponent(state.workspace);
    $("pcPackage").hidden=!state.workspace; $("pcPackage").href="/api/project-control/package?workspace="+encodeURIComponent(state.workspace);
    $("pcAgent").href="/static/agent.html"+(state.workspace?"?workspace="+encodeURIComponent(state.workspace):"");
    for(const host of ["pcDocuments","pcIssues","pcHistory"]) for(const b of $(host).querySelectorAll("button"))b.disabled=state.busy || state.packageBusy;
    $("pcImportFile").disabled=state.busy || !state.workspace;
    $("pcImportPreview").disabled=state.busy || state.packageBusy || !state.workspace || !state.packageFile;
    $("pcImportReset").disabled=!state.packageFile && !state.packagePreview && !state.packageOutcome && !state.packageBusy;
    $("pcImportAllow").disabled=state.busy || state.packageBusy || !state.packagePreview;
    $("pcImportSubmit").disabled=state.busy || state.packageBusy || !state.packagePreview || !$("pcImportAllow").checked;
  }
  async function request(url, body) { const r=await fetcher(url,body===undefined?undefined:{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)}); const value=await r.json(); if(!r.ok)throw new Error(value.detail||`HTTP ${r.status}`);return value; }
  const packageLimit=128*1024*1024;
  const packageCurrent=(epoch,seq,file,workspace)=>current(epoch) && state.packageSeq===seq && state.packageFile===file && state.workspace===workspace;
  function packageNotice(message,error=false,params){state.packageNotice={message,error,params};renderPackage();}
  function renderPackage(){
    const host=$("pcImportReceipt"), receipt=state.packagePreview || state.packageOutcome;
    host.replaceChildren();host.hidden=!receipt;
    if(receipt){
      host.append(node("h3",t(state.packageOutcome?"已导入副本":"已核验，尚未导入")));
      if(receipt.source)for(const [label,key] of [["来源工程：","workspace"],["导出人：","exported_by"],["导出截止时间：","cutoff_at"]]){const p=node("p");p.append(node("strong",t(label)),doc.createTextNode(" "+receipt.source[key]));host.append(p);}
      host.append(node("p",t("资料：{documents}；事项：{issues}；缺少旧证据的事项：{missing}",{documents:receipt.document_count,issues:receipt.issue_count,missing:receipt.missing_evidence_count})),node("p",t("交接包 SHA-256：")+" "+receipt.package_sha256,"pc-hash"));
    }
    const message=state.packageNotice || {message:"请选择工程文件夹和 ZIP，再核验交接包。",error:false};
    $("pcImportStatus").textContent=t(message.message,message.params);$("pcImportStatus").classList.toggle("pc-error",message.error);
    $("pcImportUnconfirmed").hidden=!state.packageUnconfirmed.length;$("pcImportUnconfirmed").textContent=state.packageUnconfirmed.length?t("有导入请求的结果尚未确认（工程：{workspaces}）。清空或切换页面不会撤销服务器写入；请刷新相关工程台账，必要时用同一包显式重试。",{workspaces:[...new Set(state.packageUnconfirmed.map(x=>x.workspace))].join(", ")}):"";controls();
  }
  function unconfirmedPackage(){const pending=state.packageTarget;if(pending && !state.packageUnconfirmed.some(x=>x.workspace===pending.workspace && x.hash===pending.hash))state.packageUnconfirmed.push(pending);}
  function resetPackage(clearFile=true){
    if(state.packageAction==="import")unconfirmedPackage();state.packageAction=null;state.packageTarget=null;
    state.packageSeq++;state.packageAbort?.abort();state.packageAbort=null;state.packageBusy=false;state.packageFile=null;state.packagePreview=null;state.packageOutcome=null;state.packageNotice=null;
    if(clearFile)$("pcImportFile").value="";$("pcImportAllow").checked=false;renderPackage();
  }
  function selectPackage(){
    const files=$("pcImportFile").files, file=files?.length===1?files[0]:null;
    resetPackage(false);
    if(!file)return;
    if(!state.workspace){packageNotice("请先选择工程文件夹。",true);return;}
    if(!/\.zip$/i.test(file.name) || !Number.isSafeInteger(file.size) || file.size<=0 || file.size>packageLimit){packageNotice("请选择一个不超过 128 MiB 的 ZIP 文件。",true);return;}
    state.packageFile=file;packageNotice("已选择交接包。请先核验来源与内容。");
  }
  function packageInvalid(){const error=new Error("交接包响应无法核对，请重新核验当前文件。");error.packageClientMessage=true;throw error;}
  function validatePackage(value,workspace,preview){
    const object=x=>x!==null && typeof x==="object" && !Array.isArray(x), text=(x,max)=>typeof x==="string" && x.length>0 && x.length<=max && !/[\u0000-\u001f\u007f]/.test(x);
    if(!object(value) || value.schema_version!==1 || value.workspace!==workspace || !/^[a-f0-9]{64}$/.test(value.package_sha256)
      || ["document_count","issue_count","missing_evidence_count"].some(k=>!Number.isSafeInteger(value[k]) || value[k]<0) || value.document_count>100 || value.issue_count>2000 || value.missing_evidence_count>value.issue_count)packageInvalid();
    if(preview){
      if(value.package_sha256!==preview.package_sha256 || ["document_count","issue_count","missing_evidence_count"].some(k=>value[k]!==preview[k]) || typeof value.replayed!=="boolean" || !/^[a-f0-9]{32}$/i.test(value.import_id))packageInvalid();
    }else if(value.review_reset!==true || !object(value.source) || !/^[a-f0-9]{32}$/i.test(value.source.workspace) || !text(value.source.exported_by,200) || !text(value.source.cutoff_at,100))packageInvalid();
    return value;
  }
  async function packageRequest(url,form,controller){
    let timer;
    const timeout=new Promise((_,reject)=>{timer=win.setTimeout(()=>{const error=new Error("交接包请求超时。结果可能尚未确认；可显式重试同一文件，不会自动重发。");error.packageClientMessage=true;reject(error);controller.abort();},deps.packageTimeoutMs ?? 45000);});
    try{return await Promise.race([(async()=>{const response=await fetcher(url,{method:"POST",body:form,signal:controller.signal});const value=await response.json();if(!response.ok){const error=new Error(value.detail||`HTTP ${response.status}`);error.packageHttpFailure=true;throw error;}return value;})(),timeout]);}finally{win.clearTimeout(timer);}
  }
  function packageFailure(error){
    if(error.packageClientMessage)packageNotice(error.message,true);
    else packageNotice("交接包请求失败：{error}。请核对服务器记录；可显式重试同一文件，不会自动重发。",true,{error:error.message});
  }
  async function previewPackage(){
    if(state.busy || state.packageBusy)return;
    if(!state.workspace){packageNotice("请先选择工程文件夹。",true);return;}
    if(!state.packageFile){packageNotice("请选择一个不超过 128 MiB 的 ZIP 文件。",true);return;}
    const epoch=state.epoch, workspace=state.workspace, file=state.packageFile, seq=++state.packageSeq, controller=new win.AbortController();
    state.packageAction="preview";state.packageTarget=null;state.packageAbort=controller;state.packageBusy=true;state.packagePreview=null;state.packageOutcome=null;$("pcImportAllow").checked=false;packageNotice("正在核验所选交接包；本次核验不写入资料。");
    const form=new win.FormData();form.append("workspace",workspace);form.append("file",file,file.name);
    try{const value=await packageRequest("/api/project-control/package/preview",form,controller);if(!packageCurrent(epoch,seq,file,workspace))return;state.packagePreview=validatePackage(value,workspace);packageNotice("核验完成。请核对来源与数量，再勾选允许创建副本。");}
    catch(error){if(packageCurrent(epoch,seq,file,workspace))packageFailure(error);}
    finally{if(packageCurrent(epoch,seq,file,workspace)){state.packageBusy=false;state.packageAbort=null;state.packageAction=null;renderPackage();}}
  }
  async function importPackage(){
    if(state.busy || state.packageBusy)return;
    const preview=state.packagePreview;
    if(!state.workspace || !state.packageFile || !preview || preview.workspace!==state.workspace){packageNotice("请先核验当前选择的交接包。",true);return;}
    if(!$("pcImportAllow").checked){packageNotice("请勾选允许在当前工程创建副本，再确认导入。",true);return;}
    const epoch=state.epoch, workspace=state.workspace, file=state.packageFile, seq=++state.packageSeq, controller=new win.AbortController();
    state.packageAction="import";state.packageTarget={workspace,hash:preview.package_sha256};state.packageAbort=controller;state.packageBusy=true;$("pcImportAllow").checked=false;state.refreshSeq++;state.historySeq++;packageNotice("正在导入副本，请等待服务器确认。");
    const form=new win.FormData();form.append("workspace",workspace);form.append("file",file,file.name);form.append("expected_sha256",preview.package_sha256);form.append("allow_workspace_write","true");
    try{
      const value=await packageRequest("/api/project-control/package/import",form,controller);if(!packageCurrent(epoch,seq,file,workspace))return;
      const result=validatePackage(value,workspace,preview);state.packageAction=null;state.packageTarget=null;state.packageUnconfirmed=state.packageUnconfirmed.filter(x=>x.workspace!==workspace || x.hash!==preview.package_sha256);state.packageOutcome={...result,source:preview.source};state.packagePreview=null;
      // Confirmed success is retained even if the separate read-back fails.
      try{await refresh();if(!packageCurrent(epoch,seq,file,workspace))return;packageNotice(result.replayed?"该交接包先前已导入，已读取原导入结果。内部复核仍须人工完成。":"交接包已导入。内部复核已重置；请核对资料和缺失旧证据的事项。");}
      catch(error){if(packageCurrent(epoch,seq,file,workspace))packageNotice("导入已获服务器确认，但台账刷新失败：{error}。请手动刷新核对，勿据此重复创建副本。",true,{error:error.message});}
      if(packageCurrent(epoch,seq,file,workspace)){state.packageFile=null;$("pcImportFile").value="";}
    }catch(error){if(packageCurrent(epoch,seq,file,workspace)){if(!error.packageHttpFailure)unconfirmedPackage();packageFailure(error);}}
    finally{if(current(epoch) && state.packageSeq===seq){state.packageBusy=false;state.packageAbort=null;state.packageAction=null;state.packageTarget=null;$("pcImportAllow").checked=false;renderPackage();}}
  }
  function options(host,items,first) { const old=host.value; host.replaceChildren(); if(first){const n=node("option",t(first));n.value="";host.append(n);} for(const item of items){const n=node("option",item.label);n.value=item.value;host.append(n);} if([...host.options].some(n=>n.value===old))host.value=old; }
  function renderSourceNotice(){
    if(win.CBI18n && listingI18n!==win.CBI18n){win.CBI18n.add(fileListing.messages);listingI18n=win.CBI18n;}
    const messages=[];
    if(state.fileFailure)messages.push(fileListing.failure(t));
    else if(state.fileReport){const message=fileListing.notice(state.fileReport,t);if(message)messages.push(message);}
    const source=$("pcSource").value;
    if(registeredHandoffSource(source))messages.push(t("已保留台账登记的交接来源。该受控副本不在普通文件列表中；保存修订时服务器仍会校验来源。"));
    else if(source && state.fileReport && !state.fileReport.complete && !state.files.some(f=>f.path===source))messages.push(fileListing.retained(source,t));
    if(/\.(ifc|dxf)$/i.test(source))messages.push(t("IFC/DXF 在这里用于登记文件快照；模型内容解析取决于相应工具和环境。"));
    if(state.sourceMissing)messages.push(t("之前选择的来源未出现在完整文件列表中，已取消选择。请重新选择来源。"));
    $("pcSourceNotice").textContent=messages.join(" ");$("pcSourceNotice").hidden=!messages.length;
  }
  function validateFiles(data,workspace){try{return fileListing.validate(data,workspace,"project_control");}catch(e){e.fileListingFailure=true;throw e;}}
  function errorNotice(e){notice(e.message,true,!!e.fileListingFailure);}
  function renderSources(selected=$("pcSource").value){
    const items=state.files.map(f=>({value:f.path,label:f.path}));
    if(selected && !items.some(f=>f.value===selected)){
      if(registeredHandoffSource(selected)){items.push({value:selected,label:t("{path}（已登记的交接来源）",{path:selected})});state.sourceMissing=false;}
      else if(state.fileReport && !state.fileReport.complete)items.push({value:selected,label:t("{path}（本次未列出，保留已选来源）",{path:selected})});
      else if(state.fileReport?.complete)state.sourceMissing=true;
    }
    options($("pcSource"),items,"请选择来源文件");
    if(items.some(f=>f.value===selected))$("pcSource").value=selected;else $("pcSource").value="";
    renderSourceNotice();
  }
  function documents(){return state.data?.documents||[];}
  function registeredHandoffSource(path){return typeof path==="string" && path.length<=1024 && path.startsWith(".civil-buddy/out/handoffs/") && !/[\\:\u0000-\u001f\u007f]/.test(path) && path.split("/").length>=5 && path.split("/").every(part=>part && part!=="." && part!=="..") && documents().some(d=>d.path===path);}
  function provenanceDetails(record,kind){
    const provenance=record?.provenance;if(!provenance || typeof provenance!=="object" || Array.isArray(provenance))return null;
    const fields=kind==="document"?[["原文件路径：","source_path"],["原修订：","source_revision"],["来源人：","source_exported_by"],["来源工程：","source_workspace"]]:[["来源证据修订：","source_document_revision"],["来源记录人：","source_actor_id"],["原事项 ID：","source_issue_id"],["来源工程：","source_workspace"]];
    const details=node("details",undefined,"pc-provenance");details.append(node("summary",t("交接来源")));
    for(const [label,key] of fields){const value=provenance[key];if(!(typeof value==="string" && value.length>0 && value.length<=4096) && !(Number.isSafeInteger(value) && value>=0))continue;const p=node("p");p.append(node("strong",t(label)),doc.createTextNode(" "+String(value)));details.append(p);}
    return details.childNodes.length>1?details:null;
  }
  function missingEvidenceMessage(issue){
    let message=t("交接包未包含该旧修订原件，核对当前资料后再更新。");const revision=issue?.provenance?.source_document_revision;
    if(Number.isSafeInteger(revision) && revision>=0)message+=" "+t(revision===0?"来源包中该事项的证据本就缺失（修订 0），请重新核对当前资料。":"来源包记录的证据修订：{revision}。",{revision});
    return message;
  }
  function selectedDocument(){return documents().find(d=>d.id===$("pcDocumentId").value);}
  function editDocument(){ const d=selectedDocument();state.documentRevision=d?.revision??null; if(d){$("pcTitle").value=d.title;$("pcDiscipline").value=d.discipline;$("pcRevision").value="";state.sourceMissing=false;renderSources(d.path);} }
  function issueEvidence(){const d=state.issueEvidence, latest=documents().find(x=>x.id===d?.id);$("pcIssueEvidence").textContent=d?t("本次保存绑定：{title} · {revision}",{title:d.title,revision:d.revision_label})+(latest?.revision!==d.revision?" · "+t("资料已有新修订，请重新核对后更新事项。"):""):t("先登记资料，再创建事项。");if(state.edit?.document_revision===0 || state.edit?.evidence_status==="unavailable")$("pcIssueEvidence").textContent+=" · "+missingEvidenceMessage(state.edit);$("pcResolution").required=$("pcIssueStatus").value==="resolved";}
  // Capture evidence only on an explicit editing/selection gesture. A refresh
  // must not silently rebase the user's description or resolution onto new bytes.
  function bindIssueEvidence(){const d=documents().find(x=>x.id===$("pcIssueDocument").value);state.issueEvidence=d?{id:d.id,revision:d.revision,title:d.title,revision_label:d.revision_label}:null;issueEvidence();}
  function resetIssue(){state.edit=null;state.issueEvidence=null;$("pcIssueForm").reset();$("pcIssueEditing").textContent=t("新事项从待处理开始。");issueEvidence();controls();}
  function editIssue(issue){state.edit=JSON.parse(JSON.stringify(issue));$("pcIssueEditor").open=true;for(const [field,key] of [["pcIssueDocument","document_id"],["pcKind","kind"],["pcIssueTitle","title"],["pcDescription","description"],["pcAssignee","assignee"],["pcDue","due_date"],["pcResolution","resolution"]])$(field).value=issue[key]||"";$("pcIssueStatus").value=issue.status==="review_required"?"in_progress":issue.status;$("pcIssueEditing").textContent=t("更新事项；保存时将明确绑定目前列出的资料版本。");bindIssueEvidence();controls();}
  function renderHistory(){const host=$("pcHistory");host.replaceChildren();if(!state.history)return;host.append(node("h3",t("修订快照")));for(const r of state.history.revisions){const card=node("div",undefined,"pc-card");card.append(node("p",`${r.revision_label} · ${r.path}`),node("p",r.sha256,"pc-hash"),link("下载该修订",`/api/project-control/documents/${encodeURIComponent(r.id)}/revisions/${r.revision}?workspace=${encodeURIComponent(state.workspace)}`));host.append(card);}}
  function render(){
    options($("pcWorkspaceSelect"),state.workspaces.map(w=>({value:w.id,label:w.root})),"请选择工程文件夹");
    $("pcMetrics").replaceChildren();for(const [value,label] of [[documents().length,"受控资料"],[state.data?.unresolved_issues||0,"未解决事项"],[documents().filter(d=>d.effective_status==="internally_reviewed").length,"已内部复核"]]){const n=node("div",undefined,"pc-metric");n.append(node("strong",String(value)),node("span",t(label)));$("pcMetrics").append(n);}
    options($("pcDocumentId"),documents().map(d=>({value:d.id,label:`${d.title} · ${d.revision_label}`})),"新资料");
    renderSources();
    options($("pcIssueDocument"),documents().map(d=>({value:d.id,label:`${d.title} · ${d.revision_label}`})),"请选择关联资料");
    const host=$("pcDocuments");host.replaceChildren();if(!documents().length)host.append(node("p",t("尚无受控资料。展开下方表单登记第一份文件。"),"pc-muted"));
    for(const d of documents()){
      const card=node("article",undefined,"pc-card"+(d.source_status!=="current"?" pc-warning":""));card.append(node("h3",d.title),node("p",`${d.discipline} · ${d.revision_label} · ${d.path}`),node("span",t(statusNames[d.effective_status]||d.effective_status),"pc-tag"),node("span",t(statusNames[d.source_status]||d.source_status),"pc-tag"),node("p",d.sha256,"pc-hash"));
      const provenance=provenanceDetails(d,"document");if(provenance)card.append(provenance);
      const actions=node("div",undefined,"pc-row");actions.append(button("新增修订",()=>{$("pcDocumentEditor").open=true;$("pcDocumentId").value=d.id;editDocument();}),button("修订历史",()=>loadHistory(d.id)),button("内部复核",()=>{state.review={id:d.id,revision:d.revision,reviewVersion:d.review_version,title:d.title};$("pcReviewTarget").textContent=`${d.title} · ${d.revision_label}`;$("pcReviewPanel").hidden=false;$("pcReviewNotes").value="";}));card.append(actions);host.append(card);
    }
    const issues=$("pcIssues");issues.replaceChildren();for(const i of state.data?.issues||[]){if($("pcFilter").value==="active"&&i.status==="resolved")continue;const d=documents().find(d=>d.id===i.document_id);const card=node("article",undefined,"pc-card"+(i.document_revision!==d?.revision?" pc-warning":""));card.append(node("h3",i.title),node("span",t(statusNames[i.status]||i.status),"pc-tag"),node("p",`${i.assignee} · ${i.due_date||t("未设日期")}`),node("p",i.description),node("p",t("证据修订：{revision}",{revision:i.document_revision}),"pc-muted"));if(i.resolution)card.append(node("p",i.resolution));if(i.document_revision===0 || i.evidence_status==="unavailable")card.append(node("p",missingEvidenceMessage(i),"pc-muted"));else if(i.document_revision!==d?.revision)card.append(node("p",t("资料已有新修订，请重新核对后更新事项。"),"pc-muted"));const provenance=provenanceDetails(i,"issue");if(provenance)card.append(provenance);card.append(button("更新事项",()=>editIssue(i)));issues.append(card);}if(!issues.childNodes.length)issues.append(node("p",t("当前筛选下没有事项。"),"pc-muted"));
    const events=$("pcEvents");events.replaceChildren();for(const e of state.data?.events||[]){events.append(node("p",`${e.at} · ${e.actor_id} · ${t(eventNames[e.kind]||e.kind)} · ${e.entity_id}`,"pc-muted"));}
    renderHistory();issueEvidence();renderNotice();renderPackage();controls();
  }
  async function refresh(){if(!state.workspace)return;const epoch=state.epoch, seq=++state.refreshSeq, workspace=state.workspace;notice("正在检查来源…");try{const [data,files]=await Promise.all([request("/api/project-control?complete=true&workspace="+encodeURIComponent(workspace)),request("/api/project-control/files?workspace="+encodeURIComponent(workspace))]);if(!current(epoch)||seq!==state.refreshSeq)return;const report=validateFiles(files,workspace);state.data=data;state.files=report.files;state.fileReport=report;state.fileFailure=false;render();notice("已读取服务器记录并检查来源。");}catch(e){if(current(epoch)&&seq===state.refreshSeq){state.fileFailure=true;renderSourceNotice();throw e;}}}
  async function selectWorkspace(value){state.epoch++;state.refreshSeq++;state.historySeq++;state.operationSeq++;state.busy=false;state.workspace=value;resetPackage();$("pcWorkspaceSelect").value=value;state.data=null;state.files=[];state.fileReport=null;state.fileFailure=false;state.sourceMissing=false;state.history=null;state.review=null;state.documentRevision=null;$("pcReviewPanel").hidden=true;$("pcDocumentForm").reset();resetIssue();render();if(!value){notice("请选择或打开工程文件夹。");return;}const epoch=state.epoch;try{await refresh();if(current(epoch))try{win.localStorage.setItem("cb_project_workspace_v1",value);}catch(_){/* optional */}}catch(e){if(current(epoch))errorNotice(e);}}
  async function perform(fn,success,pending="正在保存…"){if(state.busy || state.packageBusy)return;const epoch=state.epoch, operation=++state.operationSeq;state.refreshSeq++;state.historySeq++;state.busy=true;notice(pending);controls();try{await fn();if(!current(epoch)||operation!==state.operationSeq)return;if(success)success();await refresh();}catch(e){if(current(epoch)&&operation===state.operationSeq)errorNotice(e);}finally{if(operation===state.operationSeq){state.busy=false;controls();}}}
  async function loadHistory(id){const epoch=state.epoch, seq=++state.historySeq;try{const h=await request(`/api/project-control/documents/${encodeURIComponent(id)}/revisions?workspace=${encodeURIComponent(state.workspace)}`);if(current(epoch)&&seq===state.historySeq){state.history=h;renderHistory();}}catch(e){if(current(epoch)&&seq===state.historySeq)notice(e.message,true,false);}}
  async function loadWorkspaces(){const seq=++state.workspaceSeq;try{const res=await request("/api/agent/workspaces");if(seq!==state.workspaceSeq)return false;state.workspaces=res.workspaces||[];options($("pcWorkspaceSelect"),state.workspaces.map(w=>({value:w.id,label:w.root})),"请选择工程文件夹");return true;}catch(e){if(seq===state.workspaceSeq)throw e;return false;}}
  async function registerWorkspace(path){const epoch=state.epoch;await perform(async()=>{const res=await request("/api/agent/workspaces",{path});if(!current(epoch))return;if(!await loadWorkspaces()||!current(epoch))return;await selectWorkspace(res.workspace.id);},undefined,"正在打开工程文件夹…");}
  async function saveDocument(){const d=selectedDocument();await perform(()=>request("/api/project-control/documents",{workspace:state.workspace,document_id:d?.id||null,expected_revision:d?state.documentRevision:null,path:$("pcSource").value,title:$("pcTitle").value,discipline:$("pcDiscipline").value,revision_label:$("pcRevision").value}),()=>{$("pcDocumentForm").reset();state.documentRevision=null;state.sourceMissing=false;state.history=null;});}
  async function saveIssue(){const d=state.issueEvidence;if(!d||d.id!==$("pcIssueDocument").value){notice("请选择关联资料",true);return;}await perform(()=>request("/api/project-control/issues",{workspace:state.workspace,id:state.edit?.id||null,expected_version:state.edit?.version??null,document_id:d.id,document_revision:d.revision,kind:$("pcKind").value,title:$("pcIssueTitle").value,description:$("pcDescription").value,assignee:$("pcAssignee").value,due_date:$("pcDue").value,status:$("pcIssueStatus").value,resolution:$("pcResolution").value}),resetIssue);}
  async function saveReview(){if(!state.review)return;const review=state.review;await perform(()=>request(`/api/project-control/documents/${encodeURIComponent(review.id)}/review`,{workspace:state.workspace,expected_revision:review.revision,expected_review_version:review.reviewVersion,notes:$("pcReviewNotes").value}),()=>{state.review=null;$("pcReviewPanel").hidden=true;});}
  async function start(){
    $("pcWorkspaceForm").addEventListener("submit",e=>{e.preventDefault();registerWorkspace($("pcWorkspacePath").value);});$("pcWorkspaceSelect").addEventListener("change",()=>selectWorkspace($("pcWorkspaceSelect").value));$("pcRefresh").addEventListener("click",()=>perform(async()=>{},undefined,"正在检查来源…"));
    $("pcDocumentId").addEventListener("change",editDocument);$("pcSource").addEventListener("change",()=>{state.sourceMissing=false;renderSourceNotice();});$("pcDocumentForm").addEventListener("submit",e=>{e.preventDefault();saveDocument();});$("pcIssueForm").addEventListener("submit",e=>{e.preventDefault();saveIssue();});$("pcIssueDocument").addEventListener("change",bindIssueEvidence);$("pcIssueStatus").addEventListener("change",issueEvidence);$("pcNewIssue").addEventListener("click",resetIssue);$("pcFilter").addEventListener("change",render);$("pcReviewForm").addEventListener("submit",e=>{e.preventDefault();saveReview();});$("pcReviewCancel").addEventListener("click",()=>{state.review=null;$("pcReviewPanel").hidden=true;});win.addEventListener("cb:languagechange",render);
    $("pcImportFile").addEventListener("change",selectPackage);$("pcImportPreview").addEventListener("click",previewPackage);$("pcImportReset").addEventListener("click",()=>resetPackage());$("pcImportAllow").addEventListener("change",controls);$("pcImportForm").addEventListener("submit",e=>{e.preventDefault();importPackage();});
    win.addEventListener("pagehide",()=>resetPackage());win.addEventListener("beforeunload",()=>resetPackage());
    const epoch=state.epoch, operation=state.operationSeq;notice("正在读取工程列表…");controls();try{if(!await loadWorkspaces()||!current(epoch)||operation!==state.operationSeq)return;let saved="";try{saved=win.localStorage.getItem("cb_project_workspace_v1");}catch(_){}if(state.workspaces.some(w=>w.id===saved)){$("pcWorkspaceSelect").value=saved;await selectWorkspace(saved);}else notice("请选择或打开工程文件夹。");}catch(e){if(current(epoch)&&operation===state.operationSeq)notice(e.message,true,false);}
  }
  return {state,start,selectWorkspace,refresh,registerWorkspace,saveDocument,saveIssue,saveReview,editIssue,render,selectPackage,previewPackage,importPackage,resetPackage};
}
if(typeof module!=="undefined"&&module.exports)module.exports={createProjectControl};
if(typeof window!=="undefined")window.addEventListener("DOMContentLoaded",()=>{if(document.getElementById("projectControl")){window.CivilProjectControl=createProjectControl();window.CivilProjectControl.start();}});
