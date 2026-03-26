import re
import os
import subprocess
import time
import json
from pathlib import Path
from nira_app.cli import app, main
from typer.testing import CliRunner
from nira_app.models import TicketDetails, TicketData
from nira_app.services import TicketService

_typer_runner = CliRunner()


def run_cli_cov(args, cwd=".", input=None):
    original_cwd = os.getcwd()
    os.chdir(cwd)
    try:
        # result.output contains both stdout and stderr by default
        result = _typer_runner.invoke(app, args, input=input, catch_exceptions=False)
        clean_output = re.sub(r"\x1b\[[0-9;]*m", "", result.output)
        return result.exit_code, clean_output, ""
    finally:
        os.chdir(original_cwd)


def test_cli_init_error(temp_root, monkeypatch):
    from nira_app.storage import NiraStore, NiraError

    def mock_init(*args, **kwargs):
        raise NiraError("Mock init error")

    monkeypatch.setattr(NiraStore, "initialize", mock_init)
    code, out, _ = run_cli_cov(["init", "--project-key", "FAIL"], cwd=temp_root)
    assert code == 1
    assert "Mock init error" in out


def test_cli_create_error(temp_root, monkeypatch):
    run_cli_cov(["init", "--project-key", "NIRA"], cwd=temp_root)
    from nira_app.storage import NiraError

    def mock_create(*args, **kwargs):
        raise NiraError("Mock create error")

    monkeypatch.setattr(TicketService, "create_ticket", mock_create)
    code, out, _ = run_cli_cov(["create", "Title"], cwd=temp_root)
    assert code == 1
    assert "Mock create error" in out


def test_cli_show_error(temp_root, monkeypatch):
    run_cli_cov(["init", "--project-key", "NIRA"], cwd=temp_root)
    from nira_app.storage import NiraError

    def mock_details(*args, **kwargs):
        raise NiraError("Mock show error")

    monkeypatch.setattr(TicketService, "ticket_details", mock_details)
    code, out, _ = run_cli_cov(["show", "NIRA-1"], cwd=temp_root)
    assert code == 1
    assert "Mock show error" in out


def test_cli_list_error(temp_root, monkeypatch):
    run_cli_cov(["init", "--project-key", "NIRA"], cwd=temp_root)
    from nira_app.storage import NiraError

    def mock_list(*args, **kwargs):
        raise NiraError("Mock list error")

    monkeypatch.setattr(TicketService, "list_tickets", mock_list)
    code, out, _ = run_cli_cov(["list"], cwd=temp_root)
    assert code == 1
    assert "Mock list error" in out


def test_cli_update_error(temp_root, monkeypatch):
    run_cli_cov(["init", "--project-key", "NIRA"], cwd=temp_root)
    run_cli_cov(["create", "T1"], cwd=temp_root)
    from nira_app.storage import NiraError

    def mock_update(*args, **kwargs):
        raise NiraError("Mock update error")

    monkeypatch.setattr(TicketService, "update_ticket", mock_update)
    code, out, _ = run_cli_cov(["update", "NIRA-1", "--title", "New"], cwd=temp_root)
    assert code == 1
    assert "Mock update error" in out


def test_cli_comment_error(temp_root, monkeypatch):
    run_cli_cov(["init", "--project-key", "NIRA"], cwd=temp_root)
    run_cli_cov(["create", "T1"], cwd=temp_root)
    from nira_app.storage import NiraError

    def mock_comment(*args, **kwargs):
        raise NiraError("Mock comment error")

    monkeypatch.setattr(TicketService, "add_comment", mock_comment)
    # Use -m to avoid editor
    code, out, _ = run_cli_cov(["comment", "NIRA-1", "-m", "msg"], cwd=temp_root)
    assert code == 1
    assert "Mock comment error" in out


def test_cli_comment_success(temp_root, monkeypatch):
    run_cli_cov(["init", "--project-key", "NIRA"], cwd=temp_root)
    run_cli_cov(["create", "T1"], cwd=temp_root)
    code, out, _ = run_cli_cov(["comment", "NIRA-1", "-m", "msg"], cwd=temp_root)
    assert code == 0
    assert "Added comment" in out


def test_cli_close_error(temp_root, monkeypatch):
    run_cli_cov(["init", "--project-key", "NIRA"], cwd=temp_root)
    run_cli_cov(["create", "T1"], cwd=temp_root)
    from nira_app.storage import NiraError

    def mock_close(*args, **kwargs):
        raise NiraError("Mock close error")

    monkeypatch.setattr(TicketService, "close_ticket", mock_close)
    code, out, _ = run_cli_cov(["close", "NIRA-1", "-m", "done"], cwd=temp_root)
    assert code == 1
    assert "Mock close error" in out


def test_cli_reopen_error(temp_root, monkeypatch):
    run_cli_cov(["init", "--project-key", "NIRA"], cwd=temp_root)
    run_cli_cov(["create", "T1"], cwd=temp_root)
    run_cli_cov(["close", "NIRA-1", "-m", "done"], cwd=temp_root)
    from nira_app.storage import NiraError

    def mock_reopen(*args, **kwargs):
        raise NiraError("Mock reopen error")

    monkeypatch.setattr(TicketService, "reopen_ticket", mock_reopen)
    code, out, _ = run_cli_cov(["reopen", "NIRA-1"], cwd=temp_root)
    assert code == 1
    assert "Mock reopen error" in out


def test_cli_link_error(temp_root, monkeypatch):
    run_cli_cov(["init", "--project-key", "NIRA"], cwd=temp_root)
    run_cli_cov(["create", "T1"], cwd=temp_root)
    run_cli_cov(["create", "T2"], cwd=temp_root)
    from nira_app.storage import NiraError

    def mock_link(*args, **kwargs):
        raise NiraError("Mock link error")

    monkeypatch.setattr(TicketService, "link_tickets", mock_link)
    code, out, _ = run_cli_cov(["link", "NIRA-1", "NIRA-2"], cwd=temp_root)
    assert code == 1
    assert "Mock link error" in out


def test_cli_unlink_error(temp_root, monkeypatch):
    run_cli_cov(["init", "--project-key", "NIRA"], cwd=temp_root)
    run_cli_cov(["create", "T1"], cwd=temp_root)
    run_cli_cov(["create", "T2"], cwd=temp_root)
    run_cli_cov(["link", "NIRA-1", "NIRA-2"], cwd=temp_root)
    from nira_app.storage import NiraError

    def mock_unlink(*args, **kwargs):
        raise NiraError("Mock unlink error")

    monkeypatch.setattr(TicketService, "unlink_tickets", mock_unlink)
    code, out, _ = run_cli_cov(["unlink", "NIRA-1", "NIRA-2"], cwd=temp_root)
    assert code == 1
    assert "Mock unlink error" in out


def test_cli_links_error(temp_root, monkeypatch):
    run_cli_cov(["init", "--project-key", "NIRA"], cwd=temp_root)
    run_cli_cov(["create", "T1"], cwd=temp_root)
    from nira_app.storage import NiraError

    def mock_links(*args, **kwargs):
        raise NiraError("Mock links error")

    monkeypatch.setattr(TicketService, "list_links", mock_links)
    code, out, _ = run_cli_cov(["links", "NIRA-1"], cwd=temp_root)
    assert code == 1
    assert "Mock links error" in out


def test_cli_serve_error(temp_root, monkeypatch):
    run_cli_cov(["init", "--project-key", "NIRA"], cwd=temp_root)
    import nira_app.cli

    def mock_serve(*args, **kwargs):
        from nira_app.storage import ValidationError

        raise ValidationError("Mock serve error")

    monkeypatch.setattr(nira_app.cli, "run_server", mock_serve)
    code, out, _ = run_cli_cov(["serve"], cwd=temp_root)
    assert code == 1
    assert "Mock serve error" in out


def test_cli_edit_branches(temp_root, monkeypatch):
    run_cli_cov(["init", "--project-key", "NIRA"], cwd=temp_root)
    run_cli_cov(["create", "T1"], cwd=temp_root)
    import click

    monkeypatch.setattr(click, "edit", lambda text, **kwargs: "Edited body")
    run_cli_cov(["edit", "NIRA-1"], cwd=temp_root)
    run_cli_cov(["edit", "NIRA-1", "--field", "resolution"], cwd=temp_root)
    code, out, _ = run_cli_cov(["edit", "NIRA-1", "--field", "invalid"], cwd=temp_root)
    assert code == 1


def test_cli_update_more(temp_root):
    run_cli_cov(["init", "--project-key", "NIRA"], cwd=temp_root)
    run_cli_cov(["create", "T1"], cwd=temp_root)
    run_cli_cov(["create", "T2"], cwd=temp_root)
    code, out, _ = run_cli_cov(["update", "NIRA-2", "--parent", "NIRA-1"], cwd=temp_root)
    assert code == 0
    code, out, _ = run_cli_cov(["update", "NIRA-2", "--parent", ""], cwd=temp_root)
    assert code == 0
    assert "Updated NIRA-2" in out


def test_cli_create_logic_branches(temp_root):
    run_cli_cov(["init", "--project-key", "NIRA"], cwd=temp_root)
    code, out, _ = run_cli_cov(
        ["create", "T1", "--source", "s", "--type", "bug", "--priority", "high", "--labels", "l"], cwd=temp_root
    )
    assert code == 0
    code, out, _ = run_cli_cov(["create", "T2", "--parent", "MISSING-999"], cwd=temp_root)
    assert code == 1
    assert "Error" in out


def test_cli_help_command(temp_root):
    code, out, _ = run_cli_cov(["help", "list"], cwd=temp_root)
    assert code == 0
    assert "List tickets" in out


def test_cli_aliases(temp_root):
    run_cli_cov(["init", "--project-key", "NIRA"], cwd=temp_root)
    code, out, _ = run_cli_cov(["get", "NIRA-1"], cwd=temp_root)
    assert "Error" in out or "NIRA-1" in out
    code, out, _ = run_cli_cov(["create", "Alias test"], cwd=temp_root)
    assert code == 0


def test_cli_print_ticket_variations(temp_root):
    from nira_app.cli import print_ticket
    from nira_app.storage import NiraStore

    store = NiraStore(temp_root)
    store.initialize("NIRA")

    # Use type: ignore for partial TypedDicts in tests
    details: TicketDetails = {
        "ticket": {
            "id": "NIRA-1",
            "title": "T1",
            "status": "open",
            "type": "task",
            "priority": "critical",
            "labels": "l1,l2",
            "source": "s1",
            "due_date": "2024-01-01",
            "created_at": "...",
            "updated_at": "...",
            "body_md": "body",
            "resolution_reason": "res",
            "db_id": 1,
            "project": "NIRA",
            "number": 1,
            "resolution_md": "",
            "parent_id": None,
            "parent_number": None,
            "story_points": None,
        },
        "related": [
            {
                "id": "NIRA-2",
                "title": "T2",
                "project": "NIRA",
                "number": 2,
                "db_id": 2,
                "status": "open",
                "type": "task",
                "priority": "medium",
                "source": "",
                "resolution_reason": "",
                "labels": "",
                "due_date": None,
                "parent_id": None,
                "parent_number": None,
                "story_points": None,
                "body_md": "",
                "resolution_md": "",
                "created_at": "",
                "updated_at": "",
            }
        ],
        "sub_tasks": [
            {
                "id": "NIRA-3",
                "title": "T3",
                "project": "NIRA",
                "number": 3,
                "db_id": 3,
                "status": "open",
                "type": "task",
                "priority": "medium",
                "source": "",
                "resolution_reason": "",
                "labels": "",
                "due_date": None,
                "parent_id": None,
                "parent_number": None,
                "story_points": None,
                "body_md": "",
                "resolution_md": "",
                "created_at": "",
                "updated_at": "",
            }
        ],
        "parent": {
            "id": "NIRA-0",
            "title": "P1",
            "project": "NIRA",
            "number": 0,
            "db_id": 0,
            "status": "open",
            "type": "task",
            "priority": "medium",
            "source": "",
            "resolution_reason": "",
            "labels": "",
            "due_date": None,
            "parent_id": None,
            "parent_number": None,
            "story_points": None,
            "body_md": "",
            "resolution_md": "",
            "created_at": "",
            "updated_at": "",
        },
        "comments": [{"id": 1, "created_at": "...", "body_md": "c1", "ticket_id": "NIRA-1"}],
        "history": [],
    }
    print_ticket(details, store)


def test_cli_print_list_empty(temp_root):
    from nira_app.cli import print_ticket_list
    from nira_app.storage import NiraStore

    store = NiraStore(temp_root)
    store.initialize("NIRA")
    print_ticket_list([], store)


def test_cli_list_variations(temp_root):
    from nira_app.cli import print_ticket_list
    from nira_app.storage import NiraStore

    store = NiraStore(temp_root)
    store.initialize("NIRA")
    tickets: list[TicketData] = [
        {
            "id": "NIRA-1",
            "status": "open",
            "priority": "low",
            "type": "task",
            "title": "T1",
            "project": "NIRA",
            "number": 1,
            "db_id": 1,
            "source": "",
            "resolution_reason": "",
            "labels": "",
            "due_date": None,
            "parent_id": None,
            "parent_number": None,
            "story_points": None,
            "body_md": "",
            "resolution_md": "",
            "created_at": "",
            "updated_at": "",
        },
        {
            "id": "NIRA-2",
            "status": "in_progress",
            "priority": "high",
            "type": "bug",
            "title": "T2",
            "project": "NIRA",
            "number": 2,
            "db_id": 2,
            "source": "",
            "resolution_reason": "",
            "labels": "",
            "due_date": None,
            "parent_id": None,
            "parent_number": None,
            "story_points": None,
            "body_md": "",
            "resolution_md": "",
            "created_at": "",
            "updated_at": "",
        },
        {
            "id": "NIRA-3",
            "status": "closed",
            "priority": "critical",
            "type": "feature",
            "title": "T3",
            "project": "NIRA",
            "number": 3,
            "db_id": 3,
            "source": "",
            "resolution_reason": "",
            "labels": "",
            "due_date": None,
            "parent_id": None,
            "parent_number": None,
            "story_points": None,
            "body_md": "",
            "resolution_md": "",
            "created_at": "",
            "updated_at": "",
        },
    ]
    print_ticket_list(tickets, store)

    # Test CLI command with --status all
    run_cli_cov(["list", "--status", "all"], cwd=temp_root)


def test_cli_list_json(temp_root):
    run_cli_cov(["init", "--project-key", "NIRA"], cwd=temp_root)
    run_cli_cov(["create", "T1"], cwd=temp_root)
    code, out, _ = run_cli_cov(["list", "--json"], cwd=temp_root)
    assert code == 0
    data = json.loads(out)
    assert isinstance(data, list)
    assert data[0]["title"] == "T1"


def test_cli_show_json(temp_root):
    run_cli_cov(["init", "--project-key", "NIRA"], cwd=temp_root)
    run_cli_cov(["create", "T1"], cwd=temp_root)
    code, out, _ = run_cli_cov(["show", "NIRA-1", "--json"], cwd=temp_root)
    assert code == 0
    data = json.loads(out)
    assert "ticket" in data
    assert data["ticket"]["title"] == "T1"


def test_cli_export_import(temp_root):
    run_cli_cov(["init", "--project-key", "NIRA"], cwd=temp_root)
    run_cli_cov(["create", "T1", "--body", "Body 1"], cwd=temp_root)
    run_cli_cov(["create", "T2", "--body", "Body 2"], cwd=temp_root)

    csv_path = temp_root / "export.csv"
    code, out, _ = run_cli_cov(["export", str(csv_path)], cwd=temp_root)
    assert code == 0
    assert "Exported 2 tickets" in out
    assert csv_path.exists()

    # Create a new workspace and import
    workspace2 = temp_root.parent / "ws2"
    workspace2.mkdir(exist_ok=True)
    run_cli_cov(["init", "--project-key", "NIRA"], cwd=workspace2)

    # Import into workspace 2
    code, out, _ = run_cli_cov(["import", str(csv_path)], cwd=workspace2)
    assert code == 0
    assert "Imported 2 tickets" in out

    # Verify tickets in workspace 2
    code, out, _ = run_cli_cov(["list"], cwd=workspace2)
    assert "T1" in out
    assert "T2" in out

    # Test export with no tickets
    workspace3 = temp_root.parent / "ws3"
    workspace3.mkdir(exist_ok=True)
    run_cli_cov(["init", "--project-key", "NIRA"], cwd=workspace3)
    code, out, _ = run_cli_cov(["export", "empty.csv"], cwd=workspace3)
    assert "No tickets found" in out

    # Test import file not found
    code, out, _ = run_cli_cov(["import", "missing.csv"], cwd=workspace3)
    assert code == 1
    assert "not found" in out


def test_cli_verbose_logging(temp_root):
    # Just verify it doesn't crash
    code, out, _ = run_cli_cov(["--verbose", "list"], cwd=temp_root)
    # If not init, might be 1, that's fine as long as it doesn't crash on logging setup
    assert code in (0, 1)


def test_cli_describe_reload_change():
    from nira_app.cli import describe_reload_change

    s1 = {"a": (1, 1)}
    s2 = {"a": (1, 1), "b": (2, 2)}
    assert "b added" in describe_reload_change(s1, s2)
    assert "b removed" in describe_reload_change(s2, s1)

    s3 = {"a": (2, 2)}
    assert "a changed" in describe_reload_change(s1, s3)
    assert "files changed" in describe_reload_change(s1, s1)


def test_cli_main_success():
    # Calling main directly with a valid command
    import nira_app.cli

    original_app = nira_app.cli.app

    def mock_app(*args, **kwargs):
        pass

    nira_app.cli.app = mock_app  # type: ignore
    try:
        assert main(["help"]) == 0
    finally:
        nira_app.cli.app = original_app


def test_cli_stop_process_branches(monkeypatch):
    from nira_app.cli import stop_process

    class MockProc:
        def poll(self):
            return 0

    stop_process(MockProc())  # type: ignore

    class MockProcWait:
        def poll(self):
            return None

        def terminate(self):
            pass

        def wait(self, timeout=None):
            if timeout:
                raise subprocess.TimeoutExpired(["cmd"], timeout)

        def kill(self):
            pass

    stop_process(MockProcWait())  # type: ignore


def test_cli_reload_snapshot_error(temp_root, monkeypatch):
    from nira_app.cli import build_reload_snapshot

    (temp_root / "test.py").write_text("...")

    original_stat = Path.stat

    def mock_stat(self, *args, **kwargs):
        if self.name == "test.py" and len(args) == 0:
            raise FileNotFoundError()
        return original_stat(self, *args, **kwargs)

    monkeypatch.setattr("nira_app.cli.should_watch_reload_path", lambda p: True)
    monkeypatch.setattr(Path, "stat", mock_stat)
    build_reload_snapshot(temp_root)


def test_cli_links_logic_branches(temp_root):
    run_cli_cov(["init", "--project-key", "NIRA"], cwd=temp_root)
    run_cli_cov(["create", "T1"], cwd=temp_root)
    run_cli_cov(["create", "T2"], cwd=temp_root)
    run_cli_cov(["link", "NIRA-1", "NIRA-2"], cwd=temp_root)
    run_cli_cov(["links", "NIRA-1"], cwd=temp_root)
    run_cli_cov(["links", "NIRA-2"], cwd=temp_root)
    run_cli_cov(["links"], cwd=temp_root)
    run_cli_cov(["unlink", "NIRA-1", "NIRA-2"], cwd=temp_root)
    run_cli_cov(["links"], cwd=temp_root)


def test_cli_read_markdown_interactive(monkeypatch):
    from nira_app.cli import read_markdown_input

    called = []

    def mock_editor(text):
        called.append(text)
        return "edited"

    monkeypatch.setattr("nira_app.cli.launch_editor", mock_editor)
    assert read_markdown_input(body=None, edit=True) == "edited"
    assert called

    # Body is not None
    assert read_markdown_input(body="body", edit=False) == "body"

    # isatty return empty
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    assert read_markdown_input(body=None, edit=False) == ""


def test_cli_serve_reload_loop(temp_root, monkeypatch):
    import nira_app.cli

    snapshots = [
        {"a": (1, 1)},  # initial
        {"a": (1, 1)},  # 1. no change
        {"a": (2, 2)},  # 2. change
        {"a": (3, 3)},  # 3. change
    ]

    def mock_snapshot(*args):
        if snapshots:
            return snapshots.pop(0)
        raise KeyboardInterrupt()  # Break loop

    monkeypatch.setattr(nira_app.cli, "build_reload_snapshot", mock_snapshot)
    monkeypatch.setattr(time, "sleep", lambda x: None)

    class MockChild:
        def __init__(self, exited=False):
            self._exited = exited

        def poll(self):
            return 0 if self._exited else None

        def terminate(self):
            pass

        def wait(self, timeout=None):
            pass

        def kill(self):
            pass

    children = [MockChild(exited=True), MockChild(exited=False), MockChild(exited=False)]

    def mock_popen(*args, **kwargs):
        return children.pop(0) if children else MockChild()

    monkeypatch.setattr(subprocess, "Popen", mock_popen)

    from nira_app.cli import serve_with_reload

    serve_with_reload(temp_root, "127.0.0.1", 8765)


def test_cli_edit_error_real(temp_root, monkeypatch):
    run_cli_cov(["init", "--project-key", "NIRA"], cwd=temp_root)
    run_cli_cov(["create", "T1"], cwd=temp_root)

    from nira_app.storage import NiraError

    def mock_update(*args, **kwargs):
        raise NiraError("Edit fail")

    monkeypatch.setattr(TicketService, "update_ticket", mock_update)

    monkeypatch.setattr("nira_app.cli.launch_editor", lambda text: "Edited body")

    code, out, _ = run_cli_cov(["edit", "NIRA-1"], cwd=temp_root)
    assert code == 1
    assert "Edit fail" in out


def test_cli_build_serve_command():
    from nira_app.cli import build_serve_command

    cmd = build_serve_command(Path("root"), "host", 1234)
    assert "--root" in cmd
    assert "root" in cmd
    assert "1234" in cmd
