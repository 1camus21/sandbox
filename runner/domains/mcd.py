"""麦乐送的 Runner 配置：沿用 runner.contract 的话术，连接方式与改造前相同。"""
from __future__ import annotations

import os
from pathlib import Path

from runner.contract import CANARY_PROMPT, CANARY_TOOL, RECOVERY_PROMPT
from sandbox.domains.registry import manifest_for


class McDelivery:
    name = "mcd"
    read_tools = manifest_for("mcd").read_tools
    canary_tool = CANARY_TOOL
    canary_prompt = CANARY_PROMPT
    recovery_prompt = RECOVERY_PROMPT
    token_hint = "MCD_MCP_TOKEN"

    def connect_iris(self, sandbox_url: str, episode_id: str, token: str, workdir: Path,
                     *, scoped: bool = False) -> dict[str, str]:
        path = f"/mcp/{episode_id}/mcd/1" if scoped else f"/mcp/{episode_id}/1"
        return {"MCD_MCP_URL": f"{sandbox_url}{path}", "MCD_MCP_TOKEN": token}

    def disconnect_iris(self, workdir: Path) -> dict[str, str]:
        from runner.domains import DISABLED_URL
        return {"MCD_MCP_URL": f"{DISABLED_URL}/mcd", "MCD_MCP_TOKEN": "disabled"}

    def direct_token(self) -> str | None:
        return os.environ.get("MCD_MCP_TOKEN") or None
