# geodata-llm-eval

This repository tests whether a language model can complete an end-to-end EU
Deforestation Regulation (EUDR) sourcing workflow against real cloud-hosted
geospatial data. It measures whether the whole workflow succeeds reliably,
where errors enter or propagate, and what each attempt costs.

The benchmark gives an agent a Brazilian rural-property portfolio, four policy
documents, and 31 questions. The agent must inspect three Source Cooperative
catalogs, query remote GeoParquet data, apply the policies, and produce a final
list of non-compliant properties and their best contacts. A deterministic SQL
oracle and comparator grade the result; no model judges another model.

## What the benchmark measures

A successful trial answers every critical question correctly. All 31 questions
are currently critical because they form one dependent workflow: a plausible
final table is not useful if it identifies or routes the wrong property.

The report leads with two reliability measures:

- **Strict task success** is the share of valid trials that pass the entire
  workflow. Near misses, timeouts, early stops, and empty runs are failures.
- **pass^k** estimates the chance that `k` independent trials all pass. The
  report includes 95% intervals for `k = 3`, `5`, and `10` when enough
  trials exist.

Only failures proven external to the agent, such as a dead credential,
unavailable infrastructure, or a grader crash, invalidate a trial. The report
keeps those trials visible and shows both the attempted and valid counts.

Per-question accuracy, stage-level accuracy, near misses, consistency, runtime,
and imputed API cost help diagnose the strict result. They are partial-credit
signals, not substitutes for completing the workflow.

See the [committed benchmark report](results/report.md) for the results currently
in this repository.

## How a trial works

```text
portfolio + questions + policies
              |
              v
   isolated Docker session ------> three remote data catalogs
              |
              v
  31 answer CSVs + workflow.csv
              |
              v
 deterministic grader + SQL oracle
              |
              v
 strict success, pass^k, diagnostics, and cost
```

Each trial runs in a fresh Docker container with a clean workspace. The session
receives the task, question set, policy documents, and input portfolio. It does
not receive the golden answers. The harness can resume an unfinished session up
to the configured attempt limit and records every attempt in one transcript.

The workflow has six dependent stages:

1. **Catalog discovery:** find the relevant collections and inspect their
   metadata.
2. **Cadaster resolution:** resolve property IDs against Brazil's 8.45-million-
   row CAR dataset and repair duplicates, missing IDs, and swapped coordinate
   axes.
3. **Field and cadaster matching:** apply the 2/3 containment and buffered-union
   rules from [`policies/MATCHING.md`](policies/MATCHING.md).
4. **EUDR deforestation:** map MapBiomas land-cover classes to EUDR Annex I
   commodities, then measure post-2020 loss for the in-scope classes.
5. **Commodity infrastructure:** rank cooperatives and buyers for each flagged
   property and report gaps in coverage.
6. **Portfolio decision:** select the top contact for every non-compliant
   property.

Question dependencies make propagation measurable. For example, a bad parcel
match in stage 3 changes the land-cover analysis in stage 4 and the facility
selection in stage 5. The report shows both raw stage accuracy and conditional
accuracy for questions whose upstream dependencies passed.

## Run the benchmark

You need Python 3.12 or later, [uv](https://docs.astral.sh/uv/), Docker, and
either a Claude login or an `ANTHROPIC_API_KEY`. Runs query live remote
catalogs and can transfer several gigabytes, so first check the setup with one
pass or a dry run.

Install the dependencies and run the local tests:

```bash
uv sync
uv run pytest
```

Authenticate, build the session image, and start one trial:

```bash
claude login  # or: export ANTHROPIC_API_KEY=...
docker build -t geodata-llm-eval .
uv run python harness/run.py --model sonnet --passes 1 --follow
```

`--follow` prints tool calls as they happen. Without it, the harness prints a
heartbeat once a minute. Add `--dry-run` to inspect the Docker command without
starting a session. Use `--max-wall-seconds` to enforce a completion budget
and `--max-attempts` to control how often the harness resumes an unfinished
session.

Grade all runs and build the report:

```bash
uv run python harness/grade.py
uv run python harness/consistency.py --model sonnet
uv run python harness/report.py
```

These commands write the following artifacts:

| Path | Contents |
|---|---|
| `results/{model}/{run_id}/transcript.jsonl` | Complete model transcript |
| `results/{model}/{run_id}/answers/` | Answer CSVs and the final workflow |
| `results/{model}/{run_id}/meta.json` | Status, budgets, tokens, cost, and experiment fingerprints |
| `results/{model}/{run_id}/grades.json` | Per-question grades |
| `results/{model}/{run_id}/diffs.json` | Cell-level mismatches for failed answers |
| `results/report.md` | Reliability results and diagnostic tables |
| `results/pareto.png` | Per-session accuracy against imputed cost |

A run ID contains its UTC start time and harness commit. Runs remain on disk for
audit even when the agent produces nothing or external infrastructure
invalidates the trial.

## Compare like with like

The reliability report does not pool every run of the same model. A group must
share the complete experiment fingerprint: model, policy specification, golden
fixtures, pinned datasets, harness commit, input encoding, attempt limit, and
wall-clock limit. Changing any of these creates a separate row in the report.

For a standard repeated comparison, run ten independent trials per model with
the same configuration:

```bash
uv run python harness/run.py --model haiku --passes 10 --label baseline
uv run python harness/run.py --model sonnet --passes 10 --label baseline
uv run python harness/run.py --model opus --passes 10 --label baseline
```

The harness uses each model's default sampling settings. It logs input, output,
and cache tokens, then imputes cost from the list prices in
[`harness/pricing.py`](harness/pricing.py). These are comparison estimates;
sessions authenticated through a subscription do not incur those API charges.

## How grading works

[`oracle/render.py`](oracle/render.py) runs the vendored rural-land EUDR SQL
against the pinned catalogs and writes `fixtures/golden/qNN.csv` plus a
`SHA256SUMS` manifest. Golden fixtures are committed, so you can regrade a
saved run without calling a model or querying the source data again.

The comparator applies these rules:

- Rows compare as multisets, so row order does not matter.
- Column order and names do not matter; values and their types do.
- Integers match exactly, and strings match without regard to case.
- Most numeric values use a relative tolerance of `1e-3`.
- Geometry-sensitive questions use `1e-2` plus integer slack of the greater
  of 2 or 1% of the golden value.
- A value within ten times its tolerance is a `near_miss`, which does not
  count as correct.
- Missing, unparseable, near-miss, and wrong answers remain distinct.

The wider geometry tolerance covers reasonable differences between geodesic and
projected calculations. The oracle never rounds an intermediate table that a
later question reads.

To rebuild the golden fixtures:

```bash
uv run python oracle/render.py
```

This command scans the live catalogs and can take several minutes.

## Policy and data are part of the specification

The documents in [`policies/`](policies/) define how the agent must resolve
inputs, match fields, classify EUDR crops, and rank facilities. The benchmark
tests whether the model can implement those written rules, not whether it can
guess hidden policy.

[`fixtures/pins.json`](fixtures/pins.json) records each remote asset's URL and
identity, including its entity tag, size, schema, row count, projection, and
GeoParquet metadata. Check the pins before a new experiment:

```bash
uv run python harness/pin_check.py
uv run python harness/pin_check.py --deep
```

The first command checks reachability, size, and entity tags. `--deep` also
reads the Parquet footers. A changed asset requires review and regenerated
golden fixtures; a temporary outage does not.

## Reproducibility and credential handling

The container runs with no host user configuration. The harness mounts only a
copy of the Claude login credential, or passes `ANTHROPIC_API_KEY` when set.
It deletes the credential copy with the session and force-removes the container
on exit.

Full transcripts are committed for audit, which makes accidental credential
output especially serious.
[`scripts/check_credentials.py`](scripts/check_credentials.py) scans file
contents in local hooks and CI. If a token reaches a transcript, treat it as
leaked and rotate it with `claude login`.

Install the repository hooks after `uv sync`:

```bash
uvx prek@0.4.11 install --hook-type pre-commit \
  --hook-type commit-msg --hook-type pre-push
```

Run the same commit-stage checks as CI with:

```bash
uvx prek@0.4.11 run --all-files --show-diff-on-failure
```

Documentation follows the project [prose style guide](docs/PROSE.md), which
also lists the advisory Vale and proselint commands.

## Repository map

| Path | Purpose |
|---|---|
| [`fixtures/questions.yaml`](fixtures/questions.yaml) | Questions, dependencies, output contracts, and grading modes |
| [`policies/`](policies/) | Binding workflow rules supplied to the agent |
| [`harness/`](harness/) | Runner, grader, reliability analysis, consistency metrics, and reports |
| [`oracle/`](oracle/) | SQL answer-key generation |
| [`fixtures/golden/`](fixtures/golden/) | Committed answer key and checksum manifest |
| [`results/`](results/) | Transcripts, grades, summaries, and plots |
| [`docs/METHODS.md`](docs/METHODS.md) | Full methodology and design decisions |

## Scope limits

The benchmark measures the deforestation-free condition and selected CAR-based
legality indicators. It does not make a complete EUDR compliance determination,
which also requires legality evidence and a due diligence statement.

Metadata dropout, held-out catalogs, and throughput remain out of scope. Coffee
and oil palm have no delivery infrastructure in the current facilities data;
the correct result is to report that gap rather than invent a route. The crop
policy also treats MapBiomas class 21 as pasture and excludes forest plantation
because of detection limits. The policy documents state both assumptions so
their effect remains auditable.
