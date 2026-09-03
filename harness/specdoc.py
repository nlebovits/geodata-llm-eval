"""Render the agent-facing view of SPEC.md.

SPEC.md is the single ground-truth document. It carries three kinds of
content: the rules themselves, per-rule metadata (provenance, grader
equivalences, dependent questions), and the open-questions list at the end.
A session gets only the first kind. The metadata would tell it which PRs
fixed what and which conventions the grader forgives, and the open-questions
list is an itemised map of where the oracle is contestable — none of which a
graded session should see.

The view is text-to-text so the ablation operations, which work on markdown
headings, apply to the rendered file exactly as they applied to the policy
files before consolidation.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SPEC_PATH = REPO_ROOT / "SPEC.md"

# The per-rule metadata lines: "Questions affected:" paragraphs and any
# provenance/equivalence bullets. A bullet's wrapped continuation lines are
# indented, so they are stripped with it.
METADATA = re.compile(
    r"^(Questions affected:|Provenance:"
    r"|- (provenance|questions|equivalence):)"
)
CONTINUATION = re.compile(r"^ {2,}\S")

# The agent-facing preamble that replaces the Status paragraph and the
# field-key explanation, which describe repo mechanics rather than the task.
PREAMBLE = (
    "This document is the complete specification for the task. "
    "Read all of it before starting work."
)


def agent_view(text: str) -> str:
    """The spec as a session sees it: rules only.

    Removes the Status preamble, every metadata bullet with its wrapped
    continuation lines, and the Open questions section. Everything else --
    headings, prose, tables, the question definitions -- passes through
    unchanged, so a heading named by an ablation arm survives rendering
    verbatim.
    """
    lines = text.splitlines()

    # Preamble: everything between the H1 and the first horizontal rule.
    out: list[str] = []
    i = 0
    if lines and lines[0].startswith("# "):
        out += [lines[0], "", PREAMBLE]
        while i < len(lines) and lines[i].strip() != "---":
            i += 1

    skipping_bullet = False
    for line in lines[i:]:
        if line.startswith("## Open questions"):
            break
        if METADATA.match(line):
            skipping_bullet = True
            continue
        if skipping_bullet and CONTINUATION.match(line):
            continue
        skipping_bullet = False
        out.append(line)

    # Close the seams the removals leave: at most one blank line in a row,
    # exactly one trailing newline. Same discipline as an ablation cut — a
    # session must not be able to see that anything was removed.
    tidy: list[str] = []
    for line in out:
        if not line.strip() and tidy and not tidy[-1].strip():
            continue
        tidy.append(line)
    while tidy and not tidy[-1].strip():
        tidy.pop()
    return "\n".join(tidy) + "\n"


def render(repo_root: Path | None = None) -> str:
    """The agent view of the repo's SPEC.md."""
    root = repo_root or REPO_ROOT
    return agent_view((root / "SPEC.md").read_text(encoding="utf-8"))
