"use strict";
const {test}=require("node:test"), assert=require("node:assert/strict"),fs=require("node:fs"),path=require("node:path");
const {JSDOM}=require("jsdom");
const {createProjectControl}=require("../demo/static/project-control.js");
const html=fs.readFileSync(path.join(__dirname,"../demo/static/project-control.html"),"utf8");
const documentRecord={id:"doc1",title:"原始方案 <script>alert(1)</script>",path:"资料.md",discipline:"幕墙",revision:1,review_version:1,revision_label:"Rev A",sha256:"a".repeat(64),status:"work_in_progress",effective_status:"work_in_progress",source_status:"current"};
const issue={id:"issue1",version:4,document_id:"doc1",document_revision:1,kind:"rfi",title:"复核原文",description:"尺寸按原文件，不擅自翻译",assignee:"张工",due_date:"2026-10-15",status:"review_required",resolution:"原说明"};
const response=(v,status=200)=>new Response(JSON.stringify(v),{status,headers:{"Content-Type":"application/json"}});
const fileRecord=source=>({path:source,name:source.split("/").at(-1),size:21});
const fileReport=(workspace="w1",sources=["资料.md","材料.xlsx"],overrides={})=>({schema_version:1,workspace,purpose:"project_control",files:sources.map(fileRecord),returned_count:sources.length,complete:true,limits:{max_files:500,max_directory_depth:5,max_scanned_entries:10000},file_limit_reached:false,depth_limit_reached:false,scan_limit_reached:false,incomplete_reasons:[],...overrides});
function harness(override=()=>undefined,options={}){
  const dom=new JSDOM(html,{url:"http://localhost/static/project-control.html",runScripts:"outside-only"});let data={documents:[{...documentRecord}],issues:[{...issue}],events:[],unresolved_issues:1};const calls=[];
  for(const f of ["i18n.js","i18n-project.js"])dom.window.eval(fs.readFileSync(path.join(__dirname,"../demo/static",f),"utf8"));
  dom.window.document.dispatchEvent(new dom.window.Event("DOMContentLoaded"));
  const fetch=async(url,init)=>{const body=typeof init?.body==="string"?JSON.parse(init.body):init?.body;calls.push({url,body,init});const custom=await override(url,body,init);if(custom!==undefined)return custom;if(url==="/api/agent/workspaces")return response({workspaces:[{id:"w1",root:"C:/甲项目"},{id:"w2",root:"C:/乙项目"}]});if(url.startsWith("/api/project-control/files?"))return response(fileReport(new URL(url,"http://localhost").searchParams.get("workspace")));if(url.startsWith("/api/project-control?"))return response(data);if(url.startsWith("/api/project-control/documents/doc1/revisions"))return response({revisions:[documentRecord]});throw new Error("Unexpected route "+url);};
  const app=createProjectControl({document:dom.window.document,window:dom.window,fetch,...options});return{app,dom,calls,$:id=>dom.window.document.getElementById(id),setData:v=>{data=v;},async start(){await app.start();await app.selectWorkspace("w1");},close(){dom.window.close();}};
}
test("bilingual product changes UI without translating source records, fields or evidence",async()=>{
  const h=harness();try{await h.start();h.$("pcTitle").value="用户输入 Rev B";assert.equal(h.$("pcDocuments").querySelector("script"),null);assert.match(h.$("pcDocuments").textContent,/<script>/);h.dom.window.CBI18n.setLocale("en");assert.match(h.$("pcDocuments").textContent,/Source unchanged/);assert.match(h.$("pcDocuments").textContent,/原始方案/);assert.match(h.$("pcIssues").textContent,/张工/);assert.equal(h.$("pcTitle").value,"用户输入 Rev B");assert.equal(h.dom.window.document.documentElement.lang,"en");h.dom.window.CBI18n.setLocale("zh-CN");assert.match(h.$("pcDocuments").textContent,/来源一致/);}finally{h.close();}
});
test("revision edits send captured revision and preserve draft when server reports a conflict",async()=>{
  const h=harness((url,body)=>url==="/api/project-control/documents"?response({detail:"Document revision changed; reload before saving"},409):undefined);try{await h.start();h.$("pcDocumentId").value="doc1";h.$("pcDocumentId").dispatchEvent(new h.dom.window.Event("change"));h.$("pcRevision").value="Rev B";await h.app.saveDocument();const sent=h.calls.find(c=>c.url==="/api/project-control/documents").body;assert.equal(sent.expected_revision,1);assert.equal(sent.document_id,"doc1");assert.equal(sent.path,"资料.md");assert.equal(sent.revision_label,"Rev B");assert.match(h.$("pcStatus").textContent,/changed/);assert.equal(h.$("pcRevision").value,"Rev B");assert.equal(h.app.state.busy,false);}finally{h.close();}
});
test("issue updates bind exact version and retain assignee/evidence without approving a document",async()=>{
  const h=harness((url,body)=>url==="/api/project-control/issues"?response({...body,version:5}):undefined);try{await h.start();h.app.editIssue(issue);assert.equal(h.$("pcIssueDocument").disabled,true);assert.equal(h.$("pcIssueStatus").value,"in_progress");h.$("pcIssueStatus").value="resolved";h.$("pcResolution").value="核对新版图纸 R-2";await h.app.saveIssue();const sent=h.calls.find(c=>c.url==="/api/project-control/issues").body;assert.equal(sent.expected_version,4);assert.equal(sent.document_revision,1);assert.equal(sent.assignee,"张工");assert.equal(sent.resolution,"核对新版图纸 R-2");assert.equal(h.calls.some(c=>c.url.endsWith("/review")),false);assert.equal(h.app.state.edit,null);}finally{h.close();}
});

test("review preserves the opened issue basis across refresh and language changes, then requires explicit reopening",async()=>{
  const h=harness((url,body)=>url.endsWith("/review")?body.expected_review_version===3?response({...documentRecord,review_version:3,status:"internally_reviewed"}):response({detail:"Document issues or review evidence changed; reload before review"},409):undefined);
  const openReview=()=>[...h.$("pcDocuments").querySelectorAll("button")].find(b=>b.textContent===h.dom.window.CBI18n.t("内部复核")).click();
  try{
    await h.start();openReview();h.$("pcReviewNotes").value="基于旧问题记录的复核草稿";
    h.setData({documents:[{...documentRecord,review_version:3}],issues:[],events:[],unresolved_issues:0});await h.app.refresh();
    h.dom.window.CBI18n.setLocale("en");await h.app.saveReview();
    const old=h.calls.find(c=>c.url.endsWith("/review")).body;
    assert.equal(old.expected_revision,1);assert.equal(old.expected_review_version,1);
    assert.equal(old.notes,"基于旧问题记录的复核草稿");assert.equal(h.$("pcReviewNotes").value,old.notes);
    assert.equal(h.app.state.review.reviewVersion,1);assert.equal(h.$("pcReviewPanel").hidden,false);
    openReview();h.$("pcReviewNotes").value="已重新阅读新问题记录";await h.app.saveReview();
    assert.equal(h.calls.filter(c=>c.url.endsWith("/review")).at(-1).body.expected_review_version,3);
    assert.equal(h.app.state.review,null);assert.equal(h.$("pcReviewPanel").hidden,true);
  }finally{h.close();}
});
test("late data from a previous workspace cannot overwrite the new project",async()=>{
  let resolve;const waiting=new Promise(r=>{resolve=r;});let delay=false;
  const h=harness(url=>delay&&url==="/api/project-control?complete=true&workspace=w1"?waiting:undefined);try{await h.start();delay=true;const old=h.app.refresh();await new Promise(r=>setImmediate(r));h.setData({documents:[],issues:[],events:[],unresolved_issues:0});await h.app.selectWorkspace("w2");resolve(response({documents:[documentRecord],issues:[issue],events:[],unresolved_issues:1}));await old;assert.equal(h.app.state.workspace,"w2");assert.equal(h.app.state.data.documents.length,0);assert.doesNotMatch(h.$("pcDocuments").textContent,/原始方案/);assert.match(h.$("pcExport").href,/workspace=w2$/);}finally{h.close();}
});
test("late revision history cannot cross workspace boundaries",async()=>{
  let resolve;const waiting=new Promise(r=>{resolve=r;});const h=harness(url=>url.includes("/revisions?")?waiting:undefined);try{await h.start();[...h.$("pcDocuments").querySelectorAll("button")].find(b=>b.textContent==="修订历史").click();await new Promise(r=>setImmediate(r));await h.app.selectWorkspace("w2");resolve(response({revisions:[documentRecord]}));await new Promise(r=>setImmediate(r));assert.equal(h.$("pcHistory").textContent,"");}finally{h.close();}
});

const tick=()=>new Promise(r=>setImmediate(r));
function deferred(){let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b;});return{promise,resolve,reject};}
const dataAt=revision=>({documents:[{...documentRecord,revision,revision_label:`Rev ${revision}`}],issues:[{...issue}],events:[],unresolved_issues:1});

test("issue drafts retain editing-time evidence after refresh and preserve all fields on conflict",async()=>{
  const h=harness(url=>url==="/api/project-control/issues"?response({detail:"Issue evidence revision changed"},409):undefined);
  try{
    await h.start();h.app.editIssue(issue);h.$("pcResolution").value="按旧证据核对的草稿";
    h.setData(dataAt(2));await h.app.refresh();h.dom.window.CBI18n.setLocale("en");
    assert.match(h.$("pcIssueEvidence").textContent,/Rev A/);assert.match(h.$("pcIssueEvidence").textContent,/newer.*revision/i);
    await h.app.saveIssue();const sent=h.calls.find(c=>c.url==="/api/project-control/issues").body;
    assert.equal(sent.document_revision,1);assert.equal(sent.expected_version,4);assert.equal(sent.resolution,"按旧证据核对的草稿");
    assert.equal(h.$("pcResolution").value,"按旧证据核对的草稿");assert.equal(h.app.state.edit.version,4);
    h.dom.window.CBI18n.setLocale("zh-CN");assert.equal(h.$("pcStatus").textContent,"Issue evidence revision changed");
  }finally{h.close();}
});

test("new issue evidence changes only after another explicit document selection",async()=>{
  const h=harness(url=>url==="/api/project-control/issues"?response({detail:"Conflict retained for test"},409):undefined);
  try{
    await h.start();h.$("pcIssueDocument").value="doc1";h.$("pcIssueDocument").dispatchEvent(new h.dom.window.Event("change"));
    h.setData(dataAt(2));await h.app.refresh();await h.app.saveIssue();
    assert.equal(h.calls.filter(c=>c.url==="/api/project-control/issues").at(-1).body.document_revision,1);
    h.$("pcIssueDocument").dispatchEvent(new h.dom.window.Event("change"));await h.app.saveIssue();
    assert.equal(h.calls.filter(c=>c.url==="/api/project-control/issues").at(-1).body.document_revision,2);
  }finally{h.close();}
});

test("same-workspace refreshes keep the newest response and ignore an obsolete failure",async()=>{
  let pending=false;const queue=[];const h=harness(url=>pending&&url.startsWith("/api/project-control?")?queue.shift().promise:undefined);
  try{
    await h.start();pending=true;const a=deferred(),b=deferred();queue.push(a,b);
    const older=h.app.refresh(),newer=h.app.refresh();b.resolve(response(dataAt(3)));await newer;a.resolve(response(dataAt(2)));await older;
    assert.equal(h.app.state.data.documents[0].revision,3);
    const staleError=deferred(),fresh=deferred();queue.push(staleError,fresh);const oldError=h.app.refresh(),latest=h.app.refresh();
    fresh.resolve(response(dataAt(4)));await latest;staleError.reject(new Error("obsolete error"));await oldError;
    assert.equal(h.app.state.data.documents[0].revision,4);assert.doesNotMatch(h.$("pcStatus").textContent,/obsolete/);
  }finally{h.close();}
});

test("revision-history responses cannot replace a more recently selected document",async()=>{
  const first=deferred(),second=deferred();const h=harness(url=>url.includes("/doc1/revisions?")?first.promise:url.includes("/doc2/revisions?")?second.promise:undefined);
  try{
    await h.start();h.setData({documents:[documentRecord,{...documentRecord,id:"doc2",title:"第二份资料"}],issues:[],events:[]});await h.app.refresh();
    const histories=[...h.$("pcDocuments").querySelectorAll("button")].filter(b=>b.textContent==="修订历史");histories[0].click();histories[1].click();
    second.resolve(response({revisions:[{...documentRecord,id:"doc2",revision_label:"Second"}]}));await tick();
    first.resolve(response({revisions:[{...documentRecord,revision_label:"First"}]}));await tick();
    assert.match(h.$("pcHistory").textContent,/Second/);assert.doesNotMatch(h.$("pcHistory").textContent,/First/);
  }finally{h.close();}
});

test("starting a save invalidates a previously pending refresh in the same workspace",async()=>{
  const stale=deferred(),saving=deferred();let delay=false;const h=harness(url=>url==="/api/project-control/issues"?saving.promise:delay&&url.startsWith("/api/project-control?")?stale.promise:undefined);
  try{
    await h.start();h.app.editIssue(issue);delay=true;const refresh=h.app.refresh();const save=h.app.saveIssue();await tick();
    stale.resolve(response(dataAt(9)));await refresh;assert.equal(h.app.state.data.documents[0].revision,1);
    assert.equal(h.$("pcStatus").textContent,"正在保存…");delay=false;saving.resolve(response({detail:"Keep original draft"},409));await save;
    assert.equal(h.app.state.issueEvidence.revision,1);assert.equal(h.app.state.edit.version,4);
  }finally{h.close();}
});

test("an old workspace save cannot clear a newer busy state or reset its draft",async()=>{
  const first=deferred(),second=deferred();let writes=0;const h=harness(url=>url==="/api/project-control/issues"?(++writes===1?first.promise:second.promise):undefined);
  try{
    await h.start();h.app.editIssue(issue);const old=h.app.saveIssue();await tick();await h.app.selectWorkspace("w2");
    h.app.editIssue(issue);h.$("pcResolution").value="乙项目中的草稿";const current=h.app.saveIssue();await tick();
    assert.equal(h.app.state.busy,true);first.resolve(response({ok:true}));await old;
    assert.equal(h.app.state.busy,true);assert.equal(h.$("pcResolution").value,"乙项目中的草稿");
    second.resolve(response({detail:"New workspace conflict"},409));await current;
    assert.equal(h.app.state.busy,false);assert.equal(h.$("pcResolution").value,"乙项目中的草稿");assert.match(h.$("pcPackage").href,/workspace=w2$/);
  }finally{h.close();}
});

test("status and workspace placeholder switch language, pending operations give feedback, and package follows workspace",async()=>{
  const saving=deferred(),opening=deferred();const h=harness((url,body)=>url==="/api/project-control/issues"?saving.promise:url==="/api/agent/workspaces"&&body?opening.promise:undefined);
  try{
    await h.start();h.dom.window.CBI18n.setLocale("en");assert.equal(h.$("pcWorkspaceSelect").options[0].textContent,"Select a project folder");
    assert.equal(h.$("pcStatus").textContent,h.dom.window.CBI18n.t("已读取服务器记录并检查来源。"));
    assert.equal(h.$("pcWorkspaceSelect").value,"w1");assert.match(h.$("pcWorkspaceSelect").textContent,/甲项目/);
    assert.equal(h.$("pcPackage").textContent,"Download document handoff package");assert.match(h.$("pcPackage").href,/workspace=w1$/);
    assert.match(h.$("pcAgent").href,/\/static\/agent\.html\?workspace=w1$/);
    h.app.editIssue(issue);const save=h.app.saveIssue();await tick();assert.equal(h.$("pcStatus").textContent,"Saving…");
    h.dom.window.CBI18n.setLocale("zh-CN");assert.equal(h.$("pcStatus").textContent,"正在保存…");saving.resolve(response({detail:"原始服务器错误"},409));await save;
    h.dom.window.CBI18n.setLocale("en");assert.equal(h.$("pcStatus").textContent,"原始服务器错误");
    const open=h.app.registerWorkspace("C:/乙项目");await tick();assert.equal(h.$("pcStatus").textContent,"Opening project folder…");
    opening.resolve(response({workspace:{id:"w2"}}));await open;assert.equal(h.app.state.workspace,"w2");assert.match(h.$("pcPackage").href,/workspace=w2$/);assert.match(h.$("pcAgent").href,/workspace=w2$/);
  }finally{h.close();}
});

test("startup restoration cannot replace a manual open already in progress",async()=>{
  const list=deferred(),opening=deferred();let lists=0;
  const h=harness((url,body)=>url==="/api/agent/workspaces"?(body?opening.promise:++lists===1?list.promise:undefined):undefined);
  try{
    h.dom.window.localStorage.setItem("cb_project_workspace_v1","w1");const startup=h.app.start();const manual=h.app.registerWorkspace("C:/乙项目");
    list.resolve(response({workspaces:[{id:"w1",root:"C:/甲项目"},{id:"w2",root:"C:/乙项目"}]}));await startup;
    assert.equal(h.app.state.workspace,"");assert.equal(h.app.state.busy,true);assert.equal(h.$("pcStatus").textContent,"正在打开工程文件夹…");
    opening.resolve(response({workspace:{id:"w2"}}));await manual;assert.equal(h.app.state.workspace,"w2");assert.equal(h.app.state.busy,false);
  }finally{h.close();}
});

test("project source chooser uses the validated purpose-specific endpoint and offers IFC/DXF snapshots without selecting them",async()=>{
  const sources=["资料.md","模型/幕墙.ifc","图纸/节点.DXF"];
  const h=harness(url=>url.startsWith("/api/project-control/files?")?response(fileReport("w1",sources)):undefined);
  try{
    await h.start();assert.deepEqual([...h.$("pcSource").options].map(o=>o.value),["",...sources]);
    assert.equal(h.$("pcSource").value,"");assert.equal(h.$("pcSourceNotice").hidden,true);
    assert.equal(h.calls.some(c=>c.url.startsWith("/api/agent/files?")),false);
    for(const source of sources.slice(1)){
      h.$("pcSource").value=source;h.$("pcSource").dispatchEvent(new h.dom.window.Event("change"));
      h.dom.window.CBI18n.setLocale("en");assert.equal(h.$("pcSource").selectedOptions[0].textContent,source);
      assert.equal(h.$("pcSource").value,source);assert.match(h.$("pcSourceNotice").textContent,/source snapshots/);h.dom.window.CBI18n.setLocale("zh-CN");assert.match(h.$("pcSourceNotice").textContent,/登记文件快照/);
    }
    assert.equal(h.calls.some(c=>c.body),false);
    const scripts=[...h.dom.window.document.querySelectorAll("script[src]")].map(n=>n.getAttribute("src"));
    assert.ok(scripts.indexOf("/static/modules/file-listing.js")<scripts.indexOf("/static/project-control.js"));
    assert.equal(h.$("pcSource").getAttribute("aria-describedby"),"pcSourceNotice");
  }finally{h.close();}
});

test("partial listings preserve an explicitly selected omitted source and all unsaved revision and issue evidence",async()=>{
  let report=fileReport();
  const h=harness(url=>url.startsWith("/api/project-control/files?")?response(report):url==="/api/project-control/documents"?response({detail:"Preserve draft for inspection"},409):undefined);
  try{
    await h.start();h.$("pcDocumentId").value="doc1";h.$("pcDocumentId").dispatchEvent(new h.dom.window.Event("change"));h.app.editIssue(issue);
    h.$("pcTitle").value="我的草稿标题";h.$("pcDiscipline").value="我的用途";h.$("pcRevision").value="我的 Rev B";h.$("pcResolution").value="我的处理依据";
    const basis=structuredClone(h.app.state.issueEvidence);
    report=fileReport("w1",["材料.xlsx"],{complete:false,depth_limit_reached:true,incomplete_reasons:["depth_limit"]});h.setData(dataAt(2));await h.app.refresh();
    assert.equal(h.$("pcSource").value,"资料.md");assert.match(h.$("pcSource").selectedOptions[0].textContent,/保留已选来源/);assert.equal(h.$("pcSourceNotice").hidden,false);
    assert.equal(h.$("pcTitle").value,"我的草稿标题");assert.equal(h.$("pcDiscipline").value,"我的用途");assert.equal(h.$("pcRevision").value,"我的 Rev B");assert.equal(h.$("pcResolution").value,"我的处理依据");
    assert.deepEqual(h.app.state.issueEvidence,basis);assert.equal(h.app.state.documentRevision,1);
    h.dom.window.CBI18n.setLocale("en");assert.match(h.$("pcSource").selectedOptions[0].textContent,/selected source retained/);assert.match(h.$("pcSourceNotice").textContent,/资料\.md/);assert.match(h.$("pcSourceNotice").textContent,/incomplete|partial|limit/i);
    assert.equal(h.$("pcSource").value,"资料.md");assert.deepEqual(h.app.state.issueEvidence,basis);
    await h.app.saveDocument();const sent=h.calls.find(c=>c.url==="/api/project-control/documents").body;
    assert.equal(sent.path,"资料.md");assert.equal(sent.expected_revision,1);assert.equal(sent.title,"我的草稿标题");assert.equal(sent.revision_label,"我的 Rev B");
    h.dom.window.CBI18n.setLocale("zh-CN");assert.equal(h.$("pcSource").value,"资料.md");assert.match(h.$("pcSourceNotice").textContent,/资料\.md/);
  }finally{h.close();}
});

test("only a valid complete absence clears the source, gives bilingual guidance, and preserves revision draft",async()=>{
  let report=fileReport();const h=harness(url=>url.startsWith("/api/project-control/files?")?response({...report,workspace:new URL(url,"http://localhost").searchParams.get("workspace")}):undefined);
  try{
    await h.start();h.$("pcDocumentId").value="doc1";h.$("pcDocumentId").dispatchEvent(new h.dom.window.Event("change"));h.$("pcTitle").value="未保存的名称";h.$("pcRevision").value="未保存的修订";
    report=fileReport("w1",["材料.xlsx"]);await h.app.refresh();
    assert.equal(h.$("pcSource").value,"");assert.match(h.$("pcSourceNotice").textContent,/已取消选择/);assert.equal(h.$("pcTitle").value,"未保存的名称");assert.equal(h.$("pcRevision").value,"未保存的修订");assert.equal(h.app.state.documentRevision,1);
    h.dom.window.CBI18n.setLocale("en");assert.match(h.$("pcSourceNotice").textContent,/complete file list.*cleared/);assert.equal(h.$("pcSource").value,"");
    h.$("pcSource").value="材料.xlsx";h.$("pcSource").dispatchEvent(new h.dom.window.Event("change"));assert.equal(h.$("pcSourceNotice").hidden,true);
    await h.app.selectWorkspace("w2");assert.equal(h.$("pcSource").value,"");assert.equal(h.$("pcTitle").value,"");assert.equal(h.$("pcRevision").value,"");assert.equal(h.app.state.documentRevision,null);assert.equal(h.app.state.issueEvidence,null);assert.equal(h.app.state.fileFailure,false);assert.equal(h.app.state.fileReport.workspace,"w2");
  }finally{h.close();}
});

test("malformed or cross-purpose lists leave records, source, and every captured draft unchanged until retry succeeds",async t=>{
  const mutations=[
    ["missing files",()=>({})],
    ["wrong workspace",()=>fileReport("w2")],
    ["wrong purpose",()=>fileReport("w1",[],{purpose:"agent"})],
    ["count mismatch",()=>fileReport("w1",[],{returned_count:1})],
    ["invalid completeness",()=>fileReport("w1",[],{complete:false})],
    ["500 rows falsely claiming a complete list",()=>fileReport("w1",Array.from({length:500},(_,i)=>`资料-${i}.md`))],
    ["unsafe duplicate",()=>fileReport("w1",["资料.md","资料.md"])],
    ["invalid file shape",()=>fileReport("w1",[],{files:[{path:"资料.md"}],returned_count:1})],
  ];
  for(const [name,mutate] of mutations)await t.test(name,async()=>{
    let bad=false;const h=harness(url=>bad&&url.startsWith("/api/project-control/files?")?response(mutate()):undefined);
    try{
      await h.start();h.$("pcDocumentId").value="doc1";h.$("pcDocumentId").dispatchEvent(new h.dom.window.Event("change"));h.app.editIssue(issue);h.$("pcTitle").value="标题草稿";h.$("pcDiscipline").value="用途草稿";h.$("pcRevision").value="修订草稿";h.$("pcResolution").value="依据草稿";
      const records=h.app.state.data,files=h.app.state.files,report=h.app.state.fileReport,basis=structuredClone(h.app.state.issueEvidence);
      h.setData(dataAt(8));bad=true;await assert.rejects(h.app.refresh());
      assert.equal(h.app.state.data,records);assert.equal(h.app.state.files,files);assert.equal(h.app.state.fileReport,report);assert.equal(h.$("pcSource").value,"资料.md");assert.equal(h.$("pcTitle").value,"标题草稿");assert.equal(h.$("pcDiscipline").value,"用途草稿");assert.equal(h.$("pcRevision").value,"修订草稿");assert.equal(h.$("pcResolution").value,"依据草稿");assert.equal(h.app.state.documentRevision,1);assert.deepEqual(h.app.state.issueEvidence,basis);assert.equal(h.app.state.fileFailure,true);
      assert.equal(h.$("pcSourceNotice").hidden,false);h.dom.window.CBI18n.setLocale("en");assert.match(h.$("pcSourceNotice").textContent,/retry|refresh/i);assert.equal(h.$("pcSource").value,"资料.md");
      bad=false;await h.app.refresh();assert.equal(h.app.state.fileFailure,false);assert.equal(h.app.state.data.documents[0].revision,8);assert.equal(h.$("pcSourceNotice").hidden,true);assert.equal(h.app.state.documentRevision,1);assert.deepEqual(h.app.state.issueEvidence,basis);assert.equal(h.calls.some(c=>c.body),false);
    }finally{h.close();}
  });
});

test("late partial or malformed file responses from an old workspace cannot retain its source in the new workspace",async t=>{
  for(const malformed of [false,true])await t.test(malformed?"malformed old list":"partial old list",async()=>{
    const waiting=deferred();let delay=false;const h=harness(url=>delay&&url==="/api/project-control/files?workspace=w1"?waiting.promise:undefined);
    try{
      await h.start();h.$("pcSource").value="资料.md";delay=true;const old=h.app.refresh();await tick();await h.app.selectWorkspace("w2");
      waiting.resolve(response(malformed?{}:fileReport("w1",[],{complete:false,depth_limit_reached:true,incomplete_reasons:["depth_limit"]})));await old;
      assert.equal(h.app.state.workspace,"w2");assert.equal(h.app.state.fileReport.workspace,"w2");assert.equal(h.$("pcSource").value,"");assert.equal(h.$("pcSourceNotice").hidden,true);assert.equal(h.app.state.fileFailure,false);
    }finally{h.close();}
  });
});

test("the actual refresh button translates client list failures while preserving the selected source and draft",async()=>{
  let bad=false;const h=harness(url=>bad&&url.startsWith("/api/project-control/files?")?response({}):undefined);
  try{
    await h.start();h.$("pcSource").value="资料.md";h.$("pcTitle").value="保留草稿";h.dom.window.CBI18n.setLocale("en");bad=true;
    h.$("pcRefresh").click();await tick();assert.equal(h.app.state.busy,false);assert.match(h.$("pcStatus").textContent,/could not be validated/);assert.match(h.$("pcSourceNotice").textContent,/refresh again/);
    h.dom.window.CBI18n.setLocale("zh-CN");assert.match(h.$("pcStatus").textContent,/响应无法核对/);assert.match(h.$("pcSourceNotice").textContent,/重试刷新/);
    assert.equal(h.$("pcSource").value,"资料.md");assert.equal(h.$("pcTitle").value,"保留草稿");assert.equal(h.calls.some(c=>c.body),false);
  }finally{h.close();}
});
const packagePreview=(workspace="w1",overrides={})=>({schema_version:1,workspace,package_sha256:"b".repeat(64),document_count:1,issue_count:2,missing_evidence_count:1,source:{workspace:"c".repeat(32),exported_by:"李工 <script>bad()</script>",cutoff_at:"2026-10-15T10:00:00Z"},review_reset:true,...overrides});
const packageResult=(workspace="w1",overrides={})=>({schema_version:1,workspace,package_sha256:"b".repeat(64),import_id:"d".repeat(32),document_count:1,issue_count:2,missing_evidence_count:1,replayed:false,...overrides});
function choosePackage(h,name="交接资料.zip",size){const file=new h.dom.window.File(["PK example"],name,{type:"application/zip"});if(size!==undefined)Object.defineProperty(file,"size",{value:size});Object.defineProperty(h.$("pcImportFile"),"files",{value:[file],configurable:true});h.$("pcImportFile").dispatchEvent(new h.dom.window.Event("change"));return file;}
function authorizeImport(h){h.$("pcImportAllow").checked=true;h.$("pcImportAllow").dispatchEvent(new h.dom.window.Event("change"));}
const packageCalls=h=>h.calls.filter(c=>c.url.startsWith("/api/project-control/package/"));

test("handoff import sends browser FormData only after preview and fresh write authorization",async()=>{
  const h=harness(url=>url.endsWith("/package/preview")?response(packagePreview()):url.endsWith("/package/import")?response(packageResult()):undefined);
  try{
    await h.start();const before=h.calls.length;await h.app.importPackage();assert.equal(h.calls.length,before);assert.match(h.$("pcImportStatus").textContent,/先核验/);
    const file=choosePackage(h);assert.equal(h.app.state.packageFile,file);assert.equal(h.$("pcImportSubmit").disabled,true);assert.equal(packageCalls(h).length,0);
    await h.app.previewPackage();assert.equal(packageCalls(h).length,1);const checked=packageCalls(h)[0];assert.ok(checked.body instanceof h.dom.window.FormData);assert.deepEqual([...checked.body.keys()],["workspace","file"]);assert.equal(checked.body.get("workspace"),"w1");assert.equal(checked.body.get("file").name,"交接资料.zip");assert.equal(checked.init.headers,undefined);
    assert.equal(h.$("pcImportReceipt").querySelector("script"),null);assert.match(h.$("pcImportReceipt").textContent,/<script>bad\(\)<\/script>/);
    await h.app.importPackage();assert.equal(packageCalls(h).length,1);assert.match(h.$("pcImportStatus").textContent,/请勾选/);
    authorizeImport(h);assert.equal(h.$("pcImportSubmit").disabled,false);const reads=h.calls.filter(c=>c.url.startsWith("/api/project-control?")).length;await h.app.importPackage();
    const sent=packageCalls(h)[1];assert.deepEqual([...sent.body.keys()],["workspace","file","expected_sha256","allow_workspace_write"]);assert.equal(sent.body.get("expected_sha256"),"b".repeat(64));assert.equal(sent.body.get("allow_workspace_write"),"true");assert.equal(sent.init.headers,undefined);
    assert.equal(h.calls.filter(c=>c.url.startsWith("/api/project-control?")).length,reads+1);assert.equal(h.app.state.packagePreview,null);assert.equal(h.app.state.packageFile,null);assert.equal(h.$("pcImportAllow").checked,false);assert.equal(h.$("pcImportSubmit").disabled,true);assert.match(h.$("pcImportStatus").textContent,/内部复核已重置/);assert.equal(h.app.state.packageOutcome.import_id,"d".repeat(32));
    assert.equal(h.calls.some(c=>c.url.endsWith("/review")),false);
  }finally{h.close();}
});

test("handoff preview and import labels switch languages without changing the selected file, metadata or user drafts",async()=>{
  const h=harness(url=>url.endsWith("/package/preview")?response(packagePreview()):undefined);
  try{
    await h.start();h.$("pcTitle").value="保留的资料名称";h.$("pcDescription").value="用户问题描述";const file=choosePackage(h);await h.app.previewPackage();authorizeImport(h);const preview=h.app.state.packagePreview;
    h.dom.window.CBI18n.setLocale("en");assert.equal(h.$("pcImportPreview").textContent,"Verify selected package");assert.equal(h.$("pcImportSubmit").textContent,"Confirm import of copies");assert.match(h.$("pcImportStatus").textContent,/Verification complete/);assert.match(h.$("pcImportReceipt").textContent,/Documents: 1; issues: 2; issues missing historical evidence: 1/);assert.match(h.$("pcImportReceipt").textContent,/cccccccccccccccccccccccccccccccc/);assert.match(h.$("pcImportReceipt").textContent,/李工/);assert.match(h.$("pcImportReceipt").textContent,/2026-10-15T10:00:00Z/);
    assert.equal(h.app.state.packageFile,file);assert.equal(h.app.state.packagePreview,preview);assert.equal(h.$("pcImportAllow").checked,true);assert.equal(h.$("pcTitle").value,"保留的资料名称");assert.equal(h.$("pcDescription").value,"用户问题描述");h.dom.window.CBI18n.setLocale("zh-CN");assert.match(h.$("pcImportReceipt").textContent,/缺少旧证据的事项：1/);assert.equal(packageCalls(h).length,1);
  }finally{h.close();}
});

test("invalid package files and absent workspaces cannot trigger network preview or import",async t=>{
  for(const [name,size] of [["任意.txt",5],["空.zip",0],["过大.zip",128*1024*1024+1]])await t.test(name,async()=>{
    const h=harness();try{await h.start();choosePackage(h,name,size);await h.app.previewPackage();await h.app.importPackage();assert.equal(packageCalls(h).length,0);assert.equal(h.app.state.packageFile,null);assert.equal(h.app.state.packagePreview,null);assert.equal(h.$("pcImportSubmit").disabled,true);}finally{h.close();}
  });
  const h=harness();try{await h.app.start();choosePackage(h);await h.app.previewPackage();assert.equal(packageCalls(h).length,0);assert.match(h.$("pcImportStatus").textContent,/先选择工程文件夹/);}finally{h.close();}
});

test("malformed preview receipts never enable writing and retain the file for explicit re-verification",async t=>{
  for(const [name,value] of [["wrong workspace",packagePreview("w2")],["wrong schema",packagePreview("w1",{schema_version:2})],["missing hash",packagePreview("w1",{package_sha256:"x"})],["review not reset",packagePreview("w1",{review_reset:false})],["missing source",packagePreview("w1",{source:{}})],["negative count",packagePreview("w1",{issue_count:-1})],["impossible missing count",packagePreview("w1",{missing_evidence_count:3})],["too many documents",packagePreview("w1",{document_count:101})],["too many issues",packagePreview("w1",{issue_count:2001})],["invalid source workspace",packagePreview("w1",{source:{workspace:"not-a-workspace",exported_by:"actor",cutoff_at:"2026-10-15T10:00:00Z"}})]])await t.test(name,async()=>{
    const h=harness(url=>url.endsWith("/package/preview")?response(value):undefined);try{await h.start();const file=choosePackage(h);await h.app.previewPackage();authorizeImport(h);await h.app.importPackage();assert.equal(h.app.state.packagePreview,null);assert.equal(h.app.state.packageFile,file);assert.equal(packageCalls(h).length,1);assert.equal(h.$("pcImportSubmit").disabled,true);h.dom.window.CBI18n.setLocale("en");assert.match(h.$("pcImportStatus").textContent,/Verify the currently selected/);}finally{h.close();}
  });
});

test("file replacement, workspace changes, cancellation and page exit erase preview receipts and write permission",async t=>{
  for(const action of ["file","workspace","cancel","pagehide"])await t.test(action,async()=>{
    const h=harness(url=>url.endsWith("/package/preview")?response(packagePreview()):undefined);try{await h.start();choosePackage(h);await h.app.previewPackage();authorizeImport(h);if(action==="file")choosePackage(h,"第二份.zip");else if(action==="workspace")await h.app.selectWorkspace("w2");else if(action==="cancel")h.$("pcImportReset").click();else h.dom.window.dispatchEvent(new h.dom.window.Event("pagehide"));assert.equal(h.app.state.packagePreview,null);assert.equal(h.app.state.packageOutcome,null);assert.equal(h.$("pcImportAllow").checked,false);assert.equal(h.$("pcImportReceipt").hidden,true);assert.equal(h.$("pcImportSubmit").disabled,true);if(action!=="file")assert.equal(h.app.state.packageFile,null);assert.equal(packageCalls(h).length,1);assert.equal(h.dom.window.localStorage.getItem("cb_project_package_v1"),null);}finally{h.close();}
  });
});

test("late preview and import responses cannot restore a receipt or authorization for a newer file or workspace",async t=>{
  for(const operation of ["preview","import"])for(const change of ["file","workspace","cancel"])await t.test(`${operation} after ${change}`,async()=>{
    const pending=deferred();const h=harness(url=>url.endsWith(`/package/${operation}`)?pending.promise:url.endsWith("/package/preview")?response(packagePreview()):undefined);
    try{await h.start();choosePackage(h);let old;if(operation==="import"){await h.app.previewPackage();authorizeImport(h);old=h.app.importPackage();}else old=h.app.previewPackage();await tick();assert.equal(h.$("pcWorkspacePath").disabled,true);assert.equal(h.$("pcWorkspaceForm").querySelector("button[type=submit]").disabled,true);assert.equal(h.$("pcWorkspaceSelect").disabled,false);const request=packageCalls(h).at(-1);if(change==="file")choosePackage(h,"新的.zip");else if(change==="workspace")await h.app.selectWorkspace("w2");else h.app.resetPackage();assert.equal(request.init.signal.aborted,true);pending.resolve(response(operation==="preview"?packagePreview():packageResult()));await old;assert.equal(h.app.state.packagePreview,null);assert.equal(h.app.state.packageOutcome,null);assert.equal(h.app.state.packageBusy,false);assert.equal(h.$("pcImportAllow").checked,false);assert.equal(h.$("pcImportReceipt").hidden,true);if(operation==="import"){assert.equal(h.$("pcImportUnconfirmed").hidden,false);assert.match(h.$("pcImportUnconfirmed").textContent,/不会撤销服务器写入/);h.dom.window.CBI18n.setLocale("en");assert.match(h.$("pcImportUnconfirmed").textContent,/does not undo server writes/);}if(change==="file")assert.equal(h.app.state.packageFile.name,"新的.zip");if(change==="workspace")assert.equal(h.app.state.workspace,"w2");}finally{pending.resolve(response(packageResult()));h.close();}
  });
});

test("a disconnected import requires a fresh checkbox and explicit same-package retry, then recognizes replay",async()=>{
  let writes=0;const h=harness(url=>url.endsWith("/package/preview")?response(packagePreview()):url.endsWith("/package/import")?++writes===1?Promise.reject(new Error("连接已断开")):response(packageResult("w1",{replayed:true})):undefined);
  try{await h.start();const file=choosePackage(h);await h.app.previewPackage();authorizeImport(h);await h.app.importPackage();assert.equal(writes,1);assert.equal(h.app.state.packageFile,file);assert.ok(h.app.state.packagePreview);assert.equal(h.$("pcImportAllow").checked,false);assert.equal(h.$("pcImportSubmit").disabled,true);assert.match(h.$("pcImportStatus").textContent,/不会自动重发/);await tick();assert.equal(writes,1);await h.app.importPackage();assert.equal(writes,1);authorizeImport(h);await h.app.importPackage();assert.equal(writes,2);const sends=packageCalls(h).filter(c=>c.url.endsWith("/import"));assert.equal(sends[0].body.get("expected_sha256"),sends[1].body.get("expected_sha256"));assert.equal(sends[0].body.get("file").name,sends[1].body.get("file").name);assert.match(h.$("pcImportStatus").textContent,/先前已导入/);h.dom.window.CBI18n.setLocale("en");assert.match(h.$("pcImportStatus").textContent,/already imported/);assert.equal(h.$("pcImportAllow").checked,false);}finally{h.close();}
});

test("a timed-out import does not auto-resend or accept its late reply and can explicitly retry",async()=>{
  const pending=deferred();let writes=0;const h=harness(url=>url.endsWith("/package/preview")?response(packagePreview()):url.endsWith("/package/import")?++writes===1?pending.promise:response(packageResult("w1",{replayed:true})):undefined,{packageTimeoutMs:10});
  try{await h.start();choosePackage(h);await h.app.previewPackage();authorizeImport(h);await h.app.importPackage();assert.equal(writes,1);assert.equal(packageCalls(h).at(-1).init.signal.aborted,true);assert.match(h.$("pcImportStatus").textContent,/超时/);assert.ok(h.app.state.packagePreview);assert.equal(h.$("pcImportAllow").checked,false);pending.resolve(response(packageResult()));await tick();assert.equal(h.app.state.packageOutcome,null);assert.equal(writes,1);authorizeImport(h);await h.app.importPackage();assert.equal(writes,2);assert.equal(h.app.state.packageOutcome.replayed,true);}finally{pending.resolve(response(packageResult()));h.close();}
});

test("a confirmed import remains confirmed if ledger refresh fails and never silently rebinds an issue draft",async()=>{
  let imported=false;const h=harness(url=>url.endsWith("/package/preview")?response(packagePreview()):url.endsWith("/package/import")?(imported=true,response(packageResult())):imported&&url.startsWith("/api/project-control?")?response({detail:"台账暂时不可读"},503):undefined);
  try{await h.start();h.app.editIssue(issue);h.$("pcResolution").value="原证据草稿";const basis=structuredClone(h.app.state.issueEvidence);choosePackage(h);await h.app.previewPackage();authorizeImport(h);await h.app.importPackage();assert.equal(h.app.state.packageOutcome.import_id,"d".repeat(32));assert.equal(h.app.state.packagePreview,null);assert.equal(h.app.state.packageFile,null);assert.match(h.$("pcImportStatus").textContent,/导入已获服务器确认/);assert.match(h.$("pcImportStatus").textContent,/台账暂时不可读/);assert.equal(h.$("pcImportAllow").checked,false);assert.equal(h.$("pcResolution").value,"原证据草稿");assert.deepEqual(h.app.state.issueEvidence,basis);await h.app.importPackage();assert.equal(packageCalls(h).filter(c=>c.url.endsWith("/import")).length,1);}finally{h.close();}
});

test("imported registered hidden sources remain editable across complete listings while arbitrary hidden paths do not",async()=>{
  const controlled=".civil-buddy/out/handoffs/server_123/documents/方案.md";const importedDoc={...documentRecord,path:controlled};const h=harness(url=>url==="/api/project-control/documents"?response({detail:"Keep the revision draft"},409):undefined);
  try{await h.start();h.setData({documents:[importedDoc],issues:[],events:[],unresolved_issues:0});await h.app.refresh();assert.equal(h.$("pcSource").value,"");assert.equal([...h.$("pcSource").options].some(o=>o.value===controlled),false);h.$("pcDocumentId").value="doc1";h.$("pcDocumentId").dispatchEvent(new h.dom.window.Event("change"));assert.equal(h.$("pcSource").value,controlled);assert.match(h.$("pcSourceNotice").textContent,/台账登记的交接来源/);h.$("pcRevision").value="人工复核修订";await h.app.refresh();h.dom.window.CBI18n.setLocale("en");assert.equal(h.$("pcSource").value,controlled);assert.match(h.$("pcSource").selectedOptions[0].textContent,/registered handoff source/);assert.match(h.$("pcSourceNotice").textContent,/excluded from ordinary file listings/);await h.app.saveDocument();const saved=h.calls.find(c=>c.url==="/api/project-control/documents").body;assert.equal(saved.path,controlled);assert.equal(saved.expected_revision,1);assert.equal(saved.revision_label,"人工复核修订");
    const bogus=h.dom.window.document.createElement("option");bogus.value=".civil-buddy/out/handoffs/other/forged.md";h.$("pcSource").append(bogus);h.$("pcSource").value=bogus.value;await h.app.refresh();assert.equal(h.$("pcSource").value,"");assert.match(h.$("pcSourceNotice").textContent,/complete file list.*cleared/);assert.equal(h.$("pcRevision").value,"人工复核修订");
  }finally{h.close();}
});

test("issues missing older handoff evidence give explicit bilingual guidance without automatic rebasing",async()=>{
  const missing={...issue,document_revision:0,evidence_status:"unavailable"};const h=harness();
  try{await h.start();h.setData({documents:[documentRecord],issues:[missing],events:[{kind:"project_handoff_imported",at:"2026-10-15",actor_id:"李工",entity_id:"handoff_123"}],unresolved_issues:1});await h.app.refresh();assert.match(h.$("pcIssues").textContent,/未包含该旧修订原件/);assert.equal(h.app.state.data.issues[0].document_revision,0);assert.equal(h.app.state.issueEvidence,null);h.dom.window.CBI18n.setLocale("en");assert.match(h.$("pcIssues").textContent,/did not include that earlier revision file/);assert.match(h.$("pcEvents").textContent,/Import document handoff records/);h.app.editIssue(missing);assert.equal(h.app.state.issueEvidence.revision,1);assert.equal(h.app.state.edit.document_revision,0);assert.match(h.$("pcIssueEvidence").textContent,/Check the current document/);h.setData(dataAt(2));await h.app.refresh();assert.equal(h.app.state.issueEvidence.revision,1);h.dom.window.CBI18n.setLocale("zh-CN");assert.match(h.$("pcIssueEvidence").textContent,/未包含该旧修订原件/);assert.equal(h.calls.some(c=>c.body),false);}finally{h.close();}
});

test("an import response with a different hash, counts or invalid identity cannot be accepted as confirmed",async t=>{
  for(const [name,patch] of [["wrong hash",{package_sha256:"e".repeat(64)}],["different counts",{document_count:2}],["invalid import identity",{import_id:"not-an-import-id"}],["missing replay flag",{replayed:null}],["wrong workspace",{workspace:"w2"}]])await t.test(name,async()=>{
    const h=harness(url=>url.endsWith("/package/preview")?response(packagePreview()):url.endsWith("/package/import")?response(packageResult("w1",patch)):undefined);
    try{await h.start();const file=choosePackage(h);await h.app.previewPackage();authorizeImport(h);await h.app.importPackage();assert.equal(h.app.state.packageOutcome,null);assert.equal(h.app.state.packageFile,file);assert.ok(h.app.state.packagePreview);assert.equal(h.$("pcImportAllow").checked,false);assert.match(h.$("pcImportStatus").textContent,/响应无法核对/);assert.equal(h.$("pcImportUnconfirmed").hidden,false);assert.equal(packageCalls(h).length,2);}finally{h.close();}
  });
});

test("handoff audit events have product labels in both languages",async()=>{
  const h=harness();try{await h.start();h.setData({documents:[],issues:[],events:["document_handoff_imported","issue_handoff_imported","project_handoff_imported"].map(kind=>({kind,at:"2026-10-15",actor_id:"actor",entity_id:"d".repeat(32)})),unresolved_issues:0});await h.app.refresh();assert.match(h.$("pcEvents").textContent,/导入资料副本/);assert.match(h.$("pcEvents").textContent,/导入交接事项/);h.dom.window.CBI18n.setLocale("en");assert.match(h.$("pcEvents").textContent,/Import document copy/);assert.match(h.$("pcEvents").textContent,/Import handoff issue/);assert.doesNotMatch(h.$("pcEvents").textContent,/document_handoff_imported|issue_handoff_imported|project_handoff_imported/);}finally{h.close();}
});

test("handoff provenance shows original sources and missing evidence revisions as untranslated plain text in both languages",async()=>{
  const originalPath='图纸/<img src=x onerror="bad()">原件.DXF', sourceWorkspace="c".repeat(32), originalIssueId="e".repeat(32);
  const importedDoc={...documentRecord,path:".civil-buddy/out/handoffs/server_123/documents/copied.dxf",provenance:{source_path:originalPath,source_revision:7,source_exported_by:"original_exporter",source_workspace:sourceWorkspace,source_manifest:"DO_NOT_RENDER_MANIFEST",source_audit:"DO_NOT_RENDER_AUDIT"}};
  const missing={...issue,document_revision:0,evidence_status:"unavailable",provenance:{source_document_revision:7,source_actor_id:"original_author",source_issue_id:originalIssueId,source_workspace:sourceWorkspace,source_manifest:"DO_NOT_RENDER_MANIFEST"}};
  const alreadyMissing={...missing,id:"issue2",title:"上一包已缺证据",provenance:{...missing.provenance,source_document_revision:0}};
  const h=harness();
  try{
    await h.start();h.setData({documents:[importedDoc],issues:[missing,alreadyMissing],events:[],unresolved_issues:2});await h.app.refresh();
    const sourceRows=()=>[...h.$("pcDocuments").querySelectorAll(".pc-provenance p")].map(p=>p.lastChild.textContent);
    const unchanged=sourceRows();assert.deepEqual(unchanged,[" "+originalPath," 7"," original_exporter"," "+sourceWorkspace]);
    assert.match(h.$("pcDocuments").textContent,/原文件路径：/);assert.match(h.$("pcDocuments").textContent,/原修订： 7/);assert.match(h.$("pcIssues").textContent,/来源包记录的证据修订：7/);assert.match(h.$("pcIssues").textContent,/证据本就缺失（修订 0）/);
    h.app.editIssue(missing);assert.match(h.$("pcIssueEvidence").textContent,/来源包记录的证据修订：7/);h.$("pcDescription").value="接收人核对草稿";
    h.dom.window.CBI18n.setLocale("en");assert.deepEqual(sourceRows(),unchanged);assert.match(h.$("pcDocuments").textContent,/Original file path:/);assert.match(h.$("pcDocuments").textContent,/Source exporter: original_exporter/);assert.match(h.$("pcIssues").textContent,/Evidence revision recorded by the source package: 7/);assert.match(h.$("pcIssues").textContent,/already missing in the source package \(revision 0\)/);assert.match(h.$("pcIssues").textContent,/Original issue author: original_author/);assert.match(h.$("pcIssues").textContent,new RegExp(originalIssueId));assert.match(h.$("pcIssueEvidence").textContent,/source package: 7/);assert.equal(h.$("pcDescription").value,"接收人核对草稿");
    assert.equal(h.$("pcDocuments").querySelector("img,script"),null);assert.equal(h.$("pcIssues").querySelector("img,script"),null);assert.match(h.$("pcDocuments").textContent,/<img src=x onerror="bad\(\)">/);assert.doesNotMatch(h.$("pcDocuments").textContent,/DO_NOT_RENDER/);assert.doesNotMatch(h.$("pcIssues").textContent,/DO_NOT_RENDER/);
    h.app.editIssue(alreadyMissing);assert.match(h.$("pcIssueEvidence").textContent,/already missing in the source package \(revision 0\)/);h.dom.window.CBI18n.setLocale("zh-CN");assert.deepEqual(sourceRows(),unchanged);assert.match(h.$("pcIssueEvidence").textContent,/证据本就缺失（修订 0）/);assert.equal(h.calls.some(c=>c.body),false);
  }finally{h.close();}
});
