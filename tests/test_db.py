"""Tests for the database layout (ingestion/schema.sql) and app/db.py.

The first test needs no database. The rest are marked `integration`: they need Postgres
(`make db-up`) and are skipped, with a message, when it isn't running. They use their own database
(`trials_test`, set up in conftest.py), so they never touch the data you ingested into `trials`.
"""

import re

import psycopg
import pytest
from pgvector import Vector

from app import db
from app.config import Settings


def vec(position: int) -> Vector:
    """A 1024-number vector with a single 1.0 at `position`: easy to reason about distances."""
    values = [0.0] * 1024
    values[position] = 1.0
    return Vector(values)


def add_trial(conn, nct_id="NCT00000001"):
    conn.execute(
        "INSERT INTO trials (nct_id, title, raw, content_hash) VALUES (%s, %s, '{}', 'h')",
        (nct_id, "A trial"),
    )


def add_chunk(conn, text, embedding, nct_id="NCT00000001", section="summary", index=0):
    conn.execute(
        "INSERT INTO chunks (nct_id, section, chunk_index, text, embedding) "
        "VALUES (%s, %s, %s, %s, %s)",
        (nct_id, section, index, text, embedding),
    )


def test_schema_vector_size_matches_the_embedding_dim_setting():
    # The column size is written in the SQL file; the setting is read by the embedder. If they
    # drift apart, inserts fail late and confusingly. This makes it fail early and clearly.
    declared = re.search(r"vector\((\d+)\)", db.SCHEMA_PATH.read_text()).group(1)
    assert int(declared) == Settings(_env_file=None).embedding_dim


@pytest.mark.integration
def test_init_schema_is_repeatable_and_creates_the_indexes(test_db_url, conn):
    db.init_schema(test_db_url)  # a second run must not fail
    db.init_schema(test_db_url)

    info = db.status(conn)
    assert info["pgvector"] is not None
    assert "chunks_embedding_idx" in info["indexes"]
    assert "chunks_tsv_idx" in info["indexes"]


@pytest.mark.integration
def test_nearest_vector_comes_first(conn):
    add_trial(conn)
    add_chunk(conn, "about obesity", vec(0), index=0)
    add_chunk(conn, "about asthma", vec(1), index=1)
    add_chunk(conn, "about cancer", vec(2), index=2)

    # A query pointing mostly at position 1 should rank the "asthma" chunk first.
    query = [0.0] * 1024
    query[1], query[0] = 1.0, 0.2
    rows = conn.execute(
        "SELECT text FROM chunks ORDER BY embedding <=> %s LIMIT 3", (Vector(query),)
    ).fetchall()

    assert [r[0] for r in rows] == ["about asthma", "about obesity", "about cancer"]


@pytest.mark.integration
def test_keyword_search_matches_word_forms_and_ids(conn):
    add_trial(conn)
    add_chunk(conn, "Semaglutide helps obese adults. NCT04998136", vec(0), index=0)
    add_chunk(conn, "Inhaled steroid for asthma in children", vec(1), index=1)

    def search(query):
        rows = conn.execute(
            "SELECT text FROM chunks WHERE tsv @@ websearch_to_tsquery('english', %s)", (query,)
        )
        return [r[0] for r in rows]

    assert len(search("obesity semaglutide")) == 1  # "obesity" and "obese" share a stem
    assert len(search("NCT04998136")) == 1  # IDs are searchable as exact words
    assert search("diabetes") == []


@pytest.mark.integration
def test_deleting_a_trial_deletes_its_chunks(conn):
    add_trial(conn)
    add_chunk(conn, "text", vec(0))

    conn.execute("DELETE FROM trials WHERE nct_id = 'NCT00000001'")

    assert conn.execute("SELECT count(*) FROM chunks").fetchone()[0] == 0


@pytest.mark.integration
def test_a_chunk_needs_an_existing_trial(conn):
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        add_chunk(conn, "orphan", vec(0), nct_id="NCT99999999")


@pytest.mark.integration
def test_the_same_chunk_cannot_be_stored_twice(conn):
    add_trial(conn)
    add_chunk(conn, "text", vec(0))

    with pytest.raises(psycopg.errors.UniqueViolation):
        add_chunk(conn, "text again", vec(1))


@pytest.mark.integration
def test_a_vector_of_the_wrong_size_is_rejected(conn):
    add_trial(conn)

    with pytest.raises(psycopg.Error, match="1024 dimensions"):
        add_chunk(conn, "text", Vector([0.1, 0.2, 0.3]))
