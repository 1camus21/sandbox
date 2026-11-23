"""任务集自带检查的加载用例。"""
from assertions import registry
from assertions.load import load_checks

SOURCE = """
from assertions.types import check


@check(id="s0.only_addresses", version=1, domains=("mcd",))
def only_addresses(ctx):
    return True
"""


def test_load_checks_registers_decorated_checks(tmp_path):
    module_path = tmp_path / "checks.py"
    module_path.write_text(SOURCE, encoding="utf-8")

    count = load_checks(module_path)

    assert count == 1
    assert registry.resolve("s0.only_addresses").spec.id == "s0.only_addresses"


def test_missing_checks_module_is_ignored(tmp_path):
    assert load_checks(tmp_path / "missing.py") == 0
