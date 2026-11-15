from collections.abc import Iterator
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

from .config import get_settings

SCHEMA = Path(__file__).with_name("schema.sql")


def connect() -> psycopg.Connection:
    """One short connection per request: the app runs as serverless functions on Vercel,
    and Neon's pooled connection string absorbs the reconnects."""
    url = get_settings().database_url
    for prefix in ("postgresql+psycopg://", "postgres://"):
        if url.startswith(prefix):
            url = "postgresql://" + url[len(prefix):]
    return psycopg.connect(url, autocommit=True, row_factory=dict_row)


def get_db() -> Iterator[psycopg.Connection]:
    with connect() as conn:
        yield conn


def migrate() -> None:
    with connect() as conn:
        conn.execute(SCHEMA.read_text())


if __name__ == "__main__":
    migrate()
    print("Schema up to date.")
