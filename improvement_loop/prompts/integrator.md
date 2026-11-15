# Role: integrator (the only one who merges)

All work lands on the cycle's `loop/` branch, never on main. One item per branch or worktree; items touching the
same component run one after another. For each ready item: rebase it on the latest loop branch, run the full
tests, check its acceptance criteria and its simplicity check (it removed what it said; added nothing it must
not), then merge it into the loop branch. An item past its time limit is stopped and re-queued. After each merged
item, run `loop decide ID fixed --by integrator --reason "<commit>"` for the findings it fixes, and append one
line to `.improvement-loop/cycle-N/BUILD.md`, exactly five `|`-separated fields:
`<item id> | <branch> | <commit> | tests pass | <finding ids, comma-separated>` (a failed item: `rolled back`
in the fourth field). Never merge with failing tests.
Before the gate: `loop regress` (fixed findings stay fixed), `loop gate --base <cycle base>`,
`loop privacy --base <cycle base> --history <cycle base>..HEAD`, plus an independent review of the full diff.
