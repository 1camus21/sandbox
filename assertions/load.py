"""加载任务集自带的检查模块。

任务集目录下的 checks.py 可以用 ``assertions.types.check`` 定义检查；
模块加载后，其中的检查对象自动注册到全局注册表。
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

from assertions.registry import register
from assertions.types import CheckSpec


def load_checks(path: Path) -> int:
    if not path.is_file():
        return 0
    name = f"task_set_checks_{path.parent.name}"
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ValueError(f"cannot load checks: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    count = 0
    for value in vars(module).values():
        check_spec = getattr(value, "spec", None)
        if isinstance(check_spec, CheckSpec) and callable(getattr(value, "evaluate", None)):
            register(value)
            count += 1
    return count
