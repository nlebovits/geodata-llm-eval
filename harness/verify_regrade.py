"""Check that a grader change only freed distinctions someone declared.

The acceptance criterion for the spec/grader/oracle boundary work, made
mechanical. Comparing two gradings of the same stored answers:

1. No question may go from correct to not-correct.
2. Every question that goes from not-correct to correct must do so because of
   a freedom the fixtures declare. The evidence is the earlier grading's
   diffs.json, which records the golden and answer value behind every
   mismatched cell, so each newly-passing cell can be attributed to a named
   reason via grade.explain_match.

Both are needed. A column can hold a substantive difference and a
representational one at once, so "the changed cells are in the expected
column" would pass a migration that quietly started accepting wrong answers.

This proves grader neutrality only. Editing SPEC.md changes what a future
session writes, and no re-grading of stored answers can show that; only a new
sweep can.

Usage:
    python harness/verify_regrade.py BEFORE_RESULTS AFTER_RESULTS
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

from grade import (
    CORRECT,
    ColumnRule,
    column_rules_for,
    explain_match,
    load_header,
    load_questions,
)
from layout import run_dirs

# A flip with no cell-level evidence cannot be checked against criterion 2.
# diff_table reports a row- or column-count mismatch as a single shape record
# and stops, and a missing or unparseable answer produces no diffs at all.
UNVERIFIABLE = "no cell-level evidence in the earlier grading"


def _read_json(path: Path) -> dict:
    """A run's grades or diffs, or {} when the file is absent."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _rules_by_column(
    questions: list[dict], golden_dir: Path
) -> dict[str, dict[str, ColumnRule]]:
    """Each question's column rules, keyed by the golden header name.

    diffs.json names a cell by its golden column name, and column rules are
    declared in golden column order, so the two line up by position here once
    rather than at every record.
    """
    out: dict[str, dict[str, ColumnRule]] = {}
    for question in questions:
        qid = f"q{question['id']}"
        rules = column_rules_for(question)
        if rules is None:
            continue
        header = load_header(golden_dir / f"{qid}.csv")
        out[qid] = dict(zip(header, rules))
    return out


def _check_flip(
    records: list[dict], rules: dict[str, ColumnRule]
) -> tuple[list[tuple[str, object, object, str]], list[str]]:
    """Attribute one newly-passing question's earlier mismatches.

    Returns the freed (column, golden, answer, reason) evidence and the
    failures that mean the flip was not representational after all.
    """
    if not records:
        return [], [UNVERIFIABLE]
    freed: list[tuple[str, object, object, str]] = []
    failures: list[str] = []
    for record in records:
        if record.get("kind") != "cell":
            failures.append(UNVERIFIABLE)
            continue
        column = record.get("column", "?")
        golden, answer = record.get("golden"), record.get("answer")
        reason = explain_match(answer, golden, rules.get(column))
        if reason is None:
            failures.append(
                f"{column}: {answer!r} vs golden {golden!r} is a substantive "
                "difference, not a declared freedom"
            )
            continue
        freed.append((column, golden, answer, reason))
    return freed, failures


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("before", type=Path, help="results tree graded before")
    ap.add_argument("after", type=Path, help="results tree graded after")
    ap.add_argument("--golden", type=Path, default=Path("fixtures/golden"))
    ap.add_argument("--questions", type=Path, default=Path("fixtures/questions.yaml"))
    args = ap.parse_args()

    rules_by_question = _rules_by_column(load_questions(args.questions), args.golden)

    regressions: list[str] = []
    unverified: list[str] = []
    freed_by_reason: Counter[str] = Counter()
    freed_values: set[tuple[str, str, str, str]] = set()
    n_runs = 0
    n_flips = 0

    for after_dir in run_dirs(args.after):
        relative = after_dir.relative_to(args.after)
        before_dir = args.before / relative
        if not before_dir.exists():
            continue
        n_runs += 1
        before = _read_json(before_dir / "grades.json")
        after = _read_json(after_dir / "grades.json")
        diffs = _read_json(before_dir / "diffs.json")
        for qid in sorted(set(before) | set(after)):
            was, now = before.get(qid), after.get(qid)
            if was == CORRECT and now != CORRECT:
                regressions.append(f"{relative} {qid}: {was} -> {now}")
                continue
            if was == CORRECT or now != CORRECT:
                continue
            n_flips += 1
            freed, failures = _check_flip(
                diffs.get(qid, []), rules_by_question.get(qid, {})
            )
            for column, golden, answer, reason in freed:
                freed_by_reason[reason] += 1
                freed_values.add((qid, column, str(golden), str(answer)))
            for failure in failures:
                unverified.append(f"{relative} {qid}: {failure}")

    print(f"runs compared: {n_runs}")
    print(f"questions newly correct: {n_flips}")

    if freed_by_reason:
        print("\nfreed cells by reason:")
        for reason, count in freed_by_reason.most_common():
            print(f"  {count:>5}  {reason}")
        print("\nevery distinct value pair freed, for review:")
        for qid, column, golden, answer in sorted(freed_values):
            print(f"  {qid}.{column}: answer {answer!r} accepted for {golden!r}")

    if regressions:
        print(f"\nFAIL: {len(regressions)} correct answers regressed")
        for line in regressions:
            print(f"  {line}")
    if unverified:
        print(f"\nFAIL: {len(unverified)} flips are not representational")
        for line in unverified:
            print(f"  {line}")

    if regressions or unverified:
        return 1
    print("\nOK: no regression, and every new pass is a declared freedom")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
