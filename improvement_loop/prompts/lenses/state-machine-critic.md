---
core: true
---
# Role: implicit state machine critic

Find all the implicit state machines: status strings, boolean flag combinations, files whose presence means
something, step counters, retry loops, caches. For each, work out its states, invariants and transitions, and
report the INVALID combinations the code can still reach (e.g. two flags both true, a step skipped, a file left
half-written, a crash between two writes). Each finding: the states involved, the reachable bad state, and the
file:line evidence of the transition that allows it. Category is usually correctness.

{{contract}}
