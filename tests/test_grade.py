"""Comparator behavior is the grading contract — pin it with tests."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "harness"))

from grade import (  # noqa: E402
    CORRECT,
    MISSING,
    UNPARSEABLE,
    WRONG,
    compare,
    grade_question,
    load_geometry_qids,
    load_table,
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


class TestGeometryGrading:
    """Questions marked grading: geometry tolerate method variation."""

    def test_float_half_percent_passes_geometry_only(self):
        golden = [[1000.0]]
        assert compare([[1005.0]], golden, geometry=True)
        assert not compare([[1005.0]], golden)

    def test_float_beyond_one_percent_still_fails(self):
        assert not compare([[1015.0]], [[1000.0]], geometry=True)

    def test_small_count_absolute_slack(self):
        golden = [[30]]
        assert compare([[32]], golden, geometry=True)
        assert not compare([[33]], golden, geometry=True)

    def test_large_count_one_percent_slack(self):
        golden = [[10000]]
        assert compare([[10099]], golden, geometry=True)
        assert not compare([[10150]], golden, geometry=True)

    def test_integer_exact_without_geometry(self):
        assert not compare([[31]], [[30]])

    def test_string_still_exact_under_geometry(self):
        assert not compare([["sorrisso"]], [["sorriso"]], geometry=True)

    def test_qids_loaded_from_spec(self, tmp_path):
        spec = (
            "questions:\n"
            "  - id: \"01\"\n    tier: easy\n"
            "  - id: \"11\"\n    tier: medium\n    grading: geometry\n"
        )
        p = write(tmp_path, "questions.yaml", spec)
        assert load_geometry_qids(p) == {"q11"}

    def test_repo_spec_marks_geometry_questions(self):
        repo = Path(__file__).resolve().parent.parent
        qids = load_geometry_qids(repo / "fixtures" / "questions.yaml")
        assert "q11" in qids and "q28" in qids
        assert "q01" not in qids

    def test_grade_question_applies_geometry(self, tmp_path):
        golden = write(tmp_path, "g.csv", "share\n0.500\n")
        answer = write(tmp_path, "a.csv", "share\n0.503\n")
        assert grade_question(answer, golden, geometry=True) == CORRECT
        assert grade_question(answer, golden) == WRONG


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
