"""Runner 的领域配置：麦乐送行为不变，微博通过授权文件接入，多领域任务逐个领域探测。"""
import json

import pytest

from assertions.preflight import preflight_retained, probe_succeeded
from runner.domains import DOMAINS, get_domain, task_domains
from runner.domains import weibo as weibo_domain
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
    assert weibo_domain.main(["--credentials", str(path)]) == 0
    first = read_token(path)
    assert weibo_domain.main(["--credentials", str(path)]) == 0
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
