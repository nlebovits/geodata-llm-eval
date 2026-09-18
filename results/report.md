# EUDR workflow benchmark results

Strict task success comes first. Everything under Diagnostics is
partial credit: useful for triage, not a claim about reliability.

## Strict task success and reliability

A trial passes when every critical question graded correct. A near
miss does not pass. Agent timeouts, early stops, and empty runs are
failures and stay in the denominator; only a dead credential,
unavailable infrastructure, or a grader crash invalidates a trial.

pass^k is the chance that k independent trials all pass, estimated
without replacement from the trials on disk, with a 95% interval. It
is blank where there are fewer than k valid trials.

Runs are never pooled across a spec edit, a regenerated golden, a
repinned dataset, or a harness change. Each row is one fingerprint.

| Configuration | Attempted | Invalid | Valid | Strict success | pass^3 | pass^5 | pass^10 |
|---------------|-----------|---------|---------------|----------------|--------|--------|---------|
| legacy/opus · full · agent-config – · spec d94480 · golden-at-run 6cd923 · graded 7bbdf1 · pins – · harness 3f38a9 · csv · attempt limit – · wall limit – | 2 | 0 (0%) | 2 | 0% (0/2) | – (n=2<3) | – (n=2<5) | – (n=2<10) |
| legacy/opus · no-coops · agent-config – · spec 3c7aae · golden-at-run 6cd923 · graded 7bbdf1 · pins – · harness 3f38a9 · csv · attempt limit – · wall limit – | 2 | 0 (0%) | 2 | 0% (0/2) | – (n=2<3) | – (n=2<5) | – (n=2<10) |
| legacy/opus · no-crops · agent-config – · spec efea91 · golden-at-run 6cd923 · graded 7bbdf1 · pins – · harness 017dd8 · csv · attempt limit – · wall limit – | 2 | 0 (0%) | 2 | 0% (0/2) | – (n=2<3) | – (n=2<5) | – (n=2<10) |
| legacy/opus · no-inputs · agent-config – · spec ee0bd5 · golden-at-run 6cd923 · graded 7bbdf1 · pins – · harness 017dd8 · csv · attempt limit – · wall limit – | 2 | 0 (0%) | 2 | 0% (0/2) | – (n=2<3) | – (n=2<5) | – (n=2<10) |
| legacy/opus · no-matching · agent-config – · spec 08c0a4 · golden-at-run 6cd923 · graded 7bbdf1 · pins – · harness 017dd8 · csv · attempt limit – · wall limit – | 2 | 0 (0%) | 2 | 0% (0/2) | – (n=2<3) | – (n=2<5) | – (n=2<10) |
| claude/opus · full · agent-config f208cf · spec ae7901 · golden-at-run 7bbdf1 · graded 7bbdf1 · pins df2389 · harness 42b045 · csv · attempt limit 3 · wall limit unlimited | 1 | 0 (0%) | 1 | 0% (0/1) | – (n=1<3) | – (n=1<5) | – (n=1<10) |

### Trial outcomes

| Configuration | agent_produced_nothing | agent_timeout | authentication_invalid | failed | grader_error | infrastructure_invalid | passed | ungraded |
|---------------|---|---|---|---|---|---|---|---|
| legacy/opus · full · agent-config – · spec d94480 · golden-at-run 6cd923 · graded 7bbdf1 · pins – · harness 3f38a9 · csv · attempt limit – · wall limit – | 0 | 0 | 0 | 2 | 0 | 0 | 0 | 0 |
| legacy/opus · no-coops · agent-config – · spec 3c7aae · golden-at-run 6cd923 · graded 7bbdf1 · pins – · harness 3f38a9 · csv · attempt limit – · wall limit – | 0 | 0 | 0 | 2 | 0 | 0 | 0 | 0 |
| legacy/opus · no-crops · agent-config – · spec efea91 · golden-at-run 6cd923 · graded 7bbdf1 · pins – · harness 017dd8 · csv · attempt limit – · wall limit – | 1 | 0 | 0 | 1 | 0 | 0 | 0 | 0 |
| legacy/opus · no-inputs · agent-config – · spec ee0bd5 · golden-at-run 6cd923 · graded 7bbdf1 · pins – · harness 017dd8 · csv · attempt limit – · wall limit – | 0 | 0 | 0 | 2 | 0 | 0 | 0 | 0 |
| legacy/opus · no-matching · agent-config – · spec 08c0a4 · golden-at-run 6cd923 · graded 7bbdf1 · pins – · harness 017dd8 · csv · attempt limit – · wall limit – | 0 | 0 | 0 | 2 | 0 | 0 | 0 | 0 |
| claude/opus · full · agent-config f208cf · spec ae7901 · golden-at-run 7bbdf1 · graded 7bbdf1 · pins df2389 · harness 42b045 · csv · attempt limit 3 · wall limit unlimited | 0 | 0 | 0 | 1 | 0 | 0 | 0 | 0 |

### Completion budget

Configured limits and observed high-water use. A reliability figure
means nothing without it: the same agent passing nine trials in ten
says something different at three resumes than at one.

| Configuration | Resume limit | Max resumes used | Max turns used | Wall limit | Max wall used | Total cost |
|---------------|--------------|------------------|----------------|------------|---------------|------------|
| legacy/opus · full · agent-config – · spec d94480 · golden-at-run 6cd923 · graded 7bbdf1 · pins – · harness 3f38a9 · csv · attempt limit – · wall limit – | – | 0 | 123 | – | 15m | $6.66 |
| legacy/opus · no-coops · agent-config – · spec 3c7aae · golden-at-run 6cd923 · graded 7bbdf1 · pins – · harness 3f38a9 · csv · attempt limit – · wall limit – | – | 0 | 127 | – | 17m | $8.07 |
| legacy/opus · no-crops · agent-config – · spec efea91 · golden-at-run 6cd923 · graded 7bbdf1 · pins – · harness 017dd8 · csv · attempt limit – · wall limit – | – | 2 | 125 | – | 17m | $4.26 |
| legacy/opus · no-inputs · agent-config – · spec ee0bd5 · golden-at-run 6cd923 · graded 7bbdf1 · pins – · harness 017dd8 · csv · attempt limit – · wall limit – | – | 0 | 105 | – | 17m | $7.23 |
| legacy/opus · no-matching · agent-config – · spec 08c0a4 · golden-at-run 6cd923 · graded 7bbdf1 · pins – · harness 017dd8 · csv · attempt limit – · wall limit – | – | 0 | 96 | – | 16m | $5.98 |
| claude/opus · full · agent-config f208cf · spec ae7901 · golden-at-run 7bbdf1 · graded 7bbdf1 · pins df2389 · harness 42b045 · csv · attempt limit 3 · wall limit unlimited | 2 | 0 | 115 | unlimited | 30m | $4.67 |

## Diagnostics

Partial credit and where it was lost. None of what follows is a
reliability claim: a configuration can answer almost every question
correctly and still fail most trials, which is what the section
above is for.

## Question accuracy

Accuracy is the share of questions graded correct against the
golden fixture. Cost is imputed from logged tokens at list API
prices (see harness/pricing.py).

Near misses clear ten times the grading tolerance but not the
tolerance itself: computed right, formatted or rounded differently.

| Configuration | Passes | Mean accuracy | Accuracy range | Mean near misses | Mean cost (USD) |
|---------------|--------|---------------|----------------|------------------|-----------------|
| claude/opus · full · agent-config f208cf · spec ae7901 · golden-at-run 7bbdf1 · graded 7bbdf1 · pins df2389 · harness 42b045 · csv · attempt limit 3 · wall limit unlimited | 1 | 87.1% | 87.1% – 87.1% | 2.0 | $4.6713 |
| legacy/opus · full · agent-config – · spec d94480 · golden-at-run 6cd923 · graded 7bbdf1 · pins – · harness 3f38a9 · csv · attempt limit – · wall limit – | 2 | 93.5% | 93.5% – 93.5% | 1.0 | $3.3298 |
| legacy/opus · no-coops · agent-config – · spec 3c7aae · golden-at-run 6cd923 · graded 7bbdf1 · pins – · harness 3f38a9 · csv · attempt limit – · wall limit – | 2 | 83.9% | 83.9% – 83.9% | 3.0 | $4.0338 |
| legacy/opus · no-crops · agent-config – · spec efea91 · golden-at-run 6cd923 · graded 7bbdf1 · pins – · harness 017dd8 · csv · attempt limit – · wall limit – | 1 | 90.3% | 90.3% – 90.3% | 1.0 | $4.2577 |
| legacy/opus · no-inputs · agent-config – · spec ee0bd5 · golden-at-run 6cd923 · graded 7bbdf1 · pins – · harness 017dd8 · csv · attempt limit – · wall limit – | 2 | 93.5% | 93.5% – 93.5% | 1.0 | $3.6161 |
| legacy/opus · no-matching · agent-config – · spec 08c0a4 · golden-at-run 6cd923 · graded 7bbdf1 · pins – · harness 017dd8 · csv · attempt limit – · wall limit – | 2 | 41.9% | 41.9% – 41.9% | 5.0 | $2.9905 |

## Runtime

Slow-call share is time inside tool calls slow enough to emit a
heartbeat, over wall clock. A high share with timeouts means the
run was degraded by the network, not by the model.

| Configuration | Mean wall clock | In slow tool calls | Timed-out calls |
|---------------|-----------------|--------------------|-----------------|
| claude/opus · full · agent-config f208cf · spec ae7901 · golden-at-run 7bbdf1 · graded 7bbdf1 · pins df2389 · harness 42b045 · csv · attempt limit 3 · wall limit unlimited | 30m | 39% | 0 |
| legacy/opus · full · agent-config – · spec d94480 · golden-at-run 6cd923 · graded 7bbdf1 · pins – · harness 3f38a9 · csv · attempt limit – · wall limit – | 14m | 5% | 0 |
| legacy/opus · no-coops · agent-config – · spec 3c7aae · golden-at-run 6cd923 · graded 7bbdf1 · pins – · harness 3f38a9 · csv · attempt limit – · wall limit – | 17m | 12% | 0 |
| legacy/opus · no-crops · agent-config – · spec efea91 · golden-at-run 6cd923 · graded 7bbdf1 · pins – · harness 017dd8 · csv · attempt limit – · wall limit – | 17m | 6% | 0 |
| legacy/opus · no-inputs · agent-config – · spec ee0bd5 · golden-at-run 6cd923 · graded 7bbdf1 · pins – · harness 017dd8 · csv · attempt limit – · wall limit – | 15m | 7% | 0 |
| legacy/opus · no-matching · agent-config – · spec 08c0a4 · golden-at-run 6cd923 · graded 7bbdf1 · pins – · harness 017dd8 · csv · attempt limit – · wall limit – | 15m | 13% | 0 |

## Accuracy by workflow stage

Raw = correct / all in stage. Cond. = correct / questions whose
dependencies all passed (the error-propagation-adjusted score).

| Configuration | S1 | S2 | S3 | S4 | S5 | S6 |
|---------------|-----|-----|-----|-----|-----|-----|
| claude/opus · full · agent-config f208cf · spec ae7901 · golden-at-run 7bbdf1 · graded 7bbdf1 · pins df2389 · harness 42b045 · csv · attempt limit 3 · wall limit unlimited | 100%/100% | 100%/100% | 86%/86% | 78%/78% | 83%/67% | 100%/– |
| legacy/opus · full · agent-config – · spec d94480 · golden-at-run 6cd923 · graded 7bbdf1 · pins – · harness 3f38a9 · csv · attempt limit – · wall limit – | 75%/75% | 75%/– | 100%/– | 100%/– | 100%/– | 100%/– |
| legacy/opus · no-coops · agent-config – · spec 3c7aae · golden-at-run 6cd923 · graded 7bbdf1 · pins – · harness 3f38a9 · csv · attempt limit – · wall limit – | 75%/75% | 75%/– | 100%/– | 100%/– | 67%/– | 0%/– |
| legacy/opus · no-crops · agent-config – · spec efea91 · golden-at-run 6cd923 · graded 7bbdf1 · pins – · harness 017dd8 · csv · attempt limit – · wall limit – | 75%/75% | 75%/– | 100%/– | 89%/– | 100%/– | 100%/– |
| legacy/opus · no-inputs · agent-config – · spec ee0bd5 · golden-at-run 6cd923 · graded 7bbdf1 · pins – · harness 017dd8 · csv · attempt limit – · wall limit – | 75%/75% | 75%/– | 100%/– | 100%/– | 100%/– | 100%/– |
| legacy/opus · no-matching · agent-config – · spec 08c0a4 · golden-at-run 6cd923 · graded 7bbdf1 · pins – · harness 017dd8 · csv · attempt limit – · wall limit – | 75%/75% | 75%/– | 43%/– | 22%/– | 33%/– | 0%/– |
