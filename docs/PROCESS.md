# The process

Each cycle: independent critique → verified, reconciled, ranked weaknesses → a lean plan → parallel build →
gated release → audit → start again. Use it after a release, after an outside review, or whenever a project feels
heavy, fragile or hard to use.

## Roles and priorities
- **Owner** (a human) approves the ranked list, the plan, every release, and the stop decision (`loop approve`).
- The **verifier** is never the author of a finding (`loop set-verdict` refuses it). At least one critic should use
  a different model or vendor than the builders.
- **One integrator** merges lanes.
- **Priority order** for every decision and gate: correctness > security > user value > simplicity.
- **Critics disagree?** Tie-break: user evidence > correctness > simplicity; record the call with `loop decide ID note`.

## Budget, modes and stop
- **Budget** per loop in `.improvement-loop/config.json` (`minutes`, `cost`, `max_lanes`; 0 = unlimited). Record
  use with `loop spend`.
- **Lite mode**: two lenses (architecture + first-time user), verify, fix the top 3, gate, ship.
- **Full mode**: the core lenses plus every lens that applies to the project (see Lenses below).
- **Stop** (`loop stop-check`) when no verified finding of severity >= 3 remains, OR `max_cycles` is reached, OR
  the budget is spent. The owner decides (`loop approve stop`).

## Lenses and checks
Every critic is a **lens**: one dimension of quality with its own prompt (`loop prompt <lens>`). `loop lenses` shows
which run this cycle and why. The selection is fixed when a cycle starts.

| Lens | Runs when | Looks for |
|---|---|---|
| external-reviewer, architecture-critic, state-machine-critic, first-time-user | always (core) | outside view, over-engineering, reachable bad states, first-run friction |
| test-quality | test files exist | untested paths, flaky or empty tests |
| performance, reliability | code exists; every 2nd cycle | wasted work at real scale; unhandled failures, timeouts, partial writes |
| docs-drift | docs exist; every 2nd cycle | docs vs behaviour, unannounced interface breaks |
| dependencies | a manifest exists; every 2nd cycle | vulnerable, heavy, unpinned or removable dependencies |
| accessibility | UI files exist; every 2nd cycle | WCAG 2.2 AA, keyboard, contrast, phone width |

Cycle 1 runs every lens that applies. Config: `lenses` (explicit list), `retired` (never run), `critics` (old
explicit list; overrides everything). **Project lenses**: drop `<name>.md` in `.improvement-loop/lenses/` with a
header (`detect: <globs>`, `every: N`, `core: true`) and `{{contract}}` where the output contract goes; it can stay
private while the loop stays generic. **Checks**: `checks` in config is a list of
`{"name", "cmd": [...], "lens", "category", "severity", "timeout"}`. `loop check` (critique step) runs them
without a shell; each failure becomes a finding in `findings/checks.json` whose evidence is the check's log, so
coverage, audits and benchmarks reach the ranking without an opinion. **Scores**: `loop lenses --scores` writes
`LENS-SCORES.md` (per lens: raised, accepted, false, fixed, precision). Retire lenses that keep missing.

## Measured gates: performance, interface, privacy baseline
- **Performance is measured, never assumed.** A performance finding carries `measured: <command> -> <result>` in
  `notes`; without it the verifier caps it at `partly`, severity 2. A plan item that fixes one adds a `bench`
  metric: `{"key": "status_ms", "kind": "bench", "cmd": [...], "parse": "(\\d+\\.?\\d*) ms", "repeat": 5,
  "tolerance": 0.05, "gated": true}`. The gate runs it at the base and at the head (temporary checkouts) and fails
  on a median slower beyond the tolerance.
- **Interface breaks need a reason.** A `surface` metric runs a command that prints the public surface one item per
  line (CLI commands, API paths, config keys): `{"key": "cli", "kind": "surface", "cmd": [...], "gated": true}`.
  Any removed line fails the gate unless justified (with a changelog line).
- **Privacy compares with the base.** `loop privacy --base <ref>` (what `loop run` uses) fails only on hits that
  are new since the cycle base, so a project with reviewed, already-public hits can still release.
- **Critique is parallel:** checks and up to `max_parallel` (8) critics run at once; each critic has `critic_timeout` (900 s), reads the check logs instead of re-running the suite, and from cycle 2 starts from the diff since the last cycle.
- **Checks that time out or cannot start stop the run** as infrastructure failures, not findings; at background
  priority each check gets 3x its `timeout`.

## Autonomy (`loop run`)
`loop run` drives the cycle with any agent CLI: `"agent": {"cmd": ["claude", "-p", "--permission-mode",
"bypassPermissions"], "timeout": 3600}` (the prompt goes on stdin, or where `{prompt_file}` appears), with
per-role overrides in `agents` (e.g. a different vendor for `external-reviewer`). Critics run in throwaway
worktrees, blind to each other; builders commit one plan item at a time on the loop's branch.

| `autonomy` | Approved by policy | Parks for the owner at |
|---|---|---|
| `off` (default) | nothing | ranking, plan, release, next/stop |
| `build` | ranking (findings >= `policy.min_severity`), plan (top `policy.top_n`) | release |
| `full` | also release, next and stop (stop rule binding) | only failures |

Guardrails, always on: builds only on a `branch_prefix` branch (`git worktree add ../proj-loop -b loop/auto`, run
the loop there; your checkout is never touched); `test_cmd` after every item, failed items rolled back and
deferred; `protected` globs (the referee: gates, policy, CI) may not change; a failed gate rolls the cycle back to
its base with a backup tag; budget, `max_cycles` and the stop rule end the run; `touch .improvement-loop/STOP`
stops it between steps; one driver per project. Nothing is ever pushed, filed or published. Policy approvals are
logged as `policy:autonomy-<level>`; progress in `.improvement-loop/RUN.md`, alerts via `notify`.

**Background by default** (`priority`: `background` | `low` | `normal`): macOS marks the driver and every child
Darwin-background (`taskpolicy -b`: efficiency cores, throttled I/O); Linux uses nice 19, `SCHED_IDLE`, idle I/O
and, on hybrid Intel CPUs, the efficiency cores; Windows uses idle priority plus EcoQoS. `loop run --detach`
starts it in the background; `loop run --service` prints a launchd / systemd / Task Scheduler definition that
resumes it every hour. **Applying the loop to itself**: run the installed release, not the checkout, and protect
`improvement_loop/state.py`, `privacy.py`, `metrics.py`, `run.py` and `.github/**`.

## Steps and the commands that guard them

| Step | Who | Leaves when (checked by `loop advance`) |
|---|---|---|
| critique | critics in parallel, blind to each other | a schema-valid `findings/<role>.json` per configured critic |
| verify | `loop verify`, then the verifier | every finding has an evidence check and a verdict |
| rank | `loop rank` | `RANKED.md` exists and the owner approved `ranking` |
| plan | planner | valid `plan.json` (atomic items, simplicity check) and owner approved `plan` |
| issues | `loop issues draft` / `file` | issues drafted (filing is a dry run unless `--yes`) |
| build | lanes + integrator | `BUILD.md` merge log |
| gate | `loop gate` (complexity metrics + regression check of fixed findings), `loop privacy` | `gate.json` and `privacy.json` both record pass and the owner approved `release`; `loop advance` reads these recorded verdicts and does not re-run them |
| audit | auditor | `AUDIT.md` and owner approved `next` (new cycle) or `stop` (done) |

### Critique (step 0)
Every lens selected for the cycle, blind to the others, plus `loop check`. Anything sent to an external reviewer is scrubbed first (`loop privacy --text`).

### Verify (step 1)
Every quote is machine-checked against the cited file:line; the verifier then judges the claim: true / partly /
false / not checkable. Only true and partly findings are ranked.

### Reconcile and rank (step 2)
Duplicates merge by fingerprint. Score = severity (1-5) × reach (1-5). Correctness and security findings of
severity >= 4 go first regardless of score. Ties: removes complexity first, then lower effort. Rejected or deferred
findings go to `DECISIONS.md` (`loop decide`) and are never re-raised.

### Plan without over-engineering (step 3)
1. The default path gets smaller, never bigger. 2. Remove, merge or switch off before adding. 3. One owner per
moving part. 4. Every background actor names its user benefit, or it goes. 5. Prefer explicit state machines.
6. Truthful docs only.

### Issues, build, gates (steps 4-6)
One issue per atomic piece (4 hours or less) with a simplicity check and the metric it moves. One lane per
independent issue; lanes on the same component run one after another. Gates: privacy (secrets + your denylist +
two independent reviewers, one a different model), complexity (a worsened gated metric needs a one-line
justification; tests and docs are excluded; target: product code goes down), tests, regression check of fixed
findings, and a one-step rollback.

### Audit and iterate (steps 7-8)
Fresh critics, the state-machine lens again, metrics vs targets. The audit's findings seed the next cycle.

## Outputs per cycle
`.improvement-loop/cycle-N/`: `findings/`, `verified.json`, `RANKED.md`, `plan.json` + `PLAN.md`, `issues/`,
`BUILD.md`, `METRICS.md`, `gate.json`, `privacy.json`, `AUDIT.md`. Across cycles: `DECISIONS.md`, `fixed.json`,
`state.json`, `config.json`. Decide whether to commit `.improvement-loop/`; if you do, the privacy scan covers it.
