# Rust unified workbench

This implementation records feature-specific evidence in the [acceptance record](architecture/implementation.md). It runs a Rust task host and a fixed Python domain service, with one model loop in Rust for new Agent tasks. Existing specialist, CAD and engineering surfaces remain available during migration.

## Start locally

For the Windows release ZIP, install x64 CPython 3.11, extract to a writable short directory such as `C:/CivilBuddy`, run
`prepare-unified-workbench.bat` once, then use `start-unified-workbench.bat`. Preparation installs
the pinned, hash-verified base, document and logistics wheels in a package-owned versioned environment.
Only explicit preparation or rebuilding installs dependencies; normal startup and `--check` never install anything.
A failed preparation leaves the previous successful selection intact. Keep project/state paths unchanged
when upgrading and prepare a new environment in the new package; do not move a virtual environment.
The package does not include Python, credentials, speech models or a preinstalled environment.

For reproducible optional preparation, choose a fixed profile explicitly:

```powershell
.\prepare-unified-workbench.bat --profile speech-planning
.\start-unified-workbench.bat
```

`core` provides base services, documents and logistics. `speech` adds the CPU speech runtime;
`planning` adds the CP-SAT resource solver, graph algorithms and safe XML reading;
`speech-planning` includes both. Every profile has a complete exact-version, hash-verified
Windows x64/CPython 3.11 wheel lock with the same core versions. Profiles use separate owned
environments, selected only after inventory, native-library and fixed-solver checks pass.
Failure preserves the previous successful environment. Native speech checks do not load or
download model weights, and are not microphone acceptance. Prepare the speech model explicitly
from the voice control after launching.

When `--profile` is omitted, a new package selects `core`; a prepared package retains its trusted
current profile, including during `--rebuild`. If the selection cannot be read or its profile is
invalid, explicitly choose `--rebuild --profile <profile>` rather than guessing its capabilities.
Use `--profile core` to return to the base runtime. Offline wheelhouses must contain every wheel
in the selected profile's lock. MPXJ/Java interchange, CAD, IFC and OCR remain separate optional
runtimes, and are not included in these speech/resource-planning profiles.

If the selected environment has damaged packages, a missing interpreter or damaged preparation metadata,
stop its workbench and run `prepare-unified-workbench.bat --rebuild`. This installs the same hash lock in a
fresh generation directory and selects it only after verification. Old and failed directories are retained;
each rebuild attempt creates a new directory. Ordinary preparation reuses the current successful selection.
For offline preparation, pass `--wheelhouse C:/CivilBuddyWheels` containing every required locked wheel;
this also works with `--rebuild` and never downloads missing wheels from another source.

For a manually configured Python 3.11+ environment, install the project's Python dependencies plus
`requirements-documents.txt` and `requirements-logistics.txt`. Additional engineering capabilities remain
optional dependencies in their existing requirements files. Beyond the managed speech/resource-planning
profiles above, add optional capabilities in a separate manually
configured environment, then pass that interpreter to the unified launcher with `--python`. Do not add packages
to the release-managed `.civil-buddy-runtime` environment. Installed package metadata alone does not verify
native libraries, engineering solvers or speech models.

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

Named instances also have a standard-library offline backup tool: `scripts/instance_backup.py`
with `backup`, `verify` and `restore` commands. It preserves the job plus its exact instance state,
checks identity and complete file hashes, and refuses an active managed-launcher lock or a non-empty
restore target. Restore requires the original user and absolute paths. See the
[personal backup guide](instance-backup.md); private instance backups are not colleague handoff packages.

## Provider configuration

For a named account and a single private job directory, use the launcher options `--user-id`, `--workspace` and `--token-file` together. The full [release and handoff guide](release-handoff.md) describes state ownership, login, project-package transfer, restart recovery and outstanding real-world acceptance. Named instances isolate users by separate processes and physical workspaces; they are not a shared-process multi-tenant service.

### Receiving project document handoffs

The project-records page accepts its own document handoff ZIPs in a different authorized project. Select the ZIP, verify its source and counts, then explicitly allow new copies and confirm the import. Verification does not write records or files. A changed project or file invalidates the preview and write choice. Cancelling an HTTP wait does not undo a server import; an uncertain result can be checked by refreshing records or explicitly retrying the exact same package.

Import creates new document and issue IDs, new local revision-one snapshots beneath `.civil-buddy/out/handoffs/`, and a receiving-user audit trail. Source identities, original paths and revisions remain provenance. Previous internal reviews are cleared; previously resolved issues require review again. An issue referring to an earlier revision absent from the ZIP is marked with unavailable evidence and local revision zero. Only an explicit issue update after checking the current document rebinds that evidence. Registered imported sources remain selectable in the revision editor even though ordinary file discovery omits hidden output directories.

The exact package hash and target project form a durable idempotency key. An explicit retry, including after restart, reads the original import receipt instead of creating more copies. The ZIP, manifest, audit, member paths, expanded sizes, snapshot hashes and references are validated before publication. Normal failures roll back records and owned new files. This is not a demonstrated crash-atomic filesystem/database restore: process failure can leave an uncommitted output directory. The import does not restore account bindings, credentials, model settings, Agent history or old revision files omitted by the exporter. Package and project storage limits still apply.

Pass the original exported ZIP directly. The receiver admits the workbench's native single-disk ZIP subset: Stored/Deflate, UTF-8 paths, no additional ZIP metadata, encryption or ZIP64. Recompressing the package may add unsupported metadata; an ordinary archive without the workbench manifest is not a document handoff.

Execution scope and source identities are recorded in the [2026-10-15 handoff acceptance record](acceptance/2026-10-15-project-handoff-import.json).

### Selecting received materials in Agent

After importing on the project-records page, open the same receiving project in Agent, refresh **Selected source files**, and explicitly tick the entries labelled **Received project material**. Importing does not select sources, start a task or grant write permission. PDF, DOCX, XLSX, TXT, Markdown, CSV and JSON are supported Agent source formats. IFC/DXF snapshots remain project-records materials; use existing domain workflows separately. This follow-up does not make them Agent document inputs.

A received selection retains its exact path, import ID, local document ID, current registered revision and registered SHA-256. The imported hidden path admitted by this selection flow is `.civil-buddy/out/handoffs/<import_id>/<document_id>.<supported-extension>`, proven against the current project records and original import receipt. This does not open other hidden outputs, source manifests, audit files or instance state to discovery. Ordinary visible source paths keep their existing selection behavior. Inventory metadata is not an assertion that the file bytes have just been verified; the host rechecks the selected registration and actual bytes for execution.

If a complete refresh shows a changed registered version or hash, Agent deselects that material and asks you to review and select it again. A partial or malformed response retains the previous explicit selection and its original binding; it never silently upgrades to a newer revision, and the server still rejects a stale binding when submitted. Chinese/English switching preserves the selected identity and original title. Task recovery keeps that same identity without automatically resubmitting or restoring a prior write authorization.

Use read-only inspection first. Creating a draft still requires the existing workspace-write and document-preview checks, and saves a new copy; the received source and sender's original are not overwritten. Missing or inconsistent registration proof prevents the received source from being used as verified material. An older saved artifact without the required source-origin receipt reports that its source version **was not recorded and cannot be verified**, even if its source bytes still match; review or regenerate it from an explicitly selected current source before relying on its provenance.

The [received-source acceptance record](acceptance/2026-10-15-agent-handoff-sources.json) binds this follow-up to the tested native binary, fixed workers and actually served assets. Two new named instances on one Windows computer exercised the full import, explicit selection, read, retrieval, delegation, preview, Word/Excel copies, source-version checks and restart flow using synthetic documents and a local scripted model. Cookie/DOM checks used JSDOM with standard browser API bridges, rather than a native browser. This does not establish real-project, live-model, Office, microphone or second-computer acceptance. The earlier handoff-import record remains separate.

Worker events retain the full original protocol and isolation diagnostics. For known fixed-worker envelopes, the main and child agents receive a compact model view that omits repeated outer protocol version, call ID and sandbox diagnostics; business results, errors, source identities, quotes and validation fields remain unchanged. Their system instructions still explain that successful tools do not establish kernel read or network isolation. This reduces repeated context without raising the context or usage limits. Larger conversations can still exceed those limits; a failed turn must not be presented as completed merely because some draft copies were saved.

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

Jev uses the [TypeSafe System One API](https://docs.typesafe.ai/introduction/quickstart). The host defines candidates/questions from selected evidence. Off makes no Jev calls; shadow records validated proposals. For document evidence, assist may schedule an additional read-only review when the fixed candidate and confidence gate pass. The initial 0.9 confidence threshold is an unevaluated product setting, not a claimed accuracy guarantee. Jev cannot permit a write, change solver numbers or approve an engineering conclusion. The packing replan adapter below records engineering proposals in shadow mode even when assist is configured; changing a packing strategy based on Jev remains disabled pending calibration.

## Execution guarantees and limits

Document publication summaries come from this turn's registered copies and source/output hashes.
An explicit write request with no artifact, a failed tool, or an unapplied preview is reported as incomplete;
the model's own “saved” or “Done” statement is not a receipt. This does not certify that every part of a
free-form request was fulfilled, and ordinary explanatory replies do not receive comprehensive numeric
provenance verification. The legacy Python reply guard is a separate implementation.

- Events persist before the page sees them. Refresh reconnects from the last sequence; restart marks unfinished tasks interrupted and never silently repeats writes.
- If a start response is lost, an explicit retry of the same complete request uses its original
  submission key. A reload only looks for a uniquely matching server task after the saved history
  boundary, within 500 records; it never automatically resubmits. Recovery metadata contains hashes
  and identifiers, not the request body, write permissions or high-risk acknowledgement. If recovery
  is ambiguous, inspect execution history. To run against changed file contents, create a new session
  and select the files again. Recovered tasks retain their original source-check times.
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
- PDF body rewriting/OCR and Office visual rendering are not provided by this worker. The optional desktop Excel channel below runs separately from ordinary Python workers.

The product's ordinary tests use scripted local models. `scripts/unified_acceptance.py --live` is separate
opt-in acceptance against an already configured local server. It requires `--expected-model` and
`--expected-provider-host`, matching safe host-only capabilities and refusing scripted model names.
`--token-file` reads a local workbench login token kept outside the fixture/report directories; it does not
read provider keys. It refuses HTTP redirects, verifies originals even after failure, and waits a bounded
grace period after cancellation. If shutdown cannot be observed, it reports that uncertainty. Usage and
response-model receipts are product reports, not independent provider attestation or a monetary spending cap.

## Engineering, speech and distribution

The Agent page lists saved CAD sections, explicit frame projects and construction plans from the domain service. That library is shared within this launched instance; registering a source workspace does not silently migrate or filter the existing project library. A turn authorizes up to four exact project revisions and input hashes. CAD requires a fresh entity/hole confirmation. The model can choose only an index, never supply geometry or solver inputs. Computation checks the project version before and after the worker; result cards preserve solver units, source revision and signed sampled extrema.

The fixed domain service also mounts the existing CAD, analysis, schedule, planning and routing pages. Planning/schedule records in the unified launcher use its state directory. These pages retain their deterministic services; they are not registered as arbitrary code execution tools. IFC remains on its own page.

A saved construction plan can enter the unified Agent from the planning page. The link binds the project ID, saved revision and an input SHA-256 covering tasks, dependencies, calendar, resources and the saved calculation method. It carries saved inputs only; unsaved edits must first be saved. Open a workspace, read the authoritative snapshot and explicitly select it for the task. A stale link cannot silently select a newer revision. Reopening or refreshing clears the previous selection.

`planning_inspect` reads the selected saved record and labels its result as **this read did not recompute the plan**. `planning_calculate` reruns the fixed CPM or OR-Tools solver using that same saved method. The model supplies only a selection index, never durations, dates, resource capacities or code. Model mode can select this calculation tool through the user's task; the optional steps-mode calculation control is an operation choice, not another permission gate. The host verifies the source binding before and after execution and derives planning facts from tool receipts. Ordered evidence retains earlier calculation attempts when a later inspection occurs; the final report distinguishes a saved record, a successful calculation and a failed attempt. A missing resource solver fails without replacing resource scheduling with CPM. Neither tool changes the saved plan or approves construction; inspect conflicts, assumptions and source revisions before using the result.

Logistics capabilities report **digital PDF table parsing** and **scan OCR** separately. A supported extension does not mean its dependency or model is ready. Digital PDF readiness checks the `pdfplumber` import only; field accuracy still requires source review. Scanned documents need the separately prepared OCR runtime and assets. CSV/Excel intake remains usable when these optional paths are unavailable. Missing dimensions or weight scopes stay missing and can block packing.

Speech uses a cancellable fixed process with an explicit prepare step for model download. Audio transcription produces editable text and never auto-submits. Missing faster-whisper/model assets are reported as unavailable. Browser speech fallback requires the existing explicit consent flow. Preparation requests now check HTTP failures and use a 10-second control-request timeout; the UI waits at most 15 minutes for readiness and cannot begin recording from a late response after cancellation or timeout. Cancelling the UI wait does not certify that server-side preparation has stopped.

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

## Source-bound packing replan

In the Agent page, select a `packing_replan.v1` JSON file in the source list and explicitly choose it under **Packing replan review**. **Check sources** mode runs the fixed calculation without a language model; model mode exposes `packing_replan` with only a selection index. Ordinary selected JSON documents do not automatically become packing inputs. The optional selection is cleared when submitting a task or switching its context.

The [synthetic example and field guide](../../examples/packing-replan/README.md) are included in the Windows package. Copy the example to a test workspace before opening it; its dimensions and limits are examples, not defaults for a real shipment.

This first adapter requires an explicit container type, maximum container count, clearance, box net-mass limit and complete material dimensions, quantities and weights. It does not infer those constraints from an arbitrary spreadsheet or free-form request. Unsupported transport conditions and missing inputs stop for human clarification. The original source is immutable; its SHA-256 is captured before task creation and checked around computation and any Jev wait.

The fixed worker reuses the existing boxing/loading tools, computes the original baseline, and tries no more than two distinct fixed-order search candidates. Physical constraints cannot be relaxed by a model, Jev or the old critic's option deltas. A candidate must pass layout and conservation checks and improve the actual comparison objective before it replaces the baseline. The UI shows baseline and retained values separately; a layout fit never constitutes shipment release or a structural safety verdict. Repeated calls for the same source in one turn reuse the recorded calculation rather than repeating the search.

Jev sees only host-constructed, already-calculated candidate choices. Off performs no Jev request. Shadow records a validated suggestion without changing the result; missing credentials, invalid responses, timeouts and unavailable decision budget leave the deterministic path unchanged. The source hash and phase identify the decision independently of document-evidence review. This integration does not establish live Jev service reliability or confidence calibration.

Explicitly selected packing sources must each produce a successful calculation receipt before the model turn can report completion. Missing calls, failed inputs and infeasible results keep the task partial even if the model claims success. Unsupported transport-field aliases are rejected with instructions to preserve their requirements for human review; they are never silently discarded. Packaging structure issues are reported separately from geometric fit.

The [2026-10-03 acceptance record](acceptance/2026-10-03-packing-replan.json) records the bounded synthetic example, independent geometry checks and current validation limits. Its unchanged five-container result is not an efficiency improvement or a shipping authorization.

The separate [live packing follow-up](acceptance/2026-10-03-live-packing.json) preserves real DeepSeek receipts, independent numerical checks and failed attempts. Model response tokens are provider-reported usage, not billing proof. Raw tool arguments are not retained; the strict index-only schema, single selected source and full source/hash result bind the demonstrated calculation.

Final-reply review distinguishes local English negations such as “No files were modified” from an actual publication claim. A positive claim is still replaced with the registered-artifact summary, and its reviewed draft is retained under `reply_review` with `draft_trust=model_claim`; this draft is audit material, never evidence that a file was saved. Completed executions with `result.partial=true` display “Partially completed” in the result and in history entries for which detailed result evidence is available. The partial notice does not imply that downloads exist.

Saved model replies carry an “AI interpretation” label, translated with the interface while preserving the original reply. Fixed tool result cards and the execution timeline remain separate evidence. The repaired English run retained the correct core calculation but still confused ten boxes with ten box types and incompletely described its tools; neither the label nor a completed workflow certifies full prose accuracy.

New model tasks with explicitly selected packing inputs return a host-generated packing execution receipt as the main reply (`reply_origin=host`). Source hashes, baseline/final container and box counts, recorded replan rounds, structure counts in boxes, and successful main-agent tools come from fixed-worker and host records. Missing counts stay “not returned”; skipped sources, calculation failures, required clarification and infeasible layouts stay distinct. Existing document-publication receipts are preserved when the task includes both documents and packing.

The complete verdict-reviewed model interpretation is retained separately as `model_interpretation` with `trust=model_claim`, behind “AI interpretation (review required)” in the interface. This preserves additional analysis without presenting its claims as the calculation receipt. Later turns retain that interpretation as explicitly unverified assistant history within the existing context budget. Original stored tasks are not rewritten. The receipt does not certify all requested work, structural suitability or shipment release, and it does not make the retained model interpretation accurate.

The [packing receipt acceptance record](acceptance/2026-10-03-packing-receipts.json) replays the earlier incorrect model text through a localhost scripted provider and the actual Windows host/calculation worker. The main receipt contains the tool values, the original interpretation remains available, and independent numerical checks pass 46/46. This is an offline regression of the new reporting behavior, not an additional live DeepSeek acceptance.

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

## Upgrade, restore and Office follow-up (2026-09-30)

A same-machine upgrade and restore check made a complete offline backup, verified its hashes, retained the prior directories and copied the backup back to the original paths. The newly extracted `0.5.0-practical-preview.20260930.8` program used the same absolute workspace and state roots. Restored hashes matched; identity, the existing task and usage record, source originals and downloaded artifact bytes were preserved, and the saved session remained discoverable. No model request was made. This verifies that particular same-path restore and program-directory upgrade on one computer; it does not establish relocation to a different workspace path or a second-computer installation. Evidence is recorded in `work/practical-upgrade-restore-20260930/report.json` outside the repository.

Separate Office quality checks then used the existing synthetic live-model artifacts. Microsoft Word 16.0 opened the DOCX read-only, repaginated it and exported a one-page PDF. Poppler rendered the PDF at 150 dpi; the page was visually reviewed with no clipping, overlap, missing text or text errors observed, and extracted PDF text matched the source paragraphs. Microsoft Excel 16.0 ran `CalculateFullRebuild` and saved a separate XLSX copy. Reading that saved copy with `openpyxl` in `data_only` mode confirmed the cached value `D2 = 12`; the formula remained `=B2*2` with `B2 = 6`. The original source files and registered artifacts retained their hashes, and no new model request was made.

These are historical application-level rendering and recalculation checks on simple synthetic files. At that time, Word and Excel automation were outside the product's deliverable-inspection tools. The optional Excel channel below is a later implementation. Complex real-project pagination and formula recalculation, and cross-computer installation and restore, still require acceptance. The Office report, exported PDF and recalculated copy are under `work/practical-office-acceptance-20260930/` outside the repository; the [acceptance record](acceptance/2026-09-30-practical.json) includes their bounded results and output hashes.

## Optional local Excel recalculation

The [2026-10-10 feature acceptance record](acceptance/2026-10-10-local-excel.json) records 37 actual
same-machine HTTP checks through Agent preview/apply, native Excel, immutable child download,
cancellation and restart. The parent turn used a loopback scripted model; these checks do not constitute
real-project, paid-model or second-computer acceptance. Earlier failed attempts remain in local evidence.
This record is bound to its listed initial source and binary hashes. Subsequent host hardening and
newer Windows ZIPs use separate local package reports; the initial result is not retargeted to them.

On an interactive Windows desktop with Microsoft Excel installed, explicitly add
`--excel-engine windows-desktop` to the unified launcher. Normal startup defaults to `off`, including
when an inherited environment variable requests the engine. `--check` never activates Office.
The launcher and Rust host reject this channel when a public origin is configured. The capability
response checks configuration and the fixed broker file; only a completed job attests an actual Excel run.
Excel, its licensing and desktop session are not included in the ZIP.

In the Agent page, inspect a registered XLSX draft, permit workspace writes, and enter the displayed
acknowledgment for this operation. Click the recalculation action to create a persistent job.
This action is separate from model tools: a model cannot start Office or supply paths, scripts or formulas.
Job records survive refresh and restart; unfinished jobs become interrupted and are not silently repeated.
An idempotency key prevents a lost response from producing a second calculation. Cancellation must stop
the owned broker before a running job becomes cancelled.

The host first checks exact source and parent-copy hashes, then the fixed read-only Python worker admits
the workbook. Profile `civil-xlsx-engine-v1` supports ordinary XLSX worksheets, static A1 references and
a parsed allowlist of common arithmetic, conditional, lookup and aggregate functions. Limits include
32 sheets, 100,000 cells, 5,000 formulas and 2,048 characters per formula. Unknown features fail closed.
External relationships, macros/XLM, data connections, embedded objects, charts, unsupported formula
surfaces and executable/network/dynamic-reference functions are rejected before Office activation.

The compiled Rust host binds the fixed PowerShell broker's bytes, starts a dedicated STA process,
verifies a newly created Excel process by a held native handle and creation time, and permits only one
host-created staging workbook. The broker disables events/macros, opens read-only with `UpdateLinks=0`,
runs `CalculateFullRebuild`, captures every typed formula result and saves another copy. Cleanup uses
the owned process/job handles rather than terminating all Excel processes. Existing user workbooks are
never attached. Office automation is a dedicated desktop channel; it does not inherit the ordinary
worker's process sandbox and is not a server-side Office service.

When admitted bytes request `forceFullCalc` or `fullCalcOnLoad`, the broker creates a separate calculation
input and clears only those two request flags before opening it. Some installed Excel builds remain
`xlPending` if those flags were true at open, even after a full rebuild; changing COM properties afterward
did not resolve the observed failure. The retained input stays byte-for-byte unchanged. The receipt records
both input hashes and the changed flags, and a completed job still requires the engine's actual `xlDone`
state plus every saved result. This consumes a calculation request; it does not replace calculation with
an XML flag or modify formula/input/style data.

Before publication, the worker verifies the saved package's inputs, formulas, resolved styles, names,
merges and supported layout semantics, and compares every persisted formula cache with the captured
Excel value. The host rechecks provenance and registers a new child artifact with hashes and the engine
receipt; the original and parent copy remain unchanged. Genuine layout changes cause failure: for
example, installed Excel changed an openpyxl fixture's default row height from 15 to 14.4, so its output
was rejected even though the formula values were correct. A successful calculation is still a draft
requiring professional review; it does not certify design correctness or visual layout.

Microsoft documents that disabling VBA macros does not disable Excel 4.0 macros, which is why the
package admission step is required. See [AutomationSecurity](https://learn.microsoft.com/en-us/office/vba/api/excel.application.automationsecurity),
[Workbooks.Open](https://learn.microsoft.com/en-us/office/vba/api/excel.workbooks.open), and
[CalculateFullRebuild](https://learn.microsoft.com/en-us/office/vba/api/excel.application.calculatefullrebuild).

## Live read-only delegation follow-up (2026-09-30)

A separate, explicitly live acceptance used the configured `deepseek-flash` model at `api.deepseek.com` and a fresh synthetic workspace. The main agent delegated one evidence and one review child, both constrained to read-only access to a single selected PDF. Each child independently searched, read and verified the source quotation; both completed before the parent produced its final summary. Nine model responses were recorded across three tasks, taking 15.905 seconds. The existing shared budget was not changed or exhausted, and completion released all reservations. There were no tool errors, document previews or artifacts; original hashes stayed unchanged. Independent PDF extraction matched the requirement in each child's search receipt and the final host-verified quotations.

This proves that particular two-child workflow on the recorded Windows binary, not general engineering correctness or real-project coverage. The event log preserves child search hits and verification results but not the `verify_sources` arguments, and final host quotations are deduplicated without child attribution; it does not establish an argument-level audit trail for each child's verification. Provider-reported usage is not independent billing proof. No Jev event occurred. The local report is `work/practical-live-subagents-20260930/report.json` outside the repository, with the source commit, binary hash and bounded checks recorded in the [acceptance record](acceptance/2026-09-30-practical.json).

The Agent page renders saved Markdown with DOM-created headings, tables, lists, quotations and code. HTML remains literal text, images do not load, and only validated HTTP(S) links become active. Unsupported or oversized input falls back to unchanged plain text. Source quotations and stored replies remain unchanged. Subtask events show the role and child ID separately from the initiating identity, and findings remain explicitly labelled as model claims. Language switching during paged recovery retains the latest server task status. A successful manual workspace choice clears a stale workspace query so reloading cannot silently return to the previous linked project; invalid and failed selections preserve the link.

The existing live task was opened and reloaded in the browser without another model request: one comparison table, five headings, both completed child roles and the original PDF quotations were visible. Chinese/English switching preserved the reply and source text, inline JSON retained real quote characters, and the 390-pixel viewport had no document-level horizontal overflow. Agent UI tests passed 60/60, i18n 4/4 and release tests 9/9; the new renderer is included in the release allowlist. Browser proof is recorded separately in `work/practical-live-subagents-20260930/browser-review.json` outside the repository.
