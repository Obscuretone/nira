from __future__ import annotations

import re
import sqlite3
from contextlib import closing, contextmanager
from pathlib import Path
from typing import Final, Iterable, Iterator

from alembic import command
from alembic.config import Config
from sqlalchemy import (
    create_engine,
    func,
    pool,
    select,
    update,
)
from sqlalchemy.orm import (
    Session,
    sessionmaker,
)

from nira_app.models import (
    Base,
    Setting,
    Ticket,
)


STATUS_VALUES = {"open", "in_progress", "closed"}
LIST_SORT_VALUES = {"updated", "ticket_id", "priority", "status"}
LIST_DIRECTION_VALUES = {"asc", "desc"}
PROJECT_WORD_RE = re.compile(r"[A-Z]+(?=[A-Z][a-z]|\d|$)|[A-Z]?[a-z]+|\d+")
TICKET_ID_RE = re.compile(r"^([A-Za-z][A-Za-z0-9_-]*)-(\d+)$")
SOURCE_ROOT = Path(__file__).resolve().parents[1]
SCHEMA_VERSION = 2
DEFAULT_PROJECT_SETTING = "default_project"
THEME_SETTING = "theme"
LANGUAGE_SETTING = "language"

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
        word = words[0].upper()
        if len(word) < 3:
            candidate = word.ljust(4, "A")
        else:
            candidate = word
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
        raise ValidationError("Ticket IDs must look like NIRA-1.")
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

        # 1. Check if we need migration (read-only)
        needs_migration = False
        with self.connect() as connection:
            if self.needs_legacy_migration(connection):
                needs_migration = True

        # 2. Perform migration if needed (exclusive)
        stamp_revision: str | None = None
        if needs_migration:
            self.engine.dispose()
            with closing(sqlite3.connect(self.db_path)) as connection:
                connection.row_factory = sqlite3.Row
                self.migrate_legacy_schema(connection)
                connection.commit()
            stamp_revision = "head"  # We migrate directly to latest

        # 3. Create missing tables if not migrating
        if not needs_migration:
            with self.connect() as connection:
                if not self.table_exists(connection, "settings"):
                    # Use SQLAlchemy to create initial schema
                    self.engine.dispose()
                    Base.metadata.create_all(self.engine)
                    self.engine.dispose()
                    stamp_revision = "head"

        if stamp_revision:
            self.run_migrations(stamp_revision=stamp_revision)
            self.engine.dispose()

        # 4. Run alembic migrations (outside of connect context to avoid locking)
        if not _MIGRATIONS_RUN:
            self.run_migrations()
            _MIGRATIONS_RUN = True

        with self.connect() as connection:
            self.ensure_default_project(
                connection,
                default_key,
                force=default_project is not None,
            )
            # Ensure FTS is up to date and triggers are present
            self.create_fts_schema(connection)
            if not self.table_exists(connection, "tickets_search"):
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

    def close(self):
        self.engine.dispose()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.db_path)
        try:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA busy_timeout = 30000")
            connection.execute("PRAGMA journal_mode = DELETE")
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
        if "project" not in ticket_columns:
            return True
        id_info = connection.execute("PRAGMA table_info(tickets)").fetchall()
        id_type = next((str(row["type"]).upper() for row in id_info if row["name"] == "id"), "")
        if id_type != "INTEGER":
            return True
        link_columns = self.column_names(connection, "links")
        if link_columns and "ticket_a_id" not in link_columns:
            return True
        return False

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

        cols = self.column_names(connection, "tickets")
        project_col = "project" if "project" in cols else "NULL as project"

        old_tickets = connection.execute(
            f"""
            SELECT id, {project_col}, number, title, status, type, priority, source,
                   resolution_reason, body_md, resolution_md, created_at, updated_at
            FROM tickets
            ORDER BY number ASC
            """
        ).fetchall()

        legacy_projects = {normalize_project(row["project"]) for row in old_tickets if row["project"]}
        if len(legacy_projects) > 1:
            raise ValidationError("This workspace has multiple legacy ticket prefixes and cannot be auto-migrated.")

        default_key = list(legacy_projects)[0] if legacy_projects else derive_default_project_key(self.root.name)

        connection.executescript(
            """
            DROP TABLE IF EXISTS tickets_new;
            DROP TABLE IF EXISTS comments_new;
            DROP TABLE IF EXISTS links_new;
            DROP TABLE IF EXISTS history_new;

            CREATE TABLE tickets_new (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project TEXT NOT NULL,
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

            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
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

            CREATE TABLE history_new (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ticket_id INTEGER NOT NULL REFERENCES tickets_new(id) ON DELETE CASCADE,
                field TEXT NOT NULL,
                old_value TEXT,
                new_value TEXT,
                created_at TEXT NOT NULL
            );
            """
        )

        legacy_id_to_db_id: dict[str, int] = {}
        for row in old_tickets:
            project_val = normalize_project(row["project"]) if row["project"] else default_key
            cursor = connection.execute(
                """
                INSERT INTO tickets_new (
                    project, number, title, status, type, priority, source, resolution_reason,
                    body_md, resolution_md, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    project_val,
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
            link_cols = self.column_names(connection, "links")
            col_a = "ticket_a" if "ticket_a" in link_cols else "ticket_a_id"
            col_b = "ticket_b" if "ticket_b" in link_cols else "ticket_b_id"

            old_links = connection.execute(f"SELECT {col_a}, {col_b}, created_at FROM links").fetchall()
            for row in old_links:
                left_db_id = legacy_id_to_db_id.get(str(row[col_a]))
                right_db_id = legacy_id_to_db_id.get(str(row[col_b]))
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

        if self.table_exists(connection, "history"):
            old_history = connection.execute(
                "SELECT ticket_id, field, old_value, new_value, created_at FROM history"
            ).fetchall()
            for row in old_history:
                ticket_db_id = legacy_id_to_db_id.get(str(row["ticket_id"]))
                if ticket_db_id is None:
                    continue
                connection.execute(
                    """
                    INSERT INTO history_new (ticket_id, field, old_value, new_value, created_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (ticket_db_id, row["field"], row["old_value"], row["new_value"], row["created_at"]),
                )

        connection.execute("PRAGMA foreign_keys = OFF")
        try:
            for table_name in ("links", "comments", "tickets", "projects", "history"):
                if self.table_exists(connection, table_name):
                    connection.execute(f"DROP TABLE {table_name}")

            connection.execute("ALTER TABLE tickets_new RENAME TO tickets")
            connection.execute("ALTER TABLE comments_new RENAME TO comments")
            connection.execute("ALTER TABLE links_new RENAME TO links")
            connection.execute("ALTER TABLE history_new RENAME TO history")
        finally:
            connection.execute("PRAGMA foreign_keys = ON")

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
            "parent_id": ticket.parent_id,
            "parent_number": ticket.parent.number if ticket.parent else None,
            "story_points": ticket.story_points,
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

    def get_ticket(self, ticket_id: str) -> dict:
        with self.session() as session:
            project_key = self.current_project(session)
            ticket = self.resolve_ticket(session, ticket_id, project_key=project_key)
            return self.ticket_from_model(ticket, project_key)

    def list_projects(self) -> list[str]:
        with self.session() as session:
            projects = {str(row[0]) for row in session.query(Ticket.project).distinct().all() if row[0]}
            projects.add(self.current_project(session))
            return sorted(list(projects))

    def get_default_project(self) -> str:
        with self.session() as session:
            return self.current_project(session)

    def get_settings(self) -> dict[str, object]:
        with self.session() as session:
            current_project = self.current_project(session)
            theme_row = session.get(Setting, THEME_SETTING)
            theme = theme_row.value if theme_row else "auto"

            lang_row = session.get(Setting, LANGUAGE_SETTING)
            language = lang_row.value if lang_row else "auto"

            ticket_count = session.query(func.count(Ticket.id)).scalar() or 0
        return {
            "default_project": current_project,
            "theme": theme,
            "language": language,
            "ticket_count": ticket_count,
        }

    def update_settings(self, settings: dict[str, str]) -> None:
        with self.session() as session:
            if "default_project" in settings:
                new_project = normalize_project(settings["default_project"])
                stmt = update(Setting).where(Setting.key == DEFAULT_PROJECT_SETTING).values(value=new_project)
                session.execute(stmt)

            if "theme" in settings:
                theme = settings["theme"]
                if theme not in ("auto", "light", "dark"):
                    theme = "auto"

                theme_row = session.get(Setting, THEME_SETTING)
                if theme_row:
                    theme_row.value = theme
                else:
                    session.add(Setting(key=THEME_SETTING, value=theme))

            if "language" in settings:
                lang = settings["language"]
                if lang not in ("auto", "en", "fr", "es", "de"):
                    lang = "auto"

                lang_row = session.get(Setting, LANGUAGE_SETTING)
                if lang_row:
                    lang_row.value = lang
                else:
                    session.add(Setting(key=LANGUAGE_SETTING, value=lang))

            session.commit()

    def get_statuses(self) -> list[str]:
        return ["open", "in_progress", "closed"]

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
        normalized = (status or "").strip().lower().replace(" ", "_")
        statuses = self.get_statuses()
        if normalized not in statuses:
            valid = ", ".join(statuses)
            raise ValidationError(f"Status must be one of: {valid}.")
        return normalized
