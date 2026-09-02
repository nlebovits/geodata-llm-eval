"""Strict task success, trial statuses, and pass^k.

Every figure here is pinned on a hand-computed example. The point of the
module under test is to stop a reliability number being asserted rather than
derived, so the tests do not get to assert one either."""

import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "harness"))

import layout
import reliability
from grade import CRITICAL_MINIMUM, critical_ids, strict_success

QUESTIONS = [
    {"id": "01", "stage": 1, "depends_on": []},
    {"id": "02", "stage": 2, "depends_on": ["01"]},
    {"id": "03", "stage": 3, "depends_on": ["02"]},
]

ALL_CORRECT = {"q01": "correct", "q02": "correct", "q03": "correct"}


def _run(results: Path, n: int, meta: dict[str, object]) -> Path:
    """One run directory carrying nothing but its meta, which is all the
    reliability path reads."""
    run_id = f"2026080{n}T120000Z-abc1234"
    d = results / "opus" / run_id
    d.mkdir(parents=True)
    base = {
        "model": "opus",
        "model_id": "claude-opus-4-8",
        "run_id": run_id,
        "status": "done",
        "harness_commit": "abc1234",
        "golden_fingerprint": "g1",
        "spec_fingerprint": "s1",
        "pins_fingerprint": "p1",
        "input_mode": "csv",
        "attempts": 1,
        "turns": 100,
        "duration_seconds": 600.0,
        "imputed_cost_usd": 1.0,
    }
    base.update(meta)
    (d / "meta.json").write_text(json.dumps(base))
    return d


# ----------------------------------------------------------- strict success


def test_a_fully_correct_run_passes() -> None:
    assert strict_success(ALL_CORRECT, QUESTIONS) is True


def test_a_run_missing_one_critical_answer_fails() -> None:
    """The workflow is one deliverable. Thirty of thirty-one is not a pass,
    because the missing one is the file someone would have acted on."""
    grades = dict(ALL_CORRECT)
    del grades["q03"]
    assert strict_success(grades, QUESTIONS) is False


def test_a_near_miss_does_not_pass() -> None:
    """A near miss is a number close enough to triage against and not close
    enough to hand a compliance officer."""
    assert strict_success({**ALL_CORRECT, "q02": "near_miss"}, QUESTIONS) is False


def test_a_question_can_opt_out_of_being_critical() -> None:
    """The mechanism exists so a ruling can demote a diagnostic question. No
    question in fixtures/questions.yaml uses it today."""
    questions = [*QUESTIONS[:2], {**QUESTIONS[2], "critical": False}]
    assert critical_ids(questions) == {"01", "02"}
    assert strict_success({**ALL_CORRECT, "q03": "wrong"}, questions) is True


def test_an_empty_question_set_cannot_certify_a_pass() -> None:
    """No questions means nothing was checked, which is not the same as
    everything passing."""
    assert strict_success(ALL_CORRECT, []) is False


def test_the_shipped_question_set_keeps_the_minimum_critical() -> None:
    """Issue #29 names six questions no ruling may demote. A change that
    marks one `critical: false` has to argue for it there first."""
    import yaml

    root = Path(__file__).resolve().parent.parent
    spec = yaml.safe_load((root / "fixtures" / "questions.yaml").read_text())
    ids = critical_ids(spec["questions"])
    assert CRITICAL_MINIMUM <= ids
    assert ids == {q["id"] for q in spec["questions"]}, "the ruling today is all 31"


# ------------------------------------------------------------ trial statuses


def test_an_empty_run_is_a_failure_not_an_excuse() -> None:
    """An agent that wrote nothing failed the task. It stays in the
    denominator, which is the whole point of the status split."""
    meta = {"status": "agent_produced_nothing"}
    assert layout.trial_status(meta) == layout.AGENT_PRODUCED_NOTHING
    assert layout.is_valid(meta) is True


def test_a_legacy_produced_nothing_status_still_resolves() -> None:
    """Run directories on disk predate the rename and are not rewritten."""
    assert (
        layout.trial_status({"status": "produced_nothing"})
        == layout.AGENT_PRODUCED_NOTHING
    )


def test_a_timeout_is_the_agents_failure() -> None:
    meta = {"status": "agent_timeout"}
    assert layout.trial_status(meta) == layout.AGENT_TIMEOUT
    assert layout.is_valid(meta) is True


def test_a_dead_credential_invalidates_the_trial() -> None:
    """The session never reached the task, so it measured nothing about the
    agent either way."""
    meta = {"status": "authentication_invalid"}
    assert layout.trial_status(meta) == layout.AUTHENTICATION_INVALID
    assert layout.is_valid(meta) is False


def test_simulated_infrastructure_failure_invalidates_the_trial() -> None:
    meta = {"status": "infrastructure_invalid", "strict_success": False}
    assert layout.trial_status(meta) == layout.INFRASTRUCTURE_INVALID
    assert layout.is_valid(meta) is False


def test_a_run_with_no_verdict_reads_as_ungraded() -> None:
    """Not a failure: a run the grader never reached cannot testify."""
    meta = {"status": "done"}
    assert layout.trial_status(meta) == layout.UNGRADED
    assert layout.is_valid(meta) is False


def test_a_graded_run_resolves_to_passed_or_failed() -> None:
    assert layout.trial_status({"status": "done", "strict_success": True}) == "passed"
    assert (
        layout.trial_status({"status": "incomplete", "strict_success": False})
        == "failed"
    )


def test_every_status_the_module_can_return_is_declared() -> None:
    metas: list[dict[str, object]] = [
        {"status": "done", "strict_success": True},
        {"status": "done", "strict_success": False},
        {"status": "done"},
        {"status": "agent_timeout"},
        {"status": "produced_nothing"},
        {"status": "authentication_invalid"},
        {"status": "infrastructure_invalid"},
        {"status": "grader_error"},
    ]
    assert {layout.trial_status(m) for m in metas} == set(layout.TRIAL_STATUSES)


# ------------------------------------------------------------------ pass^k


def test_pass_hat_k_is_the_share_of_k_subsets_that_all_passed() -> None:
    """Three of ten passed. Of the C(10,3)=120 ways to draw three trials,
    exactly C(3,3)=1 draws three passes, so pass^3 is 1/120."""
    got = reliability.pass_hat_k(successes=3, trials=10, k=3)
    assert got is not None
    assert math.isclose(got.point, 1 / 120)


def test_pass_hat_k_is_one_when_every_trial_passed() -> None:
    got = reliability.pass_hat_k(successes=5, trials=5, k=3)
    assert got is not None
    assert got.point == 1.0
    assert got.high == 1.0
    assert got.low < 1.0, "five trials cannot buy certainty about ten"


def test_pass_hat_k_is_zero_below_k_successes() -> None:
    got = reliability.pass_hat_k(successes=2, trials=10, k=3)
    assert got is not None
    assert got.point == 0.0


def test_pass_hat_k_is_undefined_below_k_trials() -> None:
    """Two trials cannot estimate how often ten in a row succeed, and
    (s/n)**10 from them would dress a guess as a measurement."""
    assert reliability.pass_hat_k(successes=2, trials=2, k=3) is None
    assert reliability.pass_hat_k(successes=9, trials=9, k=10) is None


def test_pass_hat_k_falls_as_k_rises() -> None:
    estimates = [reliability.pass_hat_k(8, 10, k) for k in (1, 2, 3)]
    points = [e.point for e in estimates if e is not None]
    assert points == sorted(points, reverse=True)
    assert math.isclose(points[0], 0.8)


def test_the_wilson_interval_stays_inside_the_unit_range() -> None:
    """The textbook normal interval returns [1.0, 1.0] here, claiming a
    certainty five trials cannot buy."""
    low, high = reliability.wilson_interval(5, 5)
    assert 0.0 < low < 1.0
    assert high == 1.0
    assert math.isclose(low, 0.5655175, rel_tol=1e-6)


def test_the_interval_brackets_the_estimate() -> None:
    got = reliability.pass_hat_k(successes=7, trials=10, k=3)
    assert got is not None
    assert got.low <= got.point <= got.high


# ------------------------------------------------------------ group summary


def test_an_empty_run_stays_in_the_strict_success_denominator(
    tmp_path: Path,
) -> None:
    """The behaviour issue #29 exists to fix: one pass and one empty run is
    50% reliability, not 100%."""
    results = tmp_path / "results"
    _run(results, 1, {"strict_success": True})
    _run(results, 2, {"status": "agent_produced_nothing"})

    (group,) = reliability.summarise(layout.run_dirs(results))

    assert group.attempted == 2
    assert group.invalid == 0
    assert group.valid == 2
    assert group.passed == 1
    assert group.strict_success_rate == 0.5


def test_an_invalid_trial_leaves_the_denominator_but_not_the_report(
    tmp_path: Path,
) -> None:
    results = tmp_path / "results"
    _run(results, 1, {"strict_success": True})
    _run(results, 2, {"status": "authentication_invalid"})

    (group,) = reliability.summarise(layout.run_dirs(results))

    assert group.attempted == 2
    assert group.invalid == 1
    assert group.valid == 1
    assert group.strict_success_rate == 1.0
    assert group.invalid_rate == 0.5
    assert group.invalid_runs == [
        ("opus/20260802T120000Z-abc1234", "authentication_invalid")
    ]


def test_runs_are_not_pooled_across_a_changed_fingerprint(tmp_path: Path) -> None:
    """A repin, a regenerated golden, a spec edit, or a harness change each
    make a different experiment. Pooling them would report a rate no run
    ever measured."""
    results = tmp_path / "results"
    _run(results, 1, {"strict_success": True})
    _run(results, 2, {"strict_success": True, "pins_fingerprint": "p2"})
    _run(results, 3, {"strict_success": True, "golden_fingerprint": "g2"})
    _run(results, 4, {"strict_success": True, "harness_commit": "def5678"})
    _run(results, 5, {"strict_success": True, "spec_fingerprint": "s2"})

    groups = reliability.summarise(layout.run_dirs(results))

    assert len(groups) == 5
    assert all(g.attempted == 1 for g in groups)


def test_a_missing_pins_digest_does_not_pool_with_a_present_one(
    tmp_path: Path,
) -> None:
    """Absence is not evidence the data matched. A run made before the digest
    existed groups apart rather than being waved through."""
    results = tmp_path / "results"
    _run(results, 1, {"strict_success": True})
    _run(results, 2, {"strict_success": True, "pins_fingerprint": None})

    assert len(reliability.summarise(layout.run_dirs(results))) == 2


def test_the_budget_reports_the_ceiling_not_the_mean(tmp_path: Path) -> None:
    """A mean over trials that stopped early would understate what the
    passing ones were allowed to spend."""
    results = tmp_path / "results"
    _run(results, 1, {"strict_success": True, "attempts": 1, "turns": 40})
    _run(
        results,
        2,
        {
            "strict_success": False,
            "attempts": 3,
            "turns": 210,
            "duration_seconds": 1800.0,
            "imputed_cost_usd": 4.5,
        },
    )

    (group,) = reliability.summarise(layout.run_dirs(results))

    assert group.budget.max_attempts == 3
    assert group.budget.max_turns == 210
    assert group.budget.max_wall_seconds == 1800.0
    assert group.budget.total_cost_usd == 5.5


def test_pass_hat_k_over_a_summarised_group(tmp_path: Path) -> None:
    """Four passes and one failure: pass^3 is C(4,3)/C(5,3) = 4/10."""
    results = tmp_path / "results"
    for n in range(1, 5):
        _run(results, n, {"strict_success": True})
    _run(results, 5, {"strict_success": False})

    (group,) = reliability.summarise(layout.run_dirs(results))

    assert group.valid == 5
    assert group.passed == 4
    estimate = group.pass_hat(3)
    assert estimate is not None
    assert math.isclose(estimate.point, 0.4)
    assert group.pass_hat(10) is None
