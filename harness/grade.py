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

# Geometry-graded questions (grading: geometry in questions.yaml) compute areas,
# distances, or geometric thresholds, where reasonable method choices — spherical
# vs ellipsoidal distance, the choice of equal-area projection — shift results
# slightly. Their floats grade looser, and their integer counts allow an absolute
# slack of max(2, 1% of golden) so a handful of boundary fields either way does
# not fail an otherwise-correct answer.
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
    answer: list[list[object]], golden: list[list[object]], perm: tuple[int, ...],
    geometry: bool = False,
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


def compare(answer: list[list[object]], golden: list[list[object]],
            geometry: bool = False) -> bool:
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


def grade_question(answer_path: Path, golden_path: Path,
                   geometry: bool = False) -> str:
    golden = load_table(golden_path)
    if golden is None:
        raise ValueError(f"golden fixture unreadable: {golden_path}")
    if not answer_path.exists():
        return MISSING
    answer = load_table(answer_path)
    if answer is None:
        return UNPARSEABLE
    return CORRECT if compare(answer, golden, geometry) else WRONG


def geometry_graded_ids(questions_path: Path) -> set[str]:
    """The set of question ids marked `grading: geometry` in questions.yaml.

    Returns an empty set if the file or PyYAML is unavailable, so grading still
    runs (every question then uses the strict tolerance).
    """
    try:
        import yaml
    except ImportError:
        return set()
    if not questions_path.exists():
        return set()
    spec = yaml.safe_load(questions_path.read_text(encoding="utf-8"))
    return {f"q{q['id']}" for q in spec.get("questions", [])
            if q.get("grading") == "geometry"}


def grade_session(session_dir: Path, golden_dir: Path,
                  geometry_ids: set[str] | None = None) -> dict[str, str]:
    """Grade every golden question against a session's answers/ dir.

    geometry_ids (question ids like 'q08') grade with the looser geometry
    tolerance; pass the result of geometry_graded_ids().
    """
    geometry_ids = geometry_ids or set()
    grades: dict[str, str] = {}
    for golden_path in sorted(golden_dir.glob("q*.csv")):
        qid = golden_path.stem
        answer_path = session_dir / "answers" / f"{qid}.csv"
        grades[qid] = grade_question(
            answer_path, golden_path, geometry=qid in geometry_ids)
    return grades


def _deps_all_correct(qid: str, by_id: dict, grades: dict) -> bool:
    """True if every transitive dependency of qid graded correct.

    Transitive, not direct: a question whose parent was itself downstream of a
    wrong answer cannot be evidence about the model's ability at that step, so it
    is excluded from conditional accuracy. Question ids in depends_on are bare
    ('05'); grades are keyed 'q05'.
    """
    for dep in by_id[qid].get("depends_on", []):
        if grades.get(f"q{dep}") != CORRECT:
            return False
        if not _deps_all_correct(dep, by_id, grades):
            return False
    return True


def stage_summary(grades: dict, questions: list) -> dict:
    """Per-stage raw and conditional accuracy.

    raw          — correct / all questions in the stage.
    conditional  — correct / questions whose dependencies all graded correct,
                   i.e. accuracy on the questions the model actually had a fair
                   shot at. None when no question in the stage was eligible.

    The gap between the two is the error-propagation signal: a stage with high
    raw but low conditional accuracy is failing on its own merits, while the
    reverse means it is mostly inheriting upstream errors.
    """
    by_id = {q["id"]: q for q in questions}
    out: dict = {}
    for stage in sorted({q["stage"] for q in questions}):
        in_stage = [q for q in questions if q["stage"] == stage]
        n = len(in_stage)
        n_correct = sum(1 for q in in_stage
                        if grades.get(f"q{q['id']}") == CORRECT)
        eligible = [q for q in in_stage
                    if _deps_all_correct(q["id"], by_id, grades)]
        n_elig = len(eligible)
        n_elig_correct = sum(1 for q in eligible
                             if grades.get(f"q{q['id']}") == CORRECT)
        out[stage] = {
            "n": n,
            "raw": n_correct / n if n else None,
            "n_eligible": n_elig,
            "conditional": (n_elig_correct / n_elig) if n_elig else None,
        }
    return out


def load_questions(questions_path: Path) -> list:
    """questions.yaml questions list, or [] if unavailable."""
    try:
        import yaml
    except ImportError:
        return []
    if not questions_path.exists():
        return []
    return yaml.safe_load(questions_path.read_text(encoding="utf-8"))\
        .get("questions", [])


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--results", type=Path, default=Path("results"))
    ap.add_argument("--golden", type=Path, default=Path("fixtures/golden"))
    ap.add_argument("--questions", type=Path,
                    default=Path("fixtures/questions.yaml"))
    args = ap.parse_args()

    golden_files = list(args.golden.glob("q*.csv"))
    if not golden_files:
        print(f"no golden fixtures in {args.golden}", file=sys.stderr)
        return 1

    session_dirs = sorted(args.results.glob("*/pass-*"))
    if not session_dirs:
        print(f"no sessions found under {args.results}", file=sys.stderr)
        return 1

    geometry_ids = geometry_graded_ids(args.questions)
    questions = load_questions(args.questions)

    print(f"{'session':<24} {'correct':>8} {'wrong':>6} {'missing':>8} {'broken':>7}")
    for session_dir in session_dirs:
        grades = grade_session(session_dir, args.golden, geometry_ids)
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
        if questions:
            summary = stage_summary(grades, questions)
            for stage, s in summary.items():
                raw = f"{s['raw']:.2f}" if s["raw"] is not None else "  - "
                cond = (f"{s['conditional']:.2f}"
                        if s["conditional"] is not None else "  - ")
                print(f"    stage {stage}: raw {raw}  conditional {cond}"
                      f"  ({s['n_eligible']}/{s['n']} eligible)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
