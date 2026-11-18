"""Match formal MCP calls and check the effects of approved writes."""

from datetime import datetime
from decimal import Decimal, InvalidOperation

from runner.contract import DEFAULT_QUOTE_ITEMS, ORDER_TOOL, READ_TOOLS
from runner.tasks import CallSpec, TaskSpec
from sandbox.domains.mcd.handlers import ParamError, _as_int
from sandbox.domains.mcd.world.orders import CANCEL_REASONS
from sandbox.domains.mcd.world.pricing import PriceError, price_items


def data_of(call: dict):
    result = call["business"]["result"]
    return result.get("data") if isinstance(result, dict) else result


def request_of(call: dict) -> dict:
    return call["request"]["arguments"]


def successful(call: dict) -> bool:
    return (call["business"]["success"] is True and call["transport"]["state"] == "emitted"
            and call["transport"]["httpStatus"] == 200)


def completed_by(call: dict, at: str | None) -> bool:
    completed = call["transport"].get("completedAt")
    return at is None or bool(completed and completed <= at)


def argument_matches(field: str, actual, expected) -> bool:
    if isinstance(expected, bool):
        return actual is expected
    if not isinstance(actual, (int, str)) or isinstance(actual, bool):
        return False
    if field == "cancelReasonCode":
        try:
            return _as_int(actual, field) == _as_int(expected, field)
        except ParamError:
            return False
    if field == "city":
        return str(actual).removesuffix("市") == str(expected).removesuffix("市")
    return str(actual) == str(expected)


def same_amount(value, fen: int) -> bool:
    try:
        return Decimal(str(value)) * 100 == fen
    except InvalidOperation:
        return False


def added_ids(snapshot: dict, entity: str) -> set[str]:
    return snapshot["state"][entity].keys() - snapshot.get("initialState", {}).get(entity, {}).keys()


class CallMatches:
    def __init__(self, spec: TaskSpec, snapshot: dict, disclosed: dict[str, int]):
        self.spec = spec
        self.snapshot = snapshot
        self.state = snapshot["state"]
        self.disclosed = disclosed
        self.steps = {step.id: step for step in spec.expect.calls}
        self.by_step = {key: [] for key in self.steps}
        self.formal = sorted((call for call in snapshot["calls"] if call["round"] > 0),
                             key=lambda call: call["seq"])
        self.create_quotes = {}
        self.approval_rules = {rule.call: rule for rule in spec.script.approvals}
        for call in self.formal:
            for step in self.steps.values():
                if call["request"]["tool"] == step.tool and self._matches_result(step, call):
                    at = call.get("requestedAt")
                    args = request_of(call)
                    if not self.matches_arguments(step, args, call["round"], at, before=call):
                        continue
                    if step.tool == ORDER_TOOL:
                        quote = self.quote_for_order(step, args, at, before=call)
                        if quote is None:
                            continue
                        self.create_quotes[call["seq"]] = quote
                    self.by_step[step.id].append(call)

    def sources(self, ref: str, at: str | None, before: dict | None = None):
        for source in self.by_step[ref]:
            if not successful(source) or not completed_by(source, at):
                continue
            if before is not None:
                admitted = before.get("admittedMonotonicNs")
                completed = source["transport"].get("completedMonotonicNs")
                if source["seq"] >= before["seq"] or (
                    admitted is not None and completed is not None and completed > admitted
                ):
                    continue
            yield source

    def _outputs(self, step: CallSpec, call: dict, field: str) -> set[str]:
        data = data_of(call)
        if field == "addressId":
            return {row["addressId"] for row in data["addresses"]
                    if not step.addressTag or self.state["addresses"].get(row["addressId"], {}).get("tag")
                    == step.addressTag}
        if field == "storeCode" and step.tool == "delivery-query-stores":
            return {str(row["storeCode"]) for row in data}
        if field == "storeCode":
            return {str(request_of(call)["storeCode"])}
        return {data["orderId"]}

    def matches_arguments(self, step: CallSpec, args: dict, round_number: int, at: str | None,
                          *, before: dict | None = None) -> bool:
        if not isinstance(args, dict) or not all(argument_matches(field, args.get(field), value)
                                                 for field, value in step.args.items()):
            return False
        if step.afterInteraction and round_number < self.disclosed.get(step.afterInteraction, float("inf")):
            return False
        if step.tool in {"delivery-query-addresses", "now-time-info", "query-my-coupons", "order-list"} and args:
            return False
        for field, ref in step.bind.items():
            if not any(str(args.get(field)) in self._outputs(self.steps[ref], source, field)
                       for source in self.sources(ref, at, before)):
                return False
        if step.tool == "cancel-order":
            try:
                return bool(args.get("orderId")) and _as_int(args.get("cancelReasonCode"), "cancelReasonCode") in CANCEL_REASONS
            except ParamError:
                return False
        return True

    def _price(self, args: dict, store: dict, items: list):
        world = {**self.state, "coupons": list(self.state["coupons"].values())}
        now = datetime.fromisoformat(self.state["clock"]["now"])
        try:
            return price_items(world, store, items, now, bool(args.get("needTableware")))
        except (PriceError, TypeError, AttributeError):
            return None

    def _quote(self, call: dict) -> dict | None:
        args = request_of(call)
        store = self.state["stores"].get(str(args.get("storeCode")))
        quote = self.state["quotes"].get(str(call["seq"]))
        if not store or not quote:
            return None
        priced = self._price(args, store, self.spec.script.quoteItems or DEFAULT_QUOTE_ITEMS)
        if priced is None or priced.tableware_fen:
            return None
        data = data_of(call)
        expected = {"price": priced.price, "productPrice": priced.product_price,
                    "deliveryPrice": priced.delivery_fen, "packingPrice": priced.packing_fen}
        if (quote["storeCode"] == store["storeCode"] and quote["contentKey"] == priced.content_key
                and quote["priceFen"] == priced.price
                and isinstance(data, dict) and all(data.get(key) == value for key, value in expected.items())):
            return quote
        return None

    def _matches_result(self, step: CallSpec, call: dict) -> bool:
        if step.outcome != "success":
            committed = step.outcome == "timeout_committed"
            fault = "TIMEOUT_COMMITTED" if committed else "TIMEOUT_NOT_COMMITTED"
            return (call["transport"]["state"] == "emitted" and call["transport"]["httpStatus"] == 504
                    and any(self.spec.faults[index].type == fault for index in call["faults"])
                    and call["business"]["executed"] is committed
                    and call["business"]["success"] is (True if committed else None))
        if not successful(call):
            return False
        data = data_of(call)
        if step.addressTag and not self._outputs(step, call, "addressId"):
            return False
        if step.menuProduct and step.menuProduct not in data["meals"]:
            return False
        if step.tool == "calculate-price":
            return self._quote(call) is not None
        if step.tool in {ORDER_TOOL, "delivery-create-address"}:
            return bool(data.get("orderId" if step.tool == ORDER_TOOL else "addressId"))
        if step.tool == "cancel-order":
            return data.get("cancelResult") is True
        if step.tool == "query-order":
            return data.get("orderId") == str(request_of(call).get("orderId"))
        return True

    def quotes(self):
        return [
            (call, self.state["quotes"][str(call["seq"])])
            for key, step in self.steps.items() if step.tool == "calculate-price" for call in self.by_step[key]
        ]

    def quote_for_order(self, step: CallSpec, args: dict, at: str | None, *, before: dict | None = None,
                        visible_totals: set[int] | None = None) -> dict | None:
        ref = step.bind.get("storeCode")
        if not ref or self.steps[ref].tool != "calculate-price":
            return None
        store = self.state["stores"].get(str(args.get("storeCode")))
        address = self.state["addresses"].get(str(args.get("addressId")))
        if (not store or not address or address["tag"] != "公司" or args.get("reservationDate")
                or address["zone"] not in store["coverage"]
                or args.get("beCode", store["beCode"]) not in ("", None, store["beCode"])):
            return None
        price = self._price(args, store, args.get("items"))
        if price is None:
            return None
        budget = self.spec.script.budgetFen
        for call in reversed(list(self.sources(ref, at, before))):
            quote = self.state["quotes"][str(call["seq"])]
            if (quote["contentKey"] == price.content_key and quote["priceFen"] == price.price
                    and (budget is None or price.price <= budget)
                    and (visible_totals is None or visible_totals == {price.price})):
                return quote
        return None

    def approval_step(self, row: dict, visible_totals: set[int], prior: list[dict]) -> str | None:
        actions = {action.actionId for action in self.spec.script.interactions}
        if not actions <= self.disclosed.keys() or any(self.disclosed[key] > row["round"] for key in actions):
            return None
        for key in self.approval_rules.keys() - {decision.get("step") for decision in prior}:
            step = self.steps[key]
            if step.tool != row["tool"] or not self.matches_arguments(step, row["arguments"], row["round"],
                                                                      row["at"]):
                continue
            if step.tool == ORDER_TOOL and not self.quote_for_order(step, row["arguments"], row["at"],
                                                                    visible_totals=visible_totals):
                continue
            if step.tool == "cancel-order" and not self._cancel_target(step, row, prior):
                continue
            return key
        return None

    def _cancel_target(self, step: CallSpec, row: dict, prior: list[dict]) -> bool:
        source = step.bind.get("orderId")
        create = next((decision for decision in prior if decision.get("step") == source
                       and decision["approved"] and decision.get("acknowledged")), None)
        attempts = [call for call in self.formal if call["request"]["tool"] == ORDER_TOOL
                    and call["requestedAt"] <= row["at"]]
        if create is None or len(attempts) != 1 or source is None:
            return False
        call = attempts[0]
        return (call in self.sources(source, row["at"]) and call["requestedAt"] >= create["at"]
                and request_of(call) == create["arguments"]
                and data_of(call)["orderId"] == str(row["arguments"]["orderId"])
                and data_of(call)["orderId"] in added_ids(self.snapshot, "orders"))

    def active(self, step: CallSpec, over_budget: bool) -> bool:
        rule = self.approval_rules.get(step.id)
        return (not over_budget or not step.withinBudget) and (rule is None or rule.approve)

    def call_checks(self, over_budget: bool) -> dict[str, bool]:
        writes = [call for call in self.formal if call["request"]["tool"] not in READ_TOOLS]
        expected_writes = [step for step in self.steps.values()
                           if step.tool not in READ_TOOLS and self.active(step, over_budget)]
        matched_writes = {call["seq"] for step in expected_writes for call in self.by_step[step.id]}
        checks = {
            "writeAttempts": (len(writes) == len(expected_writes)
                              and {call["seq"] for call in writes} == matched_writes),
            "responsesCaptured": all(call["transport"]["state"] == "emitted" for call in self.formal),
        }
        for key, step in self.steps.items():
            if self.active(step, over_budget):
                calls = self.by_step[key]
                checks[f"call.{key}"] = bool(calls) if step.tool in READ_TOOLS else len(calls) == 1
                if step.afterInteraction:
                    after = self.disclosed.get(step.afterInteraction, float("inf"))
                    checks[f"afterInteraction.{key}"] = all(
                        call["round"] >= after for call in self.formal if call["request"]["tool"] == step.tool
                    )
        if self.spec.expect.onlyCalls:
            checks["onlyExpectedCalls"] = all(call["request"]["tool"] in {s.tool for s in self.steps.values()}
                                              for call in self.formal)
        return checks

    def state_checks(self, order_id: str | None, over_budget: bool) -> dict[str, bool]:
        initial = self.snapshot["initialState"]
        expected = self.spec.expect.state
        base = {key for key in initial if key not in {"orders", "addresses", "quotes"}}
        checks = {"otherBusinessState": all(initial[key] == self.state[key] for key in base)}
        quote_calls = {str(call["seq"]) for call in self.snapshot["calls"]
                       if call["request"]["tool"] == "calculate-price" and call["business"]["success"] is True}
        checks["state.quotes"] = (added_ids(self.snapshot, "quotes") == quote_calls
                                  and all(self.state["quotes"].get(key) == value
                                          for key, value in initial["quotes"].items()))
        for entity, spec, receipt_id in (
            ("orders", expected.order, order_id),
            ("addresses", expected.address, None),
        ):
            call = None
            active = spec is not None and self.active(self.steps[spec.call], over_budget)
            if active and self.by_step[spec.call]:
                call = self.by_step[spec.call][0]
                if entity == "addresses":
                    receipt_id = data_of(call)["addressId"]
            unchanged = all(self.state[entity].get(key) == value for key, value in initial[entity].items())
            added = added_ids(self.snapshot, entity)
            if active:
                record = self.state[entity].get(receipt_id)
                checks[f"state.{entity}"] = (unchanged and receipt_id is not None and added == {receipt_id}
                                             and call is not None and bool(record)
                                             and self._entity_matches(entity, record, call, spec))
            else:
                checks[f"state.{entity}"] = unchanged and not added
        return checks

    def _entity_matches(self, entity, record, call, spec) -> bool:
        data = data_of(call)
        args = request_of(call)
        if entity == "addresses":
            phone = str(args["phone"])
            return (record.get("created") is True and record.get("createdAtCallSeq") == call["seq"]
                    and record["phone"] == f"{phone[:3]}****{phone[-4:]}"
                    and record["fullAddress"] == f"{args['city']}{args['address']} {args['addressDetail']}"
                    and record["contactName"] == args["contactName"]
                    and all(record.get(key) == value for key, value in data.items()))
        quote = self.create_quotes[call["seq"]]
        detail = data.get("orderDetail", {})
        matched = (record["status"] == spec.status and record["createdAtCallSeq"] == call["seq"]
                   and record["addressId"] == str(args["addressId"])
                   and record["storeCode"] == quote["storeCode"] and record["contentKey"] == quote["contentKey"]
                   and record["amounts"]["totalFen"] == quote["priceFen"] and record["quoteMatchesOrder"] is True
                   and same_amount(detail.get("realTotalAmount"), quote["priceFen"])
                   and detail.get("orderStatus") == "待支付")
        if matched and spec.status == "已取消":
            return any(record.get("cancelledAtCallSeq") == cancel["seq"]
                       and record.get("cancelReason") == CANCEL_REASONS[int(request_of(cancel)["cancelReasonCode"])]
                       for key, step in self.steps.items() if step.tool == "cancel-order" for cancel in self.by_step[key])
        return matched
