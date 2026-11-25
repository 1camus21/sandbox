"""断言的注册与解析。"""
from __future__ import annotations

from assertions.types import Check

REGISTRY: dict[tuple[str, int], Check] = {}


def register(check: Check) -> None:
    REGISTRY[(check.spec.id, check.spec.version)] = check


def resolve(name: str, declared: dict[str, int] | None = None) -> Check:
    base, _, version = name.partition("@")
    if version:
        key = (base, int(version))
    else:
        namespace = base.split(".", 1)[0]
        key = (base, (declared or {}).get(namespace, 1))
    if key not in REGISTRY:
        raise LookupError(f"unknown check: {name}")
    return REGISTRY[key]


def discover() -> None:
    """导入领域模块，触发注册。"""
    import assertions.domains.mcd  # noqa: F401
    import assertions.domains.weibo  # noqa: F401


def known() -> list[str]:
    return sorted({name for name, _ in REGISTRY})
