---
name: doc-spreadsheet
description: Read and edit existing XLSX cell ranges with explicit text, number, boolean or formula types. Use for project data mapping, registers and source-backed spreadsheet revisions.
---

Inspect first to discover sheet names and used ranges. Read explicit sheet/range windows; retain source_sha256, sheet, cell and typed value as evidence. Cached formula values are stored_unverified.

Use set_cell {sheet,cell,expected:{type,value},value:{type,value}} or set_range {sheet,range,expected,values}. A literal beginning with '=' remains type text; a requested formula uses type formula explicitly. A blank uses {type:'blank',value:null}. Do not convert units or invent missing figures silently.

Preview then apply the identical patch to a new copy. Ordinary cells and untouched OOXML parts are preserved. Protected/merged targets and array/shared formulas are unsupported. Formula writes set recalculation flags but do not run Excel: tell the user recalculation is not performed, and do not treat cached values as new calculation results. Attach references to the source rows/clauses that justify proposed changes.
