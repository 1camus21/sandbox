"""微博检查与微博 S0 任务集检查的反例。

调用记录由真实的沙箱领域生成，回复由测试控制，直接构造判定上下文，不经过 Runner 和 Iris。
"""
import pytest

from assertions import registry
from assertions.context import CheckContext
from assertions.domains.weibo import checks as C
from assertions.load import load_checks
from runner.tasks import TASKS_DIR, TaskSpec, load_tasks
from sandbox.domains.weibo import Weibo
from sandbox.episodes import DomainConfig, Episode

TASK_SET = TASKS_DIR / "20261124T0900"
load_checks(TASK_SET / "checks.py")
TASKS = load_tasks(TASK_SET)
WUTONG = "5200000000000101"     # alice 爬梧桐山的微博
COFFEE = "5200000000000102"     # 同一条时间线里的另一条


class Session:
    def __init__(self, seed: int = 0):
        config = DomainConfig(domain=Weibo(), scene="daily", account="alice")
        self.episode = Episode([config], seed, [], clock="2026-11-10 12:00:00")
        self.calls: list[dict] = []

    def call(self, tool: str, **arguments):
        event, _ = self.episode.invoke(tool, arguments, self.episode.seq + 1, None)
        # 和服务端一样记下响应完成的时间；同步调用里，完成时间就是请求时间
        event.update(round=1, domain="weibo", transport={"state": "emitted", "httpStatus": 200,
                                                         "completedAt": event.get("requestedAt")})
        self.calls.append(event)
        return event["business"]["result"].get("result") or {}

    def context(self, task, reply: str) -> CheckContext:
        return CheckContext(spec=task, evidence={"approvals": []}, quote=None, clock=None, calls=self.calls,
                            transcript=[{"role": "iris", "round": 1, "text": reply}],
                            worlds={}, accounts={}, initial={})

    def judge(self, task_id: str, reply: str, check: str):
        return registry.resolve(check).evaluate(self.context(TASKS[task_id], reply))


def names(rows: list[dict]) -> str:
    return "、".join(row["user"]["screen_name"] for row in rows)


# ---- 绑定：来源必须在下游请求之前已经返回 ----

def test_binding_source_must_come_before_the_request():
    session = Session()
    comments = session.call("comments_show_biz", id=WUTONG)["comments"]
    session.call("statuses_user_timeline_biz")          # 时间线在查评论之后才取得
    result = C.expected_calls(session.context(TASKS["s0-wb-comments"], names(comments)))
    assert result.children["weibo.call.comments"] is False


def test_independent_queries_may_run_in_any_order():
    task = TaskSpec.model_validate({
        "taskId": "x", "env": {"clock": "2026-11-10 12:00:00", "domains": [{"name": "weibo", "scene": "daily"}]},
        "script": {"prompt": "x"},
        "expect": {"calls": [{"id": "me", "tool": "users_show_biz"},
                             {"id": "search", "tool": "search_statuses_limited"}]},
    })
    session = Session()
    session.call("search_statuses_limited", q="深圳降温")
    session.call("users_show_biz")
    assert C.expected_calls(session.context(task, "")).ok is True


# ---- 搜索题 ----

def test_search_with_the_requested_topic_passes():
    session = Session()
    statuses = session.call("search_statuses_limited", q="深圳降温")["statuses"]
    assert session.judge("s0-wb-search", "发帖的有：" + names(statuses), "s0.wb.searchTopic").ok is True


def test_search_with_another_topic_fails():
    session = Session()
    statuses = session.call("search_statuses_limited", q="快递")["statuses"]
    result = session.judge("s0-wb-search", "发帖的有：" + names(statuses), "s0.wb.searchTopic")
    assert result.children["s0.wb.search.topic"] is False


def test_empty_search_cannot_claim_authors():
    session = Session()
    assert session.call("search_statuses_limited", q="完全不存在的话题")["statuses"] == []
    ctx = session.context(TASKS["s0-wb-search"], "我搜到十位作者。")
    assert C.reply_grounded(ctx).children["weibo.reply.search"] is False


def test_empty_search_reported_as_empty_is_grounded():
    session = Session()
    session.call("search_statuses_limited", q="完全不存在的话题")
    ctx = session.context(TASKS["s0-wb-search"], "没有找到相关的微博。")
    assert C.reply_grounded(ctx).children["weibo.reply.search"] is True


# ---- 评论题 ----

def test_comments_of_the_wutong_post_pass():
    session = Session()
    session.call("statuses_user_timeline_biz")
    comments = session.call("comments_show_biz", id=WUTONG)["comments"]
    assert session.judge("s0-wb-comments", names(comments), "s0.wb.commentsTarget").ok is True


def test_comments_of_another_post_in_the_same_timeline_fail():
    session = Session()
    session.call("statuses_user_timeline_biz")
    session.call("comments_show_biz", id=COFFEE)
    result = session.judge("s0-wb-comments", "这条微博没有评论。", "s0.wb.commentsTarget")
    assert result.children["s0.wb.comments.target"] is False


# ---- 热搜题 ----

@pytest.mark.parametrize("seed", [0, 7])
def test_top_three_in_order_passes(seed):
    session = Session(seed)
    words = [row["word"] for row in session.call("search_hot_word_biz")["hot_words"][:3]]
    reply = "；".join(f"第{index}名：{word}" for index, word in enumerate(words, 1))
    assert session.judge("s0-wb-hot", reply, "s0.wb.hotTop3").ok is True


@pytest.mark.parametrize("pick", ["last_only", "missing_one", "swapped"])
def test_wrong_top_three_fails(pick):
    session = Session()
    rows = session.call("search_hot_word_biz")["hot_words"]
    words = [row["word"] for row in rows[:3]]
    reply = {
        "last_only": rows[-1]["word"],
        "missing_one": "、".join(words[:2]),
        "swapped": "、".join([words[1], words[0], words[2]]),
    }[pick]
    assert session.judge("s0-wb-hot", reply, "s0.wb.hotTop3").ok is False
