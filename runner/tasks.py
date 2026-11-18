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

from runner.contract import INTENTS, ORDER_TOOL, READ_TOOLS
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
    args: dict[str, StrictStr | StrictInt | StrictBool] = Field(default_factory=dict)
    bind: dict[Literal["addressId", "storeCode", "orderId"], str] = Field(default_factory=dict)
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


BINDINGS = {
    "addressId": {"delivery-query-addresses"},
    "storeCode": {"delivery-query-stores", "query-meals", "calculate-price"},
    "orderId": {"create-order"},
}
WRITES = {"delivery-create-address", "create-order", "cancel-order"}


class ExpectSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    calls: list[CallSpec] | None = None
    state: StateSpec = Field(default_factory=StateSpec)
    reply: ReplySpec = Field(default_factory=ReplySpec)
    onlyCalls: bool = False
    # Version 1 task files and recorded scenario views.
    orders: int = 0
    quote: bool = True
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
        actions = {action.actionId for action in self.script.interactions}
        if len(actions) != len(self.script.interactions):
            raise ValueError("repeated interaction ID")
        if any(not action.intentIds or not set(action.intentIds) <= INTENTS.keys()
               for action in self.script.interactions):
            raise ValueError("script.interactions must reference known intents")
        for step in calls:
            if step.tool not in READ_TOOLS | WRITES:
                raise ValueError(f"unknown tool: {step.tool}")
            if step.addressTag and step.tool != "delivery-query-addresses":
                raise ValueError("addressTag requires delivery-query-addresses")
            if step.menuProduct and step.tool != "query-meals":
                raise ValueError("menuProduct requires query-meals")
            if step.afterInteraction and step.afterInteraction not in actions:
                raise ValueError(f"unknown interaction: {step.afterInteraction}")
            if step.withinBudget and self.script.budgetFen is None:
                raise ValueError("withinBudget requires script.budgetFen")
            if step.outcome != "success" and step.tool != "create-order":
                raise ValueError("only create-order supports a timeout outcome")
            for field, ref in step.bind.items():
                if ref not in steps or steps[ref].tool not in BINDINGS[field]:
                    raise ValueError(f"invalid binding: {step.id}.{field} from {ref}")
        for step in calls:
            if step.tool == ORDER_TOOL:
                quote = steps.get(step.bind.get("storeCode"))
                if "addressId" not in step.bind or quote is None or quote.tool != "calculate-price":
                    raise ValueError("create-order must bind an address lookup and quote")
            if step.tool == "cancel-order" and "orderId" not in step.bind:
                raise ValueError("cancel-order must bind a create-order receipt")
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
            if ref not in steps or steps[ref].tool not in {"create-order", "cancel-order"}:
                raise ValueError(f"approval must reference an order write: {ref}")
        if {step.id for step in calls if step.tool in {"create-order", "cancel-order"}} != set(approved_steps):
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
        "checks": list(spec.expect.checks),
        "scene": spec.env.scene,
        "account": spec.env.account,
        "seed": spec.env.seed,
    }
