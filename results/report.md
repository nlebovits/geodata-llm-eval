# EUDR workflow benchmark results

Accuracy is the share of questions graded correct against the
golden fixture. Cost is imputed from logged tokens at list API
prices (see harness/pricing.py).

Near misses clear ten times the grading tolerance but not the
tolerance itself: computed right, formatted or rounded differently.

| Model | Passes | Mean accuracy | Accuracy range | Mean near misses | Mean cost (USD) |
|-------|--------|---------------|----------------|------------------|-----------------|
| Haiku 4.5 | 1 | 13.3% | 13.3% – 13.3% | 2.0 | $0.5011 |
| Sonnet 5 | 2 | 41.7% | 13.3% – 70.0% | 0.0 | $4.1203 |
| Opus 4.8 | 3 | 95.6% | 90.0% – 100.0% | 0.0 | $4.0822 |

## Runtime

Slow-call share is time inside tool calls slow enough to emit a
heartbeat, over wall clock. A high share with timeouts means the
run was degraded by the network, not by the model.

| Model | Mean wall clock | In slow tool calls | Timed-out calls |
|-------|-----------------|--------------------|-----------------|
| Haiku 4.5 | 0m | 0% | 0 |
| Sonnet 5 | 19m | 131% | 12 |
| Opus 4.8 | 24m | 82% | 13 |

## Accuracy by workflow stage

Raw = correct / all in stage. Cond. = correct / questions whose
dependencies all passed (the error-propagation-adjusted score).

| Model | S1 | S2 | S3 | S4 | S5 | S6 |
|-------|-----|-----|-----|-----|-----|-----|
| Haiku 4.5 | 25%/25% | 100%/– | 0%/– | 0%/– | 0%/– | 0%/– |
| Sonnet 5 | 100%/100% | 50%/50% | 43%/86% | 39%/78% | 8%/0% | 0%/– |
| Opus 4.8 | 100%/100% | 100%/100% | 95%/95% | 96%/96% | 94%/94% | 67%/100% |
