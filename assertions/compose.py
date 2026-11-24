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
    nodes: list[Check] = [resolver(name) for name in use]
    nodes.extend(resolver(name) for name in declared.all)
    if declared.any:
        nodes.append(Any([resolver(name) for name in declared.any]))
    if declared.optional:
        nodes.append(Optional([resolver(name) for name in declared.optional]))
    return All(nodes)
