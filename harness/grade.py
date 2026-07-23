"""Grade session answers against golden fixtures.

The comparator is deliberately dumb and deterministic:

- Row order is ignored (rows compare as multisets).
- Column order and column names are ignored; columns are matched by
  finding a column permutation under which all rows match.
- Integers and strings must match exactly.
- Floats match within relative tolerance 1e-3 (absolute 1e-9 near zero).
- Questions marked "grading: geometry" in questions.yaml involve computed
  areas, distances, or geometric thresholds. Their results legitimately
  vary with method choice (spherical vs ellipsoidal distance, choice of
  equal-area projection), so their floats grade at relative 1e-2 and
  their integers within max(2, 1% of golden).
- A missing or unparseable answer file is a distinct outcome from a
  wrong answer, so broken sessions and wrong sessions stay separable.

Usage:
    python harness/grade.py [--results results/] [--golden fixtures/golden/]
                            [--questions fixtures/questions.yaml]

Writes results/{model}/pass-{n}/grades.json per session and prints a
summary table.
"""

from __future__ import annotations

import argparse
import csv
import itertools
import json
import math
import sys
from pathlib import Path

import yaml

REL_TOL = 1e-3
ABS_TOL = 1e-9
GEOM_REL_TOL = 1e-2
GEOM_INT_SLACK = 2

# Grade outcomes
CORRECT = "correct"
WRONG = "wrong"
MISSING = "missing"
UNPARSEABLE = "unparseable"


def _parse_cell(cell: str) -> object:
    """Parse a CSV cell into int, float, or stripped string."""
    s = cell.strip()
    try:
        return int(s)
    except ValueError:
        pass
    try:
        return float(s)
    except ValueError:
        pass
    return s


def load_table(path: Path) -> list[list[object]] | None:
    """Load a CSV as rows of parsed cells, skipping the header row.

    Returns None if the file can't be parsed as CSV. Assumes the first
    row is a header (the output contract requires one); its values are
    ignored because the comparator is column-name-insensitive.
    """
    try:
        with open(path, newline="", encoding="utf-8") as f:
            rows = list(csv.reader(f))
    except (OSError, UnicodeDecodeError, csv.Error):
        return None
    if not rows:
        return None
    body = rows[1:]
    if not body:
        return None
    width = len(body[0])
    if any(len(r) != width for r in body):
        return None
    return [[_parse_cell(c) for c in row] for row in body]


def values_match(a: object, b: object, geometry: bool = False) -> bool:
    """Compare an answer cell against a golden cell.

    Under geometry grading, integers allow max(GEOM_INT_SLACK, 1% of
    golden) because threshold counts shift a little with legitimate
    method choice, and floats use the looser GEOM_REL_TOL.
    """
    a_num = isinstance(a, (int, float)) and not isinstance(a, bool)
    b_num = isinstance(b, (int, float)) and not isinstance(b, bool)
    if a_num and b_num:
        if isinstance(a, int) and isinstance(b, int):
            if geometry:
                return abs(a - b) <= max(GEOM_INT_SLACK, 0.01 * abs(b))
            return a == b
        rel = GEOM_REL_TOL if geometry else REL_TOL
        return math.isclose(float(a), float(b), rel_tol=rel, abs_tol=ABS_TOL)
    return a == b


def _rows_match_under_permutation(
    answer: list[list[object]],
    golden: list[list[object]],
    perm: tuple[int, ...],
    geometry: bool,
) -> bool:
    """Check answer rows == golden rows as multisets, with answer columns
    reordered by perm."""
    remaining = [list(r) for r in golden]
    for a_row in answer:
        projected = [a_row[i] for i in perm]
        for idx, g_row in enumerate(remaining):
            if all(values_match(p, g, geometry) for p, g in zip(projected, g_row)):
                del remaining[idx]
                break
        else:
            return False
    return not remaining


def compare(
    answer: list[list[object]],
    golden: list[list[object]],
    geometry: bool = False,
) -> bool:
    """True if the answer table matches golden up to row order and
    column permutation."""
    if len(answer) != len(golden):
        return False
    if not golden:
        return True
    n_cols = len(golden[0])
    if len(answer[0]) != n_cols:
        return False
    for perm in itertools.permutations(range(n_cols)):
        if _rows_match_under_permutation(answer, golden, perm, geometry):
            return True
    return False


def load_geometry_qids(questions_path: Path) -> set[str]:
    """Read questions.yaml and return the qids graded as geometry,
    in golden-fixture stem form ("q11", "q15", ...)."""
    spec = yaml.safe_load(questions_path.read_text(encoding="utf-8"))
    return {
        f"q{q['id']}"
        for q in spec["questions"]
        if q.get("grading") == "geometry"
    }


def grade_question(
    answer_path: Path, golden_path: Path, geometry: bool = False
) -> str:
    golden = load_table(golden_path)
    if golden is None:
        raise ValueError(f"golden fixture unreadable: {golden_path}")
    if not answer_path.exists():
        return MISSING
    answer = load_table(answer_path)
    if answer is None:
        return UNPARSEABLE
    return CORRECT if compare(answer, golden, geometry) else WRONG


def grade_session(
    session_dir: Path, golden_dir: Path, geometry_qids: set[str]
) -> dict[str, str]:
    """Grade every golden question against a session's answers/ dir."""
    grades: dict[str, str] = {}
    for golden_path in sorted(golden_dir.glob("q*.csv")):
        qid = golden_path.stem
        answer_path = session_dir / "answers" / f"{qid}.csv"
        grades[qid] = grade_question(
            answer_path, golden_path, geometry=qid in geometry_qids
        )
    return grades


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--results", type=Path, default=Path("results"))
    ap.add_argument("--golden", type=Path, default=Path("fixtures/golden"))
    ap.add_argument(
        "--questions", type=Path, default=Path("fixtures/questions.yaml")
    )
    args = ap.parse_args()

    golden_files = list(args.golden.glob("q*.csv"))
    if not golden_files:
        print(f"no golden fixtures in {args.golden}", file=sys.stderr)
        return 1

    geometry_qids = load_geometry_qids(args.questions)

    session_dirs = sorted(args.results.glob("*/pass-*"))
    if not session_dirs:
        print(f"no sessions found under {args.results}", file=sys.stderr)
        return 1

    print(f"{'session':<24} {'correct':>8} {'wrong':>6} {'missing':>8} {'broken':>7}")
    for session_dir in session_dirs:
        grades = grade_session(session_dir, args.golden, geometry_qids)
        (session_dir / "grades.json").write_text(
            json.dumps(grades, indent=2, sort_keys=True) + "\n"
        )
        counts = {k: sum(1 for v in grades.values() if v == k)
                  for k in (CORRECT, WRONG, MISSING, UNPARSEABLE)}
        label = f"{session_dir.parent.name}/{session_dir.name}"
        print(
            f"{label:<24} {counts[CORRECT]:>8} {counts[WRONG]:>6}"
            f" {counts[MISSING]:>8} {counts[UNPARSEABLE]:>7}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
