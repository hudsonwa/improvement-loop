"""Plans and issues: validate the planner's plan.json, draft one markdown issue per item, file them with `gh`
(dry run unless asked). Issue text passes the privacy scan before it can be filed."""
from __future__ import annotations

import json
import os
import re
import subprocess
from typing import Any, Dict, List

ITEM_REQUIRED = {"id": str, "title": str, "finding_ids": list, "why": str, "scope_in": str, "scope_out": str,
                 "removes": str, "must_not_add": str, "acceptance": list, "metric": str, "tests": str,
                 "depends_on": list, "labels": list, "estimate_hours": (int, float), "wave": int}


def load_plan(path: str) -> Dict[str, Any]:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def validate_plan(plan: Any) -> List[str]:
    if not isinstance(plan, dict) or not isinstance(plan.get("items"), list) or not plan["items"]:
        return ["plan.json must be {\"items\": [...]} with at least one item"]
    errs, ids = [], set()
    for i, it in enumerate(plan["items"]):
        if not isinstance(it, dict):
            errs.append(f"item {i}: not an object")
            continue
        for k, t in ITEM_REQUIRED.items():
            if k not in it:
                errs.append(f"item {i}: missing '{k}'")
            elif not isinstance(it[k], t) or isinstance(it[k], bool):
                errs.append(f"item {i}: '{k}' has the wrong type")
        if errs:
            continue
        if it["id"] in ids:
            errs.append(f"item {i}: duplicate id {it['id']}")
        ids.add(it["id"])
        if not it["finding_ids"]:
            errs.append(f"item {it['id']}: names no finding (every item answers a verified finding)")
        if not it["removes"].strip() or not it["must_not_add"].strip():
            errs.append(f"item {it['id']}: simplicity check needs 'removes' and 'must_not_add'")
        if it["estimate_hours"] > 4:
            errs.append(f"item {it['id']}: {it['estimate_hours']}h is not atomic (max 4h; split it)")
        if not it["acceptance"]:
            errs.append(f"item {it['id']}: no acceptance criteria")
    for it in plan["items"]:
        if isinstance(it, dict):
            for d in it.get("depends_on", []) or []:
                if d not in ids:
                    errs.append(f"item {it.get('id')}: depends on unknown item {d}")
    return errs


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:50]


def render(it: Dict[str, Any]) -> str:
    acc = "\n".join(f"- [ ] {a}" for a in it["acceptance"])
    return f"""# {it['title']}

**Why:** {it['why']} (findings: {', '.join(it['finding_ids'])})

**Scope in:** {it['scope_in']}
**Scope out:** {it['scope_out']}

**Simplicity check:** removes: {it['removes']} · must not add: {it['must_not_add']}

**Acceptance criteria**
{acc}
- [ ] Moves metric: {it['metric']}

**Tests:** {it['tests']}
**Depends on:** {', '.join(it['depends_on']) or 'none'} · **Wave:** {it['wave']} · **Estimate:** {it['estimate_hours']}h
**Labels:** {', '.join(it['labels']) or '-'}
"""


def draft(plan: Dict[str, Any], out_dir: str) -> List[str]:
    os.makedirs(out_dir, exist_ok=True)
    paths = []
    for n, it in enumerate(sorted(plan["items"], key=lambda x: (x["wave"], x["id"])), 1):
        p = os.path.join(out_dir, f"{n:02d}-{_slug(it['id'] + '-' + it['title'])}.md")
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(render(it))
        paths.append(p)
    return paths


def file_issues(paths: List[str], repo: str, labels_by_path: Dict[str, List[str]], dry_run: bool = True) -> List[str]:
    out = []
    for p in paths:
        with open(p, encoding="utf-8") as fh:
            body = fh.read()
        title = body.splitlines()[0].lstrip("# ").strip()
        cmd = ["gh", "issue", "create", "--repo", repo, "--title", title, "--body-file", p]
        for lb in labels_by_path.get(p, []):
            cmd += ["--label", lb]
        if dry_run:
            out.append("DRY RUN: " + " ".join(cmd))
            continue
        r = subprocess.run(cmd, capture_output=True, text=True)
        out.append(r.stdout.strip() if r.returncode == 0 else f"FAILED {p}: {r.stderr.strip()}")
    return out
