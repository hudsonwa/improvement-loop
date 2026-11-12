---
detect: README* readme* docs/** **/*.md **/*.rst
every: 2
---
# Lens: docs drift and interface stability

Find places where the docs and the behaviour disagree: commands, flags, options, defaults, file names, outputs or
examples that the code no longer supports or never did; documented guarantees the code does not enforce; public
interfaces (CLI, API, config keys, file formats) changed without a note, which would break existing users on
upgrade. Check examples by running them where you can. Evidence is the doc line AND the code line. Truthful,
shorter docs beat more docs. Category is usually user_value, or correctness for a false guarantee.

{{contract}}
