# geodata-llm-eval

Can a language model complete a real geospatial workflow against a cloud-native
data catalog? This repo measures that, and what it costs.

An agent gets the [trazo3-fields](https://source.coop/wri-data-lab/trazofields)
Portolan catalog on Source Cooperative, a set of 30 questions, and nothing
else. It queries the remote GeoParquet itself, recovers from its own errors,
and writes one answer file per question. We grade the answers against a
human-verified golden fixture and report accuracy against imputed API cost per
model.

## Design

One variable: the model. Everything else is fixed.

- **Models:** Claude Haiku 4.5, Sonnet 5, Opus 4.8
- **Passes:** 10 independent sessions per model, 30 sessions total
- **Task:** each session works through the full 30-question set (3 difficulty
  tiers) as one workflow. Within-session learning is intended — an agent that
  figures out the schema on question 1 should use that on question 30.
- **Metadata:** the published Portolan catalog as-is (STAC, README, AGENTS.md).
  Metadata ablation is the follow-up experiment, not this one.
- **Sampling:** default temperature, so the 10 passes form a real distribution.
- **Data:** stays remote on source.coop. Cloud-native range-request access is
  the point of the exercise, not an inconvenience to cache away.

## Independence and audit

Each session runs in its own Docker container with a pinned Claude Code CLI,
pinned DuckDB, and no user configuration. No orchestration, no shared state,
no retry harness above the session. Every session's full transcript is
committed to `results/`, along with token counts and the harness commit hash.
Golden fixtures are checksummed. Anyone can re-grade the committed transcripts
without re-running a single model call.

## Grading

Grading is executable, not model-judged. The harness compares each session's
`answers/qNN.csv` to `fixtures/golden/qNN.csv`:

- Counts and strings must match exactly
- Areas and derived numerics match within relative 1e-3
- Rows compare as sets; column order and names are ignored
- A missing or unparseable answer fails, recorded separately from a wrong one

Comparator behavior is pinned by unit tests in `tests/`.

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

## Out of scope

Metadata dropout, Fable, latency and throughput, held-out catalogs, and
expert-graded interpretation (EUDR compliance reasoning). Those are the
scale-up, and this MVP is the case for funding them.

## Build order

1. Author 30 questions with Tristan; verify golden results by hand
2. `harness/run.py` — spawn sessions, collect transcripts
3. `harness/grade.py` — compare answers to golden
4. `harness/report.py` — accuracy grid and Pareto plot

## Running

```bash
pixi install
pixi run test
docker build -t geodata-llm-eval .
python harness/run.py --model sonnet \
  --passes 10
python harness/grade.py
python harness/report.py
```
