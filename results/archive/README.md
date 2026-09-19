# Archived runs

Two cohorts sit here, archived for different reasons. Neither one is
comparable to a live run in `results/<model>/`, and neither is comparable to
the other.

## Pre-ablation runs, 2026-07-26 to 2026-07-31

These predate the ablation harness. They carry no `spec_fingerprint`, so they
cannot say which spec they saw. Between them they span two task briefs and two
primary-cadaster tie-break rules. Grouped together they read as one baseline
and are not one.

## The July ablation sweep, 2026-07-31, ten runs

These ran against goldens `6cd923b47f19`. The oracle was regenerated after
them, and today's fixtures digest to `7bbdf1bbf735`. Every one of the ten drew
the same flag from `grade.py`:

```
re-graded: ran against 6cd923b47f19, scored against 7bbdf1bbf735
```

A score produced that way measures neither the old task nor the new one. The
runs themselves are intact. Nine wrote 30 of the 31 answers and skipped q31,
which the spec of the time never got answered. The tenth,
`20260731T160543Z-017dd85`, produced nothing and never scored.

They cover five ablation arms, two passes each: `d9448089a133`,
`3c7aae61bb2a`, `08c0a4cb8420`, `ee0bd55a8d52`, and `efea91b7149e`. The
arm-to-arm report they produced is frozen beside them in
`ablations-20260731.md`. Read those deltas as the record of that sweep. Do not
set any number from it against a run graded on today's goldens.

## How the exclusion works

An archived run sits two levels deeper than a live one, so `layout.run_dirs`
skips it. That function globs `*/*` for a directory holding a `meta.json`, and
nothing here matches at that depth. The runs stay out of `grade.py` and out of
the ablation report, and nothing is deleted.

One consequence follows. `grade.py` no longer re-grades an archived run, so
each `grades.json` is frozen against the fixtures current when the run was
archived. Read a score here as a historical record. To bring a run back, move
it up to `results/<model>/` and run `grade.py` again.
