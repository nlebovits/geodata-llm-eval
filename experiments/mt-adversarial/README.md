# Experiment 2 — Input robustness (Mato Grosso adversarial portfolio)

A **separate, extendable** experiment that reuses the workflow harness but swaps
the clean Goiás list for a deliberately messy one. Where experiment 1 measures
whether a model can run the EUDR compliance workflow, this one measures whether
it applies [`policies/INPUTS.md`](../../policies/INPUTS.md) faithfully — handling
malformed input rather than silently dropping the properties it can't parse.

## The portfolio

Real Mato Grosso soy properties carrying post-2020 Hansen loss, drawn from five
frontier municipalities — **Alta Floresta, Juara, Nova Canaã do Norte, Tabaporã,
Itaúba** — then seeded with input defects. It is generated from the live
catalogs by `build_inputs.py`; selection and defect counts live in
[`config.json`](config.json).

### Defect kinds

Each is a slice of the clean sample transformed on emit (counts in `config.json`):

| Defect | Row looks like | Correct handling (INPUTS.md) |
|---|---|---|
| `duplicate` | same `cod_imovel` + geometry, twice | dedupe on (id, geometry); report the removed count |
| `centroid` | id present, geometry is a point | resolve by id; flag the point-precision geometry |
| `no_id` | no id, polygon | resolve by geometric match to the containing CAR parcel |
| `centroid_no_id` | no id, a point | resolve by point-in-parcel; ambiguous/outside ⇒ unresolvable |

### Encodings

The same portfolio in three shapes, selected at run time with
`run.py --input-mode`:

- **`csv`** — id-bearing rows only (`inputs/mt-adversarial.csv`). Tests id
  resolution and dedup.
- **`geometry`** — every row incl. the id-less ones (`inputs/mt-adversarial.parquet`).
  Tests geometric resolution and point handling.
- **`split`** — an id CSV plus a geometry parquet joined on the id
  (`inputs/mt-adversarial-split*.parquet`). Tests the join path.

## Ground truth

`build_inputs.py` also writes `manifest.json`: per-row origin cadaster, defect
kind, and the expected resolution. It is the basis for this experiment's oracle
and grader, and is **never mounted into a session** — the agent sees only the
input files, not the defect labels.

## Build

```bash
python experiments/mt-adversarial/build_inputs.py          # cached remote pulls
python experiments/mt-adversarial/build_inputs.py --force  # re-pull
```

Deterministic: same config + seed ⇒ byte-identical inputs. The two remote
extracts (CAR parcels in the target municipios, Trazo3 soy-loss fields in their
envelope) are cached under `_cache/` (gitignored); the inputs and manifest are
committed.

## Run

This experiment uses the same container and harness as experiment 1, pointed at
these inputs:

```bash
python harness/run.py --model sonnet --passes 10 --input-mode geometry
```

(Wire the experiment's input files into `run.py`'s `INPUT_FILES` for the mode
you are testing, or copy them to `fixtures/lists/`. Grading against the
manifest-derived oracle is the experiment's own step, tracked separately from
experiment 1's goldens.)

## Extend

- **More coverage** — add a municipio to `region.municipalities`, or a whole new
  `region` block, and re-run.
- **Different commodity** — change `selection.commodity_class` (e.g. `15` pasture
  → cattle) and `commodity_label`.
- **Different mess** — change the `defects` counts, or add a new defect kind to
  `DEFECTS` in `build_inputs.py` (with its transform and expected-resolution
  entry) and document it in the table above.

Because selection, region, and defects are all config, the experiment grows
without touching the harness.
