"""Run scripted McDelivery episodes against Iris and write local evidence."""

import argparse
import json
import os
import subprocess
from contextlib import nullcontext
from pathlib import Path
from uuid import uuid4

import httpx

from monitor.qwen import QwenRecognizer, api_key, model_client
from runner.batch import new_batch_id, run_manifest
from runner.clients import Iris, Sandbox
from runner.episode import run_episode
from runner.report import save_report, summarize
from runner.tasks import TASKS_DIR, TaskSpec, load_tasks
from sandbox.config import load_local_env
from sandbox.storage import write_atomic


def save(directory: Path, evidence: dict) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    snapshot = evidence["sandbox"]
    if snapshot is not None:
        lines = (json.dumps(event, ensure_ascii=False) + "\n" for event in snapshot["calls"])
        write_atomic(directory / "events.jsonl", lines)
    write_atomic(directory / "evidence.json", (json.dumps(evidence, ensure_ascii=False, indent=2) + "\n",))
    save_report(directory / "summary.json", summarize(evidence))


def source_revision() -> dict | None:
    try:
        settings = {"cwd": Path(__file__).resolve().parents[1], "capture_output": True, "text": True,
                    "check": True, "timeout": 3}
        revision = subprocess.run(["git", "rev-parse", "HEAD"], **settings).stdout.strip()
        status = subprocess.run(["git", "status", "--porcelain"], **settings).stdout
        return {"commit": revision, "workingTreeDirty": bool(status.strip())}
    except (OSError, subprocess.SubprocessError):
        return None


def run_case(task: TaskSpec, *, iris_url: str, sandbox_url: str, admin_token: str, mcp_token: str,
             episode_id: str | None, seed: int | None, monitor_mode: str, key: str) -> dict:
    with (
        httpx.Client(base_url=iris_url.rstrip("/"), timeout=httpx.Timeout(10, read=300), trust_env=False) as iris_http,
        httpx.Client(base_url=sandbox_url.rstrip("/") + "/admin", timeout=10, trust_env=False,
                     headers={"Authorization": f"Bearer {admin_token}"}) as sandbox_http,
        (model_client(key) if key else nullcontext(None)) as monitor_http,
    ):
        iris, sandbox = Iris(iris_http), Sandbox(sandbox_http, mcp_token)
        recognizer = QwenRecognizer(monitor_http) if monitor_http is not None else None
        return run_episode(task, iris, sandbox, seed=seed, monitor_mode=monitor_mode,
                           recognizer=recognizer, requested_episode_id=episode_id)


def main() -> int:
    load_local_env()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("task", nargs="?", default="order", help="任务 id，例如 order / timeout-after")
    parser.add_argument("--tasks-dir", type=Path, default=TASKS_DIR, help="任务 spec 目录")
    parser.add_argument("--iris", default="http://127.0.0.1:8080/api")
    parser.add_argument("--sandbox", default="http://127.0.0.1:8100")
    parser.add_argument("--seed", type=int, default=None, help="覆盖任务 spec 的种子（默认取 spec）")
    parser.add_argument("--iris-revision", help="Iris build revision to include in the evidence")
    parser.add_argument("--episode-id", default=os.environ.get("SANDBOX_EPISODE_ID") or None,
                        help="preassigned episode ID (default: SANDBOX_EPISODE_ID)")
    parser.add_argument("--monitor", choices=("off", "shadow", "active"), default="off",
                        help="record intent decisions or use them for configured scenario replies")
    parser.add_argument("--out", type=Path, default=Path("runs"))
    parser.add_argument("--batch-id", default=None,
                        help="批次标识；同一批次的所有局写入 runs/<batchId>/<runId>/")
    args = parser.parse_args()
    if args.episode_id and (len(args.episode_id) != 32 or any(c not in "0123456789abcdef" for c in args.episode_id)):
        parser.error("--episode-id requires 32 lowercase hexadecimal characters")
    tasks = load_tasks(args.tasks_dir)
    if args.task not in tasks:
        parser.error(f"unknown task: {args.task} (available: {', '.join(sorted(tasks))})")
    admin_token = os.environ.get("SANDBOX_ADMIN_TOKEN")
    mcp_token = os.environ.get("MCD_MCP_TOKEN")
    if not admin_token or not mcp_token:
        parser.error("set SANDBOX_ADMIN_TOKEN and MCD_MCP_TOKEN")
    key = api_key() if args.monitor != "off" else ""
    if args.monitor != "off" and not key:
        parser.error("set MONITOR_DASHSCOPE_API_KEY or DASHSCOPE_API_KEY")
    batch_id = args.batch_id or new_batch_id()
    batch_dir = args.out / batch_id
    batch_dir.mkdir(parents=True, exist_ok=True)
    run_id = args.episode_id or uuid4().hex
    source = source_revision()
    evidence = run_case(tasks[args.task], iris_url=args.iris, sandbox_url=args.sandbox, admin_token=admin_token,
                        mcp_token=mcp_token, episode_id=run_id, seed=args.seed,
                        monitor_mode=args.monitor, key=key)
    evidence["provenance"].update(runnerSource=source, irisRevision=args.iris_revision)
    directory = batch_dir / run_id
    save(directory, evidence)
    write_atomic(directory / "run.json", (
        json.dumps(run_manifest(run_id, tasks[args.task], evidence), ensure_ascii=False, indent=2) + "\n",))
    verdict = evidence["verdict"]["status"]
    print(f"{args.task} {verdict} {directory.resolve()}")
    if evidence["status"] == "interrupted":
        return 130
    return 0 if verdict == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
