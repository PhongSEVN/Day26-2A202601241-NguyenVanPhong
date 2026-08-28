#!/usr/bin/env python3
"""_versus_worker.py — one side of a cross-repo duel, run inside THAT repo.

Reads JSON-line requests on stdin, writes JSON-line replies on stdout. Loads
`agent.gateway` + `eval.prosecute` from the current working directory, so each
side runs against its OWN kit. Driven by `versus.py` in the other repo.

ops: new_gateway | update_ctx | note_card | note_result | decide | prosecute
"""
from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path.cwd()))
_G = importlib.import_module("agent.gateway")
_P = importlib.import_module("eval.prosecute")


class _Ctx:
    def __init__(self, d):
        self._set(d)

    def _set(self, d):
        self.act = d.get("act")
        self.sub = d.get("sub")
        self.scopes = frozenset(d.get("scopes", []))
        self.credits = d.get("credits", 100)
        self.round = d.get("round", 1)
        self.call_index = d.get("call_index", 0)
        self.leases = tuple(d.get("leases", ()))
        self.history = tuple(d.get("history", ()))

    def emit(self, *a, **k):  # own_telemetry — never scored, safe to drop here
        pass


_gw = None
_ctx = None


def _handle(m):
    global _gw, _ctx
    op = m["op"]
    if op == "new_gateway":
        _ctx = _Ctx(m["ctx"])
        _gw = _G.Gateway(_ctx)
        return {"ok": True}
    if op == "update_ctx":
        _ctx._set(m["ctx"])
        return {"ok": True}
    if op == "note_card":
        if hasattr(_gw, "note_card"):
            _gw.note_card(m["server"], m["card"])
        return {"ok": True}
    if op == "note_result":
        if hasattr(_gw, "note_result"):
            _gw.note_result(m["anchor"], m["etag"])
        return {"ok": True}
    if op == "decide":
        c = m["cmd"]
        cmd = _G.Command(
            cmd_id=c["cmd_id"], kind=c["kind"], raw=c.get("raw", ""),
            server=c["server"], tool=c["tool"], args=dict(c.get("args", {})),
            fields=tuple(c.get("fields", ())), headers=dict(c.get("headers", {})),
            lease_id=c.get("lease_id"), call_index=c.get("call_index", 0),
        )
        try:
            d = _gw.decide(cmd)
        except Exception as e:  # a raise -> the driver emulates CONTRACTS 4.1
            return {"raised": f"{type(e).__name__}: {e}"}
        call = getattr(d, "call", None)
        cd = None
        if call is not None:
            cd = call.to_dict() if hasattr(call, "to_dict") else dict(call)
        return {
            "verdict": d.verdict, "reason": getattr(d, "reason", None), "call": cd,
            "quarantine": getattr(d, "quarantine", False), "note": getattr(d, "note", None),
        }
    if op == "prosecute":
        try:
            r = _P.prosecute(m["trace"], m["answer"], m["card"]) or {}
        except Exception as e:
            return {"claims": [], "error": f"{type(e).__name__}: {e}"}
        cl = r.get("claims", [])
        return {"claims": cl if isinstance(cl, list) else []}
    return {"error": f"unknown op {op!r}"}


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            out = _handle(json.loads(line))
        except Exception as e:
            out = {"error": f"worker: {type(e).__name__}: {e}"}
        sys.stdout.write(json.dumps(out) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
