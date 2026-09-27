# NUS-ISS "Show Me Your Agents" 2026 — civil-buddy entry (SME track)

> This is the text as submitted at v0.7.0; later changes are described in [docs/civil-buddy/sme-integration.md](../civil-buddy/sme-integration.md).

Team Mintang · team code PJ2U63AF · as of `main` at `cab9249` (2026-09-26).

Every figure on this page comes from a command in this repository, run offline with no model key.
Nothing here is a judging outcome. The technical document
([nus-iss-technical.md](nus-iss-technical.md)) is the full reference.

## The partner's problem

Our pilot partner is a Singapore curtain-wall (façade) contractor that supplies and installs façade
packages; it is not named in this repository. It stated its problem in writing to the organisers. A
typical job needs two things at once: an English tender response, and the outbound packing and
shipping of the panels to site — often in 40HQ containers, in crates or on steel frames, under weight
and lashing limits. The two live in separate files: the tender in Word or PDF, the packing list in
Excel, the bookings in email. A statement in the bid about packing is often not tied to any loading
plan, and a container count is not tied to the tender clause it should satisfy. First drafts take days,
and the same scramble repeats on the next job. Qualifications and price remain human work. In short,
tender response and outbound packing run as two disconnected exercises and need to stay linked.

## What civil-buddy does about it: the linked run

One request links the two:

```bash
pip install -r requirements.txt && python scripts/demo_facade.py
```

Flow 1 of the demo asks, in English, *"Link the tender facade_itt_doc.md to the packing list
facade_panels.xlsx and write the logistics response"*. civil-buddy:

1. reads the ITT's logistics clauses with their numbers (4.7 handling, 4.8 container type, 4.9 gross
   mass, 4.10 securing, 4.11 delivery sequence);
2. takes the container type from the clause ("Container type 40HQ taken from Clause 4.8");
3. plans the real panel list with the packing engine, keeping its conservation and needs-human gates
   (6 × 40HQ, 24 pieces / 10,800 kg net, 24 → 24 pieces conserved);
4. writes 7 statements, each tied to its clause and a plan figure: **1 covered, 2 partial, 0 gap, 4 for
   a person** — for example S3: heaviest container 6,472.8 kg gross (2,582.8 kg cargo + 3,890 kg tare)
   against the 20,000 kg limit of Clause 4.9;
5. writes an English bid-book draft whose logistics chapter cites clause and figure, and a link record
   (`tender-packing-link.json`) holding statement → clause → figures → SHA-256 of the tender, the panel
   list and the plan, with `confirmed_by_person = false` and `submit_blocked = true`.

When revision B of the panel list arrives (30 panels, 13,920 kg), the same request re-plans (8 × 40HQ)
and names what went stale: statements S2, S3, S6 and S7 need re-confirmation, S1, S4 and S5 keep the
same figures, and the earlier Word copies `bidbook.en.docx` and `tender-packing-link.docx` "still hold
the previous statements - do not send them". A statement the plan cannot evidence is never marked
covered; `scripts/test_tender_packing_link.py` (18 tests) pins that, including a plan that does not fit
and container clauses that name a size only or a type the planner cannot model.

Flows 2–4 of the same script run tender review, packing and site paperwork on their own. Site
paperwork is a secondary capability that shows the typed licensed sign-off; it is not the partner's
problem.

## The security baseline

Shipped in pull request #61 (merge commit `d3ada11`) and enforced in code:

- Code computes every number; model text never reaches a deliverable.
- The model never approves: the Python MCP server and the web apps accept no approval flag from a
  program.
- Only a person's typed sentence (我明白，将由持证人员签认, "I understand; a licensed person will
  sign") approves the 19 high-risk posts, for that turn only. The bid posts are low risk: they draft
  without it, but every draft carries `submit_blocked = true`.
- The server is token-gated: with `CIVIL_TOKEN` set, every API route needs it, loopback included; a
  non-loopback bind without a token refuses to start.
- 146 offline checks (`npm run check`) run in CI on every push, next to a `docker-smoke` job that builds
  and starts the gateway image (pull request #64, merge commit `16316df`).

## Real and synthetic

- **Synthetic:** every demo input — the ITT, the project, the main contractor, the panel lists, the
  site day and the people — is an invented fixture in
  [examples/facade-demo](../../examples/facade-demo/README.md). The only company name in code and
  fixtures is the fictional "Harbourline Facade Pte. Ltd. (DEMO)".
- **Real:** the code paths, the checks and the numbers the commands print. No partner document or
  figure is in the repository, no pilot has started, and no time saving is claimed.
- **Deployment:** the target is one AWS Lightsail instance per company. The instance is **not running
  yet**; the Docker image is proven only in CI and local Docker. Amazon Bedrock is configurable but has
  **never been run** from this repository, and no result here comes from a live model.

## Not done yet

As the demo and the technical document print them:

- A-frame stillages, upright transport and no stacking, lashing to the CTU Code, and delivery
  sequencing are not modelled; they stay `[TO CONFIRM]` for a person. The container count rests on the
  planner's own crates, not on stillages.
- Crate structure: 24 of 24 crates are pending detailed design (待详设).
- The gross mass uses an approximate knowledge-base tare; dunnage, lashing and stillage mass are
  excluded, and the signed VGM governs.
- The plan does not follow the installation sequence (in rev B the L8 panels load in container 1).
- The tender parse shows 0 of the ITT's 12 façade specification clauses, and no liquidated-damages or
  retention row.
- The link runs from a `civil` or workbench turn only, not over MCP or a gateway route; the older
  `/api/tender/delivery` route can plan sample materials in another container type and leaves that row
  to a person.
- The English bid-book still carries Chinese titles in chapter 3 and Annex B for non-logistics rows.
- No step yet in which a person marks a link statement confirmed; the link has not been run in model
  mode.
- English routing: on the blind held-out set `heldout_en2` (28 sentences) accuracy is 0.786 with 0
  false runs; the misses fall back to chat.
- The legacy Rust workbench (`workbench/`) predates the security baseline and still accepts
  `confirm_ok`; it is not part of the deployed surface.

## Links

- [README](../../README.md) — the one-command demo, the safety model, the settings.
- [Technical document](nus-iss-technical.md) — architecture, judging-criteria map, evaluation, AWS
  status, limitations.
- [examples/facade-demo/README.md](../../examples/facade-demo/README.md) — the synthetic fixtures and
  the linked run.
- [docs/deploy-minimal.md](../deploy-minimal.md) — operator guide for one server (Chinese).
