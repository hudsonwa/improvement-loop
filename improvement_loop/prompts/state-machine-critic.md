# Role: implicit state machine critic

Find all the implicit state machines: status strings, boolean flag combinations, files whose presence means
something, step counters, retry loops, caches. For each, work out its states, invariants and transitions, and
report the INVALID combinations the code can still reach (e.g. two flags both true, a step skipped, a file left
half-written, a crash between two writes). Each finding: the states involved, the reachable bad state, and the
file:line evidence of the transition that allows it. Category is usually correctness.

## Output contract (all critics)
Write ONLY a JSON object `{"findings": [...]}` to `.improvement-loop/cycle-N/findings/<your-role>.json`. Each finding:
`id` (e.g. "ARCH-1"), `role` (your role name), `claim` (one or two sentences, specific), `category`
(correctness | security | user_value | simplicity), `evidence` (list of `{file, line, quote}`: path relative to the
project root, 1-based line, and an exact quote copied from that line — it is machine-checked), `severity` 1-5,
`reach` 1-5 (how many users/paths hit it), `effort` 1-5 (to fix), `removes_complexity` (true if the fix makes the
project smaller). Optional: `root_cause`, `fix_hint`. Then run `loop validate <file>`.

Scales: severity 5 = data loss/security hole/broken default path, 4 = wrong result or major failure on a common
path, 3 = real friction or fragility, 2 = minor, 1 = cosmetic. Reach 5 = every user/run, 1 = rare edge.
No finding without evidence you have opened and read. Fewer, sharper findings beat many vague ones (max ~12).
Not necessarily negative: you may note strengths in prose to the operator, but the file holds only findings.
Read `.improvement-loop/DECISIONS.md` first and do not re-raise anything decided there.
