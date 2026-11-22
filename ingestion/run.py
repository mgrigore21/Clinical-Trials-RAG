"""Load the downloaded trials into the database: chunk them, embed the chunks, store everything.

Run it:  uv run python -m ingestion.run            (all downloaded trials)
         uv run python -m ingestion.run --sample   (the small sample, about a minute)

Safe to run again: trials that haven't changed are skipped, so an interrupted run simply
continues where it stopped. See docs/03-chunking-and-embeddings.md.
"""

import argparse
import hashlib
import json
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import psycopg
from pgvector import Vector
from psycopg.types.json import Jsonb

from app import db
from app.config import get_settings
from app.providers.base import EmbeddingProvider, ProviderError
from app.providers.factory import get_embedder
from ingestion.chunk import CHUNKING_VERSION, Chunk, chunk_trial
from ingestion.fetch import load_cached_trials
from ingestion.parse import trial_fields

EMBED_BATCH = 32  # texts per embedding request: one request per text would be much slower
TRIALS_PER_COMMIT = 10  # how many trials are saved together; see store_trials()


@dataclass(frozen=True)
class IngestResult:
    ingested: int  # trials newly written or rewritten
    skipped: int  # trials that were already up to date
    chunks: int  # chunks written


def content_hash(study: dict, embedding_model: str) -> str:
    """A fingerprint of everything that determines what we store for a trial.

    It covers the record itself, the chunking rules and the embedding model, so changing any of
    them makes the trial look "changed" and it is processed again. Without the last two, improving
    the chunker would silently leave the database full of old chunks. (Same record, same rules,
    same model gives the same fingerprint: JSON keys are sorted so their order can't matter.)
    """
    record = json.dumps(study, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(f"{CHUNKING_VERSION}|{embedding_model}|{record}".encode()).hexdigest()


def embed_all(embedder: EmbeddingProvider, texts: list[str], batch_size: int) -> list[list[float]]:
    vectors = []
    for start in range(0, len(texts), batch_size):
        vectors.extend(embedder.embed(texts[start : start + batch_size]))
    return vectors


def store_trials(
    conn: psycopg.Connection,
    batch: list[tuple[dict, str]],
    chunks_by_trial: list[list[Chunk]],
    vectors: list[list[float]],
) -> None:
    """Write a few trials and their chunks in ONE transaction: all of it, or none of it.

    A trial's row carries its fingerprint, and the fingerprint is how a re-run knows the trial is
    done. Because the row and its chunks are saved together, a crash can never leave a trial that
    looks finished but has missing chunks.
    """
    try:
        vector_iter = iter(vectors)
        for (study, fingerprint), chunks in zip(batch, chunks_by_trial, strict=True):
            fields = trial_fields(study)
            conn.execute(
                """
                INSERT INTO trials (
                    nct_id, title, official_title, status, study_type, phases, start_date,
                    conditions, interventions, sponsor, raw, content_hash
                )
                VALUES (%(nct_id)s, %(title)s, %(official_title)s, %(status)s, %(study_type)s,
                        %(phases)s, %(start_date)s, %(conditions)s, %(interventions)s, %(sponsor)s,
                        %(raw)s, %(content_hash)s)
                ON CONFLICT (nct_id) DO UPDATE SET
                    title = EXCLUDED.title, official_title = EXCLUDED.official_title,
                    status = EXCLUDED.status, study_type = EXCLUDED.study_type,
                    phases = EXCLUDED.phases, start_date = EXCLUDED.start_date,
                    conditions = EXCLUDED.conditions, interventions = EXCLUDED.interventions,
                    sponsor = EXCLUDED.sponsor, raw = EXCLUDED.raw,
                    content_hash = EXCLUDED.content_hash, updated_at = now()
                """,
                {**fields, "raw": Jsonb(study), "content_hash": fingerprint},
            )
            # A changed trial may now produce fewer chunks than before, so replace the old set
            # instead of updating row by row.
            conn.execute("DELETE FROM chunks WHERE nct_id = %s", (fields["nct_id"],))
            with conn.cursor() as cursor:
                cursor.executemany(
                    "INSERT INTO chunks (nct_id, section, chunk_index, text, embedding) "
                    "VALUES (%s, %s, %s, %s, %s)",
                    [
                        (c.nct_id, c.section, c.chunk_index, c.text, Vector(next(vector_iter)))
                        for c in chunks
                    ],
                )
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def ingest(
    trials: dict[str, dict],
    conn: psycopg.Connection,
    embedder: EmbeddingProvider,
    embedding_model: str,
    trials_per_commit: int = TRIALS_PER_COMMIT,
    embed_batch: int = EMBED_BATCH,
    log=print,
) -> IngestResult:
    stored = dict(conn.execute("SELECT nct_id, content_hash FROM trials").fetchall())
    conn.commit()  # end the read's transaction, so each store_trials() starts clean

    todo = []
    for nct_id, study in trials.items():
        fingerprint = content_hash(study, embedding_model)
        if stored.get(nct_id) != fingerprint:
            todo.append((study, fingerprint))
    skipped = len(trials) - len(todo)
    log(f"{len(todo)} trials to ingest, {skipped} already up to date")

    started = time.perf_counter()
    chunk_count = 0
    for start in range(0, len(todo), trials_per_commit):
        batch = todo[start : start + trials_per_commit]
        chunks_by_trial = [chunk_trial(study) for study, _ in batch]
        texts = [chunk.text for chunks in chunks_by_trial for chunk in chunks]

        # The slow part (the embedding model) happens outside any database transaction.
        vectors = embed_all(embedder, texts, embed_batch)
        if len(vectors) != len(texts):
            raise ProviderError(
                f"The model returned {len(vectors)} vectors for {len(texts)} texts."
            )
        store_trials(conn, batch, chunks_by_trial, vectors)

        chunk_count += len(texts)
        done = start + len(batch)
        if done == len(todo) or (start // trials_per_commit) % 10 == 9:
            rate = done / (time.perf_counter() - started)
            remaining = (len(todo) - done) / rate
            log(f"  {done}/{len(todo)} trials, {chunk_count} chunks, about {remaining:.0f}s left")

    return IngestResult(ingested=len(todo), skipped=skipped, chunks=chunk_count)


def main() -> None:
    parser = argparse.ArgumentParser(description="Chunk, embed and store the downloaded trials.")
    parser.add_argument("--sample", action="store_true", help="use the small sample download")
    args = parser.parse_args()
    # Show progress immediately even when output is redirected to a file or another program
    # (Python otherwise holds it back until a buffer fills, which looks like a hang).
    sys.stdout.reconfigure(line_buffering=True)

    settings = get_settings()
    raw_dir = Path(settings.data_dir) / ("raw-sample" if args.sample else "raw")
    trials = load_cached_trials(raw_dir)
    if not trials:
        raise SystemExit(
            f"No trials found in {raw_dir}/. Download them first: "
            f"`{'make fetch-sample' if args.sample else 'make fetch'}`."
        )

    started = time.perf_counter()
    try:
        with db.connect() as conn:
            result = ingest(trials, conn, get_embedder(), settings.embedding_model)
    except psycopg.OperationalError as exc:
        raise SystemExit(
            f"Cannot connect to Postgres. Start it with `make db-up`.\n({exc})"
        ) from exc
    except psycopg.errors.UndefinedTable as exc:
        raise SystemExit("The tables don't exist yet. Run `make db-init` first.") from exc
    except ProviderError as exc:
        raise SystemExit(
            f"FAILED: {exc}\nTrials saved so far are kept; run the same command again to continue."
        ) from exc

    print(
        f"Done in {time.perf_counter() - started:.0f}s: {result.ingested} trials ingested "
        f"({result.chunks} chunks), {result.skipped} skipped as unchanged."
    )


if __name__ == "__main__":
    main()
