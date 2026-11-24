"""Runner 端到端跑微博任务：开局、按微博探测、出题、取证、按 weibo.standard 判定。

扮演 Iris 的是下面的 FakeWeiboIris：它像真实的微博助手一样调用沙箱工具，再用返回的数据回复。
"""
import json

import httpx
import pytest

from conftest import ADMIN, MCP_TOKEN, tool
from runner.__main__ import save
from runner.clients import Iris, Sandbox
from runner.episode import run_episode
from runner.tasks import load_all_tasks

TASKS = {name: spec for name, spec in load_all_tasks().items() if name.startswith("s0-wb-")}


@pytest.fixture
def sandbox_client(client):
    client.base_url = "http://localhost/admin"
    client.headers.update(ADMIN)
    return Sandbox(client, MCP_TOKEN)


class FakeWeiboIris:
    """按任务名调用对应的微博工具并据实回复；可以注入几种错误行为。"""

    def __init__(self, sandbox, name, *, defect=None):
        self.sandbox = sandbox
        self.name = name
        self.defect = defect
        self.confirmations = []

    def call(self, name, args=None):
        response = tool(self.sandbox, name, args or {})
        envelope = json.loads(response.raise_for_status().json()["result"]["content"][0]["text"])
        return envelope.get("result") or {}

    def answer(self):
        if self.defect == "no_call":
            return "深圳今天降温了，大家注意保暖。"
        if self.defect == "unexpected_write":
            self.call("statuses_update", {"status": "顺手发一条"})
        name = self.name
        if name == "s0-wb-hot":
            words = self.call("search_hot_word_biz")["hot_words"]
            reply = "热搜前三：" + "、".join(row["word"] for row in words[:3])
        elif name == "s0-wb-hot-cate":
            words = self.call("search_hot_word_cate_biz", {"category": "科技"})["hot_words"]
            reply = "科技类热搜：" + "、".join(row["word"] for row in words)
        elif name == "s0-wb-search":
            statuses = self.call("search_statuses_limited", {"q": "深圳降温", "type": 1})["statuses"]
            reply = "发帖的有：" + "、".join(row["user"]["screen_name"] for row in statuses)
        elif name == "s0-wb-me":
            me = self.call("users_show_biz")
            reply = f"你的账号是 {me['screen_name']}，粉丝 {me['followers_count']}。"
        elif name == "s0-wb-timeline":
            statuses = self.call("statuses_user_timeline_biz")["statuses"]
            reply = "你最近发了：" + "；".join(row["text"] for row in statuses)
        elif name == "s0-wb-mentions":
            statuses = self.call("statuses_mentions_biz")["statuses"]
            reply = "；".join(f"{row['user']['screen_name']} 说：{row['text']}" for row in statuses)
        elif name == "s0-wb-comments":
            statuses = self.call("statuses_user_timeline_biz")["statuses"]
            target = next(row for row in statuses if "梧桐山" in row["text"])
            post_id = "5200000000000004" if self.defect == "wrong_target" else target["idstr"]
            comments = self.call("comments_show_biz", {"id": post_id})["comments"]
            reply = "；".join(f"{row['user']['screen_name']}：{row['text']}" for row in comments)
        else:
            raise AssertionError(name)
        return "这条我没查到具体内容。" if self.defect == "ungrounded" else reply

    def handle(self, request):
        path = request.url.path
        if path == "/api/chat":
            data = json.loads(request.content)
            if "-preflight-" in data["userId"]:
                count = self.call("search_statuses_limited", {"q": "深圳降温"})["total_number"]
                return httpx.Response(200, text=f"搜到 {count} 条微博。")
            return httpx.Response(200, text=self.answer())
        if path == "/api/agent/state":
            return httpx.Response(200, json={"tasks": []})
        if path == "/api/trace":
            return httpx.Response(200, json=[{"type": "TOOL_RESULT", "id": "iris-event-id"}])
        raise AssertionError(path)


def run(client, sandbox_client, name, **kwargs):
    fake = FakeWeiboIris(client, name, **kwargs)
    with httpx.Client(base_url="http://iris/api", transport=httpx.MockTransport(fake.handle)) as http:
        return run_episode(TASKS[name], Iris(http), sandbox_client)


def test_weibo_task_set_is_loaded():
    assert set(TASKS) == {"s0-wb-hot", "s0-wb-hot-cate", "s0-wb-search", "s0-wb-me",
                          "s0-wb-timeline", "s0-wb-mentions", "s0-wb-comments"}


@pytest.mark.parametrize("name", sorted(TASKS))
def test_weibo_tasks_pass_end_to_end(client, sandbox_client, tmp_path, name):
    evidence = run(client, sandbox_client, name)
    save(tmp_path / name, evidence)    # 和 python -m runner 一样写出证据和摘要
    summary = json.loads((tmp_path / name / "summary.json").read_text(encoding="utf-8"))
    assert summary["orderCount"] is None and summary["verdict"] == "passed"
    assert evidence["status"] == "complete", evidence["errors"]
    assert evidence["preflight"]["verified"] is True
    assert evidence["preflight"]["prompt"].startswith("这是微博集成测试")
    assert evidence["sandbox"]["closed"]
    verdict = evidence["verdict"]
    assert verdict["status"] == "passed", verdict
    assert "weibo.noUnexpectedWrites" in verdict["checks"]


@pytest.mark.parametrize("defect, failed", [
    ("no_call", "weibo.call."),
    ("unexpected_write", "weibo.noUnexpectedWrites"),
    ("ungrounded", "weibo.reply."),
])
def test_weibo_defects_fail_the_matching_check(client, sandbox_client, defect, failed):
    evidence = run(client, sandbox_client, "s0-wb-search", defect=defect)
    verdict = evidence["verdict"]
    assert verdict["status"] == "failed"
    assert any(name.startswith(failed) and not ok for name, ok in verdict["checks"].items()), verdict


def test_binding_must_come_from_the_earlier_result(client, sandbox_client):
    evidence = run(client, sandbox_client, "s0-wb-comments", defect="wrong_target")
    assert evidence["verdict"]["checks"]["weibo.call.comments"] is False


def test_mcd_tasks_still_default_to_the_mcd_pack():
    from assertions.compose import default_use
    tasks = load_all_tasks()
    assert default_use(tasks["order"]) == ["mcd.standard"]
    assert default_use(TASKS["s0-wb-me"]) == ["weibo.standard"]
