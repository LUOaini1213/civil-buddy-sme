"""Start one local product: Rust host + fixed Python domain service.

The domain process receives no provider keys and exposes no chat runtime.
Use --env-file only for a user-selected configuration; keys are never printed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import socket
import re
import secrets
import signal
import subprocess
import sys
import time
import urllib.request
import webbrowser

ROOT = Path(__file__).resolve().parents[1]


class ProcessFamily:
    """Own only this launcher's helpers and their descendants, including ASR workers."""
    def __init__(self):
        self.job = None
        if os.name != "nt":
            return
        import ctypes
        from ctypes import wintypes
        class Basic(ctypes.Structure):
            _fields_ = [("per_process", ctypes.c_longlong), ("per_job", ctypes.c_longlong),
                        ("flags", wintypes.DWORD), ("min_working_set", ctypes.c_size_t),
                        ("max_working_set", ctypes.c_size_t), ("active_processes", wintypes.DWORD),
                        ("affinity", ctypes.c_size_t), ("priority", wintypes.DWORD), ("scheduling", wintypes.DWORD)]
        class Counters(ctypes.Structure):
            _fields_ = [(name, ctypes.c_ulonglong) for name in ("read_ops", "write_ops", "other_ops", "read_bytes", "write_bytes", "other_bytes")]
        class Limits(ctypes.Structure):
            _fields_ = [("basic", Basic), ("io", Counters), ("process_memory", ctypes.c_size_t),
                        ("job_memory", ctypes.c_size_t), ("peak_process", ctypes.c_size_t), ("peak_job", ctypes.c_size_t)]
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        self.kernel.CreateJobObjectW.restype = wintypes.HANDLE
        self.kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        self.kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        self.kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        job = self.kernel.CreateJobObjectW(None, None)
        if not job:
            raise ctypes.WinError(ctypes.get_last_error())
        limits = Limits()
        limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not self.kernel.SetInformationJobObject(job, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
            error = ctypes.WinError(ctypes.get_last_error())
            self.kernel.CloseHandle(job)
            raise error
        self.job = job

    def add(self, process):
        if self.job:
            import ctypes
            if not self.kernel.AssignProcessToJobObject(self.job, int(process._handle)):
                raise ctypes.WinError(ctypes.get_last_error())

    def close(self, processes):
        if self.job:
            self.kernel.CloseHandle(self.job)
            self.job = None
        elif os.name != "nt":
            for process in processes:
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--binary", type=Path)
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--state-root", type=Path, default=ROOT / "demo/data/unified")
    parser.add_argument("--env-file", type=Path)
    parser.add_argument("--user-id", help="Named account: ASCII letters, numbers, underscore or hyphen")
    parser.add_argument("--workspace", type=Path, help="One existing private job directory")
    parser.add_argument("--token-file", type=Path, help="UTF-8 file containing a random login token (at least 32 characters)")
    parser.add_argument("--public-origin", help="Optional HTTPS origin of your configured reverse proxy")
    parser.add_argument("--stop-file", type=Path, help="Optional local shutdown sentinel for supervised runs")
    parser.add_argument("--open", action="store_true")
    args = parser.parse_args()
    binary = args.binary or ROOT / "workbench/target/release" / ("civil-workbench.exe" if os.name == "nt" else "civil-workbench")
    if not binary.is_file():
        parser.error("Rust executable missing; run cargo build --release --manifest-path workbench/Cargo.toml or specify --binary")
    environment = dict(os.environ)
    if args.env_file:
        from dotenv import dotenv_values
        environment.update({k: v for k, v in dotenv_values(args.env_file).items() if v is not None})
    named = any((args.user_id, args.workspace, args.token_file, args.public_origin))
    token = None
    workspace = None
    if named:
        if not all((args.user_id, args.workspace, args.token_file)):
            parser.error("Named mode requires --user-id, --workspace and --token-file together")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", args.user_id):
            parser.error("Invalid user id")
        if not args.workspace.is_dir():
            parser.error("Workspace must be an existing private directory")
        workspace = args.workspace.resolve()
        base_state = args.state_root.resolve()
        if workspace.is_relative_to(base_state) or base_state.is_relative_to(workspace):
            parser.error("State storage and the workspace must be separate, non-nested directories")
        if args.token_file.resolve().is_relative_to(workspace):
            parser.error("Keep --token-file outside the workspace so it cannot become project material")
        token = args.token_file.read_text(encoding="utf-8-sig").strip()
        if len(token) < 32 or len(token) > 4096 or not token.isascii() or any(c.isspace() for c in token):
            parser.error("Login token must be 32-4096 ASCII characters without whitespace")
        digest = hashlib.sha256(os.path.normcase(str(workspace)).encode("utf-8")).hexdigest()
        args.state_root = args.state_root / "accounts" / args.user_id / "projects" / digest
    elif any(environment.get(k) for k in ("CIVIL_INSTANCE_USER", "CIVIL_TOKEN", "CIVIL_TOKEN_SHA256", "CIVIL_PUBLIC_ORIGIN", "CIVIL_ALLOWED_WORKSPACES")):
        parser.error("Identity environment is configured; use explicit --user-id, --workspace and --token-file")
    args.state_root = args.state_root.resolve()
    args.state_root.mkdir(parents=True, exist_ok=True)
    # OS lock prevents a second host from recovering a live host's active turns.
    lock = (args.state_root / "host.lock").open("a+b")
    lock.seek(0)
    lock.write(b"0")
    lock.flush()
    lock.seek(0)
    try:
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        parser.error("This state directory already has a running product host")
    with socket.socket() as reserved:
        reserved.bind(("127.0.0.1", 0))
        domain_port = reserved.getsockname()[1]
    domain_env = {k: v for k, v in os.environ.items() if k.upper() in
                  {"PATH", "SYSTEMROOT", "WINDIR", "PATHEXT", "TEMP", "TMP", "APPDATA", "LOCALAPPDATA", "USERPROFILE", "LANG"}}
    domain_token = secrets.token_urlsafe(48)
    domain_env.update(PYTHONUTF8="1", PYTHON_DOTENV_DISABLED="1", CIVIL_OUT_ROOT=str(args.state_root / "domains"),
                      CIVIL_DOMAIN_TOKEN=domain_token, CIVIL_DATA_ROOT=str(args.state_root / "legacy/data"),
                      CIVIL_SANDBOX_ROOTS=str(args.state_root / "domains"),
                      CIVIL_DOMAIN_WORKSPACE=str(args.state_root / "domains"),
                      PACKING_OUTPUT_DIR=str(args.state_root / "packing"),
                      PACKING_TRACE_DIR=str(args.state_root / "packing" / "traces"),
                      CB_DB_PATH=str(args.state_root / "packing" / "civilbuddy.db"),
                      PACKING_LG_CHECKPOINT_PATH=str(args.state_root / "packing" / "checkpoints.db"),
                      PACKING_LLM_AGENT="0", PACKING_SKIP_SKJOLBER="1", CIVIL_AGENT_MODE="steps")
    # Sidecar uses only its private store. Job material is selected by the host.
    for key in ("CIVIL_TOKEN", "CIVIL_TOKEN_SHA256", "CIVIL_INSTANCE_USER", "CIVIL_ALLOWED_WORKSPACES", "CIVIL_PUBLIC_ORIGIN", "PYTHONPATH"):
        environment.pop(key, None)
    environment.update(CIVIL_PORT=str(args.port), CIVIL_DOMAIN_URL=f"http://127.0.0.1:{domain_port}",
                       CIVIL_DOMAIN_TOKEN=domain_token, CIVIL_DOTENV_DISABLED="1", PYTHON_DOTENV_DISABLED="1",
                       CIVIL_JOB_ROOT=str(workspace or args.state_root / "jobs"),
                       CIVIL_STATE_ROOT=str(args.state_root), CIVIL_DEMO_ROOT=str(ROOT / "demo"),
                       CIVIL_PYTHON=str(Path(args.python).resolve()), CIVIL_UNIFIED_HOME="1",
                       PACKING_AGENT_URL=f"http://127.0.0.1:{domain_port}/packing")
    if named:
        environment.update(CIVIL_INSTANCE_USER=args.user_id,
                           CIVIL_TOKEN_SHA256=hashlib.sha256(token.encode("utf-8")).hexdigest(),
                           CIVIL_ALLOWED_WORKSPACES=json.dumps([str(workspace)]))
        if args.public_origin:
            environment["CIVIL_PUBLIC_ORIGIN"] = args.public_origin
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    processes = []
    family = ProcessFamily()
    logs = [(args.state_root / name).open("ab") for name in ("domains.log", "rust.log")]
    try:
        processes.append(subprocess.Popen([args.python, "-m", "uvicorn", "demo.domain_service:app", "--host", "127.0.0.1", "--port", str(domain_port)],
                                          cwd=ROOT, env=domain_env, creationflags=flags, start_new_session=os.name != "nt", stdout=logs[0], stderr=subprocess.STDOUT))
        family.add(processes[-1])
        processes.append(subprocess.Popen([str(binary.resolve())], cwd=ROOT, env=environment, creationflags=flags, start_new_session=os.name != "nt", stdout=logs[1], stderr=subprocess.STDOUT))
        family.add(processes[-1])
        for _ in range(100):
            if any(p.poll() is not None for p in processes):
                raise RuntimeError(f"A product process exited during startup; inspect {args.state_root}/domains.log and rust.log")
            try:
                host_probe = urllib.request.Request(f"http://127.0.0.1:{args.port}/api/agent/capabilities",
                                                    headers={"Authorization": f"Bearer {token}"} if token else {})
                with urllib.request.urlopen(host_probe, timeout=1) as response:
                    ready = response.status == 200
                domain_probe = urllib.request.Request(f"http://127.0.0.1:{domain_port}/health",
                                                      headers={"Authorization": f"Bearer {domain_token}"})
                with urllib.request.urlopen(domain_probe, timeout=1) as response:
                    ready = ready and response.status == 200
                if ready:
                    break
            except OSError:
                time.sleep(0.1)
        else:
            raise RuntimeError("Product startup timed out")
        entry = "/auth/login" if named else "/static/agent.html"
        print(f"Civil Buddy: http://127.0.0.1:{args.port}{entry}", flush=True)
        print(f"State: {args.state_root}; Ctrl+C stops both processes", flush=True)
        if args.open:
            webbrowser.open(f"http://127.0.0.1:{args.port}{entry}")
        while all(p.poll() is None for p in processes) and not (args.stop_file and args.stop_file.exists()):
            time.sleep(0.25)
        if any(p.poll() is not None for p in processes):
            raise RuntimeError("A product process stopped unexpectedly; inspect instance logs")
    except KeyboardInterrupt:
        pass
    finally:
        family.close(processes)
        for process in reversed(processes):
            if process.poll() is None:
                process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        lock.close()
        for log in logs:
            log.close()


if __name__ == "__main__":
    main()
