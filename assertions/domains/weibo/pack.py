"""weibo 领域的默认包与单项检查。

weibo.standard = 期望的调用都发生且绑定正确 + 没有超出题目的写操作 + 回复有工具返回作依据。
"""
from __future__ import annotations

from assertions.domains.weibo import checks as C
from assertions.registry import register
from assertions.types import Result, check

PARTS = (C.expected_calls, C.no_unexpected_writes, C.reply_grounded)


@check(id="weibo.standard", version=1, domains=("weibo",), requires=("evidence",),
       description="weibo 领域的默认检查集合")
def _standard(ctx) -> Result:
    ok, children = True, {}
    for part in PARTS:
        result = part(ctx)
        ok = ok and result.ok
        children.update(result.children)
    return Result(ok, children=children)


register(_standard)
register(check(id="weibo.expectedCalls", version=1, domains=("weibo",), requires=("evidence",),
               description="期望的调用都发生，参数和绑定正确")(C.expected_calls))
register(check(id="weibo.noUnexpectedWrites", version=1, domains=("weibo",), requires=("evidence",),
               description="没有超出题目声明的写操作，按尝试计")(C.no_unexpected_writes))
register(check(id="weibo.replyGrounded", version=1, domains=("weibo",), requires=("evidence",),
               description="回复引用了工具返回的事实")(C.reply_grounded))
