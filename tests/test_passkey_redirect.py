import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch
from urllib.parse import urlencode

from fastapi.responses import RedirectResponse
from fastapi.testclient import TestClient

from backend.app import create_app, password_hash


class PasskeyRedirectTests(unittest.TestCase):
    def setUp(self):
        directory = self.enterContext(tempfile.TemporaryDirectory())
        database = Path(directory) / "nira.sqlite"
        self.issuer = "https://login.example.test"
        oauth = self.enterContext(patch("backend.passkeys.OAuth")).return_value.register.return_value
        oauth.load_server_metadata = AsyncMock(return_value={"issuer": self.issuer})
        oauth.authorize_redirect = AsyncMock(return_value=RedirectResponse(self.issuer + "/authorize"))
        oauth.authorize_access_token = AsyncMock(return_value={"userinfo": {
            "iss": self.issuer, "sub": "owner", "auth_time": int(time.time()),
        }})
        app = create_app({
            "database_url": f"sqlite:///{database}",
            "origin": "https://tasks.example.test",
            "path_prefix": "/nira",
            "username": "owner",
            "password_hash": password_hash("fixture-password"),
            "oidc": {"issuer": self.issuer, "client_id": "nira", "client_secret": "fixture",
                     "owner_subject": "owner", "session_key": "fixture-key" * 4},
        })
        self.addCleanup(app.state.store.engine.dispose)
        self.client = self.enterContext(TestClient(app, base_url="https://tasks.example.test",
                                                   root_path="/nira", follow_redirects=False))
        project = app.state.store.create_project({"name": "Fixture", "key": "FIX"})
        app.state.store.create_issue(project["id"], {"title": "Direct issue"})

    def test_browser_redirects_to_passkey_and_keeps_the_destination(self):
        for target in ("/nira/?workspace=personal", "/nira/browse/project/FIX?tab=board",
                       "/nira/browse/issue/FIX-1?workspace=personal&search=a%2Bb"):
            with self.subTest(target=target):
                self.client.cookies.clear()
                entry = self.client.get(target)
                self.assertEqual(entry.status_code, 303)
                self.assertEqual(entry.headers["cache-control"], "no-store")
                self.assertEqual(entry.headers["location"], "/nira/auth/sign-in?" + urlencode({"next": target}))
                provider = self.client.get(entry.headers["location"])
                self.assertEqual(provider.headers["location"], self.issuer + "/authorize")
                callback = self.client.get("/nira/auth/callback")
                self.assertEqual(callback.status_code, 303)
                self.assertEqual(callback.headers["location"], target)
                self.assertEqual(self.client.get(target).status_code, 200)

    def test_recovery_and_logout_keep_the_session_boundaries(self):
        entry = self.client.get("/nira/auth/recovery", params={"next": "//external.example/"})
        self.assertIn('data-next="/nira/"', entry.text)
        self.assertIn("Use a password", entry.text)
        self.assertEqual(self.client.get("/nira/api/me").status_code, 401)
        self.assertEqual(self.client.post("/nira/mcp/").status_code, 401)
        login = self.client.post("/nira/api/login", json={"username": "owner", "password": "fixture-password"})
        self.assertEqual(login.status_code, 200)
        logout = self.client.post("/nira/api/logout", headers={"X-CSRF-Token": login.json()["csrf"]})
        self.assertEqual(logout.status_code, 200)
        self.assertEqual(self.client.get("/nira/auth/recovery").status_code, 200)
        self.assertEqual(self.client.get("/nira/api/me").status_code, 401)
