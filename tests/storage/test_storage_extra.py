import pytest
from nira_app.storage import NiraStore, ValidationError


def test_storage_validation_errors(temp_root):
    store = NiraStore(temp_root / "validation")
    store.initialize("NIRA")

    # Title required
    with pytest.raises(ValidationError, match="Title is required"):
        store.create_ticket("NIRA", "")

    # Title cannot be empty in update
    store.create_ticket("NIRA", "T1")
    with pytest.raises(ValidationError, match="Title cannot be empty"):
        store.update_ticket("NIRA-1", title=" ")

    # Closing requires resolution notes
    with pytest.raises(ValidationError, match="Closing a ticket requires resolution notes"):
        store.close_ticket("NIRA-1", resolution_md=" ")

    # Comment text required
    with pytest.raises(ValidationError, match="Comment text is required"):
        store.add_comment("NIRA-1", " ")


def test_storage_more_coverage(temp_root):
    store = NiraStore(temp_root / "more")
    store.initialize("NIRA")

    # Trigger line 921, 924, 927 in update_ticket
    store.create_ticket("NIRA", "T1")
    store.update_ticket("NIRA-1", labels="  a, b  ", due_date="2023-12-31", parent_id=1)

    # Trigger sorting/filtering logic
    store.list_tickets(sort_by="priority", overdue=True)
    store.list_tickets(sort_by="status", offset=10)
    store.list_tickets(status="closed")
    store.count_tickets(status="closed")


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
