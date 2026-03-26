from nira_app.storage import NiraStore
from nira_app.web import NiraWebApp


def test_web_search_robust(temp_root):
    store = NiraStore(temp_root)
    store.initialize("NIRA")
    app = NiraWebApp(store)

    # Create some tickets
    app.service.create_ticket("NIRA", "Robust Migration")
    app.service.create_ticket("NIRA", "Simple Task")

    # Search for "robust"
    res = app.list_page({"search": "robust"}, {})
    body = res.body
    assert isinstance(body, str)
    assert '<mark class="p-0">Robust</mark> Migration' in body
    assert "Simple Task" not in body

    # Search for ticket ID (no highlight expected in title)
    res = app.list_page({"search": "NIRA-1"}, {})
    body = res.body
    assert isinstance(body, str)
    assert "Robust Migration" in body

    # Search for just number (no highlight expected in title)
    res = app.list_page({"search": "1"}, {})
    assert isinstance(res.body, str)
    assert "Robust Migration" in res.body

    # Search with special characters (FTS5 sanitization)
    res = app.list_page({"search": "robust*"}, {})
    assert isinstance(res.body, str)
    assert '<mark class="p-0">Robust</mark> Migration' in res.body

    # Search with spaces
    res = app.list_page({"search": "Robust Migration"}, {})
    assert isinstance(res.body, str)
    assert '<mark class="p-0">Robust</mark> <mark class="p-0">Migration</mark>' in res.body

    # Search with multiple words not in order
    res = app.list_page({"search": "Migration Robust"}, {})
    assert isinstance(res.body, str)
    assert '<mark class="p-0">Robust</mark> <mark class="p-0">Migration</mark>' in res.body

    # Search for something non-existent
    res = app.list_page({"search": "missing"}, {})
    assert isinstance(res.body, str)
    assert "Robust Migration" not in res.body
    assert "No tickets match the current filters" in res.body
