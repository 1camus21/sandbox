"""按种子生成变体：只改热度和互动数，不改正文、编号和时间。

题目里引用的微博编号在任何种子下都有效；热搜排名随热度变化，题目不能写死排名，要从工具返回里取。
种子为 0 时不做任何改动。
"""
from __future__ import annotations

import random

COUNTS = ("reposts_count", "comments_count", "attitudes_count")


def apply_variant(world: dict, seed: int) -> dict:
    if not seed:
        return world
    rng = random.Random(f"weibo-world:{seed}")
    for row in world["hotWords"]:
        row["num"] = max(1000, int(row["num"] * rng.uniform(0.6, 1.4)))
    _scale(world["statuses"], rng)
    return world


def apply_account_variant(account: dict, seed: int) -> dict:
    if not seed:
        return account
    rng = random.Random(f"weibo-account:{seed}")
    _scale(account["statuses"] + account["mentions"], rng)
    return account


def _scale(statuses: list[dict], rng: random.Random) -> None:
    for status in statuses:
        for key in COUNTS:
            status[key] = max(0, int(status[key] * rng.uniform(0.7, 1.3)))
