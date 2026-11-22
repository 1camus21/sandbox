"""任务 spec：把一条任务从代码常量变成可校验的数据。

一条任务 = 环境引用（scene/account/seed） + 故障时间线 + 交互脚本 + 期望断言。
加载时完成结构校验；断言标识符由 CHECK_IDS 限定，避免判定静默错位。
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StrictStr, field_validator, model_validator

from runner.contract import INTENTS
from sandbox.domains.registry import manifest_for
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


class EnvDomain(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    scene: str = "lunch"
    account: str = "alice"


class EnvSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scene: str = "lunch"
    account: str = "alice"
    seed: int = 0
    domains: list[EnvDomain] | None = None


class InteractionSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    actionId: str
    intentIds: list[str]
    reply: str


class ApprovalSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    call: str
    approve: bool = True


class ScriptSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    prompt: str
    approvals: list[ApprovalSpec] = Field(default_factory=list)
    approve: bool = False
    expectApproval: bool = False
    budgetFen: int | None = Field(default=None, gt=0)
    quoteItems: list[dict[str, Any]] | None = None
    quoteAfterInteraction: bool = False
    interactions: list[InteractionSpec] = Field(default_factory=list)


class CallSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    tool: str
    domain: str | None = None
    args: dict[str, StrictStr | StrictInt | StrictBool] = Field(default_factory=dict)
    bind: dict[str, str] = Field(default_factory=dict)
    addressTag: str | None = None
    menuProduct: str | None = None
    afterInteraction: str | None = None
    withinBudget: bool = False
    outcome: Literal["success", "timeout_committed", "timeout_not_committed"] = "success"


class EntitySpec(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    call: str = Field(alias="from")


class OrderSpec(EntitySpec):
    status: Literal["待支付", "已取消"]


class StateSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    order: OrderSpec | None = None
    address: EntitySpec | None = None


class ReplySpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fromCalls: list[str] = Field(default_factory=list)
    noAddressQuestion: bool = False
    noOrderClaim: bool = False


class ChecksSpec(BaseModel):
    """任务声明的检查组合。

    use      默认包，缺省时使用领域默认包
    all      必须全部通过
    any      至少一个通过
    optional 参与执行并记录，不影响总体结果
    """

    model_config = ConfigDict(extra="forbid")

    use: list[str] | None = None
    all: list[str] = Field(default_factory=list)
    any: list[str] = Field(default_factory=list)
    optional: list[str] = Field(default_factory=list)


class ExpectSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    calls: list[CallSpec] | None = None
    state: StateSpec = Field(default_factory=StateSpec)
    reply: ReplySpec = Field(default_factory=ReplySpec)
    onlyCalls: bool = False
    # Version 1 task files and recorded scenario views.
    orders: int = 0
    quote: bool = True
    checks: ChecksSpec = Field(default_factory=ChecksSpec)

    @field_validator("checks", mode="before")
    @classmethod
    def _normalize_checks(cls, value):
        if value is None:
            return {}
        if isinstance(value, list):
            return {"all": value}
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

    @model_validator(mode="after")
    def _check_references(self) -> TaskSpec:
        calls = self.expect.calls
        if calls is None:
            if self.script.approvals:
                raise ValueError("script.approvals requires expect.calls")
            return self
        steps = {step.id: step for step in calls}
        if len(steps) != len(calls):
            raise ValueError("repeated call ID")
        names = [entry.name for entry in self.env.domains] if self.env.domains else ["mcd"]
        if len(set(names)) != len(names):
            raise ValueError("repeated domain")
        for step in calls:
            if step.domain is None:
                step.domain = names[0]
            elif step.domain not in names:
                raise ValueError(f"unknown domain for step {step.id}: {step.domain}")
        manifests = {name: manifest_for(name) for name in names}
        manifests = {name: item for name, item in manifests.items() if item is not None}
        approval_tools = {tool for item in manifests.values() for tool in item.approval_tools}
        actions = {action.actionId for action in self.script.interactions}
        if len(actions) != len(self.script.interactions):
            raise ValueError("repeated interaction ID")
        if any(not action.intentIds or not set(action.intentIds) <= INTENTS.keys()
               for action in self.script.interactions):
            raise ValueError("script.interactions must reference known intents")
        for step in calls:
            manifest = manifests.get(step.domain)
            if manifest is not None and step.tool not in manifest.tools:
                raise ValueError(f"unknown tool: {step.tool}")
            for attr, allowed in (manifest.field_tools.items() if manifest else ()):
                if getattr(step, attr) and step.tool not in allowed:
                    raise ValueError(f"{attr} requires {', '.join(sorted(allowed))}")
            if step.afterInteraction and step.afterInteraction not in actions:
                raise ValueError(f"unknown interaction: {step.afterInteraction}")
            if step.withinBudget and self.script.budgetFen is None:
                raise ValueError("withinBudget requires script.budgetFen")
            if step.outcome != "success" and (manifest is None or step.tool not in manifest.timeout_tools):
                raise ValueError("only create-order supports a timeout outcome")
            rules = manifest.bindings if manifest is not None else {}
            for field, ref in step.bind.items():
                if manifest is not None and field not in rules:
                    raise ValueError(f"invalid binding: {step.id}.{field} from {ref}")
                if ref not in steps or (manifest is not None and steps[ref].tool not in rules[field]):
                    raise ValueError(f"invalid binding: {step.id}.{field} from {ref}")
        for step in calls:
            manifest = manifests.get(step.domain)
            if manifest is None:
                continue
            for field in manifest.required_bindings.get(step.tool, ()):
                if field not in step.bind:
                    raise ValueError(f"invalid binding: {step.id}.{field} is required")
            for field, allowed in manifest.binding_sources.get(step.tool, {}).items():
                ref = step.bind.get(field)
                if ref is None or steps[ref].tool not in allowed:
                    raise ValueError(f"invalid binding: {step.id}.{field} from {ref}")
        resolved = set()
        while outstanding := {step.id for step in calls if step.id not in resolved}:
            ready = {key for key in outstanding if set(steps[key].bind.values()) <= resolved}
            if not ready:
                raise ValueError("call bindings contain a cycle")
            resolved.update(ready)
        approved_steps = [rule.call for rule in self.script.approvals]
        if len(approved_steps) != len(set(approved_steps)):
            raise ValueError("repeated approval call")
        for ref in approved_steps:
            if ref not in steps or steps[ref].tool not in approval_tools:
                raise ValueError(f"approval must reference an order write: {ref}")
        if {step.id for step in calls if step.tool in approval_tools} != set(approved_steps):
            raise ValueError("order writes require a scripted approval")
        for ref in self.expect.reply.fromCalls:
            if ref not in steps:
                raise ValueError(f"unknown reply call: {ref}")
        for entity, tool in ((self.expect.state.order, "create-order"),
                             (self.expect.state.address, "delivery-create-address")):
            if entity and (entity.call not in steps or steps[entity.call].tool != tool):
                raise ValueError(f"state must reference {tool}")
        self.expect.orders = int(self.expect.state.order is not None)
        self.expect.quote = any(step.tool == "calculate-price" for step in calls)
        return self

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


def load_all_tasks(directory: Path = TASKS_DIR) -> dict[str, TaskSpec]:
    """根目录的旧场景 + 各任务集目录里的任务，供按任务标识查找的兼容层使用。"""
    tasks = load_tasks(directory)
    for path in sorted(Path(directory).glob("*/task-set.json")):
        tasks.update(load_tasks(path.parent))
    return tasks


def legacy_view(spec: TaskSpec) -> dict:
    """供证据回放与旧工具读取的字典视图。"""
    expect = spec.expect
    calls = expect.calls
    return {
        "taskId": spec.taskId,
        **({"version": spec.version, "capability": spec.capability,
            "expect": expect.model_dump(mode="json", by_alias=True),
            "approvals": [rule.model_dump() for rule in spec.script.approvals]} if calls is not None else {}),
        "prompt": spec.script.prompt,
        "approve": spec.script.approve or any(rule.approve for rule in spec.script.approvals),
        "expectApproval": spec.script.expectApproval or bool(spec.script.approvals),
        "orders": expect.orders,
        "quote": expect.quote,
        "budgetFen": spec.script.budgetFen,
        "faults": [fault.model_dump() for fault in spec.faults],
        "interactions": [item.model_dump() for item in spec.script.interactions],
        "quoteItems": spec.script.quoteItems,
        "quoteAfterInteraction": spec.script.quoteAfterInteraction or any(
            step.tool == "calculate-price" and step.afterInteraction for step in calls or []
        ),
        "checks": spec.expect.checks.model_dump(exclude_none=True),
        "scene": spec.env.scene,
        "account": spec.env.account,
        "seed": spec.env.seed,
        **({"domains": [entry.model_dump() for entry in spec.env.domains]} if spec.env.domains else {}),
    }
