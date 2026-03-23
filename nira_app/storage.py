from __future__ import annotations

import re
import sqlite3
from pathlib import Path
from typing import Iterable, Final


STATUS_VALUES = {"open", "in_progress", "closed"}
LIST_SORT_VALUES = {"updated", "ticket_id", "priority", "status"}
LIST_DIRECTION_VALUES = {"asc", "desc"}


class _UnsetType:
    pass


UNSET: Final = _UnsetType()
TICKET_ID_RE = re.compile(r"^([A-Za-z][A-Za-z0-9_-]*)-(\d+)$")


class NiraError(Exception):
    """Base error for the application."""


class ValidationError(NiraError):
    """Raised when user input is invalid."""


class TicketNotFoundError(NiraError):
    """Raised when a ticket cannot be found."""


def normalize_list_sort(sort_by: str | None) -> str:
    candidate = (sort_by or "updated").strip().lower()
    return candidate if candidate in LIST_SORT_VALUES else "updated"


def normalize_list_direction(direction: str | None) -> str:
    candidate = (direction or "desc").strip().lower()
    return candidate if candidate in LIST_DIRECTION_VALUES else "desc"


def utc_now() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def normalize_project(project: str) -> str:
    project = (project or "").strip().upper()
    if not project:
        raise ValidationError("Project key is required.")
    if not re.fullmatch(r"[A-Z][A-Z0-9_-]*", project):
        raise ValidationError("Project keys must start with a letter and use only A-Z, 0-9, _ or -.")
    return project


def derive_default_project_key(folder_name: str) -> str:
    candidate = re.sub(r"[^A-Za-z0-9_-]+", "-", (folder_name or "").strip()).strip("-_")
    candidate = candidate.upper()
    if not candidate:
        candidate = "NIRA"
    if not re.match(r"^[A-Z]", candidate):
        candidate = f"N{candidate}"
    return normalize_project(candidate)


def normalize_ticket_id(ticket_id: str) -> str:
    ticket_id = (ticket_id or "").strip().upper()
    match = TICKET_ID_RE.fullmatch(ticket_id)
    if not match:
        raise ValidationError("Ticket IDs must look like EMH-1.")
    return f"{match.group(1).upper()}-{int(match.group(2))}"


def canonical_link_pair(ticket_a: str, ticket_b: str) -> tuple[str, str]:
    left = normalize_ticket_id(ticket_a)
    right = normalize_ticket_id(ticket_b)
    if left == right:
        raise ValidationError("A ticket cannot be related to itself.")
    if left < right:
        return left, right
    return right, left


def find_root(start: Path | None = None) -> Path | None:
    current = (start or Path.cwd()).resolve()
    for candidate in (current, *current.parents):
        if (candidate / ".nira").exists():
            return candidate
    return None


class NiraStore:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.state_dir = self.root / ".nira"
        self.db_path = self.state_dir / "nira.db"

    def initialize(self, default_project: str | None = None) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        if default_project:
            default_key = normalize_project(default_project)
        else:
            default_key = derive_default_project_key(self.root.name)
        with self.connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS projects (
                    key TEXT PRIMARY KEY,
                    next_number INTEGER NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS tickets (
                    id TEXT PRIMARY KEY,
                    project TEXT NOT NULL,
                    number INTEGER NOT NULL,
                    title TEXT NOT NULL,
                    status TEXT NOT NULL,
                    type TEXT NOT NULL,
                    priority TEXT NOT NULL,
                    source TEXT NOT NULL,
                    resolution_reason TEXT NOT NULL DEFAULT '',
                    body_md TEXT NOT NULL DEFAULT '',
                    resolution_md TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(project, number)
                );

                CREATE TABLE IF NOT EXISTS comments (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ticket_id TEXT NOT NULL REFERENCES tickets(id) ON DELETE CASCADE,
                    body_md TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS links (
                    ticket_a TEXT NOT NULL REFERENCES tickets(id) ON DELETE CASCADE,
                    ticket_b TEXT NOT NULL REFERENCES tickets(id) ON DELETE CASCADE,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(ticket_a, ticket_b),
                    CHECK(ticket_a < ticket_b)
                );
                """
            )
            connection.execute(
                """
                INSERT INTO settings (key, value) VALUES ('default_project', ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (default_key,),
            )

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        connection.execute("PRAGMA journal_mode = WAL")
        return connection

    def list_projects(self) -> list[str]:
        with self.connect() as connection:
            rows = connection.execute("SELECT key FROM projects ORDER BY key").fetchall()
        return [row["key"] for row in rows]

    def create_ticket(
        self,
        project: str,
        title: str,
        *,
        source: str = "",
        ticket_type: str = "task",
        priority: str = "medium",
        body_md: str = "",
        resolution_md: str = "",
    ) -> dict:
        project_key = normalize_project(project) if project else self.get_default_project()
        title = (title or "").strip()
        if not title:
            raise ValidationError("Title is required.")

        now = utc_now()
        with self.connect() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO projects (key, next_number, created_at) VALUES (?, ?, ?)",
                (project_key, 1, now),
            )
            next_row = connection.execute(
                "SELECT next_number FROM projects WHERE key = ?",
                (project_key,),
            ).fetchone()
            assert next_row is not None
            number = int(next_row["next_number"])
            ticket_id = f"{project_key}-{number}"
            connection.execute(
                """
                INSERT INTO tickets (
                    id, project, number, title, status, type, priority, source,
                    resolution_reason, body_md, resolution_md, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    ticket_id,
                    project_key,
                    number,
                    title,
                    "open",
                    (ticket_type or "task").strip() or "task",
                    (priority or "medium").strip() or "medium",
                    (source or "").strip(),
                    "",
                    body_md,
                    resolution_md,
                    now,
                    now,
                ),
            )
            connection.execute(
                "UPDATE projects SET next_number = ? WHERE key = ?",
                (number + 1, project_key),
            )
        return self.get_ticket(ticket_id)

    def get_default_project(self) -> str:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT value FROM settings WHERE key = 'default_project'"
            ).fetchone()
        if row is None:
            raise ValidationError("No default project key is configured. Run `nira init` again.")
        return normalize_project(row["value"])

    def get_ticket(self, ticket_id: str) -> dict:
        normalized_id = normalize_ticket_id(ticket_id)
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM tickets WHERE id = ?",
                (normalized_id,),
            ).fetchone()
        if row is None:
            raise TicketNotFoundError(f"Ticket {normalized_id} was not found.")
        return dict(row)

    def require_ticket(self, connection: sqlite3.Connection, ticket_id: str) -> sqlite3.Row:
        normalized_id = normalize_ticket_id(ticket_id)
        row = connection.execute(
            "SELECT * FROM tickets WHERE id = ?",
            (normalized_id,),
        ).fetchone()
        if row is None:
            raise TicketNotFoundError(f"Ticket {normalized_id} was not found.")
        return row

    def list_tickets(
        self,
        *,
        project: str | None = None,
        status: str | None = None,
        priority: str | None = None,
        ticket_type: str | None = None,
        sort_by: str | None = None,
        direction: str | None = None,
    ) -> list[dict]:
        clauses: list[str] = []
        values: list[str] = []
        if project:
            clauses.append("project = ?")
            values.append(normalize_project(project))
        if status:
            if status == "not_closed":
                clauses.append("status != ?")
                values.append("closed")
            else:
                clauses.append("status = ?")
                values.append(self.normalize_status(status))
        if priority:
            clauses.append("priority = ?")
            values.append(priority.strip())
        if ticket_type:
            clauses.append("type = ?")
            values.append(ticket_type.strip())

        query = "SELECT * FROM tickets"
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        sort_key = normalize_list_sort(sort_by)
        sort_direction = normalize_list_direction(direction).upper()

        if sort_key == "ticket_id":
            order_clause = f"project {sort_direction}, number {sort_direction}"
        elif sort_key == "priority":
            order_clause = (
                "CASE priority "
                "WHEN 'critical' THEN 4 "
                "WHEN 'high' THEN 3 "
                "WHEN 'medium' THEN 2 "
                "WHEN 'low' THEN 1 "
                f"ELSE 0 END {sort_direction}, updated_at DESC, project DESC, number DESC"
            )
        elif sort_key == "status":
            order_clause = (
                "CASE status "
                "WHEN 'open' THEN 1 "
                "WHEN 'in_progress' THEN 2 "
                "WHEN 'closed' THEN 3 "
                f"ELSE 4 END {sort_direction}, updated_at DESC, project DESC, number DESC"
            )
        else:
            order_clause = f"updated_at {sort_direction}, project DESC, number DESC"

        query += f" ORDER BY {order_clause}"

        with self.connect() as connection:
            rows = connection.execute(query, values).fetchall()
        return [dict(row) for row in rows]

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
        body_md: str | _UnsetType = UNSET,
        resolution_md: str | _UnsetType = UNSET,
    ) -> dict:
        assignments: list[str] = []
        values: list[object] = []

        if isinstance(title, str):
            clean_title = title.strip()
            if not clean_title:
                raise ValidationError("Title cannot be empty.")
            assignments.append("title = ?")
            values.append(clean_title)

        normalized_status: str | _UnsetType = UNSET
        if isinstance(status, str):
            normalized_status = self.normalize_status(status)
            assignments.append("status = ?")
            values.append(normalized_status)

        if isinstance(ticket_type, str):
            assignments.append("type = ?")
            values.append(ticket_type.strip())

        if isinstance(priority, str):
            assignments.append("priority = ?")
            values.append(priority.strip())

        if isinstance(source, str):
            assignments.append("source = ?")
            values.append(source.strip())

        normalized_reason: str | _UnsetType = UNSET
        if isinstance(resolution_reason, str):
            normalized_reason = resolution_reason.strip()

        if isinstance(body_md, str):
            assignments.append("body_md = ?")
            values.append(body_md)

        if isinstance(resolution_md, str):
            assignments.append("resolution_md = ?")
            values.append(resolution_md)

        if not assignments:
            raise ValidationError("No fields were provided to update.")
        normalized_ticket_id = normalize_ticket_id(ticket_id)
        with self.connect() as connection:
            existing_ticket = self.require_ticket(connection, normalized_ticket_id)
            final_reason = normalized_reason
            if normalized_status is not UNSET:
                if normalized_status == "closed" and (final_reason is UNSET or not final_reason):
                    final_reason = (existing_ticket["resolution_reason"] or "").strip() or "completed"
                elif existing_ticket["status"] == "closed" and final_reason is UNSET:
                    final_reason = ""

            if final_reason is not UNSET:
                assignments.append("resolution_reason = ?")
                values.append(final_reason)

            values.append(utc_now())
            values.append(normalized_ticket_id)
            connection.execute(
                f"UPDATE tickets SET {', '.join(assignments)}, updated_at = ? WHERE id = ?",
                values,
            )
        return self.get_ticket(ticket_id)

    def close_ticket(self, ticket_id: str, *, reason: str) -> dict:
        reason = (reason or "").strip()
        if not reason:
            raise ValidationError("Closing a ticket requires a resolution reason.")
        return self.update_ticket(ticket_id, status="closed", resolution_reason=reason)

    def reopen_ticket(self, ticket_id: str) -> dict:
        return self.update_ticket(ticket_id, status="open", resolution_reason="")

    def add_comment(self, ticket_id: str, body_md: str) -> dict:
        normalized_id = normalize_ticket_id(ticket_id)
        body_md = body_md.strip()
        if not body_md:
            raise ValidationError("Comment text is required.")
        now = utc_now()
        with self.connect() as connection:
            self.require_ticket(connection, normalized_id)
            cursor = connection.execute(
                "INSERT INTO comments (ticket_id, body_md, created_at) VALUES (?, ?, ?)",
                (normalized_id, body_md, now),
            )
            self.touch_tickets(connection, [normalized_id], now)
            assert cursor.lastrowid is not None
            comment_id = int(cursor.lastrowid)
            row = connection.execute(
                "SELECT * FROM comments WHERE id = ?",
                (comment_id,),
            ).fetchone()
        assert row is not None
        return dict(row)

    def list_comments(self, ticket_id: str) -> list[dict]:
        normalized_id = normalize_ticket_id(ticket_id)
        with self.connect() as connection:
            self.require_ticket(connection, normalized_id)
            rows = connection.execute(
                "SELECT * FROM comments WHERE ticket_id = ? ORDER BY created_at ASC, id ASC",
                (normalized_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def link_tickets(self, left_ticket: str, right_ticket: str) -> None:
        ticket_a, ticket_b = canonical_link_pair(left_ticket, right_ticket)
        now = utc_now()
        with self.connect() as connection:
            self.require_ticket(connection, ticket_a)
            self.require_ticket(connection, ticket_b)
            connection.execute(
                "INSERT OR IGNORE INTO links (ticket_a, ticket_b, created_at) VALUES (?, ?, ?)",
                (ticket_a, ticket_b, now),
            )
            self.touch_tickets(connection, [ticket_a, ticket_b], now)

    def unlink_tickets(self, left_ticket: str, right_ticket: str) -> None:
        ticket_a, ticket_b = canonical_link_pair(left_ticket, right_ticket)
        now = utc_now()
        with self.connect() as connection:
            self.require_ticket(connection, ticket_a)
            self.require_ticket(connection, ticket_b)
            connection.execute(
                "DELETE FROM links WHERE ticket_a = ? AND ticket_b = ?",
                (ticket_a, ticket_b),
            )
            self.touch_tickets(connection, [ticket_a, ticket_b], now)

    def list_related_tickets(self, ticket_id: str) -> list[dict]:
        normalized_id = normalize_ticket_id(ticket_id)
        with self.connect() as connection:
            self.require_ticket(connection, normalized_id)
            rows = connection.execute(
                """
                SELECT t.*
                FROM tickets t
                WHERE t.id IN (
                    SELECT ticket_b FROM links WHERE ticket_a = ?
                    UNION
                    SELECT ticket_a FROM links WHERE ticket_b = ?
                )
                ORDER BY t.id
                """,
                (normalized_id, normalized_id),
            ).fetchall()
        return [dict(row) for row in rows]

    def ticket_details(self, ticket_id: str) -> dict:
        ticket = self.get_ticket(ticket_id)
        return {
            "ticket": ticket,
            "comments": self.list_comments(ticket["id"]),
            "related": self.list_related_tickets(ticket["id"]),
        }

    def touch_tickets(
        self,
        connection: sqlite3.Connection,
        ticket_ids: Iterable[str],
        timestamp: str | None = None,
    ) -> None:
        stamp = timestamp or utc_now()
        normalized = [normalize_ticket_id(ticket_id) for ticket_id in ticket_ids]
        if not normalized:
            return
        connection.executemany(
            "UPDATE tickets SET updated_at = ? WHERE id = ?",
            [(stamp, ticket_id) for ticket_id in normalized],
        )

    def normalize_status(self, status: str) -> str:
        normalized = (status or "").strip().lower()
        if normalized not in STATUS_VALUES:
            raise ValidationError("Status must be one of: open, in_progress, closed.")
        return normalized
