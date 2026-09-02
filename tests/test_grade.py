"""Comparator behavior is the grading contract — pin it with tests."""

import csv
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
GOLDEN_DIR = REPO / "fixtures" / "golden"

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "harness"))

import pytest
from grade import (
    CORRECT,
    MISSING,
    NEAR_MISS,
    UNPARSEABLE,
    WRONG,
    compare,
    diff_summary,
    diff_table,
    evaluate_question,
    golden_fingerprint,
    grade_question,
    grade_session,
    load_table,
    stage_summary,
    values_match,
)
from layout import regraded


def write(tmp_path: Path, name: str, text: str) -> Path:
    p = tmp_path / name
    p.write_text(text)
    return p


def test_the_fingerprint_tracks_the_manifest(tmp_path: Path) -> None:
    """Regenerating the oracle changes the fixtures, and a score is only
    comparable to another score computed against the same ones."""
    (tmp_path / "SHA256SUMS").write_text("abc  q01.csv\n")
    before = golden_fingerprint(tmp_path)
    (tmp_path / "SHA256SUMS").write_text("def  q01.csv\n")
    assert before != golden_fingerprint(tmp_path)
    assert golden_fingerprint(tmp_path / "absent") is None


def test_a_run_scored_against_later_fixtures_is_flagged() -> None:
    """The axis-order fix regenerated seven goldens and moved one Opus run
    from 22/30 to 29/30. A score that moved because the fixtures moved has to
    say so, or it reads as a model that improved on its own."""
    assert regraded({"golden_fingerprint": "aaa", "graded_against": "bbb"})
    assert not regraded({"golden_fingerprint": "aaa", "graded_against": "aaa"})


def test_a_run_predating_the_fingerprint_is_not_called_regraded() -> None:
    """Absence of a digest is missing evidence, not evidence of a mismatch."""
    assert not regraded({"graded_against": "bbb"})
    assert not regraded({"golden_fingerprint": "aaa"})
    assert not regraded({})


GOLDEN = "region,count\nsanta_cruz,1523\nbeni,847\n"


class TestCompare:
    def test_exact_match(self) -> None:
        a = [["santa_cruz", 1523], ["beni", 847]]
        assert compare(a, a)

    def test_row_order_ignored(self) -> None:
        golden = [["santa_cruz", 1523], ["beni", 847]]
        answer = [["beni", 847], ["santa_cruz", 1523]]
        assert compare(answer, golden)

    def test_column_order_ignored(self) -> None:
        golden = [["santa_cruz", 1523], ["beni", 847]]
        answer = [[1523, "santa_cruz"], [847, "beni"]]
        assert compare(answer, golden)

    def test_float_within_tolerance(self) -> None:
        golden = [[152.38912661]]
        answer = [[152.38912659]]
        assert compare(answer, golden)

    def test_float_relative_tolerance_boundary(self) -> None:
        golden = [[1000.0]]
        assert compare([[1000.9]], golden)  # 0.09% off
        assert not compare([[1002.0]], golden)  # 0.2% off

    def test_integer_count_must_be_exact(self) -> None:
        assert not compare([[1524]], [[1523]])

    def test_string_must_match_apart_from_case(self) -> None:
        assert not compare([["santa cruz"]], [["santa_cruz"]])
        assert compare([["Santa_Cruz"]], [["santa_cruz"]])

    def test_row_count_mismatch(self) -> None:
        assert not compare([["beni", 847]], [["santa_cruz", 1523], ["beni", 847]])

    def test_column_count_mismatch(self) -> None:
        assert not compare([["beni", 847, 1.0]], [["beni", 847]])

    def test_duplicate_rows_are_multiset(self) -> None:
        golden = [[1], [1], [2]]
        assert compare([[2], [1], [1]], golden)
        assert not compare([[1], [2], [2]], golden)

    def test_wrong_value(self) -> None:
        golden = [["santa_cruz", 1523], ["beni", 847]]
        answer = [["santa_cruz", 1523], ["beni", 999]]
        assert not compare(answer, golden)

    def test_near_zero_absolute_tolerance(self) -> None:
        assert compare([[1e-10]], [[0.0]])


class TestLoadTable:
    def test_parses_types(self, tmp_path: Path) -> None:
        p = write(tmp_path, "a.csv", "id,share\n42,0.15\n")
        assert load_table(p) == [[42, 0.15]]

    def test_header_skipped(self, tmp_path: Path) -> None:
        p = write(tmp_path, "a.csv", "whatever,names\n1,2\n")
        assert load_table(p) == [[1, 2]]

    def test_empty_file(self, tmp_path: Path) -> None:
        assert load_table(write(tmp_path, "a.csv", "")) is None

    def test_header_only(self, tmp_path: Path) -> None:
        assert load_table(write(tmp_path, "a.csv", "count\n")) is None

    def test_ragged_rows(self, tmp_path: Path) -> None:
        p = write(tmp_path, "a.csv", "a,b\n1,2\n3\n")
        assert load_table(p) is None


class TestGradeQuestion:
    def test_correct(self, tmp_path: Path) -> None:
        golden = write(tmp_path, "g.csv", GOLDEN)
        answer = write(tmp_path, "a.csv", "n,region\n847,beni\n1523,santa_cruz\n")
        assert grade_question(answer, golden) == CORRECT

    def test_wrong(self, tmp_path: Path) -> None:
        golden = write(tmp_path, "g.csv", GOLDEN)
        answer = write(tmp_path, "a.csv", "region,count\nsanta_cruz,1\nbeni,2\n")
        assert grade_question(answer, golden) == WRONG

    def test_missing(self, tmp_path: Path) -> None:
        golden = write(tmp_path, "g.csv", GOLDEN)
        assert grade_question(tmp_path / "nope.csv", golden) == MISSING

    def test_unparseable(self, tmp_path: Path) -> None:
        golden = write(tmp_path, "g.csv", GOLDEN)
        answer = write(tmp_path, "a.csv", "")
        assert grade_question(answer, golden) == UNPARSEABLE


# --- geometry tolerance ----------------------------------------------------


def test_geometry_int_slack_allows_small_boundary_difference() -> None:
    # 502 vs 500 matched fields: two boundary fields either way, within slack.
    assert values_match(502, 500, geometry=True)
    assert not values_match(502, 500, geometry=False)


def test_geometry_int_slack_uses_one_percent_on_large_counts() -> None:
    # 1% of 10000 is 100, so 10090 passes under geometry, fails under strict.
    assert values_match(10090, 10000, geometry=True)
    assert not values_match(10090, 10000, geometry=False)


def test_geometry_float_tolerance_is_one_percent() -> None:
    assert values_match(101.0, 100.0, geometry=True)  # 1% off, ok
    assert not values_match(102.0, 100.0, geometry=True)  # 2% off, fails
    assert not values_match(100.5, 100.0, geometry=False)  # strict rejects 0.5%


def test_geometry_flag_threads_through_compare() -> None:
    golden = [[500]]
    answer = [[502]]
    assert compare(answer, golden, geometry=True)
    assert not compare(answer, golden, geometry=False)


# --- dependency-aware stage summary ----------------------------------------

QUESTIONS = [
    {"id": "01", "stage": 1, "depends_on": []},
    {"id": "02", "stage": 2, "depends_on": ["01"]},
    {"id": "03", "stage": 2, "depends_on": ["02"]},
]


def test_conditional_accuracy_excludes_questions_with_failed_dependencies() -> None:
    # q03 depended on q02, which was wrong -> q03's failure is not evidence
    # about q03, so it must not count against conditional accuracy.
    grades = {"q01": CORRECT, "q02": WRONG, "q03": WRONG}
    s = stage_summary(grades, QUESTIONS)
    assert s[2]["raw"] == 0.0
    assert s[2]["n_eligible"] == 1  # only q02 had all deps correct
    assert s[2]["conditional"] == 0.0


def test_conditional_equals_raw_when_all_dependencies_pass() -> None:
    grades = {"q01": CORRECT, "q02": CORRECT, "q03": WRONG}
    s = stage_summary(grades, QUESTIONS)
    assert s[2]["raw"] == 0.5
    assert s[2]["n_eligible"] == 2
    assert s[2]["conditional"] == 0.5


# --- rounding and boolean presentation -------------------------------------


def test_answer_is_rounded_to_goldens_precision() -> None:
    # The oracle rounds to one decimal; comparing the unrounded answer against
    # 3.5 at 1e-3 failed on the rounding, not on the analysis.
    assert values_match(3.4921141716569464, 3.5)
    assert values_match(6.250278390494227, 6.3)
    assert values_match(0.12932424484070615, 0.1)  # 29% raw relative error


def test_rounding_does_not_excuse_a_wrong_value() -> None:
    assert not values_match(3.9, 3.5)
    assert not values_match(0.2, 0.1)


def test_full_precision_golden_still_grades_on_tolerance() -> None:
    # Nothing to round to here, so the 1e-3 tolerance governs as before.
    assert values_match(152.38912659, 152.38912661)
    assert not values_match(152.9, 152.38912661)  # 0.33% off


def test_boolean_casing_folds() -> None:
    for written in ("true", "True", "TRUE", "  true "):
        assert values_match(written, "True")
    assert values_match("false", "False")
    assert not values_match("true", "False")


def test_boolean_words_match_one_and_zero() -> None:
    assert values_match(1, "True")
    assert values_match(0, "False")
    assert not values_match(1, "False")


def test_counts_are_not_read_as_booleans() -> None:
    # 1 vs 0 stays a count comparison when no boolean word is in play.
    assert not values_match(1, 0)
    assert values_match(1, 1)


# --- string casing ----------------------------------------------------------


def test_commodity_casing_does_not_decide_a_question() -> None:
    """issue #20. The lowercase house style for `annex1_commodity` is stated
    only in policies/EUDR_CROPS.md, so an arm run without that document has no
    way to recover it. Sessions that classified every class correctly still
    wrote `Cattle` and `Soya`, and since seven questions across three stages
    report the column, that one choice cost five points. Capitalisation is not
    what the benchmark measures."""
    assert values_match("Cattle", "cattle")
    assert values_match("SOYA", "soya")
    assert values_match("Oil Palm", "oil palm")


def test_case_folding_does_not_excuse_a_wrong_commodity() -> None:
    """The fix has to remove the formatting coin flip without also accepting a
    genuinely wrong classification. A session that calls soya cattle is still
    wrong however it capitalises it."""
    assert not values_match("Cattle", "soya")
    assert not values_match("oil_palm", "oil palm")
    assert not values_match("assumed pasture", "assumed_pasture")


def test_no_golden_value_is_distinguished_by_case_alone() -> None:
    """What makes case folding lossless. If any golden column held two values
    that differ only in case, folding would let a wrong answer match the right
    one; the grader would silently stop discriminating and no other test would
    notice. Checked across every column of every golden rather than the
    handful the commodity fix touches, because a later oracle change could
    introduce such a pair anywhere."""
    for path in sorted(GOLDEN_DIR.glob("q*.csv")):
        rows = list(csv.DictReader(path.open(encoding="utf-8")))
        if not rows:
            continue
        for column in rows[0]:
            by_fold: dict[str, set[str]] = {}
            for row in rows:
                value = (row[column] or "").strip()
                if value:
                    by_fold.setdefault(value.casefold(), set()).add(value)
            for folded, spellings in by_fold.items():
                assert len(spellings) == 1, (
                    f"{path.name}:{column} holds {sorted(spellings)}, which "
                    f"case folding collapses to {folded!r}"
                )


# --- near miss --------------------------------------------------------------


def test_near_miss_for_an_answer_just_outside_tolerance(tmp_path: Path) -> None:
    golden = write(tmp_path, "g.csv", "value\n1000.123456\n")
    answer = write(tmp_path, "a.csv", "value\n1002.0\n")  # 0.19% off
    assert grade_question(answer, golden) == NEAR_MISS


def test_wrong_stays_wrong_when_the_answer_is_far_out(tmp_path: Path) -> None:
    golden = write(tmp_path, "g.csv", "value\n1000.123456\n")
    answer = write(tmp_path, "a.csv", "value\n1530.0\n")  # 53% off
    assert grade_question(answer, golden) == WRONG


def test_near_miss_carries_its_diffs(tmp_path: Path) -> None:
    golden = write(tmp_path, "g.csv", "value\n1000.123456\n")
    answer = write(tmp_path, "a.csv", "value\n1002.0\n")
    outcome, diffs = evaluate_question(answer, golden)
    assert outcome == NEAR_MISS
    assert diffs[0]["column"] == "value"
    assert diffs[0]["near_miss"] is True


# --- diffs ------------------------------------------------------------------


def test_diff_names_the_failing_cells_only() -> None:
    golden = [["santa_cruz", 1523.0], ["beni", 847.0]]
    answer = [["beni", 847.0], ["santa_cruz", 1999.0]]
    diffs = diff_table(answer, golden, golden_header=["region", "count"])
    assert len(diffs) == 1
    d = diffs[0]
    assert (d["column"], d["golden"], d["answer"]) == ("count", 1523.0, 1999.0)
    assert d["rel_error"] == round(476.0 / 1523.0, 6)


def test_diff_survives_a_column_permutation() -> None:
    golden = [["beni", 847.0]]
    answer = [[999.0, "beni"]]
    diffs = diff_table(answer, golden, golden_header=["region", "count"])
    assert [d["column"] for d in diffs] == ["count"]


def test_diff_reports_shape_when_row_counts_differ() -> None:
    diffs = diff_table([[1.0]], [[1.0], [2.0]])
    assert diffs == [
        {
            "kind": "shape",
            "golden_rows": 2,
            "answer_rows": 1,
            "golden_columns": 1,
            "answer_columns": 1,
        }
    ]


def test_diff_summary_counts_cells_and_names_columns() -> None:
    golden = [[1.0, 10.0], [2.0, 20.0]]
    answer = [[1.0, 99.0], [2.0, 98.0]]
    diffs = diff_table(answer, golden, golden_header=["id", "value"])
    assert diff_summary(diffs) == "2 cells in value, worst 890.0%"


def test_grade_session_returns_grades_and_diffs(tmp_path: Path) -> None:
    golden_dir = tmp_path / "golden"
    golden_dir.mkdir()
    (golden_dir / "q01.csv").write_text("value\n10.0\n")
    (golden_dir / "q02.csv").write_text("value\n10.0\n")
    session = tmp_path / "session"
    (session / "answers").mkdir(parents=True)
    (session / "answers" / "q01.csv").write_text("value\n10.0\n")
    (session / "answers" / "q02.csv").write_text("value\n25.0\n")

    grades, diffs = grade_session(session, golden_dir)
    assert grades == {"q01": CORRECT, "q02": WRONG}
    assert list(diffs) == ["q02"]
    assert diffs["q02"][0]["golden"] == 10.0


def test_transitive_dependency_failure_propagates() -> None:
    # q01 wrong makes both q02 and q03 ineligible, not just q02.
    grades = {"q01": WRONG, "q02": WRONG, "q03": WRONG}
    s = stage_summary(grades, QUESTIONS)
    assert s[2]["n_eligible"] == 0
    assert s[2]["conditional"] is None


import grade


def _graded_tree(
    tmp_path: Path,
    status: str = "done",
    answer: str = "region,count\nsanta_cruz,1523\n",
) -> tuple[Path, Path, Path, Path]:
    """A golden set, one session, and the questions file the grader reads."""
    golden = tmp_path / "golden"
    golden.mkdir()
    (golden / "q01.csv").write_text(GOLDEN, encoding="utf-8")
    (golden / "SHA256SUMS").write_text("fixture  q01.csv\n", encoding="utf-8")

    results = tmp_path / "results"
    session = results / "sonnet" / "20260721T120000Z-abc1234"
    (session / "answers").mkdir(parents=True)
    (session / "answers" / "q01.csv").write_text(answer, encoding="utf-8")
    (session / "meta.json").write_text(f'{{"status": "{status}"}}', encoding="utf-8")

    questions = tmp_path / "questions.yaml"
    questions.write_text("questions:\n  - id: q01\n    stage: 1\n", encoding="utf-8")
    return golden, results, questions, session


def test_main_writes_grades_and_diffs_beside_each_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """grades.json is what report.py reads; diffs.json is where triage
    starts. Both belong in the run directory, not in a summary."""
    golden, results, questions, session = _graded_tree(tmp_path)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "grade.py",
            "--results",
            str(results),
            "--golden",
            str(golden),
            "--questions",
            str(questions),
        ],
    )

    assert grade.main() == 0

    grades = json.loads((session / "grades.json").read_text(encoding="utf-8"))
    assert grades["q01"] == "wrong", "one row against two is not a match"
    assert (session / "diffs.json").exists()
    out = capsys.readouterr().out
    assert "sonnet/20260721T120000Z-abc1234" in out
    assert "stage 1:" in out


def test_main_grades_a_matching_answer_correct(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    golden, results, questions, session = _graded_tree(tmp_path, answer=GOLDEN)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "grade.py",
            "--results",
            str(results),
            "--golden",
            str(golden),
            "--questions",
            str(questions),
        ],
    )

    assert grade.main() == 0
    assert json.loads((session / "grades.json").read_text())["q01"] == "correct"


def test_main_leaves_an_unscored_session_out(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A session that wrote nothing never attempted the questions. Scoring
    it as wrong blames the model for a run that did not happen."""
    golden, results, questions, session = _graded_tree(
        tmp_path, status="produced_nothing"
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "grade.py",
            "--results",
            str(results),
            "--golden",
            str(golden),
            "--questions",
            str(questions),
        ],
    )

    assert grade.main() == 0
    assert "not scored" in capsys.readouterr().out
    assert not (session / "grades.json").exists()
    meta = json.loads((session / "meta.json").read_text(encoding="utf-8"))
    assert meta["graded_against"] == grade.golden_fingerprint(golden)


def test_a_successful_retry_restores_the_execution_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A transient grader failure must not invalidate a run forever."""
    golden, results, questions, session = _graded_tree(tmp_path, answer=GOLDEN)
    (session / "meta.json").write_text(
        json.dumps(
            {
                "status": "grader_error",
                "execution_status": "incomplete",
                "strict_success": False,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "grade.py",
            "--results",
            str(results),
            "--golden",
            str(golden),
            "--questions",
            str(questions),
        ],
    )

    assert grade.main() == 0

    meta = json.loads((session / "meta.json").read_text(encoding="utf-8"))
    assert meta["status"] == "incomplete"
    assert "execution_status" not in meta


def test_a_repeated_grader_failure_preserves_the_execution_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    golden, results, questions, session = _graded_tree(tmp_path)
    (session / "meta.json").write_text(
        json.dumps({"status": "grader_error", "execution_status": "done"}),
        encoding="utf-8",
    )
    monkeypatch.setattr(grade, "grade_session", lambda *args, **kwargs: 1 / 0)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "grade.py",
            "--results",
            str(results),
            "--golden",
            str(golden),
            "--questions",
            str(questions),
        ],
    )

    assert grade.main() == 0

    meta = json.loads((session / "meta.json").read_text(encoding="utf-8"))
    assert meta["status"] == "grader_error"
    assert meta["execution_status"] == "done"
    assert meta["graded_against"] == grade.golden_fingerprint(golden)


def test_main_refuses_a_tree_with_no_sessions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    golden = tmp_path / "golden"
    golden.mkdir()
    (golden / "q01.csv").write_text(GOLDEN, encoding="utf-8")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "grade.py",
            "--results",
            str(tmp_path / "empty"),
            "--golden",
            str(golden),
        ],
    )

    assert grade.main() == 1
    assert "no sessions found" in capsys.readouterr().err


def test_questions_are_optional(tmp_path: Path) -> None:
    """The grader still runs where questions.yaml is absent; only the
    per-stage breakdown needs it."""
    assert grade.load_questions(tmp_path / "absent.yaml") == []


def test_a_declared_question_with_no_golden_grades_ungradeable(
    tmp_path: Path,
) -> None:
    """A question absent from grades.json reads to every downstream count as
    a question that was never asked, and strict success would then pass a
    trial that answered thirty of thirty-one."""
    golden = tmp_path / "golden"
    golden.mkdir()
    (golden / "q01.csv").write_text("n\n1\n")
    session = tmp_path / "run"
    (session / "answers").mkdir(parents=True)
    (session / "answers" / "q01.csv").write_text("n\n1\n")

    questions = [{"id": "01", "stage": 1}, {"id": "02", "stage": 1}]
    grades, _ = grade.grade_session(session, golden, questions=questions)

    assert grades == {"q01": "correct", "q02": "ungradeable"}
    assert grade.strict_success(grades, questions) is False


def test_grading_without_a_question_list_keeps_the_golden_glob(
    tmp_path: Path,
) -> None:
    """Callers that grade a golden directory alone are unaffected."""
    golden = tmp_path / "golden"
    golden.mkdir()
    (golden / "q01.csv").write_text("n\n1\n")
    session = tmp_path / "run"
    (session / "answers").mkdir(parents=True)

    grades, _ = grade.grade_session(session, golden)

    assert grades == {"q01": "missing"}
