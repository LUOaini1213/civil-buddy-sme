//! Untrusted business handoffs are validated as data, never restored as state.
use super::*;
use axum::extract::Multipart;
use std::{
    collections::{BTreeMap, BTreeSet},
    io::Cursor,
};

const MAX_EXPANDED: u64 = MAX_PACKAGE;
// Match schema-1 export limits: long descriptions may occupy most of a
// handoff. The shared expanded-size ceiling bounds all members together.
const MAX_MANIFEST: u64 = MAX_PACKAGE;
const MAX_AUDIT: u64 = MAX_PACKAGE;

pub(super) fn router() -> Router<Arc<ProductState>> {
    Router::new()
        .route("/api/project-control/package/preview", post(preview))
        .route("/api/project-control/package/import", post(commit))
        .layer(DefaultBodyLimit::max(MAX_PACKAGE as usize + 64 * 1024))
}

struct Upload {
    workspace: String,
    bytes: Vec<u8>,
    expected: Option<String>,
    write: bool,
}
async fn upload(mut multipart: Multipart, commit: bool) -> Result<Upload, Error> {
    let mut fields = BTreeMap::new();
    let mut bytes = None;
    while let Some(mut field) = multipart
        .next_field()
        .await
        .map_err(|_| bad("Invalid or oversized handoff upload"))?
    {
        let name = field
            .name()
            .ok_or_else(|| bad("Handoff fields must be named"))?
            .to_owned();
        if name == "file" {
            if bytes.is_some() {
                return Err(bad("Duplicate handoff file"));
            }
            let mut content = Vec::new();
            while let Some(chunk) = field
                .chunk()
                .await
                .map_err(|_| bad("Invalid or oversized handoff upload"))?
            {
                if content.len().saturating_add(chunk.len()) > MAX_PACKAGE as usize {
                    return Err(package_limit());
                }
                content.extend_from_slice(&chunk);
            }
            bytes = Some(content);
        } else {
            if !matches!(
                name.as_str(),
                "workspace" | "expected_sha256" | "allow_workspace_write"
            ) || (!commit && name != "workspace")
                || fields.contains_key(&name)
            {
                return Err(bad("Unknown or duplicate handoff field"));
            }
            let mut value = Vec::new();
            while let Some(chunk) = field
                .chunk()
                .await
                .map_err(|_| bad("Invalid handoff field"))?
            {
                if value.len().saturating_add(chunk.len()) > 128 {
                    return Err(bad("Handoff field is too long"));
                }
                value.extend_from_slice(&chunk);
            }
            fields.insert(
                name,
                String::from_utf8(value).map_err(|_| bad("Handoff fields must be UTF-8"))?,
            );
        }
    }
    let workspace = fields
        .remove("workspace")
        .ok_or_else(|| bad("Target workspace is required"))?;
    valid_id(&workspace)?;
    let expected = fields.remove("expected_sha256");
    if commit {
        valid_hash(
            expected
                .as_deref()
                .ok_or_else(|| bad("Preview hash is required"))?,
        )?;
    }
    let write = fields.remove("allow_workspace_write").as_deref() == Some("true");
    Ok(Upload {
        workspace,
        bytes: bytes.ok_or_else(|| bad("Handoff ZIP is required"))?,
        expected,
        write,
    })
}

async fn preview(
    State(st): State<Arc<ProductState>>,
    multipart: Multipart,
) -> Result<Json<Value>, Error> {
    let upload = upload(multipart, false).await?;
    blocking(move || {
        st.workspace(&upload.workspace).map_err(bad)?;
        let package = validate(&upload.bytes)?;
        let db = st.index.lock().map_err(internal)?;
        check_capacity(&db, &upload.workspace, &package)?;
        Ok(Json(package.preview(&upload.workspace)))
    })
    .await
}

async fn commit(
    State(st): State<Arc<ProductState>>,
    multipart: Multipart,
) -> Result<Json<Value>, Error> {
    let upload = upload(multipart, true).await?;
    blocking(move || import_sync(&st, upload).map(Json)).await
}

fn valid_id(value: &str) -> Result<(), Error> {
    if value.len() != 32 || !value.bytes().all(|c| c.is_ascii_hexdigit()) {
        return Err(bad("Invalid handoff identifier"));
    }
    Ok(())
}
fn valid_hash(value: &str) -> Result<(), Error> {
    if value.len() != 64
        || !value
            .bytes()
            .all(|c| c.is_ascii_digit() || (b'a'..=b'f').contains(&c))
    {
        return Err(bad("Invalid handoff SHA-256"));
    }
    Ok(())
}
fn object<'a>(v: &'a Value, allowed: &[&str]) -> Result<&'a serde_json::Map<String, Value>, Error> {
    let obj = v
        .as_object()
        .ok_or_else(|| bad("Handoff record must be an object"))?;
    if obj.keys().any(|k| !allowed.contains(&k.as_str())) {
        return Err(bad("Unknown handoff record field"));
    }
    Ok(obj)
}
fn string<'a>(v: &'a Value, key: &str, max: usize, empty: bool) -> Result<&'a str, Error> {
    let s = v[key]
        .as_str()
        .ok_or_else(|| bad("Invalid handoff text field"))?;
    if (!empty && s.trim().is_empty())
        || s.chars().count() > max
        || s.chars().any(|c| c.is_control() && c != '\n' && c != '\t')
    {
        return Err(bad("Invalid handoff text field"));
    }
    Ok(s)
}
fn date(v: &Value, key: &str) -> Result<(), Error> {
    let s = string(v, key, 64, false)?;
    chrono::DateTime::parse_from_rfc3339(s).map_err(|_| bad("Invalid handoff timestamp"))?;
    Ok(())
}
fn integer(v: &Value, key: &str, min: u64, max: u64) -> Result<u64, Error> {
    v[key]
        .as_u64()
        .filter(|n| *n >= min && *n <= max)
        .ok_or_else(|| bad("Invalid handoff count or revision"))
}
fn boolean(v: &Value, key: &str) -> Result<bool, Error> {
    v[key]
        .as_bool()
        .ok_or_else(|| bad("Invalid handoff boolean"))
}
fn actor(v: &Value, key: &str) -> Result<(), Error> {
    let s = string(v, key, 64, false)?;
    if !s
        .bytes()
        .all(|b| b.is_ascii_alphanumeric() || b"_-".contains(&b))
    {
        return Err(bad("Invalid handoff actor"));
    }
    Ok(())
}
fn path(value: &str) -> Result<(), Error> {
    if value.len() > 4096
        || value.split('/').any(invalid_package_part)
        || sensitive_source_name(value)
    {
        return Err(bad("Unsafe or sensitive handoff path"));
    }
    Ok(())
}

// Value's normal deserializer accepts duplicate object keys. Reject them at
// every depth so an independent reader cannot interpret a different manifest.
struct Unique(Value);
impl<'de> serde::Deserialize<'de> for Unique {
    fn deserialize<D: serde::Deserializer<'de>>(d: D) -> Result<Self, D::Error> {
        struct Visitor;
        impl<'de> serde::de::Visitor<'de> for Visitor {
            type Value = Unique;
            fn expecting(&self, f: &mut std::fmt::Formatter) -> std::fmt::Result {
                f.write_str("JSON with unique object keys")
            }
            fn visit_bool<E: serde::de::Error>(self, v: bool) -> Result<Unique, E> {
                Ok(Unique(json!(v)))
            }
            fn visit_i64<E: serde::de::Error>(self, v: i64) -> Result<Unique, E> {
                Ok(Unique(json!(v)))
            }
            fn visit_u64<E: serde::de::Error>(self, v: u64) -> Result<Unique, E> {
                Ok(Unique(json!(v)))
            }
            fn visit_f64<E: serde::de::Error>(self, v: f64) -> Result<Unique, E> {
                Ok(Unique(json!(v)))
            }
            fn visit_str<E: serde::de::Error>(self, v: &str) -> Result<Unique, E> {
                Ok(Unique(json!(v)))
            }
            fn visit_string<E: serde::de::Error>(self, v: String) -> Result<Unique, E> {
                Ok(Unique(json!(v)))
            }
            fn visit_none<E: serde::de::Error>(self) -> Result<Unique, E> {
                Ok(Unique(Value::Null))
            }
            fn visit_unit<E: serde::de::Error>(self) -> Result<Unique, E> {
                Ok(Unique(Value::Null))
            }
            fn visit_seq<A: serde::de::SeqAccess<'de>>(self, mut a: A) -> Result<Unique, A::Error> {
                let mut values = Vec::new();
                while let Some(v) = a.next_element::<Unique>()? {
                    values.push(v.0);
                }
                Ok(Unique(Value::Array(values)))
            }
            fn visit_map<A: serde::de::MapAccess<'de>>(self, mut a: A) -> Result<Unique, A::Error> {
                let mut values = serde_json::Map::new();
                while let Some(key) = a.next_key::<String>()? {
                    if values.contains_key(&key) {
                        return Err(serde::de::Error::custom("Duplicate JSON key"));
                    }
                    values.insert(key, a.next_value::<Unique>()?.0);
                }
                Ok(Unique(Value::Object(values)))
            }
        }
        d.deserialize_any(Visitor)
    }
}
fn parse(bytes: &[u8]) -> Result<Value, Error> {
    serde_json::from_slice::<Unique>(bytes)
        .map(|v| v.0)
        .map_err(|_| bad("Invalid handoff JSON or duplicate object key"))
}

struct Validated {
    hash: String,
    manifest: Value,
    files: BTreeMap<String, Vec<u8>>,
    missing: usize,
    payload_bytes: u64,
}
impl Validated {
    fn metadata_bytes(&self) -> u64 {
        (self.files["manifest.json"].len() + self.files["audit.jsonl"].len()) as u64
    }
    fn documents(&self) -> &[Value] {
        self.manifest["documents"].as_array().unwrap()
    }
    fn issues(&self) -> &[Value] {
        self.manifest["issues"].as_array().unwrap()
    }
    fn preview(&self, workspace: &str) -> Value {
        json!({"schema_version":1,"workspace":workspace,"package_sha256":self.hash,"document_count":self.documents().len(),"issue_count":self.issues().len(),"missing_evidence_count":self.missing,
            "source":{"workspace":self.manifest["workspace"],"exported_by":self.manifest["exported_by"],"cutoff_at":self.manifest["cutoff_at"]},"review_reset":true})
    }
}

struct Directory {
    count: usize,
    offset: u64,
    bytes: u64,
    footer: usize,
}
struct DirectoryEntry {
    name: String,
    size: u64,
    compressed_size: u64,
    crc: u32,
    local_offset: u64,
    flags: u16,
    method: u16,
}
fn little16(bytes: &[u8], at: usize) -> u16 {
    u16::from_le_bytes([bytes[at], bytes[at + 1]])
}
fn little32(bytes: &[u8], at: usize) -> u32 {
    u32::from_le_bytes(bytes[at..at + 4].try_into().unwrap())
}
fn directory_footer(bytes: &[u8]) -> Result<Directory, Error> {
    // Bound the declared count before ZipArchive allocates its internal index.
    // A schema-1 package has at most 102 members and is below 128 MiB; it
    // therefore never needs ZIP64 sentinel counts, split disks or offsets.
    if bytes.len() < 22 {
        return Err(bad("Invalid handoff ZIP footer"));
    }
    let footer = (bytes.len().saturating_sub(22 + 65535)..=bytes.len() - 22)
        .rev()
        .find(|&at| {
            bytes[at..].starts_with(b"PK\x05\x06")
                && at + 22 + little16(bytes, at + 20) as usize == bytes.len()
        })
        .ok_or_else(|| bad("Invalid handoff ZIP footer"))?;
    let count = little16(bytes, footer + 10) as usize;
    let offset = little32(bytes, footer + 16) as u64;
    let size = little32(bytes, footer + 12) as u64;
    if little16(bytes, footer + 4) != 0
        || little16(bytes, footer + 6) != 0
        || little16(bytes, footer + 8) as usize != count
        || !(2..=102).contains(&count)
        || size > MAX_PACKAGE
        || offset > MAX_PACKAGE
    {
        return Err(bad("Invalid handoff member count or split ZIP directory"));
    }
    if bytes[footer + 22..].windows(4).any(|v| v == b"PK\x05\x06") {
        return Err(bad("Ambiguous handoff ZIP footer comment"));
    }
    Ok(Directory {
        count,
        offset,
        bytes: size,
        footer,
    })
}
fn validate_directory(
    bytes: &[u8],
    directory: &Directory,
    start: u64,
    archive_offset: u64,
    index_count: usize,
) -> Result<BTreeMap<u64, DirectoryEntry>, Error> {
    let expected_start = directory
        .offset
        .checked_add(archive_offset)
        .ok_or_else(|| bad("Invalid handoff ZIP directory offset"))?;
    let end = start
        .checked_add(directory.bytes)
        .filter(|n| *n == directory.footer as u64)
        .ok_or_else(|| bad("Invalid handoff ZIP directory size"))? as usize;
    if start != expected_start || directory.count != index_count {
        return Err(bad("Duplicate, linked or unsafe handoff member"));
    }
    let mut at = usize::try_from(start).map_err(|_| bad("Invalid handoff ZIP directory offset"))?;
    let mut names = BTreeSet::new();
    let mut entries = BTreeMap::new();
    let mut count = 0;
    while at < end {
        if end - at < 46 || !bytes[at..].starts_with(b"PK\x01\x02") {
            return Err(bad("Invalid handoff ZIP central directory"));
        }
        let name_size = little16(bytes, at + 28) as usize;
        let extra = little16(bytes, at + 30) as usize;
        let comment = little16(bytes, at + 32) as usize;
        let flags = little16(bytes, at + 8);
        let method = little16(bytes, at + 10);
        if extra != 0
            || little16(bytes, at + 6) > 20
            || !matches!(method, 0 | 8)
            || flags & !(0x800 | 8 | 6) != 0
        {
            return Err(bad("Unsupported extended or encrypted ZIP metadata; use the original Civil Buddy exported package"));
        }
        let next = at
            .checked_add(46 + name_size + extra + comment)
            .filter(|n| *n <= end)
            .ok_or_else(|| bad("Invalid handoff member header size"))?;
        let name = std::str::from_utf8(&bytes[at + 46..at + 46 + name_size])
            .map_err(|_| bad("Handoff paths must be UTF-8"))?;
        path(name)?;
        if !names.insert(name.to_lowercase()) {
            return Err(bad("Duplicate, linked or unsafe handoff member"));
        }
        let size = little32(bytes, at + 24);
        let compressed_size = little32(bytes, at + 20);
        let local_offset = little32(bytes, at + 42);
        let attributes = little32(bytes, at + 38);
        let mode = attributes >> 16;
        if little16(bytes, at + 34) != 0
            || [size, compressed_size, local_offset].contains(&u32::MAX)
            || !matches!(mode & 0o170000, 0 | 0o100000)
            || attributes & (0x10 | 0x400) != 0
        {
            return Err(bad("Duplicate, linked or unsafe handoff member"));
        }
        let raw = DirectoryEntry {
            name: name.to_owned(),
            size: size as u64,
            compressed_size: compressed_size as u64,
            crc: little32(bytes, at + 16),
            local_offset: local_offset as u64,
            flags,
            method,
        };
        local_data_start(bytes, &raw, archive_offset, start)?;
        entries.insert(at as u64, raw);
        count += 1;
        if count > 102 {
            return Err(bad("Invalid handoff member count"));
        }
        at = next;
    }
    if count != directory.count || at != end {
        return Err(bad("Invalid handoff member count"));
    }
    Ok(entries)
}

fn local_data_start(
    bytes: &[u8],
    raw: &DirectoryEntry,
    archive_offset: u64,
    central_start: u64,
) -> Result<u64, Error> {
    let expected_local = archive_offset
        .checked_add(raw.local_offset)
        .ok_or_else(|| bad("Invalid handoff local header offset"))?;
    let local =
        usize::try_from(expected_local).map_err(|_| bad("Invalid handoff local header offset"))?;
    if local
        .checked_add(30)
        .is_none_or(|n| n > central_start as usize)
        || !bytes[local..].starts_with(b"PK\x03\x04")
    {
        return Err(bad("Handoff ZIP entry metadata is inconsistent"));
    }
    let name_size = little16(bytes, local + 26) as usize;
    let extra = little16(bytes, local + 28) as usize;
    if extra != 0 || little16(bytes, local + 4) > 20 {
        return Err(bad(
            "Unsupported extended ZIP local header; use the original Civil Buddy exported package",
        ));
    }
    let data_start = local
        .checked_add(30 + name_size + extra)
        .filter(|n| *n <= central_start as usize)
        .ok_or_else(|| bad("Invalid handoff local header size"))?;
    if &bytes[local + 30..local + 30 + name_size] != raw.name.as_bytes()
        || little16(bytes, local + 6) != raw.flags
        || little16(bytes, local + 8) != raw.method
        || (data_start as u64)
            .checked_add(raw.compressed_size)
            .is_none_or(|n| n > central_start)
    {
        return Err(bad("Handoff ZIP local and central headers disagree"));
    }
    if raw.flags & 8 == 0
        && (little32(bytes, local + 14) != raw.crc
            || little32(bytes, local + 18) as u64 != raw.compressed_size
            || little32(bytes, local + 22) as u64 != raw.size)
    {
        return Err(bad("Handoff ZIP local sizes or checksum disagree"));
    }
    Ok(data_start as u64)
}
fn bind_entry(
    bytes: &[u8],
    raw: &DirectoryEntry,
    entry: &zip::read::ZipFile<'_>,
    archive_offset: u64,
    central_start: u64,
) -> Result<(), Error> {
    let data_start = local_data_start(bytes, raw, archive_offset, central_start)?;
    if entry.name_raw() != raw.name.as_bytes()
        || entry.size() != raw.size
        || entry.compressed_size() != raw.compressed_size
        || entry.crc32() != raw.crc
        || entry.header_start() != raw.local_offset + archive_offset
        || entry.data_start() != data_start
    {
        return Err(bad("Handoff ZIP entry metadata is inconsistent"));
    }
    Ok(())
}

fn validate(bytes: &[u8]) -> Result<Validated, Error> {
    if bytes.len() as u64 > MAX_PACKAGE {
        return Err(package_limit());
    }
    let directory = directory_footer(bytes)?;
    // Fully validate the raw directory and local headers before the ZIP
    // library can search fallback footers or allocate its filename index.
    let headers = validate_directory(bytes, &directory, directory.offset, 0, directory.count)?;
    let mut archive =
        zip::ZipArchive::new(Cursor::new(bytes)).map_err(|_| bad("Invalid handoff ZIP"))?;
    let central_start = archive.central_directory_start();
    let archive_offset = archive.offset();
    if central_start != directory.offset || archive_offset != 0 || archive.len() != directory.count
    {
        return Err(bad(
            "Handoff ZIP index differs from its validated directory",
        ));
    }
    if archive.len() < 2 || archive.len() > 102 {
        return Err(bad("Invalid handoff member count"));
    }
    let mut files = BTreeMap::new();
    let mut aliases = BTreeSet::new();
    let mut expanded = 0u64;
    for index in 0..archive.len() {
        let mut entry = archive
            .by_index(index)
            .map_err(|_| bad("Unreadable handoff member"))?;
        let raw = headers
            .get(&entry.central_header_start())
            .ok_or_else(|| bad("Handoff ZIP entry is outside its directory"))?;
        bind_entry(bytes, raw, &entry, archive_offset, central_start)?;
        let name = std::str::from_utf8(entry.name_raw())
            .map_err(|_| bad("Handoff paths must be UTF-8"))?
            .to_owned();
        path(&name)?;
        if entry.enclosed_name().is_none()
            || entry.is_dir()
            || !aliases.insert(name.to_lowercase())
            || entry
                .unix_mode()
                .is_some_and(|m| !matches!(m & 0o170000, 0 | 0o100000))
        {
            return Err(bad("Duplicate, linked or unsafe handoff member"));
        }
        let limit = match name.as_str() {
            "manifest.json" => MAX_MANIFEST,
            "audit.jsonl" => MAX_AUDIT,
            _ if name.starts_with("sources/") => MAX_SOURCE as u64,
            _ => return Err(bad("Unknown handoff member")),
        };
        expanded = expanded
            .checked_add(entry.size())
            .ok_or_else(package_limit)?;
        if entry.size() > limit || expanded > MAX_EXPANDED {
            return Err(package_limit());
        }
        let declared = entry.size();
        let mut content = Vec::new();
        (&mut entry)
            .take(limit + 1)
            .read_to_end(&mut content)
            .map_err(|_| bad("Corrupt handoff member"))?;
        if content.len() as u64 != declared {
            return Err(bad("Handoff member size mismatch"));
        }
        files.insert(name, content);
    }
    let manifest = parse(
        files
            .get("manifest.json")
            .ok_or_else(|| bad("Handoff manifest is missing"))?,
    )?;
    object(
        &manifest,
        &[
            "schema_version",
            "export_kind",
            "workspace",
            "cutoff_at",
            "source_checks_completed_at",
            "exported_by",
            "scope",
            "issue_count",
            "unresolved_issues",
            "internal_review_complete",
            "draft",
            "status",
            "statutory_signoff",
            "notifications_enabled",
            "audit",
            "exclusions",
            "disclaimer",
            "documents",
            "issues",
        ],
    )?;
    if manifest["schema_version"] != 1
        || manifest["export_kind"] != "internal_project_handoff"
        || manifest["scope"] != "single_user_instance"
        || boolean(&manifest, "statutory_signoff")?
        || boolean(&manifest, "notifications_enabled")?
    {
        return Err(bad("Unsupported handoff schema or authority claim"));
    }
    valid_id(string(&manifest, "workspace", 32, false)?)?;
    actor(&manifest, "exported_by")?;
    date(&manifest, "cutoff_at")?;
    date(&manifest, "source_checks_completed_at")?;
    string(&manifest, "disclaimer", 2000, false)?;
    let exclusions = manifest["exclusions"]
        .as_array()
        .ok_or_else(|| bad("Invalid handoff exclusions"))?;
    if exclusions.len() > 32
        || exclusions
            .iter()
            .any(|s| s.as_str().is_none_or(|s| s.len() > 256))
    {
        return Err(bad("Invalid handoff exclusions"));
    }
    let docs = manifest["documents"]
        .as_array()
        .filter(|d| !d.is_empty() && d.len() <= 100)
        .ok_or_else(|| bad("A business handoff requires 1 to 100 documents"))?;
    let issues = manifest["issues"]
        .as_array()
        .filter(|i| i.len() <= 2000)
        .ok_or_else(|| bad("Handoff supports up to 2000 issues"))?;
    let workspace = manifest["workspace"].as_str().unwrap();
    let mut identities = BTreeMap::new();
    let mut members = BTreeSet::from(["manifest.json".to_owned(), "audit.jsonl".to_owned()]);
    let mut payload_bytes = 0;
    for doc in docs {
        object(
            doc,
            &[
                "id",
                "workspace",
                "title",
                "discipline",
                "path",
                "revision",
                "review_version",
                "revision_label",
                "sha256",
                "status",
                "updated_at",
                "actor_id",
                "review",
                "snapshot",
                "source_status",
                "source_checked_at",
                "effective_status",
                "provenance",
            ],
        )?;
        let key = string(doc, "id", 32, false)?;
        valid_id(key)?;
        if identities.insert(key, doc).is_some() || doc["workspace"] != workspace {
            return Err(bad("Duplicate or inconsistent handoff document"));
        }
        for (field, max) in [("title", 160), ("discipline", 80), ("revision_label", 80)] {
            string(doc, field, max, false)?;
        }
        actor(doc, "actor_id")?;
        date(doc, "updated_at")?;
        date(doc, "source_checked_at")?;
        integer(doc, "revision", 1, 256)?;
        integer(doc, "review_version", 0, u64::MAX)?;
        valid_hash(string(doc, "sha256", 64, false)?)?;
        if !matches!(
            string(doc, "status", 32, false)?,
            "work_in_progress" | "internally_reviewed"
        ) || !matches!(
            string(doc, "source_status", 32, false)?,
            "current" | "changed" | "unavailable"
        ) || !matches!(
            string(doc, "effective_status", 32, false)?,
            "internally_reviewed" | "review_required"
        ) {
            return Err(bad("Invalid handoff document status"));
        }
        let expected_path = package_path(doc).map_err(|_| bad("Unsafe handoff source path"))?;
        object(
            &doc["snapshot"],
            &["archive_path", "bytes", "sha256", "revision", "read_only"],
        )?;
        if doc["snapshot"]["archive_path"] != expected_path
            || doc["snapshot"]["sha256"] != doc["sha256"]
            || doc["snapshot"]["revision"] != doc["revision"]
            || doc["snapshot"]["read_only"] != true
        {
            return Err(bad("Inconsistent handoff snapshot"));
        }
        let payload = files
            .get(&expected_path)
            .ok_or_else(|| bad("Handoff snapshot is missing"))?;
        if integer(&doc["snapshot"], "bytes", 0, MAX_SOURCE as u64)? != payload.len() as u64
            || super::super::tools::sha256(payload) != doc["sha256"]
        {
            return Err(bad("Handoff snapshot digest or size mismatch"));
        }
        payload_bytes += payload.len() as u64;
        members.insert(expected_path);
        if let Some(review) = doc.get("review") {
            object(
                review,
                &[
                    "actor_id",
                    "at",
                    "notes",
                    "sha256",
                    "basis_version",
                    "statutory_signoff",
                ],
            )?;
            actor(review, "actor_id")?;
            date(review, "at")?;
            string(review, "notes", 4000, false)?;
            valid_hash(string(review, "sha256", 64, false)?)?;
            integer(review, "basis_version", 0, u64::MAX)?;
            if boolean(review, "statutory_signoff")? {
                return Err(bad("Handoff review cannot be statutory signoff"));
            }
        }
        if doc["status"] == "internally_reviewed"
            && (doc["review"]["sha256"] != doc["sha256"]
                || doc["review"]["basis_version"] != doc["review_version"])
        {
            return Err(bad("Inconsistent internal review basis"));
        }
        let effective = doc["source_status"] == "current" && doc["status"] == "internally_reviewed";
        if (doc["effective_status"] == "internally_reviewed") != effective {
            return Err(bad("Inconsistent source review status"));
        }
        validate_provenance(doc.get("provenance"))?;
    }
    if members.len() != files.len() || files.keys().any(|p| !members.contains(p)) {
        return Err(bad("Unreferenced handoff member"));
    }
    let mut issue_ids = BTreeSet::new();
    let mut missing = 0;
    let mut unresolved = 0;
    for issue in issues {
        object(
            issue,
            &[
                "id",
                "version",
                "workspace",
                "document_id",
                "document_revision",
                "source_sha256",
                "kind",
                "title",
                "description",
                "assignee",
                "due_date",
                "status",
                "resolution",
                "created_at",
                "updated_at",
                "actor_id",
                "notification_sent",
                "evidence_status",
                "provenance",
            ],
        )?;
        let key = string(issue, "id", 32, false)?;
        valid_id(key)?;
        if !issue_ids.insert(key) || issue["workspace"] != workspace {
            return Err(bad("Duplicate or inconsistent handoff issue"));
        }
        integer(issue, "version", 1, u64::MAX)?;
        let doc = identities
            .get(string(issue, "document_id", 32, false)?)
            .ok_or_else(|| bad("Handoff issue refers to an unknown document"))?;
        let rev = integer(issue, "document_revision", 0, 256)?;
        valid_hash(string(issue, "source_sha256", 64, false)?)?;
        if rev > doc["revision"].as_u64().unwrap()
            || (rev == doc["revision"] && issue["source_sha256"] != doc["sha256"])
        {
            return Err(bad("Handoff issue evidence is inconsistent"));
        }
        if rev == 0 && issue["evidence_status"] != "unavailable" {
            return Err(bad("Missing issue evidence must be explicit"));
        }
        if let Some(status) = issue.get("evidence_status") {
            if !matches!(status.as_str(), Some("current" | "unavailable")) {
                return Err(bad("Invalid evidence status"));
            }
        }
        if rev != doc["revision"] || issue["evidence_status"] == "unavailable" {
            missing += 1;
        }
        if !matches!(
            string(issue, "kind", 16, false)?,
            "rfi" | "quality" | "change" | "task"
        ) || !matches!(
            string(issue, "status", 32, false)?,
            "open" | "in_progress" | "resolved" | "review_required"
        ) {
            return Err(bad("Invalid handoff issue status or kind"));
        }
        for (field, max, empty) in [
            ("title", 160, false),
            ("description", 8000, false),
            ("assignee", 100, false),
            ("resolution", 8000, true),
        ] {
            string(issue, field, max, empty)?;
        }
        if issue["status"] == "resolved" && issue["resolution"].as_str().unwrap().trim().is_empty()
        {
            return Err(bad("Resolved issue requires a resolution"));
        }
        let due = string(issue, "due_date", 10, true)?;
        if !due.is_empty() {
            let d = chrono::NaiveDate::parse_from_str(due, "%Y-%m-%d")
                .map_err(|_| bad("Invalid handoff due date"))?;
            if d.format("%Y-%m-%d").to_string() != due {
                return Err(bad("Invalid handoff due date"));
            }
        }
        date(issue, "created_at")?;
        date(issue, "updated_at")?;
        actor(issue, "actor_id")?;
        boolean(issue, "notification_sent")?;
        validate_provenance(issue.get("provenance"))?;
        if issue["status"] != "resolved" {
            unresolved += 1;
        }
    }
    let ready = !docs.is_empty()
        && unresolved == 0
        && docs
            .iter()
            .all(|d| d["effective_status"] == "internally_reviewed");
    if integer(&manifest, "issue_count", 0, 2000)? != issues.len() as u64
        || integer(&manifest, "unresolved_issues", 0, 2000)? != unresolved
        || boolean(&manifest, "internal_review_complete")? != ready
        || boolean(&manifest, "draft")? == ready
        || manifest["status"]
            != if ready {
                "internally_reviewed"
            } else {
                "draft_not_ready"
            }
    {
        return Err(bad("Inconsistent handoff summary counts or status"));
    }
    let audit_bytes = files
        .get("audit.jsonl")
        .ok_or_else(|| bad("Handoff audit is missing"))?;
    let audit_info = &manifest["audit"];
    object(
        audit_info,
        &["archive_path", "event_count", "sha256", "complete"],
    )?;
    valid_hash(string(audit_info, "sha256", 64, false)?)?;
    if audit_info["archive_path"] != "audit.jsonl"
        || audit_info["complete"] != true
        || super::super::tools::sha256(audit_bytes) != audit_info["sha256"]
    {
        return Err(bad("Handoff audit digest mismatch"));
    }
    let expected_count = integer(audit_info, "event_count", 0, MAX_PACKAGE)?;
    if !audit_bytes.is_empty() && !audit_bytes.ends_with(b"\n") {
        return Err(bad("Incomplete handoff audit line"));
    }
    let mut count = 0;
    let mut previous = 0;
    for line in audit_bytes
        .split(|b| *b == b'\n')
        .filter(|line| !line.is_empty())
    {
        // Export adds the sequence field to an already bounded 256 KiB row.
        if line.len() > 256 * 1024 + 64 {
            return Err(bad("Handoff audit line is too large"));
        }
        let event = parse(line)?;
        // Older exporters permit arbitrary object-shaped audit details. They
        // remain inert source records; only their ordered sequence is trusted
        // for completeness, never their claimed actor or event semantics.
        if !event.is_object() {
            return Err(bad("Invalid handoff audit record"));
        }
        let sequence = integer(&event, "sequence", 1, i64::MAX as u64)?;
        if sequence <= previous {
            return Err(bad("Handoff audit sequence is not increasing"));
        }
        previous = sequence;
        count += 1;
    }
    // Empty lines are not valid events, including internal blank lines.
    if audit_bytes.windows(2).any(|b| b == b"\n\n")
        || audit_bytes.starts_with(b"\n")
        || count != expected_count
    {
        return Err(bad("Handoff audit count mismatch"));
    }
    Ok(Validated {
        hash: super::super::tools::sha256(bytes),
        manifest,
        files,
        missing,
        payload_bytes,
    })
}

// Provenance is retained as inert source data. Limit recursion and record size;
// it never supplies a local path, actor, review, revision or identifier.
fn validate_provenance(value: Option<&Value>) -> Result<(), Error> {
    if let Some(value) = value {
        if !value.is_object() || serde_json::to_vec(value).map_err(bad)?.len() > 16 * 1024 {
            return Err(bad("Invalid or oversized handoff provenance"));
        }
    }
    Ok(())
}

fn check_capacity(db: &Connection, workspace: &str, package: &Validated) -> Result<(), Error> {
    // An already committed package adds nothing, even at the current quota.
    if db
        .query_row(
            "SELECT 1 FROM project_imports WHERE workspace=?1 AND package_sha256=?2",
            params![workspace, package.hash],
            |r| r.get::<_, i64>(0),
        )
        .optional()
        .map_err(internal)?
        .is_some()
    {
        return Ok(());
    }
    let docs: i64 = db
        .query_row(
            "SELECT count(*) FROM project_documents WHERE workspace=?1",
            [workspace],
            |r| r.get(0),
        )
        .map_err(internal)?;
    let issues: i64 = db
        .query_row(
            "SELECT count(*) FROM project_issues WHERE workspace=?1",
            [workspace],
            |r| r.get(0),
        )
        .map_err(internal)?;
    let bytes: i64 = db
        .query_row(
            "SELECT (SELECT coalesce(sum(length(payload)),0) FROM project_revisions WHERE workspace=?1)+(SELECT coalesce(sum(json_extract(record,'$.source_metadata_bytes')),0) FROM project_imports WHERE workspace=?1)",
            [workspace],
            |r| r.get(0),
        )
        .map_err(internal)?;
    if docs.saturating_add(package.documents().len() as i64) > 100
        || issues.saturating_add(package.issues().len() as i64) > 2000
        || bytes
            .saturating_add(package.payload_bytes as i64)
            .saturating_add(package.metadata_bytes() as i64)
            > 512 * 1024 * 1024
    {
        return Err(conflict(
            "Target project document, issue or snapshot capacity would be exceeded",
        ));
    }
    Ok(())
}

struct NewFiles {
    ws: WorkspaceContext,
    relative: String,
    paths: Vec<String>,
    committed: bool,
}
impl NewFiles {
    fn write(&mut self, name: &str, bytes: &[u8], readonly: bool) -> Result<String, Error> {
        let relative = format!("{}/{name}", self.relative);
        let target = self.ws.resolve_write(&relative).map_err(bad)?;
        let mut opts = std::fs::OpenOptions::new();
        opts.create_new(true).write(true);
        #[cfg(unix)]
        {
            use std::os::unix::fs::OpenOptionsExt;
            opts.mode(0o600);
        }
        let mut file = opts.open(&target).map_err(internal)?;
        self.paths.push(relative.clone());
        file.write_all(bytes).map_err(internal)?;
        file.sync_all().map_err(internal)?;
        drop(file);
        let verified = self.ws.resolve_write(&relative).map_err(bad)?;
        if verified != target
            || super::super::tools::sha256(&std::fs::read(&verified).map_err(internal)?)
                != super::super::tools::sha256(bytes)
        {
            return Err(conflict("New handoff copy failed verification"));
        }
        if readonly {
            let mut p = std::fs::metadata(&verified)
                .map_err(internal)?
                .permissions();
            p.set_readonly(true);
            std::fs::set_permissions(&verified, p).map_err(internal)?;
        }
        Ok(format!(".civil-buddy/out/{relative}"))
    }
}
impl Drop for NewFiles {
    fn drop(&mut self) {
        if self.committed {
            return;
        }
        for relative in self.paths.iter().rev() {
            if let Ok(path) = self.ws.resolve_write(relative) {
                if let Ok(meta) = std::fs::symlink_metadata(&path) {
                    if meta.is_file() && !meta.file_type().is_symlink() {
                        #[cfg(windows)]
                        {
                            let mut permissions = meta.permissions();
                            permissions.set_readonly(false);
                            let _ = std::fs::set_permissions(&path, permissions);
                        }
                        let _ = std::fs::remove_file(path);
                    }
                }
            }
        }
        if let Ok(path) = self.ws.resolve_write(&self.relative) {
            let _ = std::fs::remove_dir(path);
        }
    }
}

fn create_folder(ws: &WorkspaceContext, relative: &str) -> Result<(), Error> {
    // Resolve before and after each create; never follow existing links/reparse
    // points. This is the same trusted-local-filesystem boundary as workspace.
    for part in ["handoffs", relative] {
        let target = ws.resolve_write(part).map_err(bad)?;
        let parent = target
            .parent()
            .ok_or_else(|| bad("Invalid handoff output path"))?;
        std::fs::create_dir_all(parent).map_err(internal)?;
        ws.resolve_write(part).map_err(bad)?;
        if part == "handoffs" {
            match std::fs::create_dir(&target) {
                Ok(()) => {}
                Err(e) if e.kind() == std::io::ErrorKind::AlreadyExists && target.is_dir() => {}
                Err(e) => return Err(internal(e)),
            }
        } else {
            std::fs::create_dir(&target).map_err(internal)?;
        }
        ws.resolve_write(part).map_err(bad)?;
    }
    Ok(())
}

fn import_sync(st: &ProductState, upload: Upload) -> Result<Value, Error> {
    let ws = st.workspace(&upload.workspace).map_err(bad)?;
    if !upload.write {
        return Err(err(
            StatusCode::FORBIDDEN,
            "Confirm workspace write for this handoff import",
        ));
    }
    let package = validate(&upload.bytes)?;
    if upload.expected.as_deref() != Some(package.hash.as_str()) {
        return Err(conflict(
            "Handoff changed after preview; preview the selected ZIP again",
        ));
    }
    let mut db = st.index.lock().map_err(internal)?;
    let tx = db.transaction().map_err(internal)?;
    let replay: Option<String> = tx
        .query_row(
            "SELECT record FROM project_imports WHERE workspace=?1 AND package_sha256=?2",
            params![upload.workspace, package.hash],
            |r| r.get(0),
        )
        .optional()
        .map_err(internal)?;
    if let Some(raw) = replay {
        let mut value: Value = serde_json::from_str(&raw).map_err(internal)?;
        value["replayed"] = json!(true);
        return Ok(value);
    }
    check_capacity(&tx, &upload.workspace, &package)?;
    // Reauthorize the exact registered root immediately before creating copies.
    st.auth.authorize_workspace(ws.root()).map_err(bad)?;
    let import_id = id();
    let relative = format!("handoffs/{import_id}");
    let mut copies = NewFiles {
        ws,
        relative,
        paths: Vec::new(),
        committed: false,
    };
    create_folder(&copies.ws, &copies.relative)?;
    let manifest_path = copies.write(
        "source-manifest.json",
        &package.files["manifest.json"],
        true,
    )?;
    let audit_path = copies.write("source-audit.jsonl", &package.files["audit.jsonl"], true)?;
    let mut mapping = BTreeMap::new();
    let timestamp = now();
    for original in package.documents() {
        let new_id = id();
        let old_id = original["id"].as_str().unwrap();
        mapping.insert(old_id, new_id.clone());
        let extension = original["path"]
            .as_str()
            .unwrap()
            .rsplit('.')
            .next()
            .unwrap()
            .to_ascii_lowercase();
        let payload = &package.files[original["snapshot"]["archive_path"].as_str().unwrap()];
        let path = copies.write(&format!("{new_id}.{extension}"), payload, false)?;
        let provenance = json!({"import_id":import_id,"package_sha256":package.hash,"source_workspace":package.manifest["workspace"],"source_exported_by":package.manifest["exported_by"],"source_cutoff_at":package.manifest["cutoff_at"],"source_document_id":old_id,"source_revision":original["revision"],"source_sha256":original["sha256"],"source_path":original["path"],"source_manifest":manifest_path,"source_audit":audit_path,"prior_provenance_sha256":original.get("provenance").map(|p| super::super::tools::sha256(&serde_json::to_vec(p).unwrap()))});
        let doc = json!({"id":new_id,"workspace":upload.workspace,"title":original["title"],"discipline":original["discipline"],"path":path,"revision":1,"review_version":1,"revision_label":original["revision_label"],"sha256":original["sha256"],"status":"work_in_progress","updated_at":timestamp,"actor_id":st.auth.owner(),"provenance":provenance});
        tx.execute("INSERT INTO project_revisions(workspace,document_id,revision,record,payload) VALUES(?1,?2,1,?3,?4)",params![upload.workspace,new_id,doc.to_string(),payload]).map_err(internal)?;
        tx.execute(
            "INSERT INTO project_documents(workspace,id,record) VALUES(?1,?2,?3)",
            params![upload.workspace, new_id, doc.to_string()],
        )
        .map_err(internal)?;
        audit(
            &tx,
            &upload.workspace,
            st.auth.owner(),
            "document_handoff_imported",
            &new_id,
            json!({"import_id":import_id,"package_sha256":package.hash,"source_document_id":old_id,"review_reset":true}),
        )?;
    }
    let mut issue_mapping = Vec::new();
    for original in package.issues() {
        let new_id = id();
        let original_doc = package
            .documents()
            .iter()
            .find(|d| d["id"] == original["document_id"])
            .unwrap();
        let available = original["document_revision"] == original_doc["revision"]
            && original["evidence_status"] != "unavailable";
        let provenance = json!({"import_id":import_id,"package_sha256":package.hash,"source_workspace":package.manifest["workspace"],"source_issue_id":original["id"],"source_document_id":original["document_id"],"source_document_revision":original["document_revision"],"source_sha256":original["source_sha256"],"source_status":original["status"],"source_actor_id":original["actor_id"],"source_created_at":original["created_at"],"source_updated_at":original["updated_at"],"source_manifest":manifest_path,"source_audit":audit_path,"prior_provenance_sha256":original.get("provenance").map(|p| super::super::tools::sha256(&serde_json::to_vec(p).unwrap()))});
        let status = if original["status"] == "resolved" {
            json!("review_required")
        } else {
            original["status"].clone()
        };
        let issue = json!({"id":new_id,"version":1,"workspace":upload.workspace,"document_id":mapping[original["document_id"].as_str().unwrap()],"document_revision":if available {1}else{0},"source_sha256":original["source_sha256"],"kind":original["kind"],"title":original["title"],"description":original["description"],"assignee":original["assignee"],"due_date":original["due_date"],"status":status,"resolution":original["resolution"],"created_at":timestamp,"updated_at":timestamp,"actor_id":st.auth.owner(),"notification_sent":false,"evidence_status":if available {"current"}else{"unavailable"},"provenance":provenance});
        tx.execute(
            "INSERT INTO project_issues(workspace,id,record) VALUES(?1,?2,?3)",
            params![upload.workspace, new_id, issue.to_string()],
        )
        .map_err(internal)?;
        audit(
            &tx,
            &upload.workspace,
            st.auth.owner(),
            "issue_handoff_imported",
            &new_id,
            json!({"import_id":import_id,"source_issue_id":original["id"],"evidence_status":issue["evidence_status"],"review_reset":true}),
        )?;
        issue_mapping.push(json!({"source_issue_id":original["id"],"local_issue_id":new_id,"source_document_id":original["document_id"],"source_document_revision":original["document_revision"],"source_sha256":original["source_sha256"],"local_document_id":issue["document_id"],"local_document_revision":issue["document_revision"]}));
    }
    let document_mapping:Vec<Value>=package.documents().iter().map(|d|json!({"source_document_id":d["id"],"source_revision":d["revision"],"source_sha256":d["sha256"],"local_document_id":mapping[d["id"].as_str().unwrap()],"local_revision":1})).collect();
    let receipt = json!({"schema_version":1,"workspace":upload.workspace,"import_id":import_id,"package_sha256":package.hash,"document_count":package.documents().len(),"issue_count":package.issues().len(),"missing_evidence_count":package.missing,"source_metadata_bytes":package.metadata_bytes(),"copied_file_bytes":package.metadata_bytes()+package.payload_bytes,"replayed":false,"source":{"workspace":package.manifest["workspace"],"exported_by":package.manifest["exported_by"],"cutoff_at":package.manifest["cutoff_at"],"manifest_path":manifest_path,"audit_path":audit_path},"document_mapping":document_mapping,"issue_mapping":issue_mapping});
    tx.execute(
        "INSERT INTO project_imports(workspace,package_sha256,record) VALUES(?1,?2,?3)",
        params![upload.workspace, package.hash, receipt.to_string()],
    )
    .map_err(internal)?;
    // The immutable receipt owns the full identifier mapping. Keep each local
    // audit row below the existing exporter bound even for 2,000 issues.
    audit(
        &tx,
        &upload.workspace,
        st.auth.owner(),
        "project_handoff_imported",
        &import_id,
        json!({"import_id":import_id,"package_sha256":package.hash,"document_count":package.documents().len(),"issue_count":package.issues().len(),"missing_evidence_count":package.missing,"source_manifest":manifest_path,"source_audit":audit_path,"review_reset":true}),
    )?;
    tx.commit().map_err(internal)?;
    copies.committed = true;
    Ok(receipt)
}
