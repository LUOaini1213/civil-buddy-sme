use civil_workbench::api::{app, AppState};
use civil_workbench::config::Paths;
use std::net::SocketAddr;

#[tokio::main]
async fn main() {
    civil_workbench::config::load_env();

    if std::env::var_os("CIVIL_INSTANCE_USER").is_some()
        && std::env::var_os("CIVIL_DOMAIN_URL").is_none()
    {
        eprintln!("Named instances require scripts/start_unified_workbench.py and its authenticated domain service");
        std::process::exit(1);
    }

    let paths = Paths::detect();
    // Validate identity and state ownership before starting any helper process.
    let product =
        civil_workbench::product::api::ProductState::open(paths.clone()).unwrap_or_else(|e| {
            eprintln!("product runtime startup failed: {e}");
            std::process::exit(1);
        });
    let port: u16 = std::env::var("CIVIL_PORT")
        .ok()
        .and_then(|s| s.parse().ok())
        .unwrap_or(8765);
    let addr = SocketAddr::from(([127, 0, 0, 1], port));
    eprintln!(
        "Civil Buddy workbench (Rust)  kb={}  http://{addr}",
        paths.kb_root.display()
    );
    let listener = tokio::net::TcpListener::bind(addr)
        .await
        .unwrap_or_else(|e| {
            eprintln!("bind {addr} failed: {e}");
            std::process::exit(1);
        });
    let mut state = AppState::live(paths.clone());
    let domain_base = std::env::var("CIVIL_DOMAIN_URL").ok();
    let engine = match &domain_base {
        Some(base) => civil_workbench::py_engine::PyEngine::attach(
            base,
            &std::env::var("CIVIL_DOMAIN_TOKEN").unwrap_or_default(),
        ),
        None => {
            tokio::task::spawn_blocking(move || civil_workbench::py_engine::PyEngine::start(&paths))
                .await
                .unwrap_or_else(|_| Err("Python tool startup worker failed".into()))
        }
    };
    match engine {
        Ok(engine) => {
            eprintln!("Python 工具引擎已接上：{}", engine.base());
            state.engine = Some(std::sync::Arc::new(engine));
        }
        Err(err) => {
            eprintln!("Python 工具引擎未启动（CAD / 施工计划 / 箱单 / 招标对照不可用）：{err}");
            if domain_base.is_some() {
                std::process::exit(1);
            }
        }
    }
    let auth = product.auth.clone();
    let mut routes = app(state).merge(civil_workbench::product::api::router(product));
    if std::env::var_os("CIVIL_DOMAIN_URL").is_some() {
        routes = routes.merge(civil_workbench::product::domains::router());
    }
    axum::serve(
        listener,
        civil_workbench::product::auth::protect(routes, auth),
    )
    .await
    .expect("server");
}
