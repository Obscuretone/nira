from pathlib import Path

from nira_app.markdown import render_markdown, safe_url
from nira_app.storage import (
    NiraStore,
    TicketNotFoundError,
    ValidationError,
    derive_default_project_key,
    format_ticket_id,
    normalize_ticket_id,
)


def test_derive_default_project_key_handles_single_word_folder() -> None:
    assert derive_default_project_key("emh") == "EMH"


def test_derive_default_project_key_handles_multi_word_folder() -> None:
    assert derive_default_project_key("employment-matching-hub") == "EMH"
    assert derive_default_project_key("EmploymentMatchingHub") == "EMH"


def test_derive_default_project_key_falls_back_for_empty_names() -> None:
    assert derive_default_project_key("") == "NIRA"


def test_format_and_normalize_ticket_id() -> None:
    assert format_ticket_id("emh", 4) == "EMH-4"
    assert normalize_ticket_id("emh-004") == "EMH-4"


def test_safe_url_rejects_javascript_scheme() -> None:
    assert safe_url("javascript:alert(1)") is None


def test_safe_url_allows_http_and_relative_urls() -> None:
    assert safe_url("https://example.com/docs") == "https://example.com/docs"
    assert safe_url("/tickets/EMH-1") == "/tickets/EMH-1"


def test_render_markdown_renders_common_blocks() -> None:
    rendered = render_markdown("# Title\n\n## Summary\n- first\n- second\n\nParagraph with **bold** and `code`.\n")

    assert "<h1>Title</h1>" in rendered
    assert "<h2>Summary</h2>" in rendered
    assert "<ul>" in rendered
    assert "<li>first</li>" in rendered
    assert "<strong>bold</strong>" in rendered
    assert "<code>code</code>" in rendered


def test_store_ticket_crud(temp_root):
    store = NiraStore(temp_root)
    store.initialize(default_project="TEST")

    # Create
    ticket = store.create_ticket("TEST", "Title", body_md="Body")
    assert ticket["id"] == "TEST-1"
    assert ticket["title"] == "Title"

    # Get
    fetched = store.get_ticket("TEST-1")
    assert fetched["id"] == "TEST-1"

    # List
    tickets = store.list_tickets()
    assert len(tickets) == 1
    assert tickets[0]["id"] == "TEST-1"

    # Update
    updated = store.update_ticket("TEST-1", title="New Title", status="in_progress")
    assert updated["title"] == "New Title"
    assert updated["status"] == "in_progress"

    # Count
    assert store.count_tickets() == 1
    assert store.count_tickets(status="open") == 0
    assert store.count_tickets(status="in_progress") == 1


def test_store_ticket_not_found(temp_root):
    store = NiraStore(temp_root)
    store.initialize()
    try:
        store.get_ticket("NIRA-999")
    except TicketNotFoundError:
        pass
    else:
        assert False, "Should have raised TicketNotFoundError"


def test_store_invalid_project(temp_root):
    store = NiraStore(temp_root)
    store.initialize(default_project="TEST")
    try:
        store.create_ticket("WRONG", "Title")
    except ValidationError:
        pass
    else:
        assert False, "Should have raised ValidationError"


def test_store_comments(temp_root):
    store = NiraStore(temp_root)
    store.initialize(default_project="NIRA")
    store.create_ticket("NIRA", "Title")

    store.add_comment("NIRA-1", "Comment 1")
    comments = store.list_comments("NIRA-1")
    assert len(comments) == 1
    assert comments[0]["body_md"] == "Comment 1"


def test_store_links(temp_root):
    store = NiraStore(temp_root)
    store.initialize(default_project="NIRA")
    store.create_ticket("NIRA", "T1")
    store.create_ticket("NIRA", "T2")

    store.link_tickets("NIRA-1", "NIRA-2")
    links = store.list_links()
    assert len(links) == 1

    related = store.list_related_tickets("NIRA-1")
    assert len(related) == 1
    assert related[0]["id"] == "NIRA-2"

    store.unlink_tickets("NIRA-1", "NIRA-2")
    assert len(store.list_links()) == 0


def test_entrypoint_exists() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    assert (repo_root / "pyproject.toml").exists()


def test_store_settings(temp_root: Path) -> None:
    store = NiraStore(temp_root)
    store.initialize(default_project="NIRA")

    settings = store.get_settings()
    assert settings["default_project"] == "NIRA"
    assert settings["theme"] == "auto"

    store.update_settings({"theme": "dark"})
    settings = store.get_settings()
    assert settings["theme"] == "dark"

    store.update_settings({"default_project": "PROJ", "theme": "light"})
    settings = store.get_settings()
    assert settings["default_project"] == "PROJ"
    assert settings["theme"] == "light"

    # Test invalid theme fallback
    store.update_settings({"theme": "invalid"})
    assert store.get_settings()["theme"] == "auto"
