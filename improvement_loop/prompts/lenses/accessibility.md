---
detect: **/*.html **/*.htm **/*.jsx **/*.tsx **/*.vue **/*.svelte **/*.swift **/*.kt **/*.css
every: 2
---
# Lens: accessibility and interface polish

For any user interface (web, mobile, desktop, also CLI output): controls without accessible names or labels;
keyboard and focus traps; colour contrast and meaning carried by colour alone; text that does not scale; missing
alt text; touch targets too small; motion without a reduced-motion path; layouts that break at phone width or in
dark mode; CLI output that is unreadable when piped or without colour. Check against WCAG 2.2 AA where it applies
and name the criterion in `notes`. Evidence is the markup or style line. Category is usually user_value.

{{contract}}
