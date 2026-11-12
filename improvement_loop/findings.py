"""Findings: schema validation, fingerprints, evidence checks and ranking. All deterministic."""
from __future__ import annotations

import hashlib
import json
import os
import re
from typing import Any, Dict, List, Optional, Tuple

CATEGORIES = ("correctness", "security", "user_value", "simplicity")  # priority order
VERDICTS = ("unverified", "true", "partly", "false", "not_checkable")
ACCEPTED = ("true", "partly")

_REQUIRED = {
    "id": str, "role": str, "claim": str, "category": str, "evidence": list,
    "severity": int, "reach": int, "effort": int, "removes_complexity": bool,
}
_OPTIONAL = {"verdict": str, "root_cause": str, "fix_hint": str, "notes": str, "fingerprint": str,
             "verified_by": str, "evidence_ok": bool}


def load(path: str) -> List[Dict[str, Any]]:
    """Read a findings file: a JSON list, or an object with a `findings` list."""
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    if isinstance(data, dict):
        data = data.get("findings")
    if not isinstance(data, list):
        raise ValueError(f"{path}: expected a list of findings or {{\"findings\": [...]}}")
    return data


def save(path: str, findings: List[Dict[str, Any]]) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"findings": findings}, fh, indent=2, sort_keys=True)
        fh.write("\n")


def validate(f: Any) -> List[str]:
    """Return a list of schema errors for one finding (empty when valid)."""
    if not isinstance(f, dict):
        return ["finding is not an object"]
    errs = []
    for k, t in _REQUIRED.items():
        if k not in f:
            errs.append(f"missing '{k}'")
        elif not isinstance(f[k], t) or (t is int and isinstance(f[k], bool)):
            errs.append(f"'{k}' must be {t.__name__}")
    for k, t in _OPTIONAL.items():
        if k in f and not isinstance(f[k], t):
            errs.append(f"'{k}' must be {t.__name__}")
    for k in f:
        if k not in _REQUIRED and k not in _OPTIONAL:
            errs.append(f"unknown field '{k}'")
    if errs:
        return errs
    if f["category"] not in CATEGORIES:
        errs.append(f"'category' must be one of {', '.join(CATEGORIES)}")
    for k in ("severity", "reach", "effort"):
        if not 1 <= f[k] <= 5:
            errs.append(f"'{k}' must be 1-5")
    if f.get("verdict", "unverified") not in VERDICTS:
        errs.append(f"'verdict' must be one of {', '.join(VERDICTS)}")
    if not f["claim"].strip():
        errs.append("'claim' is empty")
    if not f["evidence"]:
        errs.append("'evidence' needs at least one {file, line, quote}")
    for i, e in enumerate(f["evidence"]):
        if not isinstance(e, dict) or set(e) - {"file", "line", "quote"}:
            errs.append(f"evidence[{i}] must be an object with file, line, quote")
            continue
        if not isinstance(e.get("file"), str) or not e["file"]:
            errs.append(f"evidence[{i}].file must be a non-empty string")
        if not isinstance(e.get("line"), int) or isinstance(e.get("line"), bool) or e["line"] < 1:
            errs.append(f"evidence[{i}].line must be an integer >= 1")
        if not isinstance(e.get("quote"), str) or not e["quote"].strip():
            errs.append(f"evidence[{i}].quote must be a non-empty string")
    return errs


def validate_all(findings: List[Any]) -> List[str]:
    errs = []
    ids = set()
    for i, f in enumerate(findings):
        for e in validate(f):
            errs.append(f"finding {i} ({f.get('id', '?') if isinstance(f, dict) else '?'}): {e}")
        if isinstance(f, dict) and f.get("id") in ids:
            errs.append(f"finding {i}: duplicate id '{f['id']}'")
        if isinstance(f, dict):
            ids.add(f.get("id"))
    return errs


# ------------------------------------------------------------------ fingerprints

_STOP = set("a an the of to in on for and or is are be by with it this that from as at not no can".split())


def fingerprint(f: Dict[str, Any]) -> str:
    """Stable id for 'the same problem': category + first cited file + claim keywords (order-free)."""
    if f.get("fingerprint"):
        return f["fingerprint"]
    words = sorted({w for w in re.findall(r"[a-z0-9_]+", f["claim"].lower()) if w not in _STOP and len(w) > 2})
    files = sorted({_norm_path(e["file"]) for e in f["evidence"]})
    key = "|".join([f["category"], files[0] if files else "", " ".join(words)])
    return hashlib.sha256(key.encode()).hexdigest()[:12]


def _norm_path(p: str) -> str:
    return os.path.normpath(p)


def dedupe(findings: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Merge findings with the same fingerprint: keep the first, union evidence, max severity/reach."""
    out: Dict[str, Dict[str, Any]] = {}
    for f in findings:
        fp = fingerprint(f)
        if fp not in out:
            g = dict(f, fingerprint=fp, evidence=list(f["evidence"]))
            out[fp] = g
            continue
        g = out[fp]
        for e in f["evidence"]:
            if e not in g["evidence"]:
                g["evidence"].append(e)
        g["severity"] = max(g["severity"], f["severity"])
        g["reach"] = max(g["reach"], f["reach"])
        g["effort"] = min(g["effort"], f["effort"])
        g["removes_complexity"] = g["removes_complexity"] or f["removes_complexity"]
        roles = set(g["role"].split("+")) | {f["role"]}
        g["role"] = "+".join(sorted(roles))
    return list(out.values())


# ------------------------------------------------------------------ evidence


def _ws(s: str) -> str:
    return " ".join(s.split())


def check_evidence(root: str, e: Dict[str, Any], window: int = 2) -> Tuple[bool, str]:
    """True when root/file exists inside root, has the line, and the quote is on it (+/- window lines)."""
    root_abs = os.path.realpath(root)
    path = os.path.realpath(os.path.join(root_abs, e["file"]))
    if path != root_abs and not path.startswith(root_abs + os.sep):
        return False, "path escapes the project root"
    if not os.path.isfile(path):
        return False, "file not found"
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            lines = fh.read().splitlines()
    except OSError as ex:
        return False, f"unreadable: {ex}"
    n = e["line"]
    if n > len(lines):
        return False, f"line {n} > file length {len(lines)}"
    lo, hi = max(0, n - 1 - window), min(len(lines), n + window)
    if _ws(e["quote"]) in _ws(" ".join(lines[lo:hi])):
        return True, "ok"
    return False, f"quote not found near line {n}"


def verify_evidence(root: str, findings: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Annotate each finding with evidence_ok and per-item results. Does not set the verdict."""
    out = []
    for f in findings:
        results = [check_evidence(root, e) for e in f["evidence"]]
        g = dict(f, evidence_ok=all(ok for ok, _ in results))
        g.setdefault("verdict", "unverified")
        if not g["evidence_ok"]:
            bad = [f"{e['file']}:{e['line']}: {why}" for e, (ok, why) in zip(f["evidence"], results) if not ok]
            g["notes"] = (g.get("notes", "") + " | evidence: " + "; ".join(bad)).strip(" |")
        out.append(g)
    return out


# ------------------------------------------------------------------ ranking


def score(f: Dict[str, Any]) -> int:
    return f["severity"] * f["reach"]


def is_urgent(f: Dict[str, Any]) -> bool:
    return f["category"] in ("correctness", "security") and f["severity"] >= 4


def rank_key(f: Dict[str, Any]):
    return (
        0 if is_urgent(f) else 1,
        -score(f),
        0 if f["removes_complexity"] else 1,
        f["effort"],
        CATEGORIES.index(f["category"]),
        f["id"],
    )


def rank(findings: List[Dict[str, Any]], closed: Optional[set] = None) -> Tuple[List[Dict], List[Dict]]:
    """Return (ranked, excluded). Only evidence-checked findings with verdict true/partly are ranked;
    anything whose fingerprint is closed in DECISIONS.md is excluded."""
    closed = closed or set()
    ranked, excluded = [], []
    for f in dedupe(findings):
        reason = None
        if fingerprint(f) in closed:
            reason = "already decided (DECISIONS.md)"
        elif not f.get("evidence_ok"):
            reason = "evidence check failed or not run"
        elif f.get("verdict") not in ACCEPTED:
            reason = f"verdict {f.get('verdict', 'unverified')}"
        if reason:
            excluded.append(dict(f, excluded_because=reason))
        else:
            ranked.append(f)
    ranked.sort(key=rank_key)
    return ranked, excluded


def ranked_markdown(ranked: List[Dict], excluded: List[Dict], top: int = 10) -> str:
    hdr = "| # | Id | Fingerprint | Category | Sev | Reach | Score | Effort | Removes cx | Verdict | Claim | Evidence |"
    rows = [hdr, "|" + "---|" * 12]
    for i, f in enumerate(ranked, 1):
        ev = "; ".join(f"`{e['file']}:{e['line']}`" for e in f["evidence"])
        claim = f["claim"].replace("|", "\\|").replace("\n", " ")
        flag = " **(urgent)**" if is_urgent(f) else ""
        rows.append(f"| {i} | {f['id']} | `{f['fingerprint']}` | {f['category']}{flag} | {f['severity']} | "
                    f"{f['reach']} | {score(f)} | {f['effort']} | {'yes' if f['removes_complexity'] else 'no'} | "
                    f"{f['verdict']} | {claim} | {ev} |")
    parts = [f"# Ranked findings\n\nTop {min(top, len(ranked))} of {len(ranked)} verified findings first. "
             "Order: correctness/security severity >= 4, then severity x reach, then removes complexity, "
             "then lower effort.\n", "\n".join(rows[:top + 2])]
    if len(ranked) > top:
        parts += ["\n## The rest\n", "\n".join(rows[:2] + rows[top + 2:])]
    if excluded:
        parts.append("\n## Not ranked\n")
        parts += [f"- {f['id']} (`{fingerprint(f)}`): {f['excluded_because']}: {f['claim'][:120]}" for f in excluded]
    return "\n".join(parts) + "\n"
