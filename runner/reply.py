"""Compare user-visible facts with McDelivery tool output."""

from decimal import Decimal
import re
import unicodedata

TIME = re.compile(r"(?<![\d+:])(?P<hour>[01]?\d|2[0-3])[:点时]"
                  r"(?P<minute>[0-5]\d|[0-9](?=分))?"
                  r"(?:[:分](?P<second>[0-5]\d)秒?)?(?!\d)")
MONEY = re.compile(r"(?:[¥￥]\s*(\d+(?:\.\d{1,2})?)|(?<![\d.])(\d+(?:\.\d{1,2})?)\s*(?:元|块))")
LONG_NUMBER = re.compile(r"(?<!\d)[0-9]{20,32}(?!\d)")
ADDRESS_QUESTION = re.compile(
    r"(?:请(?:您)?|需要您|麻烦(?:您)?|能否|是否|要不要|让我知道).{0,14}"
    r"(?:选择|选一个|确认|提供|告诉|告知|补充).{0,20}(?:地址|地点|送到|送往|配送到|公司还是家)"
    r"|(?:地址|地点).{0,12}(?:请(?:您)?|需要您).{0,8}(?:选|确认|提供)"
    r"|(?:送到|送往|配送到).{0,20}(?:可以吗|对吗|好吗|吗[?？。]?$|哪个[?？。]?$)"
    r"|(?:请选择|请确认).{0,14}(?:公司或家|公司还是家|家还是公司)"
    r"|(?:地址|地点|送到|配送到|送往).{0,8}(?:哪里|哪儿|哪一个|哪个)[?？。]?"
)
NO_PENDING = re.compile(r"(?:没有|暂无|无|未发现|不存在|未查到|无任何)\s*(?:任何|尚)?(?:进行中|待支付|待付款|未支付)")
YES_PENDING = re.compile(r"(?:有|存在|查到|找到|列出|包含)(?:\s*(?:[一两二三四五六七八九十\d]+\s*笔|一笔))?\s*(?:进行中|待支付|待付款|未支付)")
EMPTY = re.compile(r"(?:暂无|没有|无|未查到|未找到|未领取|未查询到)(?:任何|可用|有效|已有|历史|最近|过|新的|的|配送|进行中的)*"
                   r"(?:优惠券|券|地址|订单|记录|门店)|(?:列表|记录)(?:为|是)?空|\[\s*\]"
                   r"|[:：]\s*0|\b0\s*[条张笔家个]")


def plain(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)
    return re.sub(r"(?<![\d*])\*{2,}|\*{2,}(?![\d*])|[`_]", "", text)


def compact(text: str) -> str:
    return re.sub(r"\s+", "", plain(text))


def amounts(text: str, *, bare: bool = False) -> set[int]:
    values = {int(Decimal(currency or yuan) * 100) for currency, yuan in MONEY.findall(text)}
    if not values and bare:
        values = {int(Decimal(value) * 100) for value in re.findall(r"(?<![\d.#])\d+(?:\.\d{1,2})?(?![\d.])", text)}
    return values


def times(text: str, *, hour_only=False) -> set[tuple[int, int, int]]:
    text = re.sub(r"(?:(?:GMT|UTC)\s*[+-]|\+)(?:0\d|1[0-4]):[0-5]\d(?=\b|$)", "", plain(text),
                  flags=re.IGNORECASE)
    found = set()
    for match in TIME.finditer(text):
        hour, minute, second = match.group("hour", "minute", "second")
        separator = text[match.start() + len(hour)]
        if minute is None and (separator == ":" or not hour_only):
            continue
        daypart = text[max(0, match.start() - 4):match.start()]
        if re.search(r"下午|晚上|傍晚", daypart) and 1 <= int(hour) < 12:
            hour = str(int(hour) + 12)
        found.add((int(hour), int(minute or 0), int(second or 0)))
    return found


def current_time(data, reply) -> bool:
    actual = times(reply, hour_only=True)
    expected = times(data["formatted"])
    if actual != expected:
        return False
    dates = re.findall(r"(\d{4})[-年](\d{1,2})[-月](\d{1,2})(?:日)?", reply)
    return not dates or {tuple(map(int, date)) for date in dates} == {tuple(map(int, data["date"].split("-")))}


def address_list(data, state, reply) -> bool:
    addresses = data["addresses"]
    if not addresses:
        return bool(EMPTY.search(reply))
    labels = {state["addresses"][row["addressId"]]["tag"] for row in addresses}
    labels.discard(None)
    numbers = {"零": 0, "一": 1, "两": 2, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6,
               "七": 7, "八": 8, "九": 9, "十": 10}
    counts = {
        numbers.get(value, int(value) if value.isdigit() else None)
        for value in re.findall(r"(?:共(?:计|有)?|共有|总共|有|查到|查询到|找到)\s*(\d+|[零一两二三四五六七八九十])\s*[个条处笔]", reply)
    }
    counts.update(int(value) for value in re.findall(r"(?:地址数量|地址数|配送地址)\s*[:：]\s*(\d+)\s*[个条处]?", reply))
    if not counts:
        counts.update(numbers.get(value, int(value) if value.isdigit() else None)
                      for value in re.findall(r"(\d+|[零一两二三四五六七八九十])\s*[个条处]\s*(?:已有|配送|收货)?地址", reply))
    return counts == {len(addresses)} and all(label in reply for label in labels)


def tagged_segments(text: str, names: list[tuple[str, ...]]) -> list[list[str]]:
    """A named row or clause owns its amounts and associated fields."""
    patterns = [re.compile("|".join(re.escape(name) for name in sorted(aliases, key=len, reverse=True)))
                for aliases in names]
    segments = [[] for _ in patterns]
    lines = re.split(r"[\n;；。]", text)
    for line in lines:
        positions = sorted((match.start(), match.end(), index) for index, pattern in enumerate(patterns)
                           for match in pattern.finditer(line))
        for index, (start, end, group) in enumerate(positions):
            # In a table or labelled list the name usually comes first. Keep left-hand cells as well
            # when this is the only named entry on its row.
            left = 0 if len(positions) == 1 else start
            right = positions[index + 1][0] if index + 1 < len(positions) else len(line)
            segments[group].append(line[left:right])
    return segments


def store_list(data, reply) -> bool:
    if not data:
        return bool(EMPTY.search(reply))
    names = []
    for store in data:
        name = compact(store["storeName"])
        local = name.removeprefix("麦当劳").removesuffix("(模拟)").removesuffix("餐厅")
        names.append((name, local))
    segments = tagged_segments(compact_lines(reply), names)
    return all(any(times(segment, hour_only=True) == times(f"{store['businessStartTime']}~{store['businessEndTime']}")
                   for segment in lines) for store, lines in zip(data, segments))


def compact_lines(text: str) -> str:
    return re.sub(r"[^\S\n]+", "", text)


def menu_prices(data, reply) -> bool:
    codes = {item["code"] for group in data["categories"] if "套餐" in group["name"] for item in group["meals"]}
    meals = [data["meals"][code] for code in data["meals"] if code in codes]
    if not meals:
        return bool(EMPTY.search(reply))
    groups = tagged_segments(reply, [(meal["name"],) for meal in meals])
    return all({value for line in lines for value in amounts(line.replace(meal["name"], ""), bare=True)}
               == {int(Decimal(meal["currentPrice"]) * 100)}
               for meal, lines in zip(meals, groups))


def meal_detail(data, reply) -> bool:
    subject = data["name"]
    # The name of the set alone does not list the burger as a component.
    content = compact(reply).replace(compact(subject), "")
    choices = [choice for group in data["rounds"] for choice in group["choices"] if choice["quantity"]]
    for choice in choices:
        name = compact(choice["name"])
        alternatives = {name}
        if name == "中薯条":
            alternatives.update(("中份薯条", "薯条(中)"))
        elif name == "中杯可口可乐":
            alternatives.update(("中杯可乐", "可口可乐(中杯)", "可乐(中杯)"))
        if not any(alias in content for alias in alternatives):
            return False
    return bool(choices)


def store_coupons(data, reply) -> bool:
    if not data:
        return bool(EMPTY.search(reply))
    if re.search(r"已(?:经)?(?:过期|失效|(?:超过|超出)有效(?:期|时间))", reply):
        return False
    reply = re.sub(r"\n[ \t]*(?:[-*]\s*)?(?=适用(?:商品|餐品|范围)?[:：])", " ", reply)
    groups = tagged_segments(reply, [(coupon["title"],) for coupon in data])
    return all(any(all(product["productName"] in line.replace(coupon["title"], "", 1)
                       for product in coupon["products"]) for line in lines)
               for coupon, lines in zip(data, groups))


def my_coupons(markdown, reply) -> bool:
    sections = re.findall(r"^## (.+)\n([\s\S]*?)(?=^## |\Z)", plain(markdown), re.MULTILINE)
    if not sections:
        return bool(EMPTY.search(reply))
    groups = tagged_segments(reply, [(title,) for title, _ in sections])
    return all({amount for line in lines for amount in amounts(line)}
               == amounts(next(line for line in body.splitlines() if "优惠" in line))
               for (_, body), lines in zip(sections, groups))


def asks_for_address(reply: str) -> bool:
    text = re.sub(r"确认(?:是否)?(?:下单|审批|订单|支付|付款)", "", plain(reply))
    return bool(ADDRESS_QUESTION.search(text))


def order_statuses(reply: str) -> set[str]:
    states = {
        "待支付": r"待支付|待付款|等待(?:支付|付款)|状态[\s:：为]*(?:尚)?未(?:付款|支付)",
        "已取消": r"已(?:经)?取消|取消成功|成功取消",
        "已支付": r"(?:已(?:经)?|成功)(?:付款|支付)|(?:付款|支付)成功",
        "配送中": r"配送中|正在配送|已送达",
        "取消失败": r"(?:未|不能|无法)取消|取消失败|取消.{0,5}未成功",
    }
    return {status for status, pattern in states.items() for match in re.finditer(pattern, reply)
            if not re.match(r"\s*(?:后|时(?:会|将|再))", reply[match.end():])
            and not re.search(r"(?:如果|若|如需|是否)[^，。；\n]{0,8}$", reply[:match.start()])}


def order_list(data, reply) -> bool:
    orders = data["list"]
    ids = set(LONG_NUMBER.findall(reply))
    if not orders:
        return not ids and bool(EMPTY.search(reply)) and not YES_PENDING.search(NO_PENDING.sub("", reply))
    pending = any(order["orderStatus"] in {"待支付", "进行中", "待付款"} for order in orders)
    if ids != {order["orderId"] for order in orders}:
        return False
    return (bool(YES_PENDING.search(reply) or "待支付" in order_statuses(reply)) and not NO_PENDING.search(reply)
            if pending else bool(NO_PENDING.search(reply)))


def created_address(data, args, reply) -> bool:
    ids = set(LONG_NUMBER.findall(reply))
    added = re.search(
        r"(?:已(?:经)?|成功)(?:为您|帮您)?(?:成功)?(?:新增|添加|保存|创建)|(?:新增|添加|保存|创建)(?:地址)?成功",
        reply,
    )
    submitted = re.search(r"(?:新增|添加|保存|创建)(?:配送)?地址.{0,6}已提交|(?:新增|添加)(?:配送)?地址请求已提交"
                          r"|(?:配送)?地址(?:新增|添加|保存|创建)请求已提交"
                          r"|已(?:经)?提交.{0,8}(?:新增|添加)(?:配送)?地址", reply)
    if ((not added and not (submitted and ids == {data["addressId"]}))
            or re.search(r"(?:未|无法)(?:能|成功)?(?:新增|添加|保存|创建)|(?:新增|添加|保存|创建)(?:地址)?失败", reply)):
        return False
    stated_contact = re.findall(r"(?:联系人|收件人|收货人)\s*[:：为]\s*([\u4e00-\u9fff]{2,4})(?=[，。；、\s]|$)", reply)
    full_address = compact(data["fullAddress"]).replace("深圳市", "深圳").replace("南山区", "南山")
    details = re.findall(r"(?:地址(?:详情)?|送达地点)\s*[:：为]\s*([^，。；\n]+)", reply)
    for detail in details:
        provided = compact(detail).replace("深圳市", "深圳").replace("南山区", "南山")
        if re.search(r"[区路园号座栋室0-9]", provided) and provided.rstrip("|") not in full_address:
            return False
    phones = set(re.findall(r"(?<!\d)1\d{2}(?:[\d*xX•·]{4})\d{4}(?!\d)", reply))
    return ((not ids or ids == {data["addressId"]})
            and all(contact == data["contactName"] for contact in stated_contact)
            and all(phone[:3] == data["phone"][:3] and phone[-4:] == data["phone"][-4:]
                    and (not phone.isdigit() or phone == str(args["phone"])) for phone in phones))


def facts_match(tool: str, data, args, state, reply: str) -> bool:
    reply = plain(reply)
    if tool == "now-time-info":
        return current_time(data, reply)
    if tool == "delivery-query-addresses":
        return address_list(data, state, reply)
    if tool == "delivery-query-stores":
        return store_list(data, reply)
    if tool == "query-meals":
        return menu_prices(data, reply)
    if tool == "query-meal-detail":
        return meal_detail(data, reply)
    if tool == "query-store-coupons":
        return store_coupons(data, reply)
    if tool == "query-my-coupons":
        return my_coupons(data, reply)
    if tool == "order-list":
        return order_list(data, reply)
    if tool == "delivery-create-address":
        # Keep masked digits for matching; ordinary Markdown emphasis is removed by the phone pattern.
        return created_address(data, args, reply)
    raise ValueError(f"No reply facts for {tool}")
