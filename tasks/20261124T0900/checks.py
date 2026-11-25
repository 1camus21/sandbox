"""微博 S0 任务集自带的检查：核对每道题自己的要求。

weibo.standard 只核对所有微博题通用的规则（期望的调用、绑定、多余写操作、回复有依据）。
这里补充具体题目的要求：搜的是不是题目的关键词、查的是不是题目指定的那条微博、
热搜是不是按名次报了前三。所需的值都从本局的工具返回中提取，不写死编号和热度，
种子变化时仍然成立。
"""
from assertions.domains.weibo import checks as C
from assertions.types import Result, check


def _call(ctx, step_id):
    return C.matches(ctx).get(step_id)


def _reported_all(text, names):
    """返回非空时，回复要提到全部名字；返回为空时，回复要如实说明没有结果。"""
    names = [name for name in names if name]
    return all(name in text for name in names) if names else C.says_empty(text)


@check(id="s0.wb.searchTopic", version=1, domains=("weibo",),
       description="搜索题：关键词是“深圳降温”，回复报出了搜到的全部作者")
def search_topic(ctx) -> Result:
    call = _call(ctx, "search")
    if call is None:
        return Result(False, children={"s0.wb.search.topic": False, "s0.wb.search.authors": False})
    query = str(C.arguments_of(call).get("q") or "").replace(" ", "")
    topic = "深圳降温" in query
    statuses = C.result_of(call).get("statuses") or []
    authors = [(status.get("user") or {}).get("screen_name") for status in statuses]
    reported = _reported_all(C.replies(ctx), authors)
    return Result(topic and reported, children={"s0.wb.search.topic": topic, "s0.wb.search.authors": reported})


@check(id="s0.wb.commentsTarget", version=1, domains=("weibo",),
       description="评论题：查的是本人时间线里爬梧桐山的那条，回复报出了全部评论人")
def comments_target(ctx) -> Result:
    mine, comments = _call(ctx, "mine"), _call(ctx, "comments")
    if mine is None or comments is None:
        return Result(False, children={"s0.wb.comments.target": False, "s0.wb.comments.reported": False})
    statuses = C.result_of(mine).get("statuses") or []
    target = next((status["idstr"] for status in statuses if "梧桐山" in (status.get("text") or "")), None)
    picked = target is not None and str(C.arguments_of(comments).get("id")) == target
    rows = C.result_of(comments).get("comments") or []
    names = [(row.get("user") or {}).get("screen_name") for row in rows]
    reported = picked and _reported_all(C.replies(ctx), names)
    return Result(picked and reported,
                  children={"s0.wb.comments.target": picked, "s0.wb.comments.reported": reported})


@check(id="s0.wb.hotTop3", version=1, domains=("weibo",),
       description="热搜题：回复按名次报出了本局热搜榜的前三个词")
def hot_top3(ctx) -> Result:
    call = _call(ctx, "hot")
    if call is None:
        return Result(False, children={"s0.wb.hot.top3": False, "s0.wb.hot.order": False})
    rows = sorted(C.result_of(call).get("hot_words") or [], key=lambda row: row.get("rank", 0))
    words = [row.get("word") for row in rows[:3]]
    text = C.replies(ctx)
    present = len(words) == 3 and all(word in text for word in words)
    positions = [text.find(word) for word in words] if present else []
    ordered = present and positions == sorted(positions)
    return Result(present and ordered, children={"s0.wb.hot.top3": present, "s0.wb.hot.order": ordered})
