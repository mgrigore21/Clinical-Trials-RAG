"""Tests for app/rag/retrieve.py.

The first group needs no database (the merging and grouping logic is plain Python). The second
group runs against a real Postgres with a small scripted collection of trials, so every expected
answer is known in advance. Skipped when Postgres isn't running.
"""

import zlib

import pytest
from pgvector import Vector

from app.rag.retrieve import (
    CHUNKS_PER_TRIAL,
    RetrievedChunk,
    _vector_query,
    distinctive_terms,
    group_by_trial,
    keyword_search,
    reciprocal_rank_fusion,
    retrieve,
    vector_search,
)
from ingestion.run import ingest
from tests.fakes import make_study

# --- Reciprocal Rank Fusion (no database) -------------------------------------------------


def test_rrf_worked_example():
    # Item 1 is 1st in list A and 2nd in list B; item 3 is 3rd in A and 1st in B; item 2 only in A.
    scores = reciprocal_rank_fusion([[1, 2, 3], [3, 1]], k=60)

    assert scores[1] == pytest.approx(1 / 61 + 1 / 62)
    assert scores[3] == pytest.approx(1 / 63 + 1 / 61)
    assert scores[2] == pytest.approx(1 / 62)
    assert sorted(scores, key=scores.get, reverse=True) == [1, 3, 2]


def test_rrf_rewards_agreement_between_the_lists():
    # Item 2 is only second in each list, but both lists have it. Items 1 and 3 each top one list.
    scores = reciprocal_rank_fusion([[1, 2], [3, 2]])

    assert scores[2] > scores[1] and scores[2] > scores[3]


def test_rrf_of_one_list_keeps_its_order_and_of_nothing_is_empty():
    scores = reciprocal_rank_fusion([[5, 9, 7]])

    assert sorted(scores, key=scores.get, reverse=True) == [5, 9, 7]
    assert reciprocal_rank_fusion([]) == {}


def test_keyword_search_without_terms_does_nothing():
    assert keyword_search(None, []) == []  # no database needed: it returns before using it


# --- Grouping chunks into trials (no database) --------------------------------------------


def chunk(chunk_id, nct_id, score):
    return RetrievedChunk(chunk_id, nct_id, "summary", "text", score, None, None)


def test_group_by_trial_orders_by_best_chunk_and_caps_both_numbers():
    chunks = [
        chunk(1, "T1", 0.9),
        chunk(2, "T2", 0.8),
        chunk(3, "T1", 0.7),
        chunk(4, "T3", 0.6),  # a third trial, but top_k is 2
        chunk(5, "T1", 0.5),  # a third chunk of T1, but only 2 are kept per trial
    ]
    meta = {t: (f"title {t}", ["PHASE3"], "COMPLETED") for t in ("T1", "T2", "T3")}

    trials = group_by_trial(chunks, meta, top_k=2, chunks_per_trial=2)

    assert [t.nct_id for t in trials] == ["T1", "T2"]
    assert [c.id for c in trials[0].chunks] == [1, 3]
    assert trials[0].score == 0.9 and trials[1].score == 0.8
    assert trials[0].title == "title T1"


# --- Against a real database --------------------------------------------------------------


class TopicEmbedder:
    """A scripted embedding model: the vector depends on which topic the text mentions.

    Real embeddings capture meaning in a way that is hard to predict; this one is crude but
    predictable, so a test can say exactly which chunks a question should be closest to.
    """

    TOPICS = ("semaglutide", "asthma")

    def embed(self, texts):
        vectors = []
        for text in texts:
            lowered = text.lower()
            topic = next((i for i, name in enumerate(self.TOPICS) if name in lowered), None)
            if topic is None:  # no known topic: a position derived from the text itself
                topic = 10 + zlib.crc32(lowered.encode()) % 900
            vector = [0.0] * 1024
            vector[topic] = 1.0
            vectors.append(vector)
        return vectors


def build_collection(ingest_conn):
    """Three trials we know well, plus 25 unrelated fillers so word frequencies look realistic."""
    semaglutide_late = make_study(
        "NCT00000001", "Semaglutide for obesity", phases=("PHASE3",), status="COMPLETED"
    )
    asthma = make_study(
        "NCT00000002",
        "Inhaled steroid for asthma",
        phases=("PHASE2",),
        summary="Budesonide in children with asthma.",
        interventions=("Budesonide",),
        status="COMPLETED",
    )
    semaglutide_recruiting = make_study(
        "NCT00000003", "Semaglutide in adults", phases=("PHASE2",), status="RECRUITING"
    )
    fillers = [
        make_study(
            f"NCT1000{i:04d}",
            f"Filler trial number {i}",
            phases=("PHASE1",),
            summary=f"Filler summary {i}.",
            interventions=("Water",),
        )
        for i in range(25)
    ]
    studies = {
        s["protocolSection"]["identificationModule"]["nctId"]: s
        for s in [semaglutide_late, asthma, semaglutide_recruiting, *fillers]
    }
    ingest(studies, ingest_conn, TopicEmbedder(), "scripted", log=lambda _: None)


@pytest.fixture
def collection(conn, ingest_conn):
    build_collection(ingest_conn)
    return conn


def ids(retrieval):
    return [t.nct_id for t in retrieval.trials]


@pytest.mark.integration
def test_meaning_search_finds_the_trials_about_the_topic(collection):
    result = retrieve(collection, TopicEmbedder(), "semaglutide", top_k=3, mode="vector")

    assert set(ids(result)[:2]) == {"NCT00000001", "NCT00000003"}
    best_chunk = result.trials[0].chunks[0]
    assert best_chunk.vector_rank == 1 and best_chunk.keyword_rank is None


@pytest.mark.integration
def test_keyword_search_finds_a_trial_by_its_id_which_meaning_search_cannot(collection):
    embedder = TopicEmbedder()

    by_keyword = retrieve(collection, embedder, "NCT00000002", top_k=3, mode="keyword")
    assert ids(by_keyword) == ["NCT00000002"]

    hybrid = retrieve(collection, embedder, "NCT00000002", top_k=3)
    assert ids(hybrid)[0] == "NCT00000002"
    assert hybrid.keyword_terms == ["nct00000002"]


@pytest.mark.integration
def test_a_trial_named_by_its_id_always_comes_first(collection):
    # "semaglutide" pulls the meaning search towards trials 1 and 3, yet the question names trial 2.
    # Without the rule, trial 2 only tied with the best unrelated vector hit.
    result = retrieve(collection, TopicEmbedder(), "semaglutide NCT00000002", top_k=1)
    assert ids(result) == ["NCT00000002"]

    lowercase = retrieve(collection, TopicEmbedder(), "tell me about nct00000002", top_k=1)
    assert ids(lowercase) == ["NCT00000002"]

    # An ID that doesn't exist in the data changes nothing and breaks nothing.
    unknown = retrieve(collection, TopicEmbedder(), "semaglutide NCT99999999", top_k=2)
    assert "NCT00000002" not in ids(unknown)[:1]


@pytest.mark.integration
def test_hybrid_finds_what_each_search_alone_would_miss(collection):
    # "semaglutide" points the meaning search at trials 1 and 3; only keyword finds the ID.
    result = retrieve(collection, TopicEmbedder(), "semaglutide NCT00000002", top_k=5)

    assert {"NCT00000001", "NCT00000002", "NCT00000003"} <= set(ids(result))


@pytest.mark.integration
def test_each_trial_appears_once_with_a_limited_number_of_chunks(collection):
    result = retrieve(collection, TopicEmbedder(), "semaglutide", top_k=5)

    assert len(ids(result)) == len(set(ids(result))) == 5
    assert all(1 <= len(t.chunks) <= CHUNKS_PER_TRIAL for t in result.trials)
    assert all(t.score == t.chunks[0].score for t in result.trials)


@pytest.mark.integration
def test_filters_are_exact(collection):
    embedder = TopicEmbedder()

    phase_2 = retrieve(collection, embedder, "semaglutide", top_k=5, phases=["PHASE2"])
    assert set(ids(phase_2)) == {"NCT00000002", "NCT00000003"}  # the only two PHASE2 trials

    recruiting = retrieve(collection, embedder, "semaglutide", top_k=5, statuses=["RECRUITING"])
    assert ids(recruiting) == ["NCT00000003"]

    both = retrieve(
        collection, embedder, "semaglutide", top_k=5, phases=["PHASE2"], statuses=["RECRUITING"]
    )
    assert ids(both) == ["NCT00000003"]

    by_keyword = retrieve(
        collection, embedder, "semaglutide", top_k=5, phases=["PHASE2"], mode="keyword"
    )
    assert ids(by_keyword) == ["NCT00000003"]  # filters apply to the keyword search too


@pytest.mark.integration
def test_common_and_meaningless_words_are_not_searched_for(collection):
    terms = distinctive_terms(
        collection, "Which filler trials test asthma in NCT00000002 zzzunknownword?"
    )

    assert "asthma" in terms and "nct00000002" in terms
    assert "filler" not in terms  # in a large share of all chunks: tells us nothing
    assert "which" not in terms  # a stop word
    assert "zzzunknownword" not in terms  # not in the data at all


@pytest.mark.integration
def test_asking_for_many_candidates_really_returns_that_many(collection):
    # Regression test for a trap we measured: with default settings the vector index returns at most
    # 40 rows however large the LIMIT is. We configure the search so that 60 means 60.
    vector = TopicEmbedder().embed(["semaglutide"])[0]
    total = collection.execute("SELECT count(*) FROM chunks").fetchone()[0]
    assert total > 100

    # On a table this small Postgres would skip the index and scan everything, which never shows
    # the trap. Forbidding that scan forces the index, like on the real, much larger table.
    collection.execute("SET enable_seqscan = off")
    try:
        assert len(vector_search(collection, vector, limit=60)) == 60
    finally:
        collection.execute("RESET enable_seqscan")


@pytest.mark.integration
def test_the_meaning_search_uses_the_vector_index(collection):
    # A query that can't use the index still returns correct results, only slowly (every chunk is
    # sorted). That is exactly the mistake a ", id" tie-breaker in ORDER BY causes, and nothing else
    # would notice it. So we ask Postgres for its plan, with full scans forbidden to make it choose.
    vector = TopicEmbedder().embed(["semaglutide"])[0]
    collection.execute("SET enable_seqscan = off")
    try:
        plan = collection.execute("EXPLAIN " + _vector_query(False, []), (Vector(vector), 10))
        assert "chunks_embedding_idx" in "\n".join(row[0] for row in plan)
    finally:
        collection.execute("RESET enable_seqscan")


@pytest.mark.integration
def test_an_empty_database_gives_an_empty_answer(conn):
    assert retrieve(conn, TopicEmbedder(), "anything at all").trials == []
