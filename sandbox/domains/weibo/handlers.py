"""微博工具的处理逻辑。

数据来源：
    世界（episode.world）：其他用户、公开微博和评论、热搜、故障标记。工具只读。
    账户（episode.account）：本人资料和微博、@我的微博、别人在我微博下的评论，以及本局里我的操作。

我的操作涉及别人的微博时（转发、评论、点赞），只在账户里记一条，不改世界里那条微博；
返回给 Iris 时，计数 = 微博自身的基数 + 账户里指向它的操作数。

返回格式（信封）：
    成功 {"success": true, "result": <数据>}
    失败 {"success": false, "error": {"error_code": <整数>, "error": <说明>, "request": <接口路径>}}
成功信封来自 Iris 源码 AgentToolkitFactory.compactWeiboSearch 的解析逻辑；其余字段、错误码按开放平台文档推断，待校准。
"""
from __future__ import annotations

from datetime import timedelta
from typing import Any, Callable

from sandbox.domains.weibo.world import parse_time, weibo_time

LONG_TEXT_LIMIT = 140
MAX_STATUS_LENGTH = 2000
MAX_COMMENT_LENGTH = 140
STATUS_ID_BASE = 5200000000100000
COMMENT_ID_BASE = 4900000000100000

READ_TOOLS = frozenset({
    "search_hot_word_biz", "search_hot_word_cate_biz", "search_statuses_limited",
    "users_show_biz", "statuses_user_timeline_biz", "statuses_mentions_biz", "comments_show_biz",
})
HOT_TOOLS = frozenset({"search_hot_word_biz", "search_hot_word_cate_biz"})
CONTENT_TOOLS = frozenset({"statuses_update", "statuses_repost", "comments_create", "comments_reply"})

# 错误码：前五个与开放平台公开文档一致；其余为沙箱自定，待真实录制校准。
PARAM_ERROR = 10008
RATE_LIMITED = 10023
CONTENT_NULL = 20008
DUPLICATE_STATUS = 20019
STATUS_NOT_FOUND = 20101
CONTENT_REJECTED = 20021
NOT_OWNER = 20112
COMMENT_NOT_FOUND = 20201
TEXT_TOO_LONG = 20012
HOT_UNAVAILABLE = 10001


class WeiboError(Exception):
    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def ok(result: Any) -> dict:
    return {"success": True, "result": result}


def fail(tool: str, error: WeiboError) -> dict:
    return {"success": False, "error": {"error_code": error.code, "error": error.message,
                                        "request": "/2/" + tool.replace("_", "/") + ".json"}}


# ---------- 世界 + 账户的只读视图 ----------

class View:
    def __init__(self, world: dict, account: dict):
        self.world = world
        self.account = account
        self.me = account["me"]["uid"]

    def user(self, uid: str) -> dict:
        if uid == self.me:
            return self.account["me"]
        return next(user for user in self.world["users"] if user["uid"] == uid)

    def statuses(self) -> list[dict]:
        """所有可见的微博：世界的公开微博、本人微博、@我的微博，排除已删除的。"""
        rows = self.world["statuses"] + self.account["statuses"] + self.account["mentions"]
        return [row for row in rows if not row["deleted"]]

    def find(self, idstr: str) -> dict:
        for status in self.statuses():
            if status["idstr"] == idstr:
                return status
        raise WeiboError(STATUS_NOT_FOUND, "微博不存在")

    def own(self, idstr: str) -> bool:
        return any(status["idstr"] == idstr for status in self.account["statuses"])

    def comments_for(self, idstr: str) -> list[dict]:
        rows = self.world["comments"] + self.account["comments"] + self.account["myComments"]
        return [row for row in rows if row["status_id"] == idstr]

    def counts(self, status: dict) -> dict:
        idstr = status["idstr"]
        reposts = sum(1 for row in self.account["statuses"]
                      if row.get("created") and not row["deleted"] and row["retweeted_id"] == idstr)
        comments = sum(1 for row in self.account["myComments"] if row["status_id"] == idstr)
        likes = sum(1 for row in self.account["attitudes"] if row.get("created") and row["status_id"] == idstr)
        return {"reposts_count": status["reposts_count"] + reposts,
                "comments_count": status["comments_count"] + comments,
                "attitudes_count": status["attitudes_count"] + likes}


# ---------- 参数 ----------

def _int(args: dict, key: str, default: int, low: int, high: int | None = None) -> int:
    value = args.get(key, default)
    if isinstance(value, str) and value.strip().lstrip("-").isdigit():
        value = int(value)
    if isinstance(value, bool) or not isinstance(value, int):
        raise WeiboError(PARAM_ERROR, f"参数 {key} 必须是整数")
    if value < low or (high is not None and value > high):
        raise WeiboError(PARAM_ERROR, f"参数 {key} 超出范围")
    return value


def _text(args: dict, key: str, limit: int, required: bool = True) -> str:
    value = args.get(key)
    if value is None and not required:
        return ""
    if not isinstance(value, str):
        raise WeiboError(PARAM_ERROR, f"缺少参数 {key}")
    value = value.strip()
    if required and not value:
        raise WeiboError(CONTENT_NULL, "内容为空")
    if len(value) > limit:
        raise WeiboError(TEXT_TOO_LONG, f"内容超过 {limit} 字")
    return value


def _id(args: dict, key: str = "id") -> str:
    value = args.get(key)
    if isinstance(value, int) and not isinstance(value, bool):
        value = str(value)
    if not isinstance(value, str) or not value.strip().isdigit():
        raise WeiboError(PARAM_ERROR, f"参数 {key} 必须是数字 ID")
    return value.strip()


def _page(rows: list, args: dict, default_count: int = 10) -> list:
    count = _int(args, "count", default_count, 1, 50)
    page = _int(args, "page", 1, 1)
    return rows[(page - 1) * count: page * count]


def _newest(rows: list[dict]) -> list[dict]:
    return sorted(rows, key=lambda row: (row["created_at"], row["idstr"]), reverse=True)


def _now(world: dict, account: dict) -> str:
    """写操作的时间：一局时钟 + 本局写操作序号秒，同一局、同一种子下可复现。"""
    return (parse_time(world["clock"]) + timedelta(seconds=account["writeSeq"])).isoformat()


def _next_id(account: dict, base: int, taken: set[str]) -> str:
    """按写操作序号分配编号；内联数据可能已经占用了某个编号，遇到就顺延。"""
    account["writeSeq"] += 1
    candidate = base + account["writeSeq"]
    while str(candidate) in taken:
        candidate += 1
    return str(candidate)


def _status_ids(view: "View") -> set[str]:
    rows = view.world["statuses"] + view.account["statuses"] + view.account["mentions"]
    return {row["idstr"] for row in rows}


def _comment_ids(view: "View") -> set[str]:
    rows = view.world["comments"] + view.account["comments"] + view.account["myComments"]
    return {row["idstr"] for row in rows}


# ---------- 渲染 ----------

def render_user(view: View, uid: str) -> dict:
    return {"id": int(uid), "idstr": uid, "screen_name": view.user(uid)["screen_name"]}


def render_status(view: View, status: dict, nested: bool = True) -> dict:
    text = status["text"]
    long_text = len(text) > LONG_TEXT_LIMIT
    out = {
        "id": int(status["idstr"]), "idstr": status["idstr"], "mid": status["idstr"],
        "created_at": weibo_time(status["created_at"]),
        "text": text[:LONG_TEXT_LIMIT] + "...全文" if long_text else text,
        "isLongText": long_text,
        "user": render_user(view, status["uid"]),
        **view.counts(status),
    }
    if long_text:
        out["longText"] = {"longTextContent": text}
    if nested and status.get("retweeted_id"):
        try:
            out["retweeted_status"] = render_status(view, view.find(status["retweeted_id"]), nested=False)
        except WeiboError:
            out["retweeted_status"] = {"idstr": status["retweeted_id"], "deleted": True,
                                       "text": "抱歉，此微博已被作者删除。"}
    return out


def render_comment(view: View, comment: dict) -> dict:
    out = {"id": int(comment["idstr"]), "idstr": comment["idstr"],
           "created_at": weibo_time(comment["created_at"]), "text": comment["text"],
           "user": render_user(view, comment["uid"]), "status_id": comment["status_id"]}
    if comment.get("reply_to"):
        out["reply_comment_id"] = comment["reply_to"]
    return out


# ---------- 读工具 ----------

def _ranked(rows: list[dict]) -> list[dict]:
    ordered = sorted(rows, key=lambda row: (-row["num"], row["word"]))
    return [{"rank": index, "word": row["word"], "num": row["num"], "category": row["category"],
             "label": row["label"]} for index, row in enumerate(ordered, 1)]


def hot_words(view: View, args: dict) -> dict:
    count = _int(args, "count", 50, 1, 50)
    return ok({"hot_words": _ranked(view.world["hotWords"])[:count],
               "updated_at": weibo_time(view.world["clock"])})


def hot_words_by_category(view: View, args: dict) -> dict:
    category = args.get("category")
    categories = view.world["hotCategories"]
    if not isinstance(category, str) or category not in categories:
        raise WeiboError(PARAM_ERROR, "分类不存在，可选：" + "、".join(categories))
    count = _int(args, "count", 20, 1, 50)
    rows = _ranked([row for row in view.world["hotWords"] if row["category"] == category])[:count]
    return ok({"category": category, "hot_words": rows, "updated_at": weibo_time(view.world["clock"])})


SORT_KEYS: dict[str, Callable[[View, dict], Any]] = {
    "time": lambda view, s: (s["created_at"], s["idstr"]),
    "hot": lambda view, s: (view.counts(s)["attitudes_count"], s["idstr"]),
    "fwnum": lambda view, s: (view.counts(s)["reposts_count"], s["idstr"]),
    "cmtnum": lambda view, s: (view.counts(s)["comments_count"], s["idstr"]),
}


def search_statuses(view: View, args: dict) -> dict:
    q = args.get("q")
    if not isinstance(q, str) or not q.strip():
        raise WeiboError(PARAM_ERROR, "缺少参数 q")
    sort = args.get("sort", "time")
    if sort not in SORT_KEYS:
        raise WeiboError(PARAM_ERROR, "sort 只能是 time、hot、fwnum、cmtnum")
    start = _int(args, "starttime", 0, 0)
    end = _int(args, "endtime", 0, 0)
    words = q.strip().lower().split()
    rows = [s for s in view.statuses() if all(word in s["text"].lower() for word in words)]
    if sort == "hot":
        rows = [s for s in rows if s["featured"]]     # 文档：sort=hot 只返回精选微博
    if start:
        rows = [s for s in rows if parse_time(s["created_at"]).timestamp() >= start]
    if end:
        rows = [s for s in rows if parse_time(s["created_at"]).timestamp() <= end]
    rows.sort(key=lambda s: SORT_KEYS[sort](view, s), reverse=True)
    return ok({"statuses": [render_status(view, s) for s in _page(rows, args)], "total_number": len(rows)})


def me(view: View, args: dict) -> dict:
    profile = view.account["me"]
    count = sum(1 for s in view.account["statuses"] if not s["deleted"])
    return ok({"id": int(view.me), "idstr": view.me, "screen_name": profile["screen_name"],
               "description": profile.get("description", ""),
               "followers_count": profile.get("followers_count", 0),
               "friends_count": profile.get("friends_count", 0), "statuses_count": count})


def user_timeline(view: View, args: dict) -> dict:
    rows = _newest([s for s in view.account["statuses"] if not s["deleted"]])
    return ok({"statuses": [render_status(view, s) for s in _page(rows, args)], "total_number": len(rows)})


def mentions(view: View, args: dict) -> dict:
    rows = _newest([s for s in view.account["mentions"] if not s["deleted"]])
    return ok({"statuses": [render_status(view, s) for s in _page(rows, args)], "total_number": len(rows)})


def comments_show(view: View, args: dict) -> dict:
    status = view.find(_id(args))
    rows = _newest(view.comments_for(status["idstr"]))
    return ok({"comments": [render_comment(view, c) for c in _page(rows, args, 20)], "total_number": len(rows)})


# ---------- 写工具：只写账户 ----------

def _new_status(view: View, text: str, retweeted_id: str | None) -> dict:
    created = _now(view.world, view.account)
    status = {"idstr": _next_id(view.account, STATUS_ID_BASE, _status_ids(view)), "uid": view.me, "created_at": created,
              "text": text, "reposts_count": 0, "comments_count": 0, "attitudes_count": 0,
              "featured": False, "deleted": False, "retweeted_id": retweeted_id, "created": True}
    view.account["statuses"].append(status)
    return status


def update(view: View, args: dict) -> dict:
    text = _text(args, "status", MAX_STATUS_LENGTH)
    if any(s["text"] == text and not s["deleted"] for s in view.account["statuses"]):
        raise WeiboError(DUPLICATE_STATUS, "不能重复发布相同内容的微博")
    return ok(render_status(view, _new_status(view, text, None)))


def repost(view: View, args: dict) -> dict:
    original = view.find(_id(args))
    text = _text(args, "status", MAX_STATUS_LENGTH, required=False) or "转发微博"
    return ok(render_status(view, _new_status(view, text, original["idstr"])))


def destroy(view: View, args: dict) -> dict:
    status = view.find(_id(args))
    if not view.own(status["idstr"]):
        raise WeiboError(NOT_OWNER, "只能删除自己发布的微博")
    rendered = render_status(view, status)
    status["deleted"] = True
    return ok(rendered)


def _add_comment(view: View, status: dict, text: str, reply_to: str | None) -> dict:
    created = _now(view.world, view.account)
    comment = {"idstr": _next_id(view.account, COMMENT_ID_BASE, _comment_ids(view)), "status_id": status["idstr"],
               "uid": view.me, "created_at": created, "text": text, "reply_to": reply_to}
    view.account["myComments"].append(comment)
    return render_comment(view, comment)


def comment_create(view: View, args: dict) -> dict:
    status = view.find(_id(args))
    return ok(_add_comment(view, status, _text(args, "comment", MAX_COMMENT_LENGTH), None))


def comment_reply(view: View, args: dict) -> dict:
    status = view.find(_id(args))
    cid = _id(args, "cid")
    if not any(c["idstr"] == cid for c in view.comments_for(status["idstr"])):
        raise WeiboError(COMMENT_NOT_FOUND, "评论不存在或不属于这条微博")
    return ok(_add_comment(view, status, _text(args, "comment", MAX_COMMENT_LENGTH), cid))


def attitude_create(view: View, args: dict) -> dict:
    status = view.find(_id(args))
    existing = next((a for a in view.account["attitudes"]
                     if a["status_id"] == status["idstr"] and a["uid"] == view.me), None)
    if existing is None:
        # 重复点赞按幂等处理：不报错、不重复计数。真实接口是否报错，待校准。
        existing = {"status_id": status["idstr"], "uid": view.me, "attitude": "heart",
                    "created_at": _now(view.world, view.account), "created": True}
        view.account["writeSeq"] += 1
        view.account["attitudes"].append(existing)
    return ok({"status": render_status(view, status, nested=False), "attitude": existing["attitude"],
               "created_at": weibo_time(existing["created_at"])})


HANDLERS: dict[str, Callable[[View, dict], dict]] = {
    "search_hot_word_biz": hot_words,
    "search_hot_word_cate_biz": hot_words_by_category,
    "search_statuses_limited": search_statuses,
    "users_show_biz": me,
    "statuses_user_timeline_biz": user_timeline,
    "statuses_mentions_biz": mentions,
    "comments_show_biz": comments_show,
    "statuses_update": update,
    "statuses_repost": repost,
    "statuses_destroy": destroy,
    "comments_create": comment_create,
    "comments_reply": comment_reply,
    "attitudes_create": attitude_create,
}
