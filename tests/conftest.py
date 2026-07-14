import pytest
from app.migrations import run_migrations
@pytest.fixture(scope='session',autouse=True)
def migrate_test_database():
    run_migrations()
