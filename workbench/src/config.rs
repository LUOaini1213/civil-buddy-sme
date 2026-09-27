use std::env;
use std::path::{Path, PathBuf};
use std::sync::RwLock;

#[derive(Clone, Debug)]
pub struct Paths {
    pub repo_root: PathBuf,
    pub demo_root: PathBuf,
    pub kb_root: PathBuf,
    pub out_root: PathBuf,
    pub data_dir: PathBuf,
    pub user_catalog: PathBuf,
    pub skill_hard_rules: PathBuf,
    pub static_dir: PathBuf,
    pub fill_scheme_py: PathBuf,
    pub scan_py: PathBuf,
    pub template_docx: PathBuf,
}

impl Paths {
    pub fn detect() -> Self {
        let demo = detect_demo_root();
        Self::from_demo(demo)
    }

    pub fn from_demo(demo_root: PathBuf) -> Self {
        let repo_root = demo_root
            .parent()
            .filter(|p| p.join("skills").is_dir() || p.join("demo").is_dir())
            .map(Path::to_path_buf)
            .unwrap_or_else(|| demo_root.clone());
        let kb_root = demo_root.join("kb");
        let instance = env::var_os("CIVIL_STATE_ROOT").map(PathBuf::from);
        let data_dir = instance
            .as_ref()
            .map(|p| p.join("legacy/data"))
            .unwrap_or_else(|| demo_root.join("data"));
        let skill = repo_root.join("skills").join("civil-buddy");
        Self {
            out_root: instance
                .as_ref()
                .map(|p| p.join("domains"))
                .unwrap_or_else(|| demo_root.join("out")),
            user_catalog: data_dir.join("user_catalog.json"),
            skill_hard_rules: skill.join("references").join("hard-rules.md"),
            fill_scheme_py: skill.join("scripts").join("fill_scheme_template.py"),
            scan_py: skill.join("scripts").join("scan_forbidden_inventions.py"),
            template_docx: skill
                .join("references")
                .join("templates")
                .join("scheme-cn-a4.docx"),
            static_dir: demo_root.join("static"),
            kb_root,
            data_dir,
            repo_root,
            demo_root,
        }
    }
}

fn looks_like_demo(p: &Path) -> bool {
    p.join("kb").is_dir() && p.join("static").is_dir()
}

fn find_demo_upwards(start: &Path) -> Option<PathBuf> {
    let mut dir = Some(start.to_path_buf());
    for _ in 0..10 {
        let d = dir?;
        if looks_like_demo(&d) {
            return Some(d.canonicalize().unwrap_or(d));
        }
        let demo = d.join("demo");
        if looks_like_demo(&demo) {
            return Some(demo.canonicalize().unwrap_or(demo));
        }
        dir = d.parent().map(Path::to_path_buf);
    }
    None
}

fn detect_demo_root() -> PathBuf {
    if let Ok(p) = env::var("CIVIL_DEMO_ROOT") {
        let p = PathBuf::from(p);
        if p.is_dir() {
            return p;
        }
    }
    if let Ok(manifest) = env::var("CARGO_MANIFEST_DIR") {
        if let Some(found) = find_demo_upwards(Path::new(&manifest)) {
            return found;
        }
    }
    if let Ok(exe) = env::current_exe() {
        let start = exe.parent().unwrap_or(&exe);
        if let Some(found) = find_demo_upwards(start) {
            return found;
        }
    }
    let cwd = env::current_dir().unwrap_or_else(|_| PathBuf::from("."));
    find_demo_upwards(&cwd).unwrap_or(cwd)
}

/// cwd `.env` fills gaps; exe-dir and repo `.env` next; `demo/.env` overrides a stale User-level key.
pub fn load_env() {
    if env::var("PYTHON_DOTENV_DISABLED").as_deref() == Ok("1")
        || env::var("CIVIL_DOTENV_DISABLED").as_deref() == Ok("1")
    {
        return;
    }
    let _ = dotenvy::dotenv();
    if let Ok(exe) = env::current_exe() {
        if let Some(dir) = exe.parent() {
            let p = dir.join(".env");
            if p.is_file() {
                let _ = dotenvy::from_path(&p);
            }
        }
    }
    let paths = Paths::detect();
    let root_env = paths.repo_root.join(".env");
    if root_env.is_file() {
        let _ = dotenvy::from_path(&root_env);
    }
    let demo_env = paths.demo_root.join(".env");
    if demo_env.is_file() {
        let _ = dotenvy::from_path_override(&demo_env);
    }
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct LlmConfig {
    pub api_key: String,
    pub base_url: String,
    pub model: String,
}

fn pick_env<F>(get: &F, names: &[&str]) -> String
where
    F: Fn(&str) -> Option<String>,
{
    for n in names {
        if let Some(v) = get(n) {
            let t = v.trim();
            if !t.is_empty() {
                return t.to_string();
            }
        }
    }
    String::new()
}

/// OpenAI-compatible Chat Completions. Testers bring any key; DeepSeek is optional.
pub fn llm_from_env_map<F>(get: F) -> LlmConfig
where
    F: Fn(&str) -> Option<String>,
{
    let api_key = pick_env(
        &get,
        &[
            "CIVIL_API_KEY",
            "OPENAI_API_KEY",
            "LLM_API_KEY",
            "DEEPSEEK_API_KEY",
        ],
    );
    let explicit_base = pick_env(
        &get,
        &[
            "CIVIL_API_BASE",
            "OPENAI_BASE_URL",
            "LLM_BASE_URL",
            "DEEPSEEK_BASE_URL",
        ],
    );
    let generic_key = pick_env(&get, &["CIVIL_API_KEY", "OPENAI_API_KEY", "LLM_API_KEY"]);
    let base_url = if !explicit_base.is_empty() {
        explicit_base.trim_end_matches('/').to_string()
    } else if !generic_key.is_empty() {
        "https://api.openai.com/v1".into()
    } else {
        "https://api.deepseek.com".into()
    };
    let model_explicit = pick_env(
        &get,
        &["CIVIL_MODEL", "LLM_MODEL", "DEEPSEEK_MODEL", "OPENAI_MODEL"],
    );
    let model = if !model_explicit.is_empty() {
        model_explicit
    } else if base_url.to_ascii_lowercase().contains("deepseek") {
        "deepseek-flash".into()
    } else {
        "gpt-4o-mini".into()
    };
    LlmConfig {
        api_key,
        base_url,
        model,
    }
}

/* ux(round17) 运行时模型配置覆盖：让评委/试用者在界面里填 Key、切 DeepSeek / z.ai 等
OpenAI 兼容供应商，不必改 demo/.env 再重启进程。
边界：只存进程内存——不写盘、不进日志、不随会话落盘；进程退出即失效。
env 仍是缺省来源，清除覆盖即回退到 .env 口径。 */
static RUNTIME_LLM: RwLock<Option<LlmConfig>> = RwLock::new(None);

pub fn set_runtime_llm(cfg: Option<LlmConfig>) {
    if let Ok(mut g) = RUNTIME_LLM.write() {
        *g = cfg;
    }
}

pub fn runtime_llm() -> Option<LlmConfig> {
    RUNTIME_LLM.read().ok().and_then(|g| g.clone())
}

pub fn llm_config() -> LlmConfig {
    if let Some(c) = runtime_llm() {
        return c;
    }
    llm_from_env_map(|k| env::var(k).ok())
}

pub fn llm_uses_thinking(base_url: &str) -> bool {
    base_url.to_ascii_lowercase().contains("deepseek")
}

pub fn llm_api_key() -> String {
    llm_config().api_key
}

pub fn llm_base_url() -> String {
    llm_config().base_url
}

pub fn llm_model() -> String {
    llm_config().model
}

pub fn deepseek_api_key() -> String {
    llm_api_key()
}

pub fn deepseek_base_url() -> String {
    llm_base_url()
}

pub fn deepseek_model() -> String {
    llm_model()
}

pub fn max_agent_steps() -> usize {
    env::var("CIVIL_MAX_AGENT_STEPS")
        .ok()
        .and_then(|s| s.parse().ok())
        .unwrap_or(8)
}

pub fn has_key() -> bool {
    !llm_api_key().is_empty()
}

#[cfg(test)]
mod llm_env_tests {
    use super::*;
    use std::collections::HashMap;

    fn from_map(m: &HashMap<&str, &str>) -> LlmConfig {
        llm_from_env_map(|k| m.get(k).map(|s| (*s).to_string()))
    }

    #[test]
    fn openai_key_does_not_need_deepseek() {
        let mut m = HashMap::new();
        m.insert("OPENAI_API_KEY", "sk-test");
        m.insert("LLM_MODEL", "gpt-4o-mini");
        let c = from_map(&m);
        assert_eq!(c.api_key, "sk-test");
        assert!(c.base_url.contains("openai.com"), "{}", c.base_url);
        assert_eq!(c.model, "gpt-4o-mini");
        assert!(!llm_uses_thinking(&c.base_url));
    }

    #[test]
    fn deepseek_only_still_works() {
        let mut m = HashMap::new();
        m.insert("DEEPSEEK_API_KEY", "sk-ds");
        let c = from_map(&m);
        assert_eq!(c.api_key, "sk-ds");
        assert!(c.base_url.contains("deepseek"), "{}", c.base_url);
        assert_eq!(c.model, "deepseek-flash");
        assert!(llm_uses_thinking(&c.base_url));
    }

    #[test]
    fn civil_key_wins_over_deepseek() {
        let mut m = HashMap::new();
        m.insert("CIVIL_API_KEY", "sk-civil");
        m.insert("DEEPSEEK_API_KEY", "sk-ds");
        m.insert("CIVIL_API_BASE", "https://api.moonshot.cn/v1");
        m.insert("CIVIL_MODEL", "moonshot-v1-8k");
        let c = from_map(&m);
        assert_eq!(c.api_key, "sk-civil");
        assert!(c.base_url.contains("moonshot"), "{}", c.base_url);
        assert_eq!(c.model, "moonshot-v1-8k");
        assert!(!llm_uses_thinking(&c.base_url));
    }
}
