import pytest
from nira_app.storage import NiraStore, ValidationError, NiraError


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


def test_missing_ticket_operations(temp_root):
    store = NiraStore(temp_root / "ops")
    store.initialize("NIRA")

    with pytest.raises(NiraError):
        store.get_ticket("NIRA-1")

    store.create_ticket("NIRA", "T1")
    # Add comment with non-existent ticket
    with pytest.raises(NiraError):
        store.add_comment("NIRA-999", "comment")
