"""调用记录的通用读取函数，不含领域语义。"""


def data_of(call: dict):
    result = call["business"]["result"]
    return result.get("data") if isinstance(result, dict) else result


def request_of(call: dict) -> dict:
    return call["request"]["arguments"]


def successful(call: dict) -> bool:
    return (call["business"]["success"] is True and call["transport"]["state"] == "emitted"
            and call["transport"]["httpStatus"] == 200)


def completed_by(call: dict, at: str | None) -> bool:
    completed = call["transport"].get("completedAt")
    return at is None or bool(completed and completed <= at)


def precedes(source: dict, call: dict) -> bool:
    """source 的响应在 call 发出之前已经返回，才能作为 call 参数的来源。

    与麦乐送 CallMatches.sources() 的时序规则一致：来源序号更小，且来源响应完成不晚于下游请求进入。
    """
    if source["seq"] >= call["seq"]:
        return False
    admitted = call.get("admittedMonotonicNs")
    completed = source["transport"].get("completedMonotonicNs")
    if admitted is not None and completed is not None and completed > admitted:
        return False
    return completed_by(source, call.get("requestedAt"))
