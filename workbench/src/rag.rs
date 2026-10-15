use crate::config::Paths;
use crate::kbio::{display_title, file_stat, iter_text_files, layer_label};
use regex::Regex;
use rusqlite::{Connection, OpenFlags};
use serde::Serialize;
use std::collections::HashSet;
use std::fs;
use std::path::PathBuf;
use std::sync::OnceLock;

#[derive(Debug, Clone, Serialize)]
pub struct Hit {
    pub path: String,
    pub layer: String,
    pub layer_label: String,
    pub title: String,
    pub snippet: String,
    pub score: f64,
}

/// data-plan M4：boost 数据化（audit A3-1）——rag.rs 5 处硬编码迁入
/// kb_index.boost（filename_eq/body_contains 无条件项）与
/// contract/kb_boosts.v1.json（sg 查询条件惩罚；脚本 build_kb_index.py 同源生成）。
#[derive(Clone, Debug)]
struct BoostRule {
    kind: String, // filename_eq | body_contains | sg_query_penalty
    value: String,
    match_filename: String,
    match_body: String,
    boost: f64,
    scope: String,
}

fn boost_rules(paths: &Paths) -> &'static Vec<BoostRule> {
    static RULES: OnceLock<Vec<BoostRule>> = OnceLock::new();
    RULES.get_or_init(|| {
        let path = paths.repo_root.join("contract").join("kb_boosts.v1.json");
        let Ok(text) = fs::read_to_string(&path) else {
            eprintln!("[rag] boost contract missing: {}", path.display());
            return Vec::new();
        };
        #[derive(serde::Deserialize)]
        struct Wrapper {
            #[serde(default)]
            rules: Vec<RawRule>,
        }
        #[derive(serde::Deserialize)]
        struct RawRule {
            #[serde(default)]
            kind: String,
            #[serde(default)]
            value: String,
            #[serde(default)]
            match_filename: String,
            #[serde(default)]
            match_body: String,
            #[serde(default)]
            boost: f64,
            #[serde(default)]
            scope: String,
        }
        let parsed: Wrapper = serde_json::from_str(&text).unwrap_or(Wrapper { rules: vec![] });
        parsed
            .rules
            .into_iter()
            .map(|r| BoostRule {
                kind: r.kind,
                value: r.value.to_ascii_lowercase(),
                match_filename: r.match_filename,
                match_body: r.match_body,
                boost: r.boost,
                scope: r.scope,
            })
            .collect()
    })
}

/// 无条件 boost（filename_eq / body_contains）之和——scan 路径用；FTS 路径直接读 kb_index.boost。
fn static_boost(paths: &Paths, kb: &str, file_name: &str, text: &str) -> f64 {
    let name = file_name.to_ascii_lowercase();
    boost_rules(paths)
        .iter()
        .filter(|r| r.kind == "filename_eq" || r.kind == "body_contains")
        .filter(|r| r.scope.is_empty() || r.scope == kb)
        .map(|r| {
            let hit = match r.kind.as_str() {
                "filename_eq" => name == r.value,
                _ => text.contains(&r.value),
            };
            if hit {
                r.boost
            } else {
                0.0
            }
        })
        .sum()
}

fn is_sg_query(query: &str, q: &[String]) -> bool {
    let qjoin = q.join(" ");
    qjoin.contains("sg")
        || qjoin.contains("singapore")
        || qjoin.contains("ptw")
        || qjoin.contains("wsh")
        || qjoin.contains("scdf")
        || qjoin.contains("pub")
        || query.contains("新加坡")
}

/// 查询条件惩罚——参数一律来自 contract/kb_boosts.v1.json（两栈同源，源码零硬编码）。
fn conditional_penalty(paths: &Paths, query: &str, q: &[String], file_name: &str, text: &str) -> f64 {
    if !is_sg_query(query, q) {
        return 0.0;
    }
    boost_rules(paths)
        .iter()
        .filter(|r| r.kind == "sg_query_penalty")
        .map(|r| {
            let name_hit = !r.match_filename.is_empty() && file_name.contains(&r.match_filename);
            let body_hit = !r.match_body.is_empty() && text.contains(&r.match_body);
            if name_hit || body_hit {
                r.boost
            } else {
                0.0
            }
        })
        .sum()
}

pub fn kb_layers(paths: &Paths, expert_id: &str, category: &str) -> Vec<(String, PathBuf)> {
    vec![
        (
            "expert".into(),
            paths.kb_root.join(category).join(expert_id),
        ),
        (
            "category".into(),
            paths.kb_root.join(category).join("_shared"),
        ),
        ("company".into(), paths.kb_root.join("company")),
    ]
}

pub fn list_kb(paths: &Paths, expert_id: &str, category: &str) -> Vec<serde_json::Value> {
    let mut rows = Vec::new();
    let kb = paths
        .kb_root
        .canonicalize()
        .unwrap_or_else(|_| paths.kb_root.clone());
    for (layer, root) in kb_layers(paths, expert_id, category) {
        for path in iter_text_files(&root) {
            let rel = path
                .strip_prefix(&kb)
                .or_else(|_| path.strip_prefix(&paths.kb_root))
                .map(|p| p.to_string_lossy().replace('\\', "/"))
                .unwrap_or_default();
            let st = file_stat(&path, &rel, &layer);
            rows.push(serde_json::to_value(st).unwrap_or(serde_json::json!({})));
        }
    }
    rows
}

pub fn read_kb(paths: &Paths, rel: &str) -> Option<(String, String)> {
    let rel = rel.replace('\\', "/");
    let rel = rel.trim_start_matches('/').to_string();
    let target = paths.kb_root.join(rel.replace('/', std::path::MAIN_SEPARATOR_STR));
    let target = target.canonicalize().ok()?;
    let kb = paths.kb_root.canonicalize().ok()?;
    target.strip_prefix(&kb).ok()?;
    if !target.is_file() {
        return None;
    }
    let text = fs::read_to_string(&target).ok()?;
    Some((rel, text))
}

fn token_re() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| Regex::new(r"[\u{4e00}-\u{9fff}]{2,}|[A-Za-z0-9_\-]{2,}").unwrap())
}

fn tokens(text: &str) -> Vec<String> {
    token_re()
        .find_iter(&text.to_lowercase())
        .map(|m| m.as_str().to_string())
        .collect()
}

fn is_cjk_run(s: &str) -> bool {
    !s.is_empty() && s.chars().all(|c| ('\u{4e00}'..='\u{9fff}').contains(&c))
}

/// 查询串 → FTS5 MATCH 串：CJK 串切 bigram、英文词整词，全部引号短语按 OR 召回
///（与 packing_assistant.kb_search.fts_match_string 同款；粗召回后由同一公式精排）。
fn fts_match_string(q: &[String]) -> String {
    let mut terms: Vec<String> = Vec::new();
    let mut seen = HashSet::new();
    for t in q {
        let t = t.to_lowercase();
        if is_cjk_run(&t) {
            let chars: Vec<char> = t.chars().collect();
            for w in chars.windows(2) {
                let bg: String = w.iter().collect();
                if seen.insert(bg.clone()) {
                    terms.push(format!("\"{bg}\""));
                }
            }
        } else if seen.insert(t.clone()) {
            terms.push(format!("\"{t}\""));
        }
    }
    terms.join(" OR ")
}

fn db_path(paths: &Paths) -> PathBuf {
    paths.repo_root.join("data").join("civilbuddy.db")
}

/// 现行打分公式（14ec2bd 原样）：token 命中 +2.0 / 整句子串 +8.0 / 文件名 +3.0，
/// 外加数据化 boost 与 sg 查询惩罚。
fn score_text(
    paths: &Paths,
    q: &[String],
    query: &str,
    text: &str,
    file_name: &str,
    boost: f64,
) -> f64 {
    let toks = tokens(text);
    if toks.is_empty() {
        return 0.0;
    }
    let bag: HashSet<&str> = toks.iter().map(|s| s.as_str()).collect();
    let mut score: f64 = q
        .iter()
        .map(|t| if bag.contains(t.as_str()) { 2.0 } else { 0.0 })
        .sum();
    let qstrip = query.trim();
    if !qstrip.is_empty() && text.contains(qstrip) {
        score += 8.0;
    }
    let name_l = file_name.to_ascii_lowercase();
    for t in q {
        if name_l.contains(t.as_str()) {
            score += 3.0;
        }
    }
    score += boost;
    score += conditional_penalty(paths, query, q, file_name, text);
    score
}

fn make_hit(
    rel: &str,
    layer: &str,
    file_name: &str,
    text: &str,
    q: &[String],
    query: &str,
    score: f64,
) -> Hit {
    let qstrip = query.trim();
    let start = if !qstrip.is_empty() {
        let key: String = qstrip.chars().take(6).collect();
        text.find(&key)
            .unwrap_or_else(|| text.find(&q[0]).unwrap_or(0))
    } else {
        text.find(&q[0]).unwrap_or(0)
    };
    let snippet: String = text
        .get(start..)
        .unwrap_or("")
        .chars()
        .take(180)
        .collect::<String>()
        .split_whitespace()
        .collect::<Vec<_>>()
        .join(" ");
    Hit {
        path: rel.to_string(),
        layer: layer.to_string(),
        layer_label: layer_label(layer).to_string(),
        title: display_title(file_name, text),
        snippet,
        score,
    }
}

fn sort_and_limit(hits: Vec<Hit>, limit: usize) -> Vec<Hit> {
    let mut hits = hits;
    hits.sort_by(|a, b| {
        b.score
            .partial_cmp(&a.score)
            .unwrap_or(std::cmp::Ordering::Equal)
            .then_with(|| {
                (a.layer != "expert")
                    .cmp(&(b.layer != "expert"))
                    .then_with(|| a.path.cmp(&b.path))
            })
    });
    let mut seen = HashSet::new();
    let mut uniq = Vec::new();
    for h in hits {
        if seen.insert(h.path.clone()) {
            uniq.push(h);
            if uniq.len() >= limit {
                break;
            }
        }
    }
    uniq
}

fn rag_mode() -> &'static str {
    static MODE: OnceLock<&'static str> = OnceLock::new();
    MODE.get_or_init(|| {
        let rust = std::env::var("CB_RUST_RAG").unwrap_or_default();
        if rust.eq_ignore_ascii_case("scan") {
            return "scan";
        }
        let py = std::env::var("CB_RAG").unwrap_or_default();
        if py.eq_ignore_ascii_case("json") {
            return "scan";
        }
        "fts"
    })
}

pub fn search_kb(
    paths: &Paths,
    expert_id: &str,
    category: &str,
    query: &str,
    limit: usize,
) -> Vec<Hit> {
    if rag_mode() == "fts" {
        match search_kb_fts(paths, expert_id, category, query, limit) {
            Some(hits) => return hits,
            None => eprintln!("[rag] kb_fts unavailable/zero-recall, fallback to scan"),
        }
    }
    search_kb_scan(paths, expert_id, category, query, limit)
}

/// FTS 粗召回路径（data-plan M4）：kb_fts MATCH 取候选 → kb_index 过滤三层 scope
/// 并读 boost → kb_chunks 拼回原文 → 现行公式精排。库缺失/零召回返回 None（回退扫描）。
fn search_kb_fts(
    paths: &Paths,
    expert_id: &str,
    category: &str,
    query: &str,
    limit: usize,
) -> Option<Vec<Hit>> {
    let db = db_path(paths);
    if !db.is_file() {
        return None;
    }
    let conn = crate::sqlite::open_with_flags(&db, OpenFlags::SQLITE_OPEN_READ_ONLY).ok()?;
    let q = tokens(query);
    if q.is_empty() {
        return Some(vec![]);
    }
    let match_str = fts_match_string(&q);
    if match_str.is_empty() {
        return None;
    }
    // 粗召回：FTS OR 召回（bm25 rank 截断）∪ 路径/文件名子串命中
    let mut cand: HashSet<String> = HashSet::new();
    {
        let mut stmt = conn
            .prepare(
                "SELECT path FROM kb_fts WHERE kb_fts MATCH ?1 AND kb='demo_kb'
                 ORDER BY rank LIMIT 250",
            )
            .ok()?;
        let rows = stmt
            .query_map([&match_str], |r| r.get::<_, String>(0))
            .ok()?;
        for r in rows.flatten() {
            cand.insert(r);
        }
    }
    for t in q.iter().filter(|t| t.chars().count() >= 2).take(8) {
        let like = format!("%{t}%");
        if let Ok(mut stmt) = conn
            .prepare("SELECT path FROM kb_index WHERE kb='demo_kb' AND path LIKE ?1 LIMIT 400")
        {
            if let Ok(rows) = stmt.query_map([&like], |r| r.get::<_, String>(0)) {
                for r in rows.flatten() {
                    cand.insert(r);
                }
            }
        }
    }
    // boost!=0 文档始终进候选（旧扫描路径靠硬编码 boost 让它们可见；数据化后同语义）
    if let Ok(mut stmt) = conn
        .prepare("SELECT path FROM kb_index WHERE kb='demo_kb' AND boost!=0")
    {
        if let Ok(rows) = stmt.query_map([], |r| r.get::<_, String>(0)) {
            for r in rows.flatten() {
                cand.insert(r);
            }
        }
    }
    if cand.is_empty() {
        return None; // 零召回：全盘扫描兜底（与 Python 行为一致）
    }
    // scope 过滤 + boost 读取（kb_index 346 行，全量拉取后内存过滤）
    #[derive(Clone)]
    struct Meta {
        layer: String,
        boost: f64,
    }
    let mut meta: std::collections::HashMap<String, Meta> = std::collections::HashMap::new();
    {
        let mut stmt = conn
            .prepare(
                "SELECT path, COALESCE(layer,''), COALESCE(category,''),
                        COALESCE(expert_id,''), COALESCE(boost,0.0)
                 FROM kb_index WHERE kb='demo_kb'",
            )
            .ok()?;
        let rows = stmt
            .query_map([], |r| {
                Ok((
                    r.get::<_, String>(0)?,
                    r.get::<_, String>(1)?,
                    r.get::<_, String>(2)?,
                    r.get::<_, String>(3)?,
                    r.get::<_, f64>(4)?,
                ))
            })
            .ok()?;
        for (path, layer, cat, exp, boost) in rows.flatten() {
            let in_scope = layer == "company"
                || (layer == "category" && cat == category)
                || (layer == "expert" && cat == category && exp == expert_id);
            if in_scope && cand.contains(&path) {
                meta.insert(path, Meta { layer, boost });
            }
        }
    }
    if meta.is_empty() {
        return None;
    }
    let mut hits = Vec::new();
    for (path, m) in meta {
        let text = chunk_text(&conn, &path)?;
        let fname = path.rsplit('/').next().unwrap_or(&path).to_string();
        let score = score_text(paths, &q, query, &text, &fname, m.boost);
        if score <= 0.0 {
            continue;
        }
        hits.push(make_hit(&path, &m.layer, &fname, &text, &q, query, score));
    }
    Some(sort_and_limit(hits, limit))
}

fn chunk_text(conn: &Connection, path: &str) -> Option<String> {
    let mut stmt = conn
        .prepare("SELECT body FROM kb_chunks WHERE kb='demo_kb' AND path=?1 ORDER BY seq")
        .ok()?;
    let rows = stmt
        .query_map([path], |r| r.get::<_, String>(0))
        .ok()?;
    let mut out = String::new();
    for b in rows.flatten() {
        out.push_str(&b);
    }
    if out.is_empty() {
        None
    } else {
        Some(out)
    }
}

/// 旧全盘扫描路径（14ec2bd 保留；CB_RUST_RAG=scan / CB_RAG=json / FTS 不可用时走此）。
/// 硬编码 boost 已数据化：filename_eq/body_contains 从 contract/kb_boosts.v1.json 读取。
fn search_kb_scan(
    paths: &Paths,
    expert_id: &str,
    category: &str,
    query: &str,
    limit: usize,
) -> Vec<Hit> {
    let q = tokens(query);
    if q.is_empty() {
        return vec![];
    }
    let mut hits = Vec::new();
    let kb = paths
        .kb_root
        .canonicalize()
        .unwrap_or_else(|_| paths.kb_root.clone());
    for (layer, root) in kb_layers(paths, expert_id, category) {
        for path in iter_text_files(&root) {
            let Ok(text) = fs::read_to_string(&path) else { continue };
            let rel = path
                .strip_prefix(&kb)
                .or_else(|_| path.strip_prefix(&paths.kb_root))
                .map(|p| p.to_string_lossy().replace('\\', "/"))
                .unwrap_or_default();
            let fname = path
                .file_name()
                .and_then(|s| s.to_str())
                .unwrap_or("")
                .to_string();
            let boost = static_boost(paths, "demo_kb", &fname, &text);
            let score = score_text(paths, &q, query, &text, &fname, boost);
            if score <= 0.0 {
                continue;
            }
            hits.push(make_hit(&rel, &layer, &fname, &text, &q, query, score));
        }
    }
    sort_and_limit(hits, limit)
}

#[cfg(all(test, windows))]
mod sqlite_longpaths_tests {
    use super::*;

    #[test]
    fn sqlite_longpaths_fts_reads_the_database_without_scan_fallback() {
        let temporary = std::env::temp_dir().join(format!("civil-sqlite-fts-{}", uuid::Uuid::new_v4()));
        let mut repo = temporary.clone();
        for i in 0..10 {
            repo.push(format!("{i}-{}", "检索资料".repeat(10)));
        }
        fs::create_dir_all(repo.join("data")).unwrap();
        assert!(repo.to_string_lossy().len() > 1040);
        let mut paths = Paths::from_demo(repo.join("demo"));
        paths.repo_root = repo.clone();
        let db = crate::sqlite::open(db_path(&paths)).unwrap();
        db.execute_batch("CREATE VIRTUAL TABLE kb_fts USING fts5(kb UNINDEXED, path UNINDEXED, body);
            CREATE TABLE kb_index(kb TEXT, path TEXT, layer TEXT, category TEXT, expert_id TEXT, boost REAL);
            CREATE TABLE kb_chunks(kb TEXT, path TEXT, seq INTEGER, body TEXT);
            INSERT INTO kb_fts VALUES('demo_kb','company/database-only.txt','uniquesqliteneedle persisted text');
            INSERT INTO kb_index VALUES('demo_kb','company/database-only.txt','company','','',0);
            INSERT INTO kb_chunks VALUES('demo_kb','company/database-only.txt',0,'uniquesqliteneedle persisted text');").unwrap();
        drop(db);
        assert!(!paths.kb_root.exists(), "there must be no text file for a scan to find");
        // Call the private FTS path, not the public fallback wrapper: inability
        // to open a database must fail this test rather than become a scan.
        let hits = search_kb_fts(&paths, "synthetic", "synthetic", "uniquesqliteneedle", 5).unwrap();
        assert_eq!(hits.len(), 1);
        assert_eq!(hits[0].path, "company/database-only.txt");
        assert!(hits[0].snippet.contains("uniquesqliteneedle"));
        let root = temporary.canonicalize().unwrap();
        let temp_root = std::env::temp_dir().canonicalize().unwrap();
        assert_eq!(root.parent(), Some(temp_root.as_path()));
        assert!(root.file_name().unwrap().to_string_lossy().starts_with("civil-sqlite-fts-"));
        fs::remove_dir_all(root).unwrap();
    }
}
