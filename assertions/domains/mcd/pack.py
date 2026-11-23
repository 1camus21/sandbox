"""mcd 领域的默认包与兼容检查。

mcd.standard 复刻重构前的隐式检查集合；LEGACY 中的检查对应任务可以显式引用的旧检查名。
"""
from __future__ import annotations

from assertions.domains.mcd import checks as C
from assertions.registry import register
from assertions.types import Result, check
from runner.tasks import CHECK_IDS


def _judgement(ctx):
    cached = ctx.cache.get("mcd.judgement")
    if cached is None:
        cached = C.evaluate_standard(ctx.evidence, ctx.quote, ctx.spec)
        ctx.cache["mcd.judgement"] = cached
    return cached


@check(id="mcd.standard", version=1, domains=("mcd",), requires=("evidence",),
       description="mcd 领域的默认检查集合")
def _standard(ctx) -> Result:
    _, checks = _judgement(ctx)
    return Result(all(checks.values()), children=checks)


def _legacy(name: str):
    def evaluate(ctx) -> Result:
        judgement, _ = _judgement(ctx)
        result = C.CHECKS[name](judgement)
        if result is None:
            return Result(True)
        return Result(all(result.values()), children=result)

    return evaluate


STANDARD = _standard
register(STANDARD)

for _name in CHECK_IDS:
    register(check(id=_name, version=1, domains=("mcd",), requires=("evidence",),
                   description=f"兼容检查：{_name}")(_legacy(_name)))
