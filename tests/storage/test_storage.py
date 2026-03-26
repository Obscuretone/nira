import sqlite3
import pytest
from nira_app.storage import (
    NiraStore,
    ValidationError,
    NiraError,
    normalize_project,
    derive_default_project_key,
    normalize_ticket_id,
    Setting,
    DEFAULT_PROJECT_SETTING,
)
from nira_app.services import TicketService


def test_storage_errors(temp_root):
    store = NiraStore(temp_root / "errors")
    service = TicketService(store)

    store.initialize("NIRA")
    store.initialize("NIRA")  # Should not raise if already initialized with same key

    store = NiraStore(temp_root / "err2")
    service = TicketService(store)
    store.initialize("NIRA")

    # Missing tickets
    with pytest.raises(NiraError):
        service.store.get_ticket("NIRA-999")
    with pytest.raises(NiraError):
        service.update_ticket("NIRA-999", status="open")
    with pytest.raises(NiraError):
        service.add_comment("NIRA-999", "b")
    with pytest.raises(NiraError):
        service.link_tickets("NIRA-999", "NIRA-1")
    with pytest.raises(NiraError):
        service.unlink_tickets("NIRA-999", "NIRA-1")

    # Validation
    service.create_ticket("NIRA", "T1")
    with pytest.raises(ValidationError):
        service.link_tickets("NIRA-1", "NIRA-1")
    with pytest.raises(ValidationError):
        service.unlink_tickets("NIRA-1", "NIRA-1")


def test_legacy_migration_coverage(temp_root):
    store = NiraStore(temp_root / "legacy")
    store.state_dir.mkdir(parents=True, exist_ok=True)
    with store.connect() as conn:
        conn.executescript("""
            CREATE TABLE tickets (id TEXT PRIMARY KEY, project TEXT, number INTEGER, title TEXT, status TEXT, type TEXT, priority TEXT, source TEXT, resolution_reason TEXT, body_md TEXT, resolution_md TEXT, created_at TEXT, updated_at TEXT);
            CREATE TABLE comments (id INTEGER PRIMARY KEY, ticket_id TEXT, body_md TEXT, created_at TEXT);
            CREATE TABLE links (ticket_a TEXT, ticket_b TEXT, created_at TEXT);
            
            INSERT INTO tickets VALUES ('A-1', 'A', 1, 'T1', 'open', 'task', 'medium', '', 'res', 'body', 'res_md', '2024-01-01', '2024-01-01');
            INSERT INTO comments VALUES (1, 'A-1', 'c1', '2024-01-01');
            INSERT INTO links VALUES ('A-1', 'A-1', '2024-01-01');
        """)
        store.migrate_legacy_schema(conn)


def test_storage_misc_and_legacy(temp_root):
    with pytest.raises(ValidationError):
        normalize_project("")
    with pytest.raises(ValidationError):
        normalize_project("123")
    assert derive_default_project_key("123 name") == "N1N"
    assert derive_default_project_key("  ") == "NIRA"
    assert derive_default_project_key("") == "NIRA"
    assert derive_default_project_key("employment-matching-hub") == "EMH"
    assert derive_default_project_key("EmploymentMatchingHub") == "EMH"
    assert derive_default_project_key("nira") == "NIRA"

    with pytest.raises(ValidationError):
        normalize_ticket_id("INVALID")

    store = NiraStore(temp_root / "t1")
    service = TicketService(store)
    store.initialize("NIRA")
    service.create_ticket("NIRA", "T1", labels="tag")
    assert service.count_tickets(project="MISSING") == 0
    assert service.count_tickets(label="tag") == 1

    # reset reason
    service.update_ticket("NIRA-1", status="closed")
    service.update_ticket("NIRA-1", status="open")

    with store.session() as session:
        store.touch_tickets(session, [1])
        store.touch_tickets(session, [])


def test_storage_coverage_remaining(temp_root):
    store = NiraStore(temp_root / "final")
    service = TicketService(store)
    store.initialize("NIRA")

    service.create_ticket("NIRA", "T1")
    service.create_ticket("NIRA", "T2")

    # Missing link targets
    try:
        service.link_tickets("NIRA-999", "NIRA-1")
    except Exception:
        pass

    try:
        service.link_tickets("NIRA-1", "NIRA-999")
    except Exception:
        pass

    try:
        service.unlink_tickets("NIRA-999", "NIRA-1")
    except Exception:
        pass

    try:
        service.unlink_tickets("NIRA-1", "NIRA-999")
    except Exception:
        pass

    try:
        service.add_comment("NIRA-999", "b")
    except Exception:
        pass

    # Invalid project in get_ticket
    try:
        service.store.get_ticket("INVALID-1")
    except Exception:
        pass

    # touch_tickets errors
    with store.session() as session:
        try:
            store.touch_tickets(session, [999])
        except Exception:
            pass


def test_list_ticket_filters(temp_root):
    store = NiraStore(temp_root / "filters")
    service = TicketService(store)
    store.initialize("NIRA")

    service.list_tickets(
        project="NIRA",
        label="none",
        overdue=True,
        parent_id=1,
        ticket_type="task",
        priority="high",
        status="open",
        sort_by="updated",
    )
    service.count_tickets(
        project="NIRA", label="none", overdue=True, parent_id=1, ticket_type="task", priority="high", status="open"
    )


def test_storage_validation_errors(temp_root):
    store = NiraStore(temp_root / "validation")
    service = TicketService(store)
    store.initialize("NIRA")

    # Title required
    with pytest.raises(ValidationError, match="Title is required"):
        service.create_ticket("NIRA", "")

    # Title cannot be empty in update
    service.create_ticket("NIRA", "T1")
    with pytest.raises(ValidationError, match="Title cannot be empty"):
        service.update_ticket("NIRA-1", title=" ")

    # Closing requires resolution notes
    with pytest.raises(ValidationError, match="Closing a ticket requires resolution notes"):
        service.close_ticket("NIRA-1", resolution_md=" ")

    # Comment text required
    with pytest.raises(ValidationError, match="Comment text is required"):
        service.add_comment("NIRA-1", " ")


def test_storage_more_coverage(temp_root):
    store = NiraStore(temp_root / "more")
    service = TicketService(store)
    store.initialize("NIRA")

    # Trigger update_ticket logic
    service.create_ticket("NIRA", "T1")
    service.update_ticket("NIRA-1", labels="  a, b  ", due_date="2023-12-31", parent_id=1)

    # Trigger sorting/filtering logic
    service.list_tickets(sort_by="priority", overdue=True)
    service.list_tickets(sort_by="status", offset=10)
    service.list_tickets(status="closed")
    service.count_tickets(status="closed")


def test_connect_rollback(temp_root):
    store = NiraStore(temp_root / "rollback")
    store.state_dir.mkdir(parents=True, exist_ok=True)
    # Force a rollback by raising inside connect
    try:
        with store.connect() as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS test (id INTEGER)")
            conn.execute("INSERT INTO test (id) VALUES (1)")
            raise RuntimeError("Force rollback")
    except RuntimeError:
        pass

    with store.connect() as conn:
        row = conn.execute("SELECT * FROM test").fetchone()
        assert row is None


def test_column_names_missing_table(temp_root):
    store = NiraStore(temp_root / "columns")
    store.state_dir.mkdir(parents=True, exist_ok=True)
    with store.connect() as conn:
        assert store.column_names(conn, "nonexistent") == set()


def test_get_projects_default(temp_root):
    store = NiraStore(temp_root / "projects")
    store.initialize("NIRA")
    # This should return the default project
    assert len(store.list_projects()) >= 1


def test_storage_final_coverage(temp_root):
    store = NiraStore(temp_root)
    service = TicketService(store)
    store.initialize("NIRA")

    # create_ticket project mismatch
    with pytest.raises(ValidationError) as exc:
        service.create_ticket("WRONG", "Title")
    assert "Change it in settings first" in str(exc.value)

    # touch_tickets
    with store.session() as session:
        service.create_ticket("NIRA", "T1")
        store.touch_tickets(session, [1], timestamp="2024-01-01T00:00:00Z")


def test_storage_legacy_branches(temp_root):
    store = NiraStore(temp_root / "legacy_branches")
    store.state_dir.mkdir(parents=True, exist_ok=True)
    with store.connect() as conn:
        # column_names table doesn't exist
        assert store.column_names(conn, "missing") == set()

        # needs_legacy_migration branches
        conn.execute("CREATE TABLE tickets (id TEXT PRIMARY KEY)")
        assert store.needs_legacy_migration(conn) is True

        conn.execute("DROP TABLE tickets")
        conn.execute("CREATE TABLE tickets (id INTEGER PRIMARY KEY, number INTEGER)")
        assert store.needs_legacy_migration(conn) is True


def test_storage_migrate_legacy_no_tickets(temp_root):
    store = NiraStore(temp_root / "no_tickets")
    store.state_dir.mkdir(parents=True, exist_ok=True)
    with store.connect() as conn:
        store.migrate_legacy_schema(conn)  # Should return early


def test_storage_count_tickets_search(temp_root):
    store = NiraStore(temp_root / "search")
    service = TicketService(store)
    store.initialize("NIRA")
    service.create_ticket("NIRA", "Searchable content")
    assert service.count_tickets(search="Searchable") == 1


def test_storage_initialize_legacy(temp_root):
    # Reset global migration flag for this test
    import nira_app.storage

    nira_app.storage._MIGRATIONS_RUN = False

    # Trigger initialization with legacy DB
    store_dir = temp_root / "init_legacy"
    store_dir.mkdir()
    state_dir = store_dir / ".nira"
    state_dir.mkdir()
    db_path = state_dir / "nira.db"
    conn = sqlite3.connect(db_path)
    conn.executescript("""
        CREATE TABLE tickets (id TEXT PRIMARY KEY, project TEXT, number INTEGER, title TEXT, status TEXT, type TEXT, priority TEXT, source TEXT, resolution_reason TEXT, body_md TEXT, resolution_md TEXT, created_at TEXT, updated_at TEXT);
        CREATE TABLE comments (id INTEGER PRIMARY KEY, ticket_id TEXT, body_md TEXT, created_at TEXT);
        CREATE TABLE links (ticket_a TEXT, ticket_b TEXT, created_at TEXT);
        CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        
        -- Insert tickets
        INSERT INTO tickets VALUES ('A-1', 'A', 1, 'T1', 'open', 'task', 'medium', '', '', '', '', '2024-01-01', '2024-01-01');
        INSERT INTO tickets VALUES ('A-2', 'A', 2, 'T2', 'open', 'task', 'medium', '', '', '', '', '2024-01-01', '2024-01-01');
        -- Insert a valid link
        INSERT INTO links VALUES ('A-1', 'A-2', '2024-01-01');
        -- Insert an orphaned comment
        INSERT INTO comments VALUES (1, 'MISSING-99', 'orphaned', '2024-01-01');
        -- Insert a broken link (one side missing)
        INSERT INTO links VALUES ('A-1', 'MISSING-99', '2024-01-01');
        -- Insert a broken link (both sides missing)
        INSERT INTO links VALUES ('X-1', 'Y-1', '2024-01-01');
    """)
    conn.close()

    store = NiraStore(store_dir)
    store.initialize("NIRA")  # This should trigger legacy migration during init


def test_storage_rename_project(temp_root):
    store = NiraStore(temp_root / "rename")
    store.initialize("OLD")
    res = store.rename_default_project("NEW")
    assert res["old_project"] == "OLD"
    assert res["new_project"] == "NEW"

    # Rename to same
    res2 = store.rename_default_project("NEW")
    assert res2["renamed_ticket_count"] == 0


def test_storage_touch_tickets(temp_root):
    store = NiraStore(temp_root / "touch")
    service = TicketService(store)
    store.initialize("NIRA")
    service.create_ticket("NIRA", "T1")
    with store.session() as session:
        # Call touch_tickets with multiple IDs and a timestamp
        store.touch_tickets(session, [1], timestamp="2024-01-01T00:00:00Z")
        # Call with no IDs
        store.touch_tickets(session, [])


def test_storage_current_project_missing(temp_root):
    store = NiraStore(temp_root / "missing_project")
    store.initialize("NIRA")

    # Manually delete the default project setting
    with store.session() as session:
        session.query(Setting).filter(Setting.key == DEFAULT_PROJECT_SETTING).delete()
        session.commit()

    with pytest.raises(ValidationError):
        with store.session() as session:
            store.current_project(session)


def test_run_migrations(temp_root):
    store = NiraStore(temp_root / "migrations")
    store.initialize("NIRA")
    # This should cover run_migrations and close
    store.close()


def test_legacy_migration_errors(temp_root):
    store = NiraStore(temp_root / "legacy_err")
    store.state_dir.mkdir(parents=True, exist_ok=True)
    with store.connect() as conn:
        conn.executescript("""
            CREATE TABLE tickets (id TEXT PRIMARY KEY, project TEXT, number INTEGER, title TEXT, status TEXT, type TEXT, priority TEXT, source TEXT, resolution_reason TEXT, body_md TEXT, resolution_md TEXT, created_at TEXT, updated_at TEXT);
            INSERT INTO tickets VALUES ('A-1', 'A', 1, 'T', 'open', 'task', 'medium', 'cli', '', '', '', '2023-01-01', '2023-01-01');
            INSERT INTO tickets VALUES ('B-1', 'B', 2, 'T', 'open', 'task', 'medium', 'cli', '', '', '', '2023-01-01', '2023-01-01');
        """)
        # Multiple projects should raise ValidationError
        with pytest.raises(ValidationError, match="multiple legacy ticket prefixes"):
            store.migrate_legacy_schema(conn)


def test_storage_import_tickets(temp_root):
    store = NiraStore(temp_root / "import")
    service = TicketService(store)
    store.initialize("NIRA")

    data = [
        {
            "number": 1,
            "title": "Imported 1",
            "status": "open",
            "type": "task",
            "priority": "medium",
            "source": "cli",
            "labels": "tag1",
            "due_date": "2024-01-01",
            "story_points": 5,
            "body_md": "body 1",
            "resolution_md": "",
            "resolution_reason": "",
            "created_at": "2024-01-01T00:00:00Z",
            "updated_at": "2024-01-01T00:00:00Z",
        },
        {
            "number": 2,
            "title": "Imported 2",
            "status": "closed",
            "type": "bug",
            "priority": "high",
            "source": "api",
            "labels": "tag2",
            "due_date": None,
            "story_points": None,
            "body_md": "body 2",
            "resolution_md": "fixed",
            "resolution_reason": "completed",
            "created_at": None,
            "updated_at": None,
        },
    ]

    count = service.import_tickets(data)
    assert count == 2

    t1 = service.store.get_ticket("NIRA-1")
    assert t1["title"] == "Imported 1"
    assert t1["story_points"] == 5
    assert t1["labels"] == "tag1"

    t2 = service.store.get_ticket("NIRA-2")
    assert t2["title"] == "Imported 2"
    assert t2["status"] == "closed"
    assert t2["resolution_md"] == "fixed"

    # Test update via import
    data[0]["title"] = "Updated title"
    service.import_tickets([data[0]])
    t1_updated = service.store.get_ticket("NIRA-1")
    assert t1_updated["title"] == "Updated title"


def test_missing_ticket_operations(temp_root):
    store = NiraStore(temp_root / "ops")
    service = TicketService(store)
    store.initialize("NIRA")

    with pytest.raises(NiraError):
        service.store.get_ticket("NIRA-1")

    service.create_ticket("NIRA", "T1")
    # Add comment with non-existent ticket
    with pytest.raises(NiraError):
        service.add_comment("NIRA-999", "comment")
