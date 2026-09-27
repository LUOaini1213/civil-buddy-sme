//! Protocol/authorization tests use a scripted domain server and deterministic
//! echo worker. They do not claim numerical solver or CAD parser validation.
use axum::{routing::get, Json, Router};
use civil_workbench::{
    product::{
        engineering::{EngineeringHost, EngineeringKind, EngineeringSelection},
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

const CAD: &str = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
const FRAME: &str = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb";
struct Temp(PathBuf);
impl Temp {
    fn new() -> Self {
        let root = std::env::temp_dir().join(format!(
            "civil-engineering-selection-{}",
            uuid::Uuid::new_v4()
        ));
        std::fs::create_dir_all(&root).unwrap();
        Self(root)
    }
    fn workspace(&self) -> WorkspaceContext {
        WorkspaceContext::new(&self.0).unwrap()
    }
    fn worker(&self) -> WorkerHost {
        let directory = self.0.join("packing_assistant");
        std::fs::create_dir_all(&directory).unwrap();
        std::fs::write(
            directory.join("host_worker.py"),
            r#"
import json, os, pathlib, sys
request = json.loads(sys.stdin.readline())
root = pathlib.Path(request['workspace'])
(root / 'worker-called.json').write_text(json.dumps(request), encoding='utf-8')
payload = request['payload']
if request['operation'] == 'frame':
    # Native analyze_frame schema: kind is deliberately absent. These finite
    # fixture numbers exercise transport only, never claim solver correctness.
    curves = {key: [0.0, 0.0] for key in ('x_m', 'axial_N', 'shear_y_N', 'shear_z_N',
        'moment_y_Nm', 'moment_z_Nm', 'torque_Nm', 'dx_m', 'dy_m', 'dz_m')}
    curves['x_m'] = [0.0, 4.0]
    result = {'ok': True, 'schema_version': 1, 'analysis': 'linear_elastic_frame',
        'units': {'length': 'm', 'force': 'N', 'moment': 'N*m', 'stress': 'Pa', 'rotation': 'rad'},
        'source': payload['source'], 'title': 'Scripted frame transport fixture',
        'engine': {'name': 'PyniteFEA', 'version': 'test-fixture'}, 'model': payload,
        'combinations': [{'id': 'C1', 'factors': {'D': 1.0},
            'nodes': [{'id': 'N1', 'displacement_m': [0.0, 0.0, 0.0], 'rotation_rad': [0.0, 0.0, 0.0],
                'reaction_N': [0.0, 123.5, 0.0], 'reaction_Nm': [0.0, 0.0, 0.0]}],
            'members': [{'id': 'B1', 'i': 'N1', 'j': 'N2', 'length_m': 4.0,
                'local_axes_global': [[1,0,0], [0,1,0], [0,0,1]], 'curves': curves,
                'sampled_extrema': {key: {'min': min(values), 'max': max(values)}
                    for key, values in curves.items() if key != 'x_m'}}]}],
        'assumptions': ['Scripted protocol fixture; not a numerical solver test.']}
else:
    result = {'kind': request['operation'], 'received_payload': payload,
              'source_sha256': payload.get('document', {}).get('sha256')}
override = root / 'worker-result-override.json'
if override.is_file():
    result = json.loads(override.read_text(encoding='utf-8'))
(root / 'worker-result.json').write_text(json.dumps(result), encoding='utf-8')
print(json.dumps({'version':1, 'ok':True, 'call_id':request['call_id'],
 'result': result,
 'sandbox': {'enforces':{'write':True,'spawn':True}}}))
"#,
        )
        .unwrap();
        WorkerHost::detect(self.0.clone())
    }
}
impl Drop for Temp {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.0);
    }
}

fn cad_record() -> Value {
    json!({"ok":true,"project":{"id":CAD,"revision":1,"name":"Saved hollow section"},
        "document":{"sha256":"a".repeat(64),"filename":"section.dxf","entities":[{"id":"entity-1","layer":"SECTION","points":[[0,0],[12,0],[12,8],[0,8]]}],"dimensions":[{"id":"dim-1","measurement":12}]},
        "draft_config":{"mode":"section","unit":"mm","layers":{"SECTION":"section"},"confirmed_solid":true,
            "selection":{"include_ids":["entity-1"],"exclude_ids":[],"groups":[]},
            "dimension_bindings":[{"dimension_id":"dim-1","role":"section","parameter":"height_m","value_source":"measurement"}],
            "parameters":{"section":{"height_m":0.012}}},"confirmation_reset":true})
}
fn frame_record() -> Value {
    json!({"ok":true,"project":{"id":FRAME,"revision":3,"kind":"frame","name":"Saved frame"},"snapshot":{"kind":"frame",
        "inputs":{"schema_version":1,"units":"SI","source":"user","nodes":[{"id":"user-node"}],"members":[{"id":"user-member"}],
            "materials":[{"E_Pa":200000000000_u64}],"sections":[{"A_m2":0.01}],"load_cases":[{"id":"D"}],"combinations":[{"id":"C1","factors":{"D":1.0}}],
            "nodal_loads":[],"member_loads":[]},"result":{"kind":"frame"}}})
}

struct Domain {
    base: String,
    cad: Arc<Mutex<Value>>,
    frame: Arc<Mutex<Value>>,
    reads: Arc<AtomicUsize>,
    change_during_calculation: Arc<AtomicBool>,
    server: tokio::task::JoinHandle<()>,
}
impl Drop for Domain {
    fn drop(&mut self) {
        self.server.abort();
    }
}
impl Domain {
    async fn start() -> Self {
        let cad = Arc::new(Mutex::new(cad_record()));
        let frame = Arc::new(Mutex::new(frame_record()));
        let reads = Arc::new(AtomicUsize::new(0));
        let change = Arc::new(AtomicBool::new(false));
        let cad_copy = cad.clone();
        let frame_copy = frame.clone();
        let reads_copy = reads.clone();
        let change_copy = change.clone();
        let app = Router::new()
            .route("/api/cad/projects", get(|| async { Json(json!({"ok":true,"projects":[{"id":CAD,"revision":1,"name":"CAD"}]})) }))
            .route("/api/engineering/projects", get(|| async { Json(json!({"projects":[{"id":FRAME,"revision":3,"kind":"frame","name":"Frame"},{"id":"c".repeat(32),"revision":1,"kind":"ifc_diff"}]})) }))
            .route(&format!("/api/cad/projects/{CAD}"), get(move || {
                let record = cad_copy.clone(); let reads = reads_copy.clone(); let change = change_copy.clone();
                async move { let n = reads.fetch_add(1, Ordering::SeqCst); let mut value = record.lock().unwrap().clone(); if change.load(Ordering::SeqCst) && n >= 1 { value["project"]["revision"] = json!(2); } Json(value) }
            }))
            .route(&format!("/api/engineering/projects/{FRAME}"), get(move || { let record = frame_copy.clone(); async move { Json(record.lock().unwrap().clone()) } }));
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let base = format!("http://{}", listener.local_addr().unwrap());
        let server = tokio::spawn(async move {
            axum::serve(listener, app).await.unwrap();
        });
        Self {
            base,
            cad,
            frame,
            reads,
            change_during_calculation: change,
            server,
        }
    }
}

#[tokio::test]
async fn reopened_cad_requires_current_confirmation_and_preserves_source_geometry() {
    let domain = Domain::start().await;
    let repository = Temp::new();
    let job = Temp::new();
    let host = EngineeringHost::new(repository.worker(), &domain.base).unwrap();
    let cancel = CancellationToken::new();
    let listed = host.list_projects(&cancel).await.unwrap();
    assert_eq!(listed["projects"].as_array().unwrap().len(), 2);
    let inspected = host
        .inspect_project(EngineeringKind::CadSection, CAD, &cancel)
        .await
        .unwrap();
    assert_eq!(inspected.status, "confirmation_required");
    assert!(!inspected.selection.confirmed_solid);
    assert!(inspected.missing_inputs.is_empty());
    assert!(host
        .calculate(&job.workspace(), &inspected.selection, &cancel)
        .await
        .unwrap_err()
        .contains("用户确认"));
    assert!(!job.0.join("worker-called.json").exists());
    let mut authorized = inspected.selection;
    authorized.confirmed_solid = true;
    let result = host
        .calculate(&job.workspace(), &authorized, &cancel)
        .await
        .unwrap();
    assert_eq!(result["ok"], true);
    assert_eq!(result["provenance"]["revision"], 1);
    let sent: Value =
        serde_json::from_slice(&std::fs::read(job.0.join("worker-called.json")).unwrap()).unwrap();
    let persisted = domain.cad.lock().unwrap();
    assert_eq!(sent["payload"]["document"], persisted["document"]);
    assert_eq!(
        sent["payload"]["config"]["dimension_bindings"],
        persisted["draft_config"]["dimension_bindings"]
    );
    assert_eq!(
        sent["payload"]["config"]["selection"],
        persisted["draft_config"]["selection"]
    );
    assert_eq!(
        sent["payload"]["config"]["parameters"],
        persisted["draft_config"]["parameters"]
    );
    assert_eq!(result["provenance"]["source_project_modified"], false);
    let mut fabricated = serde_json::to_value(&authorized).unwrap();
    fabricated["vertices"] = json!([[1, 2, 3]]);
    assert!(serde_json::from_value::<EngineeringSelection>(fabricated).is_err());
}

#[tokio::test]
async fn stale_revision_or_changed_inputs_never_reach_the_solver() {
    let domain = Domain::start().await;
    let repository = Temp::new();
    let job = Temp::new();
    let host = EngineeringHost::new(repository.worker(), &domain.base).unwrap();
    let cancel = CancellationToken::new();
    let mut selected = host
        .inspect_project(EngineeringKind::CadSection, CAD, &cancel)
        .await
        .unwrap()
        .selection;
    selected.confirmed_solid = true;
    let mut stale = selected.clone();
    stale.revision = 2;
    assert!(host
        .calculate(&job.workspace(), &stale, &cancel)
        .await
        .unwrap_err()
        .contains("已变化"));
    let mut wrong_source = selected.clone();
    wrong_source.source_sha256 = Some("b".repeat(64));
    assert!(host
        .calculate(&job.workspace(), &wrong_source, &cancel)
        .await
        .is_err());
    domain.cad.lock().unwrap()["document"]["entities"][0]["points"][0][0] = json!(999);
    assert!(host
        .calculate(&job.workspace(), &selected, &cancel)
        .await
        .unwrap_err()
        .contains("已变化"));
    assert!(!job.0.join("worker-called.json").exists());
}

#[tokio::test]
async fn concurrent_project_change_prevents_publishing_old_calculation_as_current() {
    let domain = Domain::start().await;
    let repository = Temp::new();
    let job = Temp::new();
    let host = EngineeringHost::new(repository.worker(), &domain.base).unwrap();
    let cancel = CancellationToken::new();
    let mut selected = host
        .inspect_project(EngineeringKind::CadSection, CAD, &cancel)
        .await
        .unwrap()
        .selection;
    selected.confirmed_solid = true;
    domain.reads.store(0, Ordering::SeqCst);
    domain
        .change_during_calculation
        .store(true, Ordering::SeqCst);
    let result = host.calculate(&job.workspace(), &selected, &cancel).await;
    assert!(result.unwrap_err().contains("未发布旧输入"));
    assert!(
        job.0.join("worker-called.json").exists(),
        "this scenario must exercise a completed calculation"
    );
}

#[tokio::test]
async fn missing_section_units_or_frame_sources_are_visible_and_refuse_calculation() {
    let domain = Domain::start().await;
    let repository = Temp::new();
    let job = Temp::new();
    let host = EngineeringHost::new(repository.worker(), &domain.base).unwrap();
    let cancel = CancellationToken::new();
    domain.cad.lock().unwrap()["draft_config"]["unit"] = json!("");
    let mut inspect = host
        .inspect_project(EngineeringKind::CadSection, CAD, &cancel)
        .await
        .unwrap();
    inspect.selection.confirmed_solid = true;
    assert_eq!(inspect.status, "missing_inputs");
    assert!(inspect.missing_inputs.iter().any(|m| m.contains("单位")));
    assert!(host
        .calculate(&job.workspace(), &inspect.selection, &cancel)
        .await
        .unwrap_err()
        .contains("输入不完整"));
    domain.frame.lock().unwrap()["snapshot"]["inputs"]["materials"] = json!([]);
    let inspect = host
        .inspect_project(EngineeringKind::SavedFrame, FRAME, &cancel)
        .await
        .unwrap();
    assert_eq!(inspect.status, "missing_inputs");
    assert!(inspect
        .missing_inputs
        .iter()
        .any(|m| m.contains("materials")));
    assert!(host
        .calculate(&job.workspace(), &inspect.selection, &cancel)
        .await
        .is_err());
    assert!(!job.0.join("worker-called.json").exists());
}

#[tokio::test]
async fn saved_frame_uses_only_original_inputs_without_inferring_a_model_from_cad() {
    let domain = Domain::start().await;
    let repository = Temp::new();
    let job = Temp::new();
    let host = EngineeringHost::new(repository.worker(), &domain.base).unwrap();
    let cancel = CancellationToken::new();
    let inspected = host
        .inspect_project(EngineeringKind::SavedFrame, FRAME, &cancel)
        .await
        .unwrap();
    assert_eq!(inspected.status, "ready");
    assert!(!inspected.confirmation_required);
    assert_eq!(inspected.selection.source_sha256, None);
    let result = host
        .calculate(&job.workspace(), &inspected.selection, &cancel)
        .await
        .unwrap();
    assert_eq!(
        result["result"]["model"],
        domain.frame.lock().unwrap()["snapshot"]["inputs"]
    );
    assert_eq!(result["provenance"]["revision"], 3);
    assert_eq!(result["provenance"]["source_sha256"], Value::Null);
    let raw: Value =
        serde_json::from_slice(&std::fs::read(job.0.join("worker-result.json")).unwrap()).unwrap();
    assert!(
        raw.get("kind").is_none(),
        "native frame result must not invent kind"
    );
    let mut returned = result["result"].clone();
    assert_eq!(
        returned.as_object_mut().unwrap().remove("kind"),
        Some(json!("frame"))
    );
    assert_eq!(
        returned, raw,
        "host changed a solver value or native metadata"
    );
    assert!(host
        .inspect_project(EngineeringKind::CadSection, "../other", &cancel)
        .await
        .is_err());
}

#[tokio::test]
async fn frame_result_rejects_wrong_native_schema_and_malformed_combinations() {
    let domain = Domain::start().await;
    let repository = Temp::new();
    let job = Temp::new();
    let host = EngineeringHost::new(repository.worker(), &domain.base).unwrap();
    let cancel = CancellationToken::new();
    let selection = host
        .inspect_project(EngineeringKind::SavedFrame, FRAME, &cancel)
        .await
        .unwrap()
        .selection;
    host.calculate(&job.workspace(), &selection, &cancel)
        .await
        .unwrap();
    let valid: Value =
        serde_json::from_slice(&std::fs::read(job.0.join("worker-result.json")).unwrap()).unwrap();
    let invalids = [
        ("/ok", json!(false)),
        ("/schema_version", json!(2)),
        ("/analysis", json!("unrelated_analysis")),
        ("/engine/name", json!("unregistered_engine")),
        ("/engine/version", Value::Null),
        ("/combinations", json!({})),
        ("/combinations", json!([])),
        ("/combinations/0/id", Value::Null),
        ("/combinations/0/factors", json!({"D":"fabricated"})),
        ("/combinations/0/nodes", json!([])),
        ("/combinations/0/nodes/0/reaction_N", json!([1, 2])),
        ("/combinations/0/members", json!({})),
    ];
    for (pointer, value) in invalids {
        let mut invalid = valid.clone();
        *invalid.pointer_mut(pointer).unwrap() = value;
        std::fs::write(
            job.0.join("worker-result-override.json"),
            serde_json::to_vec(&invalid).unwrap(),
        )
        .unwrap();
        let result = host.calculate(&job.workspace(), &selection, &cancel).await;
        assert!(
            result.unwrap_err().contains("梁框架计算结果格式"),
            "accepted {pointer}"
        );
    }
    // The former echo mock must not conceal this bridge/solver contract again.
    std::fs::write(
        job.0.join("worker-result-override.json"),
        br#"{"kind":"frame"}"#,
    )
    .unwrap();
    assert!(host
        .calculate(&job.workspace(), &selection, &cancel)
        .await
        .unwrap_err()
        .contains("梁框架计算结果格式"));
}

#[tokio::test]
async fn domain_endpoint_and_cancellation_are_bounded_before_worker_dispatch() {
    let repository = Temp::new();
    for base in [
        "https://example.com",
        "http://localhost:8080",
        "http://127.0.0.1:8080/other",
        "http://user@127.0.0.1:8080",
        "http://127.0.0.1:8080/?x=1",
    ] {
        assert!(
            EngineeringHost::new(repository.worker(), base).is_err(),
            "accepted {base}"
        );
    }
    let domain = Domain::start().await;
    let host = EngineeringHost::new(repository.worker(), &domain.base).unwrap();
    let cancel = CancellationToken::new();
    cancel.cancel();
    assert!(host
        .inspect_project(EngineeringKind::CadSection, CAD, &cancel)
        .await
        .is_err());
    assert_eq!(domain.reads.load(Ordering::SeqCst), 0);
}
