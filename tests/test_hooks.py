"""The pre-commit credential guard, exercised against throwaway git repos.

Every token in this file is a decoy with the right shape and no value.
"""

import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
HOOK = REPO_ROOT / "hooks" / "pre-commit"

# Shape-accurate, value-free. Split so the literal prefix never appears
# whole in this file and trip the hook on the repo's own history.
DECOY = "sk-ant-" + "oat01-" + "AAAAAAAAdecoyBBBBBBBB"

# Likewise split: the shape rule fires when both key names appear in one
# blob, and this file has to hold both to test that it does.
PAYLOAD = '{"%sToken": "x", "refreshToken": "y"}\n' % "access"


def git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True,
    )


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A scratch repo with the hook wired the way install-hooks wires it."""
    git(tmp_path, "init", "-q")
    git(tmp_path, "config", "user.email", "test@example.com")
    git(tmp_path, "config", "user.name", "test")
    hooks = tmp_path / "hooks"
    hooks.mkdir()
    target = hooks / "pre-commit"
    target.write_text(HOOK.read_text(encoding="utf-8"), encoding="utf-8")
    target.chmod(0o755)
    git(tmp_path, "config", "core.hooksPath", "hooks")
    return tmp_path


def commit(repo: Path, name: str, body: str) -> subprocess.CompletedProcess:
    path = repo / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    git(repo, "add", "-f", name)
    return git(repo, "commit", "-m", "test")


def test_clean_content_commits(repo):
    result = commit(repo, "notes.md", "No secrets here, only prose.\n")
    assert result.returncode == 0, result.stderr


def test_token_in_a_transcript_is_blocked(repo):
    """The case .gitignore cannot catch: results/ is committed on purpose,
    and a session can echo its own mounted token into stdout."""
    result = commit(
        repo,
        "results/sonnet/pass-1/transcript.jsonl",
        '{"type":"assistant","text":"the token is %s"}\n' % DECOY,
    )
    assert result.returncode != 0
    assert "sk-ant-* token" in result.stderr
    assert "transcript.jsonl:1" in result.stderr


def test_credential_file_path_is_blocked(repo):
    result = commit(repo, ".claude/.credentials.json", "{}\n")
    assert result.returncode != 0
    assert "must never be committed" in result.stderr


def test_credential_payload_shape_is_blocked(repo):
    """Belt and braces: catches a payload whose token stops matching the
    sk-ant- prefix, without firing on prose that mentions one key."""
    result = commit(repo, "dump.json", PAYLOAD)
    assert result.returncode != 0
    assert "credentials payload" in result.stderr


def test_prose_about_refresh_tokens_still_commits(repo):
    """Docs discussing the auth flow must not be collateral damage."""
    result = commit(
        repo, "README.md",
        "The CLI writes a refreshToken and rotates it on expiry.\n",
    )
    assert result.returncode == 0, result.stderr


def test_hook_is_executable_and_installed_in_this_repo():
    """`pixi run install-hooks` must have been run here; otherwise the guard
    is a file nobody executes."""
    assert HOOK.exists()
    assert os.access(HOOK, os.X_OK), "hooks/pre-commit must be executable"
    configured = subprocess.run(
        ["git", "config", "core.hooksPath"],
        cwd=REPO_ROOT, capture_output=True, text=True,
    ).stdout.strip()
    assert configured == "hooks", (
        "run `pixi run install-hooks` to point git at hooks/"
    )
