"""微博经过真实 /admin 和 /mcp 入口的行为，与麦乐送同局，以及任务校验清单。"""
import json

import pytest

from conftest import ADMIN, admin, rpc, start, tool

TOKEN = "test-weibo"
MULTI_TOKEN = "test-weibo-mcd"


def open_weibo(client, **config):
    return start(client, token=TOKEN, domain="weibo", scene="daily", account="alice", **config)


def envelope(response):
    result = response.raise_for_status().json()["result"]
    return result, json.loads(result["content"][0]["text"])


def open_both(client, **extra):
    response = client.post("/admin/episodes", headers=ADMIN, json={
        "token": MULTI_TOKEN,
        "domains": [{"name": "mcd", "scene": "lunch", "account": "alice"},
                    {"name": "weibo", "scene": "daily", "account": "alice"}],
        **extra,
    })
    assert response.status_code == 201, response.text
    return response.json()["episodeId"]


def domain_rpc(client, episode_id, domain, method, params):
    return rpc(client, method, params, token=MULTI_TOKEN, endpoint=f"/mcp/{episode_id}/{domain}/1")


# ---- 单独开局 ----

def test_catalog_lists_weibo(client):
    domains = client.get("/admin/catalog", headers=ADMIN).raise_for_status().json()["domains"]
    assert domains["weibo"] == ["daily"] and "mcd" in domains


def test_tools_list_is_the_weibo_definition(client):
    from sandbox.domains.weibo import Weibo
    open_weibo(client)
    tools = rpc(client, "tools/list", {}, token=TOKEN).raise_for_status().json()["result"]["tools"]
    assert [item["name"] for item in tools] == [item["name"] for item in Weibo.tools]
    hints = {item["name"]: item["annotations"]["readOnlyHint"] for item in tools}
    assert hints["search_statuses_limited"] is True and hints["statuses_update"] is False


def test_search_returns_the_json_envelope(client):
    open_weibo(client)
    result, body = envelope(tool(client, "search_statuses_limited", {"q": "深圳降温", "type": 1}, token=TOKEN))
    assert result["isError"] is False
    assert body["success"] is True and body["result"]["total_number"] == 3


def test_business_errors_stay_in_the_envelope(client):
    run_id = open_weibo(client)
    result, body = envelope(tool(client, "comments_create", {"id": "5200000000009999", "comment": "x"}, token=TOKEN))
    assert result["isError"] is False and body["error"]["error_code"] == 20101
    assert admin(client, run_id, "snapshot").json()["calls"][0]["changes"] == []


def test_timeout_after_commit_still_publishes(client):
    run_id = open_weibo(client, faults=[{"type": "TIMEOUT_COMMITTED", "tool": "statuses_update"}])
    assert tool(client, "statuses_update", {"status": "超时也发出去了"}, token=TOKEN).status_code == 504
    snapshot = admin(client, run_id, "snapshot").json()
    posts = snapshot["account"]["final"]["weibo"]["myStatuses"].values()
    assert [post["text"] for post in posts if post.get("created")] == ["超时也发出去了"]


def test_world_stays_unchanged_after_writes(client):
    run_id = open_weibo(client)
    tool(client, "comments_create", {"id": "5200000000000004", "comment": "期待对比数据"}, token=TOKEN)
    tool(client, "attitudes_create", {"id": "5200000000000004"}, token=TOKEN)
    snapshot = admin(client, run_id, "snapshot").json()
    world = snapshot["world"]["weibo"]
    assert world["comments"].keys() == {"4900000000000001", "4900000000000002"}
    assert world["statuses"]["5200000000000004"]["comments_count"] == 512
    assert len(snapshot["account"]["final"]["weibo"]["myComments"]) == 1


# ---- 与麦乐送同局 ----

def test_weibo_and_mcd_share_one_episode(client):
    episode_id = open_both(client)
    snapshot = admin(client, episode_id, "snapshot").json()
    assert snapshot["domains"] == ["mcd", "weibo"]
    # 麦乐送午餐 10:00-21:00 与微博 08:00-22:00 求交集，未指定时钟时取起点后一小时。
    assert snapshot["window"] == {"from": "2026-11-10T10:00:00", "to": "2026-11-10T21:00:00"}
    assert snapshot["clock"] == "2026-11-10 11:00:00"


def test_per_domain_endpoints_list_their_own_tools(client):
    episode_id = open_both(client)
    weibo = domain_rpc(client, episode_id, "weibo", "tools/list", {}).json()["result"]["tools"]
    mcd = domain_rpc(client, episode_id, "mcd", "tools/list", {}).json()["result"]["tools"]
    assert len(weibo) == 13 and "statuses_update" in {item["name"] for item in weibo}
    assert len(mcd) == 13 and "create-order" in {item["name"] for item in mcd}


def test_weibo_tool_is_rejected_on_the_mcd_endpoint(client):
    episode_id = open_both(client)
    result = domain_rpc(client, episode_id, "mcd", "tools/call",
                        {"name": "users_show_biz", "arguments": {}}).json()["result"]
    assert result["isError"] is True and "not available" in result["content"][0]["text"]


def test_calls_are_recorded_per_domain(client):
    episode_id = open_both(client)
    domain_rpc(client, episode_id, "weibo", "tools/call", {"name": "statuses_update", "arguments": {"status": "午饭吃什么"}})
    domain_rpc(client, episode_id, "mcd", "tools/call", {"name": "now-time-info", "arguments": {}})
    snapshot = admin(client, episode_id, "snapshot").json()
    assert [call["domain"] for call in snapshot["calls"]] == ["weibo", "mcd"]
    created = [p for p in snapshot["account"]["final"]["weibo"]["myStatuses"].values() if p.get("created")]
    assert [p["text"] for p in created] == ["午饭吃什么"]


# ---- 任务校验清单 ----

WEIBO_ENV = {"domains": [{"name": "weibo", "scene": "daily", "account": "alice"}]}


def weibo_task(calls, approvals=()):
    from runner.tasks import TaskSpec
    return TaskSpec.model_validate({
        "taskId": "wb-check", "env": WEIBO_ENV,
        "script": {"prompt": "删掉我最新那条微博", "approvals": [{"call": ref} for ref in approvals]},
        "expect": {"calls": calls},
    })


def test_manifest_accepts_a_bound_write():
    spec = weibo_task([{"id": "mine", "tool": "statuses_user_timeline_biz"},
                       {"id": "delete", "tool": "statuses_destroy", "bind": {"id": "mine"}}], approvals=["delete"])
    assert [step.domain for step in spec.expect.calls] == ["weibo", "weibo"]


@pytest.mark.parametrize("calls, approvals", [
    ([{"id": "delete", "tool": "statuses_destroy"}], ["delete"]),                       # 写操作必须绑定目标
    ([{"id": "list", "tool": "comments_show_biz", "args": {"id": "5200000000000101"}},
      {"id": "delete", "tool": "statuses_destroy", "bind": {"id": "list"}}], ["delete"]),  # 编号不能来自评论列表
    ([{"id": "post", "tool": "statuses_update"}], []),                                   # 写操作必须安排审批
    ([{"id": "find", "tool": "search_statuses_limited", "args": {"q": "大模型"}},
      {"id": "post", "tool": "comments_create", "bind": {"id": "find"}, "outcome": "timeout_committed"}],
     ["post"]),                                                                          # 只有发布能期望超时
    ([{"id": "order", "tool": "create-order"}], []),                                     # 不是微博的工具
    ([{"id": "post", "tool": "statuses_update", "outcome": "timeout_committed"}], ["post"]),  # 超时判定实现前不允许声明
])
def test_manifest_rejects_invalid_calls(calls, approvals):
    with pytest.raises(ValueError):
        weibo_task(calls, approvals)
