"""Grade session answers against golden fixtures.

The comparator is deliberately dumb and deterministic:

- Row order is ignored (rows compare as multisets).
- Column order and column names are ignored; columns are matched by
  finding a column permutation under which all rows match.
- Integers and strings must match exactly.
- Floats match within relative tolerance 1e-3 (absolute 1e-9 near zero).
- A missing or unparseable answer file is a distinct outcome from a
  wrong answer, so broken sessions and wrong sessions stay separable.

Usage:
    python harness/grade.py [--results results/] [--golden fixtures/golden/]

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

REL_TOL = 1e-3
ABS_TOL = 1e-9

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


def values_match(a: object, b: object) -> bool:
    a_num = isinstance(a, (int, float)) and not isinstance(a, bool)
    b_num = isinstance(b, (int, float)) and not isinstance(b, bool)
    if a_num and b_num:
        if isinstance(a, int) and isinstance(b, int):
            return a == b
        return math.isclose(float(a), float(b), rel_tol=REL_TOL, abs_tol=ABS_TOL)
    return a == b


def _rows_match_under_permutation(
    answer: list[list[object]], golden: list[list[object]], perm: tuple[int, ...]
) -> bool:
    """Check answer rows == golden rows as multisets, with answer columns
    reordered by perm."""
    remaining = [list(r) for r in golden]
    for a_row in answer:
        projected = [a_row[i] for i in perm]
        for idx, g_row in enumerate(remaining):
            if all(values_match(p, g) for p, g in zip(projected, g_row)):
                del remaining[idx]
                break
        else:
            return False
    return not remaining


def compare(answer: list[list[object]], golden: list[list[object]]) -> bool:
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
        if _rows_match_under_permutation(answer, golden, perm):
            return True
    return False


def grade_question(answer_path: Path, golden_path: Path) -> str:
    golden = load_table(golden_path)
    if golden is None:
        raise ValueError(f"golden fixture unreadable: {golden_path}")
    if not answer_path.exists():
        return MISSING
    answer = load_table(answer_path)
    if answer is None:
        return UNPARSEABLE
    return CORRECT if compare(answer, golden) else WRONG


def grade_session(session_dir: Path, golden_dir: Path) -> dict[str, str]:
    """Grade every golden question against a session's answers/ dir."""
    grades: dict[str, str] = {}
    for golden_path in sorted(golden_dir.glob("q*.csv")):
        qid = golden_path.stem
        answer_path = session_dir / "answers" / f"{qid}.csv"
        grades[qid] = grade_question(answer_path, golden_path)
    return grades


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--results", type=Path, default=Path("results"))
    ap.add_argument("--golden", type=Path, default=Path("fixtures/golden"))
    args = ap.parse_args()

    golden_files = list(args.golden.glob("q*.csv"))
    if not golden_files:
        print(f"no golden fixtures in {args.golden}", file=sys.stderr)
        return 1

    session_dirs = sorted(args.results.glob("*/pass-*"))
    if not session_dirs:
        print(f"no sessions found under {args.results}", file=sys.stderr)
        return 1

    print(f"{'session':<24} {'correct':>8} {'wrong':>6} {'missing':>8} {'broken':>7}")
    for session_dir in session_dirs:
        grades = grade_session(session_dir, args.golden)
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
