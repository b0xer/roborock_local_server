from tests.conftest import FAKE_STATUS, PNG


def test_health_and_status(client):
    assert client.get("/api/health").json()["state"] == "ready"
    assert client.get("/api/status").json() == FAKE_STATUS


def test_commands_reach_service(client, fake):
    assert client.post("/api/start").json()["ok"] is True
    assert client.post("/api/dock").json()["command"] == "app_charge"
    assert [c[0] for c in fake.commands] == ["app_start", "app_charge"]


def test_clean_rooms(client, fake):
    res = client.post("/api/clean/rooms", json={"rooms": ["kitchen"], "repeat": 2})
    assert res.status_code == 200
    assert fake.commands[-1] == ("app_segment_clean", [{"segments": [16], "repeat": 2}])


def test_clean_rooms_validation(client):
    assert client.post("/api/clean/rooms", json={"rooms": []}).status_code == 422
    assert client.post("/api/clean/rooms", json={"rooms": ["x"], "repeat": 9}).status_code == 422
    res = client.post("/api/clean/rooms", json={"rooms": ["garage"]})
    assert res.status_code == 400
    assert "garage" in res.json()["detail"]


def test_robot_errors_map_to_http(client, fake):
    fake.connected = False
    res = client.get("/api/status")
    assert res.status_code == 503
    assert "not connected" in res.json()["detail"]


def test_map_png(client):
    res = client.get("/api/map.png")
    assert res.headers["content-type"] == "image/png"
    assert res.content == PNG


def test_ui_served(client):
    res = client.get("/")
    assert res.status_code == 200
    assert "<title>Roborock</title>" in res.text


def test_token_required(token_client):
    assert token_client.get("/api/health").status_code == 200
    assert token_client.get("/api/status").status_code == 401
    assert token_client.get("/api/status", headers={"Authorization": "Bearer wrong"}).status_code == 401
    ok = token_client.get("/api/status", headers={"Authorization": "Bearer s3cret"})
    assert ok.status_code == 200
    # The UI itself stays reachable so the user can enter the token.
    assert token_client.get("/").status_code == 200
