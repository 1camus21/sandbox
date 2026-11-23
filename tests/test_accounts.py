"""账户与世界解耦的集成用例（需要 starlette/mcp）。"""
import json

from conftest import ADMIN, ORDER, admin, start, tool


def body(response):
    return json.loads(response.raise_for_status().json()["result"]["content"][0]["text"])


def snapshot(client, episode_id):
    return admin(client, episode_id, "snapshot").json()


def test_default_account_and_snapshot_fields(client):
    run_id = start(client)
    snap = snapshot(client, run_id)
    assert snap["schemaVersion"] == 3
    assert snap["domains"] == ["mcd"]
    assert snap["scene"] == snap["worldId"] == "lunch"
    assert snap["accountId"] == "alice"


def test_unknown_account_is_rejected(client):
    response = client.post("/admin/episodes", headers=ADMIN,
                           json={"token": "t-unknown", "account": "ghost",
                                 "clock": "2026-11-10 12:00:00"})
    assert response.status_code == 400
    assert "unknown account" in response.json()["error"]


def test_invalid_account_slug_is_rejected(client):
    response = client.post("/admin/episodes", headers=ADMIN,
                           json={"token": "t-bad", "account": "Alice!",
                                 "clock": "2026-11-10 12:00:00"})
    assert response.status_code == 400


def test_same_episode_id_with_different_account_conflicts(client):
    episode_id = "a" * 32
    first = client.post("/admin/episodes", headers=ADMIN,
                        json={"token": "t-idem-1", "episodeId": episode_id, "account": "alice",
                              "clock": "2026-11-10 12:00:00"})
    assert first.status_code == 201
    second = client.post("/admin/episodes", headers=ADMIN,
                         json={"token": "t-idem-2", "episodeId": episode_id, "account": "bob",
                               "clock": "2026-11-10 12:00:00"})
    assert second.status_code == 409


def test_uncovered_address_returns_empty_stores(client):
    run_id = start(client, token="t-bob", account="bob")
    stores = body(tool(client, "delivery-query-stores",
                       {"addressId": "1000000000000000000000000012", "beType": 2}, token="t-bob"))
    assert stores["success"] is True
    assert stores["data"] == []
    assert snapshot(client, run_id)["accountId"] == "bob"


def test_order_ids_differ_between_accounts(client):
    alice_run = start(client, token="t-order-alice", account="alice")
    bob_run = start(client, token="t-order-bob", account="bob")
    alice_order = body(tool(client, "create-order", ORDER, token="t-order-alice"))["data"]["orderId"]
    bob_args = {**ORDER, "addressId": "1000000000000000000000000011"}
    bob_order = body(tool(client, "create-order", bob_args, token="t-order-bob"))["data"]["orderId"]
    assert alice_order != bob_order
    assert alice_order in snapshot(client, alice_run)["state"]["orders"]
    assert bob_order in snapshot(client, bob_run)["state"]["orders"]


def test_coupon_invalid_fault_applies_to_account_coupon(client):
    start(client, token="t-coupon", faults=[
        {"type": "COUPON_INVALID", "target": "sandbox-lunch", "at": "start"},
    ])
    coupons = tool(client, "query-my-coupons", token="t-coupon").raise_for_status()
    text = coupons.json()["result"]["content"][0]["text"]
    assert "暂无可用优惠券" in text
