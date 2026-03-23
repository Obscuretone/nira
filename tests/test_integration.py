import io
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing, redirect_stderr, redirect_stdout
from pathlib import Path
from typing import Literal, TypedDict, overload
from urllib.parse import urlencode, urlsplit
from unittest import mock

from wsgiref.util import setup_testing_defaults

from nira_app.cli import build_reload_snapshot, main, serve_with_reload
from nira_app.storage import NiraStore
from nira_app.web import NiraWebApp


REPO_ROOT = Path(__file__).resolve().parents[1]
CLI_PATH = REPO_ROOT / "nira"


class ResponseCapture(TypedDict, total=False):
    status: str
    headers: dict[str, str]


def run_cli(args, cwd, env=None, input_text=None, timeout=20):
    merged_env = os.environ.copy()
    merged_env["PYTHONUNBUFFERED"] = "1"
    merged_env["PYTHONPATH"] = str(REPO_ROOT)
    if env:
        merged_env.update(env)

    return subprocess.run(
        [sys.executable, "-m", "nira_app.cli", *args],
        cwd=cwd,
        env=merged_env,
        input=input_text,
        text=True,
        capture_output=True,
        timeout=timeout,
    )


class CliIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)

    def tearDown(self):
        self.tempdir.cleanup()

    def test_cli_init_new_show_list_and_aliases(self):
        init_result = run_cli(["init", "--project-key", "EMH"], cwd=self.root)
        self.assertEqual(init_result.returncode, 0, init_result.stderr)
        self.assertTrue((self.root / ".nira" / "nira.db").exists())

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
            cwd=self.root,
        )
        self.assertEqual(create_result.returncode, 0, create_result.stderr)
        self.assertIn("EMH-1", create_result.stdout)

        show_result = run_cli(["get", "EMH-1"], cwd=self.root)
        self.assertEqual(show_result.returncode, 0, show_result.stderr)
        self.assertIn("EMH-1", show_result.stdout)
        self.assertIn("Status: open", show_result.stdout)
        self.assertIn("Type: decision", show_result.stdout)
        self.assertIn("Priority: medium", show_result.stdout)
        self.assertIn("Source: EMH-16 architecture review", show_result.stdout)
        self.assertIn("## Summary", show_result.stdout)

        list_result = run_cli(["list"], cwd=self.root)
        self.assertEqual(list_result.returncode, 0, list_result.stderr)
        self.assertIn("EMH-1", list_result.stdout)
        self.assertIn("Evaluate Tortoise alternatives", list_result.stdout)

    def test_cli_migrates_legacy_text_ticket_ids_to_integer_primary_keys(self):
        state_dir = self.root / ".nira"
        state_dir.mkdir()
        db_path = state_dir / "nira.db"

        with closing(sqlite3.connect(db_path)) as connection:
            connection.executescript(
                """
                CREATE TABLE settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );

                CREATE TABLE projects (
                    key TEXT PRIMARY KEY,
                    next_number INTEGER NOT NULL,
                    created_at TEXT NOT NULL
                );

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
                );

                CREATE TABLE comments (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ticket_id TEXT NOT NULL REFERENCES tickets(id) ON DELETE CASCADE,
                    body_md TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE links (
                    ticket_a TEXT NOT NULL REFERENCES tickets(id) ON DELETE CASCADE,
                    ticket_b TEXT NOT NULL REFERENCES tickets(id) ON DELETE CASCADE,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(ticket_a, ticket_b),
                    CHECK(ticket_a < ticket_b)
                );
                """
            )
            connection.execute(
                "INSERT INTO settings (key, value) VALUES ('default_project', 'EMH')"
            )
            connection.execute(
                "INSERT INTO projects (key, next_number, created_at) VALUES (?, ?, ?)",
                ("EMH", 2, "2026-03-23T00:00:00Z"),
            )
            connection.execute(
                """
                INSERT INTO tickets (
                    id, project, number, title, status, type, priority, source,
                    resolution_reason, body_md, resolution_md, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "EMH-1",
                    "EMH",
                    1,
                    "Legacy ticket",
                    "open",
                    "task",
                    "medium",
                    "legacy import",
                    "",
                    "Legacy body",
                    "",
                    "2026-03-23T00:00:00Z",
                    "2026-03-23T00:00:00Z",
                ),
            )
            connection.commit()

        show_result = run_cli(["show", "EMH-1"], cwd=self.root)
        self.assertEqual(show_result.returncode, 0, show_result.stderr)
        self.assertIn("EMH-1 Legacy ticket", show_result.stdout)
        self.assertIn("Legacy body", show_result.stdout)

        with closing(sqlite3.connect(db_path)) as connection:
            id_info = connection.execute("PRAGMA table_info(tickets)").fetchall()
            id_column = next(row for row in id_info if row[1] == "id")
            migrated_row = connection.execute(
                "SELECT id, number, title FROM tickets WHERE number = 1"
            ).fetchone()

        self.assertEqual(str(id_column[2]).upper(), "INTEGER")
        self.assertIsNotNone(migrated_row)
        assert migrated_row is not None
        self.assertEqual(migrated_row[1], 1)
        self.assertEqual(migrated_row[2], "Legacy ticket")

    def test_cli_new_does_not_treat_uppercase_title_words_as_project_keys(self):
        init_result = run_cli(["init", "--project-key", "EMH"], cwd=self.root)
        self.assertEqual(init_result.returncode, 0, init_result.stderr)

        create_result = run_cli(["new", "HTTP 500 on login"], cwd=self.root)
        self.assertEqual(create_result.returncode, 0, create_result.stderr)
        self.assertIn("EMH-1", create_result.stdout)

        show_result = run_cli(["show", "EMH-1"], cwd=self.root)
        self.assertEqual(show_result.returncode, 0, show_result.stderr)
        self.assertIn("HTTP 500 on login", show_result.stdout)

    def test_cli_init_uses_folder_name_as_default_project_key(self):
        workspace = self.root / "emh"
        workspace.mkdir()

        init_result = run_cli(["init"], cwd=workspace)
        self.assertEqual(init_result.returncode, 0, init_result.stderr)

        new_result = run_cli(
            ["new", "Ticket created from workspace default"],
            cwd=workspace,
        )
        self.assertEqual(new_result.returncode, 0, new_result.stderr)
        self.assertIn("EMH-1", new_result.stdout)

    def test_cli_init_uses_acronym_for_multi_word_folder_names(self):
        workspaces = [
            self.root / "employment-matching-hub",
            self.root / "employment matching hub",
            self.root / "EmploymentMatchingHub",
        ]

        for workspace in workspaces:
            workspace.mkdir()
            init_result = run_cli(["init"], cwd=workspace)
            self.assertEqual(init_result.returncode, 0, init_result.stderr)

            new_result = run_cli(["new", "Ticket created from workspace acronym"], cwd=workspace)
            self.assertEqual(new_result.returncode, 0, new_result.stderr)
            self.assertIn("EMH-1", new_result.stdout)

    def test_cli_update_link_close_and_reopen(self):
        self.assertEqual(run_cli(["init", "--project-key", "EMH"], cwd=self.root).returncode, 0)
        self.assertEqual(
            run_cli(["new", "First ticket", "--source", "user"], cwd=self.root).returncode,
            0,
        )
        self.assertEqual(
            run_cli(["new", "Second ticket", "--source", "user"], cwd=self.root).returncode,
            0,
        )

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
            cwd=self.root,
        )
        self.assertEqual(update_result.returncode, 0, update_result.stderr)

        link_result = run_cli(["link", "EMH-1", "EMH-2"], cwd=self.root)
        self.assertEqual(link_result.returncode, 0, link_result.stderr)

        links_result = run_cli(["links"], cwd=self.root)
        self.assertEqual(links_result.returncode, 0, links_result.stderr)
        self.assertIn("EMH-1 <-> EMH-2", links_result.stdout)

        scoped_links_result = run_cli(["links", "emh-1"], cwd=self.root)
        self.assertEqual(scoped_links_result.returncode, 0, scoped_links_result.stderr)
        self.assertIn("Related tickets for EMH-1", scoped_links_result.stdout)
        self.assertIn("EMH-2", scoped_links_result.stdout)
        self.assertIn("Second ticket", scoped_links_result.stdout)
        self.assertNotIn("EMH-1      First ticket", scoped_links_result.stdout)

        show_linked = run_cli(["show", "EMH-1"], cwd=self.root)
        self.assertEqual(show_linked.returncode, 0, show_linked.stderr)
        self.assertIn("Updated first ticket", show_linked.stdout)
        self.assertIn("Status: in_progress", show_linked.stdout)
        self.assertIn("Priority: high", show_linked.stdout)
        self.assertIn("Source: customer report", show_linked.stdout)
        self.assertIn("Related: EMH-2", show_linked.stdout)
        self.assertTrue(show_linked.stdout.strip().endswith("Related: EMH-2"))

        close_result = run_cli(
            ["close", "EMH-1", "--reason", "decided"],
            cwd=self.root,
        )
        self.assertEqual(close_result.returncode, 0, close_result.stderr)

        show_closed = run_cli(["show", "EMH-1"], cwd=self.root)
        self.assertEqual(show_closed.returncode, 0, show_closed.stderr)
        self.assertIn("Status: closed", show_closed.stdout)
        self.assertIn("Resolution Reason: decided", show_closed.stdout)
        self.assertTrue(show_closed.stdout.strip().endswith("Related: EMH-2"))

        with closing(sqlite3.connect(self.root / ".nira" / "nira.db")) as connection:
            row = connection.execute(
                "SELECT status, resolution_reason FROM tickets WHERE number = ?",
                (1,),
            ).fetchone()
        self.assertEqual(row, ("closed", "decided"))

        reopen_result = run_cli(["reopen", "EMH-1"], cwd=self.root)
        self.assertEqual(reopen_result.returncode, 0, reopen_result.stderr)

        unlink_result = run_cli(["unlink", "EMH-1", "EMH-2"], cwd=self.root)
        self.assertEqual(unlink_result.returncode, 0, unlink_result.stderr)

        no_links_result = run_cli(["links", "emh-1"], cwd=self.root)
        self.assertEqual(no_links_result.returncode, 0, no_links_result.stderr)
        self.assertIn("No related tickets found for EMH-1.", no_links_result.stdout)

        show_reopened = run_cli(["show", "EMH-1"], cwd=self.root)
        self.assertEqual(show_reopened.returncode, 0, show_reopened.stderr)
        self.assertIn("Status: open", show_reopened.stdout)
        self.assertNotIn("Related: EMH-2", show_reopened.stdout)

        with closing(sqlite3.connect(self.root / ".nira" / "nira.db")) as connection:
            reopened_row = connection.execute(
                "SELECT status, resolution_reason FROM tickets WHERE number = ?",
                (1,),
            ).fetchone()
        self.assertEqual(reopened_row, ("open", ""))

    def test_cli_edit_updates_body_and_resolution_with_editor(self):
        self.assertEqual(run_cli(["init", "--project-key", "EMH"], cwd=self.root).returncode, 0)
        self.assertEqual(run_cli(["new", "Editable ticket"], cwd=self.root).returncode, 0)

        body_editor = self.root / "body_editor.py"
        body_editor.write_text(
            "from pathlib import Path\n"
            "import sys\n"
            "path = Path(sys.argv[-1])\n"
            "path.write_text('## Summary\\nEdited body from test.\\n')\n"
        )

        resolution_editor = self.root / "resolution_editor.py"
        resolution_editor.write_text(
            "from pathlib import Path\n"
            "import sys\n"
            "path = Path(sys.argv[-1])\n"
            "path.write_text('## Resolution\\nResolution notes from test.\\n')\n"
        )

        edit_body_result = run_cli(
            ["edit", "EMH-1"],
            cwd=self.root,
            env={"EDITOR": f"{sys.executable} {body_editor}"},
        )
        self.assertEqual(edit_body_result.returncode, 0, edit_body_result.stderr)

        edit_resolution_result = run_cli(
            ["edit", "EMH-1", "--field", "resolution"],
            cwd=self.root,
            env={"EDITOR": f"{sys.executable} {resolution_editor}"},
        )
        self.assertEqual(edit_resolution_result.returncode, 0, edit_resolution_result.stderr)

        show_result = run_cli(["show", "EMH-1"], cwd=self.root)
        self.assertEqual(show_result.returncode, 0, show_result.stderr)
        self.assertIn("Edited body from test.", show_result.stdout)
        self.assertIn("Resolution notes from test.", show_result.stdout)

    def test_cli_serve_help_exposes_web_arguments(self):
        help_result = run_cli(["serve", "--help"], cwd=self.root)
        self.assertEqual(help_result.returncode, 0, help_result.stderr)
        self.assertIn("--host", help_result.stdout)
        self.assertIn("--port", help_result.stdout)
        self.assertIn("--reload", help_result.stdout)

    def test_cli_serve_reports_friendly_error_when_port_is_in_use(self):
        self.assertEqual(run_cli(["init"], cwd=self.root).returncode, 0)
        stderr = io.StringIO()
        with (
            mock.patch("nira_app.web.make_server", side_effect=OSError(48, "Address already in use")),
            redirect_stderr(stderr),
        ):
            exit_code = main(["--root", str(self.root), "serve", "--port", "8765"])

        self.assertEqual(exit_code, 1)
        self.assertIn("Could not start Nira server on http://127.0.0.1:8765", stderr.getvalue())
        self.assertNotIn("Traceback", stderr.getvalue())

    def test_cli_serve_exits_cleanly_on_keyboard_interrupt(self):
        self.assertEqual(run_cli(["init"], cwd=self.root).returncode, 0)

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
            exit_code = main(["--root", str(self.root), "serve", "--port", "8765"])

        self.assertEqual(exit_code, 0)
        self.assertIn("Serving Nira on http://127.0.0.1:8765", stdout.getvalue())
        self.assertEqual("", stderr.getvalue())

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
            mock.patch("nira_app.cli.build_reload_snapshot", return_value={"nira": (1, 1)}),
            mock.patch("nira_app.cli.subprocess.Popen", return_value=fake_process),
            mock.patch("nira_app.cli.time.sleep", side_effect=KeyboardInterrupt),
            redirect_stdout(stdout),
        ):
            serve_with_reload(None, "127.0.0.1", 8765)

        self.assertIn("Watching", stdout.getvalue())
        self.assertTrue(fake_process.terminated)
        self.assertFalse(fake_process.killed)

    def test_reload_snapshot_tracks_python_and_html_files(self):
        source_root = self.root / "src"
        (source_root / "nira_app" / "templates").mkdir(parents=True)
        (source_root / "nira_app" / "__pycache__").mkdir()

        entrypoint = source_root / "nira"
        entrypoint.write_text("#!/usr/bin/env python3\n", encoding="utf-8")
        python_file = source_root / "nira_app" / "web.py"
        python_file.write_text("print('hello')\n", encoding="utf-8")
        template_file = source_root / "nira_app" / "templates" / "page.html"
        template_file.write_text("<h1>Hello</h1>\n", encoding="utf-8")
        ignored_file = source_root / "notes.txt"
        ignored_file.write_text("ignore me\n", encoding="utf-8")
        ignored_cache = source_root / "nira_app" / "__pycache__" / "web.pyc"
        ignored_cache.write_bytes(b"compiled")

        snapshot = build_reload_snapshot(source_root)

        self.assertIn("nira", snapshot)
        self.assertIn("nira_app/web.py", snapshot)
        self.assertIn("nira_app/templates/page.html", snapshot)
        self.assertNotIn("notes.txt", snapshot)
        self.assertNotIn("nira_app/__pycache__/web.pyc", snapshot)

        template_file.write_text("<h1>Hello again</h1>\n", encoding="utf-8")
        updated_snapshot = build_reload_snapshot(source_root)
        self.assertNotEqual(snapshot, updated_snapshot)

    def test_cli_help_command_prints_root_and_command_help(self):
        root_help = run_cli(["help"], cwd=self.root)
        self.assertEqual(root_help.returncode, 0, root_help.stderr)
        self.assertIn("usage: nira", root_help.stdout)
        self.assertIn("serve", root_help.stdout)
        self.assertNotIn("comment", root_help.stdout)

        command_help = run_cli(["help", "new"], cwd=self.root)
        self.assertEqual(command_help.returncode, 0, command_help.stderr)
        self.assertIn("usage: nira new", command_help.stdout)
        self.assertIn("--priority", command_help.stdout)

        links_help = run_cli(["help", "links"], cwd=self.root)
        self.assertEqual(links_help.returncode, 0, links_help.stderr)
        self.assertIn("usage: nira links", links_help.stdout)


class HttpIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.store = NiraStore(self.root)
        self.store.initialize(default_project="EMH")
        self.app = NiraWebApp(self.store)

    def tearDown(self):
        self.tempdir.cleanup()

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
        self.assertEqual(status, 200)
        self.assertIn("Nira", body)
        self.assertIn('href="/assets/nira.png"', body)
        self.assertIn(">NIRA</span>", body)
        self.assertIn('href="/">Ticket List</a>', body)
        self.assertIn('href="/tickets/new">Create Ticket</a>', body)
        self.assertIn('href="/settings">Settings</a>', body)
        self.assertNotIn('name="project"', body)
        self.assertIn("not closed", body)
        self.assertIn("selected>not closed</option>", body)
        self.assertIn('name="sort"', body)
        self.assertIn('name="direction"', body)
        self.assertIn('selected>updated</option>', body)
        self.assertIn('href="/?status=not_closed&amp;sort=ticket_id&amp;direction=desc"', body)
        self.assertIn('href="/?status=not_closed&amp;sort=updated&amp;direction=asc"', body)

        status, _, body = self.request("GET", "/tickets/new")
        self.assertEqual(status, 200)
        self.assertIn("Create Ticket", body)
        self.assertIn('name="type"', body)
        self.assertIn('<option value="bug">bug</option>', body)
        self.assertIn("toastui-editor.min.css", body)
        self.assertIn("toastui-editor-all.min.js", body)
        self.assertIn('data-rich-editor="body_md"', body)
        self.assertNotIn('data-rich-editor="resolution_md"', body)
        self.assertNotIn("Resolution Notes", body)
        self.assertLess(body.index('for="title"'), body.index('data-rich-editor="body_md"'))
        self.assertLess(body.index('data-rich-editor="body_md"'), body.index('for="project_display"'))

        status, _, settings_page = self.request("GET", "/settings")
        self.assertEqual(status, 200)
        self.assertIn("Workspace Prefix", settings_page)
        self.assertIn('value="EMH"', settings_page)
        self.assertIn("Ticket labels are derived", settings_page)

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
        self.assertEqual(status, 303)
        self.assertEqual(headers["Location"], "/tickets/EMH-1")

        status, _, detail = self.request("GET", "/tickets/EMH-1")
        self.assertEqual(status, 200)
        self.assertIn("First HTTP ticket", detail)
        self.assertIn('href="/">', detail)
        self.assertIn('href="/tickets/new">Create Ticket</a>', detail)
        self.assertIn('href="/settings">Settings</a>', detail)
        self.assertIn("Created through the browser.", detail)
        self.assertIn("badge", detail)
        self.assertIn("text-bg-secondary", detail)

        status, _, preview = self.request(
            "POST",
            "/preview",
            fields={"markdown": "## Preview\n- item one\n- item two\n`code`\n"},
        )
        self.assertEqual(status, 200)
        self.assertIn("<h2>Preview</h2>", preview)
        self.assertIn("<li>item one</li>", preview)
        self.assertIn("<code>code</code>", preview)

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
        self.assertEqual(status, 303)
        self.assertEqual(headers["Location"], "/tickets/EMH-1")

        status, _, detail = self.request("GET", "/tickets/EMH-1")
        self.assertEqual(status, 200)
        self.assertIn("First HTTP ticket updated", detail)
        self.assertIn("docs/architecture/data-stack.md", detail)
        self.assertIn("in_progress", detail)
        self.assertIn("Updated body through the browser.", detail)
        self.assertIn("Pending final decision.", detail)
        self.assertIn("just now", detail)
        self.assertNotIn('data-rich-editor="resolution_md"', detail)
        self.assertIn("bg-primary-subtle", detail)
        self.assertIn("text-bg-primary", detail)
        self.assertIn('data-auto-submit', detail)
        self.assertLess(detail.index('for="title"'), detail.index("Body</h2>"))
        self.assertLess(detail.index("Body</h2>"), detail.index("Save</button>"))
        self.assertLess(detail.index("Save</button>"), detail.index("Resolution Notes</h2>"))
        self.assertLess(detail.index("Body</h2>"), detail.index("Resolution Notes</h2>"))
        self.assertLess(detail.index("Resolution Notes</h2>"), detail.index("Related</h2>"))
        self.assertLess(detail.index("Related</h2>"), detail.index("Status</h2>"))
        self.assertLess(detail.index("Status</h2>"), detail.index("Details</h2>"))
        self.assertNotIn("Edit Ticket", detail)
        self.assertNotIn("Add Comment", detail)
        self.assertNotIn("Comments", detail)
        self.assertNotIn("Track the ticket state here while the content stays front and center.", detail)
        self.assertNotIn("Close Ticket", detail)
        self.assertNotIn("Workflow", detail)
        self.assertNotIn('name="resolution_reason"', detail)
        self.assertNotIn("Resolution</dt>", detail)

        ticket = self.store.get_ticket("EMH-1")
        self.assertEqual(ticket["type"], "bug")

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
        self.assertEqual(status, 303)
        self.assertEqual(headers["Location"], "/tickets/EMH-2")

        status, headers, _ = self.request(
            "POST",
            "/tickets/EMH-1/link",
            fields={"other_ticket_id": "EMH-2"},
        )
        self.assertEqual(status, 303)
        self.assertEqual(headers["Location"], "/tickets/EMH-1")

        status, _, detail = self.request("GET", "/tickets/EMH-1")
        self.assertEqual(status, 200)
        self.assertIn("/tickets/EMH-2", detail)

        status, _, list_page = self.request("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn('href="/tickets/EMH-1"', list_page)
        self.assertIn('href="/tickets/EMH-2"', list_page)
        self.assertIn("just now", list_page)

        status, _, priority_sorted = self.request("GET", "/?status=all&sort=priority&direction=desc")
        self.assertEqual(status, 200)
        self.assertLess(priority_sorted.index('href="/tickets/EMH-1"'), priority_sorted.index('href="/tickets/EMH-2"'))

        status, _, id_sorted = self.request("GET", "/?status=all&sort=ticket_id&direction=desc")
        self.assertEqual(status, 200)
        self.assertLess(id_sorted.index('href="/tickets/EMH-2"'), id_sorted.index('href="/tickets/EMH-1"'))

        status, headers, _ = self.request(
            "POST",
            "/settings",
            fields={"default_project": "NIRA"},
        )
        self.assertEqual(status, 303)
        self.assertEqual(headers["Location"], "/settings?saved=1")

        status, _, settings_page = self.request("GET", "/settings?saved=1")
        self.assertEqual(status, 200)
        self.assertIn("Workspace settings updated.", settings_page)
        self.assertIn('value="NIRA"', settings_page)
        self.assertEqual(self.store.get_default_project(), "NIRA")

        status, _, renamed_detail = self.request("GET", "/tickets/EMH-1")
        self.assertEqual(status, 200)
        self.assertIn("NIRA-1 First HTTP ticket updated", renamed_detail)

        status, _, renamed_list = self.request("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn('href="/tickets/NIRA-1"', renamed_list)
        self.assertIn('href="/tickets/NIRA-2"', renamed_list)

        status, _, renamed_new = self.request("GET", "/tickets/new")
        self.assertEqual(status, 200)
        self.assertIn('value="NIRA-"', renamed_new)

        status, _, missing_comment_route = self.request(
            "POST",
            "/tickets/EMH-1/comment",
            fields={"body_md": "Browser comment body."},
        )
        self.assertEqual(status, 404)
        self.assertIn("Page not found.", missing_comment_route)

        status, headers, _ = self.request(
            "POST",
            "/tickets/EMH-1/close",
            fields={},
        )
        self.assertEqual(status, 303)
        self.assertEqual(headers["Location"], "/tickets/EMH-1")

        status, _, detail = self.request("GET", "/tickets/EMH-1")
        self.assertEqual(status, 200)
        self.assertIn("closed", detail)
        self.assertIn("completed", detail)
        self.assertIn("text-bg-success", detail)

        status, _, list_page = self.request("GET", "/")
        self.assertEqual(status, 200)
        self.assertNotIn('href="/tickets/NIRA-1"', list_page)
        self.assertIn('href="/tickets/NIRA-2"', list_page)

        status, _, closed_list_page = self.request("GET", "/?status=closed")
        self.assertEqual(status, 200)
        self.assertIn('href="/tickets/NIRA-1"', closed_list_page)
        self.assertNotIn('href="/tickets/NIRA-2"', closed_list_page)

        status, headers, _ = self.request("POST", "/tickets/EMH-1/reopen", fields={})
        self.assertEqual(status, 303)
        self.assertEqual(headers["Location"], "/tickets/EMH-1")

        status, _, detail = self.request("GET", "/tickets/EMH-1")
        self.assertEqual(status, 200)
        self.assertIn("open", detail)

        status, headers, _ = self.request(
            "POST",
            "/tickets/EMH-1/unlink",
            fields={"other_ticket_id": "EMH-2"},
        )
        self.assertEqual(status, 303)
        self.assertEqual(headers["Location"], "/tickets/EMH-1")

        status, _, detail = self.request("GET", "/tickets/EMH-1")
        self.assertEqual(status, 200)
        self.assertNotIn('href="/tickets/EMH-2"', detail)

    def test_asset_endpoint_serves_logo_png(self):
        status, headers, body = self.request("GET", "/assets/nira.png", decode=False)
        self.assertEqual(status, 200)
        self.assertEqual(headers["Content-Type"], "image/png")
        self.assertTrue(body.startswith(b"\x89PNG\r\n\x1a\n"))


if __name__ == "__main__":
    unittest.main()
