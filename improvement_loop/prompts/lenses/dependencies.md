---
detect: requirements*.txt pyproject.toml setup.py setup.cfg Pipfile poetry.lock package.json package-lock.json pnpm-lock.yaml yarn.lock Cargo.toml go.mod Gemfile pom.xml build.gradle* composer.json **/requirements*.txt **/package.json
every: 2
---
# Lens: dependencies and supply chain

Review what the project pulls in and how: unpinned or wide version ranges where reproducibility matters; heavy
dependencies used for one small function; abandoned or duplicated packages; known-vulnerable versions (run the
ecosystem's audit tool if available, e.g. `pip-audit`, `npm audit`, `cargo audit`, and cite its output in
`notes`); licences that conflict with the project's own; install steps that fetch code from unpinned URLs, run
remote scripts or need network at runtime; CI actions not pinned. Prefer removing a dependency over upgrading it.
Category is usually security, or simplicity for removable dependencies.

{{contract}}
