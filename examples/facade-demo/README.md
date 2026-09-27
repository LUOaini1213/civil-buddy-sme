# Façade demo pack (SYNTHETIC)

**Every file in this folder is a synthetic software fixture.** The tender, the project, the Main
Contractor, the panels, the weights, the site day and the people are invented for testing and for the
Civil Buddy demo. None of it comes from any contractor, and none of it may be shown as a real tender,
shipment or site record. The demo is built with a Singapore curtain-wall contractor as partner; that
partner supplied no document or number in this folder.

| File | What it is | Read by |
| --- | --- | --- |
| `facade_itt_doc.md` | Document-form ITT for a unitised curtain-wall subcontract: CR16 L4 workhead, 420 calendar days, 90-day validity, 10% performance bond, S$5,000/day LDs, 12-month DLP, 5% retention, PQM weightings, and a façade specification (PMU and VMU mock-ups, heat-soak test, 5% site water test, PE-endorsed calculations and shop drawings, warranties, A-frame delivery, insurance) and five logistics clauses: 4.7 upright on steel A-frame stillages, glass faces protected, no stacking; 4.8 delivery in 40HQ containers; 4.9 gross mass of each loaded container including tare ≤ 20,000 kg; 4.10 cargo secured to the IMO/ILO/UNECE CTU Code; 4.11 deliveries sequenced to the installation programme | the linked run (`Link the tender … to the packing list …`), and `解析招标 facade_itt_doc.md` (bid-parse), then bid-tech and bid-compliance from the session hand-off |
| `facade_panels.xlsx` | 24 unitised panels, 4200 × 1500 × 250 mm, 450 kg each (10,800 kg), east elevation L5–L8, English handling notes | pack-ship (`materials` sheet; a `README` sheet repeats this notice) |
| `facade_panels_zh.xlsx` | The same panels with Chinese handling notes (玻璃 易碎 禁翻 直立运输 禁叠) | pack-ship |
| `facade_panels_rev_b.xlsx` | Revision B of the English list: level L9 added (6 more panels) and the L8 panels re-weighed at 520 kg — 30 panels, 13,920 kg | the linked run's re-run |
| `daily_report_input.txt` | One façade installation day as labelled lines: 6 panels on L5, 13 workers, a roof unit hoist, 2 gondolas (吊篮), 1 MEWP (曲臂车), a gasket defect, work above 3 m under a checked permit | pm-daily (项目日报) |
| `wah_briefing_input.txt` | Work-at-height toolbox briefing as labelled lines: panel landing and sealing L5–L8, fall height above 3 m, gondola and MEWP measures, stop conditions | safety-brief (安全交底, high risk) |
| `make_panels.py` | Regenerates the three workbooks: `python examples/facade-demo/make_panels.py [NAME ...]` | — |

The two `.txt` inputs are in Chinese labelled lines because that is what the drafting posts read
today; an English sentence places almost nothing yet. In English: *daily report* — 24 Sep 2026, fine,
east elevation level 5, 6 panels installed (6 of 24), 13 workers, one roof unit hoist, two gondolas,
one MEWP, 12 panels delivered, two level-6 panels with an exposed stack-joint gasket (photos sent to the
factory), gondola and slab-edge work above 3 m with the work-at-height permit checked that day,
tomorrow level 6 east. *Briefing* — install and seal panels on the east elevation levels 5–8, fall height
above 3 m, façade gang with the WSH coordinator watching, hazards (fall from height, falling panel,
struck by load, gondola instability), measures (check the permit, harness on an independent lifeline,
separate gondola work and safety ropes checked daily, MEWP harness on the platform anchor, edge
barriers, exclusion zone below), stop in rain, high wind, hoist limit alarm or a gondola lock fault.

## The linked run: tender and packing as one exercise

The first flow of the demo is the partner's problem: the tender response and the outbound packing ran as two
disconnected exercises, bid statements on packing were not tied to a loading plan, and container counts were not
tied to the clauses they should satisfy. One request does both:

```
civil exec "Link the tender facade_itt_doc.md to the packing list facade_panels.xlsx and write the logistics response"
civil exec "按招标 facade_itt_doc.md 和装箱单 facade_panels.xlsx 出投标物流应答"      # the same, in Chinese
```

It reads the ITT's logistics clauses, takes the container type from Clause 4.8 (40HQ; a tender that names none
gets 40HQ by default and says so; a type the planner cannot model, several types, a size with no type such as
"40-foot", or a type in a sentence that also says *not* gets no plan and goes to a person, who can name the type in
the request, e.g. `... write the logistics response in 40HQ`; a plan that does not fit states no type, count or
mass), plans the
panel list through the same `run_plan` path as the packing flow (conservation check, needs-human gates) and writes,
in `.civil-buddy/out/<session>/bid-parse/`:

- `tender-packing-link.md` — the clauses, the logistics matrix (status, owner, plan figure per statement) and what
  changed since the previous run;
- `bidbook.en.md` (and a Word copy) — the English bid-book, its chapter 6 written from the plan: each statement cites
  its clause and plan figure; qualifications and price stay `[TO FILL]` for people;
- `tender-packing-link.json` — the link record: statement → clause → plan figures → sha256 of the tender, the panel
  list and the plan;
- `pack-plan.json` — the plan the figures come from.

On 2026-09-26 it printed 6 × 40HQ from Clause 4.8 (S1 covered); the count (S2) and the heaviest container's gross
mass, 6,472.8 kg against the 20,000 kg of Clause 4.9 (S3), are *partial* because the A-frame stillages of Clause
4.7 are not modelled; securing to the CTU Code (S4), the stillage / upright / no-stacking rules (S5), crate structure
(S6, 24 of 24 pending detailed design) and delivery sequencing (S7) go to a named person. Re-run with
`facade_panels_rev_b.xlsx` and it reports `containers used 6 -> 8 … statements S2, S3, S6, S7 need re-confirmation`
and names the earlier Word copies that still hold the old statements. Nothing is booked or submitted:
`submit_blocked` stays true and a person confirms the plan before booking.

## Run it

```
python scripts/demo_facade.py
```

Offline, steps mode, no model key. It creates a new job folder (a temp folder unless `--job NEW_DIR`),
copies these files into `inputs/`, writes `CIVIL.md` with two stated facts (项目, 辖区 SG) and runs every
turn through `civil.run_task`, the function `civil exec` calls. It writes nothing outside that folder,
prints what each flow extracted and where a person must sign, and exits 1 if a flow errors. The flows are
1 linked (above), 2 tender review, 3 packing, 4 site documents.

The work-at-height briefing is a high-risk post: it writes nothing until a licensed person types
「我明白，将由持证人员签认」 or its one English equivalent, "I understand; a licensed person will sign this off."
(exactly, for that turn only). The script never types it. That person reruns with
`--sign "I understand; a licensed person will sign this off."` (or the Chinese sentence; the same as
`civil exec --confirm`, or the dialog in `civil desktop`).

By hand, in any empty folder: `civil init`, copy the files into an `inputs/` sub-folder (files in the
folder's top level are pasted into every draft), then the two linked requests above, `civil exec "解析招标 facade_itt_doc.md"`,
`civil exec "出一份技术标提纲"`, `civil exec "出一份废标检查表"`, `civil exec "按 facade_panels_zh.xlsx 装柜，柜型 40HQ"`,
`civil exec - < inputs/daily_report_input.txt`, `civil exec - < inputs/wah_briefing_input.txt`.
`scripts/test_facade_demo.py` checks the flows; `scripts/test_tender_packing_link.py` checks the linked run.

## What it does not show yet

The script computes these from the drafts instead of claiming them; on 2026-09-26 it printed:

- tender.parse.md shows 0 of the 12 façade specification clauses (PMU, VMU, heat soak, water test,
  PE endorsement, warranty, A-frame delivery, 40HQ containers, gross mass, CTU Code, delivery sequence,
  insurance), and no LDs or retention row (the linked run reads the five logistics ones itself);
- the plan was 6 × 40HQ, one panel per crate, and the same with Chinese notes, English notes and no
  notes at all (the script packs a copy with the notes removed as the control): the notes (glass,
  upright, no stacking) do not change it, and A-frame stillages are not modelled — that needs the
  contractor's real stillage size, tare and capacity;
- every crate's structure check reads "pending detailed design" (待详设): the engine does not invent a pass;
- the briefing body is generic (its cover still reads 待填); only the sign-off gate is façade-ready.

`facade_itt_doc.md` carries the same clauses as the ITT used in the 2026-09-26 repo study, plus the four
logistics clauses 4.8–4.11 added the same day for the linked run (insurance moved from 4.8 to 4.12), so any score
measured on it is a development-set number, not a held-out figure.
