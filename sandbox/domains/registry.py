"""领域注册表：按名称提供任务校验所需的契约清单。"""
from __future__ import annotations

from dataclasses import dataclass, field

from sandbox.domains.mcd.manifest import (
    APPROVAL_TOOLS, BINDING_SOURCES, BINDINGS, FIELD_TOOLS, READ_TOOLS,
    REQUIRED_BINDINGS, TIMEOUT_TOOLS, WRITE_TOOLS,
)


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


MANIFESTS: dict[str, DomainManifest] = {
    "mcd": DomainManifest(
        name="mcd",
        read_tools=READ_TOOLS,
        write_tools=WRITE_TOOLS,
        bindings=BINDINGS,
        required_bindings=REQUIRED_BINDINGS,
        approval_tools=APPROVAL_TOOLS,
        timeout_tools=TIMEOUT_TOOLS,
        field_tools=FIELD_TOOLS,
        binding_sources=BINDING_SOURCES,
    ),
}


def manifest_for(name: str) -> DomainManifest | None:
    return MANIFESTS.get(name)
