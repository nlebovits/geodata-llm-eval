"""The acceptance criterion is only as good as the check that enforces it."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "harness"))

import pytest
import verify_regrade

QUESTIONS_YAML = """\
questions:
  - id: '24'
    stage: 5
    output:
      columns:
        - name: cod_imovel
          type: string
          description: flagged cadaster id
        - name: routed_tier
          type: string
          multivalued: true
          equivalents:
            - ["no_tier", "notier"]
            - ["unknown", "out_of_scope"]
          description: delivery tier(s), or a gap marker
"""


@pytest.fixture
def fixtures(tmp_path: Path) -> tuple[Path, Path]:
    """A questions.yaml and a golden whose header names q24's columns."""
    questions = tmp_path / "questions.yaml"
    questions.write_text(QUESTIONS_YAML, encoding="utf-8")
    golden = tmp_path / "golden"
    golden.mkdir()
    (golden / "q24.csv").write_text(
        "cod_imovel,routed_tier\nBR-1,no_tier\n", encoding="utf-8"
    )
    return questions, golden


def _run(root: Path, grades: dict, diffs: dict | None = None) -> None:
    """One run directory, the shape layout.run_dirs looks for."""
    d = root / "opus" / "20260101T000000Z-abc1234-deadbeef"
    d.mkdir(parents=True, exist_ok=True)
    (d / "meta.json").write_text("{}", encoding="utf-8")
    (d / "grades.json").write_text(json.dumps(grades), encoding="utf-8")
    (d / "diffs.json").write_text(json.dumps(diffs or {}), encoding="utf-8")


def _cell(golden: str, answer: str) -> dict:
    return {
        "kind": "cell",
        "row": 0,
        "column": "routed_tier",
        "golden": golden,
        "answer": answer,
    }


def _verify(
    tmp_path: Path,
    fixtures: tuple[Path, Path],
    before_grades: dict,
    after_grades: dict,
    before_diffs: dict | None = None,
    *,
    monkeypatch: pytest.MonkeyPatch,
) -> int:
    questions, golden = fixtures
    before, after = tmp_path / "before", tmp_path / "after"
    _run(before, before_grades, before_diffs)
    _run(after, after_grades)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "verify_regrade",
            str(before),
            str(after),
            "--questions",
            str(questions),
            "--golden",
            str(golden),
        ],
    )
    return verify_regrade.main()


def test_an_unchanged_grading_passes(tmp_path, fixtures, monkeypatch, capsys) -> None:
    code = _verify(
        tmp_path,
        fixtures,
        {"q24": "correct"},
        {"q24": "correct"},
        monkeypatch=monkeypatch,
    )
    assert code == 0
    assert "questions newly correct: 0" in capsys.readouterr().out


def test_a_lost_correct_answer_fails(tmp_path, fixtures, monkeypatch, capsys) -> None:
    """Criterion 1. No freedom is worth a question that used to be right."""
    code = _verify(
        tmp_path,
        fixtures,
        {"q24": "correct"},
        {"q24": "wrong"},
        monkeypatch=monkeypatch,
    )
    assert code == 1
    assert "correct answers regressed" in capsys.readouterr().out


def test_a_flip_explained_by_a_declared_synonym_passes(
    tmp_path, fixtures, monkeypatch, capsys
) -> None:
    """Criterion 2, satisfied. The only cell behind the earlier failure is a
    spelling the fixture declares, so the answer was always right."""
    code = _verify(
        tmp_path,
        fixtures,
        {"q24": "wrong"},
        {"q24": "correct"},
        {"q24": [_cell("no_tier", "notier")]},
        monkeypatch=monkeypatch,
    )
    out = capsys.readouterr().out
    assert code == 0
    assert "declared synonym" in out
    assert "answer 'notier' accepted for 'no_tier'" in out


def test_a_flip_explained_by_separator_order_passes(
    tmp_path, fixtures, monkeypatch, capsys
) -> None:
    code = _verify(
        tmp_path,
        fixtures,
        {"q24": "wrong"},
        {"q24": "correct"},
        {
            "q24": [
                _cell("intake_point|slaughter_point", "slaughter_point, intake_point")
            ]
        },
        monkeypatch=monkeypatch,
    )
    assert code == 0
    assert "separator or order" in capsys.readouterr().out


def test_a_substantive_flip_fails(tmp_path, fixtures, monkeypatch, capsys) -> None:
    """Criterion 2, violated. Routing cattle to the wrong tier is the answer
    being wrong, and no declared freedom covers it. This is the case a
    column-level check would have missed: the freed spellings and this cell
    sit in the same column."""
    code = _verify(
        tmp_path,
        fixtures,
        {"q24": "wrong"},
        {"q24": "correct"},
        {"q24": [_cell("no_tier", "notier"), _cell("intake_point", "slaughter_point")]},
        monkeypatch=monkeypatch,
    )
    out = capsys.readouterr().out
    assert code == 1
    assert "substantive difference" in out


def test_a_flip_from_a_shape_mismatch_cannot_be_verified(
    tmp_path, fixtures, monkeypatch, capsys
) -> None:
    """A shape record carries no cell values, so there is nothing to attribute
    the flip to. Reported rather than assumed harmless."""
    code = _verify(
        tmp_path,
        fixtures,
        {"q24": "wrong"},
        {"q24": "correct"},
        {"q24": [{"kind": "shape", "golden_rows": 3, "answer_rows": 4}]},
        monkeypatch=monkeypatch,
    )
    out = capsys.readouterr().out
    assert code == 1
    assert "no cell-level evidence" in out


def test_a_flip_with_no_recorded_diffs_cannot_be_verified(
    tmp_path, fixtures, monkeypatch, capsys
) -> None:
    """A missing or unparseable answer leaves no diffs, and a grader change
    cannot make an absent file appear. Something else moved."""
    code = _verify(
        tmp_path,
        fixtures,
        {"q24": "missing"},
        {"q24": "correct"},
        monkeypatch=monkeypatch,
    )
    assert code == 1
    assert "no cell-level evidence" in capsys.readouterr().out
