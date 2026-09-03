"""Session assembly: the workspace gets the exact spec and the input
list, and never the golden fixtures. Does not require Docker."""

import itertools
import json
import os
import re
import shutil
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import IO, Any, Literal, Self

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
HARNESS = REPO_ROOT / "harness"
sys.path.insert(0, str(HARNESS))

import run


def test_image_duckdb_matches_the_pin() -> None:
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


def test_list_files_resolves_each_mode() -> None:
    assert [p.name for p in run.list_files("csv")] == ["goias-sample.csv"]
    assert [p.name for p in run.list_files("split")] == [
        "goias-sample.csv",
        "goias-sample-geom.parquet",
    ]
    assert run.INPUT_FILES["geometry"] == ["goias-sample.parquet"]


def test_csv_input_file_is_vendored() -> None:
    (csv_path,) = run.list_files("csv")
    assert csv_path.exists(), "the Goiás list must be committed for experiment 1"


def test_run_py_never_copies_the_golden_fixtures() -> None:
    """Structural guard: if run.py ever copied fixtures/golden into a workspace,
    the benchmark would be void. Check the copy operations, not the prose — the
    docstring is allowed to say the goldens are never mounted."""
    for line in (HARNESS / "run.py").read_text(encoding="utf-8").splitlines():
        if "shutil.copy" in line or "copytree" in line:
            assert "golden" not in line.lower(), line.strip()


def test_the_ablation_config_never_reaches_the_workspace() -> None:
    """The workspace receives only the exact SPEC.md and the input list. A
    session that could read the ablation config would be handed an itemised
    list of what was withheld from it, which is the one thing an ablation must
    not reveal. This structural check ensures no assembly line copies or writes the
    config, and it lives outside anything that is mounted."""
    assert run.ABLATIONS.exists(), "the shipped ablation config must be committed"
    source = (HARNESS / "run.py").read_text(encoding="utf-8")
    for line in source.splitlines():
        if "shutil.copy" in line or "copytree" in line or "write_text" in line:
            assert "ablation" not in line.lower(), line.strip()


def test_an_ablated_run_assembles_the_workspace_without_the_dropped_policy(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """The receipt printed here is what makes "did this arm do anything?"
    answerable before a sweep is paid for rather than after."""

    class FakePrice:
        model_id = "claude-haiku-4-5-20251001"

    fake_credentials(tmp_path, monkeypatch)
    monkeypatch.setitem(run.PRICES, "haiku", FakePrice())
    run.run_session("haiku", dry_run=True, input_mode="csv", arm="no-coops")
    out = capsys.readouterr().out

    assert "arm no-coops" in out
    assert "removed" in out and "SPEC.md" in out
    lines = [ln for ln in out.splitlines() if "removed" in ln]
    assert lines and all("SPEC.md" in ln for ln in lines)


def test_an_unknown_arm_fails_before_a_container_starts(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A mistyped arm that fell through to the full spec would score like the
    baseline and read as "the withheld text did not matter"."""

    class FakePrice:
        model_id = "claude-haiku-4-5-20251001"

    fake_credentials(tmp_path, monkeypatch)
    monkeypatch.setitem(run.PRICES, "haiku", FakePrice())
    with pytest.raises(run.ablation.AblationError, match="unknown arm"):
        run.run_session("haiku", dry_run=True, arm="no-such-arm")


def test_a_plain_run_records_the_full_spec_and_reads_no_config(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """Every run already on disk saw the whole spec, so an unablated run has
    to group with them rather than becoming a fourth category."""

    class FakePrice:
        model_id = "claude-haiku-4-5-20251001"

    fake_credentials(tmp_path, monkeypatch)
    monkeypatch.setitem(run.PRICES, "haiku", FakePrice())
    monkeypatch.setattr(run, "ABLATIONS", tmp_path / "does-not-exist.yaml")
    run.run_session("haiku", dry_run=True, input_mode="csv")
    out = capsys.readouterr().out

    assert f"arm {run.FULL_SPEC}" in out
    assert "SPEC.md" in out and "removed" not in out


def fake_credentials(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the harness at a decoy login so no test reads the real one."""
    cred = tmp_path / "host-credentials.json"
    cred.write_text('{"accessToken": "decoy"}', encoding="utf-8")
    monkeypatch.setattr(run, "CREDENTIALS", cred)
    return cred


def test_dry_run_assembles_workspace_without_docker(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    # dry_run prints the docker command and returns before invoking anything;
    # it still stages the spec and copies the list, so this exercises mounting.
    class FakePrice:
        model_id = "claude-haiku-4-5-20251001"

    fake_credentials(tmp_path, monkeypatch)
    monkeypatch.setitem(run.PRICES, "haiku", FakePrice())
    run.run_session("haiku", dry_run=True, input_mode="csv")
    out = capsys.readouterr().out
    assert "docker run" in out
    assert "--model claude-haiku-4-5-20251001" in out


def test_workspace_gets_the_exact_contract_and_no_review_notes(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    run.assemble_workspace(workspace, "csv")

    assert (workspace / "SPEC.md").read_bytes() == (REPO_ROOT / "SPEC.md").read_bytes()
    assert not (workspace / "docs").exists()
    assert "REVIEW_ONLY_CANARY" not in (workspace / "SPEC.md").read_text("utf-8")


def test_auth_mounts_a_session_copy_not_the_host_credentials(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
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


def test_auth_mount_is_writable_for_token_refresh(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """An expiring OAuth token is refreshed by writing the file back, so a
    :ro mount would strand long sessions on an expired token."""
    fake_credentials(tmp_path, monkeypatch)
    session_home = tmp_path / "home"
    session_home.mkdir()

    (mount,) = [a for a in run.auth_args(session_home) if a != "-v"]
    assert not mount.endswith(":ro")


def test_auth_falls_back_to_api_key(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(run, "CREDENTIALS", tmp_path / "absent.json")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-decoy")
    assert run.auth_args(tmp_path / "home") == ["-e", "ANTHROPIC_API_KEY"]


def test_auth_refuses_to_launch_without_credentials(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Failing here beats burning a container to fail on the first call."""
    monkeypatch.setattr(run, "CREDENTIALS", tmp_path / "absent.json")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(SystemExit):
        run.auth_args(tmp_path / "home")


def test_container_runs_as_the_host_user(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The credential copy is 0600 and host-owned. A container uid that is not
    its owner can neither read the token nor write a refreshed one back, and
    the image's own account has a uid fixed at build time — so the run has to
    carry the host's."""
    fake_credentials(tmp_path, monkeypatch)
    session_home = tmp_path / "home"
    session_home.mkdir()

    cmd = run.docker_command(tmp_path / "workspace", session_home, "m", "c1")

    assert "--user" in cmd, "container must not run as a build-time uid"
    assert cmd[cmd.index("--user") + 1] == f"{os.getuid()}:{os.getgid()}"


def test_credentials_never_land_inside_the_mounted_workspace(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The agent is pointed at /workspace with permissions skipped. The
    credential copy lives in a sibling directory, not under it."""
    fake_credentials(tmp_path, monkeypatch)
    session_home = tmp_path / "home"
    workspace = tmp_path / "workspace"
    session_home.mkdir()
    workspace.mkdir()

    run.auth_args(session_home)

    assert not list(workspace.rglob("*credentials*"))


def write_transcript(tmp_path: Path, records: list[dict[str, Any]]) -> Path:
    path = tmp_path / "transcript.jsonl"
    path.write_text(
        "".join(json.dumps(r) + "\n" for r in records),
        encoding="utf-8",
    )
    return path


def test_a_rejected_credential_is_read_off_the_transcript(tmp_path: Path) -> None:
    """The mounted token copy expires, and a session starting after it does
    gets a 401 on its first call, retries ten times inside the CLI, then
    exits having written nothing. To the resume loop that is indistinguishable
    from a session that stopped with work left, so it starts it again into the
    same rejection. One sweep spent all three attempts on that and returned
    the arm at n=1."""
    dead = write_transcript(
        tmp_path,
        [
            {"type": "system", "subtype": "init", "session_id": "s"},
            {
                "type": "system",
                "subtype": "api_retry",
                "attempt": 1,
                "error_status": 401,
                "error": "authentication_failed",
            },
            {"type": "result", "is_error": True},
        ],
    )
    assert run.credential_rejected(dead)


def test_a_transcript_with_no_401_is_not_read_as_a_credential_failure(
    tmp_path: Path,
) -> None:
    healthy = write_transcript(
        tmp_path,
        [
            {"type": "system", "subtype": "init", "session_id": "s"},
            {
                "type": "system",
                "subtype": "api_retry",
                "attempt": 1,
                "error_status": 529,
                "error": "overloaded",
            },
            {"type": "result", "is_error": False},
        ],
    )
    assert not run.credential_rejected(healthy)


def test_the_credential_check_reads_every_attempt_not_just_the_last(
    tmp_path: Path,
) -> None:
    """The run that prompted this logged no api_retry on its third attempt:
    by then the CLI could not find its config file and failed before reaching
    the API at all. A check scoped to the latest attempt would have missed the
    failure it was written for."""
    path = write_transcript(
        tmp_path,
        [
            {"type": "system", "subtype": "init", "session_id": "s"},
            {"type": "system", "subtype": "api_retry", "error_status": 401},
            {"type": "result", "is_error": True},
            {"type": "system", "subtype": "init", "session_id": "s"},
            {"type": "assistant", "message": {"content": []}},
            {"type": "result", "is_error": True},
        ],
    )
    assert run.credential_rejected(path)


def heartbeat(call_id: str, tool: str, seconds: float) -> dict[str, Any]:
    return {
        "type": "tool_heartbeat",
        "tool_use_id": call_id,
        "tool_name": tool,
        "elapsed_time_seconds": seconds,
    }


def test_tool_timings_take_the_last_heartbeat_per_call(tmp_path: Path) -> None:
    """Heartbeats report a running elapsed for one call, so summing them all
    would count a single slow query several times over."""
    path = write_transcript(
        tmp_path,
        [
            heartbeat("a", "Bash", 30),
            heartbeat("a", "Bash", 60),
            heartbeat("a", "Bash", 90),
        ],
    )

    timings = run.tool_timings(path)

    assert timings["slow_tool_calls"] == 1
    assert timings["slow_tool_seconds"] == 90
    assert timings["slow_tool_seconds_by_tool"] == {"Bash": 90.0}


def test_tool_timings_count_calls_that_hit_the_cap(tmp_path: Path) -> None:
    """A call at the cap was killed, not answered. That is the difference
    between a slow query and one that never returned, and a run whose wall
    clock is mostly killed queries has measured nothing."""
    path = write_transcript(
        tmp_path,
        [
            heartbeat("a", "Bash", run.TOOL_TIMEOUT_SECONDS),
            heartbeat("b", "Bash", run.TOOL_TIMEOUT_SECONDS - 1),
        ],
    )

    assert run.tool_timings(path)["timed_out_tool_calls"] == 1


def test_tool_timings_survive_a_transcript_still_being_written(tmp_path: Path) -> None:
    """The heartbeat reads a file the container has open, so the last line is
    routinely half-written."""
    path = write_transcript(tmp_path, [heartbeat("a", "Bash", 30)])
    with open(path, "a", encoding="utf-8") as f:
        f.write('{"type": "tool_hea')

    assert run.tool_timings(path)["slow_tool_calls"] == 1


def test_progress_snapshot_reports_turns_and_the_last_tool(tmp_path: Path) -> None:
    path = write_transcript(
        tmp_path,
        [
            {
                "type": "assistant",
                "message": {"content": [{"type": "tool_use", "name": "Read"}]},
            },
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {"type": "text", "text": "thinking"},
                        {"type": "tool_use", "name": "Bash"},
                    ]
                },
            },
            {"type": "user", "message": {"content": []}},
        ],
    )

    assert run.progress_snapshot(path) == (2, "Bash")


def test_progress_snapshot_handles_an_absent_transcript(tmp_path: Path) -> None:
    """The first heartbeat can land before the container writes a line."""
    assert run.progress_snapshot(tmp_path / "nope.jsonl") == (0, "-")


def test_a_run_is_named_for_when_it_ran_and_what_it_ran() -> None:
    """The name used to be a position in a sequence, so two runs could want
    it and the second destroyed the first. Every guard against that -- the
    scan for a free number, --start-pass, --force -- managed a collision a
    timestamp plus nonce cannot have."""
    started = datetime(2026, 7, 26, 11, 46, 46, tzinfo=UTC)
    name = run.run_id(started, "7f8fb7a3aa1f224ee05e7dd14f13a782b0a6e3ca", "fixed123")
    assert name == "20260726T114646Z-7f8fb7a-fixed123"


def test_run_names_sort_chronologically() -> None:
    """Run ids are read and globbed as strings, so ordering has to fall out
    of the name rather than out of a stat call."""
    earlier = run.run_id(datetime(2026, 7, 26, 9, 0, tzinfo=UTC), "a" * 40)
    later = run.run_id(datetime(2026, 7, 26, 17, 0, tzinfo=UTC), "b" * 40)
    assert sorted([later, earlier]) == [earlier, later]


def test_two_runs_at_the_same_instant_do_not_collide() -> None:
    started = datetime(2026, 7, 26, 9, 0, 0, tzinfo=UTC)

    first = run.run_id(started, "abc1234")
    second = run.run_id(started, "abc1234")

    assert first != second


def test_two_runs_a_second_apart_do_not_collide() -> None:
    a = run.run_id(datetime(2026, 7, 26, 9, 0, 0, tzinfo=UTC), "abc1234")
    b = run.run_id(datetime(2026, 7, 26, 9, 0, 1, tzinfo=UTC), "abc1234")
    assert a != b


def test_the_collision_guards_are_gone(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """--force and --start-pass existed only to arbitrate a name clash."""
    monkeypatch.setattr(run, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(sys, "argv", ["run.py", "--model", "opus", "--passes", "2"])
    calls = []
    monkeypatch.setattr(run, "run_session", lambda *a, **k: calls.append(a))

    assert run.main() == 0
    assert len(calls) == 2
    assert not hasattr(run, "next_free_pass")


def test_a_label_groups_runs_without_moving_them(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """More than ten runs of one configuration needs a way to say which runs
    belong together, and a directory convention is the wrong place for it."""
    monkeypatch.setattr(run, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(
        sys,
        "argv",
        ["run.py", "--model", "opus", "--passes", "1", "--label", "experiment-1"],
    )
    seen = []
    monkeypatch.setattr(run, "run_session", lambda *a, **k: seen.append(a[-1]))

    assert run.main() == 0
    assert seen == ["experiment-1"]


# --- observability and cleanup ----------------------------------------------


def transcript_line(name: str, payload: dict[str, Any]) -> str:
    return (
        json.dumps(
            {
                "type": "assistant",
                "message": {
                    "content": [{"type": "tool_use", "name": name, "input": payload}]
                },
            }
        )
        + "\n"
    )


def result_line(call_id: str, text: str, is_error: bool = False) -> str:
    return (
        json.dumps(
            {
                "type": "user",
                "message": {
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": call_id,
                            "is_error": is_error,
                            "content": [{"type": "text", "text": text}],
                        }
                    ]
                },
            }
        )
        + "\n"
    )


def call_line(call_id: str, name: str, payload: dict[str, Any]) -> str:
    return (
        json.dumps(
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {
                            "type": "tool_use",
                            "id": call_id,
                            "name": name,
                            "input": payload,
                        }
                    ]
                },
            }
        )
        + "\n"
    )


def heartbeat_line(call_id: str, seconds: float) -> str:
    return (
        json.dumps(
            {
                "type": "tool_progress",
                "tool_use_id": f"{call_id}-hb",
                "parent_tool_use_id": call_id,
                "tool_name": "Bash",
                "elapsed_time_seconds": seconds,
                "heartbeat": True,
            }
        )
        + "\n"
    )


def test_follow_prints_each_tool_call_as_it_lands(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Watching a running session meant tailing the transcript in a second
    terminal and reassembling it with jq. --follow does it in place."""
    path = tmp_path / "transcript.jsonl"
    path.write_text(call_line("t1", "Bash", {"command": "duckdb -c 'SELECT 1'"}))

    follower = run.Follower()
    follower.consume(path)
    assert "Bash      duckdb -c 'SELECT 1'" in capsys.readouterr().out

    # A second consume prints only what arrived since.
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(call_line("t2", "Write", {"file_path": "/workspace/q01.csv"}))
    follower.consume(path)
    out = capsys.readouterr().out
    assert "q01.csv" in out and "duckdb" not in out


def test_follow_times_each_call_and_names_the_failures(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A call and its result are two records. Printing only the call made a
    failed query look exactly like a successful one."""
    path = tmp_path / "transcript.jsonl"
    ticks = iter([100.0, 112.5, 112.5, 118.0])
    follower = run.Follower(clock=lambda: next(ticks))

    path.write_text(
        call_line("t1", "Bash", {"command": "duckdb < build.sql"})
        + result_line(
            "t1", "Invalid Error: Failure receiving data from peer", is_error=True
        )
        + call_line("t2", "Bash", {"command": "echo ok"})
        + result_line("t2", "ok")
    )
    follower.consume(path)
    out = capsys.readouterr().out

    assert "FAILED after 12.5s: Invalid Error: Failure receiving data" in out
    assert "↳ 5.5s" in out


def test_follow_shows_a_pulse_while_a_call_blocks(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A four-minute query printed nothing at all, which is why a working
    session and a dead one looked the same."""
    path = tmp_path / "transcript.jsonl"
    follower = run.Follower()
    path.write_text(
        call_line("t1", "Bash", {"command": "duckdb < slow.sql"})
        + heartbeat_line("t1", 30)  # under the interval, stays quiet
        + heartbeat_line("t1", 60)
        + heartbeat_line("t1", 90)  # under the interval again
        + heartbeat_line("t1", 120)
    )
    follower.consume(path)
    beats = [ln for ln in capsys.readouterr().out.splitlines() if "still running" in ln]
    assert [b.split(", ")[-1] for b in beats] == ["60s", "120s"]


def test_follow_reports_how_long_the_current_call_has_blocked(tmp_path: Path) -> None:
    """The periodic summary said "last: Bash" whether that call started two
    seconds or four minutes ago."""
    path = tmp_path / "transcript.jsonl"
    ticks = iter([1000.0, 1240.0])
    follower = run.Follower(clock=lambda: next(ticks))
    path.write_text(call_line("t1", "Bash", {"command": "duckdb < slow.sql"}))

    follower.consume(path)
    assert follower.running_for() == 240.0


def test_follow_forgets_a_call_once_it_finishes(tmp_path: Path) -> None:
    path = tmp_path / "transcript.jsonl"
    follower = run.Follower()
    path.write_text(
        call_line("t1", "Bash", {"command": "echo hi"}) + result_line("t1", "hi")
    )
    follower.consume(path)
    assert follower.running_for() == 0.0


def test_follow_waits_for_a_half_written_record(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The harness reads the transcript while the container writes it, so the
    last line is regularly incomplete. Parsing it would drop the record."""
    path = tmp_path / "transcript.jsonl"
    whole = call_line("t1", "Bash", {"command": "echo one"})
    path.write_text(whole + '{"type": "assistant", "mess')

    follower = run.Follower()
    follower.consume(path)
    assert follower.offset == len(whole.encode())
    assert "echo one" in capsys.readouterr().out

    with open(path, "w", encoding="utf-8") as fh:
        fh.write(whole + call_line("t2", "Bash", {"command": "echo two"}))
    follower.consume(path)
    assert "echo two" in capsys.readouterr().out


def test_follow_collapses_a_multiline_command_to_one_line(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Sessions write heredocs of SQL. The transcript keeps the full text; the
    terminal gets one line per call."""
    path = tmp_path / "transcript.jsonl"
    path.write_text(
        call_line("t1", "Bash", {"command": "cat > q.sql <<'EOF'\nSELECT 1;\nEOF"})
    )

    run.Follower().consume(path)
    printed = capsys.readouterr().out.strip().splitlines()
    assert len(printed) == 1
    assert len(printed[0]) <= run.FOLLOW_WIDTH


def test_a_rerun_leaves_nothing_of_the_run_before_it(tmp_path: Path) -> None:
    """A pass is one session's record. Grades and answers surviving from the
    previous attempt read as part of the new run and are not."""
    out_dir = tmp_path / "pass-1"
    (out_dir / "answers").mkdir(parents=True)
    (out_dir / "answers" / "q01.csv").write_text("value\n1\n")
    (out_dir / "grades.json").write_text("{}")
    (out_dir / "transcript.jsonl").write_text("stale\n")

    run.clear_pass_dir(out_dir)
    assert list(out_dir.iterdir()) == []


def test_the_container_is_named_and_the_image_is_still_the_last_word(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`docker run --rm` cleans up only a container it is still attached to,
    so an interrupted run needs the name to remove its own container after
    the fact.

    The image has to stay the first positional argument. A flag that takes a
    value (`-v` wants a mount spec) placed before `--name` swallows it, and
    docker then reads the container name as the image: "pull access denied
    for geodata-eval-opus-pass5-1378874".
    """
    fake_credentials(tmp_path, monkeypatch)
    session_home = tmp_path / "home"
    session_home.mkdir()

    cmd = run.docker_command(
        tmp_path / "workspace", session_home, "m", "geodata-eval-opus-pass3-42"
    )

    assert cmd[:5] == ["docker", "run", "--rm", "--name", "geodata-eval-opus-pass3-42"]
    # Every -v carries its mount spec in the same token, so none can swallow
    # the argument that follows it.
    assert all(arg != "-v" or ":" in cmd[i + 1] for i, arg in enumerate(cmd[:-1]))


def test_stop_session_removes_a_container_the_client_left_running(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ctrl-C kills the docker client, not the container it started."""
    calls = []
    monkeypatch.setattr(run.subprocess, "run", lambda cmd, **kw: calls.append(cmd))

    class Finished:
        returncode = 0

        def poll(self) -> int | None:
            return 0

        # A session that already exited must not be signalled. Raising here
        # turns "we never call these" from a comment into a check.
        def terminate(self) -> None:
            raise AssertionError("a finished session was terminated")

        def kill(self) -> None:
            raise AssertionError("a finished session was killed")

        def wait(self, timeout: float | None = None) -> int:
            raise AssertionError("a finished session was waited on")

    run.stop_session(Finished(), "geodata-eval-opus-pass3-42")
    assert calls == [["docker", "rm", "-f", "-v", "geodata-eval-opus-pass3-42"]]


def test_stop_session_terminates_a_client_still_running(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(run.subprocess, "run", lambda cmd, **kw: None)
    events = []

    class Running:
        def poll(self) -> int | None:
            return None

        def terminate(self) -> None:
            events.append("terminate")

        def kill(self) -> None:
            events.append("kill")

        def wait(self, timeout: float | None = None) -> int:
            events.append("wait")
            return 0

    run.stop_session(Running(), "c")
    assert events == ["terminate", "wait"]


def test_a_session_that_writes_nothing_is_not_reported_as_done() -> None:
    """Two passes ended having written no answer at all and both printed
    "done", which reads as success until grading contradicts it an hour
    later."""
    assert run.session_verdict(0) == "PRODUCED NOTHING"
    assert run.session_verdict(17) == "INCOMPLETE"
    assert run.session_verdict(run.question_count()) == "done"


def test_question_count_comes_from_the_spec() -> None:
    """A hard-coded 30 beside a spec that defines the questions drifts."""
    text = (REPO_ROOT / "fixtures" / "questions.yaml").read_text("utf-8")
    assert run.question_count() == text.count("\n  - id:")


def test_the_prompt_rules_out_backgrounding_the_work() -> None:
    """One session parked an 8.4M-row join in a background task and ended its
    turn to wait for it; the container exited and killed the task. The prompt
    keeps the model in the foreground, in whatever words it uses to say so."""
    import specdoc

    task = specdoc.render(REPO_ROOT).lower()
    assert "foreground" in task
    assert "background" in task


def test_question_ids_are_the_question_count() -> None:
    """The resume prompt names ids; the progress line counts them. Both read
    the same spec, so they cannot disagree about what a full run is."""
    ids = run.question_ids()
    assert len(ids) == run.question_count()
    assert len(set(ids)) == len(ids)
    assert run.answer_name(ids[0]) == "q01", "SPEC.md asks for answers/q{id}.csv"


def test_missing_answers_names_what_the_session_still_owes(tmp_path: Path) -> None:
    """A resumed session is told which questions to do, not how many."""
    (tmp_path / "answers").mkdir()
    everything = [run.answer_name(qid) for qid in run.question_ids()]
    for name in everything[:3]:
        (tmp_path / "answers" / f"{name}.csv").write_text("x\n")

    missing = run.missing_answers(tmp_path)

    assert missing == everything[3:]
    assert run.missing_answers(tmp_path / "empty") == everything


def test_the_resume_prompt_says_the_background_work_is_gone(tmp_path: Path) -> None:
    """Handing the session back the original instruction invites the same bet
    a second time. The nudge says what was lost and what is outstanding."""
    prompt = run.resume_prompt(["q05", "q06"]).lower()

    assert "q05" in prompt and "q06" in prompt
    assert "background" in prompt
    assert "foreground" in prompt


def test_the_resume_prompt_stays_short_when_everything_is_missing() -> None:
    """A session that stops at question four owes 26 ids. Listing all of them
    buries the instruction they are attached to."""
    prompt = run.resume_prompt([run.answer_name(qid) for qid in run.question_ids()])

    assert prompt.count("q0") + prompt.count("q1") + prompt.count("q2") <= 12
    assert "more" in prompt


def test_a_resumed_attempt_asks_for_its_own_session(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Resuming into a fresh session would answer the remaining questions
    without the context that produced the first answers. The id is what makes
    it the same session, and it survives in the mounted home."""
    fake_credentials(tmp_path, monkeypatch)
    session_home = tmp_path / "home"
    session_home.mkdir()

    cmd = run.docker_command(
        tmp_path / "workspace", session_home, "m", "c1", "keep going", "sess-1234"
    )

    assert cmd[cmd.index("--resume") + 1] == "sess-1234"
    assert cmd[cmd.index("-p") + 1] == "keep going"
    assert cmd.index(run.IMAGE) < cmd.index("--resume")


def test_the_first_attempt_does_not_resume_anything(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake_credentials(tmp_path, monkeypatch)
    session_home = tmp_path / "home"
    session_home.mkdir()

    cmd = run.docker_command(tmp_path / "workspace", session_home, "m", "c1")

    assert "--resume" not in cmd
    assert cmd[cmd.index("-p") + 1] == run.INITIAL_PROMPT


def test_the_session_id_is_the_last_one_the_transcript_carries(tmp_path: Path) -> None:
    path = write_transcript(
        tmp_path,
        [
            {"type": "system", "session_id": "sess-1"},
            {"type": "assistant", "session_id": "sess-1"},
            {"type": "result", "session_id": "sess-1"},
        ],
    )

    assert run.session_id(path) == "sess-1"
    assert run.session_id(tmp_path / "absent.jsonl") is None


def fake_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A repo root a session can be assembled from, outside the real one."""
    root = tmp_path / "repo"
    shutil.copytree(REPO_ROOT / "fixtures", root / "fixtures")
    shutil.copy(REPO_ROOT / "SPEC.md", root / "SPEC.md")
    monkeypatch.setattr(run, "REPO_ROOT", root)
    monkeypatch.setattr(
        run,
        "runtime_snapshot",
        lambda image, adapter: {
            "image": image,
            "image_id": "sha256:test",
            "image_repo_digests": [],
            "cli": adapter.scaffold,
            "cli_version": adapter.expected_cli_version,
            "cli_version_output": adapter.expected_cli_version,
            "duckdb_version": run.agents.DUCKDB_VERSION,
            "duckdb_version_output": run.agents.DUCKDB_VERSION,
        },
    )
    return root


def test_codex_session_uses_the_common_result_layout_and_status(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = fake_repo(tmp_path, monkeypatch)
    credentials = tmp_path / "codex-auth.json"
    credentials.write_text('{"tokens":"decoy"}', encoding="utf-8")
    monkeypatch.setattr(run, "CODEX_CREDENTIALS", credentials)
    monkeypatch.setattr(run, "source_coop_sample", dict)
    monkeypatch.setattr(run, "harness_commit", lambda: "abc1234")
    monkeypatch.setattr(run, "stop_session", lambda proc, container: None)
    monkeypatch.setattr(run.time, "sleep", lambda seconds: None)

    class FakePopen:
        def __init__(
            self,
            cmd: list[str],
            stdout: IO[str],
            stderr: IO[str],
            text: bool,
            **kwargs: Any,
        ) -> None:
            mount = next(arg for arg in cmd if arg.endswith(":/workspace"))
            answers = Path(mount.split(":")[0]) / "answers"
            for question_id in run.question_ids():
                (answers / f"{run.answer_name(question_id)}.csv").write_text(
                    "value\n1\n", encoding="utf-8"
                )
            stdout.write('{"type":"thread.started","thread_id":"thread-1"}\n')
            stdout.write(
                '{"type":"turn.completed","usage":'
                '{"input_tokens":3,"output_tokens":1}}\n'
            )
            self.returncode = 0

        def poll(self) -> int | None:
            return 0

    monkeypatch.setattr(run.subprocess, "Popen", FakePopen)

    run.run_session("gpt-test", False, agent="codex", auth="login")

    result = next(root.glob("results/codex-gpt-test/*"))
    meta = json.loads((result / "meta.json").read_text(encoding="utf-8"))
    assert (result / "answers").is_dir()
    assert (result / "transcript.jsonl").is_file()
    assert (result / "stderr.log").is_file()
    assert meta["schema_version"] == 2
    assert meta["spec_contract_version"] == 2
    assert meta["agent"] == "codex"
    assert meta["status"] == "done"
    assert meta["imputed_cost_usd"] is None
    assert meta["agent_config"]["mode"] == "native"
    assert meta["runtime"]["image_id"] == "sha256:test"


def test_a_session_that_stops_short_is_resumed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A session ended its turn with four answers written and an 8.4M-row join
    backgrounded, and the harness scored that as the run. The same session is
    handed back its own id and told what is missing, and the second attempt
    finishes the set."""
    root = fake_repo(tmp_path, monkeypatch)
    fake_credentials(tmp_path, monkeypatch)
    monkeypatch.setattr(run, "source_coop_sample", dict)
    monkeypatch.setattr(run, "harness_commit", lambda: "abc1234")
    monkeypatch.setattr(run, "stop_session", lambda proc, container: None)
    monkeypatch.setattr(run.time, "sleep", lambda seconds: None)
    calls = []

    class FakePopen:
        """Writes the answers of an attempt, then exits like a real one."""

        def __init__(
            self,
            cmd: list[str],
            stdout: IO[str],
            stderr: IO[str],
            text: bool,
            **kwargs: Any,
        ) -> None:
            calls.append(cmd)
            mount = next(a for a in cmd if a.endswith(":/workspace"))
            answers = Path(mount.split(":")[0]) / "answers"
            names = [run.answer_name(q) for q in run.question_ids()]
            for name in names if len(calls) > 1 else names[:4]:
                (answers / f"{name}.csv").write_text("value\n1\n")
            stdout.write(
                json.dumps(
                    {
                        "type": "result",
                        "session_id": "sess-9",
                        "usage": {"input_tokens": 3, "output_tokens": 1},
                    }
                )
                + "\n"
            )
            self.returncode = 0

        def poll(self) -> int | None:
            return 0

    monkeypatch.setattr(run.subprocess, "Popen", FakePopen)

    run.run_session("haiku", dry_run=False)

    assert len(calls) == 2, "a session that stopped short was not resumed"
    assert "--resume" in calls[1]
    assert calls[1][calls[1].index("--resume") + 1] == "sess-9"

    meta = json.loads(next(root.glob("results/haiku/*/meta.json")).read_text())
    assert meta["attempts"] == 2
    assert meta["answers_written"] == run.question_count()
    assert meta["status"] == "done"
    assert meta["input_tokens"] == 6, "both attempts' tokens are the run's"


def test_a_complete_session_is_not_resumed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Resuming a session that finished would spend a second run's tokens to
    be told there is nothing to do."""
    fake_repo(tmp_path, monkeypatch)
    fake_credentials(tmp_path, monkeypatch)
    monkeypatch.setattr(run, "source_coop_sample", dict)
    monkeypatch.setattr(run, "harness_commit", lambda: "abc1234")
    monkeypatch.setattr(run, "stop_session", lambda proc, container: None)
    monkeypatch.setattr(run.time, "sleep", lambda seconds: None)
    calls = []

    class FakePopen:
        def __init__(
            self,
            cmd: list[str],
            stdout: IO[str],
            stderr: IO[str],
            text: bool,
            **kwargs: Any,
        ) -> None:
            calls.append(cmd)
            mount = next(a for a in cmd if a.endswith(":/workspace"))
            answers = Path(mount.split(":")[0]) / "answers"
            for qid in run.question_ids():
                path = answers / f"{run.answer_name(qid)}.csv"
                path.write_text("value\n1\n")
            stdout.write(json.dumps({"session_id": "s"}) + "\n")
            self.returncode = 0

        def poll(self) -> int | None:
            return 0

    monkeypatch.setattr(run.subprocess, "Popen", FakePopen)

    run.run_session("haiku", dry_run=False)

    assert len(calls) == 1


def test_resuming_is_bounded(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A session that stops for a reason other than waiting stops again. The
    attempts are capped so a wedged model cannot spend a run's budget twice
    over."""
    fake_repo(tmp_path, monkeypatch)
    fake_credentials(tmp_path, monkeypatch)
    monkeypatch.setattr(run, "source_coop_sample", dict)
    monkeypatch.setattr(run, "harness_commit", lambda: "abc1234")
    monkeypatch.setattr(run, "stop_session", lambda proc, container: None)
    monkeypatch.setattr(run.time, "sleep", lambda seconds: None)
    calls = []

    class FakePopen:
        def __init__(
            self,
            cmd: list[str],
            stdout: IO[str],
            stderr: IO[str],
            text: bool,
            **kwargs: Any,
        ) -> None:
            calls.append(cmd)
            stdout.write(json.dumps({"session_id": "s"}) + "\n")
            self.returncode = 0

        def poll(self) -> int | None:
            return 0

    monkeypatch.setattr(run.subprocess, "Popen", FakePopen)

    run.run_session("haiku", dry_run=False, max_attempts=2)

    assert len(calls) == 2
    assert run.MAX_ATTEMPTS > 1, "the default must allow at least one resume"


def test_usage_is_summed_across_attempts(tmp_path: Path) -> None:
    """Each invocation closes with its own cumulative `result` record. Reading
    only the last one bills a resumed run for its final attempt alone, and the
    imputed cost then understates what the run actually spent."""
    usage = {
        "input_tokens": 10,
        "output_tokens": 2,
        "cache_creation_input_tokens": 5,
        "cache_read_input_tokens": 7,
    }
    path = write_transcript(
        tmp_path,
        [
            {"type": "assistant"},
            {"type": "result", "usage": usage},
            {"type": "assistant"},
            {"type": "result", "usage": usage},
        ],
    )

    stats = run.parse_result_record(path)

    assert stats["turns"] == 2
    assert stats["input_tokens"] == 20
    assert stats["output_tokens"] == 4
    assert stats["cache_creation_tokens"] == 10
    assert stats["cache_read_tokens"] == 14


class SampleResponse:
    """A ranged read of the catalog, without the catalog."""

    def __init__(self, body: bytes, ray: str | None = None) -> None:
        self._body = body
        self.headers = {"cf-ray": ray} if ray else {}

    def read(self, size: int | None = None) -> bytes:
        return self._body

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> Literal[False]:
        return False


def test_the_route_sample_records_the_edge_that_served_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A run's wall clock is mostly the route. Without the colo beside it,
    a slow model and a slow route look the same afterwards."""
    fake_repo(tmp_path, monkeypatch)
    monkeypatch.setattr(
        run.urllib.request,
        "urlopen",
        lambda request, timeout=None: SampleResponse(b"x" * run.PROBE_BYTES, "abc-GRU"),
    )

    sample = run.source_coop_sample()

    assert sample["ok"] is True
    assert sample["bytes"] == run.PROBE_BYTES
    assert sample["colo"] == "GRU"


def test_a_short_route_sample_is_not_ok(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake_repo(tmp_path, monkeypatch)
    monkeypatch.setattr(
        run.urllib.request,
        "urlopen",
        lambda request, timeout=None: SampleResponse(b"x" * 128),
    )

    assert run.source_coop_sample()["ok"] is False


def test_an_unreachable_catalog_never_fails_the_run(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The sample is diagnostic. A run that dies because the probe failed
    would lose the session it was there to describe."""
    fake_repo(tmp_path, monkeypatch)

    def refuse(request: object, timeout: float | None = None) -> None:
        raise run.urllib.error.URLError("connection reset")

    monkeypatch.setattr(run.urllib.request, "urlopen", refuse)

    sample = run.source_coop_sample()

    assert sample["ok"] is False
    assert "URLError" in sample["error"]


def session_that(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    returncode: int = 0,
    session_id: str = "sess-9",
    answer_all: bool = True,
    stderr_text: str = "",
) -> tuple[Path, list[list[str]]]:
    """Wire a fake session with the exit code and transcript we want."""
    root = fake_repo(tmp_path, monkeypatch)
    fake_credentials(tmp_path, monkeypatch)
    monkeypatch.setattr(run, "source_coop_sample", dict)
    monkeypatch.setattr(run, "harness_commit", lambda: "abc1234")
    monkeypatch.setattr(run, "stop_session", lambda proc, container: None)
    monkeypatch.setattr(run.time, "sleep", lambda seconds: None)
    calls = []

    class FakePopen:
        def __init__(
            self,
            cmd: list[str],
            stdout: IO[str],
            stderr: IO[str],
            text: bool,
            **kwargs: Any,
        ) -> None:
            calls.append(cmd)
            mount = next(a for a in cmd if a.endswith(":/workspace"))
            answers = Path(mount.split(":")[0]) / "answers"
            if answer_all:
                for question in run.question_ids():
                    (answers / f"{run.answer_name(question)}.csv").write_text(
                        "value\n1\n"
                    )
            record = {
                "type": "result",
                "usage": {"input_tokens": 3, "output_tokens": 1},
            }
            if session_id:
                record["session_id"] = session_id
            stdout.write(json.dumps(record) + "\n")
            if stderr_text:
                stderr.write(stderr_text)
            self.returncode = returncode

        def poll(self) -> int | None:
            return returncode

    monkeypatch.setattr(run.subprocess, "Popen", FakePopen)
    return root, calls


def test_a_failed_session_reports_its_exit_code_and_stderr(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A container that died leaves its reason in stderr, and that is the
    only place it exists once the container is gone."""
    session_that(
        monkeypatch,
        tmp_path,
        returncode=1,
        stderr_text="docker: image not found\n",
    )

    run.run_session("haiku", dry_run=False)

    err = capsys.readouterr().err
    assert "session exited 1" in err
    assert "docker: image not found" in err


def test_a_transcript_without_a_session_id_stops_the_resume(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Resuming needs an id. Starting a fresh session instead would answer
    the rest without the context that produced the first answers."""
    session_that(monkeypatch, tmp_path, session_id="", answer_all=False)

    run.run_session("haiku", dry_run=False)

    assert "no session id in the transcript" in capsys.readouterr().out


def test_a_long_session_prints_a_heartbeat(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A session runs for tens of minutes. Silence and a hang look the
    same, so the run says where it has got to."""
    root = fake_repo(tmp_path, monkeypatch)
    fake_credentials(tmp_path, monkeypatch)
    monkeypatch.setattr(run, "source_coop_sample", dict)
    monkeypatch.setattr(run, "harness_commit", lambda: "abc1234")
    monkeypatch.setattr(run, "stop_session", lambda proc, container: None)
    monkeypatch.setattr(run.time, "sleep", lambda seconds: None)

    # Every reading is a minute later than the last, so the first poll is
    # already past the heartbeat deadline.
    ticks = itertools.count(0, run.HEARTBEAT_SECONDS + 1)
    monkeypatch.setattr(run.time, "monotonic", lambda: next(ticks))

    class SlowPopen:
        polls = 0

        def __init__(
            self,
            cmd: list[str],
            stdout: IO[str],
            stderr: IO[str],
            text: bool,
            **kwargs: Any,
        ) -> None:
            mount = next(a for a in cmd if a.endswith(":/workspace"))
            answers = Path(mount.split(":")[0]) / "answers"
            for question in run.question_ids():
                (answers / f"{run.answer_name(question)}.csv").write_text("value\n1\n")
            stdout.write(
                json.dumps(
                    {
                        "type": "result",
                        "session_id": "sess-9",
                        "usage": {"input_tokens": 3, "output_tokens": 1},
                    }
                )
                + "\n"
            )
            self.returncode = 0

        def poll(self) -> int | None:
            SlowPopen.polls += 1
            return None if SlowPopen.polls < 3 else 0

    monkeypatch.setattr(run.subprocess, "Popen", SlowPopen)

    run.run_session("haiku", dry_run=False)

    out = capsys.readouterr().out
    assert "turns ·" in out
    assert "answers ·" in out
    assert (root / "results" / "haiku").exists()


def test_a_rejected_credential_stops_the_resume_loop(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A session that answered nothing after a 401 is not a session that
    stopped early. The CLI already retried the credential ten times, and
    every further attempt fails the same way. One sweep spent all three
    attempts proving it and the arm came back n=1."""
    fake_repo(tmp_path, monkeypatch)
    fake_credentials(tmp_path, monkeypatch)
    monkeypatch.setattr(run, "source_coop_sample", dict)
    monkeypatch.setattr(run, "harness_commit", lambda: "abc1234")
    monkeypatch.setattr(run, "stop_session", lambda proc, container: None)
    monkeypatch.setattr(run.time, "sleep", lambda seconds: None)
    starts = []

    class RejectedPopen:
        def __init__(
            self,
            cmd: list[str],
            stdout: IO[str],
            stderr: IO[str],
            text: bool,
            **kwargs: Any,
        ) -> None:
            starts.append(cmd)
            stdout.write(
                json.dumps({"subtype": "api_retry", "error_status": 401}) + "\n"
            )
            stdout.write(json.dumps({"type": "result", "session_id": "sess-9"}) + "\n")
            self.returncode = 1

        def poll(self) -> int | None:
            return 1

    monkeypatch.setattr(run.subprocess, "Popen", RejectedPopen)

    run.run_session("haiku", dry_run=False)

    assert len(starts) == 1, "a rejected credential must not be retried"
    assert "the credential was rejected" in capsys.readouterr().err


def test_execution_status_separates_what_the_harness_saw() -> None:
    """Four outcomes the harness can judge without the grader. `done` and
    `incomplete` are execution outcomes, not verdicts: whether either becomes
    a passed trial is grading's call."""
    total = run.question_count()
    assert run.execution_status(total) == "done"
    assert run.execution_status(total - 1) == "incomplete"
    assert run.execution_status(0) == "agent_produced_nothing"
    assert run.execution_status(5, timed_out=True) == "agent_timeout"


def test_a_dead_credential_only_invalidates_a_run_that_answered_nothing() -> None:
    """A session that recovered mid-run and wrote answers was measured,
    whatever happened to its first token. One that never got past the 401
    never reached the task at all."""
    assert run.execution_status(0, credential_dead=True) == "authentication_invalid"
    assert run.execution_status(5, credential_dead=True) == "incomplete"


def test_a_spent_budget_outranks_an_incomplete_answer_set() -> None:
    """A run killed at its budget stopped early because it ran out of time,
    which is a different failure from one that stopped on its own."""
    assert run.execution_status(1, timed_out=True) == "agent_timeout"
    assert run.execution_status(0, timed_out=True) == "agent_timeout"


def test_the_pins_digest_changes_with_the_pinned_data(tmp_path: Path) -> None:
    """Two runs either side of a repin measured different inputs. The golden
    digest alone cannot tell them apart, because a repin that leaves every
    answer unchanged still changes what the session had to work from."""
    original = run.pins_fingerprint()
    assert original is not None and len(original) == 12
