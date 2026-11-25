"""微博的 Runner 配置。

Iris 不从环境变量读微博令牌，而是从 OAuth 授权文件（WEIBO_CREDENTIALS_FILE）读：启动时读一次，
令牌快过期时才刷新。所以让 Iris 连上一局，就是写一个带本局令牌、过期时间很远的授权文件，再注入
WEIBO_MCP_URL 和文件路径；Iris 不会走授权和刷新，Iris 代码不用改。

直接模式和手动联调用的准备命令在 runner.domains.weibo_cli。
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from sandbox.domains.registry import manifest_for
from sandbox.storage import write_atomic

FAR_FUTURE = 4102444800    # 2100-01-01：保证 Iris 不会去刷新令牌
CANARY_TOOL = "search_statuses_limited"     # Iris 固定认定的只读工具，探测不会触发审批
CANARY_PROMPT = (
    "这是微博集成测试。请委派给微博助手，实际调用 search_statuses_limited 搜索“深圳降温”，"
    "告诉我搜到了几条微博。只查询，不要发布、转发、评论、点赞或删除任何内容。"
)
RECOVERY_PROMPT = (
    "请实际调用 statuses_user_timeline_biz 查询我最近发布的微博，核实刚才的操作是否已经生效。"
    "只核查，禁止重新发布或重复操作。若查到，请回复对应微博的内容和发布时间；若查不到，请说明当前查不到，无法确认是否成功。"
)


def credentials(token: str, sandbox_url: str) -> dict:
    """Iris OAuthTokenService 读取的 5 个字段。token_endpoint 只是占位，过期时间足够远时不会被访问。"""
    return {
        "access_token": token,
        "refresh_token": "",
        "expires_at": FAR_FUTURE,
        "client_id": "sandbox",
        "token_endpoint": f"{sandbox_url.rstrip('/')}/oauth/token",
    }


def write_credentials(path: Path, token: str, sandbox_url: str) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    write_atomic(path, (json.dumps(credentials(token, sandbox_url), indent=2) + "\n",))
    return path


def read_token(path: Path) -> str | None:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data.get("access_token") or None


class Weibo:
    name = "weibo"
    read_tools = manifest_for("weibo").read_tools
    canary_tool = CANARY_TOOL
    canary_prompt = CANARY_PROMPT
    recovery_prompt = RECOVERY_PROMPT
    token_hint = "WEIBO_CREDENTIALS_FILE（先运行 python -m runner.domains.weibo_cli 生成）"

    def connect_iris(self, sandbox_url: str, episode_id: str, token: str, workdir: Path,
                     *, scoped: bool = False) -> dict[str, str]:
        mcp = f"/mcp/{episode_id}/weibo/1" if scoped else f"/mcp/{episode_id}/1"
        path = write_credentials(Path(workdir) / ".weibo" / "auth.json", token, sandbox_url)
        return {"WEIBO_MCP_URL": f"{sandbox_url}{mcp}", "WEIBO_CREDENTIALS_FILE": str(path)}

    def disconnect_iris(self, workdir: Path) -> dict[str, str]:
        """授权文件指向一个不存在的路径：Iris 读不到令牌，微博助手不会发出任何请求。"""
        from runner.domains import DISABLED_URL
        return {"WEIBO_MCP_URL": f"{DISABLED_URL}/weibo",
                "WEIBO_CREDENTIALS_FILE": str(Path(workdir) / ".weibo" / "disabled" / "auth.json")}

    def direct_token(self) -> str | None:
        path = os.environ.get("WEIBO_CREDENTIALS_FILE")
        return read_token(Path(path)) if path else None
