"""判定管线：证据校验 → 构造上下文 → 解析断言 → 执行 → 汇总。"""
from __future__ import annotations

from assertions import compose, registry
from assertions.context import build_context
from assertions.domains.mcd.checks import preflight_retained

UNAVAILABLE = {"status": "unavailable", "checks": {}}


def preconditions_ok(evidence: dict) -> bool:
    return (evidence.get("status") == "complete"
            and bool((evidence.get("preflight") or {}).get("verified"))
            and bool(evidence.get("sandbox"))
            and "state" in (evidence.get("iris") or {})
            and preflight_retained(evidence))


def judge(evidence: dict, quote: dict | None, spec) -> dict:
    if not preconditions_ok(evidence):
        return dict(UNAVAILABLE)
    ctx = build_context(evidence, quote, spec)
    try:
        registry.discover()
        tree = compose.expand(spec, lambda name: registry.resolve(name))
        result = tree.evaluate(ctx)
        checks = dict(result.children)
    except (KeyError, TypeError, AttributeError, IndexError, StopIteration):
        # 证据形状不完整时无法得出结论，与断言不成立区分开。
        return dict(UNAVAILABLE)
    return {"status": "passed" if all(checks.values()) else "failed", "checks": checks}
