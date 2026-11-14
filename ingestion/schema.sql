-- The database layout. Safe to run again and again: every statement says IF NOT EXISTS.
-- Explained in docs/04-the-database.md. Applied by `make db-init` (app/db.py).

-- pgvector adds the `vector` column type and the "how close are these two vectors" operators.
CREATE EXTENSION IF NOT EXISTS vector;

-- One row per clinical trial. The columns we filter on are real columns; the whole original
-- record is kept in `raw` so we can re-chunk without downloading again.
CREATE TABLE IF NOT EXISTS trials (
    nct_id         text PRIMARY KEY,
    title          text NOT NULL,
    official_title text,
    status         text,
    study_type     text,
    phases         text[] NOT NULL DEFAULT '{}',   -- a trial can span phases: {PHASE2,PHASE3}
    start_date     text,                           -- the source has partial dates like "2011-05"
    conditions     text[] NOT NULL DEFAULT '{}',
    interventions  text[] NOT NULL DEFAULT '{}',
    sponsor        text,
    raw            jsonb NOT NULL,
    -- Fingerprint of the source record. Ingestion skips trials whose fingerprint didn't change.
    content_hash   text NOT NULL,
    updated_at     timestamptz NOT NULL DEFAULT now()
);

-- One row per searchable piece of a trial. Search runs on this table, not on `trials`.
CREATE TABLE IF NOT EXISTS chunks (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    -- ON DELETE CASCADE: removing a trial removes its chunks, so none are left orphaned.
    nct_id      text NOT NULL REFERENCES trials (nct_id) ON DELETE CASCADE,
    section     text NOT NULL,                     -- summary, description, eligibility, outcomes
    chunk_index int NOT NULL DEFAULT 0,            -- a long section is split into several chunks
    text        text NOT NULL,
    -- 1024 numbers per vector, locally and on AWS. If you change this, change EMBEDDING_DIM too
    -- (tests/test_db.py checks that the two agree).
    embedding   vector(1024) NOT NULL,
    -- Keyword-search index entries, kept in sync by Postgres itself (a "generated column"),
    -- so no application code can forget to update it.
    tsv         tsvector GENERATED ALWAYS AS (to_tsvector('english', text)) STORED,
    -- Also gives us a fast lookup by nct_id, so no separate index is needed for that.
    UNIQUE (nct_id, section, chunk_index)
);

-- Finds the nearest vectors without comparing against every row (approximate, much faster).
-- vector_cosine_ops = compare by angle, which suits text embeddings.
CREATE INDEX IF NOT EXISTS chunks_embedding_idx ON chunks USING hnsw (embedding vector_cosine_ops);

-- Finds the rows containing given words without reading every row.
CREATE INDEX IF NOT EXISTS chunks_tsv_idx ON chunks USING gin (tsv);
