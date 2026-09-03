"""Grade session answers against golden fixtures.

The comparator is deliberately dumb and deterministic:

- Row order is ignored (rows compare as multisets).
- Column order and column names are ignored; columns are matched by
  finding a column permutation under which all rows match.
- Integers must match exactly. Strings match ignoring case.
- Floats match within relative tolerance 1e-3 (absolute 1e-9 near zero),
  either as written or after rounding to the golden's displayed precision.
- A missing or unparseable answer file is a distinct outcome from a
  wrong answer, so broken sessions and wrong sessions stay separable.

Usage:
    python harness/grade.py [--results results/] [--golden fixtures/golden/]

Writes results/{model}/{run_id}/grades.json per run and prints a
summary table.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import itertools
import json
import math
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from layout import (
    DONE,
    GRADER_ERROR,
    is_scored,
    read_meta,
    regraded,
    run_dirs,
    run_name,
)

REL_TOL = 1e-3
ABS_TOL = 1e-9


def golden_fingerprint(golden_dir: Path) -> str | None:
    """A digest of the golden manifest being graded against.

    `run.py` records the same digest at session time. The two are equal for a
    run graded against the fixtures it saw, and differ for a run re-graded
    after the oracle changed — which is a fact about the score, not a defect,
    but one nothing else in the run directory would otherwise state.
    """
    manifest = golden_dir / "SHA256SUMS"
    if not manifest.exists():
        return None
    return hashlib.sha256(manifest.read_bytes()).hexdigest()[:12]


# Geometry-graded questions (grading: geometry in questions.yaml) compute areas,
# distances, or geometric thresholds, where reasonable method choices — spherical
# vs ellipsoidal distance, the choice of equal-area projection — shift results
# slightly. Their floats grade looser, and their integer counts allow an absolute
# slack of max(2, 1% of golden) so a handful of boundary fields either way does
# not fail an otherwise-correct answer.
GEOM_REL_TOL = 1e-2
GEOM_INT_SLACK = 2

EXACT = "exact"
GEOMETRY = "geometry"
GRADING_POLICIES = frozenset({EXACT, GEOMETRY})

# An answer that fails its own tolerance but clears a tenfold looser one is a
# different result from one that is wrong by half. Recording that separately
# keeps rounding artifacts out of the failure count and surfaces tolerance
# problems in the comparator rather than filing them as model errors.
NEAR_MISS_FACTOR = 10

# Diff alignment tries every column permutation, which is factorial. Wide
# tables fall back to the identity permutation: a diff is a triage aid, and a
# slightly misaligned one beats waiting on 40,000 permutations.
MAX_DIFF_PERM_COLS = 6

# Grade outcomes
# One mismatched cell, as written into diffs.json.
Diff = dict[str, Any]
# One question from questions.yaml.
Question = dict[str, Any]

CORRECT = "correct"
NEAR_MISS = "near_miss"
WRONG = "wrong"
MISSING = "missing"
UNPARSEABLE = "unparseable"
# A question questions.yaml declares and fixtures/golden has no file for.
# Nothing about the session: the fixture set is short and needs regenerating.
UNGRADEABLE = "ungradeable"


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


def load_header(path: Path) -> list[str]:
    """The CSV header row, or [] if the file can't be read.

    The comparator ignores column names, but a diff has to name the column it
    is reporting on, so the header is read separately rather than folded into
    load_table.
    """
    try:
        with open(path, newline="", encoding="utf-8") as f:
            for row in csv.reader(f):
                return [c.strip() for c in row]
    except (OSError, UnicodeDecodeError, csv.Error):
        return []
    return []


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


BOOLEAN_WORDS = {
    "true": True,
    "false": False,
    "yes": True,
    "no": False,
    "t": True,
    "f": False,
}


def _as_boolean(value: object) -> bool | None:
    """The boolean a cell denotes, or None if it denotes something else.

    `true`, `True`, and `TRUE` are the same answer written three ways.
    questions.yaml declares `type: boolean` without naming a canonical form and
    the prompt states none, so the comparator accepts any of them. Bare 1 and 0
    only count as booleans opposite a word, which _match_booleans enforces --
    otherwise a count column of ones would start comparing equal to `true`.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return BOOLEAN_WORDS.get(value.strip().lower())
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    return None


def _match_booleans(a: object, b: object) -> bool | None:
    """Compare two cells as booleans, or None if that reading doesn't apply."""
    word = any(
        isinstance(v, (bool, str)) and _as_boolean(v) is not None for v in (a, b)
    )
    if not word:
        return None
    a_bool, b_bool = _as_boolean(a), _as_boolean(b)
    if a_bool is None or b_bool is None:
        return None
    return a_bool == b_bool


def _decimals(value: float) -> int | None:
    """Decimal places a float is written with, or None in exponent form.

    The oracle rounds most float columns to one place, so the golden value
    carries the precision the answer is being held to.
    """
    text = repr(float(value))
    if "e" in text or "E" in text:
        return None
    return len(text.partition(".")[2])


def _quantize(answer: float, golden: float) -> float:
    """Answer rounded to golden's precision.

    Golden rounds `3.4921141716569464` to `3.5`; comparing the unrounded answer
    against that at 1e-3 fails on the rounding, not on the analysis. Rounding
    the answer the same way first tests the computation instead of the
    formatting.
    """
    places = _decimals(golden)
    return answer if places is None else round(answer, places)


def _float_comparison_values(answer: float, golden: float) -> tuple[float, float]:
    """Raw and presentation-normalised answer values used by the grader."""
    return answer, _quantize(answer, golden)


def _floats_match(answer: float, golden: float, rel_tol: float) -> bool:
    """Whether raw or presentation-normalised values clear the tolerance.

    The two paths protect different valid answers. Raw comparison preserves
    the stated numeric tolerance; quantized comparison accepts an unrounded
    answer when the oracle emitted a rounded golden. Neither path may make the
    other stricter: in particular, rounding must not turn a raw in-tolerance
    answer into a miss.
    """
    raw, quantized = _float_comparison_values(answer, golden)
    return math.isclose(raw, golden, rel_tol=rel_tol, abs_tol=ABS_TOL) or math.isclose(
        quantized, golden, rel_tol=rel_tol, abs_tol=ABS_TOL
    )


def values_match(
    a: object, b: object, geometry: bool = False, slack: float = 1.0
) -> bool:
    """True if answer cell `a` matches golden cell `b` within tolerance.

    `b` is always the golden side: it sets the precision of the optional
    presentation-normalised comparison. `slack` widens the tolerance; the
    near-miss pass uses NEAR_MISS_FACTOR, everything else leaves it at 1.

    Strings compare case-insensitively. The benchmark measures whether a model
    can resolve parcels, apply a containment rule, and route a commodity, not
    whether it guesses a house capitalisation style. Where the two came apart
    the score followed the capitalisation: an ablation run that classified
    every crop correctly wrote `Cattle` and `Soya` for golden `cattle` and
    `soya`, and because `annex1_commodity` is reported by seven questions
    across three stages, that one choice cost five points and made a stable
    arm look like it swung wildly (issue #20). No golden vocabulary anywhere
    in the set distinguishes two values by case alone, so folding case can
    turn a wrong answer into a right one only if the answer was right.
    """
    as_bools = _match_booleans(a, b)
    if as_bools is not None:
        return as_bools
    # Inline rather than via a flag: the isinstance calls have to sit in
    # the condition for both branches below to see the narrowed types.
    if (
        isinstance(a, (int, float))
        and not isinstance(a, bool)
        and isinstance(b, (int, float))
        and not isinstance(b, bool)
    ):
        if isinstance(a, int) and isinstance(b, int):
            if geometry or slack > 1.0:
                int_slack = slack * max(GEOM_INT_SLACK, 0.01 * abs(b))
                return abs(a - b) <= int_slack
            return a == b
        rel = slack * (GEOM_REL_TOL if geometry else REL_TOL)
        return _floats_match(float(a), float(b), rel)
    if isinstance(a, str) and isinstance(b, str):
        return a.casefold() == b.casefold()
    return a == b


def _column_geometry(
    n_cols: int,
    geometry: bool,
    column_policies: Sequence[str] | None,
) -> list[bool]:
    """Resolve comparator policy once for each golden column."""
    if column_policies is None:
        return [geometry] * n_cols
    if len(column_policies) != n_cols:
        raise ValueError(
            "grading policy count does not match golden columns: "
            f"{len(column_policies)} policies for {n_cols} columns"
        )
    unknown = [policy for policy in column_policies if policy not in GRADING_POLICIES]
    if unknown:
        raise ValueError(f"unknown grading policy: {unknown[0]!r}")
    return [policy == GEOMETRY for policy in column_policies]


def _rows_match_under_permutation(
    answer: Sequence[Sequence[object]],
    golden: Sequence[Sequence[object]],
    perm: tuple[int, ...],
    geometry: bool = False,
    slack: float = 1.0,
    column_policies: Sequence[str] | None = None,
) -> bool:
    """Check answer rows == golden rows as multisets, with answer columns
    reordered by perm."""
    remaining = [list(r) for r in golden]
    column_geometry = _column_geometry(len(perm), geometry, column_policies)
    for a_row in answer:
        projected = [a_row[i] for i in perm]
        for idx, g_row in enumerate(remaining):
            if all(
                values_match(p, g, is_geometry, slack)
                for p, g, is_geometry in zip(projected, g_row, column_geometry)
            ):
                del remaining[idx]
                break
        else:
            return False
    return not remaining


def compare(
    answer: Sequence[Sequence[object]],
    golden: Sequence[Sequence[object]],
    geometry: bool = False,
    slack: float = 1.0,
    column_policies: Sequence[str] | None = None,
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
    _column_geometry(n_cols, geometry, column_policies)
    for perm in itertools.permutations(range(n_cols)):
        if _rows_match_under_permutation(
            answer, golden, perm, geometry, slack, column_policies
        ):
            return True
    return False


# --- diffs -----------------------------------------------------------------


def _align_under_permutation(
    answer: Sequence[Sequence[object]],
    golden: Sequence[Sequence[object]],
    perm: tuple[int, ...],
    geometry: bool,
    column_policies: Sequence[str] | None,
) -> tuple[tuple[int, int], list[tuple[int, int]]]:
    """Pair answer rows to golden rows greedily, fewest mismatched cells first.

    Near-miss mismatches break ties using the same resolved column policies.
    Returns (ordinary mismatches, near-miss mismatches) and the pairing, as
    (golden row index, answer row index).
    """
    unused = set(range(len(answer)))
    column_geometry = _column_geometry(len(perm), geometry, column_policies)
    pairs: list[tuple[int, int]] = []
    total = (0, 0)
    for g_idx, g_row in enumerate(golden):
        best_idx: int | None = None
        best_cost: tuple[int, int] | None = None
        for a_idx in unused:
            projected = [answer[a_idx][i] for i in perm]
            cells = list(zip(projected, g_row, column_geometry))
            cost = (
                sum(
                    1
                    for p, g, is_geometry in cells
                    if not values_match(p, g, is_geometry)
                ),
                sum(
                    1
                    for p, g, is_geometry in cells
                    if not values_match(p, g, is_geometry, NEAR_MISS_FACTOR)
                ),
            )
            if best_cost is None or cost < best_cost:
                best_idx, best_cost = a_idx, cost
            if cost == (0, 0):
                break
        if best_idx is None:
            continue
        unused.discard(best_idx)
        pairs.append((g_idx, best_idx))
        row_cost = best_cost or (0, 0)
        total = (total[0] + row_cost[0], total[1] + row_cost[1])
    return total, pairs


def diff_table(
    answer: Sequence[Sequence[object]],
    golden: Sequence[Sequence[object]],
    geometry: bool = False,
    golden_header: list[str] | None = None,
    column_policies: Sequence[str] | None = None,
) -> list[Diff]:
    """Per-cell differences between a wrong answer and golden.

    Rows are paired by similarity, not by position, because the comparator
    ignores row order. Where the tables differ in shape there is nothing to
    align, so the diff reports the shape instead.
    """
    if not golden:
        return []
    if len(answer) != len(golden) or len(answer[0]) != len(golden[0]):
        return [
            {
                "kind": "shape",
                "golden_rows": len(golden),
                "answer_rows": len(answer),
                "golden_columns": len(golden[0]),
                "answer_columns": len(answer[0]) if answer else 0,
            }
        ]

    n_cols = len(golden[0])
    column_geometry = _column_geometry(n_cols, geometry, column_policies)
    header = list(golden_header or [])
    perms = (
        itertools.permutations(range(n_cols))
        if n_cols <= MAX_DIFF_PERM_COLS
        else [tuple(range(n_cols))]
    )
    # Identity is a real starting permutation, not a placeholder: `perms`
    # always yields at least it, so the loop only ever improves on it.
    best_perm: tuple[int, ...] = tuple(range(n_cols))
    best_pairs: list[tuple[int, int]] = []
    best_cost: tuple[int, int] | None = None
    for perm in perms:
        cost, pairs = _align_under_permutation(
            answer, golden, perm, geometry, column_policies
        )
        if best_cost is None or cost < best_cost:
            best_perm, best_pairs, best_cost = perm, pairs, cost
        if cost == (0, 0):
            break

    diffs: list[Diff] = []
    for g_idx, a_idx in best_pairs:
        g_row = golden[g_idx]
        projected = [answer[a_idx][i] for i in best_perm]
        for col, (got, want) in enumerate(zip(projected, g_row)):
            is_geometry = column_geometry[col]
            if values_match(got, want, is_geometry):
                continue
            numeric = _numeric_diagnostics(got, want)
            diffs.append(
                {
                    "kind": "cell",
                    "row": g_idx,
                    "column": header[col] if col < len(header) else f"col{col}",
                    "golden": want,
                    "answer": got,
                    **numeric,
                    "near_miss": values_match(got, want, is_geometry, NEAR_MISS_FACTOR),
                }
            )
    return diffs


def _rel_error(got: object, want: object) -> float | None:
    """Relative error, or None when either side isn't a number to divide."""
    numeric = (int, float)
    if not (isinstance(got, numeric) and isinstance(want, numeric)):
        return None
    if isinstance(got, bool) or isinstance(want, bool) or want == 0:
        return None
    return round(abs(float(got) - float(want)) / abs(float(want)), 6)


def _numeric_diagnostics(got: object, want: object) -> dict[str, float | None]:
    """Expose every numeric representation used by the float comparator.

    `rel_error` remains the summary-compatible field and is the smaller error
    across the raw and quantized paths. The named fields make that choice
    auditable instead of printing a raw error beside a quantized verdict.
    Integer pairs have only an exact/raw path.
    """
    raw_error = _rel_error(got, want)
    quantized_answer: float | None = None
    quantized_error: float | None = None
    numeric = (int, float)
    if (
        isinstance(got, numeric)
        and not isinstance(got, bool)
        and isinstance(want, numeric)
        and not isinstance(want, bool)
        and not (isinstance(got, int) and isinstance(want, int))
    ):
        _, quantized_answer = _float_comparison_values(float(got), float(want))
        quantized_error = _rel_error(quantized_answer, want)

    errors = [error for error in (raw_error, quantized_error) if error is not None]
    return {
        "rel_error": min(errors) if errors else None,
        "raw_rel_error": raw_error,
        "quantized_answer": quantized_answer,
        "quantized_rel_error": quantized_error,
    }


def diff_summary(diffs: list[Diff]) -> str:
    """One line naming what failed: how many cells, in which columns, how far.

    Triage starts here. A question that missed four cells of twenty-four in one
    column is a different problem from one that missed every row.
    """
    if not diffs:
        return "no differences"
    shape = next((d for d in diffs if d["kind"] == "shape"), None)
    if shape:
        return (
            f"shape: golden {shape['golden_rows']}x"
            f"{shape['golden_columns']},"
            f" answer {shape['answer_rows']}x{shape['answer_columns']}"
        )
    columns = sorted({d["column"] for d in diffs})
    errors = [d["rel_error"] for d in diffs if d["rel_error"] is not None]
    worst = f", worst {max(errors):.1%}" if errors else ""
    return f"{len(diffs)} cells in {', '.join(columns)}{worst}"


def evaluate_question(
    answer_path: Path,
    golden_path: Path,
    geometry: bool = False,
    column_policies: Sequence[str] | None = None,
) -> tuple[str, list[Diff]]:
    """Grade one question and, when it fails, say where.

    Returns the outcome and the per-cell diffs behind it. A miss that clears
    NEAR_MISS_FACTOR times the tolerance grades `near_miss` rather than
    `wrong`, and still carries its diffs.
    """
    golden = load_table(golden_path)
    if golden is None:
        raise ValueError(f"golden fixture unreadable: {golden_path}")
    if not answer_path.exists():
        return MISSING, []
    answer = load_table(answer_path)
    if answer is None:
        return UNPARSEABLE, []
    if compare(answer, golden, geometry, column_policies=column_policies):
        return CORRECT, []
    diffs = diff_table(
        answer,
        golden,
        geometry,
        load_header(golden_path),
        column_policies,
    )
    if compare(
        answer,
        golden,
        geometry,
        slack=NEAR_MISS_FACTOR,
        column_policies=column_policies,
    ):
        return NEAR_MISS, diffs
    return WRONG, diffs


def grade_question(
    answer_path: Path,
    golden_path: Path,
    geometry: bool = False,
    column_policies: Sequence[str] | None = None,
) -> str:
    return evaluate_question(answer_path, golden_path, geometry, column_policies)[0]


def _grading_policy(value: object, location: str) -> str:
    """Validate and return one fixture grading policy."""
    if not isinstance(value, str) or value not in GRADING_POLICIES:
        allowed = ", ".join(sorted(GRADING_POLICIES))
        raise ValueError(
            f"invalid grading policy at {location}: {value!r}; expected {allowed}"
        )
    return str(value)


def column_grading_policies(question: Question) -> list[str] | None:
    """Column overrides in declared (and therefore golden) column order.

    None means every column inherits the question-level policy, preserving the
    scalar comparator path for old fixtures and callers.
    """
    qid = question.get("id", "?")
    default = _grading_policy(question.get("grading", EXACT), f"question q{qid}")
    output = question.get("output", {})
    columns = output.get("columns", []) if isinstance(output, dict) else []
    policies: list[str] = []
    has_override = False
    for index, column in enumerate(columns):
        if not isinstance(column, dict):
            continue
        has_override = has_override or "grading" in column
        name = column.get("name", index)
        policies.append(
            _grading_policy(
                column.get("grading", default), f"question q{qid} column {name!r}"
            )
        )
    return policies if has_override else None


def geometry_graded_questions(questions: Sequence[Question]) -> set[str]:
    """Question ids whose default grading policy is geometry."""
    return {f"q{q['id']}" for q in questions if q.get("grading", EXACT) == GEOMETRY}


def geometry_graded_ids(questions_path: Path) -> set[str]:
    """The question ids whose default grading policy is geometry.

    Returns an empty set if the file or PyYAML is unavailable, so grading still
    runs (every question then uses the strict tolerance).
    """
    return geometry_graded_questions(load_questions(questions_path))


def grade_session(
    session_dir: Path,
    golden_dir: Path,
    geometry_ids: set[str] | None = None,
    questions: list[Question] | None = None,
) -> tuple[dict[str, str], dict[str, list[Diff]]]:
    """Grade every question against a session's answers/ dir.

    Returns the outcome per question and the diffs behind each failure.
    geometry_ids (question ids like 'q08') grade with the looser geometry
    tolerance; pass the result of geometry_graded_ids().

    The question set is the union of questions.yaml and the golden files. A
    question the fixture declares but the golden set lacks grades UNGRADEABLE
    rather than vanishing, because a question absent from grades.json reads to
    every downstream count as a question that was never asked, and strict
    success would then pass a trial that answered thirty of thirty-one.
    """
    geometry_ids = geometry_ids or set()
    question_by_id = {f"q{q['id']}": q for q in (questions or [])}
    golden_paths = {p.stem: p for p in sorted(golden_dir.glob("q*.csv"))}
    declared = set(question_by_id)
    grades: dict[str, str] = {}
    diffs: dict[str, list[Diff]] = {}
    for qid in sorted(declared | set(golden_paths)):
        golden_path = golden_paths.get(qid)
        if golden_path is None:
            grades[qid] = UNGRADEABLE
            continue
        answer_path = session_dir / "answers" / f"{qid}.csv"
        question = question_by_id.get(qid)
        column_policies = (
            column_grading_policies(question) if question is not None else None
        )
        question_geometry = qid in geometry_ids or bool(
            question and question.get("grading", EXACT) == GEOMETRY
        )
        outcome, cells = evaluate_question(
            answer_path,
            golden_path,
            geometry=question_geometry,
            column_policies=column_policies,
        )
        grades[qid] = outcome
        if cells:
            diffs[qid] = cells
    return grades, diffs


# The questions the task fails without, named in issue #29 as the minimum any
# ruling has to keep critical:
#   16  compliance classification (which classes fall in EUDR scope)
#   24  flagged-property completeness (every non-compliant cadaster accounted for)
#   26  routing outcome (nearest delivery facility under the routing rule)
#   28  candidate reconciliation across the flagged list
#   30  the final workflow.csv, identical to fixtures/golden/workflow.csv
#   31  input reconciliation (every row of the input list accounted for)
# A ruling may promote a question to critical. It may not demote one of these
# without amending the issue, and tests/test_grade.py holds that line.
CRITICAL_MINIMUM = frozenset({"16", "24", "26", "28", "30", "31"})


def critical_ids(questions: list[Question]) -> set[str]:
    """Question ids a trial must get right, as bare ids ('16', not 'q16').

    Every question is critical unless it says `critical: false`. The ruling
    today is that all 31 must pass: no question in fixtures/questions.yaml
    opts out, and the whole workflow is the deliverable. Marking a question
    `critical: false` makes it a diagnostic whose failure does not fail the
    trial, which is a spec change and belongs in a spec ruling rather than in
    a grader default.
    """
    return {q["id"] for q in questions if q.get("critical", True)}


def strict_success(grades: dict[str, str], questions: list[Question]) -> bool:
    """Whether this trial completed the whole workflow correctly.

    Every critical question graded CORRECT. NEAR_MISS does not pass: a near
    miss is a number close enough to triage against and not close enough to
    hand a compliance officer.

    A critical question with no grade fails. That covers the question the
    session never answered and the question the grader never reached, both of
    which are absences of evidence that the workflow ran end to end.
    """
    if not questions:
        return False
    return all(grades.get(f"q{qid}") == CORRECT for qid in critical_ids(questions))


def _deps_all_correct(
    qid: str, by_id: dict[str, Question], grades: dict[str, str]
) -> bool:
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


def stage_summary(
    grades: dict[str, str], questions: list[Question]
) -> dict[Any, dict[str, Any]]:
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
    out: dict[Any, dict[str, Any]] = {}
    for stage in sorted({q["stage"] for q in questions}):
        in_stage = [q for q in questions if q["stage"] == stage]
        n = len(in_stage)
        n_correct = sum(1 for q in in_stage if grades.get(f"q{q['id']}") == CORRECT)
        eligible = [q for q in in_stage if _deps_all_correct(q["id"], by_id, grades)]
        n_elig = len(eligible)
        n_elig_correct = sum(
            1 for q in eligible if grades.get(f"q{q['id']}") == CORRECT
        )
        out[stage] = {
            "n": n,
            "raw": n_correct / n if n else None,
            "n_eligible": n_elig,
            "conditional": (n_elig_correct / n_elig) if n_elig else None,
        }
    return out


def load_questions(questions_path: Path) -> list[Question]:
    """questions.yaml questions list, or [] if unavailable."""
    try:
        import yaml
    except ImportError:
        return []
    if not questions_path.exists():
        return []
    loaded = yaml.safe_load(questions_path.read_text(encoding="utf-8")) or {}
    questions: list[Question] = loaded.get("questions", [])
    for question in questions:
        _grading_policy(
            question.get("grading", EXACT),
            f"question q{question.get('id', '?')}",
        )
        column_grading_policies(question)
    return questions


def _mark_grader_error(meta: dict[str, Any]) -> None:
    """Invalidate the current grade without losing how execution ended."""
    if meta.get("status") != GRADER_ERROR:
        meta["execution_status"] = meta.get("status", "unknown")
    meta["status"] = GRADER_ERROR


def _restore_execution_status(meta: dict[str, Any]) -> None:
    """Clear a recovered grader error and restore the runner's outcome."""
    if meta.get("status") == GRADER_ERROR:
        meta["status"] = meta.pop("execution_status", DONE)


def _write_meta(
    session_dir: Path,
    meta: dict[str, Any],
    *,
    strict_success: bool,
    graded_against: str | None = None,
) -> None:
    """Record this grading pass in the run's meta, in one write.

    The strict verdict and the golden digest it was produced against are the
    same fact seen twice. Writing them separately leaves a window where a run
    claims a pass with no record of what it passed against.
    """
    meta["strict_success"] = strict_success
    if graded_against is not None:
        meta["graded_against"] = graded_against
    (session_dir / "meta.json").write_text(
        json.dumps(meta, indent=2) + "\n", encoding="utf-8"
    )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--results", type=Path, default=Path("results"))
    ap.add_argument("--golden", type=Path, default=Path("fixtures/golden"))
    ap.add_argument("--questions", type=Path, default=Path("fixtures/questions.yaml"))
    args = ap.parse_args()

    golden_files = list(args.golden.glob("q*.csv"))
    if not golden_files:
        print(f"no golden fixtures in {args.golden}", file=sys.stderr)
        return 1

    session_dirs = run_dirs(args.results)
    if not session_dirs:
        print(f"no sessions found under {args.results}", file=sys.stderr)
        return 1

    questions = load_questions(args.questions)
    geometry_ids = geometry_graded_questions(questions)
    graded_against = golden_fingerprint(args.golden)

    print(
        f"{'run':<34} {'correct':>8} {'near':>5} {'wrong':>6}"
        f" {'missing':>8} {'broken':>7}"
    )
    for session_dir in session_dirs:
        meta = read_meta(session_dir)
        if not is_scored(meta):
            # A session that wrote nothing never attempted the questions.
            # Scoring it as thirty wrong answers blames the model for a run
            # that did not happen, and drags every average it appears in.
            # It did fail the task, though, so it is recorded as a trial that
            # did not pass and stays in the reliability denominator.
            _write_meta(
                session_dir,
                meta,
                strict_success=False,
                graded_against=graded_against,
            )
            print(
                f"{run_name(session_dir):<34} "
                f"{meta.get('status', 'unknown')} — not scored, trial failed"
            )
            continue
        try:
            grades, diffs = grade_session(
                session_dir, args.golden, geometry_ids, questions
            )
        except Exception as exc:  # noqa: BLE001 - one bad run must not stop the rest
            # A grader that crashes has proven nothing about the agent, so the
            # trial leaves the denominator rather than counting as a failure.
            # The execution outcome is kept, because it is still the only
            # record of what the session itself did.
            _mark_grader_error(meta)
            _write_meta(
                session_dir,
                meta,
                strict_success=False,
                graded_against=graded_against,
            )
            print(f"{run_name(session_dir):<34} grader error: {exc}", file=sys.stderr)
            continue
        (session_dir / "grades.json").write_text(
            json.dumps(grades, indent=2, sort_keys=True) + "\n"
        )
        (session_dir / "diffs.json").write_text(
            json.dumps(diffs, indent=2, sort_keys=True) + "\n"
        )
        passed = strict_success(grades, questions)
        _restore_execution_status(meta)
        _write_meta(
            session_dir,
            meta,
            strict_success=passed,
            graded_against=graded_against,
        )
        if regraded(meta):
            print(
                f"{run_name(session_dir):<34} "
                f"re-graded: ran against {meta['golden_fingerprint']}, "
                f"scored against {graded_against}"
            )
        counts = {
            k: sum(1 for v in grades.values() if v == k)
            for k in (CORRECT, NEAR_MISS, WRONG, MISSING, UNPARSEABLE)
        }
        print(
            f"{run_name(session_dir):<34} {counts[CORRECT]:>8}"
            f" {counts[NEAR_MISS]:>5}"
            f" {counts[WRONG]:>6} {counts[MISSING]:>8}"
            f" {counts[UNPARSEABLE]:>7}"
        )
        if questions:
            failed = sorted(
                qid
                for qid in critical_ids(questions)
                if grades.get(f"q{qid}") != CORRECT
            )
            verdict = "PASS" if passed else "FAIL"
            detail = "" if passed else f" (critical: {', '.join(failed)})"
            print(f"    strict task success: {verdict}{detail}")
        for qid in sorted(diffs):
            print(f"    {qid} {grades[qid]}: {diff_summary(diffs[qid])}")
        if questions:
            summary = stage_summary(grades, questions)
            for stage, s in summary.items():
                raw = f"{s['raw']:.2f}" if s["raw"] is not None else "  - "
                cond = (
                    f"{s['conditional']:.2f}"
                    if s["conditional"] is not None
                    else "  - "
                )
                print(
                    f"    stage {stage}: raw {raw}  conditional {cond}"
                    f"  ({s['n_eligible']}/{s['n']} eligible)"
                )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
