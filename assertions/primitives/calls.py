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
