"""Authorized job folder + Office interchange (WorkBuddy local-file slice).

NL run writes real .xlsx next to table drafts so Excel can open them.
If the user names an existing workbook in CIVIL_JOB_ROOT, patch only CB草稿-*
sheets and leave the owner's sheets alone.
Not a desktop shell. Not D:\\layout. Not COM into an open Excel window.
"""

from __future__ import annotations

import html
import json
import os
import re
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional, Sequence, Tuple
from zipfile import BadZipFile
from packing_assistant.document_text import csv_text, docx_document_text, table_markdown

JOB_EXTS = {".xlsx", ".csv", ".txt", ".md", ".json", ".docx", ".log"}
JOB_MAX_FILES = 12
JOB_FILE_CHARS = 8_000
JOB_TOTAL_CHARS = 48_000
#: A tender document is read whole. 8 000 characters are the first five pages of a hundred: the scoring
#: table, the rejection clauses and the forms all lie behind them. These are the limits for the posts that
#: parse a document themselves (no model context to fit into).
DOCUMENT_FILE_CHARS = 2_000_000
DOCUMENT_TOTAL_CHARS = 6_000_000
#: in a blob: the file above was longer than what was read. Said, never silent.
CUT = "（未读完）"
DRAFT_PREFIX = "CB草稿"
_OFFICE_CONTENT_ERRORS = (BadZipFile, ValueError, KeyError, SyntaxError)

FORBIDDEN_LAYOUT = ("d:\\layout", "d:/layout")


def is_forbidden_layout(path: Path) -> bool:
    n = str(path).replace("/", "\\").rstrip("\\").lower()
    return n == "d:\\layout" or n.startswith("d:\\layout\\")


#: A job folder for the current request only (the gateway's upload route, gateway/web_link.py). A context
#: variable, not CIVIL_JOB_ROOT: two uploads at once each see their own folder, and nothing is added to the
#: sandbox roots — every read still goes through sandbox.assert_open, so a folder outside them stays closed.
_JOB_ROOT_SCOPE: ContextVar[Optional[Path]] = ContextVar("civil_job_root_scope", default=None)


@contextmanager
def job_root_scope(folder: Path) -> Iterator[Path]:
    """Within the block, job_root() is ``folder`` for this thread / task and the threads it starts with
    contextvars.copy_context() (as ToolEngine.execute does)."""
    token = _JOB_ROOT_SCOPE.set(Path(folder))
    try:
        yield Path(folder)
    finally:
        _JOB_ROOT_SCOPE.reset(token)


def job_root() -> Path:
    scoped = _JOB_ROOT_SCOPE.get()
    if scoped is not None:
        return scoped
    raw = (os.getenv("CIVIL_JOB_ROOT") or "").strip()
    if raw:
        p = Path(raw).expanduser()
        if not is_forbidden_layout(p):
            return p
    return Path.cwd() / ".civil-buddy" / "out"


def _sheet_name(title: str, used: set) -> str:
    t = re.sub(r'[:\\/?*\[\]]', " ", title or "表").strip() or "表"
    t = t[:31]
    base = t
    i = 2
    while t.casefold() in {name.casefold() for name in used}:
        suffix = f"_{i}"
        t = (base[: 31 - len(suffix)] + suffix)
        i += 1
    used.add(t)
    return t


def tables_from_md(md: str) -> List[Tuple[str, List[List[str]]]]:
    """Return (sheet_name, rows) for each markdown table. Caption from last heading."""
    lines = (md or "").splitlines()
    heading = "表"
    used: set = set()
    out: List[Tuple[str, List[List[str]]]] = []
    i = 0
    while i < len(lines):
        raw = lines[i].rstrip()
        hs = raw.lstrip()
        if hs.startswith("#"):
            heading = hs.lstrip("#").strip() or heading
            i += 1
            continue
        if hs.startswith("|") and i + 1 < len(lines) and re.match(r"^\s*\|?\s*:?-{3,}", lines[i + 1]):
            rows: List[List[str]] = []
            while i < len(lines) and lines[i].lstrip().startswith("|"):
                # Decode after splitting: encoded pipes are cell contents, not
                # Markdown column separators. Excel receives the original text.
                row = lines[i].strip()[1:]
                if row.endswith("|"):
                    row = row[:-1]
                cells = [html.unescape(c.strip()) for c in row.split("|")]
                if cells and not all(re.match(r"^:?-{3,}:?$", c or "") for c in cells):
                    rows.append(cells)
                i += 1
            if rows:
                out.append((_sheet_name(heading, used), rows))
            continue
        i += 1
    return out


def _sheet_text(value: Any) -> Any:
    """Text a worksheet will accept.

    A cell's text reaches us from the user - pasted out of a terminal, a PDF or another workbook -
    and may carry control characters that the file format does not allow. openpyxl raises on them,
    which used to fail the whole export and with it the turn. They are not information: they are
    dropped, and the visible text is written.
    """
    if not isinstance(value, str):
        return value
    return "".join(ch for ch in value if ch in "\t\n\r" or ord(ch) >= 32)


def _write_rows(worksheet: Any, rows: List[List[str]]) -> None:
    """Draft tables contain text, including strings that look like formulas."""
    for r_i, row in enumerate(rows, 1):
        for c_i, value in enumerate(row, 1):
            cell = worksheet.cell(r_i, c_i, _sheet_text(value))
            if isinstance(value, str):
                cell.data_type = "s"


def _save_workbook(workbook: Any, path: Path) -> Path:
    from io import BytesIO

    from packing_assistant.sandbox import guarded_write_bytes

    try:
        with BytesIO() as buffer:
            workbook.save(buffer)
            return guarded_write_bytes(path, buffer.getvalue())
    finally:
        workbook.close()


def write_xlsx(path: Path, sheets: List[Tuple[str, List[List[str]]]]) -> Path:
    import openpyxl

    wb = openpyxl.Workbook()
    first = True
    used: set = set()
    for name, rows in sheets:
        ws = wb.active if first else wb.create_sheet()
        first = False
        ws.title = _sheet_name(name, used)
        _write_rows(ws, rows)
    return _save_workbook(wb, Path(path))


def _query_from_md(md: str) -> str:
    if "## 用户原文" in (md or ""):
        return (md or "").split("## 用户原文", 1)[1].split("##", 1)[0].strip()
    return (md or "")[:400]


def pick_job_xlsx(query: str) -> Path | None:
    """Existing job-root workbook the user named. Do not guess the first file."""
    q = (query or "").lower()
    if not q:
        return None
    for f in list_job_files():
        if f.get("suffix") != ".xlsx":
            continue
        name = str(f.get("name") or "")
        stem = Path(name).stem.lower()
        if name.lower() in q or (stem and stem in q):
            return Path(str(f["path"]))
    return None


def patch_xlsx(path: Path, sheets: List[Tuple[str, List[List[str]]]]) -> Path:
    """Replace only CB草稿-* sheets. Owner sheets stay."""
    import openpyxl

    resolved = _resolve_job_file(path)
    if not sheets:
        return resolved
    wb = openpyxl.load_workbook(resolved)
    for name in list(wb.sheetnames):
        if name.startswith(f"{DRAFT_PREFIX}-"):
            del wb[name]
    used = set(wb.sheetnames)
    for title, rows in sheets:
        ws = wb.create_sheet(_sheet_name(f"{DRAFT_PREFIX}-{title}", used))
        _write_rows(ws, rows)
    return _save_workbook(wb, resolved)


def _resolve_job_file(path: Path) -> Path:
    """Resolve every job-file read/patch through the same root and secret guard."""
    from packing_assistant.sandbox import assert_open

    root = job_root().resolve()
    try:
        resolved = Path(path).resolve()
        resolved.relative_to(root)
    except (OSError, ValueError) as e:
        raise PermissionError("job file outside authorized root") from e
    if is_forbidden_layout(resolved):
        raise PermissionError("D:\\layout denied")
    return assert_open(resolved)


def _exports_manifest() -> Path:
    return job_root() / ".civil-buddy" / "exports.json"


def own_exports() -> set:
    """Names of the Excel copies Civil Buddy itself put in the job folder's top level.

    They are deliverables, not source material. Read back as material they leak one draft into the
    next: a daily report's attendance table turned up inside a deep-excavation scheme (2026-09-19).
    """
    try:
        names = json.loads(_exports_manifest().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return set()
    return {str(name) for name in names} if isinstance(names, list) else set()


def _remember_export(path: Path) -> None:
    from packing_assistant.sandbox import guarded_write_text

    try:
        guarded_write_text(_exports_manifest(), json.dumps(sorted(own_exports() | {path.name}), ensure_ascii=False, indent=2))
    except (OSError, RuntimeError):
        pass    # the copy is still written; at worst it is listed as material until the next export


def publish_root_copy(workbook: Path) -> Optional[Path]:
    """Host side of the OS sandbox: the confined worker cannot write in the job folder itself, so the
    copy of a draft's workbook that normally lands next to the user's files is made here, by the same
    rules — never over a file we did not write, and remembered so it is not read back as material."""
    import shutil

    source = Path(workbook)
    if not job_root_granted() or source.suffix.lower() != ".xlsx" or not source.is_file():
        return None
    dest = job_root() / source.name
    try:
        if dest.resolve() == source.resolve() or (dest.exists() and dest.name not in own_exports()):
            return None
        shutil.copyfile(source, dest)
    except OSError:
        return None
    _remember_export(dest)
    return dest


def export_md_to_xlsx(md_path: Path, query: str = "") -> List[Path]:
    """Sibling xlsx always. If the user named a job-root workbook, patch it too."""
    p = Path(md_path)
    if not p.is_file() or p.suffix.lower() != ".md":
        return []
    text = p.read_text(encoding="utf-8", errors="ignore")
    sheets = tables_from_md(text)
    if not sheets:
        return []
    written: List[Path] = []
    sibling = p.with_suffix(".xlsx")
    written.append(write_xlsx(sibling, sheets))
    if not job_root_granted():
        return written
    q = query or _query_from_md(text)
    target = pick_job_xlsx(q)
    try:
        if target is not None:
            written.append(patch_xlsx(target, sheets))
        else:
            dest = job_root() / sibling.name
            # 根目录里已有同名文件、又不是我们上次写的：那是用户自己的表，不覆盖。
            if dest.resolve() != sibling.resolve() and (not dest.exists() or dest.name in own_exports()):
                written.append(write_xlsx(dest, sheets))
                _remember_export(dest)
    except (OSError, RuntimeError, *_OFFICE_CONTENT_ERRORS):
        pass
    return written


def export_md_to_docx(md_path: Path) -> Path | None:
    """Export a new editable Word sibling, never overwriting an existing file.

    Missing/non-Markdown input is not an export candidate. Content, permission
    and I/O failures propagate so callers cannot report an unsuccessful export
    as a completed deliverable.
    """
    from packing_assistant.sandbox import assert_open, assert_write
    from packing_assistant.runtime.civil_config import load_config
    from packing_assistant.word_export import markdown_docx_bytes

    source = Path(md_path)
    if source.suffix.lower() != ".md":
        return None
    source = assert_open(source)
    if not source.is_file():
        return None
    if not load_config().allow_write():
        raise PermissionError("read-only sandbox: Word export is not allowed")
    if source.stat().st_size > 2 * 1024 * 1024:
        raise ValueError("Markdown 超过 2 MB，请拆分后导出 Word")
    data = markdown_docx_bytes(source.read_text(encoding="utf-8-sig"))
    number = 1
    while True:
        suffix = "" if number == 1 else f"-{number}"
        candidate = source.with_name(source.stem + suffix + ".docx")
        number += 1
        if candidate.exists() or candidate.is_symlink():
            continue
        target = assert_write(candidate)
        try:
            stream = target.open("xb")
        except FileExistsError:
            continue  # A concurrent export won this name; preserve its document.
        try:
            with stream:
                if stream.write(data) != len(data):
                    raise OSError("Word 导出未完整写入")
        except Exception:
            # This invocation created this file exclusively; remove only its
            # incomplete output and propagate the actual failure to the caller.
            try:
                assert_write(target).unlink(missing_ok=True)
            except OSError:
                pass
            raise
        return target


def job_root_granted() -> bool:
    scoped = _JOB_ROOT_SCOPE.get()
    if scoped is not None:
        return scoped.is_dir() and not is_forbidden_layout(scoped)
    raw = (os.getenv("CIVIL_JOB_ROOT") or "").strip()
    if not raw:
        return False
    p = Path(raw).expanduser()
    return p.is_dir() and not is_forbidden_layout(p)


def list_job_files() -> List[Dict[str, Any]]:
    """Files in the authorized job folder. Empty if CIVIL_JOB_ROOT is unset."""
    if not job_root_granted():
        return []
    root = job_root()
    rows: List[Dict[str, Any]] = []
    try:
        names = sorted(root.iterdir(), key=lambda p: p.name.lower())
    except OSError:
        return []
    exported = own_exports()
    for p in names:
        if p.suffix.lower() not in JOB_EXTS or p.name in exported:
            continue
        # CIVIL.md 是给 Civil Buddy 的工程说明，不是待处理的业务资料：当资料读进去，
        # 整份模板（含 "CN / SG / EU / DUAL" 的填写提示）会被抄进成稿并把辖区带偏。
        if p.name == "CIVIL.md":
            continue
        try:
            resolved = _resolve_job_file(p)
            if not resolved.is_file():
                continue
            size = resolved.stat().st_size
        except (OSError, RuntimeError):
            continue
        rows.append(
            {
                "name": p.name,
                "path": str(p),
                "suffix": p.suffix.lower(),
                "bytes": size,
            }
        )
        if len(rows) >= JOB_MAX_FILES:
            break
    return rows


JOB_TREE_EXTS = JOB_EXTS | {".pdf"}
JOB_TREE_MAX = 40


def job_tree_files() -> List[Dict[str, Any]]:
    """The job folder plus one level of sub-folders, PDFs included.

    ``name`` is the path relative to the job folder, with forward slashes. State folders
    (.civil-buddy and anything else starting with . _ ~), CIVIL.md and Office lock files are
    not material. Every row has passed the same root and secret guard as any other job read.
    """
    if not job_root_granted():
        return []
    root = job_root().resolve()
    try:
        folders = [root] + sorted((d for d in root.iterdir() if d.is_dir() and not d.name.startswith((".", "_", "~"))),
                                  key=lambda d: d.name.lower())
    except OSError:
        return []
    rows: List[Dict[str, Any]] = []
    exported = own_exports()
    for folder in folders:
        try:
            entries = sorted(folder.iterdir(), key=lambda entry: entry.name.lower())
        except OSError:
            continue
        for entry in entries:
            if len(rows) >= JOB_TREE_MAX:
                return rows
            if entry.suffix.lower() not in JOB_TREE_EXTS or entry.name == "CIVIL.md" or entry.name.startswith("~$"):
                continue
            if folder == root and entry.name in exported:
                continue
            try:
                resolved = _resolve_job_file(entry)
                if not resolved.is_file():
                    continue
                size = resolved.stat().st_size
            except (OSError, RuntimeError):
                continue
            rows.append({"name": entry.relative_to(root).as_posix(), "path": str(resolved),
                         "suffix": entry.suffix.lower(), "bytes": size})
    return rows


def job_file_by_name(name: Any, *, by_name: bool = True) -> Optional[Path]:
    """One readable file inside the job folder, behind the same root and secret guard as every job read.

    Models (and people) drop the sub-folder — "packing.csv" for "资料/packing.csv". A bare name that
    matches exactly one listed file is that file; two matches stay unresolved, because choosing
    between them is a guess.
    """
    raw = str(name or "").strip().strip('"').strip("'")
    if not raw or not job_root_granted():
        return None
    candidate = Path(raw)
    try:
        target = _resolve_job_file(candidate if candidate.is_absolute() else job_root() / candidate)
    except (OSError, RuntimeError, ValueError):
        return None
    if target.is_file():
        return target
    if by_name and not candidate.is_absolute():
        matches = [row["path"] for row in job_tree_files() if Path(row["name"]).name.lower() == candidate.name.lower()]
        if len(matches) == 1:
            return Path(matches[0])
    return None


def files_named_in(text: str, exts: Optional[Sequence[str]] = None) -> List[Path]:
    """Job files the text names — by relative path, file name, or stem — in listing order.

    A stem made of ASCII letters has to stand alone: "packing" inside "packing-agent" names nothing.
    """
    blob = (text or "").replace("\\", "/").lower()
    if not blob:
        return []
    wanted = {e.lower() for e in exts} if exts else None
    found: List[Path] = []
    for row in job_tree_files():
        if wanted is not None and row["suffix"] not in wanted:
            continue
        name = Path(row["name"]).name.lower()
        stem = Path(row["name"]).stem.lower()
        bounded = r"(?<![a-z0-9_-])" + re.escape(stem) + r"(?![a-z0-9_-])"
        if row["name"].lower() in blob or name in blob or (len(stem) >= 3 and re.search(bounded, blob)):
            found.append(Path(row["path"]))
    return found


def read_material(path: Path, limit: int = JOB_FILE_CHARS) -> str:
    """``read_job_file`` plus PDFs (text layer only; a scanned PDF yields nothing and says so by being empty)."""
    target = _resolve_job_file(path)
    if target.suffix.lower() == ".pdf":
        return pdf_document_text(target)[: max(0, int(limit))]
    return read_job_file(target, limit)


def pdf_document_text(source: Any) -> str:
    """A PDF's text layer as a document again: pages under "〔第N页〕" markers, paragraphs joined, tables rebuilt
    (tools/pdf_layout.py). ``source`` is a path or a binary stream. A scan has no text layer and gives ""."""
    from pypdf import PdfReader

    from packing_assistant.tools import pdf_grid
    from packing_assistant.tools.pdf_layout import pages_text

    reader = PdfReader(str(source) if isinstance(source, Path) else source)
    texts = pdf_grid.document_texts(reader)
    if any(len(text.strip()) >= _MIN_TEXT for text in texts):
        return pages_text(texts)
    return _ocr_pdf_text(source) if isinstance(source, Path) else ""


def _ocr_pdf_text(path: Path) -> str:
    """A scan, read by OCR when the optional packages are installed (tools/ocr.py) - "" when they are not, and the
    caller says the file was not read. The reading is kept beside the job (keyed by the file's sha256): a hundred
    pages take minutes, and the same scan is read once. The first line marks the text as an OCR reading."""
    import hashlib

    from packing_assistant.tools import ocr
    from packing_assistant.tools.pdf_layout import pages_text

    if not ocr.available():
        return ""
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    cache = (job_root() / ".civil-buddy" / "cache" / "ocr" / f"{digest}.txt") if job_root_granted() else None
    if cache is not None and cache.is_file():
        return cache.read_text(encoding="utf-8")
    def progress(done: int, total: int) -> None:
        if done == 1 or done % 5 == 0 or done == total:   # minutes of silence look like a hang
            import sys

            print(f"OCR {path.name}：第 {done}/{total} 页", file=sys.stderr, flush=True)

    pages = ocr.pdf_page_lines(path, on_page=progress)
    text = ocr.MARK + "\n" + pages_text("\n".join(lines) for lines in pages)
    if cache is not None:
        try:
            from packing_assistant.sandbox import guarded_write_text

            guarded_write_text(cache, text)
        except (OSError, RuntimeError):
            pass  # the reading is still returned; it is only not kept
    return text


#: In a blob of job files: the file named on the heading above gave no text. Why follows on the same line.
UNREAD = "（读失败）"
_MIN_TEXT = 8  # the floor demo/uploads.py uses too: fewer characters than this is not a document
_UNREAD_BLOCK = re.compile(r"^###[ \t]+(?P<name>[^\n]+)\n（读失败）(?P<reason>[^\n]*)", re.M)


# ... and what the buyer issues AFTER the tender is the tender's too: 补遗书第1号.pdf, 澄清答疑纪要.docx
_TENDER_NAME = ("招标", "补遗", "澄清", "答疑", "修改通知", "变更通知", "更正公告", "tender", "itt", "rfp", "rfq", "addend", "clarif", "corrigend")
# a bid is many files, and few of them carry 投标 in their name: 技术标.docx, 施工组织设计.docx, 养护方案.docx, 报价文件.docx
_RESPONSE_NAME = ("响应", "应答", "投标", "技术标", "商务标", "经济标", "资信标", "报价", "施工组织设计", "方案", "承诺", "偏离表", "授权委托",
                  "资格审查资料", "项目管理机构", "response", "bid", "proposal", "method statement")


def material_role(name: str) -> str:
    """tender / response / reference, read off a file name - and never guessed past that."""
    low = (name or "").lower()
    return ("tender" if any(mark in low for mark in _TENDER_NAME)
            else "response" if any(mark in low for mark in _RESPONSE_NAME) else "reference")


def unread_reason(path: Path, body: Optional[str]) -> str:
    """Why nothing usable came out of ``path`` - "" when something did. ``body`` is None when the reader
    raised. The parser's own message is never passed on: it can echo bytes of the document."""
    if body is None:
        return "打不开，或内容与扩展名不符"
    if len(body.strip()) >= _MIN_TEXT:
        return ""
    if path.suffix.lower() == ".pdf":
        return "PDF 没有文字层（多半是扫描件）：先 OCR，或另存为 Word、文本；也可装上 OCR 组件（pip install -e .[ocr]）后重试"
    return "里面几乎没有文字"


def read_material_checked(path: Path, limit: int = JOB_FILE_CHARS, *,
                          reader: Optional[Callable[[Path, int], str]] = None) -> Tuple[str, str]:
    """``(text, "")`` for a file that could be read, ``("", why)`` for one that could not.

    A file somebody pointed at and nothing came out of is not the same as no file: a check that was
    given 投标响应.pdf and could not read it has not found the response missing."""
    try:
        body = (reader or read_material)(path, limit)
    except Exception:  # noqa: BLE001 - whatever the parser raised, the file was not read
        return "", unread_reason(path, None)
    reason = unread_reason(path, body)
    return ("", reason) if reason else (body, "")


def unread_files(text: str) -> List[Dict[str, str]]:
    """The files a blob of job files says it could not read: ``[{title, reason}]``."""
    return [{"title": m.group("name").strip(), "reason": m.group("reason").strip()}
            for m in _UNREAD_BLOCK.finditer((text or "").replace("\r\n", "\n"))]


def _read_xlsx_text(path: Path, limit: int) -> str:
    import openpyxl

    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        lines: List[str] = []
        for ws in wb.worksheets:
            lines.append(f"# {ws.title}")
            rows = ws.iter_rows(max_row=min(ws.max_row or 0, 80), max_col=min(ws.max_column or 0, 16), values_only=True)
            lines.append(table_markdown(rows, max(0, limit - sum(len(x) + 1 for x in lines))))
            if sum(len(x) for x in lines) >= limit:
                break
        return "\n".join(lines)[:limit]
    finally:
        wb.close()


def _read_docx_text(path: Path, limit: int) -> str:
    import zipfile
    from xml.etree import ElementTree as ET

    with zipfile.ZipFile(path) as z:
        xml = z.read("word/document.xml")
        numbering = z.read("word/numbering.xml") if "word/numbering.xml" in z.namelist() else None
    root_el = ET.fromstring(xml)
    return docx_document_text(root_el, limit, ET.fromstring(numbering) if numbering else None)


def read_job_file(path: Path, limit: int = JOB_FILE_CHARS) -> str:
    p = _resolve_job_file(path)
    limit = max(0, int(limit))
    if not limit:
        return ""
    suf = p.suffix.lower()
    if suf not in JOB_EXTS:
        raise ValueError("unsupported job file type")
    if suf == ".xlsx":
        return _read_xlsx_text(p, limit)
    if suf == ".docx":
        return _read_docx_text(p, limit)
    with p.open(encoding="utf-8", errors="ignore") as stream:
        text = stream.read(limit)
    return csv_text(text.lstrip("\ufeff"), limit) if suf == ".csv" else text


_BLOB_HEADER = "## 作业根文件（授权文件夹，未再上传）"


def named_files_blob(paths: Sequence[Path], *, reader: Optional[Callable[[Path, int], str]] = None,
                     per_file: int = JOB_FILE_CHARS, total: int = JOB_TOTAL_CHARS) -> str:
    """The same block ``job_files_blob`` builds, for files picked by name (sub-folders and PDFs included).

    ``per_file`` / ``total`` are the prompt-sized defaults; a post that parses the document itself passes
    DOCUMENT_FILE_CHARS / DOCUMENT_TOTAL_CHARS. A file longer than what was read says so under its text."""
    chunks: List[str] = []
    used = 0
    for path in paths:
        room = total - used
        if room < 80:
            chunks.append(f"（还有 {path.name} 未贴全文）")
            continue
        limit = min(per_file, room)
        body, why = read_material_checked(path, limit + 1, reader=reader or read_job_file)
        if why:
            chunks.append(f"### {path.name}\n{UNREAD}{why}")
            continue
        if len(body) > limit:
            body = body[:limit] + f"\n{CUT}只读了前 {limit} 个字符，后面的内容未参与解析"
        block = f"### {path.name}\n{body}"
        chunks.append(block)
        used += len(block)
    return "\n\n".join([_BLOB_HEADER, *chunks]) if chunks else ""


def job_files_blob(query: str = "") -> str:
    """Text of job-root files to prepend on run. Prefer names mentioned in query."""
    if _BLOB_HEADER in (query or ""):
        return ""   # 资料已经由调用方点名贴进来了（named_files_blob），不再整夹重贴一遍
    files = list_job_files()
    if not files:
        return ""
    q = (query or "").lower()
    named = [f for f in files if f["name"].lower() in q or Path(f["name"]).stem.lower() in q]
    pick = named or files
    chunks: List[str] = [_BLOB_HEADER]
    used = 0
    for f in pick:
        room = JOB_TOTAL_CHARS - used
        if room < 80:
            chunks.append(f"（还有 {f['name']} 未贴全文）")
            continue
        body, why = read_material_checked(Path(f["path"]), min(JOB_FILE_CHARS, room), reader=read_job_file)
        if why:
            chunks.append(f"### {f['name']}\n{UNREAD}{why}")
            continue
        block = f"### {f['name']}\n{body}"
        chunks.append(block)
        used += len(block)
    return "\n\n".join(chunks)
