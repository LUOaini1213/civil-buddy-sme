//! Source/transport boundaries only. The solver below is scripted, not a
//! numerical acceptance claim; actual fixed CPM checks live in Python tests.
use axum::{routing::get, Json, Router};
use civil_workbench::{
    product::{
        engineering::{EngineeringHost, EngineeringKind, EngineeringSelection},
        tools::sha256,
        worker::WorkerHost,
    },
    runtime_core::{CancellationToken, WorkspaceContext},
};
use serde_json::{json, Value};
use std::{
    path::PathBuf,
    sync::{
        atomic::{AtomicBool, AtomicUsize, Ordering},
        Arc, Mutex,
    },
};

const PLAN: &str = "dddddddddddddddddddddddddddddddd";
struct Temp(PathBuf);
impl Temp {
    fn new() -> Self {
        let path =
            std::env::temp_dir().join(format!("civil-plan-selection-{}", uuid::Uuid::new_v4()));
        std::fs::create_dir_all(&path).unwrap();
        Self(path)
    }
    fn worker(&self) -> WorkerHost {
        std::fs::create_dir_all(self.0.join("packing_assistant")).unwrap();
        std::fs::write(self.0.join("packing_assistant/host_worker.py"), r#"
import hashlib,json,pathlib,sys
r=json.loads(sys.stdin.readline()); p=r['payload']; root=pathlib.Path(r['workspace'])
(root/'worker-called.json').write_text(json.dumps(r),encoding='utf-8')
result={'kind':'planning','method':p['method'],'plan':p['plan'],'inputs_sha256':hashlib.sha256(json.dumps(p,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest(),
 'result':{'kind':'cpm','engine':{'name':'civil-buddy-cpm','version':'1'},'duration_workdays':2,'finish_date':'2026-09-22',
 'tasks':[{'id':'A','start':'2026-09-21','end':'2026-09-22','start_offset':0,'finish_offset':2}]}}
if (root/'wrong-result.json').is_file(): result['plan']['calendar']['holidays']=['2026-09-22']
print(json.dumps({'version':1,'ok':True,'call_id':r['call_id'],'result':result,'sandbox':{'enforces':{'write':True,'spawn':True}}}))
"#).unwrap();
        WorkerHost::detect(self.0.clone())
    }
    fn workspace(&self) -> WorkspaceContext {
        WorkspaceContext::new(&self.0).unwrap()
    }
}
impl Drop for Temp {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.0);
    }
}
fn record() -> Value {
    let plan = json!({"start_date":"2026-09-21","calendar":{"weekdays":[0,1,2,3,4],"holidays":[]},"tasks":[{"id":"A","name":"Synthetic A","duration":2,"progress":0.0000001,"parent_id":null,"dependencies":[],"resources":{},"actual_start":null,"actual_finish":null}],"resources":[]});
    let payload = json!({"plan":plan,"method":"cpm"});
    // Python writes 1e-07, Rust writes 1e-7. Hash the service's actual bytes.
    let canonical = serde_json::to_string(&payload)
        .unwrap()
        .replace("1e-7", "1e-07");
    json!({"project":{"id":PLAN,"revision":1,"schema":"civil-buddy.planning.v1","name":"Saved synthetic plan","method":"cpm","plan":plan,
        "result":{"kind":"cpm","engine":{"name":"civil-buddy-cpm","version":"1"},"duration_workdays":2,"finish_date":"2026-09-22", "tasks":[{"id":"A","start":"2026-09-21","end":"2026-09-22","start_offset":0,"finish_offset":2}]},
        "synthetic":true,"import_source":null,"agent_inputs_json":canonical,"agent_selection":{"kind":"saved_planning","project_id":PLAN,"revision":1,"source_sha256":null,"inputs_sha256":sha256(canonical.as_bytes()),"confirmed_solid":false}}})
}
struct Domain {
    base: String,
    record: Arc<Mutex<Value>>,
    reads: Arc<AtomicUsize>,
    change: Arc<AtomicBool>,
    server: tokio::task::JoinHandle<()>,
}
impl Domain {
    async fn start() -> Self {
        let record = Arc::new(Mutex::new(record()));
        let reads = Arc::new(AtomicUsize::new(0));
        let change = Arc::new(AtomicBool::new(false));
        let saved = record.clone();
        let count = reads.clone();
        let changing = change.clone();
        let app = Router::new()
            .route(
                "/api/cad/projects",
                get(|| async { Json(json!({"projects":[]})) }),
            )
            .route(
                "/api/engineering/projects",
                get(|| async { Json(json!({"projects":[]})) }),
            )
            .route(
                "/api/engineering/planning/projects",
                get(|| async {
                    Json(json!({"projects":[{"id":PLAN,"name":"Saved plan","revision":1}]}))
                }),
            )
            .route(
                &format!("/api/engineering/planning/projects/{PLAN}"),
                get(move || {
                    let saved = saved.clone();
                    let count = count.clone();
                    let changing = changing.clone();
                    async move {
                        let n = count.fetch_add(1, Ordering::SeqCst);
                        let mut result = saved.lock().unwrap().clone();
                        if changing.load(Ordering::SeqCst) && n > 0 {
                            result["project"]["revision"] = json!(2);
                            result["project"]["agent_selection"]["revision"] = json!(2);
                        }
                        Json(result)
                    }
                }),
            );
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let base = format!("http://{}", listener.local_addr().unwrap());
        let server = tokio::spawn(async move {
            axum::serve(listener, app).await.unwrap();
        });
        Self {
            base,
            record,
            reads,
            change,
            server,
        }
    }
}
impl Drop for Domain {
    fn drop(&mut self) {
        self.server.abort();
    }
}

#[tokio::test]
async fn saved_plan_inspection_preserves_calendar_and_resources_without_running_solver() {
    let domain = Domain::start().await;
    let repository = Temp::new();
    let job = Temp::new();
    let host = EngineeringHost::new(repository.worker(), &domain.base).unwrap();
    let cancel = CancellationToken::new();
    let listed = host.list_projects(&cancel).await.unwrap();
    assert_eq!(listed["projects"][0]["kind"], "saved_planning");
    let inspected = host
        .inspect_project(EngineeringKind::SavedPlanning, PLAN, &cancel)
        .await
        .unwrap();
    assert_eq!(inspected.status, "ready");
    assert_eq!(
        inspected.summary["plan"]["calendar"],
        domain.record.lock().unwrap()["project"]["plan"]["calendar"]
    );
    let result = host
        .inspect_selected_plan(&inspected.selection, &cancel)
        .await
        .unwrap();
    assert_eq!(
        result["provenance"]["result_origin"],
        "saved_record_not_recomputed"
    );
    assert_eq!(result["provenance"]["source_project_modified"], false);
    assert!(!job.0.join("worker-called.json").exists());
}

#[tokio::test]
async fn stale_revision_or_changed_calendar_is_denied_before_dispatch() {
    let domain = Domain::start().await;
    let repository = Temp::new();
    let job = Temp::new();
    let host = EngineeringHost::new(repository.worker(), &domain.base).unwrap();
    let cancel = CancellationToken::new();
    let mut selected = host
        .inspect_project(EngineeringKind::SavedPlanning, PLAN, &cancel)
        .await
        .unwrap()
        .selection;
    selected.revision = 2;
    assert!(host
        .calculate(&job.workspace(), &selected, &cancel)
        .await
        .is_err());
    selected.revision = 1;
    domain.record.lock().unwrap()["project"]["plan"]["calendar"]["holidays"] =
        json!(["2026-09-22"]);
    assert!(host
        .calculate(&job.workspace(), &selected, &cancel)
        .await
        .is_err());
    assert!(!job.0.join("worker-called.json").exists());
    let mut fabricated = serde_json::to_value(selected).unwrap();
    fabricated["duration"] = json!(999);
    assert!(serde_json::from_value::<EngineeringSelection>(fabricated).is_err());
}

#[tokio::test]
async fn fixed_result_binds_actual_inputs_and_rejects_changed_worker_plan() {
    let domain = Domain::start().await;
    let repository = Temp::new();
    let job = Temp::new();
    let host = EngineeringHost::new(repository.worker(), &domain.base).unwrap();
    let cancel = CancellationToken::new();
    let selected = host
        .inspect_project(EngineeringKind::SavedPlanning, PLAN, &cancel)
        .await
        .unwrap()
        .selection;
    let result = host
        .calculate(&job.workspace(), &selected, &cancel)
        .await
        .unwrap();
    assert_eq!(result["result"]["inputs_sha256"], selected.inputs_sha256);
    assert_eq!(
        result["provenance"]["result_origin"],
        "fixed_solver_recomputed"
    );
    std::fs::write(job.0.join("wrong-result.json"), "{}").unwrap();
    assert!(host
        .calculate(&job.workspace(), &selected, &cancel)
        .await
        .unwrap_err()
        .contains("授权"));
}

#[tokio::test]
async fn concurrent_save_and_cancellation_prevent_publication() {
    let domain = Domain::start().await;
    let repository = Temp::new();
    let job = Temp::new();
    let host = EngineeringHost::new(repository.worker(), &domain.base).unwrap();
    let cancel = CancellationToken::new();
    let selected = host
        .inspect_project(EngineeringKind::SavedPlanning, PLAN, &cancel)
        .await
        .unwrap()
        .selection;
    domain.reads.store(0, Ordering::SeqCst);
    domain.change.store(true, Ordering::SeqCst);
    assert!(host
        .calculate(&job.workspace(), &selected, &cancel)
        .await
        .unwrap_err()
        .contains("已变化"));
    domain.change.store(false, Ordering::SeqCst);
    domain.reads.store(0, Ordering::SeqCst);
    cancel.cancel();
    assert!(host
        .inspect_selected_plan(&selected, &cancel)
        .await
        .is_err());
    assert_eq!(domain.reads.load(Ordering::SeqCst), 0);
}

#[tokio::test]
async fn high_precision_saved_progress_reaches_actual_confined_cpm_without_hash_or_value_drift() {
    let domain = Domain::start().await;
    let job = Temp::new();
    let repo = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .unwrap()
        .to_path_buf();
    let host = EngineeringHost::new(WorkerHost::detect(repo), &domain.base).unwrap();
    let cancel = CancellationToken::new();
    for original in ["99.99999999999999", "61.452274546212884"] {
        let progress = original.parse::<f64>().unwrap();
        let mut current = record();
        current["project"]["plan"]["tasks"][0]["progress"] = json!(progress);
        let payload = json!({"plan":current["project"]["plan"],"method":"cpm"});
        let canonical = serde_json::to_string(&payload).unwrap();
        assert!(canonical.contains(original));
        let hash = sha256(canonical.as_bytes());
        current["project"]["agent_inputs_json"] = json!(canonical);
        current["project"]["agent_selection"]["inputs_sha256"] = json!(hash);
        *domain.record.lock().unwrap() = current;
        let inspection = host
            .inspect_project(EngineeringKind::SavedPlanning, PLAN, &cancel)
            .await
            .unwrap();
        assert_eq!(
            inspection.summary["plan"]["tasks"][0]["progress"].as_f64(),
            Some(progress),
            "the first domain JSON parse changed {original}"
        );
        let response = host
            .calculate(&job.workspace(), &inspection.selection, &cancel)
            .await
            .unwrap();
        assert_eq!(response["ok"], true, "{response}");
        assert_eq!(response["result"]["inputs_sha256"], hash);
        assert_eq!(
            response["result"]["plan"]["tasks"][0]["progress"].as_f64(),
            Some(progress)
        );
        assert_eq!(
            response["result"]["result"]["engine"]["name"],
            "civil-buddy-cpm"
        );
        assert_eq!(response["result"]["result"]["duration_workdays"], 2);
        assert_eq!(response["sandbox"]["enforces"]["write"], true);
        assert_eq!(response["sandbox"]["enforces"]["spawn"], true);
        assert_eq!(response["sandbox"]["selftest"]["spawn_process"], "denied");
        assert_eq!(
            domain.record.lock().unwrap()["project"]["plan"]["tasks"][0]["progress"].as_f64(),
            Some(progress)
        );
    }
}
