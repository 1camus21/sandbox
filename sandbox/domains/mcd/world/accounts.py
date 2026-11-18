"""账户 fixture 加载。

账户是"某个人的状态"：地址本、券、历史订单、积分、偏好。
世界（scenes/*.json）只放客观设施，两者在开局时合并成一个 world dict。
"""
from __future__ import annotations

import json
from pathlib import Path

ACCOUNT_DIR = Path(__file__).resolve().parent.parent / "accounts"
DEFAULT_ACCOUNT = "alice"


class AccountError(ValueError):
    pass


def list_accounts() -> list[str]:
    return sorted(p.stem for p in ACCOUNT_DIR.glob("*.json"))


def load_account(account_id: str) -> dict:
    if account_id not in list_accounts():
        raise AccountError(f"账户不存在: {account_id}")
    return json.loads((ACCOUNT_DIR / f"{account_id}.json").read_text(encoding="utf-8"))
