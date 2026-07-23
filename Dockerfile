# Session image: pinned Claude Code CLI + DuckDB, no user config.
# Each benchmark session runs in a fresh container from this image so
# nothing from the host (global CLAUDE.md, hooks, MCP servers, memory)
# can leak into a run.

FROM node:22-bookworm-slim

ARG CLAUDE_CODE_VERSION=2.1.218
ARG DUCKDB_VERSION=1.3.2

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       ca-certificates curl unzip python3 python3-pip git \
    && rm -rf /var/lib/apt/lists/*

# Pinned DuckDB CLI
RUN curl -fsSL -o /tmp/duckdb.zip \
      "https://github.com/duckdb/duckdb/releases/download/v${DUCKDB_VERSION}/duckdb_cli-linux-amd64.zip" \
    && unzip /tmp/duckdb.zip -d /usr/local/bin \
    && rm /tmp/duckdb.zip \
    && duckdb --version

# Pinned Claude Code CLI
RUN npm install -g @anthropic-ai/claude-code@${CLAUDE_CODE_VERSION} \
    && claude --version

# Non-root user with a clean HOME: no CLAUDE.md, no hooks, no MCP config
RUN useradd -m -s /bin/bash runner
USER runner
WORKDIR /workspace

# Pre-install the DuckDB spatial + httpfs extensions so sessions don't
# depend on extension-install network flakiness at run time
RUN duckdb -c "INSTALL spatial; INSTALL httpfs;"

ENTRYPOINT ["claude"]
