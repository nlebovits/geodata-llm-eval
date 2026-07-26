# geodata-llm-eval

Can a language model implement a real EU Deforestation Regulation (EUDR)
sourcing workflow against cloud-native data catalogs? This repo measures that,
and what it costs.

An agent gets three [Source Cooperative](https://source.coop) catalogs — field
boundaries, cadastral parcels, and commodity infrastructure — a list of
Brazilian rural properties, a set of binding policy documents, and 30 questions
across six dependent stages. It queries the remote GeoParquet itself, recovers
from its own errors, and writes one answer file per question plus a final
decision artifact. We grade the answers against a golden fixture the SQL oracle
generates, and report accuracy against imputed API cost per model.

## The workflow

The 30 questions are not independent lookups; they are six stages that build on
each other, mirroring how a supply-chain analyst actually works a portfolio:

1. **Catalog discovery** — navigate three catalogs from metadata alone.
2. **Cadaster resolution** — resolve the listed property IDs against the 8.45M-row
   national CAR file; account for duplicates and smallholdings.
3. **Field ↔ cadaster matching** — implement the containment policy in
   `policies/MATCHING.md` (2/3 containment, single-parcel or buffered union).
4. **EUDR deforestation** — decide which Mapbiomas classes are EUDR Annex I
   commodities (per `policies/EUDR_CROPS.md`), then measure post-2020 loss on
   the in-scope ones.
5. **Commodity infrastructure** — route each flagged property to the right
   cooperative or buyer (`policies/COOPS.md`), and report the honest gaps where
   no infrastructure exists.
6. **Portfolio decision** — for every non-compliant property, its top-ranked
   contact.

An error in stage 3 propagates visibly into stages 4 and 5, and the grader
measures that (see Grading).

## Policy-fed, not method-blind

The policy documents in `policies/` are mounted into the session as the binding
specification. The eval measures whether a model can **implement a written
compliance policy** against live cloud-native data — the actual EUDR job — not
whether it can guess arbitrary thresholds. The 2/3 containment bar, the 25 m
buffer, the EPSG:5880 distance rule, and the Annex I commodity mapping are
stated; the spatial work of applying them is the task.

Which crops fall under the regulation is deliberately governed by
`EUDR_CROPS.md` and not leaked into the prompt: the agent must read and apply
the scope policy, including the distinction between a class excluded because the
regulation omits it (sugarcane) and one excluded because the sensor cannot see
it reliably (planted timber).

## Design

One variable: the model. Everything else is fixed.

- **Models:** Claude Haiku 4.5, Sonnet 5, Opus 4.8
- **Passes:** 10 independent sessions per model, 30 sessions total
- **Data:** stays remote on source.coop. Cloud-native range-request access is
  the point of the exercise, not an inconvenience to cache away.
- **Sampling:** default temperature, so the 10 passes form a real distribution.

## Experiments

Each experiment fixes the workflow and varies one thing. They are separable and
extendable — a new experiment is a new input plus its own oracle, not a change
to the harness.

- **Experiment 1 — Goiás compliance baseline.** A clean 117-property portfolio
  (`fixtures/lists/goias-sample.csv`) graded against 30 oracle-generated goldens
  (`fixtures/golden/`). Measures whether a model can run the EUDR workflow at
  all. This is the experiment the rest of this README describes.
- **Experiment 2 — Input robustness** (`experiments/mt-adversarial/`). The same
  workflow, fed a deliberately malformed Mato Grosso soy portfolio in three
  encodings (`--input-mode {csv,geometry,split}`), seeded with duplicates,
  centroid-for-polygon rows, and ID-less geometries. Measures whether a model
  applies `policies/INPUTS.md` — resolving and reconciling the mess rather than
  dropping it. Self-contained and config-driven; see its
  [README](experiments/mt-adversarial/README.md).

## The oracle

Golden answers are **generated, not hand-authored.** `oracle/render.py` runs the
[rural-land](https://github.com/wri/rural-land) EUDR SQL — vendored under
`oracle/sql/` at a pinned commit recorded in `fixtures/pins.json` — against the
same pinned remote catalogs, and emits `fixtures/golden/qNN.csv` with a
`SHA256SUMS` manifest. Every expert answer is therefore the output of a
committed, re-runnable query with a stated derivation, not a typed number.
Golden fixtures are never mounted into a session.

## Grading

Grading is executable, not model-judged. The comparator compares each session's
`answers/qNN.csv` to `fixtures/golden/qNN.csv`:

- Counts and strings must match exactly; numerics within relative 1e-3.
- Geometry-sensitive questions (areas, distances) grade at relative 1e-2 with
  integer slack `max(2, 1% of golden)`, because projection and distance-method
  choices move those answers.
- Rows compare as multisets; column order and names are ignored.
- A missing or unparseable answer fails, recorded separately from a wrong one.

Because the stages are dependent, the harness reports two numbers per stage:
**raw accuracy** (correct / all) and **conditional accuracy** (correct given
every upstream dependency was correct). The gap between them is the
error-propagation signal.

Comparator and stage-summary behavior are pinned by unit tests in `tests/`.

## Consistency

After the 30 questions, each session writes `answers/workflow.csv` — one row per
property it flagged non-compliant. `harness/consistency.py` measures agreement
across the 10 runs: Jaccard on the flagged set, the named unstable properties,
Kendall tau on the loss ranking, Fleiss kappa on the contact choice. Every
metric is also recomputed against the oracle, because ten runs can agree
perfectly and all be wrong. The report shows `consistency@10` beside
`accuracy@10`.

## Independence and audit

Each session runs in its own Docker container with a pinned Claude Code CLI,
pinned DuckDB, and no user configuration. No orchestration, no shared state, no
retry harness above the session. Every session's full transcript is committed to
`results/`, along with token counts and the harness commit hash. Anyone can
re-grade the committed transcripts without re-running a single model call.

## Cost accounting

Sessions run on a subscription plan and bill nothing directly. We log token
counts from each session transcript and report imputed dollars at list API
prices, with input, output, and cache tokens priced separately:

| Model      | Input $/MTok | Output $/MTok | Cache write | Cache read |
|------------|--------------|---------------|-------------|------------|
| Haiku 4.5  | 1.00         | 5.00          | 1.25× input | 0.1× input |
| Sonnet 5   | 3.00         | 15.00         | 1.25× input | 0.1× input |
| Opus 4.8   | 5.00         | 25.00         | 1.25× input | 0.1× input |

Claude Code always uses prompt caching; it cannot be disabled. Caching applies
identically to all three models, so the comparison stays fair. The headline
figure is a Pareto plot: accuracy on the y-axis, imputed dollars on the x-axis.

## Running

```bash
pixi install
pixi run test
python oracle/render.py                          # generate golden fixtures
docker build -t geodata-llm-eval .
python harness/run.py --model sonnet --passes 10
python harness/grade.py
python harness/consistency.py --model sonnet
python harness/report.py
```

## Out of scope

Metadata dropout, Fable, latency and throughput, held-out catalogs. Coffee and
oil palm have no delivery infrastructure in the current product; the eval grades
reporting that gap rather than inventing a route, and closing it is separate
work.
