# SME competition integration

The competition repository is [LUOaini1213/civil-buddy-sme](https://github.com/LUOaini1213/civil-buddy-sme).
Its SME introduction (`0c0e803`) is retained. The unified workbench work from the original repository
(`718c61b`) was merged into this repository at `3667813`; subsequent fixes and release records belong here.
The original repository's draft PR #70 is not a release of this competition entry.

## What is being integrated

- An authenticated Rust host with one named user, private workspace and state directory per instance.
- Persistent Agent events, actor IDs, usage, errors, cancellation and interrupted-task recovery.
- A fixed Python service for deterministic CAD, planning, packing and specialist workflows; no model
  credentials are passed to that service.
- A typed high-risk confirmation for the current operation, without inheriting approval from old
  messages, attachments or imported packages.
- Business project packages and full specialist-session bundles; raw attachments and generated
  deliverables survive transfer, while approval is reset.
- English tender/packing linkage, wider clause handling, evidence-bound explanations and synthetic
  instruction-injection regressions.

The competition's main demonstration remains the synthetic façade tender → panel list → deterministic
packing → clause-linked English draft. CAD, planning and logistics pages are supporting capabilities.
The existing technical document explicitly describes commit `a161251`: its test counts, file line numbers
and statements about missing Rust identity are historical, not a description of the integrated source.
Current behavior is described in [the unified workbench guide](unified-workbench.md),
[SECURITY.md](../../SECURITY.md) and [the handoff guide](release-handoff.md).

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
