use super::{Result, RuntimeError};
use std::path::{Component, Path, PathBuf};

/// Explicit application-layer scope, not an OS sandbox. Recheck immediately
/// before opening/replacing a file; this does not prevent hostile filesystem races.
#[derive(Debug, Clone)]
pub struct WorkspaceContext {
    id: String,
    root: PathBuf,
    output_root: PathBuf,
}

impl WorkspaceContext {
    pub fn new(root: impl AsRef<Path>) -> Result<Self> {
        let root = root.as_ref().canonicalize()?;
        if !root.is_dir() {
            return Err(RuntimeError::ScopeDenied(
                "workspace must be a directory".into(),
            ));
        }
        let mut id = root
            .to_str()
            .ok_or_else(|| RuntimeError::ScopeDenied("workspace path must be Unicode".into()))?
            .to_owned();
        if cfg!(windows) {
            id = id.to_lowercase();
        }
        let output_root = root.join(".civil-buddy").join("out");
        validate_chain(&root, &output_root)?;
        Ok(Self {
            id,
            root,
            output_root,
        })
    }
    pub fn id(&self) -> &str {
        &self.id
    }
    pub fn root(&self) -> &Path {
        &self.root
    }
    pub fn output_root(&self) -> &Path {
        &self.output_root
    }

    /// Relative to the workspace root; only existing regular files can be read.
    pub fn resolve_read(&self, relative: impl AsRef<Path>) -> Result<PathBuf> {
        validate_relative(relative.as_ref())?;
        let path = self.root.join(relative);
        validate_chain(&self.root, &path)?;
        let canonical = path.canonicalize()?;
        if !canonical.starts_with(&self.root) || !canonical.is_file() {
            return Err(RuntimeError::ScopeDenied("not a workspace file".into()));
        }
        Ok(canonical)
    }

    /// Relative to `.civil-buddy/out`; missing output directories are allowed,
    /// but this method itself does not create or write anything.
    pub fn resolve_write(&self, relative: impl AsRef<Path>) -> Result<PathBuf> {
        validate_relative(relative.as_ref())?;
        let path = self.output_root.join(relative);
        validate_chain(&self.root, &path)?;
        Ok(path)
    }
}

fn validate_relative(path: &Path) -> Result<()> {
    if path.as_os_str().is_empty() {
        return Err(RuntimeError::ScopeDenied("empty path".into()));
    }
    for component in path.components() {
        let Component::Normal(part) = component else {
            return Err(RuntimeError::ScopeDenied(
                "path must be relative without traversal".into(),
            ));
        };
        let text = part
            .to_str()
            .ok_or_else(|| RuntimeError::ScopeDenied("path must be Unicode".into()))?
            .to_lowercase();
        // Windows trims trailing dots/spaces and treats device names specially;
        // reject these aliases on every platform so checks are portable.
        let stem = text.split('.').next().unwrap_or("");
        let device = matches!(stem, "con" | "prn" | "aux" | "nul")
            || ["com", "lpt"].iter().any(|prefix| {
                stem.strip_prefix(prefix).is_some_and(|suffix| {
                    matches!(suffix, "1" | "2" | "3" | "4" | "5" | "6" | "7" | "8" | "9")
                })
            });
        if text.contains(':')
            || text.contains('\\')
            || text.ends_with(['.', ' '])
            || text
                .chars()
                .any(|ch| matches!(ch, '<' | '>' | '"' | '|' | '?' | '*'))
            || device
            || text.chars().any(char::is_control)
            || text == ".env"
            || text.starts_with(".env.")
            || text == ".git"
            || text == ".ssh"
            || [".pem", ".key", ".p12", ".pfx"]
                .iter()
                .any(|suffix| text.ends_with(suffix))
        {
            return Err(RuntimeError::ScopeDenied(
                "secret or unsupported path component".into(),
            ));
        }
    }
    Ok(())
}

fn validate_chain(root: &Path, target: &Path) -> Result<()> {
    let relative = target
        .strip_prefix(root)
        .map_err(|_| RuntimeError::ScopeDenied("path outside workspace".into()))?;
    let mut current = root.to_path_buf();
    for part in relative.components() {
        current.push(part);
        match std::fs::symlink_metadata(&current) {
            Ok(meta) => {
                let mut link = meta.file_type().is_symlink();
                #[cfg(windows)]
                {
                    use std::os::windows::fs::MetadataExt;
                    link |= meta.file_attributes() & 0x400 != 0; // junctions as well as symlinks
                }
                if link {
                    return Err(RuntimeError::ScopeDenied(
                        "links/reparse points are not allowed".into(),
                    ));
                }
                if !current.canonicalize()?.starts_with(root) {
                    return Err(RuntimeError::ScopeDenied(
                        "resolved path outside workspace".into(),
                    ));
                }
            }
            Err(err) if err.kind() == std::io::ErrorKind::NotFound => break,
            Err(err) => return Err(err.into()),
        }
    }
    Ok(())
}
