# Security

## Running agents safely
`loop run` starts the agent CLI you configure, many times, unattended. The CLI enforces branch, tests, protected
paths, gates and rollback, but it cannot stop an agent with wide permissions from pushing, reading credentials
or calling other services. See [PROCESS.md, Safety model](docs/PROCESS.md#safety-model): give agents the fewest
tools that work, or run them in a container, VM or separate user without push credentials.

## Reporting a vulnerability
Please don't put details in a public issue. Open an issue titled "Security contact" with no details, and the
maintainer will arrange a private channel. Then include the version, the command, and what an attacker could do.
