from nira_app.markdown import render_markdown, safe_url


def test_safe_url_invalid_scheme():
    assert safe_url("javascript:alert(1)") is None


def test_markdown_render_coverage():
    assert "href" in render_markdown("[link](http://example.com)")
    assert "link" in render_markdown("[link](javascript:void)")
    assert "<h1>" in render_markdown("# H1")
    assert "<h2>" in render_markdown("## H2")
    assert "<h3>" in render_markdown("### H3")
    assert "<strong>" in render_markdown("**bold**")
    assert "<em>" in render_markdown("*italic*")
    assert "<code>" in render_markdown("`code`")
    assert "<pre><code>\nline1\n</code></pre>" in render_markdown("```\nline1\n```")
    assert "<pre><code>\nunclosed" in render_markdown("```\nunclosed")
    assert "<li>item</li>" in render_markdown("- item")
    assert render_markdown("") == ""
    assert "<p>p1</p>\n<p>p2</p>" in render_markdown("p1\n\np2")
