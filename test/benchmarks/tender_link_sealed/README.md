# Tender <-> packing link: sealed held-out set (clauses, one ITT, injections)

**Everything here is SYNTHETIC.** The projects, companies, people, tender references (SYN-ITT-2027-03,
SYN-ITT-2027-11) and every figure were invented. None of it is a real tender, shipment or record.

## Who wrote it, when

- **Written blind on 2026-09-26 21:02 (+08:00)** by an agent that had **not read the rules**: it did not open
  `tender_packing_link.py`, `tender_document.py`, `tender_parse.py`, the parsers, the verdict guard, any of their
  tests or the audit's scratch folders. It read only `examples/facade-demo/` (README, `facade_itt_doc.md`,
  `make_panels.py`), the repo README's headings and a grep of `docs/` for container-type words. It wrote the gold
  before any code was run.
- It was kept outside the repository while PR #68 (the clause reader) was built. The builder never saw it.
- **First scored on 2026-09-27** by the reviewer of PR #68, with `score_sealed.py` in this folder, written by the
  reviewer against the gold without changing it. No rule was changed because of a failure on this set. From this
  commit on it is in the repository, so any later change can be tuned on it: **scores after 2026-09-27 are DEV
  numbers, not held-out ones.**

## How the gold was set

Gold is what a Singapore façade logistics reader (estimator / project engineer) concludes from the text alone:

- **Container decision.** One standard type named (40HQ in any spelling; 20GP as "20-foot general purpose") gives
  `plan:<type>`. Several types allowed, only a size ("40-foot"), flat rack or open top gives `person_decides`. A
  clause with only bans or no container words is `not_stated`. A ban never selects the banned type.
- **Limits** in kg. "Gross mass of the loaded container", "tare included" are `gross_container`; "payload", "cargo
  weight", "net cargo" are `cargo`. Per-stillage and per-crate limits are `per_package`. Crane lifts and vehicle GVW
  are `not_a_container_limit`.
- **References** as a reader writes them back: `4.8.1`, `4.9(a)`, `Part C Clause 12`, `Site Logistics Schedule,
  Item 3`, `Section 5, opening paragraph (unnumbered)`.
- **Never covered without plan evidence**; site hours, crane lift and GVW can never be covered, even with a plan.
- **Injections.** Each injected file is its control file plus exactly one planted text; gold = the same result as
  the control run, nothing approved, the planted text never the product's own statement.

| File | Contents |
|---|---|
| `clauses.json` | 30 clauses: 25 logistics + 5 distractors (insurance x2, warranty, LDs, retention). 3 mixed Chinese-English |
| `sealed_itt.md` | the same 30 as one ITT; its container clauses contradict each other on purpose (document decision: person decides) |
| `injections.json`, `injections/` | 8 planted texts: 4 in ITT clauses (.md), 4 in panel-list cells (.xlsx), and the two control files |

The same blind batch also held panel lists, English routing requests and verdict sentences. They score other
changes and are not in this folder.

## How it is scored (`score_sealed.py`)

Public entry points only: `logistics_clauses`, `container_decision`, `build_checks`, `run_link`, and
`pack_ship_solve.run_plan` with the demo panel list (`examples/facade-demo/facade_panels.xlsx`) for the plan the
statements are written against. Each clause is read as its own small tender, with its context line (the parent
heading, or the table caption and header) above it; only the clauses from its own text are scored. References are
scored on `sealed_itt.md` as one document. Injections run `run_link` on the injected file and on the control file and
compare every statement's kind, clause, status and plan figures; INJ05 (a note asking for 20GP in a panel list) is
run through the link like the others.

"cite right number" accepts a cite that names the gold clause number and sub-item ("Clause 12" for "Part C Clause
12"), the gold table and row ("the table under 'SITE LOGISTICS SCHEDULE', row 3"), or, for an unnumbered paragraph, no
number at all ("line 52 of sealed_itt.md"). "cite wrong" is a cite that names another clause, drops a sub-item or
invents one ("Clause L29").

## First-run scores (2026-09-27)

BEFORE = `origin/main` `cab9249`. AFTER = PR #68 head `91ea80b`, the first run on this set. The reviewer's fixes in
`16be31d` (driven by the reviewer's own probes, not by this set) left every number below unchanged.

| metric | BEFORE | AFTER |
|---|---|---|
| logistics clauses found (not silently lost) | 21/25 | 25/25 |
| distractors returned as a logistics clause | 0/5 | 1/5 (4.16 marine cargo insurance, as a "not placed" row for a person) |
| kind recall | 23/39 | 38/39 |
| per-container limit, value | 2/7 | 7/7 |
| per-container limit, value and basis | 1/7 | 6/7 |
| stillage / crate / crane / GVW limit read as a container limit | 0/4 | 0/4 |
| stillage / crate / crane / GVW limit in its own row | 0/4 | 4/4 |
| container decision | 26/30 | 29/30 |
| "covered" without plan evidence | 0 | 0 |
| sealed_itt.md: cite exact | 12/25 | 16/25 |
| sealed_itt.md: cite right number | 12/25 | 25/25 |
| sealed_itt.md: cite wrong or invented | 9/25 (+4 clauses not found) | 0/25 |
| sealed_itt.md: document decision person_decides | yes | yes |
| injections: same result as control | 8/8 | 8/8 |
| injections: planted text as the product's own text | 0 | 0 |

AFTER failures, one by one: CL07 "the weight of cargo, including dunnage and bracing, shall not exceed 24,000 kg in
any 40' high-cube container" reads the basis as unstated (compared as gross, the stricter reading), gold cargo. CL15
"Open top and flat rack containers shall not be used" gives person_decides, gold not_stated (no plan either way).
CL20 (tower crane, single lift 3,000 kg) misses the `handling` kind; the lift limit is in its own row. CL27 (marine
cargo insurance "stored in containers at port") becomes a "not placed" row. Cites: `Part C` is not prefixed to
`Clause 12`-`17`, the schedule rows read "the table under 'SITE LOGISTICS SCHEDULE', row 3", and the unnumbered
Section 5 paragraph reads "line 52 of sealed_itt.md".

BEFORE failures: CL01 basis unstated; CL02, CL07, CL11, CL14, CL24 container limits not read; CL03, CL07, CL11
decision person_decides where one type is named; CL06, CL09, CL20, CL21 stillage / crate / crane / GVW limits lost
(CL09, CL20, CL21, CL25 no row at all); CL15 person_decides; CL16 site access and CL23 plan submission missed; cites
4.9(a)-(c) as "Clause 4.9", Section 5 and Part C as "Clause L29", "Clause L37", ... and the schedule's row 3 as
"Clause 3".

## The check

`python test/benchmarks/tender_link_sealed/score_sealed.py --check` (about 10 s, deterministic: two runs give the
same JSON) is the gate check `tender-link-sealed`. It pins the AFTER column as floors (`FLOORS` in the script): no
logistics clause lost, no invented or wrong cite, no false container limit, nothing covered without plan evidence,
and all 8 injections the same as their control.
