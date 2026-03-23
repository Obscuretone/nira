from pathlib import Path

from nira_app.markdown import render_markdown, safe_url
from nira_app.storage import derive_default_project_key, format_ticket_id, normalize_ticket_id


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
    rendered = render_markdown(
        "# Title\n\n## Summary\n- first\n- second\n\nParagraph with **bold** and `code`.\n"
    )

    assert "<h1>Title</h1>" in rendered
    assert "<h2>Summary</h2>" in rendered
    assert "<ul>" in rendered
    assert "<li>first</li>" in rendered
    assert "<strong>bold</strong>" in rendered
    assert "<code>code</code>" in rendered


def test_entrypoint_exists() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    assert (repo_root / "pyproject.toml").exists()
