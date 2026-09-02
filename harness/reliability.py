"""Strict task success and pass^k across independent trials.

Mean question accuracy answers "how much of the workflow does this agent tend
to get right". It does not answer the question anyone deploying an agent
actually asks, which is "if I run this k times, how often does every run come
back correct". A configuration scoring 93% per question can still fail every
single trial, because 31 questions at 93% pass together about one time in ten.

pass^k is that number. It is the probability that k independent trials all
pass, estimated from the n trials on disk. It is not pass@k, which asks
whether *any* of k attempts succeeds and rewards retrying; pass^k asks whether
*all* of them do, and punishes variance.

Trials are only comparable inside one fingerprint (harness/layout.py). A
figure pooled across a spec edit or a repinned dataset describes no experiment
that was ever run, so grouping happens before any arithmetic here.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

from layout import (
    INVALID_STATUSES,
    PASSED,
    Fingerprint,
    Meta,
    group_by_fingerprint,
    read_meta,
    run_name,
    trial_status,
)

# The k values reported. Three is the smallest number that says anything about
# variance, ten is roughly a working week of a team leaning on the agent daily.
REPORTED_K = (3, 5, 10)

# Two-sided 95%. The z rather than the confidence level is the constant
# because it is what the arithmetic uses.
Z_95 = 1.959963984540054


@dataclass(frozen=True)
class PassHatK:
    """pass^k with an interval, or None bounds when n is too small.

    `point` is the unbiased estimate: of the C(n, k) ways to draw k of the n
    trials without replacement, the share whose every member passed. The
    plug-in estimate (s/n)**k is biased upward at small n, which is exactly
    the regime this benchmark runs in, so it is not what is reported.

    `low` and `high` come from a Wilson interval on the per-trial pass rate,
    each bound raised to the k. Raising to a positive power is monotone, so
    the transformed bounds keep the coverage of the untransformed ones.
    """

    k: int
    point: float
    low: float
    high: float
    trials: int
    successes: int


def wilson_interval(
    successes: int, trials: int, z: float = Z_95
) -> tuple[float, float]:
    """A confidence interval for a proportion, on n small enough to need one.

    Wilson rather than the textbook normal interval: at n = 5 with 5 successes
    the normal interval is [1.0, 1.0], which claims a certainty five trials
    cannot buy. Wilson stays inside (0, 1) and stays honest at the edges.
    """
    if trials <= 0:
        return 0.0, 1.0
    p = successes / trials
    denom = 1 + z * z / trials
    centre = (p + z * z / (2 * trials)) / denom
    half = z * math.sqrt(p * (1 - p) / trials + z * z / (4 * trials * trials)) / denom
    return max(0.0, centre - half), min(1.0, centre + half)


def pass_hat_k(successes: int, trials: int, k: int) -> PassHatK | None:
    """The chance that k independent trials all pass, or None below k trials.

    None rather than a number when n < k. Two trials cannot estimate how often
    ten in a row succeed, and printing (s/n)**10 from them would dress a guess
    as a measurement. The report says how many more trials the figure needs.
    """
    if k < 1 or trials < k:
        return None
    point = math.comb(successes, k) / math.comb(trials, k) if successes >= k else 0.0
    low, high = wilson_interval(successes, trials)
    return PassHatK(
        k=k,
        point=point,
        low=low**k,
        high=high**k,
        trials=trials,
        successes=successes,
    )


@dataclass(frozen=True)
class Budget:
    """What the passing and failing trials in a group were allowed to spend.

    Reported next to the reliability figure because the two only mean anything
    together: an agent that passes nine trials in ten is telling you something
    different at three resumes and forty minutes than at one resume and five.
    """

    max_attempts: int
    max_turns: int
    max_wall_seconds: float
    total_cost_usd: float


@dataclass(frozen=True)
class Group:
    """Every trial sharing one fingerprint, and what they add up to.

    `attempted` counts every run directory. `valid` drops only the trials
    something outside the agent invalidated. Strict success is measured over
    `valid`, and both denominators are reported, because a rate over a hidden
    denominator is the failure mode this whole module exists to remove.
    """

    fingerprint: Fingerprint
    attempted: int
    invalid: int
    passed: int
    statuses: dict[str, int]
    budget: Budget
    invalid_runs: list[tuple[str, str]]

    @property
    def valid(self) -> int:
        return self.attempted - self.invalid

    @property
    def invalid_rate(self) -> float:
        return self.invalid / self.attempted if self.attempted else 0.0

    @property
    def strict_success_rate(self) -> float | None:
        """Passed over valid, or None when nothing valid was measured."""
        return self.passed / self.valid if self.valid else None

    def pass_hat(self, k: int) -> PassHatK | None:
        return pass_hat_k(self.passed, self.valid, k)


def _budget(metas: list[Meta]) -> Budget:
    """The high-water mark of what these trials spent.

    Maxima, not means: the budget is the ceiling a trial was run under, and a
    mean over trials that stopped early would understate it.
    """

    def top(key: str) -> float:
        return max((m.get(key) or 0 for m in metas), default=0)

    return Budget(
        max_attempts=int(top("attempts")),
        max_turns=int(top("turns")),
        max_wall_seconds=float(top("duration_seconds")),
        total_cost_usd=round(sum(m.get("imputed_cost_usd") or 0.0 for m in metas), 4),
    )


def summarise(run_dirs_: list[Path]) -> list[Group]:
    """One Group per fingerprint, largest first, ties broken by label.

    Every run directory is an attempted trial. Nothing is filtered out on the
    way in: a trial that produced nothing, timed out, or stopped early is a
    trial the agent failed, and dropping it here is what would make the rate
    a fiction.
    """
    groups: list[Group] = []
    for fingerprint, dirs in group_by_fingerprint(run_dirs_).items():
        metas = [read_meta(d) for d in dirs]
        statuses: dict[str, int] = {}
        invalid_runs: list[tuple[str, str]] = []
        passed = invalid = 0
        for run_dir, meta in zip(dirs, metas, strict=True):
            status = trial_status(meta)
            statuses[status] = statuses.get(status, 0) + 1
            if status in INVALID_STATUSES:
                invalid += 1
                invalid_runs.append((run_name(run_dir), status))
            elif status == PASSED:
                passed += 1
        groups.append(
            Group(
                fingerprint=fingerprint,
                attempted=len(dirs),
                invalid=invalid,
                passed=passed,
                statuses=statuses,
                budget=_budget(metas),
                invalid_runs=invalid_runs,
            )
        )
    groups.sort(key=lambda g: (-g.attempted, g.fingerprint.label()))
    return groups
