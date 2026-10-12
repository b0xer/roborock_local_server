import json

HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}


def rpc(client, method, params=None, headers=None, id_=1):
    body = {"jsonrpc": "2.0", "id": id_, "method": method, "params": params or {}}
    res = client.post("/mcp", json=body, headers={**HEADERS, **(headers or {})})
    if res.status_code != 200:
        return res, None
    if res.headers["content-type"].startswith("text/event-stream"):
        data = [line[5:].strip() for line in res.text.splitlines() if line.startswith("data:")]
        return res, json.loads(data[-1])
    return res, res.json()


def initialize(client, headers=None):
    return rpc(client, "initialize", {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "test", "version": "0"},
    }, headers)


def test_tools_listed(client):
    res, msg = initialize(client)
    assert res.status_code == 200, res.text
    _, msg = rpc(client, "tools/list", id_=2)
    names = {t["name"] for t in msg["result"]["tools"]}
    assert {"get_status", "start_cleaning", "return_to_dock", "clean_rooms", "get_map"} <= names
    assert "send_raw_command" not in names


def test_tool_call(client, fake):
    initialize(client)
    _, msg = rpc(client, "tools/call", {"name": "clean_rooms", "arguments": {"rooms": ["Living room"]}}, id_=3)
    assert msg["result"]["isError"] is False
    assert fake.commands[-1] == ("app_segment_clean", [{"segments": [17], "repeat": 1}])


def test_tool_error_is_readable(client, fake):
    initialize(client)
    fake.connected = False
    _, msg = rpc(client, "tools/call", {"name": "get_status", "arguments": {}}, id_=4)
    assert msg["result"]["isError"] is True
    assert "not connected" in msg["result"]["content"][0]["text"]


def test_mcp_requires_token(token_client):
    res, _ = initialize(token_client)
    assert res.status_code == 401
    res, msg = initialize(token_client, {"Authorization": "Bearer s3cret"})
    assert res.status_code == 200
    assert msg["result"]["serverInfo"]["name"] == "roborock"
