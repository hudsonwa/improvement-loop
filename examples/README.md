# Examples

- `config.json`: a realistic `.improvement-loop/config.json` for a small Python CLI: lite mode, `build`
  autonomy, required `test_cmd` and `protected`, a second vendor for the external reviewer, the default size
  metrics plus a `surface` and a `bench` metric. Other agent CLIs work the same way: any command that reads the
  prompt on stdin (or from `{prompt_file}`) and edits files in its working directory.
- `cycle-1/findings/first-time-user.json`: one finding with machine-checked evidence.
- `cycle-1/plan.json`: the plan item that fixes it, with a `justification` for growing a gated metric.
- `cycle-1/BUILD.md`: the build log row that counts towards `min_gain`.

Formats: [docs/PROCESS.md, Files you write](../docs/PROCESS.md#files-you-write).
