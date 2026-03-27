from __future__ import annotations

import time
from pathlib import Path
from typing import Any, cast

from nira_app.models import (
    DashboardStats,
    History,
    HistoryData,
    Ticket,
    TicketData,
    TicketDetails,
    Comment,
    CommentData,
    Link,
    Attachment,
    AttachmentData,
)
from nira_app.storage import (
    NiraStore,
    ValidationError,
    utc_now,
    normalize_project,
    format_ticket_id,
    normalize_list_sort,
    normalize_list_direction,
    TICKET_ID_RE,
    UNSET,
    _UnsetType,
)
from sqlalchemy import delete, func, or_, select, text, case


class TicketService:
    def __init__(self, store: NiraStore):
        self.store = store

    def _parse_search_query(self, query: str | None) -> tuple[str, dict[str, Any]]:
        if not query:
            return "", {}

        tokens = query.split()
        remaining_tokens = []
        filters: dict[str, Any] = {}

        for token in tokens:
            if ":" in token:
                parts = token.split(":", 1)
                key, value = parts[0].lower(), parts[1]
                if key == "is":
                    val_low = value.lower()
                    if val_low in ("open", "closed", "in_progress", "not_closed"):
                        filters["status"] = val_low
                elif key == "priority":
                    filters["priority"] = value.lower()
                elif key == "type":
                    filters["ticket_type"] = value.lower()
                elif key == "label":
                    filters["label"] = value
                else:
                    remaining_tokens.append(token)
            else:
                remaining_tokens.append(token)

        return " ".join(remaining_tokens).strip(), filters

    def create_ticket(
        self,
        project: str,
        title: str,
        *,
        source: str = "",
        ticket_type: str = "task",
        priority: str = "medium",
        labels: str = "",
        due_date: str | None = None,
        parent_id: int | None = None,
        story_points: int | None = None,
        body_md: str = "",
        resolution_md: str = "",
    ) -> TicketData:
        title = (title or "").strip()
        if not title:
            raise ValidationError("Title is required.")

        now = utc_now()
        with self.store.session() as session:
            current_project = self.store.current_project(session)
            if project:
                requested_project = normalize_project(project)
                if requested_project != current_project:
                    raise ValidationError(
                        f"This workspace uses the {current_project} ticket prefix. Change it in settings first."
                    )

            next_number = session.query(func.max(Ticket.number)).scalar() or 0
            number = next_number + 1

            initial_status = self.store.get_statuses()[0]

            ticket = Ticket(
                number=number,
                project=current_project,
                title=title,
                status=initial_status,
                type=(ticket_type or "task").strip() or "task",
                priority=(priority or "medium").strip() or "medium",
                source=(source or "").strip(),
                resolution_reason="",
                labels=(labels or "").strip(),
                due_date=due_date,
                parent_id=parent_id,
                story_points=story_points,
                body_md=body_md,
                resolution_md=resolution_md,
                created_at=now,
                updated_at=now,
            )
            session.add(ticket)
            session.flush()
            session.refresh(ticket)
            result = self.store.ticket_from_model(ticket, current_project)
        return cast(TicketData, result)

    def update_ticket(
        self,
        ticket_id: str,
        *,
        title: str | _UnsetType = UNSET,
        status: str | _UnsetType = UNSET,
        ticket_type: str | _UnsetType = UNSET,
        priority: str | _UnsetType = UNSET,
        source: str | _UnsetType = UNSET,
        resolution_reason: str | _UnsetType = UNSET,
        labels: str | _UnsetType = UNSET,
        due_date: str | None | _UnsetType = UNSET,
        parent_id: int | None | _UnsetType = UNSET,
        story_points: int | None | _UnsetType = UNSET,
        body_md: str | _UnsetType = UNSET,
        resolution_md: str | _UnsetType = UNSET,
    ) -> TicketData:
        updates: dict[str, Any] = {}

        if isinstance(title, str):
            clean_title = title.strip()
            if not clean_title:
                raise ValidationError("Title cannot be empty.")
            updates["title"] = clean_title

        normalized_status: str | _UnsetType = UNSET
        if isinstance(status, str):
            normalized_status = self.store.normalize_status(status)
            updates["status"] = normalized_status

        if isinstance(ticket_type, str):
            updates["type"] = ticket_type.strip()

        if isinstance(priority, str):
            updates["priority"] = priority.strip()

        if isinstance(source, str):
            updates["source"] = source.strip()

        if isinstance(labels, str):
            updates["labels"] = labels.strip()

        if due_date is not UNSET:
            updates["due_date"] = due_date

        if parent_id is not UNSET:
            updates["parent_id"] = parent_id

        if story_points is not UNSET:
            updates["story_points"] = story_points

        normalized_reason: str | _UnsetType = UNSET
        if isinstance(resolution_reason, str):
            normalized_reason = resolution_reason.strip()

        if isinstance(body_md, str):
            updates["body_md"] = body_md

        if isinstance(resolution_md, str):
            updates["resolution_md"] = resolution_md

        if not updates and normalized_reason is UNSET:
            raise ValidationError("No fields were provided to update.")

        with self.store.session() as session:
            current_project = self.store.current_project(session)
            ticket = self.store.resolve_ticket(session, ticket_id, project_key=current_project)

            final_reason = normalized_reason
            if normalized_status is not UNSET:
                if normalized_status == "closed" and (final_reason is UNSET or not final_reason):
                    final_reason = (ticket.resolution_reason or "").strip() or "completed"
                elif ticket.status == "closed" and final_reason is UNSET:
                    final_reason = ""

            if final_reason is not UNSET:
                updates["resolution_reason"] = final_reason

            now = utc_now()
            for key, value in updates.items():
                old_val = getattr(ticket, key)
                if old_val != value:
                    history_item = History(
                        ticket_id=ticket.id,
                        field=key,
                        old_value=str(old_val) if old_val is not None else None,
                        new_value=str(value) if value is not None else None,
                        created_at=now,
                    )
                    session.add(history_item)
                    setattr(ticket, key, value)

            ticket.updated_at = now
            session.flush()
            result = self.store.ticket_from_model(ticket, current_project)
        return cast(TicketData, result)

    def list_tickets(
        self,
        *,
        project: str | None = None,
        status: str | None = None,
        priority: str | None = None,
        ticket_type: str | None = None,
        sort_by: str | None = None,
        direction: str | None = None,
        offset: int = 0,
        limit: int | None = None,
        search: str | None = None,
        label: str | None = None,
        overdue: bool = False,
        parent_id: int | None = None,
    ) -> list[TicketData]:
        sort_key = normalize_list_sort(sort_by)
        sort_direction = normalize_list_direction(direction).lower()

        # Parse advanced search syntax
        clean_search, search_filters = self._parse_search_query(search)

        # Merge filters, search bar tokens take precedence
        final_status = search_filters.get("status", status)
        final_priority = search_filters.get("priority", priority)
        final_type = search_filters.get("ticket_type", ticket_type)
        final_label = search_filters.get("label", label)

        with self.store.session() as session:
            current_project = self.store.current_project(session)
            if project and normalize_project(project) != current_project:
                return []

            stmt = select(Ticket)

            if clean_search:
                search_term = clean_search.strip()
                search_number = None
                id_match = TICKET_ID_RE.fullmatch(search_term.upper())
                if id_match:
                    search_number = int(id_match.group(2))
                elif search_term.isdigit():
                    search_number = int(search_term)

                fts_query = search_term.replace('"', '""')
                if any(c in fts_query for c in "()+-*:"):
                    fts_query = f'"{fts_query}"'

                if search_number is not None:
                    stmt = stmt.where(
                        or_(
                            Ticket.number == search_number,
                            text("tickets.id IN (SELECT rowid FROM tickets_search WHERE tickets_search MATCH :query)"),
                        )
                    ).params(query=fts_query)
                else:
                    stmt = stmt.where(
                        text("tickets.id IN (SELECT rowid FROM tickets_search WHERE tickets_search MATCH :query)")
                    ).params(query=fts_query)

            if final_label:
                stmt = stmt.where(Ticket.labels.contains(final_label.strip()))

            if overdue:
                today = utc_now()[:10]  # YYYY-MM-DD
                stmt = stmt.where(Ticket.due_date < today).where(Ticket.status != "closed")

            if parent_id:
                stmt = stmt.where(Ticket.parent_id == parent_id)

            if final_status:
                if final_status == "not_closed":
                    statuses = self.store.get_statuses()
                    closed_status = statuses[-1] if statuses else "closed"
                    stmt = stmt.where(Ticket.status != closed_status)
                else:
                    try:
                        stmt = stmt.where(Ticket.status == self.store.normalize_status(final_status))
                    except ValidationError:
                        # Silently ignore invalid status tokens in search
                        pass

            if final_priority:
                stmt = stmt.where(Ticket.priority == final_priority.strip())
            if final_type:
                stmt = stmt.where(Ticket.type == final_type.strip())

            order_col: Any
            if sort_key == "ticket_id":
                order_col = Ticket.number
            elif sort_key == "priority":
                order_col = case(
                    (Ticket.priority == "critical", 4),
                    (Ticket.priority == "high", 3),
                    (Ticket.priority == "medium", 2),
                    (Ticket.priority == "low", 1),
                    else_=0,
                )
            elif sort_key == "status":
                order_col = case(
                    (Ticket.status == "open", 1),
                    (Ticket.status == "in_progress", 2),
                    (Ticket.status == "closed", 3),
                    else_=4,
                )
            else:
                order_col = Ticket.updated_at

            if sort_direction == "desc":
                stmt = stmt.order_by(order_col.desc(), Ticket.number.desc())
            else:
                stmt = stmt.order_by(order_col.asc(), Ticket.number.desc())

            if limit is not None:
                stmt = stmt.limit(limit)
            if offset > 0:
                stmt = stmt.offset(offset)

            tickets = session.execute(stmt).scalars().all()
            return [cast(TicketData, self.store.ticket_from_model(ticket, current_project)) for ticket in tickets]

    def count_tickets(
        self,
        *,
        project: str | None = None,
        status: str | None = None,
        priority: str | None = None,
        ticket_type: str | None = None,
        search: str | None = None,
        label: str | None = None,
        overdue: bool = False,
        parent_id: int | None = None,
    ) -> int:
        # Parse advanced search syntax
        clean_search, search_filters = self._parse_search_query(search)

        # Merge filters, search bar tokens take precedence
        final_status = search_filters.get("status", status)
        final_priority = search_filters.get("priority", priority)
        final_type = search_filters.get("ticket_type", ticket_type)
        final_label = search_filters.get("label", label)

        with self.store.session() as session:
            current_project = self.store.current_project(session)
            if project and normalize_project(project) != current_project:
                return 0

            stmt = select(func.count(Ticket.id))

            if clean_search:
                search_term = clean_search.strip()
                search_number = None
                id_match = TICKET_ID_RE.fullmatch(search_term.upper())
                if id_match:
                    search_number = int(id_match.group(2))
                elif search_term.isdigit():
                    search_number = int(search_term)

                fts_query = search_term.replace('"', '""')
                if any(c in fts_query for c in "()+-*:"):
                    fts_query = f'"{fts_query}"'

                if search_number is not None:
                    stmt = stmt.where(
                        or_(
                            Ticket.number == search_number,
                            text("tickets.id IN (SELECT rowid FROM tickets_search WHERE tickets_search MATCH :query)"),
                        )
                    ).params(query=fts_query)
                else:
                    stmt = stmt.where(
                        text("tickets.id IN (SELECT rowid FROM tickets_search WHERE tickets_search MATCH :query)")
                    ).params(query=fts_query)

            if final_label:
                stmt = stmt.where(Ticket.labels.contains(final_label.strip()))

            if overdue:
                today = utc_now()[:10]  # YYYY-MM-DD
                stmt = stmt.where(Ticket.due_date < today).where(Ticket.status != "closed")

            if parent_id:
                stmt = stmt.where(Ticket.parent_id == parent_id)

            if final_status:
                if final_status == "not_closed":
                    statuses = self.store.get_statuses()
                    closed_status = statuses[-1] if statuses else "closed"
                    stmt = stmt.where(Ticket.status != closed_status)
                else:
                    try:
                        stmt = stmt.where(Ticket.status == self.store.normalize_status(final_status))
                    except ValidationError:
                        pass

            if final_priority:
                stmt = stmt.where(Ticket.priority == final_priority.strip())
            if final_type:
                stmt = stmt.where(Ticket.type == final_type.strip())

            return session.execute(stmt).scalar() or 0

    def close_ticket(self, ticket_id: str, *, resolution_md: str) -> TicketData:
        resolution_md = (resolution_md or "").strip()
        if not resolution_md:
            raise ValidationError("Closing a ticket requires resolution notes.")
        statuses = self.store.get_statuses()
        closed_status = statuses[-1] if statuses else "closed"
        return self.update_ticket(
            ticket_id, status=closed_status, resolution_reason="completed", resolution_md=resolution_md
        )

    def reopen_ticket(self, ticket_id: str) -> TicketData:
        statuses = self.store.get_statuses()
        open_status = statuses[0] if statuses else "open"
        return self.update_ticket(ticket_id, status=open_status, resolution_reason="")

    def delete_ticket(self, ticket_id: str) -> None:
        with self.store.session() as session:
            current_project = self.store.current_project(session)
            ticket = self.store.resolve_ticket(session, ticket_id, project_key=current_project)

            # Also clean up physical attachments if we delete the ticket
            import shutil

            attach_dir = self.store.state_dir / "attachments" / str(ticket.id)
            if attach_dir.exists():
                shutil.rmtree(attach_dir, ignore_errors=True)

            session.delete(ticket)

    def add_attachment(
        self, ticket_id: str, filename: str, content: bytes, content_type: str = "application/octet-stream"
    ) -> AttachmentData:
        """Add a new attachment to a ticket."""
        import os

        if not filename:
            raise ValidationError("Filename is required.")

        with self.store.session() as session:
            project_key = self.store.current_project(session)
            ticket = self.store.resolve_ticket(session, ticket_id, project_key=project_key)
            ticket_db_id = int(ticket.id)

            # Create attachments directory for this ticket
            attach_dir = self.store.state_dir / "attachments" / str(ticket_db_id)
            attach_dir.mkdir(parents=True, exist_ok=True)

            # Prevent directory traversal and handle duplicates
            safe_filename = os.path.basename(filename)
            file_path = attach_dir / safe_filename

            # If file exists, append a timestamp
            if file_path.exists():
                name, ext = os.path.splitext(safe_filename)
                safe_filename = f"{name}_{int(time.time())}{ext}"
                file_path = attach_dir / safe_filename

            # Write physical file
            file_path.write_bytes(content)

            # Insert metadata
            now = utc_now()
            attachment = Attachment(
                ticket_id=ticket_db_id,
                filename=safe_filename,
                content_type=content_type,
                file_size=len(content),
                created_at=now,
            )
            session.add(attachment)

            # Add history entry for the attachment
            history = History(
                ticket_id=ticket_db_id,
                field="attachment",
                old_value="",
                new_value=f"Added {safe_filename}",
                created_at=now,
            )
            session.add(history)

            ticket.updated_at = now
            session.commit()

            return cast(
                AttachmentData,
                {
                    "id": int(attachment.id),
                    "ticket_id": ticket_db_id,
                    "filename": safe_filename,
                    "content_type": content_type,
                    "file_size": len(content),
                    "created_at": now,
                },
            )

    def get_attachment_path(self, ticket_id: str, filename: str) -> Path:
        """Get the physical path to an attachment."""
        import os

        with self.store.session() as session:
            project_key = self.store.current_project(session)
            ticket = self.store.resolve_ticket(session, ticket_id, project_key=project_key)

            safe_filename = os.path.basename(filename)
            file_path = self.store.state_dir / "attachments" / str(ticket.id) / safe_filename

            if not file_path.exists():
                raise ValidationError(f"Attachment {safe_filename} not found.")

            return file_path

    def add_comment(self, ticket_id: str, body_md: str) -> CommentData:
        body_md = body_md.strip()
        if not body_md:
            raise ValidationError("Comment text is required.")
        now = utc_now()
        with self.store.session() as session:
            current_project = self.store.current_project(session)
            ticket = self.store.resolve_ticket(session, ticket_id, project_key=current_project)
            comment = Comment(
                ticket_id=ticket.id,
                body_md=body_md,
                created_at=now,
            )
            session.add(comment)
            ticket.updated_at = now
            session.flush()
            session.refresh(comment)
            result = {
                "id": int(comment.id),
                "ticket_id": format_ticket_id(current_project, int(ticket.number)),
                "body_md": comment.body_md,
                "created_at": comment.created_at,
            }
        return cast(CommentData, result)

    def link_tickets(self, left_ticket: str, right_ticket: str) -> None:
        now = utc_now()
        with self.store.session() as session:
            current_project = self.store.current_project(session)
            left_ticket_model = self.store.resolve_ticket(session, left_ticket, project_key=current_project)
            right_ticket_model = self.store.resolve_ticket(session, right_ticket, project_key=current_project)

            if left_ticket_model.id == right_ticket_model.id:
                raise ValidationError("A ticket cannot be related to itself.")

            ticket_a_id, ticket_b_id = sorted((left_ticket_model.id, right_ticket_model.id))
            stmt = select(Link).where(Link.ticket_a_id == ticket_a_id, Link.ticket_b_id == ticket_b_id)
            existing = session.execute(stmt).scalar_one_or_none()
            if not existing:
                link = Link(ticket_a_id=ticket_a_id, ticket_b_id=ticket_b_id, created_at=now)
                session.add(link)

            left_ticket_model.updated_at = now
            right_ticket_model.updated_at = now

    def unlink_tickets(self, left_ticket: str, right_ticket: str) -> None:
        now = utc_now()
        with self.store.session() as session:
            current_project = self.store.current_project(session)
            left_ticket_model = self.store.resolve_ticket(session, left_ticket, project_key=current_project)
            right_ticket_model = self.store.resolve_ticket(session, right_ticket, project_key=current_project)

            if left_ticket_model.id == right_ticket_model.id:
                raise ValidationError("A ticket cannot be related to itself.")

            ticket_a_id, ticket_b_id = sorted((left_ticket_model.id, right_ticket_model.id))
            stmt = delete(Link).where(Link.ticket_a_id == ticket_a_id, Link.ticket_b_id == ticket_b_id)
            session.execute(stmt)

            left_ticket_model.updated_at = now
            right_ticket_model.updated_at = now

    def list_links(self, ticket_id: str | None = None) -> list[dict]:
        with self.store.session() as session:
            current_project = self.store.current_project(session)
            stmt = select(
                Link.ticket_a_id,
                Link.ticket_b_id,
            )
            if ticket_id is not None:
                ticket_model = self.store.resolve_ticket(session, ticket_id, project_key=current_project)
                stmt = stmt.where((Link.ticket_a_id == ticket_model.id) | (Link.ticket_b_id == ticket_model.id))

            rows = session.execute(stmt).all()

            results = []
            for ticket_a_id, ticket_b_id in rows:
                ticket_a = session.get(Ticket, ticket_a_id)
                ticket_b = session.get(Ticket, ticket_b_id)
                if ticket_a and ticket_b:
                    results.append(
                        {
                            "ticket_a": format_ticket_id(current_project, ticket_a.number),
                            "ticket_b": format_ticket_id(current_project, ticket_b.number),
                            "ticket_a_title": ticket_a.title,
                            "ticket_b_title": ticket_b.title,
                        }
                    )

            # Sort results manually as per previous behavior
            results.sort(key=lambda x: (x["ticket_a"], x["ticket_b"]))
            return results

    def import_tickets(self, tickets_data: list[dict[str, Any]]) -> int:
        count = 0
        with self.store.session() as session:
            current_project = self.store.current_project(session)
            for data in tickets_data:
                # We identify existing tickets by number
                number = int(data["number"])
                stmt = select(Ticket).where(Ticket.number == number)
                existing = session.execute(stmt).scalar_one_or_none()

                if existing:
                    # Update existing? For now, let's just update to keep it simple and useful
                    existing.title = data.get("title", existing.title)
                    existing.status = data.get("status", existing.status)
                    existing.type = data.get("type", existing.type) or data.get("ticket_type", existing.type)
                    existing.priority = data.get("priority", existing.priority)
                    existing.source = data.get("source", existing.source)
                    existing.labels = data.get("labels", existing.labels)
                    existing.due_date = data.get("due_date", existing.due_date)
                    existing.story_points = (
                        int(data["story_points"])
                        if data.get("story_points") not in (None, "")
                        else existing.story_points
                    )
                    existing.body_md = data.get("body_md", existing.body_md)
                    existing.resolution_md = data.get("resolution_md", existing.resolution_md)
                    existing.resolution_reason = data.get("resolution_reason", existing.resolution_reason)
                    existing.created_at = data.get("created_at", existing.created_at)
                    existing.updated_at = data.get("updated_at", existing.updated_at)
                else:
                    ticket = Ticket(
                        number=number,
                        project=current_project,
                        title=data["title"],
                        status=data.get("status", "open"),
                        type=data.get("type") or data.get("ticket_type") or "task",
                        priority=data.get("priority", "medium"),
                        source=data.get("source", ""),
                        labels=data.get("labels", ""),
                        due_date=data.get("due_date"),
                        story_points=int(data["story_points"]) if data.get("story_points") not in (None, "") else None,
                        body_md=data.get("body_md", ""),
                        resolution_md=data.get("resolution_md", ""),
                        resolution_reason=data.get("resolution_reason", ""),
                        created_at=data.get("created_at") or utc_now(),
                        updated_at=data.get("updated_at") or utc_now(),
                    )
                    session.add(ticket)
                count += 1
            session.commit()
        return count

    def ticket_details(self, ticket_id: str) -> TicketDetails:
        with self.store.session() as session:
            project_key = self.store.current_project(session)
            ticket_model = self.store.resolve_ticket(session, ticket_id, project_key=project_key)
            ticket_data = cast(TicketData, self.store.ticket_from_model(ticket_model, project_key))

            parent = None
            if ticket_model.parent_id:
                parent_model = session.get(Ticket, ticket_model.parent_id)
                if parent_model:
                    parent = cast(TicketData, self.store.ticket_from_model(parent_model, project_key))

            # Comments
            stmt_comments = (
                select(Comment)
                .where(Comment.ticket_id == ticket_model.id)
                .order_by(Comment.created_at.asc(), Comment.id.asc())
            )
            comments = session.execute(stmt_comments).scalars().all()
            comments_data = [
                cast(
                    CommentData,
                    {
                        "id": int(c.id),
                        "ticket_id": ticket_data["id"],
                        "body_md": c.body_md,
                        "created_at": c.created_at,
                    },
                )
                for c in comments
            ]

            # History
            stmt_history = (
                select(History).where(History.ticket_id == ticket_model.id).order_by(History.created_at.desc())
            )
            history = session.execute(stmt_history).scalars().all()
            history_data = [
                cast(
                    HistoryData,
                    {
                        "ticket_id": ticket_data["id"],
                        "field": h.field,
                        "old_value": h.old_value,
                        "new_value": h.new_value,
                        "created_at": h.created_at,
                    },
                )
                for h in history
            ]

            # Related
            stmt_a = select(Link.ticket_b_id).where(Link.ticket_a_id == ticket_model.id)
            stmt_b = select(Link.ticket_a_id).where(Link.ticket_b_id == ticket_model.id)
            related_ids = set(session.execute(stmt_a).scalars().all()) | set(session.execute(stmt_b).scalars().all())
            related_data = []
            if related_ids:
                stmt_rel = select(Ticket).where(Ticket.id.in_(related_ids)).order_by(Ticket.number)
                related_models = session.execute(stmt_rel).scalars().all()
                related_data = [
                    cast(TicketData, self.store.ticket_from_model(rm, project_key)) for rm in related_models
                ]

            # Sub-tasks
            stmt_sub = select(Ticket).where(Ticket.parent_id == ticket_model.id).order_by(Ticket.number)
            sub_models = session.execute(stmt_sub).scalars().all()
            sub_tasks_data = [cast(TicketData, self.store.ticket_from_model(sm, project_key)) for sm in sub_models]

            # Attachments
            stmt_attachments = (
                select(Attachment).where(Attachment.ticket_id == ticket_model.id).order_by(Attachment.created_at.asc())
            )
            attachments = session.execute(stmt_attachments).scalars().all()
            attachments_data = [
                cast(
                    AttachmentData,
                    {
                        "id": int(a.id),
                        "ticket_id": int(a.ticket_id),
                        "filename": a.filename,
                        "content_type": a.content_type,
                        "file_size": int(a.file_size),
                        "created_at": a.created_at,
                    },
                )
                for a in attachments
            ]

            return cast(
                TicketDetails,
                {
                    "ticket": ticket_data,
                    "parent": parent,
                    "comments": comments_data,
                    "history": history_data,
                    "related": related_data,
                    "sub_tasks": sub_tasks_data,
                    "attachments": attachments_data,
                },
            )

    def get_dashboard_stats(self) -> DashboardStats:
        statuses = self.store.get_statuses()
        with self.store.session() as session:
            project_key = self.store.current_project(session)
            # Count by status
            status_counts = {}
            for status in statuses:
                status_counts[status] = (
                    session.query(func.count(Ticket.id)).filter(Ticket.status == status).scalar() or 0
                )

            # Points by status
            status_points = {}
            for status in statuses:
                status_points[status] = (
                    session.query(func.sum(Ticket.story_points)).filter(Ticket.status == status).scalar() or 0
                )

            # Total tickets and points
            total_tickets = session.query(func.count(Ticket.id)).scalar() or 0
            total_points = session.query(func.sum(Ticket.story_points)).scalar() or 0

            # Recent activity (history)
            recent_history = session.query(History).order_by(History.created_at.desc()).limit(10).all()
            history_list: list[HistoryData] = [
                cast(
                    HistoryData,
                    {
                        "ticket_id": format_ticket_id(project_key, item.ticket.number),
                        "field": item.field,
                        "old_value": item.old_value,
                        "new_value": item.new_value,
                        "created_at": item.created_at,
                    },
                )
                for item in recent_history
            ]

            return cast(
                DashboardStats,
                {
                    "status_counts": status_counts,
                    "status_points": status_points,
                    "total_tickets": total_tickets,
                    "total_points": total_points,
                    "recent_history": history_list,
                    "recent_tickets": self.list_tickets(sort_by="updated", direction="desc", limit=5),
                },
            )
