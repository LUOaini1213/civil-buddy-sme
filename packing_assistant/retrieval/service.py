from __future__ import annotations

from collections import Counter
from contextlib import closing, redirect_stdout
from io import StringIO
import json
import os
from pathlib import Path
import re
import sqlite3
import time

from packing_assistant.documents.common import DocumentError, bounded, digest, fail, integer
from packing_assistant.documents.ooxml import Spreadsheet, Word, W, S, _pt

SCHEMA = 1
EXTRACTOR = "civil-source-v1"
MAX_BYTES = 25_000_000
MAX_FILES = 50
MAX_CHARS = 8_000_000
MAX_CHUNKS = 40_000
CHUNK_CHARS = 1200
OVERLAP = 160
_WORDS = re.compile(r"[\u3400-\u9fff]+|[a-z0-9_]+", re.I)
_FORMATS = {".txt", ".md", ".csv", ".tsv", ".json", ".log", ".docx", ".xlsx", ".pdf"}


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _tokens(text):
    for match in _WORDS.finditer(text.casefold()):
        word = match.group()
        if "\u3400" <= word[0] <= "\u9fff":
            yield from ("u" + c for c in word)
            yield from ("b" + word[i:i+2] for i in range(len(word)-1))
        else:
            yield "w" + word


def _windows(text):
    start = 0
    while start < len(text):
        end = min(len(text), start + CHUNK_CHARS)
        if end < len(text):
            newline = max(text.rfind("\n", start+CHUNK_CHARS//2, end), text.rfind("。", start+CHUNK_CHARS//2, end))
            if newline >= 0:
                end = newline+1
        yield start, end, text[start:end]
        if end == len(text):
            return
        start = max(start+1, end-OVERLAP)


def _chunks(data, suffix):
    """Yield exact extraction windows; locators are tied to the source hash."""
    if suffix == ".docx":
        doc = Word(data)
        for index, paragraph in enumerate(doc.paragraphs):
            for start, end, quote in _windows(_pt(paragraph)):
                yield {"kind": "docx_paragraph", "paragraph_id": f"p:{index}", "start": start, "end": end}, quote, "extracted_text"
        for table_index, table in enumerate(doc.tables):
            for row_index, row in enumerate(table.findall(f"{{{W}}}tr")):
                for cell_index, cell in enumerate(row.findall(f"{{{W}}}tc")):
                    value = "\n".join(_pt(p) for p in cell.findall(f"{{{W}}}p"))
                    for start, end, quote in _windows(value):
                        yield {"kind": "docx_cell", "table": table_index, "row": row_index, "column": cell_index,
                               "start": start, "end": end}, quote, "extracted_text"
    elif suffix == ".xlsx":
        book = Spreadsheet(data)
        count = 0
        for name, (_, sheet) in book.sheets.items():
            for row in sheet.findall(f"{{{S}}}sheetData/{{{S}}}row"):
                row_parts = []
                for cell in row.findall(f"{{{S}}}c"):
                    typed = book._value(cell)
                    if typed["type"] == "blank":
                        continue
                    value = str(typed["value"])
                    address = cell.get("r", "")
                    row_parts.append(f"{address}: {value}")
                    for start, end, quote in _windows(value):
                        yield {"kind": "xlsx_cell", "sheet": name, "cell": address, "value_type": typed["type"],
                               "start": start, "end": end}, quote, "stored_cell_value"
                    count += 1
                    if count > 100_000:
                        fail("too_large", "Workbook exceeds the 100000 populated-cell extraction limit")
                value = " | ".join(row_parts)
                for start, end, quote in _windows(value):
                    yield {"kind": "xlsx_row", "sheet": name, "row": int(row.get("r", "0")),
                           "start": start, "end": end}, quote, "cell_address_value_serialization"
    elif suffix == ".pdf":
        from packing_assistant.documents.pdf import PDF
        document = PDF(data)
        for index, page in enumerate(document.reader.pages):
            value = page.extract_text() or ""
            for start, end, quote in _windows(value):
                yield {"kind": "pdf_page", "page": index+1, "start": start, "end": end}, quote, "text_layer"
    else:
        try:
            value = data.decode("utf-8-sig")
        except UnicodeDecodeError:
            try:
                value = data.decode("gb18030")
            except UnicodeDecodeError:
                fail("invalid_document", "Text source is neither UTF-8 nor GB18030")
        if "\0" in value:
            fail("invalid_document", "Binary data is not a text source")
        for start, end, quote in _windows(value):
            yield {"kind": "text", "start": start, "end": end, "line_start": value.count("\n", 0, start)+1,
                   "line_end": value.count("\n", 0, end)+1}, quote, "decoded_text"


def _selected(workspace, supplied, expected):
    if not isinstance(supplied, list) or not 1 <= len(supplied) <= MAX_FILES:
        fail("invalid_request", "sources must explicitly select 1 to 50 workspace files")
    if not isinstance(expected, dict):
        fail("invalid_request", "expected_versions must be a path-to-SHA256 object")
    expected_paths = {bounded(workspace, path): sha for path, sha in expected.items()}
    result, seen, total_bytes = [], set(), 0
    for raw in supplied:
        path = bounded(workspace, raw)
        if path in seen:
            continue
        seen.add(path)
        if not path.is_file():
            fail("not_found", "A selected source no longer exists")
        if path.suffix.lower() not in _FORMATS:
            fail("unsupported", "Selected source format is unsupported")
        if path.stat().st_size > MAX_BYTES:
            fail("too_large", "A selected source exceeds 25 MB")
        data = path.read_bytes()
        total_bytes += len(data)
        if len(data) > MAX_BYTES or total_bytes > 100_000_000:
            fail("too_large", "Selected source bytes exceed the extraction limit")
        sha = digest(data)
        if path in expected_paths and expected_paths[path] != sha:
            fail("conflict", "A selected source differs from its expected version")
        relative = path.relative_to(workspace).as_posix()
        source_id = digest((os.path.normcase(str(workspace)) + "\0" + os.path.normcase(relative)).encode("utf-8"))[:32]
        result.append({"id": source_id, "source": relative, "path": path, "sha": sha, "data": data, "suffix": path.suffix.lower()})
    if set(expected_paths) - seen:
        fail("path_denied", "expected_versions contains a file outside the selected set")
    return result


def _database(workspace):
    folder = bounded(workspace, ".civil-buddy/out/retrieval")
    folder.mkdir(parents=True, exist_ok=True)
    database = bounded(workspace, str(folder / "sources.sqlite3"))
    for suffix in ("-wal", "-shm", "-journal"):
        bounded(workspace, str(database)+suffix)
    connection = sqlite3.connect(database, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout=10000")
    connection.execute("PRAGMA trusted_schema=OFF")
    return connection


def _sync(connection, selected):
    changed, unchanged, empty, total_chars, total_chunks = 0, 0, [], 0, 0
    with connection:
        connection.execute("BEGIN IMMEDIATE")
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        if version not in (0, SCHEMA):
            connection.execute("DROP TABLE IF EXISTS chunks")
            connection.execute("DROP TABLE IF EXISTS sources")
        connection.execute("CREATE TABLE IF NOT EXISTS sources(id TEXT PRIMARY KEY,path TEXT NOT NULL,sha TEXT NOT NULL,extractor TEXT NOT NULL,indexed_at INTEGER NOT NULL,chunk_count INTEGER NOT NULL)")
        connection.execute("CREATE VIRTUAL TABLE IF NOT EXISTS chunks USING fts5(tokens,source_id UNINDEXED,sha UNINDEXED,chunk_id UNINDEXED,locator UNINDEXED,quote UNINDEXED,quote_kind UNINDEXED, tokenize='unicode61')")
        connection.execute(f"PRAGMA user_version={SCHEMA}")
        for source in selected:
            old = connection.execute("SELECT sha,extractor,chunk_count FROM sources WHERE id=?", (source["id"],)).fetchone()
            if old and old["sha"] == source["sha"] and old["extractor"] == EXTRACTOR:
                unchanged += 1
                total_chunks += old["chunk_count"]
                if not old["chunk_count"]:
                    empty.append(source["source"])
                continue
            rows = []
            for locator, quote, kind in _chunks(source["data"], source["suffix"]):
                if not quote.strip():
                    continue
                total_chars += len(quote)
                total_chunks += 1
                if total_chars > MAX_CHARS or total_chunks > MAX_CHUNKS:
                    fail("too_large", "Selected sources exceed 8M extracted characters or 40000 chunks")
                loc = _json(locator)
                chunk_id = digest((source["id"]+"\0"+source["sha"]+"\0"+loc+"\0"+quote).encode("utf-8"))
                rows.append((" ".join(_tokens(source["source"]+"\n"+_json(locator)+"\n"+quote)), source["id"], source["sha"], chunk_id, loc, quote, kind))
            # Full replacement is in this transaction: stale chunks cannot survive a revision.
            connection.execute("DELETE FROM chunks WHERE source_id=?", (source["id"],))
            connection.executemany("INSERT INTO chunks(tokens,source_id,sha,chunk_id,locator,quote,quote_kind) VALUES (?,?,?,?,?,?,?)", rows)
            connection.execute("INSERT OR REPLACE INTO sources(id,path,sha,extractor,indexed_at,chunk_count) VALUES (?,?,?,?,?,?)",
                               (source["id"], source["source"], source["sha"], EXTRACTOR, int(time.time()), len(rows)))
            changed += 1
            if not rows:
                empty.append(source["source"])
        if total_chunks > MAX_CHUNKS:
            fail("too_large", "Selected sources exceed 40000 indexed chunks")
    return {"updated": changed, "unchanged": unchanged, "selected_sources": len(selected),
            "selected_chunks": total_chunks, "empty_text_sources": empty, "ocr": "not_performed"}


def _search(connection, selected, query, limit):
    if not isinstance(query, str) or not query.strip() or len(query) > 4096:
        fail("invalid_request", "query must contain 1 to 4096 characters")
    integer(limit, 1, 30)
    words = list(dict.fromkeys(_tokens(query)))[:160]
    strong = [w for w in words if not w.startswith("u")]
    if not words:
        return []
    expression = " OR ".join('"'+w+'"' for w in strong or words)
    identifiers = {s["id"]: s for s in selected}
    placeholders = ",".join("?" for _ in identifiers)
    rows = connection.execute(f"SELECT source_id,sha,chunk_id,locator,quote,quote_kind,bm25(chunks) AS rank FROM chunks WHERE chunks MATCH ? AND source_id IN ({placeholders}) ORDER BY rank LIMIT 240",
                              (expression, *identifiers)).fetchall()
    hits = []
    for row in rows:
        source = identifiers[row["source_id"]]
        if row["sha"] != source["sha"]:
            continue
        tokens = Counter(_tokens(row["quote"] + "\n" + row["locator"]))
        coverage = sum(0.15 if t.startswith("u") else 1.0 for t in words if t in tokens)
        phrase = 4 if query.casefold() in row["quote"].casefold() else 0
        hits.append({"source": source["source"], "source_sha256": row["sha"], "chunk_id": row["chunk_id"],
                     "locator": json.loads(row["locator"]), "quote": row["quote"], "quote_kind": row["quote_kind"],
                     "score": round(coverage+phrase+min(5, -float(row["rank"])), 6), "trust": "source_unverified"})
    hits.sort(key=lambda h: (-h["score"], h["source"], h["chunk_id"]))
    return hits[:limit]


def _verify(connection, workspace, selected, references):
    if not isinstance(references, list) or not 1 <= len(references) <= 100:
        fail("invalid_request", "references must contain 1 to 100 exact source references")
    allowed = {s["path"]: s for s in selected}
    extracted = {}
    results = []
    for index, reference in enumerate(references):
        reason = None
        if not isinstance(reference, dict):
            reason = "invalid_reference"
        else:
            try:
                path = bounded(workspace, reference.get("source"))
            except DocumentError:
                path = None
            source = allowed.get(path)
            if source is None:
                reason = "source_not_allowed"
            elif reference.get("source_sha256") != source["sha"]:
                reason = "version_mismatch"
            elif not isinstance(reference.get("locator"), dict) or not isinstance(reference.get("quote"), str) or not reference["quote"]:
                reason = "invalid_locator_or_quote"
            else:
                # Verification re-extracts the actual selected bytes. A mutable
                # derived SQLite cache is not authority for an exact quotation.
                if source["id"] not in extracted:
                    checked, chars = {}, 0
                    for locator, quote, _ in _chunks(source["data"], source["suffix"]):
                        chars += len(quote)
                        if len(checked) >= MAX_CHUNKS or chars > MAX_CHARS:
                            fail("too_large", "Source is too large for citation verification")
                        loc = _json(locator)
                        checked[loc] = {"quote": quote, "chunk_id": digest((source["id"]+"\0"+source["sha"]+"\0"+loc+"\0"+quote).encode("utf-8"))}
                    extracted[source["id"]] = checked
                row = extracted[source["id"]].get(_json(reference["locator"]))
                if row is None:
                    reason = "locator_mismatch"
                elif row["quote"] != reference["quote"]:
                    reason = "quote_mismatch"
                elif reference.get("chunk_id") is not None and reference["chunk_id"] != row["chunk_id"]:
                    reason = "chunk_mismatch"
        results.append({"index": index, "status": "invalid" if reason else "valid", "reason": reason})
    return {"valid": all(r["status"] == "valid" for r in results),
            "status": "valid" if all(r["status"] == "valid" for r in results) else "invalid",
            "references": results, "verification_scope": "selected_source+current_sha256+exact_locator+exact_quote",
            "engineering_truth": "not_verified"}


def _execute(request):
    if not isinstance(request, dict) or request.get("version") != 1:
        fail("invalid_request", "Protocol version must be 1")
    call_id = request.get("call_id")
    if not isinstance(call_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,79}", call_id):
        fail("invalid_request", "call_id must be a bounded identifier")
    try:
        size = len(_json(request).encode("utf-8"))
    except (TypeError, ValueError):
        fail("invalid_request", "Request must contain finite JSON values")
    if size > 1_100_000:
        fail("too_large", "Retrieval request exceeds 1.1 MB")
    operation = request.get("operation")
    if operation not in ("index", "search", "verify"):
        fail("unsupported", "Retrieval only supports index, search and verify")
    root = request.get("workspace")
    if not isinstance(root, str) or not Path(root).is_absolute() or not Path(root).is_dir():
        fail("invalid_request", "workspace must be an absolute existing directory")
    workspace = Path(root).resolve()
    selected = _selected(workspace, request.get("sources"), request.get("expected_versions", {}))
    with closing(_database(workspace)) as connection:
        sync = _sync(connection, selected)
        base = {"index": sync, "retrieval": "sqlite_fts5_bm25+lexical_rules", "embeddings": False,
                "sources": [{"source": s["source"], "source_sha256": s["sha"]} for s in selected]}
        if operation == "search":
            result = {**base, "hits": _search(connection, selected, request.get("query"), request.get("limit", 8))}
        elif operation == "verify":
            result = {**base, **_verify(connection, workspace, selected, request.get("references"))}
        else:
            result = base
        # A concurrently changed source invalidates this request instead of returning stale evidence.
        if any(not s["path"].is_file() or digest(s["path"].read_bytes()) != s["sha"] for s in selected):
            fail("conflict", "A selected source changed during retrieval; retry with its current revision")
        return result


def handle(request):
    call_id = request.get("call_id") if isinstance(request, dict) else None
    try:
        with redirect_stdout(StringIO()):
            result = _execute(request)
        return {"version": 1, "ok": True, "call_id": call_id, "result": result, "error": None}
    except DocumentError as exc:
        return {"version": 1, "ok": False, "call_id": call_id, "result": None,
                "error": {"code": exc.code, "message": str(exc)}}
    except sqlite3.Error:
        return {"version": 1, "ok": False, "call_id": call_id, "result": None,
                "error": {"code": "index_error", "message": "Source index is unavailable; verify SQLite FTS5 and the workspace cache"}}
    except Exception:
        return {"version": 1, "ok": False, "call_id": call_id, "result": None,
                "error": {"code": "invalid_document", "message": "Source extraction failed; verify selected files and supported parsers"}}
