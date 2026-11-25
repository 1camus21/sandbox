"""世界层：公共环境数据。

世界放所有账户看到的都一样的内容：其他用户、公开微博和评论、热搜，以及时间窗口。
工具调用从不修改世界；只有业务故障会改它（标记、删除公开微博），和麦乐送的售罄、门店关闭一致。
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

SCENE_DIR = Path(__file__).resolve().parent.parent / "scenes"
BEIJING = timezone(timedelta(hours=8))


def list_scenes() -> list[str]:
    return sorted(p.stem for p in SCENE_DIR.glob("*.json"))


def load_scene(scene_id: str) -> dict[str, Any]:
    if scene_id not in list_scenes():
        raise ValueError(f"世界不存在: {scene_id}")
    return json.loads((SCENE_DIR / f"{scene_id}.json").read_text(encoding="utf-8"))


def prepare_world(world: dict) -> dict:
    """补齐缺省字段并检查世界内部的引用，内置世界和内联世界都走这里。"""
    world.setdefault("users", [])
    world.setdefault("statuses", [])
    world.setdefault("comments", [])
    world.setdefault("hotCategories", [])
    world.setdefault("hotWords", [])
    world.setdefault("flags", {})
    uids = [user["uid"] for user in world["users"]]
    if len(uids) != len(set(uids)):
        raise ValueError("世界里的用户编号重复")
    ids = [status["idstr"] for status in world["statuses"]]
    if len(ids) != len(set(ids)):
        raise ValueError("世界里的微博编号重复")
    for status in world["statuses"]:
        if status["uid"] not in uids:
            raise ValueError(f"微博 {status['idstr']} 的作者 {status['uid']} 不存在")
        status.setdefault("deleted", False)
        status.setdefault("featured", False)
        status.setdefault("retweeted_id", None)
    for comment in world["comments"]:
        if comment["status_id"] not in ids:
            raise ValueError(f"评论 {comment['idstr']} 指向不存在的微博 {comment['status_id']}")
        if comment["uid"] not in uids:
            raise ValueError(f"评论 {comment['idstr']} 的作者 {comment['uid']} 不存在")
        comment.setdefault("reply_to", None)
    for row in world["hotWords"]:
        if row["category"] not in world["hotCategories"]:
            raise ValueError(f"热搜词 {row['word']} 的分类 {row['category']} 不在 hotCategories 里")
    return world


# ---------- 时间 ----------

def parse_time(value: str) -> datetime:
    """数据里的时间带 +08:00；一局的时钟不带时区，按北京时间处理。"""
    moment = datetime.fromisoformat(value.replace(" ", "T"))
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=BEIJING)


def weibo_time(value: str) -> str:
    """→ 微博接口常见的 created_at 格式，如 Tue Nov 10 09:12:00 +0800 2026。"""
    return parse_time(value).strftime("%a %b %d %H:%M:%S %z %Y")
