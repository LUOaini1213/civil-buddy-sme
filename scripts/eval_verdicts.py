#!/usr/bin/env python3
"""Score tools/verdict_guard on test/benchmarks/verdicts/cases.json.

    python scripts/eval_verdicts.py --variant all     # what the clause scope and each check buy
    python scripts/eval_verdicts.py --show            # misses and false flags of the shipped guard
    python scripts/eval_verdicts.py --english         # the English DEV set (english_dev.json)
    python scripts/eval_verdicts.py --file PATH       # any set in the same format (e.g. a held-out one kept elsewhere)
    python scripts/eval_verdicts.py --overlap ...     # a flag counts when it overlaps a wanted phrase, not only when equal
    python scripts/eval_verdicts.py --file dev_round3.json   # round-3 DEV set (bypass phrasings and look-alikes)
    python scripts/eval_verdicts.py --file dev_round3_review.json   # the review of #74's adversarial probes (DEV)
    python scripts/eval_verdicts.py --check           # CI floors: P >= 0.95, R >= 0.95
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from packing_assistant.tools.verdict_guard import ABLATIONS, stated_verdicts  # noqa: E402

BENCH = ROOT / "test" / "benchmarks" / "verdicts"
FLOOR_P, FLOOR_R = 0.95, 0.95
HELDOUT_P, HELDOUT_R = 0.85, 0.90      # heldout2.json, unseen by the shipped rules (measured 0.900 / 1.000)


def _spans(text: str, phrases):
    """Each wanted phrase with its position in the sentence (the first unused occurrence)."""
    spans, taken = [], set()
    for phrase in phrases:
        start = text.find(phrase)
        while start in taken and start >= 0:
            start = text.find(phrase, start + 1)
        taken.add(start)
        spans.append((phrase, start, start + len(phrase) if start >= 0 else -1))
    return spans


def score(flags, name="cases.json", overlap=False):
    path = BENCH / name if (BENCH / name).is_file() else Path(name)
    rows = json.loads(path.read_text(encoding="utf-8"))["cases"]
    tp = fp = fn = 0
    notes = []
    for row in rows:
        found = stated_verdicts(row["text"], **flags)
        want = _spans(row["text"], row["verdicts"])
        for item in found:
            hit = next((w for w in want if (w[0] == item["text"] if not overlap else
                                            (w[1] >= 0 and item["start"] < w[2] and w[1] < item["end"]) or w[0] == item["text"])), None)
            if hit is not None:
                want.remove(hit)
                tp += 1
            else:
                fp += 1
                notes.append(f"    extra   [{row['id']}] {item['text']}")
        fn += len(want)
        notes += [f"    missed  [{row['id']}] {phrase}" for phrase, _s, _e in want]
    precision = tp / (tp + fp) if tp + fp else 1.0
    recall = tp / (tp + fn) if tp + fn else 1.0
    return {"p": precision, "r": recall, "tp": tp, "fp": fp, "fn": fn, "notes": notes, "n": len(rows)}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--variant", default="shipped")
    parser.add_argument("--show", action="store_true")
    parser.add_argument("--heldout", type=int, choices=(1, 2), default=0, help="score a held-out round (2 = unseen by the shipped rules)")
    parser.add_argument("--english", action="store_true", help="score the English DEV set (english_dev.json; used to build the rules)")
    parser.add_argument("--file", default="", help="score this file instead (same format as cases.json)")
    parser.add_argument("--overlap", action="store_true", help="a flag that overlaps a wanted phrase counts as found")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    names = list(ABLATIONS) if args.variant == "all" else [args.variant]
    target = args.file or ("english_dev.json" if args.english else {0: "cases.json", 1: "heldout.json", 2: "heldout2.json"}[args.heldout])
    print(f"set: {target}{'  (overlap matching)' if args.overlap else ''}")
    print(f"{'variant':<42}{'P':>7}{'R':>7}   tp/fp/fn")
    for name in names:
        result = score(ABLATIONS[name], target, overlap=args.overlap)
        print(f"{name:<42}{result['p']:>7.3f}{result['r']:>7.3f}   {result['tp']}/{result['fp']}/{result['fn']}")
        if args.show:
            print("\n".join(result["notes"]))
    if args.check:
        shipped, unseen = score(ABLATIONS["shipped"]), score(ABLATIONS["shipped"], "heldout2.json")
        english = score(ABLATIONS["shipped"], "english_dev.json")
        round3 = score(ABLATIONS["shipped"], "dev_round3.json")
        review = score(ABLATIONS["shipped"], "dev_round3_review.json")     # the review of #74's adversarial probes
        ok = (shipped["p"] >= FLOOR_P and shipped["r"] >= FLOOR_R and unseen["p"] >= HELDOUT_P and unseen["r"] >= HELDOUT_R
              and english["p"] >= FLOOR_P and english["r"] >= FLOOR_R and round3["p"] >= FLOOR_P and round3["r"] >= FLOOR_R
              and review["p"] >= FLOOR_P and review["r"] >= FLOOR_R)
        print(f"dev {shipped['p']:.3f}/{shipped['r']:.3f}  heldout2 {unseen['p']:.3f}/{unseen['r']:.3f}  "
              f"english dev {english['p']:.3f}/{english['r']:.3f}  round-3 dev {round3['p']:.3f}/{round3['r']:.3f}  "
              f"round-3 review dev {review['p']:.3f}/{review['r']:.3f}")
        print(("PASS" if ok else "FAIL") + f" verdicts floors (dev, English dev and both round-3 dev sets P/R >= {FLOOR_P}, heldout2 P >= {HELDOUT_P}, R >= {HELDOUT_R})")
        return 0 if ok else 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
