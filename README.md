# improvement-loop

**A Python CLI that drives your own AI coding agent (Claude Code, Codex, Hermes, ...) through a repeatable,
gated review-and-fix cycle on a git repository.** It never calls an AI model itself: it runs the agent CLI you
already use, checks everything that has a right answer, and refuses to move on when a check fails.

What you get: commits on a separate `loop/` branch, each fixing a verified finding, plus readable reports
(`RANKED.md`, `PLAN.md`, `BUILD.md`, `AUDIT.md`). Your own branch is never touched and nothing is ever pushed.

```
critique ─► verify ─► rank ─► plan ─► issues ─► build ─► gate ─► audit ─► next cycle or stop
 blind        quotes    ▲        ▲                  tests    ▲        ▲
 agents       checked   approval approval                    approval approval
```

Every cycle aims to make the project **better without making it bigger**: product code, background processes
and the public interface may only grow with a written reason.

## Requirements
- A **git repository with at least one commit**.
- **Python 3.9+** (standard library only).
- For unattended runs: an **agent CLI** that takes a prompt on stdin, e.g. `claude -p`.
- Optional: `gh` (to file issues), a different model or vendor for one critic.

## Install
```bash
pipx install git+https://github.com/hudsonwa/improvement-loop@v0.2.1
```
(`pip install` works too inside a virtualenv.) This gives you `loop` (alias `improvement-loop`, or
`python -m improvement_loop`).

## Quickstart: an unattended run
Allow a few hours. A full cycle runs about 15 agent sessions; see [Cost and time](#cost-and-time).

```bash
cd ~/src/myproj
git worktree add ../myproj-loop -b loop/auto     # the loop only builds on a loop/ branch
cd ../myproj-loop
git config user.email "<id>+<you>@users.noreply.github.com"   # see note 3
loop init --mode lite                             # lite: 2 critics, top 3 fixes; drop it for the full run
```

Edit `.improvement-loop/config.json`:

```json
{
  "agent": {"cmd": ["claude", "-p", "--allowedTools", "Read,Edit,Write,Glob,Grep,Bash(git:*),Bash(python:*)"],
            "timeout": 3600},
  "autonomy": "build",
  "test_cmd": ["python", "-m", "pytest", "-q"],
  "protected": ["tests/**", ".github/**", "pyproject.toml"],
  "budget": {"minutes": 240, "cost": 0}
}
```

```bash
loop run --detach            # background, low priority; progress in .improvement-loop/RUN.md
loop status                  # where it is and what it waits for
```

1. **`test_cmd` and `protected` matter.** `loop run` refuses to start without `test_cmd`; without `protected`
   an agent may edit the tests that judge it.
2. **`autonomy: build`** approves the ranking and the plan for you and parks before keeping the result.
   `full` also approves that, and the next cycle. `off` (default) parks at every approval. Details:
   [PROCESS.md, Autonomy](docs/PROCESS.md#autonomy).
3. **The privacy gate flags real email addresses** in commit metadata: commit in the loop worktree with a
   noreply address, or list the identity you publish on purpose in `allow_emails`.
4. The agent line lists only the tools builders need (edit files, git, python); add your test runner.
   Read the [Safety model](docs/PROCESS.md#safety-model) before giving an agent wide permissions.

## After a run
```bash
cd ../myproj-loop
cat .improvement-loop/RUN.md                  # what happened, and why it parked or stopped
git log --oneline main..loop/auto             # one commit per fixed finding
git diff main...loop/auto                     # review it like any pull request
cd ../myproj && git merge --ff-only loop/auto # or cherry-pick what you want
```
Clean up when done:
```bash
git worktree remove ../myproj-loop
git branch -D loop/auto                       # plus any loop/item-* branches
git tag -l 'loop-backup-*'                    # backup tags from rolled-back cycles; delete when not needed
```
Also delete `../.improvement-loop.noindex/` (temporary worktrees beside your project). `.improvement-loop/`
holds the reports and decision log; keep it if you will run another cycle.

## Cost and time
- **Sessions per cycle:** lite about 6 (2 critics, verifier, planner, up to 3 builders, auditor); full about 15
  (5 to 10 critics, verifier, planner, up to 5 builders, auditor).
- **Wall time:** 1 to 4 hours per cycle on a mid-size project, longer at background priority while the machine
  is busy. Critics run in parallel (`max_parallel`, 8); each is capped at `critic_timeout` (900 s).
- **Budget:** `budget.minutes` counts agent minutes (summed across parallel agents) and stops the run when spent.
  `loop run` cannot see money: record cost yourself with `loop spend --cost` if you want `budget.cost` to apply.
- **Stop:** after `max_cycles` (3), when no serious finding is left, or when a cycle's fixes add up to little
  (`min_gain`). A second cycle usually adds much less than the first.

## Use it from your agent (skill)
```bash
git clone https://github.com/hudsonwa/improvement-loop ~/improvement-loop
pipx install -e ~/improvement-loop                      # one copy for both the CLI and the skill
mkdir -p ~/.claude/skills && ln -s ~/improvement-loop/skills/claude-code ~/.claude/skills/improvement-loop
```
Hermes: link `skills/hermes` into `~/.hermes/skills/`. Windows: `mklink /D` or copy the folder. Agents without
skills (Codex, Cursor): add to the project's `AGENTS.md`: *When asked to "run the improvement loop", follow
~/improvement-loop/skills/claude-code/SKILL.md.*

Then say **"run the improvement loop"**. The agent sets up the worktree and config, asks you which autonomy level
and permissions to use, runs it and reports. **"...and improve recursively"** adds two steps: a blind before/after
evaluation ([EVALUATION.md](docs/EVALUATION.md)), then fixes to the loop itself, in its clone at
`~/improvement-loop`, from what went wrong. It asks before pushing anything.

## Manual mode (no agent CLI)
You, or an agent in chat, do each step; `loop status` always prints what is missing. One cycle:

```bash
loop init
loop check                                   # project checks, if configured
loop lenses                                  # the critics for this cycle
loop prompt first-time-user > /tmp/p.md      # give each lens prompt to a fresh, separate agent session;
                                             # it writes .improvement-loop/cycle-1/findings/<lens>.json
loop validate .improvement-loop/cycle-1/findings/first-time-user.json
loop advance                                 # critique -> verify
loop verify                                  # checks every quote against the code
loop set-verdict FTU-1 true --by verifier --note "reproduced"    # a different session from the critic
loop advance && loop rank                    # verify -> rank; writes RANKED.md
loop approve ranking --by YOU && loop advance
# plan: give `loop prompt planner` to an agent -> plan.json + PLAN.md
loop approve plan --by YOU && loop advance
loop issues draft && loop advance
# build: fix each plan item on a loop/ branch; write BUILD.md (format in PROCESS.md, Files you write)
loop advance
loop gate --base <commit the cycle started from> && loop privacy --base <same> --history <same>..HEAD
loop approve release --by YOU && loop advance
# audit: give `loop prompt auditor` to an agent -> AUDIT.md
loop stop-check; loop approve stop --by YOU   # or: loop approve next --by YOU && loop advance
```

## FAQ
- **Does it call an AI API?** No. It runs the agent CLI you configure, or you run the agents yourself.
- **Can it push or publish?** The CLI never does. Agents are *told* not to push; see the safety model.
- **How do I stop it?** `touch .improvement-loop/STOP`. Delete the file before resuming with `loop run`.
- **Should I commit `.improvement-loop/`?** `loop run` keeps it untracked (`.git/info/exclude`). Commit it only
  if you want the decision log in history; the privacy scan covers it.
- **How is this different from "review my repo"?** Findings must quote the code and are machine-checked, a
  separate verifier judges them, rejected ideas are never re-raised, and a gate blocks growth and regressions.
- **Is it safe for private code?** It sends code only to the agents you configure. Keep private names in a
  denylist outside the repo and run `loop privacy` before any push.
- **Which models?** Your best model for the planner and verifier; a different vendor for `external-reviewer` if
  the code may leave your machine.

## Docs
- [docs/PROCESS.md](docs/PROCESS.md): the steps, configuration reference, gates, files, troubleshooting, glossary.
- [examples/](examples/): a realistic config and one cycle's files.
- [docs/EVALUATION.md](docs/EVALUATION.md): measuring whether a run really improved a project.
- [CONTRIBUTING.md](CONTRIBUTING.md), [CHANGELOG.md](CHANGELOG.md), [SECURITY.md](SECURITY.md).

MIT licence.
