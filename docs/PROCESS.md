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
- **Lite mode**: two critics (architecture + first-time user), verify, fix the top 3, gate, ship.
- **Full mode**: all critics. The state-machine lens and metrics may run every second cycle if nothing structural
  changed (set `critics` in the config).
- **Stop** (`loop stop-check`) when no verified finding of severity >= 3 remains, OR `max_cycles` is reached, OR
  the budget is spent. The owner decides (`loop approve stop`).

## Steps and the commands that guard them

| Step | Who | Leaves when (checked by `loop advance`) |
|---|---|---|
| critique | critics in parallel, blind to each other | a schema-valid `findings/<role>.json` per configured critic |
| verify | `loop verify`, then the verifier | every finding has an evidence check and a verdict |
| rank | `loop rank` | `RANKED.md` exists and the owner approved `ranking` |
| plan | planner | valid `plan.json` (atomic items, simplicity check) and owner approved `plan` |
| issues | `loop issues draft` / `file` | issues drafted (filing is a dry run unless `--yes`) |
| build | lanes + integrator | `BUILD.md` merge log |
| gate | `loop gate`, `loop privacy`, `loop regress` | both pass and the owner approved `release` |
| audit | auditor | `AUDIT.md` and owner approved `next` (new cycle) or `stop` (done) |

### Critique (step 0)
External objective review · architecture / over-engineering critic · implicit state machine review · first-time
user on a fresh machine · metrics. Anything sent to an external reviewer is scrubbed first (`loop privacy --text`).

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
