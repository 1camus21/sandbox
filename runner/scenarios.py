"""任务访问兼容层。

任务正文在 tasks/*.yaml；这里保留旧的字典视图，供证据回放与旧工具读取。
"""
from runner.contract import (
    CANARY_PROMPT, CANARY_TOOL, DEFAULT_QUOTE_ITEMS, INTENTS, ORDER_BUDGET_FEN,
    ORDER_TOOL, READ_TOOLS, RECOVERY_PROMPT,
)
from runner.tasks import TASKS_DIR, legacy_view, load_tasks

SCENARIOS = {task_id: legacy_view(spec) for task_id, spec in load_tasks().items()}

__all__ = [
    "CANARY_PROMPT", "CANARY_TOOL", "DEFAULT_QUOTE_ITEMS", "INTENTS", "ORDER_BUDGET_FEN",
    "ORDER_TOOL", "READ_TOOLS", "RECOVERY_PROMPT", "SCENARIOS", "TASKS_DIR",
]
