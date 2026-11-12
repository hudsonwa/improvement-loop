# Role: integrator (the only one who merges)

One lane per issue, each in its own branch/worktree; lanes touching the same component run one after another.
For each ready lane: rebase on the latest main, run the full tests, check its acceptance criteria and its
simplicity check (it removed what it said; added nothing it must not), then merge. A lane past its time limit is
stopped and its issue re-queued. After each merged lane, run `loop decide ID fixed --by integrator --reason
"<commit>"` for the findings it fixes, and append one line to `.improvement-loop/cycle-N/BUILD.md`:
`<issue id> | <branch> | <merge commit> | tests pass | findings fixed`. Never merge with failing tests.
Before the gate: `loop regress` (fixed findings stay fixed), `loop gate --base <last release>`,
`loop privacy --history <last release>..HEAD`, plus an independent review of the full diff.
