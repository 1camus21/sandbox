"""手动联调和直接模式用的准备命令：生成本局的编号和令牌，写好 Iris 的微博授权文件。

    python -m runner.domains.weibo_cli --credentials D:/Iris/.weibo-sandbox/auth.json [--open]

加 --open 会同时在沙箱里开局，用于不经过 Runner、直接和 Iris 对话的手动试用。
命令行放在单独的模块里：runner.domains 建注册表时会导入 weibo，若在 weibo 里运行 -m 会被加载两遍。
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

from runner.domains.weibo import write_credentials

DEFAULT_SANDBOX = "http://127.0.0.1:8100"


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
