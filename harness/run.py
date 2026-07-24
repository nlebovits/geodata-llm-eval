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
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from pricing import PRICES, imputed_cost_usd

IMAGE = "geodata-llm-eval"
REPO_ROOT = Path(__file__).resolve().parent.parent

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


def docker_command(workspace: Path, model_id: str) -> list[str]:
    return [
        "docker", "run", "--rm",
        "-v", f"{workspace}:/workspace",
        "-e", "ANTHROPIC_API_KEY",  # pass through if set; else CLI login volume
        IMAGE,
        "-p", "Read task.md and complete it.",
        "--model", model_id,
        "--output-format", "stream-json",
        "--verbose",
        "--dangerously-skip-permissions",
    ]


def parse_result_record(transcript_path: Path) -> dict:
    """Pull token counts and turn count from the stream-json transcript.

    The final `result` record carries cumulative usage; turn count is the
    number of assistant records.
    """
    usage: dict = {}
    turns = 0
    with open(transcript_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
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


def run_session(model: str, pass_n: int, dry_run: bool,
                input_mode: str = "csv") -> None:
    model_id = PRICES[model].model_id
    out_dir = REPO_ROOT / "results" / model / f"pass-{pass_n}"

    with tempfile.TemporaryDirectory(prefix="geodata-eval-") as tmp:
        workspace = Path(tmp)
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

        cmd = docker_command(workspace, model_id)
        if dry_run:
            print(" ".join(cmd))
            return

        out_dir.mkdir(parents=True, exist_ok=True)
        transcript_path = out_dir / "transcript.jsonl"
        started = datetime.now(timezone.utc).isoformat()
        print(f"[{model}/pass-{pass_n}] starting")

        with open(transcript_path, "w", encoding="utf-8") as transcript:
            proc = subprocess.run(cmd, stdout=transcript, stderr=subprocess.PIPE,
                                  text=True)
        if proc.returncode != 0:
            print(f"[{model}/pass-{pass_n}] session exited {proc.returncode}",
                  file=sys.stderr)
            print(proc.stderr[-2000:], file=sys.stderr)

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
            "exit_code": proc.returncode,
            "harness_commit": harness_commit(),
            "input_mode": input_mode,
            **stats,
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
        print(
            f"[{model}/pass-{pass_n}] done:"
            f" {stats['turns']} turns,"
            f" ${meta['imputed_cost_usd']:.4f} imputed"
        )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--model", choices=sorted(PRICES), required=True)
    ap.add_argument("--passes", type=int, default=10)
    ap.add_argument("--start-pass", type=int, default=1,
                    help="resume numbering from here")
    ap.add_argument("--dry-run", action="store_true",
                    help="print the docker command instead of running")
    ap.add_argument("--input-mode", choices=sorted(INPUT_FILES), default="csv",
                    help="encoding of the input list; see policies/INPUTS.md")
    args = ap.parse_args()

    for n in range(args.start_pass, args.start_pass + args.passes):
        run_session(args.model, n, args.dry_run, args.input_mode)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
