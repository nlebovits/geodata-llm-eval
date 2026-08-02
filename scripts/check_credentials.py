#!/usr/bin/env python3
"""Refuse content that carries Anthropic credentials.

Two ways a token could reach this history:

1. Someone stages ~/.claude/.credentials.json, or a copy of it.
2. A session reads its own mounted credential copy and echoes the token
   into stdout. That stdout becomes results/<model>/pass-N/transcript.jsonl,
   and transcripts are committed on purpose, for audit.

The second is the one .gitignore cannot catch, so this scans content as
well as paths.

Paths come in on argv, which is how prek invokes it: at commit time it
passes the staged files, and `prek run --all-files` passes the whole
tree. The same code is therefore both the local guard and the CI
backstop. prek stashes unstaged changes before running hooks, so at
commit time the working tree this reads is the content being committed.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

# Anthropic tokens: sk-ant-api03-… (API keys) and sk-ant-oat01-… (OAuth).
# The prefix alone is the signal; length and body vary.
TOKEN = re.compile(rb"sk-ant-[A-Za-z0-9_-]{8,}")

# The credential file's own shape, in case a future token stops matching
# the prefix above. Both keys must appear for this to fire, so ordinary
# prose about refresh tokens does not trip it.
CRED_SHAPE = (re.compile(rb'"accessToken"\s*:'), re.compile(rb'"refreshToken"\s*:'))

BLOCKED_PATHS = re.compile(
    r"(^|/)\.credentials\.json$"
    r"|(^|/)\.claude/"
    r"|(^|/)\.env($|\.)"
    r"|\.pem$"
)

ADVICE = """
Unstage the file, or scrub the token from the transcript before
committing. If a token did reach a transcript, treat it as leaked
and run `claude login` to rotate it.
"""


def problems_in(paths: list[str]) -> list[str]:
    """Return one message per path that must not be committed."""
    problems: list[str] = []

    for path in paths:
        if BLOCKED_PATHS.search(path):
            problems.append(f"{path}: credential file must never be committed")
            continue

        try:
            blob = Path(path).read_bytes()
        except OSError:
            # Deleted between staging and this read, or unreadable. Either
            # way there is no content here to leak.
            continue
        if not blob:
            continue

        if TOKEN.search(blob):
            line = next(
                (n for n, ln in enumerate(blob.splitlines(), 1) if TOKEN.search(ln)),
                0,
            )
            problems.append(f"{path}:{line}: contains an sk-ant-* token")
        if all(p.search(blob) for p in CRED_SHAPE):
            problems.append(f"{path}: looks like a credentials payload")

    return problems


def main(argv: list[str]) -> int:
    problems = problems_in(argv)
    if not problems:
        return 0

    print("refusing to commit credentials\n", file=sys.stderr)
    for problem in problems:
        print(f"  {problem}", file=sys.stderr)
    print(ADVICE, file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
