# Methods: an EUDR workflow benchmark for language models

## The question

The EU Deforestation Regulation obliges an operator to show that a commodity
placed on the EU market comes from land not deforested after 31 December 2020.
Doing that for a Brazilian soy or cattle portfolio requires a few geospatial analyses. Someone
resolves property IDs against the national cadastre, matches agricultural fields
to those properties, decides which land-cover classes count as Annex I
commodities, measures forest loss on the ones that do, and routes each flagged
property to the cooperative or buyer that can act; become compliant or improve land management practices.
This last step isn't EUDR focused, but it is key for improving behaviors beyond the market signal of the purchaser.

This experiment asks whether a language model can do that job end to end against
cloud-native data, and what the attempt costs in dollars. The agent gets three
remote catalogs, a portfolio of properties, four binding policy documents, and 30
questions. It queries the GeoParquet itself over HTTP range requests, recovers
from its own SQL errors, and writes one CSV per question plus a final decision
artifact. A SQL oracle produces the answer key. An executable comparator does the
grading.

Two repositories: [`wri/rural-land`](https://github.com/wri/rural-land)
holds the production EUDR reporting pipeline and its SQL. `geodata-llm-eval`
holds the benchmark, and vendors that SQL at a pinned commit so the answer key
comes from the same queries that generate reports.

## The data

We have hosted three catalogs on [Source Cooperative](https://source.coop), necessary for this multi-step analysis.

| Layer | Source | Pin |
|---|---|---|
| Field boundaries | `wri-data-lab/trazofields`, Trazo3 Goiás 2024 | collection `trazo3-fields` |
| Cadastral parcels | `tristangruppwri/cadastral`, Brazil CAR, 8.45M rows | snapshot 2026-07-16 |
| Commodity infrastructure | `tristangruppwri/soft-commodity-infrastructure`, `BR_facilities` | release 2026-07-23 |

Trazo3 fields contain a MapBiomas 2024 mode class and five Hansen-dervied columns of
cleared area. Post-2020 loss comes from Hansen. One definition of loss holds across
every query.

The pins live in `fixtures/pins.json` along with the `rural-land` commit
(`42f6837`) and the DuckDB version used to render the answer key (1.5.5).

## What the agent receives

`harness/run.py` mounts four things into a fresh container workspace.

1. `prompts/task.md`, the framing and the output contract.
2. `fixtures/questions.yaml`, the 30 questions with their stages, dependencies,
   and column meanings.
3. `policies/`, the four policy documents.
4. The input portfolio under `lists/`.

Golden fixtures are kept separate.

## The workflow

The 30 questions form six stages that build on each other, tracking how a
supply-chain analyst might work within a portfolio.

**Stage 1, catalog discovery (q01-q04).** Navigate three catalogs from metadata
alone. Count STAC collections, read the recommended collection out of the
catalog's own description, report row counts and the geometry EPSG.

**Stage 2, cadaster resolution (q05-q07).** Resolve the listed `cod_imovel`
values against the 8.45M-row CAR file. Report what resolves, what goes missing,
what appears twice, and how many parcels fall under one hectare.

**Stage 3, field to cadaster matching (q08-q14).** Implement `MATCHING.md`. A
field enters the analysis when two-thirds of its area falls inside a single
listed parcel, or when two-thirds falls inside the buffered union of all listed
parcels. The 25 m buffer closes sliver gaps between neighbouring CAR parcels so a
field straddling two properties is still counted. The stage reports how many fields
each rule admitted, the containment-fraction bounds, and the fields that
intersect a parcel and fail both tests.

**Stage 4, EUDR deforestation (q15-q23).** Classify every MapBiomas class present
under `EUDR_CROPS.md`, then measure post-2020 loss on the in-scope ones. The
scope table distinguishes a class the regulation omits (sugarcane) from a class
excluded because the sensor cannot see it reliably (planted timber, a genuine
Annex I commodity). The out-of-scope breakdown is reported separately so the
exclusion stays auditable.

**Stage 5, commodity infrastructure (q24-q29).** Route each flagged property
under `COOPS.md`. Every cadaster gets a ranked candidate set drawn from five
tiers: municipality-level cooperative membership, grain intake points, slaughter
points, mill points, and modeled gravity catchments. The dominant crop decides
which delivery tiers survive. Distances run in EPSG:5880, from cadastral centroid
to facility point. When fewer than two delivery candidates sit inside 100 km, the
radius widens toward a 300 km ceiling and the result carries a `widened` flag.

**Stage 6, portfolio decision (q30).** For every property non-compliant on an
in-scope crop, its top-ranked contact with the candidate's tier, basis, and
distance.

Each question declares its dependencies, so the grader can separate a question
wrong on its own merits from one wrong because an upstream answer was wrong.

## Policy as the specification

The four documents in `policies/` are the binding spec, mounted into the session.
They state the thresholds. The 2/3 containment bar, the 25 m buffer, the EPSG:5880
distance rule, and the Annex I commodity mapping are all written down. The task
is the spatial work of applying them across a live portfolio.

Crop scope is deliberately governed by `EUDR_CROPS.md` and kept out of the
prompt. The agent has to read the scope policy and carry its caveats through
nine downstream questions. That mirrors the real job, where a compliance rule
arrives as a document and the analyst implements it.

`INPUTS.md` governs the input list. It forbids silent drops, because an omitted
property is an unaudited sourcing area, the exact failure the workflow exists to
prevent. Its reconciliation identity has to hold:

```
resolved_clean + centroid_resolved + geometry_resolved
  + duplicates_removed + unresolvable = input_rows
```

## The oracle

Golden answers are generated. `oracle/render.py` runs the `rural-land` EUDR SQL,
vendored under `oracle/sql/` at the pinned commit, against the same pinned remote
catalogs. The pipeline runs in dependency order, each stage reading the parquet
the previous one wrote:

```
eudr_crops → cad_extract → fields_extract → match → coop_match
```

Thirty query templates then render against those extracts and emit
`fixtures/golden/qNN.csv`, with a `SHA256SUMS` manifest over the set. Every
expert answer is therefore the output of a committed, re-runnable query with a
stated derivation. Anyone can regenerate the key and diff the checksums.

## Grading

Grading is executable. `harness/grade.py` compares each
session's `answers/qNN.csv` to the golden file.

- Rows compare as multisets.
- Column names and column order are ignored. The comparator searches column
  permutations for one under which every row matches.
- Integers and strings match exactly. Floats match within relative 1e-3.
- Questions marked `grading: geometry` compute areas, distances, or geometric
  thresholds, where the choice of equal-area projection or distance method moves
  the number. Those grade at relative 1e-2, and their integer counts allow
  absolute slack of `max(2, 1% of golden)`, so a handful of boundary fields does
  not fail an otherwise-correct answer.
- A missing file and an unparseable file each record as their own outcome,
  distinct from a wrong answer. Broken sessions stay separable from wrong ones.

Because the stages depend on each other, the harness reports two numbers per
stage. Raw accuracy is correct over all questions in the stage. Conditional
accuracy is correct over the questions whose every transitive dependency graded
correct, meaning the questions the model had a fair shot at. The gap between the
two is the error-propagation signal. High raw with low conditional means the LLM fails that stage. The reverse means a stage inheriting upstream mistakes, but would have been otherwise correctly calculated.

The comparator and the stage summary are pinned by unit tests in `tests/`.

## Consistency

After the 30 questions, each session writes `answers/workflow.csv`, one row per
property it flagged non-compliant. `harness/consistency.py` measures agreement
across the ten runs of a model:

- Jaccard similarity on the flagged set, plus the named properties that move
  between runs.
- Kendall tau-b on the post-2020 loss ranking.
- Fleiss kappa on the choice of top contact.

The flagged set is agent-determined, since it inherits the stage-4 scope
classification. Set-level agreement therefore measures whether runs agree on who
falls in scope at all, well beyond agreeing on arithmetic.

Every metric is also computed against the oracle. Ten runs can agree perfectly
and all be wrong, so the report shows `consistency@10` beside `accuracy@10`.

## Independence and audit

One variable moves across the experiment: the model. Everything else holds fixed.

- Models: Claude Haiku 4.5, Sonnet 5, Opus 4.8.
- Passes: 10 independent sessions per model, 30 sessions total.
- Sampling: default temperature, so the ten passes form a real distribution.

Each session is one `docker run` of a pinned image against a fresh temporary
workspace. The image carries Claude Code CLI 2.1.218 and DuckDB 1.3.2 with the
`spatial` and `httpfs` extensions preinstalled, and runs as a non-root user with
an empty HOME, so no host `CLAUDE.md`, hook, MCP server, or memory file reaches a
run. There is no orchestration above the session, no shared state, and no retry
harness.

Every session commits its full `transcript.jsonl`, its `answers/`, and a
`meta.json` carrying token counts, turn count, exit code, input mode, and the
harness commit hash. Anyone can re-grade the committed transcripts without
spending a single model call.

## Cost accounting

Sessions run on a subscription plan and bill nothing directly. The harness logs
token counts from each transcript's final `result` record and imputes dollars at
list API prices, pricing input, output, and cache tokens separately.

| Model | Input $/MTok | Output $/MTok | Cache write | Cache read |
|---|---|---|---|---|
| Haiku 4.5 | 1.00 | 5.00 | 1.25x input | 0.1x input |
| Sonnet 5 | 3.00 | 15.00 | 1.25x input | 0.1x input |
| Opus 4.8 | 5.00 | 25.00 | 1.25x input | 0.1x input |

Claude Code always uses prompt caching and cannot disable it. Caching applies
identically to all three models, which keeps the comparison fair. Sonnet 5 has an
introductory price of $2/$10 through 2026-08-31, and the benchmark uses list
price so results stay comparable after the promotion ends.

The headline figure is a Pareto plot, accuracy on the y-axis against imputed
dollars on the x-axis.

## Experiment 2: input robustness

Experiment 1 measures whether a model can run the workflow on a clean 117-property
Goiás portfolio. Experiment 2 (`experiments/mt-adversarial/`) holds the workflow
fixed and feeds it a deliberately malformed Mato Grosso soy portfolio, measuring
whether the model applies `INPUTS.md` faithfully.

The portfolio draws real soy properties carrying post-2020 loss from five
frontier municipalities (Alta Floresta, Juara, Nova Canaã do Norte, Tabaporã,
Itaúba), then seeds four defect kinds:

| Defect | The row | Required handling |
|---|---|---|
| `duplicate` | same ID and geometry, twice | dedupe on (id, geometry), report the count |
| `centroid` | ID present, geometry is a point | resolve by ID, flag the point precision |
| `no_id` | polygon, no ID | resolve by containment against CAR |
| `centroid_no_id` | point, no ID | point-in-parcel. Zero or several matches means unresolvable, reported and never guessed |

The same portfolio ships in three encodings, selected with `run.py --input-mode`:
a CSV of IDs, a geometry parquet including the ID-less rows, and a split pair
joined on the ID. `build_inputs.py` generates all of them from the live catalogs
and writes `manifest.json` with each row's origin cadaster, defect kind, and
expected resolution. The manifest is the experiment's answer key and never enters
a session. Selection, region, and defect counts live in `config.json`, so a new
municipality or a new commodity is a config change.

Deterministic build: the same config and seed produce byte-identical inputs.

## Scope limits

Metadata dropout, latency and throughput measurement, and held-out catalogs stay
out of this design.

Coffee and oil palm have no delivery infrastructure in the current facilities
product. The benchmark grades reporting that gap as the correct answer, and
inventing a route as a wrong one. Closing the gap is separate work.

`EUDR_CROPS.md` excludes forest plantation on detection grounds despite its Annex
I status, and treats MapBiomas class 21 (mosaic of uses) as pasture. Both
assumptions are stated in the policy and carried into the answers as caveats, so
a reader can see where the analysis rests on a judgment call.

EUDR compliance is cumulative. A commodity qualifies when it is deforestation-free
*and* legal *and* covered by a due diligence statement. This workflow measures the
deforestation-free condition and supplies CAR-based legality indicators, which
makes it an evidence tool feeding a compliance decision made elsewhere.
