"""断言的只读上下文与领域视图。"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class MixView:
    """跨领域检查的视图：只有调用、对话与时间，不暴露领域状态。"""

    calls: list[dict]
    transcript: list[dict]
    clock: str | None
    domains: tuple[str, ...]


@dataclass(frozen=True)
class CheckContext:
    spec: object
    evidence: dict
    quote: dict | None
    clock: str | None
    calls: list[dict]
    transcript: list[dict]
    worlds: dict
    accounts: dict
    initial: dict
    cache: dict = field(default_factory=dict, compare=False)

    def domain(self, name: str) -> "DomainView":
        return DomainView(self, name)

    def mix(self, domains: tuple[str, ...]) -> MixView:
        return MixView(self.calls, self.transcript, self.clock, domains)

    def for_domain(self, name: str) -> "CheckContext":
        """多领域任务中，单领域检查使用的本领域上下文。"""
        from assertions.scope import scoped_evidence, scoped_spec, task_domain_names
        primary = task_domain_names(self.spec)[0]
        return build_context(scoped_evidence(self.evidence, name, primary), self.quote,
                             scoped_spec(self.spec, name))


@dataclass(frozen=True)
class DomainView:
    ctx: CheckContext
    name: str

    @property
    def world(self) -> dict:
        return self.ctx.worlds.get(self.name, {})

    @property
    def account(self) -> dict:
        return self.ctx.accounts.get(self.name, {})

    @property
    def initial(self) -> dict:
        return self.ctx.initial.get(self.name, {})

    @property
    def calls(self) -> list[dict]:
        return [call for call in self.ctx.calls if call.get("domain", self.name) == self.name]

    @property
    def transcript(self) -> list[dict]:
        return self.ctx.transcript


def build_context(evidence: dict, quote: dict | None, spec) -> CheckContext:
    """从证据构造上下文。

    mcd 沿用合并后的单领域状态，保持既有检查不变；其他领域取快照里按领域记录的世界和账户。
    """
    sandbox = evidence.get("sandbox") or {}
    state = sandbox.get("state") or {}
    initial = sandbox.get("initialState") or {}
    worlds, accounts, initials = {"mcd": state}, {"mcd": state}, {"mcd": initial}
    account = sandbox.get("account") or {}
    for name, world in (sandbox.get("world") or {}).items():
        if name == "mcd":
            continue
        worlds[name] = world
        accounts[name] = (account.get("final") or {}).get(name, {})
        initials[name] = (account.get("initial") or {}).get(name, {})
    return CheckContext(
        spec=spec,
        evidence=evidence,
        quote=quote,
        clock=(state.get("clock") or {}).get("now"),
        calls=sandbox.get("calls") or [],
        transcript=evidence.get("transcript") or [],
        worlds=worlds,
        accounts=accounts,
        initial=initials,
    )
