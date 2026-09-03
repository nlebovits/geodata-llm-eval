# Golden fixtures

One verified result CSV per question (`q01.csv` through `q31.csv`), plus a
`SHA256SUMS` manifest. Authored by running hand-written queries against
trazo3-fields and reviewing the results against known facts about the
data, jointly with domain review.

Regenerate the manifest after any change:

```bash
cd fixtures/golden
sha256sum q*.csv > SHA256SUMS
```

These files are never mounted into session containers.

## Which remote files these describe

A golden answer is only meaningful against the objects it was computed from.
`fixtures/pins.json` records those objects by URL and by identity: byte size,
entity tag, row count, row-group count, declared EPSG, GeoParquet version,
bbox covering, and column list. Check them before trusting a regeneration:

```bash
python harness/pin_check.py --deep
```

The set committed here was generated on 2026-09-01 against the CAR file the
publisher republished on 2026-08-02. That republication deleted the previous
object, reprojected the parcels from SIRGAS 2000 to WGS 84, and quarantined two
corrupt geometries into a sidecar. Both quarantined parcels sit in Sergipe and
Maranhão, outside the Goiás portfolio, so they change the national counts in
q03 and nothing downstream.

## Fingerprints and old runs

`harness/run.py` records a `golden_fingerprint` at session time, the first
twelve hex characters of the SHA-256 of `SHA256SUMS`. `harness/grade.py`
records `graded_against` the same way, and `regraded` in `harness/layout.py`
reports a run whose two values differ.

Regenerating the goldens moves that fingerprint. The runs under `results/`
carry four earlier fingerprints (`6cd923b47f19`, `64c8b3ffbf6e`,
`128499a9709d`, `f28d3aec77f2`) and were scored against the fixtures of their
own day. Leave them that way. Re-grading them against this set would attach
scores computed from one CAR file to sessions that read another, and the
`regraded` flag exists to make that visible rather than to license it.
