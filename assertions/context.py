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
    """从证据构造上下文；当前证据是单领域结构，统一归到 mcd 视图。"""
    sandbox = evidence.get("sandbox") or {}
    state = sandbox.get("state") or {}
    initial = sandbox.get("initialState") or {}
    return CheckContext(
        spec=spec,
        evidence=evidence,
        quote=quote,
        clock=(state.get("clock") or {}).get("now"),
        calls=sandbox.get("calls") or [],
        transcript=evidence.get("transcript") or [],
        worlds={"mcd": state},
        accounts={"mcd": state},
        initial={"mcd": initial},
    )
