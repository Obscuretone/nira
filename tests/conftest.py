import shutil
import tempfile
from pathlib import Path

import pytest


@pytest.fixture
def temp_root():
    """Provides a temporary directory path for tests."""
    tempdir = tempfile.mkdtemp()
    yield Path(tempdir)
    shutil.rmtree(tempdir)
