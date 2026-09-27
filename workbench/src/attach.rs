//! Session uploads: save bytes, extract text, let experts read them.

use crate::config::Paths;
use flate2::read::ZlibDecoder;
use regex::Regex;
use serde_json::{json, Value};
use std::fs;
use std::io::{Cursor, Read, Write};
use std::path::{Path, PathBuf};
use uuid::Uuid;

pub const MAX_BYTES: usize = 20 * 1024 * 1024;
pub const MAX_TEXT_CHARS: usize = 200_000;
pub const MAX_FILES: usize = 12;
pub const INJECT_CHARS: usize = 60_000;

const ALLOWED_EXT: &[&str] = &["pdf", "docx", "xlsx", "txt", "md", "csv", "json", "log"];

pub fn session_dir(paths: &Paths, session: &str) -> Result<PathBuf, String> {
    let sid = sanitize_session(session)?;
    // A session's attachments live with the rest of it: <out_root>/<sid>/uploads (the Python
    // workbench moved there on 2026-09-21 and adopts the old data/uploads/<sid> on startup).
    let dir = paths.out_root.join(&sid).join("uploads");
    if !dir.exists() {
        let legacy = paths.data_dir.join("uploads").join(&sid);
        if legacy.is_dir() {
            if let Some(parent) = dir.parent() {
                let _ = fs::create_dir_all(parent);
            }
            let _ = fs::rename(&legacy, &dir);
        }
    }
    fs::create_dir_all(&dir).map_err(|e| e.to_string())?;
    Ok(dir)
}

fn sanitize_session(session: &str) -> Result<String, String> {
    let s: String = session
        .chars()
        .filter(|c| c.is_ascii_alphanumeric() || *c == '-' || *c == '_')
        .take(32)
        .collect();
    if s.len() < 4 {
        return Err("session_id 无效".into());
    }
    Ok(s)
}

fn ext_of(name: &str) -> String {
    Path::new(name)
        .extension()
        .and_then(|s| s.to_str())
        .unwrap_or("")
        .to_ascii_lowercase()
}

pub fn safe_filename(name: &str) -> String {
    let raw = Path::new(name)
        .file_name()
        .and_then(|s| s.to_str())
        .unwrap_or("upload.bin");
    let cleaned: String = raw
        .chars()
        .map(|c| {
            if c.is_ascii_alphanumeric() || matches!(c, '.' | '-' | '_' | '（' | '）') || ('\u{4e00}'..='\u{9fff}').contains(&c)
            {
                c
            } else {
                '_'
            }
        })
        .collect();
    if cleaned.is_empty() {
        "upload.bin".into()
    } else {
        cleaned.chars().take(80).collect()
    }
}

pub fn extract_text(filename: &str, bytes: &[u8]) -> Result<(String, String), String> {
    let (kind, text, _) = extract_upload(filename, bytes)?;
    Ok((kind, text))
}

pub fn extract_upload(filename: &str, bytes: &[u8]) -> Result<(String, String, String), String> {
    if bytes.len() > MAX_BYTES {
        return Err(format!("单文件不能超过 {} MB", MAX_BYTES / 1024 / 1024));
    }
    let ext = ext_of(filename);
    if !ALLOWED_EXT.contains(&ext.as_str()) {
        return Err("只接受 pdf / docx / xlsx / txt / md / csv / json".into());
    }
    let builtin = match ext.as_str() {
        "pdf" => extract_pdf(bytes),
        "docx" => extract_docx(bytes),
        "xlsx" => extract_xlsx(bytes),
        _ => Ok(String::from_utf8_lossy(bytes).into_owned()),
    };
    let got = crate::parse::extract_rich(filename, &ext, bytes, builtin)?;
    let text = collapse_ws(&got.text);
    if text.chars().count() < 8 {
        return Err("抽不出可用文字。扫描件 PDF 需要先 OCR（安装 MinerU 或另存为 Word/文本）。".into());
    }
    let cut: String = text.chars().take(MAX_TEXT_CHARS).collect();
    Ok((ext, cut, got.engine))
}

fn collapse_ws(s: &str) -> String {
    let mut out = String::with_capacity(s.len());
    let mut prev_nl = false;
    for line in s.lines() {
        let t = line.trim();
        if t.is_empty() {
            if !prev_nl && !out.is_empty() {
                out.push('\n');
                prev_nl = true;
            }
            continue;
        }
        out.push_str(t);
        out.push('\n');
        prev_nl = false;
    }
    out
}

fn xlsx_shared_strings(xml: &str) -> Vec<String> {
    let re = Regex::new(r"(?s)<t[^>]*>([^<]*)</t>").unwrap();
    re.captures_iter(xml)
        .filter_map(|c| c.get(1).map(|m| decode_xml(m.as_str())))
        .collect()
}

fn xlsx_sheet_text(xml: &str, shared: &[String]) -> Vec<String> {
    let row_re = Regex::new(r"(?s)<row\b[^>]*>(.*?)</row>").unwrap();
    let c_re = Regex::new(r#"(?s)<c\b([^>]*)>(.*?)</c>"#).unwrap();
    let v_re = Regex::new(r"(?s)<v>([^<]*)</v>").unwrap();
    let t_re = Regex::new(r"(?s)<t[^>]*>([^<]*)</t>").unwrap();
    let mut rows = Vec::new();
    for row in row_re.captures_iter(xml) {
        let body = row.get(1).map(|m| m.as_str()).unwrap_or("");
        let mut cells = Vec::new();
        for c in c_re.captures_iter(body) {
            let attrs = c.get(1).map(|m| m.as_str()).unwrap_or("");
            let inner = c.get(2).map(|m| m.as_str()).unwrap_or("");
            let is_s = attrs.contains("t=\"s\"") || attrs.contains("t='s'");
            let is_inline = attrs.contains("t=\"inlineStr\"") || attrs.contains("t='inlineStr'");
            if is_inline {
                if let Some(t) = t_re.captures(inner) {
                    cells.push(decode_xml(t.get(1).unwrap().as_str()));
                }
                continue;
            }
            if let Some(v) = v_re.captures(inner) {
                let raw = v.get(1).unwrap().as_str().trim();
                if is_s {
                    if let Ok(i) = raw.parse::<usize>() {
                        cells.push(shared.get(i).cloned().unwrap_or_default());
                    }
                } else if !raw.is_empty() {
                    cells.push(raw.to_string());
                }
            }
        }
        let line = cells.join("\t");
        if !line.trim().is_empty() {
            rows.push(line);
        }
    }
    rows
}

fn extract_xlsx(bytes: &[u8]) -> Result<String, String> {
    let mut zip = zip::ZipArchive::new(Cursor::new(bytes)).map_err(|e| format!("xlsx 不是有效压缩包：{e}"))?;
    let mut shared = Vec::new();
    if let Ok(mut f) = zip.by_name("xl/sharedStrings.xml") {
        let mut xml = String::new();
        f.read_to_string(&mut xml).map_err(|e| e.to_string())?;
        drop(f);
        shared = xlsx_shared_strings(&xml);
    }
    let mut sheet_names: Vec<String> = Vec::new();
    for i in 0..zip.len() {
        if let Ok(f) = zip.by_index(i) {
            let n = f.name().replace('\\', "/");
            if n.starts_with("xl/worksheets/") && n.ends_with(".xml") {
                sheet_names.push(n);
            }
        }
    }
    sheet_names.sort();
    let mut rows = Vec::new();
    for name in sheet_names {
        let mut xml = String::new();
        {
            let mut f = zip.by_name(&name).map_err(|e| e.to_string())?;
            f.read_to_string(&mut xml).map_err(|e| e.to_string())?;
        }
        rows.extend(xlsx_sheet_text(&xml, &shared));
    }
    let text = rows.join("\n");
    if text.chars().count() >= 8 {
        return Ok(text);
    }
    let joined = shared.join(" ");
    if joined.chars().count() >= 8 {
        return Ok(joined);
    }
    Err("xlsx 抽不出可用文字。请另存为 CSV 或把单元格改成文本。".into())
}

fn xml_escape(s: &str) -> String {
    s.replace('&', "&amp;")
        .replace('<', "&lt;")
        .replace('>', "&gt;")
        .replace('"', "&quot;")
}

/// Minimal OOXML spreadsheet for tests and civil-software interchange fixtures.
pub fn pack_minimal_xlsx(cells: &[&str]) -> Result<Vec<u8>, String> {
    let mut buf = Cursor::new(Vec::<u8>::new());
    {
        let mut z = zip::ZipWriter::new(&mut buf);
        let opt = zip::write::SimpleFileOptions::default()
            .compression_method(zip::CompressionMethod::Stored);
        z.start_file("[Content_Types].xml", opt)
            .map_err(|e| e.to_string())?;
        z.write_all(br#"<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
<Override PartName="/xl/sharedStrings.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sharedStrings+xml"/>
</Types>"#)
            .map_err(|e| e.to_string())?;
        z.start_file("_rels/.rels", opt).map_err(|e| e.to_string())?;
        z.write_all(br#"<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>"#)
            .map_err(|e| e.to_string())?;
        z.start_file("xl/_rels/workbook.xml.rels", opt)
            .map_err(|e| e.to_string())?;
        z.write_all(br#"<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/sharedStrings" Target="sharedStrings.xml"/>
</Relationships>"#)
            .map_err(|e| e.to_string())?;
        z.start_file("xl/workbook.xml", opt).map_err(|e| e.to_string())?;
        z.write_all(br#"<?xml version="1.0" encoding="UTF-8"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
<sheets><sheet name="Sheet1" sheetId="1" r:id="rId1"/></sheets>
</workbook>"#)
            .map_err(|e| e.to_string())?;
        let mut sst = format!(
            r#"<?xml version="1.0" encoding="UTF-8"?><sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" count="{}" uniqueCount="{}">"#,
            cells.len(),
            cells.len()
        );
        for c in cells {
            sst.push_str("<si><t>");
            sst.push_str(&xml_escape(c));
            sst.push_str("</t></si>");
        }
        sst.push_str("</sst>");
        z.start_file("xl/sharedStrings.xml", opt)
            .map_err(|e| e.to_string())?;
        z.write_all(sst.as_bytes()).map_err(|e| e.to_string())?;
        let mut sheet = String::from(
            r#"<?xml version="1.0" encoding="UTF-8"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData><row r="1">"#,
        );
        for (i, _) in cells.iter().enumerate() {
            let col = if i < 26 {
                (b'A' + i as u8) as char
            } else {
                'A'
            };
            sheet.push_str(&format!(r#"<c r="{col}1" t="s"><v>{i}</v></c>"#));
        }
        sheet.push_str("</row></sheetData></worksheet>");
        z.start_file("xl/worksheets/sheet1.xml", opt)
            .map_err(|e| e.to_string())?;
        z.write_all(sheet.as_bytes()).map_err(|e| e.to_string())?;
        z.finish().map_err(|e| e.to_string())?;
    }
    Ok(buf.into_inner())
}

fn extract_docx(bytes: &[u8]) -> Result<String, String> {
    let mut zip = zip::ZipArchive::new(Cursor::new(bytes)).map_err(|e| format!("docx 不是有效压缩包：{e}"))?;
    let mut file = zip
        .by_name("word/document.xml")
        .map_err(|_| "docx 缺少 word/document.xml".to_string())?;
    let mut xml = String::new();
    file.read_to_string(&mut xml).map_err(|e| e.to_string())?;
    let re = Regex::new(r"<w:t[^>]*>([^<]*)</w:t>").unwrap();
    let mut out = String::new();
    for cap in re.captures_iter(&xml) {
        if let Some(t) = cap.get(1) {
            let piece = decode_xml(t.as_str());
            if !piece.is_empty() {
                if !out.is_empty() {
                    out.push(' ');
                }
                out.push_str(&piece);
            }
        }
    }
    Ok(out)
}

fn decode_xml(s: &str) -> String {
    s.replace("&amp;", "&")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&quot;", "\"")
        .replace("&apos;", "'")
}

fn extract_pdf(bytes: &[u8]) -> Result<String, String> {
    let raw = String::from_utf8_lossy(bytes);
    let mut chunks = Vec::new();
    // uncompressed operators in the file body
    collect_pdf_strings(&raw, &mut chunks);
    // flate streams
    let data = bytes;
    let marker = b"stream";
    let end_marker = b"endstream";
    let mut i = 0;
    while i + marker.len() < data.len() {
        if let Some(rel) = find_sub(data, marker, i) {
            let mut start = rel + marker.len();
            if start < data.len() && data[start] == b'\r' {
                start += 1;
            }
            if start < data.len() && data[start] == b'\n' {
                start += 1;
            }
            if let Some(end) = find_sub(data, end_marker, start) {
                let slice = &data[start..end];
                if let Ok(plain) = inflate(slice) {
                    collect_pdf_strings(&plain, &mut chunks);
                }
                i = end + end_marker.len();
                continue;
            }
        }
        break;
    }
    let text = chunks.join(" ");
    if text.trim().is_empty() {
        return Err("这个 PDF 没有可抽取的文字层".into());
    }
    Ok(text)
}

fn inflate(slice: &[u8]) -> Result<String, ()> {
    let mut dec = ZlibDecoder::new(slice);
    let mut buf = Vec::new();
    dec.read_to_end(&mut buf).map_err(|_| ())?;
    Ok(String::from_utf8_lossy(&buf).into_owned())
}

fn find_sub(hay: &[u8], needle: &[u8], from: usize) -> Option<usize> {
    hay[from..]
        .windows(needle.len())
        .position(|w| w == needle)
        .map(|p| from + p)
}

fn collect_pdf_strings(src: &str, out: &mut Vec<String>) {
    let mut chars = src.chars().peekable();
    while let Some(c) = chars.next() {
        if c == '(' {
            let mut buf = String::new();
            let mut depth = 1;
            while let Some(ch) = chars.next() {
                if ch == '\\' {
                    if let Some(n) = chars.next() {
                        buf.push(match n {
                            'n' => '\n',
                            'r' => '\n',
                            't' => '\t',
                            _ => n,
                        });
                    }
                    continue;
                }
                if ch == '(' {
                    depth += 1;
                    buf.push(ch);
                    continue;
                }
                if ch == ')' {
                    depth -= 1;
                    if depth == 0 {
                        break;
                    }
                    buf.push(ch);
                    continue;
                }
                buf.push(ch);
            }
            let t = buf.trim();
            if t.chars().count() >= 2 {
                out.push(t.to_string());
            }
        }
    }
}

pub fn save_upload(paths: &Paths, session: &str, filename: &str, bytes: &[u8]) -> Result<Value, String> {
    let dir = session_dir(paths, session)?;
    let existing = list_uploads(paths, session);
    if existing.len() >= MAX_FILES {
        return Err(format!("同一会话最多 {MAX_FILES} 个附件"));
    }
    let (kind, text, engine) = extract_upload(filename, bytes)?;
    let id = Uuid::new_v4().simple().to_string()[..12].to_string();
    let name = safe_filename(filename);
    fs::write(dir.join(format!("{id}.bin")), bytes).map_err(|e| e.to_string())?;
    fs::write(dir.join(format!("{id}.txt")), &text).map_err(|e| e.to_string())?;
    let meta = json!({
        "id": id,
        "name": name,
        "kind": kind,
        "bytes": bytes.len(),
        "chars": text.chars().count(),
        "parse": engine,
    });
    fs::write(dir.join(format!("{id}.json")), meta.to_string()).map_err(|e| e.to_string())?;
    Ok(meta)
}

/// ux(round21)：从本会话的上传件里挑一个**表格**，返回一个 load_table 能吃的路径。
///
/// 上传件是按 `<id>.bin` 存的（原始字节，无扩展名），而 Python 侧的 `load_table`
/// 按后缀分发 —— 直接把 `.bin` 交过去会被判 unsupported。这里按存下来的原文件名
/// 取扩展名，惰性物化一份 `<id><ext>` 再返回，避免改动既有的 save_upload 布局。
///
/// 取最后一个（list_uploads 按 name 排序），只认 xlsx/xlsm/csv/tsv。
pub fn latest_table_upload(paths: &Paths, session: &str) -> Option<(PathBuf, String)> {
    let dir = session_dir(paths, session).ok()?;
    let mut hit: Option<(String, String, String)> = None; // (id, name, ext)
    for m in list_uploads(paths, session) {
        let id = m.get("id").and_then(|v| v.as_str()).unwrap_or("").to_string();
        let name = m.get("name").and_then(|v| v.as_str()).unwrap_or("").to_string();
        if id.is_empty() || name.is_empty() {
            continue;
        }
        let lower = name.to_ascii_lowercase();
        let ext = [".xlsx", ".xlsm", ".csv", ".tsv"]
            .iter()
            .find(|e| lower.ends_with(*e))
            .map(|e| e.to_string());
        if let Some(ext) = ext {
            hit = Some((id, name, ext));
        }
    }
    let (id, name, ext) = hit?;
    let bin = dir.join(format!("{id}.bin"));
    if !bin.is_file() {
        return None;
    }
    let typed = dir.join(format!("{id}{ext}"));
    if !typed.is_file() {
        let bytes = fs::read(&bin).ok()?;
        fs::write(&typed, bytes).ok()?;
    }
    Some((typed, name))
}

pub fn list_uploads(paths: &Paths, session: &str) -> Vec<Value> {
    let Ok(dir) = session_dir(paths, session) else {
        return vec![];
    };
    let Ok(rd) = fs::read_dir(&dir) else {
        return vec![];
    };
    let mut out = Vec::new();
    for ent in rd.flatten() {
        let p = ent.path();
        if p.extension().and_then(|s| s.to_str()) != Some("json") {
            continue;
        }
        if let Ok(raw) = fs::read_to_string(&p) {
            if let Ok(v) = serde_json::from_str::<Value>(&raw) {
                out.push(v);
            }
        }
    }
    out.sort_by(|a, b| a["name"].as_str().cmp(&b["name"].as_str()));
    out
}

pub fn read_upload(paths: &Paths, session: &str, id: &str, offset: usize, limit: usize) -> Result<String, String> {
    let sid = id
        .chars()
        .filter(|c| c.is_ascii_alphanumeric())
        .take(16)
        .collect::<String>();
    if sid.is_empty() {
        return Err("附件 id 无效".into());
    }
    let dir = session_dir(paths, session)?;
    let text = fs::read_to_string(dir.join(format!("{sid}.txt"))).map_err(|_| "附件不存在".to_string())?;
    let take = if limit == 0 { 8000 } else { limit.min(20_000) };
    let skipped: String = text.chars().skip(offset).take(take).collect();
    let more = text.chars().count().saturating_sub(offset + skipped.chars().count());
    let meta = fs::read_to_string(dir.join(format!("{sid}.json"))).unwrap_or_default();
    let name = serde_json::from_str::<Value>(&meta)
        .ok()
        .and_then(|v| v.get("name").and_then(|x| x.as_str()).map(|s| s.to_string()))
        .unwrap_or_else(|| sid.clone());
    Ok(format!(
        "【用户上传：{name}】offset={offset} 本段{}字 剩余约{more}字\n\n{skipped}",
        skipped.chars().count()
    ))
}

/// A document fetched by its URL, saved the way an uploaded one is (same caps, same extraction, same records).
pub fn import_url(paths: &Paths, session: &str, url: &str) -> Result<Vec<Value>, String> {
    let (name, bytes) = crate::websearch::run_blocking(|| crate::websearch::fetch_document(url.trim()))?;
    Ok(vec![save_upload(paths, session, &name, &bytes)?])
}

/// The http(s) addresses a message holds, in order, each once: up to the first blank or the first mark that ends an
/// address in running Chinese text ("…见 https://example.org/a.pdf，请解析").
pub fn addresses_in(message: &str) -> Vec<String> {
    let mut out: Vec<String> = Vec::new();
    let lower = message.to_lowercase();
    let mut from = 0usize;
    while let Some(at) = lower[from..].find("http") {
        let start = from + at;
        let rest = &message[start..];
        let low = &lower[start..];
        if !(low.starts_with("http://") || low.starts_with("https://")) {
            from = start + 4;
            continue;
        }
        let end = rest
            .char_indices()
            .find(|(_, c)| c.is_whitespace() || "<>\"'，。；、（）()【】".contains(*c))
            .map(|(i, _)| i)
            .unwrap_or(rest.len());
        let url = rest[..end].trim_end_matches(|c: char| ".,;:!?".contains(c)).to_string();
        if url.len() > 10 && !out.contains(&url) {
            out.push(url);
        }
        from = start + end.max(4);
    }
    out
}

/// Addresses typed into the message of a task turn ("解析这份招标文件 https://…"): fetched the way the 网址 button
/// fetches them, at most two a turn. Returns the new attachment ids and a note each for the person - a failure is a
/// note too ("请下载后用附件上传"), never a silent skip.
pub fn import_addresses(paths: &Paths, session: &str, message: &str) -> (Vec<String>, Vec<String>) {
    let (mut ids, mut notes) = (Vec::new(), Vec::new());
    for url in addresses_in(message).into_iter().take(2) {
        match import_url(paths, session, &url) {
            Ok(files) => {
                for f in files {
                    if let Some(id) = f.get("id").and_then(|v| v.as_str()) {
                        ids.push(id.to_string());
                        let name = f.get("name").and_then(|v| v.as_str()).unwrap_or("文件");
                        notes.push(format!("已从网址取回「{name}」并作为本轮附件。"));
                    }
                }
            }
            Err(e) => notes.push(format!("网址没有取到（{e}）：请下载后用「附件」上传。")),
        }
    }
    (ids, notes)
}

pub fn import_local(paths: &Paths, session: &str, raw: &str) -> Result<Vec<Value>, String> {
    let target = allow_local_path(paths, raw)?;
    if target.is_dir() {
        let mut files: Vec<PathBuf> = fs::read_dir(&target)
            .map_err(|e| e.to_string())?
            .flatten()
            .map(|e| e.path())
            .filter(|p| p.is_file() && ALLOWED_EXT.contains(&ext_of(&p.to_string_lossy()).as_str()))
            .collect();
        files.sort();
        files.truncate(8);
        if files.is_empty() {
            return Err("这个文件夹里没有可抽文字的 pdf/docx/xlsx/txt/md/csv".into());
        }
        let mut out = Vec::new();
        for f in files {
            // Recheck every child; a permitted directory can contain a link to
            // another workspace, which must not become a copied attachment.
            let f = allow_local_path(paths, &f.to_string_lossy())?;
            let name = f
                .file_name()
                .and_then(|s| s.to_str())
                .unwrap_or("file");
            let bytes = fs::read(&f).map_err(|e| format!("{}: {e}", f.display()))?;
            out.push(save_upload(paths, session, name, &bytes)?);
        }
        return Ok(out);
    }
    if !target.is_file() {
        return Err("路径不是文件或文件夹".into());
    }
    let name = target
        .file_name()
        .and_then(|s| s.to_str())
        .unwrap_or("file");
    let bytes = fs::read(&target).map_err(|e| e.to_string())?;
    Ok(vec![save_upload(paths, session, name, &bytes)?])
}

pub fn allow_local_path(_paths: &Paths, raw: &str) -> Result<PathBuf, String> {
    let trimmed = raw.trim().trim_matches('"').trim();
    if trimmed.is_empty() {
        return Err("请给出本机完整路径，例如桌面上的招标文件。禁止把 D:\\layout 当缺省作业根。".into());
    }
    let p = PathBuf::from(trimmed);
    crate::product::auth::InstanceAuth::from_env()?.authorize_local_path(&p)?;
    let canon = if p.exists() {
        p.canonicalize().unwrap_or(p)
    } else {
        return Err(format!("找不到文件：{trimmed}"));
    };
    let display = canon.to_string_lossy();
    let lower = display.to_ascii_lowercase().replace('/', "\\");
    if lower.contains("d:\\layout") {
        return Err("禁止把 D:\\layout 当作业根。请改成本项目招标文件的完整路径。".into());
    }
    if lower.contains("\\windows\\")
        || lower.contains("\\program files")
        || lower.contains("\\$recycle.bin")
    {
        return Err("拒绝读取系统目录".into());
    }
    Ok(canon)
}

pub fn bundle_for_prompt(paths: &Paths, session: &str, ids: &[String], user_msg: &str) -> String {
    if ids.is_empty() {
        return user_msg.to_string();
    }
    let mut parts = Vec::new();
    let mut used = 0usize;
    for id in ids {
        match read_upload(paths, session, id, 0, 20_000) {
            Ok(t) => {
                let room = INJECT_CHARS.saturating_sub(used);
                if room < 80 {
                    parts.push(format!("（还有附件 {id} 未贴全文，请用 read_attachment）"));
                    continue;
                }
                let cut: String = t.chars().take(room).collect();
                used += cut.chars().count();
                parts.push(cut);
            }
            Err(e) => parts.push(format!("附件 {id}：{e}")),
        }
    }
    format!(
        "{}\n\n---\n用户说：\n{}",
        parts.join("\n\n"),
        user_msg
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn extracts_plain_text() {
        let (kind, text) = extract_text("招标.txt", "评分点 技术标 40 分\n必须编制临边专项".as_bytes()).unwrap();
        assert_eq!(kind, "txt");
        assert!(text.contains("技术标"));
    }

    #[test]
    fn rejects_exe() {
        assert!(extract_text("x.exe", b"MZ").is_err());
    }

    #[test]
    fn addresses_typed_into_a_message_are_found_once_each_without_the_marks_around_them() {
        let found = addresses_in("解析这份招标文件 https://example.org/notice/a.pdf，另见（HTTP://example.org/b.docx）。再说一遍 https://example.org/notice/a.pdf");
        assert_eq!(found, vec!["https://example.org/notice/a.pdf".to_string(), "HTTP://example.org/b.docx".to_string()]);
        assert!(addresses_in("这个 httpd 配置怎么写？http 和 https 有什么区别").is_empty());
    }

    #[test]
    fn an_address_that_points_inward_is_a_note_not_an_attachment() {
        let paths = Paths::detect();      // refused before anything is written
        let (ids, notes) = import_addresses(&paths, "s1", "解析招标 http://127.0.0.1:9/a.pdf");
        assert!(ids.is_empty());
        assert_eq!(notes.len(), 1);
        assert!(notes[0].contains("请下载后用「附件」上传"), "{}", notes[0]);
    }

    #[test]
    fn extracts_xlsx_shared_string() {
        let bytes = pack_minimal_xlsx(&["C30临边梁 C-LN-01", "12"]).expect("xlsx");
        let (kind, text) = extract_text("清单.xlsx", &bytes).unwrap();
        assert_eq!(kind, "xlsx");
        assert!(text.contains("C30临边梁 C-LN-01"), "{text}");
    }
}
