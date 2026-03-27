import pytest
import io
from nira_app.services import TicketService
from nira_app.storage import NiraStore, ValidationError
from nira_app.web import NiraWebApp, highlight


def test_highlight_edge_cases():
    # Only operators, no actual words
    assert highlight("test", "*") == "test"
    assert highlight("test", '"') == "test"
    assert highlight("test", "") == "test"


def test_parse_search_invalid_status(temp_root):
    store = NiraStore(temp_root)
    service = TicketService(store)
    store.initialize("NIRA")

    # is:invalid-status should be ignored gracefully
    tickets = service.list_tickets(search="is:bogus")
    assert len(tickets) == 0

    count = service.count_tickets(search="is:bogus")
    assert count == 0


def test_ticket_details_with_parent(temp_root):
    store = NiraStore(temp_root)
    service = TicketService(store)
    store.initialize("NIRA")

    parent = service.create_ticket("NIRA", "Parent")
    child = service.create_ticket("NIRA", "Child", parent_id=parent["db_id"])

    details = service.ticket_details(child["id"])
    assert details["parent"] is not None
    assert details["parent"]["id"] == parent["id"]


def test_web_htmx_errors(temp_root):
    store = NiraStore(temp_root)
    store.initialize("NIRA")
    app = NiraWebApp(store)

    # Test ValidationError with HTMX
    environ = {
        "REQUEST_METHOD": "POST",
        "PATH_INFO": "/tickets",
        "QUERY_STRING": "",
        "HTTP_HX_REQUEST": "true",
        "wsgi.input": io.BytesIO(b"title="),  # Empty title -> ValidationError
        "CONTENT_LENGTH": "6",
        "CONTENT_TYPE": "application/x-www-form-urlencoded",
    }

    def start_response(status, headers):
        assert status == "200 OK"
        headers_dict = dict(headers)
        assert "HX-Trigger" in headers_dict
        assert "nira:toast" in headers_dict["HX-Trigger"]
        assert "Title is required" in headers_dict["HX-Trigger"]

    app(environ, start_response)


def test_web_htmx_nira_error(temp_root, monkeypatch):
    store = NiraStore(temp_root)
    store.initialize("NIRA")
    app = NiraWebApp(store)

    from nira_app.storage import NiraError

    def mock_list_tickets(*args, **kwargs):
        raise NiraError("Simulated nira error")

    # We must patch the service instance that the app is actually using
    monkeypatch.setattr(app.service, "list_tickets", mock_list_tickets)

    environ = {
        "REQUEST_METHOD": "GET",
        "PATH_INFO": "/list",
        "QUERY_STRING": "",
        "HTTP_HX_REQUEST": "true",
        "wsgi.input": io.BytesIO(b""),
    }

    def start_response(status, headers):
        assert status == "200 OK"
        headers_dict = dict(headers)
        assert "HX-Trigger" in headers_dict
        assert "Simulated nira error" in headers_dict["HX-Trigger"]

    app(environ, start_response)


def test_derive_default_project_key_edge_cases():
    from nira_app.storage import derive_default_project_key

    # No words
    assert derive_default_project_key("...") == "NIRA"
    # Single short word
    assert derive_default_project_key("ab") == "ABAA"
    # Multi word
    assert derive_default_project_key("my-project") == "MP"


def test_normalize_ticket_id_digit(temp_root):
    from nira_app.storage import normalize_ticket_id

    assert normalize_ticket_id("123") == "TEMP-123"


def test_rename_default_project(temp_root):
    store = NiraStore(temp_root)
    store.initialize("OLD")

    res = store.rename_default_project("NEW")
    assert res["old_project"] == "OLD"
    assert res["new_project"] == "NEW"
    assert store.get_default_project() == "NEW"

    # Rename to same key
    res = store.rename_default_project("NEW")
    assert res["renamed_ticket_count"] == 0


def test_cli_config(temp_root, monkeypatch):
    from nira_app.cli import app
    from typer.testing import CliRunner

    runner = CliRunner()

    monkeypatch.chdir(temp_root)
    runner.invoke(app, ["init", "--project-key", "NIRA"])

    # Show all
    result = runner.invoke(app, ["config"])
    assert result.exit_code == 0
    assert "default_project" in result.stdout

    # Get specific
    result = runner.invoke(app, ["config", "default_project"])
    assert result.exit_code == 0
    assert "NIRA" in result.stdout

    # Set specific
    result = runner.invoke(app, ["config", "theme", "dark"])
    assert result.exit_code == 0
    assert "Updated theme to dark" in result.stdout

    # Get invalid
    result = runner.invoke(app, ["config", "nonexistent"])
    assert result.exit_code == 1


def test_cli_errors_nira_error(temp_root, monkeypatch):
    from nira_app.cli import app
    from typer.testing import CliRunner
    from nira_app.storage import NiraError

    runner = CliRunner()
    monkeypatch.chdir(temp_root)
    runner.invoke(app, ["init"])

    # Mocking resolve_store to raise NiraError
    def mock_resolve_store(*args, **kwargs):
        raise NiraError("BOOM!")

    monkeypatch.setattr("nira_app.cli.resolve_store", mock_resolve_store)

    # Check commands that have NiraError handler
    for cmd in [["dashboard"], ["board"], ["export", "out.csv"], ["import", "out.csv"], ["config", "key", "val"]]:
        result = runner.invoke(app, cmd)
        assert result.exit_code == 1
        assert "BOOM!" in result.stdout


def test_cli_errors_os_error_export_import(temp_root, monkeypatch):
    from nira_app.cli import app
    from typer.testing import CliRunner

    runner = CliRunner()
    monkeypatch.chdir(temp_root)
    runner.invoke(app, ["init"])
    runner.invoke(app, ["new", "test"])

    # Mock open to raise OSError
    import builtins

    original_open = builtins.open

    def mock_open(*args, **kwargs):
        if "out.csv" in str(args[0]):
            raise OSError("Access denied")
        return original_open(*args, **kwargs)

    monkeypatch.setattr(builtins, "open", mock_open)

    # Create the file so it exists for import
    (temp_root / "out.csv").write_text("dummy")

    result = runner.invoke(app, ["export", "out.csv"])
    assert result.exit_code == 1
    assert "Access denied" in result.stdout

    # Import OSError
    result = runner.invoke(app, ["import", "out.csv"])
    assert result.exit_code == 1
    assert "Access denied" in result.stdout


def test_cli_import_value_error(temp_root, monkeypatch):
    from nira_app.cli import app
    from typer.testing import CliRunner

    runner = CliRunner()
    monkeypatch.chdir(temp_root)
    runner.invoke(app, ["init"])

    (temp_root / "bad.csv").write_text("header\nval")

    def mock_import_tickets(*args, **kwargs):
        raise ValueError("Bad data")

    monkeypatch.setattr("nira_app.services.TicketService.import_tickets", mock_import_tickets)

    result = runner.invoke(app, ["import", "bad.csv"])
    assert result.exit_code == 1
    assert "Bad data" in result.stdout


def test_cli_show_with_resolution(temp_root, monkeypatch):
    from nira_app.cli import app
    from typer.testing import CliRunner

    runner = CliRunner()
    monkeypatch.chdir(temp_root)
    runner.invoke(app, ["init"])
    runner.invoke(app, ["new", "To be closed"])
    # CLI doesn't have --reason option, and it uses --notes or --resolution
    runner.invoke(app, ["close", "NIRA-1", "--notes", "Done!"])

    result = runner.invoke(app, ["show", "NIRA-1"])
    assert result.exit_code == 0
    assert "Resolution:" in result.stdout
    assert "completed" in result.stdout
    assert "Resolution Notes" in result.stdout
    assert "Done!" in result.stdout


def test_cli_export_empty(temp_root, monkeypatch):
    from nira_app.cli import app
    from typer.testing import CliRunner

    runner = CliRunner()

    monkeypatch.chdir(temp_root)
    runner.invoke(app, ["init"])

    result = runner.invoke(app, ["export", "out.csv"])
    assert result.exit_code == 0
    assert "No tickets found" in result.stdout


def test_cli_export_excludes_internal_fields(temp_root, monkeypatch):
    from nira_app.cli import app
    from typer.testing import CliRunner

    runner = CliRunner()

    monkeypatch.chdir(temp_root)
    runner.invoke(app, ["init"])
    runner.invoke(app, ["new", "Test export"])

    out_file = temp_root / "clean_export.csv"
    result = runner.invoke(app, ["export", str(out_file)])
    assert result.exit_code == 0

    content = out_file.read_text()
    header = content.splitlines()[0]
    assert "db_id" not in header
    assert "parent_id" not in header
    assert "parent_number" not in header
    assert "id" in header
    assert "title" in header


def test_cli_dashboard_and_board_extras(temp_root, monkeypatch):
    from nira_app.cli import app
    from typer.testing import CliRunner

    runner = CliRunner()

    monkeypatch.chdir(temp_root)
    runner.invoke(app, ["init"])

    # Create some history
    runner.invoke(app, ["new", "Ticket with history"])
    runner.invoke(app, ["update", "NIRA-1", "--priority", "high"])

    # Create 11 more tickets to trigger "and X more" in board (total 12)
    for i in range(11):
        runner.invoke(app, ["new", f"Ticket {i}"])

    result = runner.invoke(app, ["board"])
    assert result.exit_code == 0
    assert "and 2 more" in result.stdout  # 12 tickets total, top 10 shown -> 2 more

    # Dashboard
    result = runner.invoke(app, ["dashboard"])
    assert result.exit_code == 0
    assert "Dashboard" in result.stdout
    assert "Tickets by Status" in result.stdout
    assert "Recent Tickets" in result.stdout
    assert "Recent Activity" in result.stdout
    assert "priority -> high" in result.stdout


def test_cli_serve_errors(temp_root, monkeypatch):
    from nira_app.cli import app
    from typer.testing import CliRunner
    from nira_app.storage import NiraError

    runner = CliRunner()
    monkeypatch.chdir(temp_root)
    runner.invoke(app, ["init"])

    # 1. NiraError
    def mock_run_server(*args, **kwargs):
        raise NiraError("Serve failed")

    monkeypatch.setattr("nira_app.cli.run_server", mock_run_server)

    result = runner.invoke(app, ["serve"])
    assert result.exit_code == 1
    # Check both stdout and stderr since rich console might use stderr
    assert "Serve failed" in result.stdout + result.stderr

    # 2. OSError (Address already in use, errno 48)
    def mock_run_server_os_error(*args, **kwargs):
        err = OSError("Address already in use")
        err.errno = 48
        raise err

    monkeypatch.setattr("nira_app.cli.run_server", mock_run_server_os_error)

    result = runner.invoke(app, ["serve"])
    assert result.exit_code == 1
    # Check both stdout and stderr since rich console might use stderr
    combined = (result.stdout + result.stderr).replace("\n", " ")
    assert "Address already in" in combined
    assert "use" in combined


def test_cli_print_ticket_list_with_title(temp_root, monkeypatch):
    from nira_app.cli import app
    from typer.testing import CliRunner

    runner = CliRunner()

    monkeypatch.chdir(temp_root)
    runner.invoke(app, ["init"])
    runner.invoke(app, ["new", "Test ticket"])

    # dashboard command uses print_ticket_list with a title
    result = runner.invoke(app, ["dashboard"])
    assert result.exit_code == 0
    assert "Recent Tickets" in result.stdout


def test_cli_print_ticket_list_empty_with_title(temp_root, monkeypatch):
    from nira_app.cli import app, print_ticket_list
    from nira_app.storage import NiraStore
    from typer.testing import CliRunner

    runner = CliRunner()

    monkeypatch.chdir(temp_root)
    runner.invoke(app, ["init"])
    store = NiraStore(temp_root)

    # Call directly to cover line 800
    print_ticket_list([], store, title="Direct Call")
    # No assertion needed, just need it to run


def test_cli_delete_ticket(temp_root, monkeypatch):
    from nira_app.cli import app
    from typer.testing import CliRunner

    runner = CliRunner()

    monkeypatch.chdir(temp_root)
    runner.invoke(app, ["init"])
    runner.invoke(app, ["new", "To be deleted"])

    # 1. Test cancellation
    result = runner.invoke(app, ["delete", "NIRA-1"], input="n\n")
    assert "Aborted" in result.stdout

    # 2. Test force delete
    result = runner.invoke(app, ["delete", "NIRA-1", "--force"])
    assert result.exit_code == 0
    assert "Deleted NIRA-1" in result.stdout

    # 3. Verify it's gone
    result = runner.invoke(app, ["show", "NIRA-1"])
    assert result.exit_code == 1


def test_cli_delete_nira_error(temp_root, monkeypatch):
    from nira_app.cli import app
    from typer.testing import CliRunner
    from nira_app.storage import NiraError

    runner = CliRunner()

    monkeypatch.chdir(temp_root)
    runner.invoke(app, ["init"])

    def mock_delete_ticket(*args, **kwargs):
        raise NiraError("Delete failed")

    monkeypatch.setattr("nira_app.services.TicketService.delete_ticket", mock_delete_ticket)

    result = runner.invoke(app, ["delete", "NIRA-1"], input="y\n")
    assert result.exit_code == 1
    assert "Delete failed" in result.stdout


def test_cli_print_ticket_low_priority(temp_root, monkeypatch):

    from nira_app.cli import app
    from typer.testing import CliRunner

    runner = CliRunner()

    monkeypatch.chdir(temp_root)
    runner.invoke(app, ["init"])
    runner.invoke(app, ["new", "Low prio", "--priority", "low"])

    result = runner.invoke(app, ["show", "NIRA-1"])
    assert result.exit_code == 0
    assert "low" in result.stdout


def test_cli_import_empty_or_missing(temp_root, monkeypatch):
    from nira_app.cli import app
    from typer.testing import CliRunner

    runner = CliRunner()

    monkeypatch.chdir(temp_root)
    runner.invoke(app, ["init"])

    # Missing file
    result = runner.invoke(app, ["import", "missing.csv"])
    assert result.exit_code == 1

    # Empty file
    empty = temp_root / "empty.csv"
    empty.write_text("")
    result = runner.invoke(app, ["import", str(empty)])
    assert result.exit_code == 0
    assert "No tickets found" in result.stdout


def test_cli_import_malformed(temp_root, monkeypatch):
    from nira_app.cli import app
    from typer.testing import CliRunner

    runner = CliRunner()

    monkeypatch.chdir(temp_root)
    runner.invoke(app, ["init"])

    # Malformed CSV
    malformed = temp_root / "bad.csv"
    malformed.write_text("not,a,csv\n1,2")
    result = runner.invoke(app, ["import", str(malformed)])
    assert result.exit_code == 1


def test_normalize_ticket_id_invalid():
    from nira_app.storage import normalize_ticket_id

    with pytest.raises(ValidationError, match="Ticket IDs must look like"):
        normalize_ticket_id("!!!")


def test_storage_needs_legacy_migration_links(temp_root):
    db_path = temp_root / ".nira" / "nira.db"
    db_path.parent.mkdir(parents=True)
    import sqlite3

    with sqlite3.connect(db_path) as conn:
        # INTEGER id to pass previous check, but links have old ticket_a/ticket_b
        conn.execute(
            "CREATE TABLE tickets (id INTEGER PRIMARY KEY, project TEXT, number INTEGER, title TEXT, status TEXT, type TEXT, priority TEXT, source TEXT, resolution_reason TEXT, body_md TEXT, resolution_md TEXT, created_at TEXT, updated_at TEXT)"
        )
        conn.execute("CREATE TABLE links (ticket_a INTEGER, ticket_b INTEGER, created_at TEXT)")

    store = NiraStore(temp_root)
    with store.connect() as conn:
        assert store.needs_legacy_migration(conn) is True


def test_storage_legacy_migration_with_history(temp_root):
    db_path = temp_root / ".nira" / "nira.db"
    db_path.parent.mkdir(parents=True)
    import sqlite3

    with sqlite3.connect(db_path) as conn:
        # Create a database that looks like it needs migration and has history
        conn.execute(
            "CREATE TABLE tickets (id INTEGER PRIMARY KEY, number INTEGER, title TEXT, status TEXT, type TEXT, priority TEXT, source TEXT, resolution_reason TEXT, body_md TEXT, resolution_md TEXT, created_at TEXT, updated_at TEXT)"
        )
        conn.execute(
            "INSERT INTO tickets (id, number, title, status, type, priority, source, resolution_reason, body_md, resolution_md, created_at, updated_at) VALUES (1, 1, 'Test', 'open', 'task', 'medium', 'user', '', '', '', '2026-01-01', '2026-01-01')"
        )
        conn.execute(
            "CREATE TABLE history (id INTEGER PRIMARY KEY, ticket_id INTEGER, field TEXT, old_value TEXT, new_value TEXT, created_at TEXT)"
        )
        conn.execute(
            "INSERT INTO history (ticket_id, field, old_value, new_value, created_at) VALUES (1, 'status', 'open', 'in_progress', '2026-01-01')"
        )

    store = NiraStore(temp_root)
    # This should trigger legacy migration including history
    store.initialize("NIRA")

    with store.session() as session:
        from nira_app.models import History

        h = session.query(History).first()
        assert h is not None
        assert h.field == "status"


def test_storage_legacy_migration_with_history_missing_ticket(temp_root):
    db_path = temp_root / ".nira" / "nira.db"
    db_path.parent.mkdir(parents=True)
    import sqlite3

    with sqlite3.connect(db_path) as conn:
        # Create a database with history for a ticket that doesn't exist
        conn.execute(
            "CREATE TABLE tickets (id INTEGER PRIMARY KEY, number INTEGER, title TEXT, status TEXT, type TEXT, priority TEXT, source TEXT, resolution_reason TEXT, body_md TEXT, resolution_md TEXT, created_at TEXT, updated_at TEXT)"
        )
        conn.execute(
            "CREATE TABLE history (id INTEGER PRIMARY KEY, ticket_id INTEGER, field TEXT, old_value TEXT, new_value TEXT, created_at TEXT)"
        )
        conn.execute(
            "INSERT INTO history (ticket_id, field, old_value, new_value, created_at) VALUES (999, 'status', 'open', 'in_progress', '2026-01-01')"
        )

    store = NiraStore(temp_root)
    # This should trigger legacy migration and skip the history record for ticket 999
    store.initialize("NIRA")

    with store.session() as session:
        from nira_app.models import History

        assert session.query(History).count() == 0


def test_storage_needs_legacy_migration_id_type(temp_root):
    db_path = temp_root / ".nira" / "nira.db"
    db_path.parent.mkdir(parents=True)
    import sqlite3

    with sqlite3.connect(db_path) as conn:
        # id is TEXT instead of INTEGER
        conn.execute(
            "CREATE TABLE tickets (id TEXT PRIMARY KEY, project TEXT, number INTEGER, title TEXT, status TEXT, type TEXT, priority TEXT, source TEXT, resolution_reason TEXT, body_md TEXT, resolution_md TEXT, created_at TEXT, updated_at TEXT)"
        )

    store = NiraStore(temp_root)
    with store.connect() as conn:
        assert store.needs_legacy_migration(conn) is True


def test_storage_initialize_guessing_logic_and_corruption_fix(temp_root):
    # This test aims to cover lines 219-248 of storage.py
    db_path = temp_root / ".nira" / "nira.db"
    db_path.parent.mkdir(parents=True)
    import sqlite3

    with sqlite3.connect(db_path) as conn:
        # Create a database that has all modern columns EXCEPT parent_id,
        # AND has an alembic_version table with an OLD revision.
        # This triggers the "Healing" logic.
        conn.execute("""
            CREATE TABLE tickets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project TEXT NOT NULL,
                number INTEGER NOT NULL UNIQUE,
                title TEXT NOT NULL,
                status TEXT NOT NULL,
                type TEXT NOT NULL,
                priority TEXT NOT NULL,
                source TEXT NOT NULL,
                resolution_reason TEXT NOT NULL DEFAULT '',
                labels TEXT NOT NULL DEFAULT '',
                due_date TEXT,
                story_points INTEGER,
                body_md TEXT NOT NULL DEFAULT '',
                resolution_md TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ticket_id INTEGER NOT NULL REFERENCES tickets(id) ON DELETE CASCADE,
                field TEXT NOT NULL,
                old_value TEXT,
                new_value TEXT,
                created_at TEXT NOT NULL
            )
        """)
        # We also need an old alembic revision to trigger the path
        conn.execute("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL PRIMARY KEY)")
        conn.execute("INSERT INTO alembic_version (version_num) VALUES ('b00dc4b8ba72')")
        # And we need settings table so it doesn't assume fresh install and stamp head
        conn.execute("CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)")

    store = NiraStore(temp_root)
    # This should:
    # 1. Detect alembic_version 'b00dc4b8ba72'
    # 2. Guess version based on story_points (c0d1284dadf6)
    # 3. Detect missing parent_id and MANUALLY add it (corruption fix)
    # 4. Stamp the revision
    store.initialize("NIRA")

    with store.connect() as conn:
        cols = store.column_names(conn, "tickets")
        assert "parent_id" in cols

        # Check if alembic_version has been updated to head (or at least past b00dc4b8ba72)
        rev = conn.execute("SELECT version_num FROM alembic_version").fetchone()[0]
        assert rev != "b00dc4b8ba72"


def test_storage_initialize_guessing_logic_with_parent_id(temp_root):
    # This covers line 225-226 (parent_id in cols)
    db_path = temp_root / ".nira" / "nira.db"
    db_path.parent.mkdir(parents=True)
    import sqlite3

    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "CREATE TABLE tickets (id INTEGER PRIMARY KEY, project TEXT, number INTEGER, title TEXT, status TEXT, type TEXT, priority TEXT, source TEXT, resolution_reason TEXT, labels TEXT, due_date TEXT, parent_id INTEGER, story_points INTEGER, body_md TEXT, resolution_md TEXT, created_at TEXT, updated_at TEXT)"
        )
        conn.execute("CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        conn.execute("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL PRIMARY KEY)")
        conn.execute("INSERT INTO alembic_version (version_num) VALUES ('b00dc4b8ba72')")

    store = NiraStore(temp_root)
    store.initialize("NIRA")
    with store.connect() as conn:
        rev = conn.execute("SELECT version_num FROM alembic_version").fetchone()[0]
        assert rev != "b00dc4b8ba72"


def test_alembic_env_coverage(temp_root, monkeypatch):
    # This aims to cover alembic/env.py
    from alembic.config import Config
    from alembic import command
    import os
    import shutil

    original_ini = os.path.abspath("alembic.ini")
    original_migrations = os.path.abspath("nira_app/migrations")

    monkeypatch.chdir(temp_root)
    db_path = temp_root / "test.db"
    # Create alembic.ini that points to our test db
    ini_path = temp_root / "alembic.ini"
    shutil.copy(original_ini, ini_path)

    # Update the ini to use our test.db and point to migrations
    content = ini_path.read_text()
    content = content.replace("sqlite:///.nira/nira.db", f"sqlite:///{db_path}")
    # Also need to point script_location to absolute path of migrations
    content = content.replace(
        "script_location = nira_app/migrations/alembic", f"script_location = {original_migrations}/alembic"
    )
    ini_path.write_text(content)

    cfg = Config(str(ini_path))

    # 1. Test offline migration (line 31-33, 105)
    # Use a revision that doesn't use batch mode (b00dc4b8ba72 is initial schema)
    command.upgrade(cfg, "b00dc4b8ba72", sql=True)

    # 2. Test online migration without connection in attributes (line 48-59)
    command.upgrade(cfg, "head")


def test_list_tickets_invalid_status(temp_root):
    store = NiraStore(temp_root)
    service = TicketService(store)
    store.initialize("NIRA")

    # Passing an invalid status directly should be caught by the try/except in list_tickets
    tickets = service.list_tickets(status="bogus")
    assert len(tickets) == 0

    count = service.count_tickets(status="bogus")
    assert count == 0


def test_parse_search_query_edge_cases(temp_root):
    store = NiraStore(temp_root)
    service = TicketService(store)

    # What if key is unknown?
    clean, filters = service._parse_search_query("foo:bar")
    assert clean == "foo:bar"
    assert filters == {}

    # What if token is just ":"?
    clean, filters = service._parse_search_query(":")
    assert clean == ":"
    assert filters == {}


def test_cli_new_interactive(temp_root, monkeypatch):
    from nira_app.cli import app
    from typer.testing import CliRunner

    runner = CliRunner()
    monkeypatch.chdir(temp_root)
    runner.invoke(app, ["init", "--project-key", "NIRA"])

    # Mock Prompt.ask and Confirm.ask
    # 1. Title
    # 2. Priority
    # 3. Type
    # 4. Labels
    # 5. Due Date
    # 6. Parent
    # 7. Source
    # 8. Add description? (Confirm)
    # 9. Open Editor? (Confirm)
    inputs = [
        "Interactive Title",
        "high",
        "bug",
        "tag1, tag2",
        "2026-12-31",
        "",  # no parent
        "email",
        "y",  # Add description
        "n",  # Open editor? No
    ]
    # stdin for manual description
    description = "Manual description via stdin\n"

    # We need to mock sys.stdin.isatty to return True
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)

    from rich.prompt import Prompt, Confirm

    input_iter = iter(inputs)

    def mock_ask(*args, **kwargs):
        val = next(input_iter)
        # print(f"MOCK ASK: {args} -> {val}")
        return val

    monkeypatch.setattr(Prompt, "ask", mock_ask)
    monkeypatch.setattr(Confirm, "ask", lambda *args, **kwargs: True if next(input_iter) == "y" else False)

    # Use explicit -i flag to avoid tty issues in tests
    # Since we are NOT mocking sys.stdin.read anymore (it will read from input argument)
    result = runner.invoke(app, ["new", "-i"], input=description)
    assert result.exit_code == 0
    assert "Created NIRA-1" in result.stdout

    # Verify ticket was created with correct data
    from nira_app.services import TicketService
    from nira_app.storage import NiraStore

    store = NiraStore(temp_root)
    service = TicketService(store)
    details = service.ticket_details("NIRA-1")
    t = details["ticket"]
    assert t["title"] == "Interactive Title"
    assert t["priority"] == "high"
    assert t["type"] == "bug"
    assert t["labels"] == "tag1, tag2"
    assert t["due_date"] == "2026-12-31"
    assert t["source"] == "email"
    assert t["body_md"] == description


def test_markdown_checklists(temp_root):
    from nira_app.markdown import render_markdown

    # Static rendering
    html = render_markdown("- [ ] task 1\n- [x] task 2")
    assert '<input type="checkbox"' in html
    assert "checked" in html
    assert "disabled" in html

    # Interactive rendering
    html_int = render_markdown("- [ ] task 1", ticket_id="NIRA-1")
    assert 'hx-post="/tickets/NIRA-1/task/0/check"' in html_int
    assert 'hx-target="closest div.activity-body, closest div.card-body"' in html_int


def test_service_attachments(temp_root):
    store = NiraStore(temp_root)
    store.initialize("NIRA")
    service = TicketService(store)

    ticket = service.create_ticket("NIRA", "Attach test")
    tid = ticket["id"]

    # 1. Add attachment
    content = b"fake file content"
    service.add_attachment(tid, "test.txt", content, "text/plain")

    # 2. Verify details
    details = service.ticket_details(tid)
    assert len(details["attachments"]) == 1
    assert details["attachments"][0]["filename"] == "test.txt"
    assert details["attachments"][0]["file_size"] == len(content)

    # 3. Add duplicate filename (should append timestamp)
    service.add_attachment(tid, "test.txt", b"v2 content")
    details2 = service.ticket_details(tid)
    assert len(details2["attachments"]) == 2
    assert details2["attachments"][1]["filename"].startswith("test_")

    # 4. Get path
    path = service.get_attachment_path(tid, "test.txt")
    assert path.exists()
    assert path.read_bytes() == content

    # 5. Delete ticket cleans up attachments
    attach_dir = store.state_dir / "attachments" / str(ticket["db_id"])
    assert attach_dir.exists()
    service.delete_ticket(tid)
    assert not attach_dir.exists()


def test_web_attachments(temp_root):
    store = NiraStore(temp_root)
    store.initialize("NIRA")
    service = TicketService(store)
    app = NiraWebApp(store)

    ticket = service.create_ticket("NIRA", "Web attach")
    tid = ticket["id"]

    # Test Multipart upload
    boundary = "boundary123"
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="web.txt"\r\n'
        f"Content-Type: text/plain\r\n\r\n"
        f"web content\r\n"
        f"--{boundary}--\r\n"
    ).encode("utf-8")

    environ = {
        "REQUEST_METHOD": "POST",
        "PATH_INFO": f"/tickets/{tid}/attachments",
        "CONTENT_TYPE": f"multipart/form-data; boundary={boundary}",
        "CONTENT_LENGTH": str(len(body)),
        "wsgi.input": io.BytesIO(body),
    }

    def start_response(status, headers):
        assert status == "200 OK"
        assert any(h[0] == "HX-Refresh" for h in headers)

    app(environ, start_response)

    # Verify upload
    details = service.ticket_details(tid)
    assert len(details["attachments"]) == 1
    assert details["attachments"][0]["filename"] == "web.txt"

    # Test Serve attachment
    environ_get = {
        "REQUEST_METHOD": "GET",
        "PATH_INFO": f"/tickets/{tid}/attachments/web.txt",
        "wsgi.input": io.BytesIO(b""),
    }

    def start_response_get(status, headers):
        assert status == "200 OK"
        assert any(h[0] == "Content-Type" and h[1] == "text/plain" for h in headers)

    resp_body = app(environ_get, start_response_get)
    assert b"web content" in resp_body[0]


def test_web_toggle_task(temp_root):
    store = NiraStore(temp_root)
    store.initialize("NIRA")
    service = TicketService(store)
    app = NiraWebApp(store)

    ticket = service.create_ticket("NIRA", "Task test", body_md="- [ ] incomplete\n- [x] complete")
    tid = ticket["id"]

    # 1. Check the incomplete one
    environ = {
        "REQUEST_METHOD": "POST",
        "PATH_INFO": f"/tickets/{tid}/task/0/check",
        "wsgi.input": io.BytesIO(b""),
    }

    def start_response(status, headers):
        assert status == "200 OK"

    app(environ, start_response)

    # Verify body updated
    details = service.ticket_details(tid)
    assert "- [x] incomplete" in details["ticket"]["body_md"]

    # 2. Uncheck the complete one
    environ2 = {
        "REQUEST_METHOD": "POST",
        "PATH_INFO": f"/tickets/{tid}/task/1/uncheck",
        "wsgi.input": io.BytesIO(b""),
    }
    app(environ2, start_response)

    details2 = service.ticket_details(tid)
    assert "- [ ] complete" in details2["ticket"]["body_md"]


def test_cli_attach_command(temp_root, monkeypatch):
    from nira_app.cli import app
    from typer.testing import CliRunner

    runner = CliRunner()
    monkeypatch.chdir(temp_root)
    runner.invoke(app, ["init"])
    runner.invoke(app, ["new", "test"])

    dummy = temp_root / "dummy.png"
    dummy.write_bytes(b"png data")

    result = runner.invoke(app, ["attach", "NIRA-1", str(dummy)])
    assert result.exit_code == 0
    assert "Attached dummy.png" in result.stdout

    # Test error
    result = runner.invoke(app, ["attach", "NIRA-1", "nonexistent.file"])
    assert result.exit_code == 1


def test_service_attachments_errors(temp_root):
    store = NiraStore(temp_root)
    store.initialize("NIRA")
    service = TicketService(store)

    ticket = service.create_ticket("NIRA", "Error test")
    tid = ticket["id"]

    # 1. Missing filename
    with pytest.raises(ValidationError, match="Filename is required"):
        service.add_attachment(tid, "", b"data")

    # 2. Non-existent attachment path
    with pytest.raises(ValidationError, match="not found"):
        service.get_attachment_path(tid, "missing.txt")


def test_web_attachments_errors(temp_root):
    store = NiraStore(temp_root)
    store.initialize("NIRA")
    app = NiraWebApp(store)

    # 1. Upload missing file
    environ = {
        "REQUEST_METHOD": "POST",
        "PATH_INFO": "/tickets/NIRA-1/attachments",
        "CONTENT_TYPE": "multipart/form-data; boundary=b",
        "CONTENT_LENGTH": "0",
        "wsgi.input": io.BytesIO(b""),
    }

    def start_response(status, headers):
        assert status == "400 Bad Request"

    app(environ, start_response)

    # 2. Serve missing ticket attachment
    environ_get = {
        "REQUEST_METHOD": "GET",
        "PATH_INFO": "/tickets/NIRA-999/attachments/test.txt",
        "wsgi.input": io.BytesIO(b""),
    }

    def start_response_404(status, headers):
        assert status == "404 Not Found"

    app(environ_get, start_response_404)


def test_web_toggle_task_errors(temp_root):
    store = NiraStore(temp_root)
    store.initialize("NIRA")
    app = NiraWebApp(store)

    service = TicketService(store)
    service.create_ticket("NIRA", "Task")

    # Invalid index
    environ = {
        "REQUEST_METHOD": "POST",
        "PATH_INFO": "/tickets/NIRA-1/task/99/check",
        "wsgi.input": io.BytesIO(b""),
    }

    def start_response(status, headers):
        assert status == "400 Bad Request"

    app(environ, start_response)


def test_storage_get_all_labels(temp_root):
    store = NiraStore(temp_root)
    store.initialize("NIRA")
    service = TicketService(store)

    service.create_ticket("NIRA", "T1", labels="a,b")
    service.create_ticket("NIRA", "T2", labels="b,c")

    labels = store.get_all_labels()
    assert labels == ["a", "b", "c"]


def test_cli_new_interactive_no_title(temp_root, monkeypatch):
    from nira_app.cli import app
    from typer.testing import CliRunner
    from rich.prompt import Prompt

    runner = CliRunner()
    monkeypatch.chdir(temp_root)
    runner.invoke(app, ["init", "--project-key", "NIRA"])

    # Mock empty title -> should Exit(1)
    monkeypatch.setattr(Prompt, "ask", lambda *args, **kwargs: "")
    result = runner.invoke(app, ["new", "-i"])
    assert result.exit_code == 1
    assert "Title is required" in result.stdout


def test_cli_new_interactive_with_editor(temp_root, monkeypatch):
    from nira_app.cli import app
    from typer.testing import CliRunner
    from rich.prompt import Prompt, Confirm

    runner = CliRunner()
    monkeypatch.chdir(temp_root)
    runner.invoke(app, ["init", "--project-key", "NIRA"])

    inputs = ["Title", "medium", "task", "", "", "", "", "y", "y"]
    input_iter = iter(inputs)
    monkeypatch.setattr(Prompt, "ask", lambda *args, **kwargs: next(input_iter))
    monkeypatch.setattr(Confirm, "ask", lambda *args, **kwargs: True if next(input_iter) == "y" else False)
    monkeypatch.setattr("nira_app.cli.launch_editor", lambda x: "Editor content")

    result = runner.invoke(app, ["new", "-i"])
    assert result.exit_code == 0
    from nira_app.services import TicketService
    from nira_app.storage import NiraStore

    service = TicketService(NiraStore(temp_root))
    assert service.ticket_details("NIRA-1")["ticket"]["body_md"] == "Editor content"


def test_web_multipart_text_field(temp_root):
    # Test decoding of regular text fields in multipart form
    app = NiraWebApp(NiraStore(temp_root))
    boundary = "b"
    body = (
        f'--{boundary}\r\nContent-Disposition: form-data; name="title"\r\n\r\nMultipart Title\r\n--{boundary}--\r\n'
    ).encode("utf-8")

    environ = {
        "REQUEST_METHOD": "POST",
        "PATH_INFO": "/tickets",
        "CONTENT_TYPE": f"multipart/form-data; boundary={boundary}",
        "CONTENT_LENGTH": str(len(body)),
        "wsgi.input": io.BytesIO(body),
    }

    form = app.parse_form(environ)
    assert form["title"] == "Multipart Title"


def test_cli_new_interactive_no_desc(temp_root, monkeypatch):
    from nira_app.cli import app
    from typer.testing import CliRunner
    from rich.prompt import Prompt, Confirm

    runner = CliRunner()
    monkeypatch.chdir(temp_root)
    runner.invoke(app, ["init", "--project-key", "NIRA"])

    # inputs: Title, Priority, Type, Labels, Due, Parent, Source, Add description? (No)
    inputs = ["No Desc", "medium", "task", "", "", "", "", "n"]
    input_iter = iter(inputs)
    monkeypatch.setattr(Prompt, "ask", lambda *args, **kwargs: next(input_iter))
    monkeypatch.setattr(Confirm, "ask", lambda *args, **kwargs: True if next(input_iter) == "y" else False)

    result = runner.invoke(app, ["new", "-i"])
    assert result.exit_code == 0
    from nira_app.services import TicketService
    from nira_app.storage import NiraStore

    service = TicketService(NiraStore(temp_root))
    assert service.ticket_details("NIRA-1")["ticket"]["body_md"] == ""


def test_web_upload_validation_error(temp_root, monkeypatch):
    from nira_app.web import NiraWebApp
    from nira_app.storage import NiraStore, ValidationError

    store = NiraStore(temp_root)
    store.initialize("NIRA")
    app = NiraWebApp(store)

    # Mock add_attachment to raise ValidationError
    def mock_add_attachment(*args, **kwargs):
        raise ValidationError("File too big")

    monkeypatch.setattr(app.service, "add_attachment", mock_add_attachment)

    boundary = "b"
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="big.txt"\r\n'
        f"Content-Type: text/plain\r\n\r\n"
        f"content\r\n"
        f"--{boundary}--\r\n"
    ).encode("utf-8")

    environ = {
        "REQUEST_METHOD": "POST",
        "PATH_INFO": "/tickets/NIRA-1/attachments",
        "CONTENT_TYPE": f"multipart/form-data; boundary={boundary}",
        "CONTENT_LENGTH": str(len(body)),
        "wsgi.input": io.BytesIO(body),
    }

    def start_response(status, headers):
        assert status == "400 Bad Request"

    resp = app(environ, start_response)
    assert b"File too big" in resp[0]


def test_storage_get_ticket_template(temp_root):
    from nira_app.storage import NiraStore

    store = NiraStore(temp_root)

    # Missing dir
    assert store.get_ticket_template("bug") == ""

    # Create templates dir
    templates_dir = temp_root / ".nira" / "templates"
    templates_dir.mkdir(parents=True)

    # Missing file, no default
    assert store.get_ticket_template("bug") == ""

    # Default file
    (templates_dir / "default.md").write_text("Default template")
    assert store.get_ticket_template("bug") == "Default template"

    # Specific file overrides default
    (templates_dir / "bug.md").write_text("Bug template")
    assert store.get_ticket_template("bug") == "Bug template"


def test_cli_new_template_fallback(temp_root, monkeypatch):
    from nira_app.cli import app
    from typer.testing import CliRunner

    (temp_root / ".nira" / "templates").mkdir(parents=True, exist_ok=True)
    (temp_root / ".nira" / "templates" / "task.md").write_text("Task Template Content")

    runner = CliRunner()
    monkeypatch.chdir(temp_root)
    runner.invoke(app, ["init", "--project-key", "NIRA"])

    # Create without interactive, no body, should use template
    result = runner.invoke(app, ["new", "Test Template", "--type", "task"])
    assert result.exit_code == 0

    # Verify body
    from nira_app.services import TicketService
    from nira_app.storage import NiraStore

    service = TicketService(NiraStore(temp_root))
    assert service.ticket_details("NIRA-1")["ticket"]["body_md"] == "Task Template Content"


def test_web_get_template(temp_root, monkeypatch):
    from nira_app.web import NiraWebApp
    from nira_app.storage import NiraStore

    (temp_root / ".nira" / "templates").mkdir(parents=True, exist_ok=True)
    (temp_root / ".nira" / "templates" / "bug.md").write_text("Bug Template Web")

    store = NiraStore(temp_root)
    store.initialize("NIRA")
    app_inst = NiraWebApp(store)

    env = {
        "REQUEST_METHOD": "GET",
        "PATH_INFO": "/templates/bug",
        "wsgi.input": io.BytesIO(b""),
    }

    def start_response(status, headers):
        assert status == "200 OK"

    resp = app_inst(env, start_response)
    assert b"Bug Template Web" in resp[0]


def test_web_get_template_fallback(temp_root, monkeypatch):
    (temp_root / ".nira" / "templates").mkdir(parents=True, exist_ok=True)
    (temp_root / ".nira" / "templates" / "default.md").write_text("Default Template Web")

    from nira_app.web import NiraWebApp
    from nira_app.storage import NiraStore

    store = NiraStore(temp_root)
    store.initialize("NIRA")
    app_inst = NiraWebApp(store)

    def start_response(status, headers):
        assert status == "200 OK"

    # Test missing type, should fallback to default
    env = {
        "REQUEST_METHOD": "GET",
        "PATH_INFO": "/templates/missing_type",
        "QUERY_STRING": "",
        "wsgi.input": None,
    }
    resp = app_inst(env, start_response)
    assert b"Default Template Web" in resp[0]


def test_get_dashboard_stats_velocity_and_labels(temp_root):
    from nira_app.services import TicketService
    from nira_app.storage import NiraStore

    store = NiraStore(temp_root)
    store.initialize("NIRA")
    service = TicketService(store)

    # Create some tickets with labels
    service.create_ticket("NIRA", "T1", labels="bug, ui")
    service.create_ticket("NIRA", "T2", labels="bug, backend")
    service.create_ticket("NIRA", "T3", labels="ui")

    # Close one ticket to test velocity
    with store.session() as session:
        from nira_app.storage import Ticket

        t1 = session.query(Ticket).filter(Ticket.number == 1).one()
        t1.status = "closed"
        session.commit()

    stats = service.get_dashboard_stats()
    assert stats["velocity"] >= 0
    assert ("bug", 2) in stats["common_labels"]
    assert ("ui", 2) in stats["common_labels"]
    assert ("backend", 1) in stats["common_labels"]
