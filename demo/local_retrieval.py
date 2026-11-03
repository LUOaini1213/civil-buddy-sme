"""Rebuildable, session-local SQLite retrieval over complete source text.

Chinese unigrams/bigrams and Latin words are indexed locally with FTS5/BM25.
No embeddings, network requests, model calls, or cross-session scans are used.
Offsets always refer to the unchanged Python source string, not tokenized text.
"""
from __future__ import annotations

from collections import Counter
from contextlib import closing
import hashlib
import json
import re
import sqlite3
from threading import RLock
from typing import Any
from urllib.parse import quote

if __package__:
    from . import projects
else:
    import projects

SCHEMA_VERSION = 2
CHUNK_CHARS = 900
CHUNK_OVERLAP = 160
MAX_SOURCE_CHARS = 4_000_000
MAX_TOTAL_CHARS = 16_000_000
MAX_SOURCES = 100_000
MAX_CHUNKS = 30_000
MAX_RESULTS = 30
MAX_CANDIDATES = 240
_IDENTIFIER = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")
_SOURCE_ID = re.compile(r"src-[a-f0-9]{32}\Z")
_WORDS = re.compile(r"[\u3400-\u9fff]+|[a-z0-9_]+", re.I)
_SYNC_LOCK = RLock()


class RetrievalError(ValueError):
    """An explicit retrieval/index failure; never disguise it as no matches."""


def _path(root, sid: str):
    projects.safe_session_id(sid)
    path = projects._bounded_path(root, sid, "local-retrieval.sqlite3")
    for suffix in ("-wal", "-shm", "-journal"):
        projects._bounded_path(root, sid, path.name + suffix)
    return path


def _identifier(value: Any, label: str) -> str:
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        raise RetrievalError(f"{label}无效")
    return value


def _text(value: Any, label: str) -> str:
    if not projects._valid_text(value):
        raise RetrievalError(f"{label}必须是有效文本")
    return value


def _source_id(sid: str, kind: str, identifier: str) -> str:
    return "src-" + hashlib.sha256(f"{sid}\0{kind}\0{identifier}".encode("utf-8")).hexdigest()[:32]


def _tokens(text: str):
    for match in _WORDS.finditer(text.casefold()):
        word = match.group()
        if "\u3400" <= word[0] <= "\u9fff":
            for char in word:
                yield "u" + char
            for index in range(len(word) - 1):
                yield "b" + word[index:index + 2]
        else:
            yield "w" + word


def _chunks(text: str):
    start = 0
    while start < len(text):
        end = min(len(text), start + CHUNK_CHARS)
        if end < len(text):
            boundary = max(text.rfind("\n", start + CHUNK_CHARS // 2, end),
                           text.rfind("。", start + CHUNK_CHARS // 2, end))
            if boundary >= 0:
                end = boundary + 1
        yield start, end
        if end == len(text):
            break
        start = max(start + 1, end - CHUNK_OVERLAP)


def _documents(sid: str, history: list[dict], attachments: list[dict]) -> list[dict]:
    if not isinstance(history, list) or not isinstance(attachments, list):
        raise RetrievalError("历史和附件须为列表")
    if len(history) + len(attachments) > MAX_SOURCES:
        raise RetrievalError("检索来源超过 100000 条，请拆分任务")
    if len(attachments) > 12:
        raise RetrievalError("同一任务最多检索 12 个附件")
    documents = []
    seen = set()
    total = 0
    chunks = 0
    for kind, records in (("history", history), ("attachment", attachments)):
        for order, row in enumerate(records):
            if not isinstance(row, dict):
                raise RetrievalError("检索来源记录无效")
            identifier = _identifier(row.get("id"), "消息或附件 ID")
            text = _text(row.get("content") if kind == "history" else row.get("text"), "来源正文")
            if len(text) > MAX_SOURCE_CHARS:
                raise RetrievalError("单个检索来源超过 400 万字，请拆分后重试")
            total += len(text)
            chunks += sum(1 for _ in _chunks(text))
            if total > MAX_TOTAL_CHARS or chunks > MAX_CHUNKS:
                raise RetrievalError("任务检索容量超限，请拆分任务；完整原文未裁剪")
            role = _text(row.get("role", ""), "消息角色") if kind == "history" else ""
            if kind == "history" and (not role or len(role) > 64):
                raise RetrievalError("消息角色无效")
            ts = row.get("ts", 0) if kind == "history" else 0
            if not projects._nonnegative_int(ts):
                raise RetrievalError("消息时间戳无效")
            title = (f"{'用户' if role == 'user' else '助手' if role == 'assistant' else role} · 第 {order + 1} 条消息"
                     if kind == "history" else _text(row.get("name"), "附件名"))
            source_id = _source_id(sid, kind, identifier)
            if source_id in seen:
                raise RetrievalError("检索来源 ID 重复，请核对消息或附件")
            seen.add(source_id)
            fingerprint = hashlib.sha256(json.dumps(
                [kind, title, text, role, ts], ensure_ascii=False
            ).encode("utf-8")).hexdigest()
            documents.append({"source_id": source_id, "kind": kind, "title": title[:300],
                              "content": text, "message_id": identifier if kind == "history" else None,
                              "attachment_id": identifier if kind == "attachment" else None,
                              "role": role, "ts": ts, "ordinal": order, "fingerprint": fingerprint})
    return documents


def _connect(path, *, write: bool):
    # Path.as_uri() interprets the Windows \\?\ prefix as a URI authority.
    # Encode the native absolute filename as a file: path instead: SQLite
    # decodes it back to exactly that filename, without a host or query injection.
    readonly_uri = "file:" + quote(str(path.absolute()), safe="/:") + "?mode=ro"
    connection = sqlite3.connect(str(path) if write else readonly_uri,
                                 uri=not write, timeout=10)
    try:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=10000")
        connection.execute("PRAGMA trusted_schema=OFF")
        if not write:
            connection.execute("PRAGMA query_only=ON")
    except BaseException:
        connection.close()
        raise
    return connection


def _initialize(connection):
    version = connection.execute("PRAGMA user_version").fetchone()[0]
    if version not in (0, SCHEMA_VERSION):
        # This database contains derived text only. A changed tokenizer/schema
        # must rebuild all chunks from the caller's complete source snapshot.
        connection.execute("DROP TABLE IF EXISTS chunks")
        connection.execute("DROP TABLE IF EXISTS sources")
    connection.execute("""CREATE TABLE IF NOT EXISTS sources(
        source_id TEXT PRIMARY KEY, kind TEXT NOT NULL, title TEXT NOT NULL,
        content TEXT NOT NULL, message_id TEXT, attachment_id TEXT,
        role TEXT NOT NULL, ts INTEGER NOT NULL, ordinal INTEGER NOT NULL,
        fingerprint TEXT NOT NULL)""")
    connection.execute("CREATE INDEX IF NOT EXISTS source_attachment ON sources(attachment_id)")
    connection.execute("""CREATE VIRTUAL TABLE IF NOT EXISTS chunks USING fts5(
        tokens, source_id UNINDEXED, start UNINDEXED, end UNINDEXED, text UNINDEXED,
        tokenize='unicode61')""")
    if version != SCHEMA_VERSION:
        connection.execute(f"PRAGMA user_version={SCHEMA_VERSION}")


def sync_session(root, sid: str, history: list[dict], attachments: list[dict], *, rebuild: bool = False) -> dict:
    """Synchronize the complete supplied snapshot, updating changed sources only.

    Omitted sources are removed from this derived index. Original transcript and
    attachment files are never modified. Unchanged calls do not recreate chunks.
    """
    path = _path(root, sid)
    documents = _documents(sid, history, attachments)  # Validate before mutation.
    with _SYNC_LOCK:
        path.parent.mkdir(parents=True, exist_ok=True)
        for attempt in range(2):
            try:
                result = _sync_documents(path, documents, rebuild=rebuild)
                result["rebuilt"] = rebuild or bool(attempt)
                return result
            except sqlite3.DatabaseError as exc:
                code = getattr(exc, "sqlite_errorcode", 0) & 0xFF
                if attempt == 0 and code in (sqlite3.SQLITE_CORRUPT, sqlite3.SQLITE_NOTADB):
                    # Only this disposable index is removed; original messages
                    # and uploads are outside these exact cache paths.
                    for suffix in ("", "-wal", "-shm", "-journal"):
                        cache = projects._bounded_path(root, sid, path.name + suffix)
                        cache.unlink(missing_ok=True)
                    continue
                raise RetrievalError("本地检索索引无法更新，请检查磁盘或重建索引；原文未改动") from exc
    raise RetrievalError("本地检索索引未能重建")


def _sync_documents(path, documents: list[dict], *, rebuild: bool = False) -> dict:
    with closing(_connect(path, write=True)) as connection, connection:
        connection.execute("BEGIN IMMEDIATE")
        _initialize(connection)
        if rebuild:
            # A structurally valid index may contain stale or damaged chunks
            # even when source fingerprints match. Rebuild in one transaction.
            connection.execute("DELETE FROM chunks")
            connection.execute("DELETE FROM sources")
        previous = {row["source_id"]: row["fingerprint"]
                    for row in connection.execute("SELECT source_id,fingerprint FROM sources")}
        current = {row["source_id"] for row in documents}
        removed = set(previous) - current
        for source_id in removed:
            connection.execute("DELETE FROM chunks WHERE source_id=?", (source_id,))
            connection.execute("DELETE FROM sources WHERE source_id=?", (source_id,))
        changed = 0
        for document in documents:
            if previous.get(document["source_id"]) == document["fingerprint"]:
                continue
            changed += 1
            connection.execute("DELETE FROM chunks WHERE source_id=?", (document["source_id"],))
            connection.execute("""INSERT OR REPLACE INTO sources
                (source_id,kind,title,content,message_id,attachment_id,role,ts,ordinal,fingerprint)
                VALUES (:source_id,:kind,:title,:content,:message_id,:attachment_id,:role,:ts,:ordinal,:fingerprint)""",
                               document)
            connection.executemany("INSERT INTO chunks(tokens,source_id,start,end,text) VALUES (?,?,?,?,?)", (
                (" ".join(_tokens(document["title"] + "\n" + document["content"][start:end])),
                 document["source_id"], start, end, document["content"][start:end])
                for start, end in _chunks(document["content"])
            ))
        count = connection.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
    return {"ok": True, "sources": len(documents), "chunks": count, "updated": changed,
            "removed": len(removed), "unchanged": len(documents) - changed}


def _selection(attachment_ids, kind):
    if kind not in (None, "history", "attachment"):
        raise RetrievalError("检索类型无效")
    if attachment_ids is None:
        attachment_ids = []
    if not isinstance(attachment_ids, (list, tuple, set)) or len(attachment_ids) > 12:
        raise RetrievalError("附件选择无效")
    identifiers = list(dict.fromkeys(_identifier(value, "附件 ID") for value in attachment_ids))
    arguments = []
    clauses = []
    if kind != "attachment":
        clauses.append("s.kind='history'")
    if identifiers and kind != "history":
        clauses.append("(s.kind='attachment' AND s.attachment_id IN (" + ",".join("?" for _ in identifiers) + "))")
        arguments.extend(identifiers)
    return identifiers, "(" + " OR ".join(clauses) + ")" if clauses else "0", arguments


def _hit(row, *, score: float) -> dict:
    result = {"source_id": row["source_id"], "kind": row["kind"], "title": row["title"],
              "text": row["text"], "start": int(row["start"]), "end": int(row["end"]), "score": score}
    for key in ("message_id", "attachment_id"):
        if row[key] is not None:
            result[key] = row[key]
    return result


_COLUMNS = """s.source_id,s.kind,s.title,s.message_id,s.attachment_id,
    c.start,c.end,c.text"""


def search(root, sid: str, query: str, attachment_ids=None, limit: int = 6, *, kind: str | None = None) -> list[dict]:
    """Search full history and only explicitly selected attachments.

    Empty queries return the first chunk of selected attachments, in selection
    order, without allowing history to crowd out those fallback excerpts.
    """
    path = _path(root, sid)
    query = _text(query, "检索问题").strip()
    if type(limit) is not int or limit < 1:
        raise RetrievalError("检索条数必须为正整数")
    limit = min(limit, MAX_RESULTS)
    selected, where, arguments = _selection(attachment_ids, kind)
    if not path.is_file():
        return []
    try:
        with closing(_connect(path, write=False)) as connection:
            if not query:
                if not selected or kind == "history":
                    return []
                result = []
                for identifier in selected:
                    row = connection.execute(
                        f"SELECT {_COLUMNS} FROM chunks c JOIN sources s ON s.source_id=c.source_id "
                        "WHERE s.kind='attachment' AND s.attachment_id=? ORDER BY CAST(c.start AS INTEGER) LIMIT 1",
                        (identifier,)
                    ).fetchone()
                    if row is not None:
                        result.append(_hit(row, score=0.0))
                    if len(result) == limit:
                        break
                return result
            terms = list(dict.fromkeys(_tokens(query[:4096])))[:160]
            if not terms or where == "0":
                return []
            # A full Chinese question must not match every message containing
            # a common single character such as 是. Single-character queries
            # still work; longer queries require a bigram or a Latin word.
            strong = [term for term in terms if not term.startswith("u")]
            expression = " OR ".join('"' + term + '"' for term in (strong or terms))
            # Repeated user questions are not new evidence. Penalize only an
            # exact full-message repeat; a question containing additional facts
            # still participates normally. Compare in SQL to avoid loading every
            # complete source merely to score its small candidate chunk.
            rows = connection.execute(
                f"SELECT {_COLUMNS},bm25(chunks) AS rank,"
                "(s.kind='history' AND s.role='user' AND trim(s.content)=? COLLATE NOCASE) AS repeated "
                "FROM chunks c JOIN sources s ON s.source_id=c.source_id "
                f"WHERE chunks MATCH ? AND {where} ORDER BY repeated,rank LIMIT ?",
                (query, expression, *arguments, MAX_CANDIDATES)
            ).fetchall()
            results = []
            for row in rows:
                tokens = Counter(_tokens(row["text"]))
                matching = set(tokens)
                if row["kind"] == "attachment":
                    matching.update(_tokens(row["title"]))
                if strong and not matching.intersection(strong):
                    continue
                coverage = sum((0.15 if term.startswith("u") else 1.0) for term in terms if term in tokens)
                phrase = 4.0 if query.casefold() in row["text"].casefold() else 0.0
                score = round(coverage + phrase + min(5.0, -float(row["rank"])) - (1000.0 if row["repeated"] else 0.0), 6)
                results.append(_hit(row, score=score))
            results.sort(key=lambda item: (-item["score"], item["source_id"], item["start"]))
            return results[:limit]
    except sqlite3.DatabaseError as exc:
        raise RetrievalError("本地检索索引不可读，请重建索引") from exc


def source(root, sid: str, source_id: str) -> dict | None:
    """Read one full indexed source within the requested task; never a file path."""
    path = _path(root, sid)
    if not isinstance(source_id, str) or not _SOURCE_ID.fullmatch(source_id):
        raise RetrievalError("检索来源 ID 无效")
    if not path.is_file():
        return None
    try:
        with closing(_connect(path, write=False)) as connection:
            row = connection.execute(
                "SELECT source_id,kind,title,content AS text,message_id,attachment_id,role,ts FROM sources WHERE source_id=?",
                (source_id,)
            ).fetchone()
            if row is None:
                return None
            return {**dict(row), "start": 0, "end": len(row["text"]), "score": 0.0}
    except sqlite3.DatabaseError as exc:
        raise RetrievalError("本地检索来源不可读，请重建索引") from exc
