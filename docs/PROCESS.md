# The process: reference

Start with the [README](../README.md). This page is the reference: [glossary](#glossary) · [steps](#steps) ·
[roles](#roles-and-priorities) · [modes and stop](#modes-and-the-stop-rule) · [autonomy](#autonomy) ·
[safety](#safety-model) · [gates](#gates) · [privacy](#privacy-scan) · [lenses and checks](#lenses-and-checks) ·
[files you write](#files-you-write) · [configuration](#configuration-reference) ·
[troubleshooting](#troubleshooting) · [the loop on itself](#applying-the-loop-to-itself)

## Glossary
| Term | Meaning |
|---|---|
| cycle | one pass through the 8 steps; `cycle-N/` holds its files |
| lens / critic | one quality dimension with its own prompt; each runs as a separate, blind agent session |
| role | any prompt: a lens, or `verifier`, `planner`, `integrator`, `auditor`, `metrics`, `eval-judge` |
| finding | one problem with quoted evidence (`file`, `line`, `quote`) that the CLI checks against the code |
| verdict | the verifier's judgement of a finding: `true`, `partly`, `false`, `not_checkable` |
| owner | the human who approves; approvals are recorded with `--by NAME`, not authenticated |
| approval | `ranking`, `plan`, `release`, `next`, `stop`: the permission to leave a step (table below) |
| "release" | approval to keep the gated cycle on the loop branch. Nothing is published or pushed |
| item / issue | one planned fix (`plan.json`), at most 4 hours; drafted as an issue file |
| wave | plan items that touch different components and can be built in parallel |
| gate | the checks before a cycle is kept: tests, complexity metrics, regressions, privacy |
| referee | files the agents must not change (`protected`): tests, CI, gate config |
| park | `loop run` stops and waits, with the reason in `RUN.md` |
| surface / bench | gate metrics: the public interface (lines that may not vanish) / a measured timing |
| fingerprint | a hash of a finding's claim and place, used to dedupe and to never re-raise decided findings |

## Steps
| Step | Who | Leaves when (`loop advance` checks) | Approval |
|---|---|---|---|
| critique | lenses in parallel, blind; `loop check` | a valid `findings/<lens>.json` per lens (an empty list is fine) | |
| verify | `loop verify`, then a verifier who wrote none of them | every finding has an evidence check and a verdict | |
| rank | `loop rank` | `RANKED.md` exists | `ranking` |
| plan | planner (`loop prompt planner`) | valid `plan.json` | `plan` |
| issues | `loop issues draft` (`file --repo O/N --yes` to file) | issue drafts exist | |
| build | builders, one per item, on the loop branch | `BUILD.md` | |
| gate | `loop gate`, `loop privacy`, tests | `gate.json` and `privacy.json` pass for this step and commit | `release` |
| audit | auditor (`loop prompt auditor`) | `AUDIT.md` | `next` (new cycle) or `stop` (done) |

- **Verify:** quotes are machine-checked (exact text within 2 lines of the cited line). Only `true` and `partly`
  are ranked. A performance finding without `measured: <command> -> <result>` in `notes` gets at most `partly`,
  severity 2.
- **Rank:** duplicates merge by fingerprint. Score = severity × reach. Correctness and security findings of
  severity ≥ 4 go first. Ties: removes complexity, then lower effort. `loop decide ID rejected|deferred` puts a
  finding in `DECISIONS.md`; it is never raised again.
- **Plan:** the planner's rules: the default path gets smaller; remove before adding; one owner per moving part;
  every background actor names its user benefit; prefer explicit state machines; truthful docs; removing a feature
  also removes or migrates what it stored, with a deprecation or changelog line.
- **Build (`loop run`):** items in one wave are built at once, each by its own agent in its own worktree and
  `loop/item-*` branch, then cherry-picked onto the loop branch; a conflict rejects only that item. One test run
  checks the merged wave; on failure the wave is re-applied one item at a time. Manual mode: build each item on
  the loop branch yourself and log it in `BUILD.md`.
- **Audit:** fresh eyes on the cycle. The auditor's findings go in `cycle-N/next-findings.json` and seed the next
  cycle's critique.

## Roles and priorities
- **Priority for every decision and gate: correctness > security > performance > user value > simplicity.**
  When critics disagree, real user evidence wins; record the call with `loop decide ID note`.
- The verifier is never the finding's author (`set-verdict` compares `--by` with the finding's role).
- At least one critic (`external-reviewer`) should use a different model or vendor than the builders.

## Modes and the stop rule
- **Lite** (`loop init --mode lite`): two lenses (architecture, first-time user), then fix the top
  `lite_top_n` (3).
- **Full:** core lenses plus every lens that applies to the project.
- **Stop** (`loop stop-check` exits 0 when met) when any of these holds:
  - no verified finding at or above `stop_severity` (3) is left;
  - the cycle's fixes add up to less than `min_gain` (6), summing the severities of `BUILD.md` rows marked
    `tests pass`;
  - `max_cycles` (3) is reached, or the budget is spent.
- `loop run` also parks at rank when no finding reaches `policy.min_severity` (3): nothing worth building.

## Autonomy
`loop run` drives the steps with the agent CLI in `agent` (prompt on stdin, or written to the file named where
`{prompt_file}` appears in `cmd`; `{cwd}` is replaced too). `agents` overrides it per role: a lens name,
`verifier`, `planner`, `auditor`, or `build-<item id>`.

| `autonomy` | Approved for you (logged as `policy:autonomy-<level>`) | Parks for the owner at |
|---|---|---|
| `off` (default) | nothing | ranking, plan, release, next/stop |
| `build` | ranking (findings ≥ `policy.min_severity`), plan (top `policy.top_n`, 5) | release |
| `full` | also release, next and stop | only failures |

### Running in the background
- `loop run --detach` starts it at background priority; progress goes to `.improvement-loop/RUN.md`, raw output
  to `.improvement-loop/run.log`, the pid to `run.pid`.
- Stop: `touch .improvement-loop/STOP` (it stops between steps). **Delete the file before resuming**, or the run
  parks at once.
- `loop run --service [--every-hours N]` only *prints* a launchd / systemd / Task Scheduler definition that
  resumes the run; install it yourself, and remove it when done. A resume while parked for the owner parks again.
- **Priority** (`priority`: `background` | `low` | `normal`): macOS `taskpolicy -b` (efficiency cores, throttled
  I/O); Linux nice 19, `SCHED_IDLE`, idle I/O, and the efficiency cores on hybrid Intel; Windows idle priority
  plus EcoQoS. Expect slower runs when the machine is busy.

### What `loop run` touches
- Commits on the current `loop/` branch only (it refuses any other branch).
- `loop/item-c<N>-*` branches for parallel builds, and `loop-backup-c<N>-*` tags before a rollback.
- `../.improvement-loop.noindex/<hash>/` beside the project: temporary worktrees and agent scratch space (change
  with `worktrees_dir`). Delete it after a run.
- `.improvement-loop/` added to `.git/info/exclude`.

## Safety model
| Enforced by the CLI | Only asked of the agent (in its prompt) |
|---|---|
| builds only on a `loop/` branch; your branch untouched | never push, file issues or publish |
| `test_cmd` after each item and at the gate; failures rolled back | no installs, no network beyond the model |
| `protected` paths may not change | no files outside the worktree and scratch dir |
| failed gate rolls the cycle back, with a backup tag | |
| budget, `max_cycles`, STOP file, one driver per project | |

The CLI cannot stop an agent with wide permissions from running `git push`, reading your SSH keys or calling
other services. So:
- give the agent the fewest tools that work (the README's `--allowedTools` line), not `bypassPermissions`;
- or run in a container, VM or separate user with no push credentials;
- always set `test_cmd` and `protected`. Without `test_cmd`, builds are merged untested.

## Gates
`loop gate --base <ref>` compares the base with the head (the working tree, or `--head <ref>`).

### Complexity metrics
`metrics` in config.json is a list. **A list replaces the defaults**, so copy the ones you want to keep:

| key | kind | gated | counts |
|---|---|---|---|
| `product_files` | `files` | yes | product code files (tests, docs, examples excluded) |
| `product_loc` | `loc` | yes | non-blank product code lines |
| `background_actors` | `regex` | yes | threads, timers, tasks, processes started |
| `listeners` | `regex` | yes | servers and open ports |
| `readme_lines` | `lines` | no | README length |
| `test_files` | `files` | no | test files (more is fine: `higher_is_worse: false`) |

Fields: `key`, `kind` (`files`, `loc`, `regex`, `lines`, `bench`, `surface`), `include` / `exclude` (globs),
`pattern` (regex), `file` (for `lines`), `gated`, `higher_is_worse` (default true), `target` (text for reports).

**A gated metric that grows fails the gate**, even for a one-line bug fix. To keep a justified increase, pass
`--justify FILE` with one `metric_key: one-line reason` per line. Under `loop run`, only plan items labelled
`correctness` or `security` with a `justification` object may grow a gated metric.

### Performance: `bench`
`{"key": "status_ms", "kind": "bench", "cmd": [...], "parse": "(\\d+\\.?\\d*) ms", "repeat": 5,
"tolerance": 0.05, "gated": true}`: run at base and head in temporary checkouts; fails when the head's median is
slower beyond the tolerance.

### Interface: `surface`
`{"key": "cli", "kind": "surface", "cmd": ["sh", "-c", "mytool --help"], "gated": true}`: the command prints the
public interface, one item per line (commands, routes, config keys). Any removed line fails unless justified,
with a changelog line. The gate warns when no `surface` metric is set.

### Regressions of fixed findings
Every finding marked fixed keeps its quotes. If **all** of them are still at the cited place, the gate calls it
`suspect` and fails (`loop regress` lists them). So a fix that adds a guard above the quoted line, leaving the
line itself, looks like a regression. Critics are told to quote the line the fix must change. If the fix is
real, add `ID: why the fix holds` to the `--justify` file; if it is not fixed, `loop decide ID reopened`.

### Manual mode: the base
Tag the commit you start from (`git tag loop/c1-base`) and pass it to `--base` and `--history loop/c1-base..HEAD`.
`loop run` records the base itself.

## Privacy scan
`loop privacy [--denylist FILE] [--base REF] [--history RANGE] [--text FILE...]` flags:
- private keys, tokens and secret-looking assignments;
- email addresses, except noreply and `example.com` ones (**including commit author and committer lines** with
  `--history`: commit with a noreply address);
- IPv4 addresses outside documentation ranges, and home paths (`/Users/x`, `/home/x`, `C:\Users\x`);
- every term in your denylist.

`--base REF` fails only on hits that are new since REF. Commit metadata in `--history` is always checked.

**Denylist** (keep it outside the repo; or set `denylist` / `$IMPROVEMENT_LOOP_DENYLIST`):
```
# one term per line, case-insensitive substring
acme-internal
Project Falcon
re:\bfalcon-[0-9]+\b
```
A line containing `privacy:allow` is skipped by the scan (reviewers see it; use sparingly).

## Lenses and checks
`loop lenses` shows which lenses run this cycle and why; `loop lenses --scores` writes `LENS-SCORES.md` (raised,
accepted, false, fixed, precision per lens). Retire weak ones with `retired`.

| Lens | Runs when | Looks for |
|---|---|---|
| external-reviewer, architecture-critic, state-machine-critic, first-time-user | always (core) | outside view, over-engineering, reachable bad states, first-run friction |
| test-quality | test files exist | untested paths, flaky or empty tests |
| performance, reliability | code exists; every 2nd cycle | measured waste; unhandled failures, timeouts, partial writes |
| docs-drift | docs exist; every 2nd cycle | docs vs behaviour, unannounced interface breaks |
| dependencies | a manifest exists; every 2nd cycle | vulnerable, heavy, unpinned or removable dependencies |
| accessibility | UI files exist; every 2nd cycle | WCAG 2.2 AA, keyboard, contrast, phone width |

Cycle 1 runs every lens that applies. The selection is recomputed until the first findings file is written.

**Writing a lens:** `.improvement-loop/lenses/<name>.md`. A project lens with a built-in's name replaces it; one
without `detect` always runs.
```markdown
---
detect: api/**, **/*.proto
every: 1
core: false
lite: false
---
# Lens: api-compat
Look for breaking changes in the public API ...
{{contract}}
```

**Checks:** commands whose failure becomes a finding, with the log as evidence. `loop check` runs them (no shell)
during critique; a check that times out or cannot start stops the run instead.
```json
"checks": [{"name": "coverage", "cmd": ["pytest", "--cov", "--cov-fail-under=80"], "lens": "test-quality",
            "category": "correctness", "severity": 3, "reach": 3, "timeout": 600}]
```
Defaults: `timeout` 600 s (×3 at background priority), `severity` 3, `reach` 3, `category` correctness.

## Files you write
Formats the CLI checks. In `loop run` the agents write these; in manual mode you do.

**Findings** (`cycle-N/findings/<lens>.json`, schema in `improvement_loop/finding.schema.json`):
```json
{"findings": [{"id": "FTU-1", "role": "first-time-user", "claim": "divide() crashes on b=0",
  "category": "correctness", "evidence": [{"file": "calc.py", "line": 3, "quote": "return a / b"}],
  "severity": 4, "reach": 3, "effort": 1, "removes_complexity": false,
  "fix_hint": "raise ValueError", "notes": "measured: python -c ... -> ZeroDivisionError"}]}
```
Nothing to report: `{"findings": []}`.

**plan.json:** `{"targets": {...}, "items": [{"id", "title", "finding_ids", "why", "scope_in", "scope_out",
"removes", "must_not_add", "acceptance", "metric", "tests", "depends_on", "labels", "estimate_hours" (≤ 4),
"wave"}]}`. Optional per item: `justification` (`{"metric_key": "reason"}`) for correctness or security items
that must grow a gated metric.

**BUILD.md:** one row per item, exactly five `|`-separated fields; only rows with `tests pass` count towards
`min_gain`:
```
P1 | loop/auto | 3f2a9c1 | tests pass | FTU-1, ARCH-2
P2 | loop/auto | - | rolled back | PERF-1
```

**AUDIT.md:** prose. New findings for the next cycle go in `cycle-N/next-findings.json` (findings format).

## Configuration reference
`.improvement-loop/config.json`; `loop init` writes the defaults.

| Key | Default | Effect |
|---|---|---|
| `mode` | `full` | `full` or `lite` |
| `max_cycles` | 3 | stop after this many cycles |
| `stop_severity` | 3 | stop when no verified finding at or above this is left |
| `min_gain` | 6 | stop when a cycle's fixes sum to less (0 = off) |
| `lite_top_n` | 3 | lite mode: findings planned per cycle (planner prompt) |
| `budget.minutes` / `budget.cost` | 0 / 0 | stop when spent (0 = unlimited); `loop run` records minutes only |
| `budget.max_lanes` | 4 | unused; parallelism is `max_parallel` and the plan's waves |
| `lenses` / `retired` / `critics` | auto / [] / null | lens selection; `critics` overrides everything |
| `checks` | [] | see Lenses and checks |
| `metrics` | null (defaults) | gate metrics; a list replaces the defaults |
| `denylist` | null | path to the privacy denylist |
| `agent` | null | `{"cmd": [...], "timeout": 3600}` for `loop run` |
| `agents` | {} | per-role overrides of `agent` |
| `autonomy` | `off` | `off`, `build`, `full` |
| `policy.top_n` / `policy.min_severity` | 5 / 3 | what autonomy approves |
| `priority` | `background` | `background`, `low`, `normal` |
| `max_parallel` | 8 | critics at once |
| `critic_timeout` | 900 | seconds per critic |
| `test_cmd` | null | **set it**: run after each item and at the gate (list, no shell) |
| `test_timeout` | 900 | seconds per test run; a killed run is retried once, then parks |
| `protected` | [] | **set it**: globs agents may not change |
| `branch_prefix` | `loop/` | the branch `loop run` builds on |
| `worktrees_dir` | `../.improvement-loop.noindex` | temporary worktrees and scratch |
| `notify` | null | command run on park or stop, e.g. `["say", "{message}"]` |

## Troubleshooting
Logs: `RUN.md` (steps and park reasons), `run.log` (raw output), `cycle-N/agents/*.log` (each agent),
`cycle-N/tests/*.log`, `cycle-N/checks/*.log`, `cycle-N/gate.json`, `privacy.json`.

| Message | Cause | Fix |
|---|---|---|
| builds only on a `loop/*` branch | started on `main` | `git worktree add ../p-loop -b loop/auto`, run there |
| git ... failed / `--git-common-dir` | not a git repo, or no commit yet | `git init && git commit` first |
| no agent configured | `agent` unset | set `agent.cmd` |
| critique incomplete | a lens wrote no valid file | read `cycle-N/agents/<lens>.log`; rerun `loop run` |
| working tree not clean before build | uncommitted changes in the loop worktree | commit or stash them |
| gate failed: product_loc ... | a gated metric grew | justify it (see Gates) or simplify the fix |
| gate failed: regression ... | a fixed finding's quote is still in place | see Regressions of fixed findings |
| PRIVACY: FAIL on `git-log:` | a real email in commit metadata | commit with a noreply address |
| test run was killed twice | `test_timeout` too short, or the machine is overloaded | raise `test_timeout` |
| ... is stale; re-run it | `gate.json` or `privacy.json` is from another step or commit | run the gate again |
| STOP file present | `.improvement-loop/STOP` exists | delete it, then `loop run` |
| ranking/plan ready for the owner | autonomy is `off` (or below the policy) | read it, `loop approve ...`, `loop run` |
| another driver is running | `run.pid` is live (or stale after a crash) | stop it, or delete a stale `run.pid` |

Exit codes: 0 ok, 1 a check failed (gate, privacy, validate, regress), 2 error, 3 `loop run` parked.

## Applying the loop to itself
Run an installed release, not the checkout you are improving, and protect the referee:
`improvement_loop/state.py`, `privacy.py`, `metrics.py`, `run.py` and `.github/**`. Judge the result from
outside with [EVALUATION.md](EVALUATION.md), not with the loop's own scores.
