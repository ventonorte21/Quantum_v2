"""Shared test configuration — credentials and fixtures loaded from environment."""
import os
import pytest

# All test credentials come from environment variables.
# Never hardcode secrets in test files.
SESSION_TOKEN = os.environ.get("TEST_SESSION_TOKEN", "test_session_auth_1775441581328")
BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/")
MONGO_URL = os.environ.get("MONGO_URL", "mongodb://localhost:27017")
DB_NAME = os.environ.get("DB_NAME", "test_database")


def get_auth_headers():
    """Return standard auth headers for API tests."""
    return {
        "Authorization": f"Bearer {SESSION_TOKEN}",
        "Cookie": f"session_token={SESSION_TOKEN}",
    }


@pytest.fixture
def auth_headers():
    return get_auth_headers()


@pytest.fixture
def api_url():
    return BASE_URL
