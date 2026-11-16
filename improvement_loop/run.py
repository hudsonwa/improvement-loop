"""`loop run`: drive the cycle unattended, at background priority, with any agent CLI.

The driver does what an operator does by hand: it reads the state machine, runs the deterministic commands,
starts one agent per role (critics in throwaway worktrees, blind to each other), and passes approvals only as the
`autonomy` policy allows:

    off    agents do the work; the run parks at every owner approval (ranking, plan, release, next/stop)
    build  ranking and plan are approved by policy; the run parks before release
    full   release, next and stop are approved by policy too (a release is a local commit on a loop/ branch;
           nothing is ever pushed, filed or published by the driver)

Guardrails, always on: builds only on a branch named `<branch_prefix>...`; one item per commit with the tests
run after each, rolled back on failure; `protected` paths (the referee: gates, policy, CI) may not change without
the owner; a failed gate rolls the cycle back to its base (a backup tag is kept); the budget, max_cycles and stop
rule end the run; a `.improvement-loop/STOP` file stops it between steps; one driver per project (pidfile).
Approvals made by policy are recorded as `policy:autonomy-<level>`, so the audit trail shows them.
"""
from __future__ import annotations

import concurrent.futures as cf
import datetime as _dt
import hashlib
import io
import contextlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

from . import findings as F
from . import issues as I
from . import lenses as L
from . import metrics as M
from . import priority as PR
from . import privacy as P
from .state import Loop, LoopError

AUTONOMY = ("off", "build", "full")
LOOP = [sys.executable, "-m", "improvement_loop"]
HARD_RULES = (
    "HARD RULES (from the improvement-loop driver): work only inside {cwd}. Never push, never open PRs, never "
    "file issues, never change git remotes or global config, never install system packages, never touch files "
    "outside this directory. Put scratch work (temporary installs, homes, caches, copies) only under "
    "{scratch}, never inside the project. Never edit .improvement-loop/state.json or record approvals. The loop CLI is "
    "`{loop}` (run it from {root}).\n\n")


class Parked(Exception):
    """The run stops here and waits for the owner (or a fix); the message says why."""


def _now() -> str:
    return _dt.datetime.now().replace(microsecond=0).isoformat()


class Runner:
    def __init__(self, lp: Loop, echo: bool = True):
        self.lp, self.root, self.echo = lp, lp.root, echo
        self.cfg = lp.config()
        self.level = self.cfg.get("priority") or "background"
        self.autonomy = self.cfg.get("autonomy") or "off"
        if self.autonomy not in AUTONOMY:
            raise LoopError(f"autonomy must be one of {', '.join(AUTONOMY)}")
        self.policy = dict({"top_n": 5, "min_severity": 3}, **(self.cfg.get("policy") or {}))
        if self.cfg.get("mode") == "lite":  # lite plans fewer items
            self.policy["top_n"] = min(self.policy["top_n"], int(self.cfg.get("lite_top_n") or 3))
        self._lock = threading.RLock()
        self.run_path = os.path.join(lp.dir, "run.json")
        self.log_path = os.path.join(lp.dir, "RUN.md")
        key = hashlib.sha256(self.root.encode()).hexdigest()[:10]
        self.wt_dir = self.cfg.get("worktrees_dir") or os.path.join(os.path.dirname(self.root), ".improvement-loop.noindex", key)
        # beside the project: same drive (fast worktrees, nothing on the system disk); .noindex: Spotlight skips it

    # ------------------------------------------------------------------ plumbing

    def say(self, msg: str) -> None:
        with self._lock:
            self._say(msg)

    def _say(self, msg: str) -> None:
        line = f"- {_now()} {msg}"
        with open(self.log_path, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
        if self.echo:
            print(line, flush=True)

    def notify(self, msg: str) -> None:
        cmd = self.cfg.get("notify")
        if cmd:
            try:
                subprocess.run([c.replace("{message}", msg) for c in cmd], timeout=60, capture_output=True)
            except (OSError, subprocess.SubprocessError):
                pass

    def runstate(self) -> Dict[str, Any]:
        if os.path.exists(self.run_path):
            with open(self.run_path, encoding="utf-8") as fh:
                return json.load(fh)
        return {"base": {}}

    def save_runstate(self, rs: Dict[str, Any]) -> None:
        with open(self.run_path + ".tmp", "w", encoding="utf-8") as fh:
            json.dump(rs, fh, indent=2, sort_keys=True)
        os.replace(self.run_path + ".tmp", self.run_path)

    def git(self, *args: str, cwd: Optional[str] = None, check: bool = True) -> str:
        p = subprocess.run(["git", "-C", cwd or self.root, *args], capture_output=True, text=True)
        if check and p.returncode:
            raise Parked(f"git {' '.join(args)} failed: {p.stderr.strip()[:300]}")
        return p.stdout.strip()

    def cli(self, *args: str) -> Tuple[int, str]:
        """Run a `loop` command in-process (same code, same checks as the CLI)."""
        from . import cli
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            code = cli.main(["--root", self.root, *args])
        return code, out.getvalue()

    def stop_requested(self) -> bool:
        return os.path.exists(os.path.join(self.lp.dir, "STOP"))

    def approve(self, gate: str, why: str) -> None:
        self.lp.approve(gate, f"policy:autonomy-{self.autonomy}", why)
        self.say(f"approved `{gate}` by policy ({why})")

    def agent(self, role: str, prompt: str, cwd: str, timeout: Optional[int] = None) -> int:
        """Start the configured agent for this role with the prompt; returns its exit code."""
        spec = dict(self.cfg.get("agent") or {}, **((self.cfg.get("agents") or {}).get(role) or {}))
        if not spec.get("cmd"):
            raise Parked("no agent configured: set `agent.cmd` in config.json (see https://github.com/hudsonwa/improvement-loop/blob/main/docs/PROCESS.md#autonomy)")
        adir = self.lp.path("agents")
        os.makedirs(adir, exist_ok=True)
        pfile = os.path.join(adir, f"{role}.prompt.md")
        with open(pfile, "w", encoding="utf-8") as fh:
            fh.write(HARD_RULES.format(cwd=cwd, root=self.root, loop=" ".join(LOOP), scratch=self.scratch(role)) + prompt)
        cmd = [c.replace("{prompt_file}", pfile).replace("{cwd}", cwd) for c in spec["cmd"]]
        stdin = None if any("{prompt_file}" in c for c in spec["cmd"]) else open(pfile, "rb")
        t0 = time.time()
        with open(os.path.join(adir, f"{role}.log"), "wb") as log:
            try:
                code = PR.run(cmd, self.level, cwd=cwd, stdin=stdin, stdout=log, stderr=subprocess.STDOUT,
                              timeout=timeout or int(spec.get("timeout", 3600)),
                              env=_agent_env(self.scratch(role))).returncode
            except subprocess.TimeoutExpired:
                code = -1
            except OSError as e:
                raise Parked(f"agent for {role} could not start: {e}")
            finally:
                if stdin:
                    stdin.close()
        with self._lock:  # agents run in parallel; state.json has one writer at a time
            self.lp.spend(minutes=round((time.time() - t0) / 60, 2))
        self.say(f"agent {role}: exit {code} in {round(time.time() - t0)} s")
        return code

    def scratch(self, role: str) -> str:
        """A per-agent scratch directory outside the project (also its TMPDIR), so nothing an agent leaves
        behind is scanned by the project's own tests or gates."""
        d = os.path.join(self.wt_dir, "scratch", role)
        os.makedirs(d, exist_ok=True)
        return d

    def tests_pass(self, cwd: Optional[str] = None, label: str = "tests") -> Tuple[bool, str]:
        """Run test_cmd; keep the full output in cycle-N/tests/<label>.log and return the failing lines.
        A run that is killed (timeout or signal) is not a verdict on the code: it is retried once, then the run
        parks, because rolling back good work for a slow machine is worse than stopping."""
        cmd = self.cfg.get("test_cmd")
        if not cmd:
            return True, "no test_cmd configured"
        for attempt in (1, 2):
            try:
                p = PR.run(cmd, self.level, cwd=cwd or self.root, capture_output=True,
                           timeout=int(self.cfg.get("test_timeout", 900)))
                out, ok, killed = (p.stdout + p.stderr).decode("utf-8", errors="replace"), p.returncode == 0, \
                    p.returncode < 0 or p.returncode in (137, 142, 143)
            except subprocess.TimeoutExpired:
                out, ok, killed = "tests timed out\n", False, True
            if not killed:
                break
            self.say(f"{label}: test run was killed (timeout or signal), attempt {attempt} of 2")
        else:
            raise Parked(f"{label}: the test run was killed twice (timeout or signal), so there is no verdict on "
                         f"the code; raise `test_timeout` (and any alarm in `test_cmd`) or free the machine")
        tdir = self.lp.path("tests")
        os.makedirs(tdir, exist_ok=True)
        log = os.path.join(tdir, f"{label}.log")
        with open(log, "w", encoding="utf-8") as fh:
            fh.write(out)
        lines = out.strip().splitlines()
        bad = [ln for ln in lines if any(k in ln for k in ("FAILED", "ERROR", "FAIL ", "Error:", "timed out"))]
        summary = " / ".join((bad[:4] or lines[-2:]))
        return ok, f"{summary} (log: {os.path.relpath(log, self.root)})"

    def touched_protected(self, base: str, cwd: Optional[str] = None) -> List[str]:
        globs = list(self.cfg.get("protected") or [])
        if not globs:
            return []
        names = self.git("diff", "--name-only", f"{base}..HEAD", cwd=cwd).splitlines()
        return [n for n in names if M.match(n, globs)]

    def ensure_exclude(self) -> None:
        """Keep loop state out of commits made by builders (local, never committed)."""
        gd = self.git("rev-parse", "--git-common-dir")
        p = os.path.join(gd if os.path.isabs(gd) else os.path.join(self.root, gd), "info", "exclude")
        os.makedirs(os.path.dirname(p), exist_ok=True)
        text = ""
        if os.path.exists(p):
            with open(p, encoding="utf-8") as fh:
                text = fh.read()
        if ".improvement-loop/" not in text.split():
            with open(p, "a", encoding="utf-8") as fh:
                fh.write("\n.improvement-loop/\n")

    # ------------------------------------------------------------------ steps

    def step_critique(self, st: Dict[str, Any]) -> None:
        rs = self.runstate()
        rs["base"].setdefault(str(st["cycle"]), self.git("rev-parse", "HEAD"))
        self.save_runstate(rs)
        fdir = self.lp.path("findings")
        os.makedirs(fdir, exist_ok=True)
        todo = [c for c in self.lp.critics() if not os.path.exists(os.path.join(fdir, c + ".json"))]
        done_mark = self.lp.path(os.path.join("checks", ".done"))
        need_checks = bool(self.cfg.get("checks")) and not os.path.exists(done_mark)
        # checks and every critic run at the same time: the agents wait on the model API, not on this machine
        with cf.ThreadPoolExecutor(max_workers=max(1, int(self.cfg.get("max_parallel", 8))) + 1) as ex:
            chk = ex.submit(self.run_checks_subprocess) if need_checks else None
            list(ex.map(lambda c: self.critic(c, st), todo))
            if chk:
                code, out = chk.result()
                self.say("checks: " + out.strip().replace("\n", "; "))
                if code:
                    raise Parked("checks broken: " + (out.strip().splitlines() or ["?"])[-1])
                os.makedirs(os.path.dirname(done_mark), exist_ok=True)
                open(done_mark, "w").close()
        unmet = self.lp.guards()
        if unmet:
            raise Parked("critique incomplete: " + "; ".join(unmet[:4]))

    def run_checks_subprocess(self) -> Tuple[int, str]:
        """`loop check` in its own process (safe next to the critic threads; inherits background priority)."""
        try:
            p = PR.run([*LOOP, "--root", self.root, "check"], self.level, cwd=self.root, capture_output=True,
                       env=_agent_env(), timeout=6 * 3600)
        except subprocess.TimeoutExpired:
            return 2, "checks: no result after 6 h"
        return p.returncode, (p.stdout + p.stderr).decode("utf-8", errors="replace")

    def critic_brief(self, st: Dict[str, Any]) -> str:
        """Shared guidance for every critic: time budget, no duplicate test runs, start from what changed."""
        mins = int(self.cfg.get("critic_timeout", 900)) // 60
        out = (f"Time budget: finish within {mins} minutes (you are stopped after that). Do NOT run the project's "
               f"full test suite or other long jobs: the loop's checks run it, and their logs appear in "
               f".improvement-loop/cycle-{st['cycle']}/checks/ in {self.root} while you work. Short targeted "
               f"commands and timings are fine.")
        prev = self.runstate()["base"].get(str(st["cycle"] - 1))
        if prev:
            try:
                stat = self.git("diff", "--stat", f"{prev}..HEAD")
            except Parked:
                stat = ""
            if stat:
                out += (f"\n\nStart from what changed since the last cycle (`git diff {prev[:12]}..HEAD`):\n"
                        + "\n".join(stat.splitlines()[-40:]) + "\nThe rest of the project stays in scope.")
        return out

    def critic(self, lens: str, st: Dict[str, Any]) -> None:
        text = L.prompt_text(self.root, lens)
        if text is None:
            self.say(f"no prompt for lens {lens}")
            return
        wt = os.path.join(self.wt_dir, f"c{st['cycle']}-{lens}")
        self.git("worktree", "remove", "--force", wt, check=False)
        self.git("worktree", "add", "--detach", wt, "HEAD")
        rel = f".improvement-loop/cycle-{st['cycle']}/findings/{lens}.json"
        out = os.path.join(wt, rel)
        os.makedirs(os.path.dirname(out), exist_ok=True)
        dec = self.lp.decisions_path
        if os.path.exists(dec):
            shutil.copy(dec, os.path.join(wt, ".improvement-loop", "DECISIONS.md"))
        prompt = (f"You are the read-only critic `{lens}`, cycle {st['cycle']}. Change no file except {rel}. Write "
                  f"it, then run `{' '.join(LOOP)} validate {rel}` until it prints ok.\n\n{text}")
        try:
            self.agent(lens, prompt + "\n\n" + self.critic_brief(st), wt,
                       timeout=int(self.cfg.get("critic_timeout", 900)))
            if os.path.exists(out):
                fs = F.load(out)
                if not F.validate_all(fs):
                    shutil.copy(out, self.lp.path(os.path.join("findings", lens + ".json")))
                else:
                    self.say(f"{lens}: findings file not schema-valid; skipped")
        finally:
            self.git("worktree", "remove", "--force", wt, check=False)

    def step_verify(self, st: Dict[str, Any]) -> None:
        if not os.path.exists(self.lp.path("verified.json")):
            code, out = self.cli("verify")
            if code:
                raise Parked("loop verify failed: " + out.strip()[-300:])
        if any(u for u in self.lp.guards() if "unverified" in u):
            text = L.prompt_text(self.root, "verifier") or ""
            self.agent("verifier", f"Verify every finding in cycle {st['cycle']} with "
                                   f"`{' '.join(LOOP)} set-verdict ID VERDICT --by verifier-agent ...` (run from "
                                   f"{self.root}). Do not advance or rank.\n\n{text}", self.root)
        unmet = self.lp.guards()
        if unmet:
            raise Parked("verification incomplete: " + "; ".join(unmet[:3]))

    def step_rank(self, st: Dict[str, Any]) -> None:
        if not os.path.exists(self.lp.path("RANKED.md")):
            code, out = self.cli("rank")
            if code:
                raise Parked("loop rank failed: " + out.strip()[-300:])
        if self.lp.approved(st, "ranking"):
            return
        ranked = F.load(self.lp.path("ranked.json"))
        worth = [f for f in ranked if f["severity"] >= self.policy["min_severity"]]
        if not worth:
            raise Parked(f"stop rule: no verified finding with severity >= {self.policy['min_severity']}; "
                         "nothing worth building (owner: `loop decide`/`approve` to continue anyway)")
        if self.autonomy == "off":
            raise Parked("ranking ready for the owner: RANKED.md")
        self.approve("ranking", f"{len(worth)} verified findings >= severity {self.policy['min_severity']}")

    def step_plan(self, st: Dict[str, Any]) -> None:
        pp = self.lp.path("plan.json")
        if not os.path.exists(pp) or I.validate_plan(I.load_plan(pp)):
            text = L.prompt_text(self.root, "planner") or ""
            self.agent("planner", f"Plan cycle {st['cycle']}: at most the top {self.policy['top_n']} ranked "
                                  f"findings with severity >= {self.policy['min_severity']}. Do NOT `loop decide` findings you "
                                  f"merely leave for later: a decision closes a finding for every later cycle, so "
                                  f"decide only on merit (rejected, or deferred for a stated reason). Every item is built unattended by one agent in one commit, so "
                                  f"keep items small and testable. An item that fixes a performance finding must add a `bench` metric to "
                                  f"config `metrics` (see metrics.py) or a benchmark in `checks`, so the gate can compare base "
                                  f"and head; name it in the item's `metric`. A correctness or security item that must grow a gated metric may "
                                  f"carry \"justification\": {{\"<metric_key>\": \"<one-line reason>\"}}; nothing else may "
                                  f"grow product code. Do not edit files under: "
                                  f"{', '.join(self.cfg.get('protected') or []) or '(none)'}.\n\n{text}", self.root)
        if not os.path.exists(pp):
            raise Parked("planner wrote no plan.json")
        errs = I.validate_plan(I.load_plan(pp))
        if errs:
            raise Parked("plan.json invalid: " + "; ".join(errs[:3]))
        if self.lp.approved(st, "plan"):
            return
        if self.autonomy == "off":
            raise Parked("plan ready for the owner: PLAN.md / plan.json")
        self.approve("plan", f"{len(I.load_plan(pp)['items'])} items, schema-valid")

    def step_issues(self, st: Dict[str, Any]) -> None:
        code, out = self.cli("issues", "draft")
        if code:
            raise Parked("issue drafting failed: " + out.strip()[-300:])

    def step_build(self, st: Dict[str, Any]) -> None:
        """Build the plan wave by wave. Items in one wave (which by the plan's rule touch different components)
        are built at the same time, each by its own agent in its own worktree and branch, checked there, then
        merged one by one; one test run checks the merged wave. If that fails, the wave is re-applied one item at
        a time with tests after each, so one bad item cannot sink the others."""
        branch = self.git("rev-parse", "--abbrev-ref", "HEAD")
        prefix = self.cfg.get("branch_prefix") or "loop/"
        if not branch.startswith(prefix):
            raise Parked(f"the driver builds only on a `{prefix}*` branch (now `{branch}`); e.g. "
                         f"`git worktree add ../<project>-loop -b {prefix}auto` and run the loop there")
        if self.git("status", "--porcelain"):
            raise Parked("working tree not clean before build")
        items = sorted(I.load_plan(self.lp.path("plan.json"))["items"], key=lambda x: (x["wave"], x["id"]))
        waves: Dict[int, List[Dict[str, Any]]] = {}
        for it in items:
            waves.setdefault(it["wave"], []).append(it)
        log: List[str] = []
        for wave in sorted(waves):
            if self.stop_requested():
                raise Parked("STOP file present")
            pre = self.git("rev-parse", "HEAD")
            todo = waves[wave]
            self.say(f"wave {wave}: building {', '.join(i['id'] for i in todo)} in parallel")
            with cf.ThreadPoolExecutor(max_workers=max(1, int(self.cfg.get("max_parallel", 8)))) as ex:
                built = list(ex.map(lambda it: self.build_item(it, pre, st["cycle"]), todo))
            merged = [b for b in built if b["ok"] and self.merge_item(b)]
            if merged:
                ok, why = self.tests_pass(label=f"wave-{wave}")
                if not ok:
                    self.say(f"wave {wave}: merged tests fail ({why[:120]}); re-applying one item at a time")
                    self.git("reset", "--hard", pre)
                    keep = []
                    for b in merged:
                        before = self.git("rev-parse", "HEAD")
                        if self.merge_item(b):
                            ok1, why1 = self.tests_pass(label=f"wave-{wave}-{b['item']['id']}")
                            if ok1:
                                keep.append(b)
                                continue
                            self.git("reset", "--hard", before)
                            b["why"] = "tests fail together with the rest of its wave: " + why1[:100]
                        b["ok"] = False
                    merged = keep
            for b in built:
                it = b["item"]
                if b in merged:
                    head = b["head_after_merge"]
                    for fid in it["finding_ids"]:
                        self.cli("decide", fid, "fixed", "--by", "integrator", "--reason", head[:12])
                    log.append(f"{it['id']} | {branch} | {head[:12]} | tests pass | {', '.join(it['finding_ids'])}")
                    self.say(f"built {it['id']} at {head[:12]}")
                else:
                    why = b["why"] if not b["ok"] else (b.get("why") or "not merged")
                    for fid in it["finding_ids"]:
                        self.cli("decide", fid, "deferred", "--by", "integrator", "--reason", f"build failed: {why}")
                    log.append(f"{it['id']} | {branch} | rolled back | {why[:120]} | -")
                    self.say(f"rolled back {it['id']}: {why[:200]}")
                self.git("branch", "-D", b["branch"], check=False)
        with open(self.lp.path("BUILD.md"), "w", encoding="utf-8") as fh:
            fh.write("# Build\n\n" + "\n".join(log) + "\n")

    def build_item(self, it: Dict[str, Any], pre: str, cycle: int) -> Dict[str, Any]:
        """One agent builds one item in its own worktree on its own branch from `pre`; checked there."""
        prefix = self.cfg.get("branch_prefix") or "loop/"
        br = f"{prefix}item-c{cycle}-{it['id']}"
        wt = os.path.join(self.wt_dir, f"b{cycle}-{it['id']}")
        self.git("worktree", "remove", "--force", wt, check=False)
        self.git("branch", "-D", br, check=False)
        self.git("worktree", "add", "-q", "-b", br, wt, pre)
        out: Dict[str, Any] = {"item": it, "branch": br, "pre": pre, "ok": False, "why": ""}
        try:
            text = L.prompt_text(self.root, "integrator") or ""
            self.agent(f"build-{it['id']}", "Implement exactly this one plan item in this worktree, with a "
                       "regression test named after the finding id, then commit once with message "
                       f"\"{it['id']}: {it['title']}\". Other items are being built at the same time in other "
                       "worktrees: stay inside this item's scope. Before committing, re-read the docs, docstrings and "
                       "comments in every file you touched and make them match the new behaviour (counts, names, "
                       "commands). If the item removes a feature, also remove or migrate what it stored on existing "
                       "installs, with a test. New secrets, emails, IP addresses, home paths and denylisted names fail this "
                       "item (the gate's privacy scan runs on it); invented test fixtures carry `privacy:allow` on "
                       "their line. Do not run `loop decide`; the driver records it."
                       f"\n\nRun the tests only with: {' '.join(self.cfg.get('test_cmd') or []) or 'the project suite'}"
                       f" (the configured, fastest full run); do not start other test runners.\n\n"
                       f"Item:\n{json.dumps(it, indent=2)}\n\nProtected (do not edit): "
                       f"{', '.join(self.cfg.get('protected') or []) or '(none)'}\n\nContext:\n{text}", wt)
            head = self.git("rev-parse", "HEAD", cwd=wt)
            if head == pre:
                out["why"] = "no commit"
            elif self.git("status", "--porcelain", cwd=wt):
                out["why"] = "left uncommitted changes"
            elif self.touched_protected(pre, wt):
                out["why"] = "touched protected paths: " + ", ".join(self.touched_protected(pre, wt)[:5])
            elif (leaks := self.privacy_hits(wt, pre)):
                # the gate's privacy scan, per item: one item's hit rejects that item, not the whole cycle
                out["why"] = "privacy: " + "; ".join(leaks[:3])
            else:
                ok, why = self.tests_pass(wt, f"build-{it['id']}")
                out.update(ok=ok, why="" if ok else "tests fail: " + why[:120])
        finally:
            self.git("worktree", "remove", "--force", wt, check=False)
        return out

    def privacy_hits(self, wt: str, pre: str) -> List[str]:
        """What the item's commits would newly publish (same scan and denylist as the gate), as printable lines."""
        if not hasattr(self, "_deny"):
            path = os.environ.get("IMPROVEMENT_LOOP_DENYLIST") or self.cfg.get("denylist")
            self._deny = P.load_denylist(path) if path and os.path.exists(path) else []
        hits, _, _ = P.new_since(wt, pre, self._deny, history=f"{pre}..HEAD")
        return [f"{h['file']}:{h['line']}: {h['kind']}: {h['match']}" for h in hits]

    def merge_item(self, b: Dict[str, Any]) -> bool:
        """Cherry-pick an item's commits onto the loop branch; a conflict rejects only that item."""
        name, email = self.git("log", "-1", "--format=%an%x00%ae", b["branch"]).split("\0")
        env = dict(os.environ, GIT_COMMITTER_NAME=name, GIT_COMMITTER_EMAIL=email)  # the builder's identity
        p = subprocess.run(["git", "-C", self.root, "cherry-pick", "--allow-empty", f"{b['pre']}..{b['branch']}"],
                           capture_output=True, text=True, env=env)
        if p.returncode:
            subprocess.run(["git", "-C", self.root, "cherry-pick", "--abort"], capture_output=True)
            b.update(ok=False, why="merge conflict with an item built beside it")
            return False
        b["head_after_merge"] = self.git("rev-parse", "HEAD")
        return True

    def step_gate(self, st: Dict[str, Any]) -> None:
        if self.lp.approved(st, "release"):
            return
        base = self.runstate()["base"].get(str(st["cycle"]))
        if not base:
            raise Parked("no recorded base commit for this cycle")
        if self.git("rev-parse", "HEAD") == base and self.built_findings():
            # an earlier gate rolled this cycle back; resuming must re-gate the built work (the cause may be fixed
            # now), never gate the empty base and release a cycle that changed nothing
            tags = self.git("tag", "--list", f"loop-backup-c{st['cycle']}-*", "--sort=-refname").split()
            if not tags:
                raise Parked("this cycle was rolled back and no backup tag is left to re-gate")
            self.git("reset", "--hard", tags[0])
            self.say(f"resuming a rolled-back cycle: restored {tags[0]} to gate it again")
        fails = []
        if not any(m.get("kind") == "surface" for m in (self.cfg.get("metrics") or [])):
            self.say("WARNING: no `surface` metric configured, so interface breaks (removed commands, routes, "
                     "config keys) are not gated; see https://github.com/hudsonwa/improvement-loop/blob/main/docs/PROCESS.md#gates")
        jpath = self.lp.path("justify.txt")
        with open(jpath, "w", encoding="utf-8") as fh:
            fh.write(self.justifications())
        if self.cli("gate", "--base", base, "--head", "HEAD", "--justify", jpath)[0]:
            fails.append("complexity gate")
        if self.cli("privacy", "--base", base, "--history", f"{base}..HEAD")[0]:
            fails.append("privacy")
        if self.cli("regress")[0]:
            fails.append("regression check")
        ok, why = self.tests_pass()
        if not ok:
            # one failing run must not roll back a whole cycle when the same code then passes: say it was flaky
            # (the auditor and the owner see it in RUN.md and tests/flaky.log) instead of discarding the work
            again, why2 = self.tests_pass(label="tests-rerun")
            if again:
                with open(os.path.join(self.lp.path("tests"), "flaky.log"), "a", encoding="utf-8") as fh:
                    fh.write(why + "\n")
                self.say(f"tests: FLAKY, failed then passed on a rerun of the same commit: {why[:160]}")
            else:
                fails.append("tests: " + why2[:120])
        bad = self.touched_protected(base)
        if bad:
            fails.append("protected paths changed: " + ", ".join(bad[:5]))
        if fails:
            if self.autonomy != "off" and self.git("rev-parse", "HEAD") != base:
                tag = f"loop-backup-c{st['cycle']}-{int(time.time())}"
                self.git("tag", tag)
                self.git("reset", "--hard", base)
                for fid in self.built_findings():
                    self.cli("decide", fid, "reopened", "--by", "integrator", "--reason", f"gate failed; see {tag}")
                self.say(f"gate failed; cycle rolled back to {base[:12]} (backup tag {tag}); its findings reopened")
            raise Parked("gate failed: " + "; ".join(fails))
        if self.autonomy != "full":
            raise Parked("gate passed; release awaits the owner (`loop approve release --by NAME`)")
        self.approve("release", "tests, complexity, privacy, regression and protected-path checks pass")

    def built_findings(self) -> List[str]:
        """Finding ids of the items this cycle's build committed (BUILD.md rows with a commit)."""
        out: List[str] = []
        bp = self.lp.path("BUILD.md")
        if os.path.exists(bp):
            with open(bp, encoding="utf-8") as fh:
                rows = fh.read().splitlines()
            for line in rows:
                parts = [p.strip() for p in line.split("|")]
                if len(parts) == 5 and parts[3] == "tests pass":
                    out += [x.strip() for x in parts[4].split(",") if x.strip()]
        return out

    def justifications(self) -> str:
        """Metric justifications from plan items, accepted only for correctness/security items that were built."""
        pp, built = self.lp.path("plan.json"), set(self.built_findings())
        if not os.path.exists(pp):
            return ""
        lines = []
        for it in I.load_plan(pp)["items"]:
            j = it.get("justification")
            if (isinstance(j, dict) and set(it["finding_ids"]) & built
                    and set(it.get("labels", [])) & {"correctness", "security"}):
                lines += [f"{k}: {it['id']}: {v}" for k, v in j.items() if str(v).strip()]
        return "\n".join(lines) + ("\n" if lines else "")

    def step_audit(self, st: Dict[str, Any]) -> None:
        if not os.path.exists(self.lp.path("AUDIT.md")):
            text = L.prompt_text(self.root, "auditor") or ""
            nxt = f".improvement-loop/cycle-{st['cycle']}/next-findings.json"
            self.agent("auditor", f"Audit cycle {st['cycle']}. Write AUDIT.md, and put any new findings (same "
                                  f"schema, role `auditor`) in {nxt} instead of a block in AUDIT.md.\n\n{text}",
                       self.root)
        if not os.path.exists(self.lp.path("AUDIT.md")):
            raise Parked("auditor wrote no AUDIT.md")
        if self.lp.approved(st, "next") or self.lp.approved(st, "stop"):
            return
        if self.autonomy != "full":
            raise Parked("audit done; the owner approves `next` or `stop`")
        reasons = self.lp.stop_reasons(st)
        if reasons:
            self.approve("stop", "; ".join(reasons))
        else:
            self.approve("next", "verified findings above the stop severity remain; budget and cycles left")

    # ------------------------------------------------------------------ main loop

    def run(self, max_steps: int = 200) -> int:
        PR.lower_self(self.level)
        self.ensure_exclude()
        self.say(f"run start: autonomy {self.autonomy}, priority {self.level}, pid {os.getpid()}")
        try:
            for _ in range(max_steps):
                if self.stop_requested():
                    raise Parked("STOP file present (.improvement-loop/STOP)")
                st = self.lp.load()
                if st["step"] == "done":
                    self.say("loop done (stop approved)")
                    self.notify("improvement-loop: done")
                    return 0
                spent = self.lp.budget_spent(st)
                if spent:
                    raise Parked("budget spent: " + ", ".join(spent))
                getattr(self, "step_" + st["step"])(st)
                prev = st["cycle"]
                st = self.lp.advance()
                self.say(f"advanced to cycle {st['cycle']}, step {st['step']}")
                if st["cycle"] != prev and st["step"] == "critique":
                    self.carry_auditor_findings(prev)
            raise Parked(f"max_steps {max_steps} reached")
        except (Parked, LoopError) as e:
            self.say(f"PARKED: {e}")
            self.notify(f"improvement-loop parked: {e}")
            return 3

    def carry_auditor_findings(self, prev: int) -> None:
        src = os.path.join(self.lp.dir, f"cycle-{prev}", "next-findings.json")
        if os.path.exists(src):
            try:
                fs = F.load(src)
                if not F.validate_all(fs):
                    F.save(self.lp.path(os.path.join("findings", "auditor.json")), fs)
            except (ValueError, json.JSONDecodeError):
                self.say("auditor next-findings.json unreadable; skipped")


def _agent_env(scratch: Optional[str] = None) -> Dict[str, str]:
    """Agents call `python -m improvement_loop`: make this very package importable from any cwd; temp files go
    to the agent's scratch directory outside the project."""
    env = dict(os.environ)
    if scratch:
        env.update(TMPDIR=scratch, TEMP=scratch, TMP=scratch)
    pkg_parent = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    env["PYTHONPATH"] = os.pathsep.join(x for x in (pkg_parent, env.get("PYTHONPATH")) if x)
    return env


# ------------------------------------------------------------------ detach and service


def detach(root: str, level: str) -> int:
    log = open(os.path.join(root, ".improvement-loop", "run.log"), "ab")
    kw: Dict[str, Any] = dict(stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, cwd=root)
    if sys.platform == "win32":
        kw["creationflags"] = 0x00000008 | 0x00000200 | 0x40  # detached, new group, idle priority
    else:
        kw["start_new_session"] = True
        kw.update(PR.popen_kwargs(level))
    p = subprocess.Popen(PR.wrap([*LOOP, "--root", root, "run"], level), **kw)
    return p.pid


def service_text(root: str, hours: float = 1.0) -> str:
    """A user-level service definition that resumes the run periodically (it parks quickly when blocked)."""
    exe = " ".join([*LOOP, "--root", root, "run"])
    secs = int(hours * 3600)
    if sys.platform == "darwin":
        args = "".join(f"<string>{a}</string>" for a in [*LOOP, "--root", root, "run"])
        return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<!-- save as ~/Library/LaunchAgents/dev.improvement-loop.plist; launchctl bootstrap gui/$(id -u) <that file> -->
<plist version="1.0"><dict>
  <key>Label</key><string>dev.improvement-loop</string>
  <key>ProgramArguments</key><array>{args}</array>
  <key>WorkingDirectory</key><string>{root}</string>
  <key>StartInterval</key><integer>{secs}</integer>
  <key>ProcessType</key><string>Background</string>
  <key>LowPriorityIO</key><true/>
  <key>Nice</key><integer>19</integer>
  <key>StandardOutPath</key><string>{root}/.improvement-loop/run.log</string>
  <key>StandardErrorPath</key><string>{root}/.improvement-loop/run.log</string>
</dict></plist>
"""
    if sys.platform == "win32":
        return (f'schtasks /Create /TN improvement-loop /SC MINUTE /MO {max(1, secs // 60)} '
                f'/TR "{exe}" /RL LIMITED\n')
    return f"""# ~/.config/systemd/user/improvement-loop.service  (+ the .timer below); systemctl --user enable --now improvement-loop.timer
[Service]
Type=oneshot
WorkingDirectory={root}
ExecStart={exe}
Nice=19
CPUSchedulingPolicy=idle
IOSchedulingClass=idle

# ~/.config/systemd/user/improvement-loop.timer
[Timer]
OnBootSec=10min
OnUnitActiveSec={secs}s
[Install]
WantedBy=timers.target
"""


def acquire_lock(lp: Loop) -> Optional[str]:
    """One driver per project. Returns an error message when another live driver holds the lock."""
    p = os.path.join(lp.dir, "run.pid")
    if os.path.exists(p):
        try:
            pid = int(open(p, encoding="ascii").read().strip())
            if pid != os.getpid() and _alive(pid):
                return f"another `loop run` is active (pid {pid}); remove {p} if that is wrong"
        except ValueError:
            pass
    with open(p, "w", encoding="ascii") as fh:
        fh.write(str(os.getpid()))
    return None


def release_lock(lp: Loop) -> None:
    p = os.path.join(lp.dir, "run.pid")
    try:
        if open(p, encoding="ascii").read().strip() == str(os.getpid()):
            os.remove(p)
    except OSError:
        pass


def _alive(pid: int) -> bool:
    if sys.platform == "win32":
        out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}"], capture_output=True, text=True).stdout
        return str(pid) in out
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
