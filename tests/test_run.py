"""Session assembly: the workspace gets the policies and the input list, and
never the golden fixtures. Does not require Docker."""

import json
import os
import re
import shutil
import sys
from datetime import datetime, timezone
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
    run.run_session("haiku", dry_run=True, input_mode="csv")
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


def test_a_run_is_named_for_when_it_ran_and_what_it_ran():
    """The name used to be a position in a sequence, so two runs could want
    it and the second destroyed the first. Every guard against that -- the
    scan for a free number, --start-pass, --force -- managed a collision a
    timestamped name cannot have."""
    started = datetime(2026, 7, 26, 11, 46, 46, tzinfo=timezone.utc)
    name = run.run_id(started, "7f8fb7a3aa1f224ee05e7dd14f13a782b0a6e3ca")
    assert name == "20260726T114646Z-7f8fb7a"


def test_run_names_sort_chronologically():
    """Run ids are read and globbed as strings, so ordering has to fall out
    of the name rather than out of a stat call."""
    earlier = run.run_id(datetime(2026, 7, 26, 9, 0, tzinfo=timezone.utc), "a" * 40)
    later = run.run_id(datetime(2026, 7, 26, 17, 0, tzinfo=timezone.utc), "b" * 40)
    assert sorted([later, earlier]) == [earlier, later]


def test_two_runs_a_second_apart_do_not_collide():
    a = run.run_id(datetime(2026, 7, 26, 9, 0, 0, tzinfo=timezone.utc), "abc1234")
    b = run.run_id(datetime(2026, 7, 26, 9, 0, 1, tzinfo=timezone.utc), "abc1234")
    assert a != b


def test_the_collision_guards_are_gone(monkeypatch, tmp_path):
    """--force and --start-pass existed only to arbitrate a name clash."""
    monkeypatch.setattr(run, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(sys, "argv",
                        ["run.py", "--model", "opus", "--passes", "2"])
    calls = []
    monkeypatch.setattr(run, "run_session",
                        lambda *a, **k: calls.append(a))

    assert run.main() == 0
    assert len(calls) == 2
    assert not hasattr(run, "next_free_pass")


def test_a_label_groups_runs_without_moving_them(monkeypatch, tmp_path):
    """More than ten runs of one configuration needs a way to say which runs
    belong together, and a directory convention is the wrong place for it."""
    monkeypatch.setattr(run, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(sys, "argv",
                        ["run.py", "--model", "opus", "--passes", "1",
                         "--label", "experiment-1"])
    seen = []
    monkeypatch.setattr(run, "run_session",
                        lambda *a, **k: seen.append(a[-1]))

    assert run.main() == 0
    assert seen == ["experiment-1"]


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


def test_the_prompt_rules_out_backgrounding_the_work():
    """One session parked an 8.4M-row join in a background task and ended its
    turn to wait for it; the container exited and killed the task. "Nothing
    wakes it up later" did not survive contact with a tool that offers to do
    exactly that, so the prompt names the move rather than the consequence."""
    task = (REPO_ROOT / "prompts" / "task.md").read_text("utf-8").lower()
    assert "foreground" in task
    assert "background" in task
    assert "killed when the turn ends" in task


def test_the_prompt_warns_about_the_duckdb_writer_lock():
    """A second duckdb process on the same database file fails with
    "Conflicting lock is held". One session spent ten turns polling /proc for
    the pid holding it, in a container with no ps."""
    task = (REPO_ROOT / "prompts" / "task.md").read_text("utf-8").lower()
    assert "conflicting lock is held" in task
    assert "one duckdb process at a time" in task


def test_the_prompt_caps_concurrency_on_remote_scans():
    """data.source.coop fronts S3 with a proxy that resets connections under a
    burst of range requests, and DuckDB opens one per row group per thread. A
    run whose big scans die on "Failure when receiving data from the peer" has
    measured the proxy, not the model."""
    task = (REPO_ROOT / "prompts" / "task.md").read_text("utf-8").lower()
    assert "threads" in task
    assert "failure when receiving data from the peer" in task


def test_question_ids_are_the_question_count():
    """The resume prompt names ids; the progress line counts them. Both read
    the same spec, so they cannot disagree about what a full run is."""
    ids = run.question_ids()
    assert len(ids) == run.question_count()
    assert len(set(ids)) == len(ids)
    assert run.answer_name(ids[0]) == "q01", "task.md asks for answers/q{id}.csv"


def test_missing_answers_names_what_the_session_still_owes(tmp_path):
    """A resumed session is told which questions to do, not how many."""
    (tmp_path / "answers").mkdir()
    everything = [run.answer_name(qid) for qid in run.question_ids()]
    for name in everything[:3]:
        (tmp_path / "answers" / f"{name}.csv").write_text("x\n")

    missing = run.missing_answers(tmp_path)

    assert missing == everything[3:]
    assert run.missing_answers(tmp_path / "empty") == everything


def test_the_resume_prompt_says_the_background_work_is_gone(tmp_path):
    """Handing the session back the original instruction invites the same bet
    a second time. The nudge says what was lost and what is outstanding."""
    prompt = run.resume_prompt(["q05", "q06"]).lower()

    assert "q05" in prompt and "q06" in prompt
    assert "background" in prompt
    assert "foreground" in prompt


def test_the_resume_prompt_stays_short_when_everything_is_missing():
    """A session that stops at question four owes 26 ids. Listing all of them
    buries the instruction they are attached to."""
    prompt = run.resume_prompt(
        [run.answer_name(qid) for qid in run.question_ids()])

    assert prompt.count("q0") + prompt.count("q1") + prompt.count("q2") <= 12
    assert "more" in prompt


def test_a_resumed_attempt_asks_for_its_own_session(monkeypatch, tmp_path):
    """Resuming into a fresh session would answer the remaining questions
    without the context that produced the first answers. The id is what makes
    it the same session, and it survives in the mounted home."""
    fake_credentials(tmp_path, monkeypatch)
    session_home = tmp_path / "home"
    session_home.mkdir()

    cmd = run.docker_command(tmp_path / "workspace", session_home, "m", "c1",
                             "keep going", "sess-1234")

    assert cmd[cmd.index("--resume") + 1] == "sess-1234"
    assert cmd[cmd.index("-p") + 1] == "keep going"
    assert cmd.index(run.IMAGE) < cmd.index("--resume")


def test_the_first_attempt_does_not_resume_anything(monkeypatch, tmp_path):
    fake_credentials(tmp_path, monkeypatch)
    session_home = tmp_path / "home"
    session_home.mkdir()

    cmd = run.docker_command(tmp_path / "workspace", session_home, "m", "c1")

    assert "--resume" not in cmd
    assert cmd[cmd.index("-p") + 1] == run.INITIAL_PROMPT


def test_the_session_id_is_the_last_one_the_transcript_carries(tmp_path):
    path = write_transcript(tmp_path, [
        {"type": "system", "session_id": "sess-1"},
        {"type": "assistant", "session_id": "sess-1"},
        {"type": "result", "session_id": "sess-1"},
    ])

    assert run.session_id(path) == "sess-1"
    assert run.session_id(tmp_path / "absent.jsonl") is None


def fake_repo(tmp_path, monkeypatch):
    """A repo root a session can be assembled from, outside the real one."""
    root = tmp_path / "repo"
    for name in ("prompts", "policies", "fixtures"):
        shutil.copytree(REPO_ROOT / name, root / name)
    monkeypatch.setattr(run, "REPO_ROOT", root)
    return root


def test_a_session_that_stops_short_is_resumed(monkeypatch, tmp_path):
    """A session ended its turn with four answers written and an 8.4M-row join
    backgrounded, and the harness scored that as the run. The same session is
    handed back its own id and told what is missing, and the second attempt
    finishes the set."""
    root = fake_repo(tmp_path, monkeypatch)
    fake_credentials(tmp_path, monkeypatch)
    monkeypatch.setattr(run, "source_coop_sample", lambda: {})
    monkeypatch.setattr(run, "harness_commit", lambda: "abc1234")
    monkeypatch.setattr(run, "stop_session", lambda proc, container: None)
    monkeypatch.setattr(run.time, "sleep", lambda seconds: None)
    calls = []

    class FakePopen:
        """Writes the answers of an attempt, then exits like a real one."""

        def __init__(self, cmd, stdout, stderr, text, **kwargs):
            calls.append(cmd)
            mount = next(a for a in cmd if a.endswith(":/workspace"))
            answers = Path(mount.split(":")[0]) / "answers"
            names = [run.answer_name(q) for q in run.question_ids()]
            for name in names if len(calls) > 1 else names[:4]:
                (answers / f"{name}.csv").write_text("value\n1\n")
            stdout.write(json.dumps({
                "type": "result", "session_id": "sess-9",
                "usage": {"input_tokens": 3, "output_tokens": 1},
            }) + "\n")
            self.returncode = 0

        def poll(self):
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


def test_a_complete_session_is_not_resumed(monkeypatch, tmp_path):
    """Resuming a session that finished would spend a second run's tokens to
    be told there is nothing to do."""
    fake_repo(tmp_path, monkeypatch)
    fake_credentials(tmp_path, monkeypatch)
    monkeypatch.setattr(run, "source_coop_sample", lambda: {})
    monkeypatch.setattr(run, "harness_commit", lambda: "abc1234")
    monkeypatch.setattr(run, "stop_session", lambda proc, container: None)
    monkeypatch.setattr(run.time, "sleep", lambda seconds: None)
    calls = []

    class FakePopen:
        def __init__(self, cmd, stdout, stderr, text, **kwargs):
            calls.append(cmd)
            mount = next(a for a in cmd if a.endswith(":/workspace"))
            answers = Path(mount.split(":")[0]) / "answers"
            for qid in run.question_ids():
                path = answers / f"{run.answer_name(qid)}.csv"
                path.write_text("value\n1\n")
            stdout.write(json.dumps({"session_id": "s"}) + "\n")
            self.returncode = 0

        def poll(self):
            return 0

    monkeypatch.setattr(run.subprocess, "Popen", FakePopen)

    run.run_session("haiku", dry_run=False)

    assert len(calls) == 1


def test_resuming_is_bounded(monkeypatch, tmp_path):
    """A session that stops for a reason other than waiting stops again. The
    attempts are capped so a wedged model cannot spend a run's budget twice
    over."""
    fake_repo(tmp_path, monkeypatch)
    fake_credentials(tmp_path, monkeypatch)
    monkeypatch.setattr(run, "source_coop_sample", lambda: {})
    monkeypatch.setattr(run, "harness_commit", lambda: "abc1234")
    monkeypatch.setattr(run, "stop_session", lambda proc, container: None)
    monkeypatch.setattr(run.time, "sleep", lambda seconds: None)
    calls = []

    class FakePopen:
        def __init__(self, cmd, stdout, stderr, text, **kwargs):
            calls.append(cmd)
            stdout.write(json.dumps({"session_id": "s"}) + "\n")
            self.returncode = 0

        def poll(self):
            return 0

    monkeypatch.setattr(run.subprocess, "Popen", FakePopen)

    run.run_session("haiku", dry_run=False, max_attempts=2)

    assert len(calls) == 2
    assert run.MAX_ATTEMPTS > 1, "the default must allow at least one resume"


def test_usage_is_summed_across_attempts(tmp_path):
    """Each invocation closes with its own cumulative `result` record. Reading
    only the last one bills a resumed run for its final attempt alone, and the
    imputed cost then understates what the run actually spent."""
    usage = {"input_tokens": 10, "output_tokens": 2,
             "cache_creation_input_tokens": 5, "cache_read_input_tokens": 7}
    path = write_transcript(tmp_path, [
        {"type": "assistant"},
        {"type": "result", "usage": usage},
        {"type": "assistant"},
        {"type": "result", "usage": usage},
    ])

    stats = run.parse_result_record(path)

    assert stats["turns"] == 2
    assert stats["input_tokens"] == 20
    assert stats["output_tokens"] == 4
    assert stats["cache_creation_tokens"] == 10
    assert stats["cache_read_tokens"] == 14
