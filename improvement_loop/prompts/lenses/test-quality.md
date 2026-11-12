---
detect: tests/** test/** **/test_*.py **/*_test.py **/*_test.go **/*.test.* **/*.spec.* **/__tests__/**
every: 1
---
# Lens: test quality

Judge whether the tests would catch the bugs that matter, not how many there are. Find: important paths and
error branches with no test; tests that assert nothing meaningful or only re-state the implementation; tests
that depend on time, order, network or machine state (flaky); slow tests that discourage running the suite;
fixtures that hide what is being tested. Run the suite if you can (with a timeout) and report its time and
failures. Evidence for a missing test is the untested code line. Prefer fewer, sharper tests over more tests.
Category is usually correctness.

{{contract}}
