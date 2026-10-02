# civil-buddy — the tender response and the packing plan, linked

[![ci](https://github.com/LUOaini1213/civil-buddy-sme/actions/workflows/ci.yml/badge.svg)](https://github.com/LUOaini1213/civil-buddy-sme/actions/workflows/ci.yml) [![release](https://img.shields.io/github/v/release/LUOaini1213/civil-buddy-sme)](https://github.com/LUOaini1213/civil-buddy-sme/releases) [![license](https://img.shields.io/badge/license-MIT-blue)](LICENSE)

中文说明：[README.zh.md](README.zh.md)

**Product language:** switch between English and 中文 in the workbench header.
See [bilingual product support](docs/civil-buddy/bilingual-workbench.md) for scope and startup.

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

**Submitted version.** The version submitted for NUS-ISS shortlisting is release
[v0.7.0](https://github.com/LUOaini1213/civil-buddy-sme/tree/v0.7.0) (commit `0c0e803`); the figures and
file:line references in the submitted PDFs were measured there. Since then `main` has added a unified Rust
workbench (PR #1) and a `/demo` page and a Lightsail guide (PR #2), and on 2026-09-28 it merged the later
review rounds of the development line (how the link reads mass limits and clauses, English verdict and record
guards, panel lists with title rows and packaging-equipment rows, a hardened `/demo` upload, a stricter sign-off
rule); none of these are in the PDFs. The current preview additionally preserves unsupported transport requirements
and stops the original façade fixture before planning; its old 6/8-container figures are historical only. Other post-submission differences: the
Rust workbench refuses a bare `confirm_ok`, the Python surfaces also accept one English sign-off sentence but only
typed on its own, the English bid-book has no Chinese rows, and the tender parse keeps the liquidated-damages and
retention rows for a person.

## Try it: one command, offline, no key

```bash
pip install -r requirements.txt && python scripts/demo_facade.py
```

Python 3.11. No model key, no network, no account. It writes drafts into a throw-away job folder
(a new temporary folder, or `--job <new folder>`) and ends with `PASS demo_facade`. Flow 1 is the linked run; flows 2–4
run tender review, packing and site paperwork on their own.

**Clause → source requirement → statement.** Current preview output of flow 1
(reproduced offline on 2026-10-03 at `4fc84a5`):

```text
Linked facade_itt_doc.md and facade_panels.xlsx: 5 logistics clauses, 7 statements
(0 covered by the plan, 0 partial, 0 gap, 7 for a person).
container type: 40HQ taken from Clause 4.8.
no loading plan: unsupported_transport_requirements
plan name: null; plan sha256: null
S1 Clause 4.8  container_type    human_required
S2 Clause 4.8  containers_used   human_required
S3 Clause 4.9  gross_mass        human_required
S4 Clause 4.10 securing          human_required
S5 Clause 4.7  handling          human_required
S6 Clause 4.7  crate_structure   human_required
S7 Clause 4.11 delivery_sequence human_required
```

The original panel list requires upright transport, A-frame stillages and no
stacking. Automatic boxing cannot enforce those requirements, so the demo
preserves their source text and asks for package data instead of calculating an
unsupported container count. The English bid-book and `tender-packing-link.json`
retain source hashes, clause references and unresolved statements, with
`confirmed_by_person = false` and `submit_blocked = true`.

**When revision B arrives**, the source name and hash change. Transport data is
still missing, so no container-count or mass change is invented. Earlier Word
copies are preserved; the re-run writes new copies such as `bidbook.en-2.docx` and
`tender-packing-link-2.docx`. The packing-only flow likewise writes a row-by-row
human supplement checklist in English or Chinese and gives no loading plan.

**To try a numerical packing example**, use the separate
[bounded replan fixture](examples/packing-replan/README.md). It explicitly declares
fixed orientation and floor-only placement, and reports layout feasibility apart
from unresolved box-structure checks. Do not remove requirements from a real
shipment to make it resemble this synthetic fixture.

### What remains for a person

- Supply the loaded package outer dimensions, package count and gross mass;
  A-frame/stillage loads also require net mass, tare and declared capacity.
- Check securing, lifting, delivery sequence, stability, packaging structure and
  the signed VGM. A geometric fit is not shipping release.
- Review façade specification clauses and unclassified commercial rows. The
  ordinary tender parser does not provide complete façade-specification coverage;
  the linked run separately reads the five logistics clauses.
- Review the site-document drafts. The high-risk safety-brief flow deliberately
  writes nothing until the operator supplies the current-turn confirmation.

The [v0.7.0 README](https://github.com/LUOaini1213/civil-buddy-sme/blob/v0.7.0/README.md)
and submitted documents retain the earlier 6/8-container results. They describe
that historical version, whose handling notes did not alter the plan; they are
not the expected result of today's command. More fixture details and limitations
are in [examples/facade-demo/README.md](examples/facade-demo/README.md).

## Safety model

Enforced in code, not in prompts; shipped as the security baseline (pull request #61, merge commit
`d3ada11`):

1. **Code computes the numbers.** Container counts, masses, coordinates and clause figures come from
   deterministic tools. The legacy Python pipeline excludes model prose from deliverables. The unified
   Rust workbench can publish model-drafted document copies only after source, preview, permission and
   verification gates; see [the runtime boundaries](docs/civil-buddy/unified-workbench.md).
2. **The model never approves.** A program cannot approve by sending a flag: the Python MCP server
   neither offers nor accepts one, and the web apps refuse a `true` boolean. In civil-buddy's own model
   loop the model's tools have no confirm field, and a copied sentence is replaced.
3. **Only a person's typed sentence approves high-risk work.** The 19 high-risk posts in
   `workbench/seed.json` (structure, geotechnics, safety briefing, …) write nothing until a person types
   `我明白，将由持证人员签认` or its one English equivalent, "I understand; a licensed person will sign
   this off." (the Rust workbench accepts only the Chinese sentence), and it covers that turn only. It is
   typed on its own, in the confirmation box, at the terminal's `approve>` prompt or in the desktop dialog:
   on the Python surfaces the sentence written into a task, or quoted from a tender, approves nothing. The
   legacy Python flow asks for the sentence when a high-risk post is selected or loaded. The unified Rust
   Agent requires a person to select a post before saving document copies; automatic mode can read and
   preview. It accepts acknowledgement only from the dedicated current-turn field or the entire trimmed
   message, never a line inside pasted material. See [SECURITY.md](SECURITY.md).
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
  **The Lightsail instance is not running yet** (we are waiting for the team's AWS account); no cloud host has
  run the image. Meanwhile a temporary live instance of the same Docker image, behind its access token, runs on
  our own machine behind a tunnel; its address is given to the organisers, not published here.
- **Docker image:** CI's `docker-smoke` job (pull request #64, merge commit `16316df`) builds the
  gateway image on every push and checks that it refuses to start without `CIVIL_TOKEN` (exit 3),
  answers 401 without the token and 200 with it, parses the synthetic ITT through the API, and keeps a
  session in its SQLite database across a container re-create and across `docker compose down` / `up`.
  The image starts the Python gateway and its browser demo; the Rust workbench uses the separate unified
  launcher. Container checks are local/CI evidence, not a Lightsail deployment result.
- **Amazon Bedrock:** configurable through the OpenAI-compatible Chat Completions setting, but **never
  run** from this repository. No result in this repository comes from a live model.
- Operator guides: [minimal deployment](docs/deploy-minimal.md) and [Lightsail runbook](docs/deploy-aws-lightsail.md).
  The gateway also provides the token-protected `/demo` page: the linked run on the synthetic façade job in one
  click, or on a tender and a panel list you upload (size, row and cell limits; at most 2 runs at once).
  What each post-submission preview adds, and its known limits, is listed in the notes of the v0.8.0 preview
  [releases](https://github.com/LUOaini1213/civil-buddy-sme/releases).

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
The [handoff guide](docs/civil-buddy/release-handoff.md) (in Chinese) covers login, restart, project packages and
remaining real-business acceptance. The [SME integration record](docs/civil-buddy/sme-integration.md)
distinguishes this source from the older competition repository and archived evaluation figures.

## Documents

- [docs/submission/nus-iss-technical.md](docs/submission/nus-iss-technical.md) — the technical
  document as submitted at v0.7.0: architecture, the judging-criteria map, evaluation, AWS status,
  limitations (every file:line and number re-checked at `a161251`).
- [docs/submission/nus-iss-entry.md](docs/submission/nus-iss-entry.md) — the one-page entry sheet as
  submitted at v0.7.0.
- [examples/facade-demo/README.md](examples/facade-demo/README.md) — the synthetic fixtures and the
  linked run, flow by flow.
- [docs/deploy-minimal.md](docs/deploy-minimal.md) — operator guide.

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
separate host/service configuration are documented in the handoff guide (in Chinese). Copy `.env.example` /
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
not a second author. The commits after `cab9249` (`0c0e803`, the v0.7.0 README commit; pull
requests #1–#2 with their merges; later documentation commits; the 2026-09-28 merge of the development line)
are all authored under the maintainer's name; pull requests #1–#2 were drafted by a Codex session, partly from
earlier agent-drafted development commits, and the development line's commits keep their own authors.

## Licence

MIT — see [LICENSE](LICENSE). Outputs are internal working drafts, never signed or statutory
documents.
