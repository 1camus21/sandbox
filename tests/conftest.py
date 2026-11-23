import pytest

ADMIN = {"Authorization": "Bearer test-admin"}
MCP_TOKEN = "test-mcd"
ORDER = {
    "addressId": "1000000000000000000000000001",
    "storeCode": "100001",
    "beType": 2,
    "orderType": 2,
    "items": [{"productCode": "100101", "quantity": 1}],
}


@pytest.fixture
def client():
    # 惰性导入：领域层用例不依赖 starlette/mcp，可离线执行。
    from starlette.testclient import TestClient

    from sandbox.server import create_app

    with TestClient(create_app(admin_token="test-admin"), base_url="http://localhost") as http:
        yield http


def start(client, *, token=MCP_TOKEN, clock="2026-11-10 12:00:00", **config):
    response = client.post("/admin/episodes", headers=ADMIN,
                           json={"token": token, "clock": clock, **config})
    return response.raise_for_status().json()["episodeId"]


def admin(client, episode_id, action, method="GET", **kwargs):
    return client.request(method, f"/admin/episodes/{episode_id}/{action}", headers=ADMIN, **kwargs).raise_for_status()


def rpc(client, method, params, *, token=MCP_TOKEN, rpc_id=7, endpoint="/mcp", **headers):
    return client.post(f"http://localhost{endpoint}", headers={
        "Authorization": f"Bearer {token}", "Accept": "application/json, text/event-stream", **headers,
    }, json={"jsonrpc": "2.0", "id": rpc_id, "method": method, "params": params})


def tool(client, name, args=None, *, meta=None, **kwargs):
    params = {"name": name, "arguments": args or {}}
    if meta is not None:
        params["_meta"] = meta
    return rpc(client, "tools/call", params, **kwargs)
