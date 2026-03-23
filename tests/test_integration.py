import io
import os
import sqlite3
import subprocess
import sys
from contextlib import closing, redirect_stderr, redirect_stdout
from pathlib import Path
from typing import Literal, TypedDict, overload
from unittest import mock
from urllib.parse import urlencode, urlsplit

import pytest
from sqlalchemy import create_engine, text
from wsgiref.util import setup_testing_defaults

from nira_app.cli import main
from nira_app.storage import NiraStore
from nira_app.web import NiraWebApp

REPO_ROOT = Path(__file__).resolve().parents[1]


class ResponseCapture(TypedDict, total=False):
    status: str
    headers: dict[str, str]


def run_cli(args, cwd, env=None, input_text=None, timeout=20):
    original_cwd = os.getcwd()
    os.chdir(cwd)
    original_env = os.environ.copy()
    if env:
        os.environ.update(env)

    stdout = io.StringIO()
    stderr = io.StringIO()

    try:
        with (
            redirect_stdout(stdout),
            redirect_stderr(stderr),
            mock.patch("sys.stdin", io.StringIO(input_text or "")),
        ):
            return_code = main(args)
    finally:
        os.chdir(original_cwd)
        os.environ.clear()
        os.environ.update(original_env)

    return subprocess.CompletedProcess(
        args=["nira", *args],
        returncode=return_code,
        stdout=stdout.getvalue(),
        stderr=stderr.getvalue(),
    )


class TestCliIntegration:
    def test_cli_init_new_show_list_and_aliases(self, temp_root):
        init_result = run_cli(["init", "--project-key", "EMH"], cwd=temp_root)
        assert init_result.returncode == 0
        assert (temp_root / ".nira" / "nira.db").exists()

        create_result = run_cli(
            [
                "create",
                "Evaluate Tortoise alternatives",
                "--source",
                "EMH-16 architecture review",
                "--type",
                "decision",
                "--priority",
                "medium",
                "--body",
                "## Summary\nDecision ticket body.\n",
            ],
            cwd=temp_root,
        )
        assert create_result.returncode == 0
        assert "EMH-1" in create_result.stdout

        show_result = run_cli(["get", "EMH-1"], cwd=temp_root)
        assert show_result.returncode == 0
        assert "EMH-1" in show_result.stdout
        assert "open" in show_result.stdout
        assert "decision" in show_result.stdout
        assert "medium" in show_result.stdout
        assert "EMH-16 architecture review" in show_result.stdout
        assert "Summary" in show_result.stdout

        list_result = run_cli(["list"], cwd=temp_root)
        assert list_result.returncode == 0
        assert "EMH-1" in list_result.stdout
        assert "Evaluate Tortoise alternatives" in list_result.stdout

    def test_cli_migrates_legacy_text_ticket_ids_to_integer_primary_keys(self, temp_root):
        workspace = temp_root / "legacy_workspace"
        workspace.mkdir()
        state_dir = workspace / ".nira"
        state_dir.mkdir()
        db_path = state_dir / "nira.db"

        # Use SQLAlchemy to ensure connections are closed properly
        engine = create_engine(f"sqlite:///{db_path}")
        with engine.connect() as connection:
            connection.execute(text("CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)"))
            connection.execute(
                text(
                    "CREATE TABLE projects (key TEXT PRIMARY KEY, next_number INTEGER NOT NULL, created_at TEXT NOT NULL)"
                )
            )
            connection.execute(
                text("""
                CREATE TABLE tickets (
                    id TEXT PRIMARY KEY,
                    project TEXT NOT NULL,
                    number INTEGER NOT NULL,
                    title TEXT NOT NULL,
                    status TEXT NOT NULL,
                    type TEXT NOT NULL,
                    priority TEXT NOT NULL,
                    source TEXT NOT NULL,
                    resolution_reason TEXT NOT NULL DEFAULT '',
                    body_md TEXT NOT NULL DEFAULT '',
                    resolution_md TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(project, number)
                )
            """)
            )
            connection.execute(
                text(
                    "CREATE TABLE comments (id INTEGER PRIMARY KEY AUTOINCREMENT, ticket_id TEXT NOT NULL REFERENCES tickets(id) ON DELETE CASCADE, body_md TEXT NOT NULL, created_at TEXT NOT NULL)"
                )
            )
            connection.execute(
                text(
                    "CREATE TABLE links (ticket_a TEXT NOT NULL REFERENCES tickets(id) ON DELETE CASCADE, ticket_b TEXT NOT NULL REFERENCES tickets(id) ON DELETE CASCADE, created_at TEXT NOT NULL, PRIMARY KEY(ticket_a, ticket_b), CHECK(ticket_a < ticket_b))"
                )
            )

            connection.execute(text("INSERT INTO settings (key, value) VALUES ('default_project', 'EMH')"))
            connection.execute(
                text("INSERT INTO projects (key, next_number, created_at) VALUES ('EMH', 2, '2026-03-23T00:00:00Z')")
            )
            connection.execute(
                text("""
                INSERT INTO tickets (
                    id, project, number, title, status, type, priority, source,
                    resolution_reason, body_md, resolution_md, created_at, updated_at
                ) VALUES ('EMH-1', 'EMH', 1, 'Legacy ticket', 'open', 'task', 'medium', 'legacy import', '', 'Legacy body', '', '2026-03-23T00:00:00Z', '2026-03-23T00:00:00Z')
            """)
            )
            connection.commit()
        engine.dispose()

        show_result = run_cli(["show", "EMH-1"], cwd=workspace)
        assert show_result.returncode == 0
        assert "EMH-1 Legacy ticket" in show_result.stdout
        assert "Legacy body" in show_result.stdout

        with closing(sqlite3.connect(db_path)) as connection:
            id_info = connection.execute("PRAGMA table_info(tickets)").fetchall()
            id_column = next(row for row in id_info if row[1] == "id")
            migrated_row = connection.execute("SELECT id, number, title FROM tickets WHERE number = 1").fetchone()

        assert str(id_column[2]).upper() == "INTEGER"
        assert migrated_row is not None
        assert migrated_row[1] == 1
        assert migrated_row[2] == "Legacy ticket"

    def test_cli_new_does_not_treat_uppercase_title_words_as_project_keys(self, temp_root):
        init_result = run_cli(["init", "--project-key", "EMH"], cwd=temp_root)
        assert init_result.returncode == 0

        create_result = run_cli(["new", "HTTP 500 on login"], cwd=temp_root)
        assert create_result.returncode == 0
        assert "EMH-1" in create_result.stdout

        show_result = run_cli(["show", "EMH-1"], cwd=temp_root)
        assert show_result.returncode == 0
        assert "HTTP 500 on login" in show_result.stdout

    def test_cli_init_uses_folder_name_as_default_project_key(self, temp_root):
        workspace = temp_root / "emh"
        workspace.mkdir()

        init_result = run_cli(["init"], cwd=workspace)
        assert init_result.returncode == 0

        new_result = run_cli(
            ["new", "Ticket created from workspace default"],
            cwd=workspace,
        )
        assert new_result.returncode == 0
        assert "EMH-1" in new_result.stdout

    def test_cli_init_uses_acronym_for_multi_word_folder_names(self, temp_root):
        workspaces = [
            temp_root / "employment-matching-hub",
            temp_root / "employment matching hub",
            temp_root / "EmploymentMatchingHub",
        ]

        for workspace in workspaces:
            workspace.mkdir()
            init_result = run_cli(["init"], cwd=workspace)
            assert init_result.returncode == 0

            new_result = run_cli(["new", "Ticket created from workspace acronym"], cwd=workspace)
            assert new_result.returncode == 0
            assert "EMH-1" in new_result.stdout

    def test_cli_update_link_close_and_reopen(self, temp_root):
        assert run_cli(["init", "--project-key", "EMH"], cwd=temp_root).returncode == 0
        assert run_cli(["new", "First ticket", "--source", "user"], cwd=temp_root).returncode == 0
        assert run_cli(["new", "Second ticket", "--source", "user"], cwd=temp_root).returncode == 0

        update_result = run_cli(
            [
                "update",
                "EMH-1",
                "--title",
                "Updated first ticket",
                "--status",
                "in_progress",
                "--type",
                "task",
                "--priority",
                "high",
                "--source",
                "customer report",
            ],
            cwd=temp_root,
        )
        assert update_result.returncode == 0

        link_result = run_cli(["link", "EMH-1", "EMH-2"], cwd=temp_root)
        assert link_result.returncode == 0

        links_result = run_cli(["links"], cwd=temp_root)
        assert links_result.returncode == 0
        assert "EMH-1" in links_result.stdout
        assert "EMH-2" in links_result.stdout

        scoped_links_result = run_cli(["links", "emh-1"], cwd=temp_root)
        assert scoped_links_result.returncode == 0
        assert "Related tickets for" in scoped_links_result.stdout
        assert "EMH-1" in scoped_links_result.stdout
        assert "EMH-2" in scoped_links_result.stdout
        assert "Second ticket" in scoped_links_result.stdout
        assert "EMH-1      First ticket" not in scoped_links_result.stdout

        show_linked = run_cli(["show", "EMH-1"], cwd=temp_root)
        assert show_linked.returncode == 0
        assert "Updated first ticket" in show_linked.stdout
        assert "in_progress" in show_linked.stdout
        assert "high" in show_linked.stdout
        assert "customer report" in show_linked.stdout
        assert "Related" in show_linked.stdout
        assert "EMH-2" in show_linked.stdout

        close_result = run_cli(
            ["close", "EMH-1", "--notes", "## Resolution\ndecided"],
            cwd=temp_root,
        )
        assert close_result.returncode == 0

        show_closed = run_cli(["show", "EMH-1"], cwd=temp_root)
        assert show_closed.returncode == 0
        assert "closed" in show_closed.stdout
        assert "decided" in show_closed.stdout
        assert "EMH-2" in show_closed.stdout

        with closing(sqlite3.connect(temp_root / ".nira" / "nira.db")) as connection:
            row = connection.execute(
                "SELECT status, resolution_reason, resolution_md FROM tickets WHERE number = ?",
                (1,),
            ).fetchone()
        assert row == ("closed", "completed", "## Resolution\ndecided")

        reopen_result = run_cli(["reopen", "EMH-1"], cwd=temp_root)
        assert reopen_result.returncode == 0

        unlink_result = run_cli(["unlink", "EMH-1", "EMH-2"], cwd=temp_root)
        assert unlink_result.returncode == 0

        no_links_result = run_cli(["links", "emh-1"], cwd=temp_root)
        assert no_links_result.returncode == 0
        assert "No related tickets found" in no_links_result.stdout

        show_reopened = run_cli(["show", "EMH-1"], cwd=temp_root)
        assert show_reopened.returncode == 0
        assert "open" in show_reopened.stdout
        assert "EMH-2" not in show_reopened.stdout

        with closing(sqlite3.connect(temp_root / ".nira" / "nira.db")) as connection:
            reopened_row = connection.execute(
                "SELECT status, resolution_reason FROM tickets WHERE number = ?",
                (1,),
            ).fetchone()
        assert reopened_row == ("open", "")

    def test_cli_edit_updates_body_and_resolution_with_editor(self, temp_root):
        assert run_cli(["init", "--project-key", "EMH"], cwd=temp_root).returncode == 0
        assert run_cli(["new", "Editable ticket"], cwd=temp_root).returncode == 0

        body_editor = temp_root / "body_editor.py"
        body_editor.write_text(
            "from pathlib import Path\n"
            "import sys\n"
            "path = Path(sys.argv[-1])\n"
            "path.write_text('## Summary\\nEdited body from test.\\n')\n"
        )

        resolution_editor = temp_root / "resolution_editor.py"
        resolution_editor.write_text(
            "from pathlib import Path\n"
            "import sys\n"
            "path = Path(sys.argv[-1])\n"
            "path.write_text('## Resolution\\nResolution notes from test.\\n')\n"
        )

        edit_body_result = run_cli(
            ["edit", "EMH-1"],
            cwd=temp_root,
            env={"EDITOR": f"{sys.executable} {body_editor}"},
        )
        assert edit_body_result.returncode == 0

        edit_resolution_result = run_cli(
            ["edit", "EMH-1", "--field", "resolution"],
            cwd=temp_root,
            env={"EDITOR": f"{sys.executable} {resolution_editor}"},
        )
        assert edit_resolution_result.returncode == 0

        show_result = run_cli(["show", "EMH-1"], cwd=temp_root)
        assert show_result.returncode == 0
        assert "Edited body from test." in show_result.stdout
        assert "Resolution notes from test." in show_result.stdout

    def test_cli_serve_help_exposes_web_arguments(self, temp_root):
        help_result = run_cli(["serve", "--help"], cwd=temp_root)
        assert help_result.returncode == 0
        assert "--host" in help_result.stdout
        assert "--port" in help_result.stdout
        assert "--reload" in help_result.stdout

    def test_cli_serve_reports_friendly_error_when_port_is_in_use(self, temp_root):
        assert run_cli(["init"], cwd=temp_root).returncode == 0
        stderr = io.StringIO()
        with (
            mock.patch("nira_app.web.make_server", side_effect=OSError(48, "Address already in use")),
            redirect_stderr(stderr),
        ):
            exit_code = main(["--root", str(temp_root), "serve", "--port", "8765"])

        assert exit_code == 1
        assert "Could not start Nira server on http://127.0.0.1:8765" in stderr.getvalue()
        assert "Traceback" not in stderr.getvalue()

    def test_cli_serve_exits_cleanly_on_keyboard_interrupt(self, temp_root):
        assert run_cli(["init"], cwd=temp_root).returncode == 0

        class FakeServer:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def serve_forever(self):
                raise KeyboardInterrupt

        stdout = io.StringIO()
        stderr = io.StringIO()
        with (
            mock.patch("nira_app.web.make_server", return_value=FakeServer()),
            redirect_stdout(stdout),
            redirect_stderr(stderr),
        ):
            exit_code = main(["--root", str(temp_root), "serve", "--port", "8765"])

        assert exit_code == 0
        assert "Serving Nira on http://127.0.0.1:8765" in stdout.getvalue()
        # Allow alembic info in stderr
        err = stderr.getvalue()
        if err:
            assert "alembic" in err or "DDL" in err

    def test_serve_with_reload_stops_child_cleanly_on_keyboard_interrupt(self):
        class FakeProcess:
            def __init__(self):
                self.terminated = False
                self.killed = False

            def poll(self):
                return None

            def terminate(self):
                self.terminated = True

            def wait(self, timeout=None):
                return 0

            def kill(self):
                self.killed = True

        fake_process = FakeProcess()
        stdout = io.StringIO()
        with (
            mock.patch("nira_app.cli.subprocess.Popen", return_value=fake_process),
            mock.patch("nira_app.cli.time.sleep", side_effect=KeyboardInterrupt),
            redirect_stdout(stdout),
        ):
            from nira_app.cli import serve_with_reload

            serve_with_reload(None, "127.0.0.1", 8765)

        assert "Watching" in stdout.getvalue()
        assert fake_process.terminated
        assert not fake_process.killed

    def test_cli_help_command_prints_root_and_command_help(self, temp_root):
        root_help = run_cli(["help"], cwd=temp_root)
        assert root_help.returncode == 0
        assert "nira" in root_help.stdout.lower()
        assert "serve" in root_help.stdout.lower()

        command_help = run_cli(["help", "new"], cwd=temp_root)
        assert command_help.returncode == 0
        assert "new" in command_help.stdout.lower()
        assert "--priority" in command_help.stdout.lower()

        links_help = run_cli(["help", "links"], cwd=temp_root)
        assert links_help.returncode == 0
        assert "links" in links_help.stdout.lower()


class TestHttpIntegration:
    @pytest.fixture(autouse=True)
    def setup(self, temp_root):
        self.root = temp_root
        self.store = NiraStore(self.root)
        self.store.initialize(default_project="EMH")
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
        status, _, body = self.request("GET", "/")
        assert status == 200
        assert "Nira" in body
        assert 'href="/assets/nira.png"' in body
        assert ">NIRA</span>" in body
        assert 'href="/">Ticket List</a>' in body
        assert 'href="/tickets/new">Create Ticket</a>' in body
        assert 'href="/settings">Settings</a>' in body
        assert 'name="project"' not in body
        assert "not closed" in body
        assert "selected>not closed</option>" in body
        assert 'name="sort"' in body
        assert 'name="direction"' in body
        assert "selected>updated</option>" in body
        assert 'href="/?status=not_closed&amp;sort=ticket_id&amp;direction=desc&amp;page=1"' in body
        assert 'href="/?status=not_closed&amp;sort=updated&amp;direction=asc&amp;page=1"' in body

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
        assert 'value="EMH"' in settings_page
        assert "Ticket labels are derived" in settings_page

        status, headers, _ = self.request(
            "POST",
            "/tickets",
            fields={
                "project": "EMH",
                "title": "First HTTP ticket",
                "source": "user",
                "type": "bug",
                "priority": "medium",
                "body_md": "## Summary\nCreated through the browser.\n",
            },
        )
        assert status == 303
        assert headers["Location"] == "/tickets/EMH-1"

        status, _, detail = self.request("GET", "/tickets/EMH-1")
        assert status == 200
        assert "First HTTP ticket" in detail
        assert 'href="/"' in detail
        assert 'href="/tickets/new">Create Ticket</a>' in detail
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
            "/tickets/EMH-1/edit",
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
        assert headers["Location"] == "/tickets/EMH-1"

        status, _, detail = self.request("GET", "/tickets/EMH-1")
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
        assert detail.index('for="title"') < detail.index("Body</h2>")
        assert detail.index("Body</h2>") < detail.index("Save</button>")
        assert detail.index("Save</button>") < detail.index("Resolution Notes</h2>")
        assert detail.index("Body</h2>") < detail.index("Resolution Notes</h2>")
        assert detail.index("Resolution Notes</h2>") < detail.index("Related</h2>")
        assert detail.index("Related</h2>") < detail.index("Status</h2>")
        assert detail.index("Status</h2>") < detail.index("Details</h2>")
        assert "Edit Ticket" not in detail
        assert "Track the ticket state here while the content stays front and center." not in detail
        assert "Close Ticket" not in detail
        assert "Workflow" not in detail
        assert 'name="resolution_reason"' not in detail
        assert "Resolution</dt>" not in detail

        ticket = self.store.get_ticket("EMH-1")
        assert ticket["type"] == "bug"

        status, headers, _ = self.request(
            "POST",
            "/tickets",
            fields={
                "project": "EMH",
                "title": "Second HTTP ticket",
                "source": "user",
                "type": "task",
                "priority": "low",
                "body_md": "",
                "resolution_md": "",
            },
        )
        assert status == 303
        assert headers["Location"] == "/tickets/EMH-2"

        status, headers, _ = self.request(
            "POST",
            "/tickets/EMH-1/link",
            fields={"other_ticket_id": "EMH-2"},
        )
        assert status == 303
        assert headers["Location"] == "/tickets/EMH-1"

        status, _, detail = self.request("GET", "/tickets/EMH-1")
        assert status == 200
        assert "/tickets/EMH-2" in detail

        status, _, list_page = self.request("GET", "/")
        assert status == 200
        assert 'href="/tickets/EMH-1"' in list_page
        assert 'href="/tickets/EMH-2"' in list_page
        assert "just now" in list_page

        status, _, priority_sorted = self.request("GET", "/?status=all&sort=priority&direction=desc")
        assert status == 200
        assert priority_sorted.index('href="/tickets/EMH-1"') < priority_sorted.index('href="/tickets/EMH-2"')

        status, _, id_sorted = self.request("GET", "/?status=all&sort=ticket_id&direction=desc")
        assert status == 200
        assert id_sorted.index('href="/tickets/EMH-2"') < id_sorted.index('href="/tickets/EMH-1"')

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

        status, _, renamed_detail = self.request("GET", "/tickets/EMH-1")
        assert status == 200
        assert "NIRA-1 First HTTP ticket updated" in renamed_detail

        status, _, renamed_list = self.request("GET", "/")
        assert status == 200
        assert 'href="/tickets/NIRA-1"' in renamed_list
        assert 'href="/tickets/NIRA-2"' in renamed_list

        status, _, renamed_new = self.request("GET", "/tickets/new")
        assert status == 200
        assert 'value="NIRA-"' in renamed_new

        status, headers, _ = self.request(
            "POST",
            "/tickets/EMH-1/comment",
            fields={"body_md": "Browser comment body."},
        )
        assert status == 303
        assert headers["Location"] == "/tickets/EMH-1"

        status, _, detail = self.request("GET", "/tickets/EMH-1")
        assert status == 200
        assert "Browser comment body." in detail

        status, headers, _ = self.request(
            "POST",
            "/tickets/EMH-1/close",
            fields={"resolution_md": "Closed via test"},
        )
        assert status == 303
        assert headers["Location"] == "/tickets/EMH-1"

        status, _, detail = self.request("GET", "/tickets/EMH-1")
        assert status == 200
        assert "closed" in detail
        assert "completed" in detail
        assert "Closed via test" in detail
        assert "text-bg-success" in detail

        status, _, list_page = self.request("GET", "/")
        assert status == 200
        assert 'href="/tickets/NIRA-1"' not in list_page
        assert 'href="/tickets/NIRA-2"' in list_page

        status, _, closed_list_page = self.request("GET", "/?status=closed")
        assert status == 200
        assert 'href="/tickets/NIRA-1"' in closed_list_page
        assert 'href="/tickets/NIRA-2"' not in closed_list_page

        status, headers, _ = self.request("POST", "/tickets/EMH-1/reopen", fields={})
        assert status == 303
        assert headers["Location"] == "/tickets/EMH-1"

        status, _, detail = self.request("GET", "/tickets/EMH-1")
        assert status == 200
        assert "open" in detail

        status, headers, _ = self.request(
            "POST",
            "/tickets/EMH-1/unlink",
            fields={"other_ticket_id": "EMH-2"},
        )
        assert status == 303
        assert headers["Location"] == "/tickets/EMH-1"

        status, _, detail = self.request("GET", "/tickets/EMH-1")
        assert status == 200
        assert 'href="/tickets/EMH-2"' not in detail

    def test_kanban_board(self):
        self.request(
            "POST",
            "/tickets",
            fields={
                "project": "EMH",
                "title": "Open ticket",
            },
        )
        self.request(
            "POST",
            "/tickets",
            fields={
                "project": "EMH",
                "title": "In progress ticket",
                "status": "in_progress",
            },
        )

        status, _, body = self.request("GET", "/board")
        assert status == 200
        assert "Kanban Board" in body
        assert "Open ticket" in body
        assert "In progress ticket" in body
        assert "Open" in body
        assert "In Progress" in body
        assert "Closed" in body

    def test_dashboard(self):
        self.request(
            "POST",
            "/tickets",
            fields={
                "project": "EMH",
                "title": "Stats ticket",
                "story_points": "5",
            },
        )
        # Record some activity
        self.request(
            "POST",
            "/tickets/EMH-1/edit",
            fields={"status": "in_progress"},
        )
        status, _, body = self.request("GET", "/dashboard")
        assert status == 200
        assert "Dashboard" in body
        assert "EMH-1" in body
        assert "in_progress" in body
        assert "5" in body

    def test_asset_endpoint_serves_logo_png(self):
        status, headers, body = self.request("GET", "/assets/nira.png", decode=False)
        assert status == 200
        assert headers["Content-Type"] == "image/png"
        assert body.startswith(b"\x89PNG\r\n\x1a\n")

    def test_full_text_search(self):
        self.request(
            "POST",
            "/tickets",
            fields={
                "project": "EMH",
                "title": "Unique title for search",
                "body_md": "This body contains the word flamingo.",
            },
        )
        self.request(
            "POST",
            "/tickets",
            fields={
                "project": "EMH",
                "title": "Another ticket",
                "body_md": "This one does not.",
            },
        )

        # Search in Web
        status, _, body = self.request("GET", "/?search=flamingo")
        assert status == 200
        assert "Unique title for search" in body
        assert "Another ticket" not in body

        # Search in CLI
        search_result = run_cli(["list", "--search", "flamingo"], cwd=self.root)
        assert search_result.returncode == 0
        assert "Unique title for search" in search_result.stdout
        assert "Another ticket" not in search_result.stdout
