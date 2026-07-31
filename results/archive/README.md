# Archived runs

Runs from before the ablation harness. They carry no `spec_fingerprint`, so
they cannot say which spec they saw, and between them they span two task
briefs and two primary-cadaster tie-break rules. Grouped together they read as
one baseline and are not one.

They sit two levels deeper than a live run, so `layout.run_dirs` (which globs
`*/*` for a directory holding a `meta.json`) skips them. That keeps them out of
`grade.py` and out of the ablation report without deleting anything.

One consequence: `grade.py` no longer re-grades them, so their `grades.json`
is frozen against the fixtures current when they were archived
(`6cd923b47f19`). Read a score here as a historical record, not as a
comparison against today's goldens. To bring one back, move it up to
`results/<model>/` and re-run `grade.py`.
