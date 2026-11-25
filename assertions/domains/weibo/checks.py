"""weibo 领域的检查。

只看本领域在正式轮次（round >= 1）里的调用；第 0 轮是探测，不参与判定。
"""
from __future__ import annotations

from collections import Counter

from assertions.primitives.calls import precedes, request_of, successful
from assertions.types import Result
from sandbox.domains.registry import manifest_for

DOMAIN = "weibo"
WRITE_TOOLS = manifest_for(DOMAIN).write_tools
HOT_TOOLS = frozenset({"search_hot_word_biz", "search_hot_word_cate_biz"})
STATUS_TOOLS = frozenset({"search_statuses_limited", "statuses_user_timeline_biz", "statuses_mentions_biz"})
SNIPPET = 8    # 用正文开头几个字作为可核对的事实
EMPTY_WORDS = ("没有", "未找到", "没找到", "暂无", "无结果", "0 条", "0条", "零条")


def scenario_calls(ctx) -> list[dict]:
    return [call for call in ctx.domain(DOMAIN).calls if call.get("round", 0) >= 1]


def steps(ctx) -> list:
    return [step for step in (ctx.spec.expect.calls or []) if (step.domain or "mcd") == DOMAIN]


def result_of(call: dict) -> dict:
    envelope = (call.get("business") or {}).get("result")
    if not isinstance(envelope, dict) or envelope.get("success") is not True:
        return {}
    return envelope.get("result") or {}


def ids_in(value) -> set[str]:
    """返回值里所有 idstr，供绑定核对：后一步的 id、cid 必须来自前一步的返回。"""
    found: set[str] = set()
    if isinstance(value, dict):
        for key, item in value.items():
            if key == "idstr" and isinstance(item, str):
                found.add(item)
            else:
                found |= ids_in(item)
    elif isinstance(value, list):
        for item in value:
            found |= ids_in(item)
    return found


def bound_to(value, ref_step, call: dict, calls: list[dict]) -> bool:
    """value 必须出现在 ref_step 某次成功返回里，且那次返回在 call 发出前已经完成。

    分页或重试时，下游请求之前的所有合格返回都可以作为来源。
    """
    if ref_step is None:
        return False
    sources = [source for source in calls
               if (source.get("request") or {}).get("tool") == ref_step.tool
               and successful(source) and precedes(source, call)]
    return any(str(value) in ids_in(result_of(source)) for source in sources)


def matches(ctx) -> dict[str, dict | None]:
    """把期望的调用和实际调用一一对上，结果在一次判定里共用。

    没有依赖关系的步骤不限先后；有绑定的步骤，来源必须在它之前已经返回。
    """
    cached = ctx.cache.get("weibo.matches")
    if cached is not None:
        return cached
    calls = scenario_calls(ctx)
    expected = steps(ctx)
    by_id = {step.id: step for step in expected}
    used: set[int] = set()
    bound: dict[str, dict | None] = {}
    for step in expected:
        bound[step.id] = None
        for index, call in enumerate(calls):
            request = call.get("request") or {}
            if index in used or request.get("tool") != step.tool:
                continue
            arguments = request.get("arguments") or {}
            if any(str(arguments.get(key)) != str(value) for key, value in step.args.items()):
                continue
            if any(not bound_to(arguments.get(field), by_id.get(ref), call, calls)
                   for field, ref in step.bind.items()):
                continue
            if step.outcome == "success" and not successful(call):
                continue
            used.add(index)
            bound[step.id] = call
            break
    ctx.cache["weibo.matches"] = bound
    return bound


def facts(call: dict) -> list[str]:
    """一次读调用里可以在回复中核对的事实：热搜词、作者昵称、正文开头、评论内容。"""
    tool = (call.get("request") or {}).get("tool")
    data = result_of(call)
    found: list[str] = []
    if tool in HOT_TOOLS:
        found += [row.get("word", "") for row in data.get("hot_words") or []]
    elif tool == "users_show_biz":
        found.append(data.get("screen_name", ""))
    elif tool in STATUS_TOOLS:
        for status in data.get("statuses") or []:
            found += [(status.get("user") or {}).get("screen_name", ""), (status.get("text") or "")[:SNIPPET]]
    elif tool == "comments_show_biz":
        for comment in data.get("comments") or []:
            found += [(comment.get("user") or {}).get("screen_name", ""), (comment.get("text") or "")[:SNIPPET]]
    return [item for item in found if item]


def says_empty(text: str) -> bool:
    return any(word in text for word in EMPTY_WORDS)


def arguments_of(call: dict) -> dict:
    return request_of(call) or {}


def replies(ctx) -> str:
    return "\n".join(row.get("text") or "" for row in ctx.transcript
                     if row.get("role") == "iris" and row.get("round", 0) >= 1)


def expected_calls(ctx) -> Result:
    bound = matches(ctx)
    children = {f"weibo.call.{name}": call is not None for name, call in bound.items()}
    return Result(all(children.values()), children=children)


def no_unexpected_writes(ctx) -> Result:
    """写操作按尝试计：沙箱里执行的、以及 Iris 请求审批的写操作，都不能超出题目声明。"""
    expected = Counter(step.tool for step in steps(ctx) if step.tool in WRITE_TOOLS)
    executed = Counter(call["request"]["tool"] for call in scenario_calls(ctx)
                       if call["request"]["tool"] in WRITE_TOOLS)
    requested = Counter(row.get("tool") for row in ctx.evidence.get("approvals") or []
                        if row.get("tool") in WRITE_TOOLS)
    ok = all(count <= expected[tool] for tool, count in (executed | requested).items())
    return Result(ok, "" if ok else "unexpected weibo write", {"weibo.noUnexpectedWrites": ok})


def reply_grounded(ctx) -> Result:
    """回复里至少出现一条来自对应调用返回的事实；返回为空时，回复必须如实说明没有结果。

    只核对指向微博步骤的引用；其他领域的引用由各自领域的检查负责。
    """
    text = replies(ctx)
    bound = matches(ctx)
    children: dict[str, bool] = {}
    for ref in ctx.spec.expect.reply.fromCalls:
        if ref not in bound:
            continue
        call = bound[ref]
        if call is None:
            children[f"weibo.reply.{ref}"] = False
            continue
        candidates = facts(call)
        children[f"weibo.reply.{ref}"] = (any(item in text for item in candidates) if candidates
                                         else says_empty(text))
    return Result(all(children.values()), children=children)
