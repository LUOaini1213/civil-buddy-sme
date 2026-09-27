//! One named user per host process. This is not a multi-tenant identity service.
//! Apply `protect` to the fully merged router, including legacy and domain routes.
//! Users need independent, non-nested physical workspaces. Ownership records are
//! configuration guards, not OS account isolation: an OS user who can change the
//! directories/records or concurrently reconfigure instances remains trusted.
use axum::{
    extract::{DefaultBodyLimit, Request, State},
    http::{header, HeaderMap, StatusCode},
    middleware::{self, Next},
    response::{Html, IntoResponse, Redirect, Response},
    routing::{get, post},
    Form, Json, Router,
};
use rusqlite::{params, Connection, OptionalExtension};
use serde::Deserialize;
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::{
    collections::HashMap,
    path::{Path, PathBuf},
    sync::{Arc, Mutex},
    time::{Duration, Instant},
};

const SESSION_SECONDS: u64 = 8 * 60 * 60;

pub struct InstanceAuth {
    owner: String,
    digest: Option<[u8; 32]>,
    roots: Vec<PathBuf>,
    public_origin: Option<String>,
    cookie_name: String,
    sessions: Mutex<HashMap<String, Instant>>,
}

impl InstanceAuth {
    pub fn from_env() -> Result<Arc<Self>, String> {
        let owner = std::env::var("CIVIL_INSTANCE_USER").ok();
        let digest = std::env::var("CIVIL_TOKEN_SHA256").ok().or_else(|| {
            std::env::var("CIVIL_TOKEN")
                .ok()
                .filter(|v| !v.is_empty())
                .map(|v| format!("{:x}", Sha256::digest(v.as_bytes())))
        });
        let roots = std::env::var("CIVIL_ALLOWED_WORKSPACES").ok();
        let origin = std::env::var("CIVIL_PUBLIC_ORIGIN").ok();
        if owner.is_none() && digest.is_none() && roots.is_none() && origin.is_none() {
            return Ok(Self::local());
        }
        let roots: Vec<PathBuf> = serde_json::from_str(&roots.ok_or(
            "CIVIL_ALLOWED_WORKSPACES must be a JSON array of exact absolute directories",
        )?)
        .map_err(|_| {
            "CIVIL_ALLOWED_WORKSPACES must be a JSON array of exact absolute directories"
        })?;
        Self::named(
            &owner.ok_or("CIVIL_INSTANCE_USER required")?,
            &digest.ok_or("CIVIL_TOKEN_SHA256 or CIVIL_TOKEN required")?,
            roots,
            origin,
        )
    }

    /// Compatibility mode for an unconfigured loopback-only personal host.
    pub fn local() -> Arc<Self> {
        Arc::new(Self {
            owner: "local-owner".into(),
            digest: None,
            roots: Vec::new(),
            public_origin: None,
            cookie_name: "civil_local_session".into(),
            sessions: Mutex::new(HashMap::new()),
        })
    }

    pub fn named(
        owner: &str,
        digest: &str,
        roots: Vec<PathBuf>,
        public_origin: Option<String>,
    ) -> Result<Arc<Self>, String> {
        if owner.is_empty()
            || owner.len() > 64
            || !owner
                .bytes()
                .all(|b| b.is_ascii_alphanumeric() || b"_-".contains(&b))
        {
            return Err("instance user must be 1-64 ASCII letters, digits, '-' or '_'".into());
        }
        if digest.len() != 64 || !digest.bytes().all(|b| b.is_ascii_hexdigit()) {
            return Err("token digest must be a SHA-256 hex value".into());
        }
        let mut decoded = [0u8; 32];
        for (i, byte) in decoded.iter_mut().enumerate() {
            *byte = u8::from_str_radix(&digest[i * 2..i * 2 + 2], 16)
                .map_err(|_| "invalid token digest")?;
        }
        if roots.len() != 1 {
            return Err("one exact workspace is required per named instance; use a separate state directory and domain worker for each project".into());
        }
        let mut allowed = Vec::new();
        for root in roots {
            if !root.is_absolute() {
                return Err("allowed workspaces must be absolute".into());
            }
            reject_links(&root)?;
            let root = root
                .canonicalize()
                .map_err(|_| "allowed workspace does not exist")?;
            if !root.is_dir() {
                return Err("allowed workspace must be a directory".into());
            }
            if allowed
                .iter()
                .any(|old: &PathBuf| root.starts_with(old) || old.starts_with(&root))
            {
                return Err("allowed workspace directories must not overlap".into());
            }
            allowed.push(root);
        }
        let public_origin = public_origin
            .map(|origin| {
                let url = reqwest::Url::parse(&origin).map_err(|_| "invalid public origin")?;
                if url.scheme() != "https"
                    || !url.username().is_empty()
                    || url.password().is_some()
                    || url.query().is_some()
                    || url.fragment().is_some()
                    || url.path() != "/"
                {
                    return Err(
                        "public origin must be an HTTPS origin without credentials, query or path",
                    );
                }
                Ok(url.origin().ascii_serialization())
            })
            .transpose()?;
        let cookie_name = format!(
            "civil_session_{}",
            &format!(
                "{:x}",
                Sha256::digest(format!("{owner}:{digest}:{}", allowed[0].to_string_lossy()).as_bytes())
            )[..16]
        );
        Ok(Arc::new(Self {
            owner: owner.into(),
            digest: Some(decoded),
            roots: allowed,
            public_origin,
            cookie_name,
            sessions: Mutex::new(HashMap::new()),
        }))
    }

    pub fn owner(&self) -> &str {
        &self.owner
    }
    pub fn requires_token(&self) -> bool {
        self.digest.is_some()
    }
    pub fn capabilities(&self) -> Value {
        json!({"user_id":self.owner,"mode":if self.requires_token(){"named_single_user_instance"}else{"local_single_user"},
            "authentication_required":self.requires_token(),"multi_tenant":false,"workspace_allowlist_enforced":self.requires_token(),
            "domain_isolation":"dedicated_instance_required","approval_scope":"current_turn"})
    }

    /// Called while holding the runtime-owner process lock, before crash recovery.
    pub fn claim_state(&self, folder: &Path) -> Result<(), String> {
        if let Some(root) = self.roots.first() {
            reject_links(folder)?;
            let state = folder.canonicalize().map_err(|_| "state directory is not accessible")?;
            if root.starts_with(&state) || state.starts_with(root) {
                return Err("state storage and workspace must be separate non-nested directories".into());
            }
            reject_owned_overlap(&state, true)?;
            reject_owned_overlap(root, false)?;
        }
        let mut db = Connection::open(folder.join("identity.sqlite")).map_err(|e| e.to_string())?;
        db.execute_batch("CREATE TABLE IF NOT EXISTS instance_owner(id INTEGER PRIMARY KEY CHECK(id=1),user_id TEXT NOT NULL,workspace_root TEXT NOT NULL)").map_err(|e| e.to_string())?;
        let tx = db.transaction().map_err(|e| e.to_string())?;
        let root = self
            .roots
            .first()
            .map(|p| p.to_string_lossy().into_owned())
            .unwrap_or_default();
        let owner: Option<(String, String)> = tx
            .query_row(
                "SELECT user_id,workspace_root FROM instance_owner WHERE id=1",
                [],
                |r| Ok((r.get(0)?, r.get(1)?)),
            )
            .optional()
            .map_err(|e| e.to_string())?;
        match owner {
            Some((owner, workspace)) if owner != self.owner || workspace != root => return Err("state directory belongs to a different user or workspace; use a separate state directory".into()),
            Some(_) => {},
            None => { tx.execute("INSERT INTO instance_owner(id,user_id,workspace_root) VALUES(1,?1,?2)", params![self.owner,root]).map_err(|e| e.to_string())?; }
        }
        tx.commit().map_err(|e| e.to_string())?;
        // Source workspaces also contain .civil-buddy/out. Separate state roots
        // alone would not isolate those outputs if users shared a source root.
        if let Some(root) = self.roots.first() {
            self.authorize_workspace(root)?;
            let metadata = root.join(".civil-buddy");
            if std::fs::symlink_metadata(&metadata).is_ok() {
                reject_links(&metadata)?;
            }
            std::fs::create_dir_all(&metadata).map_err(|e| e.to_string())?;
            let binding = metadata.join("instance-owner.sqlite");
            if std::fs::symlink_metadata(&binding).is_ok() {
                reject_links(&binding)?;
            }
            let mut db = Connection::open(binding).map_err(|e| e.to_string())?;
            db.busy_timeout(Duration::from_secs(5))
                .map_err(|e| e.to_string())?;
            db.execute_batch("CREATE TABLE IF NOT EXISTS workspace_owner(id INTEGER PRIMARY KEY CHECK(id=1),user_id TEXT NOT NULL,state_root TEXT NOT NULL)").map_err(|e| e.to_string())?;
            let state = folder
                .canonicalize()
                .map_err(|e| e.to_string())?
                .to_string_lossy()
                .into_owned();
            let tx = db
                .transaction_with_behavior(rusqlite::TransactionBehavior::Immediate)
                .map_err(|e| e.to_string())?;
            let existing: Option<(String, String)> = tx
                .query_row(
                    "SELECT user_id,state_root FROM workspace_owner WHERE id=1",
                    [],
                    |r| Ok((r.get(0)?, r.get(1)?)),
                )
                .optional()
                .map_err(|e| e.to_string())?;
            match existing {
                Some((owner, old_state)) if owner != self.owner || old_state != state => return Err("workspace already belongs to another user or state directory; each user needs an independent workspace copy".into()),
                Some(_) => {},
                None => { tx.execute("INSERT INTO workspace_owner(id,user_id,state_root) VALUES(1,?1,?2)",params![self.owner,state]).map_err(|e| e.to_string())?; }
            }
            tx.commit().map_err(|e| e.to_string())?;
        }
        Ok(())
    }

    pub fn authorize_workspace(&self, path: &Path) -> Result<(), String> {
        if !self.requires_token() {
            return Ok(());
        }
        reject_links(path)?;
        let path = path
            .canonicalize()
            .map_err(|_| "workspace is not allowed")?;
        if self.roots.iter().any(|root| root == &path) {
            Ok(())
        } else {
            Err("workspace is not allowed for this instance user".into())
        }
    }

    /// Legacy deterministic tools share this boundary with the unified host;
    /// selecting a path in chat does not grant access to another user's files.
    pub fn authorize_local_path(&self, path: &Path) -> Result<(), String> {
        if !self.requires_token() { return Ok(()); }
        reject_links(path)?;
        let canonical = path.canonicalize().map_err(|_| "local path is not accessible")?;
        let root = self.roots.iter().find(|root| canonical.starts_with(root))
            .ok_or("local path is outside this instance's allowed workspace")?;
        if canonical.is_file() {
            crate::runtime_core::WorkspaceContext::new(root).map_err(|e| e.to_string())?
                .resolve_read(canonical.strip_prefix(root).map_err(|e| e.to_string())?).map_err(|e| e.to_string())?;
        }
        Ok(())
    }

    fn valid_token(&self, token: &str) -> bool {
        let Some(expected) = self.digest else {
            return false;
        };
        let actual = Sha256::digest(token.as_bytes());
        let mut difference = 0u8;
        for (a, b) in expected.iter().zip(actual.iter()) {
            difference |= a ^ b;
        }
        difference == 0 && !token.is_empty()
    }
    fn cookie_value<'a>(&self, headers: &'a HeaderMap) -> Option<&'a str> {
        headers
            .get(header::COOKIE)?
            .to_str()
            .ok()?
            .split(';')
            .find_map(|part| {
                let (name, value) = part.trim().split_once('=')?;
                (name == self.cookie_name).then_some(value)
            })
    }
    fn authenticated(&self, headers: &HeaderMap) -> bool {
        if !self.requires_token() {
            return true;
        }
        if let Some(value) = headers.get(header::AUTHORIZATION) {
            return value
                .to_str()
                .ok()
                .and_then(|v| v.strip_prefix("Bearer "))
                .is_some_and(|token| self.valid_token(token));
        }
        let Some(cookie) = self.cookie_value(headers) else {
            return false;
        };
        self.sessions
            .lock()
            .ok()
            .and_then(|sessions| sessions.get(cookie).copied())
            .is_some_and(|expires| expires > Instant::now())
    }
}

fn reject_owned_overlap(root: &Path, state: bool) -> Result<(), String> {
    const BINDING: &str = ".civil-buddy/instance-owner.sqlite";
    // identity.sqlite is reserved for instance state. Treat its presence as
    // private state even if damaged; never open it as selected project material.
    let exists = |path: PathBuf| path.try_exists().map_err(|_| "cannot verify directory ownership");
    if state && exists(root.join(BINDING))? {
        return Err("state storage cannot be an owned workspace".into());
    }
    if !state && exists(root.join("identity.sqlite"))? {
        return Err("workspace cannot be an instance state directory".into());
    }
    for parent in root.ancestors().skip(1) {
        if exists(parent.join(BINDING))? {
            return Err("workspace is nested inside an owned workspace; use independent non-overlapping directories".into());
        }
        if exists(parent.join("identity.sqlite"))? {
            return Err("directory is nested inside private instance state".into());
        }
    }
    let mut pending = vec![root.to_path_buf()];
    let mut visited = 0usize;
    while let Some(directory) = pending.pop() {
        if directory != root && directory.join(BINDING).try_exists().map_err(|_| "cannot verify nested workspace ownership")? {
            return Err("workspace contains an owned workspace; use independent non-overlapping directories".into());
        }
        if directory != root && exists(directory.join("identity.sqlite"))? {
            return Err("directory contains private instance state; choose independent non-overlapping directories".into());
        }
        for entry in std::fs::read_dir(&directory).map_err(|_| "cannot verify all nested workspace directories")? {
            visited += 1;
            if visited > 100_000 {
                return Err("workspace ownership scan exceeds 100000 entries; choose a smaller dedicated project directory".into());
            }
            let entry = entry.map_err(|_| "cannot verify nested workspace entry")?;
            let metadata = std::fs::symlink_metadata(entry.path()).map_err(|_| "cannot verify nested workspace entry")?;
            let mut linked = metadata.file_type().is_symlink();
            #[cfg(windows)] {
                use std::os::windows::fs::MetadataExt;
                linked |= metadata.file_attributes() & 0x400 != 0;
            }
            // Linked paths are already inaccessible to the workspace resolver.
            if metadata.is_dir() && !linked { pending.push(entry.path()); }
        }
    }
    Ok(())
}

fn reject_links(path: &Path) -> Result<(), String> {
    for ancestor in path.ancestors() {
        let meta =
            std::fs::symlink_metadata(ancestor).map_err(|_| "workspace path is not accessible")?;
        let mut linked = meta.file_type().is_symlink();
        #[cfg(windows)]
        {
            use std::os::windows::fs::MetadataExt;
            linked |= meta.file_attributes() & 0x400 != 0;
        }
        if linked {
            return Err("workspace links and reparse points are not allowed".into());
        }
    }
    Ok(())
}

fn denied(status: StatusCode, message: &str) -> Response {
    let mut response = (
        status,
        [(header::CACHE_CONTROL, "no-store")],
        Json(json!({"detail":message})),
    )
        .into_response();
    if status == StatusCode::UNAUTHORIZED {
        response
            .headers_mut()
            .insert("x-civil-login", "/auth/login".parse().unwrap());
    }
    response
}

fn origin_allowed(auth: &InstanceAuth, request: &Request) -> bool {
    let headers = request.headers();
    if headers
        .get("sec-fetch-site")
        .is_some_and(|v| v == "cross-site")
    {
        return false;
    }
    let Some(host) = headers.get(header::HOST).and_then(|h| h.to_str().ok()) else {
        return false;
    };
    let Ok(local) = reqwest::Url::parse(&format!("http://{host}")) else {
        return false;
    };
    if local.path() != "/"
        || local.query().is_some()
        || local.fragment().is_some()
        || !local.username().is_empty()
        || local.password().is_some()
    {
        return false;
    }
    let local_host = matches!(local.host_str(), Some("localhost" | "127.0.0.1" | "[::1]"));
    let public_host = auth.public_origin.as_ref().is_some_and(|origin| {
        reqwest::Url::parse(origin).ok().is_some_and(|u| {
            u.host_str() == local.host_str()
                && u.port().unwrap_or(443) == local.port().unwrap_or(443)
        })
    });
    if !local_host && !public_host {
        return false;
    }
    if !auth.requires_token()
        && headers
            .keys()
            .any(|key| key.as_str() == "forwarded" || key.as_str().starts_with("x-forwarded-"))
    {
        return false;
    }
    if let Some(origin) = headers.get(header::ORIGIN) {
        let Some(origin) = origin
            .to_str()
            .ok()
            .and_then(|v| reqwest::Url::parse(v).ok())
        else {
            return false;
        };
        let expected = if public_host {
            auth.public_origin.clone().unwrap()
        } else {
            local.origin().ascii_serialization()
        };
        if origin.origin().ascii_serialization() != expected
            || origin.as_str().trim_end_matches('/') != expected
        {
            return false;
        }
    }
    true
}

pub fn protect(router: Router, auth: Arc<InstanceAuth>) -> Router {
    let login = Router::new()
        .route("/auth/login", get(login_page).post(login))
        .route("/auth/logout", post(logout))
        .layer(DefaultBodyLimit::max(16 * 1024))
        .with_state(auth.clone());
    router
        .merge(login)
        .layer(middleware::from_fn_with_state(auth, gate))
}

async fn gate(State(auth): State<Arc<InstanceAuth>>, mut request: Request, next: Next) -> Response {
    if !origin_allowed(&auth, &request) {
        return denied(
            StatusCode::FORBIDDEN,
            "request host or origin is not allowed",
        );
    }
    let token_query = reqwest::Url::parse(&format!("http://localhost{}", request.uri()))
        .ok()
        .is_some_and(|url| {
            url.query_pairs()
                .any(|(key, _)| key.eq_ignore_ascii_case("token"))
        });
    if token_query {
        return denied(
            StatusCode::BAD_REQUEST,
            "credentials in URLs are not accepted; use the login form or Bearer header",
        );
    }
    let login = request.uri().path() == "/auth/login";
    if !login && !auth.authenticated(request.headers()) {
        return if request.uri().path().starts_with("/api/") {
            denied(StatusCode::UNAUTHORIZED, "authentication required")
        } else {
            Redirect::to("/auth/login").into_response()
        };
    }
    let path = request.uri().path();
    if auth.requires_token()
        && (matches!(path, "/api/local" | "/api/upload-url" | "/api/studio")
            || path.starts_with("/api/studio/"))
    {
        return denied(StatusCode::FORBIDDEN, "this legacy endpoint is unavailable in a named instance; use its explicitly allowed workspace");
    }
    let named = auth.requires_token();
    request.extensions_mut().insert(auth);
    let mut response = next.run(request).await;
    response
        .headers_mut()
        .insert(header::CACHE_CONTROL, "no-store".parse().unwrap());
    response
        .headers_mut()
        .insert(header::REFERRER_POLICY, "no-referrer".parse().unwrap());
    response.headers_mut().insert(
        "x-civil-identity-mode",
        if named {
            "named_single_user_instance"
        } else {
            "local_single_user"
        }
        .parse()
        .unwrap(),
    );
    response
}

async fn login_page() -> Html<&'static str> {
    Html("<!doctype html><html lang=\"zh-CN\"><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width\"><title>Civil Buddy 登录</title><body><h1>Civil Buddy</h1><p>每位成员使用独立实例与资料目录。请输入管理员提供的访问令牌。</p><form action=\"/auth/login\" method=\"post\"><label>访问令牌 <input type=\"password\" name=\"token\" required autocomplete=\"current-password\"></label><button type=\"submit\">登录</button></form></body></html>")
}
#[derive(Deserialize)]
struct Login {
    token: String,
}
async fn login(State(auth): State<Arc<InstanceAuth>>, Form(form): Form<Login>) -> Response {
    if !auth.valid_token(&form.token) {
        return denied(StatusCode::UNAUTHORIZED, "invalid access token");
    }
    let session = format!(
        "{}{}",
        uuid::Uuid::new_v4().simple(),
        uuid::Uuid::new_v4().simple()
    );
    let Ok(mut sessions) = auth.sessions.lock() else {
        return denied(StatusCode::SERVICE_UNAVAILABLE, "session store unavailable");
    };
    sessions.retain(|_, expiry| *expiry > Instant::now());
    if sessions.len() >= 64 {
        return denied(
            StatusCode::TOO_MANY_REQUESTS,
            "too many active sessions; restart this personal instance to revoke them",
        );
    }
    sessions.insert(
        session.clone(),
        Instant::now() + Duration::from_secs(SESSION_SECONDS),
    );
    let secure = if auth.public_origin.is_some() {
        "; Secure"
    } else {
        ""
    };
    let cookie = format!(
        "{}={session}; HttpOnly; SameSite=Strict; Path=/; Max-Age={SESSION_SECONDS}{secure}",
        auth.cookie_name
    );
    let mut response = Redirect::to("/").into_response();
    response
        .headers_mut()
        .insert(header::SET_COOKIE, cookie.parse().unwrap());
    response
}
async fn logout(State(auth): State<Arc<InstanceAuth>>, headers: HeaderMap) -> Response {
    if let Some(cookie) = auth.cookie_value(&headers) {
        if let Ok(mut sessions) = auth.sessions.lock() {
            sessions.remove(cookie);
        }
    }
    let mut response = Redirect::to("/auth/login").into_response();
    response.headers_mut().insert(
        header::SET_COOKIE,
        format!(
            "{}=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0",
            auth.cookie_name
        )
        .parse()
        .unwrap(),
    );
    response
}
