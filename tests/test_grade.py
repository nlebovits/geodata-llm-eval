"""Comparator behavior is the grading contract — pin it with tests."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "harness"))

from grade import (  # noqa: E402
    CORRECT,
    MISSING,
    NEAR_MISS,
    UNPARSEABLE,
    WRONG,
    compare,
    diff_summary,
    diff_table,
    evaluate_question,
    grade_question,
    grade_session,
    load_table,
    stage_summary,
    values_match,
)


def write(tmp_path, name, text):
    p = tmp_path / name
    p.write_text(text)
    return p


GOLDEN = "region,count\nsanta_cruz,1523\nbeni,847\n"


class TestCompare:
    def test_exact_match(self):
        a = [["santa_cruz", 1523], ["beni", 847]]
        assert compare(a, a)

    def test_row_order_ignored(self):
        golden = [["santa_cruz", 1523], ["beni", 847]]
        answer = [["beni", 847], ["santa_cruz", 1523]]
        assert compare(answer, golden)

    def test_column_order_ignored(self):
        golden = [["santa_cruz", 1523], ["beni", 847]]
        answer = [[1523, "santa_cruz"], [847, "beni"]]
        assert compare(answer, golden)

    def test_float_within_tolerance(self):
        golden = [[152.38912661]]
        answer = [[152.38912659]]
        assert compare(answer, golden)

    def test_float_relative_tolerance_boundary(self):
        golden = [[1000.0]]
        assert compare([[1000.9]], golden)      # 0.09% off
        assert not compare([[1002.0]], golden)  # 0.2% off

    def test_integer_count_must_be_exact(self):
        assert not compare([[1524]], [[1523]])

    def test_string_must_be_exact(self):
        assert not compare([["santa cruz"]], [["santa_cruz"]])

    def test_row_count_mismatch(self):
        assert not compare([["beni", 847]],
                           [["santa_cruz", 1523], ["beni", 847]])

    def test_column_count_mismatch(self):
        assert not compare([["beni", 847, 1.0]], [["beni", 847]])

    def test_duplicate_rows_are_multiset(self):
        golden = [[1], [1], [2]]
        assert compare([[2], [1], [1]], golden)
        assert not compare([[1], [2], [2]], golden)

    def test_wrong_value(self):
        golden = [["santa_cruz", 1523], ["beni", 847]]
        answer = [["santa_cruz", 1523], ["beni", 999]]
        assert not compare(answer, golden)

    def test_near_zero_absolute_tolerance(self):
        assert compare([[1e-10]], [[0.0]])


class TestLoadTable:
    def test_parses_types(self, tmp_path):
        p = write(tmp_path, "a.csv", "id,share\n42,0.15\n")
        assert load_table(p) == [[42, 0.15]]

    def test_header_skipped(self, tmp_path):
        p = write(tmp_path, "a.csv", "whatever,names\n1,2\n")
        assert load_table(p) == [[1, 2]]

    def test_empty_file(self, tmp_path):
        assert load_table(write(tmp_path, "a.csv", "")) is None

    def test_header_only(self, tmp_path):
        assert load_table(write(tmp_path, "a.csv", "count\n")) is None

    def test_ragged_rows(self, tmp_path):
        p = write(tmp_path, "a.csv", "a,b\n1,2\n3\n")
        assert load_table(p) is None


class TestGradeQuestion:
    def test_correct(self, tmp_path):
        golden = write(tmp_path, "g.csv", GOLDEN)
        answer = write(tmp_path, "a.csv",
                       "n,region\n847,beni\n1523,santa_cruz\n")
        assert grade_question(answer, golden) == CORRECT

    def test_wrong(self, tmp_path):
        golden = write(tmp_path, "g.csv", GOLDEN)
        answer = write(tmp_path, "a.csv",
                       "region,count\nsanta_cruz,1\nbeni,2\n")
        assert grade_question(answer, golden) == WRONG

    def test_missing(self, tmp_path):
        golden = write(tmp_path, "g.csv", GOLDEN)
        assert grade_question(tmp_path / "nope.csv", golden) == MISSING

    def test_unparseable(self, tmp_path):
        golden = write(tmp_path, "g.csv", GOLDEN)
        answer = write(tmp_path, "a.csv", "")
        assert grade_question(answer, golden) == UNPARSEABLE


# --- geometry tolerance ----------------------------------------------------

def test_geometry_int_slack_allows_small_boundary_difference():
    # 502 vs 500 matched fields: two boundary fields either way, within slack.
    assert values_match(502, 500, geometry=True)
    assert not values_match(502, 500, geometry=False)


def test_geometry_int_slack_uses_one_percent_on_large_counts():
    # 1% of 10000 is 100, so 10090 passes under geometry, fails under strict.
    assert values_match(10090, 10000, geometry=True)
    assert not values_match(10090, 10000, geometry=False)


def test_geometry_float_tolerance_is_one_percent():
    assert values_match(101.0, 100.0, geometry=True)      # 1% off, ok
    assert not values_match(102.0, 100.0, geometry=True)  # 2% off, fails
    assert not values_match(100.5, 100.0, geometry=False)  # strict rejects 0.5%


def test_geometry_flag_threads_through_compare():
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


def test_conditional_accuracy_excludes_questions_with_failed_dependencies():
    # q03 depended on q02, which was wrong -> q03's failure is not evidence
    # about q03, so it must not count against conditional accuracy.
    grades = {"q01": CORRECT, "q02": WRONG, "q03": WRONG}
    s = stage_summary(grades, QUESTIONS)
    assert s[2]["raw"] == 0.0
    assert s[2]["n_eligible"] == 1          # only q02 had all deps correct
    assert s[2]["conditional"] == 0.0


def test_conditional_equals_raw_when_all_dependencies_pass():
    grades = {"q01": CORRECT, "q02": CORRECT, "q03": WRONG}
    s = stage_summary(grades, QUESTIONS)
    assert s[2]["raw"] == 0.5
    assert s[2]["n_eligible"] == 2
    assert s[2]["conditional"] == 0.5


# --- rounding and boolean presentation -------------------------------------

def test_answer_is_rounded_to_goldens_precision():
    # The oracle rounds to one decimal; comparing the unrounded answer against
    # 3.5 at 1e-3 failed on the rounding, not on the analysis.
    assert values_match(3.4921141716569464, 3.5)
    assert values_match(6.250278390494227, 6.3)
    assert values_match(0.12932424484070615, 0.1)  # 29% raw relative error


def test_rounding_does_not_excuse_a_wrong_value():
    assert not values_match(3.9, 3.5)
    assert not values_match(0.2, 0.1)


def test_full_precision_golden_still_grades_on_tolerance():
    # Nothing to round to here, so the 1e-3 tolerance governs as before.
    assert values_match(152.38912659, 152.38912661)
    assert not values_match(152.9, 152.38912661)  # 0.33% off


def test_boolean_casing_folds():
    for written in ("true", "True", "TRUE", "  true "):
        assert values_match(written, "True")
    assert values_match("false", "False")
    assert not values_match("true", "False")


def test_boolean_words_match_one_and_zero():
    assert values_match(1, "True")
    assert values_match(0, "False")
    assert not values_match(1, "False")


def test_counts_are_not_read_as_booleans():
    # 1 vs 0 stays a count comparison when no boolean word is in play.
    assert not values_match(1, 0)
    assert values_match(1, 1)


# --- near miss --------------------------------------------------------------

def test_near_miss_for_an_answer_just_outside_tolerance(tmp_path):
    golden = write(tmp_path, "g.csv", "value\n1000.123456\n")
    answer = write(tmp_path, "a.csv", "value\n1002.0\n")   # 0.19% off
    assert grade_question(answer, golden) == NEAR_MISS


def test_wrong_stays_wrong_when_the_answer_is_far_out(tmp_path):
    golden = write(tmp_path, "g.csv", "value\n1000.123456\n")
    answer = write(tmp_path, "a.csv", "value\n1530.0\n")   # 53% off
    assert grade_question(answer, golden) == WRONG


def test_near_miss_carries_its_diffs(tmp_path):
    golden = write(tmp_path, "g.csv", "value\n1000.123456\n")
    answer = write(tmp_path, "a.csv", "value\n1002.0\n")
    outcome, diffs = evaluate_question(answer, golden)
    assert outcome == NEAR_MISS
    assert diffs[0]["column"] == "value"
    assert diffs[0]["near_miss"] is True


# --- diffs ------------------------------------------------------------------

def test_diff_names_the_failing_cells_only():
    golden = [["santa_cruz", 1523.0], ["beni", 847.0]]
    answer = [["beni", 847.0], ["santa_cruz", 1999.0]]
    diffs = diff_table(answer, golden, golden_header=["region", "count"])
    assert len(diffs) == 1
    d = diffs[0]
    assert (d["column"], d["golden"], d["answer"]) == ("count", 1523.0, 1999.0)
    assert d["rel_error"] == round(476.0 / 1523.0, 6)


def test_diff_survives_a_column_permutation():
    golden = [["beni", 847.0]]
    answer = [[999.0, "beni"]]
    diffs = diff_table(answer, golden, golden_header=["region", "count"])
    assert [d["column"] for d in diffs] == ["count"]


def test_diff_reports_shape_when_row_counts_differ():
    diffs = diff_table([[1.0]], [[1.0], [2.0]])
    assert diffs == [{"kind": "shape", "golden_rows": 2, "answer_rows": 1,
                      "golden_columns": 1, "answer_columns": 1}]


def test_diff_summary_counts_cells_and_names_columns():
    golden = [[1.0, 10.0], [2.0, 20.0]]
    answer = [[1.0, 99.0], [2.0, 98.0]]
    diffs = diff_table(answer, golden, golden_header=["id", "value"])
    assert diff_summary(diffs) == "2 cells in value, worst 890.0%"


def test_grade_session_returns_grades_and_diffs(tmp_path):
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


def test_transitive_dependency_failure_propagates():
    # q01 wrong makes both q02 and q03 ineligible, not just q02.
    grades = {"q01": WRONG, "q02": WRONG, "q03": WRONG}
    s = stage_summary(grades, QUESTIONS)
    assert s[2]["n_eligible"] == 0
    assert s[2]["conditional"] is None
