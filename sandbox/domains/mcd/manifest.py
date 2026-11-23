"""mcd 领域的契约清单：工具分类、绑定规则与审批范围。

任务加载期按这份清单校验调用声明；领域实现本身不读取它。
"""

READ_TOOLS = frozenset({
    "delivery-query-addresses", "delivery-query-stores", "query-meals", "query-meal-detail",
    "query-store-coupons", "query-my-coupons", "now-time-info", "calculate-price",
    "query-order", "order-list",
})
WRITE_TOOLS = frozenset({"delivery-create-address", "create-order", "cancel-order"})

BINDINGS = {
    "addressId": frozenset({"delivery-query-addresses"}),
    "storeCode": frozenset({"delivery-query-stores", "query-meals", "calculate-price"}),
    "orderId": frozenset({"create-order"}),
}
REQUIRED_BINDINGS = {
    "create-order": ("addressId", "storeCode"),
    "cancel-order": ("orderId",),
}
BINDING_SOURCES = {
    "create-order": {"storeCode": frozenset({"calculate-price"})},
}
APPROVAL_TOOLS = frozenset({"create-order", "cancel-order"})
TIMEOUT_TOOLS = frozenset({"create-order"})
FIELD_TOOLS = {
    "addressTag": frozenset({"delivery-query-addresses"}),
    "menuProduct": frozenset({"query-meals"}),
}
