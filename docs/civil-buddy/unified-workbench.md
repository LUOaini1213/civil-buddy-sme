# Rust unified workbench

This implementation records feature-specific evidence in the [acceptance record](architecture/implementation.md). It runs a Rust task host and a fixed Python domain service, with one model loop in Rust for new Agent tasks. Existing specialist, CAD and engineering surfaces remain available during migration.

## Start locally

Install the project's Python dependencies plus `requirements-documents.txt` in a virtual environment. Engineering and local speech remain optional dependencies in their existing requirements files.

```powershell
cargo build --release --manifest-path workbench/Cargo.toml
python scripts/start_unified_workbench.py --python .venv/Scripts/python.exe --env-file demo/.env --open
```

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
- Document patches require matching original hashes, expected old values and a successful identical preview. The returned preview_id can apply the cached patch without asking the model to reproduce it. Added numbers need preserved original values, explicit user input or verified source quotations. Saved files remain model proposals.
- PDF body rewriting/OCR, Office visual rendering and spreadsheet formula recalculation are not provided by this worker. Results report these limitations explicitly.

The product's ordinary tests use scripted local models. `scripts/unified_acceptance.py --live` is separate opt-in acceptance against an already configured local server; it never reads or writes API keys.

## Engineering, speech and distribution

The Agent page lists saved CAD section and explicit frame projects from the domain service. That library is shared within this launched instance; registering a source workspace does not silently migrate or filter the existing project library. A turn authorizes up to four exact project revisions and input hashes. CAD requires a fresh entity/hole confirmation. The model can choose only an index, never supply geometry or solver inputs. Computation checks the project version before and after the worker; result cards preserve solver units, source revision and signed sampled extrema.

The fixed domain service also mounts the existing CAD, analysis, schedule, planning and routing pages. Planning/schedule records in the unified launcher use its state directory. These extra pages retain their deterministic services; they are not all registered as arbitrary model tools. IFC and planning remain on their own pages.

Speech uses a cancellable fixed process with an explicit prepare step for model download. Audio transcription produces editable text and never auto-submits. Missing faster-whisper/model assets are reported as unavailable. Browser speech fallback requires the existing explicit consent flow. The current acceptance environment did not exercise a real microphone or download a speech model.

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
