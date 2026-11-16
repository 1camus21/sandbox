"""任务 spec：把一条任务从代码常量变成可校验的数据。

一条任务 = 环境引用（scene/account/seed） + 故障时间线 + 交互脚本 + 期望断言。
加载时完成结构校验；断言标识符由 CHECK_IDS 限定，避免判定静默错位。
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

from sandbox.episodes import Fault

TASKS_DIR = Path(__file__).resolve().parents[1] / "tasks"
CHECK_IDS = (
    "reported_order",
    "reported_denial",
    "no_claim_of_order",
    "budget_exceeded",
    "recovery",
    "scripted_interactions",
)


class EnvSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scene: str = "lunch"
    account: str = "alice"
    seed: int = 0


class InteractionSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    actionId: str
    intentIds: list[str]
    reply: str


class ScriptSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    prompt: str
    approve: bool = False
    expectApproval: bool = False
    budgetFen: int | None = None
    quoteItems: list[dict[str, Any]] | None = None
    quoteAfterInteraction: bool = False
    interactions: list[InteractionSpec] = Field(default_factory=list)


class ExpectSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    orders: int = 0
    checks: list[str] = Field(default_factory=list)

    @field_validator("checks")
    @classmethod
    def _known_checks(cls, value: list[str]) -> list[str]:
        unknown = [check for check in value if check not in CHECK_IDS]
        if unknown:
            raise ValueError(f"unknown check: {', '.join(unknown)}")
        return value


class TaskSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    taskId: str
    version: int = 1
    capability: str | None = None
    env: EnvSpec = Field(default_factory=EnvSpec)
    faults: list[Fault] = Field(default_factory=list)
    script: ScriptSpec
    expect: ExpectSpec = Field(default_factory=ExpectSpec)

    def digest(self) -> str:
        payload = json.dumps(self.model_dump(mode="json"), sort_keys=True,
                             separators=(",", ":"), ensure_ascii=False)
        return hashlib.sha256(payload.encode()).hexdigest()


def load_task(path: Path) -> TaskSpec:
    spec = TaskSpec.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
    if path.stem != spec.taskId:
        raise ValueError(f"taskId 与文件名不一致: {spec.taskId} != {path.stem}")
    return spec


def load_tasks(directory: Path = TASKS_DIR) -> dict[str, TaskSpec]:
    tasks: dict[str, TaskSpec] = {}
    for path in sorted(Path(directory).glob("*.yaml")):
        spec = load_task(path)
        if spec.taskId in tasks:
            raise ValueError(f"taskId 重复: {spec.taskId}")
        tasks[spec.taskId] = spec
    return tasks


def legacy_view(spec: TaskSpec) -> dict:
    """供证据回放与旧工具读取的字典视图。"""
    return {
        "taskId": spec.taskId,
        "prompt": spec.script.prompt,
        "approve": spec.script.approve,
        "expectApproval": spec.script.expectApproval,
        "orders": spec.expect.orders,
        "budgetFen": spec.script.budgetFen,
        "faults": [fault.model_dump() for fault in spec.faults],
        "interactions": [item.model_dump() for item in spec.script.interactions],
        "quoteItems": spec.script.quoteItems,
        "quoteAfterInteraction": spec.script.quoteAfterInteraction,
        "checks": list(spec.expect.checks),
        "scene": spec.env.scene,
        "account": spec.env.account,
        "seed": spec.env.seed,
    }
