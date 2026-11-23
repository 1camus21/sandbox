"""模拟微博（Synthetic Weibo MCP adapter）。

世界：其他用户、公开微博和评论、热搜、时间窗口。工具只读，只有业务故障会修改。
账户：本人资料和微博、@我的微博、别人在我微博下的评论，以及本局里我的发布、转发、删除、评论、点赞。
"""
from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

from sandbox.domains.weibo.handlers import (
    CONTENT_REJECTED, CONTENT_TOOLS, HANDLERS, HOT_TOOLS, HOT_UNAVAILABLE, PARAM_ERROR, RATE_LIMITED,
    View, WeiboError, fail,
)
from sandbox.domains.weibo.world import list_scenes, load_scene, prepare_world
from sandbox.domains.weibo.world.accounts import DEFAULT_ACCOUNT, list_accounts, load_account, prepare_account
from sandbox.domains.weibo.world.variants import apply_account_variant, apply_variant
from sandbox.episodes import Episode, Fault, Outcome


class Weibo:
    name = "weibo"
    tools = json.loads(Path(__file__).with_name("tools.json").read_text(encoding="utf-8"))
    business_faults = frozenset({"RATE_LIMITED", "HOT_UNAVAILABLE", "STATUS_DELETED", "CONTENT_REJECTED"})

    def worlds(self) -> list[str]:
        return list_scenes()

    def accounts(self) -> list[str]:
        return list_accounts()

    def load_world(self, world_id: str, seed: int) -> dict:
        return apply_variant(prepare_world(load_scene(world_id)), seed)

    def load_account(self, account_id: str | None, world: dict, seed: int) -> dict:
        account = prepare_account(load_account(account_id or DEFAULT_ACCOUNT), world)
        return apply_account_variant(account, seed)

    def load_inline_world(self, data: dict, seed: int) -> dict:
        return prepare_world(deepcopy(data))

    def load_inline_account(self, data: dict, world: dict, seed: int) -> dict:
        return prepare_account(deepcopy(data), world)

    def world_window(self, world: dict) -> dict | None:
        return world.get("window")

    def world_state(self, world: dict) -> dict:
        return {
            "clock": {"now": world["clock"]},
            "users": {row["uid"]: row for row in world["users"]},
            "statuses": {row["idstr"]: row for row in world["statuses"]},
            "comments": {row["idstr"]: row for row in world["comments"]},
            "hotWords": {row["word"]: row for row in world["hotWords"]},
            "flags": dict(world["flags"]),
        }

    def account_state(self, account: dict) -> dict:
        # 集合名与世界的不重名：state() 合并两者计算 changes，重名会互相覆盖。
        return {
            "profile": {account["me"]["uid"]: account["me"]},
            "myStatuses": {row["idstr"]: row for row in account["statuses"]},
            "mentions": {row["idstr"]: row for row in account["mentions"]},
            "inboundComments": {row["idstr"]: row for row in account["comments"]},
            "myComments": {row["idstr"]: row for row in account["myComments"]},
            "myAttitudes": {f"{row['uid']}:{row['status_id']}": row for row in account["attitudes"]},
        }

    def state(self, world: dict, account: dict) -> dict[str, dict]:
        return {**self.world_state(world), **self.account_state(account)}

    def fault(self, world: dict, account: dict, fault: Fault) -> None:
        """业务故障只改世界。框架开局时会先在副本上调用一次，配错在这里抛 ValueError。

        RATE_LIMITED     按调用：只影响这一次；开局生效：所有调用都超限
        HOT_UNAVAILABLE  按调用：下一次热搜调用报错；开局生效：热搜正常返回但榜单为空
        STATUS_DELETED   target 为世界里的微博编号，删除后对它的操作都返回“微博不存在”
        CONTENT_REJECTED 按调用：下一次发布、转发、评论、回复被拒；开局生效：一直被拒
        """
        kind = fault.type
        if kind != "STATUS_DELETED" and fault.target is not None:
            raise ValueError(f"{kind} takes no target")
        lasting = "always" if fault.at == "start" else "once"
        if kind == "RATE_LIMITED":
            world["flags"]["rateLimited"] = lasting
        elif kind == "HOT_UNAVAILABLE":
            world["flags"]["hotEmpty" if fault.at == "start" else "hotError"] = True
        elif kind == "CONTENT_REJECTED":
            world["flags"]["contentRejected"] = lasting
        elif kind == "STATUS_DELETED":
            status = next((row for row in world["statuses"] if row["idstr"] == fault.target), None)
            if status is None or status["deleted"]:
                raise ValueError(f"unknown target for STATUS_DELETED: {fault.target!r}")
            status["deleted"] = True
        else:
            raise ValueError(f"unknown weibo fault: {kind}")

    def call(self, episode: Episode, tool: str, arguments: dict) -> Outcome:
        view = View(episode.world, episode.account)
        try:
            envelope = self._dispatch(view, tool, arguments if isinstance(arguments, dict) else {})
        except WeiboError as error:
            envelope = fail(tool, error)
        return Outcome(success=envelope["success"], result=deepcopy(envelope),
                       text=json.dumps(envelope, ensure_ascii=False, separators=(",", ":")))

    @staticmethod
    def _dispatch(view: View, tool: str, arguments: dict) -> dict:
        flags = view.world["flags"]
        handler = HANDLERS.get(tool)
        if handler is None:
            raise WeiboError(PARAM_ERROR, f"未知工具: {tool}")
        if flags.get("rateLimited"):
            if flags["rateLimited"] == "once":
                del flags["rateLimited"]
            raise WeiboError(RATE_LIMITED, "用户请求频次超过上限")
        if tool in HOT_TOOLS:
            if flags.pop("hotError", False):
                raise WeiboError(HOT_UNAVAILABLE, "热搜服务暂时不可用")
            if flags.get("hotEmpty"):
                envelope = handler(view, arguments)
                envelope["result"]["hot_words"] = []
                return envelope
        if tool in CONTENT_TOOLS and flags.get("contentRejected"):
            if flags["contentRejected"] == "once":
                del flags["contentRejected"]
            raise WeiboError(CONTENT_REJECTED, "内容不符合发布规范")
        return handler(view, arguments)
