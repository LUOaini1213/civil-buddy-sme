"""Explicit, package-local Windows preparation; --launch never installs anything."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import socket
import stat
import subprocess
import sys
import tempfile
import uuid

ROOT = Path(__file__).absolute().parents[1]
LOCK_NAME = "requirements-unified-windows-py311.lock"
PROFILE_LOCKS = {
    "core": LOCK_NAME,
    "speech": "requirements-unified-windows-py311-speech.lock",
    "planning": "requirements-unified-windows-py311-planning.lock",
    "speech-planning": "requirements-unified-windows-py311-speech-planning.lock",
}
RUNTIME_NAME = ".civil-buddy-runtime"
OWNER_NAME = "civil-buddy-environment.json"
PRODUCT = "civil-buddy-unified-python"
PROBE = """import json,struct,sys,sysconfig
print(json.dumps(dict(implementation=sys.implementation.name,version=list(sys.version_info[:3]),
platform=sys.platform,bits=struct.calcsize('P')*8,build_platform=sysconfig.get_platform(),
executable=sys.executable,prefix=sys.prefix,base_prefix=sys.base_prefix)))
"""
INVENTORY_PROBE = """import importlib.metadata as m,json,sys
result={}
for name in json.loads(sys.argv[1]):
    try: result[name]=m.version(name)
    except m.PackageNotFoundError: result[name]=None
print(json.dumps(result))
"""


class PreparationError(Exception):
    pass


def reject_links(path: Path) -> None:
    """Check each existing component before resolving or writing a generated path."""
    path = path.absolute()
    for item in reversed((path, *path.parents)):
        try:
            attributes = item.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(attributes.st_mode) or getattr(attributes, "st_file_attributes", 0) & 0x400:
            raise PreparationError("A preparation path traverses a link or reparse point; choose a regular local directory.")


def clean_environment(*, windows_x64: bool = False) -> dict[str, str]:
    keep = {"PATH", "SYSTEMROOT", "WINDIR", "PATHEXT", "TEMP", "TMP", "APPDATA",
            "LOCALAPPDATA", "USERPROFILE", "HOME", "LANG"}
    env = {key: value for key, value in os.environ.items() if key.upper() in keep}
    # Do not use pip --isolated: PIP_CONFIG_FILE must remain effective. NUL/devnull
    # disables configuration files; the allowlist removes all other pip settings.
    env.update(PIP_CONFIG_FILE=os.devnull, PIP_DISABLE_PIP_VERSION_CHECK="1", PIP_NO_INPUT="1",
               PIP_KEYRING_PROVIDER="disabled", NETRC=os.devnull, NO_PROXY="*",
               PYTHONNOUSERSITE="1", PYTHONUTF8="1", PYTHONDONTWRITEBYTECODE="1",
               PYTHON_DOTENV_DISABLED="1", CIVIL_DOTENV_DISABLED="1")
    if windows_x64:
        if os.name != "nt":
            raise PreparationError("The canonical Windows x64 dependency environment is unavailable on this platform.")
        # platform.machine(), including pip's target dependency markers, reads
        # this on Windows. Only verified win-amd64 callers opt in; never inherit
        # an untrusted architecture value or use it to verify the interpreter.
        env["PROCESSOR_ARCHITECTURE"] = "AMD64"
    return env


def run_command(command: list[str], root: Path, *, timeout: int = 900,
                windows_x64: bool = False) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(command, cwd=root, env=clean_environment(windows_x64=windows_x64), stdin=subprocess.DEVNULL,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout,
                              creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    except (OSError, subprocess.TimeoutExpired):
        raise PreparationError("Preparation command could not finish. Check the interpreter, network and directory permissions, then retry.") from None


def interpreter_info(python: str, root: Path) -> dict:
    result = run_command([python, "-I", "-B", "-c", PROBE], root, timeout=20)
    try:
        if result.returncode or len(result.stdout) > 8192:
            raise ValueError
        data = json.loads(result.stdout)
        # On Windows, sysconfig uses interpreter build metadata (sys.version),
        # while platform.machine() reads caller environment variables that the
        # installer deliberately strips. Require the x64 build and pointer width.
        if (data["implementation"] != "cpython" or data["version"][:2] != [3, 11]
                or data["platform"] != "win32" or data["bits"] != 64
                or data["build_platform"] != "win-amd64"):
            raise ValueError
        if not Path(data["executable"]).is_absolute():
            raise ValueError
        return data
    except (ValueError, KeyError, TypeError, AttributeError):
        raise PreparationError("This preparation lane requires CPython 3.11 for Windows x64. Select it with --python <full python.exe path>.") from None


def lock_versions(data: bytes) -> dict[str, str]:
    try:
        content = data.decode("ascii")
    except UnicodeError:
        raise PreparationError("Dependency lock must be ASCII text.") from None
    if len(data) > 1024 * 1024:
        raise PreparationError("Dependency lock is unexpectedly large.")
    logical = ""
    versions = {}
    for line in content.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        continuation = line.endswith("\\")
        logical += " " + (line[:-1].strip() if continuation else line)
        if continuation:
            continue
        match = re.fullmatch(r"([A-Za-z0-9][A-Za-z0-9._-]*)(?:\[[A-Za-z0-9][A-Za-z0-9_,.-]*\])?==([A-Za-z0-9][A-Za-z0-9.!+_-]*)(?:\s+--hash=sha256:[a-f0-9]{64})+", logical.strip())
        if not match:
            raise PreparationError("Dependency lock must contain only exact package versions and SHA-256 hashes.")
        name = re.sub(r"[-_.]+", "-", match.group(1)).lower()
        if name in versions:
            raise PreparationError("Dependency lock contains a duplicate package.")
        versions[name] = match.group(2)
        logical = ""
    if logical or not versions:
        raise PreparationError("Dependency lock is empty or incomplete.")
    return versions


def profile_name(value: str) -> str:
    if not isinstance(value, str) or value not in PROFILE_LOCKS:
        raise PreparationError("Invalid preparation profile. Choose core, speech, planning or speech-planning; no environment was selected.")
    return value


def selected_profile(selected: dict) -> str:
    # Releases preceding typed profiles prepared only core. Explicit null must
    # not acquire the meaning of an absent, legacy core field.
    return profile_name(selected["profile"]) if "profile" in selected else "core"


def lock_bytes(root: Path, profile: str = "core") -> bytes:
    source = root / PROFILE_LOCKS[profile_name(profile)]
    reject_links(source)
    try:
        data = source.read_bytes()
    except OSError:
        raise PreparationError("The release's pinned Windows dependency lock is missing or unreadable.") from None
    lock_versions(data)
    return data


def check_inventory(python: Path, root: Path, locked: bytes) -> None:
    versions = lock_versions(locked)
    # Launch/check reaches this path too. Verify build metadata without marker
    # overrides before selecting the fixed Windows target environment.
    interpreter_info(str(python), root)
    result = run_command([str(python), "-I", "-B", "-c", INVENTORY_PROBE, json.dumps(list(versions))], root, timeout=30,
                         windows_x64=True)
    try:
        if result.returncode or len(result.stdout) > 65536:
            raise ValueError
        actual = json.loads(result.stdout)
        if actual != versions:
            raise ValueError
    except (ValueError, TypeError):
        raise PreparationError("Installed package versions do not exactly match the release lock. Run prepare-unified-workbench.bat --rebuild to prepare a fresh environment; existing files were retained.") from None


def check_native_profile(python: Path, root: Path, profile: str) -> None:
    profile = profile_name(profile)
    script = root / "scripts/check_optional_preparation.py"
    reject_links(script)
    if not script.is_file():
        raise PreparationError("The native dependency check is missing. Extract the complete release; normal startup never installs packages.")
    result = run_command([str(python), "-I", "-B", str(script), "--profile", profile], root, timeout=120)
    try:
        if result.returncode or len(result.stdout) > 65536:
            raise ValueError
        report = json.loads(result.stdout)
        if not isinstance(report, dict) or report.get("passed") is not True or report.get("profile") != profile:
            raise ValueError
    except (ValueError, TypeError):
        raise PreparationError("The selected profile's native dependencies did not pass. Run prepare-unified-workbench.bat --rebuild to prepare a fresh environment; existing files were retained.") from None


def reject_tree_links(folder: Path) -> None:
    reject_links(folder)
    def unreadable(error):
        raise error
    for parent, directories, files in os.walk(folder, followlinks=False, onerror=unreadable):
        for name in (*directories, *files):
            item = Path(parent) / name
            attributes = item.lstat()
            if stat.S_ISLNK(attributes.st_mode) or getattr(attributes, "st_file_attributes", 0) & 0x400:
                raise PreparationError("The owned environment contains a link or reparse point; it will not be modified.")


def read_json(path: Path) -> dict:
    reject_links(path)
    try:
        if path.stat().st_size > 65536:
            raise ValueError
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError
        return value
    except (OSError, ValueError):
        raise PreparationError("Preparation metadata is missing or invalid; existing files were left unchanged.") from None


def atomic_json(path: Path, value: dict) -> None:
    reject_links(path)
    descriptor, temporary = tempfile.mkstemp(prefix=".prepare-", suffix=".json", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        reject_links(path)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def identity(root: Path) -> dict:
    return {"schema_version": 1, "product": PRODUCT, "package_root": str(root.absolute())}


def check_identity(value: dict, expected: dict) -> None:
    if (any(value.get(key) != item for key, item in expected.items())
            or ("generation" in value) != ("generation" in expected)
            or ("profile" in value) != ("profile" in expected)):
        raise PreparationError("This environment is not owned by this package and dependency lock; it will not be modified.")


def owned_runtime(root: Path, *, create: bool) -> Path:
    reject_links(root)
    runtime = root / RUNTIME_NAME
    reject_links(runtime)
    expected = identity(root)
    if runtime.exists():
        check_identity(read_json(runtime / "owner.json"), expected)
    elif create:
        runtime.mkdir()
        atomic_json(runtime / "owner.json", expected)
    else:
        raise PreparationError("No prepared environment. Run prepare-unified-workbench.bat first; normal startup never installs packages.")
    return runtime


@contextmanager
def preparation_lock(runtime: Path):
    target = runtime / "prepare.lock"
    reject_links(target)
    with target.open("a+b") as handle:
        handle.seek(0)
        if not handle.read(1):
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise PreparationError("Another preparation is running. Let it finish before retrying.") from None
        try:
            yield
        finally:
            if os.name == "nt":
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def env_details(root: Path, lock_hash: str, generation: str | None = None, *,
                profile: str = "core", legacy_profile: bool = False) -> tuple[Path, dict]:
    profile = profile_name(profile)
    if legacy_profile and profile != "core":
        raise PreparationError("Legacy preparation metadata can select only the core profile.")
    if not isinstance(lock_hash, str) or not re.fullmatch(r"[a-f0-9]{64}", lock_hash):
        raise PreparationError("Invalid prepared environment identifier.")
    if generation is not None and (not isinstance(generation, str) or not re.fullmatch(r"[a-f0-9]{32}", generation)):
        raise PreparationError("Invalid prepared environment generation.")
    # Shorten only the directory component. Ownership always compares all 64 digits.
    name = lock_hash[:16] + ("--" + generation if generation is not None else "")
    env = root / RUNTIME_NAME / "py311" / name
    reject_links(env)
    expected = {**identity(root), "lock_sha256": lock_hash, "environment": env.relative_to(root).as_posix()}
    if not legacy_profile:
        expected["profile"] = profile
    if generation is not None:
        expected["generation"] = generation
    return env, expected


def selection_details(root: Path, selected: dict) -> tuple[Path, dict]:
    profile = selected_profile(selected)
    generation = selected.get("generation")
    # Absence selects the original directory layout. An explicit null or another
    # malformed value must not silently turn a generation into a legacy path.
    if "generation" in selected and generation is None:
        raise PreparationError("Invalid prepared environment generation. Run prepare-unified-workbench.bat --rebuild to create a fresh selection.")
    env, expected = env_details(root, selected.get("lock_sha256", ""), generation,
                                profile=profile, legacy_profile="profile" not in selected)
    check_identity(selected, expected)
    packaged = lock_bytes(root, profile)
    if hashlib.sha256(packaged).hexdigest() != expected["lock_sha256"]:
        # A prior release's same-profile lock may legitimately be upgraded by
        # explicit preparation. A lock belonging to another current profile
        # cannot be reinterpreted as an upgrade or a silent downgrade, even by
        # an omitted-profile rebuild of otherwise damaged metadata.
        for other, filename in PROFILE_LOCKS.items():
            if (other != profile and (root / filename).exists()
                    and hashlib.sha256(lock_bytes(root, other)).hexdigest() == expected["lock_sha256"]):
                raise PreparationError("The selected profile does not match its dependency lock. Use an explicitly named --profile with --rebuild; existing files were retained.")
    return env, expected


def selected_environment(root: Path, selected: dict) -> tuple[Path, dict, bytes]:
    """Validate ownership and snapshot without interpreting a stale package lock."""
    env, expected = selection_details(root, selected)
    check_identity(read_json(env / OWNER_NAME), expected)
    check_identity(read_json(env / "ready.json"), {**expected, "ready": True})
    snapshot = env / "requirements.lock"
    reject_links(snapshot)
    locked = snapshot.read_bytes()
    if hashlib.sha256(locked).hexdigest() != expected["lock_sha256"]:
        raise PreparationError("The prepared lock snapshot changed. Run prepare-unified-workbench.bat --rebuild; normal startup will not repair it.")
    lock_versions(locked)
    return env, expected, locked


def prepared_python(root: Path, *, profile: str | None = None) -> Path:
    if profile is not None:
        profile_name(profile)
    runtime = owned_runtime(root, create=False)
    selected = read_json(runtime / "selected.json")
    actual_profile = selected_profile(selected)
    if profile is not None and profile != actual_profile:
        raise PreparationError("The selected environment uses a different profile. Explicit preparation is required to switch profiles; normal startup never installs packages.")
    env, expected, locked = selected_environment(root, selected)
    packaged = lock_bytes(root, actual_profile)
    if packaged != locked or hashlib.sha256(packaged).hexdigest() != expected["lock_sha256"]:
        raise PreparationError("The selected profile's lock no longer matches this release. Run prepare-unified-workbench.bat --rebuild; normal startup will not repair it.")
    python = env / "Scripts/python.exe"
    reject_links(python)
    if not python.is_file():
        raise PreparationError("The selected interpreter is missing. Run prepare-unified-workbench.bat --rebuild; nothing was installed during startup.")
    check_inventory(python, root, packaged)
    check_native_profile(python, root, actual_profile)
    return python


def load_preflight(root: Path):
    path = root / "scripts/workbench_preflight.py"
    reject_links(path)
    name = "_civil_unified_preparation_preflight"
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise PreparationError("Release preflight script is missing.")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def prepare(root: Path, *, python: str, binary: Path, port: int | None = None,
            wheelhouse: Path | None = None, rebuild: bool = False,
            profile: str | None = None) -> Path:
    root = root.absolute()
    reject_links(root)
    if profile is not None:
        profile_name(profile)
    base = interpreter_info(python, root)
    base_python = base["executable"]
    if wheelhouse is not None:
        wheelhouse = wheelhouse.absolute()
        reject_links(wheelhouse)
        if not wheelhouse.is_dir():
            raise PreparationError("The supplied wheelhouse must be an existing regular directory.")
    runtime = owned_runtime(root, create=True)
    with preparation_lock(runtime):
        selection = runtime / "selected.json"
        # An explicitly named rebuild may recover unreadable metadata. Without
        # a named profile, retain the typed selected profile; never downgrade a
        # damaged or unknown optional selection to core.
        reject_links(selection)
        current = None
        current_details = None
        if selection.exists() and not (rebuild and profile is not None):
            current = read_json(selection)
            current_details = selection_details(root, current)
            if not rebuild:
                selected_environment(root, current)
        chosen_profile = profile if profile is not None else (selected_profile(current) if current is not None else "core")
        locked = lock_bytes(root, chosen_profile)
        lock_hash = hashlib.sha256(locked).hexdigest()
        target = None
        if not rebuild and current_details is not None:
            current_env, current_expected = current_details
            if selected_profile(current) == chosen_profile and current_expected["lock_sha256"] == lock_hash:
                # Keep the successfully selected generation. A damaged selection
                # requires explicit rebuild; never fall back to an older baseline.
                prepared_python(root, profile=chosen_profile)
                target = current_env, current_expected
        if target is None:
            generation = uuid.uuid4().hex if rebuild else None
            target = env_details(root, lock_hash, generation, profile=chosen_profile)
        env, expected = target
        if env.exists():
            if rebuild:
                raise PreparationError("The fresh environment generation already exists; no existing environment was modified. Retry --rebuild to create another generation.")
            check_identity(read_json(env / OWNER_NAME), expected)
        else:
            env.parent.mkdir(exist_ok=True)
            env.mkdir()
            atomic_json(env / OWNER_NAME, expected)
        reject_tree_links(env)
        selected = env / "Scripts/python.exe"
        reject_links(selected)
        ready = env / "ready.json"
        reject_links(ready)
        if ready.exists():
            check_identity(read_json(ready), {**expected, "ready": True})
        else:
            # A killed venv creation may leave python.exe but no working pip.
            # Re-running venv without --clear repairs only this owned final path.
            print("Preparing this package's Python environment / 正在准备本包专用环境", flush=True)
            result = run_command([base_python, "-I", "-B", "-m", "venv", str(env)], root)
            if result.returncode:
                raise PreparationError("Could not create the package environment. Retry explicit preparation; the previous selection is unchanged.")
        probe = interpreter_info(str(selected), root)
        if Path(probe["prefix"]).absolute() != env.absolute() or probe["prefix"] == probe["base_prefix"]:
            raise PreparationError("The target interpreter is not this package's virtual environment; no packages were installed.")
        snapshot = env / "requirements.lock"
        reject_links(snapshot)
        if snapshot.exists() and snapshot.read_bytes() != locked:
            raise PreparationError("The owned environment's lock snapshot differs; existing files were left unchanged.")
        if not snapshot.exists():
            snapshot.write_bytes(locked)
        if not ready.exists():
            print("Installing the locked dependencies / 正在安装固定版本依赖", flush=True)
            command = [str(selected), "-I", "-B", "-m", "pip", "--require-virtualenv", "--disable-pip-version-check",
                       "install", "--no-input", "--no-cache-dir", "--no-compile", "--require-hashes", "--only-binary=:all:"]
            if wheelhouse is None:
                command += ["--index-url", "https://pypi.org/simple"]
            else:
                command += ["--no-index", "--find-links", str(wheelhouse)]
            result = run_command(command + ["-r", str(snapshot)], root, windows_x64=True)
            if result.returncode:
                raise PreparationError("Locked dependency installation failed. Check network/wheelhouse and available space, then retry preparation. The previous selected environment is unchanged.")
        check_inventory(selected, root, locked)
        result = run_command([str(selected), "-I", "-B", "-m", "pip", "--require-virtualenv", "--disable-pip-version-check", "check"], root,
                             windows_x64=True)
        if result.returncode:
            raise PreparationError("Dependency consistency check failed. No new environment was selected.")
        check_native_profile(selected, root, chosen_profile)
        preflight = load_preflight(root)
        if port is None:
            # Preparation checks dependencies, not whether a running workbench's
            # default port is free. Normal launch still checks its actual port.
            with socket.socket() as probe_socket:
                probe_socket.bind(("127.0.0.1", 0))
                port = probe_socket.getsockname()[1]
        report = preflight.run(python=str(selected), binary=binary, port=port, root=root)
        print(preflight.format_report(report, root), flush=True)
        if not report.ok:
            raise PreparationError("Startup prerequisites did not pass. Fix the reported items and retry; the previous selection is unchanged.")
        if lock_bytes(root, chosen_profile) != locked:
            raise PreparationError("The dependency lock changed during preparation; no new environment was selected.")
        atomic_json(ready, {**expected, "ready": True})
        atomic_json(selection, expected)
        return selected


def launch(root: Path, arguments: list[str], *, profile: str | None = None) -> int:
    if any(value.startswith("--") and "--python".startswith(value.split("=", 1)[0])
           for value in arguments):
        raise PreparationError("This launcher uses the prepared interpreter. Use CIVIL_PYTHON only to select the bootstrap interpreter, or invoke the advanced launcher directly.")
    python = prepared_python(root, profile=profile)
    script = root / "scripts/start_unified_workbench.py"
    reject_links(script)
    if not script.is_file():
        raise PreparationError("The unified launcher is missing; extract the complete release.")
    defaults = ["--python", str(python)]
    if not any(value == "--binary" or value.startswith("--binary=") for value in arguments):
        defaults += ["--binary", str(root / "bin/civil-workbench.exe")]
    env = dict(os.environ)
    for name in list(env):
        if name.upper().startswith("PYTHON"):
            env.pop(name)
    env.update(PYTHONUTF8="1", PYTHONDONTWRITEBYTECODE="1", PYTHONNOUSERSITE="1")
    return subprocess.call([str(python), "-B", str(script), *defaults, *arguments], cwd=root, env=env)


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    try:
        if arguments[:1] == ["--launch"]:
            return launch(ROOT, arguments[1:])
        parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
        parser.add_argument("--profile", choices=tuple(PROFILE_LOCKS), help="Fixed dependency profile; omitted preserves the selected profile, or uses core for a new package")
        parser.add_argument("--python", default=sys.executable, help="Base CPython 3.11 Windows x64 interpreter")
        parser.add_argument("--binary", type=Path, default=ROOT / "bin/civil-workbench.exe")
        parser.add_argument("--port", type=int, help="Optional preparation probe port; ordinary startup checks its actual port")
        parser.add_argument("--wheelhouse", type=Path, help="Install exclusively from this local wheel directory; no index access")
        mode = parser.add_mutually_exclusive_group()
        mode.add_argument("--check", action="store_true", help="Check the selected environment only; do not prepare or install")
        mode.add_argument("--rebuild", action="store_true", help="Install the same release lock in a fresh generation; retain every prior environment and select only after successful checks")
        args = parser.parse_args(arguments)
        if args.check:
            return launch(ROOT, ["--check", "--binary", str(args.binary), "--port", str(args.port or 8765)], profile=args.profile)
        selected = prepare(ROOT, python=args.python, binary=args.binary, port=args.port, wheelhouse=args.wheelhouse,
                           rebuild=args.rebuild, profile=args.profile)
        print("Preparation complete / 环境准备完成: " + str(selected))
        print("Open start-unified-workbench.bat to start. No product or model was started during preparation.")
        return 0
    except (PreparationError, OSError, ValueError) as exc:
        # Do not expose raw subprocess output or environment/configuration values.
        detail = str(exc) if isinstance(exc, PreparationError) else "Preparation could not access a required local file. Existing environments and product state were preserved."
        print("[FAIL] " + detail, file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
