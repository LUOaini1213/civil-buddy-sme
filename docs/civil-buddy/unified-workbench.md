# Rust unified workbench

This implementation records feature-specific evidence in the [acceptance record](architecture/implementation.md). It runs a Rust task host and a fixed Python domain service, with one model loop in Rust for new Agent tasks. Existing specialist, CAD and engineering surfaces remain available during migration.

## Start locally

Install the project's Python dependencies plus `requirements-documents.txt` in a virtual environment. Engineering and local speech remain optional dependencies in their existing requirements files.

```powershell
cargo build --release --manifest-path workbench/Cargo.toml
python scripts/start_unified_workbench.py --python .venv/Scripts/python.exe --env-file demo/.env --open
```

Add `--check` before first launch to inspect the selected Python version, required package metadata,
Rust executable, port and directory configuration. It does not start the product, load provider configuration
or create project/state files. Optional package discovery reports unavailable capabilities without blocking
base startup; it does not prove native libraries or speech models can run. Both services and document workers
use the same resolved interpreter, including when `--python python` is found through PATH.

The launcher binds both services to loopback, starts `/static/agent.html`, and stops both on Ctrl+C. `--state-root` isolates task history/domain records. `--binary` selects an already built executable. It refuses a second launcher using the same state directory. Source workspaces are opened explicitly in the page; only selected files enter a model task. New copies are saved beneath that workspace's `.civil-buddy/out`.

The default view offers a model-free structural check and a model task. Configure the model in the page or through environment variables. A model task with `read-only` cannot apply a document patch. `workspace-write` permits new draft copies; original files cannot be replaced.

## Provider configuration

For a named account and a single private job directory, use the launcher options `--user-id`, `--workspace` and `--token-file` together. The full [release and handoff guide](release-handoff.md) describes state ownership, login, project-package transfer, restart recovery and outstanding real-world acceptance. Named instances isolate users by separate processes and physical workspaces; they are not a shared-process multi-tenant service.

## Provider configuration details

```dotenv
DEEPSEEK_API_KEY=your-key
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-flash
JEV_MODE=off
# Optional: JEV_MODE=shadow or assist
# JEV_API_KEY=your-key
# JEV_ENDPOINT=https://api.typesafe.ai/v1/systemone
# JEV_MODEL=jev-latest
```

The target model is **DeepSeek V4.1 Flash**, requested as `deepseek-flash`. The historical acceptance request ID `deepseek-v4-flash` is retained in the report; [DeepSeek documents](https://api-docs.deepseek.com/updates/) that this compatibility alias is served by V4.1 Flash.

DeepSeek Chat Completions requests explicitly disable thinking for the initial tool-loop baseline, bound output tokens, preserve complete tool interactions, and record provider usage. See the [official DeepSeek API](https://api-docs.deepseek.com/api/create-chat-completion/). An explicit existing model setting is preserved.

Jev uses the [TypeSafe System One API](https://docs.typesafe.ai/introduction/quickstart). The host defines candidates/questions from selected evidence. Off makes no Jev calls; shadow records validated proposals; assist may schedule an additional read-only review when the fixed candidate and confidence gate pass. The initial 0.9 confidence threshold is an unevaluated product setting, not a claimed accuracy guarantee. Jev cannot permit a write, change solver numbers or approve an engineering conclusion. Engineering replan adapters beyond document review remain on the implementation checklist.

## Execution guarantees and limits

Document publication summaries come from this turn's registered copies and source/output hashes.
An explicit write request with no artifact, a failed tool, or an unapplied preview is reported as incomplete;
the model's own “saved” or “Done” statement is not a receipt. This does not certify that every part of a
free-form request was fulfilled, and ordinary explanatory replies do not receive comprehensive numeric
provenance verification. The legacy Python reply guard is a separate implementation.

- Events persist before the page sees them. Refresh reconnects from the last sequence; restart marks unfinished tasks interrupted and never silently repeats writes.
- A task tree shares input/output/call budgets. At most two read-only children run together, four children in total, depth one; cancellation propagates.
- Context occupancy is a conservative serialized-byte estimate, separately shown from model billed usage and task stages. It never silently removes the current request or half a tool interaction.
- Fixed Python workers establish the requested OS sandbox before reading their request. The default `CIVIL_WORKER_SANDBOX=os` fails closed. Explicit `app` is a diagnostic/application-policy mode and must never be reported as OS isolation.
- On Windows, the existing Low Integrity/Job backend restricts writes/spawn. It does not claim kernel read or network confinement; the UI records the actual process probe.
- RAG uses selected-source SQLite FTS5/BM25 with original hashes and exact locators. Verification re-extracts the current original, not the derived index. This proves quotation identity, not engineering truth.
- Ordinary model tasks collect source quotes from this turn's actual main/child tool calls. Before completion,
  the host rechecks up to 12 quotes against the selected originals and displays their locator, original/current
  hash and check time. Changed, unavailable, invalid and timed-out sources remain visible. These are source
  identity receipts at the stated time, not proof that every model sentence is supported; model-written prose
  cannot create a verified card. Original quotations are preserved when switching the interface language.
- Document patches require matching original hashes, expected old values and a successful identical preview. The returned preview_id can apply the cached patch without asking the model to reproduce it. Added numbers need preserved original values, explicit user input or verified source quotations. Saved files remain model proposals.
- PDF body rewriting/OCR, Office visual rendering and spreadsheet formula recalculation are not provided by this worker. Results report these limitations explicitly.

The product's ordinary tests use scripted local models. `scripts/unified_acceptance.py --live` is separate
opt-in acceptance against an already configured local server. It requires `--expected-model` and
`--expected-provider-host`, matching safe host-only capabilities and refusing scripted model names.
`--token-file` reads a local workbench login token kept outside the fixture/report directories; it does not
read provider keys. It refuses HTTP redirects, verifies originals even after failure, and waits a bounded
grace period after cancellation. If shutdown cannot be observed, it reports that uncertainty. Usage and
response-model receipts are product reports, not independent provider attestation or a monetary spending cap.

## Engineering, speech and distribution

The Agent page lists saved CAD section and explicit frame projects from the domain service. That library is shared within this launched instance; registering a source workspace does not silently migrate or filter the existing project library. A turn authorizes up to four exact project revisions and input hashes. CAD requires a fresh entity/hole confirmation. The model can choose only an index, never supply geometry or solver inputs. Computation checks the project version before and after the worker; result cards preserve solver units, source revision and signed sampled extrema.

The fixed domain service also mounts the existing CAD, analysis, schedule, planning and routing pages. Planning/schedule records in the unified launcher use its state directory. These extra pages retain their deterministic services; they are not all registered as arbitrary model tools. IFC and planning remain on their own pages.

Speech uses a cancellable fixed process with an explicit prepare step for model download. Audio transcription produces editable text and never auto-submits. Missing faster-whisper/model assets are reported as unavailable. Browser speech fallback requires the existing explicit consent flow.

On 2026-09-30, actual Windows HTTP acceptance used a dedicated ASR environment and the **working-tree ASR launcher patch on top of `e199ab7`**, not that clean commit alone. The launcher forwarded the selected local model/cache settings without forwarding provider credentials. An explicit prepare request downloaded the public faster-whisper `small` model and reached `ready` in 48.594 seconds; transcription before preparation returned HTTP 409. The runtime used faster-whisper 1.2.1, CTranslate2 4.8.2 and PyAV 18.1.0. Model download required network access; transcription ran locally without a cloud LLM call.

| Synthetic clip | Actual HTTP result | Observed transcript boundary |
|---|---|---|
| `L01_huihui.webm`, Chinese, 6.37 s | 200; 5.140 s end to end | Returned `请帮我核对这份相单,看看能不能装进一个40尺高柜。`; **箱单 was incorrectly recognised as 相单** |
| `L19_yaoyao.webm`, Chinese, 4.24 s | 200; 4.782 s end to end | Returned `哪些属于危大工程,需要编专项方案。` |
| `english-synthetic.wav`, English, 4.77 s | 200; 4.078 s end to end | Returned `Please compare the tender requirements and prepare an editable draft for review.` |

A separate in-flight request was cancelled: the cancellation endpoint reported `process_reaped=true`, the transcription returned HTTP 409, and the service reported no active request afterwards. Source audio hashes and the workspace file list were unchanged, no audio files were found in the state directory, and the Agent task list stayed empty. These observations establish three functioning synthetic-audio HTTP transcriptions and cancellation, not a speech-accuracy score, real microphone acceptance or field/noise/accent performance. Transcripts still require human review. The local report is `work/practical-asr-acceptance-20260930/report.json` outside the repository; its bounded summary is recorded in the [2026-09-30 acceptance record](acceptance/2026-09-30-practical.json).

The same working-tree follow-up passed launcher tests 12/12, voice UI tests 23/23 and i18n tests 4/4, including known English error details, original Chinese transcripts, timeout and cancellation. Separately, clean commit `e199ab7` passed all four [CI jobs in run 36691392773](https://github.com/LUOaini1213/civil-buddy-sme/actions/runs/36691392773), with smoke 187/187. That CI run predates the ASR working-tree follow-up and does not certify these later changes.

Build a Windows source-plus-executable package after committing distribution sources:

```powershell
python scripts/build_unified_release.py --version 0.5.0-preview --binary workbench/target/release/civil-workbench.exe --output-dir dist
python scripts/build_unified_release.py --verify dist/civil-buddy-unified-workbench-0.5.0-preview-windows-x86_64.zip
```

Use the actual filename printed by the builder for verification. The package includes a supplied Windows executable and the allowlisted source/assets; Python and optional dependencies are installed separately in a local virtual environment. No provider credentials, personal project records or generated outputs are packaged. Each file and the archive have SHA-256 checksums.


## Shared frontend appearance

All seven workbench pages share `demo/static/theme.css`, the persisted appearance bootstrap in `theme.js`, and navigation/status components in `workbench-shell.css`. Domain layouts remain scoped to their page styles. Reuse the shared `--cb-*` tokens when adding surfaces; do not introduce a separate palette. The home composer folds the optional high-risk acknowledgment into a disclosure, automatically expanded when the server requires confirmation; the exact phrase gate is unchanged. Voice warnings use the same status treatment on the home and Agent pages.

2026-09-21 validation: seven targeted offline gates passed (assets, JS/CSS syntax, chat streaming, UI modules, Agent UI, release packaging and real-page DOM); voice harness 13/13; existing CAD 51/51, engineering 14/14, planning 18/18 and date-plan gesture tests passed. Browser checks covered persisted light/dark themes, all seven pages at 390 px without document overflow, and representative desktop layouts at 1280 px. Voice warning presentation was checked with a static simulated warning; microphone recognition was not exercised.


## Packing workbench integration

The navigation and home card expose `/packing`. The unified domain service reuses the existing packing UI and an explicit subset of gateway endpoints for uploads, deterministic packing, human confirmation, SSE progress, run recovery and Excel exports. The Rust bridge streams SSE immediately and uploads validated material-file bytes rather than forwarding a filesystem path. Packing state, exports and checkpoint databases live under the selected state root. Generic gateway agents and MCP dispatch are not exposed by this adapter; model credentials remain in the Rust host.

The standalone gateway continues to support its original routes. In unified mode the page uses SSE and HTTP reload recovery; the supplemental standalone WebSocket observer is not proxied. The default packing engine is the local Python engine; Java is optional. Model-free parser → confirmation → solve → Excel checks and bounded Rust proxy/upload regressions pass. A browser check of the built-in synthetic full-load example produced 15 boxes and stopped at the confirmation gate.

## Deliverable checks

The Agent page checks registered DOCX, XLSX and PDF outputs against their registered SHA-256 and reports whether the original source is current, changed, unavailable or unrecorded. A changed source requires a new copy generated from the updated material. Inspection records identify who requested the check; they are not a reviewer's acceptance or professional sign-off.

Office checks inspect package structure, macros, embedded objects and external relationships without opening links or executing content. XLSX reports formula/cache/error counts: missing or invalid caches and error cells block delivery; existing caches remain unverified. `calcMode=auto`, `fullCalcOnLoad` and `forceFullCalc` on a generated copy request future calculation but do not prove it happened. No Office renderer or spreadsheet calculation engine runs in this check, so page layout and formula results still need review in the appropriate application.

A structurally inspected PDF with no detected active-content markers can open in the browser's native PDF viewer. The preview endpoint rechecks the registered output hash and rejects changed bytes. This is an actual view of that PDF, not a converted DOCX preview or evidence that every page has been visually reviewed. Checks neither validate engineering conclusions nor authorize tender submission, construction or shipment.


## Live document workflow follow-up (2026-09-30)

A selected local project credential resolved an inherited-key HTTP 401. The first authenticated synthetic workflow reached the real provider but repeatedly failed numeric evidence checks and then stopped at the unchanged budget gate. Tool schemas now describe nested Word/Excel patch fields and complete `patches[i].evidence` references; invalid shapes have actionable errors. A separate `verify_sources` success never grants later writes. XLSX calls without an operation or worksheet inspect available sheets first; explicitly requested reads still need an actual sheet name. Existing bounded default ranges remain supported.

With the same fixture generator, task, validation oracle and budget, the next real `deepseek-flash` workflow completed in about 15.6 seconds with five provider responses. Both actual new copies contained the PDF-required value; original hashes, untouched document parts, styles and the unmodified worksheet were preserved. These are structural/content checks on synthetic files, not Word pagination, Excel calculation, engineering sign-off or a field reliability score. The model key stays outside the repository and release archive.

## Persisted Agent history recovery (2026-09-30)

The Agent page now discovers saved sessions from the server when a registered workspace opens, including sessions created through the API before this browser was used. It merges these summaries with local unsent drafts and preserves an existing browser selection. Session summaries contain only the session ID, latest task ID, timestamp, status and task count. Both session discovery and task history are restricted to the current authenticated actor and workspace; task pages additionally respect the selected session. Cursors are bound to that scope, and task boundaries are checked against the stored records again.

`GET /api/agent/sessions` supports `limit` and `cursor` with a default of 50 and a maximum of 100. The existing `/api/agent/turns` endpoint adds the same pagination parameters while retaining its default of 100 and the existing `turns` field. Both return `next_cursor`; the frontend requests 50 records at a time and provides controls to refresh sessions, load more sessions and load older tasks. Late responses are checked against the current workspace and selection. Reloading an older task preserves the loaded history position. Event recovery continues past the first 200-event page, even for a terminal task, until the recorded last sequence is reached; a no-progress guard prevents endless polling. Discovery and recovery do not start a model, replay tools or restore prior write permissions.

Offline validation on the working tree based on `9a6fd62` passed 35 Rust tests: `product_sessions` 5/5, `product_identity` 17/17 and `runtime_core` 13/13. Coverage includes reopening persisted state, 105-session and 105-task pagination with tied timestamps, actor/workspace/session isolation, invalid cursors and unchanged events during history reads. The final Agent UI suite passed 50/50, including terminal event draining and history-position preservation; the separate i18n suite had previously passed 4/4.

A same-machine browser check used a fresh origin with no previous browser history and the existing state directory. It discovered the saved synthetic acceptance session, opened its one task and restored 43 events, four source quotations and the two DOCX/XLSX download cards. Chinese/English switching preserved original filenames and quotations. The task IDs and task count were unchanged before and after; the existing five-call usage record was unchanged, and both downloaded files matched their registered SHA-256. No new model request was made. This validates recovery of that existing task in a fresh browser origin, not a second-computer installation or a browser run with more than 200 events; those larger history cases are covered by the offline tests. The local report is `work/practical-history-recovery-20260930/report.json` outside the repository, with its bounded summary in the [acceptance record](acceptance/2026-09-30-practical.json).
