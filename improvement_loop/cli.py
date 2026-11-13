"""`loop`: the improvement-loop orchestrator CLI. Run `loop -h` or `loop <command> -h`."""
from __future__ import annotations

import argparse
import json
import os
import sys
from typing import List, Optional

from . import __version__
from . import decisions as D
from . import findings as F
from . import issues as I
from . import lenses as L
from . import metrics as M
from . import privacy as P
from .state import APPROVALS, Loop, LoopError


def _out(obj) -> None:
    print(json.dumps(obj, indent=2, sort_keys=True))


def _write_json(path: str, obj) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, indent=2, sort_keys=True)
        fh.write("\n")


# ------------------------------------------------------------------ commands


def cmd_init(a, lp: Loop) -> int:
    st = lp.init(a.mode)
    print(f"initialised {lp.dir} (mode {a.mode}); cycle {st['cycle']}, step {st['step']}")
    print("lenses this cycle:")
    for x in lp.lens_selection():
        print(f"  {x['lens']:22} {x['why']}")
    print("each lens is a critic: `loop prompt <lens>`; project checks: `loop check`")
    return 0


def cmd_status(a, lp: Loop) -> int:
    st = lp.load()
    unmet = lp.guards(st)
    info = {"cycle": st["cycle"], "step": st["step"], "mode": lp.config()["mode"],
            "budget_used": st["budget_used"], "budget": lp.config()["budget"],
            "approvals_this_cycle": [x for x in st["approvals"] if x["cycle"] == st["cycle"]],
            "can_advance": not unmet, "unmet": unmet, "critics": lp.critics(),
            "cycle_dir": _rel(lp.cycle_dir(st), lp.root)}
    if st["step"] == "audit":
        info["stop_reasons"] = lp.stop_reasons(st)
    _out(info)
    return 0


def cmd_advance(a, lp: Loop) -> int:
    st = lp.advance()
    print(f"cycle {st['cycle']}, step {st['step']}")
    return 0


def cmd_approve(a, lp: Loop) -> int:
    if not a.yes:
        if not sys.stdin.isatty():
            raise LoopError("approvals are the owner's: run this in a terminal (or pass --yes as the owner)")
        ans = input(f"{a.by}, approve '{a.gate}' for this cycle? type 'yes': ")
        if ans.strip().lower() != "yes":
            print("not approved")
            return 1
    lp.approve(a.gate, a.by, a.note or "")
    print(f"approved '{a.gate}' by {a.by}")
    return 0


def cmd_spend(a, lp: Loop) -> int:
    st = lp.spend(a.minutes, a.cost)
    spent = lp.budget_spent(st)
    print(f"budget used: {st['budget_used']}" + (f"  (SPENT: {', '.join(spent)})" if spent else ""))
    return 0


def cmd_validate(a, lp: Loop) -> int:
    bad = 0
    for p in a.files:
        try:
            errs = F.validate_all(F.load(p))
        except (OSError, ValueError, json.JSONDecodeError) as e:
            errs = [str(e)]
        for e in errs:
            print(f"{p}: {e}")
        bad += bool(errs)
        if not errs:
            print(f"{p}: ok")
    return 1 if bad else 0


def cmd_verify(a, lp: Loop) -> int:
    """Merge the cycle's findings, dedupe, check every evidence quote; keep verdicts already set."""
    fdir = lp.path("findings")
    allf: List[dict] = []
    for n in sorted(os.listdir(fdir)) if os.path.isdir(fdir) else []:
        if n.endswith(".json"):
            fs = F.load(os.path.join(fdir, n))
            errs = F.validate_all(fs)
            if errs:
                raise LoopError(f"findings/{n} is not schema-valid:\n  " + "\n  ".join(errs[:10]))
            allf += fs
    if not allf:
        raise LoopError("no findings in " + fdir)
    out = F.verify_evidence(lp.root, F.dedupe(allf))
    vp = lp.path("verified.json")
    if os.path.exists(vp):
        old = {F.fingerprint(f): f for f in F.load(vp)}
        for f in out:
            prev = old.get(f["fingerprint"])
            if prev and prev.get("verdict", "unverified") != "unverified":
                for k in ("verdict", "verified_by", "notes", "severity", "reach"):
                    if k in prev:
                        f[k] = prev[k]
    seen = set()
    for f in out:  # ids must stay unique after merging critics
        if f["id"] in seen:
            f["id"] = f"{f['id']}-{f['fingerprint'][:4]}"
        seen.add(f["id"])
    F.save(vp, out)
    bad = sum(1 for f in out if not f["evidence_ok"])
    print(f"{len(allf)} findings -> {len(out)} after dedupe; evidence failed for {bad}; wrote {_rel(vp)}")
    print("next: the verifier sets each verdict (`loop set-verdict ID true|partly|false|not_checkable --by ROLE`)")
    return 0


def _require_step(lp: Loop, step: str) -> None:
    st = lp.load()
    if st["step"] != step:
        raise LoopError(f"only allowed at the '{step}' step (now '{st['step']}')")


def cmd_set_verdict(a, lp: Loop) -> int:
    _require_step(lp, "verify")
    vp = lp.path("verified.json")
    fs = F.load(vp)
    hit = [f for f in fs if f["id"] == a.id]
    if not hit:
        raise LoopError(f"no finding {a.id} in verified.json")
    f = hit[0]
    if a.by in f["role"].split("+"):
        raise LoopError(f"the verifier must not be the finding's author ({f['role']})")
    if a.verdict not in F.VERDICTS[1:]:
        raise LoopError("verdict must be one of " + ", ".join(F.VERDICTS[1:]))
    f["verdict"], f["verified_by"] = a.verdict, a.by
    if a.severity:
        f["severity"] = a.severity
    if a.note:
        f["notes"] = (f.get("notes", "") + " | " + a.note).strip(" |")
    F.save(vp, fs)
    print(f"{a.id}: {a.verdict}")
    return 0


def cmd_rank(a, lp: Loop) -> int:
    src = a.file or lp.path("verified.json")
    ranked, excluded = F.rank(F.load(src), D.closed(lp.decisions_path))
    md = F.ranked_markdown(ranked, excluded, a.top)
    if a.file and not lp.exists:
        print(md)
        return 0
    _require_step(lp, "rank")
    if lp.approved(lp.load(), "ranking"):
        raise LoopError("the ranking is already approved; it cannot be rewritten this cycle")
    if a.file:
        raise LoopError("--file only works outside an initialised loop")
    with open(lp.path("RANKED.md"), "w", encoding="utf-8") as fh:
        fh.write(md)
    F.save(lp.path("ranked.json"), ranked)
    print(md)
    print(f"wrote {_rel(lp.path('RANKED.md'))}; the owner approves with `loop approve ranking --by NAME`")
    return 0


def cmd_decide(a, lp: Loop) -> int:
    fs = F.load(lp.path("verified.json"))
    hit = [f for f in fs if f["id"] == a.id or F.fingerprint(f) == a.id]
    if not hit:
        raise LoopError(f"no finding {a.id} in this cycle's verified.json")
    fp = D.record(lp.decisions_path, hit[0], a.status, a.by, a.reason, lp.fixed_path)
    print(f"recorded {a.status} for {a.id} ({fp}) in {_rel(lp.decisions_path)}")
    return 0


def cmd_regress(a, lp: Loop) -> int:
    rows = D.regressions(lp.root, lp.fixed_path)
    for r in rows:
        print(f"{r['status']:8} {r['id']} ({r['fingerprint']}): {r['claim'][:100]}")
    if not rows:
        print("no fixed findings recorded yet")
    return 1 if any(r["status"] == "suspect" for r in rows) else 0


def cmd_stop_check(a, lp: Loop) -> int:
    reasons = lp.stop_reasons()
    if reasons:
        print("stop rule met (owner decides: `loop approve stop --by NAME` at the audit step):")
        for r in reasons:
            print("  - " + r)
        return 0
    print("keep going: verified findings at or above the stop severity remain, and budget/cycles are left")
    return 1


def _metrics(lp: Loop):
    ms = lp.config().get("metrics") or M.DEFAULT_METRICS
    errs = [e for m in ms for e in M.validate_metric(m)]
    if errs:
        raise LoopError("bad metrics config:\n  " + "\n  ".join(errs))
    return ms


def cmd_gate(a, lp: Loop) -> int:
    ms = _metrics(lp)
    base = M.collect(M.Source(lp.root, a.base), ms)
    head = M.collect(M.Source(lp.root, a.head), ms)
    just = {}
    if a.justify:
        with open(a.justify, encoding="utf-8") as fh:
            just = M.read_justifications(fh.read())
    rows, fails = M.gate(ms, base, head, just)
    fails += ["regression: " + r["id"] for r in D.regressions(lp.root, lp.fixed_path) if r["status"] == "suspect"]
    md = M.markdown(rows, a.base, a.head)
    print(md + f"\nGATE: {'FAIL (' + ', '.join(fails) + ')' if fails else 'PASS'}")
    if lp.exists:
        _write_json(lp.path("gate.json"), {"pass": not fails, "fails": fails, "base": a.base, "head": a.head, "stamp": lp.stamp(),
                                           "rows": rows})
        with open(lp.path("METRICS.md"), "w", encoding="utf-8") as fh:
            fh.write(f"# Metrics: {a.base} -> {a.head}\n\n" + md)
    return 1 if fails else 0


def cmd_privacy(a, lp: Loop) -> int:
    deny_path = a.denylist or os.environ.get("IMPROVEMENT_LOOP_DENYLIST") or (lp.config().get("denylist")
                                                                            if lp.exists else None)
    if deny_path and not os.path.exists(deny_path):
        raise LoopError(f"denylist not found: {deny_path}")
    deny = P.load_denylist(deny_path)
    skip = set()
    if deny_path:
        rel = _rel(os.path.abspath(deny_path), lp.root)
        if not rel.startswith("..") and not os.path.isabs(rel):
            print(f"WARNING: the denylist {rel} is inside the repo; keep it outside (it names private things)")
            skip.add(rel)
    if a.text:
        hits = []
        for p in a.text:
            with open(p, encoding="utf-8", errors="replace") as fh:
                hits += P.scan_text(p, fh.read(), deny)
    else:
        hits = P.scan_repo(lp.root, deny, history=a.history, skip=skip)
        if a.base:
            with M.checkout(lp.root, a.base) as old:
                before = P.scan_repo(old, deny, skip=skip)
            print(f"baseline {a.base}: {len(before)} existing hits ignored; only new hits count")
            hits = P.new_hits([h for h in hits if not h["file"].startswith("git-log:")], before) + \
                [h for h in hits if h["file"].startswith("git-log:")]
    for h in hits:
        print(f"{h['file']}:{h['line']}: {h['kind']}: {h['match']}")
    verdict = {"base": a.base or "", "stamp": lp.stamp() if lp.exists else None, "pass": not hits, "hits": len(hits), "denylist_terms": len(deny), "history": a.history or ""}
    print(("PRIVACY: PASS" if not hits else f"PRIVACY: FAIL ({len(hits)} hits)") +
          ("" if deny else "  (no denylist given: only generic secret/email/IP/home-path checks ran)"))
    if lp.exists and not a.text:
        _write_json(lp.path("privacy.json"), verdict)
    return 0 if not hits else 1


def cmd_issues(a, lp: Loop) -> int:
    plan_path = a.plan or lp.path("plan.json")
    plan = I.load_plan(plan_path)
    errs = I.validate_plan(plan)
    if errs:
        raise LoopError("plan.json invalid:\n  " + "\n  ".join(errs))
    out_dir = lp.path("issues")
    if a.action == "draft":
        for p in I.draft(plan, out_dir):
            print(_rel(p))
        return 0
    paths = I.draft(plan, out_dir)
    deny = P.load_denylist(a.denylist or os.environ.get("IMPROVEMENT_LOOP_DENYLIST") or lp.config().get("denylist"))
    hits = []
    for p in paths:
        with open(p, encoding="utf-8") as fh:
            hits += P.scan_text(p, fh.read(), deny)
    if hits:
        for h in hits:
            print(f"{h['file']}:{h['line']}: {h['kind']}: {h['match']}")
        raise LoopError("issue text failed the privacy scan; nothing filed")
    items = sorted(plan["items"], key=lambda x: (x["wave"], x["id"]))
    labels = {p: it["labels"] for p, it in zip(paths, items)}
    for line in I.file_issues(paths, a.repo, labels, dry_run=not a.yes):
        print(line)
    return 0


def cmd_prompt(a, lp: Loop) -> int:
    names = L.prompt_names(lp.root)
    if a.list or not a.role:
        print("\n".join(names))
        return 0
    text = L.prompt_text(lp.root, a.role)
    if text is None:
        raise LoopError(f"no prompt '{a.role}'; one of {', '.join(names)}")
    print(text)
    return 0


def cmd_run(a, lp: Loop) -> int:
    from . import run as R
    lp.load()
    if a.service:
        print(R.service_text(lp.root, a.every_hours))
        return 0
    if a.detach:
        pid = R.detach(lp.root, lp.config().get("priority") or "background")
        print(f"loop run started in the background (pid {pid}); progress in .improvement-loop/RUN.md, "
              f"stop with `touch .improvement-loop/STOP`")
        return 0
    err = R.acquire_lock(lp)
    if err:
        raise LoopError(err)
    try:
        return R.Runner(lp).run(a.max_steps)
    finally:
        R.release_lock(lp)


def cmd_lenses(a, lp: Loop) -> int:
    if a.scores:
        rows = L.scores(lp.dir)
        md = L.scores_markdown(rows)
        print(md)
        if lp.exists:
            with open(os.path.join(lp.dir, "LENS-SCORES.md"), "w", encoding="utf-8") as fh:
                fh.write(md)
        return 0
    cfg = lp.config()
    active = {x["lens"]: x["why"] for x in (lp.lens_selection() if lp.exists else
                                            [{"lens": n, "why": w} for n, w in L.select(lp.root, cfg, 1)])}
    for name, lens in L.available(lp.root).items():
        why = active.get(name) or ("retired" if name in (cfg.get("retired") or []) else "not this cycle")
        print(f"{'*' if name in active else ' '} {name:22} {lens['origin']:9} {why}")
    for name in active:
        if name not in L.available(lp.root):
            print(f"* {name:22} {'config':9} {active[name]}")
    return 0


def cmd_check(a, lp: Loop) -> int:
    _require_step(lp, "critique")
    checks = lp.config().get("checks") or []
    errs = L.validate_checks(checks)
    if errs:
        raise LoopError("bad checks config:\n  " + "\n  ".join(errs))
    if not checks:
        print("no checks configured: add {\"name\", \"cmd\": [...]} entries to `checks` in config.json")
        return 0
    factor = 3.0 if (lp.config().get("priority") or "background") == "background" else 1.0
    found = L.run_checks(lp.root, checks, lp.cycle_dir(), factor)
    for c in checks:
        print(f"{'ok  ' if c['_result'] == '0' else 'FAIL'} {c['name']} (exit {c['_result']})")
    broken = [c["name"] for c in checks if c["_result"] in ("timeout", "not-run")]
    fp = lp.path(os.path.join("findings", "checks.json"))
    if found:
        F.save(fp, found)
        print(f"{len(found)} failing check(s) -> {_rel(fp)}")
    elif os.path.exists(fp):
        os.remove(fp)
    if broken:
        raise LoopError("check(s) could not run to the end (timeout or not started): " + ", ".join(broken) +
                        "; fix the check or its `timeout` (background priority already allows 3x)")
    return 0


# ------------------------------------------------------------------ parser


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="loop", description="Run a gated improvement cycle on a project.")
    ap.add_argument("--root", default=".", help="project root (default: current directory)")
    ap.add_argument("--version", action="version", version=f"improvement-loop {__version__}")
    sp = ap.add_subparsers(dest="cmd", required=True)

    p = sp.add_parser("init", help="start a loop in this project")
    p.add_argument("--mode", choices=["full", "lite"], default="full")
    p.set_defaults(fn=cmd_init)
    sp.add_parser("status", help="current step, unmet guards, budget").set_defaults(fn=cmd_status)
    sp.add_parser("advance", help="move to the next step if every guard holds").set_defaults(fn=cmd_advance)
    p = sp.add_parser("approve", help="record an OWNER approval (asks for confirmation)")
    p.add_argument("gate", choices=APPROVALS)
    p.add_argument("--by", required=True)
    p.add_argument("--note")
    p.add_argument("--yes", action="store_true", help="skip the prompt (only the owner may use this)")
    p.set_defaults(fn=cmd_approve)
    p = sp.add_parser("spend", help="add to the budget used this loop")
    p.add_argument("--minutes", type=float, default=0)
    p.add_argument("--cost", type=float, default=0.0)
    p.set_defaults(fn=cmd_spend)
    p = sp.add_parser("validate", help="check findings files against the schema")
    p.add_argument("files", nargs="+")
    p.set_defaults(fn=cmd_validate)
    sp.add_parser("verify", help="merge, dedupe and evidence-check this cycle's findings").set_defaults(fn=cmd_verify)
    p = sp.add_parser("set-verdict", help="the verifier's verdict on one finding")
    p.add_argument("id")
    p.add_argument("verdict")
    p.add_argument("--by", required=True, help="verifier role/name (must not be the finding's author)")
    p.add_argument("--severity", type=int, choices=range(1, 6))
    p.add_argument("--note")
    p.set_defaults(fn=cmd_set_verdict)
    p = sp.add_parser("rank", help="rank verified findings into RANKED.md")
    p.add_argument("--top", type=int, default=10)
    p.add_argument("--file", help="rank this findings file instead of the cycle's verified.json")
    p.set_defaults(fn=cmd_rank)
    p = sp.add_parser("decide", help="log a rejected/deferred/fixed decision in DECISIONS.md")
    p.add_argument("id", help="finding id or fingerprint")
    p.add_argument("status", choices=D.STATUSES)
    p.add_argument("--by", required=True)
    p.add_argument("--reason", required=True)
    p.set_defaults(fn=cmd_decide)
    sp.add_parser("regress", help="check that fixed findings stay fixed").set_defaults(fn=cmd_regress)
    sp.add_parser("stop-check", help="evaluate the stop rule (exit 0 = stop rule met)").set_defaults(fn=cmd_stop_check)
    p = sp.add_parser("gate", help="complexity gate between two git refs")
    p.add_argument("--base", required=True, help="git ref of the last release")
    p.add_argument("--head", default=M.WORKTREE, help="git ref or WORKTREE (default)")
    p.add_argument("--justify", help="file of `metric_key: one-line reason` lines")
    p.set_defaults(fn=cmd_gate)
    p = sp.add_parser("privacy", help="secret + denylist scan of what a push would publish")
    p.add_argument("--denylist", help="denylist file outside the repo (or $IMPROVEMENT_LOOP_DENYLIST)")
    p.add_argument("--history", help="also scan commits and metadata in this rev range, e.g. origin/main..HEAD")
    p.add_argument("--text", nargs="+", help="scan these files instead (e.g. text for an external reviewer)")
    p.add_argument("--base", help="fail only on hits that are new since this git ref (e.g. the cycle base)")
    p.set_defaults(fn=cmd_privacy)
    p = sp.add_parser("issues", help="draft issues from plan.json, or file them (dry run unless --yes)")
    p.add_argument("action", choices=["draft", "file"])
    p.add_argument("--plan")
    p.add_argument("--repo", help="owner/name for `file`")
    p.add_argument("--denylist")
    p.add_argument("--yes", action="store_true", help="really file (default: dry run)")
    p.set_defaults(fn=cmd_issues)
    p = sp.add_parser("run", help="drive the cycle unattended with the configured agent (see `autonomy`)")
    p.add_argument("--detach", action="store_true", help="run in the background at low priority")
    p.add_argument("--service", action="store_true", help="print a launchd/systemd/schtasks definition")
    p.add_argument("--every-hours", type=float, default=1.0)
    p.add_argument("--max-steps", type=int, default=200)
    p.set_defaults(fn=cmd_run)
    p = sp.add_parser("lenses", help="which lenses run this cycle and why; --scores for per-lens precision")
    p.add_argument("--scores", action="store_true")
    p.set_defaults(fn=cmd_lenses)
    sp.add_parser("check", help="run the configured project checks; failures become findings"
                  ).set_defaults(fn=cmd_check)
    p = sp.add_parser("prompt", help="print a lens or role prompt")
    p.add_argument("role", nargs="?")
    p.add_argument("--list", action="store_true")
    p.set_defaults(fn=cmd_prompt)
    return ap


def main(argv: Optional[List[str]] = None) -> int:
    a = build_parser().parse_args(argv)
    if a.cmd == "issues" and a.action == "file" and not a.repo:
        print("loop: issues file needs --repo owner/name", file=sys.stderr)
        return 2
    try:
        return a.fn(a, Loop(a.root))
    except (LoopError, ValueError, OSError, RuntimeError, json.JSONDecodeError) as e:
        print(f"loop: {e}", file=sys.stderr)
        return 2



def _rel(path: str, start: str = os.curdir) -> str:
    """os.path.relpath that does not raise across Windows drives (falls back to the absolute path)."""
    try:
        return os.path.relpath(path, start)
    except ValueError:
        return os.path.abspath(path)


if __name__ == "__main__":
    sys.exit(main())
