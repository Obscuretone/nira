from typing import TypedDict, Optional, List, Dict


class HistoryData(TypedDict):
    ticket_id: str
    field: str
    old_value: Optional[str]
    new_value: Optional[str]
    created_at: str


class TicketData(TypedDict):
    db_id: int
    id: str
    project: str
    number: int
    title: str
    status: str
    type: str
    priority: str
    source: str
    resolution_reason: str
    labels: str
    due_date: Optional[str]
    parent_id: Optional[int]
    parent_number: Optional[int]
    story_points: Optional[int]
    body_md: str
    resolution_md: str
    created_at: str
    updated_at: str


class CommentData(TypedDict):
    id: int
    ticket_id: str
    body_md: str
    created_at: str


class DashboardStats(TypedDict):
    status_counts: Dict[str, int]
    status_points: Dict[str, int]
    total_tickets: int
    total_points: int
    recent_history: List[HistoryData]
    recent_tickets: List[TicketData]


class TicketDetails(TypedDict):
    ticket: TicketData
    parent: Optional[TicketData]
    related: List[TicketData]
    sub_tasks: List[TicketData]
    comments: List[CommentData]
    history: List[HistoryData]
