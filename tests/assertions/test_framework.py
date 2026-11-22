"""断言框架的单元用例。"""
import pytest

from assertions import compose, registry
from assertions.types import Result, check
from runner.tasks import TaskSpec


def task(checks=None) -> TaskSpec:
    payload = {"taskId": "t", "script": {"prompt": "p"}}
    if checks is not None:
        payload["expect"] = {"checks": checks}
    return TaskSpec.model_validate(payload)


def test_check_decorator_normalizes_bool():
    @check(id="test.pass", version=1)
    def passed(ctx):
        return True

    @check(id="test.fail", version=1)
    def failed(ctx):
        return Result(False, "原因")

    assert passed.evaluate(None).ok is True
    result = failed.evaluate(None)
    assert result.ok is False and result.detail == "原因"


def test_registry_resolves_version_and_rejects_unknown():
    registry.discover()
    assert registry.resolve("mcd.standard").spec.id == "mcd.standard"
    with pytest.raises(LookupError):
        registry.resolve("missing.check")


def test_composition_semantics():
    ok = Result(True)
    bad = Result(False, "x")

    class Fixed:
        def __init__(self, result):
            self.result = result
            self.spec = type("S", (), {"id": "x"})()

        def evaluate(self, ctx):
            return self.result

    assert compose.All([Fixed(ok), Fixed(ok)]).evaluate(None).ok is True
    assert compose.All([Fixed(ok), Fixed(bad)]).evaluate(None).ok is False
    assert compose.Any([Fixed(bad), Fixed(ok)]).evaluate(None).ok is True
    assert compose.Any([Fixed(bad), Fixed(bad)]).evaluate(None).ok is False
    assert compose.Optional([Fixed(bad)]).evaluate(None).ok is True


def test_expand_defaults_to_domain_pack():
    registry.discover()
    resolved = []

    def resolver(name):
        resolved.append(name)
        return registry.resolve(name)

    tree = compose.expand(task(), resolver)
    assert resolved == ["mcd.standard"]
    assert tree.checks[0].spec.id == "mcd.standard"


def test_expand_groups_all_any_optional():
    registry.discover()
    spec = task({"all": ["reported_order"], "any": ["budget_exceeded"], "optional": ["recovery"]})
    tree = compose.expand(spec, registry.resolve)
    assert [type(node).__name__ for node in tree.checks] == ["_Check", "_Check", "Any", "Optional"]
    assert tree.checks[0].spec.id == "mcd.standard"
    assert tree.checks[1].spec.id == "reported_order"
    assert [node.spec.id for node in tree.checks[2].checks] == ["budget_exceeded"]
    assert [node.spec.id for node in tree.checks[3].checks] == ["recovery"]


def test_validate_rejects_check_domains_not_declared():
    from assertions.validate import validate_checks

    mixed = check(id="test.mixed", version=1, domains=("mcd", "echo"))(lambda ctx: True)
    registry.register(mixed)
    only_mcd = TaskSpec.model_validate({
        "taskId": "t", "script": {"prompt": "p"},
        "expect": {"checks": {"all": ["test.mixed"]}},
    })
    with pytest.raises(ValueError, match="requires domains: echo"):
        validate_checks(only_mcd)
    both = TaskSpec.model_validate({
        "taskId": "t", "script": {"prompt": "p"},
        "env": {"domains": [{"name": "mcd"}, {"name": "echo"}]},
        "expect": {"checks": {"all": ["test.mixed"]}},
    })
    validate_checks(both)
