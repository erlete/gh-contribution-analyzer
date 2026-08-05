import pytest

from gca.config import get_settings
from gca.db.engine import reset_engine_cache


@pytest.fixture(autouse=True)
def _clear_caches() -> None:
    get_settings.cache_clear()
    reset_engine_cache()
