"""任务检查声明的加载期校验。"""
from __future__ import annotations

from assertions import registry


def declared_names(spec) -> list[str]:
    checks = spec.expect.checks
    names: list[str] = []
    if checks.use is not None:
        names.extend(checks.use)
    names.extend(checks.all)
    names.extend(checks.any)
    names.extend(checks.optional)
    return list(dict.fromkeys(names))


def validate_checks(spec) -> None:
    """未知检查名直接报错；领域覆盖等校验在接入多领域后补充。"""
    registry.discover()
    for name in declared_names(spec):
        try:
            registry.resolve(name)
        except LookupError:
            raise ValueError(f"unknown check: {name}") from None
