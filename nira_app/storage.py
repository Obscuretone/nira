from __future__ import annotations

import re
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Final, Iterable, Iterator


STATUS_VALUES = {"open", "in_progress", "closed"}
LIST_SORT_VALUES = {"updated", "ticket_id", "priority", "status"}
LIST_DIRECTION_VALUES = {"asc", "desc"}
PROJECT_WORD_RE = re.compile(r"[A-Z]+(?=[A-Z][a-z]|\d|$)|[A-Z]?[a-z]+|\d+")
TICKET_ID_RE = re.compile(r"^([A-Za-z][A-Za-z0-9_-]*)-(\d+)$")
SCHEMA_VERSION = 2
DEFAULT_PROJECT_SETTING = "default_project"


class _UnsetType:
    pass


UNSET: Final = _UnsetType()


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
    raw_name = (folder_name or "").strip()
    words: list[str] = []
    for token in re.split(r"[^A-Za-z0-9]+", raw_name):
        if not token:
            continue
        words.extend(PROJECT_WORD_RE.findall(token))

    if not words:
        candidate = ""
    elif len(words) == 1:
        candidate = words[0].upper()
    else:
        candidate = "".join(word[0].upper() for word in words if word)

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


def ticket_number_from_ref(ticket_id: str) -> int:
    normalized = normalize_ticket_id(ticket_id)
    return int(normalized.rsplit("-", 1)[1])


def format_ticket_id(project_key: str, number: int) -> str:
    return f"{normalize_project(project_key)}-{int(number)}"


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
        self.ensure_schema(default_project=default_project)

    def ensure_schema(self, default_project: str | None = None) -> None:
        default_key = normalize_project(default_project) if default_project else derive_default_project_key(
            self.root.name
        )
        with self.connect() as connection:
            if not self.table_exists(connection, "settings"):
                self.create_schema(connection)
            elif self.needs_legacy_migration(connection):
                self.migrate_legacy_schema(connection)
            else:
                self.create_schema(connection)

            self.ensure_default_project(
                connection,
                default_key,
                force=default_project is not None,
            )
            if self.table_exists(connection, "projects"):
                connection.execute("DROP TABLE projects")
            connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.db_path)
        try:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA busy_timeout = 5000")
            connection.execute("PRAGMA journal_mode = WAL")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def table_exists(self, connection: sqlite3.Connection, table_name: str) -> bool:
        row = connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = ?",
            (table_name,),
        ).fetchone()
        return row is not None

    def column_names(self, connection: sqlite3.Connection, table_name: str) -> set[str]:
        if not self.table_exists(connection, table_name):
            return set()
        rows = connection.execute(f"PRAGMA table_info({table_name})").fetchall()
        return {str(row["name"]) for row in rows}

    def needs_legacy_migration(self, connection: sqlite3.Connection) -> bool:
        if not self.table_exists(connection, "tickets"):
            return False
        ticket_columns = self.column_names(connection, "tickets")
        if "project" in ticket_columns:
            return True
        id_info = connection.execute("PRAGMA table_info(tickets)").fetchall()
        id_type = next((str(row["type"]).upper() for row in id_info if row["name"] == "id"), "")
        if id_type != "INTEGER":
            return True
        link_columns = self.column_names(connection, "links")
        return "ticket_a" in link_columns or "ticket_b" in link_columns

    def create_schema(self, connection: sqlite3.Connection) -> None:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS tickets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                number INTEGER NOT NULL UNIQUE,
                title TEXT NOT NULL,
                status TEXT NOT NULL,
                type TEXT NOT NULL,
                priority TEXT NOT NULL,
                source TEXT NOT NULL,
                resolution_reason TEXT NOT NULL DEFAULT '',
                body_md TEXT NOT NULL DEFAULT '',
                resolution_md TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS comments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ticket_id INTEGER NOT NULL REFERENCES tickets(id) ON DELETE CASCADE,
                body_md TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS links (
                ticket_a_id INTEGER NOT NULL REFERENCES tickets(id) ON DELETE CASCADE,
                ticket_b_id INTEGER NOT NULL REFERENCES tickets(id) ON DELETE CASCADE,
                created_at TEXT NOT NULL,
                PRIMARY KEY(ticket_a_id, ticket_b_id),
                CHECK(ticket_a_id < ticket_b_id)
            );

            CREATE INDEX IF NOT EXISTS comments_ticket_id_idx ON comments(ticket_id);
            CREATE INDEX IF NOT EXISTS links_ticket_b_idx ON links(ticket_b_id);
            """
        )

    def ensure_default_project(
        self,
        connection: sqlite3.Connection,
        default_key: str,
        *,
        force: bool,
    ) -> None:
        row = connection.execute(
            "SELECT value FROM settings WHERE key = ?",
            (DEFAULT_PROJECT_SETTING,),
        ).fetchone()
        if row is None:
            connection.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?)",
                (DEFAULT_PROJECT_SETTING, default_key),
            )
            return
        if force:
            connection.execute(
                "UPDATE settings SET value = ? WHERE key = ?",
                (default_key, DEFAULT_PROJECT_SETTING),
            )

    def migrate_legacy_schema(self, connection: sqlite3.Connection) -> None:
        if not self.table_exists(connection, "tickets"):
            return

        old_tickets = connection.execute(
            """
            SELECT id, project, number, title, status, type, priority, source,
                   resolution_reason, body_md, resolution_md, created_at, updated_at
            FROM tickets
            ORDER BY number ASC
            """
        ).fetchall()

        legacy_projects = {
            normalize_project(row["project"])
            for row in old_tickets
            if row["project"]
        }
        if len(legacy_projects) > 1:
            raise ValidationError(
                "This workspace has multiple legacy ticket prefixes and cannot be auto-migrated."
            )

        connection.executescript(
            """
            DROP TABLE IF EXISTS tickets_new;
            DROP TABLE IF EXISTS comments_new;
            DROP TABLE IF EXISTS links_new;

            CREATE TABLE tickets_new (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                number INTEGER NOT NULL UNIQUE,
                title TEXT NOT NULL,
                status TEXT NOT NULL,
                type TEXT NOT NULL,
                priority TEXT NOT NULL,
                source TEXT NOT NULL,
                resolution_reason TEXT NOT NULL DEFAULT '',
                body_md TEXT NOT NULL DEFAULT '',
                resolution_md TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE comments_new (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ticket_id INTEGER NOT NULL REFERENCES tickets_new(id) ON DELETE CASCADE,
                body_md TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE links_new (
                ticket_a_id INTEGER NOT NULL REFERENCES tickets_new(id) ON DELETE CASCADE,
                ticket_b_id INTEGER NOT NULL REFERENCES tickets_new(id) ON DELETE CASCADE,
                created_at TEXT NOT NULL,
                PRIMARY KEY(ticket_a_id, ticket_b_id),
                CHECK(ticket_a_id < ticket_b_id)
            );
            """
        )

        legacy_id_to_db_id: dict[str, int] = {}
        for row in old_tickets:
            cursor = connection.execute(
                """
                INSERT INTO tickets_new (
                    number, title, status, type, priority, source, resolution_reason,
                    body_md, resolution_md, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    int(row["number"]),
                    row["title"],
                    row["status"],
                    row["type"],
                    row["priority"],
                    row["source"],
                    row["resolution_reason"],
                    row["body_md"],
                    row["resolution_md"],
                    row["created_at"],
                    row["updated_at"],
                ),
            )
            assert cursor.lastrowid is not None
            legacy_id_to_db_id[str(row["id"])] = int(cursor.lastrowid)

        if self.table_exists(connection, "comments"):
            old_comments = connection.execute(
                "SELECT id, ticket_id, body_md, created_at FROM comments ORDER BY id ASC"
            ).fetchall()
            for row in old_comments:
                ticket_db_id = legacy_id_to_db_id.get(str(row["ticket_id"]))
                if ticket_db_id is None:
                    continue
                connection.execute(
                    """
                    INSERT INTO comments_new (id, ticket_id, body_md, created_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (row["id"], ticket_db_id, row["body_md"], row["created_at"]),
                )

        if self.table_exists(connection, "links"):
            old_links = connection.execute(
                "SELECT ticket_a, ticket_b, created_at FROM links ORDER BY ticket_a, ticket_b"
            ).fetchall()
            for row in old_links:
                left_db_id = legacy_id_to_db_id.get(str(row["ticket_a"]))
                right_db_id = legacy_id_to_db_id.get(str(row["ticket_b"]))
                if left_db_id is None or right_db_id is None or left_db_id == right_db_id:
                    continue
                ticket_a_id, ticket_b_id = sorted((left_db_id, right_db_id))
                connection.execute(
                    """
                    INSERT OR IGNORE INTO links_new (ticket_a_id, ticket_b_id, created_at)
                    VALUES (?, ?, ?)
                    """,
                    (ticket_a_id, ticket_b_id, row["created_at"]),
                )

        connection.execute("PRAGMA foreign_keys = OFF")
        try:
            for table_name in ("links", "comments", "tickets", "projects"):
                if self.table_exists(connection, table_name):
                    connection.execute(f"DROP TABLE {table_name}")

            connection.execute("ALTER TABLE tickets_new RENAME TO tickets")
            connection.execute("ALTER TABLE comments_new RENAME TO comments")
            connection.execute("ALTER TABLE links_new RENAME TO links")
        finally:
            connection.execute("PRAGMA foreign_keys = ON")

        connection.execute("CREATE INDEX IF NOT EXISTS comments_ticket_id_idx ON comments(ticket_id)")
        connection.execute("CREATE INDEX IF NOT EXISTS links_ticket_b_idx ON links(ticket_b_id)")

    def current_project(self, connection: sqlite3.Connection) -> str:
        row = connection.execute(
            "SELECT value FROM settings WHERE key = ?",
            (DEFAULT_PROJECT_SETTING,),
        ).fetchone()
        if row is None:
            raise ValidationError("No default project key is configured. Run `nira init` again.")
        return normalize_project(row["value"])

    def ticket_from_row(self, row: sqlite3.Row, project_key: str) -> dict:
        return {
            "db_id": int(row["id"]),
            "id": format_ticket_id(project_key, int(row["number"])),
            "project": project_key,
            "number": int(row["number"]),
            "title": row["title"],
            "status": row["status"],
            "type": row["type"],
            "priority": row["priority"],
            "source": row["source"],
            "resolution_reason": row["resolution_reason"],
            "body_md": row["body_md"],
            "resolution_md": row["resolution_md"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def resolve_ticket_row(
        self,
        connection: sqlite3.Connection,
        ticket_ref: str,
        *,
        project_key: str | None = None,
    ) -> sqlite3.Row:
        number = ticket_number_from_ref(ticket_ref)
        row = connection.execute(
            "SELECT * FROM tickets WHERE number = ?",
            (number,),
        ).fetchone()
        if row is None:
            current_project = project_key or self.current_project(connection)
            raise TicketNotFoundError(f"Ticket {format_ticket_id(current_project, number)} was not found.")
        return row

    def list_projects(self) -> list[str]:
        return [self.get_default_project()]

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
        title = (title or "").strip()
        if not title:
            raise ValidationError("Title is required.")

        now = utc_now()
        with self.connect() as connection:
            current_project = self.current_project(connection)
            if project:
                requested_project = normalize_project(project)
                if requested_project != current_project:
                    raise ValidationError(
                        f"This workspace uses the {current_project} ticket prefix. Change it in settings first."
                    )

            next_number_row = connection.execute(
                "SELECT COALESCE(MAX(number), 0) + 1 AS next_number FROM tickets"
            ).fetchone()
            assert next_number_row is not None
            number = int(next_number_row["next_number"])
            cursor = connection.execute(
                """
                INSERT INTO tickets (
                    number, title, status, type, priority, source,
                    resolution_reason, body_md, resolution_md, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
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
            assert cursor.lastrowid is not None
            row = connection.execute(
                "SELECT * FROM tickets WHERE id = ?",
                (int(cursor.lastrowid),),
            ).fetchone()
        assert row is not None
        return self.ticket_from_row(row, current_project)

    def get_default_project(self) -> str:
        with self.connect() as connection:
            return self.current_project(connection)

    def get_settings(self) -> dict[str, object]:
        with self.connect() as connection:
            current_project = self.current_project(connection)
            count_row = connection.execute("SELECT COUNT(*) AS ticket_count FROM tickets").fetchone()
            assert count_row is not None
            ticket_count = int(count_row["ticket_count"])
        return {
            "default_project": current_project,
            "ticket_count": ticket_count,
        }

    def rename_default_project(self, new_project: str) -> dict[str, object]:
        new_key = normalize_project(new_project)
        with self.connect() as connection:
            current_key = self.current_project(connection)
            if current_key != new_key:
                connection.execute(
                    "UPDATE settings SET value = ? WHERE key = ?",
                    (new_key, DEFAULT_PROJECT_SETTING),
                )
            count_row = connection.execute("SELECT COUNT(*) AS ticket_count FROM tickets").fetchone()
            assert count_row is not None
            ticket_count = int(count_row["ticket_count"])
        return {
            "old_project": current_key,
            "new_project": new_key,
            "renamed_ticket_count": ticket_count if current_key != new_key else 0,
        }

    def get_ticket(self, ticket_id: str) -> dict:
        with self.connect() as connection:
            project_key = self.current_project(connection)
            row = self.resolve_ticket_row(connection, ticket_id, project_key=project_key)
        return self.ticket_from_row(row, project_key)

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
        sort_key = normalize_list_sort(sort_by)
        sort_direction = normalize_list_direction(direction).upper()

        with self.connect() as connection:
            current_project = self.current_project(connection)
            if project and normalize_project(project) != current_project:
                return []
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

            if sort_key == "ticket_id":
                order_clause = f"number {sort_direction}"
            elif sort_key == "priority":
                order_clause = (
                    "CASE priority "
                    "WHEN 'critical' THEN 4 "
                    "WHEN 'high' THEN 3 "
                    "WHEN 'medium' THEN 2 "
                    "WHEN 'low' THEN 1 "
                    f"ELSE 0 END {sort_direction}, updated_at DESC, number DESC"
                )
            elif sort_key == "status":
                order_clause = (
                    "CASE status "
                    "WHEN 'open' THEN 1 "
                    "WHEN 'in_progress' THEN 2 "
                    "WHEN 'closed' THEN 3 "
                    f"ELSE 4 END {sort_direction}, updated_at DESC, number DESC"
                )
            else:
                order_clause = f"updated_at {sort_direction}, number DESC"

            rows = connection.execute(f"{query} ORDER BY {order_clause}", values).fetchall()
        return [self.ticket_from_row(row, current_project) for row in rows]

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

        with self.connect() as connection:
            current_project = self.current_project(connection)
            existing_ticket = self.resolve_ticket_row(connection, ticket_id, project_key=current_project)
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
            values.append(int(existing_ticket["id"]))
            connection.execute(
                f"UPDATE tickets SET {', '.join(assignments)}, updated_at = ? WHERE id = ?",
                values,
            )
            row = connection.execute(
                "SELECT * FROM tickets WHERE id = ?",
                (int(existing_ticket["id"]),),
            ).fetchone()
        assert row is not None
        return self.ticket_from_row(row, current_project)

    def close_ticket(self, ticket_id: str, *, reason: str) -> dict:
        reason = (reason or "").strip()
        if not reason:
            raise ValidationError("Closing a ticket requires a resolution reason.")
        return self.update_ticket(ticket_id, status="closed", resolution_reason=reason)

    def reopen_ticket(self, ticket_id: str) -> dict:
        return self.update_ticket(ticket_id, status="open", resolution_reason="")

    def add_comment(self, ticket_id: str, body_md: str) -> dict:
        body_md = body_md.strip()
        if not body_md:
            raise ValidationError("Comment text is required.")
        now = utc_now()
        with self.connect() as connection:
            current_project = self.current_project(connection)
            ticket_row = self.resolve_ticket_row(connection, ticket_id, project_key=current_project)
            cursor = connection.execute(
                "INSERT INTO comments (ticket_id, body_md, created_at) VALUES (?, ?, ?)",
                (int(ticket_row["id"]), body_md, now),
            )
            self.touch_tickets(connection, [int(ticket_row["id"])], now)
            assert cursor.lastrowid is not None
            row = connection.execute(
                "SELECT * FROM comments WHERE id = ?",
                (int(cursor.lastrowid),),
            ).fetchone()
        assert row is not None
        return {
            "id": int(row["id"]),
            "ticket_id": format_ticket_id(current_project, int(ticket_row["number"])),
            "body_md": row["body_md"],
            "created_at": row["created_at"],
        }

    def list_comments(self, ticket_id: str) -> list[dict]:
        with self.connect() as connection:
            current_project = self.current_project(connection)
            ticket_row = self.resolve_ticket_row(connection, ticket_id, project_key=current_project)
            rows = connection.execute(
                "SELECT * FROM comments WHERE ticket_id = ? ORDER BY created_at ASC, id ASC",
                (int(ticket_row["id"]),),
            ).fetchall()
        return [
            {
                "id": int(row["id"]),
                "ticket_id": format_ticket_id(current_project, int(ticket_row["number"])),
                "body_md": row["body_md"],
                "created_at": row["created_at"],
            }
            for row in rows
        ]

    def link_tickets(self, left_ticket: str, right_ticket: str) -> None:
        now = utc_now()
        with self.connect() as connection:
            current_project = self.current_project(connection)
            left_row = self.resolve_ticket_row(connection, left_ticket, project_key=current_project)
            right_row = self.resolve_ticket_row(connection, right_ticket, project_key=current_project)
            left_db_id = int(left_row["id"])
            right_db_id = int(right_row["id"])
            if left_db_id == right_db_id:
                raise ValidationError("A ticket cannot be related to itself.")

            ticket_a_id, ticket_b_id = sorted((left_db_id, right_db_id))
            connection.execute(
                "INSERT OR IGNORE INTO links (ticket_a_id, ticket_b_id, created_at) VALUES (?, ?, ?)",
                (ticket_a_id, ticket_b_id, now),
            )
            self.touch_tickets(connection, [left_db_id, right_db_id], now)

    def unlink_tickets(self, left_ticket: str, right_ticket: str) -> None:
        now = utc_now()
        with self.connect() as connection:
            current_project = self.current_project(connection)
            left_row = self.resolve_ticket_row(connection, left_ticket, project_key=current_project)
            right_row = self.resolve_ticket_row(connection, right_ticket, project_key=current_project)
            left_db_id = int(left_row["id"])
            right_db_id = int(right_row["id"])
            if left_db_id == right_db_id:
                raise ValidationError("A ticket cannot be related to itself.")

            ticket_a_id, ticket_b_id = sorted((left_db_id, right_db_id))
            connection.execute(
                "DELETE FROM links WHERE ticket_a_id = ? AND ticket_b_id = ?",
                (ticket_a_id, ticket_b_id),
            )
            self.touch_tickets(connection, [left_db_id, right_db_id], now)

    def list_related_tickets(self, ticket_id: str) -> list[dict]:
        with self.connect() as connection:
            current_project = self.current_project(connection)
            ticket_row = self.resolve_ticket_row(connection, ticket_id, project_key=current_project)
            rows = connection.execute(
                """
                SELECT t.*
                FROM tickets t
                WHERE t.id IN (
                    SELECT ticket_b_id FROM links WHERE ticket_a_id = ?
                    UNION
                    SELECT ticket_a_id FROM links WHERE ticket_b_id = ?
                )
                ORDER BY t.number
                """,
                (int(ticket_row["id"]), int(ticket_row["id"])),
            ).fetchall()
        return [self.ticket_from_row(row, current_project) for row in rows]

    def list_links(self, ticket_id: str | None = None) -> list[dict]:
        with self.connect() as connection:
            current_project = self.current_project(connection)
            values: tuple[object, ...] = ()
            where_clause = ""
            if ticket_id is not None:
                ticket_row = self.resolve_ticket_row(connection, ticket_id, project_key=current_project)
                ticket_db_id = int(ticket_row["id"])
                where_clause = "WHERE l.ticket_a_id = ? OR l.ticket_b_id = ?"
                values = (ticket_db_id, ticket_db_id)

            rows = connection.execute(
                f"""
                SELECT
                    l.ticket_a_id,
                    l.ticket_b_id,
                    left_ticket.number AS ticket_a_number,
                    right_ticket.number AS ticket_b_number,
                    left_ticket.title AS ticket_a_title,
                    right_ticket.title AS ticket_b_title
                FROM links l
                JOIN tickets AS left_ticket ON left_ticket.id = l.ticket_a_id
                JOIN tickets AS right_ticket ON right_ticket.id = l.ticket_b_id
                {where_clause}
                ORDER BY left_ticket.number, right_ticket.number
                """,
                values,
            ).fetchall()

        return [
            {
                "ticket_a": format_ticket_id(current_project, int(row["ticket_a_number"])),
                "ticket_b": format_ticket_id(current_project, int(row["ticket_b_number"])),
                "ticket_a_title": row["ticket_a_title"],
                "ticket_b_title": row["ticket_b_title"],
            }
            for row in rows
        ]

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
        ticket_db_ids: Iterable[int],
        timestamp: str | None = None,
    ) -> None:
        stamp = timestamp or utc_now()
        normalized_ids = [int(ticket_db_id) for ticket_db_id in ticket_db_ids]
        if not normalized_ids:
            return
        connection.executemany(
            "UPDATE tickets SET updated_at = ? WHERE id = ?",
            [(stamp, ticket_db_id) for ticket_db_id in normalized_ids],
        )

    def normalize_status(self, status: str) -> str:
        normalized = (status or "").strip().lower()
        if normalized not in STATUS_VALUES:
            raise ValidationError("Status must be one of: open, in_progress, closed.")
        return normalized
