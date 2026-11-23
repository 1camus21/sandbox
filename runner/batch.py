"""Batch collection: plan, freeze task definitions, and aggregate a batch manifest."""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import yaml

from assertions.load import load_checks
from assertions.validate import validate_checks
from runner.tasks import TASKS_DIR, EnvDomain, TaskSpec, load_tasks


def new_batch_id(now: datetime | None = None) -> str:
    moment = now or datetime.now(timezone.utc)
    return f"b-{moment:%Y%m%d}-{uuid4().hex[:4]}"


@dataclass(frozen=True)
class BatchPlan:
    batch_id: str
    name: str
    task_set_id: str
    tasks: tuple[str, ...]
    seed: int | None = None


def load_plan(path: Path) -> BatchPlan:
    config = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return BatchPlan(
        batch_id=str(config.get("batchId") or new_batch_id()),
        name=str(config.get("name") or path.stem),
        task_set_id=str(config.get("taskSetId") or path.stem),
        tasks=tuple(config.get("tasks") or ()),
        seed=config.get("seed"),
    )


def _spec_payload(spec: TaskSpec) -> dict:
    return spec.model_dump(mode="json", by_alias=True, exclude_none=True)


def task_env(spec: TaskSpec) -> dict:
    """任务声明的领域、世界与账户；未声明 domains 时按单领域 mcd 处理。"""
    entries = spec.env.domains or [
        EnvDomain(name="mcd", scene=spec.env.scene, account=spec.env.account)]
    names = [entry.name for entry in entries]
    if len(set(names)) != len(names):
        raise ValueError(f"{spec.taskId}: repeated domain")
    return {
        "domains": names,
        "clock": spec.env.clock,
        "worlds": {entry.name: ("@inline" if entry.worldInline is not None else entry.scene)
                   for entry in entries},
        "accounts": {entry.name: ("@inline" if entry.accountInline is not None else entry.account)
                     for entry in entries},
        "inlineWorlds": [entry.name for entry in entries if entry.worldInline is not None],
    }


def validate(plan: BatchPlan, tasks: dict[str, TaskSpec], *, task_set_dir: Path | None = None) -> list[str]:
    """批次级校验：收集全部问题，不遇错即停。"""
    if task_set_dir is not None:
        load_checks(Path(task_set_dir) / "checks.py")
    task_ids = plan.tasks or tuple(sorted(tasks))
    problems: list[str] = []
    if not task_ids:
        problems.append("task set has no tasks")
    for task_id in task_ids:
        spec = tasks.get(task_id)
        if spec is None:
            problems.append(f"unknown task: {task_id}")
            continue
        try:
            task_env(spec)
        except ValueError as error:
            problems.append(f"{task_id}: {error}")
        try:
            validate_checks(spec)
        except ValueError as error:
            problems.append(f"{task_id}: {error}")
    return problems


def prepare(plan: BatchPlan, tasks: dict[str, TaskSpec], out_root: Path,
            *, task_set_dir: Path | None = None) -> dict:
    """创建批次目录，冻结任务定义，写入初始清单。

    计划未显式列出任务时，使用任务集目录里的全部任务。
    """
    problems = validate(plan, tasks, task_set_dir=task_set_dir)
    if problems:
        raise ValueError("batch validation failed:\n" + "\n".join(problems))
    task_ids = plan.tasks or tuple(sorted(tasks))
    batch_dir = out_root / plan.batch_id
    specs_dir = batch_dir / "specs"
    specs_dir.mkdir(parents=True, exist_ok=True)
    entries = []
    for task_id in task_ids:
        spec = tasks[task_id]
        (specs_dir / f"{task_id}.yaml").write_text(
            yaml.safe_dump(_spec_payload(spec), allow_unicode=True, sort_keys=False),
            encoding="utf-8")
        entries.append({"taskId": task_id, "specSha256": spec.digest(), **task_env(spec)})
    manifest = {
        "batchId": plan.batch_id,
        "name": plan.name,
        "taskSetId": plan.task_set_id,
        "seed": plan.seed,
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "tasks": entries,
        "domains": sorted({name for entry in entries for name in entry["domains"]}),
        "worlds": {
            name: sorted({entry["worlds"][name] for entry in entries if name in entry["worlds"]})
            for name in {name for entry in entries for name in entry["worlds"]}
        },
        "runs": [],
        "counts": {"planned": len(task_ids), "completed": 0, "passed": 0,
                   "failed": 0, "unavailable": 0, "errored": 0},
    }
    _write(batch_dir / "batch.json", manifest)
    return manifest


def aggregate(batch_dir: Path) -> dict:
    """汇总各局 run.json，更新批次清单。"""
    manifest_path = batch_dir / "batch.json"
    if not manifest_path.is_file():
        raise FileNotFoundError(f"batch manifest not found: {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    runs = []
    for run_dir in sorted(batch_dir.iterdir()):
        if not run_dir.is_dir() or run_dir.name == "specs":
            continue
        run_path = run_dir / "run.json"
        if not run_path.is_file():
            runs.append({"runId": run_dir.name, "status": "missing-run-json"})
            continue
        runs.append(json.loads(run_path.read_text(encoding="utf-8")))
    counts = {
        "planned": len(manifest["tasks"]),
        "completed": len(runs),
        "passed": sum(1 for run in runs if run.get("verdict") == "passed"),
        "failed": sum(1 for run in runs if run.get("verdict") == "failed"),
        "unavailable": sum(1 for run in runs if run.get("verdict") == "unavailable"),
        "errored": sum(1 for run in runs if run.get("status") == "error"),
    }
    manifest["runs"] = runs
    manifest["counts"] = counts
    manifest["updatedAt"] = datetime.now(timezone.utc).isoformat()
    _write(manifest_path, manifest)
    return manifest


def run_manifest(run_id: str, task: TaskSpec, evidence: dict) -> dict:
    verdict = (evidence.get("verdict") or {}).get("status")
    checks = (evidence.get("verdict") or {}).get("checks") or {}
    provenance = evidence.get("provenance") or {}
    return {
        "runId": run_id,
        "taskId": task.taskId,
        "domains": task_env(task)["domains"],
        "verdict": verdict,
        "status": evidence.get("status"),
        "failedChecks": sorted(name for name, ok in checks.items() if ok is False),
        "specSha256": provenance.get("specSha256"),
        "envId": provenance.get("envId"),
        "seed": evidence.get("seed"),
        "startedAt": evidence.get("startedAt"),
        "finishedAt": evidence.get("finishedAt"),
    }


def _write(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="runner.batch", description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)

    prepare_parser = sub.add_parser("prepare", help="创建批次目录并冻结任务定义")
    prepare_parser.add_argument("--plan", type=Path, required=True)
    prepare_parser.add_argument("--tasks-dir", type=Path, default=TASKS_DIR)
    prepare_parser.add_argument("--out", type=Path, default=Path("runs"))

    aggregate_parser = sub.add_parser("aggregate", help="汇总批次内各局结果")
    aggregate_parser.add_argument("--batch", type=Path, required=True,
                                  help="批次目录，例如 runs/b-20261119-abcd")

    validate_parser = sub.add_parser("validate", help="只校验批次与任务，不创建批次目录")
    validate_parser.add_argument("--plan", type=Path, required=True)
    validate_parser.add_argument("--tasks-dir", type=Path, default=TASKS_DIR)

    args = parser.parse_args(argv)
    if args.action == "prepare":
        plan = load_plan(args.plan)
        manifest = prepare(plan, load_tasks(args.tasks_dir), args.out, task_set_dir=args.tasks_dir)
        print(f"{plan.batch_id} planned={manifest['counts']['planned']} -> {args.out / plan.batch_id}")
    elif args.action == "validate":
        plan = load_plan(args.plan)
        problems = validate(plan, load_tasks(args.tasks_dir), task_set_dir=args.tasks_dir)
        for problem in problems:
            print(problem)
        print(json.dumps({"problems": len(problems)}, ensure_ascii=False))
        return 1 if problems else 0
    else:
        manifest = aggregate(args.batch)
        print(json.dumps(manifest["counts"], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
