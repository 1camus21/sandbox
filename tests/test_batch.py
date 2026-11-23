"""Batch planning, task-definition freezing and manifest aggregation."""

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import pytest

from runner.batch import aggregate, load_plan, new_batch_id, prepare, run_manifest
from runner.tasks import load_tasks

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def workdir():
    path = ROOT / "runs" / f".test-{uuid4().hex}"
    path.mkdir(parents=True)
    yield path
    shutil.rmtree(path, ignore_errors=True)


def test_batch_id_is_sortable_and_opaque():
    moment = datetime(2026, 11, 19, tzinfo=timezone.utc)

    batch_id = new_batch_id(moment)

    assert batch_id.startswith("b-20261119-")
    assert len(batch_id) == len("b-20261119-") + 4


def test_load_plan_fills_defaults(workdir):
    plan_path = workdir / "s0.yaml"
    plan_path.write_text("tasks: [order, quote]\n", encoding="utf-8")

    plan = load_plan(plan_path)

    assert plan.task_set_id == "s0"
    assert plan.name == "s0"
    assert plan.tasks == ("order", "quote")
    assert plan.batch_id.startswith("b-")


def test_prepare_freezes_specs_and_writes_manifest(workdir):
    tasks = load_tasks()
    plan = load_plan(_write_plan(workdir, ["order", "quote"]))

    manifest = prepare(plan, tasks, workdir / "runs")

    batch_dir = workdir / "runs" / plan.batch_id
    assert (batch_dir / "specs" / "order.yaml").is_file()
    assert (batch_dir / "specs" / "quote.yaml").is_file()
    stored = json.loads((batch_dir / "batch.json").read_text(encoding="utf-8"))
    assert stored["batchId"] == plan.batch_id
    assert stored["counts"]["planned"] == 2
    assert [entry["taskId"] for entry in stored["tasks"]] == ["order", "quote"]
    assert manifest["counts"]["completed"] == 0


def test_prepare_derives_tasks_from_set_when_plan_has_none(workdir):
    plan_path = workdir / "plan.yaml"
    plan_path.write_text("name: 全量批次\n", encoding="utf-8")
    plan = load_plan(plan_path)
    tasks = load_tasks()

    manifest = prepare(plan, tasks, workdir / "runs")

    assert plan.tasks == ()
    assert manifest["counts"]["planned"] == len(tasks)
    assert [entry["taskId"] for entry in manifest["tasks"]] == sorted(tasks)


def test_prepare_rejects_unknown_tasks(workdir):
    plan = load_plan(_write_plan(workdir, ["order", "missing-task"]))

    with pytest.raises(ValueError, match="missing-task"):
        prepare(plan, load_tasks(), workdir / "runs")


def test_aggregate_counts_runs_and_errors(workdir):
    tasks = load_tasks()
    plan = load_plan(_write_plan(workdir, ["order", "quote"]))
    runs = workdir / "runs"
    prepare(plan, tasks, runs)
    batch_dir = runs / plan.batch_id

    _write_run(batch_dir, "aaaa", {"verdict": "passed", "status": "complete"})
    _write_run(batch_dir, "bbbb", {"verdict": "failed", "status": "complete",
                                   "failedChecks": ["reportedOrder"]})
    _write_run(batch_dir, "cccc", {"status": "error", "error": "boom"})

    manifest = aggregate(batch_dir)

    assert manifest["counts"] == {"planned": 2, "completed": 3, "passed": 1,
                                  "failed": 1, "unavailable": 0, "errored": 1}
    assert {run["runId"] for run in manifest["runs"]} == {"aaaa", "bbbb", "cccc"}


def test_aggregate_marks_missing_run_json(workdir):
    tasks = load_tasks()
    plan = load_plan(_write_plan(workdir, ["order"]))
    runs = workdir / "runs"
    prepare(plan, tasks, runs)
    batch_dir = runs / plan.batch_id
    (batch_dir / "dddd").mkdir()

    manifest = aggregate(batch_dir)

    assert manifest["runs"][0] == {"runId": "dddd", "status": "missing-run-json"}


def test_run_manifest_extracts_verdict_fields():
    task = load_tasks()["order"]
    evidence = {
        "status": "complete",
        "seed": 0,
        "startedAt": "2026-11-19T00:00:00Z",
        "finishedAt": "2026-11-19T00:01:00Z",
        "verdict": {"status": "failed", "checks": {"orderCount": False, "irisLedger": True}},
        "provenance": {"specSha256": "abc", "envId": "lunch:alice:0"},
    }

    manifest = run_manifest("run-1", task, evidence)

    assert manifest["runId"] == "run-1"
    assert manifest["taskId"] == "order"
    assert manifest["verdict"] == "failed"
    assert manifest["failedChecks"] == ["orderCount"]
    assert manifest["specSha256"] == "abc"
    assert manifest["envId"] == "lunch:alice:0"


def test_prepare_records_domains_and_worlds(workdir):
    tasks = load_tasks()
    plan = load_plan(_write_plan(workdir, ["order"]))

    manifest = prepare(plan, tasks, workdir / "runs")

    entry = manifest["tasks"][0]
    assert entry["domains"] == ["mcd"]
    assert entry["worlds"] == {"mcd": "lunch"}
    assert entry["accounts"] == {"mcd": "alice"}
    assert manifest["domains"] == ["mcd"]
    assert manifest["worlds"] == {"mcd": ["lunch"]}


def test_prepare_rejects_repeated_domain(workdir):
    from runner.tasks import TaskSpec

    spec = TaskSpec.model_validate({
        "taskId": "dup",
        "script": {"prompt": "p"},
        "env": {"clock": "2026-11-10 12:00:00",
                "domains": [{"name": "mcd"}, {"name": "mcd"}]},
    })
    with pytest.raises(ValueError, match="repeated domain"):
        prepare(load_plan(_write_plan(workdir, ["dup"])), {"dup": spec}, workdir / "runs")


def test_validate_loads_task_set_checks(workdir):
    from runner.batch import BatchPlan, validate

    task_dir = workdir / "taskset"
    task_dir.mkdir()
    (task_dir / "checks.py").write_text(
        "from assertions.types import check\n\n"
        "@check(id=\"s0.wired\", version=1, domains=(\"mcd\",))\n"
        "def wired(ctx):\n"
        "    return True\n",
        encoding="utf-8")
    (task_dir / "t.yaml").write_text(
        "taskId: t\nscript:\n  prompt: p\n"
        "env:\n  clock: \"2026-11-10 12:00:00\"\n"
        "expect:\n  checks:\n    all: [s0.wired]\n",
        encoding="utf-8")
    plan = BatchPlan(batch_id="b-test", name="n", task_set_id="taskset", tasks=("t",))

    assert validate(plan, load_tasks(task_dir), task_set_dir=task_dir) == []


def _write_plan(directory: Path, tasks: list[str]) -> Path:
    plan_path = directory / f"plan-{len(tasks)}-{uuid4().hex[:6]}.yaml"
    plan_path.write_text("tasks: [" + ", ".join(tasks) + "]\n", encoding="utf-8")
    return plan_path


def _write_run(batch_dir: Path, run_id: str, payload: dict) -> None:
    run_dir = batch_dir / run_id
    run_dir.mkdir()
    (run_dir / "run.json").write_text(
        json.dumps({"runId": run_id, "taskId": "order", **payload}, ensure_ascii=False),
        encoding="utf-8")
