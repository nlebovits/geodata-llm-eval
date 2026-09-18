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
| claude/opus · full · agent-config f208cf · spec ae7901 · golden-at-run 7bbdf1 · graded 7bbdf1 · pins df2389 · harness ff0a0c · csv · attempt limit 3 · wall limit unlimited | 5 | 0 (0%) | 5 | 0% (0/5) | 0% [0%–8%] | 0% [0%–2%] | – (n=5<10) |
| claude/opus · full · agent-config f208cf · spec ae7901 · golden-at-run 7bbdf1 · graded 7bbdf1 · pins df2389 · harness ec3740 · csv · attempt limit 3 · wall limit unlimited | 4 | 0 (0%) | 4 | 0% (0/4) | 0% [0%–12%] | – (n=4<5) | – (n=4<10) |
| claude/opus · full · agent-config f208cf · spec ae7901 · golden-at-run 7bbdf1 · graded 7bbdf1 · pins df2389 · harness 42b045 · csv · attempt limit 3 · wall limit unlimited | 1 | 0 (0%) | 1 | 0% (0/1) | – (n=1<3) | – (n=1<5) | – (n=1<10) |

### Trial outcomes

| Configuration | agent_produced_nothing | agent_timeout | authentication_invalid | failed | grader_error | infrastructure_invalid | passed | ungraded |
|---------------|---|---|---|---|---|---|---|---|
| claude/opus · full · agent-config f208cf · spec ae7901 · golden-at-run 7bbdf1 · graded 7bbdf1 · pins df2389 · harness ff0a0c · csv · attempt limit 3 · wall limit unlimited | 0 | 0 | 0 | 5 | 0 | 0 | 0 | 0 |
| claude/opus · full · agent-config f208cf · spec ae7901 · golden-at-run 7bbdf1 · graded 7bbdf1 · pins df2389 · harness ec3740 · csv · attempt limit 3 · wall limit unlimited | 0 | 0 | 0 | 4 | 0 | 0 | 0 | 0 |
| claude/opus · full · agent-config f208cf · spec ae7901 · golden-at-run 7bbdf1 · graded 7bbdf1 · pins df2389 · harness 42b045 · csv · attempt limit 3 · wall limit unlimited | 0 | 0 | 0 | 1 | 0 | 0 | 0 | 0 |

### Completion budget

Configured limits and observed high-water use. A reliability figure
means nothing without it: the same agent passing nine trials in ten
says something different at three resumes than at one.

| Configuration | Resume limit | Max resumes used | Max turns used | Wall limit | Max wall used | Total cost |
|---------------|--------------|------------------|----------------|------------|---------------|------------|
| claude/opus · full · agent-config f208cf · spec ae7901 · golden-at-run 7bbdf1 · graded 7bbdf1 · pins df2389 · harness ff0a0c · csv · attempt limit 3 · wall limit unlimited | 2 | 0 | 134 | unlimited | 23m | $22.65 |
| claude/opus · full · agent-config f208cf · spec ae7901 · golden-at-run 7bbdf1 · graded 7bbdf1 · pins df2389 · harness ec3740 · csv · attempt limit 3 · wall limit unlimited | 2 | 0 | 133 | unlimited | 26m | $17.59 |
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
| claude/opus · full · agent-config f208cf · spec ae7901 · golden-at-run 7bbdf1 · graded 7bbdf1 · pins df2389 · harness ec3740 · csv · attempt limit 3 · wall limit unlimited | 4 | 90.3% | 90.3% – 90.3% | 0.8 | $4.3986 |
| claude/opus · full · agent-config f208cf · spec ae7901 · golden-at-run 7bbdf1 · graded 7bbdf1 · pins df2389 · harness ff0a0c · csv · attempt limit 3 · wall limit unlimited | 5 | 91.6% | 87.1% – 96.8% | 0.8 | $4.5292 |

## Runtime

Slow-call share is time inside tool calls slow enough to emit a
heartbeat, over wall clock. A high share with timeouts means the
run was degraded by the network, not by the model.

| Configuration | Mean wall clock | In slow tool calls | Timed-out calls |
|---------------|-----------------|--------------------|-----------------|
| claude/opus · full · agent-config f208cf · spec ae7901 · golden-at-run 7bbdf1 · graded 7bbdf1 · pins df2389 · harness 42b045 · csv · attempt limit 3 · wall limit unlimited | 30m | 39% | 0 |
| claude/opus · full · agent-config f208cf · spec ae7901 · golden-at-run 7bbdf1 · graded 7bbdf1 · pins df2389 · harness ec3740 · csv · attempt limit 3 · wall limit unlimited | 21m | 7% | 0 |
| claude/opus · full · agent-config f208cf · spec ae7901 · golden-at-run 7bbdf1 · graded 7bbdf1 · pins df2389 · harness ff0a0c · csv · attempt limit 3 · wall limit unlimited | 20m | 3% | 0 |

## Accuracy by workflow stage

Raw = correct / all in stage. Cond. = correct / questions whose
dependencies all passed (the error-propagation-adjusted score).

| Configuration | S1 | S2 | S3 | S4 | S5 | S6 |
|---------------|-----|-----|-----|-----|-----|-----|
| claude/opus · full · agent-config f208cf · spec ae7901 · golden-at-run 7bbdf1 · graded 7bbdf1 · pins df2389 · harness 42b045 · csv · attempt limit 3 · wall limit unlimited | 100%/100% | 100%/100% | 86%/86% | 78%/78% | 83%/67% | 100%/– |
| claude/opus · full · agent-config f208cf · spec ae7901 · golden-at-run 7bbdf1 · graded 7bbdf1 · pins df2389 · harness ec3740 · csv · attempt limit 3 · wall limit unlimited | 94%/94% | 100%/100% | 96%/100% | 78%/74% | 92%/78% | 100%/100% |
| claude/opus · full · agent-config f208cf · spec ae7901 · golden-at-run 7bbdf1 · graded 7bbdf1 · pins df2389 · harness ff0a0c · csv · attempt limit 3 · wall limit unlimited | 95%/95% | 100%/100% | 100%/100% | 80%/81% | 90%/75% | 100%/100% |
