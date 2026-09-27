# english_requests: where 30 English requests are routed (HELD-OUT, SYNTHETIC)

`sealed.json` holds 30 English requests an estimator or project engineer might type: 12 that should start the
tender <-> packing link, 8 packing only, 4 tender review only and 6 questions that should stay chat. Every project,
file name and tender reference in it is fictional (SYNTHETIC).

**How it was written.** A subagent wrote it blind on 2026-09-26 at 21:02 SGT. It had not read the router
(`task_router.py`), the link, their tests or any probe folder. It read only `examples/facade-demo/`, the README's
headings and a grep of `docs/` for container words. It wrote the set before PR #67 changed the router. The file is
copied here byte for byte.

**How gold was set.** The writer set gold from the text alone, before any code ran, as a Singapore façade reader
would. A tender file and a panel list both named, with a check / draft / compare verb, gives `link`. A panel list
only gives `pack`, even when the ITT is mentioned in words. A tender only gives `tender`, even with logistics words.
Questions about the feature, hypotheticals and "before I upload" give `chat`. Nobody changed the gold after scoring.

**How it is scored.** `score.py` calls only the public entry points `task_router.route_task` and
`task_router.wants_link`, and reads the route the steps-mode agent acts on:

- `chat`: the intent is chat.
- `link`: `wants_link`, and the route is `bid-parse` with intent run, which is when the agent runs `tender.packing_link`.
- `pack`: otherwise, `pack-ship` is routed.
- `tender`: otherwise, a `bid-*` post or the tender-review workflow is routed.
- `other`: anything else.

`false_link` counts a request that is not a link request but starts the link. `false_run` counts a question that
runs anything.

```
python test/benchmarks/english_requests/score.py            # score and every miss
python test/benchmarks/english_requests/score.py --check    # the floor (check english-requests-heldout)
python test/benchmarks/english_requests/score.py --root R   # score the router of another checkout R
```

## First run (2026-09-27, by the PR #67 reviewer, before any change was made because of it)

| router | accuracy | link | pack | tender | chat | false_link | false_run |
|---|---|---|---|---|---|---|---|
| BEFORE `main` `cab9249` | 0.367 (11/30) | 1/12 | 3/8 | 1/4 | 6/6 | 0 | 0 |
| AFTER PR #67 head `986a3df` | **0.633 (19/30)** | 9/12 | 3/8 | 1/4 | 6/6 | 0 | 0 |

PR #67 changed only the link way in, so the change is in the link column: 8 more link requests reach the link, and
no pack, tender or chat request was pulled into it. The misses that remain after the change:

- **link, 3 misses, all routed to chat, so nothing runs.** R06 ("... does the load plan respect the 26 t container
  limit in the tender?") gets `bid-parse` with intent chat. R07 ("Link tender_block3.md to pl_block3.xlsx and show
  the clause-by-clause result.") and R09 ("Will panels_pkg2.xlsx fit the container type and weight limits that
  itt_pkg2.md asks for?") get no post. The causes were not investigated, so that nobody tunes on them.
- **pack, 5 misses.** R14, R16 and R18 go to chat, R17 ("in 40' HC") to no post, and R19 (a panel list plus
  "the ITT says 40HQ") to `bid-parse`. The same result on `main`: English pack wording outside the link was not
  part of this PR.
- **tender, 3 misses.** R22 and R23 go to no post, and R24 (a question) goes to chat. The same on `main`.

The check pins the AFTER score as a floor: at least 19 of 30, `false_link` 0 and `false_run` 0. It is a guard
against regression, not a target. The set is held-out for its first run only. Nobody may tune the router on these
misses and then quote the set as held-out. Any later score on it is a dev figure.
