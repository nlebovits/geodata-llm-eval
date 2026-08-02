"""Report renders the stage grid and consistency panel from synthetic sessions.
No network, no real model runs."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "harness"))

import report

QUESTIONS = [
    {"id": "01", "stage": 1, "depends_on": []},
    {"id": "02", "stage": 2, "depends_on": ["01"]},
    {"id": "03", "stage": 2, "depends_on": ["02"]},
]


def _session(tmp, model, pass_n, grades, cost, runtime=None):
    run_id = f"2026072{pass_n}T120000Z-abc1234"
    d = tmp / model / run_id
    (d / "answers").mkdir(parents=True)
    (d / "grades.json").write_text(json.dumps(grades))
    meta = {
        "model": model,
        "run_id": run_id,
        "label": "",
        "status": "done",
        "imputed_cost_usd": cost,
        "turns": 12,
    }
    meta.update(runtime or {})
    (d / "meta.json").write_text(json.dumps(meta))


def test_stage_grid_and_consistency_render(tmp_path):
    results = tmp_path / "results"
    _session(
        results, "sonnet", 1, {"q01": "correct", "q02": "correct", "q03": "wrong"}, 0.5
    )
    _session(
        results, "sonnet", 2, {"q01": "correct", "q02": "wrong", "q03": "wrong"}, 0.6
    )
    (results / "consistency.json").write_text(
        json.dumps(
            {
                "n_runs": 2,
                "flag_jaccard": 1.0,
                "contact_agreement": 0.83,
                "ranking_tau": 0.9,
                "contact_kappa": 0.7,
                "unstable_cadasters": ["GO-1", "GO-2"],
                "oracle": {"flag_jaccard": 0.4, "contact_agreement": 0.5},
            }
        )
    )

    sessions = report.load_sessions(results)
    out = results / "report.md"
    report.write_report_md(sessions, out, QUESTIONS, results)
    text = out.read_text()

    assert "Accuracy by workflow stage" in text
    assert "S1" in text and "S2" in text
    assert "Cross-run consistency" in text
    assert "Unstable cadasters" in text
    assert "GO-1" in text
    # oracle deviation column is present
    assert "0.400" in text


def test_runtime_breakdown_is_reported_next_to_accuracy(tmp_path):
    """A run that spent 73% of its wall clock waiting on source.coop scores
    worse for reasons that are not the model's; the report has to say so."""
    results = tmp_path / "results"
    _session(
        results,
        "opus",
        1,
        {"q01": "correct", "q02": "wrong", "q03": "near_miss"},
        1.2,
        runtime={
            "duration_seconds": 1858.2,
            "slow_tool_calls": 21,
            "slow_tool_seconds": 1350.0,
            "timed_out_tool_calls": 4,
        },
    )
    sessions = report.load_sessions(results)
    assert sessions[0]["near_miss"] == 1
    assert round(sessions[0]["slow_tool_share"], 2) == 0.73

    out = results / "report.md"
    report.write_report_md(sessions, out, QUESTIONS, results)
    text = out.read_text()
    assert "## Runtime" in text
    assert "73%" in text  # share of wall clock inside slow calls
    assert "31m" in text  # wall clock
    assert "| 4 |" in text  # tool timeouts

    report.write_summary_csv(sessions, results / "summary.csv")
    header, row = results.joinpath("summary.csv").read_text().splitlines()[:2]
    assert "timed_out_tool_calls" in header and "near_miss" in header
    assert row.endswith("1858.2,1350.0,4")


def test_report_without_consistency_file_still_renders(tmp_path):
    results = tmp_path / "results"
    _session(
        results, "haiku", 1, {"q01": "correct", "q02": "correct", "q03": "correct"}, 0.1
    )
    sessions = report.load_sessions(results)
    out = results / "report.md"
    report.write_report_md(sessions, out, QUESTIONS, results)
    text = out.read_text()
    assert "Accuracy by workflow stage" in text
    assert "Cross-run consistency" not in text  # no consistency.json present


def test_a_run_that_wrote_nothing_stays_out_of_the_averages(tmp_path):
    """A session that ended without attempting the questions is not thirty
    wrong answers. Scoring it as one blamed the model for a run that never
    happened and dragged every average it appeared in."""
    results = tmp_path / "results"
    _session(
        results, "opus", 1, {"q01": "correct", "q02": "correct", "q03": "correct"}, 1.0
    )
    _session(results, "opus", 2, {}, 2.0)
    aborted = next(d for d in (results / "opus").iterdir() if "20260722" in d.name)
    meta = json.loads((aborted / "meta.json").read_text())
    meta["status"] = "produced_nothing"
    (aborted / "meta.json").write_text(json.dumps(meta))

    sessions = report.load_sessions(results)

    assert [s["accuracy"] for s in sessions] == [1.0]
    assert sessions[0]["run_id"].startswith("20260721T")


def test_sessions_carry_their_run_id_and_label(tmp_path):
    results = tmp_path / "results"
    _session(results, "opus", 1, {"q01": "correct"}, 1.0)
    (sess,) = report.load_sessions(results)
    assert sess["run_id"] == "20260721T120000Z-abc1234"
    assert sess["label"] == ""


def test_main_writes_every_artifact(tmp_path, monkeypatch, capsys):
    """One command produces the three files the write-up is built from."""
    results = tmp_path / "results"
    _session(results, "sonnet", 1, {"q01": "correct"}, 1.5)
    questions = tmp_path / "questions.yaml"
    questions.write_text("q01:\n  stage: 1\n", encoding="utf-8")
    monkeypatch.setattr(
        sys,
        "argv",
        ["report.py", "--results", str(results), "--questions", str(questions)],
    )

    assert report.main() == 0

    assert (results / "summary.csv").exists()
    assert (results / "report.md").exists()
    assert (results / "pareto.png").exists()
    assert "wrote summary.csv" in capsys.readouterr().out


def test_main_refuses_an_ungraded_tree(tmp_path, monkeypatch, capsys):
    """Reporting on nothing would print an empty table rather than say the
    grader has not run."""
    monkeypatch.setattr(
        sys, "argv", ["report.py", "--results", str(tmp_path / "empty")]
    )

    assert report.main() == 1
    assert "run grade.py first" in capsys.readouterr().err
