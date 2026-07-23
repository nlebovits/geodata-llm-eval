# Golden fixtures

One verified result CSV per question (`q01.csv` … `q30.csv`), plus a
`SHA256SUMS` manifest. Authored by running hand-written queries against
trazo3-fields and soft-commodity-infrastructure, and reviewing the
results against known facts about the data, jointly with domain review.
Commit each gold query alongside its CSV so results can be regenerated.

Because the questions never prescribe a method, the gold queries are the
reference implementation. Use these methods and record any deviation in
the query file: EPSG:6933 for areas, spherical distance
(`ST_Distance_Sphere`) for distance thresholds, planar centroid of the
stored EPSG:4326 geometry for field reference points, and `firstyear`
for the first-detection year. Questions marked `grading: geometry` in
`questions.yaml` grade with loosened tolerance (floats at relative 1e-2,
counts within max(2, 1%)) to absorb legitimate method variation. While
authoring, check any question with a qualifying threshold (Q19's plant
floors, Q25's 1,000 ha and 500-member cuts): if a candidate row sits
within about 2% of the cut, method choice could flip the row set itself,
which tolerance cannot absorb — flag it and adjust the question threshold
before locking golden.

Regenerate the manifest after any change:

```bash
cd fixtures/golden
sha256sum q*.csv > SHA256SUMS
```

These files are never mounted into session containers.
