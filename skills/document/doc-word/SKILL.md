---
name: doc-word
description: Read and revise existing DOCX paragraphs or ordinary table cells as a source-preserving draft copy. Use for Word report revisions based on project material.
---

Use read_file with operation inspect/read to obtain source_sha256 and paragraph/table locators. These locators identify the version of the XML body, not rendered page numbers. Headers, footers and advanced objects are preserved but are outside the initial editing interface.

Prepare replace_paragraph {paragraph_id,expected_text,text} or replace_cell {table,row,column,expected_text,text} patches. Preserve numeric values and contractual statements unless an identified source or explicit user request supports the change. Expected text must match the exact read result.

Call preview_document with source, expected_sha256 and patches; inspect the returned changes. In an authorized workspace-write task, apply_document accepts exactly the same arguments and returns a new draft copy. Report its artifact link and source reference. The original remains unchanged. Model-authored prose is model_proposed; a package reopening pass does not verify its meaning or page layout. Unsupported fields, complex runs and merged cells require a narrower edit or another supported editor, not XML fabrication.
