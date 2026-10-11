//! Bounded source discovery. Inventory purposes never grant execution authority.
use crate::runtime_core::WorkspaceContext;
use serde::Serialize;
use std::{fs, path::Path};

pub const MAX_FILES: usize = 500;
pub const MAX_DIRECTORY_DEPTH: usize = 5;
pub const MAX_SCANNED_ENTRIES: usize = 10_000;

#[derive(Clone, Copy, Debug, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum FileListPurpose {
    Agent,
    ProjectControl,
}

impl FileListPurpose {
    fn supports(self, path: &Path) -> bool {
        let ext = path.extension().and_then(|v| v.to_str()).unwrap_or("").to_ascii_lowercase();
        matches!(ext.as_str(), "pdf" | "xlsx" | "docx" | "txt" | "md" | "csv" | "json")
            || matches!(self, Self::ProjectControl) && matches!(ext.as_str(), "ifc" | "dxf")
    }
}

#[derive(Debug, Serialize)]
pub struct ListedFile {
    pub path: String,
    pub name: String,
    pub size: u64,
}

#[derive(Debug, Serialize)]
pub struct ListingLimits {
    pub max_files: usize,
    pub max_directory_depth: usize,
    pub max_scanned_entries: usize,
}

#[derive(Debug, Serialize)]
pub struct FileListing {
    pub schema_version: u8,
    pub purpose: FileListPurpose,
    pub files: Vec<ListedFile>,
    pub returned_count: usize,
    pub complete: bool,
    pub limits: ListingLimits,
    pub file_limit_reached: bool,
    pub depth_limit_reached: bool,
    pub scan_limit_reached: bool,
    pub incomplete_reasons: Vec<&'static str>,
}

struct Scan<'a> {
    workspace: &'a WorkspaceContext,
    purpose: FileListPurpose,
    files: Vec<ListedFile>,
    scanned_entries: usize,
    file_limit: bool,
    depth_limit: bool,
    scan_limit: bool,
}

impl Scan<'_> {
    fn directory(&mut self, directory: &Path, depth: usize) -> Result<(), String> {
        let mut entries = Vec::new();
        for entry in fs::read_dir(directory).map_err(|_| "Project folder could not be scanned; check access and refresh")? {
            let entry = entry.map_err(|_| "Project folder entry could not be read; refresh the file list")?;
            if self.scanned_entries == MAX_SCANNED_ENTRIES {
                // Never process an arbitrary unsorted prefix of this directory.
                self.scan_limit = true;
                return Ok(());
            }
            self.scanned_entries += 1;
            let name = entry.file_name().into_string().map_err(|_| "A project filename is not valid Unicode")?;
            entries.push((name, entry));
        }
        entries.sort_by(|a, b| a.0.as_bytes().cmp(b.0.as_bytes()));
        for (name, entry) in entries {
            if name.starts_with('.') { continue; }
            let path = entry.path();
            let kind = entry.file_type().map_err(|_| "Project file metadata could not be read; refresh the file list")?;
            if kind.is_symlink() { continue; }
            if kind.is_dir() {
                #[cfg(windows)]
                {
                    use std::os::windows::fs::MetadataExt;
                    if entry.metadata().map_err(|_| "Project directory metadata could not be read")?.file_attributes() & 0x400 != 0 {
                        continue;
                    }
                }
                if !path.canonicalize().map_err(|_| "Project directory could not be resolved")?.starts_with(self.workspace.root()) {
                    continue;
                }
                if depth == MAX_DIRECTORY_DEPTH { self.depth_limit = true; continue; }
                self.directory(&path, depth + 1)?;
                if self.file_limit || self.scan_limit { break; }
            } else if kind.is_file() && self.purpose.supports(&path) {
                let relative = path.strip_prefix(self.workspace.root()).map_err(|_| "Project path escaped its workspace")?;
                if self.workspace.resolve_read(relative).is_err() { continue; }
                let relative = relative.to_str().ok_or("A project source path is not valid Unicode")?.replace('\\', "/");
                self.files.push(ListedFile { path: relative, name, size: entry.metadata().map_err(|_| "Project file size could not be read")?.len() });
                if self.files.len() == MAX_FILES { self.file_limit = true; break; }
            }
        }
        Ok(())
    }
}

pub fn list_files(workspace: &WorkspaceContext, purpose: FileListPurpose) -> Result<FileListing, String> {
    let mut scan = Scan { workspace, purpose, files: Vec::new(), scanned_entries: 0, file_limit: false, depth_limit: false, scan_limit: false };
    scan.directory(workspace.root(), 0)?;
    scan.files.sort_by(|a, b| a.path.as_bytes().cmp(b.path.as_bytes()));
    let mut reasons = Vec::new();
    if scan.file_limit { reasons.push("file_limit"); }
    if scan.depth_limit { reasons.push("depth_limit"); }
    if scan.scan_limit { reasons.push("scan_limit"); }
    Ok(FileListing { schema_version: 1, purpose, returned_count: scan.files.len(), files: scan.files,
        complete: reasons.is_empty(), limits: ListingLimits { max_files: MAX_FILES, max_directory_depth: MAX_DIRECTORY_DEPTH, max_scanned_entries: MAX_SCANNED_ENTRIES },
        file_limit_reached: scan.file_limit, depth_limit_reached: scan.depth_limit, scan_limit_reached: scan.scan_limit, incomplete_reasons: reasons })
}

#[cfg(test)]
mod tests {
    use super::*;

    struct Fixture(std::path::PathBuf);
    impl Fixture {
        fn new() -> Self {
            let dir = std::env::temp_dir().join(format!("civil-file-list-test-{}", uuid::Uuid::new_v4().simple()));
            fs::create_dir_all(&dir).unwrap(); Self(dir)
        }
        fn put(&self, relative: &str) {
            let path = self.0.join(relative); fs::create_dir_all(path.parent().unwrap()).unwrap(); fs::write(path, b"synthetic source").unwrap();
        }
        fn workspace(&self) -> WorkspaceContext { WorkspaceContext::new(&self.0).unwrap() }
    }
    impl Drop for Fixture { fn drop(&mut self) { let _ = fs::remove_dir_all(&self.0); } }

    #[test]
    fn purpose_separation_keeps_agent_formats_and_excludes_hidden_sources() {
        let fixture = Fixture::new();
        for path in ["source.md", "models/设计.IFC", "models/detail.dXf", "private.exe", ".env", ".hidden/secret.md"] { fixture.put(path); }
        let ws = fixture.workspace();
        let agent = list_files(&ws, FileListPurpose::Agent).unwrap();
        assert!(agent.complete); assert_eq!(agent.files.iter().map(|v| v.path.as_str()).collect::<Vec<_>>(), ["source.md"]);
        let project = list_files(&ws, FileListPurpose::ProjectControl).unwrap();
        assert!(project.complete); assert_eq!(project.returned_count, 3);
        assert_eq!(project.files.iter().map(|v| v.path.as_str()).collect::<Vec<_>>(), ["models/detail.dXf", "models/设计.IFC", "source.md"]);
    }

    #[test]
    fn file_cap_is_deterministic_and_not_an_unproven_complete_inventory() {
        for count in [499, 500, 501] {
            let left = Fixture::new(); let right = Fixture::new();
            for index in 0..count { left.put(&format!("{index:04}.md")); }
            for index in (0..count).rev() { right.put(&format!("{index:04}.md")); }
            let left = list_files(&left.workspace(), FileListPurpose::Agent).unwrap();
            let right = list_files(&right.workspace(), FileListPurpose::Agent).unwrap();
            assert_eq!(left.files.iter().map(|v| &v.path).collect::<Vec<_>>(), right.files.iter().map(|v| &v.path).collect::<Vec<_>>());
            assert_eq!(left.returned_count, count.min(500)); assert_eq!(left.complete, count < 500);
            assert_eq!(left.file_limit_reached, count >= 500);
            assert_eq!(left.incomplete_reasons, if count >= 500 { vec!["file_limit"] } else { vec![] });
        }
    }

    #[test]
    fn depth_limit_is_reported_without_traversing_a_deeper_source() {
        let fixture = Fixture::new(); fixture.put("a/b/c/d/e/visible.md"); fixture.put("a/b/c/d/e/f/omitted.md");
        let report = list_files(&fixture.workspace(), FileListPurpose::Agent).unwrap();
        assert!(!report.complete); assert!(report.depth_limit_reached);
        assert_eq!(report.files.iter().map(|v| v.path.as_str()).collect::<Vec<_>>(), ["a/b/c/d/e/visible.md"]);
        assert_eq!(report.incomplete_reasons, ["depth_limit"]);
    }

    #[test]
    fn oversized_directory_is_not_processed_as_an_arbitrary_prefix() {
        let fixture = Fixture::new();
        for index in 0..=MAX_SCANNED_ENTRIES { fixture.put(&format!("{index:05}.ignored")); }
        fixture.put("first.md");
        let report = list_files(&fixture.workspace(), FileListPurpose::Agent).unwrap();
        assert!(!report.complete); assert!(report.scan_limit_reached); assert!(report.files.is_empty());
        assert_eq!(report.incomplete_reasons, ["scan_limit"]);
    }

    #[cfg(unix)]
    #[test]
    fn source_and_directory_symlinks_are_not_followed() {
        let fixture = Fixture::new(); let outside = Fixture::new(); outside.put("secret.md");
        std::os::unix::fs::symlink(outside.0.join("secret.md"), fixture.0.join("linked.md")).unwrap();
        std::os::unix::fs::symlink(&outside.0, fixture.0.join("linked-dir")).unwrap();
        let report = list_files(&fixture.workspace(), FileListPurpose::Agent).unwrap();
        assert!(report.complete); assert!(report.files.is_empty());
    }
}
