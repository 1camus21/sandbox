"""Runner 侧的领域配置。

sandbox/domains 描述“商家怎样响应工具调用”，sandbox/domains/registry 的清单描述“任务怎样声明调用”；
这里描述 Runner 与具体商家有关的部分：怎样让 Iris 连上这一局、探测时说什么、写操作遇到故障后怎样追问。
主流程按任务涉及的领域取配置，不再直接引用某个商家的常量。
"""
from __future__ import annotations

from pathlib import Path
from typing import Protocol


class RunnerDomain(Protocol):
    name: str
    read_tools: frozenset[str]    # 探测阶段允许出现的工具，取自沙箱的领域清单
    canary_tool: str              # 探测必须成功调用的只读工具
    canary_prompt: str
    recovery_prompt: str          # 写操作遇到故障后的核实追问
    token_hint: str               # 直接模式缺少令牌时提示用户设置什么

    def connect_iris(self, sandbox_url: str, episode_id: str, token: str, workdir: Path,
                     *, scoped: bool = False) -> dict[str, str]:
        """为一个全新的 Iris 进程准备连接这一局所需的环境变量，必要时在 workdir 里写文件。

        scoped 为真时使用 /mcp/<局>/<领域>/<代次>，Iris 的这个助手只看到本领域的工具；
        单领域任务保持 /mcp/<局>/<代次>。
        """
        ...

    def direct_token(self) -> str | None:
        """直接模式（连接已在运行的 Iris）下这一局使用的 MCP 令牌，没有配置时返回 None。"""
        ...


def task_domains(task) -> list[str]:
    """任务涉及的领域，按声明顺序；没有声明 env.domains 的旧任务只涉及 mcd。"""
    entries = task.env.domains
    return [entry.name for entry in entries] if entries else ["mcd"]


from runner.domains.mcd import McDelivery  # noqa: E402
from runner.domains.weibo import Weibo  # noqa: E402

DOMAINS: dict[str, RunnerDomain] = {domain.name: domain for domain in (McDelivery(), Weibo())}


def get_domain(name: str) -> RunnerDomain:
    try:
        return DOMAINS[name]
    except KeyError:
        raise ValueError(f"unknown domain: {name}") from None
