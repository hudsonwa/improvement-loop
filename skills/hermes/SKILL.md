---
name: improvement-loop
description: "Gated, repeatable improvement cycle for a software project: critiques, verified ranked findings, owner approval, lean plan, issues, build, privacy and complexity gates, audit."
version: 0.1.0
author: improvement-loop contributors
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [code-review, refactoring, quality, process, planning]
    category: software-development
---

# improvement-loop

The `loop` CLI (`pip install git+https://github.com/hudsonwa/improvement-loop`) holds the state machine and does
every deterministic check. You do the judgement through role prompts. Never edit `.improvement-loop/state.json`
by hand and **never run `loop approve`: approvals belong to the owner**. When a step needs approval, stop and ask.

## Each turn
1. `loop status` (run `loop init [--mode lite]` first if needed). Do exactly what `unmet` lists.
2. **critique**: for each critic in `critics`, run an independent pass (a delegated subagent if available, else a fresh context; blind to the others) with the text of
   `loop prompt <role>`; each writes `.improvement-loop/cycle-N/findings/<role>.json`. Use a different model for
   `external-reviewer` when you can, and scrub what you send it with `loop privacy --text FILE`. Then
   `loop validate` each file and `loop advance`.
3. **verify**: `loop verify`, then a verifier subagent that wrote none of the findings follows
   `loop prompt verifier` and calls `loop set-verdict`. `loop advance`.
4. **rank**: `loop rank`. Show the owner the top of `RANKED.md` and STOP: ask them to approve
   (`loop approve ranking --by NAME`) or to reject/defer items (`loop decide ID rejected --by NAME --reason ...`).
5. **plan**: follow `loop prompt planner`; write `plan.json` + `PLAN.md`; STOP for owner approval of `plan`.
6. **issues**: `loop issues draft`; `loop issues file --repo OWNER/NAME` is a dry run - add `--yes` only if the owner
   asked to file.
7. **build**: one delegated task per independent issue in its own git worktree; you are the integrator
   (`loop prompt integrator`).
8. **gate**: `loop regress`, `loop gate --base <last release>`, `loop privacy --history <range>`, an independent review
   of the diff; STOP for owner approval of `release`. **audit**: `loop prompt auditor`, `loop stop-check`, STOP for
   `next` or `stop`.

Priority for every call: correctness > security > user value > simplicity. Run long jobs in the background.
