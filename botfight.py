#!/usr/bin/env python3
"""botfight.py — run the kit bots against EACH OTHER (not against your agent).

    python botfight.py                       # full round-robin, rookie/operator/adversary
    python botfight.py --a operator --b adversary --rounds 10 --seed 1
    python botfight.py --a you --b adversary # your agent vs a bot (same as `spar.py --bot adversary`)

`spar.py` only ever pits "you" against one bot. This drives the same real
machinery (`spar._exchange` — real world corpus, real mutation engine, real
kit referee) with an ARBITRARY pair of sides: side A's gateway + prosecutor
vs side B's deck, and side B's gateway + prosecutor vs side A's deck, one
exchange each per round, HP folded exactly as `spar.main` does.

Pure tooling: stdlib only, imports `spar`, writes nothing unless --ui. Not
bundled by `make submit` (same as `ladder.py`).
"""
from __future__ import annotations

import argparse
import itertools
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import spar  # noqa: E402

SIDES = ("you", "rookie", "operator", "adversary")
START_HP = spar.START_HP


def _one_match(a: str, b: str, world, rounds: int, seed: int) -> dict:
    a_gw, a_pr, a_deck, a_lineup = spar._load_side(a)
    b_gw, b_pr, b_deck, b_lineup = spar._load_side(b)
    a_cards = {c["id"]: c for c in a_deck["cards"]}
    b_cards = {c["id"]: c for c in b_deck["cards"]}
    rng = random.Random(seed)
    hp_a = hp_b = START_HP

    for r in range(1, rounds + 1):
        b_card = b_cards[b_lineup[(r - 1) % len(b_lineup)]]   # b attacks a
        a_card = a_cards[a_lineup[(r - 1) % len(a_lineup)]]   # a attacks b
        # a defends b's card; b's prosecutor argues a's trace
        d_a = spar._exchange(b, a, a_gw, b_pr, b_card, world, r, rng, "learner:sv-0417")
        # b defends a's card; a's prosecutor argues b's trace
        d_b = spar._exchange(a, b, b_gw, a_pr, a_card, world, r, rng, "learner:sv-0417")
        hp_a -= d_a["damage"] + d_b["recoil"]
        hp_b -= d_b["damage"] + d_a["recoil"]
        hp_a, hp_b = max(0, hp_a), max(0, hp_b)
        if hp_a == 0 or hp_b == 0:
            break

    winner = a if hp_a > hp_b else b if hp_b > hp_a else "draw"
    return {"a": a, "b": b, "hp_a": hp_a, "hp_b": hp_b, "winner": winner, "rounds": r}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--a", choices=SIDES, help="one side (default: round-robin all bots)")
    ap.add_argument("--b", choices=SIDES, help="the other side")
    ap.add_argument("--rounds", type=int, default=10)
    ap.add_argument("--seed", type=int, default=1)
    a = ap.parse_args(argv)

    world = spar._load_world()

    if a.a and a.b:
        pairs = [(a.a, a.b)]
    else:
        pairs = list(itertools.combinations(("rookie", "operator", "adversary"), 2))

    print(f"\n  BOT vs BOT — real corpus, real referee   ({a.rounds} rounds, seed {a.seed})")
    print(f"  {'-' * 58}")
    wins: dict[str, int] = {}
    for x, y in pairs:
        m = _one_match(x, y, world, a.rounds, a.seed)
        wins[m["winner"]] = wins.get(m["winner"], 0) + 1
        tag = "DRAW" if m["winner"] == "draw" else f"{m['winner']} wins"
        print(f"  {x:>9}  {m['hp_a']:>3} — {m['hp_b']:<3}  {y:<9}   {tag}  (r{m['rounds']})")

    if len(pairs) > 1:
        print(f"  {'-' * 58}")
        for name, n in sorted(wins.items(), key=lambda kv: -kv[1]):
            print(f"  {name:>9}: {n} win(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
