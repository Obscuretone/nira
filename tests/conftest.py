import shutil
import tempfile
from pathlib import Path

import pytest

import nira_app.storage

@pytest.fixture(autouse=True)
def reset_migrations():
    """Resets the migration tracking flag before each test."""
    nira_app.storage._MIGRATIONS_RUN = False
    yield

@pytest.fixture
def temp_root():

    """Provides a temporary directory path for tests."""
    tempdir = tempfile.mkdtemp()
    yield Path(tempdir)
    shutil.rmtree(tempdir)
