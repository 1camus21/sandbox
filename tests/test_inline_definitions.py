"""内联世界与账户的用例。"""
from sandbox.domains.mcd import McDelivery
from sandbox.domains.mcd.world import load_scene
from sandbox.episodes import DomainConfig, Episode


def test_inline_world_is_used():
    data = load_scene("lunch")
    data["clock"] = "2026-11-10 13:30:00"
    data["stores"] = data["stores"][:1]

    episode = Episode([DomainConfig(McDelivery(), "lunch", "alice", world_inline=data)], 0, [])

    snapshot = episode.snapshot()
    assert len(episode.world["stores"]) == 1
    assert snapshot["clock"] == "2026-11-10 13:30:00"
    assert snapshot["worldIds"] == {"mcd": "@inline"}


def test_inline_account_is_used():
    account = {
        "accountId": "carol",
        "addresses": [{"addressId": "addr-1", "tag": "公司", "zone": "zone-a",
                       "contactName": "Carol", "phone": "138****0000", "fullAddress": "示例路 1 号"}],
    }

    episode = Episode([DomainConfig(McDelivery(), "lunch", None, account_inline=account)], 0, [])

    assert episode.account_id == "carol"
    assert [row["addressId"] for row in episode.account["addresses"]] == ["addr-1"]
    assert episode.snapshot()["accountIds"] == {"mcd": "carol"}
