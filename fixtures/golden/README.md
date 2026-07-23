# Golden fixtures

One verified result CSV per question (`q01.csv` … `q30.csv`), plus a
`SHA256SUMS` manifest. Authored by running hand-written queries against
trazo3-fields and soft-commodity-infrastructure, and reviewing the
results against known facts about the data, jointly with domain review.
Commit each gold query alongside its CSV so results can be regenerated.
Write leading-zero labels such as the era bins (`'0104'`) as quoted
strings; a bare `0104` becomes the integer `104` on CSV round-trip.

Regenerate the manifest after any change:

```bash
cd fixtures/golden
sha256sum q*.csv > SHA256SUMS
```

These files are never mounted into session containers.
