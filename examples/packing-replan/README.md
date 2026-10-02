# Bounded packing replan input

`geometry-only.json` is a self-contained synthetic numerical example, not a real
shipping instruction. Copy it to a workspace, select the file and explicitly
enable packing replan in the Agent workbench. The host binds the file hash; the
model selects its index and cannot supply materials, solver options or coordinates.

The v1 JSON requires every top-level field shown in the example. Dimensions and
clearance are millimetres; material weights and the maximum box net mass are kg;
`quantity` counts individual pieces. `container_type` is one of `20GP`, `40GP`,
`40HQ`, `45HQ`; `max_containers` is a hard cap (1–40), not a target. Clearance is
an integer from 0 to 80 mm. At most 40 rows / 200 pieces and 2 MiB are accepted.
Material IDs must be distinct. Unknown fields, arbitrary options and missing
policy values are rejected, not silently ignored.

When both unit and total weight are supplied, total weight must match unit
weight times quantity (0.01 kg absolute rounding tolerance). Conflicting values
return a row-level `source_weight_mismatch` question with the original values;
the planner does not choose the lighter value.

An individual source piece remains indivisible. If the boxing rules would meet
a mass limit by splitting one piece into virtual parts, replan returns
`needs_human` with `physical_split_not_authorized` before layout solving. This
also applies to the boxer's own structural mass limit. Supply a suitable package
design or a reviewed source describing actual separate pieces; lowering a mass
cap does not authorize cutting or dismantling.

`layout_policy` must explicitly declare `orientation: fixed` and
`stacking: floor_only`. The existing standard boxing rules produce the baseline
boxes. Those outer dimensions and masses remain fixed during at most two
priority-order experiments (`size_desc`, `weight_desc`), using the same loader
and its existing search defaults. The critic's options delta is never applied.
Identical effective candidates are skipped, and only an independently verified
improvement can replace the baseline. A result may correctly report no improvement
or inability to fit. This is not a claim of optimality.

Keep source handling text in its `note`, `handling_requirements`, `orientation`
or `stacking` fields. Upright, A-frame, no-stack or other unsupported raw-material
handling requirements still stop automatic boxing. Do not delete those facts to
make a real shipment resemble this synthetic example. The result separately
reports conservation, layout checks and box structure status; it does not verify
securing, lifting or vehicle stability and cannot authorize shipping or replace
professional signoff. No deliverable is written; source bytes are checked before
and after calculation. Jev off/shadow cannot change the deterministic result.
