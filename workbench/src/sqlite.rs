//! SQLite connections for host-managed on-disk databases.
//!
//! This is not an interface for SQLite URI filenames or in-memory databases.
//! On Windows both a verbatim absolute path and the locking long-path VFS are
//! needed: SQLite appends journal/WAL/SHM suffixes after opening the main file.
use rusqlite::{Connection, OpenFlags, Result};
use std::path::Path;

pub fn open(path: impl AsRef<Path>) -> Result<Connection> {
    open_with_flags(path, OpenFlags::default())
}

pub fn open_with_flags(path: impl AsRef<Path>, flags: OpenFlags) -> Result<Connection> {
    let path = path.as_ref();
    if path.as_os_str().is_empty()
        || flags.contains(OpenFlags::SQLITE_OPEN_MEMORY)
        || path
            .to_str()
            .is_some_and(|name| name == ":memory:" || name.starts_with("file:"))
    {
        return Err(rusqlite::Error::InvalidPath(path.to_owned()));
    }
    #[cfg(windows)]
    {
        let path = verbatim_disk_path(path)?;
        Connection::open_with_flags_and_vfs(path, flags, "win32-longpath")
    }
    #[cfg(not(windows))]
    {
        Connection::open_with_flags(path, flags)
    }
}

#[cfg(windows)]
fn verbatim_disk_path(path: &Path) -> Result<std::path::PathBuf> {
    use std::path::{Component, Prefix};

    let name = path
        .file_name()
        .ok_or_else(|| rusqlite::Error::InvalidPath(path.to_owned()))?;
    let parent = path
        .parent()
        .filter(|parent| !parent.as_os_str().is_empty())
        .unwrap_or_else(|| Path::new("."));
    // The callers create the parent first. Canonicalizing that parent also
    // supports new database files, while preserving normal/UNC disk identity.
    let parent = parent.canonicalize().map_err(|error| {
        rusqlite::Error::SqliteFailure(
            rusqlite::ffi::Error::new(rusqlite::ffi::SQLITE_CANTOPEN),
            Some(format!("cannot resolve SQLite parent directory: {error}")),
        )
    })?;
    match parent.components().next() {
        Some(Component::Prefix(prefix))
            if matches!(
                prefix.kind(),
                Prefix::VerbatimDisk(_) | Prefix::VerbatimUNC(_, _)
            ) =>
        {
            Ok(parent.join(name))
        }
        _ => Err(rusqlite::Error::InvalidPath(path.to_owned())),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn sqlite_host_paths_reject_uri_memory_and_empty_names() {
        for path in [
            "",
            ":memory:",
            "file:not-a-host-path?mode=memory&vfs=win32-none",
        ] {
            assert!(matches!(open(path), Err(rusqlite::Error::InvalidPath(_))));
        }
        assert!(matches!(
            open_with_flags(
                "not-created.sqlite",
                OpenFlags::default() | OpenFlags::SQLITE_OPEN_MEMORY
            ),
            Err(rusqlite::Error::InvalidPath(_))
        ));
    }
}
