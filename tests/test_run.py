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

    cmd = run.docker_command(tmp_path / "workspace", session_home, "m", "c1")

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


def write_transcript(tmp_path, records):
    path = tmp_path / "transcript.jsonl"
    path.write_text(
        "".join(json.dumps(r) + "\n" for r in records), encoding="utf-8",
    )
    return path


def heartbeat(call_id, tool, seconds):
    return {"type": "tool_heartbeat", "tool_use_id": call_id,
            "tool_name": tool, "elapsed_time_seconds": seconds}


def test_tool_timings_take_the_last_heartbeat_per_call(tmp_path):
    """Heartbeats report a running elapsed for one call, so summing them all
    would count a single slow query several times over."""
    path = write_transcript(tmp_path, [
        heartbeat("a", "Bash", 30),
        heartbeat("a", "Bash", 60),
        heartbeat("a", "Bash", 90),
    ])

    timings = run.tool_timings(path)

    assert timings["slow_tool_calls"] == 1
    assert timings["slow_tool_seconds"] == 90
    assert timings["slow_tool_seconds_by_tool"] == {"Bash": 90.0}


def test_tool_timings_count_calls_that_hit_the_cap(tmp_path):
    """A call at the cap was killed, not answered. That is the difference
    between a slow query and one that never returned, and a run whose wall
    clock is mostly killed queries has measured nothing."""
    path = write_transcript(tmp_path, [
        heartbeat("a", "Bash", run.TOOL_TIMEOUT_SECONDS),
        heartbeat("b", "Bash", run.TOOL_TIMEOUT_SECONDS - 1),
    ])

    assert run.tool_timings(path)["timed_out_tool_calls"] == 1


def test_tool_timings_survive_a_transcript_still_being_written(tmp_path):
    """The heartbeat reads a file the container has open, so the last line is
    routinely half-written."""
    path = write_transcript(tmp_path, [heartbeat("a", "Bash", 30)])
    with open(path, "a", encoding="utf-8") as f:
        f.write('{"type": "tool_hea')

    assert run.tool_timings(path)["slow_tool_calls"] == 1


def test_progress_snapshot_reports_turns_and_the_last_tool(tmp_path):
    path = write_transcript(tmp_path, [
        {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "name": "Read"}]}},
        {"type": "assistant", "message": {"content": [
            {"type": "text", "text": "thinking"},
            {"type": "tool_use", "name": "Bash"}]}},
        {"type": "user", "message": {"content": []}},
    ])

    assert run.progress_snapshot(path) == (2, "Bash")


def test_progress_snapshot_handles_an_absent_transcript(tmp_path):
    """The first heartbeat can land before the container writes a line."""
    assert run.progress_snapshot(tmp_path / "nope.jsonl") == (0, "-")


def results_dir(monkeypatch, tmp_path, model, passes):
    monkeypatch.setattr(run, "REPO_ROOT", tmp_path)
    for n in passes:
        (tmp_path / "results" / model / f"pass-{n}").mkdir(parents=True)
    return tmp_path


def test_next_free_pass_skips_what_exists(monkeypatch, tmp_path):
    """Re-running one pass at a time used to overwrite the pass before it,
    destroying transcripts that had already been graded."""
    results_dir(monkeypatch, tmp_path, "opus", [1, 2])

    assert run.next_free_pass("opus") == 3


def test_next_free_pass_starts_at_one_when_empty(monkeypatch, tmp_path):
    monkeypatch.setattr(run, "REPO_ROOT", tmp_path)
    assert run.next_free_pass("opus") == 1


def test_explicit_start_pass_refuses_to_clobber(monkeypatch, tmp_path, capsys):
    results_dir(monkeypatch, tmp_path, "opus", [1])
    monkeypatch.setattr(sys, "argv",
                        ["run.py", "--model", "opus", "--passes", "1",
                         "--start-pass", "1"])

    with pytest.raises(SystemExit) as exit_info:
        run.main()

    assert "already exist" in str(exit_info.value)


def test_force_allows_overwriting_a_pass(monkeypatch, tmp_path):
    """Deliberate replacement stays possible, it just has to be asked for."""
    results_dir(monkeypatch, tmp_path, "opus", [1])
    monkeypatch.setattr(sys, "argv",
                        ["run.py", "--model", "opus", "--passes", "1",
                         "--start-pass", "1", "--force", "--dry-run"])
    monkeypatch.setattr(run, "run_session", lambda *a, **k: None)

    assert run.main() == 0


# --- observability and cleanup ----------------------------------------------

def transcript_line(name, payload):
    return json.dumps({
        "type": "assistant",
        "message": {"content": [
            {"type": "tool_use", "name": name, "input": payload}]},
    }) + "\n"


def result_line(call_id, text, is_error=False):
    return json.dumps({
        "type": "user",
        "message": {"content": [{
            "type": "tool_result", "tool_use_id": call_id,
            "is_error": is_error,
            "content": [{"type": "text", "text": text}]}]},
    }) + "\n"


def call_line(call_id, name, payload):
    return json.dumps({
        "type": "assistant",
        "message": {"content": [{
            "type": "tool_use", "id": call_id, "name": name,
            "input": payload}]},
    }) + "\n"


def heartbeat_line(call_id, seconds):
    return json.dumps({
        "type": "tool_progress", "tool_use_id": f"{call_id}-hb",
        "parent_tool_use_id": call_id, "tool_name": "Bash",
        "elapsed_time_seconds": seconds, "heartbeat": True,
    }) + "\n"


def test_follow_prints_each_tool_call_as_it_lands(tmp_path, capsys):
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


def test_follow_times_each_call_and_names_the_failures(tmp_path, capsys):
    """A call and its result are two records. Printing only the call made a
    failed query look exactly like a successful one."""
    path = tmp_path / "transcript.jsonl"
    ticks = iter([100.0, 112.5, 112.5, 118.0])
    follower = run.Follower(clock=lambda: next(ticks))

    path.write_text(
        call_line("t1", "Bash", {"command": "duckdb < build.sql"})
        + result_line("t1", "Invalid Error: Failure receiving data from peer",
                      is_error=True)
        + call_line("t2", "Bash", {"command": "echo ok"})
        + result_line("t2", "ok")
    )
    follower.consume(path)
    out = capsys.readouterr().out

    assert "FAILED after 12.5s: Invalid Error: Failure receiving data" in out
    assert "↳ 5.5s" in out


def test_follow_shows_a_pulse_while_a_call_blocks(tmp_path, capsys):
    """A four-minute query printed nothing at all, which is why a working
    session and a dead one looked the same."""
    path = tmp_path / "transcript.jsonl"
    follower = run.Follower()
    path.write_text(
        call_line("t1", "Bash", {"command": "duckdb < slow.sql"})
        + heartbeat_line("t1", 30)      # under the interval, stays quiet
        + heartbeat_line("t1", 60)
        + heartbeat_line("t1", 90)      # under the interval again
        + heartbeat_line("t1", 120)
    )
    follower.consume(path)
    beats = [ln for ln in capsys.readouterr().out.splitlines()
             if "still running" in ln]
    assert [b.split(", ")[-1] for b in beats] == ["60s", "120s"]


def test_follow_reports_how_long_the_current_call_has_blocked(tmp_path):
    """The periodic summary said "last: Bash" whether that call started two
    seconds or four minutes ago."""
    path = tmp_path / "transcript.jsonl"
    ticks = iter([1000.0, 1240.0])
    follower = run.Follower(clock=lambda: next(ticks))
    path.write_text(call_line("t1", "Bash", {"command": "duckdb < slow.sql"}))

    follower.consume(path)
    assert follower.running_for() == 240.0


def test_follow_forgets_a_call_once_it_finishes(tmp_path):
    path = tmp_path / "transcript.jsonl"
    follower = run.Follower()
    path.write_text(call_line("t1", "Bash", {"command": "echo hi"})
                    + result_line("t1", "hi"))
    follower.consume(path)
    assert follower.running_for() == 0.0


def test_follow_waits_for_a_half_written_record(tmp_path, capsys):
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


def test_follow_collapses_a_multiline_command_to_one_line(tmp_path, capsys):
    """Sessions write heredocs of SQL. The transcript keeps the full text; the
    terminal gets one line per call."""
    path = tmp_path / "transcript.jsonl"
    path.write_text(call_line(
        "t1", "Bash", {"command": "cat > q.sql <<'EOF'\nSELECT 1;\nEOF"}))

    run.Follower().consume(path)
    printed = capsys.readouterr().out.strip().splitlines()
    assert len(printed) == 1
    assert len(printed[0]) <= run.FOLLOW_WIDTH


def test_a_rerun_leaves_nothing_of_the_run_before_it(tmp_path):
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
        monkeypatch, tmp_path):
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

    cmd = run.docker_command(tmp_path / "workspace", session_home, "m",
                             "geodata-eval-opus-pass3-42")

    assert cmd[:5] == ["docker", "run", "--rm", "--name",
                       "geodata-eval-opus-pass3-42"]
    # Every -v carries its mount spec in the same token, so none can swallow
    # the argument that follows it.
    assert all(arg != "-v" or ":" in cmd[i + 1]
               for i, arg in enumerate(cmd[:-1]))


def test_stop_session_removes_a_container_the_client_left_running(monkeypatch):
    """Ctrl-C kills the docker client, not the container it started."""
    calls = []
    monkeypatch.setattr(run.subprocess, "run",
                        lambda cmd, **kw: calls.append(cmd))

    class Finished:
        returncode = 0

        def poll(self):
            return 0

    run.stop_session(Finished(), "geodata-eval-opus-pass3-42")
    assert calls == [["docker", "rm", "-f", "-v",
                      "geodata-eval-opus-pass3-42"]]


def test_stop_session_terminates_a_client_still_running(monkeypatch):
    monkeypatch.setattr(run.subprocess, "run", lambda cmd, **kw: None)
    events = []

    class Running:
        def poll(self):
            return None

        def terminate(self):
            events.append("terminate")

        def wait(self, timeout=None):
            events.append("wait")

    run.stop_session(Running(), "c")
    assert events == ["terminate", "wait"]


def test_a_session_that_writes_nothing_is_not_reported_as_done():
    """Two passes ended having written no answer at all and both printed
    "done", which reads as success until grading contradicts it an hour
    later."""
    assert run.session_verdict(0) == "PRODUCED NOTHING"
    assert run.session_verdict(17) == "INCOMPLETE"
    assert run.session_verdict(run.question_count()) == "done"


def test_question_count_comes_from_the_spec():
    """A hard-coded 30 beside a spec that defines the questions drifts."""
    text = (REPO_ROOT / "fixtures" / "questions.yaml").read_text("utf-8")
    assert run.question_count() == text.count("\n  - id:")


def test_the_prompt_tells_the_session_it_will_not_be_resumed():
    """A session deferred its remaining work with ScheduleWakeup and ended its
    turn. Nothing resumes a headless container, so the run cost $2 and wrote
    no answers. The environment fact belongs in the prompt; the method for
    finishing on time does not."""
    task = (REPO_ROOT / "prompts" / "task.md").read_text("utf-8").lower()
    assert "runs once and is not resumed" in task
    assert "deferred" in task or "defer" in task
