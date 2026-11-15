## Output contract (all critics and lenses)
Write ONLY a JSON object `{"findings": [...]}` to `.improvement-loop/cycle-N/findings/<your-role>.json`. Each finding:
`id` (e.g. "ARCH-1"), `role` (your role name), `claim` (one or two sentences, specific), `category`
(correctness | security | user_value | simplicity), `evidence` (list of `{file, line, quote}`: path relative to the
project root, 1-based line, and an exact quote from that line or within 2 lines of it; it is machine-checked.
Quote the line the fix must change: a fixed finding whose quotes all remain counts as a regression), `severity` 1-5,
`reach` 1-5 (how many users/paths hit it), `effort` 1-5 (to fix), `removes_complexity` (true if the fix makes the
project smaller). Optional: `root_cause`, `fix_hint`, `notes` (some lenses require it). Nothing to report: write `{"findings": []}`.
Then run `loop validate <file>`.

Scales: severity 5 = data loss/security hole/broken default path, 4 = wrong result or major failure on a common
path, 3 = real friction or fragility, 2 = minor, 1 = cosmetic. Reach 5 = every user/run, 1 = rare edge.
No finding without evidence you have opened and read. Fewer, sharper findings beat many vague ones (max ~12).
Not necessarily negative: you may note strengths in prose to the operator, but the file holds only findings.
Read `.improvement-loop/DECISIONS.md` first and do not re-raise anything decided there.
