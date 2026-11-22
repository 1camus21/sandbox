"""一局多领域的集成用例：分领域端点、工具隔离、证据按领域记录。"""
import json

from starlette.testclient import TestClient

from sandbox.domains.mcd import McDelivery
from sandbox.episodes import Outcome
from sandbox.server import create_app

ADMIN = {"Authorization": "Bearer multi-admin"}
ORDER = {"addressId": "1000000000000000000000000001", "storeCode": "100001",
         "beType": 2, "orderType": 2, "items": [{"productCode": "100101", "quantity": 1}]}


class EchoDomain:
    """测试用的第二个领域：一个回显工具。"""

    name = "echo"
    tools = [{"name": "echo-say", "description": "回显文本",
              "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}}}}]
    business_faults = frozenset()

    def worlds(self):
        return ["default"]

    def accounts(self):
        return ["guest"]

    def load_world(self, world_id, seed):
        return {"greeting": f"hello-{seed}"}

    def load_account(self, account_id, world, seed):
        return {"accountId": account_id or "guest", "spoken": []}

    def world_window(self, world):
        return None

    def world_state(self, world):
        return {"greeting": {"value": {"text": world["greeting"]}}}

    def account_state(self, account):
        return {"spoken": {str(index): {"text": text}
                           for index, text in enumerate(account["spoken"])}}

    def state(self, world, account):
        return {**self.world_state(world), **self.account_state(account)}

    def fault(self, world, account, fault):
        raise ValueError(f"unknown fault: {fault.type}")

    def call(self, episode, tool, arguments):
        text = str(arguments.get("text", ""))
        episode.account["spoken"].append(text)
        return Outcome(True, {"echo": text}, json.dumps({"echo": text}, ensure_ascii=False))


def open_episode(client, token="multi-token"):
    response = client.post("/admin/episodes", headers=ADMIN, json={
        "token": token,
        "domains": [
            {"name": "mcd", "scene": "lunch", "account": "alice"},
            {"name": "echo", "scene": "default", "account": "guest"},
        ],
    })
    assert response.status_code == 201, response.text
    return response.json()["episodeId"]


def rpc(client, episode_id, domain, method, params, *, token="multi-token"):
    endpoint = f"/mcp/{episode_id}/{domain}/1"
    response = client.post(endpoint, headers={
        "Authorization": f"Bearer {token}",
        "Accept": "application/json, text/event-stream",
    }, json={"jsonrpc": "2.0", "id": 7, "method": method, "params": params})
    return response


def test_catalog_lists_both_domains():
    with TestClient(create_app(admin_token="multi-admin",
                               domains=[McDelivery(), EchoDomain()]), base_url="http://localhost") as client:
        body = client.get("/admin/catalog", headers=ADMIN).raise_for_status().json()
        assert set(body["domains"]) == {"mcd", "echo"}
        assert body["domains"]["echo"] == ["default"]


def test_per_domain_endpoints_list_their_own_tools():
    with TestClient(create_app(admin_token="multi-admin",
                               domains=[McDelivery(), EchoDomain()]), base_url="http://localhost") as client:
        episode_id = open_episode(client)
        echo = rpc(client, episode_id, "echo", "tools/list", {}).json()["result"]["tools"]
        mcd = rpc(client, episode_id, "mcd", "tools/list", {}).json()["result"]["tools"]
        assert [tool["name"] for tool in echo] == ["echo-say"]
        assert len(mcd) == 13
        assert "create-order" in {tool["name"] for tool in mcd}


def test_tool_from_another_domain_is_rejected_on_endpoint():
    with TestClient(create_app(admin_token="multi-admin",
                               domains=[McDelivery(), EchoDomain()]), base_url="http://localhost") as client:
        episode_id = open_episode(client)
        response = rpc(client, episode_id, "echo", "tools/call",
                       {"name": "now-time-info", "arguments": {}})
        content = response.json()["result"]
        assert content["isError"] is True
        assert "not available" in content["content"][0]["text"]


def test_both_domains_share_one_episode_and_evidence():
    with TestClient(create_app(admin_token="multi-admin",
                               domains=[McDelivery(), EchoDomain()]), base_url="http://localhost") as client:
        episode_id = open_episode(client)
        echo = rpc(client, episode_id, "echo", "tools/call",
                   {"name": "echo-say", "arguments": {"text": "hi"}}).json()["result"]
        assert json.loads(echo["content"][0]["text"]) == {"echo": "hi"}
        mcd = rpc(client, episode_id, "mcd", "tools/call",
                  {"name": "now-time-info", "arguments": {}}).json()["result"]
        assert mcd["isError"] is False

        snap = client.get(f"/admin/episodes/{episode_id}/snapshot", headers=ADMIN).raise_for_status().json()
        assert snap["domains"] == ["mcd", "echo"]
        assert set(snap["world"]) == {"mcd", "echo"}
        assert set(snap["account"]["final"]) == {"mcd", "echo"}
        assert snap["account"]["final"]["echo"]["spoken"] == {"0": {"text": "hi"}}
        assert {call["domain"] for call in snap["calls"]} == {"mcd", "echo"}


def test_legacy_endpoint_exposes_all_tools():
    with TestClient(create_app(admin_token="multi-admin",
                               domains=[McDelivery(), EchoDomain()]), base_url="http://localhost") as client:
        episode_id = open_episode(client)
        response = client.post(f"/mcp/{episode_id}/1", headers={
            "Authorization": "Bearer multi-token", "Accept": "application/json, text/event-stream",
        }, json={"jsonrpc": "2.0", "id": 7, "method": "tools/list", "params": {}})
        names = {tool["name"] for tool in response.json()["result"]["tools"]}
        assert "echo-say" in names and "create-order" in names


def test_unknown_domain_in_path_is_rejected():
    with TestClient(create_app(admin_token="multi-admin",
                               domains=[McDelivery(), EchoDomain()]), base_url="http://localhost") as client:
        episode_id = open_episode(client)
        response = client.post(f"/mcp/{episode_id}/ghost/1", headers={
            "Authorization": "Bearer multi-token", "Accept": "application/json, text/event-stream",
        }, json={"jsonrpc": "2.0", "id": 7, "method": "tools/list", "params": {}})
        assert response.status_code == 404
