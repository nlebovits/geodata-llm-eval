"""Sweep ordering, failure containment, and how arms are compared.

A sweep is hours long and costs tens of dollars, so the properties worth
pinning are the ones that make a sweep's output trustworthy after it has
already been paid for: that the arms were not confounded with the network,
that one dead container did not silently shorten an arm, and that two
different specs were never averaged together.
"""

import json
import sys
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
HARNESS = REPO_ROOT / "harness"
sys.path.insert(0, str(HARNESS))

import sweep

CONFIG = {
    "baseline": "full",
    "arms": {
        "full": {"why": "all", "ops": []},
        "a": {"why": "a", "ops": []},
        "b": {"why": "b", "ops": []},
    },
}


def test_the_sweep_interleaves_arms_rather_than_blocking_them() -> None:
    """run.py samples source.coop throughput at both ends of every session
    because it moves by an order of magnitude with the route. Running one
    arm's passes back to back puts that drift between arms, where it is
    indistinguishable from the effect being measured."""
    order = [arm for _p, arm in sweep.plan(CONFIG, [], passes=2, order="interleaved")]
    assert order == ["full", "a", "b", "full", "a", "b"]


def test_blocked_order_exists_for_debugging_but_is_not_the_default() -> None:
    order = [arm for _p, arm in sweep.plan(CONFIG, [], passes=2, order="blocked")]
    assert order == ["full", "full", "a", "a", "b", "b"]


def test_an_arm_subset_runs_only_those_arms() -> None:
    order = [
        arm for _p, arm in sweep.plan(CONFIG, ["b"], passes=2, order="interleaved")
    ]
    assert order == ["b", "b"]


def test_a_failing_session_does_not_abort_the_rest_of_the_sweep(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Hour three of a four-hour sweep dying on a transient Docker error must
    not throw away the two hours before it."""
    seen = []

    def flaky(_model: str, _dry: bool, arm: str = "", **_kw: Any) -> None:
        seen.append(arm)
        if arm == "a":
            raise RuntimeError("container died")

    monkeypatch.setattr(sweep.runner, "run_session", flaky)
    failures = sweep.sweep("opus", CONFIG, [], passes=2, order="interleaved")

    assert seen == ["full", "a", "b", "full", "a", "b"], "every step still ran"
    assert failures == {"a": 2}


# --- report ------------------------------------------------------------------


def make_run(
    results: Path,
    model: str,
    run_id: str,
    arm: str,
    spec: str,
    grades: dict[str, str],
    **meta_extra: Any,
) -> Path:
    run_dir = results / model / run_id
    run_dir.mkdir(parents=True)
    meta = {
        "model": model,
        "run_id": run_id,
        "status": "done",
        "spec_fingerprint": spec,
        "ablation": {"arm": arm, "why": f"why {arm}", "ops": [], "receipt": {}},
    }
    meta.update(meta_extra)
    (run_dir / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    (run_dir / "grades.json").write_text(json.dumps(grades), encoding="utf-8")
    return run_dir


QUESTIONS = [{"id": "01", "stage": 1}, {"id": "02", "stage": 1}]


def test_two_arms_are_reported_as_separate_rows(tmp_path: Path) -> None:
    results = tmp_path / "results"
    make_run(
        results,
        "opus",
        "20260101T000000Z-aaa",
        "full",
        "1111",
        {"q01": "correct", "q02": "correct"},
    )
    make_run(
        results,
        "opus",
        "20260101T000100Z-aaa",
        "no-coops",
        "2222",
        {"q01": "correct", "q02": "wrong"},
    )

    rows, _excluded = sweep.arm_rows(results, "opus", QUESTIONS)
    by_arm = {r["arm"]: r for r in rows}
    assert set(by_arm) == {"full", "no-coops"}
    assert by_arm["full"]["mean"] == 1.0
    assert by_arm["no-coops"]["mean"] == 0.5


def test_runs_in_one_arm_with_different_specs_are_not_pooled(tmp_path: Path) -> None:
    """Edit a policy mid-sweep and both days answer to the same arm name while
    measuring different specs. Averaging them would report one number for two
    experiments."""
    results = tmp_path / "results"
    make_run(
        results,
        "opus",
        "20260101T000000Z-aaa",
        "no-coops",
        "1111",
        {"q01": "correct", "q02": "correct"},
    )
    make_run(
        results,
        "opus",
        "20260101T000100Z-aaa",
        "no-coops",
        "9999",
        {"q01": "wrong", "q02": "wrong"},
    )

    rows, _excluded = sweep.arm_rows(results, "opus", QUESTIONS)
    assert len(rows) == 2, "one arm, two specs, two rows"
    assert {r["spec"] for r in rows} == {"1111", "9999"}

    lines = sweep.report(results, tmp_path / "none.yaml", CONFIG)
    assert any("saw 2 different specs" in line for line in lines)


def test_ablation_rows_are_separated_by_agent_configuration(
    tmp_path: Path,
) -> None:
    results = tmp_path / "results"
    for run_id, config, grade in (
        ("20260101T000000Z-aaa", "config-one", "correct"),
        ("20260101T000100Z-aaa", "config-two", "wrong"),
    ):
        make_run(
            results,
            "opus",
            run_id,
            "full",
            "1111",
            {"q01": grade},
            agent="claude",
            agent_config_fingerprint=config,
        )

    rows, _excluded = sweep.arm_rows(results, "opus", QUESTIONS)

    assert len(rows) == 2
    assert {row["runs"] for row in rows} == {1}
    assert len(sweep._rows_by_context(rows)) == 2


def test_a_regraded_run_is_excluded_and_named(tmp_path: Path) -> None:
    """A run scored against goldens it never saw is a fact about the fixtures,
    not about the withheld text."""
    results = tmp_path / "results"
    make_run(
        results,
        "opus",
        "20260101T000000Z-aaa",
        "full",
        "1111",
        {"q01": "correct"},
        golden_fingerprint="old",
        graded_against="new",
    )

    rows, excluded = sweep.arm_rows(results, "opus", QUESTIONS)
    assert rows == []
    assert [why for _dir, why in excluded] == ["regraded"]


def test_an_unscored_run_is_excluded(tmp_path: Path) -> None:
    """A run that wrote no answers has no per-arm mean to contribute. It is
    still a failed trial, and harness/reliability.py counts it as one."""
    results = tmp_path / "results"
    make_run(
        results,
        "opus",
        "20260101T000000Z-aaa",
        "full",
        "1111",
        {"q01": "correct"},
        status="agent_produced_nothing",
    )
    rows, excluded = sweep.arm_rows(results, "opus", QUESTIONS)
    assert rows == [] and excluded


def test_a_graded_failure_still_carries_its_arm_mean(tmp_path: Path) -> None:
    """`failed` is a trial verdict, not a reason to drop a run from the
    diagnostics: a run that answered every question and got some wrong is
    exactly what a per-arm mean is measuring."""
    results = tmp_path / "results"
    make_run(
        results,
        "opus",
        "20260101T000000Z-aaa",
        "full",
        "1111",
        {"q01": "correct"},
        status="incomplete",
    )
    rows, excluded = sweep.arm_rows(results, "opus", QUESTIONS)
    assert [r["runs"] for r in rows] == [1] and not excluded


def test_a_run_predating_the_harness_groups_as_the_full_spec(tmp_path: Path) -> None:
    """Every run already on disk saw the whole spec, so reading a missing
    ablation block as `full` is accurate rather than a guess."""
    results = tmp_path / "results"
    run_dir = results / "opus" / "20260101T000000Z-aaa"
    run_dir.mkdir(parents=True)
    (run_dir / "meta.json").write_text(json.dumps({"status": "done"}), encoding="utf-8")
    (run_dir / "grades.json").write_text(
        json.dumps({"q01": "correct"}), encoding="utf-8"
    )

    rows, _excluded = sweep.arm_rows(results, "opus", QUESTIONS)
    assert [r["arm"] for r in rows] == ["full"]
    assert rows[0]["spec"] == "-", "no fingerprint, so it cannot claim one"


def test_fingerprinted_and_legacy_runs_coexist_in_one_results_tree(
    tmp_path: Path,
) -> None:
    """The first real sweep hit this: a tree holding runs from before the
    harness and runs from after sorted a spec digest against None and died
    after the containers had already been paid for. Every tree has both for
    as long as the older runs are kept."""
    results = tmp_path / "results"
    legacy = results / "opus" / "20260101T000000Z-old"
    legacy.mkdir(parents=True)
    (legacy / "meta.json").write_text(json.dumps({"status": "done"}), encoding="utf-8")
    (legacy / "grades.json").write_text(
        json.dumps({"q01": "correct"}), encoding="utf-8"
    )
    make_run(
        results, "opus", "20260101T000100Z-new", "full", "1111", {"q01": "correct"}
    )
    make_run(
        results, "opus", "20260101T000200Z-new", "no-coops", "2222", {"q01": "wrong"}
    )

    rows, _excluded = sweep.arm_rows(results, "opus", QUESTIONS)
    assert {(r["arm"], r["spec"]) for r in rows} == {
        ("full", "-"),
        ("full", "1111"),
        ("no-coops", "2222"),
    }
    assert sweep.report(results, tmp_path / "none.yaml", CONFIG)


def test_the_question_table_reports_a_delta_against_the_baseline(
    tmp_path: Path,
) -> None:
    results = tmp_path / "results"
    make_run(
        results,
        "opus",
        "20260101T000000Z-aaa",
        "full",
        "1111",
        {"q01": "correct", "q02": "correct"},
    )
    make_run(
        results,
        "opus",
        "20260101T000100Z-aaa",
        "no-coops",
        "2222",
        {"q01": "correct", "q02": "wrong"},
    )

    rows, _ = sweep.arm_rows(results, "opus", QUESTIONS)
    lines = sweep.question_table(rows, "full", QUESTIONS)
    body = "\n".join(lines)
    assert "q02" in body and "-100" in body
    assert "q01" not in body, "a question every arm agrees on is hidden"
    assert "1 questions scored the same" in body


def test_the_question_table_says_so_when_the_baseline_has_no_runs(
    tmp_path: Path,
) -> None:
    """Better than silently baselining against whichever arm sorts first."""
    results = tmp_path / "results"
    make_run(
        results, "opus", "20260101T000000Z-aaa", "no-coops", "2222", {"q01": "correct"}
    )
    rows, _ = sweep.arm_rows(results, "opus", QUESTIONS)
    body = "\n".join(sweep.question_table(rows, "full", QUESTIONS))
    assert "no runs for the baseline" in body


def test_the_dry_run_receipt_names_every_arm_and_what_it_removes() -> None:
    receipts = {"full": {}, "no-coops": {"policies/COOPS.md": 150}}
    config = {
        "baseline": "full",
        "arms": {
            "full": {"why": "a", "ops": []},
            "no-coops": {"why": "b", "ops": [{"drop": "policies/COOPS.md"}]},
        },
    }
    body = "\n".join(sweep.describe(config, receipts, [], 3, "opus"))
    assert "2 arms x 3 passes = 6 sessions" in body
    assert "no operations" in body
    assert "-150 lines" in body.replace("- 150", "-150")


@pytest.mark.parametrize("model", ["opus", "sonnet"])
def test_arms_are_never_compared_across_models(tmp_path: Path, model: str) -> None:
    """An ablation's effect on haiku and on opus are different measurements."""
    results = tmp_path / "results"
    make_run(
        results, "opus", "20260101T000000Z-aaa", "full", "1111", {"q01": "correct"}
    )
    make_run(
        results, "sonnet", "20260101T000100Z-aaa", "full", "1111", {"q01": "wrong"}
    )
    rows, _ = sweep.arm_rows(results, model, QUESTIONS)
    assert len(rows) == 1
    assert rows[0]["mean"] == (1.0 if model == "opus" else 0.0)


def _ablations(tmp_path: Path) -> Path:
    """An ablation config whose arms remove nothing, so validation passes
    against the real policy documents."""
    path = tmp_path / "ablations.yaml"
    path.write_text(
        "baseline: full\narms:\n  full:\n    why: the whole spec\n    ops: []\n",
        encoding="utf-8",
    )
    return path


def test_main_reports_the_runs_already_on_disk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """--report is how a sweep is read after the fact, without paying for
    it again."""
    results = tmp_path / "results"
    make_run(
        results, "opus", "20260101T000000Z-aaa", "full", "1111", {"q01": "correct"}
    )
    questions = tmp_path / "questions.yaml"
    questions.write_text("questions:\n  - id: q01\n    stage: 1\n", encoding="utf-8")
    monkeypatch.setattr(sweep, "REPORT", tmp_path / "ablation.md")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "sweep.py",
            "--report",
            "--results",
            str(results),
            "--questions",
            str(questions),
            "--ablations",
            str(_ablations(tmp_path)),
        ],
    )

    assert sweep.main() == 0

    assert (tmp_path / "ablation.md").exists()
    assert "wrote" in capsys.readouterr().out


def test_main_validates_every_arm_before_the_first_container(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A heading reworded since the arm was written should cost a second,
    not four hours of containers."""
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "sweep.py",
            "--dry-run",
            "--model",
            "haiku",
            "--ablations",
            str(_ablations(tmp_path)),
        ],
    )

    assert sweep.main() == 0
    assert "sweep: haiku" in capsys.readouterr().out


def test_main_rejects_an_arm_that_does_not_exist(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "sweep.py",
            "--model",
            "haiku",
            "--arms",
            "no-such-arm",
            "--ablations",
            str(_ablations(tmp_path)),
        ],
    )

    assert sweep.main() == 2
    assert "unknown arms" in capsys.readouterr().err


def test_main_rejects_an_unreadable_ablation_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    bad = tmp_path / "bad.yaml"
    bad.write_text("arms: not-a-mapping\n", encoding="utf-8")
    monkeypatch.setattr(
        sys, "argv", ["sweep.py", "--model", "haiku", "--ablations", str(bad)]
    )

    assert sweep.main() == 2
    assert "ablation:" in capsys.readouterr().err


def test_main_needs_a_model_unless_it_is_only_reporting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        sys, "argv", ["sweep.py", "--ablations", str(_ablations(tmp_path))]
    )

    with pytest.raises(SystemExit):
        sweep.main()


def test_main_grades_and_reports_after_a_sweep(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The sweep ends by grading what it produced. Leaving that to the
    operator is how a finished sweep sits ungraded overnight."""
    results = tmp_path / "results"
    make_run(
        results, "haiku", "20260101T000000Z-aaa", "full", "1111", {"q01": "correct"}
    )
    questions = tmp_path / "questions.yaml"
    questions.write_text("questions:\n  - id: q01\n    stage: 1\n", encoding="utf-8")
    graded = []
    monkeypatch.setattr(sweep, "sweep", lambda *a, **k: {})
    monkeypatch.setattr(sweep.subprocess, "run", lambda cmd, **kw: graded.append(cmd))
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "sweep.py",
            "--model",
            "haiku",
            "--passes",
            "1",
            "--results",
            str(results),
            "--questions",
            str(questions),
            "--ablations",
            str(_ablations(tmp_path)),
        ],
    )

    sweep.main()

    assert graded, "the sweep did not run the grader"
    assert graded[0][1].endswith("grade.py")


def test_a_failed_arm_is_named_on_stderr(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """One dead container must not silently shorten an arm."""
    results = tmp_path / "results"
    questions = tmp_path / "questions.yaml"
    questions.write_text("questions: []\n", encoding="utf-8")
    monkeypatch.setattr(sweep, "sweep", lambda *a, **k: {"full": 2})
    monkeypatch.setattr(sweep.subprocess, "run", lambda cmd, **kw: None)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "sweep.py",
            "--model",
            "haiku",
            "--passes",
            "1",
            "--results",
            str(results),
            "--questions",
            str(questions),
            "--ablations",
            str(_ablations(tmp_path)),
        ],
    )

    sweep.main()

    assert "failed sessions by arm: {'full': 2}" in capsys.readouterr().err
