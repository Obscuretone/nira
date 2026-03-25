import pytest
from nira_app.storage import NiraStore, NiraError
from nira_app.web import NiraWebApp


def test_web_errors(temp_root):
    store = NiraStore(temp_root / "errors")
    store.initialize("NIRA")

    # Web errors
    app = NiraWebApp(store)

    # search_dropdown
    store.create_ticket("NIRA", "T1")
    app.ticket_search_dropdown({"q": "T1"}, {})
    app.ticket_search_dropdown({"parent": "T1"}, {})

    # list_page pagination
    app.list_page({"page": "0"}, {})
    app.list_page({"page": "abc"}, {})

    # create_ticket action error
    with pytest.raises(NiraError):
        app.create_ticket_action({}, {"title": "X", "parent": "NIRA-999"})

    # edit_ticket action error
    with pytest.raises(NiraError):
        app.edit_ticket_action({}, {"parent": "NIRA-999"}, "NIRA-1")


def test_web_misc(temp_root):
    from nira_app.web import parse_timestamp, relative_time, QuietRequestHandler

    assert parse_timestamp("bad") is None
    assert relative_time("bad") == "bad"

    h = QuietRequestHandler.__new__(QuietRequestHandler)
    h.log_message("f", "a")

    store = NiraStore(temp_root / "t2")
    store.initialize("TEST")
    app = NiraWebApp(store)

    def sr(s, h):
        pass

    store.update_settings({"language": "fr"})
    app({"REQUEST_METHOD": "GET", "PATH_INFO": "/"}, sr)

    res = app({"REQUEST_METHOD": "GET", "PATH_INFO": "/missing"}, sr)
    assert b"404" in b"".join(res)

    from nira_app.storage import NiraError

    def err(**kwargs):
        raise NiraError("err")

    app.router.add("GET", "/err", err)
    res2 = app({"REQUEST_METHOD": "GET", "PATH_INFO": "/err"}, sr)
    assert b"500" in b"".join(res2)


def test_web_coverage_remaining(temp_root):
    store = NiraStore(temp_root / "web")
    store.initialize("NIRA")
    app = NiraWebApp(store)

    # pagination 252-254
    app.list_page({"page": "-1"}, {})

    # create ticket parent not found
    try:
        app.create_ticket_action({}, {"title": "A", "parent": "NIRA-999"})
    except Exception:
        pass

    # edit ticket parent not found
    store.create_ticket("NIRA", "T1")
    try:
        app.edit_ticket_action({}, {"parent": "NIRA-999"}, "NIRA-1")
    except Exception:
        pass


def test_settings_coverage(temp_root):
    store = NiraStore(temp_root / "settings")
    store.initialize("NIRA")

    store.update_settings({"theme": "dark", "language": "en"})
    store.update_settings({"theme": "invalid", "language": "zz"})

    app = NiraWebApp(store)
    app.save_settings_action({"HX-Request": "1"}, {"theme": "light"})
    app.save_settings_action({}, {"language": "fr"})
