---
name: doc-pdf
description: Read selectable PDF text by page and create draft copies with annotations, text-form values or page reordering. Use for requirements evidence and PDF review markup.
---

Inspect and read pages with read_file; pages are one-based. Keep source_sha256 and page identifiers with quotes. Empty selectable text may indicate a scan; OCR is unavailable in this worker, so do not infer text from an empty extraction.

Supported patches are annotate {page,rect:[x0,y0,x1,y1],text}, fill_fields {fields:{field_name:text}}, and reorder_pages {pages:[...]} containing every original page exactly once. Coordinates follow PDF page coordinates, not a guessed screenshot rectangle. Read form names before filling. Preview the structured diff and apply the same patch to a new copy.

The worker cannot rewrite body text, redact hidden content, perform OCR or render layout. A successful reopen is a structural check, not a visual or signature validation. Preserve background content and explain exactly which supported operation was applied.
