# Task

You are producing an EU Deforestation Regulation (EUDR) risk analysis for a
portfolio of Brazilian rural properties, working entirely against cloud-native
data catalogs on Source Cooperative.

## Your data — three catalogs

Start from each catalog's own metadata (STAC `catalog.json`, README, `llms.txt`
where present). Do not assume a schema; discover it.

Each catalog below names the file to query. Read the metadata for structure,
scope, and provenance; read the named file for rows.

- **Field boundaries** — https://data.source.coop/wri-data-lab/trazofields/
  Agricultural fields delineated from satellite imagery, carrying Hansen
  forest-loss attributes and a MapBiomas land-cover class per field.
  Goiás: `trazo3-fields/trazo3_brazil_goias_2024.parquet`
- **Cadastral parcels** — https://data.source.coop/tristangruppwri/cadastral/
  Brazilian CAR rural-property boundaries, keyed by `cod_imovel`.
  File: `Brazil_CAR_AREA_IMOVEL.parquet`
- **Commodity infrastructure** — https://data.source.coop/tristangruppwri/soft-commodity-infrastructure/
  Silos, slaughterhouses, mills, and cooperative membership — the midstream a
  property sells into.
  File: `facilities/BR_facilities.parquet`

Each path is relative to its catalog root. The cadastral catalog describes its
Paraguay and Uruguay collections; the Brazil CAR file above sits beside them at
the catalog root and is the one this analysis uses.

The data are GeoParquet you can query remotely with DuckDB (`duckdb` is
installed, with the `spatial` and `httpfs` extensions). Some geometry is stored
as WKB — read the catalog metadata to find out which.

## Your input

`lists/goias-sample.csv` — the portfolio of cadastral properties under analysis.
Its encoding for this run is stated to you separately; handle it per
`policies/INPUTS.md`.

## Your policies — binding

`policies/` holds the rules this analysis runs under. They are not background
reading; they are the specification you implement:

- `MATCHING.md` — how an agricultural field is matched to a cadastral parcel.
- `COOPS.md` — how a parcel is matched to a cooperative or buyer.
- `EUDR_CROPS.md` — which land-cover classes are in scope, which Annex I
  commodity each is, and how each routes to infrastructure.
- `INPUTS.md` — how to handle the input list and its defects.

Where a policy states a threshold, use that threshold exactly. Where it requires
a flag or a caveat, carry it through into your output. Deciding which crops fall
under the regulation, and which land-cover classes represent them, is part of
the task and is governed by `EUDR_CROPS.md` — not by anything in this prompt.

## Your output

Answer every question in `questions.yaml`. For each, write one CSV to
`answers/q{id}.csv` matching that question's output contract: the specified
columns, meanings, and types. Column *names* are yours; column *meanings* and
*types* are not.

The questions are staged and build on each other — a later question relies on a
result you established earlier. Do that work once and reuse it; an error early
will propagate, exactly as it would in a real workflow.

Finally, write `answers/workflow.csv`: one row for every property you have
determined to be non-compliant (post-2020 loss on an EUDR-relevant crop), with
columns `cod_imovel`, `annex1_commodity`, `post2020_loss_ha`,
`top_contact_entity_id`, `entity_kind`, `tier`, `basis`, `distance_km`.

## Rules

- Query the remote data directly. Do not download whole files when a targeted
  query will do.
- If a query errors, read the error and fix your approach.
- Answer every question, even if uncertain. A best-effort answer beats a missing
  file.
- Do not fabricate numbers. Every value must come from a query you actually ran.
- Where the data cannot support an answer, say so in the answer rather than
  substituting something that looks similar. Reporting a gap is a correct answer
  when a gap is what exists — for instance, an in-scope commodity for which the
  infrastructure catalog carries no facilities.

When all files are written, print a one-line summary per question: the id and
the first row of your answer.
