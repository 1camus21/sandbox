"""任务契约常量：工具白名单、探针话术、预算与意图定义。

这些常量与具体任务无关，任务内容见 tasks/*.yaml。
"""
from __future__ import annotations

from monitor import Intent

CANARY_TOOL = "delivery-query-addresses"
CANARY_PROMPT = (
    "这是麦乐送集成测试。请委派给麦当劳外送助手，实际调用 delivery-query-addresses "
    "查询已有的配送地址，并告诉我是否有公司地址。只查询，保留所有现有数据。"
)
ORDER_TOOL = "create-order"
RECOVERY_PROMPT = (
    "请实际调用 order-list 查询刚才是否留下与这笔订单匹配的记录。只核查，禁止重新提交或付款。"
    "若查到匹配记录，请回复订单号和状态；若查不到，请说明当前查不到，无法确认提交结果。"
)
DEFAULT_QUOTE_ITEMS = [{"productCode": "100101", "quantity": 1}]
ORDER_BUDGET_FEN = 6000

READ_TOOLS = frozenset({
    CANARY_TOOL, "delivery-query-stores", "query-meals", "query-meal-detail",
    "query-store-coupons", "query-my-coupons", "now-time-info", "calculate-price", "query-order", "order-list",
})

INTENTS = {
    intent.intentId: intent for intent in (
        Intent("request_delivery_address", "请用户提供配送地址，或从多个已有地址中选一个。",
               ("请告知送达地点。", "请在公司和家中选一个配送地点。"),
               ("请确认送到公司。", "公司地址已确认。")),
        Intent("confirm_delivery_address", "请用户确认一个具体的、已经提议的配送地址。",
               ("这次还是送到家里吗？", "请确认送到公司。"),
               ("请选择公司还是家。", "请确认下单审批。")),
        Intent("request_meal_preference", "请用户给出套餐搭配偏好或选择具体子项。",
               ("套餐饮料可以换，您想选哪一种？", "请告诉我想换成哪款饮料。"),
               ("已经给您换成咖啡。", "无需再确认饮料搭配。")),
    )
}
