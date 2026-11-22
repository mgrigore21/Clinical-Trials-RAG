"""Talking to Postgres: create the tables, open connections, and a quick status report.

Run `uv run python -m app.db init` (or `make db-init`) to create the tables and
`uv run python -m app.db status` (or `make db-status`) to see what is inside.
See docs/04-the-database.md.
"""

import sys
from pathlib import Path

import psycopg
from pgvector.psycopg import register_vector

from app.config import get_settings

SCHEMA_PATH = Path(__file__).resolve().parent.parent / "ingestion" / "schema.sql"


def init_schema(database_url: str | None = None) -> None:
    """Create the extension, tables and indexes. Safe to call repeatedly.

    Done from Python, not by mounting schema.sql into the Postgres container: the container only
    runs such files the very first time its data folder is empty, which is a confusing trap. This
    way the same command also sets up a database on AWS.
    """
    url = database_url or get_settings().database_url
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute(SCHEMA_PATH.read_text())


def connect(database_url: str | None = None) -> psycopg.Connection:
    """Open a connection that can send and receive vectors. Call init_schema() first.

    (register_vector needs the `vector` extension to exist, which init_schema creates.)
    """
    conn = psycopg.connect(database_url or get_settings().database_url)
    register_vector(conn)
    return conn


def status(conn: psycopg.Connection) -> dict:
    """A small summary used by `make db-status`."""
    server = conn.execute("SHOW server_version").fetchone()[0]
    vector = conn.execute("SELECT extversion FROM pg_extension WHERE extname = 'vector'").fetchone()
    return {
        "postgres": server,
        "pgvector": vector[0] if vector else None,
        "trials": conn.execute("SELECT count(*) FROM trials").fetchone()[0],
        "chunks": conn.execute("SELECT count(*) FROM chunks").fetchone()[0],
        "indexes": [
            row[0]
            for row in conn.execute(
                "SELECT indexname FROM pg_indexes WHERE tablename = 'chunks' ORDER BY indexname"
            )
        ],
    }


def main() -> None:
    command = sys.argv[1] if len(sys.argv) > 1 else ""
    if command not in ("init", "status"):
        raise SystemExit("usage: python -m app.db init|status")
    try:
        if command == "init":
            init_schema()
            print("Schema is ready (tables: trials, chunks).")
        with connect() as conn:
            for key, value in status(conn).items():
                print(f"{key:<9} {value}")
    except psycopg.OperationalError as exc:
        raise SystemExit(
            f"Cannot connect to Postgres at {get_settings().database_url}\n"
            f"Is it running? Start it with `make db-up`.\n({exc})"
        ) from exc
    except psycopg.errors.UndefinedTable as exc:
        raise SystemExit("The tables don't exist yet. Run `make db-init` first.") from exc


if __name__ == "__main__":
    main()
