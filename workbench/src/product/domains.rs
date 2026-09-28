//! Narrow loopback bridge for existing deterministic CAD/engineering/packing pages.
//! It cannot proxy chat, sessions, model settings or arbitrary URLs.
use axum::{
    body::{to_bytes, Body},
    extract::Request,
    http::{header, StatusCode},
    response::{IntoResponse, Response},
    routing::any,
    Router,
};
use serde_json::json;
use std::time::Duration;
use tokio_stream::StreamExt;

const MAX_RESULT_BYTES: usize = 50 * 1024 * 1024;

pub fn router() -> Router {
    Router::new()
        .route("/packing", any(forward))
        .route("/packing/{*path}", any(forward))
        .route("/cad", any(forward))
        .route("/logistics", any(forward))
        .route("/engineering", any(forward))
        .route("/engineering/{*path}", any(forward))
        .route("/api/cad/{*path}", any(forward))
        .route("/api/logistics/{*path}", any(forward))
        .route("/api/engineering/{*path}", any(forward))
        .route("/api/asr", any(forward))
        .route("/api/asr/{*path}", any(forward))
}
fn error(status: StatusCode, message: &str) -> Response {
    (status, axum::Json(json!({"detail":message}))).into_response()
}

async fn forward(request: Request) -> Response {
    let Some(base) = std::env::var("CIVIL_DOMAIN_URL")
        .ok()
        .filter(|s| !s.is_empty())
    else {
        return error(
            StatusCode::SERVICE_UNAVAILABLE,
            "领域服务未启动，请使用 scripts/start_unified_workbench.py 启动完整工作台",
        );
    };
    let Ok(url) = reqwest::Url::parse(&base) else {
        return error(StatusCode::SERVICE_UNAVAILABLE, "领域服务地址无效");
    };
    if url.scheme() != "http"
        || !matches!(url.host_str(), Some("127.0.0.1" | "[::1]"))
        || url.path() != "/"
        || url.query().is_some()
        || url.fragment().is_some()
        || !url.username().is_empty()
        || url.password().is_some()
    {
        return error(
            StatusCode::SERVICE_UNAVAILABLE,
            "领域服务必须使用固定回环地址",
        );
    }
    let (parts, body) = request.into_parts();
    // reqwest normalizes dot segments. Reject them before building the fixed
    // domain URL, including encoded separators; current route IDs are ASCII.
    let path = parts.uri.path();
    let is_packing = path == "/packing" || path.starts_with("/packing/");
    if path.contains(['%', '\\']) || path.split('/').any(|segment| matches!(segment, "." | "..")) {
        return error(StatusCode::BAD_REQUEST, "领域路径含不支持的编码或目录跳转");
    }
    if parts
        .headers
        .get("sec-fetch-site")
        .is_some_and(|v| v == "cross-site")
    {
        return error(StatusCode::FORBIDDEN, "只接受当前工作台请求");
    }
    if let Some(origin) = parts.headers.get(header::ORIGIN) {
        let origin = origin
            .to_str()
            .ok()
            .and_then(|s| reqwest::Url::parse(s).ok());
        let host = parts
            .headers
            .get(header::HOST)
            .and_then(|h| h.to_str().ok())
            .unwrap_or("");
        if origin.as_ref().is_none_or(|o| {
            !matches!(o.scheme(), "http" | "https")
                || o.origin().ascii_serialization() != format!("{}://{}", o.scheme(), host)
        }) {
            return error(StatusCode::FORBIDDEN, "请求来源不匹配工作台");
        }
    }
    let bytes = match to_bytes(body, 32 * 1024 * 1024).await {
        Ok(b) => b,
        Err(_) => return error(StatusCode::PAYLOAD_TOO_LARGE, "领域请求超过32MiB"),
    };
    let Ok(client) = reqwest::Client::builder()
        .no_proxy()
        .timeout(Duration::from_secs(if is_packing { 300 } else { 150 }))
        .redirect(reqwest::redirect::Policy::none())
        .build()
    else {
        return error(StatusCode::BAD_GATEWAY, "无法建立领域服务连接");
    };
    let mut outgoing = client
        .request(
            parts.method,
            format!(
                "{}{}",
                base.trim_end_matches('/'),
                parts
                    .uri
                    .path_and_query()
                    .map(|p| p.as_str())
                    .unwrap_or("/")
            ),
        )
        .body(bytes);
    if let Ok(token) = std::env::var("CIVIL_DOMAIN_TOKEN") {
        outgoing = outgoing.bearer_auth(token);
    }
    for name in [
        "content-type",
        "x-civil-asr-id",
        "x-civil-asr-language",
        "x-civil-operation-id",
        "x-cad-operation-id",
    ] {
        if let Some(value) = parts.headers.get(name) {
            outgoing = outgoing.header(name, value);
        }
    }
    // Outer same-origin check has passed. The fixed sidecar validates its own host.
    if parts.headers.contains_key(header::ORIGIN) {
        outgoing = outgoing.header(header::ORIGIN, base.trim_end_matches('/'));
    }
    let mut response = match outgoing.send().await {
        Ok(r) => r,
        Err(_) => return error(StatusCode::BAD_GATEWAY, "领域服务不可用或请求超时"),
    };
    let status = response.status();
    let headers = response.headers().clone();
    let mut output = Response::builder().status(status);
    for name in [
        header::CONTENT_TYPE,
        header::CONTENT_DISPOSITION,
        header::CACHE_CONTROL,
    ] {
        if let Some(value) = headers.get(&name) {
            output = output.header(name, value);
        }
    }
    let is_event_stream = headers
        .get(header::CONTENT_TYPE)
        .and_then(|value| value.to_str().ok())
        .is_some_and(|value| {
            value
                .split(';')
                .next()
                .unwrap_or("")
                .trim()
                .eq_ignore_ascii_case("text/event-stream")
        });
    if is_packing && status.is_success() && is_event_stream {
        // Deliver progress immediately instead of waiting for the packing job
        // to finish. The request timeout still bounds the complete stream, and
        // the same output cap applies without buffering the result in memory.
        // A transport/size error aborts the body; it must not look like a clean
        // end-of-stream to the browser's existing resume recovery.
        let mut received = 0usize;
        let stream = response.bytes_stream().map(move |chunk| {
            let chunk = chunk.map_err(std::io::Error::other)?;
            received = received.saturating_add(chunk.len());
            if received > MAX_RESULT_BYTES {
                return Err(std::io::Error::other("领域结果超过50MiB"));
            }
            Ok(chunk)
        });
        return output
            .header("x-accel-buffering", "no")
            .body(Body::from_stream(stream))
            .unwrap_or_else(|_| error(StatusCode::INTERNAL_SERVER_ERROR, "无效领域响应"));
    }
    let mut bytes = Vec::new();
    loop {
        match response.chunk().await {
            Ok(Some(chunk)) => {
                if bytes.len() + chunk.len() > MAX_RESULT_BYTES {
                    return error(StatusCode::BAD_GATEWAY, "领域结果超过50MiB");
                }
                bytes.extend_from_slice(&chunk);
            }
            Ok(None) => break,
            Err(_) => return error(StatusCode::BAD_GATEWAY, "读取领域结果失败"),
        }
    }
    output
        .body(Body::from(bytes))
        .unwrap_or_else(|_| error(StatusCode::INTERNAL_SERVER_ERROR, "无效领域响应"))
}
