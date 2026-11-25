"""探测核对：正式任务前，Iris 对任务涉及的每个领域都成功完成了一次只读探测。"""
from __future__ import annotations

from collections.abc import Iterable

from runner.domains import get_domain


def probe_succeeded(calls: list[dict], domains: Iterable[str]) -> bool:
    """每个领域的探测工具都成功调用过一次，且探测期间只出现这些领域的只读工具。"""
    profiles = [get_domain(name) for name in domains]
    allowed = frozenset().union(*(profile.read_tools for profile in profiles))
    return all(
        any(call["request"]["tool"] == profile.canary_tool and call["business"]["success"] is True
            and call["transport"]["state"] == "emitted" and call["transport"]["httpStatus"] == 200
            for call in calls)
        for profile in profiles
    ) and all(call["request"]["tool"] in allowed for call in calls)


def preflight_retained(evidence: dict, domains: Iterable[str] = ("mcd",)) -> bool:
    """关闭后的快照仍保留通过的探测：同一局、同一代次，探测调用与记录一致。"""
    probe = evidence["preflight"]
    snapshot = evidence["sandbox"]
    if not snapshot or not snapshot["closed"] or snapshot["runId"] != evidence["runId"]:
        return False
    generation = probe.get("generation")
    if snapshot["generation"] != generation:
        return False
    if any(event["generation"] != generation or event["runId"] != snapshot["runId"] for event in snapshot["calls"]):
        return False
    calls = [event for event in snapshot["calls"] if event["round"] == 0]
    return {event["requestId"] for event in calls} == set(probe["requestIds"]) and probe_succeeded(calls, domains)
