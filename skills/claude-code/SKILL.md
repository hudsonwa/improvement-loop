---
name: improvement-loop
description: Run the improvement loop on a software project, then improve the loop itself from what it got wrong. Use when the user says "run the improvement loop", "run the improvement-loop and improve recursively", "improve this project with the loop", or asks to review, simplify, speed up or harden a project in a repeatable, gated way.
---

# improvement-loop

The `loop` CLI (`pip install git+https://github.com/hudsonwa/improvement-loop`, or the user's own checkout) holds
the state machine and does every deterministic check. Agents do the judgement through role prompts. Never edit
`.improvement-loop/state.json` by hand. Never push, publish or file issues without the user's yes.

## "Run the improvement loop" (on the current project)
1. **Set up**: `loop init` in the project root if `.improvement-loop/` is missing. In
   `.improvement-loop/config.json` set:
   - `test_cmd`: the project's own test command (a list, no shell);
   - `metrics`: add a `surface` metric (public CLI help, routes, config keys) and a `bench` metric for the latency
     or throughput that matters; `loop init` prints suggestions;
   - `agent`: e.g. `{"cmd": ["claude", "-p", "--permission-mode", "bypassPermissions"], "timeout": 3600}`;
     use a different vendor for `agents.external-reviewer` only if the project may leave the machine;
   - `autonomy: "full"` and `max_cycles: 2` unless the user says otherwise;
   - `protected`: the tests and configs that act as the referee.
2. **Run**: `loop run --detach` (background, low priority, on a `loop/` branch, never pushes). Follow progress with
   `loop status` and `.improvement-loop/run.log`. If it parks, read why, fix the cause, and run it again.
3. **Report**: when it stops, summarise what each cycle released, what was rolled back and why, and the stop reason.

Without an agent CLI, drive it by hand: `loop status`, then do exactly what `unmet` lists, one step per turn
(critics as blind subagents from `loop prompt <role>`, `loop verify`, `loop rank`, plan, build, gate, audit).
Approvals (`loop approve`) are the user's unless they set autonomy.

## "...and improve recursively" (the loop improves itself)
Do this after the project run, never instead of it. The loop improves from evidence, not from scoring itself.
1. **Evaluate the project run** with `docs/EVALUATION.md` in the loop's repo: before/after snapshots as blind copies
   A and B, measurements, and an independent judge that saw no git history. Private projects: local models only.
2. **Turn what went wrong into loop findings**: every regression the judge confirmed, every false rollback, park or
   slow step in `run.log`. Say where each escaped (critique, verify, plan, build, gate, policy).
3. **Fix the loop** on a `loop/` branch of the loop's repo: one regression test per finding, the full suite green,
   then `loop privacy --base origin/main --history origin/main..HEAD` with a denylist of the project's private
   names, so nothing from the project leaks into the public loop.
4. **Report** both results (the project's net score, the loop fixes) and ask before pushing either repo.

Priority for every call: correctness > performance > security > simplicity. Measure before claiming a speed-up.
Run long jobs in the background at low priority.
