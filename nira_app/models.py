from __future__ import annotations

from typing import Dict, List, Optional, TypedDict

from sqlalchemy import CheckConstraint, ForeignKey, Index, Integer, MetaData, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

# --- SQLAlchemy Models ---

convention = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=convention)


class Setting(Base):
    __tablename__ = "settings"
    key: Mapped[str] = mapped_column(String, primary_key=True)
    value: Mapped[str] = mapped_column(String, nullable=False)


class Ticket(Base):
    __tablename__ = "tickets"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project: Mapped[str] = mapped_column(String, nullable=False)
    number: Mapped[int] = mapped_column(Integer, nullable=False, unique=True)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False)
    type: Mapped[str] = mapped_column(String, nullable=False)
    priority: Mapped[str] = mapped_column(String, nullable=False)
    source: Mapped[str] = mapped_column(String, nullable=False)
    resolution_reason: Mapped[str] = mapped_column(Text, nullable=False, default="")
    labels: Mapped[str] = mapped_column(String, nullable=False, default="")
    due_date: Mapped[str | None] = mapped_column(String, nullable=True)
    parent_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("tickets.id", ondelete="SET NULL"), nullable=True)
    story_points: Mapped[int | None] = mapped_column(Integer, nullable=True)
    body_md: Mapped[str] = mapped_column(Text, nullable=False, default="")
    resolution_md: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[str] = mapped_column(String, nullable=False)
    updated_at: Mapped[str] = mapped_column(String, nullable=False)

    comments: Mapped[list["Comment"]] = relationship("Comment", back_populates="ticket", cascade="all, delete-orphan")
    attachments: Mapped[list["Attachment"]] = relationship(
        "Attachment", back_populates="ticket", cascade="all, delete-orphan"
    )
    history: Mapped[list["History"]] = relationship("History", back_populates="ticket", cascade="all, delete-orphan")
    parent: Mapped[Optional["Ticket"]] = relationship("Ticket", remote_side=[id], back_populates="sub_tasks")
    sub_tasks: Mapped[list["Ticket"]] = relationship(
        "Ticket", back_populates="parent", cascade="all, delete-orphan", passive_deletes=True
    )


class Comment(Base):
    __tablename__ = "comments"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ticket_id: Mapped[int] = mapped_column(Integer, ForeignKey("tickets.id", ondelete="CASCADE"), nullable=False)
    body_md: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[str] = mapped_column(String, nullable=False)

    ticket: Mapped["Ticket"] = relationship("Ticket", back_populates="comments")
    __table_args__ = (Index("comments_ticket_id_idx", "ticket_id"),)


class Attachment(Base):
    __tablename__ = "attachments"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ticket_id: Mapped[int] = mapped_column(Integer, ForeignKey("tickets.id", ondelete="CASCADE"), nullable=False)
    filename: Mapped[str] = mapped_column(String, nullable=False)
    content_type: Mapped[str] = mapped_column(String, nullable=False)
    file_size: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[str] = mapped_column(String, nullable=False)

    ticket: Mapped["Ticket"] = relationship("Ticket", back_populates="attachments")
    __table_args__ = (Index("attachments_ticket_id_idx", "ticket_id"),)


class Link(Base):
    __tablename__ = "links"
    ticket_a_id: Mapped[int] = mapped_column(Integer, ForeignKey("tickets.id", ondelete="CASCADE"), primary_key=True)
    ticket_b_id: Mapped[int] = mapped_column(Integer, ForeignKey("tickets.id", ondelete="CASCADE"), primary_key=True)
    created_at: Mapped[str] = mapped_column(String, nullable=False)

    __table_args__ = (
        CheckConstraint("ticket_a_id < ticket_b_id", name="check_links_ordering"),
        Index("links_ticket_b_idx", "ticket_b_id"),
    )


class History(Base):
    __tablename__ = "history"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ticket_id: Mapped[int] = mapped_column(Integer, ForeignKey("tickets.id", ondelete="CASCADE"), nullable=False)
    field: Mapped[str] = mapped_column(String, nullable=False)
    old_value: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    new_value: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[str] = mapped_column(String, nullable=False)

    ticket: Mapped["Ticket"] = relationship("Ticket", back_populates="history")
    __table_args__ = (Index("history_ticket_id_idx", "ticket_id"),)


# --- Data Transfer Objects (TypedDicts) ---


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


class AttachmentData(TypedDict):
    id: int
    ticket_id: int
    filename: str
    content_type: str
    file_size: int
    created_at: str


class DashboardStats(TypedDict):
    status_counts: Dict[str, int]
    status_points: Dict[str, int]
    total_tickets: int
    total_points: int
    velocity: float
    common_labels: List[tuple[str, int]]
    recent_history: List[HistoryData]
    recent_tickets: List[TicketData]


class TicketDetails(TypedDict):
    ticket: TicketData
    parent: Optional[TicketData]
    related: List[TicketData]
    sub_tasks: List[TicketData]
    comments: List[CommentData]
    history: List[HistoryData]
    attachments: List[AttachmentData]
