import unittest
from pathlib import Path

from nira_app.markdown import render_markdown, safe_url
from nira_app.storage import derive_default_project_key, format_ticket_id, normalize_ticket_id


class ProjectKeyTests(unittest.TestCase):
    def test_derive_default_project_key_handles_single_word_folder(self) -> None:
        self.assertEqual(derive_default_project_key("emh"), "EMH")

    def test_derive_default_project_key_handles_multi_word_folder(self) -> None:
        self.assertEqual(derive_default_project_key("employment-matching-hub"), "EMH")
        self.assertEqual(derive_default_project_key("EmploymentMatchingHub"), "EMH")

    def test_derive_default_project_key_falls_back_for_empty_names(self) -> None:
        self.assertEqual(derive_default_project_key(""), "NIRA")

    def test_format_and_normalize_ticket_id(self) -> None:
        self.assertEqual(format_ticket_id("emh", 4), "EMH-4")
        self.assertEqual(normalize_ticket_id("emh-004"), "EMH-4")


class MarkdownTests(unittest.TestCase):
    def test_safe_url_rejects_javascript_scheme(self) -> None:
        self.assertIsNone(safe_url("javascript:alert(1)"))

    def test_safe_url_allows_http_and_relative_urls(self) -> None:
        self.assertEqual(safe_url("https://example.com/docs"), "https://example.com/docs")
        self.assertEqual(safe_url("/tickets/EMH-1"), "/tickets/EMH-1")

    def test_render_markdown_renders_common_blocks(self) -> None:
        rendered = render_markdown(
            "# Title\n\n## Summary\n- first\n- second\n\nParagraph with **bold** and `code`.\n"
        )

        self.assertIn("<h1>Title</h1>", rendered)
        self.assertIn("<h2>Summary</h2>", rendered)
        self.assertIn("<ul>", rendered)
        self.assertIn("<li>first</li>", rendered)
        self.assertIn("<strong>bold</strong>", rendered)
        self.assertIn("<code>code</code>", rendered)


class RepositoryShapeTests(unittest.TestCase):
    def test_entrypoint_exists(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        self.assertTrue((repo_root / "pyproject.toml").exists())
