"""DECISIONS.md: the human-readable log of rejected / deferred / fixed findings and tie-break calls.

Machine-readable lines look like:
    - `3f2a9c0d1e4b` | rejected | F7 | 2026-01-31 | owner | reason text
Any other text in the file is kept as is. Fixed findings also keep their evidence in fixed.json for the
regression check.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import re
from typing import Any, Dict, List, Set

from . import findings as F

STATUSES = ("rejected", "deferred", "fixed", "reopened", "note")
_LINE = re.compile(r"^- `([0-9a-f]{6,64})` \| (\w+) \| ([^|]*) \| ([^|]*) \| ([^|]*) \| (.*)$")
HEADER = "# Decisions\n\nRejected, deferred and fixed findings, and tie-break calls. Later cycles skip these.\n\n"


def parse(path: str) -> List[Dict[str, str]]:
    if not os.path.exists(path):
        return []
    out = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            m = _LINE.match(line.rstrip("\n"))
            if m:
                fp, status, fid, date, by, reason = (x.strip() for x in m.groups())
                out.append({"fingerprint": fp, "status": status, "id": fid, "date": date, "by": by,
                            "reason": reason})
    return out


def closed(path: str) -> Set[str]:
    """Fingerprints that must not be re-raised: the last decision per fingerprint is rejected/deferred/fixed."""
    last: Dict[str, str] = {}
    for d in parse(path):
        if d["status"] != "note":
            last[d["fingerprint"]] = d["status"]
    return {fp for fp, st in last.items() if st in ("rejected", "deferred", "fixed")}


def record(path: str, finding: Dict[str, Any], status: str, by: str, reason: str, fixed_ledger: str = "") -> str:
    if status not in STATUSES:
        raise ValueError(f"status must be one of {', '.join(STATUSES)}")
    if not by.strip() or not reason.strip():
        raise ValueError("a decision needs --by and a reason")
    fp = F.fingerprint(finding)
    clean = lambda s: " ".join(str(s).replace("|", "/").split())
    line = (f"- `{fp}` | {status} | {clean(finding['id'])} | {_dt.date.today().isoformat()} | "
            f"{clean(by)} | {clean(reason)}\n")
    new = not os.path.exists(path)
    with open(path, "a", encoding="utf-8") as fh:
        if new:
            fh.write(HEADER)
        fh.write(line)
    if status == "reopened" and fixed_ledger:
        ledger = load_fixed(fixed_ledger)
        if ledger.pop(fp, None) is not None:
            with open(fixed_ledger, "w", encoding="utf-8") as fh:
                json.dump(ledger, fh, indent=2, sort_keys=True)
                fh.write("\n")
    if status == "fixed" and fixed_ledger:
        ledger = load_fixed(fixed_ledger)
        ledger[fp] = {"id": finding["id"], "claim": finding["claim"], "evidence": finding["evidence"]}
        with open(fixed_ledger, "w", encoding="utf-8") as fh:
            json.dump(ledger, fh, indent=2, sort_keys=True)
            fh.write("\n")
    return fp


def load_fixed(path: str) -> Dict[str, Dict[str, Any]]:
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def regressions(root: str, fixed_ledger: str) -> List[Dict[str, Any]]:
    """For each fixed finding: if every quoted snippet is still at its cited place, it may have regressed
    (or was never fixed). Returns one row per fixed finding with status 'holds' or 'suspect'."""
    rows = []
    for fp, item in sorted(load_fixed(fixed_ledger).items()):
        still = [F.check_evidence(root, e)[0] for e in item["evidence"]]
        rows.append({"fingerprint": fp, "id": item["id"], "status": "suspect" if all(still) else "holds",
                     "claim": item["claim"]})
    return rows
