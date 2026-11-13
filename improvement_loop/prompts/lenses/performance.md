---
detect: **/*.py **/*.js **/*.mjs **/*.ts **/*.tsx **/*.jsx **/*.go **/*.rs **/*.java **/*.kt **/*.swift **/*.rb **/*.php **/*.c **/*.cc **/*.cpp **/*.cs **/*.scala **/*.lua
every: 2
---
# Lens: performance

Find work the project does that it does not need to: repeated I/O or subprocess calls in loops, quadratic scans
over data that grows with users or history, whole-file reads where a stream would do, missing caching of pure
results, blocking calls on a hot or interactive path, startup cost paid on every invocation. Only raise what
matters at the project's real scale: say what input size or call rate makes it hurt. **Measure it**: run the
project's benchmark, or write a small timing harness outside the repo, and put the exact command and the numbers
(median of several runs) in `notes` as `measured: <command> -> <result>`. A performance finding without a
measurement is treated as unproven. Category is
usually user_value (slow) or correctness (timeouts, resource exhaustion). Prefer fixes that remove work.

{{contract}}
