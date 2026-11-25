"""Runner 的领域配置：麦乐送行为不变，微博通过授权文件接入，多领域任务逐个领域探测。"""
import json
from pathlib import Path

import pytest

from assertions.preflight import preflight_retained, probe_succeeded
from runner.domains import DISABLED_URL, DOMAINS, get_domain, iris_connection, task_domains
from runner.domains import weibo_cli
from runner.domains.weibo import FAR_FUTURE, read_token, write_credentials
from runner.tasks import TaskSpec

SANDBOX = "http://127.0.0.1:8100"
EPISODE = "a" * 32
TOKEN = "b" * 48


def spec(domains=None):
    env = {"domains": [{"name": name, "scene": scene} for name, scene in domains]} if domains else {}
    return TaskSpec.model_validate({"taskId": "x", "env": env, "script": {"prompt": "x"}})


# ---- 注册表和清单 ----

def test_registry_matches_sandbox_manifests():
    from sandbox.domains.registry import MANIFESTS
    assert set(DOMAINS) == set(MANIFESTS)
    for name, domain in DOMAINS.items():
        assert domain.read_tools == MANIFESTS[name].read_tools
        assert domain.canary_tool in domain.read_tools
    with pytest.raises(ValueError):
        get_domain("taxi")


def test_mcd_read_tools_match_the_runner_contract():
    from runner.contract import CANARY_PROMPT, CANARY_TOOL, READ_TOOLS
    mcd = get_domain("mcd")
    assert (mcd.read_tools, mcd.canary_tool, mcd.canary_prompt) == (READ_TOOLS, CANARY_TOOL, CANARY_PROMPT)


def test_task_domains():
    assert task_domains(spec()) == ["mcd"]
    assert task_domains(spec([("weibo", "daily")])) == ["weibo"]
    assert task_domains(spec([("mcd", "lunch"), ("weibo", "daily")])) == ["mcd", "weibo"]


# ---- 连接 Iris ----

def test_mcd_connection_is_unchanged(tmp_path):
    env = get_domain("mcd").connect_iris(SANDBOX, EPISODE, TOKEN, tmp_path)
    assert env == {"MCD_MCP_URL": f"{SANDBOX}/mcp/{EPISODE}/1", "MCD_MCP_TOKEN": TOKEN}
    assert not list(tmp_path.iterdir())


def test_weibo_connection_writes_credentials(tmp_path):
    env = get_domain("weibo").connect_iris(SANDBOX, EPISODE, TOKEN, tmp_path)
    path = tmp_path / ".weibo" / "auth.json"
    assert env == {"WEIBO_MCP_URL": f"{SANDBOX}/mcp/{EPISODE}/1", "WEIBO_CREDENTIALS_FILE": str(path)}
    assert json.loads(path.read_text(encoding="utf-8")) == {
        "access_token": TOKEN, "refresh_token": "", "expires_at": FAR_FUTURE,
        "client_id": "sandbox", "token_endpoint": f"{SANDBOX}/oauth/token",
    }


def test_multi_domain_connections_use_domain_endpoints(tmp_path):
    env = {}
    for name in ("mcd", "weibo"):
        env.update(get_domain(name).connect_iris(SANDBOX, EPISODE, TOKEN, tmp_path, scoped=True))
    assert env["MCD_MCP_URL"] == f"{SANDBOX}/mcp/{EPISODE}/mcd/1"
    assert env["WEIBO_MCP_URL"] == f"{SANDBOX}/mcp/{EPISODE}/weibo/1"
    assert env["MCD_MCP_TOKEN"] == read_token(env["WEIBO_CREDENTIALS_FILE"]) == TOKEN


def test_direct_tokens(monkeypatch, tmp_path):
    monkeypatch.setenv("MCD_MCP_TOKEN", "mcd-token")
    assert get_domain("mcd").direct_token() == "mcd-token"
    monkeypatch.delenv("WEIBO_CREDENTIALS_FILE", raising=False)
    assert get_domain("weibo").direct_token() is None
    path = write_credentials(tmp_path / "auth.json", TOKEN, SANDBOX)
    monkeypatch.setenv("WEIBO_CREDENTIALS_FILE", str(path))
    assert get_domain("weibo").direct_token() == TOKEN
    assert read_token(tmp_path / "missing.json") is None


def test_prepare_command_writes_a_fresh_token(tmp_path, capsys):
    path = tmp_path / "auth.json"
    assert weibo_cli.main(["--credentials", str(path)]) == 0
    first = read_token(path)
    assert weibo_cli.main(["--credentials", str(path)]) == 0
    assert read_token(path) not in {None, first}
    out = capsys.readouterr().out
    assert "SANDBOX_EPISODE_ID=" in out and f"WEIBO_MCP_URL={SANDBOX}/mcp\n" in out


# ---- 探测 ----

def event(request_id, tool, round_number=0):
    return {"requestId": request_id, "generation": 1, "runId": "run", "round": round_number,
            "request": {"tool": tool, "arguments": {}}, "business": {"success": True},
            "transport": {"state": "emitted", "httpStatus": 200}}


def evidence(calls):
    return {"runId": "run",
            "preflight": {"generation": 1, "requestIds": [c["requestId"] for c in calls if c["round"] == 0]},
            "sandbox": {"closed": True, "runId": "run", "generation": 1, "calls": calls}}


MCD_PROBE = event("r1", "delivery-query-addresses")
WEIBO_PROBE = event("r2", "search_statuses_limited")


def test_single_domain_probes_are_unchanged():
    assert preflight_retained(evidence([MCD_PROBE])) is True
    assert preflight_retained(evidence([MCD_PROBE, WEIBO_PROBE])) is False    # 麦乐送任务里出现了微博工具
    assert preflight_retained(evidence([WEIBO_PROBE]), ["weibo"]) is True


def test_multi_domain_probe_needs_every_domain():
    assert preflight_retained(evidence([MCD_PROBE, WEIBO_PROBE]), ["mcd", "weibo"]) is True
    assert preflight_retained(evidence([MCD_PROBE]), ["mcd", "weibo"]) is False
    assert probe_succeeded([MCD_PROBE, event("r3", "statuses_update")], ["mcd", "weibo"]) is False  # 探测期间有写操作


def test_mcd_wrapper_keeps_its_import_path():
    from runner.verdict import preflight_retained as legacy
    assert legacy(evidence([MCD_PROBE])) is True
    assert legacy(evidence([WEIBO_PROBE])) is False


# ---- 环境标识与回归任务 ----

def test_env_id_names_each_domain():
    from runner.episode import env_id
    from runner.tasks import TASKS_DIR, load_tasks
    assert env_id(load_tasks()["order"], 0) == "lunch:alice:0"     # 旧任务不变
    weibo = load_tasks(TASKS_DIR / "20261124T0900")["s0-wb-me"]
    assert env_id(weibo, 0) == "weibo:daily:alice:0"
    both = spec([("mcd", "lunch"), ("weibo", "daily")])
    assert env_id(both, 3) == "mcd:lunch:alice+weibo:daily:alice:3"
    inline = TaskSpec.model_validate({"taskId": "x", "script": {"prompt": "x"}, "env": {"domains": [
        {"name": "weibo", "worldInline": {"users": []}, "account": "bob"}]}})
    assert env_id(inline, 0) == "weibo:inline:bob:0"


def test_regression_reads_task_set_directories():
    from runner.regression import Case, suite_tasks
    from runner.tasks import TASKS_DIR
    tasks = suite_tasks(TASKS_DIR / "20261124T0900")
    assert {"s0-wb-me", "s0-wb-comments"} <= set(tasks)
    case = Case(1, "s0-wb-me", tasks["s0-wb-me"])
    assert case.spec.env.domains[0].name == "weibo"
    assert case == Case(1, "s0-wb-me")     # 任务定义不参与比较


# ---- runner.connect：供外部编排调用 ----

def connect_output(capsys, *argv):
    from runner import connect
    assert connect.main(list(argv)) == 0
    return json.loads(capsys.readouterr().out)


def test_connect_prints_mcd_environment(tmp_path, capsys):
    out = connect_output(capsys, "order", "--episode-id", EPISODE, "--token", TOKEN, "--workdir", str(tmp_path))
    env = out["env"]
    assert out["domains"] == ["mcd"]
    assert (env["MCD_MCP_URL"], env["MCD_MCP_TOKEN"]) == (f"{SANDBOX}/mcp/{EPISODE}/1", TOKEN)
    assert env["WEIBO_MCP_URL"].startswith(DISABLED_URL)           # 没用到的微博被显式禁用
    assert env["SANDBOX_EPISODE_ID"] == EPISODE                     # 直接模式的 Runner 与 Iris 同一局号


def test_connect_writes_weibo_credentials(tmp_path, capsys):
    from runner.tasks import TASKS_DIR
    out = connect_output(capsys, "s0-wb-me", "--tasks-dir", str(TASKS_DIR / "20261124T0900"),
                         "--episode-id", EPISODE, "--token", TOKEN, "--workdir", str(tmp_path))
    env = out["env"]
    assert out["domains"] == ["weibo"] and env["WEIBO_MCP_URL"] == f"{SANDBOX}/mcp/{EPISODE}/1"
    assert read_token(env["WEIBO_CREDENTIALS_FILE"]) == TOKEN
    assert Path(env["WEIBO_CREDENTIALS_FILE"]).parent.parent == tmp_path.resolve()


def test_connect_rejects_bad_input(tmp_path):
    from runner import connect
    with pytest.raises(SystemExit):
        connect.main(["order", "--episode-id", "short", "--token", TOKEN, "--workdir", str(tmp_path)])
    with pytest.raises(SystemExit):
        connect.main(["missing", "--episode-id", EPISODE, "--token", TOKEN, "--workdir", str(tmp_path)])



# ---- 隔离任务没用到的领域 ----

HOST = {"MCD_MCP_URL": "https://mcd.example.com/mcp", "MCD_MCP_TOKEN": "real-mcd",
        "WEIBO_MCP_URL": "https://weibo.example.com/mcp", "WEIBO_CREDENTIALS_FILE": "C:/real/auth.json"}


def test_unused_domains_are_disabled(tmp_path):
    from runner.tasks import TASKS_DIR
    weibo_only = load_tasks_for("s0-wb-me", TASKS_DIR / "20261124T0900")
    env = dict(HOST, **iris_connection(weibo_only, SANDBOX, EPISODE, TOKEN, tmp_path))
    assert env["WEIBO_MCP_URL"] == f"{SANDBOX}/mcp/{EPISODE}/1"
    assert (env["MCD_MCP_URL"], env["MCD_MCP_TOKEN"]) == (f"{DISABLED_URL}/mcd", "disabled")

    mcd_only = load_tasks_for("order")
    env = dict(HOST, **iris_connection(mcd_only, SANDBOX, EPISODE, TOKEN, tmp_path / "mcd"))
    assert env["MCD_MCP_URL"] == f"{SANDBOX}/mcp/{EPISODE}/1"
    assert env["WEIBO_MCP_URL"] == f"{DISABLED_URL}/weibo"
    assert not Path(env["WEIBO_CREDENTIALS_FILE"]).exists()        # Iris 读不到令牌，不会发请求


def test_two_domain_task_connects_both_through_domain_endpoints(tmp_path):
    env = iris_connection(spec([("mcd", "lunch"), ("weibo", "daily")]), SANDBOX, EPISODE, TOKEN, tmp_path)
    assert env["MCD_MCP_URL"] == f"{SANDBOX}/mcp/{EPISODE}/mcd/1"
    assert env["WEIBO_MCP_URL"] == f"{SANDBOX}/mcp/{EPISODE}/weibo/1"


@pytest.mark.parametrize("task_id", ["s0-wb-me", "order"])
def test_regression_case_environment_overrides_host_services(tmp_path, monkeypatch, task_id):
    """回归启动 Java 之前，宿主里预设的真实服务配置都被本局或禁用值覆盖。"""
    from contextlib import contextmanager
    from runner import regression
    from runner.tasks import TASKS_DIR
    task = load_tasks_for(task_id, TASKS_DIR / "20261124T0900" if task_id.startswith("s0-wb") else TASKS_DIR)
    seen = {}

    @contextmanager
    def launch(command, *, cwd, env, log, processes):
        seen.update(env)
        raise RuntimeError("stop before Java starts")
        yield

    monkeypatch.setattr(regression, "running", launch)
    regression.collect_case(regression.Case(1, task_id, task), batch=tmp_path, jar=tmp_path / "iris.jar",
                            iris_env=dict(HOST), sandbox_url=SANDBOX, admin_token="admin", seed=0, key="",
                            provenance={}, processes=None)
    assert seen["MCD_MCP_URL"] != HOST["MCD_MCP_URL"] and seen["WEIBO_MCP_URL"] != HOST["WEIBO_MCP_URL"]
    assert seen["MCD_MCP_TOKEN"] != HOST["MCD_MCP_TOKEN"]
    assert seen["WEIBO_CREDENTIALS_FILE"] != HOST["WEIBO_CREDENTIALS_FILE"]
    enabled = "WEIBO_MCP_URL" if task_id.startswith("s0-wb") else "MCD_MCP_URL"
    assert seen[enabled].startswith(f"{SANDBOX}/mcp/")


def load_tasks_for(task_id, directory=None):
    from runner.tasks import TASKS_DIR, load_tasks
    return load_tasks(directory or TASKS_DIR)[task_id]



def test_regression_rejects_an_empty_task_set(tmp_path, monkeypatch):
    import sys
    from runner import regression
    monkeypatch.setattr(sys, "argv", ["runner.regression", "--tasks-dir", str(tmp_path)])
    with pytest.raises(SystemExit) as error:
        regression.main()
    assert error.value.code == 2
