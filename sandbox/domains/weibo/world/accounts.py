"""账户层：当前授权用户的数据。

账户放只属于这个用户、或只对他有意义的内容：本人资料、本人微博、别人 @ 他的微博、
别人在他微博下的评论，以及本局里他做的所有操作（发布、转发、删除、评论、点赞）。
"""
from __future__ import annotations

import json
from pathlib import Path

ACCOUNT_DIR = Path(__file__).resolve().parent.parent / "accounts"
DEFAULT_ACCOUNT = "alice"


def list_accounts() -> list[str]:
    return sorted(p.stem for p in ACCOUNT_DIR.glob("*.json"))


def load_account(account_id: str) -> dict:
    if account_id not in list_accounts():
        raise ValueError(f"账户不存在: {account_id}")
    return json.loads((ACCOUNT_DIR / f"{account_id}.json").read_text(encoding="utf-8"))


def prepare_account(account: dict, world: dict) -> dict:
    """补齐运行时字段，并检查账户与世界之间的引用，内置账户和内联账户都走这里。"""
    me = account["me"]
    account.setdefault("accountId", me.get("uid", ""))
    users = {user["uid"] for user in world["users"]}
    if me["uid"] in users:
        raise ValueError(f"账户用户 {me['uid']} 和世界里的用户重复")
    own = account.setdefault("statuses", [])
    mentions = account.setdefault("mentions", [])
    inbound = account.setdefault("comments", [])
    attitudes = account.setdefault("attitudes", [])
    account.setdefault("myComments", [])
    account.setdefault("writeSeq", 0)

    world_ids = {status["idstr"] for status in world["statuses"]}
    ids = [status["idstr"] for status in own + mentions]
    if len(ids) != len(set(ids)) or world_ids & set(ids):
        raise ValueError("账户里的微博编号重复，或与世界里的微博重复")
    for status in own:
        if status["uid"] != me["uid"]:
            raise ValueError(f"本人微博 {status['idstr']} 的作者不是本人")
    for status in mentions:
        if status["uid"] not in users:
            raise ValueError(f"@我的微博 {status['idstr']} 的作者 {status['uid']} 不存在")
    for status in own + mentions:
        status.setdefault("deleted", False)
        status.setdefault("featured", False)
        status.setdefault("retweeted_id", None)
    own_ids = {status["idstr"] for status in own}
    for comment in inbound:
        if comment["status_id"] not in own_ids:
            raise ValueError(f"评论 {comment['idstr']} 不在本人微博下")
        if comment["uid"] not in users:
            raise ValueError(f"评论 {comment['idstr']} 的作者 {comment['uid']} 不存在")
        comment.setdefault("reply_to", None)
    for attitude in attitudes:
        if attitude["status_id"] not in world_ids | set(ids):
            raise ValueError(f"点赞记录指向不存在的微博 {attitude['status_id']}")
    return account
