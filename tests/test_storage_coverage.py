import pytest
from nira_app.storage import (
    normalize_project,
    derive_default_project_key,
    normalize_ticket_id,
    ValidationError,
    NiraStore,
    NiraError,
)


def test_normalize_project_errors():
    with pytest.raises(ValidationError, match="Project key is required"):
        normalize_project("")
    with pytest.raises(ValidationError, match="Project keys must start with a letter"):
        normalize_project("123INVALID")


def test_derive_default_project_key_empty():
    assert derive_default_project_key("...") == "NIRA"
    assert derive_default_project_key("123") == "N123"


def test_normalize_ticket_id_errors():
    with pytest.raises(ValidationError, match="Ticket IDs must look like EMH-1"):
        normalize_ticket_id("INVALID")
    with pytest.raises(ValidationError, match="Ticket IDs must look like EMH-1"):
        normalize_ticket_id("")


def test_connect_rollback(tmp_path):
    store = NiraStore(tmp_path)
    store.initialize("TEST")
    with pytest.raises(Exception, match="rollback test"):
        with store.connect():
            raise Exception("rollback test")


def test_column_names_missing_table(tmp_path):
    store = NiraStore(tmp_path)
    store.initialize("TEST")
    with store.connect() as conn:
        cols = store.column_names(conn, "missing_table")
        assert cols == set()


def test_needs_legacy_migration_id_type(tmp_path):
    store = NiraStore(tmp_path)
    # create .nira dir manually without initialize to have an empty DB
    store.state_dir.mkdir(parents=True, exist_ok=True)
    with store.connect() as conn:
        conn.execute("CREATE TABLE tickets (id TEXT, number INTEGER)")
        assert store.needs_legacy_migration(conn) is True


def test_ensure_default_project_insert(tmp_path):
    store = NiraStore(tmp_path)
    store.state_dir.mkdir(parents=True, exist_ok=True)
    with store.connect() as conn:
        conn.execute("CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT)")
        store.ensure_default_project(conn, "EMH", force=False)
        row = conn.execute("SELECT value FROM settings WHERE key = 'default_project'").fetchone()
        assert row["value"] == "EMH"


def test_migrate_legacy_schema_no_tickets(tmp_path):
    store = NiraStore(tmp_path)
    store.state_dir.mkdir(parents=True, exist_ok=True)
    with store.connect() as conn:
        store.migrate_legacy_schema(conn)


def test_migrate_legacy_schema_multiple_projects(tmp_path):
    store = NiraStore(tmp_path)
    store.state_dir.mkdir(parents=True, exist_ok=True)
    with store.connect() as conn:
        conn.execute("""
            CREATE TABLE tickets (
                id TEXT PRIMARY KEY, project TEXT, number INTEGER, title TEXT, status TEXT, type TEXT, priority TEXT, source TEXT,
                resolution_reason TEXT, body_md TEXT, resolution_md TEXT, created_at TEXT, updated_at TEXT
            )
        """)
        conn.execute(
            "INSERT INTO tickets (id, project, number, title, status, type, priority, source, resolution_reason, body_md, resolution_md, created_at, updated_at) VALUES ('A-1', 'A', 1, '', '', '', '', '', '', '', '', '', '')"
        )
        conn.execute(
            "INSERT INTO tickets (id, project, number, title, status, type, priority, source, resolution_reason, body_md, resolution_md, created_at, updated_at) VALUES ('B-1', 'B', 1, '', '', '', '', '', '', '', '', '', '')"
        )
        with pytest.raises(ValidationError, match="multiple legacy ticket prefixes"):
            store.migrate_legacy_schema(conn)


def test_migrate_legacy_schema_orphaned_data(tmp_path):
    store = NiraStore(tmp_path)
    store.state_dir.mkdir(parents=True, exist_ok=True)
    with store.connect() as conn:
        conn.executescript("""
            CREATE TABLE tickets (
                id TEXT PRIMARY KEY, project TEXT, number INTEGER, title TEXT, status TEXT, type TEXT, priority TEXT, source TEXT,
                resolution_reason TEXT, body_md TEXT, resolution_md TEXT, created_at TEXT, updated_at TEXT
            );
            CREATE TABLE comments (id INTEGER PRIMARY KEY, ticket_id TEXT, body_md TEXT, created_at TEXT);
            CREATE TABLE links (ticket_a TEXT, ticket_b TEXT, created_at TEXT);
        """)
        conn.execute(
            "INSERT INTO tickets (id, project, number, title, status, type, priority, source, resolution_reason, body_md, resolution_md, created_at, updated_at) VALUES ('A-1', 'A', 1, '', '', '', '', '', '', '', '', '', '')"
        )

        # Orphaned comment
        conn.execute("INSERT INTO comments (ticket_id, body_md, created_at) VALUES ('A-99', 'hello', '')")

        # Orphaned links
        conn.execute("INSERT INTO links (ticket_a, ticket_b, created_at) VALUES ('A-1', 'A-99', '')")
        conn.execute("INSERT INTO links (ticket_a, ticket_b, created_at) VALUES ('A-99', 'A-1', '')")

        store.migrate_legacy_schema(conn)

        # Assert they were dropped during migration
        comments = conn.execute("SELECT * FROM comments").fetchall()
        assert len(comments) == 0

        links = conn.execute("SELECT * FROM links").fetchall()
        assert len(links) == 0


def test_list_projects(tmp_path):
    store = NiraStore(tmp_path)
    store.initialize("EMH")
    assert store.list_projects() == ["EMH"]


def test_create_ticket_empty_title(tmp_path):
    store = NiraStore(tmp_path)
    store.initialize("EMH")
    with pytest.raises(ValidationError, match="Title is required"):
        store.create_ticket("EMH", "   ")


def test_update_settings_edge_cases(tmp_path):
    store = NiraStore(tmp_path)
    store.initialize("EMH")

    # 1. invalid theme
    store.update_settings({"theme": "invalid"})
    assert store.get_settings()["theme"] == "auto"

    # 2. update existing theme
    store.update_settings({"theme": "dark"})
    assert store.get_settings()["theme"] == "dark"

    # 3. invalid language
    store.update_settings({"language": "zz"})
    assert store.get_settings()["language"] == "auto"

    # 4. update existing language
    store.update_settings({"language": "fr"})
    assert store.get_settings()["language"] == "fr"

    # 5. insert custom statuses
    store.update_settings({"statuses": " open , in review , done "})
    settings = store.get_settings()
    assert settings["statuses"] == ["open", "in_review", "done"]

    # 6. empty statuses fallback
    store.update_settings({"statuses": "   ,   ,  "})
    assert store.get_settings()["statuses"] == ["open", "in_progress", "closed"]

    # 7. duplicate statuses
    store.update_settings({"statuses": "open, open, closed"})
    assert store.get_settings()["statuses"] == ["open", "closed"]


def test_rename_default_project(tmp_path):
    store = NiraStore(tmp_path)
    store.initialize("EMH")
    store.create_ticket("EMH", "Test")

    # Rename to same
    res = store.rename_default_project("EMH")
    assert res["renamed_ticket_count"] == 0

    # Rename to new
    res = store.rename_default_project("NIRA")
    assert res["renamed_ticket_count"] == 1
    assert store.get_default_project() == "NIRA"


def test_list_and_count_tickets_filters(tmp_path):
    store = NiraStore(tmp_path)
    store.initialize("EMH")
    store.create_ticket("EMH", "Test 1", labels="bug", priority="high", ticket_type="bug", due_date="2020-01-01")
    store.create_ticket(
        "EMH", "Test 2", labels="enhancement", priority="low", ticket_type="task", due_date="2030-01-01"
    )
    store.update_ticket("EMH-2", status="closed")

    # Project mismatch
    assert store.list_tickets(project="MISSING") == []

    # Label filter
    assert len(store.list_tickets(label="bug")) == 1

    # Overdue filter
    assert len(store.list_tickets(overdue=True)) == 1

    # Status not_closed
    assert len(store.list_tickets(status="not_closed")) == 1
    assert store.count_tickets(status="not_closed") == 1

    # Specific status
    assert len(store.list_tickets(status="open")) == 1
    assert store.count_tickets(status="open") == 1

    # Priority
    assert len(store.list_tickets(priority="high")) == 1
    assert store.count_tickets(priority="high") == 1

    # Ticket type
    assert len(store.list_tickets(ticket_type="bug")) == 1
    assert store.count_tickets(ticket_type="bug") == 1


def test_ticket_not_found(tmp_path):
    store = NiraStore(tmp_path)
    store.initialize("EMH")
    with pytest.raises(NiraError, match="not found"):
        store.get_ticket("EMH-99")


def test_list_and_count_more_filters(tmp_path):
    store = NiraStore(tmp_path)
    store.initialize("EMH")
    ticket = store.create_ticket("EMH", "Parent")
    store.create_ticket("EMH", "Child", parent_id=ticket["db_id"])

    assert len(store.list_tickets(parent_id=ticket["db_id"])) == 1
    assert store.count_tickets(parent_id=ticket["db_id"]) == 1

    # Sorting
    assert len(store.list_tickets(sort_by="ticket_id", direction="asc")) == 2
    assert len(store.list_tickets(sort_by="status", direction="asc")) == 2


def test_update_ticket_edge_cases(tmp_path):
    store = NiraStore(tmp_path)
    store.initialize("EMH")
    store.create_ticket("EMH", "Test")

    with pytest.raises(ValidationError, match="Title cannot be empty"):
        store.update_ticket("EMH-1", title="   ")

    with pytest.raises(ValidationError, match="Status must be one of"):
        store.update_ticket("EMH-1", status="invalid")

    store.update_ticket("EMH-1", due_date="")
    t2 = store.get_ticket("EMH-1")
    assert t2["due_date"] == ""


def test_link_unlink_errors(tmp_path):
    store = NiraStore(tmp_path)
    store.initialize("EMH")
    store.create_ticket("EMH", "T1")
    store.create_ticket("EMH", "T2")

    with pytest.raises(ValidationError, match="A ticket cannot be related to itself."):
        store.link_tickets("EMH-1", "EMH-1")

    store.link_tickets("EMH-1", "EMH-2")
    store.link_tickets("EMH-1", "EMH-2")

    store.unlink_tickets("EMH-1", "EMH-2")
    store.unlink_tickets("EMH-1", "EMH-2")
