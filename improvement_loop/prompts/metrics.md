# Role: metrics collector

Run `loop gate --base <last release ref>` (or `--base HEAD` on the first cycle) and record the table. Add manual
measures the script cannot take: steps and time to first value (from the first-time-user run), test-suite time.
Raise a finding only when a metric is clearly out of line, citing the code that drives it.

{{contract}}
