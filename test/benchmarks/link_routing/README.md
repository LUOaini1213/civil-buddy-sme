# link_routing: which requests start the tender <-> packing link

`dev.json` holds 53 requests (38 English, 15 Chinese): 26 that should start the linked run, 18 that should go
to their own post (parse only, pack only, a BOQ or price schedule against a tender, two documents and no list,
a negated request), and 9 questions that should stay chat.

**This is a DEV set.** It was written by the implementer on 2026-09-26, before the router change, and then used
while building the rule, so its score after the change says the rule does what it was built to do, not how it
does on requests nobody has seen. 22 of the 53 were already known: the demo sentence, the phrasings in
`scripts/test_tender_packing_link.py`, and ten phrasings an earlier probe had run (marked `"seen": true`).

```
python scripts/eval_link_routing.py --show     # route-level score and misses
python scripts/eval_link_routing.py --e2e      # also runs the requests on the SYNTHETIC demo files, steps mode
python scripts/eval_link_routing.py --check    # CI floor (accuracy >= 0.95, no request wrongly linked)
```

| router | accuracy | link recall | other | chat | wrongly linked |
|---|---|---|---|---|---|
| before (`cab9249`) | 0.642 | 7 / 26 | 18 / 18 | 9 / 9 | 0 |
| after | 1.000 | 26 / 26 | 18 / 18 | 9 / 9 | 0 |

End to end on the demo job (`--e2e`, the 37 requests that name only the demo files): the link ran for 7 of 23 link
requests before and 23 of 23 after; none of the 14 others (other + chat) ran it either time. English requests whose reply had any Chinese
character: 21 of 26 before, 8 of 26 after (the parse and pack posts' own replies, and the reference notes under an
English question, are still Chinese).

The same change leaves the router's own sets as they were: `eval_task_intent.py --check` (dev 1.000, heldout2
1.000, 0 false runs), `test_english_intents.py --score` (heldout_en2 0.786 with 0 false runs, unchanged), and 0 of
the 214 requests in `test/benchmarks/task_intent/*.json` change route, intent or link decision.

## Round 3: look-alikes (`dev_round3.json`)

The review of PR #67 found requests that name both files and a link word but do not ask for the run, and each of
them ran it (11 files written, the bid book among them): advice ("Should I check P against T first?", "Do I need
to ...", "Is it worth ...", "Would it make sense ...", 要不要 / 我应该), a hypothetical ("If I check P against T, will
it change my crate count?"), past tense or someone else's work ("I already checked ...", "My colleague checked ...",
"Has anyone checked ..."), and a negated link inside a packing request ("Pack P without checking T").
`dev_round3.json` holds those 12 (origin `review`) and 20 more written by the implementer before the fix (origin
`implementer`): 10 that must stay chat, 3 that must not start the link and 7 that must still run it. It is a DEV set too: it was used while building the fix.

The fix (`task_router.py`): a request that rules the link out ("without checking", "no need to link", "don't check
it against", 不用核对) is not a link request, so its packing still runs on its own post; a link request put as
advice, a hypothetical or past tense is a chat whatever its verbs say; and a link request the rules read as a chat
turns into a run only when it asks for one (a closed compliance question, "check whether", "which statements", or a
sentence that opens with its verb rather than a subject).

| router | round3 accuracy | link | other | chat | wrongly linked | look-alikes run (any post) |
|---|---|---|---|---|---|---|
| before (`923ed38`) | 0.250 | 6 / 7 | 2 / 5 | 0 / 20 | 23 | 20 |
| after | 1.000 | 7 / 7 | 5 / 5 | 20 / 20 | 0 | 0 |

End to end on the demo job (steps mode, the same 32 requests): the link ran 29 times before (23 of them for a request
that did not ask for it, 11 files each) and 7 times after, once for each request that asks for it. `dev.json` stays
1.000; `eval_task_intent.py --check` (dev 1.000, heldout2 1.000, 0 false runs), `test_english_intents.py`
(heldout_en2 0.786, 0 false runs) and the sealed `english_requests` score (19/30, 0 false runs) are unchanged, and
none of the 297 requests in `task_intent/*.json`, `dev.json` and `english_requests/sealed.json` changes route,
intent or link decision. `--check` now also holds round3 at accuracy >= 0.95 with no wrong link and no look-alike run.

### Round 3, independent review (origin `review-r3`)

The reviewer of PR #75 wrote 32 more inputs before the follow-up fix and ran them on `923ed38`, on the PR head
`d455e57` and after: deferred ("Remind me tomorrow to check P against T", "Note to self: check ...", "Wait until
Monday, then check ..."), a sentence that opens with the check done or as its subject ("Linked T and P yesterday",
"Checking P against T was a waste of time"), "w/o checking" and "No checking against T" inside a pack request, and
zero-width characters. At `d455e57` the opening-verb rule read "Remind", "Linked" and "Checking" as imperatives.
The follow-up adds deferral and participle / gerund openings to the not-asked rule, `no` and `w/o` to the
exclusions, and stops the opening-verb rule at a first word in -ed / -ing.

| router | review-r3 accuracy | link | other | chat | wrongly linked | look-alikes run |
|---|---|---|---|---|---|---|
| `923ed38` | 0.375 | 6 / 7 | 2 / 4 | 4 / 21 | 19 | 17 |
| `d455e57` | 0.719 | 6 / 7 | 2 / 4 | 15 / 21 | 8 | 6 |
| after | 0.969 | 6 / 7 | 4 / 4 | 21 / 21 | 0 | 0 |

The one miss is safe-side: "When you get a chance, check P against T." stays a chat (a leading "When" reads as a
question). None of the 297 requests in `task_intent/*.json`, `dev.json` and `english_requests/sealed.json` changes
route, intent or link decision between `d455e57`, `923ed38` and the follow-up.
