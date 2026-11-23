"""时钟由任务声明、世界只提供窗口的用例。"""
import pytest

from sandbox.domains.mcd import McDelivery
from sandbox.episodes import DomainConfig, Episode


class Night:
    """与午餐场景不重叠的领域，用于验证窗口约束。"""

    name = "night"
    tools = []
    business_faults = frozenset()

    def worlds(self):
        return ["default"]

    def accounts(self):
        return []

    def load_world(self, world_id, seed):
        return {"window": {"from": "2026-11-11T10:00:00+08:00", "to": "2026-11-11T21:00:00+08:00"}}

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


def test_task_clock_is_applied_and_window_recorded():
    episode = Episode([DomainConfig(McDelivery(), "lunch", "alice")], 0, [],
                      clock="2026-11-10T12:00:00+08:00")

    snapshot = episode.snapshot()

    assert snapshot["clock"] == "2026-11-10 12:00:00"
    assert snapshot["clockSource"] == "task"
    assert snapshot["window"]["from"].startswith("2026-11-10T10:00")
    assert snapshot["window"]["to"].startswith("2026-11-10T21:00")
    assert episode.world["clock"] == "2026-11-10 12:00:00"


def test_missing_task_clock_is_selected_inside_window():
    episode = Episode([DomainConfig(McDelivery(), "lunch", "alice")], 0, [])

    snapshot = episode.snapshot()

    assert snapshot["clock"] == "2026-11-10 11:00:00"
    assert snapshot["clockSource"] == "window"


def test_clock_outside_window_is_rejected():
    with pytest.raises(ValueError, match="clock outside world windows"):
        Episode([DomainConfig(McDelivery(), "lunch", "alice")], 0, [],
                clock="2026-11-10T23:00:00+08:00")


def test_worlds_without_overlapping_window_are_rejected():
    with pytest.raises(ValueError, match="no overlapping window: mcd .*night") as error:
        Episode([DomainConfig(McDelivery(), "lunch", "alice"),
                 DomainConfig(Night(), "default")], 0, [],
                clock="2026-11-10T12:00:00+08:00")
    assert "2026-11-10T10:00:00" in str(error.value)
    assert "2026-11-11T10:00:00" in str(error.value)
