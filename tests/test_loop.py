import contextlib
import io
import json
import os
import subprocess
import tempfile
import unittest

from improvement_loop import cli
from improvement_loop import decisions as D
from improvement_loop import findings as F
from improvement_loop import issues as I
from improvement_loop import lenses as L
from improvement_loop import metrics as M
from improvement_loop import privacy as P
from improvement_loop.state import Loop, LoopError


def finding(id="A-1", role="architecture-critic", claim="the cache is never invalidated", category="correctness",
            file="app.py", line=2, quote="cache = {}", sev=3, reach=3, effort=2, rc=False, **kw):
    f = {"id": id, "role": role, "claim": claim, "category": category,
         "evidence": [{"file": file, "line": line, "quote": quote}],
         "severity": sev, "reach": reach, "effort": effort, "removes_complexity": rc}
    f.update(kw)
    return f


def git(repo, *args):
    subprocess.run(["git", "-C", repo, *args], check=True, capture_output=True)


class Repo(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name
        self.write("app.py", "import os\ncache = {}\n\ndef run():\n    return cache\n")

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, rel, text):
        p = os.path.join(self.root, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w") as fh:
            fh.write(text)
        return p

    def run_cli(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(["--root", self.root, *args])
        return code, out.getvalue() + err.getvalue()


class TestSchema(unittest.TestCase):
    def test_valid(self):
        self.assertEqual(F.validate(finding()), [])

    def test_errors(self):
        bad = finding(sev=9, category="vibes")
        bad["evidence"] = [{"file": "x", "line": 0, "quote": ""}]
        bad["extra"] = 1
        errs = " ".join(F.validate(bad))
        for s in ("unknown field 'extra'",):
            self.assertIn(s, errs)
        bad.pop("extra")
        errs = " ".join(F.validate(bad))
        for s in ("category", "severity", "line must be", "quote must be"):
            self.assertIn(s, errs)

    def test_bool_is_not_int(self):
        self.assertTrue(F.validate(finding(sev=True)))

    def test_duplicate_ids(self):
        self.assertTrue(any("duplicate" in e for e in F.validate_all([finding(), finding()])))


class TestFingerprintRank(unittest.TestCase):
    def test_fingerprint_ignores_word_order_and_case(self):
        a = finding(claim="The cache is never invalidated", sev=2, reach=4, effort=3, rc=False)
        # TQ-1: pins the whole dedupe merge contract, not just the joined role.
        b = finding(claim="never invalidated: the CACHE", id="B-1", role="first-time-user",
                    sev=4, reach=2, effort=1, rc=True)
        shared = dict(a["evidence"][0])
        distinct = {"file": "app.py", "line": 5, "quote": "return cache"}
        b["evidence"] = [dict(shared), distinct]  # same file, so the fingerprint is unchanged
        self.assertEqual(F.fingerprint(a), F.fingerprint(b))
        merged = F.dedupe([a, b])
        self.assertEqual(len(merged), 1)
        got = {k: merged[0][k] for k in ("severity", "reach", "effort", "removes_complexity", "evidence", "role")}
        self.assertEqual(got, {"severity": 4, "reach": 4, "effort": 1, "removes_complexity": True,
                               "evidence": [shared, distinct],
                               "role": "architecture-critic+first-time-user"})

    def test_dotfile_path_kept(self):
        a = finding(file=".github/x.yml")
        b = finding(file="github/x.yml")
        self.assertNotEqual(F.fingerprint(a), F.fingerprint(b))

    def test_rank_order(self):
        ok = dict(verdict="true", evidence_ok=True)
        fs = [finding(id="low", claim="a b c minor", category="simplicity", sev=3, reach=5, **ok),     # 15
              finding(id="urgent", claim="data loss", category="security", sev=4, reach=1, **ok),       # 4 urgent
              finding(id="tie-rc", claim="tie one", category="user_value", sev=3, reach=5, rc=True, **ok),
              finding(id="tie-cheap", claim="tie two", category="user_value", sev=3, reach=5, effort=1, **ok),
              finding(id="false", claim="nope", verdict="false", evidence_ok=True),
              finding(id="noev", claim="no evidence", verdict="true", evidence_ok=False)]
        ranked, excluded = F.rank(fs)
        self.assertEqual([f["id"] for f in ranked], ["urgent", "tie-rc", "tie-cheap", "low"])
        self.assertEqual({f["id"] for f in excluded}, {"false", "noev"})

    def test_closed_fingerprints_excluded(self):
        f = finding(verdict="true", evidence_ok=True)
        ranked, excluded = F.rank([f], {F.fingerprint(f)})
        self.assertEqual(ranked, [])
        self.assertIn("DECISIONS", excluded[0]["excluded_because"])


class TestEvidence(Repo):
    def test_ok_and_window(self):
        self.assertEqual(F.check_evidence(self.root, {"file": "app.py", "line": 2, "quote": "cache = {}"})[0], True)
        self.assertTrue(F.check_evidence(self.root, {"file": "app.py", "line": 4, "quote": "cache  =  {}"})[0])

    def test_failures(self):
        for e, why in [({"file": "app.py", "line": 99, "quote": "x"}, "line 99"),
                       ({"file": "nope.py", "line": 1, "quote": "x"}, "not found"),
                       ({"file": "../etc/passwd", "line": 1, "quote": "root"}, "escapes"),
                       ({"file": "app.py", "line": 1, "quote": "def walk"}, "quote not found")]:
            ok, msg = F.check_evidence(self.root, e)
            self.assertFalse(ok)
            self.assertIn(why, msg)


class TestDecisions(Repo):
    def test_record_closed_regress(self):
        dp, fx = os.path.join(self.root, "DECISIONS.md"), os.path.join(self.root, "fixed.json")
        f, g = finding(), finding(id="B", claim="other thing entirely", line=1, quote="import os")
        D.record(dp, f, "fixed", "integrator", "abc123", fx)
        D.record(dp, g, "deferred", "owner", "later | maybe")
        self.assertEqual(D.closed(dp), {F.fingerprint(f), F.fingerprint(g)})
        self.assertEqual(D.regressions(self.root, fx)[0]["status"], "suspect")  # quote still there
        self.write("app.py", "import os\nstore = {}\n")
        self.assertEqual(D.regressions(self.root, fx)[0]["status"], "holds")
        with self.assertRaises(ValueError):
            D.record(dp, f, "rejected", "", "x")

    def test_note_does_not_reopen(self):
        dp = os.path.join(self.root, "DECISIONS.md")
        f = finding()
        D.record(dp, f, "rejected", "owner", "no")
        D.record(dp, f, "note", "owner", "tie-break")
        self.assertIn(F.fingerprint(f), D.closed(dp))


class TestStateMachine(Repo):
    def test_full_cycle_guards_and_approvals(self):
        lp = Loop(self.root)
        lp.init("lite")
        self.assertRaises(LoopError, lp.init)
        self.assertRaises(LoopError, lp.advance)               # no findings yet
        fdir = lp.path("findings")
        F.save(os.path.join(fdir, "architecture-critic.json"), [finding()])
        F.save(os.path.join(fdir, "first-time-user.json"),
               [finding(id="U-1", role="first-time-user", claim="readme lies about install", category="user_value",
                        line=1, quote="import os")])
        self.assertEqual(lp.advance()["step"], "verify")
        self.assertIn("missing verified.json", " ".join(lp.guards()))
        code, out = self.run_cli("verify")
        self.assertEqual(code, 0, out)
        self.assertIn("still unverified", " ".join(lp.guards()))
        code, out = self.run_cli("set-verdict", "A-1", "true", "--by", "architecture-critic")
        self.assertEqual(code, 2)                               # author may not verify
        self.assertIn("must not be the finding's author", out)
        for i in ("A-1", "U-1"):
            self.assertEqual(self.run_cli("set-verdict", i, "true", "--by", "verifier")[0], 0)
        lp.advance()
        self.assertEqual(self.run_cli("rank")[0], 0)
        self.assertIn("approval 'ranking'", " ".join(lp.guards()))
        self.assertRaises(LoopError, lp.approve, "plan", "owner")    # wrong gate for this step
        self.assertRaises(LoopError, lp.approve, "ranking", " ")
        code, out = self.run_cli("approve", "ranking", "--by", "owner")  # not a tty, no --yes
        self.assertEqual(code, 2)
        self.assertEqual(self.run_cli("approve", "ranking", "--by", "owner", "--yes")[0], 0)
        self.assertEqual(self.run_cli("rank")[0], 2)            # approved ranking is frozen
        self.assertEqual(self.run_cli("set-verdict", "A-1", "false", "--by", "verifier")[0], 2)  # wrong step
        self.assertEqual(lp.advance()["step"], "plan")
        with open(lp.path("plan.json"), "w") as fh:
            json.dump({"items": [plan_item()]}, fh)
        lp.approve("plan", "owner")
        lp.advance()
        self.assertEqual(self.run_cli("issues", "draft")[0], 0)
        lp.advance()
        self.assertRaises(LoopError, lp.advance)               # BUILD.md missing
        _text(lp.path("BUILD.md"), "P-1 | lane | abc | tests pass | A-1\n")
        lp.advance()
        _write(lp.path("gate.json"), {"pass": True})
        _write(lp.path("privacy.json"), {"pass": False})
        lp.approve("release", "owner")
        self.assertIn("privacy scan (`loop privacy`) did not pass", " ".join(lp.guards()))
        _write(lp.path("privacy.json"), {"pass": True})
        self.assertIn("is stale or was not written by the CLI", " ".join(lp.guards()))   # ARCH-1, GATE-1
        _write(lp.path("gate.json"), {"pass": True, "stamp": lp.stamp()})
        _write(lp.path("privacy.json"), {"pass": True, "stamp": lp.stamp()})
        lp.advance()
        _text(lp.path("AUDIT.md"), "ok\n")
        self.assertIn("approval 'next'", " ".join(lp.guards()))
        lp.approve("next", "owner")
        st = lp.advance()
        self.assertEqual((st["cycle"], st["step"]), (2, "critique"))
        self.assertTrue(os.path.isdir(lp.path("findings")))

    def test_stop_and_budget(self):
        lp = Loop(self.root)
        lp.init()
        cfg = lp.config()
        cfg.update(max_cycles=5, budget={"minutes": 10, "cost": 0, "max_lanes": 2})
        _write(lp.config_path, cfg)
        self.assertEqual(lp.stop_reasons(), [])
        lp.spend(minutes=12)
        self.assertTrue(any("budget" in r for r in lp.stop_reasons()))
        self.assertRaises(LoopError, lp.spend, -1)
        F.save(lp.path("verified.json"), [finding(sev=2, verdict="true", evidence_ok=True)])
        self.assertTrue(any("severity >= 3" in r for r in lp.stop_reasons()))

    def test_stop_approval_ends_loop(self):
        lp = Loop(self.root)
        lp.init()
        st = lp.load()
        st["step"] = "audit"
        _write(lp.state_path, st)
        self.assertRaises(LoopError, lp.approve, "release", "owner")
        _text(lp.path("AUDIT.md"), "x")
        lp.approve("stop", "owner")
        self.assertEqual(lp.advance()["step"], "done")
        self.assertRaises(LoopError, lp.advance)


def plan_item(**kw):
    it = {"id": "P-1", "title": "Invalidate the cache", "finding_ids": ["A-1"], "why": "stale data",
          "scope_in": "app.py", "scope_out": "storage", "removes": "manual reset", "must_not_add": "a new thread",
          "acceptance": ["cache cleared on write"], "metric": "product_loc", "tests": "test_cache",
          "depends_on": [], "labels": ["correctness"], "estimate_hours": 2, "wave": 1}
    it.update(kw)
    return it


def _text(p, s):
    with open(p, "w") as fh:
        fh.write(s)


def _write(p, obj):
    with open(p, "w") as fh:
        json.dump(obj, fh)


class TestIssues(Repo):
    def test_validate(self):
        self.assertEqual(I.validate_plan({"items": [plan_item()]}), [])
        errs = " ".join(I.validate_plan({"items": [plan_item(estimate_hours=9, finding_ids=[], removes="",
                                                             depends_on=["X"])]}))
        for s in ("not atomic", "names no finding", "simplicity check", "unknown item X"):
            self.assertIn(s, errs)
        self.assertTrue(I.validate_plan({}))

    def test_draft_and_dry_run(self):
        paths = I.draft({"items": [plan_item()]}, os.path.join(self.root, "issues"))
        with open(paths[0]) as fh:
            text = fh.read()
        self.assertIn("**Simplicity check:** removes: manual reset", text)
        out = I.file_issues(paths, "o/r", {paths[0]: ["bug"]})
        self.assertTrue(out[0].startswith("DRY RUN: gh issue create --repo o/r"))


class TestPrivacy(Repo):
    def test_generic_patterns(self):
        fake_key = "ghp_" + "a" * 36
        text = "\n".join([f"token = {fake_key}", "mail me at someone@corp-internal.com",  # privacy:allow (fixture)
                          "ok: noreply@example.com", "host 10.1.2.3", "loopback 127.0.0.1 and version 1.2.3.999",  # privacy:allow
                          "path /Users/alice/x", "fine /Users/<you>/x", f"{fake_key}  privacy:allow"])
        kinds = [h["kind"] for h in P.scan_text("t", text, [])]
        self.assertEqual(kinds, ["secret:github-token", "email", "ip-address", "home-path"])
        self.assertNotIn("a" * 20, json.dumps(P.scan_text("t", text, [])))  # secrets are redacted

    def test_denylist_never_echoed(self):
        dl = self.write("outside/deny.txt", "# comment\nProjectNightjar\nre:acme-\\d+\n")
        deny = P.load_denylist(dl)
        hits = P.scan_text("t", "uses projectnightjar and ACME-42", deny)
        self.assertEqual(len(hits), 2)
        self.assertNotIn("nightjar", json.dumps(hits).lower())

    def test_repo_scan_and_cli(self):
        git(self.root, "init", "-q")
        self.write("notes.txt", "contact boss@private-co.com\n")  # privacy:allow (fixture)
        self.write("ignored.log", "boss@private-co.com\n")  # privacy:allow (fixture)
        self.write(".gitignore", "*.log\n")
        files = {h["file"] for h in P.scan_repo(self.root, [])}
        self.assertEqual(files, {"notes.txt"})
        code, out = self.run_cli("privacy")
        self.assertEqual(code, 1)
        self.assertIn("PRIVACY: FAIL", out)


class TestMetrics(Repo):
    def test_glob(self):
        self.assertTrue(M.match("a.py", ["**/*.py"]))
        self.assertTrue(M.match("x/y/a.py", ["**/*.py"]))
        self.assertTrue(M.match("tests/a.py", ["tests/**"]))
        self.assertFalse(M.match("a.js", ["**/*.py"]))

    def test_gate_between_refs(self):
        git(self.root, "init", "-q")
        git(self.root, "-c", "user.name=t", "-c", "user.email=t@example.com", "add", "-A")
        git(self.root, "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-qm", "base")
        self.write("app.py", "import os, threading\ncache = {}\nthreading.Thread(target=print).start()\n\n\n")
        self.write("tests/test_app.py", "x = 1\n" * 50)  # tests are not product code
        base = M.collect(M.Source(self.root, "HEAD"), M.DEFAULT_METRICS)
        head = M.collect(M.Source(self.root, M.WORKTREE), M.DEFAULT_METRICS)
        self.assertEqual((base["product_loc"], head["product_loc"]), (4, 3))
        self.assertEqual((base["background_actors"], head["background_actors"]), (0, 1))
        rows, fails = M.gate(M.DEFAULT_METRICS, base, head, {})
        self.assertEqual(fails, ["background_actors"])
        rows, fails = M.gate(M.DEFAULT_METRICS, base, head, M.read_justifications("- background_actors: needed\n"))
        self.assertEqual(fails, [])
        self.assertIn("worse, justified", M.markdown(rows, "a", "b"))
        code, out = self.run_cli("gate", "--base", "HEAD")
        self.assertEqual(code, 1)
        self.assertIn("GATE: FAIL (background_actors)", out)

    def test_REL_10_gate_records_regression(self):
        git(self.root, "init", "-q")
        self.run_cli("init")
        git(self.root, "-c", "user.name=t", "-c", "user.email=t@example.com", "add", "-A")
        git(self.root, "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-qm", "base")
        lp = Loop(self.root)
        D.record(lp.decisions_path, finding(), "fixed", "integrator", "abc123", lp.fixed_path)
        code, out = self.run_cli("gate", "--base", "HEAD")
        self.assertEqual(code, 1, out)
        self.assertIn("GATE: FAIL (regression: A-1)", out)
        with open(lp.path("gate.json")) as fh:
            verdict = json.load(fh)
        self.assertEqual((verdict["pass"], verdict["fails"]), (False, ["regression: A-1"]))
        self.assertEqual(self.run_cli("regress")[0], 1)
        self.write("app.py", "import os\nstore = {}\n\ndef run():\n    return store\n")
        code, out = self.run_cli("gate", "--base", "HEAD")
        self.assertEqual(code, 0, out)
        with open(lp.path("gate.json")) as fh:
            self.assertTrue(json.load(fh)["pass"])
        self.assertEqual(self.run_cli("regress")[0], 0)

    def test_metric_validation(self):
        self.assertTrue(M.validate_metric({"key": "x", "kind": "regex", "include": ["*"], "pattern": "("}))
        self.assertEqual(M.validate_metric(M.DEFAULT_METRICS[0]), [])


class TestCli(Repo):
    def test_prompts_and_validate(self):
        code, out = self.run_cli("prompt", "--list")
        self.assertEqual(code, 0)
        for role in ("external-reviewer", "architecture-critic", "state-machine-critic", "first-time-user",
                     "verifier", "planner", "integrator", "auditor", "performance", "reliability",
                     "test-quality", "docs-drift", "dependencies", "accessibility", "eval-judge"):
            self.assertIn(role, out)
        p = self.write("f.json", json.dumps({"findings": [finding()]}))
        self.assertEqual(self.run_cli("validate", p)[0], 0)
        self.write("g.json", "[{}]")
        self.assertEqual(self.run_cli("validate", os.path.join(self.root, "g.json"))[0], 1)

    def test_status_uninitialised(self):
        code, out = self.run_cli("status")
        self.assertEqual(code, 2)
        self.assertIn("loop init", out)


class TestLenses(Repo):
    def test_selection_core_detect_rotation_lite(self):
        self.write("tests/test_app.py", "x = 1\n")
        names = lambda cfg, cyc=1: [n for n, _ in L.select(self.root, cfg, cyc)]
        full = names({"mode": "full"})
        for n in ("architecture-critic", "external-reviewer", "first-time-user", "state-machine-critic",
                  "test-quality", "performance", "reliability"):
            self.assertIn(n, full)
        self.assertNotIn("accessibility", full)            # no UI files
        self.assertNotIn("performance", names({"mode": "full"}, 2))   # every 2 cycles
        self.assertIn("test-quality", names({"mode": "full"}, 2))
        self.assertEqual(sorted(names({"mode": "lite"})), ["architecture-critic", "first-time-user"])
        self.assertNotIn("performance", names({"mode": "full", "retired": ["performance"]}))
        self.assertEqual(names({"critics": ["x"]}), ["x"])
        self.assertEqual(names({"lenses": ["performance"]}), ["performance"])
        self.assertRaises(ValueError, L.select, self.root, {"lenses": ["nope"]}, 1)

    def test_project_lens_and_prompt_contract(self):
        self.write(".improvement-loop/lenses/latency.md", "---\ndetect: **/*.py\n---\n# Lens: latency\n{{contract}}\n")
        self.assertIn("latency", [n for n, _ in L.select(self.root, {"mode": "full"}, 1)])
        text = L.prompt_text(self.root, "latency")
        self.assertIn("Output contract", text)
        self.assertNotIn("{{contract}}", text)
        self.assertIsNone(L.prompt_text(self.root, "_contract"))
        for n in L.prompt_names(self.root):
            self.assertNotIn("{{contract}}", L.prompt_text(self.root, n))

    def test_selection_frozen_per_cycle_and_guard(self):
        lp = Loop(self.root)
        lp.init()
        before = lp.critics()
        self.write("ui/index.html", "<button></button>\n")      # before any critic ran: picked up (L-4)
        self.assertIn("accessibility", lp.critics())
        F.save(lp.path("findings/architecture-critic.json"), [finding()])
        now = lp.critics()
        self.write("ui/more.vue", "<x/>\n")                     # once critique started: frozen
        self.write(".improvement-loop/lenses/late.md", "---\ndetect: **/*.py\n---\nx {{contract}}\n")
        self.assertEqual(lp.critics(), now)
        self.assertNotEqual(now, before)
        self.assertTrue(any("findings/performance.json" in u for u in lp.guards()))

    def test_checks_become_findings(self):
        import sys
        lp = Loop(self.root)
        lp.init()
        cfg = lp.config()
        cfg["checks"] = [{"name": "ok", "cmd": [sys.executable, "-c", "pass"]},
                         {"name": "bad", "cmd": [sys.executable, "-c", "import sys; sys.exit(3)"],
                          "category": "security", "severity": 4}]
        _write(lp.config_path, cfg)
        code, out = self.run_cli("check")
        self.assertEqual(code, 0, out)
        fs = F.load(lp.path("findings/checks.json"))
        self.assertEqual([f["id"] for f in fs], ["CHECK-bad"])
        self.assertEqual(F.validate_all(fs), [])
        self.assertTrue(F.verify_evidence(self.root, fs)[0]["evidence_ok"])
        self.assertTrue(L.validate_checks([{"name": "x", "cmd": "make test"}]))

    def test_scores(self):
        lp = Loop(self.root)
        lp.init()
        fs = [finding(id=f"A-{i}", claim="claim " + "abcdef"[i] * 3, verdict="true" if i < 4 else "false", evidence_ok=True)
              for i in range(6)]
        fs.append(finding(id="B-1", role="performance", claim="slow", verdict="partly", evidence_ok=True, sev=4))
        F.save(lp.path("verified.json"), fs)
        D.record(lp.decisions_path, fs[0], "fixed", "integrator", "abc")
        rows = {r["lens"]: r for r in L.scores(lp.dir)}
        a = rows["architecture-critic"]
        self.assertEqual((a["accepted"], a["false"], a["fixed"], a["precision"]), (4, 2, 1, 0.67))
        self.assertEqual(rows["performance"]["advice"], "too few judged findings")
        code, out = self.run_cli("lenses", "--scores")
        self.assertIn("| performance |", out)


FAKE_AGENT = r"""
import json, os, subprocess, sys
pf = sys.argv[1]
role = os.path.basename(pf).replace(".prompt.md", "")
text = open(pf).read()
loop = [sys.executable, "-m", "improvement_loop"]
root = os.environ["FAKE_ROOT"]
cyc = json.load(open(os.path.join(root, ".improvement-loop/state.json")))["cycle"]
cdir = os.path.join(root, ".improvement-loop", "cycle-%d" % cyc)
import time
if role == "architecture-critic" and os.environ.get("FAKE_STAMP"):
    open(os.environ["FAKE_STAMP"] + ".start", "w").write(repr(time.time()))
time.sleep(float(os.environ.get("FAKE_SLEEP", "0")) if role == "architecture-critic" else 0)
if role == "architecture-critic" and os.environ.get("FAKE_STAMP"):
    open(os.environ["FAKE_STAMP"] + ".end", "w").write(repr(time.time()))
if role == "architecture-critic":
    rel = text.split("Change no file except ")[1].split(".json")[0] + ".json"
    os.makedirs(os.path.dirname(rel), exist_ok=True)
    json.dump({"findings": [{"id": "A-1", "role": role, "claim": "the cache dict literal is unclear",
               "category": "correctness", "evidence": [{"file": "app.py", "line": 2, "quote": "cache = {}"}],
               "severity": 4, "reach": 3, "effort": 1, "removes_complexity": False}]}, open(rel, "w"))
elif role == "verifier":
    subprocess.run(loop + ["--root", root, "set-verdict", "A-1", "true", "--by", "verifier-agent"], check=True)
elif role == "planner":
    it = {"id": "P-1", "title": "Clearer cache", "finding_ids": ["A-1"], "why": "w", "scope_in": "app.py",
          "scope_out": "-", "removes": "the literal", "must_not_add": "lines", "acceptance": ["a"],
          "metric": "product_loc", "tests": "t", "depends_on": [], "labels": ["correctness"],
          "estimate_hours": 1, "wave": 1}
    items = [it]
    if os.environ.get("FAKE_PLAN"):
        items.append(dict(it, id="P-2", title="Second", scope_in="other.py"))
    json.dump({"items": items}, open(os.path.join(cdir, "plan.json"), "w"))
elif role.startswith("build-"):
    here = os.getcwd()                              # the item's own worktree
    item = role[len("build-"):]
    target = os.environ.get("FAKE_EDIT", "app.py")
    if item == "P-2":
        target = "app.py" if os.environ.get("FAKE_PLAN") == "conflict" else "other.py"
    src = open(os.path.join(here, "app.py")).read().replace("cache = {}", "cache = dict()" if item == "P-1" else "cache = {1: 1}")
    if os.environ.get("FAKE_GROW"):
        src += "\n".join("extra_%d = %d" % (i, i) for i in range(5)) + "\n"
    time.sleep(float(os.environ.get("FAKE_BUILD_SLEEP", "0")))
    open(os.path.join(here, target), "w").write(src if target == "app.py" else "changed = 1\n")
    g = ["git", "-C", here, "-c", "user.name=t", "-c", "user.email=t@example.com"]
    subprocess.run(g + ["add", "-A"], check=True)
    subprocess.run(g + ["commit", "-qm", item + ": change"], check=True)
elif role == "auditor":
    open(os.path.join(cdir, "AUDIT.md"), "w").write("# Audit\nok\n")
"""


class TestEvalFindings(Repo):
    """Regression tests for the loop defects found by the first private-project evaluation (L-1..L-5)."""

    def commit(self, msg="c"):
        git(self.root, "-c", "user.name=t", "-c", "user.email=t@example.com", "add", "-A")
        git(self.root, "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-qm", msg)

    def test_L1_privacy_counts_only_new_hits(self):
        git(self.root, "init", "-q")
        self.write("net.py", "HOST = '10.1.2.3'\n")
        self.commit()
        base = subprocess.run(["git", "-C", self.root, "rev-parse", "HEAD"], capture_output=True,
                              text=True).stdout.strip()
        self.write("net.py", "# moved\nHOST = '10.1.2.3'\n")                 # an old hit, moved: not new
        self.assertEqual(self.run_cli("privacy", "--base", base)[0], 0)
        self.write("more.py", "OTHER = '10.9.9.9'\n")                         # a new hit
        code, out = self.run_cli("privacy", "--base", base)
        self.assertEqual(code, 1)
        self.assertIn("more.py", out)
        self.assertNotIn("net.py:", out)

    def test_L2_bench_metric_with_tolerance(self):
        import sys
        git(self.root, "init", "-q")
        self.write("speed.txt", "100\n")
        self.commit()
        m = {"key": "status_ms", "kind": "bench", "gated": True, "repeat": 3, "tolerance": 0.1, "parse": r"(\d+)",
             "cmd": [sys.executable, "-c", "print(open('speed.txt').read())"]}
        self.assertEqual(M.validate_metric(m), [])
        self.assertTrue(M.validate_metric(dict(m, parse="x")))
        base = M.collect(M.Source(self.root, "HEAD"), [m])
        for val, verdict in (("105", "same"), ("150", "WORSE: FAIL"), ("50", "better")):
            self.write("speed.txt", val + "\n")
            rows, _ = M.gate([m], base, M.collect(M.Source(self.root, M.WORKTREE), [m]), {})
            self.assertEqual(rows[0]["verdict"], verdict)

    def test_L3_surface_metric_flags_removals(self):
        import sys
        git(self.root, "init", "-q")
        self.write("cmds.txt", "facts\nstatus\n")
        self.commit()
        m = {"key": "cli", "kind": "surface", "gated": True,
             "cmd": [sys.executable, "-c", "print(open('cmds.txt').read())"]}
        base = M.collect(M.Source(self.root, "HEAD"), [m])
        self.write("cmds.txt", "status\nnew\n")
        rows, fails = M.gate([m], base, M.collect(M.Source(self.root, M.WORKTREE), [m]), {})
        self.assertEqual(fails, ["cli"])
        self.assertIn("removed: facts", rows[0]["verdict"])
        _, fails = M.gate([m], base, M.collect(M.Source(self.root, M.WORKTREE), [m]), {"cli": "deprecated in 0.4"})
        self.assertEqual(fails, [])

    def test_L5_check_timeout_is_not_a_finding(self):
        import sys
        lp = Loop(self.root)
        lp.init()
        cfg = lp.config()
        cfg.update(priority="normal",
                   checks=[{"name": "slow", "cmd": [sys.executable, "-c", "import time; time.sleep(5)"], "timeout": 1}])
        _write(lp.config_path, cfg)
        code, out = self.run_cli("check")
        self.assertEqual(code, 2)
        self.assertIn("could not run to the end", out)
        self.assertFalse(os.path.exists(lp.path("findings/checks.json")))


class TestCycle2Findings(Repo):
    def test_L8_history_ignores_removed_lines(self):
        git(self.root, "init", "-q")
        self.write("fix.py", "BOX = '10.0.0.5'\nok = 1\n")
        git(self.root, "-c", "user.name=t", "-c", "user.email=t@example.com", "add", "-A")
        git(self.root, "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-qm", "base")
        self.write("fix.py", "ok = 1\n")                                        # the cycle deletes an old hit
        git(self.root, "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-qam", "remove")
        self.assertEqual(P.scan_text("h", P.git_history(self.root, "HEAD~1..HEAD"), []), [])
        self.write("fix.py", "ok = 1\nNEW = '10.9.9.9'\n")                     # an added hit still counts
        git(self.root, "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-qam", "add")
        self.assertEqual(len(P.scan_text("h", P.git_history(self.root, "HEAD~1..HEAD"), [])), 1)


class TestRun(Repo):
    def setUp(self):
        super().setUp()
        import sys
        git(self.root, "init", "-q")
        self.write("gate.py", "x = 1\n")
        self.write("other.py", "changed = 0\n")
        git(self.root, "-c", "user.name=t", "-c", "user.email=t@example.com", "add", "-A")
        git(self.root, "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-qm", "base")
        git(self.root, "checkout", "-qb", "loop/auto")
        agent = os.path.join(tempfile.mkdtemp(), "fake_agent.py")
        with open(agent, "w") as fh:
            fh.write(FAKE_AGENT)
        self.lp = Loop(self.root)
        cfg = dict(lenses=["architecture-critic"], max_cycles=1, autonomy="full", protected=["gate.py"],
                   test_cmd=[sys.executable, "-c", "pass"], agent={"cmd": [sys.executable, agent, "{prompt_file}"]},
                   worktrees_dir=os.path.join(tempfile.mkdtemp(), "wt"))
        self.lp.init()
        _write(self.lp.config_path, dict(self.lp.config(), **cfg))
        st = self.lp.load()
        self.lp._select_lenses(st)
        self.lp._save(st)
        os.environ["FAKE_ROOT"] = self.root

    def tearDown(self):
        os.environ.pop("FAKE_ROOT", None)
        os.environ.pop("FAKE_EDIT", None)
        super().tearDown()

    def run_loop(self):
        from improvement_loop import run as R
        with contextlib.redirect_stdout(io.StringIO()):
            return R.Runner(self.lp).run()

    def test_full_autonomy_one_cycle(self):
        self.assertEqual(self.run_loop(), 0)
        st = self.lp.load()
        self.assertEqual(st["step"], "done")
        self.assertTrue(all(a["by"] == "policy:autonomy-full" for a in st["approvals"]))
        log = subprocess.run(["git", "-C", self.root, "log", "--format=%s"], capture_output=True, text=True).stdout
        self.assertIn("P-1: change", log)
        self.assertEqual(D.parse(self.lp.decisions_path)[-1]["status"], "fixed")
        self.assertNotIn("improvement-loop", subprocess.run(["git", "-C", self.root, "status", "--porcelain"],
                                                            capture_output=True, text=True).stdout)

    def test_checks_and_critics_run_concurrently(self):
        import sys, time
        cfg = self.lp.config()
        stamp = os.path.join(tempfile.mkdtemp(), "check")
        cfg.update(autonomy="off", checks=[{"name": "slow", "cmd": [sys.executable, "-c",
                   f"import time; open({stamp!r} + '.start', 'w').write(repr(time.time())); time.sleep(3); "
                   f"open({stamp!r} + '.end', 'w').write(repr(time.time()))"]}])
        _write(self.lp.config_path, cfg)
        os.environ["FAKE_SLEEP"] = "3"
        os.environ["FAKE_STAMP"] = os.path.join(tempfile.mkdtemp(), "critic")
        try:
            self.run_loop()                                     # parks at ranking
        finally:
            os.environ.pop("FAKE_SLEEP")
        self.addCleanup(os.environ.pop, "FAKE_STAMP", None)
        read = lambda p: float(open(p).read())
        c0, c1 = read(stamp + ".start"), read(stamp + ".end")
        a0, a1 = read(os.environ["FAKE_STAMP"] + ".start"), read(os.environ["FAKE_STAMP"] + ".end")
        self.assertTrue(a0 < c1 and c0 < a1, "the check and the critic should run at the same time")
        self.assertTrue(os.path.exists(self.lp.path("checks/.done")))
        with open(self.lp.path("agents/architecture-critic.prompt.md")) as fh:
            text = fh.read()
        self.assertIn("Do NOT run the project's full test suite", text)
        self.assertIn("never inside the project", text)                     # L-7: scratch stays outside
        self.assertNotIn(self.root, text.split("Put scratch work")[1].split("never")[0])

    def test_wave_items_build_in_parallel_and_merge(self):
        import time
        os.environ.update(FAKE_PLAN="two", FAKE_BUILD_SLEEP="2")
        try:
            self.assertEqual(self.run_loop(), 0)
        finally:
            for k in ("FAKE_PLAN", "FAKE_BUILD_SLEEP"):
                os.environ.pop(k)
        import datetime as dt
        with open(os.path.join(self.lp.dir, "RUN.md")) as fh:
            ends = [dt.datetime.fromisoformat(ln.split()[1]) for ln in fh if "agent build-" in ln]
        self.assertEqual(len(ends), 2)
        self.assertLess(abs((ends[0] - ends[1]).total_seconds()), 1.5, "the two 2 s builds should overlap")
        log = subprocess.run(["git", "-C", self.root, "log", "--format=%s"], capture_output=True, text=True).stdout
        self.assertIn("P-1: change", log)
        self.assertIn("P-2: change", log)
        branches = subprocess.run(["git", "-C", self.root, "branch"], capture_output=True, text=True).stdout
        self.assertNotIn("item-c1", branches)                                       # temp branches cleaned up

    def test_wave_conflict_rejects_only_one_item(self):
        os.environ["FAKE_PLAN"] = "conflict"
        try:
            self.assertEqual(self.run_loop(), 0)
        finally:
            os.environ.pop("FAKE_PLAN")
        with open(self.lp.path("BUILD.md")) as fh:
            build = fh.read()
        self.assertIn("P-1 | loop/auto |", build)
        self.assertIn("P-2 | loop/auto | rolled back | merge conflict", build)

    def test_L9_failing_test_output_is_kept(self):
        import sys
        cfg = self.lp.config()
        cfg["test_cmd"] = [sys.executable, "-c", "import sys; print('noise\\n' * 50 + 'FAILED tests/test_x.py::test_y'); sys.exit(1)"]
        _write(self.lp.config_path, cfg)
        from improvement_loop import run as R
        ok, why = R.Runner(self.lp, echo=False).tests_pass(label="probe")
        self.assertFalse(ok)
        self.assertIn("FAILED tests/test_x.py::test_y", why)
        self.assertTrue(os.path.exists(self.lp.path("tests/probe.log")))

    def test_L10_killed_test_run_parks_instead_of_failing_the_item(self):
        import sys
        cfg = self.lp.config()
        cfg["test_cmd"] = [sys.executable, "-c", "import os, signal; os.kill(os.getpid(), signal.SIGALRM)"]
        _write(self.lp.config_path, cfg)
        from improvement_loop import run as R
        with contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(R.Parked) as cm:
                R.Runner(self.lp).tests_pass(label="probe")
        self.assertIn("killed twice", str(cm.exception))

    def test_off_parks_at_ranking(self):
        cfg = self.lp.config()
        cfg["autonomy"] = "off"
        _write(self.lp.config_path, cfg)
        self.assertEqual(self.run_loop(), 3)
        self.assertEqual(self.lp.load()["step"], "rank")
        with open(os.path.join(self.lp.dir, "RUN.md")) as fh:
            self.assertIn("ranking ready for the owner", fh.read())

    def test_protected_path_rolled_back(self):
        os.environ["FAKE_EDIT"] = "gate.py"
        self.assertEqual(self.run_loop(), 0)        # item rolled back; the empty cycle still completes
        with open(os.path.join(self.root, "gate.py")) as fh:
            self.assertEqual(fh.read(), "x = 1\n")
        self.assertEqual(D.parse(self.lp.decisions_path)[-1]["status"], "deferred")

    def test_build_autonomy_resumes_after_owner_next(self):
        cfg = self.lp.config()
        cfg.update(autonomy="build", max_cycles=3)
        _write(self.lp.config_path, cfg)
        self.assertEqual(self.run_loop(), 3)                    # parks at release
        self.lp.approve("release", "owner")
        self.assertEqual(self.run_loop(), 3)                    # parks at audit
        self.assertEqual(self.lp.load()["step"], "audit")
        self.lp.approve("next", "owner")
        self.run_loop()
        self.assertEqual(self.lp.load()["cycle"], 2)            # resumed past the owner's approval

    def test_gate_rollback_reopens_and_justification(self):
        cfg = self.lp.config()
        cfg["metrics"] = [{"key": "product_loc", "kind": "loc", "include": ["**/*.py"], "gated": True}]
        _write(self.lp.config_path, cfg)
        os.environ["FAKE_GROW"] = "1"
        try:
            self.assertEqual(self.run_loop(), 3)                 # builder grew code, no justification
        finally:
            os.environ.pop("FAKE_GROW")
        self.assertEqual(D.parse(self.lp.decisions_path)[-1]["status"], "reopened")
        self.assertEqual(D.closed(self.lp.decisions_path), set())
        self.assertEqual(D.load_fixed(self.lp.fixed_path), {})
        from improvement_loop import run as R
        plan = I.load_plan(self.lp.path("plan.json"))
        plan["items"][0]["justification"] = {"product_loc": "needed for the fix"}
        _write(self.lp.path("plan.json"), plan)
        _text(self.lp.path("BUILD.md"), "P-1 | loop/auto | abc | tests pass | A-1\n")
        self.assertIn("product_loc: P-1: needed for the fix", R.Runner(self.lp, echo=False).justifications())
        plan["items"][0]["labels"] = ["simplicity"]
        _write(self.lp.path("plan.json"), plan)
        self.assertEqual(R.Runner(self.lp, echo=False).justifications(), "")

    def test_stop_file_and_wrong_branch(self):
        _text(os.path.join(self.lp.dir, "STOP"), "")
        self.assertEqual(self.run_loop(), 3)
        os.remove(os.path.join(self.lp.dir, "STOP"))
        git(self.root, "checkout", "-qb", "main-ish")
        cfg = self.lp.config()
        cfg["autonomy"] = "build"
        _write(self.lp.config_path, cfg)
        self.assertEqual(self.run_loop(), 3)
        self.assertEqual(self.lp.load()["step"], "build")

    def test_priority_helpers(self):
        from improvement_loop import priority as PR
        self.assertEqual(PR._cpu_list("0-2,5"), {0, 1, 2, 5})
        self.assertEqual(PR.wrap(["x"], "normal"), ["x"])
        import sys
        p = PR.run([sys.executable, "-c", "print(1)"], "background", capture_output=True, timeout=30)
        self.assertEqual(p.stdout.strip(), b"1")
        self.assertRaises(ValueError, PR.lower_self, "turbo")


if __name__ == "__main__":
    unittest.main()
