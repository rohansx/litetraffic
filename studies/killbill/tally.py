#!/usr/bin/env python3
"""Tally every finished run in runs/: scenario (by its own assertion id), verdict, accepted requests, assertions."""
import collections
import glob
import json
import os

here = os.path.dirname(os.path.abspath(__file__))
summary = collections.defaultdict(list)
for d in sorted(glob.glob(os.path.join(here, "runs", "run_*"))):
    try:
        res = json.load(open(d + "/result.json"))
    except Exception:
        continue
    ids = [a["id"] for a in res.get("assertions", []) if a["id"] not in ("clean_response", "own_read_works", "no_foreign_data")]
    name = ids[0] if ids else "privacy-canary"
    stages = {}
    for j in (res.get("journeys") or {}).values():
        for s in j.get("stages", []):
            stages[s["name"]] = s["reached"]
    asserts = {a["id"]: a["status"] for a in res.get("assertions", [])}
    print(os.path.basename(d), name, res.get("verdict"), stages, asserts)
    summary[name].append((res.get("verdict"), tuple(stages.values())))
print()
for k, v in summary.items():
    print(k, collections.Counter(x[0] for x in v), "accepted per run:", [x[1][0] if x[1] else None for x in v])
