"""Package committed, allowlisted sources and a supplied Windows Rust executable.

No compilation, dependency installation, provider calls, or executable launch occurs.
The supplied binary's hash is recorded separately from the source commit.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import struct
import subprocess
import tempfile
import zipfile

try:
    from . import build_workbench_release as legacy
except ImportError:  # Direct invocation from scripts/.
    import build_workbench_release as legacy

ROOT = Path(__file__).resolve().parents[1]
EXTRA = (
    "examples/packing-replan/README.md", "examples/packing-replan/geometry-only.json",
    "SECURITY.md", "docs/civil-buddy/release-handoff.md",
    "docs/civil-buddy/sme-integration.md", "README.zh.md",
    "docs/civil-buddy/submission-sync-20260927.md",
    "docs/civil-buddy/review-fixes-20260927.md",
    "docs/civil-buddy/bilingual-workbench.md",
    "docs/deploy-minimal.md", "docs/deploy-aws-lightsail.md",
    "Dockerfile", "docker-compose.yml", ".dockerignore",
    "deploy/lightsail/Caddyfile", "deploy/lightsail/compose.override.yml",
    "deploy/lightsail/user-data.sh", "deploy/lightsail/civil-admin.sh", "deploy/lightsail/test-local.sh",
    "docs/civil-buddy/acceptance/sme-preview-checklist.md",
    "scripts/unified_acceptance.py", "scripts/test_unified_runtime_http.py",
    "examples/facade-demo/README.md", "examples/facade-demo/facade_itt_doc.md",
    "examples/facade-demo/facade_panels.xlsx", "examples/facade-demo/facade_panels_rev_b.xlsx",
    "examples/facade-demo/facade_panels_zh.xlsx", "examples/facade-demo/daily_report_input.txt",
    "examples/facade-demo/facade_panels_mixed.xlsx", "examples/facade-demo/make_panels.py",
    "examples/facade-demo/wah_briefing_input.txt",
    "scripts/demo_facade.py",
    "workbench/Cargo.toml", "workbench/Cargo.lock",
    "workbench/scripts/run_tender_extract.py", "workbench/scripts/run_packing_sidecar.py",
    "scripts/docker_smoke.sh",
    "scripts/build_workbench_release.py", "scripts/build_unified_release.py",
    "docs/civil-buddy/architecture/implementation.md",
    "docs/civil-buddy/acceptance/2026-09-21.json",
    "docs/civil-buddy/acceptance/2026-09-30-practical.json",
    "docs/civil-buddy/acceptance/2026-10-03-packing-replan.json",
    "docs/civil-buddy/architecture/civil-buddy-rust-architecture.md",
    "docs/civil-buddy/architecture/civil-buddy-document-skills.md",
    "docs/civil-buddy/architecture/civil-buddy-agent-infra.md",
)
BINARY_NAME = "bin/civil-workbench.exe"
MANIFEST_NAME = "release-manifest.json"
MAX_FILE = 512 * 1024 * 1024
MAX_ARCHIVE_CONTENT = 1024 * 1024 * 1024
FORBIDDEN_ROOTS = (
    "demo/data/", "demo/out/", "workbench/target/", "target/", "work/",
    "output/", "outputs/", "dist/", "runtime/",
)


def digest(path: Path) -> str:
    hashed = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            hashed.update(chunk)
    return hashed.hexdigest()


def git(root: Path, *args: str) -> bytes:
    result = subprocess.run(["git", *args], cwd=root, check=True,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return result.stdout


def safe_name(name: str) -> None:
    path = PurePosixPath(name)
    if (not name or path.is_absolute() or name != path.as_posix()
            or any(p in {"", ".", ".."} for p in path.parts)
            or "\\" in name or ":" in name or "\x00" in name):
        raise ValueError(f"Unsafe release member: {name}")
    folded = name.casefold()
    if (folded.startswith(FORBIDDEN_ROOTS)
            or any(part.casefold() in {".git", ".venv", "__pycache__", "node_modules"} for part in path.parts)
            or (path.name.casefold().startswith(".env") and path.name.casefold() != ".env.example")
            or path.name.casefold().startswith("login-token")
            or path.suffix.casefold() in {".key", ".pem", ".pfx", ".p12", ".sqlite", ".sqlite3", ".db", ".jsonl", ".pyc",
                                          ".sqlite-wal", ".sqlite-shm", ".sqlite3-wal", ".sqlite3-shm", ".db-wal", ".db-shm"}):
        raise ValueError(f"Private or runtime file cannot be released: {name}")


def committed_inputs(root: Path) -> tuple[dict[str, Path], str]:
    """Reuse the existing allowlist; discovered files must also exist in HEAD."""
    root = root.resolve()
    top = Path(os.fsdecode(git(root, "rev-parse", "--show-toplevel")).strip()).resolve()
    if top != root:
        raise ValueError("Source root must be the Git repository root")
    commit = git(root, "rev-parse", "HEAD").decode("ascii").strip()
    tracked = set(git(root, "ls-tree", "-r", "--name-only", "-z", "HEAD").decode("utf-8").rstrip("\0").split("\0"))
    candidates = legacy.release_inputs(root)
    required = {*legacy.EXPLICIT, *("demo/static/" + item for item in legacy.STATIC),
                "scripts/start-workbench.bat", *EXTRA,
                "workbench/src/main.rs", "workbench/src/lib.rs"}
    missing = required - tracked
    if missing:
        raise ValueError("Required source inputs must be committed before packaging: " + ", ".join(sorted(missing)))
    inputs = {name: path for name, path in candidates.items()
              if path.relative_to(root).as_posix() in tracked}
    additions = set(EXTRA) | {name for name in tracked if name.startswith("workbench/src/") and name.endswith(".rs")}
    for name in additions:
        inputs[name] = legacy.checked_path(root, name)
    folded = set()
    for name, path in inputs.items():
        safe_name(name)
        if name.casefold() in folded:
            raise ValueError("Release paths collide on Windows: " + name)
        folded.add(name.casefold())
        if not path.is_file():
            raise FileNotFoundError("Committed input is missing from checkout: " + name)
    skills = [name for name in inputs if name.startswith(".agents/skills/") and name != ".agents/skills/civil-buddy/SKILL.md"]
    if len(skills) != 66:
        raise ValueError(f"Expected 66 committed expert skills, found {len(skills)}")
    assert_clean(root, inputs, commit)
    return dict(sorted(inputs.items())), commit


def assert_clean(root: Path, inputs: dict[str, Path], commit: str) -> None:
    if git(root, "rev-parse", "HEAD").decode("ascii").strip() != commit:
        raise ValueError("Source commit changed while packaging")
    changed = set(git(root, "diff", "--name-only", "-z", "HEAD", "--").decode("utf-8").rstrip("\0").split("\0"))
    selected = {path.relative_to(root).as_posix() for path in inputs.values()}
    dirty = selected & changed
    if dirty:
        raise ValueError("Release source has uncommitted changes: " + ", ".join(sorted(dirty)))


def binary_info(binary: Path) -> dict:
    binary = binary.absolute()
    for path in (binary, *binary.parents):
        if path.exists() or path.is_symlink():
            attrs = path.lstat()
            if path.is_symlink() or getattr(attrs, "st_file_attributes", 0) & 0x400:
                raise ValueError("Binary path must not traverse a link or reparse point")
    if not binary.is_file() or not 128 <= binary.stat().st_size <= MAX_FILE:
        raise ValueError("Supply a regular Windows executable between 128 bytes and 512 MiB")
    with binary.open("rb") as source:
        header = source.read(64)
        if header[:2] != b"MZ":
            raise ValueError("Supplied binary is not a Windows PE executable")
        offset = struct.unpack_from("<I", header, 0x3C)[0]
        if not 64 <= offset <= min(binary.stat().st_size - 6, 4 * 1024 * 1024):
            raise ValueError("Invalid Windows PE header offset")
        source.seek(offset)
        header = source.read(6)
        if header[:4] != b"PE\0\0":
            raise ValueError("Invalid Windows PE signature")
        architecture = {0x8664: "x86_64", 0xAA64: "arm64", 0x14C: "x86"}.get(struct.unpack_from("<H", header, 4)[0])
        if architecture is None:
            raise ValueError("Unsupported Windows PE architecture")
    return {"path": BINARY_NAME, "bytes": binary.stat().st_size, "sha256": digest(binary),
            "platform": "windows", "architecture": architecture,
            "validation": "PE signature and architecture only; executable not run",
            "build_provenance": "user_supplied; source-to-binary correspondence not attested"}


def release_readme(version: str, architecture: str, commit: str) -> str:
    return f"""# Civil Buddy 统一土木工作台 {version}

本包包含 Windows {architecture} 的 Rust 主程序、固定 Python 工具服务、原 Python 工作台、
66 个岗位 SOP、知识库、文档技能，以及 Rust 源码和架构文档。需要另备 **Python 3.11 或更高版本**。
不包含 Python 解释器、API Key、`.env`、运行历史或用户工程资料。

新比赛仓库：https://github.com/LUOaini1213/civil-buddy-sme 。
比赛主线为英文招标与箱单联动；全部演示资料均为合成样例。依赖安装后可运行
`python scripts/demo_facade.py` 复跑离线演示，不需要模型密钥。

## 首次准备

先完整解压到可写目录，再在解压根目录打开 PowerShell。请手动建立本地虚拟环境并安装依赖：

```powershell
py -3.11 -m venv .venv
& ".\\.venv\\Scripts\\python.exe" -m pip install -r requirements.txt -r requirements-documents.txt
```

安装需要网络；上述命令只修改本目录 `.venv`，不会安装全局依赖。统一启动器不自动安装任何依赖。
已有兼容解释器时，可直接使用其完整路径，并把同一路径传给 `--python`。

## 统一入口

先检查解释器、必需依赖、可执行文件、端口和目录配置：

```powershell
& ".\\.venv\\Scripts\\python.exe" scripts/start_unified_workbench.py --binary bin/civil-workbench.exe --python .venv/Scripts/python.exe --state-root runtime/unified --check
```

`--check` 不启动产品、不访问模型、不创建工程或状态文件；它会运行限时的 Python 依赖探测。
缺少 CAD、工程、语音等可选依赖只显示能力提示。普通启动也会执行相同的预检。

```powershell
& ".\\.venv\\Scripts\\python.exe" scripts/start_unified_workbench.py --binary bin/civil-workbench.exe --python .venv/Scripts/python.exe --state-root runtime/unified --open
```

主程序和 Python 领域服务均监听本机；浏览器打开 `http://127.0.0.1:8765/static/agent.html`。
保留启动窗口，按 Ctrl+C 停止两个服务。端口冲突时追加 `--port 8766`。
`--state-root` 保存产品状态与日志；新文档副本保存在所选工程目录 `.civil-buddy/out`。
上面的相对状态目录用于本机演示。具名日常工程应将状态放在包外；升级时保留工程根、
状态基础目录的两个绝对路径和 `--user-id`，勿将旧 `.env` 或状态混入分发包。

无需模型 Key 可执行资料结构检查。自然语言 Agent 任务需在页面模型设置配置兼容服务，
或显式传入自己创建的 `--env-file <路径>`；`.env.example` 仅为模板。Jev 为可选工程决策建议，默认关闭。
岗位签认、引用哈希、先预览再保存及只读权限仍由主程序执行；模型不能把文件或自身提议变成工程事实。

箱单受限重排示例见 `examples/packing-replan/README.md`。将示例复制到测试工程目录，
在 Agent 页勾选 JSON 并显式选择“箱单重排核对”，使用“检查资料”可不调用模型。
示例尺寸与约束均为合成测试输入；不能据此装运放行。普通 Excel 箱单仍使用装箱/物流页面。

具名账号和独立工程启动、登录、停机备份及原路径恢复见 `docs/civil-buddy/release-handoff.md`。
具名模式使用 `--user-id`、`--workspace`、`--token-file`；每位用户独立目录与进程，
不是同一进程多租户平台。`examples/facade-demo` 是明确标注的合成演示资料。

例如已按交接指南建立工程目录和个人口令后，可在**新版解压目录**继续启动原实例：

```powershell
& ".\\.venv\\Scripts\\python.exe" scripts/start_unified_workbench.py --binary bin/civil-workbench.exe --python .venv/Scripts/python.exe --state-root C:/CivilBuddyState/teammate-a/demo --user-id teammate-a --workspace C:/CivilJobs/demo --token-file C:/CivilBuddySecrets/teammate-a/login-token.txt --port 8765 --open
```

每次传最初的 `--state-root` 基础目录；启动器会追加 `accounts/<user>/projects/<root-hash>/`，
不要把终端 `State:` 显示的叶目录再次传入。若旧实例最初用了 `runtime/unified`，新版必须继续
指向旧程序目录中该基础目录的绝对路径，不能复制到新版目录后直接重新绑定。
个人完整备份需先停止两个服务，再复制整个工程与状态基础目录到新的空备份目录；
恢复只允许原绝对路径且目标不存在或为空，遇到非空目录拒绝覆盖。交接指南提供可执行步骤。
完整个人备份含归属数据库，不能交给同事；同事使用支持导入的业务项目包建立新副本，
这不会迁移 Rust Agent 的完整执行历史，也不会继承签认。

## 原入口与可选能力

原 Python CLI `python -m packing_assistant.civil` 和 `start-workbench.bat` 保留。
旧启动器可能按原流程准备本地 `.venv`；统一入口不调用该安装流程。两种入口不要使用同一个端口同时运行。
CAD、工程计算、排程、语音依赖分别见 `requirements-cad.txt`、`requirements-engineering.txt`、
`requirements-planning.txt`、`requirements-asr.txt`，请只在同一虚拟环境内按需安装。
Java/JVM、Office 或 OCR 引擎均不随包提供。

文档副本只是待核查草稿；PDF 正文改写/OCR、Office 排版渲染与 Excel 公式重算尚未提供。
岗位目录不代表每个岗位都有数值求解器；任何工程结论仍需有资格的人审核签认。

## 来源与校验

源码提交：`{commit}`。`release-manifest.json` 记录除自身以外每个包内文件的 SHA-256、大小，
并独立记录用户指定二进制的哈希。该打包脚本只检查 PE 文件头，**不证明该 exe 由本次源码编译**。
同名 `.zip.sha256` 校验整个 ZIP，同名 `.manifest.json` 是包内 manifest 的副本。
可使用 Python 标准库校验器检查成员和逐文件哈希：

```powershell
python scripts/build_unified_release.py --verify <压缩包路径>
```

架构及已实现边界见 [统一工作台](docs/civil-buddy/unified-workbench.md)、
[实现与验收](docs/civil-buddy/architecture/implementation.md)、
[Rust 架构](docs/civil-buddy/architecture/civil-buddy-rust-architecture.md)、
[Agent 基础设施](docs/civil-buddy/architecture/civil-buddy-agent-infra.md)、
[文档技能](docs/civil-buddy/architecture/civil-buddy-document-skills.md)。
Rust 开发可用 `cargo build --release --manifest-path workbench/Cargo.toml`；需要自行安装 Rust 工具链。
完整测试和研究资料仍以完整源码仓库为准。
"""


def verify_archive(archive: Path, expected_manifest: dict | None = None) -> dict:
    with zipfile.ZipFile(archive) as package:
        members = package.infolist()
        seen = set()
        total = 0
        for item in members:
            safe_name(item.filename)
            if item.filename.casefold() in seen or item.is_dir() or stat.S_ISLNK(item.external_attr >> 16):
                raise ValueError("Duplicate, directory, or linked archive member")
            if item.flag_bits & 1 or item.file_size > MAX_FILE:
                raise ValueError("Encrypted or oversized archive member")
            seen.add(item.filename.casefold())
            total += item.file_size
        if total > MAX_ARCHIVE_CONTENT:
            raise ValueError("Archive content exceeds 1 GiB")
        manifest = json.loads(package.read(MANIFEST_NAME))
        if (manifest.get("schema_version") != 1 or manifest.get("product") != "civil-buddy-unified-workbench"
                or not isinstance(manifest.get("files"), list)):
            raise ValueError("Unknown release manifest")
        if expected_manifest is not None and manifest != expected_manifest:
            raise ValueError("Archive manifest differs from the build plan")
        expected = {MANIFEST_NAME}
        folded = {MANIFEST_NAME.casefold()}
        for entry in manifest["files"]:
            name = entry["path"]
            safe_name(name)
            if name.casefold() in folded or not re.fullmatch(r"[a-f0-9]{64}", entry["sha256"]):
                raise ValueError("Invalid or duplicate manifest entry")
            expected.add(name)
            folded.add(name.casefold())
            actual = hashlib.sha256()
            size = 0
            with package.open(name) as source:
                for chunk in iter(lambda: source.read(1024 * 1024), b""):
                    actual.update(chunk)
                    size += len(chunk)
            if size != entry["bytes"] or actual.hexdigest() != entry["sha256"]:
                raise ValueError("Archive checksum mismatch: " + name)
        if expected != {item.filename for item in members}:
            raise ValueError("Archive members differ from manifest")
        if BINARY_NAME not in expected or "scripts/start_unified_workbench.py" not in expected:
            raise ValueError("Unified runtime entry points missing")
        binary_entry = next(entry for entry in manifest["files"] if entry["path"] == BINARY_NAME)
        if any(manifest.get("binary", {}).get(field) != binary_entry[field] for field in ("path", "bytes", "sha256")):
            raise ValueError("Binary metadata differs from payload")
    return manifest


def build_release(root: Path, version: str, binary: Path, output_dir: Path,
                  staging_dir: Path | None = None) -> tuple[Path, Path, dict]:
    legacy.validate_version(version)  # Validate before any write.
    root = root.resolve()
    inputs, commit = committed_inputs(root)
    info = binary_info(binary)
    output_dir = output_dir.resolve()
    staging_dir = (staging_dir or root / "work" / "release-staging").resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    staging_dir.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=f"unified-{version}-", dir=staging_dir))
    entries = []
    for name, source in {**inputs, BINARY_NAME: binary}.items():
        target = legacy.checked_path(stage, name)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        if target.stat().st_size > MAX_FILE:
            raise ValueError("Oversized release input: " + name)
        entries.append({"path": name, "bytes": target.stat().st_size, "sha256": digest(target)})
    if digest(stage / BINARY_NAME) != info["sha256"]:
        raise ValueError("Supplied binary changed while copying")
    readme = stage / "README.md"
    readme.write_text(release_readme(version, info["architecture"], commit), encoding="utf-8", newline="\n")
    entries.append({"path": "README.md", "bytes": readme.stat().st_size, "sha256": digest(readme)})
    manifest = {"schema_version": 1, "product": "civil-buddy-unified-workbench", "version": version,
                "runtime": "rust-host+fixed-python-workers", "python_min": "3.11", "expert_skills": 66,
                "source_commit": commit, "source_policy": "committed allowlist; selected working tree files clean",
                "binary": info, "files": sorted(entries, key=lambda entry: entry["path"])}
    manifest_bytes = (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    (stage / MANIFEST_NAME).write_bytes(manifest_bytes)
    assert_clean(root, inputs, commit)
    stem = f"civil-buddy-unified-workbench-{version}-windows-{info['architecture']}"
    archive = output_dir / (stem + ".zip")
    fd, temporary = tempfile.mkstemp(prefix=".unified-", suffix=".zip", dir=output_dir)
    os.close(fd)
    try:
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as package:
            for name in sorted([*(entry["path"] for entry in entries), MANIFEST_NAME]):
                package.write(legacy.checked_path(stage, name), name)
        verify_archive(Path(temporary), manifest)
        assert_clean(root, inputs, commit)
        os.replace(temporary, archive)
    finally:
        Path(temporary).unlink(missing_ok=True)
    archive.with_suffix(".manifest.json").write_bytes(manifest_bytes)
    archive.with_suffix(".zip.sha256").write_text(f"{digest(archive)}  {archive.name}\n", encoding="ascii")
    return archive, stage, manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", default="0.5.0-preview")
    parser.add_argument("--binary", type=Path, help="Explicit Windows Rust executable; never built or executed")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "dist")
    parser.add_argument("--staging-dir", type=Path)
    parser.add_argument("--verify", type=Path, help="Verify an existing ZIP without Git, installing or launching anything")
    args = parser.parse_args(argv)
    try:
        if args.verify:
            manifest = verify_archive(args.verify)
            print(json.dumps({"verified": True, "archive": str(args.verify), "files": len(manifest["files"]),
                              "version": manifest["version"]}, ensure_ascii=False))
            return 0
        if not args.binary:
            parser.error("--binary is required when building a release")
        archive, stage, manifest = build_release(ROOT, args.version, args.binary, args.output_dir, args.staging_dir)
    except (OSError, ValueError, KeyError, TypeError, subprocess.CalledProcessError, zipfile.BadZipFile) as exc:
        parser.exit(1, f"Unified packaging failed: {exc}\n")
    print(json.dumps({"archive": str(archive), "stage": str(stage), "manifest": str(archive.with_suffix('.manifest.json')),
                      "sha256": digest(archive), "bytes": archive.stat().st_size,
                      "source_commit": manifest["source_commit"], "verified": True}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
