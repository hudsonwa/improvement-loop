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
import time
from typing import Any, Dict, List, Optional, Tuple

from . import findings as F
from . import issues as I
from . import lenses as L
from . import metrics as M
from . import priority as PR
from .state import Loop, LoopError

AUTONOMY = ("off", "build", "full")
LOOP = [sys.executable, "-m", "improvement_loop"]
HARD_RULES = (
    "HARD RULES (from the improvement-loop driver): work only inside {cwd}. Never push, never open PRs, never "
    "file issues, never change git remotes or global config, never install system packages, never touch files "
    "outside this directory. Never edit .improvement-loop/state.json or record approvals. The loop CLI is "
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
        self.run_path = os.path.join(lp.dir, "run.json")
        self.log_path = os.path.join(lp.dir, "RUN.md")
        key = hashlib.sha256(self.root.encode()).hexdigest()[:10]
        self.wt_dir = self.cfg.get("worktrees_dir") or os.path.join(tempfile.gettempdir(), "improvement-loop", key)

    # ------------------------------------------------------------------ plumbing

    def say(self, msg: str) -> None:
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
            raise Parked("no agent configured: set `agent.cmd` in config.json (see docs/PROCESS.md, Autonomy)")
        adir = self.lp.path("agents")
        os.makedirs(adir, exist_ok=True)
        pfile = os.path.join(adir, f"{role}.prompt.md")
        with open(pfile, "w", encoding="utf-8") as fh:
            fh.write(HARD_RULES.format(cwd=cwd, root=self.root, loop=" ".join(LOOP)) + prompt)
        cmd = [c.replace("{prompt_file}", pfile).replace("{cwd}", cwd) for c in spec["cmd"]]
        stdin = None if any("{prompt_file}" in c for c in spec["cmd"]) else open(pfile, "rb")
        t0 = time.time()
        with open(os.path.join(adir, f"{role}.log"), "wb") as log:
            try:
                code = PR.run(cmd, self.level, cwd=cwd, stdin=stdin, stdout=log, stderr=subprocess.STDOUT,
                              timeout=timeout or int(spec.get("timeout", 3600)), env=_agent_env()).returncode
            except subprocess.TimeoutExpired:
                code = -1
            except OSError as e:
                raise Parked(f"agent for {role} could not start: {e}")
            finally:
                if stdin:
                    stdin.close()
        self.lp.spend(minutes=round((time.time() - t0) / 60, 2))
        self.say(f"agent {role}: exit {code} in {round(time.time() - t0)} s")
        return code

    def tests_pass(self) -> Tuple[bool, str]:
        cmd = self.cfg.get("test_cmd")
        if not cmd:
            return True, "no test_cmd configured"
        try:
            p = PR.run(cmd, self.level, cwd=self.root, capture_output=True,
                       timeout=int(self.cfg.get("test_timeout", 900)))
        except subprocess.TimeoutExpired:
            return False, "tests timed out"
        tail = (p.stdout + p.stderr).decode("utf-8", errors="replace").strip().splitlines()[-3:]
        return p.returncode == 0, " / ".join(tail)

    def touched_protected(self, base: str) -> List[str]:
        globs = list(self.cfg.get("protected") or [])
        if not globs:
            return []
        names = self.git("diff", "--name-only", f"{base}..HEAD").splitlines()
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
        branch = self.git("rev-parse", "--abbrev-ref", "HEAD")
        prefix = self.cfg.get("branch_prefix") or "loop/"
        if not branch.startswith(prefix):
            raise Parked(f"the driver builds only on a `{prefix}*` branch (now `{branch}`); e.g. "
                         f"`git worktree add ../<project>-loop -b {prefix}auto` and run the loop there")
        if self.git("status", "--porcelain"):
            raise Parked("working tree not clean before build")
        items = sorted(I.load_plan(self.lp.path("plan.json"))["items"], key=lambda x: (x["wave"], x["id"]))
        log = []
        for it in items:
            if self.stop_requested():
                raise Parked("STOP file present")
            pre = self.git("rev-parse", "HEAD")
            text = L.prompt_text(self.root, "integrator") or ""
            self.agent(f"build-{it['id']}", "Implement exactly this one plan item on the current branch, with a "
                       "regression test named after the finding id, then commit once with message "
                       f"\"{it['id']}: {it['title']}\". Do not run `loop decide`; the driver records it.\n\n"
                       f"Run the tests only with: {' '.join(self.cfg.get('test_cmd') or []) or 'the project suite'} "
                       f"(the configured, fastest full run); do not start other test runners.\n\n"
                       f"Item:\n{json.dumps(it, indent=2)}\n\nProtected (do not edit): "
                       f"{', '.join(self.cfg.get('protected') or []) or '(none)'}\n\nContext:\n{text}", self.root)
            ok, why = self.tests_pass()
            head = self.git("rev-parse", "HEAD")
            bad = self.touched_protected(pre)
            dirty = bool(self.git("status", "--porcelain"))
            if head == pre:
                ok, why = False, "no commit"
            elif bad:
                ok, why = False, "touched protected paths: " + ", ".join(bad[:5])
            elif dirty:
                ok, why = False, "left uncommitted changes"
            if ok:
                for fid in it["finding_ids"]:
                    self.cli("decide", fid, "fixed", "--by", "integrator", "--reason", head[:12])
                log.append(f"{it['id']} | {branch} | {head[:12]} | tests pass | {', '.join(it['finding_ids'])}")
                self.say(f"built {it['id']} at {head[:12]}")
            else:
                self.git("reset", "--hard", pre)
                self.git("clean", "-fd")
                for fid in it["finding_ids"]:
                    self.cli("decide", fid, "deferred", "--by", "integrator", "--reason", f"build failed: {why}")
                log.append(f"{it['id']} | {branch} | rolled back | {why[:120]} | -")
                self.say(f"rolled back {it['id']}: {why[:200]}")
        with open(self.lp.path("BUILD.md"), "w", encoding="utf-8") as fh:
            fh.write("# Build\n\n" + "\n".join(log) + "\n")

    def step_gate(self, st: Dict[str, Any]) -> None:
        if self.lp.approved(st, "release"):
            return
        base = self.runstate()["base"].get(str(st["cycle"]))
        if not base:
            raise Parked("no recorded base commit for this cycle")
        fails = []
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
            fails.append("tests: " + why[:120])
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


def _agent_env() -> Dict[str, str]:
    """Agents call `python -m improvement_loop`: make this very package importable from any cwd."""
    env = dict(os.environ)
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
