"""Tests run against a throwaway local Postgres database (radar_vie_test)."""

import os
import subprocess

import pytest
from argon2 import PasswordHasher

TEST_DB = "radar_vie_test"
PASSWORD = "correct horse battery"
TOKEN = "t" * 40

os.environ.update(
    DATABASE_URL=f"postgresql://localhost/{TEST_DB}",
    APP_PASSWORD_HASH=PasswordHasher().hash(PASSWORD),
    SESSION_SECRET="s" * 40,
    API_TOKEN=TOKEN,
    SECURE_COOKIES="false",
)

from fastapi.testclient import TestClient  # noqa: E402

from app.db import connect, migrate  # noqa: E402
from app.main import app  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def database():
    subprocess.run(["createdb", TEST_DB], capture_output=True)  # already there is fine
    migrate()
    yield
    subprocess.run(["dropdb", "--if-exists", TEST_DB], capture_output=True)


@pytest.fixture(autouse=True)
def empty_tables():
    with connect() as conn:
        conn.execute("TRUNCATE offers, files, meta, failed_logins")


@pytest.fixture
def anonymous():
    return TestClient(app)


@pytest.fixture
def agent():
    return TestClient(app, headers={"Authorization": f"Bearer {TOKEN}"})


@pytest.fixture
def owner():
    client = TestClient(app)
    assert client.post("/api/login", json={"password": PASSWORD}).status_code == 200
    return client
