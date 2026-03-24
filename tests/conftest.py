import pytest
import gc
from nira_app.storage import NiraStore


@pytest.fixture(autouse=True)
def cleanup_sqlite_connections():
    stores = []
    original_init = NiraStore.__init__

    def tracking_init(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        stores.append(self)

    NiraStore.__init__ = tracking_init  # type: ignore
    yield
    NiraStore.__init__ = original_init  # type: ignore

    for store in stores:
        if hasattr(store, "engine"):
            store.engine.dispose()
    gc.collect()


@pytest.fixture
def temp_root(tmp_path):
    return tmp_path
