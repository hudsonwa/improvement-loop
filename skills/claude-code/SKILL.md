---
name: improvement-loop
description: Run the improvement loop on a software project, then improve the loop itself from what it got wrong. Use when the user says "run the improvement loop", "run the improvement-loop and improve recursively", "improve this project with the loop", or asks to review, simplify, speed up or harden a project in a repeatable, gated way.
---

# improvement-loop

The `loop` CLI holds the state machine and does every deterministic check; agents do the judgement through role
prompts. Reference: https://github.com/hudsonwa/improvement-loop/blob/main/docs/PROCESS.md. Never edit
`.improvement-loop/state.json` by hand. Never push, publish or file issues without the user's yes.

## "Run the improvement loop" (on the current project)
1. **Ask the user once**, with your recommendation: lite or full mode; `autonomy` (`build` recommended: it
   parks before keeping the result; `full` decides everything); and which tools the agents get (the fewest that
   work, not `bypassPermissions`, unless they run in a sandbox). Say it takes hours and about 6 (lite) or 15
   (full) agent sessions per cycle.
2. **Set up**, from the project's main checkout (a git repo with a commit):
   - `git worktree add ../<name>-loop -b loop/auto` and work there; the loop only builds on a `loop/` branch;
   - `git config user.email` to a noreply address in that worktree (the privacy gate flags real emails);
   - `loop init [--mode lite]`, then in `.improvement-loop/config.json` set **`test_cmd`** (the project's test
     command, a list, no shell) and **`protected`** (tests, CI, gate config). Both are required: without them
     builds merge untested and agents can edit their own referee;
   - `agent` (e.g. `{"cmd": ["claude", "-p", "--allowedTools", "..."], "timeout": 3600}`), `autonomy`,
     `max_cycles: 2`, a `budget.minutes`;
   - `metrics`: a list replaces the defaults, so keep them and add a `surface` metric (public CLI help, routes,
     config keys) and a `bench` metric for the latency that matters.
3. **Run**: `loop run --detach`. Follow `loop status` and `.improvement-loop/RUN.md`. If it parks on a failure,
   read why (PROCESS.md, Troubleshooting), fix the cause, delete any `.improvement-loop/STOP`, run it again.
   If it parks for an approval, show the user `RANKED.md`, `PLAN.md` or the gate result and ask.
4. **Report**: what each cycle kept, what was rolled back and why, the stop reason, and how to review and merge
   (`git log main..loop/auto`; README, After a run).

Without an agent CLI, drive it by hand: `loop status`, then do exactly what `unmet` lists, one step per turn
(each lens as a separate blind subagent from `loop prompt <lens>`, `loop verify`, verdicts by a different
subagent, `loop rank`, plan, build, gate, audit). Approvals (`loop approve`) are the user's.

## "...and improve recursively" (the loop improves itself)
Do this after the project run, never instead of it. The loop improves from evidence, not from scoring itself.
It changes the loop's own clone (e.g. `~/improvement-loop`); tell the user so first.
1. **Evaluate the project run** with docs/EVALUATION.md: before/after snapshots as blind copies A and B,
   measurements, and an independent judge with no git history. Private projects: local models only.
2. **Turn what went wrong into loop findings**: every regression the judge confirmed, every false rollback, park
   or slow step in `RUN.md`. Say where each escaped (critique, verify, plan, build, gate, policy).
3. **Fix the loop** on a `loop/` branch of its clone: one regression test per finding, the full suite green,
   then `loop privacy --base origin/main --history origin/main..HEAD` with a denylist of the project's private
   names, so nothing from the project leaks into the public loop.
4. **Report** both results (the project's net score, the loop fixes) and ask before pushing either repo.

Priority for every call: correctness > security > performance > user value > simplicity. Measure before
claiming a speed-up. Run long jobs in the background at low priority.
