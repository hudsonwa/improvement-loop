# Changelog

## Unreleased
- Docs rewritten for new users: what it is, requirements, an unattended quickstart, after a run, cost and time,
  safety model, configuration reference, troubleshooting, glossary, CONTRIBUTING and SECURITY.
- `loop verify` with no findings writes an empty `verified.json` instead of failing, so a clean project can stop.
- `loop gate --justify` accepts `FINDING-ID: reason` to keep a fix the regression check flags.
- The skill asks before choosing autonomy and agent permissions, creates the `loop/` branch, and requires
  `test_cmd` and `protected`.
- CLI messages link to the online docs.

## 0.2.0 (2026-11-15)
- **Lenses:** pluggable critique dimensions (performance, reliability, test quality, docs drift, dependencies,
  accessibility) chosen per project; project lenses; `loop lenses --scores`.
- **`loop check`:** project commands whose failures become findings.
- **`loop run`:** an unattended driver with `autonomy` levels, background priority on every OS, `--detach` and
  `--service`; builds only on a `loop/` branch; rolls back failed cycles.
- **Parallel** critique and per-wave builds in separate worktrees.
- **Measured gates:** `bench` (latency) and `surface` (public interface) metrics; performance findings need a
  measurement.
- **Privacy:** `--base` fails only on new hits; history scans only added lines.
- **Stop rule:** also stops when a cycle's fixes add up to less than `min_gain`.
- **docs/EVALUATION.md:** the before/after protocol with a blind judge.

## 0.1.0
First release: the state machine, findings schema, evidence checks, ranking, decision log, metrics gate, privacy
scan, issue drafting and stop rule.
