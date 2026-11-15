# Evaluating the loop: did it actually make the project better?

The loop judges its own cycles. Its critics, verifier and gates all live inside it. This document is the
outside check: run the loop on a real project, then compare the project **before** and **after** with
measurements and a blind, independent judge. Every confirmed regression is turned into a finding against the
loop itself. Use it on your own projects to decide whether to keep a loop's changes. Use it on the loop to
improve the loop.

## When to run it
- After a loop run of one or more cycles on a project, before you publish the result.
- After changing the loop itself (new lenses, prompts, policy, models), on the same projects as last time.
- Never on the run's own branch while the run is active. Snapshot first.

## 1. Snapshots
| Name | What | How |
|---|---|---|
| `before` | the commit the run started from | `git tag eval/before <base>` (`loop run` records the base of cycle 1 in `.improvement-loop/run.json`; manual runs: the commit you tagged at the start) |
| `after` | the commit the run ended on | `git tag eval/after <head>` |
| `cN` (optional) | the release of each cycle | one tag per cycle, to see where the returns flatten out |

Check each one out into its own clean worktree. Strip `.improvement-loop/`, commit messages and branch names from
what the judge sees. Label the two copies **A** and **B**, assigned by a coin flip, and record the mapping
somewhere the judge cannot read it.

Commands (bash), run from the project, outside the judge's view:
```bash
mkdir -p ../eval && for s in before after; do
  git worktree add --detach ../eval/$s eval/$s
  rm -rf ../eval/$s/.git ../eval/$s/.improvement-loop       # no history, no loop state
done
if [ $((RANDOM % 2)) = 0 ]; then a=before; b=after; else a=after; b=before; fi
mv ../eval/$a ../eval/A && mv ../eval/$b ../eval/B
echo "A=$a B=$b" > ../eval-mapping.txt                       # keep it outside ../eval
git worktree prune
```
The judge's working directory is `../eval`: it sees only A, B and, later, `MEASURES.md`.

## 2. Measure both, the same way, on the same machine
Measurement comes first. The judge explains the numbers; it does not stand in for them. Run each measure on A
and on B. Run performance measures at least 5 times, report the median and spread, and use the same machine,
power state and priority for both copies.

| Dimension | Typical measure (pick what applies) |
|---|---|
| Correctness | the test suite: pass, fail and skip counts; and the old suite run against the new code, which catches behaviour the loop changed together with its own tests |
| Performance | the project's benchmark or a latency harness (p50/p99), throughput, startup time, peak RSS, CPU time per request or run |
| Resources | binary or package size, dependency count, memory at rest, background threads and processes, listeners |
| Size / simplicity | `loop gate --base eval/before --head eval/after`, run by you in the project (not by the judge), then relabel the columns A and B; cyclomatic complexity if you have a tool |
| Security | secret scan, `loop privacy`, the ecosystem's audit (`pip-audit`, `npm audit`, `cargo audit`) |
| Interface | public CLI/API/config surface: added, removed or changed (a diff of `--help`, exported symbols, schema) |
| Docs | the README quick start, run literally on a clean machine or container |

A change counts as a regression or a win only when it falls outside the measured noise. Record it all in
`eval/MEASURES.md`, labelled A and B only.

## 3. The blind judge
Use an independent agent: ideally a different model or vendor than the loop's builders, at a high reasoning
effort, in a fresh session. Give it read-only access to A, B and `MEASURES.md`, and the prompt from
`loop prompt eval-judge`. Do not tell it which copy is newer or that an automated loop ran, because knowing which
side is "improved" biases the review. It writes `eval/JUDGE.json`: every difference that matters, in both
directions, with evidence. For best results, run two judges from different vendors and keep what both confirm.

## 4. Score
Unblind A/B, then for each judged difference:
- **win** (after is better), **regression** (after is worse), **neutral**, or **unclear** (not confirmed by a
  measurement or a reproduction).
- **Weight** = severity (1-5) × the project's priority for that dimension. State the priorities up front, e.g.
  performance 3, correctness 3, security 2, simplicity 1.

**Net score = sum of weighted wins − sum of weighted regressions.** Any severity-4+ regression in correctness,
security or a top-priority dimension fails the run, whatever the net score; do not merge it. With per-cycle
snapshots, score each cycle against the one before it: when the net gain per cycle stops beating its cost, stop
the loop earlier next time (lower `max_cycles`).

## 5. Feed regressions back into the loop
Every confirmed regression is a defect of the loop as well as of the project. Find the step that let it through:

| Escaped at | Typical cause | Typical change to the loop |
|---|---|---|
| critique | no lens looked at that dimension | add or enable a lens; a project lens for domain-specific risks |
| verify | the verifier accepted a wrong claim, or a true one was ranked too low | tighten the verifier prompt; require a measurement for that category |
| plan / build | the fix traded one quality for another | add the measure to `checks` or to the plan's acceptance criteria |
| gate | the gate did not measure it | add a gated metric (e.g. a benchmark) or a `test_cmd` that covers it |
| audit | the auditor missed it | add the dimension to the auditor's fresh critics |

Write each one as a finding in the loop's own repository (a project lens or a findings file in its next cycle)
with the evidence from the evaluation. Rank and fix it like any other finding.

## Guarding against fooling yourself
- **Holdout:** keep at least one project you evaluate on but never tune the loop's prompts against.
- **Same conditions:** same machine, same load, same toolchain versions for A and B. Noise is not a result.
- **Both directions:** a judge asked only "what got worse?" always finds something. Ask for wins too, and
  require evidence for both.
- **Old tests on new code:** the loop may change a behaviour and its test together. The old suite catches that.
- **Cost:** record agent minutes and cost per run next to the net score. A small gain at a large cost is a
  finding too.
- **Privacy:** for a private project, keep the judge's inputs, outputs and any external model inside your own
  privacy rules (`loop privacy --text` before anything leaves the machine).
