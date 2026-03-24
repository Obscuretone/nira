import pytest
import sqlite3
from nira_app.storage import NiraStore, ValidationError


def test_storage_final_coverage(temp_root):
    store = NiraStore(temp_root)
    store.initialize("NIRA")

    # create_ticket project mismatch
    with pytest.raises(ValidationError) as exc:
        store.create_ticket("WRONG", "Title")
    assert "Change it in settings first" in str(exc.value)

    # touch_tickets
    with store.session() as session:
        store.create_ticket("NIRA", "T1")
        store.touch_tickets(session, [1], timestamp="2024-01-01T00:00:00Z")

    # get_settings with manual custom_statuses
    store.update_settings({"statuses": "todo,done"})
    settings = store.get_settings()
    assert settings["statuses"] == ["todo", "done"]

    # update_settings with empty statuses
    store.update_settings({"statuses": ""})
    settings = store.get_settings()
    from typing import cast, List

    assert "open" in cast(List[str], settings["statuses"])


def test_storage_legacy_branches(temp_root):
    store = NiraStore(temp_root / "legacy")
    store.state_dir.mkdir(parents=True, exist_ok=True)
    with store.connect() as conn:
        # column_names table doesn't exist
        assert store.column_names(conn, "missing") == set()

        # needs_legacy_migration branches
        conn.execute("CREATE TABLE tickets (id TEXT PRIMARY KEY)")
        assert store.needs_legacy_migration(conn) is True

        conn.execute("DROP TABLE tickets")
        conn.execute("CREATE TABLE tickets (id INTEGER PRIMARY KEY, project TEXT)")
        assert store.needs_legacy_migration(conn) is True


def test_storage_migrate_legacy_no_tickets(temp_root):
    store = NiraStore(temp_root / "no_tickets")
    store.state_dir.mkdir(parents=True, exist_ok=True)
    with store.connect() as conn:
        store.migrate_legacy_schema(conn)  # Should return early


def test_storage_count_tickets_search(temp_root):
    store = NiraStore(temp_root / "search")
    store.initialize("NIRA")
    store.create_ticket("NIRA", "Searchable content")
    assert store.count_tickets(search="Searchable") == 1


def test_storage_initialize_legacy(temp_root):
    # Reset global migration flag for this test
    import nira_app.storage

    nira_app.storage._MIGRATIONS_RUN = False

    # Trigger 254-255: initialize with legacy DB
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
    store.initialize("NIRA")
    store.create_ticket("NIRA", "T1")
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
        from nira_app.storage import Setting, DEFAULT_PROJECT_SETTING

        session.query(Setting).filter(Setting.key == DEFAULT_PROJECT_SETTING).delete()
        session.commit()

    with pytest.raises(ValidationError):
        with store.session() as session:
            store.current_project(session)
