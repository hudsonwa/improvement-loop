---
detect: **/*.py **/*.js **/*.mjs **/*.ts **/*.tsx **/*.jsx **/*.go **/*.rs **/*.java **/*.kt **/*.swift **/*.rb **/*.php **/*.c **/*.cc **/*.cpp **/*.cs **/*.scala **/*.lua
every: 2
---
# Lens: reliability and operations

Find how the project fails in real use: unhandled errors on I/O, network and subprocess calls; partial writes and
non-atomic updates; retries without limits or backoff; missing timeouts; crashes that lose state or leave locks;
errors swallowed silently or reported without enough context to act on; behaviour that differs between platforms
(paths, encodings, line endings, shells). If the project runs as a service or deploy, also: startup and shutdown
order, health checks, rollback, logs. Each finding names the trigger (what goes wrong in the world) and the
reachable bad outcome. Category is usually correctness.

{{contract}}
