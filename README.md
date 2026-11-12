# improvement-loop

A repeatable cycle for making a software project **better without making it bigger**: independent critiques →
evidence-verified, ranked findings → a lean plan → parallel build → privacy and complexity gates → audit → repeat.

Deterministic code does everything that has a right answer (state machine, schema, evidence checks, ranking,
dedupe, decision log, metrics gate, privacy scan, issue drafting, stop rule, regression check). LLM agents only
judge and write, through role prompts that must emit schema-valid JSON. Works with any agent (Claude Code,
Hermes, others). The owner, a human, approves the ranking, the plan, every release and the stop decision; the
CLI refuses to move on without those approvals.

## Install

```bash
pip install git+https://github.com/hudsonwa/improvement-loop
```

Python 3.9+, standard library only. Agent skills: copy `skills/claude-code/SKILL.md` to
`~/.claude/skills/improvement-loop/SKILL.md`, or `skills/hermes/SKILL.md` to `~/.hermes/skills/improvement-loop/SKILL.md`.

## Use it in 3 steps

1. `loop init` (or `loop init --mode lite`) in your project.
2. Have your agent run each lens (`loop lenses`, then `loop prompt <lens>`) and `loop check` into `.improvement-loop/cycle-1/findings/`, then
   `loop verify`, the verifier's `loop set-verdict ...`, `loop advance`, `loop rank`.
3. Read `RANKED.md`, approve with `loop approve ranking --by YOU`, and keep going: `loop status` always says what
   is missing before the next `loop advance`.

Before any push: `loop privacy --denylist ~/private-terms.txt --history origin/main..HEAD` (keep the denylist
outside the repo). The full process is in [docs/PROCESS.md](docs/PROCESS.md). MIT licence.
