"""Build the accuracy grid and Pareto plot from graded sessions.

Reads results/{model}/pass-{n}/{grades.json,meta.json} and writes:
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

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

MODEL_ORDER = ["haiku", "sonnet", "opus"]
MODEL_LABELS = {
    "haiku": "Haiku 4.5",
    "sonnet": "Sonnet 5",
    "opus": "Opus 4.8",
}


def load_sessions(results_dir: Path) -> list[dict]:
    sessions = []
    for session_dir in sorted(results_dir.glob("*/pass-*")):
        grades_path = session_dir / "grades.json"
        meta_path = session_dir / "meta.json"
        if not (grades_path.exists() and meta_path.exists()):
            continue
        grades = json.loads(grades_path.read_text())
        meta = json.loads(meta_path.read_text())
        total = len(grades)
        correct = sum(1 for v in grades.values() if v == "correct")
        sessions.append({
            "model": meta["model"],
            "pass": meta["pass"],
            "accuracy": correct / total if total else 0.0,
            "correct": correct,
            "total": total,
            "cost_usd": meta.get("imputed_cost_usd", 0.0),
            "turns": meta.get("turns", 0),
        })
    return sessions


def write_summary_csv(sessions: list[dict], path: Path) -> None:
    fields = ["model", "pass", "accuracy", "correct", "total", "cost_usd", "turns"]
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows({k: s[k] for k in fields} for s in sessions)


def write_report_md(sessions: list[dict], path: Path) -> None:
    lines = [
        "# Benchmark results",
        "",
        "Accuracy is the share of questions graded correct against the",
        "golden fixture. Cost is imputed from logged tokens at list API",
        "prices (see harness/pricing.py).",
        "",
        "| Model | Passes | Mean accuracy | Accuracy range | Mean cost (USD) |",
        "|-------|--------|---------------|----------------|-----------------|",
    ]
    for model in MODEL_ORDER:
        rows = [s for s in sessions if s["model"] == model]
        if not rows:
            continue
        accs = [s["accuracy"] for s in rows]
        costs = [s["cost_usd"] for s in rows]
        lines.append(
            f"| {MODEL_LABELS[model]} | {len(rows)}"
            f" | {statistics.mean(accs):.1%}"
            f" | {min(accs):.1%} – {max(accs):.1%}"
            f" | ${statistics.mean(costs):.4f} |"
        )
    lines.append("")
    path.write_text("\n".join(lines))


def write_pareto_png(sessions: list[dict], path: Path) -> None:
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
    ax.set_title("Accuracy vs cost: trazo3-fields workflow, 10 passes per model")
    ax.legend()
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    fig.savefig(path, dpi=150)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--results", type=Path, default=Path("results"))
    args = ap.parse_args()

    sessions = load_sessions(args.results)
    if not sessions:
        print(f"no graded sessions under {args.results} "
              "(run grade.py first)", file=sys.stderr)
        return 1

    write_summary_csv(sessions, args.results / "summary.csv")
    write_report_md(sessions, args.results / "report.md")
    write_pareto_png(sessions, args.results / "pareto.png")
    print(f"wrote summary.csv, report.md, pareto.png to {args.results}/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
