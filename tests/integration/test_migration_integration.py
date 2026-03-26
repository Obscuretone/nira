from pathlib import Path
import tempfile
from nira_app.storage import SOURCE_ROOT
from sqlalchemy import inspect, create_engine, pool
from alembic import command
from alembic.config import Config
import logging


def test_run_migrations():
    with tempfile.TemporaryDirectory() as td:
        db_path = Path(td) / "test.db"

        engine = create_engine(f"sqlite:///{db_path}", poolclass=pool.NullPool)

        # Test running all upgrades
        alembic_cfg = Config()
        alembic_cfg.set_main_option("script_location", str(SOURCE_ROOT / "nira_app" / "migrations" / "alembic"))
        alembic_cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")

        # Suppress logging slightly
        logging.getLogger("alembic").setLevel(logging.WARNING)

        command.upgrade(alembic_cfg, "head")

        # Verify tables created
        inspector = inspect(engine)
        tables = inspector.get_table_names()
        assert "tickets" in tables
        assert "comments" in tables
        assert "links" in tables
        assert "history" in tables
        assert "settings" in tables

        # Now test downgrade
        command.downgrade(alembic_cfg, "base")

        inspector = inspect(engine)
        tables = inspector.get_table_names()
        assert "tickets" not in tables
        assert "comments" not in tables
        assert "links" not in tables
        assert "history" not in tables
        assert "settings" not in tables
