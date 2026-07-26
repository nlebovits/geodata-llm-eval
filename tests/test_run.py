"""Session assembly: the workspace gets the policies and the input list, and
never the golden fixtures. Does not require Docker."""

import json
import os
import re
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
HARNESS = REPO_ROOT / "harness"
sys.path.insert(0, str(HARNESS))

import run  # noqa: E402


def test_image_duckdb_matches_the_pin():
    """The oracle and the session must read the catalogs with the same engine.
    A stale image pin silently voids a whole run: the catalogs are GeoParquet
    2.0.0, spatial rejects that before 1.5, and a session that cannot read a
    geometry answers the spatial stages from guesswork instead of failing."""
    pinned = json.loads(
        (REPO_ROOT / "fixtures" / "pins.json").read_text(encoding="utf-8")
    )["duckdb_version"]
    dockerfile = (REPO_ROOT / "Dockerfile").read_text(encoding="utf-8")

    match = re.search(r"^ARG DUCKDB_VERSION=(\S+)", dockerfile, re.MULTILINE)
    assert match, "Dockerfile must pin DUCKDB_VERSION"
    assert match.group(1) == pinned, (
        f"Dockerfile pins DuckDB {match.group(1)}, fixtures/pins.json pins "
        f"{pinned}; rebuild the image after changing either"
    )


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


def fake_credentials(tmp_path, monkeypatch):
    """Point the harness at a decoy login so no test reads the real one."""
    cred = tmp_path / "host-credentials.json"
    cred.write_text('{"accessToken": "decoy"}', encoding="utf-8")
    monkeypatch.setattr(run, "CREDENTIALS", cred)
    return cred


def test_dry_run_assembles_workspace_without_docker(monkeypatch, capsys,
                                                    tmp_path):
    # dry_run prints the docker command and returns before invoking anything;
    # it still copies policies + the list, so this exercises the mounting.
    class FakePrice:
        model_id = "claude-haiku-4-5-20251001"

    fake_credentials(tmp_path, monkeypatch)
    monkeypatch.setitem(run.PRICES, "haiku", FakePrice())
    run.run_session("haiku", 1, dry_run=True, input_mode="csv")
    out = capsys.readouterr().out
    assert "docker run" in out
    assert "--model claude-haiku-4-5-20251001" in out


def test_auth_mounts_a_session_copy_not_the_host_credentials(monkeypatch,
                                                             tmp_path):
    """The host file is copied, never bind-mounted. A session that corrupts
    or rewrites the token must not be able to reach the real login."""
    cred = fake_credentials(tmp_path, monkeypatch)
    session_home = tmp_path / "home"
    session_home.mkdir()

    args = run.auth_args(session_home)

    (mount,) = [a for a in args if ":" in a and a != "-v"]
    host_side = mount.rsplit(":", 1)[0]
    assert host_side != str(cred), "must not mount the host credentials file"
    assert Path(host_side) == session_home / ".claude"
    copy = session_home / ".claude" / ".credentials.json"
    assert copy.read_text(encoding="utf-8") == cred.read_text(encoding="utf-8")
    assert copy.stat().st_mode & 0o077 == 0, "copy must not be group/world readable"


def test_auth_mount_is_writable_for_token_refresh(monkeypatch, tmp_path):
    """An expiring OAuth token is refreshed by writing the file back, so a
    :ro mount would strand long sessions on an expired token."""
    fake_credentials(tmp_path, monkeypatch)
    session_home = tmp_path / "home"
    session_home.mkdir()

    (mount,) = [a for a in run.auth_args(session_home) if a != "-v"]
    assert not mount.endswith(":ro")


def test_auth_falls_back_to_api_key(monkeypatch, tmp_path):
    monkeypatch.setattr(run, "CREDENTIALS", tmp_path / "absent.json")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-decoy")
    assert run.auth_args(tmp_path / "home") == ["-e", "ANTHROPIC_API_KEY"]


def test_auth_refuses_to_launch_without_credentials(monkeypatch, tmp_path):
    """Failing here beats burning a container to fail on the first call."""
    monkeypatch.setattr(run, "CREDENTIALS", tmp_path / "absent.json")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(SystemExit):
        run.auth_args(tmp_path / "home")


def test_container_runs_as_the_host_user(monkeypatch, tmp_path):
    """The credential copy is 0600 and host-owned. A container uid that is not
    its owner can neither read the token nor write a refreshed one back, and
    the image's own account has a uid fixed at build time — so the run has to
    carry the host's."""
    fake_credentials(tmp_path, monkeypatch)
    session_home = tmp_path / "home"
    session_home.mkdir()

    cmd = run.docker_command(tmp_path / "workspace", session_home, "m")

    assert "--user" in cmd, "container must not run as a build-time uid"
    assert cmd[cmd.index("--user") + 1] == f"{os.getuid()}:{os.getgid()}"


def test_credentials_never_land_inside_the_mounted_workspace(monkeypatch,
                                                             tmp_path):
    """The agent is pointed at /workspace with permissions skipped. The
    credential copy lives in a sibling directory, not under it."""
    fake_credentials(tmp_path, monkeypatch)
    session_home = tmp_path / "home"
    workspace = tmp_path / "workspace"
    session_home.mkdir()
    workspace.mkdir()

    run.auth_args(session_home)

    assert not list(workspace.rglob("*credentials*"))
