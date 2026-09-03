"""Native coding-agent adapters for the benchmark runner.

The runner owns the experiment: workspace assembly, budgets, retries, result
layout, and grading.  An adapter owns only the parts that differ between
agent scaffolds: credentials, command construction, and interpretation of the
native JSONL trajectory.  Native records are never rewritten, so a newer
parser can always recover more information from an old run.
"""

from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

Record = dict[str, Any]

CLAUDE_CODE_VERSION = "2.1.218"
CODEX_CLI_VERSION = "0.153.0"
DUCKDB_VERSION = "1.5.5"

CONTAINER_CLAUDE_DIR = "/home/runner/.claude"
CONTAINER_CODEX_DIR = "/home/runner/.codex"


@dataclass(frozen=True)
class TaskBundle:
    """The complete agent-visible task, shared unchanged by every adapter."""

    workspace: Path
    prompt: str


@dataclass(frozen=True)
class TranscriptFacts:
    """Agent-independent facts recoverable from one native trajectory."""

    session_id: str | None
    turns: int
    input_tokens: int
    output_tokens: int
    cache_creation_tokens: int
    cache_read_tokens: int
    reasoning_output_tokens: int
    model_id: str | None
    cli_version: str | None
    tools: tuple[str, ...] | None
    permission_mode: str | None
    authentication_rejected: bool

    def usage(self) -> dict[str, int]:
        return {
            "turns": self.turns,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cache_creation_tokens": self.cache_creation_tokens,
            "cache_read_tokens": self.cache_read_tokens,
            "reasoning_output_tokens": self.reasoning_output_tokens,
        }


class AgentAdapter(Protocol):
    """The scaffold-specific half of a benchmark trial."""

    name: str
    scaffold: str
    expected_cli_version: str
    transcript_format: str
    auth_method: str
    model_id: str
    reasoning_effort: str | None

    def auth_args(self, session_home: Path) -> list[str]: ...

    def command(
        self,
        image: str,
        task: TaskBundle,
        session_home: Path,
        container: str,
        resume_session: str | None = None,
    ) -> list[str]: ...

    def facts(self, records: list[Record]) -> TranscriptFacts: ...

    def progress(self, records: list[Record]) -> tuple[int, str]: ...

    def config(self) -> dict[str, Any]: ...


def _docker_prefix(
    image: str,
    workspace: Path,
    container: str,
    entrypoint: str,
    auth_args: list[str],
) -> list[str]:
    return [
        "docker",
        "run",
        "--rm",
        "--name",
        container,
        "--user",
        f"{os.getuid()}:{os.getgid()}",
        "-v",
        f"{workspace}:/workspace",
        *auth_args,
        "--entrypoint",
        entrypoint,
        image,
    ]


def _copy_secret(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.parent.chmod(0o700)
    if not destination.exists():
        shutil.copy(source, destination)
    destination.chmod(0o600)


class ClaudeAdapter:
    name = "claude"
    scaffold = "claude-code"
    expected_cli_version = CLAUDE_CODE_VERSION
    transcript_format = "claude-stream-json"

    def __init__(
        self,
        model_id: str,
        credentials: Path,
        reasoning_effort: str | None = None,
    ) -> None:
        self.model_id = model_id
        self.credentials = credentials
        self.reasoning_effort = reasoning_effort
        self.auth_method = "login" if credentials.exists() else "api-key"

    def auth_args(self, session_home: Path) -> list[str]:
        if self.credentials.exists():
            claude_dir = session_home / ".claude"
            _copy_secret(self.credentials, claude_dir / ".credentials.json")
            return ["-v", f"{claude_dir}:{CONTAINER_CLAUDE_DIR}"]
        if os.environ.get("ANTHROPIC_API_KEY"):
            return ["-e", "ANTHROPIC_API_KEY"]
        raise SystemExit(
            f"No credentials found. Run `claude login` on the host (writes "
            f"{self.credentials}) or set ANTHROPIC_API_KEY."
        )

    def command(
        self,
        image: str,
        task: TaskBundle,
        session_home: Path,
        container: str,
        resume_session: str | None = None,
    ) -> list[str]:
        resume = ["--resume", resume_session] if resume_session else []
        effort = ["--effort", self.reasoning_effort] if self.reasoning_effort else []
        return [
            *_docker_prefix(
                image,
                task.workspace,
                container,
                "claude",
                self.auth_args(session_home),
            ),
            "-p",
            task.prompt,
            *resume,
            "--model",
            self.model_id,
            *effort,
            "--output-format",
            "stream-json",
            "--verbose",
            "--dangerously-skip-permissions",
        ]

    def facts(self, records: list[Record]) -> TranscriptFacts:
        totals = {
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_creation_tokens": 0,
            "cache_read_tokens": 0,
        }
        turns = 0
        found_session = None
        model_id = None
        cli_version = None
        tools = None
        permission_mode = None
        rejected = False
        for record in records:
            value = record.get("session_id")
            if isinstance(value, str) and value:
                found_session = value
            if record.get("type") == "assistant":
                turns += 1
                message = record.get("message") or {}
                if isinstance(message.get("model"), str):
                    model_id = message["model"]
            if record.get("type") == "system" and record.get("subtype") == "init":
                if isinstance(record.get("model"), str):
                    model_id = record["model"]
                if isinstance(record.get("claude_code_version"), str):
                    cli_version = record["claude_code_version"]
                advertised = record.get("tools")
                if isinstance(advertised, list) and all(
                    isinstance(tool, str) for tool in advertised
                ):
                    tools = tuple(advertised)
                if isinstance(record.get("permissionMode"), str):
                    permission_mode = record["permissionMode"]
            if record.get("type") == "result":
                usage = record.get("usage") or {}
                totals["input_tokens"] += int(usage.get("input_tokens") or 0)
                totals["output_tokens"] += int(usage.get("output_tokens") or 0)
                totals["cache_creation_tokens"] += int(
                    usage.get("cache_creation_input_tokens") or 0
                )
                totals["cache_read_tokens"] += int(
                    usage.get("cache_read_input_tokens") or 0
                )
            if (
                record.get("subtype") == "api_retry"
                and record.get("error_status") == 401
            ):
                rejected = True
        return TranscriptFacts(
            session_id=found_session,
            turns=turns,
            reasoning_output_tokens=0,
            model_id=model_id,
            cli_version=cli_version,
            tools=tools,
            permission_mode=permission_mode,
            authentication_rejected=rejected,
            **totals,
        )

    def progress(self, records: list[Record]) -> tuple[int, str]:
        turns = 0
        last_tool = "-"
        for record in records:
            if record.get("type") != "assistant":
                continue
            turns += 1
            for block in (record.get("message") or {}).get("content") or []:
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    last_tool = str(block.get("name", "-"))
        return turns, last_tool

    def config(self) -> dict[str, Any]:
        return {
            "adapter": self.name,
            "mode": "native",
            "scaffold": self.scaffold,
            "model_id_requested": self.model_id,
            "reasoning_effort_requested": self.reasoning_effort,
            "auth_method": self.auth_method,
            "tool_policy": "native",
            "permission_policy": "bypassPermissions",
            "outer_sandbox": "docker",
            "network_policy": "unrestricted",
        }


class CodexAdapter:
    name = "codex"
    scaffold = "codex-cli"
    expected_cli_version = CODEX_CLI_VERSION
    transcript_format = "codex-events-jsonl"

    def __init__(
        self,
        model_id: str,
        credentials: Path,
        auth_method: str,
        reasoning_effort: str | None = None,
    ) -> None:
        if auth_method not in {"login", "api-key"}:
            raise ValueError("Codex auth must be 'login' or 'api-key'")
        self.model_id = model_id
        self.credentials = credentials
        self.auth_method = auth_method
        self.reasoning_effort = reasoning_effort

    def auth_args(self, session_home: Path) -> list[str]:
        if self.auth_method == "login":
            if not self.credentials.exists():
                raise SystemExit(
                    f"No Codex login found at {self.credentials}. Run `codex login` "
                    "or choose --auth api-key."
                )
            codex_dir = session_home / ".codex"
            _copy_secret(self.credentials, codex_dir / "auth.json")
            return ["-v", f"{codex_dir}:{CONTAINER_CODEX_DIR}"]
        if not os.environ.get("CODEX_API_KEY"):
            raise SystemExit("--auth api-key requires CODEX_API_KEY")
        return ["-e", "CODEX_API_KEY"]

    def _options(self) -> list[str]:
        options = [
            "--json",
            "--model",
            self.model_id,
            "--skip-git-repo-check",
            "--ignore-user-config",
            "--ignore-rules",
            "--dangerously-bypass-approvals-and-sandbox",
        ]
        if self.reasoning_effort:
            options += ["--config", f'model_reasoning_effort="{self.reasoning_effort}"']
        return options

    def command(
        self,
        image: str,
        task: TaskBundle,
        session_home: Path,
        container: str,
        resume_session: str | None = None,
    ) -> list[str]:
        prefix = _docker_prefix(
            image, task.workspace, container, "codex", self.auth_args(session_home)
        )
        if resume_session:
            return [
                *prefix,
                "exec",
                "resume",
                *self._options(),
                resume_session,
                task.prompt,
            ]
        return [*prefix, "exec", *self._options(), task.prompt]

    def facts(self, records: list[Record]) -> TranscriptFacts:
        totals = {
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_creation_tokens": 0,
            "cache_read_tokens": 0,
            "reasoning_output_tokens": 0,
        }
        found_session = None
        turns = 0
        model_id = None
        rejected = False
        for record in records:
            if record.get("type") == "thread.started":
                value = record.get("thread_id")
                if isinstance(value, str) and value:
                    found_session = value
            if record.get("type") == "turn.completed":
                turns += 1
                usage = record.get("usage") or {}
                totals["input_tokens"] += int(usage.get("input_tokens") or 0)
                totals["output_tokens"] += int(usage.get("output_tokens") or 0)
                totals["cache_read_tokens"] += int(
                    usage.get("cached_input_tokens") or 0
                )
                totals["reasoning_output_tokens"] += int(
                    usage.get("reasoning_output_tokens") or 0
                )
            item = record.get("item") or {}
            candidate = record.get("model") or item.get("model")
            if isinstance(candidate, str) and candidate:
                model_id = candidate
            if record.get("type") == "error":
                error_text = json.dumps(record, sort_keys=True).lower()
                if "401" in error_text or "unauthorized" in error_text:
                    rejected = True
        return TranscriptFacts(
            session_id=found_session,
            turns=turns,
            model_id=model_id,
            cli_version=None,
            tools=None,
            permission_mode=None,
            authentication_rejected=rejected,
            **totals,
        )

    def progress(self, records: list[Record]) -> tuple[int, str]:
        turns = 0
        last_tool = "-"
        for record in records:
            item = record.get("item") or {}
            if record.get("type") == "turn.completed":
                turns += 1
            if record.get("type") == "item.started":
                item_type = item.get("type")
                if isinstance(item_type, str) and item_type != "agent_message":
                    last_tool = item_type
        return turns, last_tool

    def config(self) -> dict[str, Any]:
        return {
            "adapter": self.name,
            "mode": "native",
            "scaffold": self.scaffold,
            "model_id_requested": self.model_id,
            "reasoning_effort_requested": self.reasoning_effort,
            "auth_method": self.auth_method,
            "tool_policy": "native",
            "permission_policy": "bypass-approvals-and-sandbox",
            "outer_sandbox": "docker",
            "network_policy": "unrestricted",
        }
