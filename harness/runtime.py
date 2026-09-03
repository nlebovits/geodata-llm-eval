"""Inspect the container that will execute a paid benchmark trial."""

from __future__ import annotations

import json
import re
import subprocess
from typing import Any

import agents


class RuntimeDrift(RuntimeError):
    """The local image does not match the version the harness claims."""


def _run(command: list[str]) -> str:
    try:
        return subprocess.run(
            command, capture_output=True, text=True, check=True
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError) as exc:
        raise RuntimeDrift(
            f"runtime preflight failed: {' '.join(command)}: {exc}"
        ) from exc


def _version(text: str) -> str:
    match = re.search(r"\d+\.\d+\.\d+", text)
    if not match:
        raise RuntimeDrift(f"could not read a semantic version from {text!r}")
    return match.group(0)


def inspect(image: str, adapter: agents.AgentAdapter) -> dict[str, Any]:
    """Return an immutable runtime receipt, failing before a model call on drift."""
    raw = _run(["docker", "image", "inspect", image])
    image_data = json.loads(raw)[0]
    cli_output = _run(
        ["docker", "run", "--rm", "--entrypoint", adapter.name, image, "--version"]
    )
    duckdb_output = _run(
        ["docker", "run", "--rm", "--entrypoint", "duckdb", image, "--version"]
    )
    cli_version = _version(cli_output)
    duckdb_version = _version(duckdb_output)
    mismatches = []
    if cli_version != adapter.expected_cli_version:
        mismatches.append(
            f"{adapter.scaffold} {cli_version} != {adapter.expected_cli_version}"
        )
    if duckdb_version != agents.DUCKDB_VERSION:
        mismatches.append(f"DuckDB {duckdb_version} != {agents.DUCKDB_VERSION}")
    if mismatches:
        raise RuntimeDrift("runtime pin mismatch: " + "; ".join(mismatches))
    return {
        "image": image,
        "image_id": image_data.get("Id"),
        "image_repo_digests": image_data.get("RepoDigests") or [],
        "cli": adapter.scaffold,
        "cli_version": cli_version,
        "cli_version_output": cli_output,
        "duckdb_version": duckdb_version,
        "duckdb_version_output": duckdb_output,
    }
