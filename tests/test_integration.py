import io
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import TypedDict
from urllib.parse import urlencode, urlsplit

from wsgiref.util import setup_testing_defaults

from nira_app.storage import NiraStore
from nira_app.web import NiraWebApp


REPO_ROOT = Path(__file__).resolve().parents[1]
CLI_PATH = REPO_ROOT / "nira"


class ResponseCapture(TypedDict, total=False):
    status: str
    headers: dict[str, str]


def run_cli(args, cwd, env=None, input_text=None, timeout=10):
    merged_env = os.environ.copy()
    merged_env["PYTHONUNBUFFERED"] = "1"
    if env:
        merged_env.update(env)

    return subprocess.run(
        [sys.executable, str(CLI_PATH), *args],
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
        init_result = run_cli(["init"], cwd=self.root)
        self.assertEqual(init_result.returncode, 0, init_result.stderr)
        self.assertTrue((self.root / ".nira" / "nira.db").exists())

        create_result = run_cli(
            [
                "create",
                "EMH",
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

    def test_cli_update_link_close_and_reopen(self):
        self.assertEqual(run_cli(["init"], cwd=self.root).returncode, 0)
        self.assertEqual(
            run_cli(["new", "EMH", "First ticket", "--source", "user"], cwd=self.root).returncode,
            0,
        )
        self.assertEqual(
            run_cli(["new", "EMH", "Second ticket", "--source", "user"], cwd=self.root).returncode,
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

        show_linked = run_cli(["show", "EMH-1"], cwd=self.root)
        self.assertEqual(show_linked.returncode, 0, show_linked.stderr)
        self.assertIn("Updated first ticket", show_linked.stdout)
        self.assertIn("Status: in_progress", show_linked.stdout)
        self.assertIn("Priority: high", show_linked.stdout)
        self.assertIn("Source: customer report", show_linked.stdout)
        self.assertIn("Related: EMH-2", show_linked.stdout)

        close_result = run_cli(
            ["close", "EMH-1", "--reason", "decided"],
            cwd=self.root,
        )
        self.assertEqual(close_result.returncode, 0, close_result.stderr)

        show_closed = run_cli(["show", "EMH-1"], cwd=self.root)
        self.assertEqual(show_closed.returncode, 0, show_closed.stderr)
        self.assertIn("Status: closed", show_closed.stdout)
        self.assertIn("Resolution Reason: decided", show_closed.stdout)

        with sqlite3.connect(self.root / ".nira" / "nira.db") as connection:
            row = connection.execute(
                "SELECT status, resolution_reason FROM tickets WHERE id = ?",
                ("EMH-1",),
            ).fetchone()
        self.assertEqual(row, ("closed", "decided"))

        reopen_result = run_cli(["reopen", "EMH-1"], cwd=self.root)
        self.assertEqual(reopen_result.returncode, 0, reopen_result.stderr)

        unlink_result = run_cli(["unlink", "EMH-1", "EMH-2"], cwd=self.root)
        self.assertEqual(unlink_result.returncode, 0, unlink_result.stderr)

        show_reopened = run_cli(["show", "EMH-1"], cwd=self.root)
        self.assertEqual(show_reopened.returncode, 0, show_reopened.stderr)
        self.assertIn("Status: open", show_reopened.stdout)
        self.assertNotIn("Related: EMH-2", show_reopened.stdout)

        with sqlite3.connect(self.root / ".nira" / "nira.db") as connection:
            reopened_row = connection.execute(
                "SELECT status, resolution_reason FROM tickets WHERE id = ?",
                ("EMH-1",),
            ).fetchone()
        self.assertEqual(reopened_row, ("open", ""))

    def test_cli_edit_updates_body_and_resolution_with_editor(self):
        self.assertEqual(run_cli(["init"], cwd=self.root).returncode, 0)
        self.assertEqual(run_cli(["new", "EMH", "Editable ticket"], cwd=self.root).returncode, 0)

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


class HttpIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.store = NiraStore(self.root)
        self.store.initialize()
        self.app = NiraWebApp(self.store)

    def tearDown(self):
        self.tempdir.cleanup()

    def request(self, method, path, fields=None, headers=None):
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
        payload = b"".join(chunks).decode("utf-8")
        assert "status" in captured
        assert "headers" in captured
        status_code = int(captured["status"].split()[0])
        return status_code, captured["headers"], payload

    def test_http_endpoints_cover_full_crud_flow(self):
        status, _, body = self.request("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn("Nira", body)
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
        self.assertNotIn('href="/tickets/EMH-1"', list_page)
        self.assertIn('href="/tickets/EMH-2"', list_page)

        status, _, closed_list_page = self.request("GET", "/?status=closed")
        self.assertEqual(status, 200)
        self.assertIn('href="/tickets/EMH-1"', closed_list_page)
        self.assertNotIn('href="/tickets/EMH-2"', closed_list_page)

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


if __name__ == "__main__":
    unittest.main()
