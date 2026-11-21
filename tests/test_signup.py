from fastapi.testclient import TestClient

from backend.app import create_app


def test_standalone_owner_signup_persists_and_cannot_be_reused(tmp_path):
    origin = "https://testserver"
    config = {
        "database_url": f"sqlite:///{tmp_path / 'workspace.sqlite3'}",
        "origin": origin,
        "path_prefix": "",
    }
    setup_token = "local-test-token-" + "A" * 32
    data = {"email": "Owner@example.test", "password": "A long fixture password", "token": setup_token}
    with TestClient(create_app({**config, "owner_signup_token": setup_token}), base_url=origin) as client:
        assert "Create the owner account" in client.get("/").text
        assert client.get("/api/projects").status_code == 401
        assert client.post("/api/signup", json=data).status_code == 403
        assert client.post("/api/signup", json={**data, "token": "wrong"}, headers={"Origin": origin}).status_code == 403
        assert client.post("/api/signup", json=data, headers={"Origin": origin}).status_code == 200
        assert client.get("/api/projects").status_code == 200
        assert client.post("/api/signup", json=data, headers={"Origin": origin}).status_code == 404

    with TestClient(create_app(config), base_url=origin) as client:
        assert 'type="email"' in client.get("/").text
        assert client.post("/api/login", json={"username": data["email"], "password": data["password"]}).status_code == 200
        assert client.get("/api/me").json()["username"] == "owner@example.test"
