"""Session assembly: the workspace gets the policies and the input list, and
never the golden fixtures. Does not require Docker."""

import sys
from pathlib import Path

import pytest

HARNESS = Path(__file__).resolve().parent.parent / "harness"
sys.path.insert(0, str(HARNESS))

import run  # noqa: E402


def test_list_files_resolves_each_mode():
    assert [p.name for p in run.list_files("csv")] == ["goias-sample.csv"]
    assert [p.name for p in run.list_files("split")] == [
        "goias-sample.csv", "goias-sample-geom.parquet"]
    assert run.INPUT_FILES["geometry"] == ["goias-sample.parquet"]


def test_csv_input_file_is_vendored():
    (csv_path,) = run.list_files("csv")
    assert csv_path.exists(), "the Goiás list must be committed for experiment 1"


def test_run_py_never_copies_the_golden_fixtures():
    """Structural guard: if run.py ever copied fixtures/golden into a workspace,
    the benchmark would be void. Check the copy operations, not the prose — the
    docstring is allowed to say the goldens are never mounted."""
    for line in (HARNESS / "run.py").read_text(encoding="utf-8").splitlines():
        if "shutil.copy" in line or "copytree" in line:
            assert "golden" not in line.lower(), line.strip()


def test_dry_run_assembles_workspace_without_docker(monkeypatch, capsys):
    # dry_run prints the docker command and returns before invoking anything;
    # it still copies policies + the list, so this exercises the mounting.
    class FakePrice:
        model_id = "claude-haiku-4-5-20251001"

    monkeypatch.setitem(run.PRICES, "haiku", FakePrice())
    run.run_session("haiku", 1, dry_run=True, input_mode="csv")
    out = capsys.readouterr().out
    assert "docker run" in out
    assert "--model claude-haiku-4-5-20251001" in out
