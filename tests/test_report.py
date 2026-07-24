"""Report renders the stage grid and consistency panel from synthetic sessions.
No network, no real model runs."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "harness"))

import report  # noqa: E402

QUESTIONS = [
    {"id": "01", "stage": 1, "depends_on": []},
    {"id": "02", "stage": 2, "depends_on": ["01"]},
    {"id": "03", "stage": 2, "depends_on": ["02"]},
]


def _session(tmp, model, pass_n, grades, cost):
    d = tmp / model / f"pass-{pass_n}"
    (d / "answers").mkdir(parents=True)
    (d / "grades.json").write_text(json.dumps(grades))
    (d / "meta.json").write_text(json.dumps({
        "model": model, "pass": pass_n, "imputed_cost_usd": cost, "turns": 12}))


def test_stage_grid_and_consistency_render(tmp_path):
    results = tmp_path / "results"
    _session(results, "sonnet", 1,
             {"q01": "correct", "q02": "correct", "q03": "wrong"}, 0.5)
    _session(results, "sonnet", 2,
             {"q01": "correct", "q02": "wrong", "q03": "wrong"}, 0.6)
    (results / "consistency.json").write_text(json.dumps({
        "n_runs": 2, "flag_jaccard": 1.0, "contact_agreement": 0.83,
        "ranking_tau": 0.9, "contact_kappa": 0.7,
        "unstable_cadasters": ["GO-1", "GO-2"],
        "oracle": {"flag_jaccard": 0.4, "contact_agreement": 0.5},
    }))

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


def test_report_without_consistency_file_still_renders(tmp_path):
    results = tmp_path / "results"
    _session(results, "haiku", 1,
             {"q01": "correct", "q02": "correct", "q03": "correct"}, 0.1)
    sessions = report.load_sessions(results)
    out = results / "report.md"
    report.write_report_md(sessions, out, QUESTIONS, results)
    text = out.read_text()
    assert "Accuracy by workflow stage" in text
    assert "Cross-run consistency" not in text  # no consistency.json present
