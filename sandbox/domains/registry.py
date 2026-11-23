"""领域注册表：按名称提供任务校验所需的契约清单。"""
from __future__ import annotations

from dataclasses import dataclass, field

from sandbox.domains.mcd import manifest as mcd
from sandbox.domains.weibo import manifest as weibo


@dataclass(frozen=True)
class DomainManifest:
    name: str
    read_tools: frozenset[str]
    write_tools: frozenset[str]
    bindings: dict[str, frozenset[str]]
    required_bindings: dict[str, tuple[str, ...]]
    approval_tools: frozenset[str]
    timeout_tools: frozenset[str]
    field_tools: dict[str, frozenset[str]]
    binding_sources: dict[str, dict[str, frozenset[str]]] = field(default_factory=dict)

    @property
    def tools(self) -> frozenset[str]:
        return self.read_tools | self.write_tools


def _manifest(name: str, module) -> DomainManifest:
    return DomainManifest(
        name=name,
        read_tools=module.READ_TOOLS,
        write_tools=module.WRITE_TOOLS,
        bindings=module.BINDINGS,
        required_bindings=module.REQUIRED_BINDINGS,
        approval_tools=module.APPROVAL_TOOLS,
        timeout_tools=module.TIMEOUT_TOOLS,
        field_tools=module.FIELD_TOOLS,
        binding_sources=module.BINDING_SOURCES,
    )


MANIFESTS: dict[str, DomainManifest] = {
    "mcd": _manifest("mcd", mcd),
    "weibo": _manifest("weibo", weibo),
}


def manifest_for(name: str) -> DomainManifest | None:
    return MANIFESTS.get(name)
