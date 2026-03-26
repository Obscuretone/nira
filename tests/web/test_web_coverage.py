import pytest
from nira_app.web import NiraWebApp, relative_time
from nira_app.storage import NiraStore, TicketNotFoundError, ValidationError
from datetime import datetime, timedelta, UTC


def test_highlight_helper():
    from nira_app.web import highlight

    assert highlight("Hello World", None) == "Hello World"
    assert highlight("Hello World", "  ") == "Hello World"
    assert highlight("Hello World", "hello") == '<mark class="p-0">Hello</mark> World'
    assert highlight("Hello World", "world") == 'Hello <mark class="p-0">World</mark>'
    assert highlight("Hello World", "hello world") == '<mark class="p-0">Hello</mark> <mark class="p-0">World</mark>'
    assert (
        highlight("<b>Bold</b>", "b")
        == '&lt;<mark class="p-0">b</mark>&gt;<mark class="p-0">B</mark>old&lt;/<mark class="p-0">b</mark>&gt;'
    )


def test_relative_time_units():
    now = datetime.now(UTC)

    # Just now
    t1 = now.isoformat().replace("+00:00", "Z")
    assert relative_time(t1) == "just now"

    # Minutes
    t2 = (now - timedelta(minutes=5)).isoformat().replace("+00:00", "Z")
    assert "minute(s) ago" in relative_time(t2)

    # Hours
    t3 = (now - timedelta(hours=5)).isoformat().replace("+00:00", "Z")
    assert "hour(s) ago" in relative_time(t3)

    # Days
    t4 = (now - timedelta(days=5)).isoformat().replace("+00:00", "Z")
    assert "day(s) ago" in relative_time(t4)

    # Weeks
    t5 = (now - timedelta(days=15)).isoformat().replace("+00:00", "Z")
    assert "week(s) ago" in relative_time(t5)


def test_web_app_error_handling(temp_root):
    store = NiraStore(temp_root)
    store.initialize("NIRA")
    app = NiraWebApp(store)

    def start_response(status, headers):
        pass

    # TicketNotFoundError
    def mock_handler_not_found(**kwargs):
        raise TicketNotFoundError("Not found")

    app.router.add("GET", "/test-not-found", mock_handler_not_found)
    environ = {"REQUEST_METHOD": "GET", "PATH_INFO": "/test-not-found"}
    res = app(environ, start_response)
    assert b"404 Not Found" in b"".join(res)

    # ValidationError
    def mock_handler_validation(**kwargs):
        raise ValidationError("Invalid")

    app.router.add("GET", "/test-validation", mock_handler_validation)
    environ = {"REQUEST_METHOD": "GET", "PATH_INFO": "/test-validation"}
    res = app(environ, start_response)
    assert b"400 Bad Request" in b"".join(res)


def test_web_app_misc_coverage(temp_root):
    store = NiraStore(temp_root)
    store.initialize("NIRA")
    app = NiraWebApp(store)

    # Test list_page with various query params to trigger branches
    app.list_page({"status": "not_closed"}, {})
    app.list_page({"status": "closed"}, {})
    app.list_page({"sort": "priority", "direction": "asc"}, {})
    app.list_page({"sort": "status"}, {})
    app.list_page({"sort": "ticket_id"}, {})

    # Test board_page branches
    app.board_page({"label": "test"}, {})

    # Test ticket_detail_page with missing parent/related
    store.create_ticket("NIRA", "T1")
    app.ticket_detail_page({}, {}, "NIRA-1")

    # Test edit_ticket_action validation
    with pytest.raises(ValidationError):
        app.edit_ticket_action({}, {"title": ""}, "NIRA-1")


def test_web_action_errors(temp_root):
    store = NiraStore(temp_root)
    store.initialize("NIRA")
    app = NiraWebApp(store)

    store.create_ticket("NIRA", "T1")

    # edit_ticket_action status change
    app.edit_ticket_action({}, {"status": "closed", "resolution_reason": "done"}, "NIRA-1")

    # close_ticket_action
    app.close_ticket_action({}, {"resolution_reason": "done"}, "NIRA-1")

    # comment action empty
    with pytest.raises(ValidationError):
        app.add_comment_action({}, {"body_md": ""}, "NIRA-1")


def test_web_more_coverage(temp_root):
    store = NiraStore(temp_root)
    store.initialize("NIRA")
    app = NiraWebApp(store)

    # asset_response
    app.asset_response({}, {}, "nira.png")
    app.asset_response({}, {}, "")  # 404
    app.asset_response({}, {}, "missing.png")  # 404

    # ticket_search_dropdown with exclude
    store.create_ticket("NIRA", "T1")
    app.ticket_search_dropdown({"exclude": "NIRA-1"}, {})

    # create_ticket_action with parent
    app.create_ticket_action({}, {"title": "Child", "parent": "NIRA-1"})

    # edit_ticket_action various fields
    app.edit_ticket_action({}, {"due_date": "", "story_points": "5", "parent": "", "type": "bug"}, "NIRA-1")
    app.edit_ticket_action({}, {"story_points": ""}, "NIRA-1")
    app.edit_ticket_action({}, {"parent": "NIRA-2"}, "NIRA-1")

    # sort_header_link with filters
    app.sort_header_link("Label", "status", "updated", "desc", "open", search_query="q", label_filter="l")

    # ticket_type_options with custom
    app.ticket_type_options("custom")
