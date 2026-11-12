# Role: auditor (after all items are done)

Run fresh critics (design, first-time user, security) that did not take part in this cycle, the implicit state
machine lens again, and compare the metrics table with the plan's targets. Write
`.improvement-loop/cycle-N/AUDIT.md`: what improved, what did not, metric targets met or missed, and new
findings (as a findings JSON block in the same schema). Those findings are the next cycle's step 0 input: the
operator copies them to `cycle-(N+1)/findings/auditor.json` after advancing. End with the stop-rule result from
`loop stop-check`; the owner then approves `next` or `stop`.
