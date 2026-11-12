"""Lenses: the dimensions a cycle critiques, plus project checks and per-lens scores.

A lens is a markdown prompt with a small header:
    ---
    core: true            # always on in full mode
    lite: true            # also on in lite mode
    detect: <globs>       # otherwise on only when a tracked file matches one of these globs
    every: 2              # and only every Nth cycle (cycle 1 always runs every lens that applies)
    ---
Built-in lenses live in the package (prompts/lenses/); a project adds or overrides lenses in
`.improvement-loop/lenses/*.md`, which never needs to be published. The selection for a cycle is computed once,
when the cycle starts, and stored in state.json so the critique guard never changes under the critics.

Checks are project commands (tests, coverage, audits, benchmarks) whose failure becomes a finding with
machine-checked evidence, so deterministic signals reach the ranking without an LLM's opinion.
"""
from __future__ import annotations

import glob
import os
import subprocess
from typing import Any, Dict, List, Optional, Tuple

from . import decisions as D
from . import findings as F
from . import metrics as M

PROMPTS = os.path.join(os.path.dirname(__file__), "prompts")
BUILTIN = os.path.join(PROMPTS, "lenses")
CONTRACT_MARK = "{{contract}}"


def parse(path: str) -> Dict[str, Any]:
    """Read a lens file: header keys plus the prompt body."""
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    meta: Dict[str, Any] = {"core": False, "lite": False, "detect": [], "every": 1}
    if text.startswith("---\n") and "\n---\n" in text[4:]:
        head, text = text[4:].split("\n---\n", 1)
        for line in head.splitlines():
            if ":" not in line:
                continue
            k, v = (x.strip() for x in line.split(":", 1))
            if k in ("core", "lite"):
                meta[k] = v.lower() == "true"
            elif k == "detect":
                meta[k] = v.split()
            elif k == "every":
                meta[k] = max(1, int(v))
    meta["name"] = os.path.splitext(os.path.basename(path))[0]
    meta["path"] = path
    meta["body"] = text.lstrip("\n")
    return meta


def local_dir(root: str) -> str:
    return os.path.join(root, ".improvement-loop", "lenses")


def available(root: str) -> Dict[str, Dict[str, Any]]:
    """Built-in lenses, then project lenses (a project lens overrides a built-in of the same name)."""
    out: Dict[str, Dict[str, Any]] = {}
    for d, origin in ((BUILTIN, "built-in"), (local_dir(root), "project")):
        for p in sorted(glob.glob(os.path.join(d, "*.md"))):
            lens = parse(p)
            lens["origin"] = origin
            out[lens["name"]] = lens
    return out


def project_files(root: str) -> List[str]:
    """What the project is made of: tracked + untracked-not-ignored files, or a plain walk outside git."""
    try:
        return M.Source(root, M.WORKTREE).files()
    except (RuntimeError, OSError):
        out = []
        for d, dirs, files in os.walk(root):
            dirs[:] = [x for x in dirs if not x.startswith(".")]
            out += [os.path.relpath(os.path.join(d, f), root).replace(os.sep, "/") for f in files]
        return out


def select(root: str, cfg: Dict[str, Any], cycle: int,
           files: Optional[List[str]] = None) -> List[Tuple[str, str]]:
    """(lens, reason) for this cycle. An explicit `critics` list in config wins (backward compatible)."""
    if cfg.get("critics"):
        return [(c, "config: critics") for c in cfg["critics"]]
    lenses, retired = available(root), set(cfg.get("retired") or [])
    wanted = cfg.get("lenses")
    files = project_files(root) if files is None else files
    out = []
    for name, lens in lenses.items():
        if name in retired:
            continue
        if wanted is not None:
            if name in wanted:
                out.append((name, "config: lenses"))
            continue
        if cfg.get("mode") == "lite":
            if lens["lite"]:
                out.append((name, "lite"))
            continue
        if lens["core"]:
            out.append((name, "core"))
            continue
        hit = next((f for f in files if M.match(f, lens["detect"])), None) if lens["detect"] else None
        if lens["origin"] == "project" and not lens["detect"]:
            hit = "(project lens)"
        if hit is None:
            continue
        if (cycle - 1) % lens["every"]:
            continue
        out.append((name, f"detected {hit}" + (f", every {lens['every']} cycles" if lens["every"] > 1 else "")))
    if wanted is not None:
        missing = [w for w in wanted if w not in lenses]
        if missing:
            raise ValueError("config lenses not found: " + ", ".join(missing))
    return out


def prompt_text(root: str, name: str) -> Optional[str]:
    """A lens or role prompt with the shared output contract filled in; None if there is no such prompt."""
    lens = available(root).get(name)
    if lens:
        body = lens["body"]
    else:
        p = os.path.join(PROMPTS, name + ".md")
        if name.startswith("_") or not os.path.exists(p):
            return None
        with open(p, encoding="utf-8") as fh:
            body = fh.read()
    if CONTRACT_MARK in body:
        with open(os.path.join(PROMPTS, "_contract.md"), encoding="utf-8") as fh:
            body = body.replace(CONTRACT_MARK, fh.read().rstrip("\n"))
    return body


def prompt_names(root: str) -> List[str]:
    roles = [n[:-3] for n in os.listdir(PROMPTS) if n.endswith(".md") and not n.startswith("_")]
    return sorted(set(roles) | set(available(root)))


# ------------------------------------------------------------------ checks


def validate_checks(checks: Any) -> List[str]:
    if not isinstance(checks, list):
        return ["'checks' must be a list"]
    errs, seen = [], set()
    for c in checks:
        if not isinstance(c, dict) or not isinstance(c.get("name"), str) or not c["name"].strip():
            errs.append("each check needs a string 'name'")
            continue
        n = c["name"]
        if n in seen:
            errs.append(f"duplicate check '{n}'")
        seen.add(n)
        if not (isinstance(c.get("cmd"), list) and c["cmd"] and all(isinstance(x, str) for x in c["cmd"])):
            errs.append(f"{n}: 'cmd' must be a non-empty list of strings (no shell)")
        if c.get("category", "correctness") not in F.CATEGORIES:
            errs.append(f"{n}: category must be one of {', '.join(F.CATEGORIES)}")
        for k in ("severity", "reach"):
            if not isinstance(c.get(k, 3), int) or not 1 <= c.get(k, 3) <= 5:
                errs.append(f"{n}: {k} must be 1-5")
    return errs


def run_checks(root: str, checks: List[Dict[str, Any]], cycle_dir: str) -> List[Dict[str, Any]]:
    """Run each check; write checks/<name>.log; return one finding per failing check (none when all pass)."""
    out_dir = os.path.join(cycle_dir, "checks")
    os.makedirs(out_dir, exist_ok=True)
    found = []
    for c in checks:
        name, cmd, limit = c["name"], c["cmd"], int(c.get("timeout", 600))
        try:
            p = subprocess.run(cmd, cwd=root, capture_output=True, timeout=limit)
            code, body = str(p.returncode), (p.stdout + p.stderr).decode("utf-8", errors="replace")
        except subprocess.TimeoutExpired:
            code, body = "timeout", f"no result after {limit} s\n"
        except OSError as e:
            code, body = "not-run", f"{e}\n"
        head = f"exit {code}: {' '.join(cmd)}"
        log = os.path.join(out_dir, f"{name}.log")
        with open(log, "w", encoding="utf-8") as fh:
            fh.write(head + "\n" + body)
        c["_result"] = code
        if code == "0":
            continue
        rel = os.path.relpath(log, root).replace(os.sep, "/")
        found.append({"id": f"CHECK-{name}", "role": f"check-{name}",
                      "claim": c.get("claim") or f"Check '{name}' fails ({head}); see {rel}.",
                      "category": c.get("category", "correctness"),
                      "evidence": [{"file": rel, "line": 1, "quote": head}],
                      "severity": c.get("severity", 3), "reach": c.get("reach", 3), "effort": 2,
                      "removes_complexity": False, "notes": f"lens: {c.get('lens', 'checks')}"})
    return found


# ------------------------------------------------------------------ scores


def scores(loop_dir: str) -> List[Dict[str, Any]]:
    """Per lens across every cycle: raised, accepted, rejected by the verifier, fixed, precision."""
    fixed = {d["fingerprint"] for d in D.parse(os.path.join(loop_dir, "DECISIONS.md")) if d["status"] == "fixed"}
    rows: Dict[str, Dict[str, Any]] = {}
    for vp in sorted(glob.glob(os.path.join(loop_dir, "cycle-*", "verified.json"))):
        cyc = os.path.basename(os.path.dirname(vp))
        for f in F.load(vp):
            for role in f["role"].split("+"):
                r = rows.setdefault(role, {"lens": role, "raised": 0, "accepted": 0, "false": 0, "unclear": 0,
                                           "fixed": 0, "sev_sum": 0, "cycles": set()})
                r["raised"] += 1
                r["cycles"].add(cyc)
                v = f.get("verdict", "unverified")
                if v in F.ACCEPTED and f.get("evidence_ok", True):
                    r["accepted"] += 1
                    r["sev_sum"] += f["severity"]
                elif v == "false":
                    r["false"] += 1
                else:
                    r["unclear"] += 1
                if F.fingerprint(f) in fixed:
                    r["fixed"] += 1
    out = []
    for r in rows.values():
        judged = r["accepted"] + r["false"]
        r["precision"] = round(r["accepted"] / judged, 2) if judged else None
        r["mean_severity"] = round(r.pop("sev_sum") / r["accepted"], 1) if r["accepted"] else None
        r["cycles"] = len(r["cycles"])
        r["advice"] = ("consider retiring (low precision)" if judged >= 5 and r["precision"] < 0.4 else
                       "keep" if judged >= 5 else "too few judged findings")
        out.append(r)
    return sorted(out, key=lambda r: (-(r["precision"] or 0), -r["accepted"], r["lens"]))


def scores_markdown(rows: List[Dict[str, Any]]) -> str:
    lines = ["# Lens scores", "",
             "Across all cycles. Precision = accepted / (accepted + false). Retire a lens with `retired` in config.",
             "", "| Lens | Cycles | Raised | Accepted | False | Unclear | Fixed | Precision | Mean sev | Advice |",
             "|---|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        lines.append(f"| {r['lens']} | {r['cycles']} | {r['raised']} | {r['accepted']} | {r['false']} | "
                     f"{r['unclear']} | {r['fixed']} | {r['precision'] if r['precision'] is not None else '-'} | "
                     f"{r['mean_severity'] if r['mean_severity'] is not None else '-'} | {r['advice']} |")
    return "\n".join(lines) + "\n"
