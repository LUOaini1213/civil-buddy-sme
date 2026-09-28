"""Application-level sandbox: path + spawn policy.

Not a kernel jail: it holds for code that asks it. Writes and agent-initiated
opens stay inside allowed roots; .env / secret / key paths are denied; generic
spawn stays blocked. The kernel-enforced counterpart — a worker process that
confines itself with Landlock + seccomp (Linux) or Low integrity + a job object
(Windows) — is packing_assistant/runtime/os_sandbox; this layer keeps running
inside it, and is the only one that refuses to *read* secrets.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Union

GENERIC_SPAWN_KINDS = frozenset({"generic", "shell", "spawn", "arbitrary", "cmd"})
GENERIC_SPAWN_STEMS = frozenset(
    {
        "cmd",
        "cmd.exe",
        "powershell",
        "powershell.exe",
        "pwsh",
        "pwsh.exe",
        "bash",
        "sh",
        "zsh",
        "wscript",
        "wscript.exe",
        "cscript",
        "cscript.exe",
    }
)
ALLOWED_HELPER_STEMS = frozenset(
    {"mineru", "docling", "marker", "marker_single", "python", "python.exe", "py", "py.exe"}
)
ALLOWED_SCRIPT_NAMES = frozenset(
    {
        "run_packing_sidecar.py",
        "scan_forbidden_inventions.py",
        "validate.py",
    }
)
SECRET_NAMES = frozenset({".env", ".env.local", ".env.production", ".env.development"})
SECRET_SUBSTR = ("secret", "api_key", "apikey", "private_key")
SECRET_SUFFIXES = (".pem", ".key", ".p12", ".pfx")


@dataclass(frozen=True)
class Decision:
    allowed: bool
    reason: str
    path: str = ""
    action: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "allowed": self.allowed,
            "ok": self.allowed,
            "reason": self.reason,
            "path": self.path,
            "action": self.action,
        }


@dataclass
class SandboxProfile:
    allowed_write_roots: List[Path] = field(default_factory=list)
    deny_names: Iterable[str] = field(default_factory=lambda: SECRET_NAMES)
    deny_substrings: Iterable[str] = field(default_factory=lambda: SECRET_SUBSTR)
    deny_suffixes: Iterable[str] = field(default_factory=lambda: SECRET_SUFFIXES)

    def resolved_roots(self) -> List[Path]:
        out: List[Path] = []
        for r in self.allowed_write_roots:
            try:
                out.append(Path(r).expanduser().resolve())
            except OSError:
                out.append(Path(r).expanduser())
        return out


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def default_profile() -> SandboxProfile:
    root = repo_root()
    roots = [
        root / "output",
        root / "demo" / "out",
        root / "demo" / "kb",
        root / "demo" / "data",
        root / "workbench" / "out",
    ]
    extra = (os.getenv("CIVIL_SANDBOX_ROOTS") or os.getenv("CIVIL_SANDBOX_ROOT") or "").strip()
    if extra:
        for part in extra.split(os.pathsep):
            if part.strip():
                roots.append(Path(part.strip()))
    out_dir = (os.getenv("PACKING_OUTPUT_DIR") or "").strip()
    if out_dir:
        roots.append(Path(out_dir))
    job = (os.getenv("CIVIL_JOB_ROOT") or "").strip()
    if job:
        jp = Path(job)
        n = str(jp).replace("/", "\\").rstrip("\\").lower()
        if n != "d:\\layout" and not n.startswith("d:\\layout\\"):
            roots.append(jp)
    roots.append(Path.cwd() / ".civil-buddy" / "out")
    try:
        from packing_assistant.runtime.workspace_ctx import current_worktree

        wt = current_worktree()
        if wt:
            roots.append(Path(wt) / ".civil-buddy" / "out")
    except Exception:
        pass
    return SandboxProfile(allowed_write_roots=roots)


def _norm(path: Union[str, Path]) -> Path:
    p = Path(path)
    try:
        return p.expanduser().resolve()
    except OSError:
        return p.expanduser()
    except ValueError:      # an embedded NUL: never resolved, so _inside_roots refuses it
        return p


def _is_secret(path: Path, profile: SandboxProfile) -> Optional[str]:
    name = path.name.lower()
    hay = str(path).replace("\\", "/").lower()
    deny_names = {str(n).lower() for n in profile.deny_names}
    if name in deny_names or path.name in set(profile.deny_names):
        return f"secret path denied: {path.name}"
    for sub in profile.deny_substrings:
        if sub.lower() in hay:
            return f"secret path denied: contains {sub}"
    for suf in profile.deny_suffixes:
        if name.endswith(str(suf).lower()):
            return f"secret path denied: suffix {suf}"
    return None


def _inside_roots(path: Path, roots: Sequence[Path]) -> bool:
    # 只有解析过的路径可比：relative_to 是字面比较，<root>/../x 与带 NUL 的路径会被当成在根内。
    if not path.is_absolute() or ".." in path.parts or "\x00" in str(path):
        return False
    for root in roots:
        try:
            path.relative_to(root)
            return True
        except ValueError:
            continue
    return False


def check_write(path: Union[str, Path], *, profile: Optional[SandboxProfile] = None) -> Decision:
    prof = profile or default_profile()
    target = _norm(path)
    secret = _is_secret(target, prof)
    if secret:
        return Decision(False, secret, str(target), "write")
    roots = prof.resolved_roots()
    if not roots:
        return Decision(False, "sandbox has no allowed write roots", str(target), "write")
    if not _inside_roots(target, roots):
        return Decision(False, "write outside allowed root", str(target), "write")
    return Decision(True, "ok", str(target), "write")


def check_open(path: Union[str, Path], *, profile: Optional[SandboxProfile] = None) -> Decision:
    prof = profile or default_profile()
    target = _norm(path)
    secret = _is_secret(target, prof)
    if secret:
        return Decision(False, secret, str(target), "open")
    roots = prof.resolved_roots()
    if not roots:
        return Decision(False, "sandbox has no allowed roots", str(target), "open")
    if not _inside_roots(target, roots):
        return Decision(False, "open outside allowed root", str(target), "open")
    return Decision(True, "ok", str(target), "open")


def check_spawn(
    command: Union[str, Sequence[str], None] = None,
    *,
    kind: Optional[str] = None,
    profile: Optional[SandboxProfile] = None,
) -> Decision:
    _ = profile
    k = (kind or "").strip().lower()
    if k in GENERIC_SPAWN_KINDS:
        return Decision(False, "generic spawn blocked", action="spawn")
    argv: List[str]
    if command is None:
        argv = []
    elif isinstance(command, (list, tuple)):
        argv = [str(x) for x in command]
    else:
        argv = str(command).split()
    if not argv:
        return Decision(False, "generic spawn blocked", action="spawn")
    stem = Path(argv[0]).name.lower()
    if stem in GENERIC_SPAWN_STEMS:
        return Decision(False, "generic spawn blocked", action="spawn")
    if stem in ALLOWED_HELPER_STEMS:
        # python/py only for allowlisted helper scripts
        if stem.startswith("py"):
            script = next((Path(a).name.lower() for a in argv[1:] if a.endswith(".py")), "")
            if script and script not in {s.lower() for s in ALLOWED_SCRIPT_NAMES}:
                return Decision(False, "generic spawn blocked", action="spawn")
        return Decision(True, "allowlisted helper", action="spawn")
    return Decision(False, "generic spawn blocked", action="spawn")


def assert_write(path: Union[str, Path], *, profile: Optional[SandboxProfile] = None) -> Path:
    d = check_write(path, profile=profile)
    if not d.allowed:
        raise PermissionError(d.reason)
    return _norm(path)


def assert_open(path: Union[str, Path], *, profile: Optional[SandboxProfile] = None) -> Path:
    d = check_open(path, profile=profile)
    if not d.allowed:
        raise PermissionError(d.reason)
    return _norm(path)


def request_spawn(
    command: Union[str, Sequence[str], None] = None,
    *,
    kind: Optional[str] = None,
    profile: Optional[SandboxProfile] = None,
) -> Decision:
    """Real spawn entry: every agent spawn goes through here."""
    return check_spawn(command, kind=kind, profile=profile)


#: prefix and suffix of the temporary file a write goes through; a killed writer can leave one behind
ATOMIC_TMP_PREFIX, ATOMIC_TMP_SUFFIX = ".", ".tmp"


def _atomic_write(target: Path, fill: Any, *, mode: str, encoding: Optional[str] = None) -> None:
    """Write through a temporary file in the target's own folder, then rename it over the target.

    A write that raises (an encoding error, a full disk, a tool timeout that interrupts the worker) or a
    process that is killed mid-write leaves the previous file, or no file, under the deliverable's name:
    never a truncated file that looks complete. os.replace is atomic within one folder. Text mode keeps
    Path.write_text's newline translation, so the bytes on disk are what they were before this change.
    """
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOINHERIT", 0)
    for _ in range(100):
        tmp = target.with_name(f"{ATOMIC_TMP_PREFIX}{target.name}.{os.urandom(4).hex()}{ATOMIC_TMP_SUFFIX}")
        try:
            fd = os.open(tmp, flags, 0o666)     # 0o666 minus the umask, as a plain open() would create it
            break
        except FileExistsError:
            continue
    else:
        raise FileExistsError(f"no free temporary name next to {target.name}")
    try:
        with os.fdopen(fd, mode, encoding=encoding) as f:
            f.write(fill)
            f.flush()
            os.fsync(f.fileno())
        if os.name != "nt" and target.exists():
            os.chmod(tmp, target.stat().st_mode & 0o7777)
        for attempt in range(20):
            try:
                os.replace(tmp, target)
                break
            except PermissionError:
                # Windows refuses to replace a file another process has open; that reader is brief.
                if os.name != "nt" or attempt == 19:
                    raise
                time.sleep(0.05)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    # what a killed earlier writer of this same file left behind (an hour is far past any write)
    for stale in target.parent.glob(f"{ATOMIC_TMP_PREFIX}{target.name}.*{ATOMIC_TMP_SUFFIX}"):
        try:
            if stale.stat().st_mtime < time.time() - 3600:
                stale.unlink()
        except OSError:
            pass


def guarded_write_text(
    path: Union[str, Path],
    text: str,
    *,
    profile: Optional[SandboxProfile] = None,
    encoding: str = "utf-8",
) -> Path:
    target = assert_write(path, profile=profile)
    target.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write(target, text, mode="w", encoding=encoding)
    return target


def guarded_write_bytes(
    path: Union[str, Path],
    data: bytes,
    *,
    profile: Optional[SandboxProfile] = None,
) -> Path:
    target = assert_write(path, profile=profile)
    target.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write(target, data, mode="wb")
    return target


def guarded_open_read(
    path: Union[str, Path],
    *,
    profile: Optional[SandboxProfile] = None,
    encoding: str = "utf-8",
) -> str:
    target = assert_open(path, profile=profile)
    return target.read_text(encoding=encoding)
