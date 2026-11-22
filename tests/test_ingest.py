"""Tests for ingestion/run.py against a real Postgres, with a fake embedding model.

Skipped when Postgres isn't running. The fake embedder means no Ollama and no waiting, while
everything on the database side (transactions, replacing chunks, skipping) is the real thing.

Two connections are used on purpose. `ingest_conn` (conftest.py) is a normal connection, like the
real ingestion's, whose changes only exist once committed. `conn` (autocommit, also from
conftest.py) is a second connection used to look at the result: it only sees what was committed.
"""

import psycopg
import pytest

from app.providers.base import ProviderError
from ingestion.chunk import chunk_trial
from ingestion.run import content_hash, ingest
from tests.fakes import FakeEmbedder, make_study

MODEL = "fake-model"

pytestmark = pytest.mark.integration


def trials_of(*studies):
    return {s["protocolSection"]["identificationModule"]["nctId"]: s for s in studies}


def count(conn, table):
    return conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]


def run(studies, ingest_conn, embedder=None, model=MODEL, **options):
    return ingest(
        studies, ingest_conn, embedder or FakeEmbedder(), model, log=lambda _: None, **options
    )


def test_ingest_stores_trials_and_their_chunks(conn, ingest_conn):
    studies = trials_of(make_study("NCT00000001"), make_study("NCT00000002", phases=("PHASE2",)))

    result = run(studies, ingest_conn)

    expected_chunks = sum(len(chunk_trial(s)) for s in studies.values())
    assert (result.ingested, result.skipped, result.chunks) == (2, 0, expected_chunks)
    assert count(conn, "trials") == 2
    assert count(conn, "chunks") == expected_chunks

    row = conn.execute(
        "SELECT title, phases, sponsor, "
        "raw -> 'protocolSection' -> 'identificationModule' ->> 'nctId' "
        "FROM trials WHERE nct_id = 'NCT00000002'"
    ).fetchone()
    assert row == ("Semaglutide for obesity", ["PHASE2"], "Acme Pharma", "NCT00000002")


def test_the_stored_chunks_are_searchable_by_id_and_by_word(conn, ingest_conn):
    run(trials_of(make_study("NCT00000042")), ingest_conn)

    def search(query):
        return conn.execute(
            "SELECT DISTINCT nct_id FROM chunks WHERE tsv @@ websearch_to_tsquery('english', %s)",
            (query,),
        ).fetchall()

    assert search("NCT00000042") == [("NCT00000042",)]  # findable by ID thanks to the header
    assert search("pregnancy") == [("NCT00000042",)]


def test_running_again_skips_unchanged_trials_without_calling_the_model(ingest_conn):
    studies = trials_of(make_study("NCT00000001"), make_study("NCT00000002"))
    run(studies, ingest_conn)

    second_embedder = FakeEmbedder()
    result = run(studies, ingest_conn, second_embedder)

    assert (result.ingested, result.skipped, result.chunks) == (0, 2, 0)
    assert second_embedder.calls == []


def test_a_changed_trial_is_rewritten_and_the_others_are_left_alone(conn, ingest_conn):
    first, second = make_study("NCT00000001"), make_study("NCT00000002")
    run(trials_of(first, second), ingest_conn)
    untouched_ids = conn.execute("SELECT id FROM chunks WHERE nct_id = 'NCT00000002'").fetchall()

    changed = make_study("NCT00000001", summary="A brand new summary.", description=None)
    result = run(trials_of(changed, second), ingest_conn)

    assert (result.ingested, result.skipped) == (1, 1)
    texts = [r[0] for r in conn.execute("SELECT text FROM chunks WHERE nct_id = 'NCT00000001'")]
    assert any("A brand new summary." in t for t in texts)
    assert not any("A study of semaglutide" in t for t in texts)  # the old chunks are gone
    assert not any("Detailed description" in t for t in texts)  # and a dropped section vanished
    later_ids = conn.execute("SELECT id FROM chunks WHERE nct_id = 'NCT00000002'").fetchall()
    assert later_ids == untouched_ids


def test_changing_the_embedding_model_reprocesses_everything(ingest_conn):
    studies = trials_of(make_study("NCT00000001"))
    run(studies, ingest_conn, model="model-a")

    result = run(studies, ingest_conn, model="model-b")

    assert result.ingested == 1  # vectors from different models must never be mixed


def test_the_fingerprint_depends_on_the_record_and_the_model_but_not_key_order():
    study = make_study()
    reordered = {"protocolSection": dict(reversed(list(study["protocolSection"].items())))}

    assert content_hash(study, MODEL) == content_hash(reordered, MODEL)
    assert content_hash(study, MODEL) != content_hash(make_study(summary="other"), MODEL)
    assert content_hash(study, MODEL) != content_hash(study, "another-model")


def test_a_crash_keeps_finished_trials_and_leaves_no_half_written_trial(conn, ingest_conn):
    studies = trials_of(
        make_study("NCT00000001"), make_study("NCT00000002"), make_study("NCT00000003")
    )
    # One trial per save, and the model breaks on its second request (the second trial).
    broken = FakeEmbedder(fail_on_call=2)

    with pytest.raises(ProviderError):
        run(studies, ingest_conn, broken, trials_per_commit=1)

    assert [r[0] for r in conn.execute("SELECT nct_id FROM trials")] == ["NCT00000001"]
    assert conn.execute("SELECT DISTINCT nct_id FROM chunks").fetchall() == [("NCT00000001",)]

    # Running again continues where it stopped: one skipped, two done.
    result = run(studies, ingest_conn, trials_per_commit=1)
    assert (result.ingested, result.skipped) == (2, 1)
    assert count(conn, "trials") == 3


def test_a_failed_write_rolls_back_the_whole_batch(conn, ingest_conn):
    studies = trials_of(make_study("NCT00000001"), make_study("NCT00000002"))

    class LastVectorTooShort(FakeEmbedder):
        def embed(self, texts):
            vectors = super().embed(texts)
            vectors[-1] = [0.1, 0.2, 0.3]  # the database rejects it, after trial 1 was written
            return vectors

    with pytest.raises(psycopg.Error, match="1024 dimensions"):
        run(studies, ingest_conn, LastVectorTooShort())

    # Trial 1 had already been written inside the same transaction. It must be gone too.
    assert count(conn, "trials") == 0
    assert count(conn, "chunks") == 0


def test_a_model_that_returns_too_few_vectors_is_reported_clearly(conn, ingest_conn):
    class DropsOne(FakeEmbedder):
        def embed(self, texts):
            return super().embed(texts)[:-1]

    with pytest.raises(ProviderError, match="vectors for"):
        run(trials_of(make_study()), ingest_conn, DropsOne())

    assert count(conn, "trials") == 0
