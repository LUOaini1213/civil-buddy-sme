# civil-buddy — the tender response and the packing plan, linked

[![ci](https://github.com/LUOaini1213/civil-buddy-sme/actions/workflows/ci.yml/badge.svg)](https://github.com/LUOaini1213/civil-buddy-sme/actions/workflows/ci.yml) [![release](https://img.shields.io/github/v/release/LUOaini1213/civil-buddy-sme)](https://github.com/LUOaini1213/civil-buddy-sme/releases) [![license](https://img.shields.io/badge/license-MIT-blue)](LICENSE)

中文说明：[README.zh.md](README.zh.md)

[Public static showcase](https://huggingface.co/spaces/Niki68868/civil-buddy-sme)
— workflow and recorded synthetic results, without an online Python backend.
[Showcase source and publishing instructions](deploy/huggingface-space/README.md).

**Who it is for.** Singapore façade and curtain-wall SMEs that supply and install façade packages and
must answer English tenders *and* ship the panels to site in containers.

**The problem, in one sentence.** Tender response and outbound packing run as two disconnected
exercises — the tender in Word or PDF, the panel list in Excel, bookings in email — so a statement
about packing in the bid is not tied to a loading plan and a container count is not tied to the clause
it should satisfy; civil-buddy links them clause by clause.

**What it does.** One request reads the tender's logistics clauses with their clause numbers, plans
the real panel list in the container type the tender names, and writes each logistics statement of the
English response from a plan figure, citing its clause. A link record ties every statement to its
clause, to the plan figure and to the SHA-256 of the tender, the panel list and the plan. When the
panel list is revised, the same request re-plans and names the statements a person must re-confirm
and the earlier Word copies that are now stale. Code computes the numbers; qualifications and price
stay with people (`[TO FILL]`); nothing is booked or submitted.

> This repository is the NUS-ISS "Show Me Your Agents" 2026 SME-track entry of Team Mintang
> (PJ2U63AF). The pilot partner is not named here. **Every demo input is SYNTHETIC**
> ([examples/facade-demo](examples/facade-demo/README.md)); no contractor's document or number is in
> this repository.

## Try it: one command, offline, no key

```bash
pip install -r requirements.txt && python scripts/demo_facade.py
```

Python 3.11. No model key, no network, no account. It writes drafts into a throw-away job folder
(a new temporary folder, or `--job <new folder>`) and ends with `PASS demo_facade`. Flow 1 is the linked run; flows 2–4
run tender review, packing and site paperwork on their own.

**Clause → plan → statement.** Abridged output of flow 1 (`main` at `cab9249`):

```text
== 1 Tender <-> packing, linked: the ITT's logistics clauses, the plan under them, the English statements
  $ civil exec 'Link the tender facade_itt_doc.md to the packing list facade_panels.xlsx and write the logistics response'
  reply: Linked facade_itt_doc.md and facade_panels.xlsx: 5 logistics clauses, 7 statements (1 covered by the plan, 2 partial, 0 gap, 4 for a person). ...
    container type: Container type 40HQ taken from Clause 4.8.
    inputs: tender facade_itt_doc.md sha256 855de144f92e · panel_list facade_panels.xlsx sha256 3d62fd55274c · plan pack-plan.json sha256 8a0bec8ece8a
    S1 Clause 4.8 · container_type · covered · clause names 40HQ; plan made in 40HQ
    S2 Clause 4.8 · containers_used · partial · 6 x 40HQ (N0 6) for 24 pieces / 10,800 kg net from facade_panels.xlsx
    S3 Clause 4.9 · gross_mass · partial · heaviest container 6,472.8 kg gross (2,582.8 cargo + 3,890.0 tare) vs limit 20,000 kg, margin 13,527.2
    S4 Clause 4.10 · securing · human_required · not modelled -> competent person (lashing)
    S5 Clause 4.7 · handling · human_required · not modelled -> logistics
    S6 Clause 4.7 · crate_structure · human_required · 24 of 24 crates pending detailed design (待详设)
    S7 Clause 4.11 · delivery_sequence · human_required · not modelled -> project manager
```

It also writes an English bid-book draft (`bidbook.en.md` / `.docx`) whose logistics chapter states
each of these with its clause and figure, and the link record `tender-packing-link.json`
(`confirmed_by_person = false`, `submit_blocked = true`). S2 and S3 read *partial* because the
tender asks for A-frame stillages, which the planner does not model; the statement says so with a
`[TO CONFIRM by logistics: ...]` placeholder.

**What goes stale when the panel list changes.** The demo then feeds revision B of the panel list
(level L9 added, L8 panels heavier; 30 panels, 13,920 kg) with the same request, asked in Chinese:

```text
    S2 Clause 4.8 · containers_used · partial · 8 x 40HQ (N0 8) for 30 pieces / 13,920 kg net from facade_panels_rev_b.xlsx
    since the previous run: panel list (facade_panels.xlsx -> facade_panels_rev_b.xlsx) changed, plan changed: containers used 6 -> 8;
      pieces 24 -> 30; cargo net kg 10,800 -> 13,920; max cargo kg 2,582.8 -> 2,862.8; max gross kg 6,472.8 -> 6,752.8;
      statements S2, S3, S6, S7 need re-confirmation; earlier Word copies bidbook.en.docx, tender-packing-link.docx
      still hold the previous statements - do not send them
      re-derived with the same figures: S1, S4, S5
  sign-off: nothing here is booked or submitted (submit_blocked stays true). A person confirms the loading plan
  before booking, and re-confirms every statement the re-run names.
```

(Lines wrapped here for width.) The re-run never overwrites the earlier Word files; it writes
`bidbook.en-2.docx` and `tender-packing-link-2.docx` beside them.

### What the demo itself says is not done

These are printed by `scripts/demo_facade.py`, not added for this page:

- **Securing, handling and delivery sequence are not modelled.** Lashing to the CTU Code (S4), A-frame
  stillages / upright transport / no stacking (S5) and the delivery sequence (S7) go to a person.
  "not modelled: A-frame stillages (the ITT asks for them). That needs the contractor's stillage size,
  tare and capacity."
- **Crate structure is not designed.** 24 of 24 crates are *pending detailed design* (待详设); the
  engine does not invent a pass.
- **Handling notes do not change the plan.** Glass / upright / no-stack notes have no effect on the
  plan, in English or in Chinese.
- **The tender parse lists 0 of the ITT's 12 façade specification clauses** (PMU and VMU mock-ups, heat
  soak, site water test, PE-endorsed calculations, warranty, A-frame delivery, the four logistics
  clauses, insurance), and shows no liquidated-damages or retention row. The four logistics clauses are
  read by the link instead.
- **Nothing is booked or submitted.** `submit_blocked` stays `true`; a person confirms the loading plan
  and re-confirms every statement a re-run names.

The gross mass is the engine's per-container cargo plus an approximate knowledge-base tare (40HQ
3,890 kg); dunnage, lashing and stillage mass are excluded, and the signed VGM governs. More limits
(there is no dedicated linked-run MCP tool; the bid-book body has Chinese rows in chapter 3 and
Annex B; no live-model run) are listed in [examples/facade-demo/README.md](examples/facade-demo/README.md)
and in §3.4 of the technical document.

The current development branch also exposes the token-protected `POST /api/tender/link`
upload route through the gateway's `/demo` page. This route was added after the submitted
`v0.7.0` baseline; the frozen technical document describes that earlier version.

## Safety model

Enforced in code, not in prompts; shipped as the security baseline (pull request #61, merge commit
`d3ada11`):

1. **Code computes the numbers.** Container counts, masses, coordinates and clause figures come from
   deterministic tools. A model (optional, any OpenAI-compatible endpoint) may route and phrase, but its
   text never reaches a deliverable.
2. **The model never approves.** A program cannot approve by sending a flag: the Python MCP server
   neither offers nor accepts one, and the web apps refuse a `true` boolean. In civil-buddy's own model
   loop the model's tools have no confirm field, and a copied sentence is replaced.
3. **Only a person's typed sentence approves high-risk work.** The 19 high-risk posts in
   `workbench/seed.json` (structure, geotechnics, safety briefing, …) write nothing until a person types
   `我明白，将由持证人员签认` ("I understand; a licensed person will sign"), and it covers that turn only.
   The bid posts (bid-parse, bid-tech, bid-compliance) are **low risk** in `seed.json`: they draft without
   the sentence, but every draft carries `submit_blocked = true` and the qualification / rejection rows
   wait for a person.
4. **The Python server is token-gated.** With `CIVIL_TOKEN` set, every API route and WebSocket needs the token,
   from loopback too (so a reverse proxy cannot bypass it). Binding a non-loopback address without a
   token refuses to start. `/api/health` stays public on purpose for health checks.
5. **Policy as code and an offline gate.** Every registered tool call on the default path, over MCP and
   through the gateway's tool route passes a policy function that refuses with a stated reason. `npm run check` runs offline in CI on every push; `python scripts/check_project.py --list` lists the current checks. The
   badge above is this repository's CI.

## Where it runs (AWS, stated honestly)

- **Target:** one AWS Lightsail Linux instance per company, used by employees in a browser behind TLS.
  **The Lightsail instance is not running yet**; no cloud host has run the image.
- **Docker image:** CI's `docker-smoke` job (pull request #64, merge commit `16316df`) builds the
  gateway image on every push and checks that it refuses to start without `CIVIL_TOKEN` (exit 3),
  answers 401 without the token and 200 with it, parses the synthetic ITT through the API, and keeps a
  session in its SQLite database across a container re-create and across `docker compose down` / `up`.
  The image starts the Python gateway and its browser demo; the Rust workbench uses the separate unified
  launcher. Container checks are local/CI evidence, not a Lightsail deployment result.
- **Amazon Bedrock:** configurable through the OpenAI-compatible Chat Completions setting, but **never
  run** from this repository. No result in this repository comes from a live model.
- Operator guides: [minimal deployment](docs/deploy-minimal.md) and [Lightsail runbook](docs/deploy-aws-lightsail.md).
  The gateway also provides the token-protected `/demo` page for tender and panel-list uploads.
  The [submission audit](docs/civil-buddy/submission-sync-20260927.md) lists recovered local features and known limits.

```bash
git clone https://github.com/LUOaini1213/civil-buddy-sme && cd civil-buddy-sme
export CIVIL_TOKEN=$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')
docker compose up -d --build
# open http://localhost:8000/?token=$CIVIL_TOKEN once (sets an HttpOnly cookie), or
curl -H "Authorization: Bearer $CIVIL_TOKEN" http://127.0.0.1:8000/api/tools
```

## Numbers we stand behind

Each figure comes from a command in this repository, run offline with no key.

| What | Figure | Command |
|---|---|---|
| Linked run (synthetic façade job) | 5 logistics clauses; 7 statements: 1 covered, 2 partial, 0 gap, 4 for a person. Rev B: 6 → 8 containers, 4 statements to re-confirm, 2 stale Word copies named | `python scripts/demo_facade.py` |
| Link behaviour pinned by tests | Includes tenders that must *not* read as covered (a plan that does not fit, a 6,000 kg limit, size-only or negated container clauses, open-top / flat-rack types); current counts are printed by the command | `python scripts/test_tender_packing_link.py` |
| English request routing, blind held-out set | `heldout_en2`: 28 sentences, accuracy 0.786, **0 false runs** (all 6 misses fall back to chat). The two other English sets are not blind and are not quoted | `python scripts/test_english_intents.py --score` |
| Chinese request routing, held-out | 1.000 accuracy, 0 false runs | `python scripts/eval_task_intent.py --check` |
| Packing fan-out evaluation (128 runs) | All 128 runs completed, but only **71 of 128** produced a plan that fits; 57 returned `can_fit=False` to a person | `python scripts/render_eval_table.py` |
| Tools | 82 registered tools (71 write), each with JSON Schema input and output contracts; MCP scopes of 11 / 8 / 9 / 13 tools | `scripts/check_project.py` checks `tool-contracts` |
| Release gate | Current offline checks, with separate full HTTP and Rust checks | `python scripts/check_project.py --list`; `npm run check`; `npm run check:full` |

The fan-out line that CI checks against the archive is kept in its original wording (CI runs
`python scripts/render_eval_table.py --check README.md`):

> **128** 次（16 并发 × 8 轮），2026-09-02 复跑 **128/128 PASS**。PASS 只表示流水线跑完并返回了柜数与 `can_fit`，不表示都装得下：其中 `can_fit=True` **71/128**，其余 57 次 `can_fit=False` 交回人改方案

In English: 128 runs (16 lanes × 8 rounds), re-run on 2026-09-02, 128/128 PASS, where PASS only means
the pipeline finished and returned a container count and `can_fit`; `can_fit=True` in 71 of 128.
Archive: [docs/eval/fanout16x8-2026-09-02](docs/eval/fanout16x8-2026-09-02/README.md).

What we do **not** claim: results on real tender PDFs (archived first-run scores only; the files are
outside the repository), any customer shipment, earlier held-out scores on sets now seen, a
"L2 66/66" depth figure (no command produces it), and any live-model or Bedrock result.

## The rest of the product (briefly)

The link is built on a local-first agent workbench:

- **66 job "posts" in 16 categories** (tender review, packing and shipping, site documents, design
  disciplines, HR, admin, IT, finance), each an SOP (`.agents/skills/<id>/SKILL.md`) that turns the
  user's words and the files in a job folder into an internal draft in Markdown, Word and Excel.
  Missing values stay `UNSPECIFIED`; most posts are drafting aids, not engines.
- **Entry points:** `python -m packing_assistant.civil app` (browser workbench on 127.0.0.1:8765),
  `civil exec "..."` / the TUI in a job folder, `civil desktop`, and the packing gateway
  (`uvicorn gateway.app:app --host 127.0.0.1 --port 8000`).
- **MCP:** `python -m packing_assistant.civil mcp --pack construction` (stdio JSON-RPC, for VS Code,
  Cursor or other agent hosts; configs in [ide/](ide/README.md)). High-risk writes over MCP return
  `approval_required` and write nothing.
- **Unified Rust workbench** (`workbench/`): one browser entry for Agent tasks, the specialist
  workflow, document copies, CAD, schedules and packing-list records. The host keeps actor and tool
  events in SQLite and calls a fixed, authenticated Python service for deterministic tools. Current
  HTTP requests require the typed confirmation for high-risk operations; MCP arguments cannot grant
  that approval. Historical trial binaries are not evidence for the current source.

### Run the unified workbench from source

```powershell
python -m pip install -r requirements.txt -r requirements-documents.txt
cargo build --locked --release --manifest-path workbench/Cargo.toml
python scripts/start_unified_workbench.py --binary workbench/target/release/civil-workbench.exe --open
```

This local preview starts both processes and opens the Agent page. Install optional CAD, engineering
and planning dependencies from their respective requirements files when using those pages. The
ordinary checks use scripted local models; no paid model or cloud deployment is implied.

For a named user, provide `--user-id`, `--workspace` and `--token-file` together. Each instance owns
one physical workspace and private state; this is **not a shared-process multi-tenant service**.
The [handoff guide](docs/civil-buddy/release-handoff.md) covers login, restart, project packages and
remaining real-business acceptance. The [SME integration record](docs/civil-buddy/sme-integration.md)
distinguishes this source from the older competition repository and archived evaluation figures.

## Documents

- [docs/submission/nus-iss-technical.md](docs/submission/nus-iss-technical.md) — the technical
  document: architecture, the judging-criteria map, evaluation, AWS status, limitations (every file:line
  and number re-checked at `a161251`).
- [docs/submission/nus-iss-entry.md](docs/submission/nus-iss-entry.md) — the one-page entry sheet.
- [examples/facade-demo/README.md](examples/facade-demo/README.md) — the synthetic fixtures and the
  linked run, flow by flow.
- [docs/deploy-minimal.md](docs/deploy-minimal.md) — operator guide (Chinese).

## Folder map

```text
packing_assistant/          agent runtime, tools, policy, packing engine
  tender_packing_link.py    the tender <-> packing link (clauses, plan, statements, link record)
  runtime/                  tool engine, tool contracts, policy, model loop, sandbox
  bidbook/                  English bid-book (sg_facade)
gateway/                    FastAPI packing gateway (the Docker image serves this)
demo/                       Python browser workbench, MCP server, post knowledge bases (demo/kb)
examples/facade-demo/       SYNTHETIC façade ITT, panel lists (rev A, rev B), site inputs
scripts/                    demos, tests, evaluations; scripts/check_project.py is the gate
test/benchmarks/            benchmark sets behind the evaluation figures
docs/                       technical document, deployment guide, design notes (many in Chinese)
workbench/                  unified Rust task host and compatibility routes
.github/workflows/ci.yml    CI: smoke (the gate), rust, packing-eval-slice, docker-smoke
```

## Settings

The table below describes the original Python entry points. The unified launcher's options and
separate host/service configuration are documented in the handoff guide. Copy `.env.example` /
`demo/.env.example`; never commit keys.

| Variable | Default | Effect |
|---|---|---|
| `CIVIL_TOKEN` | empty | One access token for the workbench and the gateway (`Authorization: Bearer`, or the cookie set by `?token=`). Set: required on every API route, loopback included. Empty: only genuinely local requests are served. `docker compose` refuses to start without it |
| `CIVIL_ALLOW_OPEN_LAN` | unset | `1` lets a token-less app accept requests from other machines. **Never set it on a server** |
| `CIVIL_HOST` / `CIVIL_PORT` | `127.0.0.1` / `8765` | Workbench bind address and port; a non-loopback host without `CIVIL_TOKEN` refuses to start |
| `CIVIL_API_KEY` / `CIVIL_API_BASE` / `CIVIL_MODEL` | unset | Optional model endpoint (any OpenAI-compatible Chat Completions API); no key, no model. `OPENAI_API_KEY` / `OPENAI_BASE_URL` / `LLM_MODEL` are also read |
| `CIVIL_AGENT_MODE` | `steps` | `model` or `auto` enables the model loop |
| `CIVIL_JOB_ROOT` | unset | Job-folder root the tools may read and write |
| `CB_DB_PATH` | `data/civilbuddy.db` | SQLite database path (the Docker image sets `/app/output/db/civilbuddy.db`, inside the volume) |
| `PACKING_AGENT_URL` / `PACKING_AGENT_ROOT` | unset / repo root | Where the workbench finds the packing engine |
| `PACKING_TMS_MODE` | unset (stub) | Only the server environment can select a live TMS; nothing is booked by default |

## Commit authorship

As of `cab9249`, 98 of the 447 commits on `main` (22%) are authored as `Packing Assistant`
(`git log --format=%an | sort | uniq -c`): changes drafted by an agent and committed under their own
name, then reviewed by a person before landing on `main`. It is part of the human-in-the-loop process,
not a second author.

## Licence

MIT — see [LICENSE](LICENSE). Outputs are internal working drafts, never signed or statutory
documents.
