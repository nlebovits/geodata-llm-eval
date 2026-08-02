# geodata-llm-eval

This benchmark tests whether a language model can complete a sourcing review
under the EU Deforestation Regulation (EUDR). The review uses real geospatial
data from cloud-hosted catalogs.

Each trial gives the model a portfolio of rural properties in Brazil and a
binding specification. The model must answer 31 questions. To find the answers, it
searches three Source Cooperative catalogs and queries remote GeoParquet data.
The policy rules determine which properties fail the review and whom to contact
about each failure.

SQL code produces the answer key and grades the model's work. A language model
never judges another language model.

## What the benchmark measures

A trial succeeds only when the model answers every critical question correctly.
All 31 questions are critical because each answer supports a later step. A
plausible final table still fails if it names the wrong property or chooses the
wrong contact.

The report begins with two measures of reliability:

- **Strict task success** asks whether one trial passed the entire workflow. The
  reported rate is the share of valid trials that succeeded. A near miss,
  timeout, early stop, or empty run counts as a failure.
- **pass^k** estimates whether repeated success is reliable. It gives the
  chance that `k` independent trials all pass. When enough trials exist, the
  report includes 95% intervals for `k = 3`, `5`, and `10`.

A trial is invalid only when evidence proves that the model wasn't responsible
for the failure. Examples include an expired credential, unavailable
infrastructure, and a grader crash. The attempted count includes invalid
trials, while the valid count shows how many trials contribute to the success
rate.

The report also shows where and why trials failed. It includes accuracy by
question and stage, near misses, consistency, runtime, and estimated API cost.
These diagnostic measures award partial credit, but they don't replace strict
success.

See the [committed benchmark report](results/report.md) for the repository's
current results.

## How a trial works

```text
portfolio + specification
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

Each trial starts a fresh Docker container with a clean workspace. The container
holds a rendered view of `SPEC.md` and the property portfolio. It can't access the
answer key.

If the model stops early, the harness can resume the same session. It stops
trying at the configured attempt limit and records all attempts in one
transcript.

The workflow has six dependent stages:

1. **Catalog discovery:** find the relevant collections and inspect their
   metadata.
2. **Cadaster resolution:** resolve property IDs against Brazil's 8.45-million-
   row Rural Environmental Registry (CAR) dataset. This stage also repairs
   duplicates, missing IDs, and swapped coordinate axes.
3. **Field and cadaster matching:** apply the 2/3 containment and buffered-union
   rules from [`SPEC.md`](SPEC.md).
4. **EUDR deforestation:** map MapBiomas land-cover classes to EUDR Annex I
   commodities, then measure post-2020 loss for the in-scope classes.
5. **Commodity infrastructure:** rank cooperatives and buyers for each flagged
   property and report gaps in coverage.
6. **Portfolio decision:** select the top contact for every non-compliant
   property.

Each question declares the earlier answers that it depends on. These links make
error propagation measurable. For example, a bad parcel match in stage 3
changes the land-cover analysis in stage 4. It can then change the facility
selected in stage 5. The report gives raw accuracy for each stage and a
conditional score for questions whose dependencies passed.

## Run the benchmark

Install Python 3.12 or later, [uv](https://docs.astral.sh/uv/), and Docker. You
also need credentials for the agent you plan to run.

A run can transfer several gigabytes from the remote catalogs. Test your setup
with one trial or use a dry run before you start a larger experiment.

Install the dependencies and run the local tests:

```bash
uv sync
uv run pytest
```

Build the pinned session image, authenticate the selected CLI, and start one
trial:

```bash
docker build -t geodata-llm-eval .

claude login  # or: export ANTHROPIC_API_KEY=...
uv run python harness/run.py --agent claude --model sonnet --passes 1 --follow

codex login  # or: export CODEX_API_KEY=...
uv run python harness/run.py --agent codex --model gpt-5.6-sol \
  --auth login --reasoning-effort high --passes 1 --follow
```

The command accepts these options:

- `--follow` prints each tool call. Without this option, the harness prints a
  heartbeat once a minute.
- `--dry-run` prints the Docker command without starting a session.
- `--max-wall-seconds` limits the total run time.
- `--max-attempts` limits how often the harness resumes an unfinished session.

Grade all runs and build the report:

```bash
uv run python harness/grade.py
uv run python harness/consistency.py --model sonnet
uv run python harness/report.py
```

These commands write the following artifacts:

| Path | Contents |
|---|---|
| `results/{configuration}/{run_id}/transcript.jsonl` | Complete native JSONL trajectory |
| `results/{configuration}/{run_id}/stderr.log` | Native CLI diagnostics |
| `results/{configuration}/{run_id}/answers/` | Answer CSVs and the final workflow |
| `results/{configuration}/{run_id}/meta.json` | Status, normalized usage, runtime receipt, and experiment fingerprints |
| `results/{configuration}/{run_id}/grades.json` | Per-question grades |
| `results/{configuration}/{run_id}/diffs.json` | Cell-level mismatches for failed answers |
| `results/report.md` | Reliability results and diagnostic tables |
| `results/pareto.png` | Per-session accuracy against imputed cost |

A run ID contains its start time in Coordinated Universal Time (UTC) and the
harness commit. The harness keeps every run for audit. This includes empty runs
and trials invalidated by external infrastructure.

## Compare like with like

The report groups trials only when they describe the same experiment. Trials in
a group must use the same:

- model
- policy specification
- answer key and pinned datasets
- harness commit
- input encoding
- attempt limit
- wall-clock limit

If any value changes, the report starts a separate group.

For a standard repeated comparison, run ten independent trials per model with
the same configuration:

```bash
uv run python harness/run.py --model haiku --passes 10 --label baseline
uv run python harness/run.py --model sonnet --passes 10 --label baseline
uv run python harness/run.py --model opus --passes 10 --label baseline
```

The harness uses each model's default sampling settings. It counts input,
output, and cache tokens. The prices in
[`harness/pricing.py`](harness/pricing.py) convert those counts into a cost
estimate. A session authenticated through a subscription doesn't incur this
API charge.

## How grading works

[`oracle/render.py`](oracle/render.py) queries the pinned catalogs with the
vendored rural-land EUDR SQL. It stores the correct answers in
`fixtures/golden/qNN.csv` and records their checksums in `SHA256SUMS`. These
files form the answer key. Because they're committed, you can regrade a saved
run without calling a model or querying the source data.

The comparator applies these rules:

- Row order does not matter because rows compare as multisets.
- The comparator matches columns by their contents. Column names and order can
  differ, but values and data types must meet the grading policy.
- Integers require exact matches.
- String comparison is case-insensitive.
- Numeric values default to an `exact` grading policy with relative tolerance
  `1e-3` and exact integer comparison.
- A question sets either `exact` or `geometry` as its default policy. An
  individual column can override that default.
- The `geometry` policy uses a relative tolerance of `1e-2`. For integer
  values, it also allows a difference of at least 2 or 1% of the correct value,
  whichever is greater.
- A grading policy belongs to a column in the answer key. It stays with that
  column while the comparator finds the matching submitted column.
- A value within ten times its tolerance is a `near_miss`, which does not
  count as correct.
- Missing, unparseable, near-miss, and wrong answers remain distinct.

Geodesic and projected calculations can produce slightly different geometry
values. The wider tolerance accepts those reasonable differences. The oracle
doesn't round any intermediate table that a later question uses.

To rebuild the golden fixtures:

```bash
uv run python oracle/render.py
```

This command scans the live catalogs and can take several minutes.

## Policy and data are part of the specification

[`SPEC.md`](SPEC.md) is the policy source and part of the model's input. It
explains how to resolve input errors, match fields, classify EUDR crops, and rank
facilities. The benchmark tests whether the model can apply these written
rules. It doesn't require the model to guess hidden policy.

[`fixtures/pins.json`](fixtures/pins.json) identifies the exact remote data used
by an experiment. For each asset, it records:

- the network address, entity tag, and size
- the schema and row count
- the projection and GeoParquet metadata

Check the pins before a new experiment:

```bash
uv run python harness/pin_check.py
uv run python harness/pin_check.py --deep
```

The first command checks whether each asset is reachable and still has the
expected size and entity tag. The `--deep` option also reads the Parquet
footers. If an asset changed, review it and rebuild the answer key. A temporary
outage doesn't change the recorded identity.

## Reproducibility and credential handling

A trial container doesn't inherit the host's user configuration. The harness
gives it only the authentication it needs: either a copy of the Claude login
credential or the value of `ANTHROPIC_API_KEY`. After the trial, the harness
deletes the credential copy and removes the container.

The repository stores full transcripts for audit. If a model prints a
credential, that secret can therefore enter Git history.
[`scripts/check_credentials.py`](scripts/check_credentials.py) scans file
contents in local hooks and CI. If the scanner finds a token in a transcript,
treat the token as leaked and rotate it with `claude login`.

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
| [`SPEC.md`](SPEC.md) | Binding workflow rules supplied to the agent |
| [`harness/`](harness/) | Runner, grader, reliability analysis, consistency metrics, and reports |
| [`oracle/`](oracle/) | SQL answer-key generation |
| [`fixtures/golden/`](fixtures/golden/) | Committed answer key and checksum manifest |
| [`results/`](results/) | Transcripts, grades, summaries, and plots |
| [`docs/METHODS.md`](docs/METHODS.md) | Full methodology and design decisions |

## Scope limits

The benchmark measures the EUDR's deforestation-free condition and selected
legality indicators from CAR. It doesn't make a complete EUDR compliance
decision. That decision also needs evidence of legality and a due diligence
statement.

The benchmark doesn't yet test metadata dropout, held-out catalogs, or
throughput.

The facilities data contains no delivery route for coffee or oil palm. A model
must report this gap instead of inventing a route. The crop policy also treats
MapBiomas class 21 as pasture. Because the source data can't detect forest
plantation reliably, the policy excludes that class. Both assumptions remain
in the policy documents, where an auditor can inspect them.
