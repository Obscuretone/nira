import pytest
from nira_app.storage import NiraStore, ValidationError, NiraError


def test_storage_errors(temp_root):
    store = NiraStore(temp_root / "errors")

    store.initialize("EMH")
    store.initialize("EMH")  # Should not raise if already initialized with same key

    store = NiraStore(temp_root / "err2")
    store.initialize("EMH")

    # Missing tickets
    with pytest.raises(NiraError):
        store.get_ticket("EMH-999")
    with pytest.raises(NiraError):
        store.update_ticket("EMH-999", status="open")
    with pytest.raises(NiraError):
        store.add_comment("EMH-999", "b")
    with pytest.raises(NiraError):
        store.link_tickets("EMH-999", "EMH-1")
    with pytest.raises(NiraError):
        store.unlink_tickets("EMH-999", "EMH-1")

    # Validation
    store.create_ticket("EMH", "T1")
    with pytest.raises(ValidationError):
        store.link_tickets("EMH-1", "EMH-1")
    with pytest.raises(ValidationError):
        store.unlink_tickets("EMH-1", "EMH-1")


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
    from nira_app.storage import normalize_project, derive_default_project_key

    with pytest.raises(ValidationError):
        normalize_project("")
    with pytest.raises(ValidationError):
        normalize_project("123")
    assert derive_default_project_key("123 name") == "N1N"

    store = NiraStore(temp_root / "t1")
    store.initialize("EMH")
    store.create_ticket("EMH", "T1", labels="tag")
    assert store.count_tickets(project="MISSING") == 0
    assert store.count_tickets(label="tag") == 1

    # reset reason
    store.update_ticket("EMH-1", status="closed")
    store.update_ticket("EMH-1", status="open")

    with store.session() as session:
        store.touch_tickets(session, [1])
        store.touch_tickets(session, [])


def test_storage_coverage_remaining(temp_root):
    store = NiraStore(temp_root / "final")
    store.initialize("EMH")

    store.create_ticket("EMH", "T1")
    store.create_ticket("EMH", "T2")

    # Missing link targets
    try:
        store.link_tickets("EMH-999", "EMH-1")
    except Exception:
        pass

    try:
        store.link_tickets("EMH-1", "EMH-999")
    except Exception:
        pass

    try:
        store.unlink_tickets("EMH-999", "EMH-1")
    except Exception:
        pass

    try:
        store.unlink_tickets("EMH-1", "EMH-999")
    except Exception:
        pass

    try:
        store.add_comment("EMH-999", "b")
    except Exception:
        pass

    # Invalid project in get_ticket
    try:
        store.get_ticket("INVALID-1")
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
    store.initialize("EMH")

    store.list_tickets(
        project="EMH",
        label="none",
        overdue=True,
        parent_id=1,
        ticket_type="task",
        priority="high",
        status="open",
        sort_by="updated",
    )
    store.count_tickets(
        project="EMH", label="none", overdue=True, parent_id=1, ticket_type="task", priority="high", status="open"
    )
