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


def task_domains(spec) -> set[str]:
    return {entry.name for entry in spec.env.domains} if spec.env.domains else {"mcd"}


def validate_checks(spec) -> None:
    """未知检查名、领域覆盖不满足都直接报错。"""
    registry.discover()
    domains = task_domains(spec)
    for name in declared_names(spec):
        try:
            check = registry.resolve(name)
        except LookupError:
            raise ValueError(f"unknown check: {name}") from None
        missing = set(check.spec.domains) - domains
        if missing:
            raise ValueError(f"check {name} requires domains: {', '.join(sorted(missing))}")
