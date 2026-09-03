"""Exercise every custom Vale rule and lint generated report prose."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / ".vale.ini"

sys.path.insert(0, str(ROOT / "harness"))

import report

pytestmark = pytest.mark.skipif(
    shutil.which("vale") is None,
    reason="Vale is not installed; the prose CI job runs these tests",
)


def _words(count: int) -> str:
    return " ".join(["word"] * count) + "."


CASES = {
    "Geodata-Docs.Sentence26": (_words(27), _words(26)),
    "Geodata-Mechanics.Ellipsis": ("Wait...", "Wait."),
    "Geodata-Mechanics.EmDash": ("word—word", "word — word"),
    "Geodata-Mechanics.EmDashDensity": (
        "a — b — c — d — e",
        "a — b — c — d",
    ),
    "Geodata-Mechanics.Headings": ("# Bad Heading Here", "# Good heading"),
    "Geodata-Mechanics.Oxford": (
        "Choose red, blue and green options.",
        "Choose red, blue, and green options.",
    ),
    "Geodata-Mechanics.Quotes": ("“quoted”", '"quoted"'),
    "Geodata-Terms.Casing": ("Mapbiomas data.", "MapBiomas data."),
    "Geodata-Terms.Hype": ("A seamless tool.", "A direct tool."),
    "Geodata-Voice.ChatbotResidue": (
        "I hope this helps.",
        "The command prints the result.",
    ),
    "Geodata-Voice.ClosingTail": (
        "In conclusion, publish the files.",
        "Publish the files.",
    ),
    "Geodata-Voice.ConsequenceCadence": (
        "It is indexed, so people can search. It is open, so people can query.",
        "It is indexed, so people can search. People query the open files.",
    ),
    "Geodata-Voice.ContrastSlogan": (
        "It is not just a report, but a complete transformation.",
        "The report includes the measured results.",
    ),
    "Geodata-Voice.DramaticColon": (
        "Remember: this changes the final result.",
        "Remember that this changes the result.",
    ),
    "Geodata-Voice.Filler": ("It basically works.", "It works."),
    "Geodata-Voice.Passive": (
        "The file was written yesterday.",
        "The publisher wrote the file yesterday.",
    ),
    "Geodata-Voice.SoYouCan": (
        "It is open, so you can read it. It is indexed, so you can find it.",
        "It is open, so you can read it. The index makes it searchable.",
    ),
    "Geodata-Voice.StockTransitions": (
        "In today's landscape, catalogs matter.",
        "Catalogs make distributed data searchable.",
    ),
}


def _checks(tmp_path: Path, text: str, level: str = "suggestion") -> set[str]:
    target = tmp_path / "page.md"
    target.write_text(text + "\n", encoding="utf-8")
    completed = subprocess.run(
        [
            "vale",
            "--config",
            str(CONFIG),
            "--minAlertLevel",
            level,
            "--output",
            "JSON",
            target.name,
        ],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode in {0, 1}, completed.stderr
    parsed = json.loads(completed.stdout or "{}")
    return {alert["Check"] for alerts in parsed.values() for alert in alerts}


def test_rule_inventory_matches_the_cases() -> None:
    rules = {
        f"{path.parent.name}.{path.stem}"
        for path in (ROOT / "styles").glob("Geodata-*/*.yml")
    }
    assert rules == set(CASES)


def test_filler_allows_a_concrete_not_just_contrast(tmp_path: Path) -> None:
    assert "Geodata-Voice.Filler" not in _checks(
        tmp_path, "The scanner checks content, not just paths."
    )


def test_dramatic_colon_does_not_treat_a_stage_label_as_prose(
    tmp_path: Path,
) -> None:
    assert "Geodata-Voice.DramaticColon" not in _checks(
        tmp_path,
        "**Stage 3: field matching.** Apply the matching rule.",
    )


@pytest.mark.parametrize(("check", "examples"), CASES.items())
def test_each_rule_reports_bad_and_accepts_good(
    tmp_path: Path, check: str, examples: tuple[str, str]
) -> None:
    bad, good = examples
    assert check in _checks(tmp_path, bad)
    assert check not in _checks(tmp_path, good)


def _session(results: Path, run_id: str, *, status: str = "done") -> None:
    run_dir = results / "opus" / run_id
    run_dir.mkdir(parents=True)
    (run_dir / "grades.json").write_text(
        json.dumps({"q01": "correct", "q02": "wrong"}), encoding="utf-8"
    )
    (run_dir / "meta.json").write_text(
        json.dumps(
            {
                "model": "opus",
                "model_id": "claude-opus",
                "run_id": run_id,
                "status": status,
                "strict_success": status == "done",
                "imputed_cost_usd": 1.25,
                "duration_seconds": 600,
                "slow_tool_seconds": 120,
                "timed_out_tool_calls": 1,
                "turns": 12,
                "max_attempts": 3,
                "attempts_used": 2,
                "max_wall_seconds": 3600,
                "spec_fingerprint": "spec123",
                "golden_fingerprint": "gold123",
                "graded_against": "gold123",
                "pins_fingerprint": "pins123",
                "harness_commit": "abc1234",
                "input_mode": "csv",
            }
        ),
        encoding="utf-8",
    )


def test_generated_report_has_no_error_level_prose_findings(tmp_path: Path) -> None:
    results = tmp_path / "results"
    _session(results, "20260903T100000Z-abc1234")
    _session(
        results,
        "20260903T110000Z-abc1234",
        status="authentication_invalid",
    )
    (results / "consistency.json").write_text(
        json.dumps(
            {
                "n_runs": 2,
                "flag_jaccard": 0.8,
                "contact_agreement": 0.7,
                "ranking_tau": 0.6,
                "contact_kappa": 0.5,
                "unstable_cadasters": ["GO-1"],
                "oracle": {"flag_jaccard": 0.9, "contact_agreement": 0.8},
            }
        ),
        encoding="utf-8",
    )
    questions = [
        {"id": "01", "stage": 1, "depends_on": []},
        {"id": "02", "stage": 2, "depends_on": ["01"]},
    ]
    output = tmp_path / "generated.md"
    report.write_report_md(report.load_sessions(results), output, questions, results)

    assert not _checks(tmp_path, output.read_text(encoding="utf-8"), "error")
