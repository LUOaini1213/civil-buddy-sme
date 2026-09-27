# Rust unified workbench: implementation and evidence

Updated 2026-09-21. This is a working local preview. The adjacent v0.5 documents remain the architecture target and historical source audit; optional capabilities below must not be inferred from the design alone.

## Integrated sources

- main `b3ccc72ef8e2a6894d683b8a9af4ef365d036dfb`
- frontend PR56 `ca145930487e8446609b148c503bdf2759b5f180`
- CAD/engineering/planning/routing PR58 `29279c7699688a844cb6a309767dfe1deaeb782d`
- knowledge/intents/scorecard PR57 `79bcb5833f7b87e7b04dac7e5b52aa6ae461165f`

The integration lives on `codex/unified-workbench-20260921`. Original user and teammate working trees were not overwritten. The active CAD tree was verified clean at the PR58 commit before integration.

## Implemented boundaries

| Area | Implementation | Evidence and practical limit |
|---|---|---|
| Rust task host | Workspace scopes, session exclusivity, shared task budgets, persisted event sequences, context accounting, cancellation, restart recovery | runtime_core and API tests; browser refresh recovers the same events; unfinished work becomes interrupted, never an automatic write replay |
| DeepSeek | Bounded compatible Chat Completions loop; prompt/tool request budget and actual provider usage | Live PDF requirements to Word/XLSX edits completed with 7 calls, zero tool errors, 2 validated copies, unchanged original hashes; turn `47499b754e744a84a37f5a0b504cb1a5` |
| Children | Isolated read-only evidence/review contexts, at most 2 concurrent / 4 total, shared budget and cancellation | Scripted HTTP and earlier live execution exercised child tasks; no child write capability |
| Jev | Typed Choice/Score/Noul questions; off/shadow/assist; host constructs legal candidates | Contract tests only. No live Jev credential/accuracy validation. Assist can request extra read-only document review, cannot approve engineering results or writes |
| Document workers | DOCX paragraph/cell, XLSX typed cell/range/formula, PDF annotation/text form/page order; immutable source + new copy | Real worker tests and live independent OOXML reopening; matching old values/hashes, verified quotation references, identical preview or preview_id, high-risk signoff and verdict gates |
| RAG | Selected-source SQLite FTS5/BM25 plus lexical rules; original hashes and locators | 13 retrieval tests; re-extraction verifies citations. No embedding dependency, no automatic trust promotion of generated drafts |
| Sandbox | Fixed isolated Python entry, env credential scrubbing, bounded IO, kill/reap, truthful process probe | Actual Windows worker tests. Low Integrity/Job controls writes/spawn; kernel read/network confinement is not claimed |
| Engineering | User-authorized saved CAD/frame revision and input hashes, fixed numerical tools, before/after version check | Real CAD UI and analytical rectangle/beam fixtures; Agent model acceptance recorded separately. Model supplies only selection_index. Shared domain project library is not yet per-workspace migrated |
| Voice | Explicit prepare, cancellable process, editable transcript, no late insertion after session/workspace change | Service/JS tests. Local faster-whisper/model absent in acceptance environment; microphone/transcription quality not tested |
| UI | Unified entry, 66 posts, file and engineering selection, context/usage/task history, result cards, draft downloads | Real desktop browser and narrow layout; engineering cards show direct solver values and retain raw events as secondary detail |
| Existing domains | CAD, frame/section/IFC, schedules, planning and routing pages; modular legacy post UI | Existing regression suite retained. Only saved CAD section/frame are registered as new Agent engineering tools |

## Acceptance record

- Scripted cross-file HTTP task: 2 workers' output copies, citation verification, immutable originals and output hashes passed.
- Live cross-file task: successful output was independently reopened; unmodified OOXML parts/styles/other sheet preserved. A repeat exposed redundant model calls reaching the conservative root budget; preview_id and explicit completion guidance corrected the loop, and the subsequent run completed without tool errors.
- CAD browser task: explicit current-turn solid/hole confirmation, steps/read-only, 5 durable events, zero model calls. Rectangle 400 x 600 mm yielded area 240000 mm2, Ixx 7.2e9 mm4, Iyy 3.2e9 mm4, centroid (200,300); refresh preserved the completed task and cleared new-task authorization.
- Saved beam fixture: 4 m simple support, 1000 N/m load; reactions 2000 N each, |M|max 2000 N*m, |dy|max 0.002083333333 m. These are synthetic analytical checks, not real engineering acceptance.
- PR57 scorecard: all 35 pilots passed actual offline execution; execution exceptions now fail instead of producing a schema-only green status.
- Default gate initial final-integration run: 115/116, with one existing 0.4-second semantic HTTP deadline test failing under concurrent compilation. Its isolated rerun and the extended-gate rerun passed; no production timeout or test assertion was weakened.
- Real DeepSeek engineering turn `456a38e82a894974bf9a21416120f47d` called both section and frame workers; the independent analytical checks passed. A native frame schema mismatch found by this live test was fixed without changing solver values.
- Final Rust suite: 190 passed / 0 failed / 0 ignored. Agent UI: 18 passed. Extended non-Rust checks: 9/9 gates passed, including HTTP demo 87 passed / 9 optional-dependency skips. An earlier Rust attempt compiled during the frame fix; the complete stable-source rerun supersedes it.
- Unpacked clean-environment acceptance exposed an optional JVM discovery exception. Capability probing now returns MPP/P6 unavailable while retaining JSON/CSV/XLSX/XML; 4 targeted tests include the real HTTP endpoint. No Java installation is attempted.
- Machine-readable summary: [2026-09-21 acceptance](../acceptance/2026-09-21.json). Distribution verification and unpacked startup are recorded with the delivered package. Optional dependency skips remain skips.

## Unverified or intentionally unsupported

PDF body rewriting/OCR; Office visual rendering; spreadsheet formula recalculation or full formula semantic validation; real microphone/ASR model quality; live Jev/calibrated confidence; arbitrary code/shell tools; automatic IFC/planning Agent tools; engineering approval. File structure/hash checks do not imply layout correctness or engineering truth. The existing Python CLI and its older policies remain a separately documented compatibility entry.

See [startup and capability details](../unified-workbench.md). Distribution packages contain no credentials and require Python dependencies separately.
