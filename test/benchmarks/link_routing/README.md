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
