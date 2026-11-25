"""断言组合：全部 / 任一 / 可选。"""
from __future__ import annotations

from dataclasses import dataclass

from assertions.types import Check, Result


@dataclass
class All:
    checks: list[Check]

    def evaluate(self, ctx) -> Result:
        ok = True
        children: dict[str, bool] = {}
        details: list[str] = []
        for item in self.checks:
            result = item.evaluate(ctx)
            ok = ok and result.ok
            children.update(result.children)
            if result.detail:
                details.append(result.detail)
        return Result(ok, "; ".join(details), children)


@dataclass
class Scoped:
    """多领域任务里的单领域检查：在本领域的视图上执行。"""

    domain: str
    check: Check

    @property
    def spec(self):
        return self.check.spec

    def evaluate(self, ctx) -> Result:
        return self.check.evaluate(ctx.for_domain(self.domain))


@dataclass
class Any:
    checks: list[Check]

    def evaluate(self, ctx) -> Result:
        results = [item.evaluate(ctx) for item in self.checks]
        children: dict[str, bool] = {}
        for item, result in zip(self.checks, results):
            children[item.spec.id] = result.ok
            children.update(result.children)
        if any(result.ok for result in results):
            return Result(True, "", children)
        return Result(False, "; ".join(result.detail for result in results if result.detail), children)


@dataclass
class Optional:
    checks: list[Check]

    def evaluate(self, ctx) -> Result:
        children: dict[str, bool] = {}
        details: list[str] = []
        for item in self.checks:
            result = item.evaluate(ctx)
            children[item.spec.id] = result.ok
            children.update(result.children)
            if result.detail:
                details.append(result.detail)
        return Result(True, "; ".join(details), children)


DEFAULT_USE = ("mcd.standard",)


def default_use(spec) -> list[str]:
    """未声明 use 时，任务涉及的每个领域使用自己的默认包；只涉及 mcd 的任务仍是 mcd.standard。"""
    if not spec.env.domains:
        return list(DEFAULT_USE)
    return [f"{entry.name}.standard" for entry in spec.env.domains]


def expand(spec, resolver) -> All:
    declared = spec.expect.checks
    use = declared.use if declared.use is not None else default_use(spec)
    multi = len(spec.env.domains or []) > 1     # 旧任务没有 env.domains，字段为 None

    def node(name: str):
        # 多领域任务中，只声明一个领域的检查在本领域视图上执行；跨领域检查看完整上下文。
        item = resolver(name)
        return Scoped(item.spec.domains[0], item) if multi and len(item.spec.domains) == 1 else item

    nodes: list[Check] = [node(name) for name in use]
    nodes.extend(node(name) for name in declared.all)
    if declared.any:
        nodes.append(Any([node(name) for name in declared.any]))
    if declared.optional:
        nodes.append(Optional([node(name) for name in declared.optional]))
    return All(nodes)
