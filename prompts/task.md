# Task

You are working with two cloud-native geospatial data catalogs:

- https://data.source.coop/wri-data-lab/trazofields/
- https://data.source.coop/tristangruppwri/soft-commodity-infrastructure/

Start from each catalog's own metadata (README, STAC records, AGENTS.md if
present) and find the data files yourself. The data are GeoParquet files
you can query remotely with DuckDB (`duckdb` is installed, with the
`spatial` and `httpfs` extensions).

Answer every question in `questions.yaml`. For each question, write one CSV
file to `answers/q{id}.csv` matching that question's output contract: the
specified number of rows and columns with the specified meanings. Column
names are up to you; column meanings and types are not.

Rules:

- Query the remote data directly. Do not download entire files when a
  targeted query will do.
- If a query errors, read the error and fix your approach.
- Answer every question, even if uncertain. A best-effort answer beats a
  missing file.
- Do not fabricate numbers. Every value in an answer file must come from a
  query you actually ran.

When all answer files are written, print a one-line summary per question:
the question id and the first row of your answer.
