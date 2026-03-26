import pytest
from pathlib import Path
import tempfile
import io
import os
import re
import sqlite3
import sys
from contextlib import closing, redirect_stderr, redirect_stdout
from unittest import mock

from nira_app.cli import app, main
from typer.testing import CliRunner

_typer_runner = CliRunner()


def run_cli(args, cwd, env=None, input_text=None, timeout=20):
    original_cwd = os.getcwd()
    os.chdir(cwd)
    original_env = os.environ.copy()

    os.environ["COLUMNS"] = "120"
    os.environ["TERMINAL_WIDTH"] = "120"

    if env:
        os.environ.update(env)

    try:
        result = _typer_runner.invoke(app, args, input=input_text, env=env)

        # Remove ANSI escape sequences from standard output so assertions are robust
        # even if Rich forces styles.
        stdout_clean = re.sub(r"\x1b\[[0-9;]*m", "", result.stdout or "")
        stderr_clean = re.sub(r"\x1b\[[0-9;]*m", "", result.stderr or "")

        import subprocess

        return subprocess.CompletedProcess(
            args=["nira", *args],
            returncode=result.exit_code,
            stdout=stdout_clean,
            stderr=stderr_clean,
        )
    finally:
        os.chdir(original_cwd)
        os.environ.clear()
        os.environ.update(original_env)


@pytest.fixture
def temp_root():
    with tempfile.TemporaryDirectory() as td:
        yield Path(td)


class TestCliIntegration:
    def test_cli_init_new_show_list_and_aliases(self, temp_root):
        init_result = run_cli(["init", "--project-key", "NIRA"], cwd=temp_root)
        assert init_result.returncode == 0
        assert (temp_root / ".nira" / "nira.db").exists()

        create_result = run_cli(
            [
                "create",
                "Evaluate Tortoise alternatives",
                "--source",
                "NIRA-16 architecture review",
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
        assert "NIRA-1" in create_result.stdout

        show_result = run_cli(["get", "NIRA-1"], cwd=temp_root)
        assert show_result.returncode == 0
        assert "NIRA-1" in show_result.stdout
        assert "open" in show_result.stdout
        assert "decision" in show_result.stdout
        assert "medium" in show_result.stdout
        assert "NIRA-16 architecture review" in show_result.stdout
        assert "Summary" in show_result.stdout

        list_result = run_cli(["list"], cwd=temp_root)
        assert list_result.returncode == 0
        assert "NIRA-1" in list_result.stdout
        assert "Evaluate Tortoise alternatives" in list_result.stdout

    def test_cli_new_does_not_treat_uppercase_title_words_as_project_keys(self, temp_root):
        init_result = run_cli(["init", "--project-key", "NIRA"], cwd=temp_root)
        assert init_result.returncode == 0

        create_result = run_cli(["new", "HTTP 500 on login"], cwd=temp_root)
        assert create_result.returncode == 0
        assert "NIRA-1" in create_result.stdout

        show_result = run_cli(["show", "NIRA-1"], cwd=temp_root)
        assert show_result.returncode == 0
        assert "HTTP 500 on login" in show_result.stdout

    def test_cli_init_uses_folder_name_as_default_project_key(self, temp_root):
        workspace = temp_root / "nira"
        workspace.mkdir()

        init_result = run_cli(["init"], cwd=workspace)
        assert init_result.returncode == 0

        new_result = run_cli(
            ["new", "Ticket created from workspace default"],
            cwd=workspace,
        )
        assert new_result.returncode == 0
        assert "NIRA-1" in new_result.stdout

    def test_cli_init_uses_acronym_for_multi_word_folder_names(self, temp_root):
        workspaces = [
            (temp_root / "new-issue-reporting-app", "NIRA"),
            (temp_root / "new issue reporting app", "NIRA"),
            (temp_root / "NewIssueReportingApp", "NIRA"),
            (temp_root / "employment-matching-hub", "EMH"),
            (temp_root / "EmploymentMatchingHub", "EMH"),
            (temp_root / "my-test-project", "MTP"),
        ]

        for workspace, expected_key in workspaces:
            workspace.mkdir()
            init_result = run_cli(["init"], cwd=workspace)
            assert init_result.returncode == 0

            new_result = run_cli(["new", "Ticket created from workspace acronym"], cwd=workspace)
            assert new_result.returncode == 0
            assert f"{expected_key}-1" in new_result.stdout

    def test_cli_update_link_close_and_reopen(self, temp_root):
        assert run_cli(["init", "--project-key", "NIRA"], cwd=temp_root).returncode == 0
        assert run_cli(["new", "First ticket", "--source", "user"], cwd=temp_root).returncode == 0
        assert run_cli(["new", "Second ticket", "--source", "user"], cwd=temp_root).returncode == 0

        update_result = run_cli(
            [
                "update",
                "NIRA-1",
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

        link_result = run_cli(["link", "NIRA-1", "NIRA-2"], cwd=temp_root)
        assert link_result.returncode == 0

        links_result = run_cli(["links"], cwd=temp_root)
        assert links_result.returncode == 0
        assert "NIRA-1" in links_result.stdout
        assert "NIRA-2" in links_result.stdout

        scoped_links_result = run_cli(["links", "nira-1"], cwd=temp_root)
        assert scoped_links_result.returncode == 0
        assert "Related tickets for" in scoped_links_result.stdout
        assert "NIRA-1" in scoped_links_result.stdout
        assert "NIRA-2" in scoped_links_result.stdout
        assert "Second ticket" in scoped_links_result.stdout
        assert "NIRA-1      First ticket" not in scoped_links_result.stdout

        show_linked = run_cli(["show", "NIRA-1"], cwd=temp_root)
        assert show_linked.returncode == 0
        assert "Updated first ticket" in show_linked.stdout
        assert "in_progress" in show_linked.stdout
        assert "high" in show_linked.stdout
        assert "customer report" in show_linked.stdout
        assert "Related" in show_linked.stdout
        assert "NIRA-2" in show_linked.stdout

        close_result = run_cli(
            ["close", "NIRA-1", "--notes", "## Resolution\ndecided"],
            cwd=temp_root,
        )
        assert close_result.returncode == 0

        show_closed = run_cli(["show", "NIRA-1"], cwd=temp_root)
        assert show_closed.returncode == 0
        assert "closed" in show_closed.stdout
        assert "decided" in show_closed.stdout
        assert "NIRA-2" in show_closed.stdout

        with closing(sqlite3.connect(temp_root / ".nira" / "nira.db")) as connection:
            row = connection.execute(
                "SELECT status, resolution_reason, resolution_md FROM tickets WHERE number = ?",
                (1,),
            ).fetchone()
        assert row == ("closed", "completed", "## Resolution\ndecided")

        reopen_result = run_cli(["reopen", "NIRA-1"], cwd=temp_root)
        assert reopen_result.returncode == 0

        unlink_result = run_cli(["unlink", "NIRA-1", "NIRA-2"], cwd=temp_root)
        assert unlink_result.returncode == 0

        no_links_result = run_cli(["links", "nira-1"], cwd=temp_root)
        assert no_links_result.returncode == 0
        assert "No related tickets found" in no_links_result.stdout

        show_reopened = run_cli(["show", "NIRA-1"], cwd=temp_root)
        assert show_reopened.returncode == 0
        assert "open" in show_reopened.stdout
        assert "NIRA-2" not in show_reopened.stdout

        with closing(sqlite3.connect(temp_root / ".nira" / "nira.db")) as connection:
            reopened_row = connection.execute(
                "SELECT status, resolution_reason FROM tickets WHERE number = ?",
                (1,),
            ).fetchone()
        assert reopened_row == ("open", "")

    def test_cli_edit_updates_body_and_resolution_with_editor(self, temp_root):
        assert run_cli(["init", "--project-key", "NIRA"], cwd=temp_root).returncode == 0
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
            ["edit", "NIRA-1"],
            cwd=temp_root,
            env={"EDITOR": f"{sys.executable} {body_editor}"},
        )
        assert edit_body_result.returncode == 0

        edit_resolution_result = run_cli(
            ["edit", "NIRA-1", "--field", "resolution"],
            cwd=temp_root,
            env={"EDITOR": f"{sys.executable} {resolution_editor}"},
        )
        assert edit_resolution_result.returncode == 0

        show_result = run_cli(["show", "NIRA-1"], cwd=temp_root)
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

    def test_cli_global_options_and_resolve_store(self, temp_root):
        from nira_app.cli import resolve_store

        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as jail:
            jail_path = Path(jail).resolve()
            res = run_cli(["--root", str(jail_path), "list"], cwd=".")
            assert res.returncode == 1
            assert "Error" in res.stdout

            store = resolve_store(jail_path, create=True)
            assert store.root == jail_path

            res = run_cli(["--root", str(jail_path), "init"], cwd=".")
            assert res.returncode == 0

            res = run_cli(["--root", str(jail_path), "list"], cwd=".")
            assert res.returncode == 0
            assert "No tickets found" in res.stdout

    def test_cli_not_found_errors(self, temp_root):
        run_cli(["init"], cwd=temp_root)

        res = run_cli(["show", "MISSING-1"], cwd=temp_root)
        assert res.returncode == 1
        assert "Error" in res.stdout

        res = run_cli(["update", "MISSING-1"], cwd=temp_root)
        assert res.returncode == 1
        assert "Error" in res.stdout

        res = run_cli(["close", "MISSING-1", "--notes", "testing"], cwd=temp_root)
        assert res.returncode == 1
        assert "Error" in res.stdout

        res = run_cli(["links", "MISSING-1"], cwd=temp_root)
        assert res.returncode == 1
        assert "Error" in res.stdout

    def test_cli_missing_root_dir_without_explicit_root(self, temp_root):
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            res = run_cli(["list"], cwd=td)
            assert res.returncode == 1
            assert "Error" in res.stdout

    def test_cli_explicit_missing_root_no_create(self, temp_root):
        import tempfile
        from pathlib import Path
        from nira_app.cli import resolve_store
        import pytest

        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "missing"
            with pytest.raises(Exception, match="No .nira directory"):
                resolve_store(p, create=False)
