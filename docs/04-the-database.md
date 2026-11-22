# 04 - The database

## What we're doing

Setting up the place where everything is stored and searched: a **Postgres** database with the
**pgvector** add-on, running in Docker. We create two tables, `trials` and `chunks`, and the indexes that
make searching them fast.

Nothing is loaded into it yet. Step 05 fills it. This step builds the shelves, and checks with real
(throwaway) rows that both kinds of search work.

The pieces:

| File | Role |
|---|---|
| `docker-compose.yml` | Starts Postgres (with pgvector) in a container |
| `ingestion/schema.sql` | The tables and indexes, written in SQL |
| `app/db.py` | Creates the schema from Python, opens connections, reports status |
| `tests/test_db.py` | Checks the layout and the searches against a real Postgres |

## Why

- **We need to search by meaning *and* by words.** Embeddings find chunks that *mean* the same as the question
  ("which drug helps people lose weight?" finds a semaglutide trial). Keyword search finds exact words
  (drug names, NCT IDs). Postgres can do both in one place: pgvector for meaning, Postgres's built-in
  **full-text search** for words. That is one database to run instead of two.
- **Why Postgres and not a dedicated vector database?** It is the same Postgres you would use for any
  application, it runs on AWS as a managed service, and we can mix vector search with normal filters
  ("only phase 3") in one query.
- **Why the layout matters.** Getting the layout right now (what is a column, what is indexed, what happens
  when a trial is deleted) avoids painful rewrites once thousands of rows exist.

## How it works

### Postgres, SQL and pgvector

**Postgres** is a database: it stores data in **tables** (like spreadsheets: **columns** are the fields,
**rows** are the records). You talk to it in **SQL**, a language for asking for rows
(`SELECT ... FROM ... WHERE ...`). **pgvector** teaches Postgres a new column type, `vector(1024)`, and
a way to ask "which stored vectors are closest to this one?".

### Docker Compose

`docker-compose.yml` describes the container in a file instead of a long command:

- **`image: pgvector/pgvector:pg16`**: Postgres 16 with pgvector already installed.
- **`ports: "5432:5432"`**: makes the database reachable from your Mac at `localhost:5432`. That is
  the address in `DATABASE_URL`.
- **`volumes: pgdata`**: a **volume** is storage that lives outside the container. Without it, the
  data would vanish whenever the container is recreated. With it, `make db-down` and `make db-up` keep your data.
- **`healthcheck`**: Postgres takes a few seconds to start. The healthcheck runs `pg_isready` until it answers,
  and `docker compose up --wait` (what `make db-up` uses) doesn't return until it does, so nothing tries
  to connect too early.
- The user, password and database name are all `rag`/`rag`/`trials`. These are for **local development only**.
  AWS will use real secrets.

### The two tables

```
trials                              chunks
------                              ------
nct_id  (the ID, unique)  <-------  nct_id   (which trial this piece belongs to)
title, status, phases, ...          section  (summary, eligibility, ...)
conditions, interventions           chunk_index
sponsor                             text
raw (the original JSON)             embedding  vector(1024)   <- search by meaning
content_hash                        tsv        (word index)   <- search by words
```

**`trials`** has one row per trial, holding the facts we may *filter* on (status, phase, sponsor) and
the whole original record (`raw`, a **jsonb** column, which stores JSON in a form Postgres can search).
Keeping `raw` means we can change how we split trials without downloading them again.

**`chunks`** has one row per searchable piece. **Search happens here.** A trial becomes several chunks
(its summary, its eligibility criteria, ...). Each chunk has its text, its embedding, and a word index.

Two tables, not one, because a trial's facts exist once, but it has many chunks. This avoids copying
the title and sponsor onto every chunk.

Details worth knowing:

- **`nct_id` is the primary key** of `trials`: no two rows may have the same one. In `chunks`, `nct_id`
  is a **foreign key**: it must match an existing trial. Postgres refuses a chunk that points nowhere.
- **`ON DELETE CASCADE`**: delete a trial and its chunks go with it, so there are never orphans.
- **`UNIQUE (nct_id, section, chunk_index)`**: the same piece can't be stored twice. It also gives a fast
  lookup of a trial's chunks.
- **`content_hash`** is a fingerprint of the source record. In Step 05, a trial whose fingerprint hasn't
  changed is skipped, which makes re-running ingestion cheap and safe.
- **`phases` is a list** (`text[]`), because a trial can be "phase 2/3".
- **`vector(1024) NOT NULL`**: exactly 1024 numbers, matching `EMBEDDING_DIM`. A vector of any other
  size is rejected. The test `test_schema_vector_size_matches_the_embedding_dim_setting` fails if the SQL and the setting disagree.
- **`tsv` is a generated column**: Postgres computes it from `text` by itself, and recomputes it when the
  text changes. Application code can't forget to keep it in sync.

### The two indexes

An **index** is a side structure that lets the database find rows without reading every one (like a book's
index). Without them, every search would compare against every chunk.

- **`chunks_embedding_idx`: HNSW** (Hierarchical Navigable Small World). It organises vectors as a
  web of "nearby" links, so a search hops towards the closest vectors instead of checking all of them.
  The answer is *approximate* (it can occasionally miss the true closest), in exchange for being much faster.
  `vector_cosine_ops` means closeness is measured by angle (**cosine distance**), the usual choice for text
  embeddings. Cosine **distance** is 1 minus the cosine **similarity** from the providers guide, so *smaller
  means closer*.
- **`chunks_tsv_idx`: GIN** (Generalized Inverted Index). Like the index at the back of a book: for each word, the
  list of chunks that contain it.

### Full-text search in Postgres

`to_tsvector('english', text)` breaks text into words and **stems** them: it reduces them to a root so
word forms match ("obesity" and "obese" both become `obes`, "drugs" becomes `drug`). Drug names and
NCT IDs stay intact (`semaglutid`, `nct04998136`). `websearch_to_tsquery('english', 'GLP-1 drugs')` turns a
search phrase into a query the same way, and the operator `@@` tests "does this chunk match?".

### Creating the tables from Python (not from Docker)

The Postgres image can run `.sql` files automatically, but **only the very first time its data folder is
empty**. Change the file later and nothing happens, which is a confusing trap. So `app/db.py` applies
`schema.sql` explicitly (`make db-init`). Every statement says `IF NOT EXISTS`, so running it twice is harmless,
and the same command later sets up the AWS database.

### Tests that use a real database

`tests/test_db.py` runs against a real Postgres, because the things worth checking (vector distances, stemming,
cascade deletes, size limits) are Postgres behaviours that a pretend database can't reproduce. These are
**integration tests**: they test several parts working together. They use a **separate database** called
`trials_test`, so they never touch the data you ingest into `trials`. If Postgres isn't running they are
**skipped** with a message, not failed.

## Try it yourself

Docker Desktop must be running. If your terminal says `docker: command not found`, see the first entry of
"Common problems" in the [setup guide](01-setup.md).

### 1. Start Postgres

```bash
make db-up
```

Expected: ends with `Container clinical-trials-rag-postgres-1  Healthy`. The first time it also downloads the
image, which takes a minute.

```bash
docker compose ps
```

Expected: one row, `postgres`, with `Up ... (healthy)` and `0.0.0.0:5432->5432/tcp`.

### 2. Create the tables

```bash
make db-init
```

Expected:

```
Schema is ready (tables: trials, chunks).
postgres  16.16 (Debian 16.16-1.pgdg12+1)
pgvector  0.8.7
trials    0
chunks    0
indexes   ['chunks_embedding_idx', 'chunks_nct_id_section_chunk_index_key', 'chunks_pkey', 'chunks_tsv_idx']
```

(Version numbers may differ slightly.) Run it again: same output, no error.

### 3. Look at the table

```bash
docker compose exec -T postgres psql -U rag trials -c '\d chunks'
```

Expected: the columns of `chunks` (including `embedding | vector(1024)` and a generated `tsv`), then the
`Indexes:` list with `hnsw (embedding vector_cosine_ops)` and `gin (tsv)`, and the foreign key with `ON DELETE CASCADE`.

For an interactive SQL prompt, use `make db-psql` (type `\q` to leave).

### 4. Search by meaning and by words

This loads three made-up trials with **real embeddings** from Ollama, searches them both ways, and then
**rolls back** (throws away) the rows, so the database stays empty for Step 05. Ollama must be running.

```bash
uv run python - <<'EOF'
from pgvector import Vector

from app.db import connect
from app.providers.factory import get_embedder

docs = [
    ("NCT00000001", "Semaglutide 2.4 mg for weight loss in adults with obesity"),
    ("NCT00000002", "Inhaled corticosteroid for asthma in children"),
    ("NCT00000003", "Tirzepatide versus placebo in type 2 diabetes"),
]
embedder = get_embedder()
vectors = embedder.embed([text for _, text in docs])

with connect() as conn:
    for (nct_id, text), vector in zip(docs, vectors):
        conn.execute(
            "INSERT INTO trials (nct_id, title, raw, content_hash) VALUES (%s, %s, '{}', 'demo')",
            (nct_id, text),
        )
        conn.execute(
            "INSERT INTO chunks (nct_id, section, text, embedding) VALUES (%s, 'summary', %s, %s)",
            (nct_id, text, Vector(vector)),
        )

    question = "which drug helps people lose weight?"
    query_vector = Vector(embedder.embed([question])[0])
    print(f"Search by meaning: {question}")
    rows = conn.execute(
        "SELECT nct_id, text, embedding <=> %s AS distance FROM chunks ORDER BY distance",
        (query_vector,),
    )
    for nct_id, text, distance in rows:
        print(f"  {distance:.2f}  {nct_id}  {text}")

    for word in ("tirzepatide", "NCT00000002", "weight"):
        rows = conn.execute(
            "SELECT nct_id FROM chunks WHERE tsv @@ websearch_to_tsquery('english', %s)", (word,)
        )
        print(f"Search by keyword {word!r}: {[r[0] for r in rows]}")

    conn.rollback()  # the demo rows are thrown away
print("rows left after rollback:", connect().execute("SELECT count(*) FROM chunks").fetchone()[0])
EOF
```

Expected (the distances may differ by a few hundredths):

```
Search by meaning: which drug helps people lose weight?
  0.33  NCT00000001  Semaglutide 2.4 mg for weight loss in adults with obesity
  0.46  NCT00000003  Tirzepatide versus placebo in type 2 diabetes
  0.58  NCT00000002  Inhaled corticosteroid for asthma in children
Search by keyword 'tirzepatide': ['NCT00000003']
Search by keyword 'NCT00000002': []
Search by keyword 'weight': ['NCT00000001']
rows left after rollback: 0
```

Read the output carefully, because it teaches two things:

- **Meaning search ranks everything**, closest first. The question never used the word "semaglutide" or
  "obesity", yet the weight-loss trial wins (0.33, the smallest distance), the diabetes drug comes second, the
  asthma inhaler last.
- **Keyword search returns only exact matches**, and it found nothing for `NCT00000002` even though that
  trial exists. The reason: the ID is stored in the `trials` row, but it is **not in the chunk's text**, which is
  what keyword search reads. This is why Step 05 will start every chunk with `NCT ID | title | phase`:
  so people can find a trial by its ID, and embeddings carry the trial's identity.

Neither search is best at everything. Step 06 combines them (**hybrid search**).

### 5. Run the tests

```bash
make test
```

Expected: `30 passed` with Postgres running (the number grows with every step).

Now stop the database and run them again:

```bash
make db-down
uv run pytest -rs
```

Expected: ends with lines like `SKIPPED [1] tests/test_db.py:120: Postgres is not running (start it with `make db-up`)` and
`23 passed, 7 skipped`. Nothing fails: the tests that need the database say so and step aside.

```bash
make db-status
```

Expected: `Cannot connect to Postgres at postgresql://rag:rag@localhost:5432/trials` and
`Is it running? Start it with `make db-up`.`

Start it again with `make db-up`.

### 6. Start over from an empty database

```bash
make db-reset
```

**This deletes all data in the database**, which is why it is a separate, clearly named command. Expected: the
container restarts and the same output as in step 2, with 0 trials and 0 chunks. Use it when you want a clean slate,
for example after changing the embedding model.

## Common problems

- **`docker: command not found`**: see the setup guide: Docker Desktop didn't link its commands onto your PATH.
- **`port is already allocated` / `address already in use` on 5432**: another Postgres is already using
  that port. Stop it, or change the left number in `ports:` (for example `"5433:5432"`) and put `5433` in `DATABASE_URL`.
- **`Cannot connect to Postgres ... Is it running?`**: run `make db-up` (and make sure Docker Desktop is open).
- **`The tables don't exist yet`**: you started Postgres but never created the tables. Run `make db-init`.
- **`expected 1024 dimensions, not N`**: you tried to store a vector of the wrong size. Check `EMBEDDING_MODEL` and
  `EMBEDDING_DIM`. If you really want a different size, change `vector(1024)` in `schema.sql`, set
  `EMBEDDING_DIM` to match, and run `make db-reset`. Vectors from different models must never share a table.
- **My data disappeared**: `make db-reset` and `docker compose down -v` delete the volume on purpose. `make db-down`
  and restarting Docker keep it.
- **Edited `schema.sql` but nothing changed**: `IF NOT EXISTS` skips things that already exist, so it won't
  alter existing tables. While the project is young, `make db-reset` applies the new layout from scratch.
- **Tests were skipped and I don't know why**: run `uv run pytest -rs` to see the reasons.
