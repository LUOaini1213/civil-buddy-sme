# Document worker, protocol v1

This is a deterministic file worker. The host owns user authorization, selected
artifacts, engineering gates, evidence validation, cancellation, and publishing.
Model-authored text can enter a new draft through a structured patch. Its trust
stays `model_proposed`; a successful file write does not verify engineering facts.

Run `python -m packing_assistant.documents.worker` from the repository. Send one
UTF-8 JSON line to stdin; read exactly one JSON line from stdout. One process
handles one request. There is no shell/script/expression execution operation.
The process may be terminated by the host; outputs are only published by the
host after receiving and accepting the successful response. Dead-process call
locks are recovered on retry. Orphan `.tmp` files from killed processes are not
registered documents. No worker claims to provide OS-level confinement itself.

## Envelope

```json
{
  "version": 1,
  "call_id": "turn1-document1",
  "operation": "apply",
  "workspace": "C:/jobs/project-a",
  "source": "report.docx",
  "expected_sha256": "sha256-from-inspect",
  "arguments": {"patches": []},
  "output_dir": ".civil-buddy/out/documents"
}
```

`operation` is `capabilities|inspect|read|preview|apply|validate`.
`capabilities` needs only version, call_id and operation. All other operations
require an existing absolute workspace and a source resolving inside it. Only
`.docx`, `.xlsx` and `.pdf` are accepted. Symlinks escaping the workspace fail.
`preview` and `apply` require the exact source SHA-256. An optional SHA on reads
also guards the version. `output_dir` must resolve inside the workspace.

Responses always have `version`, `ok`, `call_id`, `result`, and `error`:

```json
{"version":1,"ok":false,"call_id":"turn1-document1","result":null,
 "error":{"code":"conflict","message":"Source SHA-256 differs from expected_sha256; inspect the current version"}}
```

Error codes include `invalid_request`, `invalid_patch`, `unsupported`,
`unavailable`, `conflict`, `not_found`, `path_denied`, `too_large`, `busy`,
`invalid_document`, and `validation_failed`. Parser exceptions never echo raw
document content. Python imports can call `packing_assistant.documents.handle`.

`inspect` returns source/source_sha256/format/capabilities and format-specific
structure. `read` returns versioned content. `validate` performs a package/read
check, not full OOXML schema validation, semantic verification, or visual review.
`preview` executes the same patch against bytes in memory, reopens the result,
and returns `changes`, validation, `writes:false`; it is a structured difference,
not a rendered visual preview. `apply` writes a new uniquely named draft,
reopens it, verifies the original hash is unchanged and returns `output_path`,
`output_sha256`, changes and validation. The source is never overwritten.

Successful apply calls have an on-disk idempotency record in the output folder.
The same call_id and request return the same verified artifact. Reusing a
call_id with a different request or modifying its artifact returns conflict.
Use a new call_id after changing a proposal.

## DOCX

`inspect.nodes` enumerates up to 1000 main-document paragraph locators `p:0`,
`p:1`, etc. It also exposes table shapes. Locators are tied to source SHA; they
are not page numbers. `read.arguments.block_ids` selects at most 500 paragraphs
(the default is the first 100). Header/footer content is preserved but is not
included in this initial read/edit interface.

```json
{"patches":[
 {"op":"replace_paragraph","paragraph_id":"p:1",
  "expected_text":"Old wording","text":"Proposed new wording"},
 {"op":"replace_cell","table":0,"row":1,"column":1,
  "expected_text":"Old cell","text":"Proposed cell"}
]}
```

Table/row/column indices are zero-based XML nodes; merged/nested target cells
are rejected. Expected text must exactly match. Replacing a paragraph uses its
first text-run style and retains paragraph properties; empty remaining runs keep
their properties. Fields, tracked changes, links, nontext runs and protected
documents require other operations and are rejected. Unmodified package parts
are kept byte-for-byte; serialization of the edited XML part may differ.
Native Word tracked changes and layout rendering are unavailable.

## XLSX

`read.arguments` is `{"sheet":"Quantities","range":"A1:D20"}`. Cells return
`cell,type,value,cached_value`; cached formula values are stored/unverified.
Dates stored as numeric serials retain that representation; styles are not
interpreted as calculations. `inspect` reports sheet names, ranges, formula
counts, merged ranges and protection.

```json
{"patches":[
 {"op":"set_cell","sheet":"Quantities","cell":"A2",
  "expected":{"type":"number","value":3},
  "value":{"type":"number","value":7}},
 {"op":"set_range","sheet":"Quantities","range":"C2:D2",
  "expected":[[{"type":"blank","value":null},{"type":"blank","value":null}]],
  "values":[[{"type":"text","value":"=1+1"},{"type":"formula","value":"=SUM(A2,4)"}]]}
]}
```

Value types are `blank|null`, `text|string`, `number|finite-number`,
`boolean|boolean`, `formula|string beginning with =`. Text beginning `=` stays
text. Dates/errors are readable but are not a writable type in this version.
Formula guards reject external references and functions outside the allowlist;
they are not a full formula parser or recalculation engine. New formulas have
no cached value; other cached values may be stale. The workbook requests full
calculation on opening, but validation reports `not_recalculated` until a real
engine has run. Protected/merged target cells and shared/array formula targets
are rejected. Macro formats are unsupported. Editing keeps all unrelated ZIP
members including charts, drawings and other sheets; targeted XML is reserialized.
Rendering and Excel recalculation are unavailable.

## PDF

Requires `pypdf>=5`. `inspect` returns 1-based pages with dimensions and named
form fields. `read.arguments.pages` selects up to 100 pages (default first20),
returning their text-layer content and page locator. Text coordinates, OCR and
generic table extraction are not implemented here. The existing Rust heavy
parser adapter is a different optional ingestion backend.

```json
{"patches":[
 {"op":"annotate","page":1,"rect":[20,200,60,220],"text":"Review this detail"},
 {"op":"fill_fields","fields":{"project":"Project A"}},
 {"op":"reorder_pages","pages":[2,1]}
]}
```

Annotation rectangles use PDF points in the unrotated page mediabox coordinate
system. Optional `author` is supported. Only writable text AcroForm fields are
supported; XFA, encrypted PDFs and signature-field PDFs are rejected for edits.
Page reorder must be a permutation of all pages; operations are applied in order,
so page numbers after a reorder refer to the new order. Source content streams
are preserved by pypdf cloning, but the PDF container is rewritten. Reopening
checks page count and text field values. Visual appearance, all PDF feature
fidelity and rendering are not verified. PDF body rewriting/redaction is not
implemented and unsupported operations fail explicitly.

## Bounds and provenance

Sources ≤25 MB, OOXML expanded members ≤100 MB, request ≤1.1 MB, patches ≤200,
modified cells ≤2000, resulting file ≤50 MB. Each patch may carry `evidence`
(at most50 entries) and `trust=model_proposed|user_requested`; the output remains
a candidate draft and reports evidence validation as not checked. Source hash,
locators, old/new values, output hash and validation state accompany each edit.

Run offline fixtures with `python scripts/test_document_worker.py -v`.
Fixtures independently reopen XLSX with openpyxl and PDF with pypdf, compare
unmodified package members, confirm actual proposed text lands in DOCX, and
cover version/value conflicts, idempotency and path boundaries. Rendering and
real model invocation require separate integration acceptance.
