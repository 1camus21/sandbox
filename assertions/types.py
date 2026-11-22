"""断言协议与结果类型。"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Protocol


class Status(str, Enum):
    PASSED = "passed"
    FAILED = "failed"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class CheckSpec:
    id: str
    version: int = 1
    domains: tuple[str, ...] = ()
    requires: tuple[str, ...] = ()
    params: dict = field(default_factory=dict)
    description: str = ""


@dataclass(frozen=True)
class Result:
    """断言结果：ok 表示成立；children 保留按名称展开的子检查，供报告与兼容。"""

    ok: bool
    detail: str = ""
    children: dict[str, bool] = field(default_factory=dict)

    def __bool__(self) -> bool:
        return self.ok


class CheckError(Exception):
    """断言内部错误，区别于“不成立”。"""


class Check(Protocol):
    spec: CheckSpec

    def evaluate(self, ctx) -> Result: ...


def check(id: str, *, version: int = 1, domains: tuple[str, ...] = (),
          requires: tuple[str, ...] = (), params: dict | None = None,
          description: str = ""):
    """把函数包装成断言对象，函数可返回 bool 或 Result。"""
    spec = CheckSpec(id=id, version=version, domains=tuple(domains), requires=tuple(requires),
                     params=dict(params or {}), description=description)

    def wrap(function):
        class _Check:
            def __init__(self):
                self.spec = spec
                self.function = function

            def evaluate(self, ctx) -> Result:
                value = function(ctx)
                if isinstance(value, Result):
                    return value
                return Result(bool(value))

            def __repr__(self) -> str:
                return f"<check {spec.id}@version{spec.version}>"

        return _Check()

    return wrap
