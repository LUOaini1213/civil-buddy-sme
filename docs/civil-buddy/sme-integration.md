# SME competition integration

The competition repository is [LUOaini1213/civil-buddy-sme](https://github.com/LUOaini1213/civil-buddy-sme).
The version submitted for NUS-ISS shortlisting is release v0.7.0 (`0c0e803`); its SME introduction is retained.
After v0.7.0, the unified workbench from earlier development work (`718c61b`) was merged into this repository at
`3667813` (PR #1), and a `/demo` page and a Lightsail guide followed in PR #2; none of this is in the submitted
PDFs. Subsequent fixes and release records belong here. Draft work in the earlier development repository is not a
release of this competition entry.

Features recovered after this first integration, and their remaining acceptance limits, are listed in the notes
of the v0.8.0 preview releases.

On 2026-09-28 `main` merged the later review rounds of the development line (its pull requests #67 to #69 and #71 to #77:
mass limits read per subject and per direction, clauses cited as written, English verdict / claim / record guards,
panel lists with title rows, tonnes and packaging-equipment rows, a hardened `/demo` upload, and the deployment kit
rehearsal). Where both lines had fixed the same thing, the stricter behaviour was kept: a message approves on the
Python surfaces only when the whole of it is the sign-off sentence, `/confirm` in the terminal still only retries
the task waiting for approval, and parse records stay per task. The façade demo figures are unchanged.

## What is being integrated

- An authenticated Rust host with one named user, private workspace and state directory per instance.
- Persistent Agent events, actor IDs, usage, errors, cancellation and interrupted-task recovery.
- A fixed Python service for deterministic CAD, planning, packing and specialist workflows; no model
  credentials are passed to that service.
- A typed high-risk confirmation for the current turn, without inheriting approval from old
  messages, attachments or imported packages; it applies when a high-risk post is selected or loaded (see
  [SECURITY.md](../../SECURITY.md) for the open items).
- Business project packages and full specialist-session bundles; raw attachments and generated
  deliverables survive transfer, while approval is reset.
- English tender/packing linkage, wider clause handling, evidence-bound explanations and synthetic
  instruction-injection regressions.

The competition's main demonstration remains the synthetic façade tender → panel list → deterministic
packing → clause-linked English draft. CAD, planning and logistics pages are supporting capabilities.
The existing technical document explicitly describes commit `a161251`: its test counts, file line numbers
and statements about missing Rust identity are historical, not a description of the integrated source.
Current behavior is described in [the unified workbench guide](unified-workbench.md),
[SECURITY.md](../../SECURITY.md) and [the handoff guide](release-handoff.md) (in Chinese).

## Verification and limits

Run `npm run check` before `npm run check:full`. Full checks include compiled Rust/Python HTTP tests:
login, private project records, restart, cross-instance denial, document-copy tools, specialist attachment
and deliverable handoff. The script uses local scripted providers and synthetic fixtures, not paid models.

The specialist-session ZIP is not a backup of the new Rust Agent execution database. Preserve the owned
workspace and state directory for same-owner recovery; use business packages to hand work to a colleague.
Release packages contain allowlisted committed source and one explicitly supplied executable, with file
hashes. The package verifier checks integrity; source/binary correspondence needs a separate build record.

The 2026-09-27 bounded local file check did not establish real-business acceptance. The selected logistics
image explicitly identified itself as a synthetic OCR fixture, and OCR was not configured. The selected CAD
parsed, but its extrusion length and solid-region confirmation were missing; no dimensions were invented and
no model was generated. Private files and reports remain outside Git and release archives.

Real drawing/model acceptance, a complete real schedule, real packing-list field review, a colleague's second
computer and a public deployment remain separate acceptance tasks. AWS Lightsail and Bedrock are not claimed
as deployed or tested. No booked shipment, approved bid, professional sign-off or time saving is claimed.
