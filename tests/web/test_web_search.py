from nira_app.storage import NiraStore
from nira_app.web import NiraWebApp


def test_web_search_robust(temp_root):
    store = NiraStore(temp_root)
    store.initialize("NIRA")

    # Create some tickets
    store.create_ticket("NIRA", "Robust Migration")
    store.create_ticket("NIRA", "Simple Task")

    app = NiraWebApp(store)

    # Search for "robust"
    res = app.list_page({"search": "robust"}, {})
    body = res.body
    assert isinstance(body, str)
    assert "Robust Migration" in body
    assert "Simple Task" not in body

    # Search for ticket ID
    res = app.list_page({"search": "NIRA-1"}, {})
    body = res.body
    assert isinstance(body, str)
    assert "Robust Migration" in body

    # Search for just number
    res = app.list_page({"search": "1"}, {})
    assert isinstance(res.body, str)
    assert "Robust Migration" in res.body

    # Search with special characters (FTS5 sanitization)
    res = app.list_page({"search": "robust*"}, {})
    assert isinstance(res.body, str)
    assert "Robust Migration" in res.body
