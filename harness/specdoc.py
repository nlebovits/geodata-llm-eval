"""Read the exact agent contract.

SPEC.md is already the complete agent-facing artifact. Reviewer and grader
notes live in docs/SPEC_REVIEW.md, which the harness never mounts. Keeping the
boundary at the file level avoids a fail-open text transformation.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SPEC_PATH = REPO_ROOT / "SPEC.md"
CONTRACT_VERSION = 2


def agent_view(text: str) -> str:
    """Return the contract unchanged."""
    return text


def render(repo_root: Path | None = None) -> str:
    """Read the exact SPEC.md that a session receives."""
    root = repo_root or REPO_ROOT
    return agent_view((root / "SPEC.md").read_text(encoding="utf-8"))
