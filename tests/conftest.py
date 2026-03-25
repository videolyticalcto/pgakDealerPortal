"""
Shared fixtures for PGAK tests.
Patches the database so no real PostgreSQL connection is needed.
"""

import pytest
from unittest.mock import patch, MagicMock
from app import create_app


@pytest.fixture()
def app():
    """Create a Flask app configured for testing."""
    application = create_app()
    application.config.update({
        "TESTING": True,
        "SECRET_KEY": "test-secret-key",
    })
    yield application


@pytest.fixture()
def client(app):
    """Flask test client."""
    return app.test_client()


@pytest.fixture()
def admin_session(client):
    """Set up an admin session so routes pass the auth check."""
    with client.session_transaction() as sess:
        sess["user_id"] = 1
        sess["user_type"] = "admin"
        sess["full_name"] = "Test Admin"
        sess["email"] = "admin@test.com"
    return client


@pytest.fixture()
def mock_db():
    """
    Patch psycopg2.connect used inside admin routes.
    Returns (mock_connection, mock_cursor) so tests can configure return values.
    """
    with patch("app.blueprints.admin.routes.psycopg2.connect") as mock_connect:
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_connect.return_value = mock_conn

        # Default: cursor context manager returns mock_cursor
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)

        yield mock_conn, mock_cursor
