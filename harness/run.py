"""Spawn independent benchmark sessions in Docker and collect transcripts.

Each session is one `docker run` of the pinned image: a fresh container,
a fresh workspace, no state shared with any other session or with the
host. The model works through the full question set once per session.

The container workspace receives only prompts/task.md and
fixtures/questions.yaml — never the golden answers.

Per session, this writes results/{model}/pass-{n}/:
    transcript.jsonl   raw stream-json output from the session
    answers/           the CSVs the agent wrote
    meta.json          model, tokens, turns, imputed cost, harness commit

Usage:
    python harness/run.py --model sonnet --passes 10
    python harness/run.py --model haiku --passes 1 --dry-run
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

from pricing import PRICES, imputed_cost_usd

IMAGE = "geodata-llm-eval"
REPO_ROOT = Path(__file__).resolve().parent.parent

# How often the run prints where a session has got to, and how often it
# checks whether the container has exited. A session runs for tens of
# minutes, so a minute between lines is frequent enough to show progress
# without burying the two lines that matter.
HEARTBEAT_SECONDS = 60
POLL_SECONDS = 2

# --follow truncates each tool call to one line of this width. A session
# writes multi-line heredocs of SQL; the point of following is to see what it
# is doing now, and the transcript keeps the full text either way.
FOLLOW_WIDTH = 150

# Where a tool call's subject lives, by tool. Falls back to the whole input.
TOOL_SUBJECT_KEYS = ("command", "file_path", "pattern", "path", "prompt")

# The input list, by encoding (see policies/INPUTS.md). experiment 1 (Goiás)
# ships csv; the geometry/split encodings drive the adversarial follow-up.
INPUT_FILES = {
    "csv": ["goias-sample.csv"],
    "geometry": ["goias-sample.parquet"],
    "split": ["goias-sample.csv", "goias-sample-geom.parquet"],
}


def list_files(input_mode: str) -> list[Path]:
    """The input-list file(s) to mount for an encoding mode."""
    base = REPO_ROOT / "fixtures" / "lists"
    return [base / name for name in INPUT_FILES[input_mode]]


def harness_commit() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=REPO_ROOT, capture_output=True, text=True, check=True,
        ).stdout.strip()
    except subprocess.CalledProcessError:
        return "unknown"


CREDENTIALS = Path.home() / ".claude" / ".credentials.json"

# The container HOME is a clean /home/runner (see Dockerfile): no CLAUDE.md,
# no hooks, no MCP config, no memory. A login token is the single host
# artifact that crosses the boundary, and it crosses as a per-session copy
# holding nothing else from ~/.claude.
CONTAINER_CLAUDE_DIR = "/home/runner/.claude"


def auth_args(session_home: Path) -> list[str]:
    """Docker args carrying credentials into the session.

    Prefers a subscription login and falls back to ANTHROPIC_API_KEY, so
    sessions bill against the plan rather than the API. Raises if neither
    is available, rather than launching a session that will fail on its
    first model call.

    The host credentials file is copied into a throwaway per-session home
    and that copy is mounted read-write: the CLI refreshes an expiring
    OAuth token by writing the file back, which a read-only mount of the
    real one would break. The copy dies with the session's temp dir, so a
    session can neither corrupt nor outlive the host login.
    """
    if CREDENTIALS.exists():
        claude_dir = session_home / ".claude"
        claude_dir.mkdir(parents=True, exist_ok=True)
        claude_dir.chmod(0o700)
        dest = claude_dir / ".credentials.json"
        shutil.copy(CREDENTIALS, dest)
        dest.chmod(0o600)
        return ["-v", f"{claude_dir}:{CONTAINER_CLAUDE_DIR}"]
    if os.environ.get("ANTHROPIC_API_KEY"):
        return ["-e", "ANTHROPIC_API_KEY"]
    raise SystemExit(
        f"No credentials found. Run `claude login` on the host (writes "
        f"{CREDENTIALS}) or set ANTHROPIC_API_KEY."
    )


def docker_command(workspace: Path, session_home: Path,
                   model_id: str, container: str) -> list[str]:
    return [
        # `--rm -v` also drops the anonymous volumes a container accumulates,
        # which survive `--rm` on its own. `--name` is what lets an
        # interrupted run find and remove its own container afterwards.
        "docker", "run", "--rm", "-v", "--name", container,
        # Run as the invoking user. The mounted credential copy is 0600 and
        # host-owned, and the CLI has to both read it and write a refreshed
        # token back; a container uid that isn't the file's owner cannot do
        # either. The image's own `runner` account can't be relied on for
        # this, since its uid is fixed at build time and the host's is not.
        "--user", f"{os.getuid()}:{os.getgid()}",
        "-v", f"{workspace}:/workspace",
        *auth_args(session_home),
        IMAGE,
        "-p", "Read task.md and complete it.",
        "--model", model_id,
        "--output-format", "stream-json",
        "--verbose",
        "--dangerously-skip-permissions",
    ]


def read_records(transcript_path: Path):
    """Yield the parseable records of a transcript.

    Tolerates a truncated final line, so this works on a transcript the
    session is still writing.
    """
    try:
        handle = open(transcript_path, encoding="utf-8")
    except FileNotFoundError:
        return
    with handle as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def parse_result_record(transcript_path: Path) -> dict:
    """Pull token counts and turn count from the stream-json transcript.

    The final `result` record carries cumulative usage; turn count is the
    number of assistant records.
    """
    usage: dict = {}
    turns = 0
    for record in read_records(transcript_path):
        if record.get("type") == "assistant":
            turns += 1
        if record.get("type") == "result":
            usage = record.get("usage") or {}
    return {
        "turns": turns,
        "input_tokens": usage.get("input_tokens", 0),
        "output_tokens": usage.get("output_tokens", 0),
        "cache_creation_tokens": usage.get("cache_creation_input_tokens", 0),
        "cache_read_tokens": usage.get("cache_read_input_tokens", 0),
    }


# The CLI caps a Bash call here. A call reported at or above the cap was
# killed rather than answered, which is the difference between a query that
# was slow and one that never returned.
TOOL_TIMEOUT_SECONDS = 120


def tool_timings(transcript_path: Path) -> dict:
    """Where a session spent its wall clock.

    The CLI emits heartbeat records carrying a running `elapsed_time_seconds`
    for calls slow enough to need one, so the last heartbeat per tool_use_id
    is a lower bound on that call's duration. Short calls emit none and are
    invisible here, which is the point: this measures the tail.

    Remote reads dominate a run of this benchmark, so a total duration on its
    own cannot distinguish a session that thought for half an hour from one
    that waited on source.coop for half an hour.
    """
    longest: dict[str, tuple[str, float]] = {}
    for record in read_records(transcript_path):
        seconds = record.get("elapsed_time_seconds")
        call_id = record.get("tool_use_id")
        if seconds is None or call_id is None:
            continue
        name = record.get("tool_name") or "unknown"
        if call_id not in longest or seconds > longest[call_id][1]:
            longest[call_id] = (name, float(seconds))

    per_tool: dict[str, float] = {}
    for name, seconds in longest.values():
        per_tool[name] = round(per_tool.get(name, 0.0) + seconds, 1)
    timed_out = sum(
        1 for _, s in longest.values() if s >= TOOL_TIMEOUT_SECONDS
    )
    return {
        "slow_tool_calls": len(longest),
        "slow_tool_seconds": round(sum(s for _, s in longest.values()), 1),
        "slow_tool_seconds_by_tool": per_tool,
        "timed_out_tool_calls": timed_out,
    }


def tool_subject(payload: dict) -> str:
    """The interesting part of a tool call's input, as one line."""
    for key in TOOL_SUBJECT_KEYS:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return " ".join(value.split())
    return json.dumps(payload, sort_keys=True)


def follow_transcript(transcript_path: Path, offset: int) -> int:
    """Print tool calls written since `offset`; return the new offset.

    Reads bytes rather than lines so a partially-written final record is left
    for the next poll instead of being parsed as truncated JSON.
    """
    try:
        with open(transcript_path, "rb") as handle:
            handle.seek(offset)
            data = handle.read()
    except FileNotFoundError:
        return offset

    complete = data.rfind(b"\n") + 1
    for raw in data[:complete].splitlines():
        try:
            record = json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue
        if record.get("type") != "assistant":
            continue
        for block in record.get("message", {}).get("content", []) or []:
            if block.get("type") != "tool_use":
                continue
            line = f"    → {block.get('name')}: {tool_subject(block.get('input', {}))}"
            print(line[:FOLLOW_WIDTH], flush=True)
    return offset + complete


def progress_snapshot(transcript_path: Path) -> tuple[int, str]:
    """Turns so far and the most recent tool call, from a partial transcript."""
    turns = 0
    last_tool = "-"
    for record in read_records(transcript_path):
        if record.get("type") != "assistant":
            continue
        turns += 1
        for block in (record.get("message") or {}).get("content") or []:
            if isinstance(block, dict) and block.get("type") == "tool_use":
                last_tool = block.get("name", "-")
    return turns, last_tool


def stop_session(proc: subprocess.Popen, container: str) -> None:
    """Make sure neither the client process nor the container outlives us."""
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
    # `docker run --rm` deletes the container on a normal exit, so this is a
    # no-op for a session that finished. It is the interrupted case that
    # leaves one behind.
    subprocess.run(["docker", "rm", "-f", "-v", container],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                   check=False)


def clear_pass_dir(out_dir: Path) -> None:
    """Empty a pass directory so a re-run leaves nothing of the run before it.

    A pass is one session's record. Re-running into a directory that still
    holds the previous attempt's grades, diffs, or answers produces a mixture
    that reads as a single coherent run and is not one.
    """
    if not out_dir.exists():
        return
    for path in sorted(out_dir.iterdir()):
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()


def run_session(model: str, pass_n: int, dry_run: bool,
                input_mode: str = "csv", follow: bool = False) -> None:
    model_id = PRICES[model].model_id
    out_dir = REPO_ROOT / "results" / model / f"pass-{pass_n}"
    container = f"geodata-eval-{model}-pass{pass_n}-{os.getpid()}"

    with tempfile.TemporaryDirectory(prefix="geodata-eval-") as tmp:
        # Two siblings: only `workspace` is mounted at /workspace, so the
        # session's credential copy in `home` sits outside the directory
        # the agent is pointed at.
        workspace = Path(tmp) / "workspace"
        session_home = Path(tmp) / "home"
        workspace.mkdir()
        session_home.mkdir()
        shutil.copy(REPO_ROOT / "prompts" / "task.md", workspace / "task.md")
        shutil.copy(
            REPO_ROOT / "fixtures" / "questions.yaml",
            workspace / "questions.yaml",
        )
        # The policy documents are the binding spec the agent implements; the
        # golden fixtures never enter the workspace.
        shutil.copytree(REPO_ROOT / "policies", workspace / "policies")
        lists_dir = workspace / "lists"
        lists_dir.mkdir()
        for src in list_files(input_mode):
            shutil.copy(src, lists_dir / src.name)
        (workspace / "answers").mkdir()

        cmd = docker_command(workspace, session_home, model_id, container)
        if dry_run:
            print(" ".join(cmd))
            return

        clear_pass_dir(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        transcript_path = out_dir / "transcript.jsonl"
        errors_path = Path(tmp) / "stderr.log"
        started = datetime.now(timezone.utc).isoformat()
        started_at = time.monotonic()
        print(f"[{model}/pass-{pass_n}] starting")

        # Popen rather than run(): a session works remote catalogs for tens of
        # minutes, and a single line at the end cannot tell a run that is
        # progressing from one that is wedged. stderr goes to a file because a
        # pipe nobody drains fills its buffer and deadlocks the container.
        with open(transcript_path, "w", encoding="utf-8") as transcript, \
                open(errors_path, "w", encoding="utf-8") as errors:
            proc = subprocess.Popen(cmd, stdout=transcript, stderr=errors,
                                    text=True)
            next_beat = started_at + HEARTBEAT_SECONDS
            followed = 0
            try:
                while proc.poll() is None:
                    time.sleep(POLL_SECONDS)
                    if follow:
                        followed = follow_transcript(transcript_path, followed)
                    if time.monotonic() < next_beat:
                        continue
                    next_beat = time.monotonic() + HEARTBEAT_SECONDS
                    turns, last_tool = progress_snapshot(transcript_path)
                    elapsed = (time.monotonic() - started_at) / 60
                    print(
                        f"[{model}/pass-{pass_n}] {elapsed:.0f}m"
                        f" {turns} turns, last: {last_tool}",
                        flush=True,
                    )
            finally:
                # Ctrl-C kills this process, not the container it started, and
                # `docker run --rm` only cleans up after a container it is
                # still attached to exits. Without this the session keeps
                # running headless, holding the mounted credential copy open
                # and writing into a transcript nobody reads.
                stop_session(proc, container)
            if follow:
                follow_transcript(transcript_path, followed)

        duration = round(time.monotonic() - started_at, 1)
        if proc.returncode != 0:
            print(f"[{model}/pass-{pass_n}] session exited {proc.returncode}",
                  file=sys.stderr)
            print(errors_path.read_text(encoding="utf-8")[-2000:],
                  file=sys.stderr)

        answers_out = out_dir / "answers"
        if answers_out.exists():
            shutil.rmtree(answers_out)
        shutil.copytree(workspace / "answers", answers_out)

        stats = parse_result_record(transcript_path)
        meta = {
            "model": model,
            "model_id": model_id,
            "pass": pass_n,
            "started_utc": started,
            "finished_utc": datetime.now(timezone.utc).isoformat(),
            "duration_seconds": duration,
            "exit_code": proc.returncode,
            "harness_commit": harness_commit(),
            "input_mode": input_mode,
            **stats,
            **tool_timings(transcript_path),
            "imputed_cost_usd": round(
                imputed_cost_usd(
                    model,
                    stats["input_tokens"],
                    stats["output_tokens"],
                    stats["cache_creation_tokens"],
                    stats["cache_read_tokens"],
                ),
                6,
            ),
        }
        (out_dir / "meta.json").write_text(json.dumps(meta, indent=2) + "\n")
        waited = meta["slow_tool_seconds"]
        print(
            f"[{model}/pass-{pass_n}] done:"
            f" {stats['turns']} turns,"
            f" {duration / 60:.0f}m wall"
            f" ({waited / 60:.0f}m in slow tool calls,"
            f" {meta['timed_out_tool_calls']} timed out),"
            f" ${meta['imputed_cost_usd']:.4f} imputed"
        )


def next_free_pass(model: str) -> int:
    """The lowest pass number with no directory yet.

    Runs are the record. Re-running a model wrote over the previous pass's
    transcript, meta, and answers, so iterating one pass at a time destroyed
    the pass before it -- including passes already graded, which the audit
    trail depends on. Numbering forward keeps every run.
    """
    model_dir = REPO_ROOT / "results" / model
    n = 1
    while (model_dir / f"pass-{n}").exists():
        n += 1
    return n


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--model", choices=sorted(PRICES), required=True)
    ap.add_argument("--passes", type=int, default=10)
    ap.add_argument("--start-pass", type=int, default=None,
                    help="number from here; defaults to the first free pass")
    ap.add_argument("--force", action="store_true",
                    help="overwrite an existing pass instead of refusing")
    ap.add_argument("--dry-run", action="store_true",
                    help="print the docker command instead of running")
    ap.add_argument("--follow", action="store_true",
                    help="print each tool call as the session makes it")
    ap.add_argument("--input-mode", choices=sorted(INPUT_FILES), default="csv",
                    help="encoding of the input list; see policies/INPUTS.md")
    args = ap.parse_args()

    start = args.start_pass
    if start is None:
        start = next_free_pass(args.model)
        if start > 1 and not args.dry_run:
            print(f"[{args.model}] existing passes found, starting at {start}")

    wanted = range(start, start + args.passes)
    if not args.dry_run and not args.force:
        taken = [n for n in wanted
                 if (REPO_ROOT / "results" / args.model / f"pass-{n}").exists()]
        if taken:
            names = ", ".join(f"pass-{n}" for n in taken)
            raise SystemExit(
                f"{names} already exist under results/{args.model}. Drop "
                f"--start-pass to number forward, or pass --force to "
                f"overwrite them."
            )

    for n in wanted:
        run_session(args.model, n, args.dry_run, args.input_mode, args.follow)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
