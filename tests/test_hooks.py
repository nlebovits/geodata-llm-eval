"""The credential guard, exercised against throwaway files.

Every token in this file is a decoy: the right shape, no value.

The guard reads paths off argv, so these call it the way prek does
rather than driving a git commit. One test covers the wiring itself,
since a scanner nothing invokes is no guard at all.
"""

from __future__ import annotations

import os
import shlex
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

import pytest
import yaml

# The `written` fixture: give it a name and a body, get back a path.
Writer = Callable[[str, str], str]

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = REPO_ROOT / "scripts"
CONFIG = REPO_ROOT / ".pre-commit-config.yaml"

sys.path.insert(0, str(SCRIPTS))

from check_credentials import problems_in

# Shape-accurate, value-free. The prefix is never whole in this file, so
# the guard does not trip on the repo's own history.
DECOY = "sk-ant-" + "oat01-" + "AAAAAAAAdecoyBBBBBBBB"

# Split for the same reason: the shape check fires when both names appear
# in one blob, and this file holds both because the test needs them.
PAYLOAD = '{"' + "access" + 'Token": "x", "refreshToken": "y"}\n'


@pytest.fixture
def written(tmp_path: Path) -> Writer:
    """Write a file under tmp_path and hand back its path as a string."""

    def _written(name: str, body: str) -> str:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
        return str(path)

    return _written


def test_token_in_content_is_blocked(written: Writer) -> None:
    """The case .gitignore cannot catch: a token inside a transcript."""
    path = written(
        "results/opus/pass-1/transcript.jsonl",
        f'{{"type": "text", "text": "your key is {DECOY}"}}\n',
    )
    problems = problems_in([path])
    assert len(problems) == 1
    assert "sk-ant-* token" in problems[0]


def test_token_report_names_the_line(written: Writer) -> None:
    """A transcript is thousands of lines; the number is what makes it
    findable."""
    path = written("transcript.jsonl", f"clean\nclean\n{DECOY}\n")
    assert problems_in([path])[0].endswith(":3: contains an sk-ant-* token")


def test_credential_path_is_blocked_without_reading_it(written: Writer) -> None:
    """Path alone is enough. The file need not still exist."""
    problems = problems_in(["home/.claude/.credentials.json"])
    assert len(problems) == 1
    assert "must never be committed" in problems[0]


@pytest.mark.parametrize(
    "path",
    [
        ".credentials.json",
        "backup/.credentials.json",
        "sub/.claude/settings.json",
        ".env",
        ".env.local",
        "keys/server.pem",
    ],
)
def test_every_blocked_path_shape(path: str) -> None:
    assert problems_in([path]), f"{path} should be refused"


def test_credential_payload_shape_is_blocked(written: Writer) -> None:
    """Belt and braces: catches a payload whose token stops matching the
    sk-ant- prefix, without firing on prose that mentions one key."""
    path = written("dump.json", PAYLOAD)
    problems = problems_in([path])
    assert len(problems) == 1
    assert "credentials payload" in problems[0]


def test_prose_about_refresh_tokens_is_allowed(written: Writer) -> None:
    """Docs discussing the auth flow are not collateral damage."""
    path = written(
        "README.md",
        "The CLI writes a refreshToken and rotates it on expiry.\n",
    )
    assert problems_in([path]) == []


def test_clean_tree_reports_nothing(written: Writer) -> None:
    paths = [written("a.py", "x = 1\n"), written("b.md", "# title\n")]
    assert problems_in(paths) == []


def test_missing_file_is_not_an_error() -> None:
    """prek can hand over a path deleted since staging."""
    assert problems_in(["no/such/file.txt"]) == []


def test_guard_is_wired_into_prek() -> None:
    """The scanner has to run on every commit and every CI run. This is
    the line that makes that true."""
    config = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    hooks = [
        hook
        for repo in config["repos"]
        if repo["repo"] == "local"
        for hook in repo["hooks"]
    ]
    guard = next((h for h in hooks if h["id"] == "check-credentials"), None)
    assert guard is not None, "check-credentials hook is missing"
    assert "scripts/check_credentials.py" in guard["entry"]
    # The hygiene hooks skip results/. Transcripts are exactly where a
    # leaked token lands, so this one must see them.
    assert "exclude" not in guard, "the guard must not skip any path"
    assert "exclude" not in config, (
        "a top-level exclude would apply to the guard too, and cannot be "
        "overridden per hook"
    )
    # No stages key means every stage, which includes the pre-commit run.
    assert "stages" not in guard


def test_import_linter_hook_propagates_failure(tmp_path: Path) -> None:
    """A configured, failing architecture check must fail the hook."""
    config = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    hooks = [
        hook
        for repo in config["repos"]
        if repo["repo"] == "local"
        for hook in repo["hooks"]
    ]
    entry = next(hook for hook in hooks if hook["id"] == "import-linter")["entry"]

    (tmp_path / "pyproject.toml").write_text(
        "[tool.importlinter]\nroot_package = 'harness'\n",
        encoding="utf-8",
    )
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake_uv = bin_dir / "uv"
    fake_uv.write_text("#!/bin/sh\nexit 23\n", encoding="utf-8")
    fake_uv.chmod(0o755)
    environment = os.environ.copy()
    environment["PATH"] = f"{bin_dir}{os.pathsep}{environment['PATH']}"

    completed = subprocess.run(
        shlex.split(entry),
        cwd=tmp_path,
        env=environment,
        check=False,
    )

    assert completed.returncode == 23
