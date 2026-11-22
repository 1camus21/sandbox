"""Synthetic McDelivery adapter."""

import json
from copy import deepcopy
from pathlib import Path

from sandbox.domains.mcd.handlers import RawText, dispatch
from sandbox.domains.mcd.world import list_scenes, load_scene
from sandbox.domains.mcd.world.accounts import DEFAULT_ACCOUNT, list_accounts, load_account
from sandbox.domains.mcd.world.variants import apply_account_variant, apply_variant
from sandbox.episodes import Episode, Fault, Outcome


class McDelivery:
    name = "mcd"
    tools = json.loads(Path(__file__).with_name("tools.json").read_text(encoding="utf-8"))
    business_faults = frozenset({"SOLD_OUT", "STORE_CLOSED", "COUPON_INVALID"})

    def worlds(self) -> list[str]:
        return list_scenes()

    def accounts(self) -> list[str]:
        return list_accounts()

    def load_world(self, world_id: str, seed: int) -> dict:
        """加载世界：客观设施（门店、菜单）与变体，不含账户数据。"""
        world = load_scene(world_id)
        world.update(soldOut=[])
        return apply_variant(world, seed)

    def load_account(self, account_id: str | None, world: dict, seed: int) -> dict:
        """加载账户：用户状态（地址、券、历史订单），并校验与世界之间的引用。

        只做引用完整性校验；地址不被覆盖、券在某店不可用都是合法状态，留给模型判断。
        """
        user = load_account(account_id or DEFAULT_ACCOUNT)
        self._check_references(world, user)
        account = apply_account_variant(user, seed)
        account["orders"] = deepcopy(user.get("orders") or [])
        account["quotes"] = deepcopy(user.get("quotes") or [])
        account["invalidCoupons"] = list(user.get("invalidCoupons") or [])
        return account

    def world_state(self, world: dict) -> dict:
        return {
            "clock": {"now": world["clock"]},
            "stores": {row["storeCode"]: row for row in world["stores"]},
            "menus": world["menus"],
            "soldOut": dict.fromkeys(world["soldOut"], True),
        }

    def account_state(self, account: dict) -> dict:
        return {
            "addresses": {row["addressId"]: row for row in account["addresses"]},
            "coupons": {row["couponId"]: row for row in account["coupons"]},
            "invalidCoupons": dict.fromkeys(account["invalidCoupons"], True),
            "quotes": {str(row["callSeq"]): row for row in account["quotes"]},
            "orders": {row["orderId"]: row for row in account["orders"]},
        }

    def state(self, world: dict, account: dict) -> dict[str, dict]:
        return {**self.world_state(world), **self.account_state(account)}

    def fault(self, world: dict, account: dict, fault: Fault) -> None:
        target = fault.target
        if fault.type == "SOLD_OUT" and any(target in menu["meals"] for menu in world["menus"].values()):
            if target not in world["soldOut"]:
                world["soldOut"].append(target)
            return
        if fault.type == "STORE_CLOSED":
            for store in world["stores"]:
                if store["storeCode"] == target:
                    store["forceClosed"] = True
                    return
        if fault.type == "COUPON_INVALID" and any(coupon["couponId"] == target for coupon in account["coupons"]):
            if target not in account["invalidCoupons"]:
                account["invalidCoupons"].append(target)
            return
        raise ValueError(f"unknown target for {fault.type}: {target!r}")

    def call(self, episode: Episode, tool: str, arguments: dict) -> Outcome:
        result = dispatch(episode, tool, arguments)
        if isinstance(result, RawText):
            return Outcome(result.success, result.text, result.text)
        return Outcome(result["success"], result, json.dumps(result, ensure_ascii=False, separators=(",", ":")))

    @staticmethod
    def _check_references(world: dict, account: dict) -> None:
        """引用完整性：fixture 内部自洽，避免调用期 KeyError。

        不校验可用性：地址没有门店覆盖、券在某店不适用，都是合法的世界状态。
        """
        seen: set[str] = set()
        for address in account.get("addresses", []):
            address_id = str(address.get("addressId", ""))
            if not address_id:
                raise ValueError("账户地址缺少 addressId")
            if address_id in seen:
                raise ValueError(f"账户地址 id 重复: {address_id}")
            seen.add(address_id)

        menus = world.get("menus") or {}
        for store in world.get("stores", []):
            if store.get("menu", "default") not in menus:
                raise ValueError(f"门店 {store.get('storeCode')} 引用了不存在的菜单: {store.get('menu')}")

        products = {code for menu in menus.values() for code in menu.get("meals", {})}
        for coupon in account.get("coupons", []):
            unknown = [code for code in coupon.get("appliesTo", []) if code not in products]
            if unknown:
                raise ValueError(f"优惠券 {coupon.get('couponId')} 指向不存在的商品: {', '.join(unknown)}")

        stores = {store.get("storeCode") for store in world.get("stores", [])}
        for order in account.get("orders", []):
            if order.get("storeCode") not in stores:
                raise ValueError(f"历史订单 {order.get('orderId')} 引用了不存在的门店: {order.get('storeCode')}")
            unknown = [item.get("productCode") for item in order.get("items", [])
                       if item.get("productCode") not in products]
            if unknown:
                raise ValueError(f"历史订单 {order.get('orderId')} 引用了不存在的商品: {', '.join(map(str, unknown))}")
