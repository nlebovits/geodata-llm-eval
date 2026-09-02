"""Build the accuracy grid and Pareto plot from graded sessions.

Reads results/{model}/{run_id}/{grades.json,meta.json} and writes:
    results/summary.csv    one row per session
    results/report.md      per-model table (mean accuracy, cost, spread)
    results/pareto.png     accuracy vs imputed dollars, one point per session

Usage:
    python harness/report.py [--results results/]
"""

from __future__ import annotations

import argparse
import csv
import json
import statistics
import sys
from pathlib import Path
from typing import Any

import layout
import matplotlib
import reliability
from layout import is_scored, run_dirs

matplotlib.use("Agg")
import matplotlib.pyplot as plt

sys.path.insert(0, str(Path(__file__).resolve().parent))
from grade import load_questions, stage_summary

# One graded session, flattened for the tables and the plot: identity,
# scores, cost, and runtime in one row.
Session = dict[str, Any]

MODEL_ORDER = ["haiku", "sonnet", "opus"]
MODEL_LABELS = {
    "haiku": "Haiku 4.5",
    "sonnet": "Sonnet 5",
    "opus": "Opus 4.8",
}


def load_sessions(results_dir: Path) -> list[Session]:
    sessions: list[Session] = []
    for session_dir in run_dirs(results_dir):
        grades_path = session_dir / "grades.json"
        meta_path = session_dir / "meta.json"
        if not (grades_path.exists() and meta_path.exists()):
            continue
        grades = json.loads(grades_path.read_text())
        meta = json.loads(meta_path.read_text())
        # A grader failure can leave artifacts from an older successful pass.
        # They are stale by definition and must not leak into diagnostics.
        if not is_scored(meta) or layout.trial_status(meta) == layout.GRADER_ERROR:
            continue
        total = len(grades)
        correct = sum(1 for v in grades.values() if v == "correct")
        near_miss = sum(1 for v in grades.values() if v == "near_miss")
        duration = meta.get("duration_seconds", 0.0) or 0.0
        slow = meta.get("slow_tool_seconds", 0.0) or 0.0
        sessions.append(
            {
                "model": meta["model"],
                "run_id": meta.get("run_id", session_dir.name),
                "label": meta.get("label", ""),
                "accuracy": correct / total if total else 0.0,
                "correct": correct,
                "near_miss": near_miss,
                "total": total,
                "cost_usd": meta.get("imputed_cost_usd", 0.0),
                "turns": meta.get("turns", 0),
                "duration_seconds": duration,
                "slow_tool_seconds": slow,
                "slow_tool_share": slow / duration if duration else 0.0,
                "timed_out_tool_calls": meta.get("timed_out_tool_calls", 0),
                "grades": grades,
            }
        )
    return sessions


def _mean_or_none(values: list[float | None]) -> float | None:
    vals = [v for v in values if v is not None]
    return statistics.mean(vals) if vals else None


def stage_grid_lines(
    sessions: list[Session], questions: list[dict[str, Any]]
) -> list[str]:
    """Per-model, per-stage mean raw and conditional accuracy across sessions.

    Conditional accuracy is 'correct given every upstream dependency was
    correct'. A stage whose raw accuracy is high but conditional is low is
    failing on its own; the reverse means it is inheriting upstream errors.
    """
    if not questions:
        return []
    stages = sorted({q["stage"] for q in questions})
    lines = [
        "## Accuracy by workflow stage",
        "",
        "Raw = correct / all in stage. Cond. = correct / questions whose",
        "dependencies all passed (the error-propagation-adjusted score).",
        "",
    ]
    header = "| Model | " + " | ".join(f"S{s}" for s in stages) + " |"
    sep = "|-------|" + "|".join(["-----"] * len(stages)) + "|"
    lines += [header, sep]
    for model in MODEL_ORDER:
        rows = [s for s in sessions if s["model"] == model]
        if not rows:
            continue
        summaries = [stage_summary(s["grades"], questions) for s in rows]
        cells = []
        for st in stages:
            raw = _mean_or_none([sm[st]["raw"] for sm in summaries])
            cond = _mean_or_none([sm[st]["conditional"] for sm in summaries])
            raw_s = f"{raw:.0%}" if raw is not None else "–"
            cond_s = f"{cond:.0%}" if cond is not None else "–"
            cells.append(f"{raw_s}/{cond_s}")
        lines.append(f"| {MODEL_LABELS[model]} | " + " | ".join(cells) + " |")
    lines.append("")
    return lines


def consistency_lines(results_dir: Path) -> list[str]:
    """Render results/consistency.json if it exists: consistency@N beside the
    oracle deviation, and the unstable-cadaster list."""
    path = results_dir / "consistency.json"
    if not path.exists():
        return []
    c = json.loads(path.read_text())
    n = c.get("n_runs", "?")
    lines = [
        "## Cross-run consistency",
        "",
        f"Agreement across {n} runs of the workflow artifact. Consistency is",
        "not correctness: the oracle column is the reality check.",
        "",
        "| Metric | Across runs | vs oracle |",
        "|--------|-------------|-----------|",
    ]

    def fmt(x: object) -> str:
        return f"{x:.3f}" if isinstance(x, (int, float)) else "–"

    oracle = c.get("oracle") or {}
    lines.append(
        f"| Flagged-set Jaccard | {fmt(c.get('flag_jaccard'))}"
        f" | {fmt(oracle.get('flag_jaccard'))} |"
    )
    lines.append(
        f"| Contact agreement | {fmt(c.get('contact_agreement'))}"
        f" | {fmt(oracle.get('contact_agreement'))} |"
    )
    lines.append(f"| Ranking tau-b | {fmt(c.get('ranking_tau'))} | – |")
    lines.append(f"| Contact kappa | {fmt(c.get('contact_kappa'))} | – |")
    lines.append("")
    unstable = c.get("unstable_cadasters") or []
    if unstable:
        lines.append(
            f"**Unstable cadasters** (flagged by some runs but not "
            f"all): {len(unstable)}"
        )
        lines.append("")
        for cid in unstable[:20]:
            lines.append(f"- `{cid}`")
        if len(unstable) > 20:
            lines.append(f"- … and {len(unstable) - 20} more")
        lines.append("")
    return lines


def reliability_lines(results_dir: Path) -> list[str]:
    """Strict task success and pass^k, per fingerprint.

    First section in the report because it answers the question the benchmark
    exists to answer. Everything below it is diagnostic: mean accuracy says
    how much of the workflow tends to come back right, and this says how often
    all of it does.
    """
    groups = reliability.summarise(run_dirs(results_dir))
    if not groups:
        return []
    lines = [
        "## Strict task success and reliability",
        "",
        "A trial passes when every critical question graded correct. A near",
        "miss does not pass. Agent timeouts, early stops, and empty runs are",
        "failures and stay in the denominator; only a dead credential,",
        "unavailable infrastructure, or a grader crash invalidates a trial.",
        "",
        "pass^k is the chance that k independent trials all pass, estimated",
        "without replacement from the trials on disk, with a 95% interval. It",
        "is blank where there are fewer than k valid trials.",
        "",
        "Runs are never pooled across a spec edit, a regenerated golden, a",
        "repinned dataset, or a harness change. Each row is one fingerprint.",
        "",
        (
            "| Configuration | Attempted | Invalid | Valid | Strict success |"
            " pass^3 | pass^5 | pass^10 |"
        ),
        (
            "|---------------|-----------|---------|-------|----------------|"
            "--------|--------|---------|"
        ),
    ]

    def cell(estimate: reliability.PassHatK | None, valid: int, k: int) -> str:
        if estimate is None:
            return f"– (n={valid}<{k})"
        return f"{estimate.point:.0%} [{estimate.low:.0%}–{estimate.high:.0%}]"

    for group in groups:
        rate = group.strict_success_rate
        rate_s = (
            f"{rate:.0%} ({group.passed}/{group.valid})" if rate is not None else "–"
        )
        cells = " | ".join(
            cell(group.pass_hat(k), group.valid, k) for k in reliability.REPORTED_K
        )
        lines.append(
            f"| {group.fingerprint.label()} | {group.attempted}"
            f" | {group.invalid} ({group.invalid_rate:.0%})"
            f" | {group.valid} | {rate_s} | {cells} |"
        )
    lines.append("")

    lines += [
        "### Trial outcomes",
        "",
        "| Configuration | " + " | ".join(sorted(layout.TRIAL_STATUSES)) + " |",
        "|---------------|" + "|".join(["---"] * len(layout.TRIAL_STATUSES)) + "|",
    ]
    for group in groups:
        counts = " | ".join(
            str(group.statuses.get(status, 0))
            for status in sorted(layout.TRIAL_STATUSES)
        )
        lines.append(f"| {group.fingerprint.label()} | {counts} |")
    lines.append("")

    lines += [
        "### Completion budget",
        "",
        "Configured limits and observed high-water use. A reliability figure",
        "means nothing without it: the same agent passing nine trials in ten",
        "says something different at three resumes than at one.",
        "",
        (
            "| Configuration | Resume limit | Max resumes used | Max turns used |"
            " Wall limit | Max wall used | Total cost |"
        ),
        (
            "|---------------|--------------|------------------|----------------|"
            "------------|---------------|------------|"
        ),
    ]
    for group in groups:
        b = group.budget
        resume_limit = (
            str(max(0, b.attempt_limit - 1)) if b.attempt_limit is not None else "–"
        )
        resumes_used = max(0, b.max_attempts_used - 1)
        if b.wall_limit_seconds is None:
            wall_limit = "–"
        elif b.wall_limit_seconds == 0:
            wall_limit = "unlimited"
        else:
            wall_limit = f"{b.wall_limit_seconds / 60:g}m"
        lines.append(
            f"| {group.fingerprint.label()} | {resume_limit}"
            f" | {resumes_used} | {b.max_turns_used} | {wall_limit}"
            f" | {b.max_wall_seconds_used / 60:.0f}m"
            f" | ${b.total_cost_usd:.2f} |"
        )
    lines.append("")

    invalidated = [(g, run) for g in groups for run in g.invalid_runs]
    if invalidated:
        lines += ["**Invalidated trials**", ""]
        for group, (name, status) in invalidated:
            lines.append(f"- `{name}` — {status}")
        lines.append("")
    return lines


def runtime_lines(sessions: list[Session]) -> list[str]:
    """Wall clock, and how much of it went to waiting on remote reads.

    A session that loses a third of its turns to source.coop timeouts scores
    worse for reasons that have nothing to do with the model. Printing the
    share next to accuracy makes a degraded run visible instead of inferred.
    """
    if not sessions:
        return []
    lines = [
        "## Runtime",
        "",
        "Slow-call share is time inside tool calls slow enough to emit a",
        "heartbeat, over wall clock. A high share with timeouts means the",
        "run was degraded by the network, not by the model.",
        "",
        "| Model | Mean wall clock | In slow tool calls | Timed-out calls |",
        "|-------|-----------------|--------------------|-----------------|",
    ]
    for model in MODEL_ORDER:
        rows = [s for s in sessions if s["model"] == model]
        if not rows:
            continue
        wall = statistics.mean([s["duration_seconds"] for s in rows])
        share = statistics.mean([s["slow_tool_share"] for s in rows])
        timeouts = sum(s["timed_out_tool_calls"] for s in rows)
        lines.append(
            f"| {MODEL_LABELS[model]} | {wall / 60:.0f}m | {share:.0%} | {timeouts} |"
        )
    lines.append("")
    return lines


def write_summary_csv(sessions: list[Session], path: Path) -> None:
    fields = [
        "model",
        "run_id",
        "label",
        "accuracy",
        "correct",
        "near_miss",
        "total",
        "cost_usd",
        "turns",
        "duration_seconds",
        "slow_tool_seconds",
        "timed_out_tool_calls",
    ]
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows({k: s[k] for k in fields} for s in sessions)


def accuracy_lines(sessions: list[Session]) -> list[str]:
    """Mean question accuracy per model: partial credit, for triage.

    A dependent workflow fails all at once, so the share of questions a run
    got right says where it went wrong. It does not say whether the run
    worked, which is what the reliability section is for.
    """
    lines = [
        "## Question accuracy",
        "",
        "Accuracy is the share of questions graded correct against the",
        "golden fixture. Cost is imputed from logged tokens at list API",
        "prices (see harness/pricing.py).",
        "",
        "Near misses clear ten times the grading tolerance but not the",
        "tolerance itself: computed right, formatted or rounded differently.",
        "",
        (
            "| Model | Passes | Mean accuracy | Accuracy range |"
            " Mean near misses | Mean cost (USD) |"
        ),
        (
            "|-------|--------|---------------|----------------|"
            "------------------|-----------------|"
        ),
    ]
    for model in MODEL_ORDER:
        rows = [s for s in sessions if s["model"] == model]
        if not rows:
            continue
        accs = [s["accuracy"] for s in rows]
        costs = [s["cost_usd"] for s in rows]
        near = statistics.mean([s["near_miss"] for s in rows])
        lines.append(
            f"| {MODEL_LABELS[model]} | {len(rows)}"
            f" | {statistics.mean(accs):.1%}"
            f" | {min(accs):.1%} – {max(accs):.1%}"
            f" | {near:.1f}"
            f" | ${statistics.mean(costs):.4f} |"
        )
    lines.append("")
    return lines


def write_report_md(
    sessions: list[Session],
    path: Path,
    questions: list[dict[str, Any]] | None = None,
    results_dir: Path | None = None,
) -> None:
    """Reliability first, diagnostics second.

    The order is the argument. A reader who stops after the first table
    should leave knowing how often the whole workflow came back correct, not
    a mean that can sit at 93% while every trial failed.
    """
    lines = [
        "# EUDR workflow benchmark results",
        "",
        "Strict task success comes first. Everything under Diagnostics is",
        "partial credit: useful for triage, not a claim about reliability.",
        "",
    ]
    if results_dir is not None:
        lines += reliability_lines(results_dir)
    lines += [
        "## Diagnostics",
        "",
        "Partial credit and where it was lost. None of what follows is a",
        "reliability claim: a configuration can answer almost every question",
        "correctly and still fail most trials, which is what the section",
        "above is for.",
        "",
    ]
    lines += accuracy_lines(sessions)
    lines += runtime_lines(sessions)
    lines += stage_grid_lines(sessions, questions or [])
    if results_dir is not None:
        lines += consistency_lines(results_dir)
    path.write_text("\n".join(lines))


def write_pareto_png(sessions: list[Session], path: Path) -> None:
    fig, ax = plt.subplots(figsize=(7, 5))
    colors = {"haiku": "tab:green", "sonnet": "tab:blue", "opus": "tab:purple"}
    for model in MODEL_ORDER:
        rows = [s for s in sessions if s["model"] == model]
        if not rows:
            continue
        ax.scatter(
            [s["cost_usd"] for s in rows],
            [s["accuracy"] for s in rows],
            label=MODEL_LABELS[model],
            color=colors[model],
            alpha=0.7,
        )
    ax.set_xlabel("Imputed cost per session (USD, list prices)")
    ax.set_ylabel("Accuracy (share of questions correct)")
    ax.set_ylim(0, 1.02)
    ax.set_title("Accuracy vs cost: EUDR workflow, 10 passes per model")
    ax.legend()
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    fig.savefig(path, dpi=150)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--results", type=Path, default=Path("results"))
    ap.add_argument("--questions", type=Path, default=Path("fixtures/questions.yaml"))
    args = ap.parse_args()

    sessions = load_sessions(args.results)
    if not sessions:
        print(
            f"no graded sessions under {args.results} (run grade.py first)",
            file=sys.stderr,
        )
        return 1

    questions = load_questions(args.questions)
    write_summary_csv(sessions, args.results / "summary.csv")
    write_report_md(sessions, args.results / "report.md", questions, args.results)
    write_pareto_png(sessions, args.results / "pareto.png")
    print(f"wrote summary.csv, report.md, pareto.png to {args.results}/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
