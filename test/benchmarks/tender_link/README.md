# Tender <-> packing link: clause-reading DEV set

`dev.json` holds 90 short SYNTHETIC tender excerpts, each labelled with what the link
(`packing_assistant/tender_packing_link.py`) should read from it: the logistics kinds, the per-container mass limit and its
basis (gross / cargo), a per-package limit (stillage, crate, piece, crane lift), the container decision, and how the
statement must cite the clause. No clause comes from a real tender; project, contractor and figures are invented.

**This is a DEV set, not a held-out score.** It was used while building the change that it measures, and the
implementer read every case. A number on it shows the reader does what the labels say on these cases. It does not
show how the reader does on tenders nobody has seen. Blind held-out sets are kept outside the repository, and only
reviewers use them.

## Where the cases come from

| ids | origin | how it was made |
|---|---|---|
| P01-P18 | blind probe | written by the product lens of the 2026-09-26 audit **before** it read the link's regexes; labels translated into the link's kind names |
| A1-A7 | post-blind | the same auditor, **after** reading the regexes |
| V1-V7 | verify | the audit's verifier: rewordings of the demo's Clause 4.9 |
| C01-C26 | auditor clause probe | the technical lens's clause list (not strictly blind). C06 (truck GVW) and C20 (crane lifting capacity) were relabelled to follow this change's rule that a vehicle or crane limit is never a container limit (see each case's `note`) |
| G1-G4 | paraphrase | the agentic lens's paraphrases of 4.7 / 4.9 / 4.10 / 4.11. G3 and G4 were re-written from the audit's quoted fragments |
| D01-D24 | implementer | written by the implementer on 2026-09-26 **after** reading the code, while building |
| D25-D28 | implementer | written after the first build, together with the change they probe (a subject named after the figure, the vehicle guard). On the first run 3 of 4 passed: D28 ("rated 3 t per lift") missed and D25's label lacked the optional `crating` kind that other crate cases carry. Both were then fixed. On `cab9249`, D27 (a 30 t road limit for vehicles) was read as a container limit and marked **covered** |

## Labels

- `kinds`: kinds the reader must produce. `optional_kinds`: extra kinds that are acceptable. `unplaced` is the
  safety-net kind: a row for a person that quotes a clause the reader could not place.
- `container_limit_kg`: the per-container limit. `null` means **no** container limit may be read. If the key is
  absent, the limit is not scored.
- `basis`: `gross` / `cargo`. If it is absent or null, the basis is not scored, because the clause does not say.
- `package_limit_kg`: the per-package / per-lift limit.
- `type`: the container type the plan should be made in, `person` (the tender allows several types or names only a
  size, so a person decides), or `default`.
- `cite`: how the statement must name the clause, e.g. `Clause 4.9(a)`, `Part C Clause 12`, `Table 4-1, row 4.9`,
  `line 3 of D13.md`. Each case's text is read as a file named `<id>.md`.

## Run

```bash
python scripts/bench_tender_link.py            # prints the scores
python scripts/bench_tender_link.py --verbose  # plus every case that misses
```

The gate check `tender-link-clauses` (`scripts/test_tender_link_clauses.py`) runs it. It requires zero silently lost
kinds, zero false container limits, zero invalid "Clause L<n>" cites and zero false "covered" statements, and it holds
the floors below.

## Scores (DEV, `python scripts/bench_tender_link.py`)

"Before" is the same scorer with `origin/main`'s `tender_packing_link.py` (`cab9249`) loaded in place of this change's.

| metric | before (`cab9249`, main) | after (this change) |
|---|---|---|
| kind recall | 51/98 | 98/98 |
| extra kinds | 2 | 0 |
| silently lost (no kind and no person row) | 47 | 0 |
| container limit read exactly | 12/32 | 32/32 |
| false container limit (stillage, crate, truck ...) | 2 | 0 |
| per-package limit read | 0/11 | 11/11 |
| basis (gross / cargo) | 9/28 | 28/28 |
| container decision | 13/18 | 18/18 |
| cites as the tender writes them | 4/22 | 22/22 |
| invalid cites ("Clause L3") | 10 | 0 |
| false "covered" | 1 (D27) | 0 |
| "not placed" rows for a person | 0 | 3 (D17, D18, D26) |
| "not placed" rows on cases labelled with no kind (false alarms) | 0 | 0 |

The "after" column is a DEV figure: the reader was tuned on these cases until they passed. The regex changes were
written for the phrasings in this file, so expect lower numbers on unseen wording. That risk is why the safety net
exists: a clause the reader cannot place goes to a person instead of disappearing.
