"""Find the trials that best answer a question.

Two searches run side by side, and their rankings are merged:
  * by meaning (vectors): finds chunks that talk about the same thing, whatever the words;
  * by keyword (Postgres full-text): finds exact terms like drug names, places and NCT IDs.
Then chunks are grouped into trials, because the question is about trials, not chunks.
See docs/05-retrieval.md.

Try it:  make search Q="Which trials test semaglutide in Thailand?"
"""

import argparse
import re
import time
from dataclasses import dataclass
from typing import Literal

import psycopg
from pgvector import Vector

from app import db
from app.config import get_settings
from app.providers.base import EmbeddingProvider
from app.providers.factory import get_embedder

Mode = Literal["hybrid", "vector", "keyword"]

CANDIDATES = 50  # how many chunks each search may contribute to the merge
RRF_K = 60  # the usual constant of Reciprocal Rank Fusion; see reciprocal_rank_fusion()
CHUNKS_PER_TRIAL = 3  # the best few chunks of a trial are kept as its evidence

# A word that appears in more than this share of all chunks cannot help find anything: "trial",
# "phase" and "study" are everywhere. Postgres ranks matches without noticing that, so we drop
# such words ourselves. Measured on our data in docs/05-retrieval.md.
KEYWORD_MAX_SHARE = 0.15

# A question that names a trial by its ID wants exactly that trial. Merged scores are all below
# 0.04, so this bonus always puts it first. Without it the exact match only tied with the best
# meaningless vector hit (docs/05-retrieval.md shows the tie).
NCT_ID = re.compile(r"NCT\d{8}", re.IGNORECASE)
ID_MATCH_BONUS = 1.0


@dataclass(frozen=True)
class RetrievedChunk:
    id: int
    nct_id: str
    section: str
    text: str
    score: float
    vector_rank: int | None  # position in the meaning search (1 = best), None if not found there
    keyword_rank: int | None  # same for the keyword search


@dataclass(frozen=True)
class RetrievedTrial:
    nct_id: str
    title: str
    phases: list[str]
    status: str | None
    score: float  # the score of its best chunk
    chunks: list[RetrievedChunk]  # best first


@dataclass(frozen=True)
class Retrieval:
    trials: list[RetrievedTrial]
    keyword_terms: list[str]  # the words the keyword search really used (it drops common ones)


def reciprocal_rank_fusion(rankings: list[list[int]], k: int = RRF_K) -> dict[int, float]:
    """Merge several ranked lists into one score per item.

    An item at position `rank` (1 = first) in a list earns 1 / (k + rank) from that list; its final
    score is the sum over all lists. So an item that appears near the top of *both* lists beats one
    that is first in only one, and a list that doesn't contain an item simply adds nothing.

    Why this and not "add the raw scores": a vector distance (0.22) and a text-match score (2.5)
    live on different scales and cannot be added. Ranks can. `k` softens the gap between
    neighbouring positions: with k = 60, first place is worth 1/61 and second 1/62, so no single
    list can dominate.
    """
    scores: dict[int, float] = {}
    for ranking in rankings:
        for rank, item in enumerate(ranking, start=1):
            scores[item] = scores.get(item, 0.0) + 1.0 / (k + rank)
    return scores


def group_by_trial(
    chunks: list[RetrievedChunk],
    meta: dict[str, tuple[str, list[str], str | None]],
    top_k: int,
    chunks_per_trial: int = CHUNKS_PER_TRIAL,
) -> list[RetrievedTrial]:
    """Turn best-first chunks into best-first trials, each with its best few chunks.

    Walking down the list, the first chunk of a trial fixes the trial's place (it is the best one);
    later chunks of the same trial are added as extra evidence. Once `top_k` trials are collected,
    chunks of any other trial are ignored.
    """
    grouped: dict[str, list[RetrievedChunk]] = {}
    for chunk in chunks:
        if chunk.nct_id not in grouped and len(grouped) == top_k:
            continue
        kept = grouped.setdefault(chunk.nct_id, [])
        if len(kept) < chunks_per_trial:
            kept.append(chunk)
    return [
        RetrievedTrial(nct_id, *meta[nct_id], score=kept[0].score, chunks=kept)
        for nct_id, kept in grouped.items()
    ]


def _filter_clauses(phases: list[str] | None, statuses: list[str] | None) -> tuple[list, list]:
    clauses, params = [], []
    if phases:
        clauses.append("t.phases && %s")  # && = "has at least one element in common"
        params.append(list(phases))
    if statuses:
        clauses.append("t.status = ANY(%s)")
        params.append(list(statuses))
    return clauses, params


def _vector_query(filtered: bool, clauses: list[str]) -> str:
    if filtered:
        return (
            "SELECT c.id FROM chunks c JOIN trials t USING (nct_id) WHERE "
            + " AND ".join(clauses)
            + " ORDER BY c.embedding <=> %s LIMIT %s"
        )
    # Ordered by the distance ALONE, on purpose: adding a tie-breaker such as ", id" makes
    # Postgres stop using the vector index and sort every chunk instead (we measured it).
    return "SELECT id FROM chunks ORDER BY embedding <=> %s LIMIT %s"


def vector_search(
    conn: psycopg.Connection,
    vector: list[float],
    limit: int = CANDIDATES,
    phases: list[str] | None = None,
    statuses: list[str] | None = None,
) -> list[int]:
    """Chunk IDs closest in meaning to `vector`, best first."""
    clauses, params = _filter_clauses(phases, statuses)
    sql = _vector_query(bool(clauses), clauses)

    with conn.transaction():
        # The HNSW index explores only `ef_search` candidates (default 40) and then stops, so
        # "LIMIT 100" quietly returned 40 rows when we measured it. Either of these two settings
        # alone fixes the count; we set both. A larger ef_search also widens the search, which finds
        # better neighbours. Iterative scanning keeps searching when a filter rejects candidates,
        # so a restrictive filter can't leave us with too few.
        conn.execute(
            "SELECT set_config('hnsw.ef_search', %s, true), "
            "set_config('hnsw.iterative_scan', 'relaxed_order', true)",
            (str(max(limit, 40)),),
        )
        return [row[0] for row in conn.execute(sql, [*params, Vector(vector), limit])]


def distinctive_terms(conn: psycopg.Connection, question: str) -> list[str]:
    """The words of the question that are worth searching for.

    Drops words that are too common to help (more than KEYWORD_MAX_SHARE of all chunks) and words
    that match nothing: stop words like "which" and "for", and words not in the data at all.
    """
    words = list(dict.fromkeys(w.lower() for w in re.findall(r"[A-Za-z0-9]+", question)))
    if not words:
        return []
    total = conn.execute("SELECT count(*) FROM chunks").fetchone()[0]
    rows = conn.execute(
        "SELECT w, (SELECT count(*) FROM chunks WHERE tsv @@ to_tsquery('english', w)) "
        "FROM unnest(%s::text[]) AS w",
        (words,),
    ).fetchall()
    counts = dict(rows)
    return [w for w in words if 0 < counts[w] <= KEYWORD_MAX_SHARE * total]


def keyword_search(
    conn: psycopg.Connection,
    terms: list[str],
    limit: int = CANDIDATES,
    phases: list[str] | None = None,
    statuses: list[str] | None = None,
) -> list[int]:
    """Chunk IDs containing any of `terms`, those with the most DIFFERENT terms first.

    Why not just Postgres's own ranking: it rewards repeating a word. An outcomes chunk that says
    "Phase 1 ... Phase 2 ... Phase 3 ..." ten times beat chunks that actually contained the rare
    word we cared about (seen on real data, docs/05-retrieval.md). So we count distinct matching
    terms first, a soft "must contain all the words", and use Postgres's rank only to order ties.
    """
    if not terms:
        return []
    clauses, params = _filter_clauses(phases, statuses)
    join = "JOIN trials t USING (nct_id)" if clauses else ""
    where = "".join(f" AND {clause}" for clause in clauses)
    # Each term is letters and digits only (see distinctive_terms), so these can't be malformed.
    distinct_matches = " + ".join(["(c.tsv @@ to_tsquery('english', %s))::int"] * len(terms))
    sql = (
        f"SELECT c.id FROM chunks c {join}, to_tsquery('english', %s) AS q "
        f"WHERE c.tsv @@ q{where} "
        f"ORDER BY ({distinct_matches}) DESC, ts_rank_cd(c.tsv, q) DESC, c.id LIMIT %s"
    )
    return [row[0] for row in conn.execute(sql, [" | ".join(terms), *params, *terms, limit])]


def retrieve(
    conn: psycopg.Connection,
    embedder: EmbeddingProvider,
    question: str,
    top_k: int | None = None,
    phases: list[str] | None = None,
    statuses: list[str] | None = None,
    mode: Mode = "hybrid",
) -> Retrieval:
    """The best `top_k` trials for the question, each with its best chunks.

    `phases` and `statuses` are exact filters ("PHASE3", "RECRUITING"). Nothing here reads them out
    of the question: plain retrieval treats "phase 3" as just more words, which is a weakness the
    evaluation phase will measure and the agent extension will fix (see docs/roadmap.md).
    `mode` lets you try each search alone, to see what it contributes.
    """
    top_k = top_k or get_settings().top_k

    vector_ids: list[int] = []
    keyword_ids: list[int] = []
    terms: list[str] = []
    if mode in ("hybrid", "vector"):
        vector = embedder.embed([question])[0]
        vector_ids = vector_search(conn, vector, CANDIDATES, phases, statuses)
    if mode in ("hybrid", "keyword"):
        terms = distinctive_terms(conn, question)
        keyword_ids = keyword_search(conn, terms, CANDIDATES, phases, statuses)

    scores = reciprocal_rank_fusion([r for r in (vector_ids, keyword_ids) if r])
    if not scores:
        return Retrieval(trials=[], keyword_terms=terms)

    rows = conn.execute(
        "SELECT c.id, c.nct_id, c.section, c.text, t.title, t.phases, t.status "
        "FROM chunks c JOIN trials t USING (nct_id) WHERE c.id = ANY(%s)",
        (list(scores),),
    ).fetchall()
    by_id = {row[0]: row for row in rows}

    named = {match.upper() for match in NCT_ID.findall(question)}
    for chunk_id in scores:
        if by_id[chunk_id][1] in named:
            scores[chunk_id] += ID_MATCH_BONUS
    ordered = sorted(scores, key=lambda chunk_id: (-scores[chunk_id], chunk_id))

    vector_rank = {chunk_id: rank for rank, chunk_id in enumerate(vector_ids, start=1)}
    keyword_rank = {chunk_id: rank for rank, chunk_id in enumerate(keyword_ids, start=1)}

    chunks = [
        RetrievedChunk(
            id=chunk_id,
            nct_id=by_id[chunk_id][1],
            section=by_id[chunk_id][2],
            text=by_id[chunk_id][3],
            score=scores[chunk_id],
            vector_rank=vector_rank.get(chunk_id),
            keyword_rank=keyword_rank.get(chunk_id),
        )
        for chunk_id in ordered
    ]
    meta = {row[1]: (row[4], row[5], row[6]) for row in rows}
    return Retrieval(trials=group_by_trial(chunks, meta, top_k), keyword_terms=terms)


def main() -> None:
    parser = argparse.ArgumentParser(description="Search the ingested trials.")
    parser.add_argument("question")
    parser.add_argument("--top-k", type=int, help="how many trials to show (default: TOP_K)")
    parser.add_argument("--phase", action="append", help="exact filter, e.g. PHASE3 (repeatable)")
    parser.add_argument("--status", action="append", help="exact filter, e.g. RECRUITING")
    parser.add_argument("--mode", choices=["hybrid", "vector", "keyword"], default="hybrid")
    args = parser.parse_args()

    started = time.perf_counter()
    try:
        with db.connect() as conn:
            result = retrieve(
                conn, get_embedder(), args.question, args.top_k, args.phase, args.status, args.mode
            )
    except psycopg.OperationalError as exc:
        raise SystemExit(
            f"Cannot connect to Postgres. Start it with `make db-up`.\n({exc})"
        ) from exc
    elapsed = time.perf_counter() - started

    print(f"mode={args.mode}  keyword terms: {result.keyword_terms or '-'}")
    for position, trial in enumerate(result.trials, start=1):
        phases = "/".join(trial.phases) or "NA"
        print(f"\n{position}. {trial.nct_id}  [{phases}, {trial.status}]  score {trial.score:.4f}")
        print(f"   {trial.title[:110]}")
        for chunk in trial.chunks:
            found = f"vector #{chunk.vector_rank or '-'}, keyword #{chunk.keyword_rank or '-'}"
            print(f"     - {chunk.section:<11} {found}")
    print(f"\n{len(result.trials)} trials in {elapsed:.1f}s")


if __name__ == "__main__":
    main()
