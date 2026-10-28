# File/DB mutation findings

These are the verified findings where a file operation and its DB row can disagree. Read CLAUDE.md
§ Key invariants first (PM-013, PM-021, the containment guards), and `docs/dev/bulk-image-jobs.md` /
`docs/dev/image-files.md` / `docs/dev/file-browser.md`. All of these findings were reproduced with
`docs/dev/gpt-review/probes/backend/probe_file_mutations.py`. Conventions are in `docs/dev/gpt-review/README.md`.
