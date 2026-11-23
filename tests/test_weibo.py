"""微博领域层测试：直接驱动 Episode，不经过 HTTP。"""
from __future__ import annotations

import inspect
import json

import pytest

from sandbox.domains.weibo import Weibo
from sandbox.domains.weibo.handlers import HANDLERS, READ_TOOLS
from sandbox.episodes import Domain, DomainConfig, Episode, Fault

DOMAIN = Weibo()
CLOCK = "2026-11-10 12:00:00"
ALICE_POST = "5200000000000101"     # alice 自己的微博（梧桐山），账户里
OTHERS_POST = "5200000000000004"    # 别人的公开微博（大模型），世界里
ALICE_COMMENT = "4900000000000101"  # 别人在 alice 微博下的评论，账户里


def episode(*faults: Fault, account: str | None = "alice", seed: int = 0, clock: str | None = CLOCK,
            world_inline: dict | None = None, account_inline: dict | None = None) -> Episode:
    config = DomainConfig(domain=DOMAIN, scene="daily", account=account,
                          world_inline=world_inline, account_inline=account_inline)
    return Episode([config], seed, list(faults), clock=clock)


def call(ep: Episode, tool: str, **arguments):
    event, _ = ep.invoke(tool, arguments, ep.seq + 1, None)
    return event["business"]["result"], event


def changed(event, collection):
    return [c for c in event["changes"] if c["collection"] == collection]


def world_of(ep: Episode) -> dict:
    return DOMAIN.world_state(ep.worlds["weibo"])


# ---- 接口 ----

def test_domain_implements_protocol():
    for attribute in Domain.__annotations__:
        assert hasattr(DOMAIN, attribute), attribute
    for name, member in vars(Domain).items():
        if callable(member) and not name.startswith("_"):
            declared = list(inspect.signature(member).parameters)[1:]
            assert list(inspect.signature(getattr(DOMAIN, name)).parameters) == declared, name
    assert DOMAIN.worlds() == ["daily"]
    assert DOMAIN.accounts() == ["alice", "bob"]


def test_tools_match_handlers_and_carry_read_only_hints():
    names = [tool["name"] for tool in DOMAIN.tools]
    assert len(names) == len(set(names)) and set(names) == set(HANDLERS)
    for tool in DOMAIN.tools:
        assert tool["annotations"]["readOnlyHint"] is (tool["name"] in READ_TOOLS)


def test_world_and_account_collections_do_not_overlap():
    ep = episode()
    world = DOMAIN.world_state(ep.worlds["weibo"])
    account = DOMAIN.account_state(ep.accounts["weibo"])
    assert not set(world) & set(account)


def test_text_is_the_json_envelope():
    ep = episode()
    result, event = call(ep, "users_show_biz")
    assert json.loads(event["business"]["text"]) == result
    assert result["result"]["screen_name"] == "Alice在深圳"


# ---- 时钟 ----

def test_clock_defaults_inside_the_window_and_times_carry_beijing_offset():
    ep = episode(clock=None)
    assert ep.clock == "2026-11-10 09:00:00" and ep.clock_source == "window"
    result, _ = call(ep, "statuses_update", status="现在几点")
    assert result["result"]["created_at"] == "Tue Nov 10 09:00:00 +0800 2026"


def test_clock_outside_the_window_is_rejected():
    with pytest.raises(ValueError):
        episode(clock="2026-11-11 12:00:00")


# ---- 读 ----

def test_search_returns_fields_iris_compacts():
    ep = episode()
    result, _ = call(ep, "search_statuses_limited", q="深圳降温", type=1)
    body = result["result"]
    assert result["success"] is True and body["total_number"] == 3
    for status in body["statuses"]:
        for field in ("idstr", "mid", "created_at", "reposts_count", "comments_count", "attitudes_count"):
            assert field in status
        assert status["user"]["screen_name"]
    assert body["statuses"][0]["created_at"].startswith("Tue Nov 10 09:12")


def test_long_status_keeps_full_text_in_long_text():
    ep = episode()
    result, _ = call(ep, "search_statuses_limited", q="深圳本周天气提示")
    status = result["result"]["statuses"][0]
    assert status["isLongText"] is True and len(status["longText"]["longTextContent"]) > 500


def test_hot_sort_returns_only_featured_posts():
    ep = episode()
    hot, _ = call(ep, "search_statuses_limited", q="深圳降温", sort="hot")
    assert [s["idstr"] for s in hot["result"]["statuses"]] == ["5200000000000002"]


def test_search_pages_and_validates():
    ep = episode()
    page1, _ = call(ep, "search_statuses_limited", q="大模型", count=1)
    page2, _ = call(ep, "search_statuses_limited", q="大模型", count=1, page=2)
    assert page1["result"]["total_number"] == 2
    assert page1["result"]["statuses"][0]["idstr"] != page2["result"]["statuses"][0]["idstr"]
    bad, _ = call(ep, "search_statuses_limited", q="大模型", sort="best")
    assert bad["error"]["error_code"] == 10008
    assert call(ep, "search_statuses_limited")[0]["success"] is False


def test_search_covers_world_and_account_statuses():
    ep = episode()
    mine, _ = call(ep, "search_statuses_limited", q="好汉坡")
    mention, _ = call(ep, "search_statuses_limited", q="深圳湾")
    assert [s["idstr"] for s in mine["result"]["statuses"]] == [ALICE_POST]
    assert [s["idstr"] for s in mention["result"]["statuses"]] == ["5200000000000103"]


def test_hot_board_is_ranked_and_filtered_by_category():
    ep = episode()
    words = call(ep, "search_hot_word_biz")[0]["result"]["hot_words"]
    assert [w["rank"] for w in words] == list(range(1, 16)) and words[0]["word"] == "深圳降温"
    tech, _ = call(ep, "search_hot_word_cate_biz", category="科技")
    assert [w["word"] for w in tech["result"]["hot_words"]] == ["开源大模型新版本", "电动车冬季续航", "端侧小模型"]
    unknown, _ = call(ep, "search_hot_word_cate_biz", category="军事")
    assert unknown["success"] is False and "可选" in unknown["error"]["error"]


def test_timeline_mentions_and_comments_for_alice():
    ep = episode()
    timeline, _ = call(ep, "statuses_user_timeline_biz")
    assert [s["idstr"] for s in timeline["result"]["statuses"]] == ["5200000000000102", ALICE_POST]
    mentions, _ = call(ep, "statuses_mentions_biz")
    assert [s["idstr"] for s in mentions["result"]["statuses"]] == ["5200000000000103"]
    comments, _ = call(ep, "comments_show_biz", id=ALICE_POST)
    assert comments["result"]["total_number"] == 2


def test_bob_is_an_empty_account():
    ep = episode(account="bob")
    assert call(ep, "statuses_user_timeline_biz")[0]["result"] == {"statuses": [], "total_number": 0}
    assert call(ep, "statuses_mentions_biz")[0]["result"]["total_number"] == 0
    assert call(ep, "users_show_biz")[0]["result"]["statuses_count"] == 0


def test_default_account_is_alice():
    ep = episode(account=None)
    assert call(ep, "users_show_biz")[0]["result"]["screen_name"] == "Alice在深圳"


def test_reads_leave_no_changes():
    ep = episode()
    for tool, args in [("search_hot_word_biz", {}), ("search_statuses_limited", {"q": "快递"}),
                       ("users_show_biz", {}), ("comments_show_biz", {"id": ALICE_POST})]:
        _, event = call(ep, tool, **args)
        assert event["changes"] == [], tool


# ---- 写：只改账户 ----

def test_update_creates_a_status_in_the_account():
    ep = episode()
    result, event = call(ep, "statuses_update", status="今天降温，记得加衣服")
    new_id = result["result"]["idstr"]
    [created] = event["changes"]
    assert (created["collection"], created["id"], created["before"]) == ("myStatuses", new_id, None)
    assert call(ep, "statuses_user_timeline_biz")[0]["result"]["statuses"][0]["idstr"] == new_id


def test_update_rejects_empty_and_duplicate_text():
    ep = episode()
    empty, event = call(ep, "statuses_update", status="   ")
    assert empty["error"]["error_code"] == 20008 and event["changes"] == []
    call(ep, "statuses_update", status="同一句话")
    again, event = call(ep, "statuses_update", status="同一句话")
    assert again["error"]["error_code"] == 20019 and event["changes"] == []


def test_repost_counts_without_touching_the_world():
    ep = episode()
    base = call(ep, "search_statuses_limited", q="开源大模型")[0]["result"]["statuses"][0]["reposts_count"]
    result, event = call(ep, "statuses_repost", id=OTHERS_POST, status="收藏一下")
    assert result["result"]["retweeted_status"]["reposts_count"] == base + 1
    assert {c["collection"] for c in event["changes"]} == {"myStatuses"}
    after = call(ep, "search_statuses_limited", q="开源大模型")[0]["result"]["statuses"]
    assert next(s for s in after if s["idstr"] == OTHERS_POST)["reposts_count"] == base + 1


def test_destroy_only_own_status():
    ep = episode()
    others, event = call(ep, "statuses_destroy", id=OTHERS_POST)
    assert others["error"]["error_code"] == 20112 and event["changes"] == []
    mine, event = call(ep, "statuses_destroy", id=ALICE_POST)
    [change] = event["changes"]
    assert change["collection"] == "myStatuses"
    assert change["before"]["deleted"] is False and change["after"]["deleted"] is True
    assert call(ep, "statuses_destroy", id=ALICE_POST)[0]["error"]["error_code"] == 20101


def test_comment_and_reply():
    ep = episode()
    created, event = call(ep, "comments_create", id=OTHERS_POST, comment="期待对比数据")
    assert [c["collection"] for c in event["changes"]] == ["myComments"]
    listed, _ = call(ep, "comments_show_biz", id=OTHERS_POST)
    assert created["result"]["idstr"] in [c["idstr"] for c in listed["result"]["comments"]]
    reply, _ = call(ep, "comments_reply", id=ALICE_POST, cid=ALICE_COMMENT, comment="好呀")
    assert reply["result"]["reply_comment_id"] == ALICE_COMMENT
    wrong, _ = call(ep, "comments_reply", id=OTHERS_POST, cid=ALICE_COMMENT, comment="好呀")
    assert wrong["success"] is False


def test_like_is_idempotent_and_counted_once():
    ep = episode()
    first, event = call(ep, "attitudes_create", id=OTHERS_POST)
    assert [c["collection"] for c in event["changes"]] == ["myAttitudes"]
    second, event = call(ep, "attitudes_create", id=OTHERS_POST)
    assert event["changes"] == []
    assert second["result"]["status"]["attitudes_count"] == first["result"]["status"]["attitudes_count"]


def test_tools_never_change_the_world():
    ep = episode()
    before = world_of(ep)
    created = call(ep, "statuses_update", status="一条新微博")[0]["result"]["idstr"]
    call(ep, "statuses_repost", id=OTHERS_POST)
    call(ep, "comments_create", id=OTHERS_POST, comment="评论")
    call(ep, "comments_reply", id=ALICE_POST, cid=ALICE_COMMENT, comment="回复")
    call(ep, "attitudes_create", id=OTHERS_POST)
    call(ep, "statuses_destroy", id=created)
    assert world_of(ep) == before


def test_new_ids_are_reproducible():
    a, b = episode(), episode()
    ra = call(a, "statuses_update", status="可复现")[0]["result"]
    rb = call(b, "statuses_update", status="可复现")[0]["result"]
    assert (ra["idstr"], ra["created_at"]) == (rb["idstr"], rb["created_at"])


def test_episodes_do_not_share_state():
    a, b = episode(), episode()
    call(a, "statuses_update", status="只在 a 里")
    assert all(s["text"] != "只在 a 里" for s in call(b, "statuses_user_timeline_biz")[0]["result"]["statuses"])


def test_unknown_tool_returns_error_envelope():
    ep = episode()
    result, event = call(ep, "statuses_update_biz", status="旧名字")
    assert result["error"]["error_code"] == 10008 and event["changes"] == []


# ---- 业务故障 ----

def test_rate_limited_once_at_call():
    ep = episode(Fault(type="RATE_LIMITED", tool="search_statuses_limited", nth=1))
    first, event = call(ep, "search_statuses_limited", q="快递")
    assert first["error"]["error_code"] == 10023 and event["changes"] == []
    assert call(ep, "search_statuses_limited", q="快递")[0]["success"] is True


def test_rate_limited_from_start_blocks_everything():
    ep = episode(Fault(type="RATE_LIMITED", at="start"))
    for tool, args in [("users_show_biz", {}), ("search_hot_word_biz", {}), ("statuses_update", {"status": "x"})]:
        result, event = call(ep, tool, **args)
        assert result["error"]["error_code"] == 10023, tool
        assert event["changes"] == []


def test_hot_unavailable_at_call_then_recovers():
    ep = episode(Fault(type="HOT_UNAVAILABLE", tool="search_hot_word_biz", nth=1))
    assert call(ep, "search_hot_word_biz")[0]["success"] is False
    assert call(ep, "search_hot_word_biz")[0]["result"]["hot_words"]


def test_hot_unavailable_from_start_returns_empty_board():
    ep = episode(Fault(type="HOT_UNAVAILABLE", at="start"))
    assert call(ep, "search_hot_word_biz")[0]["result"]["hot_words"] == []
    assert call(ep, "search_hot_word_cate_biz", category="科技")[0]["result"]["hot_words"] == []
    assert call(ep, "search_statuses_limited", q="深圳降温")[0]["result"]["total_number"] == 3


def test_status_deleted_changes_the_world_and_hides_the_status():
    with pytest.raises(ValueError):
        episode(Fault(type="STATUS_DELETED", at="start", target="5200000000099999"))
    with pytest.raises(ValueError):
        episode(Fault(type="STATUS_DELETED", at="start", target=ALICE_POST))    # 只能删世界里的
    ep = episode(Fault(type="STATUS_DELETED", tool="comments_create", nth=1, target=OTHERS_POST))
    result, _ = call(ep, "comments_create", id=OTHERS_POST, comment="还在吗")
    assert result["error"]["error_code"] == 20101
    assert call(ep, "search_statuses_limited", q="开源大模型")[0]["result"]["total_number"] == 0


def test_content_rejected_once():
    ep = episode(Fault(type="CONTENT_REJECTED", tool="statuses_update", nth=1))
    first, event = call(ep, "statuses_update", status="第一次")
    assert first["error"]["error_code"] == 20021 and event["changes"] == []
    assert call(ep, "statuses_update", status="第一次")[0]["success"] is True


def test_faults_with_unexpected_targets_are_rejected():
    with pytest.raises(ValueError):
        episode(Fault(type="RATE_LIMITED", at="start", target="x"))


def test_timeout_committed_on_update_still_publishes():
    ep = episode(Fault(type="TIMEOUT_COMMITTED", tool="statuses_update", nth=1))
    event, override = ep.invoke("statuses_update", {"status": "超时也发出去了"}, 1, None)
    assert override is not None and event["business"]["executed"] is True
    assert [c["collection"] for c in event["changes"]] == ["myStatuses"]
    assert call(ep, "statuses_user_timeline_biz")[0]["result"]["statuses"][0]["text"] == "超时也发出去了"
    assert call(ep, "statuses_update", status="超时也发出去了")[0]["error"]["error_code"] == 20019


# ---- 种子、引用检查、内联 ----

def test_seed_changes_numbers_but_not_ids_or_text():
    base, other = episode(), episode(seed=7)
    b = call(base, "search_hot_word_biz")[0]["result"]["hot_words"]
    o = call(other, "search_hot_word_biz")[0]["result"]["hot_words"]
    assert sorted(w["word"] for w in b) == sorted(w["word"] for w in o)
    assert [w["num"] for w in b] != [w["num"] for w in o]
    assert call(episode(seed=7), "search_hot_word_biz")[0]["result"]["hot_words"] == o


def test_unknown_world_or_account_is_rejected():
    with pytest.raises(ValueError):
        DOMAIN.load_world("missing", 0)
    with pytest.raises(ValueError):
        DOMAIN.load_account("nobody", DOMAIN.load_world("daily", 0), 0)


def test_account_references_are_checked():
    from sandbox.domains.weibo.world.accounts import load_account
    world = DOMAIN.load_world("daily", 0)
    dangling = load_account("alice")
    dangling["comments"].append({"idstr": "4900000000009999", "status_id": "5200000000009999",
                                 "uid": "1004", "created_at": "2026-11-10T09:00:00+08:00", "text": "x"})
    with pytest.raises(ValueError):
        DOMAIN.load_inline_account(dangling, world, 0)
    clash = load_account("alice")
    clash["me"]["uid"] = "1001"
    with pytest.raises(ValueError):
        DOMAIN.load_inline_account(clash, world, 0)


def test_inline_world_and_account():
    world = {"window": {"from": "2026-11-10T08:00:00+08:00", "to": "2026-11-10T22:00:00+08:00"},
             "users": [{"uid": "1001", "screen_name": "测试号"}],
             "statuses": [{"idstr": "5200000000000001", "uid": "1001", "created_at": "2026-11-10T08:30:00+08:00",
                           "text": "内联世界里的唯一一条", "reposts_count": 0, "comments_count": 0,
                           "attitudes_count": 0}]}
    account = {"accountId": "solo", "me": {"uid": "2009", "screen_name": "内联账户"}}
    ep = episode(world_inline=world, account_inline=account)
    assert call(ep, "search_statuses_limited", q="内联")[0]["result"]["total_number"] == 1
    assert call(ep, "users_show_biz")[0]["result"]["screen_name"] == "内联账户"


def test_manifest_matches_the_tool_definitions():
    from sandbox.domains.registry import manifest_for
    manifest = manifest_for("weibo")
    assert manifest.tools == {tool["name"] for tool in DOMAIN.tools}
    assert manifest.read_tools == READ_TOOLS
