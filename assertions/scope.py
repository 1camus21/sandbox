"""多领域任务里，把单领域检查限定在本领域的视图上。

单领域检查（包括各领域的默认包）按“整道任务都是本领域”编写。多领域任务中，
它们只应看到本领域的步骤、回复引用、审批、调用和状态，否则会去核对其他领域的步骤，
或从排在首位的领域读取状态。单领域任务不经过这里。
"""
from __future__ import annotations

from sandbox.domains.registry import manifest_for


def task_domain_names(spec) -> list[str]:
    return [entry.name for entry in spec.env.domains] if spec.env.domains else ["mcd"]


def scoped_spec(spec, domain: str):
    """只含本领域步骤的任务视图。未声明领域的步骤属于首个领域。"""
    names = task_domain_names(spec)
    primary = names[0]
    expect = spec.expect
    steps = None if expect.calls is None else [
        step for step in expect.calls if (step.domain or primary) == domain]
    ids = {step.id for step in steps or []}
    reply = expect.reply.model_copy(update={"fromCalls": [ref for ref in expect.reply.fromCalls if ref in ids]})
    state = expect.state
    if state.domains:
        state = state.for_domain(domain)
    elif domain != primary:
        state = type(state)()    # 旧写法的状态期望只属于首个领域
    script = spec.script.model_copy(update={
        "approvals": [rule for rule in spec.script.approvals if rule.call in ids]})
    env = spec.env.model_copy(update={"domains": [entry for entry in spec.env.domains if entry.name == domain]})
    return spec.model_copy(update={
        "env": env, "script": script,
        "expect": expect.model_copy(update={"calls": steps, "reply": reply, "state": state}),
    })


def scoped_evidence(evidence: dict, domain: str, primary: str) -> dict:
    """只含本领域调用、审批和状态的证据视图；对话、探测、Iris 状态保持原样。"""
    tools = manifest_for(domain).tools
    view = dict(evidence)
    view["approvals"] = [row for row in evidence.get("approvals") or [] if row.get("tool") in tools]
    sandbox = evidence.get("sandbox")
    if sandbox:
        world = (sandbox.get("world") or {}).get(domain, {})
        account = sandbox.get("account") or {}
        view["sandbox"] = {
            **sandbox,
            "calls": [call for call in sandbox.get("calls") or [] if call.get("domain", primary) == domain],
            "state": {**world, **((account.get("final") or {}).get(domain) or {})},
            "initialState": {**world, **((account.get("initial") or {}).get(domain) or {})},
        }
    return view
