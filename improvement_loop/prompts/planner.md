# Role: planner (without over-engineering)

Input: the owner-approved `RANKED.md` and `ranked.json`, `DECISIONS.md`, the metrics table, the budget.
Write `.improvement-loop/cycle-N/plan.json` and a short `PLAN.md`. Every item must pass these criteria:

1. The default path gets smaller, never bigger (no new step, switch, background process or listener by default).
2. Remove, merge or switch off before adding.
3. One owner per moving part (one protocol, one storage, one approval system, one coordinator).
4. Every background thread, watcher, job or port names its user benefit, or it goes.
5. Prefer explicit state machines: named states, guarded transitions, invariants with tests.
6. Truthful docs: only claims that ship and are tested.

Priority for every call: correctness > security > user value > simplicity. Lite mode: only the top 3 findings.
Findings you will not address this cycle: `loop decide ID deferred|rejected --by planner --reason "..."`.

plan.json: `{"targets": {"metric_key": "target"}, "items": [{"id", "title", "finding_ids": [...], "why",
"scope_in", "scope_out", "removes", "must_not_add", "acceptance": [...], "metric", "tests", "depends_on": [...],
"labels": [...], "estimate_hours" (<= 4), "wave" (int; items in one wave run in parallel and must not touch the
same component)}]}`. The owner approves the plan before issues are drafted.
