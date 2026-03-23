from __future__ import annotations

import re
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Final, Iterable, Iterator

from alembic import command
from alembic.config import Config
from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    case,
    create_engine,
    delete,
    func,
    pool,
    select,
    text,
    update,
)

from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    Session,
    mapped_column,
    relationship,
    sessionmaker,
)


STATUS_VALUES = {"open", "in_progress", "closed"}
LIST_SORT_VALUES = {"updated", "ticket_id", "priority", "status"}
LIST_DIRECTION_VALUES = {"asc", "desc"}
PROJECT_WORD_RE = re.compile(r"[A-Z]+(?=[A-Z][a-z]|\d|$)|[A-Z]?[a-z]+|\d+")
TICKET_ID_RE = re.compile(r"^([A-Za-z][A-Za-z0-9_-]*)-(\d+)$")
SOURCE_ROOT = Path(__file__).resolve().parents[1]
SCHEMA_VERSION = 2
DEFAULT_PROJECT_SETTING = "default_project"

_MIGRATIONS_RUN = False


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


class Base(DeclarativeBase):
    pass


class Setting(Base):
    __tablename__ = "settings"
    key: Mapped[str] = mapped_column(String, primary_key=True)
    value: Mapped[str] = mapped_column(String, nullable=False)


class Ticket(Base):
    __tablename__ = "tickets"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    number: Mapped[int] = mapped_column(Integer, nullable=False, unique=True)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False)
    type: Mapped[str] = mapped_column(String, nullable=False)
    priority: Mapped[str] = mapped_column(String, nullable=False)
    source: Mapped[str] = mapped_column(String, nullable=False)
    resolution_reason: Mapped[str] = mapped_column(Text, nullable=False, default="")
    labels: Mapped[str] = mapped_column(String, nullable=False, default="")
    due_date: Mapped[str | None] = mapped_column(String, nullable=True)
    body_md: Mapped[str] = mapped_column(Text, nullable=False, default="")
    resolution_md: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[str] = mapped_column(String, nullable=False)
    updated_at: Mapped[str] = mapped_column(String, nullable=False)

    comments: Mapped[list["Comment"]] = relationship("Comment", back_populates="ticket", cascade="all, delete-orphan")


class Comment(Base):
    __tablename__ = "comments"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ticket_id: Mapped[int] = mapped_column(Integer, ForeignKey("tickets.id", ondelete="CASCADE"), nullable=False)
    body_md: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[str] = mapped_column(String, nullable=False)

    ticket: Mapped["Ticket"] = relationship("Ticket", back_populates="comments")
    __table_args__ = (Index("comments_ticket_id_idx", "ticket_id"),)


class Link(Base):
    __tablename__ = "links"
    ticket_a_id: Mapped[int] = mapped_column(Integer, ForeignKey("tickets.id", ondelete="CASCADE"), primary_key=True)
    ticket_b_id: Mapped[int] = mapped_column(Integer, ForeignKey("tickets.id", ondelete="CASCADE"), primary_key=True)
    created_at: Mapped[str] = mapped_column(String, nullable=False)

    __table_args__ = (
        CheckConstraint("ticket_a_id < ticket_b_id"),
        Index("links_ticket_b_idx", "ticket_b_id"),
    )


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
        self.engine = create_engine(
            f"sqlite:///{self.db_path}",
            connect_args={"timeout": 30},
            poolclass=pool.StaticPool,
        )
        self.Session = sessionmaker(bind=self.engine)
        self.alembic_cfg = Config(str(SOURCE_ROOT / "alembic.ini"))
        self.alembic_cfg.set_main_option("script_location", str(SOURCE_ROOT / "nira_app" / "migrations" / "alembic"))

    def initialize(self, default_project: str | None = None) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.ensure_schema(default_project=default_project)

    def ensure_schema(self, default_project: str | None = None) -> None:
        global _MIGRATIONS_RUN
        default_key = (
            normalize_project(default_project) if default_project else derive_default_project_key(self.root.name)
        )

        stamp_revision: str | None = None
        with self.connect() as connection:
            if self.needs_legacy_migration(connection):
                self.migrate_legacy_schema(connection)
                stamp_revision = "b00dc4b8ba72"  # Initial schema revision
            elif not self.table_exists(connection, "settings"):
                # Use SQLAlchemy to create initial schema
                Base.metadata.create_all(self.engine)
                stamp_revision = "head"

        if stamp_revision:
            self.run_migrations(stamp_revision=stamp_revision)

        # Run alembic migrations (outside of connect context to avoid locking)
        if not _MIGRATIONS_RUN:
            self.run_migrations()
            _MIGRATIONS_RUN = True

        with self.connect() as connection:
            self.ensure_default_project(
                connection,
                default_key,
                force=default_project is not None,
            )
            # Ensure FTS is up to date
            if not self.table_exists(connection, "tickets_search"):
                self.create_fts_schema(connection)
                self.populate_fts_index(connection)

            connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

    def run_migrations(self, *, stamp_revision: str | None = None) -> None:
        # Alembic needs a SQLAlchemy connection
        with self.engine.begin() as sa_conn:
            self.alembic_cfg.attributes["connection"] = sa_conn
            if stamp_revision:
                command.stamp(self.alembic_cfg, stamp_revision)
            else:
                command.upgrade(self.alembic_cfg, "head")
        self.engine.dispose()

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

    @contextmanager
    def session(self) -> Iterator[Session]:
        with self.Session() as session:
            try:
                yield session
                session.commit()
            except Exception:
                session.rollback()
                raise

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

    def create_fts_schema(self, connection: sqlite3.Connection) -> None:
        connection.executescript(
            """
            CREATE VIRTUAL TABLE IF NOT EXISTS tickets_search USING fts5(
                title,
                body_md,
                resolution_md,
                content='tickets',
                content_rowid='id'
            );

            CREATE TRIGGER IF NOT EXISTS tickets_ai AFTER INSERT ON tickets BEGIN
                INSERT INTO tickets_search(rowid, title, body_md, resolution_md)
                VALUES (new.id, new.title, new.body_md, new.resolution_md);
            END;

            CREATE TRIGGER IF NOT EXISTS tickets_ad AFTER DELETE ON tickets BEGIN
                INSERT INTO tickets_search(tickets_search, rowid, title, body_md, resolution_md)
                VALUES('delete', old.id, old.title, old.body_md, old.resolution_md);
            END;

            CREATE TRIGGER IF NOT EXISTS tickets_au AFTER UPDATE ON tickets BEGIN
                INSERT INTO tickets_search(tickets_search, rowid, title, body_md, resolution_md)
                VALUES('delete', old.id, old.title, old.body_md, old.resolution_md);
                INSERT INTO tickets_search(rowid, title, body_md, resolution_md)
                VALUES (new.id, new.title, new.body_md, new.resolution_md);
            END;
            """
        )

    def populate_fts_index(self, connection: sqlite3.Connection) -> None:
        connection.execute(
            "INSERT INTO tickets_search(rowid, title, body_md, resolution_md) "
            "SELECT id, title, body_md, resolution_md FROM tickets"
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

        legacy_projects = {normalize_project(row["project"]) for row in old_tickets if row["project"]}
        if len(legacy_projects) > 1:
            raise ValidationError("This workspace has multiple legacy ticket prefixes and cannot be auto-migrated.")

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

    def current_project(self, session: Session) -> str:
        stmt = select(Setting).where(Setting.key == DEFAULT_PROJECT_SETTING)
        row = session.execute(stmt).scalar_one_or_none()
        if row is None:
            raise ValidationError("No default project key is configured. Run `nira init` again.")
        return normalize_project(row.value)

    def ticket_from_model(self, ticket: Ticket, project_key: str) -> dict:
        return {
            "db_id": int(ticket.id),
            "id": format_ticket_id(project_key, int(ticket.number)),
            "project": project_key,
            "number": int(ticket.number),
            "title": ticket.title,
            "status": ticket.status,
            "type": ticket.type,
            "priority": ticket.priority,
            "source": ticket.source,
            "resolution_reason": ticket.resolution_reason,
            "labels": ticket.labels,
            "due_date": ticket.due_date,
            "body_md": ticket.body_md,
            "resolution_md": ticket.resolution_md,
            "created_at": ticket.created_at,
            "updated_at": ticket.updated_at,
        }

    def resolve_ticket(
        self,
        session: Session,
        ticket_ref: str,
        *,
        project_key: str | None = None,
    ) -> Ticket:
        number = ticket_number_from_ref(ticket_ref)
        stmt = select(Ticket).where(Ticket.number == number)
        row = session.execute(stmt).scalar_one_or_none()
        if row is None:
            current_project = project_key or self.current_project(session)
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
        labels: str = "",
        due_date: str | None = None,
        body_md: str = "",
        resolution_md: str = "",
    ) -> dict:
        title = (title or "").strip()
        if not title:
            raise ValidationError("Title is required.")

        now = utc_now()
        with self.session() as session:
            current_project = self.current_project(session)
            if project:
                requested_project = normalize_project(project)
                if requested_project != current_project:
                    raise ValidationError(
                        f"This workspace uses the {current_project} ticket prefix. Change it in settings first."
                    )

            next_number = session.query(func.max(Ticket.number)).scalar() or 0
            number = next_number + 1

            ticket = Ticket(
                number=number,
                title=title,
                status="open",
                type=(ticket_type or "task").strip() or "task",
                priority=(priority or "medium").strip() or "medium",
                source=(source or "").strip(),
                resolution_reason="",
                labels=(labels or "").strip(),
                due_date=due_date,
                body_md=body_md,
                resolution_md=resolution_md,
                created_at=now,
                updated_at=now,
            )
            session.add(ticket)
            session.flush()
            session.refresh(ticket)
            result = self.ticket_from_model(ticket, current_project)
        return result

    def get_default_project(self) -> str:
        with self.session() as session:
            return self.current_project(session)

    def get_settings(self) -> dict[str, object]:
        with self.session() as session:
            current_project = self.current_project(session)
            ticket_count = session.query(func.count(Ticket.id)).scalar() or 0
        return {
            "default_project": current_project,
            "ticket_count": ticket_count,
        }

    def rename_default_project(self, new_project: str) -> dict[str, object]:
        new_key = normalize_project(new_project)
        with self.session() as session:
            current_key = self.current_project(session)
            if current_key != new_key:
                stmt = update(Setting).where(Setting.key == DEFAULT_PROJECT_SETTING).values(value=new_key)
                session.execute(stmt)
            ticket_count = session.query(func.count(Ticket.id)).scalar() or 0
        return {
            "old_project": current_key,
            "new_project": new_key,
            "renamed_ticket_count": ticket_count if current_key != new_key else 0,
        }

    def get_ticket(self, ticket_id: str) -> dict:
        with self.session() as session:
            project_key = self.current_project(session)
            ticket = self.resolve_ticket(session, ticket_id, project_key=project_key)
            result = self.ticket_from_model(ticket, project_key)
        return result

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
    ) -> list[dict]:
        sort_key = normalize_list_sort(sort_by)
        sort_direction = normalize_list_direction(direction).lower()

        with self.session() as session:
            current_project = self.current_project(session)
            if project and normalize_project(project) != current_project:
                return []

            stmt = select(Ticket)

            if search:
                stmt = stmt.where(
                    text("tickets.id IN (SELECT rowid FROM tickets_search WHERE tickets_search MATCH :query)")
                ).params(query=search)

            if label:
                stmt = stmt.where(Ticket.labels.contains(label.strip()))

            if overdue:
                today = utc_now()[:10]  # YYYY-MM-DD
                stmt = stmt.where(Ticket.due_date < today).where(Ticket.status != "closed")

            if status:
                if status == "not_closed":
                    stmt = stmt.where(Ticket.status != "closed")
                else:
                    stmt = stmt.where(Ticket.status == self.normalize_status(status))
            if priority:
                stmt = stmt.where(Ticket.priority == priority.strip())
            if ticket_type:
                stmt = stmt.where(Ticket.type == ticket_type.strip())

            # Handle sorting
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
            return [self.ticket_from_model(ticket, current_project) for ticket in tickets]

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
    ) -> int:
        with self.session() as session:
            current_project = self.current_project(session)
            if project and normalize_project(project) != current_project:
                return 0

            stmt = select(func.count(Ticket.id))

            if search:
                stmt = stmt.where(
                    text("tickets.id IN (SELECT rowid FROM tickets_search WHERE tickets_search MATCH :query)")
                ).params(query=search)

            if label:
                stmt = stmt.where(Ticket.labels.contains(label.strip()))

            if overdue:
                today = utc_now()[:10]  # YYYY-MM-DD
                stmt = stmt.where(Ticket.due_date < today).where(Ticket.status != "closed")

            if status:
                if status == "not_closed":
                    stmt = stmt.where(Ticket.status != "closed")
                else:
                    stmt = stmt.where(Ticket.status == self.normalize_status(status))
            if priority:
                stmt = stmt.where(Ticket.priority == priority.strip())
            if ticket_type:
                stmt = stmt.where(Ticket.type == ticket_type.strip())

            return session.execute(stmt).scalar() or 0

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
        body_md: str | _UnsetType = UNSET,
        resolution_md: str | _UnsetType = UNSET,
    ) -> dict:
        updates: dict[str, Any] = {}

        if isinstance(title, str):
            clean_title = title.strip()
            if not clean_title:
                raise ValidationError("Title cannot be empty.")
            updates["title"] = clean_title

        normalized_status: str | _UnsetType = UNSET
        if isinstance(status, str):
            normalized_status = self.normalize_status(status)
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

        normalized_reason: str | _UnsetType = UNSET
        if isinstance(resolution_reason, str):
            normalized_reason = resolution_reason.strip()

        if isinstance(body_md, str):
            updates["body_md"] = body_md

        if isinstance(resolution_md, str):
            updates["resolution_md"] = resolution_md

        if not updates and normalized_reason is UNSET:
            raise ValidationError("No fields were provided to update.")

        with self.session() as session:
            current_project = self.current_project(session)
            ticket = self.resolve_ticket(session, ticket_id, project_key=current_project)

            final_reason = normalized_reason
            if normalized_status is not UNSET:
                if normalized_status == "closed" and (final_reason is UNSET or not final_reason):
                    final_reason = (ticket.resolution_reason or "").strip() or "completed"
                elif ticket.status == "closed" and final_reason is UNSET:
                    final_reason = ""

            if final_reason is not UNSET:
                updates["resolution_reason"] = final_reason

            for key, value in updates.items():
                setattr(ticket, key, value)

            ticket.updated_at = utc_now()
            session.flush()
            result = self.ticket_from_model(ticket, current_project)
        return result

    def close_ticket(self, ticket_id: str, *, resolution_md: str) -> dict:
        resolution_md = (resolution_md or "").strip()
        if not resolution_md:
            raise ValidationError("Closing a ticket requires resolution notes.")
        return self.update_ticket(
            ticket_id, status="closed", resolution_reason="completed", resolution_md=resolution_md
        )

    def reopen_ticket(self, ticket_id: str) -> dict:
        return self.update_ticket(ticket_id, status="open", resolution_reason="")

    def add_comment(self, ticket_id: str, body_md: str) -> dict:
        body_md = body_md.strip()
        if not body_md:
            raise ValidationError("Comment text is required.")
        now = utc_now()
        with self.session() as session:
            current_project = self.current_project(session)
            ticket = self.resolve_ticket(session, ticket_id, project_key=current_project)
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
        return result

    def list_comments(self, ticket_id: str) -> list[dict]:
        with self.session() as session:
            current_project = self.current_project(session)
            ticket = self.resolve_ticket(session, ticket_id, project_key=current_project)
            stmt = (
                select(Comment)
                .where(Comment.ticket_id == ticket.id)
                .order_by(Comment.created_at.asc(), Comment.id.asc())
            )
            comments = session.execute(stmt).scalars().all()
            return [
                {
                    "id": int(comment.id),
                    "ticket_id": format_ticket_id(current_project, int(ticket.number)),
                    "body_md": comment.body_md,
                    "created_at": comment.created_at,
                }
                for comment in comments
            ]

    def link_tickets(self, left_ticket: str, right_ticket: str) -> None:
        now = utc_now()
        with self.session() as session:
            current_project = self.current_project(session)
            left_ticket_model = self.resolve_ticket(session, left_ticket, project_key=current_project)
            right_ticket_model = self.resolve_ticket(session, right_ticket, project_key=current_project)

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
        with self.session() as session:
            current_project = self.current_project(session)
            left_ticket_model = self.resolve_ticket(session, left_ticket, project_key=current_project)
            right_ticket_model = self.resolve_ticket(session, right_ticket, project_key=current_project)

            if left_ticket_model.id == right_ticket_model.id:
                raise ValidationError("A ticket cannot be related to itself.")

            ticket_a_id, ticket_b_id = sorted((left_ticket_model.id, right_ticket_model.id))
            stmt = delete(Link).where(Link.ticket_a_id == ticket_a_id, Link.ticket_b_id == ticket_b_id)
            session.execute(stmt)

            left_ticket_model.updated_at = now
            right_ticket_model.updated_at = now

    def list_related_tickets(self, ticket_id: str) -> list[dict]:
        with self.session() as session:
            current_project = self.current_project(session)
            ticket_model = self.resolve_ticket(session, ticket_id, project_key=current_project)

            # Find all tickets linked to this one
            stmt_a = select(Link.ticket_b_id).where(Link.ticket_a_id == ticket_model.id)
            stmt_b = select(Link.ticket_a_id).where(Link.ticket_b_id == ticket_model.id)

            related_ids_a = session.execute(stmt_a).scalars().all()
            related_ids_b = session.execute(stmt_b).scalars().all()
            related_ids = set(related_ids_a) | set(related_ids_b)

            if not related_ids:
                return []

            stmt = select(Ticket).where(Ticket.id.in_(related_ids)).order_by(Ticket.number)
            tickets = session.execute(stmt).scalars().all()
            return [self.ticket_from_model(ticket, current_project) for ticket in tickets]

    def list_links(self, ticket_id: str | None = None) -> list[dict]:
        with self.session() as session:
            current_project = self.current_project(session)
            stmt = select(
                Link.ticket_a_id,
                Link.ticket_b_id,
            )
            if ticket_id is not None:
                ticket_model = self.resolve_ticket(session, ticket_id, project_key=current_project)
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

    def ticket_details(self, ticket_id: str) -> dict:
        ticket = self.get_ticket(ticket_id)
        return {
            "ticket": ticket,
            "comments": self.list_comments(ticket["id"]),
            "related": self.list_related_tickets(ticket["id"]),
        }

    def touch_tickets(
        self,
        session: Session,
        ticket_db_ids: Iterable[int],
        timestamp: str | None = None,
    ) -> None:
        stamp = timestamp or utc_now()
        normalized_ids = [int(ticket_db_id) for ticket_db_id in ticket_db_ids]
        if not normalized_ids:
            return
        stmt = update(Ticket).where(Ticket.id.in_(normalized_ids)).values(updated_at=stamp)
        session.execute(stmt)

    def normalize_status(self, status: str) -> str:
        normalized = (status or "").strip().lower()
        if normalized not in STATUS_VALUES:
            raise ValidationError("Status must be one of: open, in_progress, closed.")
        return normalized
