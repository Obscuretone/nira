import pytest
from pathlib import Path
import tempfile
import io
from typing import Literal, TypedDict, overload
from urllib.parse import urlencode, urlsplit

from wsgiref.util import setup_testing_defaults

from nira_app.storage import NiraStore
from nira_app.web import NiraWebApp


class ResponseCapture(TypedDict, total=False):
    status: str
    headers: dict[str, str]


@pytest.fixture
def temp_root():
    with tempfile.TemporaryDirectory() as td:
        yield Path(td)


class TestHttpIntegration:
    @pytest.fixture(autouse=True)
    def setup(self, temp_root):
        self.root = temp_root
        self.store = NiraStore(self.root)
        self.store.initialize(default_project="NIRA")
        self.app = NiraWebApp(self.store)

    @overload
    def request(
        self,
        method: str,
        path: str,
        fields: dict[str, str] | None = None,
        headers: dict[str, str] | None = None,
        *,
        decode: Literal[True] = True,
    ) -> tuple[int, dict[str, str], str]: ...

    @overload
    def request(
        self,
        method: str,
        path: str,
        fields: dict[str, str] | None = None,
        headers: dict[str, str] | None = None,
        *,
        decode: Literal[False],
    ) -> tuple[int, dict[str, str], bytes]: ...

    def request(self, method, path, fields=None, headers=None, *, decode=True):
        body = b""
        final_headers = headers.copy() if headers else {}
        if fields is not None:
            body = urlencode(fields).encode("utf-8")
            final_headers["Content-Type"] = "application/x-www-form-urlencoded"
        final_headers["Content-Length"] = str(len(body))

        captured: ResponseCapture = {}

        def start_response(status, response_headers):
            captured["status"] = status
            captured["headers"] = dict(response_headers)

        environ: dict[str, object] = {}
        setup_testing_defaults(environ)
        split = urlsplit(path)
        environ["REQUEST_METHOD"] = method
        environ["PATH_INFO"] = split.path
        environ["QUERY_STRING"] = split.query
        environ["CONTENT_LENGTH"] = final_headers["Content-Length"]
        environ["CONTENT_TYPE"] = final_headers.get("Content-Type", "")
        environ["wsgi.input"] = io.BytesIO(body)

        chunks = self.app(environ, start_response)
        raw_payload = b"".join(chunks)
        assert "status" in captured
        assert "headers" in captured
        status_code = int(captured["status"].split()[0])
        payload = raw_payload.decode("utf-8") if decode else raw_payload
        return status_code, captured["headers"], payload

    def test_http_endpoints_cover_full_crud_flow(self):
        status, _, body = self.request("GET", "/list")
        assert status == 200
        assert "Nira" in body
        assert 'href="/assets/nira.png"' in body
        assert ">NIRA</span>" in body
        assert 'href="/list" title=' in body
        assert 'href="/tickets/new" title=' in body
        assert 'href="/settings">Settings</a>' in body
        assert 'name="project"' not in body
        assert "not closed" in body
        assert "selected>not closed</option>" in body
        assert 'name="sort"' in body
        assert 'name="direction"' in body
        assert "selected>updated</option>" in body
        assert 'href="/list?status=not_closed&amp;sort=ticket_id&amp;direction=desc&amp;page=1"' in body
        assert 'href="/list?status=not_closed&amp;sort=updated&amp;direction=asc&amp;page=1"' in body

        status, _, body = self.request("GET", "/tickets/new")
        assert status == 200
        assert "Create Ticket" in body
        assert 'name="type"' in body
        assert '<option value="bug"' in body
        assert "bug</option>" in body
        assert "toastui-editor.min.css" in body
        assert "toastui-editor-all.min.js" in body
        assert 'data-rich-editor="body_md"' in body
        assert 'data-rich-editor="resolution_md"' not in body
        assert "Resolution Notes" not in body
        assert body.index('for="title"') < body.index('data-rich-editor="body_md"')
        assert body.index('data-rich-editor="body_md"') < body.index('for="project_display"')

        status, _, settings_page = self.request("GET", "/settings")
        assert status == 200
        assert "Workspace Prefix" in settings_page
        assert 'value="NIRA"' in settings_page
        assert "Ticket labels are derived" in settings_page

        status, headers, _ = self.request(
            "POST",
            "/tickets",
            fields={
                "project": "NIRA",
                "title": "First HTTP ticket",
                "source": "user",
                "type": "bug",
                "priority": "medium",
                "body_md": "## Summary\nCreated through the browser.\n",
            },
        )
        assert status == 303
        assert headers["Location"] == "/tickets/NIRA-1"

        status, _, detail = self.request("GET", "/tickets/NIRA-1")
        assert status == 200
        assert "First HTTP ticket" in detail
        assert 'href="/"' in detail
        assert 'href="/tickets/new" title=' in detail
        assert 'href="/settings">Settings</a>' in detail
        assert "Created through the browser." in detail
        assert "badge" in detail
        assert "text-bg-secondary" in detail

        status, _, preview = self.request(
            "POST",
            "/preview",
            fields={"markdown": "## Preview\n- item one\n- item two\n`code`\n"},
        )
        assert status == 200
        assert "<h2>Preview</h2>" in preview
        assert "<li>item one</li>" in preview
        assert "<code>code</code>" in preview

        status, headers, _ = self.request(
            "POST",
            "/tickets/NIRA-1/edit",
            fields={
                "title": "First HTTP ticket updated",
                "source": "docs/architecture/data-stack.md",
                "status": "in_progress",
                "priority": "high",
                "type": "bug",
                "body_md": "## Summary\nUpdated body through the browser.\n",
                "resolution_md": "## Resolution\nPending final decision.\n",
            },
        )
        assert status == 303
        assert headers["Location"] == "/tickets/NIRA-1"

        status, _, detail = self.request("GET", "/tickets/NIRA-1")
        assert status == 200
        assert "First HTTP ticket updated" in detail
        assert "docs/architecture/data-stack.md" in detail
        assert "in_progress" in detail
        assert "Updated body through the browser." in detail
        assert "Pending final decision." in detail
        assert "just now" in detail
        assert 'data-rich-editor="resolution_md"' in detail
        assert "bg-primary-subtle" in detail
        assert "text-bg-primary" in detail
        assert "data-auto-submit" in detail
        assert "Activity Feed" in detail
        assert "Related" in detail
        assert "Status" in detail
        assert "Details" in detail
        assert "Edit Ticket" not in detail
        assert "Track the ticket state here while the content stays front and center." not in detail
        assert "Close Ticket" not in detail
        assert "Workflow" not in detail
        assert 'name="resolution_reason"' not in detail
        assert "Resolution</dt>" not in detail

        ticket = self.app.service.store.get_ticket("NIRA-1")
        assert ticket["type"] == "bug"

        status, headers, _ = self.request(
            "POST",
            "/tickets",
            fields={
                "project": "NIRA",
                "title": "Second HTTP ticket",
                "source": "user",
                "type": "task",
                "priority": "low",
                "body_md": "",
                "resolution_md": "",
            },
        )
        assert status == 303
        assert headers["Location"] == "/tickets/NIRA-2"

        status, headers, _ = self.request(
            "POST",
            "/tickets/NIRA-1/link",
            fields={"other_ticket_id": "NIRA-2"},
        )
        assert status == 303
        assert headers["Location"] == "/tickets/NIRA-1"

        status, _, detail = self.request("GET", "/tickets/NIRA-1")
        assert status == 200
        assert "/tickets/NIRA-2" in detail

        status, _, list_page = self.request("GET", "/list")
        assert status == 200
        assert 'href="/tickets/NIRA-1"' in list_page
        assert 'href="/tickets/NIRA-2"' in list_page
        assert "just now" in list_page

        status, _, priority_sorted = self.request("GET", "/list?status=all&sort=priority&direction=desc")
        assert status == 200
        assert priority_sorted.index('href="/tickets/NIRA-1"') < priority_sorted.index('href="/tickets/NIRA-2"')

        status, _, id_sorted = self.request("GET", "/list?status=all&sort=ticket_id&direction=desc")
        assert status == 200
        assert id_sorted.index('href="/tickets/NIRA-2"') < id_sorted.index('href="/tickets/NIRA-1"')

        status, headers, _ = self.request(
            "POST",
            "/settings",
            fields={"default_project": "NIRA"},
        )
        assert status == 303
        assert headers["Location"] == "/settings?saved=1"

        status, _, settings_page = self.request("GET", "/settings?saved=1")
        assert status == 200
        assert "Workspace settings updated." in settings_page
        assert 'value="NIRA"' in settings_page
        assert self.store.get_default_project() == "NIRA"

        status, _, renamed_detail = self.request("GET", "/tickets/NIRA-1")
        assert status == 200
        assert "NIRA-1 First HTTP ticket updated" in renamed_detail

        status, _, renamed_list = self.request("GET", "/list")
        assert status == 200
        assert 'href="/tickets/NIRA-1"' in renamed_list
        assert 'href="/tickets/NIRA-2"' in renamed_list

        status, _, renamed_new = self.request("GET", "/tickets/new")
        assert status == 200
        assert 'value="NIRA-"' in renamed_new

        status, headers, _ = self.request(
            "POST",
            "/tickets/NIRA-1/comment",
            fields={"body_md": "Browser comment body."},
        )
        assert status == 303
        assert headers["Location"] == "/tickets/NIRA-1"

        status, _, detail = self.request("GET", "/tickets/NIRA-1")
        assert status == 200
        assert "Browser comment body." in detail

        status, headers, _ = self.request(
            "POST",
            "/tickets/NIRA-1/close",
            fields={"resolution_md": "Closed via test"},
        )
        assert status == 303
        assert headers["Location"] == "/tickets/NIRA-1"

        status, _, detail = self.request("GET", "/tickets/NIRA-1")
        assert status == 200
        assert "closed" in detail
        assert "completed" in detail
        assert "Closed via test" in detail
        assert "text-bg-success" in detail

        status, _, closed_list_page = self.request("GET", "/list?status=closed")
        assert status == 200
        assert 'href="/tickets/NIRA-1"' in closed_list_page
        assert 'href="/tickets/NIRA-2"' not in closed_list_page

        status, headers, _ = self.request("POST", "/tickets/NIRA-1/reopen", fields={})
        assert status == 303
        assert headers["Location"] == "/tickets/NIRA-1"

        status, _, detail = self.request("GET", "/tickets/NIRA-1")
        assert status == 200
        assert "open" in detail

        status, headers, _ = self.request(
            "POST",
            "/tickets/NIRA-1/unlink",
            fields={"other_ticket_id": "NIRA-2"},
        )
        assert status == 303
        assert headers["Location"] == "/tickets/NIRA-1"

        status, _, detail = self.request("GET", "/tickets/NIRA-1")
        assert status == 200
        assert 'href="/tickets/NIRA-2"' not in detail

    def test_kanban_board(self):
        self.request(
            "POST",
            "/tickets",
            fields={
                "project": "NIRA",
                "title": "Open ticket",
            },
        )
        self.request(
            "POST",
            "/tickets",
            fields={
                "project": "NIRA",
                "title": "In progress ticket",
                "status": "in_progress",
            },
        )

        status, _, body = self.request("GET", "/board")
        assert status == 200
        assert "Kanban Board" in body
        assert "Open ticket" in body
        assert "In progress ticket" in body

    def test_editor_autocomplete(self):
        self.app.service.create_ticket("NIRA", "Some ticket")
        status, headers, body = self.request("GET", "/tickets/editor_autocomplete?q=Some")
        assert status == 200
        assert "Some ticket" in body

        status, headers, body = self.request("GET", "/tickets/editor_autocomplete?q=NotThere")
        assert status == 200
        assert "No matching tickets" in body
