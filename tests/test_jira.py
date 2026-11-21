from fastapi.testclient import TestClient

from backend.app import create_app, digest, password_hash
from backend.store import Token


def test_jira_routes_share_native_records_and_scope(tmp_path):
    origin = "https://testserver"
    email = "owner@example.test"
    secret = "test-token-" + "X" * 30
    read_secret = "test-read-" + "X" * 30
    app = create_app({
        "database_url": f"sqlite:///{tmp_path / 'test.sqlite3'}", "origin": origin, "path_prefix": "",
        "jira_api_email": email, "username": "owner", "password_hash": password_hash("owner password"),
    })
    with app.state.store.tx() as db:
        db.add_all([Token(name="write", digest=digest(secret), scope="write"),
                    Token(name="read", digest=digest(read_secret), scope="read")])
    app.state.store.create_project({"key": "DEMO", "name": "Demo project"})
    app.state.store.create_issue("DEMO", {"title": "Separate native issue"})
    with TestClient(app, base_url=origin) as client:
        base = "/rest/api/3"
        assert client.get(base + "/project/DEMO").status_code == 401
        assert client.get(base + "/project/DEMO", auth=("wrong@example.test", secret)).status_code == 401
        client.auth = (email, secret)
        assert client.get(base + "/project/search").json()["values"][0]["key"] == "DEMO"
        doc = {"version": 1, "type": "doc", "content": [
            {"type": "paragraph", "content": [{"type": "text", "text": "Fixture"}]},
        ]}
        create = {"fields": {"project": {"key": "DEMO"}, "summary": "Build compatibility",
                             "description": doc, "issuetype": {"name": "Task"}}}
        assert client.post(base + "/issue", auth=(email, read_secret), json=create).status_code == 403
        result = client.post(base + "/issue", json=create)
        assert result.status_code == 201, result.text
        issue = base + "/issue/" + result.json()["key"]
        assert client.get(issue).json()["fields"]["description"] == doc
        assert client.put(issue, json={"fields": {"labels": ["contract"]}}).status_code == 204
        assert client.post(issue + "/comment", json={"body": doc}).status_code == 201
        assert client.get(issue + "/comment").json()["comments"][0]["body"] == doc
        transitions = client.get(issue + "/transitions").json()["transitions"]
        target = next(t for t in transitions if t["name"] == "Done")
        assert client.post(issue + "/transitions", json={"transition": {"id": target["id"]}}).status_code == 204
        result = client.post(base + "/search/jql", auth=(email, read_secret),
                             json={"jql": "project = DEMO AND status = Done", "fields": ["summary"]})
        assert result.json()["issues"][0]["fields"] == {"summary": "Build compatibility"}
        assert app.state.store.get_issue("DEMO-2")["status"] == "done"
        assert client.get(base + "/missing").status_code == 404
