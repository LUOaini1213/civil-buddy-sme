# Sealed panel lists (SYNTHETIC)

**Everything in this folder is synthetic.** The projects (Marina Crest Residences, Harbour Line Office Block,
Seaview Polyclinic, Tanjong Loft Hotel, Kallang Point Offices), the people and every figure were invented. None of
it is a real shipment or record.

## Who wrote it, when, and how gold was set

- Written **blind on 2026-09-26 21:02 (+08:00)** by an agent that had **not read the rules**: it did not open
  `table_mapper.py`, `pack_ship_solve.py`, `tender_packing_link.py` or their tests. It read only
  `examples/facade-demo/` (README, `facade_itt_doc.md`, `make_panels.py`). It was kept outside the repository
  until the first scored run below, and the builder of PR #69 never opened it.
- **Gold** (`panel_lists/gold.json`, unchanged): pieces and kg computed by hand from the rows the generator
  writes, then checked by reading the saved workbooks back. A list is `plan` when a finished plan with exactly
  those pieces and kg is right. A list is `stop` when a row cannot be read without guessing (an empty weight, the
  quantity `10/12`) or is not cargo (a steel A-frame stillage listed like a panel); then the row goes to a person.
- The six lists: title rows + `W x H (mm)` + `Thk` + a TOTAL row (plan, 26 pcs / 10,488 kg); a merged two-row
  header in tonnes (plan, 28 / 11,640); a stillage row (stop at row 7); a missing weight (stop at SK-07, row 5);
  `10/12` in tonnes (stop at TL-V2, row 5); a Cover sheet first, per-floor Sub-totals, a GRAND TOTAL and
  `1,500 x 3,900` sizes (plan, 24 / 9,540).

## The scorer

`score_sealed.py` calls only public entry points: `pack_ship_solve.run_plan(file_path, "40HQ", lang="en")` (the
pack route; `lang` only when the code has it) and `tender_packing_link.run_link` with the SYNTHETIC control ITT
of `../tender_link_sealed/injections/control_itt.md` (it names 40HQ), inside a fresh job folder so the read
sandbox holds. No model, no network, deterministic (two runs give the same JSON), about 10-30 s.

| Score | Meaning |
| --- | --- |
| exact | plan lists: a solver plan whose conservation record has the gold pieces and kg (+-1 kg). Stop lists: no finished plan, and the rows sent to a person are exactly the gold stop rows |
| safe | plan lists: right, or no plan with a stated reason. Stop lists: no finished plan |
| row named | stop lists: the question names the gold sheet row |
| link ok | the linked run marks nothing covered without a correct plan; for a stop list its reply names the stop mark |
| wrong plan | a solver plan with wrong pieces or kg, or a plan where the gold says stop |

`--check` fails when a score falls below `FLOORS`, the AFTER numbers of the first run.

## First scored run (held-out, 2026-09-27, by the reviewer of PR #69)

BEFORE = origin/main `fa63c28`; AFTER = PR #69 head `77d6122`. Nothing was tuned on this set before these runs.

| | BEFORE | AFTER |
| --- | --- | --- |
| exact | 0/6 | **4/6** |
| safe | 6/6 | 5/6 |
| row named (stop lists) | 0/3 | 2/3 |
| link ok | 3/6 | 5/6 |
| wrong plan | 0 | 1 |
| covered without a correct plan | 0 | 0 |

BEFORE every list stopped at `no_materials`: the header was never found, so nothing was read (safe, but no
list planned and no stop named its row). AFTER, per list:

- pl01 title rows + TOTAL: plan 7 x 40HQ, 26 pcs / 10,488 kg, the TOTAL row not packed. **ok**
- pl02 merged header, tonnes: plan 7 x 40HQ, 28 pcs / 11,640 kg, header read from rows 2 and 3, x1000. **ok**
- pl03 stillage row: **FAIL.** The four A-frame stillages are packed as cargo: a solver plan of 20 pcs /
  7,816 kg (9 x 40HQ, `can_fit` false, so the linked run states no count and marks nothing covered). This is the
  gold's first "must not". No code on main or in the PR knows a stillage row is transport equipment; the PR only
  made the header readable. BEFORE it was "safe" only because nothing was read. Left open: no rule was added
  because of this failure.
- pl04 missing weight: stop, `Row 5 (SK-07) has no usable weight`. **ok**
- pl05 `10/12`: stop, `Row 5 (TL-V2): the quantity '10/12' is not a whole number of pieces`. **ok**
- pl06 Cover sheet first: **FAIL (safe).** The reader reads the active sheet (Cover) and stops at
  `no_materials`; the reply lists the cover's title as a "column not read", which does not tell the person that
  the table is on the second sheet. Left open.

## After the first run (seen, not held-out)

The reviewer's fixes (commit after `77d6122`) were found with the reviewer's own adversarial sheets, except one:
thousands separators in a size pair (`1,500 x 3,900` was left unread) was first noticed in pl06's layout note, so
**pl06 is now "seen"**. The fix does not change pl06's score (the Cover sheet still stops it). The scores after the
fixes are identical to the AFTER column. From now on the set is in the repository, so every later number on it is
a DEV number.
