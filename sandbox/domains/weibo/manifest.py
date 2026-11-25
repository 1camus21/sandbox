"""weibo 领域的契约清单：工具分类、绑定规则与审批范围。

任务加载期按这份清单校验调用声明；领域实现本身不读取它。
"""

READ_TOOLS = frozenset({
    "search_hot_word_biz", "search_hot_word_cate_biz", "search_statuses_limited", "users_show_biz",
    "statuses_user_timeline_biz", "statuses_mentions_biz", "comments_show_biz",
})
WRITE_TOOLS = frozenset({
    "statuses_update", "statuses_repost", "statuses_destroy",
    "comments_create", "comments_reply", "attitudes_create",
})

# 参数 → 可以提供它的工具。id 可来自发布、转发的返回，支持“发完再删”这类任务。
BINDINGS = {
    "id": frozenset({"search_statuses_limited", "statuses_user_timeline_biz", "statuses_mentions_biz",
                     "statuses_update", "statuses_repost"}),
    "cid": frozenset({"comments_show_biz"}),
}
# 写操作的目标必须来自前面的工具返回，不能凭空写一个编号。
REQUIRED_BINDINGS = {
    "statuses_repost": ("id",),
    "statuses_destroy": ("id",),
    "comments_create": ("id",),
    "comments_reply": ("id", "cid"),
    "attitudes_create": ("id",),
}
BINDING_SOURCES: dict[str, dict[str, frozenset[str]]] = {}
APPROVAL_TOOLS = WRITE_TOOLS
# 超时结果（timeout_committed / timeout_not_committed）的判定需要联合核对传输、注入的故障和业务执行结果，
# 微博尚未实现；在实现之前，任务加载时拒绝对微博工具声明超时结果，避免一条普通成功记录同时匹配两种结果。
TIMEOUT_TOOLS: frozenset[str] = frozenset()
FIELD_TOOLS: dict[str, frozenset[str]] = {}
