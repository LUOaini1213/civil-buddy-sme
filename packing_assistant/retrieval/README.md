# Versioned source retrieval worker

This worker indexes explicitly selected workspace files and returns verifiable
source quotations. It does not run an agent, call an LLM, embed vectors, access
the network, or search outside the selected source set. Retrieval uses local
SQLite FTS5/BM25 with Chinese unigrams/bigrams and Latin words, followed by
deterministic coverage/phrase scoring. No RRF or model reranker is claimed.

Call `packing_assistant.retrieval.handle(request)` inside the fixed host worker,
or run `python -m packing_assistant.retrieval.worker` with one UTF-8 JSON line.
The direct callable does not spawn another process and therefore can run after
the host applies its OS sandbox. SQLite lives exclusively in
`<workspace>/.civil-buddy/out/retrieval/sources.sqlite3`, a rebuildable cache.

```json
{
  "version": 1,
  "call_id": "turn1-sources",
  "operation": "search",
  "workspace": "C:/jobs/project-a",
  "sources": ["requirements.pdf", "quantities.xlsx", "report.docx"],
  "query": "钢梁数量变更",
  "limit": 8,
  "expected_versions": {"requirements.pdf": "optional-known-sha256"}
}
```

`operation` is `index|search|verify`. `sources` is mandatory for every request:
1–50 paths, relative to the workspace or absolute paths resolving inside it.
It is the host's explicit permission set, not an invitation for the model to
enumerate all project files. Existing cached documents that are absent from this
set cannot participate in search or verification. The host must bind the set to
the user's selected artifacts. The worker does not infer approval from a path.

Every operation reads and hashes the selected originals. Changed source hashes
replace all associated chunks transactionally; unchanged versions are reused.
Optional `expected_versions` pins a subset of the selected files. A source that
changes during the request yields conflict. A missing source yields not_found,
never stale search results. File extensions supported: PDF, DOCX, XLSX, TXT, MD,
CSV, TSV, JSON and LOG. PDF text layers use the document worker's pypdf backend.
Empty/image-only pages do not get invented text; index status reports files with
no extracted text and `ocr:not_performed`.

Responses have the same envelope as the document worker:

```json
{"version":1,"ok":true,"call_id":"turn1-sources","error":null,"result":{
 "hits":[{"source":"requirements.pdf","source_sha256":"...","chunk_id":"...",
 "locator":{"kind":"pdf_page","page":2,"start":0,"end":120},
 "quote":"exact extracted text","quote_kind":"text_layer","score":6.1,
 "trust":"source_unverified"}],
 "index":{"updated":1,"unchanged":2,"selected_sources":3},
 "retrieval":"sqlite_fts5_bm25+lexical_rules","embeddings":false
}}
```

Source paths in responses are workspace-relative POSIX paths. Citations contain
the original binary SHA-256, not a hash of an abbreviated extraction. Chunk IDs
bind the workspace/path, version, locator and quote. Locators use zero-based
character offsets (`end` exclusive) within their identified block:

| Kind | Locator |
|---|---|
| `pdf_page` | 1-based `page`, `start`, `end` |
| `docx_paragraph` | hash-bound `paragraph_id:p:0`, `start`, `end` |
| `docx_cell` | zero-based XML `table,row,column`, `start`, `end` |
| `xlsx_cell` | `sheet,cell,value_type,start,end` |
| `xlsx_row` | `sheet,row,start,end`; row is 1-based |
| `text` | character `start,end`, 1-based `line_start,line_end` |

Word main-document paragraphs and table cells are indexed. Header/footer text
and rendered page coordinates are not included. XLSX indexes stored cell values
and a deterministic row serialization (`A2: Beam | B2: 12`). `quote_kind`
distinguishes that serialization from plain extracted text. Formula cells quote
their formula strings; cached or recalculated results are not asserted. Numeric
Excel dates remain stored serial values. Chunks are at most1200 characters with
160-character overlap. PDF page and Office XML positions are not visual bounding
boxes or evidence that the layout was reviewed.

## Exact citation verification

```json
{
 "version":1,"call_id":"turn1-verify","operation":"verify",
 "workspace":"C:/jobs/project-a","sources":["requirements.pdf"],
 "references":[{"source":"requirements.pdf","source_sha256":"...",
   "locator":{"kind":"pdf_page","page":2,"start":0,"end":120},
   "quote":"exact extracted text","chunk_id":"optional-exact-chunk-id"}]
}
```

`references` contains1–100 references. A complete search hit is accepted as a
reference. Verification requires the selected file, current SHA, exact locator
and exact quote; an optional chunk ID must match too. It **re-extracts the selected
source bytes**, so a tampered derived cache cannot validate a fabricated quote.
The result is `valid:true|false`, `status:valid|invalid`, and per-reference
`index,status,reason`. Reasons include `source_not_allowed`, `version_mismatch`,
`invalid_locator_or_quote`, `locator_mismatch`, `quote_mismatch`, `chunk_mismatch`.
An invalid reference is a successful verification request with `valid:false`;
request/parser/index failures instead use envelope `ok:false` and an error code.
The host must check both fields and must not interpret `ok:true` as valid evidence.

Exact quotation validates provenance, not the engineering truth of the cited
statement. The result always retains `engineering_truth:not_verified`. Derived
model drafts do not become independently corroborated facts by being indexed.

Limits: 25MB per file, 100MB selected input bytes, 8M newly extracted characters,
40000 selected chunks, request1.1MB, search query4096 characters, top1–30 results,
at most240 BM25 candidates **after filtering to selected source IDs**. SQLite
errors are explicit `index_error`; there is no silent global or stale fallback.
Run `python scripts/test_source_retrieval.py -v` for offline file, Chinese query,
scope, hash refresh, exact-citation, tampered-cache and worker protocol tests.
