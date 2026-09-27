---
title: "civil-buddy: Technical Document"
subtitle: "NUS-ISS “Show Me Your Agents” Hackathon 2026"
author: "Team Mintang · Team Code PJ2U63AF"
date: "Code: main at a161251, 2026-09-26"
---

Repository: <https://github.com/LUOaini1213/civil-buddy-sme> (MIT licence; commits are cited by SHA) · Code: `main` at `a161251`, 2026-09-26

Every result we claim was produced offline, with no model key, on a named checkout of `main`. The linked tender and packing run (§3.4), the release gate (§5.3), the Docker checks (§6) and every `file:line` reference are those of `a161251`, which is `main` after pull request #65. The benchmark rows of §5.2 and the measured flows of §3.1–§3.3 were run on the earlier `d3ada11` and are labelled so. Release-gate results are GitHub CI's. The few older figures we mention are labelled as archived. Appendix A names the command or file behind each number and strong claim. Each part of the architecture carries one status tag:

| Tag | Meaning |
|---|---|
| `LIVE` | On `main`, on by default. |
| `OPT-IN` | On `main`, off by default. |
| `BEFORE DEMO` | Planned by demo day, 2026-10-10. |
| `ROADMAP` | Planned after the demo. |
| `NOT NEEDED` | Needed only by a multi-tenant platform, so left out on purpose. |

## 1. Summary

**The partner's problem.** Our pilot partner, <!--SME:SME_NAME-->a Singapore curtain-wall contractor<!--/SME-->, supplies and installs façade packages. It stated its problem in writing to the organisers. A typical job needs two things at once: an English tender response, and the outbound packing and shipping of the panels to site, often in 40HQ containers, in crates or on steel frames, under weight and lashing limits. The two live in separate files: the tender in Word or PDF, the packing list in Excel, the bookings in email. A statement in the bid about packing is often not tied to any loading plan, and a container count is not tied to the tender clause it should satisfy. First drafts take days, and the same scramble repeats on the next job. In the partner's words: "tender response and outbound packing run as two disconnected exercises and need to stay linked." Qualifications and price remain human work.

**What civil-buddy does about it.** One request links the two. civil-buddy reads the tender's logistics clauses with their clause numbers, plans the real panel list in the container type the tender names, and writes each logistics statement of the English response from a plan figure, citing its clause. A link record ties every statement to its clause, to the plan figure and to the SHA-256 of the tender, the panel list and the plan. When the panel list is revised, the same request re-runs the plan and names the statements a person must re-confirm and the earlier Word copies that are now stale. A statement the plan cannot evidence is never marked covered: lashing, handling and delivery sequence go to a person. Qualifications and price stay `[TO FILL]`, and nothing is booked or submitted (§3.4).

**The rest of the workbench.** The link is built on a local-first agent workbench with 66 job "posts" in 16 categories, including tender review, packing and shipping, site documents, design disciplines, HR, admin, IT and finance. Staff use it through a browser workbench, a CLI, a desktop app, an MCP server for VS Code and Cursor, or a packing gateway. A post turns the user's words and the files in a job folder into an internal draft in Markdown, Word and Excel. Missing values stay `UNSPECIFIED` or 待填 ("to be filled"); they are never guessed. The expensive mistakes are numbers and verdicts: a wrong container count, a missed tender requirement, or a "compliant" that nobody checked. A general chat assistant is fluent at exactly those, so here code computes them.

**Why it is safe to trust.** Four properties are enforced in code, not in prompts.

1. **Code computes every number.** A model (any OpenAI-compatible endpoint) may plan and phrase, but its text never reaches a deliverable.
2. **A person approves high-risk work.** The 19 high-risk posts write nothing until a person types 我明白，将由持证人员签认 ("I understand; a licensed person will sign"), and the approval covers only the turn it was typed in. Since the security baseline (pull request #61), this holds over the Python MCP server and both web apps as well as in the desktop app, the TUI and the workbench: a program cannot approve by sending a flag (§4.3). Two separate Rust tools that are not deployed still accept a flag (§4.4). Every draft carries `submit_blocked = true`.
3. **Policy as code.** Every registered tool call on the default path, over MCP and through the gateway's tool route first passes a policy function that refuses with a stated reason.
4. **An offline release gate.** 146 checks run in CI on every pull request and push to `main`, beside a Docker job that builds and starts the image. All 146 pass on `main` at `a161251` (§5.3).

**Headline numbers:**

- **The linked run** (synthetic façade job, `a161251`): 5 logistics clauses read from the ITT, 7 statements written from one plan (1 covered by the plan, 2 partial, 0 gap, 4 for a person). A revised panel list re-runs it and names the 4 statements to re-confirm and the 2 stale Word copies (§3.4).
- **Tender vs bid-response matcher** (19-case development set): link precision 1.000 and recall 0.980. All 14 gold number conflicts are found, and no false conflict is raised.
- **Held-out sets:** the task-intent router scores 1.000 with 0 false runs on 18 unseen sentences. The verdict guard scores precision 0.900 and recall 1.000 on 20 unseen sentences.
- **Cargo conservation:** pieces in equal pieces out on all 50 tracked packing fixtures (3 571 pieces).

The last three were measured on `d3ada11`.

**The honest counterweight.** Across 65 posts, only 350 of 1 529 stated facts (0.229) land in the right field. The three bid posts and the warehouse post score 1.00; the other 61 place fewer than half.

**Where it runs.** The target is one AWS Lightsail instance per company, used by employees in a browser. The instance is not running. What CI proves on every change is the Docker image the server would run: it builds, refuses to start without an access token, serves the API behind the token, and keeps its database across a container re-create (§6). We plan to set up the instance before submitting and to give its URL in the submission email.

**Pilot partner.** We propose to pilot civil-buddy with <!--SME:SME_NAME-->a Singapore curtain-wall contractor<!--/SME-->, which has confirmed the collaboration in writing; no pilot has been agreed or started yet. The demo on 10 October runs the linked tender and packing flow on synthetic files (§3.4). The same demo also shows tender review, packing on its own and site paperwork, which keeps the typed licensed sign-off in view; site paperwork is a secondary capability, not the partner's problem. The Business Proposal describes the partner and the proposed pilot.

## How this entry meets the judging criteria

The organisers' briefing gives seven judging criteria, without weights, and the event page lists six qualities a solution should have. The table maps each one to what civil-buddy does, with the strongest evidence a reader can check: a file, a gate check named as in `npm run check`, a figure from this document, or a pull request. It also gives the section that shows it and the gap we know of. Every figure here is one this document already reports, and every file, line, count and check name was re-checked against the code at `a161251`; this section adds no new measurement.

| Criterion | What civil-buddy does, and the evidence | Shown in | Honest gap |
|---|---|---|---|
| **Judging rubric** | | | |
| **1. Goal & Scope Definition** (business value, clear purpose) | One purpose, set by the partner's stated problem: keep the English tender response and the outbound packing linked, as internal drafts whose numbers a small firm can trust. One request reads the ITT's logistics clauses, plans the panel list in the container type the tender names, and writes each logistics statement from a plan figure, citing its clause (`packing_assistant/tender_packing_link.py`, check `tender-packing-link`, pull request #65; flow 1 of `scripts/demo_facade.py`). Code computes every number, gaps stay `UNSPECIFIED`, 待填 or `[TO FILL]`, qualifications and price are left to people, and every draft carries `submit_blocked = true`. | §1, §3.4 | Run only on a synthetic ITT; no pilot has started, so the business value is not measured. Securing, handling and delivery sequence are not modelled, so 4 of the 7 statements go to a person. Beyond the link, breadth is ahead of depth: across 65 posts only 0.229 of stated facts land in the right field. |
| **2. Architecture & Reasoning Loop** (planning pattern, explicit state and memory) | Workflow before loop: rules route each request; a linked request runs one fixed chain (clauses, container decision, plan, statements, link record); tender review is a fixed DAG (parse, then technical ∥ compliance, then aggregate); the opt-in model loop runs only when no workflow matches, for at most 10 steps (`model_loop.py:40`). State is explicit and on disk: the link record `tender-packing-link.json`, compared with the previous one on every re-run; a hashed `handoff.json`; a `check.json` with each input's SHA-256; a LangGraph `SqliteSaver` checkpoint for the packing pause; a revisioned `/logistics` ledger; task memory. An approval never outlives its turn (`memory.py:84-85`). | §2.2, §3.1, §3.2, §3.4 | Model-mode behaviour is outside the gate, and no live-model result is claimed (§5.1, §5.4). Sessions are not yet per user (`ROADMAP`). |
| **3. Tool Use & Integration** (purpose-fit tools, well-typed schemas) | 82 registered tools (71 of them write), each with a JSON Schema input and output contract (`runtime/tool_contracts.py`, check `tool-contracts`). The link is one of them: `tender.packing_link`, read-only, 60 s cap, owned by the bid-parse post (`tool_engine.py:495`, contract `tool_contracts.py:69-74`); its files are written through the engine's `write_deliverable`. An unknown or mistyped argument is refused before policy runs (`tool_engine.py:186-194`); a post writer's successful result without `submit_blocked = true` is refused as a contract error (`tool_contracts.py:43-47`, `tool_engine.py:344-351`); MCP advertises the same schemas, minus the approval fields (`demo/mcp_surface.py:102, 124-136`). Integrations: a tender file from the job folder and an Excel panel list in, Word and Excel out; MCP over stdio for VS Code and Cursor; the knowledge base as MCP resources; REST under `/api/mcp/*`; the packing gateway API. | §2.2 (tool gateway; MCP server), §3.4, §4.3 | The link runs from a steps-mode or `civil` turn only: it is not offered over MCP or a gateway route, and the gateway's older `/api/tender/delivery` still packs through a different path (§3.4). Not every path passes the engine: the gateway's own packing routes and some model-loop tools call their functions directly, and the undeployed Rust `civil-mcp` has its own tool path (§2.3, rule 1). No per-tool version (rule 3). |
| **4. Autonomy & Human-in-the-Loop** (risk-calibrated autonomy, escalation checkpoints) | Four levels, each set in code: a question writes nothing; a low-risk post drafts on its own, for internal use only; input a machine cannot trust stops for a person; and the 19 high-risk posts write nothing until a person types the sign-off sentence, which no setting removes and which covers one turn. In the link, each statement is covered, partial, gap or for a person, and never covered without a plan figure: a plan that does not fit turns the type and the count into gaps (`tender_packing_link.py:288-294, 340-345`); a re-run names the statements a person must re-confirm; the record keeps `confirmed_by_person = false`. Details below. | §3.1–§3.4, §4.1, §4.3 | No approver is named until accounts land (`BEFORE DEMO`). The link record has no step yet in which a person marks a statement confirmed. On the CLI, `--confirm` is the operator's own assertion. A gateway API caller that omits `enable_auto_confirm` skips the packing pause (§4.4). |
| **5. Safety, Security & Guardrails** (prompt-injection resistance, least-privilege access) | Containment by design, not detection: with no key, no model runs; with a key, model text never reaches a deliverable, the model cannot approve, and its numbers and verdicts pass two deterministic guards. Least privilege: policy as code before each registered tool (9 deny codes, `policy.py:21-29`), per-post exclusive tools, per-launch MCP scopes, every tool's `file_path` checked against the sandbox roots and writes confined to them, the link's two inputs read only from inside the job folder (`tender_packing_link.py:726-729`), and one access guard in front of both web apps. The security baseline (pull request #61) is pinned by the checks `access-guard`, `human-approval`, `http-confirmation` and `pack-ship-read-sandbox`. Details below. | §2.2, §4.1–§4.4 | No prompt-injection detector and no injection test set. One shared token and no user identity. The OS sandbox is opt-in and inherits the host's keys. The undeployed Rust `civil-mcp` and Rust workbench still accept `confirm_ok` (§4.4). |
| **6. Observability & Evaluation** (logging and tracing of decisions, golden-path and adversarial cases) | Every policy refusal carries a deny code and a reason; each run writes a `workbench.json`; the link record keeps each statement's clause, clause-text hash, figures and owner, and the SHA-256 of the tender, the list and the plan; a tender check writes `check.json`; a packing run keeps a per-run trace (`packing_assistant/trace_events.py`, check `trace-artifacts`). A 146-check offline gate runs in CI on every pull request and push to `main` (146 of 146 at `a161251`, §5.3). Every benchmark set is labelled held-out, dev or seen, and adversarial cases sit beside the golden path, including link inputs that must not read as covered. Details below. | §2.2, §3.4, §5 | The engine's audit log is in memory only, and tool events are not persisted. Run records carry no model name and no cost. OTel is opt-in. The gate covers the deterministic path only, and there is no LLM judge (`ROADMAP`). |
| **7. Platform & Tooling Usage** (idiomatic framework use, clean multi-agent orchestration) | Standard parts in their usual roles: FastAPI behind one access guard; MCP over stdio (JSON-RPC); LangGraph `SqliteSaver` to pause packing Team A for a person's confirmation and resume Team B after a restart; two checkers running in parallel on one frozen handoff, with no shared live state; OpenAI-compatible Chat Completions; SQLite FTS5; Docker, built and started by CI's `docker-smoke` job; GitHub Actions. On AWS, as designed: one Lightsail instance per company, and Bedrock through the same Chat Completions setting. | §2.2, §3.1, §3.2, §6 | The Lightsail instance is not running. Bedrock is configurable but has never been run from this repository, and whether it works from Singapore is not yet known (§6.3). Four model clients, not one egress (`ROADMAP`). |
| **Solutions should be** | | | |
| **Practical & relevant to SME needs** | Aimed at the partner's own problem and sized for a firm with a handful of people: no key and no model call by default, on a laptop or one server. On synthetic versions of the partner's files, one request links the ITT's 5 logistics clauses to a plan of 24 panels in 6 × 40HQ and writes 7 English statements; a revised list of 30 panels re-plans to 8 × 40HQ and names what changed. The same demo lists the tender's key terms and rejection clauses, and drafts the daily report while the work-at-height briefing waits for the sign-off. | §1, §3.4 | A-frame stillages, lashing and delivery sequence are not modelled. The tender parse shows 0 of the ITT's 12 façade specification clauses. The UI is in Chinese. Since PR #67 the sign-off sentence has one exact English equivalent and the English bid-book is English throughout (§3.4, §7). |
| **Feasible to deploy or pilot** | What a server needs is on `main`: the gateway's Docker image and compose file with a health check (`docker-compose.yml:23`), a start that refuses to open without `CIVIL_TOKEN`, the database inside the data volume, SQLite backups (7 kept, `storage.py:735`) and an operator guide for one Lightsail instance (`docs/deploy-minimal.md`). On every change, CI's `docker-smoke` job builds the image and proves the refusal, the token check and that a session survives a container re-create (§6). Every post runs offline with no key, so a pilot needs no model account. | §6 | The instance is not running; its URL is to follow in the submission email. The image has never run on a cloud host. No TLS proxy configuration file ships, there are no accounts yet, and no pilot has been agreed (§1, §7). |
| **Secure & responsible in design** | Code computes every number, the link never marks a clause covered without a plan figure, and the 19 high-risk posts need a person's typed sign-off. Verdicts the product may never state ("can bid", "passed review") are struck from model-mode replies by the verdict guard, and `civil review` flags them in any document. With no key, nothing is sent to a model. The limits are listed, not hidden (§4.4, §5.4). | §1, §3.4, §4 | Nothing is masked before a cloud model call (PII masking `ROADMAP`), so no key or a local Ollama is the safeguard. The open items of §4.4. |
| **Sound technical architecture** | The one-company version of a zero-trust agent platform: each region is tagged with its real status, each of the six rules gets a verdict and its gap, and multi-tenant parts are left out on purpose. | §2.2–§2.4 | Rules 1 and 2 hold on the deployed surface; rule 3 holds for registration, not for versioning; rules 4–6 (two identities, a session as a sandbox, telemetry and cost) are planned (§2.3). |
| **Effective use of agentic AI** | The model plans and phrases; code computes, checks and writes. Rules route first (a linked request in English or Chinese goes to the link), a fixed workflow runs two evidence-bound checkers in parallel, and a bounded model loop picks from a fixed tool menu. A model analysis must cite its sources and use only numbers from the cited quotes, or it is rejected (`runtime/tender_workflow.py:117-146`). If the model is down, `agent_mode = auto` falls back to deterministic steps (`runtime/turn.py:97`). | §2.2, §3.1, §3.4, §4.1, §4.2 | Model mode is opt-in and not measured on a live model (§5.4); the link has not been run in model mode. Most non-bid drafts are correct skeletons that place fewer than half of the stated facts (0.229 overall, §5.2). |
| **Measurable business impact** | Measured today, on the machine side. On `a161251`, one request turns the synthetic ITT and a 24-panel list into 7 clause-linked statements, an English bid-book chapter and a hashed link record; a revised list re-plans and names the 4 statements and the 2 Word copies to redo (§3.4). On `d3ada11`: the bundled tender reviewed into 14 files in 1.6 s, with all 72 requirements quoted; the long-frame packing list planned in 0.4 s; pieces in equal pieces out on all 50 packing fixtures. The pilot's business metrics are defined in the Business Proposal's pilot section. | §3.1, §3.2, §3.4, §5.2; Business Proposal | No staff time saved has been measured, and no baseline has been measured at the partner: the linked run has not been timed against the partner's own first drafts. Baselines are to be measured by the partner during the pilot, and the targets agreed with it. No pilot, price or user count yet. |

**For an evaluation and safety review.** Five points the table can only summarise.

**Golden path and adversarial cases, side by side.** The golden path is the linked run and the other three flows of the façade demo (§3.4, on `a161251`), the three flows through the real entries (§3.1–§3.3), 12 of 12 acceptance cases and the no-key one-shot demo (§5.2). The adversarial cases sit in the same gate:

- **Held-out sets written after the rules were frozen.** The router's 18 sentences pair drafting requests with look-alike questions that must not start a run: accuracy 1.000, 0 false runs. The verdict guard's 20 sentences mix 9 stated verdicts with look-alikes that must not be flagged, such as negated, asked, conditional or quoted ones: P 0.900, R 1.000 (`test/benchmarks/verdicts/heldout2.json`). On `a161251`, 28 unseen English sentences score 0.786 with 0 false runs, and all 6 misses fall back to chat.
- **Known faults, on dev or seen sets.** The number guard flags all 35 unsourced numbers in 17 of 37 drafts and nothing else, the 20 clean drafts included. The tender matcher finds all 14 gold number conflicts in 19 cases with no false conflict. The bid check catches 6 of 6 planted defects with 0 of 8 false alarms on a set that is now seen (2 of 8 on its first held-out run).
- **Inputs that must not produce a plan.** A list missing dimensions and a crafted CSV with a blank weight and "10/12" give needs-human rows and no container count (§3.2). In the link, the same two rows stop the plan and the statements name the row (`P02`).
- **Tenders the link must not read as covered.** The check `tender-packing-link` (18 tests) varies the synthetic ITT. A 20GP clause gives a plan that does not fit (9 × 20GP hold 18 of the 24 crates, N0 12), so the type and the count become gaps, no gross mass is stated, and nothing is covered. A 6,000 kg gross limit gives a gap ("exceeds the limit by 472.8 kg"); a 2,500 kg payload clause is read as a cargo limit and gives a gap. A size with no type ("40-foot"), a type in a sentence that also says "not", or a type the planner cannot model (40 ft open top, 40FR) gives no plan and goes to a person. Two types typed in one request stop with `ambiguous_container_type`. Lashing, stillages and delivery sequence are never covered.
- **Attacks on the security baseline.** `access-guard` sends forwarded, rebound, HTTP/1.0 and real-proxy-shaped requests, remote WebSockets and a foreign `Origin`, and tries to start the apps on an open address with no token. `http-confirmation` and `human-approval` send approval flags in every shape (`true`, `"true"`, `"yes"`, `1` and more) and text that merely quotes the sentence, over HTTP and MCP, and check that an approval does not carry into a later turn. `pack-ship-read-sandbox` asks for paths outside the roots, a secret file name inside the job folder, a link out of it, `..` and an embedded NUL. The gate passes only if every one is refused.

Not covered: prompt injection (next point), any live-model run (§5.1), and the real tender PDFs, whose results are archived only (§5.4).

**Prompt injection, as it really is.** There is no injection detector and no injection test set (§4.4); resistance comes from what a model is allowed to do, not from spotting attacks. With no key, the default, no model runs, so text planted in a tender or a packing list reaches only deterministic parsers. With a key, the model does read file text, and in model mode the limits are structural. Attachments are reference data, and the writer receives only the user's words and the named files, so model text never reaches a deliverable. The model's tools have no approval field, and a sign-off sentence it writes is replaced (`model_loop.py:785`). Its tools are a fixed menu, their writes go through the engine and its policy, and every reply passes the number and verdict guards. With a key, a planted instruction could still steer which menu tool runs on which listed file, and what a chat reply says (in the workbench a question-only turn calls the model whenever a key is set, §4.4). No test measures this yet.

**Decision logs and traces: what exists and what does not.** Each policy refusal carries one of 9 deny codes and a stated reason. Each run writes a `workbench.json` in which the steps path records each tool's real `ok`. A tender check writes `check.json` with every input's SHA-256 and what changed since the last run, next to the hashed `handoff.json`. A packing run keeps a per-run trace (`trace_events.py`: JSONL or SQLite, exportable) and a checkpoint of its pause, and a model turn records `model_calls` and `tool_calls`. Not yet: the engine's audit log is in memory only, tool events are not persisted, a failed tool on the model path still shows "done", run records name no model and no cost, and OTel spans are opt-in. A durable audit log of tool calls and approvals is on the roadmap (§7).

**Least privilege.** A post cannot call another post's exclusive tools (`policy.py:137-145`). A scoped MCP launch (`--pack` or `--expert`) sees only its 8, 9 or 13 tools, and `civil.turn` runs only posts inside it; an unscoped launch lists 11 tools and cannot approve either. A model turn gets a fixed menu, and a question-only turn only the 5 read tools. Every `file_path` is checked against the sandbox roots, writes stay inside them, and secret files and production paths are refused. The model key stays on the host, and a new base URL needs the key again. A URL fetch refuses private, loopback, link-local and metadata addresses. The network is closed without the token, and only a person's typed sentence approves, for one turn. Missing: user identity. There is one shared token, the post id is declared by the caller, and every token holder sees all sessions (accounts `BEFORE DEMO`, per-user sessions `ROADMAP`).

**Risk-tiered autonomy and its escalation points.**

1. **Answer.** A question is answered and writes nothing (0 false runs on the held-out router set).
2. **Draft.** A low-risk post drafts without asking. The draft is internal (`submit_blocked = true`), and gaps stay `UNSPECIFIED` or 待填.
3. **Stop for a person.** Input a machine cannot trust halts that step: needs-human packing rows (no container count), a link statement the plan cannot evidence (lashing, handling, delivery sequence, crate design, a plan that does not fit), cargo that is not conserved (`cargo_not_conserved`), an unreadable tender on the CLI (`tender_unreadable`, nothing written), an unreadable response (未能判断, "cannot judge", never 未响应, "no response"), number conflicts (`needs_review`, never resolved automatically), a façade clause on unmodelled handling ("Pending SME"), a panel list revised after the statements were written (the re-run names the statements to re-confirm), and an ambiguous request (a question, not a guess). On the gateway page, a packing run pauses at `await_user_confirm` before any container is loaded.
4. **Licensed sign-off.** The 19 high-risk posts need the typed sentence, for that turn only, and no setting removes it; on `/logistics` a person also types it before packing and before export.

Bounds on all four levels: at most 10 steps per model turn, 2 model calls and 30 s per tender workflow, cancel per run or session, and a read-only configuration that freezes writes.

The escalation gaps are those in row 4 of the table.

## 2. Architecture

### 2.1 The one-company version of a zero-trust agent platform

A reference zero-trust agent platform has a control plane; entry, model and tool gateways; a runtime in which every session is a sandbox; a data layer; and an improvement loop, all held together by six rules (§2.3). It is drawn for an enterprise running many agents for many users and tenants.

civil-buddy is **its one-company, one-server version**: the same regions, sized for one firm on one Linux instance, with each box tagged in §2.2 by what exists today. The per-agent core already runs on `main`: a fixed workflow before any open loop, policy as code, typed human sign-off, a fixed menu of registered tools, two parallel evidence-bound checkers, an offline gate and, since pull request #61, one access guard in front of both web apps. Multi-tenant infrastructure is left out on purpose (§2.4).

![Architecture](nus-iss-architecture.svg)

*Figure 1. civil-buddy as a one-company zero-trust agent platform. The status tags are those of the legend (top right) and of the table at the start of this document.*

### 2.2 Regions and status

`policy.py`, `tool_engine.py`, `model_loop.py`, `memory.py`, `civil_config.py` and `os_sandbox/` are in `packing_assistant/runtime/`.

| Region / part | Status | What is true at `a161251` |
|---|---|---|
| **Control plane** | | |
| Post and tool registry | `LIVE` | 66 posts, 19 of them high-risk (`workbench/seed.json`). Unknown tools are refused. Declarative plugins cannot shadow a built-in post and are trusted by content SHA-256. |
| Policy as code | `LIVE` | `policy.evaluate` runs before each registered tool. It has 9 deny codes, each with a reason: unknown tool, write from a chat turn, another post's exclusive tool, sandbox, production path, secret file, circuit breaker, budget, cancelled (`policy.py:20-29, 103-224`). It also checks any `file_path` argument against the sandbox roots (`policy.py:172-180`). |
| Typed human sign-off | `LIVE` | No typed sentence, no write on a high-risk post. The desktop app, the TUI, the workbench server, the packing gateway and `civil serve` check the sentence in code (`civil_config.py:171-190`, `demo/chat_service.py:349`, `gateway/app.py:479-497`). On the CLI, `--confirm` is the local operator's own assertion. |
| Human-only approval, MCP and HTTP | `LIVE` | MCP never advertises or accepts `confirm_ok` or `p0_confirmed`; a high-risk write over MCP returns `approval_required` and writes nothing (`demo/mcp_surface.py:102, 133-134, 297-333, 358`). The gateway and `civil serve` accept only `confirm_text` equal to the sentence; a `true` boolean is refused (HTTP 422 on the gateway). The workbench's `confirm_ok` field no longer approves. In model mode an approval covers that turn only (`memory.py:85`). The undeployed Rust `civil-mcp` and Rust workbench still accept a flag (§4.4). |
| Accounts and roles | `BEFORE DEMO` | Admin, engineer and licensed approver; each sign-off names its approver. Today: one shared token, and the operator is recorded as 本地用户 ("local user"). |
| Offline release gate | `LIVE` | 146 checks, run by `npm run check` in CI with model keys stripped. It covers the deterministic path only. 146 of 146 pass on GitHub CI at `a161251` (§5.3). |
| Turn budgets and stop | `LIVE` | Model loop: at most 10 steps, and 1 500 output tokens per call. Tender workflow: 2 model calls and 30 s. Cancel per run or per session; a read-only config freezes writes. |
| Cost ledger and usage | `ROADMAP` | The `SessionLedger` fuse is wired only in a demo script (`tool_engine.py:84`: `ledger = None`). No token or money cost is recorded. |
| **Touch points and entry gateway** | | |
| Browser workbench, CLI and desktop app; loopback by default | `LIVE` | FastAPI on port 8765, bound to 127.0.0.1 unless `CIVIL_HOST` is set; a non-loopback host without `CIVIL_TOKEN` refuses to start. The `civil` CLI and TUI run in a job folder; there is also a Tk desktop app. CI blocks external URLs in shipped assets. |
| MCP server; KB over MCP | `LIVE` | MCP over stdio (JSON-RPC), used by VS Code and Cursor; the workbench and gateway mirror the tool list, tool calls and KB resources as REST routes under `/api/mcp/*`. Tools are scoped per launch: 8 (`--pack construction`, the first server in the shipped IDE configs), 9 (`--pack bid`) and 13 (`--expert pack-ship`). Within a `--pack` or `--expert` scope, `civil.turn` runs only posts inside it (`demo/mcp_surface.py:304-315, 369-373`); a launch with neither flag lists 11 tools and does not restrict `civil.turn` (it still cannot approve). The knowledge base is exposed as MCP resources. A separate Rust `civil-mcp` binary in `workbench/` is not part of the deployed surface (§4.4). |
| Packing gateway | `LIVE` | Pack-ship API, page and Docker image, behind the same access guard as the workbench. The container refuses to start without `CIVIL_TOKEN`; CI's `docker-smoke` job builds and starts the image on every change (§6). No server runs it yet. |
| Voice · IM approvals and schedules | `OPT-IN` · `ROADMAP` | Voice: local faster-whisper, an optional install. IM approvals and schedules: not built. |
| Shared access token | `OPT-IN` | One `CIVIL_TOKEN` for both apps, off by default. When set it is required on every route except the page shells, static files and `/api/health`, WebSockets included, and from loopback too, because behind a proxy every request is loopback. It is compared with `hmac.compare_digest`; `?token=` is accepted once and swapped for an HttpOnly, SameSite=Strict cookie (`packing_assistant/access_guard.py:101-106, 135-149`). |
| Fail-closed network | `LIVE` | With no token, only a request that really came from this machine passes. Any forwarding header, HTTP/1.0, a non-loopback `Host`, `Origin` or `Referer`, or a cross-site fetch makes it remote (`access_guard.py:60-78`). `demo/serve.py`, a `uvicorn --host` start and the container refuse a non-loopback bind without a token; `CIVIL_ALLOW_OPEN_LAN=1` is the one explicit opt-out. The gateway's CORS allows only same-machine origins, without credentials (`gateway/app.py:110-127`). |
| TLS reverse proxy | `BEFORE DEMO` | HTTPS on the Lightsail instance, with the apps on 127.0.0.1. The nginx settings are in `docs/deploy-minimal.md` (§6.2). |
| Session turn lock; input isolation | `LIVE` | One turn per session (HTTP 409 otherwise). Attachments are reference data. A sign-off sentence written by the model is scrubbed, and no caller of the Python MCP server or the web apps can assert the approval. No injection detector yet. |
| PII masking | `ROADMAP` | Nothing is masked before a cloud call. Today's safeguard: no key, or a local Ollama. |
| **Agent runtime** | | |
| Deterministic post pipelines | `LIVE` | Default `agent_mode = steps`: code writes every deliverable. No key is needed, and the CLI makes no model call. |
| Workflow before loop | `LIVE` | Tender review is a fixed DAG: parse, then technical ∥ compliance, then aggregate. The open loop runs only when no workflow matches. |
| Tender ↔ packing link | `LIVE` | One request, in steps mode with no key, reads the ITT's logistics clauses, plans the panel list in the tender's container type, writes clause-linked statements and a hashed link record, and on a re-run names what changed (§3.4). Reached from a `civil` or workbench turn, not over MCP or a gateway route. |
| Two evidence-bound checkers | `LIVE` | Two parallel children, each with its own copy of the handoff. An optional model analysis must cite its evidence and is stored as unverified (§4.1). |
| Bounded model loop | `OPT-IN` | `agent_mode = model`: a fixed menu of 8 registered tools (a question-only turn gets the 5 read tools; a turn bound to a CAD, planning or logistics project gets that page's own fixed menu) and at most 10 steps. The writer gets only the user's words and the named files (`model_loop.py:319-323`). |
| Number and verdict guards | `OPT-IN` | Check every model-mode reply, with one rewrite allowed. `civil review` runs the same checks on any document with no model. |
| Session and project memory | `LIVE` | Task memory and a session-local FTS5 index; neither reads other sessions. Layered `CIVIL.md` job instructions. The approval is not kept between turns. |
| Durable approval pause | `LIVE` | On the gateway page, which sends `enable_auto_confirm = false`, packing Team A stops at the gate and is checkpointed (LangGraph `SqliteSaver`); Team B resumes after a restart. An API caller that omits the field skips the pause (`enable_auto_confirm` defaults to true, `gateway/app.py:1391`). |
| OS sandbox worker | `OPT-IN` | A per-turn confined worker for CLI and desktop turns in a job folder. Linux: Landlock and seccomp. Windows: Low integrity, which blocks writes and spawn but not network. |
| Per-user sessions | `ROADMAP` | Today every token holder sees all sessions. |
| **Model gateway and pool** | | |
| One wire protocol | `LIVE` | OpenAI-compatible Chat Completions and one config resolver (`packing_assistant/llm.py:40-71`). Not yet a single egress: four clients build their own requests. |
| Key stays on the host | `LIVE` | The host puts the key only into the outbound `Authorization` header; the settings API returns `key_masked`, and runtime settings are never written to disk. One exception: the opt-in OS-sandbox worker inherits the host environment, keys included (`os_sandbox/__init__.py:116`). |
| Settings key lock | `LIVE` | A settings POST that moves `base_url` to another scheme, host, port or path is refused unless the key is typed again, so the stored key never goes to a new host (`demo/model_settings.py:33-42, 118-127`). |
| Steps fallback | `OPT-IN` | `agent_mode = auto`: if the model is down, the turn runs as deterministic steps. |
| Cloud endpoints; local Ollama and helper models | `OPT-IN` | OpenRouter, DeepSeek or any compatible API (no key by default). Locally: Ollama (`127.0.0.1:11434/v1`), faster-whisper, PaddleOCR. |
| Amazon Bedrock | `BEFORE DEMO` | Configurable through the same Chat Completions setting; it has never been run from this repository. In Singapore the endpoint this setting needs is available, but the models AWS's examples use are not listed there (§6.3), so whether it works from Singapore is not yet known. A verification run is planned before the demo. |
| Single egress and metering | `ROADMAP` | One client, provider usage per turn, failover to a second model. |
| **Tool gateway** | | |
| Single execution point; registered tools only; write-path checks | `LIVE` | `ToolEngine.admit` checks the tool's contract, then policy; `ToolEngine.execute` runs `admit`, then the tool, then records it, for 82 registered tools (71 of them write) (`tool_engine.py:174-222, 224`). Writes are confined to the allowed roots; secret files, production paths and spawns not on the allowlist are refused. Pack-ship over MCP and the gateway's `/api/mcp/tools/call` pass `admit` and then run without `execute`, on purpose (§4.3). The gateway's own packing routes (`/api/pipeline`, `/api/table/parse`, `/api/export/*`) do not pass the engine; they call the packing engine directly, with their own path checks. In the opt-in model loop some tools also call their function directly (for example `pack_plan` calls `run_plan` on a file inside the job folder) and only write through the engine. |
| Pack-ship read guard | `LIVE` | Policy checks `file_path` against the sandbox roots for every tool, and the pack-ship parser opens the resolved path it checked (`tools/pack_ship_mcp.py:310-332`). Gateway `/api/table/parse` confines `path=` to the sandbox roots whenever a token is set or the request is not from this machine (a token-less local request may still name any table it can read; `gateway/app.py:1492-1509`), `/api/run-pdf` takes a file name, not a path, and `/api/artifact` serves only the output folders. |
| Public-URL fetch only | `LIVE` | Only http(s) URLs the user supplies. Private, loopback, link-local and metadata ranges are refused, and every redirect is re-checked (`demo/uploads.py:480-530`). |
| Per-run records | `LIVE` | Each run writes a `workbench.json`, and the steps path records each tool's real `ok`. Tool events are not yet persisted. |
| **Data, knowledge, improvement loop** | | |
| Job folder; deterministic engines | `LIVE` | The firm's own tenders, packing lists and drawings are the only business data. The packing solver, tender parser and post writers compute every number. |
| Post knowledge base | `LIVE` | 436 files in a SQLite FTS5 index: 346 post documents and 90 packing documents. No vectors, by design: every excerpt is a literal quote a number can be traced to. |
| SQLite store | `LIVE` | WAL, migrations and `VACUUM INTO` backups. At start, the gateway backs up if the last backup is more than 24 h old, and keeps 7. |
| Offline eval gate; manual attribution; trace store | `LIVE` | The 146-check gate (§5). Ablation variants and error codes; fixes land as PR + bench. Exportable packing SQLite trace. |
| OTel spans | `OPT-IN` | `PACKING_OTEL=1` plus `requirements-observability.txt`. 3 span sites; not installed by default. |
| Judge and auto-attribution | `ROADMAP` | An LLM judge on real model runs, and automatic failure classes. |

### 2.3 The six rules

| Rule | Verdict, and what holds today | Gap → when |
|---|---|---|
| **1. One way in, one way out** | *Holds for every registered tool call:* on the default path, over MCP and on the gateway's tool route (`/api/mcp/tools/call`), each passes the engine's contract and policy check (`ToolEngine.admit`); on the default path `execute` then runs and records it. Pack-ship over MCP and the gateway tool route is admitted, then run without `execute`, on purpose (§4.3). The gateway's own packing routes (`/api/pipeline`, `/api/table/parse`, `/api/export/*`) call the packing engine directly, with their own path checks. | The Rust `civil-mcp` binary has its own tool path; it is not deployed → open (§4.4). One model protocol but four clients → single egress `ROADMAP`. |
| **2. Decide apart from execute** | *Holds on the default path, over MCP and over HTTP.* `decide_gate` is code. Only the typed sentence approves, and only for its turn; no program can send an approval flag to the Python MCP server or the web apps. In civil-buddy's own model loop, the model's tools have no confirm field. Model text never reaches a deliverable. | The Rust `civil-mcp` binary and Rust workbench still accept `confirm_ok`; neither is deployed → open (§4.4). |
| **3. Registered and versioned** | *Registration holds; versioning is planned.* Unknown tools are refused, and plugins are trusted by hash. Prompts, skills and policy sit in git behind the gate. | No per-tool or per-post version, no model name in run records, and runtime model switches skip the gate → `ROADMAP`. |
| **4. Two identities, least privilege** | *Planned.* Least privilege per post holds: a post cannot call another post's exclusive tools (`policy.py:137-145`). | No user identity: the post id is caller-declared, and the operator is recorded as "local user" → accounts `BEFORE DEMO`. |
| **5. A session is a sandbox** | *Planned; opt-in today.* A per-turn confined worker exists. | Off by default and bypassed by the workbench. The host is long-lived, and the worker inherits keys → `ROADMAP`. |
| **6. Telemetry and cost built in** | *Planned.* Per-turn caps; each model turn records `model_calls` and `tool_calls`. | No token or money cost, OTel off, and the fuse only in a demo → `ROADMAP`. |

### 2.4 What we deliberately do not build (`NOT NEEDED`)

These are left out because one company runs on one server:

| Not built | Why |
|---|---|
| microVM and browser sandbox fleets | The product runs no model-written code and has no browser tool, so one confined process per turn is enough. |
| Kubernetes and GPU clusters | The model is a hosted endpoint or a single Ollama machine. |
| A lakehouse | Run records fit in SQLite. |
| A2A federation | civil-buddy calls no outside agent; other agents come in through MCP. |
| Tenant isolation, tenant billing, delegation tokens | One install serves one company, and the job folder is the data source. Per-user isolation is on the roadmap. |
| Batch inference and self-trained models | The volume does not justify them; off-the-shelf endpoints plus deterministic code are cheaper and easier to audit. |

## 3. How it works

§3.1–§3.3 describe the three building blocks: tender review, packing and shipping, and site and office posts. §3.4 is the flow that answers the partner's problem: it chains the tender parse of §3.1 and the packing plan of §3.2 into one linked run.

### 3.1 Tender review: tender file → parse → two checkers → aggregated review

**Entry.** In the workbench, the user uploads the tender and bid files and asks 全面检查投标响应 ("fully check the bid response"). Files can be tagged tender, response or reference. DOCX and text-layer PDF are read; a scanned PDF counts as unreadable unless OCR is installed. On the CLI the same request is `civil exec -C <job folder> "全面检查投标响应：招标文件.docx 投标函.docx …"`. In model mode the workflow is the `tender_compare` tool.

**Routing.** No model is involved. `task_router.route_task` picks the fixed `tender-review` workflow when the request contains a tender word, a word such as 全面 ("full") and a check word, or when all three bid posts are selected.

**Pipeline.**

1. `decide_gate` runs.
2. `tender_parse` extracts each requirement and locates its exact quote, with offsets.
3. `tender_response_match` finds candidate responses and numeric mismatches.
4. The handoff is frozen as `handoff.json`, with a SHA-256 hash.
5. Two checkers run in parallel (`ThreadPoolExecutor(max_workers=2)`) with no shared live state. **bid-tech** drafts a technical outline from the scoring points. **bid-compliance** builds the gap table: responses, numeric conflicts, unreadable files and hazard items.
6. `tender_review.review_draft` aggregates. Conflicts are listed as `needs_review` and never resolved automatically. `responses_verified` is always 0. `check.json` records every input's SHA-256 and what changed since the last run.

The output is 14 files: the handoff, `check.json`, and four drafts (tender extract, bid-tech, bid-compliance, collaboration review), each as `.md`, `.docx` and `.xlsx`.

**The person's part.** The person chooses the files and their roles, checks every candidate response, and submits and signs outside the product; `submit_blocked` is always true. If the tender cannot be read, the CLI stops with `tender_unreadable` and writes nothing. If a response file cannot be read, its rows become 未能判断 ("cannot judge"), never 未响应 ("no response").

**With and without a key.** Without a key, `model_calls = 0`. The CLI never passes a model, even when a key is set. With a key, in the workbench only, each checker makes one extra call under the rules in §4.1. The output goes to a separate `model-analysis.md` marked unverified. If the handoff does not fit the model's window, the model is skipped and nothing is truncated. The default 30 s deadline is untested with a live model.

**Measured on `d3ada11`.** We ran the bundled `cn_municipal` tender and three bid files through `run_agent`, the code behind `civil exec`:

- 72 requirements, all with a located quote;
- 51 response candidates, 0 marked verified;
- 84 open items and 3 conflicts;
- 0 forbidden claims and 0 model calls;
- 14 files in 1.6 s (2.4 s from the PDF, with identical counts).

On this set the bid check catches 6/6 planted defects with 0/8 false alarms. The set is now seen: its first held-out run, archived in the benchmark README, had 2/8 false alarms.

### 3.2 Packing and shipping plan: packing list → needs-human gate → crates and containers → approval → export

Three entries share one engine.

- **Packing gateway page.** `POST /api/table/parse` maps columns and units with `table_mapper` and computes nothing. `POST /api/pipeline` runs Team A: crates plus `check_conservation`. With the page's `enable_auto_confirm = false`, the run stops at `await_user_confirm` and is checkpointed. `POST /api/confirm` resumes Team B, which loads N containers of one type from the lower bound N0. `POST /api/export/shipment` writes the POR and lashing workbook.
- **CLI or chat on the pack-ship post.** `civil exec -C <job folder> "装柜方案 list.xlsx"` ("container plan") calls `pack_ship_solve.run_plan`, which runs the same mapper, gate, crating, loading and conservation check. The report copies its numbers from the JSON tool result. In model mode the model may call `pack_plan` only on a file it has listed.
- **Workbench `/logistics`.** A revisioned ledger. Packing runs in a killable subprocess with a 60 s cap and no API keys. A person applies any change a model proposes, checked against the ledger revision and a content digest.

**The gate.** No path yields a container count while any row lacks a usable weight or dimensions, has a quantity that is not a positive integer, or does not fit the container. Each such row gets one plain sentence. "3 EA" reads as 3; "10/12" is flagged, not read as 1012. If pieces or net mass differ between input and plan, the tool path returns `cargo_not_conserved` instead of a plan, and the gateway sets `ship_ok = false`.

**The person's part.** The person fixes or excludes each needs-human row. On the gateway they confirm, revise or cancel the crate list. On `/logistics` they confirm the ledger and type the sign-off sentence before packing and before export. CLI drafts are marked "not for booking; lashing and VGM signed separately". A model may choose tools, never coordinates or counts.

**Measured on `d3ada11`.**

- *Long-frame list (`case_b_long_frames_40hq.xlsx`) through the gateway:* 4 rows and 23 800 kg parsed, 9 crates, then a pause at `await_user_confirm`. After confirm: 3 × 40HQ, N0 = 2, `can_fit` true, resumed from disk, and a 5-sheet export. The CLI gives the same plan in 0.4 s.
- *Blocked inputs.* A list missing dimensions (`G7_missing_dims`) gives no plan and 3 needs-human rows. A crafted CSV with a blank weight and "10/12" gives 2 needs-human rows and nothing is stored. `/logistics` holds `case_b` for a person, because its dimension scope is not declared.

### 3.3 Site and office posts in steps mode, with no key

In the workbench, the user picks a post such as 项目日报 ("daily report"), or just types. On the CLI: `civil exec -C <job folder> "写一份项目日报；项目名称：…；日期：…"`, with `--skill` to force a post. The TUI and desktop app share `run_turn`.

1. **Routing.** An explicit selection, an `@mention` or a phrase table picks the post. A label that could mean two duties gets a question instead of a guess.
2. **Gate.** A high-risk post returns `hitl_pending` and writes nothing until the sentence is typed: on the card, in the TUI or in the dialog. On the CLI, `--confirm` is the operator's own assertion.
3. **Draft.** `run_agent` calls `tool_engine`, which runs policy first, then the post writer. The writer copies the stated facts into their rows. Gaps stay 待填 or `UNSPECIFIED`, and Word and Excel copies are exported. The benchmark counts 0 forbidden sign-off words.

**Measured through the real CLI entry, on `d3ada11`.**

- The daily-report example went to `pm-daily` and placed all 6 stated facts.
- A meeting-logistics sentence mentioning 图纸会审 ("drawing review") went to two posts and left three stated fields `UNSPECIFIED`.
- A recruitment sentence mentioning 危大工程旁站 ("hazardous-works supervision") went to the high-risk `method-hazard` post and wrote nothing. With `--skill hr-recruit`, it wrote the brief.
- `safety-brief` wrote 0 files without `--confirm` and 3 with it.

Routing can misfire. For most non-bid posts, the draft is a correct skeleton that keeps the user's words but places fewer than half of the facts (§5.2).

### 3.4 The partner's problem: tender response and packing, linked (pull request #65)

Pull request #65, merged on 2026-09-26 as `a161251`, answers the problem the partner stated (§1): the English tender response and the outbound packing run as two disconnected exercises. It chains the parse of §3.1 and the plan of §3.2 into one run whose statements stay tied to their clauses and to the plan.

**Entry.** `civil exec -C <job folder> "Link the tender facade_itt_doc.md to the packing list facade_panels.xlsx and write the logistics response"`, or the same in Chinese (for example 招标装柜联动：按招标 … 和装箱单 … 重出物流应答), in the CLI, the TUI or the workbench. `task_router.wants_link` recognises the request in either language; a question stays a chat turn. A bid-parse request that names exactly one tender and one panel list in the job folder runs the registered, read-only tool `tender.packing_link`, and its files are written through the engine. A missing input stops with `link_inputs`; two container types typed in one request stop with `ambiguous_container_type`.

**Pipeline** (`packing_assistant/tender_packing_link.py`).

1. **Clauses.** `logistics_clauses` reads the ITT's logistics clauses with their clause numbers: container type, gross mass (the limit and its basis: gross, cargo or unstated), securing, handling, crating and delivery sequence.
2. **Container type.** A type typed in the request wins; otherwise the type comes from the clause; if the ITT names none, the plan uses 40HQ and says so. If the ITT allows several types, names only a size ("40-foot containers"), names a type the planner cannot model, or only refuses types, no plan is made and the row goes to a person.
3. **Plan.** `pack_ship_solve.run_plan` plans the real panel list, with the needs-human and conservation gates of §3.2 still in force.
4. **Statements.** S1 to S7: container type, containers used, the heaviest container's gross mass against the limit (the engine's per-container cargo plus the knowledge-base tare), securing, handling, crate structure and delivery sequence. Each carries a status (covered, partial, gap or for a person), an owner, its clause, a SHA-256 of the clause text and the plan figures. A statement is covered only when a plan figure shows it. A plan that does not fit makes the type and the count gaps. Securing, handling and delivery sequence are never covered, because they are not modelled. When the tender asks for A-frame stillages, which are not modelled either, the count and the gross mass read partial.
5. **Link record.** `tender-packing-link.json` ties each statement to its clause, its plan figures and the SHA-256 of the tender, the panel list and the plan, with `confirmed_by_person = false` and `submit_blocked = true`. On a re-run, `compare` reports each earlier statement as changed, unchanged, new or withdrawn, names the inputs that changed, and names the earlier Word copies that still hold the old statements; Word export never overwrites a copy.
6. **English bid-book.** Chapter 6 of `bidbook.en.md` is written from the plan, and each statement cites its clause and figure; the clause-level rows join the deviation schedule. Qualifications and price stay `[TO FILL]`.

The run writes `tender-packing-link.md` and `.json`, `bidbook.en.md` and `pack-plan.json`, with Word and Excel copies, and saves the tender hand-off for the bid posts.

**The person's part.** A person confirms the loading plan before any booking, answers each `[TO CONFIRM]` (lashing by a competent person, handling by logistics, delivery sequence by the project manager, crate design), fills in qualifications and price, and re-confirms every statement a re-run names. Nothing is booked or submitted.

**Measured on `a161251`.** `python scripts/demo_facade.py` runs every turn through `civil.run_task`, the function behind `civil exec`, in steps mode with no key, in a new job folder; it writes nothing outside that folder. Every input in `examples/facade-demo/` is synthetic and says so. Flow 1 is the linked run. It reads 5 logistics clauses from the ITT (4.7 handling; 4.8 container type; 4.9 gross mass; 4.10 securing; 4.11 delivery sequence), takes 40HQ from Clause 4.8, and writes 7 statements. Then a revised panel list arrives and the same request runs again:

| | First run | Revised panel list (rev B) |
|---|---|---|
| Panel list (synthetic) | `facade_panels.xlsx`: 24 panels, 10 800 kg net | `facade_panels_rev_b.xlsx`: level L9 added and the L8 panels heavier; 30 panels, 13 920 kg net |
| Plan, in 40HQ from Clause 4.8 | 6 × 40HQ (N0 = 6); 24 crates; pieces 24 → 24 | 8 × 40HQ (N0 = 8); 30 crates |
| Heaviest container against Clause 4.9 (20 000 kg gross) | 6 472.8 kg (2 582.8 cargo + 3 890.0 tare), margin 13 527.2 kg | 6 752.8 kg (2 862.8 cargo + 3 890.0 tare), margin 13 247.2 kg |
| Statements | 1 covered, 2 partial, 0 gap, 4 for a person | 1 covered, 2 partial, 0 gap, 4 for a person |
| SHA-256, first 12 hex: tender · list · plan | `855de144f92e` · `3d62fd55274c` · `8a0bec8ece8a` | `855de144f92e` · `7f091a5c9a04` · `21d13e1931b6` |

The seven statements of the first run:

| | Clause | Kind | Status | What the statement rests on |
|---|---|---|---|---|
| S1 | 4.8 | Container type | covered | The clause names 40HQ, and the plan was made in 40HQ. |
| S2 | 4.8 | Containers used | partial | 6 × 40HQ for 24 pieces and 10 800 kg. The count rests on the planner's own crate model, so it carries "[TO CONFIRM by logistics: re-confirm the count once the packaging of Clause 4.7 (A-frame stillages, upright transport, no stacking, face protection) is sized - it is not modelled]". |
| S3 | 4.9 | Gross mass | partial | The heaviest container is 6 472.8 kg gross against the 20 000 kg limit. |
| S4 | 4.10 | Securing | for a person | Not modelled; lashing goes to a competent person. |
| S5 | 4.7 | Handling | for a person | Not modelled; goes to logistics. |
| S6 | 4.7 | Crate structure | for a person | 24 of 24 crates are pending detailed design (待详设). |
| S7 | 4.11 | Delivery sequence | for a person | Not modelled; goes to the project manager. |

On the re-run the demo prints what changed since the previous run: containers used 6 → 8, pieces 24 → 30, cargo 10 800 → 13 920 kg net, heaviest cargo 2 582.8 → 2 862.8 kg and heaviest gross 6 472.8 → 6 752.8 kg. Statements S2, S3, S6 and S7 need re-confirmation, and S1, S4 and S5 were re-derived with the same figures. S7 changed because the rows in each container moved. The earlier Word copies `bidbook.en.docx` and `tender-packing-link.docx` "still hold the previous statements - do not send them"; the re-run wrote `bidbook.en-2.docx` and `tender-packing-link-2.docx` beside them.

The check `tender-packing-link` (18 tests, "Ran 18 tests … OK" on `a161251`) pins this behaviour and the tenders that must not read as covered (§1, "For an evaluation and safety review"). On `main` before the pull request, the first 14 of these tests failed (3 failures, 11 errors).

**What the link does not do yet.**

- A-frame stillages, upright transport and no stacking, lashing to the CTU Code, and delivery sequencing are not modelled, so they stay `[TO CONFIRM]`. The container count rests on the planner's own crates, not on stillages.
- The gross mass is the engine's per-container cargo plus an approximate knowledge-base tare (40HQ: 3 890 kg). Dunnage, lashing material and stillage mass are excluded, and the signed VGM governs.
- The plan does not follow the installation sequence: in rev B the L8 panels are loaded in container 1, and some containers mix floors.
- The link runs from a `civil` or workbench turn only, not over MCP or a gateway route. The gateway's older `/api/tender/delivery` still packs through a different path: with no materials it plans canned sample materials, labelled `materials_source = "sample"`, and it can return a plan in another container type than asked. Its matrix leaves that row to a person.
- The English bid-book's body says DRAFT and uses the fictional "Harbourline Facade Pte. Ltd. (DEMO)". Since PR #67 it carries a SYNTHETIC banner when the tender marks itself SYNTHETIC, takes its title from the ITT's own heading, and prints chapter 3, chapter 6 and Annexes A and B in English (0 of 230 lines in Chinese on the demo, was 25 of 227). Its section 4b still says no scoring points were extracted although the parse finds 4.
- The record has no step yet in which a person marks a statement confirmed; `confirmed_by_person` stays false.
- The link has not been run in model mode.

**The rest of the façade demo: tender review, packing, site paperwork.** Flows 2 to 4 of the same script (from pull request #63) run the three posts on their own. Site paperwork is a secondary capability: it is not the partner's problem, but it shows the typed licensed sign-off. On `a161251` the script printed:

- **Tender.** The parse lists the CR16 workhead, 420 calendar days, 90-day validity, the 10% performance bond, the 12-month defects-liability period, "alternative tenders not permitted", the 4 PQM weightings and 3 rejection clauses, and leaves 10 rows 未在原文检出 ("not found in the text") for a person. From its handoff, bid-tech drafts 4 chapters with 13 `[A001]` cells, and bid-compliance lists 8 requirements with no response and 3 rejection clauses to tick.
- **Packing.** 24 panels become 24 crates in 6 × 40HQ (N0 = 6, `can_fit` true, space 0.4387, weight 0.0903). Pieces 24 → 24 and 10 800 → 10 800 kg; all 24 crate structure checks read 待详设 ("pending detailed design").
- **Site documents.** The daily report copies the day's facts (date, weather, location, progress, attendance, plant, safety notes, next day's plan) into their rows and leaves the preparer and reviewer blank. The high-risk work-at-height briefing writes nothing, and the script never types the sentence.

The script also prints what these flows do not do yet. The tender parse shows 0 of the ITT's 12 façade specification clauses (mock-ups, heat soak, site water test, PE endorsement, warranty, A-frame delivery, the four logistics clauses and insurance) and no liquidated-damages or retention row; the four logistics clauses are read by the link instead. The packing plan is identical with Chinese notes, English notes and none, and A-frame stillages are not modelled. The folder's README adds that, once signed, the briefing body is generic, and that the ITT carries the same clauses as the ITT of our 26 September study plus the four logistics clauses, so any score on it is a development figure, not a held-out one.

**English requests.** Pull request #63 routes English requests in steps mode. On `heldout_en2`, 28 English sentences written after the English rules were frozen, `route_task` scores 0.786 accuracy with 0 false runs on `a161251`, and all 6 misses fall back to chat (`scripts/test_english_intents.py --score`). The two other English sets are not blind, so we do not quote them. The Chinese held-out 2 still scores 1.000 with 0 false runs. An English sentence still places almost nothing in a site-post draft, which is why the site inputs are in Chinese; the linked run itself is asked in English. PR #67 widens which requests reach the link (a named tender and panel list plus a check / match / comply / clauses word). On a DEV set of 53 requests, written before that change and then used to build it (so not a held-out figure), link requests reaching the link went from 7 of 26 to 26 of 26 with none wrongly linked, and `heldout_en2` stays 0.786.

## 4. Safety and governance

### 4.1 Human sign-off: the model proposes, code writes

The 19 high-risk posts cover structure, geotechnical, bridge, tunnel, fire protection, construction method and hazards, safety briefs, quality, survey, lab work and others. For these posts, `decide_gate` returns `hitl` until a person supplies the sentence. That check runs before the auto-approve setting, so no configuration removes it.

Inside civil-buddy's own model loop the model cannot approve: its tools have no confirm field, and if it copies the sentence, the sentence is replaced (`model_loop.py:785`). Since pull request #61 an external MCP host's model cannot approve either: the Python MCP server neither offers nor accepts an approval field (§4.3; the undeployed Rust `civil-mcp` still does, §4.4). `run_skill` also gives the writer only the user's words and the named files.

In tender review, a model analysis must be JSON that cites source ids and uses only numbers from the cited quotes. It is rejected if it says 可以投标 ("can bid"), 已通过审查 ("passed review") or 已确认合格 ("confirmed compliant") (`tender_workflow.py:117-146`).

No person is named on a sign-off yet; that comes with accounts, `BEFORE DEMO`.

### 4.2 Guards, policy and sandboxing

**Number provenance** (`tools/number_provenance.py`) flags any quantity or clause number that is missing from the turn's evidence: the user's text, tool results, knowledge-base excerpts and the files read. Exact matches count as evidence, and so do rounding, percent/decimal swaps and unit scaling. The check asks "is there a source?", not "is it right?".

**The verdict guard** (`tools/verdict_guard.py`) flags verdicts the product may never state: can bid, can start work, can book, passed review, complies with the tender. It ignores them when negated, asked or conditional.

In model mode, a flagged reply gets one rewrite. After that, untraced numbers are listed to the user and verdicts are struck. `civil review <document>` runs both guards with no model.

**Policy** is plain, tested Python. Since pull request #61 it also runs for pack-ship over MCP and the gateway's tool route, through the engine's admission step. We still do not say "every call": in the opt-in model loop some tools call their function directly, the gateway's own packing routes (`/api/pipeline`, `/api/table/parse`, `/api/export/*`) call the packing engine directly with their own path checks, and the undeployed Rust `civil-mcp` binary has its own tool path. `scripts/demo_agent_middleware.py` shows four beats. **Allow** lets a permitted call run. **Deny** stops bid-parse from calling `pack-ship__plan`. **Degrade** retries, then falls back to `UNSPECIFIED`. **Circuit** trips the cost fuse, which is attached only in that script. The run ends with `submit_blocked = true`, and its attempt to write a `.env` file is refused with a secret-file reason (`deny_secret`).

**Sandbox** (`OPT-IN`). With `sandbox_backend = os` or `auto`, a CLI or desktop turn in a job folder runs in a confined worker that is discarded when the turn ends. In steps mode, the whole pipeline runs there. In model mode, the file and write tools run there, while the model conversation stays in the host (it needs network). If `os` is requested and cannot start, the turn is refused (`runtime/turn.py:66-127`).

On our Windows test machine the probe enforces writes and spawn but not network; Linux Landlock and seccomp were not exercised. The hosted workbench bypasses the worker, and the worker inherits the host's keys. This is desktop hardening, not a server control.

### 4.3 The security baseline (shipped in PR #61)

Pull request #61 was merged as `d3ada11` on 2026-09-26 to make a public deployment defensible. It added three gate checks (`human-approval`, `access-guard`, `pack-ship-read-sandbox`) and extended `http-confirmation`. What changed:

1. **Approval only from a person, per turn.** MCP never advertises or accepts `confirm_ok` / `p0_confirmed`; a high-risk post over MCP returns `approval_required` and writes nothing. The gateway's routes and `civil serve` accept only `confirm_text` equal to the sentence, and a `true` boolean is refused (a 422 on the gateway). The workbench approves only on `confirm_text` or the sentence typed in the message. In model mode the approval no longer lasts the whole session: `memory.py` keeps it for this turn only. MCP `civil.turn` runs only posts inside the server's `--expert` / `--pack` launch scope.
2. **Only a token holder, or this machine, gets in.** `packing_assistant/access_guard.py` sits in front of both apps, WebSockets included. With `CIVIL_TOKEN` set, the token is required from everyone, loopback included, compared with `hmac.compare_digest`; `?token=` is swapped once for an HttpOnly, SameSite=Strict cookie. With no token, only a genuinely local request passes. `demo/serve.py`, a `uvicorn --host` start and the container refuse a non-loopback bind without a token; `CIVIL_ALLOW_OPEN_LAN=1` is the one explicit opt-out. The gateway's CORS no longer allows credentials.
3. **Settings key lock.** Changing the model `base_url` requires the key again.
4. **Pack-ship reads stay in the sandbox.** Policy checks `file_path` for every tool, and pack-ship over MCP and the gateway's tool route goes through `ToolEngine.admit` (contract, then policy). Gateway `/api/table/parse` confines `path=` to the sandbox roots whenever a token is set or the request is not from this machine (a token-less local request may still name any table it can read), and `/api/run-pdf` takes a file name.
5. **Smaller gateway fixes.** `/api/artifact` serves only the output folders, and the TMS mode comes only from the server environment, not the request body.

Three design choices are worth knowing.

- **Admit, not execute, for MCP and gateway pack-ship.** `execute` counts failures per tool and opens a process-wide circuit after three. Pack-ship returns `ok = false` for every list with a needs-human row, so on a shared server three such lists from one employee would switch pack-ship off for every employee. `admit` gives the same contract and policy check without that shared latch (`tool_engine.py:174-222`, `circuit=False`).
- **`tool_contracts` keeps `confirm_ok` and `p0_confirmed`.** The product's own steps path passes them to `engine.execute` after `decide_gate` has checked the typed sentence (`agent_loop.py:298, 317, 339`). Removing them from the shared contract would break that path. The cut is at the MCP and HTTP boundary, where a program is the caller.
- **The token is required from loopback too.** A reverse proxy on the same host makes every request arrive from 127.0.0.1, so trusting loopback while a token is set would let the internet in through the proxy. A "behind a proxy" flag was rejected because it fails open when forgotten.

### 4.4 Still open

Left open by pull request #61, and stated in it:

- **Rust tools.** The Rust `civil-mcp` binary and the Rust workbench still accept `confirm_ok` (`workbench/src/mcp.rs:173-176`; `workbench/src/api.rs:1292`). Neither is part of the deployed surface, although old Rust-workbench trial builds are on GitHub Releases.
- **Pack-ship circuit on the steps path.** The steps path still latches `pack-ship__plan`'s process-wide circuit after three needs-human lists in a row. Only a successful run resets the count, and none can run while the circuit is open, so on a shared server the workbench's packing post stays off for everyone until a restart (`tool_engine.py:354`; `policy.py:150`). This predates the baseline.
- **Run routes.** `/api/runs/compare` and the `{run_id}` routes join request values onto `RUNS_DIR` (`gateway/app.py:576, 2705-2711`). They are behind the token now.
- **Token-less proxying.** With no token, a proxy that rewrites `Host` and sends no forwarding header over HTTP/1.1 would look local. A proxied deployment must therefore set `CIVIL_TOKEN` (§6.2).

Older items that are still true:

- **Sandbox worker.** It inherits the host environment, keys included (`os_sandbox/__init__.py:116`), and its network is open on Windows.
- **Cost and telemetry.** The `SessionLedger` fuse is wired only in a demo script. OTel is off and not installed by default.
- **Audit.** The engine's audit log is in memory only, and tool events are not persisted. A failed tool on the model path still shows "done".
- **Identity.** No approver is named until accounts land (`BEFORE DEMO`).
- **Gateway packing pause.** `enable_auto_confirm` defaults to true for API callers (`gateway/app.py:1391`). This is the packing-plan pause, not the licensed-person gate.
- **Fetch and injection.** The URL fetch resolves DNS twice. There is no prompt-injection detector or test set.
- **Chat.** In the workbench, question-only turns call the model whenever a key is set, whatever `agent_mode` says.

## 5. Evaluation

### 5.1 How we measure

Every figure comes from a script in the repository, runs offline with no key, and exercises the deterministic path. Each set carries one of three labels:

- **Held-out:** written after the rules were frozen and not yet used to change them.
- **Dev:** used while building the rules.
- **Seen:** once held-out, since used to fix rules, and now a regression floor.

Model behaviour is outside the gate. Recorded live-model observations (`qwen2.5:3b`, `docs/civil-buddy/civil-codex-eval-2026-09-19.md`) are archived and were not rerun here.

### 5.2 Results

We re-ran every row on `d3ada11`. Apart from timings, the output is identical to our run on the previous baseline, `be18c6a`. Pull requests #60 and #61 changed none of these benchmark scripts (`git diff be18c6a d3ada11 --stat`); the one scoring script they touched, `eval_post_scorecard.py`, passes 35 of 35 posts.

| What is measured | Set | Result at `d3ada11` |
|---|---|---|
| Task-intent router: right post or workflow; no write from a question | held-out 2: 18 sentences, team-written | Accuracy 1.000, request recall 1.000, 0 false runs. The core rules before the change scored 0.556 accuracy and 0.111 recall. |
| Verdict guard: flag stated verdicts, not negated, questioned or conditional ones | held-out 2: 20 sentences, 9 must-flag | P 0.900, R 1.000 (tp/fp/fn 9/1/0). The v1 phrase list scored P 0.667, R 0.222. Small sample. |
| Number-provenance guard | dev: 37 drafts, 17 of them with 35 must-flag numbers and 20 clean | P 1.000, R 1.000 (35/0/0). Exact match alone scored P 0.729. |
| Tender requirement ↔ bid response | dev: 19 cases, 51 links, 14 conflicts | Links P 1.000, R 0.980 (50/0/1), F1 0.990. Conflicts 14/14, P 1.000. Per the benchmark README, the rules were extended after 5 cases were added (recall 0.765 → 0.980). |
| Synthetic tenders; bid check | 4 synthetic tenders read as 8 documents (`cn_construction` and `cn_municipal` as DOCX, PDF and OCR; `cn_consultation`; `cn_selection`) and 2 bid sets, dev or seen | `cn_construction`: fields 18/18, rejections 18/18. `cn_municipal`: 17/17 and 22/22 (OCR 21/22). Bid check: 5/5 and 6/6 defects, 0 false alarms. English fields: `en_itt` 16/16, `en_bds` 12/12. |
| Post content: are stated facts in the right field? | dev: 65 posts, 193 cases, 1 529 facts | 350 placed (0.229 micro, 0.241 macro); 112 misplaced, 309 dumped, 175 echoed, 583 missing, 0 forbidden. 4 posts at 1.00, 5 at 0.00. The bid posts' former held-out sets, now seen, rerun at 78/78, 70/70, 84/84 and 85/86. |
| Cargo conservation | 50 fixtures, 3 571 pieces | Pieces in = out on all 50. Mass is equal on 48; on the other 2 (`syn_overweight_risk`, `over_payload_monster`) the plan is 0.18 kg and 0.33 kg lighter, because a row's mass is split across crates. Seven fixtures split mass, and five of them still balance exactly. Long-frame list `case_b`: 9 crates, 3 × 40HQ, N0 2, `can_fit` true, 23 800 kg in = out, crate structure 9 pass / 0 fail. |
| Packing fan-out, 16 lanes × 8 rounds | fixture | 128/128 completed: `can_fit` is true on 71 runs and false on 57; 44 runs end in phase `done` and 84 in `need_revision`. 0 of 128 differ from the 2026-09-02 archive. Completion does not mean the cargo fits. |
| Acceptance, no-key demo, middleware | fixture | 12/12 PASS; `demo_one_shot.py --all` passed (exit 0); the four-beat middleware test passed. |

### 5.3 The gate: 146 of 146 in CI at `a161251`

`npm run check` runs 146 default checks at `a161251` (`scripts/check_project.py --list` prints 152 lines: 146 default and 6 `--full`). On GitHub CI all 146 passed for pull request #65 (run 36230427585, on the PR head `02620a3`) and again on `main` at `a161251` (run 36231052332). CI now has four jobs, all of which succeeded in both runs: smoke (the gate), rust (`cargo test`, 156 tests passed on `a161251`), packing-eval-slice, and `docker-smoke`, added by pull request #64 (§6.1). The one check #65 added is `tender-packing-link` (§3.4). Locally on `a161251` the gate gives 145 of 146; the one failure is `ui-dom`, for the reason below.

**At the benchmark baseline `d3ada11`: 142 checks.** `npm run check` ran 142 default checks. On GitHub CI all 142 passed for pull request #61 (run 36170078042, on the PR head `fcd1c01`, whose tree is identical to `d3ada11`) and again on `main` at `d3ada11` (run 36171221472). All three CI jobs (smoke, rust, packing-eval-slice) succeeded in both runs, and the rust job's `cargo test` passed 156 tests on `d3ada11`.

Locally, `ui-dom` fails until `npm ci` has installed `node_modules`, because it needs `jsdom`; CI installs them (`ci.yml:47`). On our machine we re-ran the checks that #60 and #61 touched or added (`planning-chat-ui`, `post-scorecard`, `access-guard`, `human-approval`, `pack-ship-read-sandbox`, `http-confirmation`), and all six pass. The 5 Python checks that run only with `--full` also pass, including `pytest demo/tests` (76 passed, 9 skipped).

**Between the two: 145 checks.** Pull request #63 (merged 2026-09-26 as `916971d`) added three checks. `facade-tender` pins the tender-matrix fix: short Latin codes match whole words, so "CTU" in "structural" is no longer a lashing clause; a packing run is evidence only for the clauses it answers, so a clause on unmodelled handling (A-frame, stillage, upright, no stacking, fragile) or naming another container type goes to a person ("Pending SME") instead of "No Deviation", and a lashing or CTU clause is never covered by the run (at most "Partial Deviation"); and CR16 reaches the handoff. `english-intents` pins English routing, with floors on the three English held-out sets. `facade-demo` runs the flows of §3.4. All 145 checks passed on CI for the pull request (run 36222056643, on the PR head `46b6ac1`, whose tree is identical to `916971d`) and again on `main` at `916971d` (run 36222613544), and all three CI jobs succeeded in both runs. Locally on `916971d`, the three new checks, `task-intent-bench` and `tender-delivery-api`, which #63 also changed, pass (5 of 5). Pull request #64 (merged as `16316df`) added the `docker-smoke` job and no gate check; all four jobs passed on `main` at `16316df` (run 36228491164).

`main` was red from 2026-09-22 until pull request #60; at `be18c6a` CI stopped at 137 of 139 (run 35688428017), and our local run there had 136. #60 fixed both failures, neither a product regression: `planning-chat-ui` now loads the ES modules that the #56 split made of `demo/static/app.js` (the same 9 tests and assertions), and `post-scorecard`'s bid-parse gate asks for the section `ad3d079` renamed to SKILL.md's "7 专项触发", with unchanged strictness.

### 5.4 What we do not claim

We do not claim the following:

- **Real tender PDFs.** First-run fields of 35/67, 52/70 and 29/39, and 0/13 on the first real English tender. The source files are outside the repository, so these results are only archived (`test/benchmarks/real_tender/README.md`).
- **A customer shipment.** The 446-tonne result (29 → 25 containers).
- **Earlier held-out scores.** First-run scores on sets now seen, such as 0.923 / 0.957 / 0.988 / 0.791.
- **"L2 66/66".** No command produces it.
- **Live models.** Any live-model or Bedrock result.

### 5.5 How to reproduce

From the repository root, with Python 3.11, `pip install -r requirements.txt` and no key; only `npm ci` needs the network. Appendix A lists the remaining commands.

```
python scripts/check_project.py --list           # a161251: 152 lines = 146 default + 6 --full
npm ci ; npm run check                           # the release gate (ui-dom needs npm ci)
python scripts/eval_task_intent.py --check       # heldout2 1.000/0
python scripts/eval_verdicts.py --check          # heldout2 0.900/1.000
python scripts/eval_number_provenance.py --check
python scripts/eval_tender_response_match.py --check
python scripts/eval_post_content.py              # --set heldout_bid4 etc.
python scripts/test_real_tender.py ; python scripts/eval_real_bid_check.py --set bid_cn_municipal
python scripts/test_pack_ship_conservation.py --numbers
python scripts/fanout16x8_online_cargo.py --skip-fetch   # ~3 min, 16 workers
python scripts/test_acceptance_cases.py ; python scripts/demo_one_shot.py --all
python scripts/demo_facade.py                    # §3.4: flow 1 is the linked run
python scripts/test_tender_packing_link.py       # the linked run: 18 tests
python scripts/test_english_intents.py --score   # heldout_en2 0.786, 0 false runs
bash scripts/docker_smoke.sh <image>             # §6.1: needs Docker (CI job docker-smoke)
```

## 6. Deployment on AWS

### 6.1 Target and status

The enterprise edition is **one AWS Lightsail Linux instance per company**, used by employees in a browser. The instance is not running: no cloud host has run the image yet. We plan to set it up before the submission and to give the deployment URL in the submission email. What the server needs from the code is on `main`: the Docker build for the packing gateway (`Dockerfile`, `docker-compose.yml` with a health check on `/api/health`), the access guard, and the operator guide `docs/deploy-minimal.md`.

**What CI proves about the image.** Before pull request #64 (merged as `16316df`), an image built from `main` exited at startup because a contract file was not copied in, and nothing in CI built it. #64 copies the tree behind a strict `.dockerignore`, puts the SQLite database inside the data volume (`CB_DB_PATH=/app/output/db/civilbuddy.db`), and adds the CI job `docker-smoke`. On `main` at `a161251` (run 36231052332) the job built the image (430 MB in `docker image ls`) and `scripts/docker_smoke.sh` printed:

- with no `CIVIL_TOKEN`, the container refused to start (exit 3);
- cold start, from `docker run` to the first `/api/health` 200: 1 739 ms, and 1 941 ms after a re-create;
- `GET /api/tools`: 401 with no token, 401 with a wrong token, 200 with the token; `POST /api/tender/parse`: 401 with no token;
- the synthetic ITT parsed through the API: 12 requirements, 12 response-matrix rows;
- a session written through `/api/demo` was in the database's sessions table, and it survived a container re-create on the same volume.

The same job then ran `docker compose` itself: it refused to start without `CIVIL_TOKEN`, answered 401 without the token and 200 with it, and kept the session across `docker compose down` and `up`. `/api/health` stays public on purpose, because the compose health check and load balancers probe it without a token. That is all the `LIVE` tag on the image means: it builds, starts, guards and keeps its data in CI. The run made no model call, and the image starts the gateway only, not the workbench.

| Layer | Design | Status |
|---|---|---|
| TLS on 443 | Caddy or nginx with a certificate, proxying to the apps on 127.0.0.1 | `BEFORE DEMO`. The guide gives the nginx settings; no proxy config file ships. |
| Applications | Gateway container published on `127.0.0.1:8000` only; optionally the workbench from the repository on `127.0.0.1:8765`, with the same token, under its own host name behind the proxy (§6.2 item 7). The image starts only the gateway. | Image build, start and fail-closed start `LIVE` in CI (`docker-smoke`): without `CIVIL_TOKEN`, the container, `demo/serve.py` and `uvicorn --host` on a non-loopback address refuse to start. On a server: `BEFORE DEMO`. |
| Access | One `CIVIL_TOKEN` for both apps, required on every API route and WebSocket (only page shells, static files and `/api/health` are open); then three roles | Token and guard `LIVE`; roles `BEFORE DEMO` |
| Model key and egress | Key held in the server environment, never sent to the browser; a new base URL needs the key again. Egress only to the model endpoint and public URLs the user gives. | `LIVE` |
| Data | Job folders and SQLite (WAL) in the container's data volume (`/app/output/db/civilbuddy.db`); gateway backups, keeping 7 | Database in the volume and backups `LIVE` (a session survives a re-create in CI); Lightsail snapshots `ROADMAP` |

### 6.2 Operator settings for the server

`docs/deploy-minimal.md` sets these rules for the Lightsail server. The guide also covers Render, Railway and a generic Linux host; this document describes only the Lightsail setup.

1. **Token.** `CIVIL_TOKEN` is mandatory: a long random value, kept only in the server's `.env`, never in the repository. Both apps use the same token, because their cookie has one name and no port scope, so two tokens would clear each other's cookie. `docker compose` refuses to start without it. To change the token, change the value and restart; old cookies then get a 401 and are cleared.
2. **No open LAN.** Never set `CIVIL_ALLOW_OPEN_LAN` on a server. It is the one opt-out that lets a token-less app accept requests from other machines.
3. **Only 80 and 443.** The shipped `docker-compose.yml` publishes `8000:8000` for a laptop; on the server it becomes `127.0.0.1:8000:8000`, and the Lightsail firewall opens 80 and 443, not 8000.
4. **Proxy.** Caddy or nginx terminates TLS and proxies to `127.0.0.1:8000`. For nginx the guide sets HTTP/1.1, `Host`, `X-Forwarded-For`, `X-Forwarded-Proto` and the WebSocket upgrade headers. A request with a forwarding header, or over HTTP/1.0, counts as remote, and the token is required from loopback too, so no proxy configuration bypasses it. `X-Forwarded-Proto: https` makes the cookie `Secure`. Do not set uvicorn's `FORWARDED_ALLOW_IPS` to `*`.
5. **Domain root.** Serve the app at the domain root (`location /`). Under a sub-path, the `?token=` redirect returns to the domain root.
6. **First visit.** Each employee opens `https://<domain>/?token=<token>` once. The server answers 303 to the same address without the token and sets an HttpOnly, SameSite=Strict cookie. That link appears once in the proxy log and the browser history, so it should not be forwarded. Scripts send `Authorization: Bearer <token>`.
7. **Workbench.** To serve the workbench as well, run `CIVIL_TOKEN=<same token> python demo/serve.py` (it binds 127.0.0.1:8765 by default) and give it its own host name at that name's root in the proxy (for example `wb.<domain>` → `127.0.0.1:8765`). The guide's nginx block covers only the gateway.
8. **Secrets and TMS.** Keys live only in environment variables, never as files in the repository root or `output/`; `/api/artifact` reads only `output/`, `PACKING_OUTPUT_DIR` and the runs folder. Leave `PACKING_TMS_MODE` unset, which keeps the TMS a stub.

**Why the token is mandatory behind a proxy.** With no token, a proxy that rewrites `Host` to 127.0.0.1 and sends no forwarding header over HTTP/1.1, or any TCP port forward (socat, `netsh portproxy`, `ssh -R`), makes a remote request look local. A token-less instance must never be exposed that way.

### 6.3 Model endpoints

| Endpoint | Configuration | Status |
|---|---|---|
| None (default) | No key: every post runs deterministically. | `LIVE` |
| OpenRouter, DeepSeek or any compatible API | `CIVIL_API_BASE`, `CIVIL_API_KEY` and `CIVIL_MODEL`, or Settings → Model in the workbench (held in memory only). | `OPT-IN` |
| Amazon Bedrock Chat Completions | Base URL `https://bedrock-runtime.<region>.amazonaws.com/openai/v1`, with a Bedrock API key as the bearer token, in the same setting. For Singapore (`ap-southeast-1`), AWS's documentation, checked on 2026-09-26, lists the `bedrock-runtime` endpoint as supported and the `bedrock-mantle` endpoint as not supported. Its model table does not list for Singapore the gpt-oss models that AWS's Chat Completions examples use. So it is not established that Bedrock's OpenAI-compatible Chat Completions works from Singapore with a model we could use. | `BEFORE DEMO`. **Configurable, never run** from this repository. A verification run is planned before the demo. |
| Local Ollama | `http://127.0.0.1:11434/v1` with any non-empty key. On Lightsail it would share the instance, so it suits laptop and on-premises editions. | `OPT-IN` |

### 6.4 Settings reference

| Setting | Default | Effect |
|---|---|---|
| `CIVIL_TOKEN` | empty | One token for the workbench and the gateway (`Authorization: Bearer`, or the cookie set by `?token=`). Empty: only genuinely local requests are served. |
| `CIVIL_ALLOW_OPEN_LAN` | unset | `1` lets a token-less app accept requests from other machines. Never on a server. |
| `CIVIL_HOST` / `CIVIL_PORT` | `127.0.0.1` / `8765` | Workbench bind address and port. A non-loopback host without `CIVIL_TOKEN` refuses to start. |
| `CIVIL_API_KEY` / `CIVIL_API_BASE` / `CIVIL_MODEL` | unset | Model endpoint; no key, no model |
| `CIVIL_MODEL_MAX_TOKENS` | `1500` | Output cap per model call |
| `CIVIL_AGENT_MODE` | `steps` | `model` or `auto` enables the model loop |
| `CIVIL_SANDBOX` / `CIVIL_APPROVAL` / `CIVIL_SANDBOX_BACKEND` | `workspace-write` / `on-request` / `app` | `read-only` freezes writes. No approval setting lifts the high-risk sentence. `os` or `auto` enables the confined worker. |
| `PACKING_TMS_MODE` | unset (stub) | Only the server environment selects a live TMS; a request body cannot. |
| `CIVIL_JOB_ROOT` · `CB_STORAGE` · `PORT` · `PACKING_OTEL` | unset · `sqlite` · `8000` · `0` | Job root · storage · gateway port · OTel |

## 7. Limitations and roadmap

| Item | Today (`a161251`) | When |
|---|---|---|
| Lightsail instance behind TLS | Not running; the image is built and started only in CI, and the operator guide is on `main` (§6.1, §6.2) | `BEFORE DEMO`; planned before the submission, URL to follow in the submission email |
| Accounts and roles (admin, engineer, licensed approver), with each sign-off naming its approver | One shared token; operator recorded as "local user" | `BEFORE DEMO` (by 2026-10-10) |
| A person confirms each linked statement in the record | `confirmed_by_person` is always false; confirmation happens outside the product | `ROADMAP` |
| Bedrock verification run | Configurable, never run from this repository; whether it works from Singapore is not yet known (§6.3) | `BEFORE DEMO` |
| Durable audit log of tool calls and approvals | A `workbench.json` per run; the engine's audit is in memory only | `ROADMAP` |
| Metering and budgets (usage per turn, session budget) | Per-turn caps and model-call counts only | `ROADMAP` |
| Admin console and kill switch | Cancel per run or session; read-only config | `ROADMAP` |
| OIDC single sign-on | None | `ROADMAP` |
| PII masking before cloud calls | None; a local Ollama keeps files on the host | `ROADMAP` |
| Per-user sessions, single egress, IM approvals, LLM judge | Not built | `ROADMAP` |

**Known limits, not yet scheduled.**

- **Security.** The items in §4.4, including the Rust `civil-mcp` binary and Rust workbench, which still accept an approval flag and must not be shipped as safe.
- **Quality.** Most non-bid post writers place fewer than half of the stated facts.
- **Tender ↔ packing link.** A-frame stillages, upright transport, lashing to the CTU Code and delivery sequencing are not modelled; the gross mass leaves out dunnage and lashing and uses an approximate tare; the link is not on MCP or a gateway route, and the gateway's older `/api/tender/delivery` can plan sample materials in another type (§3.4).
- **Packing.** A plan uses one container type (`container_mix_supported = false`). The report calls N0 a booking lower bound, but on small-carton lists it includes a geometric estimate and can exceed the containers used; that label needs correcting.
- **Input formats.** PDF packing lists are read only in known layouts, and scans need OCR.
- **Language.** The UI (`lang="zh-CN"`) and most post templates are in Chinese. The sign-off sentence has one exact English equivalent ("I understand; a licensed person will sign this off."), and English requests get English link replies, approval notes and guard notices (PR #67). English tender parsing is reproducibly measured only on synthetic sets. The one archived real English tender scored 0/13 fields on its first run.

## Appendix A. Evidence

**Environment.** Windows 11 with Python 3.11.5 and Node v24.19.0. Model keys were unset and `PYTHON_DOTENV_DISABLED=1` was set; the fan-out ran with `--skip-fetch`. No tracked file changed. `node_modules` was not installed, so `ui-dom` was not run locally. Release-gate results are GitHub CI's.

**Two checkouts.** The benchmark rows of §5.2, the measured flows of §3.1–§3.3 and rows 4–7 and 10 below were run on `d3ada11` (`main`, 2026-09-26). Everything in §3.4, the gate and CI runs of §5.3 at `a161251`, the Docker results of §6.1 and rows 15–17 below were run on `a161251` (`main` after pull requests #64 and #65, 2026-09-26), and the figures of pull request #63 on `916971d` (row 13). Every `file:line` reference in this document, including the criteria map, was checked against `a161251`; where a line moved after `d3ada11`, the reference gives its `a161251` line.

**Harness.** Rows marked *harness* were run by short scripts outside the repository that call the repository's own entry points: `run_agent`, `packing_assistant.civil.main`, `run_plan`, and FastAPI `TestClient`.

| # | Claim | Source |
|---|---|---|
| 1 | 66 posts, 16 categories, 19 high-risk, 0 disabled | `workbench/seed.json` (via `demo/catalog_seed.py`); `risk:` in `.agents/skills/*/SKILL.md` |
| 2 | Sign-off sentence; high-risk check before auto-approve | `runtime/civil_config.py:16, 160-190` |
| 3 | 346 post documents; 436 indexed files | `git ls-files 'demo/kb/*.md'`; `kb_search.py:105-119` |
| 4 | Gate: 142 checks (+6 `--full`), keys stripped; 142/142 on CI; three CI jobs; rust job 156 tests passed; locally the six checks #60/#61 touched or added pass, `ui-dom` needs `npm ci`; `--full` Python 5/5 (pytest 76 passed, 9 skipped) | `scripts/check_project.py:25-184, 187-194` (`--list`; `--only <name>`; `--full --only <name>`); `.github/workflows/ci.yml:3-7, 47, 147-148`; GitHub Actions run 36170078042 (PR #61, head `fcd1c01`, tree identical to `d3ada11`) and run 36171221472 (push to `main` at `d3ada11`) |
| 5 | §5.2 benchmark rows, re-run on `d3ada11` | `scripts/eval_task_intent.py`, `eval_verdicts.py`, `eval_number_provenance.py`, `eval_tender_response_match.py` (each `--check`); `test_real_tender.py`; `eval_real_bid_check.py --set`; `test_english_itt.py`; `eval_post_content.py [--set heldout_bid{,2,3,4}]`; `eval_post_scorecard.py --all-pilots`; `git diff be18c6a d3ada11 --stat` |
| 6 | §5.2 packing rows, re-run on `d3ada11` | `scripts/test_pack_ship_conservation.py --numbers`; `run_plan` on `test/benchmarks/excel/case_b_long_frames_40hq.xlsx`; `fanout16x8_online_cargo.py --skip-fetch` (`output/fanout16x8/rollup.json`: `can_fit` and `phase` per run) vs `docs/eval/fanout16x8-2026-09-02/` |
| 7 | Demos, acceptance, middleware | `main.py --demo`; `test_acceptance_cases.py`; `demo_one_shot.py --all`; `demo_agent_middleware.py` |
| 8 | Model-loop menu and caps; fuse; secret-file refusal | `model_loop.py:40, 53-80, 640-660`; `model_client.py:43`; `middleware.py:119`; `tool_engine.py:84`; `policy.py:23, 178, 194` (`deny_secret`) |
| 9 | 82 tools at `a161251`, 71 of them write (81 at `d3ada11`; #65 added the read-only `tender.packing_link`), 9 deny codes, MCP scopes and transport (8, 9, 13 and 11 tools, unchanged at `a161251`), sandbox probe | `tool_engine.default_engine()` (`len(engine.tools)`, `spec.writes`); `policy.py:21-29`; `demo/mcp_surface.list_tools`; `test_mcp_stdio.py`; `test_pack_ship_solver_mcp.py`; `demo/mcp_stdio.py` and `ide/vscode/mcp.json`, `ide/cursor/mcp.json` (stdio launch only); `test_os_sandbox.py` |
| 10 | Flows 1–3 (§3), re-run on `d3ada11` | harness: `run_agent` on `test/benchmarks/real_tender/bid_cn_municipal.json`; `TestClient` on `gateway.app`; `civil.main(['exec', …])` |
| 11 | Security baseline and what remains open (§4.3–4.4) | `packing_assistant/access_guard.py:60-78, 101-106, 135-149, 159`; `demo/mcp_surface.py:102, 133-134, 297-333, 358, 369-389`; `gateway/app.py:110-127, 479-497, 576, 650, 767-781, 943, 1391, 1492-1509, 2705-2711, 2869, 2909`; `demo/app.py:66-67, 1126`; `demo/serve.py:23-29`; `demo/chat_service.py:349`; `demo/model_settings.py:33-42, 118-127`; `runtime/app_server.py:76-101`; `runtime/memory.py:85`; `runtime/policy.py:172-180`; `runtime/tool_engine.py:174-222`; `runtime/agent_loop.py:298, 317, 339`; `tools/pack_ship_mcp.py:310-332`; `workbench/src/mcp.rs:173-176`; `workbench/src/api.rs:1292`; `runtime/os_sandbox/__init__.py:116`; `model_loop.py:347-368`; tests `scripts/test_access_guard.py`, `test_human_approval.py`, `test_pack_ship_read_sandbox.py`, `test_http_confirmation.py` |
| 12 | Operator settings; Lightsail not yet running; Bedrock not yet exercised; Bedrock endpoints and models in Singapore | `docs/deploy-minimal.md` ("AWS Lightsail"); AWS Bedrock User Guide, accessed 2026-09-26: <https://docs.aws.amazon.com/bedrock/latest/userguide/endpoints-region-availability.html>, <https://docs.aws.amazon.com/bedrock/latest/userguide/inference-chat-completions.html>, <https://docs.aws.amazon.com/bedrock/latest/userguide/models-region-compatibility.html> |
| 13 | Pull request #63 at `916971d`: three new checks (145/145 on CI), 5 of 5 local checks; its façade demo flows and English routing were re-run on `a161251` with the same figures, except 0 of 12 (was 0 of 8) façade specification clauses | `python scripts/demo_facade.py` (exit 0) on `examples/facade-demo/`; `scripts/test_english_intents.py --score` (`test/benchmarks/task_intent/heldout_en2.json`; README in that folder); `eval_task_intent.py --check`; `check_project.py --only facade-tender,facade-demo,english-intents,task-intent-bench,tender-delivery-api`; `check_project.py --list` (151 lines); GitHub Actions run 36222056643 (PR #63, head `46b6ac1`, tree identical to `916971d`) and run 36222613544 (push to `main` at `916971d`) |
| 14 | Criteria map (after §1): typed tool contracts; per-run packing trace; the adversarial cases of the held-out sets and of the security baseline | `runtime/tool_contracts.py`; `runtime/tool_engine.py:186-194, 344-351, 506-510`; `demo/mcp_surface.py:124-136`; `packing_assistant/trace_events.py`; checks `tool-contracts` and `trace-artifacts` (`scripts/test_tool_contracts.py`, `scripts/test_trace_artifacts.py`); `test/benchmarks/task_intent/heldout2.json`; `test/benchmarks/verdicts/heldout2.json`; `scripts/test_access_guard.py`, `test_http_confirmation.py`, `test_human_approval.py`, `test_pack_ship_read_sandbox.py`. Files and line numbers are those of `a161251`. |
| 15 | The linked run (§3.4) and its checks: 5 clauses, 7 statements (1 covered, 2 partial, 0 gap, 4 for a person), first run and rev B figures, hashes, what changed; 18 tests; the tenders that must not read as covered | `python scripts/demo_facade.py --job <new folder>` on `a161251` (exit 0, "PASS demo_facade"), flow 1; `python scripts/test_tender_packing_link.py` ("Ran 18 tests … OK" on `a161251`), check `tender-packing-link`; `packing_assistant/tender_packing_link.py:124` (`logistics_clauses`), `:172` (`container_decision`), `:288-294, 340-345` (`build_checks`: never covered without a fitting plan), `:520` (`compare`), `:714, 726-729, 754-755` (`run_link`: job-folder reads, `confirmed_by_person = false`, `submit_blocked = true`); `runtime/task_router.py:127-140` (`wants_link`); `runtime/agent_loop.py:270-290` (routing, `link_inputs` and `ambiguous_container_type` stops), `:845-849` (writes through `write_deliverable`); `runtime/tool_engine.py:422-434, 495`; `runtime/tool_contracts.py:69-74`; `bidbook/sg_facade.py:220-234` (`logistics_section`, `annex_a`); `tools/pack_ship_solve.py:348` (`per_container_figures`); fixtures `examples/facade-demo/facade_itt_doc.md` (clauses 4.7–4.11), `facade_panels.xlsx`, `facade_panels_rev_b.xlsx` (`make_panels.py`). The 14 tests failing on `main` before #65 (3 failures, 11 errors) are from the pull request's own verification on `916971d` and `16316df`. |
| 16 | Docker image in CI (§6.1): build, 430 MB, refusal without a token (exit 3), cold start 1 739 ms and 1 941 ms, 401/200, 12 requirements parsed, session kept across a re-create and across `docker compose down` and `up` | GitHub Actions run 36231052332, job `docker-smoke` (push to `main` at `a161251`); `.github/workflows/ci.yml:233-282`; `scripts/docker_smoke.sh`; `Dockerfile`, `.dockerignore`, `docker-compose.yml:23`; `packing_assistant/storage.py:135-143` (`default_db_path` honours `CB_DB_PATH`); the start failure before #64 is from that pull request's verification on `916971d` |
| 17 | Gate at `a161251`: 146 checks (+6 `--full`, 152 lines); 146/146 on CI for PR #65 and on `main`; four CI jobs; rust job 156 tests passed; 145 of 146 locally (`ui-dom` needs `npm ci`) | `scripts/check_project.py --list`; `npm run check`; GitHub Actions run 36230427585 (PR #65, head `02620a3`) and run 36231052332 (push to `main` at `a161251`); run 36228491164 (push to `main` at `16316df`, 145/145) |
