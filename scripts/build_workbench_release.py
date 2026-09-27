"""Build an allowlisted Python source release, without deleting existing staging trees."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
VERSION = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z]+(?:[.-][0-9A-Za-z]+)*)?")
STATIC = (
    "app.js", "chat-stream.js", "docpreview.js", "fixcard.js", "index.html",
    "manifest.webmanifest", "posts.js", "studio.js", "styles.css", "tickets.js", "voice.js",
    "sw.js", "modules/auth.js", "modules/toast.js", "modules/drafts.js", "modules/uploads.js",
    "modules/turn-stream.js", "modules/deliverables.js", "modules/session-watch.js", "modules/session-nav.js",
    "agent.html", "agent.css", "agent.js", "theme.css", "theme.js", "workbench-shell.css",
    "icons/cb-icon-192.png", "icons/cb-icon-512.png", "icons/cb-icon.svg",
    "vendor/marked.LICENSE.md", "vendor/marked.min.js",
    "cad.html", "cad.css", "cad.js", "cad-viewer.js",
    "engineering.html", "engineering.css", "engineering.js", "engineering-notices.txt",
    "engineering-schedule.html", "engineering-schedule.css", "engineering-schedule.js",
    "engineering-schedule-state.js",
    "engineering-planning.html", "engineering-planning.css", "engineering-planning.js",
    "engineering-routing.html", "engineering-routing.css", "engineering-routing.js",
    "logistics.html", "logistics.css", "logistics.js",
    "vendor/frappe-gantt-1.2.2/frappe-gantt.css", "vendor/frappe-gantt-1.2.2/frappe-gantt.es.js",
    "vendor/frappe-gantt-1.2.2/license.txt", "vendor/frappe-gantt-1.2.2/SOURCE.json", "vendor/frappe-gantt-1.2.2/package.json",
    "vendor/three/three.module.js", "vendor/three/three.core.js",
    "vendor/three/OrbitControls.js", "vendor/three/LICENSE.txt", "vendor/three/manifest.json",
)
EXPLICIT = (
    "gateway/__init__.py", "gateway/app.py", "frontend/workbench.html",
    "frontend/vendor/vue.min.js", "frontend/vendor/vue.LICENSE",
    "frontend/vendor/marked.min.js", "frontend/vendor/marked.LICENSE.md",
    "frontend/vendor/cb-doc.js", "frontend/vendor/cb-fix.js",
    "LICENSE", "requirements.txt", ".env.example", "demo/.env.example",
    "TRY.md", "docs/depth-ladder.md", "docs/civil-buddy/GETTING-STARTED.md",
    "docs/civil-buddy/PROTOCOL.md", "docs/civil-buddy/MCP.md", "docs/civil-buddy/SKILLS.md",
    "docs/civil-buddy/KB.md", "docs/civil-buddy/CONTEXT.md", "docs/civil-buddy/COLLABORATION.md",
    "docs/civil-buddy/task-routing.md", "docs/civil-buddy/product-plan.md", "docs/civil-buddy/product-completion-plan.md",
    "contract/intents.v1.json", "contract/projects.v1.json", "contract/kb_boosts.v1.json",
    "workbench/seed.json", "workbench/yibiao-map.json",
    "scripts/start_workbench.py",
    "scripts/start_unified_workbench.py", "requirements-documents.txt", "docs/civil-buddy/unified-workbench.md",
    "skills/document/manifest.json", "skills/document/doc-word/SKILL.md", "skills/document/doc-spreadsheet/SKILL.md",
    "skills/document/doc-pdf/SKILL.md", "skills/document/doc-review/SKILL.md",
    # 语音输入：页面加载 voice.js；本机识别需要术语表，装依赖的说明在 requirements-asr.txt 里
    "demo/asr_lexicon.txt", "requirements-asr.txt", "docs/voice-input.md",
    "requirements-cad.txt", "docs/civil-buddy/cad-to-3d.md",
    "requirements-analysis.txt", "requirements-engineering.txt", "docs/civil-buddy/engineering-workbench.md",
    "requirements-planning.txt", "docs/civil-buddy/planning-workbench.md",
    "requirements-logistics.txt", "requirements-logistics-ocr.txt",
    "scripts/prepare_logistics_ocr.py", "scripts/smoke_logistics_ocr.py",
    "docs/civil-buddy/logistics-workbench.md", "docs/civil-buddy/logistics-packing-list-agent-research.md",
    "docs/civil-buddy/open-source-integrate.md", "docs/civil-buddy/open-source-integrate.xlsx",
    "examples/cad-to-3d/synthetic-building-mm.dxf", "examples/cad-to-3d/synthetic-hollow-section-mm.dxf",
    "examples/cad-to-3d/README.md",
    "examples/cad-to-3d/EXTENDED-SAMPLES.md", "examples/cad-to-3d/generate_extended_samples.py",
    "examples/cad-to-3d/synthetic-curved-building-mm.dxf", "examples/cad-to-3d/synthetic-curved-building-config.json",
    "examples/cad-to-3d/synthetic-bulge-section-mm.dxf", "examples/cad-to-3d/synthetic-bulge-section-config.json",
)
ASSET_ROOTS = ("demo/kb", "knowledge", "knowledge_base", "skills/civil-buddy", ".agents/skills")


def validate_version(version: str) -> str:
    if len(version) > 64 or VERSION.fullmatch(version) is None:
        raise ValueError("Version must be MAJOR.MINOR.PATCH with an optional safe prerelease suffix.")
    return version


def checked_path(root: Path, relative: str) -> Path:
    rel = PurePosixPath(relative)
    if rel.is_absolute() or any(part in {"", ".", ".."} for part in rel.parts) or "\\" in relative or ":" in relative:
        raise ValueError(f"Unsafe release path: {relative}")
    path = root.joinpath(*rel.parts)
    path.resolve().relative_to(root.resolve())
    for parent in (path, *path.parents):
        if parent == root:
            break
        if parent.exists() or parent.is_symlink():
            attrs = parent.lstat()
            if parent.is_symlink() or getattr(attrs, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400):
                raise ValueError(f"Links/reparse points are not release inputs: {relative}")
    return path


def tracked_assets(root: Path) -> list[str]:
    # Restrict content/knowledge to explicitly versioned inputs. A local uploaded
    # markdown file, owner catalog, or project attachment is never swept into a zip.
    result = subprocess.run(["git", "ls-files", "-z", "--", *ASSET_ROOTS], cwd=root,
                            check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return result.stdout.decode("utf-8").rstrip("\0").split("\0")


def asset_allowed(name: str) -> bool:
    path = PurePosixPath(name)
    if name.startswith(".agents/skills/"):
        return len(path.parts) == 4 and path.name == "SKILL.md"
    if name.startswith("demo/kb/"):
        return path.suffix == ".md" and not any(p.startswith(".") for p in path.parts)
    if name.startswith(("knowledge/", "knowledge_base/")):
        return path.suffix in {".md", ".json", ".yaml"} and not any(p.startswith(".") for p in path.parts)
    if name.startswith("skills/civil-buddy/"):
        return path.suffix in {".md", ".py", ".docx"} and "__pycache__" not in path.parts
    return False


def release_inputs(root: Path) -> dict[str, Path]:
    names = set(EXPLICIT)
    names.update("demo/static/" + name for name in STATIC)
    names.update(name for name in tracked_assets(root) if name and asset_allowed(name))
    # Source roots are deliberate: current new product modules are included before
    # commit, but data, .env, caches, output, test fixtures and binaries are excluded.
    names.update(path.relative_to(root).as_posix() for path in (root / "packing_assistant").rglob("*.py")
                 if "__pycache__" not in path.parts and "tests" not in path.parts)
    names.update(path.relative_to(root).as_posix() for path in (root / "demo").glob("*.py"))
    result = {name: checked_path(root, name) for name in sorted(names)}
    result["start-workbench.bat"] = checked_path(root, "scripts/start-workbench.bat")
    for name, source in result.items():
        if not source.is_file():
            raise FileNotFoundError(f"Required release input missing: {name}")
    skills = [name for name in names if name.startswith(".agents/skills/") and name != ".agents/skills/civil-buddy/SKILL.md"]
    if len(skills) != 66:
        raise ValueError(f"Expected 66 expert skills, found {len(skills)}")
    return result


def release_readme(version: str) -> str:
    return f"""# Civil Buddy Python 工作台 {version}

这是当前 `civil app` 产品的源码分发包，需要 Python 3.10 或更高版本。
包含 66 个岗位 skills、知识库、Web 工作台和 Python 岗位工具；不包含 Python 解释器或 Rust exe。

1. 将整个压缩包解压到一个可写目录，勿直接在压缩软件内启动。
2. Windows 双击 `start-workbench.bat`。首次缺少依赖时仅在本目录创建 `.venv`，
   并按 `requirements.txt` 安装依赖；首次安装需要网络。不会安装或升级系统 Python 包。
3. 服务健康检查通过后自动打开浏览器。保留启动窗口；按 Ctrl+C 停止。
4. 若已有安装完依赖的解释器，可先设置 `CIVIL_PYTHON` 为其完整 `python.exe` 路径。
   重新安装本包依赖：`start-workbench.bat --setup`。
   服务器/测试模式：`start-workbench.bat --no-browser --port 8766`。

无需 API Key 即可问岗位能力、使用已实现的离线模板。例如选择“项目日报”后输入：
“写一份项目日报模板，项目：试用工程，日期：2026-09-12，天气：晴，其他内容待填”。
可下载实际生成的 Markdown / XLSX 文件，并从会话列表恢复记录。
开放式模型问答需在界面的模型设置中配置兼容 API；Key 不随包分发。
CAD → 3D 建模可从首页进入；先用本包 Python 安装可选依赖：
`python -m pip install -r requirements-cad.txt`。两份内置 DXF 为合成演示样例。
临时图纸缓存约 30 分钟；使用“保存项目”持久保存原图、草稿和成功模型版本，重启后可从最近项目恢复。
工程计算与计划从首页进入。截面性质、梁/杆系和 IFC 检查另需安装：
`python -m pip install -r requirements-engineering.txt`；甘特图资源已随包提供。
施工排程在 `/engineering/planning`，场内最短路线在 `/engineering/routes`。
排程页支持对话提出明确改参建议，核对原值与新值后确认计算；保存后可从统一 Agent 对话继续。
与同事交接请使用排程页的完整 ZIP 项目包，包含已保存结果、基线、周承诺、保留历史和已上传原件。
缺失原件会列出，可按文件摘要补齐；导入创建新副本并重置签认确认，不会覆盖原项目。
CPM 工作日排程使用标准库；资源容量排程、路线与计划文件交换另需安装：
`python -m pip install -r requirements-planning.txt`。这些可选工程依赖建议使用 Python 3.11 或更高版本，
并用启动本包的同一解释器安装。MPP 导入还需要可用的 Java/JVM；本包不含 Java，
不会自动安装系统 Java。当前可用格式以页面探测为准；原生 MPP 只读，导出使用 Project XML。
工程分析结果须点击保存；计划须点击保存计划。内置 DXF、梁和 IFC 均为合成测试数据，不代替真实工程验收。
物流材料在 `/logistics`，安装 `requirements-logistics.txt` 后可读取表格与数字 PDF。
扫描件另需独立 OCR 环境及官方模型预热，步骤见 `docs/civil-buddy/logistics-workbench.md`；
模型未就绪会明确提示。先核对来源和修订差异，再确认台账；完整 ZIP 项目包可交接原件与版本历史。
如使用环境文件，只编辑本包 `.env` 或 `demo/.env`；`.env.example` 只是样例。

产物保存在本包 `demo/out/`，上传和本地目录配置保存在本包目录；升级前可在设置菜单备份各任务，再导入新包。任务备份不含模型 Key、全局知识库和外部作业目录。
默认不授予任意外部作业目录访问。装箱求解器未包含，不能据此声称求解已通过。
高风险草稿仍需输入“我明白，将由持证人员签认”；所有交付物都是内部讨论草稿，
`submit_blocked=true`，不是法定签认件。66 岗目录不意味着所有专业均具备数值求解器。

本包是 Python 工作台，不声称与历史 `civil-workbench.exe` Rust 试用包功能相同。
`release-manifest.json` 列出逐文件 SHA-256；主包 SHA-256 在同名 `.zip.sha256` 中。

包内文档：[试用说明](TRY.md)、[上手指南](docs/civil-buddy/GETTING-STARTED.md)、
[岗位深度](docs/depth-ladder.md)、[产品计划](docs/civil-buddy/product-plan.md)、
[完成情况](docs/civil-buddy/product-completion-plan.md)、[协议](docs/civil-buddy/PROTOCOL.md)、
[MCP](docs/civil-buddy/MCP.md)、[Skills](docs/civil-buddy/SKILLS.md)、[知识库](docs/civil-buddy/KB.md)。
[施工排程与路线说明](docs/civil-buddy/planning-workbench.md) 记录工作日历、资源方案、导入差异及交接边界。
开发文档中涉及完整测试套件和其他服务的命令需使用完整源码仓库；本包用于运行工作台。
"""


def build_release(root: Path, version: str) -> tuple[Path, Path]:
    validate_version(version)  # Validate before any directory creation or write.
    root = root.resolve()
    inputs = release_inputs(root)
    dist = checked_path(root, "dist")
    dist.mkdir(exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=f"workbench-python-{version}-", dir=dist))
    stage.resolve().relative_to(dist.resolve())
    entries = []
    for name, source in inputs.items():
        target = checked_path(stage, name)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        entries.append({"path": name, "bytes": target.stat().st_size,
                        "sha256": hashlib.sha256(target.read_bytes()).hexdigest()})
    readme = stage / "README.md"
    readme.write_text(release_readme(version), encoding="utf-8")
    entries.append({"path": "README.md", "bytes": readme.stat().st_size,
                    "sha256": hashlib.sha256(readme.read_bytes()).hexdigest()})
    manifest = {"product": "civil-buddy-python-workbench", "version": version,
                "runtime": "python", "python_min": "3.10", "expert_skills": 66,
                "files": sorted(entries, key=lambda entry: entry["path"])}
    (stage / "release-manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    archive = checked_path(dist, f"civil-buddy-python-workbench-{version}.zip")
    fd, temporary = tempfile.mkstemp(prefix=".workbench-", suffix=".zip", dir=dist)
    os.close(fd)
    try:
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as output:
            for path in sorted(stage.rglob("*")):
                if path.is_file():
                    output.write(path, path.relative_to(stage).as_posix())
        os.replace(temporary, archive)
    finally:
        Path(temporary).unlink(missing_ok=True)
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    archive.with_suffix(".zip.sha256").write_text(f"{digest}  {archive.name}\n", encoding="ascii")
    return archive, stage


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", default="0.9.0-preview")
    args = parser.parse_args()
    try:
        archive, stage = build_release(ROOT, args.version)
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        parser.exit(1, f"Packaging failed: {exc}\n")
    print(json.dumps({"archive": str(archive), "stage": str(stage), "bytes": archive.stat().st_size}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
