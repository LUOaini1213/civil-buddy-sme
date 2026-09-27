# Security

Civil Buddy drafts internal documents for engineering and logistics staff: bid responses, packing and container
plans, site reports. Its outputs are drafts for a person to check, never signed or submitted documents. This page
states the safety model, which checks test it, what is still open, and how to report a problem. Every claim here is
backed by a check in `npm run check` (`scripts/check_project.py`) or by a known-open item listed below.

v0.7.0 is the submitted competition version; `main` holds post-submission preview work. Earlier trial builds
(including the Rust workbench zips on GitHub Releases) are not maintained.

## Safety model

1. **Code computes; the model never decides.** Container counts, crate sizes, masses and coordinates come only from
   the deterministic packing engine (`pack-ship`). In the tender-to-packing link
   (`packing_assistant/tender_packing_link.py`) each statement's status is computed from the loading plan: a
   statement is `covered` only when a plan figure supports it; lashing / securing, stillages, upright transport,
   stacking and delivery sequencing are never `covered`; a plan that does not fit evidences nothing. The link record
   is written with `confirmed_by_person = false` and `submit_blocked = true`. With no model key (the default) no
   model runs at all.
2. **The model never approves.** In model mode the model picks tools from a fixed menu; its tools and the Python MCP
   server have no approval field, and a copy of the confirmation sentence in a model reply is replaced. The deliverable
   writer receives only the user's words and the named job files, so model text does not reach a draft. Every model
   reply passes three deterministic checks:
   - *number provenance*: a quantity or clause number with no source in the turn's evidence is rewritten once, then
     listed to the user;
   - *verdict guard* (`packing_assistant/tools/verdict_guard.py`, Chinese and English): verdicts the product must
     never give (every clause covered, complies with the tender / ITT / specification, approved, ready to submit,
     can book / ship / bid, passes review, no risk of disqualification ...) are rewritten once, then struck and named;
     negated, asked, conditional, quoted and reported sentences are left alone;
   - *claim check* (`packing_assistant/tools/claim_check.py`): when the turn wrote a link record, a coverage claim
     the record does not support ("all seven clauses are covered" with one covered statement, "S4 is covered" when S4
     waits for a person) is replaced by what the record says.
3. **Only a person's typed sentence approves high-risk work, for one turn.** The 19 high-risk posts (structure,
   geotechnical, fire protection, construction method, safety briefs and others) write nothing until a person types
   the confirmation sentence in that turn. The gateway and `civil serve` accept only `confirm_text` equal to the
   sentence; a boolean such as `confirm_ok` or `p0_confirmed` is refused (HTTP 422 on the gateway). The approval is
   not remembered for the next turn. The same sentence inside a tender or panel-list file, or in a model reply, does
   not approve; pasted into the message as a line or sentence of its own, it still does (see Known open items).
   The sentence is asked for only when a high-risk post is selected or loaded. In the Rust workbench's default
   automatic post selection the model decides whether to load a post; if it loads none, a copy can be written
   without the sentence. Writes are always new copies, and a copy is written only after the source was read and the
   identical change was previewed.
4. **Token-gated server.** `packing_assistant/access_guard.py` sits in front of the gateway and the workbench,
   WebSockets included. With `CIVIL_TOKEN` set, every request needs the token (loopback too; compared with
   `hmac.compare_digest`). With no token, only a genuinely local request passes, and `demo/serve.py`, a `uvicorn --host`
   start and the container refuse to bind a non-loopback address. `CIVIL_ALLOW_OPEN_LAN=1` is the one explicit opt-out.
5. **Sandboxed reads and policy on tool calls.** Policy (`packing_assistant/runtime/policy.py`) runs before each
   registered tool call: per-post exclusive tools, secret paths such as `.env` refused, and every tool's `file_path`
   checked against the sandbox roots. Pack-ship over MCP and the gateway's tool route go through the same admission
   step. The link reads its two inputs only through the job folder's read guard. An opt-in OS sandbox confines a desktop
   or CLI turn in a worker; on our Windows test machine it enforces writes and process spawn but not network, and the
   Linux backend (Landlock and seccomp) was not exercised there. It is desktop hardening, not a server control.
6. **Secrets stay out of the repository.** `.env` files are git-ignored; keys live in the host environment or the
   runtime settings, which the settings API returns masked.

## What is tested

All of these run in `npm run check` and in CI on every pull request:

| Check | What it pins |
|---|---|
| `access-guard` | the token gate on both web apps, the refusal to bind a non-loopback address without a token |
| `human-approval` | MCP never offers or accepts an approval flag; `civil serve` takes only the typed sentence; approval does not carry over to a later turn |
| `http-confirmation` | the gateway and workbench HTTP routes approve only on `confirm_text` equal to the sentence |
| `pack-ship-read-sandbox` | pack-ship reads stay inside the sandbox roots over MCP and the gateway |
| `injection-plants` | instructions planted in SYNTHETIC tender (Markdown and Word) and panel-list files do not change statuses, approve anything or become statements, in the steps-mode link, the steps-mode turn and the gateway; a scripted fake model that obeys the plant is corrected and struck by the guards |
| `verdict-bench` | floors for the verdict guard on its Chinese dev set, its second Chinese held-out set and the English dev set |
| `model-loop` | the model loop with a scripted model: routing, number and verdict guards, approvals, read limits |
| `tender-packing-link` | statuses are computed from the plan; lashing, stillages and sequencing are never covered |
| `number-provenance-bench`, `civil-review` | the number guard and the model-free review of a document |
| `sandbox`, `os-sandbox`, `middleware` | write / secret / spawn policy, the OS worker (kernel tests skip where no backend exists), policy allow / deny / degrade / circuit |

Measured numbers for the guards are in `test/benchmarks/verdicts/README.md`. The injection test is worded
deliberately: **planted text does not change statuses; a live model was not tested.** The model-mode assertions use a
scripted model that always obeys the plant, so they show that the guards catch an obedient reply, not how often a real
model would obey. The plants are our own synthetic development cases, not an independent benchmark.

## Known open items

The technical document (`docs/submission/nus-iss-technical.md`, §4.4) describes the older `a161251`
snapshot. Remaining boundaries and their current scope are listed here:

- **Old Rust binaries.** Earlier trial builds accepted an approval flag. Current HTTP routes require the
  confirmation text in the current turn. Current Rust MCP does not advertise or accept approval arguments,
  denies high-risk tool calls, validates session IDs and enforces its launch scope. Use the interactive
  workbench for approval; do not substitute an old executable for this source.
- **Pack-ship circuit on the steps path.** Three needs-human packing lists in a row open a process-wide circuit for
  `pack-ship__plan` until a restart.
- **Run routes.** `/api/runs/compare` and the `{run_id}` routes join request values onto the runs folder; they are
  behind the token.
- **Token-less proxying.** Without a token, a proxy that rewrites `Host` and sends no forwarding header can look
  local. A proxied deployment must set `CIVIL_TOKEN`.
- **Sandbox worker.** The opt-in OS worker inherits the host environment, keys included, and its network is not
  confined on Windows.
- **No PII masking.** Nothing is masked before a call to a cloud model; the safeguard today is no key, or a local
  model.
- **Legacy cost, audit, identity.** The original Python engine's audit log is in memory and its demo cost fuse
  is not a shared billing system. The new Rust Agent records actor, events and provider usage in SQLite;
  this does not retroactively add identity or complete billing to separately launched legacy entry points.
- **Gateway packing pause.** `enable_auto_confirm` defaults to true for API callers. This is the packing-plan pause,
  not the licensed-person gate.
- **Prompt injection.** There is no injection detector. The planted-instruction test above covers the steps mode and
  a scripted model; a live model reading a planted instruction could still steer which menu tool runs on which listed
  file, and what a chat reply says before the guards. The URL fetch resolves DNS twice.
- **Chat.** In the workbench, question-only turns call the model whenever a key is set, whatever `agent_mode` says.
- **Sign-off inside pasted text.** A sign-off sentence on its own line inside pasted text is still taken as approval:
  the Rust workbench accepts it when it forms a whole line or sentence of the message, and the Python
  `message_confirmation` accepts it standing alone outside quotation marks. Only the dedicated confirmation field
  should approve; until then, a person should not paste unreviewed text into a high-risk turn.
- **Automatic post selection.** The typed confirmation applies only when a high-risk post is selected or loaded. In
  the Rust workbench's default automatic mode the model decides whether to load a post, so a model that loads none
  can write a copy of a high-risk document without the sentence. The copy is new and the original is unchanged.

## Unified named instances (2026-09-27)

The unified launcher now supports one named user and one exact private workspace per process pair. The Rust outer
authentication layer covers Agent, legacy, domain and artifact routes. Accounts use a hashed login token and an
HttpOnly SameSite=Strict session cookie; cookies expire on restart. Per-instance state and workspace ownership
records reject reuse by another user or another state directory. This is separate-instance isolation, not a
shared-process multi-tenant service. A public reverse proxy requires the configured HTTPS public origin.
This is application isolation, not an OS security boundary against someone who can edit another instance's
files under the same Windows/Linux account. Use separate OS accounts or hosts when that boundary is required.

The internal Python service has a separate random Bearer token, a route allowlist, no provider keys and no model
loop. Named mode disables legacy local-path import, URL import and studio editing. Rust Agent events, actor IDs,
usage and interrupted/cancelled states are persisted. Confirmation is specific to the current turn and is
not inherited from old turns, restored sessions or a boolean field; it is asked for only when a high-risk post is
selected or loaded, and pasted text can still carry it (both listed under Known open items). The legacy limitations
above still apply to separately launched Python/gateway entry points unless their own implementation says otherwise;
they must not be used as an unprotected alternate entry into a named instance.

Evidence: `product_identity`, `runtime_core`, `product_document_gates`, `scripts/test_domain_service.py`,
`scripts/test_link_confirmation_regressions.py`, and the optional compiled-process
`scripts/test_unified_runtime_http.py`. Tests use local scripted providers, not a live-model security assessment.
See [the handoff guide](docs/civil-buddy/release-handoff.md) (in Chinese) for deployment and acceptance boundaries.

## Reporting a problem

Please open an issue at <https://github.com/LUOaini1213/civil-buddy-sme/issues> and put "security" in the title. Do
not post exploit details in a public issue: if the problem could be exploited, describe what is affected and how to
reproduce it at a high level, and leave out working exploit code, tokens or keys; a maintainer will follow up in the
issue. Never paste a real API key, token or customer file into an issue.
