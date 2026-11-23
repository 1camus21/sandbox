"""微博的 Runner 配置。

Iris 不从环境变量读微博令牌，而是从 OAuth 授权文件（WEIBO_CREDENTIALS_FILE）读：启动时读一次，
令牌快过期时才刷新。所以让 Iris 连上一局，就是写一个带本局令牌、过期时间很远的授权文件，再注入
WEIBO_MCP_URL 和文件路径；Iris 不会走授权和刷新，Iris 代码不用改。

直接模式和手动联调先运行本模块准备授权文件：

    python -m runner.domains.weibo --credentials D:/Iris/.weibo-sandbox/auth.json

它生成本局的编号和令牌、写好授权文件，并打印 Iris 和 Runner 需要的设置。加 --open 会同时在沙箱里
开局，用于不经过 Runner、直接和 Iris 对话的手动试用。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from secrets import token_hex

from sandbox.domains.registry import manifest_for
from sandbox.storage import write_atomic

FAR_FUTURE = 4102444800    # 2100-01-01：保证 Iris 不会去刷新令牌
DEFAULT_SANDBOX = "http://127.0.0.1:8100"
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
    token_hint = "WEIBO_CREDENTIALS_FILE（先运行 python -m runner.domains.weibo 生成）"

    def connect_iris(self, sandbox_url: str, episode_id: str, token: str, workdir: Path,
                     *, scoped: bool = False) -> dict[str, str]:
        mcp = f"/mcp/{episode_id}/weibo/1" if scoped else f"/mcp/{episode_id}/1"
        path = write_credentials(Path(workdir) / ".weibo" / "auth.json", token, sandbox_url)
        return {"WEIBO_MCP_URL": f"{sandbox_url}{mcp}", "WEIBO_CREDENTIALS_FILE": str(path)}

    def direct_token(self) -> str | None:
        path = os.environ.get("WEIBO_CREDENTIALS_FILE")
        return read_token(Path(path)) if path else None


def open_episode(sandbox_url: str, admin_token: str, body: dict) -> dict:
    request = urllib.request.Request(
        f"{sandbox_url.rstrip('/')}/admin/episodes", data=json.dumps(body).encode("utf-8"), method="POST",
        headers={"Authorization": f"Bearer {admin_token}", "Content-Type": "application/json"},
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))   # 本机地址不走系统代理
    with opener.open(request, timeout=15) as response:
        return json.loads(response.read().decode("utf-8"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="为直接模式或手动联调准备微博授权文件，可选同时开局。")
    parser.add_argument("--credentials", default=os.environ.get("WEIBO_CREDENTIALS_FILE"),
                        help="授权文件路径，与 Iris 的 WEIBO_CREDENTIALS_FILE 一致（默认读同名环境变量）")
    parser.add_argument("--sandbox", default=DEFAULT_SANDBOX)
    parser.add_argument("--open", action="store_true", help="同时在沙箱里开局，供不经过 Runner 的手动试用")
    parser.add_argument("--scene", default="daily")
    parser.add_argument("--account", default="alice")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--clock", default=None, help="一局的时钟，如 2026-11-10 12:00:00；默认由世界的时间窗口决定")
    parser.add_argument("--fault", action="append", default=[], help="故障计划，JSON 字符串，可写多次")
    args = parser.parse_args(argv)
    if not args.credentials:
        parser.error("set --credentials or WEIBO_CREDENTIALS_FILE")

    episode_id, token = token_hex(16), token_hex(24)
    path = write_credentials(Path(args.credentials), token, args.sandbox)
    if args.open:
        admin_token = os.environ.get("SANDBOX_ADMIN_TOKEN")
        if not admin_token:
            parser.error("--open requires SANDBOX_ADMIN_TOKEN")
        body = {"episodeId": episode_id, "token": token, "seed": args.seed,
                "domains": [{"name": "weibo", "scene": args.scene, "account": args.account}],
                "faults": [json.loads(item) for item in args.fault]}
        if args.clock:
            body["clock"] = args.clock
        try:
            open_episode(args.sandbox, admin_token, body)
        except (urllib.error.URLError, OSError) as error:
            print(f"开局失败：{error}", file=sys.stderr)
            return 1

    print(f"授权文件已写入：{path}")
    print("\n1. Iris 的 .env（只需设置一次；沙箱按令牌找到这一局）：")
    print(f"   WEIBO_MCP_URL={args.sandbox.rstrip('/')}/mcp")
    print(f"   WEIBO_CREDENTIALS_FILE={path.resolve().as_posix()}")
    print("   然后重启 Iris：Iris 只在启动时读授权文件。")
    if args.open:
        print(f"\n2. 已开局 {episode_id}（{args.scene} / {args.account} / seed {args.seed}），重启 Iris 后即可对话。")
    else:
        print("\n2. 运行 Runner 前，在沙箱的 .env 或当前终端设置：")
        print(f"   SANDBOX_EPISODE_ID={episode_id}")
        print(f"   WEIBO_CREDENTIALS_FILE={path.resolve().as_posix()}")
        print(f"   同一局还涉及麦乐送时，另设 MCD_MCP_TOKEN={token}")
    print("\n每局的令牌只能用一次；下一局请重新运行本命令并重启 Iris。")
    return 0


if __name__ == "__main__":
    from sandbox.config import load_local_env
    load_local_env()
    sys.exit(main())
