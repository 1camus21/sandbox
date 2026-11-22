"""判定入口。

实现位于 assertions.domains.mcd；这里保留原有导入路径，避免调用方改动。
"""
from assertions.domains.mcd.checks import (
    judge,
    preflight_retained,
    recovery_evidence,
    reported_totals,
)

__all__ = ["judge", "preflight_retained", "recovery_evidence", "reported_totals"]
