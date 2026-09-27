use crate::config::LlmConfig;
use crate::runtime_core::{check_request_budget, BudgetTree, CancellationToken, TaskId, Usage};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::time::Duration;

#[derive(Debug, thiserror::Error)]
pub enum ProviderError {
    #[error("{0}")]
    Invalid(String),
    #[error("provider HTTP {0}; check configuration or retry later")]
    Http(u16),
    /// HTTP 429 carries the provider's Retry-After, when it sent one.
    #[error("provider rate-limited this request (HTTP 429{}); this is not a configuration problem, wait a moment and try again", wait_hint(.0))]
    RateLimited(Option<Duration>),
    #[error("provider HTTP {status}: the provider failed temporarily{}; retry later", wait_hint(.retry_after))]
    Unavailable {
        status: u16,
        retry_after: Option<Duration>,
    },
    #[error("provider request failed or timed out")]
    Transport,
    /// The attempt ran to its timeout. The provider may still be working on
    /// it, so this is not retried: another attempt would only wait again.
    #[error("provider did not answer before the request timeout; retry later")]
    TimedOut,
    #[error(transparent)]
    Runtime(#[from] crate::runtime_core::RuntimeError),
}
type Result<T> = std::result::Result<T, ProviderError>;

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct Completion {
    pub message: Value,
    pub model: String,
    pub usage: Usage,
    pub finish_reason: String,
}

pub async fn cancelled(token: &CancellationToken) {
    while !token.is_cancelled() {
        tokio::time::sleep(Duration::from_millis(40)).await;
    }
}

fn client(timeout: Duration) -> Result<reqwest::Client> {
    reqwest::Client::builder()
        .timeout(timeout)
        .redirect(reqwest::redirect::Policy::none())
        .build()
        .map_err(|_| ProviderError::Transport)
}

fn validate_endpoint(raw: &str) -> Result<()> {
    let url = reqwest::Url::parse(raw)
        .map_err(|_| ProviderError::Invalid("invalid provider endpoint".into()))?;
    let loopback = matches!(url.host_str(), Some("localhost" | "127.0.0.1" | "[::1]"));
    if url.scheme() != "https" && !(url.scheme() == "http" && loopback) {
        return Err(ProviderError::Invalid(
            "provider endpoint requires HTTPS (HTTP only on loopback)".into(),
        ));
    }
    if !url.username().is_empty()
        || url.password().is_some()
        || url.query().is_some()
        || url.fragment().is_some()
    {
        return Err(ProviderError::Invalid(
            "provider endpoint must not contain credentials, query or fragment".into(),
        ));
    }
    Ok(())
}

/// One bounded HTTP request (120 s). Failed attempts stay charged by the budget
/// reservation; retrying is the caller's choice, see [`complete_with_retry`].
pub async fn complete(
    cfg: &LlmConfig,
    messages: &[Value],
    tools: &[Value],
    max_output: u64,
    window: u64,
    budget: &BudgetTree,
    task: &TaskId,
    cancel: &CancellationToken,
) -> Result<Completion> {
    let timeout = MODEL_REQUEST_TIMEOUT;
    attempt(
        cfg, messages, tools, max_output, window, budget, task, cancel, timeout,
    )
    .await
}

async fn attempt(
    cfg: &LlmConfig,
    messages: &[Value],
    tools: &[Value],
    max_output: u64,
    window: u64,
    budget: &BudgetTree,
    task: &TaskId,
    cancel: &CancellationToken,
    timeout: Duration,
) -> Result<Completion> {
    if cfg.api_key.is_empty() {
        return Err(ProviderError::Invalid(
            "请先在模型设置中配置 API Key".into(),
        ));
    }
    let endpoint = format!("{}/chat/completions", cfg.base_url.trim_end_matches('/'));
    validate_endpoint(&endpoint)?;
    let mut payload =
        json!({"model":cfg.model,"messages":messages,"max_tokens":max_output,"stream":false});
    if cfg.base_url.to_ascii_lowercase().contains("deepseek") {
        payload["thinking"] = json!({"type":"disabled"});
    }
    if !tools.is_empty() {
        payload["tools"] = json!(tools);
        payload["tool_choice"] = json!("auto");
    }
    let estimated_input = check_request_budget(&payload, max_output, window)?;
    cancel.check()?;
    let mut reservation = budget.reserve(task, estimated_input, max_output, 1)?;
    reservation.start()?;
    let request = async {
        let mut response = client(timeout)?
            .post(endpoint)
            .bearer_auth(&cfg.api_key)
            .json(&payload)
            .send()
            .await
            .map_err(transport_error)?;
        let status = response.status();
        if !status.is_success() {
            return Err(status_error(status.as_u16(), response.headers()));
        }
        let mut bytes = Vec::new();
        while let Some(chunk) = response.chunk().await.map_err(transport_error)? {
            if bytes.len() + chunk.len() > 4 * 1024 * 1024 {
                return Err(ProviderError::Invalid(
                    "provider response exceeds 4 MiB".into(),
                ));
            }
            bytes.extend_from_slice(&chunk);
        }
        serde_json::from_slice::<Value>(&bytes)
            .map_err(|_| ProviderError::Invalid("provider returned invalid JSON".into()))
    };
    let raw = tokio::select! {
        _ = cancelled(cancel) => { cancel.check()?; unreachable!() },
        result = request => result,
    };
    let raw = match raw {
        Ok(raw) => raw,
        Err(error) => {
            if error.is_status() {
                // The provider answered with an error status, so it generated
                // no output. Keep the call and the (estimated) input charged;
                // transport failures stay charged their whole reservation.
                let _ = reservation.settle(Usage {
                    input_tokens: estimated_input,
                    output_tokens: 0,
                    model_calls: 1,
                    estimated: true,
                });
            }
            return Err(error);
        }
    };
    let message = raw
        .pointer("/choices/0/message")
        .cloned()
        .ok_or_else(|| ProviderError::Invalid("provider response missing message".into()))?;
    if message["role"] != "assistant" {
        return Err(ProviderError::Invalid(
            "provider returned non-assistant message".into(),
        ));
    }
    let supplied_usage = raw
        .pointer("/usage/prompt_tokens")
        .and_then(Value::as_u64)
        .zip(
            raw.pointer("/usage/completion_tokens")
                .and_then(Value::as_u64),
        );
    let usage = match supplied_usage {
        Some((input_tokens, output_tokens)) => Usage {
            input_tokens,
            output_tokens,
            model_calls: 1,
            estimated: false,
        },
        None => Usage {
            input_tokens: estimated_input,
            output_tokens: max_output,
            model_calls: 1,
            estimated: true,
        },
    };
    reservation.settle(usage.clone())?;
    Ok(Completion {
        message,
        model: raw["model"].as_str().unwrap_or(&cfg.model).into(),
        usage,
        finish_reason: raw
            .pointer("/choices/0/finish_reason")
            .and_then(Value::as_str)
            .unwrap_or("unknown")
            .into(),
    })
}

const MODEL_REQUEST_TIMEOUT: Duration = Duration::from_secs(120);
/// A retry starts only if at least this much of the task deadline would remain
/// after its backoff, so a retry never runs the turn past its deadline.
const MIN_RETRY_WINDOW: Duration = Duration::from_secs(5);

fn wait_hint(wait: &Option<Duration>) -> String {
    match wait.map(|d| d.as_secs_f64().ceil() as u64) {
        Some(secs) if secs > 0 => format!(", it asked to wait {secs} s"),
        _ => String::new(),
    }
}

fn transport_error(error: reqwest::Error) -> ProviderError {
    if error.is_timeout() {
        ProviderError::TimedOut
    } else {
        ProviderError::Transport
    }
}

fn status_error(status: u16, headers: &reqwest::header::HeaderMap) -> ProviderError {
    match status {
        429 => ProviderError::RateLimited(retry_after(headers)),
        500..=599 => ProviderError::Unavailable {
            status,
            retry_after: retry_after(headers),
        },
        _ => ProviderError::Http(status),
    }
}

/// `retry-after-ms` (sent by some OpenAI-compatible APIs) or `Retry-After` in
/// seconds. The HTTP-date form is ignored and ordinary backoff applies.
fn retry_after(headers: &reqwest::header::HeaderMap) -> Option<Duration> {
    let number = |name: &str| {
        headers
            .get(name)?
            .to_str()
            .ok()?
            .trim()
            .parse::<f64>()
            .ok()
            .filter(|v| v.is_finite() && *v >= 0.0)
            .map(|v| v.min(86_400_000.0))
    };
    number("retry-after-ms")
        .map(|ms| Duration::from_secs_f64(ms / 1000.0))
        .or_else(|| number("retry-after").map(|s| Duration::from_secs_f64(s.min(86_400.0))))
}

impl ProviderError {
    /// The provider answered with an HTTP error status (no output generated).
    fn is_status(&self) -> bool {
        matches!(
            self,
            Self::Http(_) | Self::RateLimited(_) | Self::Unavailable { .. }
        )
    }
    /// 429, 5xx and connection errors are worth another attempt; a timeout is
    /// not (the attempt already waited its whole window). The inner value is
    /// the provider's Retry-After, if it sent one.
    fn transient(&self) -> Option<Option<Duration>> {
        match self {
            Self::RateLimited(wait)
            | Self::Unavailable {
                retry_after: wait, ..
            } => Some(*wait),
            Self::Transport => Some(None),
            _ => None,
        }
    }
}

/// Bounded retry of transient provider failures.
#[derive(Clone, Debug)]
pub struct RetryPolicy {
    pub max_retries: u32,
    /// Full jitter: retry n sleeps a uniform time in [0, min(max, base * 2^n)].
    pub base_delay: Duration,
    /// Also the longest Retry-After honoured; a longer one ends the retries.
    pub max_delay: Duration,
    /// Each attempt's timeout (never past the task deadline).
    pub attempt_timeout: Duration,
}
impl Default for RetryPolicy {
    fn default() -> Self {
        Self {
            max_retries: 2,
            base_delay: Duration::from_secs(2),
            max_delay: Duration::from_secs(8),
            attempt_timeout: MODEL_REQUEST_TIMEOUT,
        }
    }
}
impl RetryPolicy {
    pub fn none() -> Self {
        Self {
            max_retries: 0,
            ..Self::default()
        }
    }
    /// A model call may be retried only while no tool has run in the turn.
    pub fn before_tools(no_tool_has_run: bool) -> Self {
        if no_tool_has_run {
            Self::default()
        } else {
            Self::none()
        }
    }
    fn delay(&self, retry: u32, requested: Option<Duration>) -> Option<Duration> {
        match requested {
            Some(wait) => (wait <= self.max_delay).then_some(wait),
            None => {
                let ceiling = self
                    .base_delay
                    .saturating_mul(1u32 << retry.min(16))
                    .min(self.max_delay);
                // The low 62 bits of a v4 UUID are random.
                let bits = uuid::Uuid::new_v4().as_u128() as u64 & ((1u64 << 53) - 1);
                Some(ceiling.mul_f64(bits as f64 / (1u64 << 53) as f64))
            }
        }
    }
}

/// Reported to the caller before each backoff sleep.
#[derive(Clone, Debug)]
pub struct RetryNotice {
    pub retry: u32,
    pub max_retries: u32,
    pub delay: Duration,
    pub reason: String,
}

fn remaining(budget: &BudgetTree) -> Duration {
    budget
        .snapshot()
        .map(|s| Duration::from_millis(s.limits.timeout_ms.saturating_sub(s.elapsed_ms)))
        .unwrap_or(MODEL_REQUEST_TIMEOUT)
}

/// [`complete`] with bounded retry of transient failures (429, 5xx, dropped
/// connections): at most `policy.max_retries` more attempts after a
/// full-jitter backoff or the provider's Retry-After, never past the task
/// deadline, and cancellation ends the backoff sleep at once. A timed-out
/// attempt is not retried, so a hung provider fails after one attempt window
/// (120 s) instead of holding the turn to its 300 s limit. Every attempt
/// makes its own budget reservation and consumes a model call. The caller
/// decides when a retry is safe; the product agent retries only before any
/// tool has run in the turn.
pub async fn complete_with_retry(
    cfg: &LlmConfig,
    messages: &[Value],
    tools: &[Value],
    max_output: u64,
    window: u64,
    budget: &BudgetTree,
    task: &TaskId,
    cancel: &CancellationToken,
    policy: &RetryPolicy,
    mut on_retry: impl FnMut(&RetryNotice),
) -> Result<Completion> {
    let mut retry = 0;
    loop {
        let timeout = remaining(budget).min(policy.attempt_timeout);
        let error = match attempt(
            cfg, messages, tools, max_output, window, budget, task, cancel, timeout,
        )
        .await
        {
            Ok(completion) => return Ok(completion),
            Err(error) => error,
        };
        let Some(requested) = error.transient() else {
            return Err(error);
        };
        if retry >= policy.max_retries || cancel.is_cancelled() {
            return Err(error);
        }
        let Some(delay) = policy.delay(retry, requested) else {
            return Err(error);
        };
        if remaining(budget) < delay.saturating_add(MIN_RETRY_WINDOW) {
            return Err(error);
        }
        retry += 1;
        on_retry(&RetryNotice {
            retry,
            max_retries: policy.max_retries,
            delay,
            reason: error.to_string(),
        });
        tokio::select! {
            _ = cancelled(cancel) => { cancel.check()?; unreachable!() },
            _ = tokio::time::sleep(delay) => {}
        }
    }
}

#[derive(Debug, Clone, Copy, Default, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum JevMode {
    #[default]
    Off,
    Shadow,
    Assist,
}

#[derive(Clone)]
pub struct JevConfig {
    pub endpoint: String,
    pub api_key: String,
    pub model: String,
    pub mode: JevMode,
}
impl JevConfig {
    pub fn from_env() -> Self {
        Self {
            endpoint: std::env::var("JEV_ENDPOINT")
                .unwrap_or_else(|_| "https://api.typesafe.ai/v1/systemone".into()),
            api_key: std::env::var("JEV_API_KEY")
                .or_else(|_| std::env::var("TYPESAFE_API_KEY"))
                .unwrap_or_default(),
            model: std::env::var("JEV_MODEL").unwrap_or_else(|_| "jev-latest".into()),
            mode: match std::env::var("JEV_MODE").unwrap_or_default().as_str() {
                "shadow" => JevMode::Shadow,
                "assist" => JevMode::Assist,
                _ => JevMode::Off,
            },
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(tag = "type", rename_all = "lowercase")]
pub enum DecisionQuestion {
    Choice {
        instructions: String,
        criteria: std::collections::BTreeMap<String, String>,
    },
    Score {
        instructions: String,
        criteria: Vec<String>,
    },
    Noul {
        instructions: String,
    },
}

fn probability(value: &Value) -> Result<f64> {
    value
        .as_f64()
        .filter(|n| n.is_finite() && (0.0..=1.0).contains(n))
        .ok_or_else(|| ProviderError::Invalid("invalid Jev probability".into()))
}

/// The host owns the question IDs, candidate meanings and evidence. A validated
/// answer remains a proposal; it never becomes a tool permission.
pub fn validate_answers(
    questions: &std::collections::BTreeMap<String, DecisionQuestion>,
    raw: &Value,
) -> Result<Value> {
    let answers = raw["answers"]
        .as_object()
        .ok_or_else(|| ProviderError::Invalid("missing Jev answers".into()))?;
    if answers.len() != questions.len() {
        return Err(ProviderError::Invalid("Jev question set mismatch".into()));
    }
    for (id, question) in questions {
        let answer = answers
            .get(id)
            .ok_or_else(|| ProviderError::Invalid("Jev question ID mismatch".into()))?;
        let keys: Vec<String> = match question {
            DecisionQuestion::Choice { criteria, .. } => {
                if answer["type"] != "choice"
                    || criteria.is_empty()
                    || !criteria.contains_key(answer["choice"].as_str().unwrap_or(""))
                {
                    return Err(ProviderError::Invalid(
                        "Jev chose an invalid candidate".into(),
                    ));
                }
                criteria.keys().cloned().collect()
            }
            DecisionQuestion::Score { criteria, .. } => {
                let score = answer["score"].as_f64().unwrap_or(f64::NAN);
                if answer["type"] != "score"
                    || criteria.len() < 2
                    || !score.is_finite()
                    || !(0.0..=(criteria.len() - 1) as f64).contains(&score)
                {
                    return Err(ProviderError::Invalid("invalid Jev score".into()));
                }
                for (index, label) in criteria.iter().enumerate() {
                    if answer["legend"][index.to_string()] != *label {
                        return Err(ProviderError::Invalid("Jev score legend mismatch".into()));
                    }
                }
                (0..criteria.len()).map(|i| i.to_string()).collect()
            }
            DecisionQuestion::Noul { .. } => {
                if answer["type"] != "noul" {
                    return Err(ProviderError::Invalid("Jev answer type mismatch".into()));
                }
                probability(&answer["noul"])?;
                continue;
            }
        };
        probability(&answer["confidence"])?;
        let probabilities = answer["probabilities"]
            .as_object()
            .ok_or_else(|| ProviderError::Invalid("missing Jev probabilities".into()))?;
        if probabilities.len() != keys.len() {
            return Err(ProviderError::Invalid(
                "Jev probability candidates mismatch".into(),
            ));
        }
        let mut total = 0.0;
        for key in keys {
            total += probability(probabilities.get(&key).ok_or_else(|| {
                ProviderError::Invalid("missing Jev probability candidate".into())
            })?)?;
        }
        if (total - 1.0).abs() > 0.02 {
            return Err(ProviderError::Invalid(
                "Jev probabilities do not sum to one".into(),
            ));
        }
    }
    Ok(raw["answers"].clone())
}

pub async fn decide(
    cfg: &JevConfig,
    state: Value,
    questions: std::collections::BTreeMap<String, DecisionQuestion>,
    budget: &BudgetTree,
    task: &TaskId,
    cancel: &CancellationToken,
) -> Result<Value> {
    if cfg.mode == JevMode::Off {
        return Ok(json!({"mode":"off","applied":false}));
    }
    if cfg.api_key.is_empty() {
        return Err(ProviderError::Invalid("Jev key is not configured".into()));
    }
    validate_endpoint(&cfg.endpoint)?;
    let payload = json!({"model":cfg.model,"state":state,"questions":questions});
    let input_estimate = check_request_budget(&payload, 1024, 32768)?;
    let mut reservation = budget.reserve(task, input_estimate, 1024, 1)?;
    cancel.check()?;
    reservation.start()?;
    let request = async {
        let mut response = client(Duration::from_secs(15))?
            .post(&cfg.endpoint)
            .bearer_auth(&cfg.api_key)
            .json(&payload)
            .send()
            .await
            .map_err(|_| ProviderError::Transport)?;
        if !response.status().is_success() {
            return Err(status_error(response.status().as_u16(), response.headers()));
        }
        if response.content_length().is_some_and(|n| n > 256 * 1024) {
            return Err(ProviderError::Invalid("Jev response too large".into()));
        }
        let mut bytes = Vec::new();
        while let Some(chunk) = response
            .chunk()
            .await
            .map_err(|_| ProviderError::Transport)?
        {
            if bytes.len() + chunk.len() > 256 * 1024 {
                return Err(ProviderError::Invalid("Jev response too large".into()));
            }
            bytes.extend_from_slice(&chunk);
        }
        serde_json::from_slice::<Value>(&bytes)
            .map_err(|_| ProviderError::Invalid("invalid Jev response JSON".into()))
    };
    let raw = tokio::select! {_=cancelled(cancel)=>{cancel.check()?;unreachable!()},result=request=>result?};
    let known = raw
        .pointer("/usage/input_tokens")
        .and_then(Value::as_u64)
        .zip(raw.pointer("/usage/output_tokens").and_then(Value::as_u64));
    reservation.settle(match known {
        Some((i, o)) => Usage {
            input_tokens: i,
            output_tokens: o,
            model_calls: 1,
            estimated: false,
        },
        None => Usage {
            input_tokens: input_estimate,
            output_tokens: 1024,
            model_calls: 1,
            estimated: true,
        },
    })?;
    let answers = validate_answers(&questions, &raw)?;
    Ok(
        json!({"mode":cfg.mode,"applied":false,"model":raw["model"],"answers":answers,"usage":raw["usage"],"question_version":"engineering-v1"}),
    )
}
