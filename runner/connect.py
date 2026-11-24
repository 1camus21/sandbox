"""为一道题准备 Iris 的连接，输出要注入 Iris 和 Runner 的环境变量（JSON）。

供外部编排（例如 IrisMind 的采集器）在启动 Iris 之前调用，连接方式的知识留在 runner.domains：

    python -m runner.connect <task> --tasks-dir <dir> --episode-id <id> --token <token> --workdir <dir>

输出 {"env": {...}}。麦乐送给出 MCD_MCP_URL、MCD_MCP_TOKEN；微博把授权文件写进 workdir，
给出 WEIBO_MCP_URL、WEIBO_CREDENTIALS_FILE。任务涉及多个领域时使用按领域的端点。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from runner.domains import get_domain, task_domains
from runner.tasks import TASKS_DIR, load_tasks

EPISODE_ID = re.compile(r"[0-9a-f]{32}")


def connection_env(task, sandbox_url: str, episode_id: str, token: str, workdir: Path) -> dict[str, str]:
    domains = task_domains(task)
    env: dict[str, str] = {}
    for name in domains:
        env.update(get_domain(name).connect_iris(sandbox_url.rstrip("/"), episode_id, token, Path(workdir),
                                                 scoped=len(domains) > 1))
    return env


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="runner.connect", description="输出一道题连接 Iris 所需的环境变量")
    parser.add_argument("task")
    parser.add_argument("--tasks-dir", type=Path, default=TASKS_DIR)
    parser.add_argument("--episode-id", required=True)
    parser.add_argument("--token", required=True)
    parser.add_argument("--workdir", type=Path, required=True, help="写授权文件等连接材料的目录，每局一个")
    parser.add_argument("--sandbox", default="http://127.0.0.1:8100")
    args = parser.parse_args(argv)
    if not EPISODE_ID.fullmatch(args.episode_id):
        parser.error("--episode-id requires 32 lowercase hexadecimal characters")
    tasks = load_tasks(args.tasks_dir)
    if args.task not in tasks:
        parser.error(f"unknown task: {args.task}")
    task = tasks[args.task]
    env = connection_env(task, args.sandbox, args.episode_id, args.token, args.workdir.resolve())
    print(json.dumps({"domains": task_domains(task), "env": env}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
