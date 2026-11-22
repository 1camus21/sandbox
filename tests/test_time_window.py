"""世界时间窗口与开局时钟的用例。"""
import pytest

from sandbox.domains.mcd import McDelivery
from sandbox.episodes import DomainConfig, Episode


class Night:
    """与午餐场景不重叠的领域，用于验证窗口交集。"""

    name = "night"
    tools = []
    business_faults = frozenset()

    def worlds(self):
        return ["default"]

    def accounts(self):
        return []

    def load_world(self, world_id, seed):
        return {"clock": "2026-11-11 12:00:00",
                "window": {"from": "2026-11-11T10:00:00+08:00", "to": "2026-11-11T21:00:00+08:00"}}

    def load_account(self, account_id, world, seed):
        return {"accountId": account_id or "guest"}

    def world_window(self, world):
        return world.get("window")

    def world_state(self, world):
        return {}

    def account_state(self, account):
        return {}

    def state(self, world, account):
        return {}

    def fault(self, world, account, fault):
        raise ValueError(f"unknown fault: {fault.type}")

    def call(self, episode, tool, arguments):
        raise AssertionError("no tools")


def test_primary_clock_inside_window_is_used():
    episode = Episode([DomainConfig(McDelivery(), "lunch", "alice")], 0, [])

    snapshot = episode.snapshot()

    assert snapshot["clock"] == "2026-11-10 12:00:00"
    assert snapshot["window"]["from"].startswith("2026-11-10T10:00")
    assert snapshot["window"]["to"].startswith("2026-11-10T21:00")


def test_requested_clock_outside_window_is_rejected():
    with pytest.raises(ValueError, match="clock outside world window"):
        Episode([DomainConfig(McDelivery(), "lunch", "alice")], 0, [],
                clock="2026-11-10T23:00:00+08:00")


def test_requested_clock_inside_window_is_applied():
    episode = Episode([DomainConfig(McDelivery(), "lunch", "alice")], 0, [],
                      clock="2026-11-10T15:00:00+08:00")

    assert episode.snapshot()["clock"] == "2026-11-10 15:00:00"
    assert episode.world["clock"] == "2026-11-10 15:00:00"


def test_worlds_without_overlapping_window_are_rejected():
    with pytest.raises(ValueError, match="no overlapping window"):
        Episode([DomainConfig(McDelivery(), "lunch", "alice"),
                 DomainConfig(Night(), "default")], 0, [])
