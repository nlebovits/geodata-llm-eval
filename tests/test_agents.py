"""Common adapter commands, native trajectory parsing, and runtime receipts."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "harness"))

import agents
import layout
import run
import runtime


def test_codex_login_command_is_native_and_resumable(tmp_path: Path) -> None:
    auth = tmp_path / "auth.json"
    auth.write_text('{"tokens":"decoy"}', encoding="utf-8")
    home = tmp_path / "home"
    workspace = tmp_path / "workspace"
    home.mkdir()
    workspace.mkdir()
    adapter = agents.CodexAdapter("gpt-test", auth, "login", "high")

    first = adapter.command("image", agents.TaskBundle(workspace, "do it"), home, "c1")
    resumed = adapter.command(
        "image", agents.TaskBundle(workspace, "finish"), home, "c2", "thread-1"
    )

    assert first[first.index("--entrypoint") + 1] == "codex"
    assert first[first.index("--model") + 1] == "gpt-test"
    assert "--json" in first
    assert "--dangerously-bypass-approvals-and-sandbox" in first
    assert 'model_reasoning_effort="high"' in first
    assert resumed[resumed.index("exec") + 1] == "resume"
    assert "thread-1" in resumed
    copied = home / ".codex" / "auth.json"
    assert copied.exists() and copied.stat().st_mode & 0o077 == 0
    assert str(auth) not in " ".join(first)


def test_codex_api_key_is_passed_by_name_not_value(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("CODEX_API_KEY", "secret-decoy")
    adapter = agents.CodexAdapter("gpt-test", tmp_path / "missing.json", "api-key")

    args = adapter.auth_args(tmp_path / "home")

    assert args == ["-e", "CODEX_API_KEY"]
    assert "secret-decoy" not in " ".join(args)


def test_both_adapters_receive_the_same_agent_visible_bundle(tmp_path: Path) -> None:
    claude_workspace = tmp_path / "claude-workspace"
    codex_workspace = tmp_path / "codex-workspace"
    claude_workspace.mkdir()
    codex_workspace.mkdir()
    run.assemble_workspace(claude_workspace, "csv")
    run.assemble_workspace(codex_workspace, "csv")

    def snapshot(root: Path) -> dict[str, bytes]:
        return {
            str(path.relative_to(root)): path.read_bytes()
            for path in root.rglob("*")
            if path.is_file()
        }

    visible = snapshot(claude_workspace)
    assert visible == snapshot(codex_workspace)
    assert visible
    assert not any(
        "golden" in path or "ablation" in path or path.startswith(".git/")
        for path in visible
    )


def test_codex_dry_run_is_a_no_cost_smoke_test(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    credentials = tmp_path / "auth.json"
    credentials.write_text('{"tokens":"decoy"}', encoding="utf-8")
    monkeypatch.setattr(run, "CODEX_CREDENTIALS", credentials)

    run.run_session(
        "gpt-test", dry_run=True, agent="codex", auth="login", input_mode="csv"
    )

    output = capsys.readouterr().out
    assert "--entrypoint codex" in output
    assert "SPEC.md" in output
    assert "fixtures/golden" not in output
    assert '"mode": "native"' in output


def test_codex_facts_sum_resumed_usage_and_keep_unknown_events() -> None:
    adapter = agents.CodexAdapter("gpt-test", Path("missing"), "api-key")
    records: list[dict[str, Any]] = [
        {"type": "thread.started", "thread_id": "thread-1"},
        {"type": "future.event", "new_field": True},
        {
            "type": "item.completed",
            "item": {"type": "agent_message", "text": "one"},
        },
        {
            "type": "turn.completed",
            "usage": {
                "input_tokens": 10,
                "cached_input_tokens": 4,
                "output_tokens": 3,
                "reasoning_output_tokens": 2,
            },
        },
        {
            "type": "turn.completed",
            "usage": {"input_tokens": 5, "output_tokens": 1},
        },
    ]

    facts = adapter.facts(records)

    assert facts.session_id == "thread-1"
    assert facts.turns == 2
    assert facts.input_tokens == 15
    assert facts.cache_read_tokens == 4
    assert facts.output_tokens == 4
    assert facts.reasoning_output_tokens == 2


def test_claude_facts_capture_reported_scaffold_snapshot() -> None:
    adapter = agents.ClaudeAdapter("claude-test", Path("missing"))
    facts = adapter.facts(
        [
            {
                "type": "system",
                "subtype": "init",
                "session_id": "session-1",
                "model": "claude-snapshot",
                "claude_code_version": agents.CLAUDE_CODE_VERSION,
                "tools": ["Bash", "Read"],
                "permissionMode": "bypassPermissions",
            },
            {
                "type": "result",
                "usage": {"input_tokens": 3, "output_tokens": 2},
            },
        ]
    )

    assert facts.model_id == "claude-snapshot"
    assert facts.cli_version == agents.CLAUDE_CODE_VERSION
    assert facts.tools == ("Bash", "Read")
    assert facts.permission_mode == "bypassPermissions"


def _completed(command: list[str], stdout: str) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(command, 0, stdout=stdout, stderr="")


def test_runtime_receipt_records_exact_image_and_versions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_run(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        if command[1:3] == ["image", "inspect"]:
            return _completed(
                command,
                json.dumps([{"Id": "sha256:abc", "RepoDigests": ["image@sha256:def"]}]),
            )
        if "duckdb" in command:
            return _completed(command, f"v{agents.DUCKDB_VERSION} build")
        return _completed(command, f"codex-cli {agents.CODEX_CLI_VERSION}")

    monkeypatch.setattr(runtime.subprocess, "run", fake_run)
    adapter = agents.CodexAdapter("gpt-test", Path("missing"), "api-key")

    receipt = runtime.inspect("image", adapter)

    assert receipt["image_id"] == "sha256:abc"
    assert receipt["cli_version"] == agents.CODEX_CLI_VERSION
    assert receipt["duckdb_version"] == agents.DUCKDB_VERSION


def test_runtime_drift_fails_before_an_agent_invocation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_run(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        if command[1:3] == ["image", "inspect"]:
            return _completed(command, json.dumps([{"Id": "sha256:abc"}]))
        if "duckdb" in command:
            return _completed(command, f"v{agents.DUCKDB_VERSION}")
        return _completed(command, "codex-cli 0.0.1")

    monkeypatch.setattr(runtime.subprocess, "run", fake_run)
    adapter = agents.CodexAdapter("gpt-test", Path("missing"), "api-key")

    with pytest.raises(runtime.RuntimeDrift, match="runtime pin mismatch"):
        runtime.inspect("image", adapter)


def test_full_agent_configuration_changes_the_experiment_fingerprint() -> None:
    base = {"adapter": "codex", "model": "gpt-test", "reasoning": "high"}
    changed = {**base, "reasoning": "xhigh"}

    assert run.agent_config_fingerprint(base) == run.agent_config_fingerprint(
        dict(reversed(list(base.items())))
    )
    assert run.agent_config_fingerprint(base) != run.agent_config_fingerprint(changed)


def test_report_fingerprint_separates_agent_configurations() -> None:
    base: dict[str, Any] = {
        "model": "gpt-test",
        "model_id": "gpt-test",
        "agent": "codex",
        "agent_config_fingerprint": "config-one",
    }

    assert layout.fingerprint_of(base) != layout.fingerprint_of(
        {**base, "agent_config_fingerprint": "config-two"}
    )


@pytest.mark.parametrize("effort", ["max", "ultra"])
def test_codex_accepts_reasoning_efforts_advertised_for_sol(
    effort: str,
) -> None:
    adapter = run.make_adapter("codex", "gpt-5.6-sol", effort, "login")

    assert adapter.reasoning_effort == effort


def test_codex_rejects_reasoning_effort_not_advertised_for_sol() -> None:
    with pytest.raises(ValueError, match="does not support reasoning effort"):
        run.make_adapter("codex", "gpt-5.6-sol", "minimal", "login")


def test_dockerfile_pins_both_native_agent_clis() -> None:
    dockerfile = (REPO_ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert f"ARG CLAUDE_CODE_VERSION={agents.CLAUDE_CODE_VERSION}" in dockerfile
    assert f"ARG CODEX_CLI_VERSION={agents.CODEX_CLI_VERSION}" in dockerfile
    assert "@openai/codex@${CODEX_CLI_VERSION}" in dockerfile


def test_the_claude_container_raises_the_bash_timeout_past_the_default(
    tmp_path: Path,
) -> None:
    """A remote scan of the 8.45M-row CAR parquet outlives Claude Code's 120s
    Bash default. The 2026-09-18 Opus run hit that cap, gave up on remote
    reads, and corrupted a 3 GB download in the background instead."""
    credentials = tmp_path / "creds.json"
    credentials.write_text('{"token":"decoy"}', encoding="utf-8")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    adapter = agents.ClaudeAdapter("claude-test", credentials)

    cmd = adapter.command(
        "image", agents.TaskBundle(workspace, "do it"), tmp_path / "home", "c1"
    )

    default = f"BASH_DEFAULT_TIMEOUT_MS={agents.BASH_DEFAULT_TIMEOUT_MS}"
    ceiling = f"BASH_MAX_TIMEOUT_MS={agents.BASH_MAX_TIMEOUT_MS}"
    assert default in cmd
    assert ceiling in cmd
    assert agents.BASH_DEFAULT_TIMEOUT_MS > 120_000
    assert agents.BASH_MAX_TIMEOUT_MS >= agents.BASH_DEFAULT_TIMEOUT_MS
    # Every -e carries an explicit assignment, so the value is pinned by the
    # harness rather than inherited from whatever shell launched it.
    assert cmd[cmd.index(default) - 1] == "-e"
    assert cmd.index("--entrypoint") > cmd.index(default)


def test_the_tool_timeout_is_recorded_in_the_agent_config(tmp_path: Path) -> None:
    """A cap that reshapes strategy belongs in the fingerprint. Raising it
    should split new runs from old ones rather than pool them."""
    credentials = tmp_path / "creds.json"
    credentials.write_text('{"token":"decoy"}', encoding="utf-8")
    claude = agents.ClaudeAdapter("claude-test", credentials).config()
    codex = agents.CodexAdapter("gpt-test", credentials, "login").config()

    assert claude["bash_default_timeout_ms"] == agents.BASH_DEFAULT_TIMEOUT_MS
    assert claude["bash_max_timeout_ms"] == agents.BASH_MAX_TIMEOUT_MS
    # Codex exposes no equivalent: the model sets timeout_ms per shell call.
    # See openai/codex#4775.
    assert codex["bash_default_timeout_ms"] is None
    assert codex["bash_max_timeout_ms"] is None
    assert run.agent_config_fingerprint(claude) != run.agent_config_fingerprint(codex)


def test_codex_carries_no_claude_only_environment(tmp_path: Path) -> None:
    auth = tmp_path / "auth.json"
    auth.write_text('{"tokens":"decoy"}', encoding="utf-8")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    adapter = agents.CodexAdapter("gpt-test", auth, "login")

    cmd = adapter.command(
        "image", agents.TaskBundle(workspace, "do it"), tmp_path / "home", "c1"
    )

    assert not [arg for arg in cmd if arg.startswith("BASH_")]


def test_the_image_carries_ps() -> None:
    """Without ps a session cannot tell a finished background download from a
    stalled one. Guessing wrong once cost a 3 GB file and a whole run."""
    dockerfile = (REPO_ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert "procps" in dockerfile


EXPIRED_OAUTH = (
    "Failed to authenticate: OAuth session expired and could not be refreshed"
)


def claude_rejected(records: list[dict[str, Any]]) -> bool:
    """Whether the Claude adapter reads these records as a dead credential."""
    adapter = agents.ClaudeAdapter("claude-test", Path("missing"))
    return adapter.facts(records).authentication_rejected


def test_an_expired_token_the_cli_never_sent_is_a_dead_credential() -> None:
    """The 2026-09-18 Sonnet sweep died here. The mounted token had expired, so
    the CLI refused before its first API call and wrote no `api_retry` record
    at all. A 401-only check saw nothing, the resume loop spent all three
    attempts re-proving it, and the trial landed as `agent_produced_nothing`
    instead of invalid -- a stale token counted against the model's score.

    Note `subtype` on this record: `success`, while `is_error` is true and
    `api_error_status` is null. No status field names the failure.
    """
    assert claude_rejected(
        [
            {"type": "system", "subtype": "init", "session_id": "s"},
            {
                "type": "result",
                "subtype": "success",
                "is_error": True,
                "terminal_reason": "api_error",
                "api_error_status": None,
                "result": EXPIRED_OAUTH,
            },
        ]
    )


def test_the_synthetic_turn_alone_is_enough() -> None:
    """The refusal lands twice, and the assistant turn comes first. A session
    killed between the two still has to read as a dead credential."""
    assert claude_rejected(
        [
            {"type": "system", "subtype": "init", "session_id": "s"},
            {
                "type": "assistant",
                "message": {
                    "model": "<synthetic>",
                    "content": [{"type": "text", "text": EXPIRED_OAUTH}],
                },
            },
        ]
    )


def test_a_401_the_cli_retried_is_still_a_dead_credential() -> None:
    """The older shape. The token was live when mounted and expired mid-sweep,
    so the API answered 401 and the CLI logged one record per retry."""
    assert claude_rejected(
        [
            {"type": "system", "subtype": "init", "session_id": "s"},
            {
                "type": "system",
                "subtype": "api_retry",
                "attempt": 1,
                "error_status": 401,
                "error": "authentication_failed",
            },
            {"type": "result", "is_error": True},
        ]
    )


def test_the_check_reads_every_attempt_not_just_the_last() -> None:
    """A later attempt can fail before reaching the API and log nothing
    diagnostic. Scoping the check to the newest attempt would miss the
    rejection that the earlier one recorded."""
    assert claude_rejected(
        [
            {"type": "system", "subtype": "init", "session_id": "s"},
            {"type": "system", "subtype": "api_retry", "error_status": 401},
            {"type": "result", "is_error": True},
            {"type": "system", "subtype": "init", "session_id": "s"},
            {"type": "assistant", "message": {"content": []}},
            {"type": "result", "is_error": True},
        ]
    )


def test_an_overloaded_api_is_not_a_dead_credential() -> None:
    """529 is infrastructure. Invalidating the trial as an auth failure would
    hide a real outage behind a credential warning."""
    assert not claude_rejected(
        [
            {"type": "system", "subtype": "init", "session_id": "s"},
            {
                "type": "system",
                "subtype": "api_retry",
                "attempt": 1,
                "error_status": 529,
                "error": "overloaded",
            },
            {"type": "result", "is_error": False},
        ]
    )


def test_an_error_result_that_is_not_about_authentication_is_not_one() -> None:
    """Every agent failure ends in an error result. Only the text separates a
    dead credential from a session that ran and lost."""
    assert not claude_rejected(
        [
            {"type": "system", "subtype": "init", "session_id": "s"},
            {
                "type": "result",
                "subtype": "success",
                "is_error": True,
                "terminal_reason": "api_error",
                "result": "API Error: 500 Internal Server Error",
            },
        ]
    )
