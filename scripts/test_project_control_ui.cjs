"use strict";
const {test}=require("node:test"), assert=require("node:assert/strict"),fs=require("node:fs"),path=require("node:path");
const {JSDOM}=require("jsdom");
const {createProjectControl}=require("../demo/static/project-control.js");
const html=fs.readFileSync(path.join(__dirname,"../demo/static/project-control.html"),"utf8");
const documentRecord={id:"doc1",title:"原始方案 <script>alert(1)</script>",path:"资料.md",discipline:"幕墙",revision:1,review_version:1,revision_label:"Rev A",sha256:"a".repeat(64),status:"work_in_progress",effective_status:"work_in_progress",source_status:"current"};
const issue={id:"issue1",version:4,document_id:"doc1",document_revision:1,kind:"rfi",title:"复核原文",description:"尺寸按原文件，不擅自翻译",assignee:"张工",due_date:"2026-10-15",status:"review_required",resolution:"原说明"};
const response=(v,status=200)=>new Response(JSON.stringify(v),{status,headers:{"Content-Type":"application/json"}});
function harness(override=()=>undefined){
  const dom=new JSDOM(html,{url:"http://localhost/static/project-control.html",runScripts:"outside-only"});let data={documents:[{...documentRecord}],issues:[{...issue}],events:[],unresolved_issues:1};const calls=[];
  for(const f of ["i18n.js","i18n-project.js"])dom.window.eval(fs.readFileSync(path.join(__dirname,"../demo/static",f),"utf8"));
  dom.window.document.dispatchEvent(new dom.window.Event("DOMContentLoaded"));
  const fetch=async(url,init)=>{const body=init?.body?JSON.parse(init.body):undefined;calls.push({url,body});const custom=await override(url,body);if(custom!==undefined)return custom;if(url==="/api/agent/workspaces")return response({workspaces:[{id:"w1",root:"C:/甲项目"},{id:"w2",root:"C:/乙项目"}]});if(url.startsWith("/api/agent/files?"))return response({files:[{path:"资料.md"},{path:"材料.xlsx"}]});if(url.startsWith("/api/project-control?"))return response(data);if(url.startsWith("/api/project-control/documents/doc1/revisions"))return response({revisions:[documentRecord]});throw new Error("Unexpected route "+url);};
  const app=createProjectControl({document:dom.window.document,window:dom.window,fetch});return{app,dom,calls,$:id=>dom.window.document.getElementById(id),setData:v=>{data=v;},async start(){await app.start();await app.selectWorkspace("w1");},close(){dom.window.close();}};
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
