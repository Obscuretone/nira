from nira_app.services import TicketService
from nira_app.storage import NiraStore


def test_parse_search_query(temp_root):
    store = NiraStore(temp_root)
    service = TicketService(store)

    # Test normal search
    clean, filters = service._parse_search_query("hello world")
    assert clean == "hello world"
    assert filters == {}

    # Test single filter
    clean, filters = service._parse_search_query("hello is:open world")
    assert clean == "hello world"
    assert filters == {"status": "open"}

    # Test multiple filters
    clean, filters = service._parse_search_query("priority:high type:bug fix this")
    assert clean == "fix this"
    assert filters == {"priority": "high", "ticket_type": "bug"}

    # Test labels and status
    clean, filters = service._parse_search_query("label:frontend is:in_progress")
    assert clean == ""
    assert filters == {"label": "frontend", "status": "in_progress"}

    # Test invalid filter key
    clean, filters = service._parse_search_query("unknown:value something")
    assert clean == "unknown:value something"
    assert filters == {}

    # Test mixed cases
    clean, filters = service._parse_search_query("IS:CLOSED PRIORITY:LOW test")
    assert clean == "test"
    assert filters == {"status": "closed", "priority": "low"}
