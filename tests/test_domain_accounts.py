"""账户与世界解耦的领域层用例（不依赖 starlette/mcp，可离线执行）。"""
import json

import pytest

import sandbox.domains.mcd as mcd_module
from sandbox.domains.mcd import McDelivery
from sandbox.domains.mcd.world import stores_for_address
from sandbox.episodes import Episode, Fault

STATE_KEYS = {"clock", "addresses", "stores", "menus", "coupons",
              "soldOut", "invalidCoupons", "quotes", "orders"}
COVERED_ADDRESS = "1000000000000000000000000001"
BOB_COVERED_ADDRESS = "1000000000000000000000000011"
BOB_UNCOVERED_ADDRESS = "1000000000000000000000000012"
ORDER = {"storeCode": "100001", "beType": 2, "orderType": 2,
         "items": [{"productCode": "100101", "quantity": 1}]}


@pytest.fixture
def domain():
    return McDelivery()


def account_fixture(name: str) -> dict:
    from sandbox.domains.mcd.world.accounts import ACCOUNT_DIR

    return json.loads((ACCOUNT_DIR / f"{name}.json").read_text(encoding="utf-8"))


def patch_account(monkeypatch, account: dict) -> None:
    monkeypatch.setattr(mcd_module, "list_accounts", lambda: [account["accountId"]])
    monkeypatch.setattr(mcd_module, "load_account", lambda name: json.loads(json.dumps(account)))


def test_accounts_and_scenes_are_discoverable(domain):
    assert domain.accounts() == ["alice", "bob"]
    assert "lunch" in domain.scenes()


def test_episode_carries_world_and_account_identity(domain):
    episode = Episode(domain, "lunch", 0, [], account="alice")
    snapshot = episode.snapshot()
    assert episode.account_id == "alice"
    assert snapshot["schemaVersion"] == 2
    assert snapshot["scene"] == snapshot["worldId"] == "lunch"
    assert snapshot["accountId"] == "alice"


def test_state_projection_keys_are_unchanged(domain):
    snapshot = Episode(domain, "lunch", 0, [], account="alice").snapshot()
    assert set(snapshot["state"]) == STATE_KEYS
    assert set(snapshot["initialState"]) == STATE_KEYS
    assert "sandbox-lunch" in snapshot["state"]["coupons"]


def test_tool_schema_is_unchanged(domain):
    names = {tool["name"] for tool in domain.tools}
    assert len(names) == 13
    assert {"calculate-price", "create-order", "delivery-query-stores", "query-store-coupons"} <= names
    alice = Episode(domain, "lunch", 0, [], account="alice")
    bob = Episode(domain, "lunch", 0, [], account="bob")
    assert alice.tool_schema_sha256 == bob.tool_schema_sha256


def test_uncovered_address_has_no_stores(domain):
    bob = Episode(domain, "lunch", 0, [], account="bob")
    assert stores_for_address(bob.world, BOB_UNCOVERED_ADDRESS) == []


def test_covered_address_has_stores(domain):
    alice = Episode(domain, "lunch", 0, [], account="alice")
    assert len(stores_for_address(alice.world, COVERED_ADDRESS)) == 2


def test_order_ids_differ_between_accounts(domain):
    alice = Episode(domain, "lunch", 0, [], account="alice")
    bob = Episode(domain, "lunch", 0, [], account="bob")
    alice.round = bob.round = 1
    alice_event, _ = alice.invoke("create-order", {**ORDER, "addressId": COVERED_ADDRESS}, 7, None)
    bob_event, _ = bob.invoke("create-order", {**ORDER, "addressId": BOB_COVERED_ADDRESS}, 7, None)
    alice_id = alice_event["changes"][0]["id"]
    bob_id = bob_event["changes"][0]["id"]
    assert alice_id != bob_id


def test_coupon_referencing_unknown_product_is_rejected(domain, monkeypatch):
    account = account_fixture("alice")
    account["coupons"][0]["appliesTo"] = ["999999"]
    patch_account(monkeypatch, account)
    with pytest.raises(ValueError, match="999999"):
        domain.open("lunch", 0, "alice")


def test_duplicate_address_id_is_rejected(domain, monkeypatch):
    account = account_fixture("alice")
    account["addresses"].append(dict(account["addresses"][0]))
    patch_account(monkeypatch, account)
    with pytest.raises(ValueError, match="重复"):
        domain.open("lunch", 0, "alice")


def test_dangling_menu_reference_is_rejected(domain, monkeypatch):
    from sandbox.domains.mcd.world import load_scene

    scene = load_scene("lunch")
    scene["stores"][0]["menu"] = "missing-menu"
    monkeypatch.setattr(mcd_module, "load_scene", lambda name: json.loads(json.dumps(scene)))
    with pytest.raises(ValueError, match="菜单"):
        domain.open("lunch", 0, "alice")


def test_start_coupon_fault_applies_to_account_coupon(domain):
    episode = Episode(domain, "lunch", 0,
                      [Fault(type="COUPON_INVALID", target="sandbox-lunch", at="start")],
                      account="alice")
    assert episode.world["invalidCoupons"] == ["sandbox-lunch"]
    assert episode.snapshot()["initialState"]["invalidCoupons"] == {"sandbox-lunch": True}
