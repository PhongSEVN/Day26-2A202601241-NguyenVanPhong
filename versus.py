#!/usr/bin/env python3
"""versus.py — duel YOUR agent against another team's repo, head to head.

    python versus.py "../TRACK2_DAY26_..._LETHIYENNHI-main"
    python versus.py <other-repo> --rounds 10 --seed 1 --matches 5

The two kits differ, so neither can import the other. Each side runs in its
OWN subprocess (`_versus_worker.py`, launched with that repo as cwd) and this
script drives the exchange over JSON lines: this repo's `spar._exchange` for
the world / mutation engine / event trace, this repo's `spar._detect` +
`kit.referee.rubric` weights as the COMMON referee, and the two workers for
`Gateway.decide` and `prosecute`.

Each round: you defend their attack card (their prosecutor argues your trace),
they defend your attack card (your prosecutor argues theirs). HP folds exactly
as `spar.main` does — damage proven against you, plus your own false-claim
recoil. First to 0 loses; else higher HP after N rounds wins.

Tooling only, stdlib + this repo's `spar`. Not bundled by `make submit`.
"""
from __future__ import annotations

import argparse
import json
import random
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import spar  # noqa: E402
from kit.referee.rubric import weight_of  # noqa: E402  (this repo's rubric = the shared referee)

_WORKER = HERE / "_versus_worker.py"


def _ctx_dict(ctx) -> dict:
    return {
        "act": ctx.act, "sub": ctx.sub, "scopes": sorted(ctx.scopes),
        "credits": ctx.credits, "round": ctx.round,
        "call_index": getattr(ctx, "call_index", 0),
        "leases": list(getattr(ctx, "leases", ())), "history": [],
    }


class Worker:
    """A persistent subprocess running `_versus_worker.py` with `repo` as cwd."""

    def __init__(self, repo: Path, label: str):
        self.repo, self.label = repo, label
        self._script = repo / "_versus_worker.py"
        self._owned = not self._script.exists()
        if self._owned:
            shutil.copy(_WORKER, self._script)
        self.p = subprocess.Popen(
            [sys.executable, "_versus_worker.py"], cwd=str(repo),
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, bufsize=1,
        )

    def rpc(self, msg: dict) -> dict:
        self.p.stdin.write(json.dumps(msg) + "\n")
        self.p.stdin.flush()
        line = self.p.stdout.readline()
        if not line:
            err = self.p.stderr.read() if self.p.stderr else ""
            raise RuntimeError(f"{self.label} worker died. {err[-500:]}")
        return json.loads(line)

    def close(self):
        try:
            self.p.stdin.close()
            self.p.wait(timeout=5)
        except Exception:
            self.p.kill()
        if self._owned:
            self._script.unlink(missing_ok=True)


class RemoteGateway:
    """Stands in for a `Gateway` inside `spar._exchange`; forwards to a Worker."""

    def __init__(self, ctx, worker: Worker):
        self._ctx, self._w = ctx, worker
        worker.rpc({"op": "new_gateway", "ctx": _ctx_dict(ctx)})

    def note_card(self, server, card):
        self._w.rpc({"op": "note_card", "server": server, "card": card})

    def note_result(self, anchor, etag):
        self._w.rpc({"op": "note_result", "anchor": anchor, "etag": etag})

    def decide(self, cmd):
        self._w.rpc({"op": "update_ctx", "ctx": _ctx_dict(self._ctx)})
        r = self._w.rpc({"op": "decide", "cmd": cmd.to_dict()})
        if "raised" in r:
            raise RuntimeError(r["raised"])  # spar._exchange catches -> 2cr + integrity
        call = r.get("call")
        call_ns = SimpleNamespace(**call) if isinstance(call, dict) else call
        return SimpleNamespace(
            verdict=r["verdict"], reason=r.get("reason"), call=call_ns,
            quarantine=r.get("quarantine", False), note=r.get("note"),
        )


def _gw_factory(worker: Worker):
    return lambda ctx: RemoteGateway(ctx, worker)


def _prosecutor(worker: Worker):
    def pr(view, answer, card):
        return worker.rpc({"op": "prosecute", "trace": view, "answer": answer, "card": card})
    return pr


def _load_deck(repo: Path):
    deck = json.loads((repo / "deck" / "deck.json").read_text(encoding="utf8"))
    lineup = json.loads((repo / "deck" / "lineup.json").read_text(encoding="utf8"))["order"]
    return {c["id"]: c for c in deck["cards"]}, lineup


def run_match(mine: Worker, opp: Worker, my_cards, my_lineup, op_cards, op_lineup,
              world, rounds: int, seed: int, verbose: bool) -> tuple[int, int]:
    hp_me = hp_op = spar.START_HP
    for r in range(1, rounds + 1):
        op_card = op_cards[op_lineup[(r - 1) % len(op_lineup)]]
        my_card = my_cards[my_lineup[(r - 1) % len(my_lineup)]]
        rng = random.Random(seed * 1000 + r)
        d_me = spar._exchange(opp.label, mine.label, _gw_factory(mine), _prosecutor(opp),
                              op_card, world, r, rng, "learner:sv-0417")
        d_op = spar._exchange(mine.label, opp.label, _gw_factory(opp), _prosecutor(mine),
                              my_card, world, r, rng, "learner:sv-0417")
        hp_me -= d_me["damage"] + d_op["recoil"]
        hp_op -= d_op["damage"] + d_me["recoil"]
        hp_me, hp_op = max(0, hp_me), max(0, hp_op)
        if verbose:
            print(f"  R{r:<2} {mine.label} {hp_me:>3} - {hp_op:<3} {opp.label}   "
                  f"(you take {d_me['damage']}+{d_op['recoil']}r, deal {d_op['damage']}+{d_me['recoil']}r)")
        if hp_me == 0 or hp_op == 0:
            break
    return hp_me, hp_op


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("opponent", help="path to the other team's repo root")
    ap.add_argument("--rounds", type=int, default=10)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--matches", type=int, default=1, help="seeds seed..seed+matches-1")
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args(argv)

    opp_repo = Path(a.opponent).resolve()
    if not (opp_repo / "agent" / "gateway.py").is_file():
        print(f"  no agent/gateway.py under {opp_repo}", file=sys.stderr)
        return 2

    my_label = HERE.name.split("_")[-1][:12] or "YOU"
    op_label = opp_repo.name.split("_")[-1][:12] or "OPP"

    world = spar._load_world()
    my_cards, my_lineup = _load_deck(HERE)
    op_cards, op_lineup = _load_deck(opp_repo)

    mine = Worker(HERE, my_label)
    opp = Worker(opp_repo, op_label)
    print(f"\n  VERSUS — {my_label}  vs  {op_label}")
    print(f"  referee: this repo's kit ({HERE.name})   world: {world.world_id if hasattr(world, 'world_id') else 'df8c55dabb35'}")
    print(f"  {a.rounds} rounds x {a.matches} match(es)\n")

    try:
        wins = {my_label: 0, op_label: 0, "draw": 0}
        for m in range(a.matches):
            seed = a.seed + m
            hp_me, hp_op = run_match(mine, opp, my_cards, my_lineup, op_cards, op_lineup,
                                     world, a.rounds, seed, verbose=not a.quiet)
            who = my_label if hp_me > hp_op else op_label if hp_op > hp_me else "draw"
            wins[who] += 1
            print(f"  seed {seed:<3}  {my_label} {hp_me:>3} - {hp_op:<3} {op_label}   -> {who}\n")
    finally:
        mine.close()
        opp.close()

    print(f"  {'-' * 46}")
    print(f"  {my_label}: {wins[my_label]}   {op_label}: {wins[op_label]}   draw: {wins['draw']}")
    if wins[my_label] > wins[op_label]:
        print(f"  WINNER: {my_label}")
    elif wins[op_label] > wins[my_label]:
        print(f"  WINNER: {op_label}")
    else:
        print("  TIED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
