# Contributing

## Setup and tests
```bash
git clone https://github.com/hudsonwa/improvement-loop && cd improvement-loop
python -m unittest            # about 40 s; no dependencies
```
CI runs the suite on Linux, macOS and Windows with Python 3.9 and 3.13. Every change keeps it green.

## What makes a good contribution
- **A fix to the loop from a real run.** Describe what went wrong (a false rollback, a regression the gate missed,
  a slow step), add a regression test that fails before the fix, then fix it. You don't need to share the
  project: an issue with the finding alone (where it escaped, what happened, what should have) is valuable.
- **A new lens.** Add `improvement_loop/prompts/lenses/<name>.md` with a `detect` header, end it with
  `{{contract}}`, and add it to the lens table in docs/PROCESS.md. Lenses must ask for quoted evidence.
- **Docs.** If something in the README did not work for you as written, that is a bug.

## Rules
- Standard library only. The CLI never calls an AI model itself.
- The skills in `skills/claude-code` and `skills/hermes` share one body (a test checks it); edit both.
- Interface changes (commands, flags, config keys) get a line in CHANGELOG.md.
- Don't commit private data: run `loop privacy --history origin/main..HEAD` before opening a pull request.
- Evaluating a change to the loop: [docs/EVALUATION.md](docs/EVALUATION.md). To run the loop on itself, see
  PROCESS.md, "Applying the loop to itself".
