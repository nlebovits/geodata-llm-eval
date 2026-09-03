"""Tests for the Vale baseline comparator."""

from __future__ import annotations

import json
from pathlib import Path
import sys
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import compare_vale


def _alert(match: str = "bad", check: str = "Geodata.Term") -> dict[str, Any]:
    return {
        "Check": check,
        "Line": 10,
        "Message": "Rewrite it.",
        "Match": match,
        "Severity": "error",
    }


def _report(
    *alerts: dict[str, Any], path: str = "policies/a.md"
) -> compare_vale.Report:
    return {path: list(alerts)}


def test_an_unchanged_finding_is_not_new() -> None:
    item = _alert()
    assert not compare_vale.added_findings(_report(item), _report(item))


def test_line_number_changes_do_not_create_a_finding() -> None:
    before = _alert()
    after = dict(before, Line=90)
    assert not compare_vale.added_findings(_report(before), _report(after))


def test_an_added_finding_and_an_extra_occurrence_are_new() -> None:
    old = _alert("old")
    new = _alert("new")
    added = compare_vale.added_findings(_report(old), _report(old, new))
    assert sum(added.values()) == 1
    assert next(iter(added))[2] == "new"

    duplicate = compare_vale.added_findings(_report(old), _report(old, old))
    assert sum(duplicate.values()) == 1


def test_a_rename_preserves_the_baseline(tmp_path: Path) -> None:
    item = _alert()
    rename_data = tmp_path / "renames"
    rename_data.write_bytes(b"R100\0policies/old.md\0policies/new.md\0")

    renames = compare_vale.read_renames(rename_data)
    assert not compare_vale.added_findings(
        _report(item, path="policies/old.md"),
        _report(item, path="policies/new.md"),
        renames,
    )


def test_name_status_parser_skips_non_rename_records(tmp_path: Path) -> None:
    data = tmp_path / "names"
    data.write_bytes(
        b"M\0policies/a.md\0R095\0prompts/old.md\0prompts/new.md\0D\0gone.md\0"
    )
    assert compare_vale.read_renames(data) == {
        "prompts/old.md": "prompts/new.md"
    }


def test_main_reports_new_findings(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    baseline = tmp_path / "baseline.json"
    current = tmp_path / "current.json"
    baseline.write_text(json.dumps({}), encoding="utf-8")
    current.write_text(json.dumps(_report(_alert())), encoding="utf-8")

    assert compare_vale.main([str(baseline), str(current)]) == 1
    assert "adds Vale errors" in capsys.readouterr().err


def test_main_rejects_empty_or_invalid_reports(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    empty = tmp_path / "empty.json"
    invalid = tmp_path / "invalid.json"
    valid = tmp_path / "valid.json"
    empty.write_text("", encoding="utf-8")
    invalid.write_text("[]", encoding="utf-8")
    valid.write_text("{}", encoding="utf-8")

    assert compare_vale.main([str(empty), str(valid)]) == 2
    assert compare_vale.main([str(invalid), str(valid)]) == 2
    assert "Cannot compare Vale reports" in capsys.readouterr().err
