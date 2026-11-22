# 03 - Chunking and embeddings (ingestion)

(Built in Step 05. The number `03` is its place in the final reading order; see the
[roadmap](roadmap.md).)

## What we're doing

Turning the 3,408 downloaded trials into something searchable. For every trial we:

1. **Chunk** it: cut it into small, labelled pieces (summary, eligibility rules, outcomes, ...).
2. **Embed** each chunk: have the embedding model turn its text into 1024 numbers.
3. **Store** the trial, its chunks and their embeddings in Postgres.

Doing all this is called **ingestion**. Afterwards the database is ready to be searched (Step 06).

| File | Role |
|---|---|
| `ingestion/parse.py` | Digs the handful of facts we need out of the nested raw record |
| `ingestion/chunk.py` | Cuts one trial into chunks |
| `ingestion/run.py` | The pipeline: chunk, embed, store, skip what is already done |

## Why

- **Why not embed whole trials?** A trial mixes sponsor, summary, eligibility rules and outcomes.
  One vector for all of it is a blurry average of everything, and a question about one detail
  ("which trials exclude pregnant women?") barely moves it. A chunk about *one thing* gets a vector
  that is clearly about that thing.
- **Why not make chunks tiny?** A sentence on its own loses its context. It also means thousands of
  extra vectors to store and search.
- **There is a hard upper limit anyway.** The embedding model reads at most 512 tokens (about
  1,660 characters of trial text), as measured in the [providers guide](07-providers.md). Anything longer
  would fail, so chunks have to fit.
- **Why label every chunk with the trial?** A chunk saying "Pregnancy; type 1 diabetes" is useless on
  its own: whose exclusion list is it? So every chunk starts with `NCT ID | title | phase`.
  Two payoffs: the embedding "knows" which trial it belongs to, and keyword search can find a trial by its ID
  (the [database guide](04-the-database.md) showed that this was impossible without it).
- **Why make re-running safe?** Embedding takes minutes, and things go wrong: Ollama gets closed, the laptop sleeps,
  you improve the chunker. We want "run it again" to be always safe and to only do the missing work.

## How it works

### The anatomy of a chunk

Here is one real trial (NCT04998136, semaglutide for obesity in Asian adults), as 8 chunks:

```
section      index  chars  label
summary          0   1308  Summary
summary          1    497  Summary
eligibility      0    441  Eligibility: inclusion criteria
eligibility      1    665  Eligibility: exclusion criteria
outcomes         0   1394  Outcomes: primary
outcomes         1    855  Outcomes: primary
outcomes         2    866  Outcomes: primary
outcomes         3   1230  Outcomes: secondary
```

And this is the first chunk, *exactly* as it is embedded and stored:

```
NCT04998136 | Research Study Investigating How Well Semaglutide Works in People From Thailand and South Korea Living With Obesity | PHASE3
Summary
Official title: Efficacy and Safety of Semaglutide 2.4 mg Once-weekly in Asians With Obesity Diagnosed as BMI ≥ 25 kg/m2 According to Local Guidelines
Status: COMPLETED. Study type: INTERVENTIONAL. Start date: 2022-08-15. Sponsor: Novo Nordisk A/S.
Conditions: Obesity
Interventions: Semaglutide 2.4 mg (DRUG); Placebo (semaglutide 2.4 mg) (DRUG)
This study looks at how well semaglutide helps people lose weight. ...
```

- **Line 1, the header**: `NCT ID | title | phase`. The same on every chunk of the trial. A very long title is cut
  to 150 characters so it can't crowd out the content.
- **Line 2, the label**: which part of the trial this is.
- **The rest**: the content.

### The four sections

| Section | Built from | Notes |
|---|---|---|
| `summary` | facts (status, sponsor, conditions, interventions) + the brief summary | Every trial has one. The facts are put here so a question like "Novo Nordisk trials for obesity" has something to match |
| `description` | the detailed description | **Only about half of the trials have one.** If missing, there is simply no such chunk |
| `eligibility` | the inclusion and exclusion criteria, plus sex and age limits | Split into **two parts** at the words "Exclusion Criteria" (98% of trials have them) |
| `outcomes` | primary outcomes, then secondary outcomes | Split into two labelled parts as well |

Why split inclusion from exclusion? Because "Pregnancy" means opposite things in the two lists. A chunk of
bullet points with no heading would be ambiguous, to the embedding model as much as to a human. So each part gets
its own label, and the label is repeated on **every** chunk of that part, even when a long list spills over.

### Cutting long text: `split_text`

Some sections are far too long for one chunk (the longest description in our data has 23,000
characters). `split_text` cuts at the most natural place available:

1. **Between lines** first. Eligibility criteria are one per line, so they split cleanly.
2. If a single line is too long, **between sentences**.
3. Only as a last resort, **between words**. (31 trials have eligibility lines over 1,400 characters.)

Pieces are then packed greedily: add lines to the current chunk until the next one would not fit, then start a new
one. This gives a few full chunks instead of many tiny ones. Every chunk is at most **1,400 characters** (header
included). That is roughly 320 tokens on average and about 425 at worst in what we measured, safely below the
512-token limit.

The pieces of a section are numbered 0, 1, 2, ... (`chunk_index`). Together with the trial and the section, that
is the unique key you saw in the database schema.

One deliberate simplification: chunks **don't overlap**. Some systems repeat the last sentences of one chunk at the
start of the next so nothing falls in a gap. Our labelled header already carries the context, and overlap makes
chunks bigger and more numerous. If the evaluation phase shows that answers suffer at chunk boundaries, adding overlap
is the first thing to try.

### Embedding

`run.py` sends the chunk texts to the embedding provider (Step 03) **32 at a time**. A request per text would be far
slower. (We measured batch sizes from 8 to 128: no difference, about 15 to 25 chunks per second. The model itself is
the bottleneck, so 32 is just a comfortable size.) Each chunk text must be under the model's limit, otherwise the
provider raises an error instead of silently cutting it, as explained in the providers guide.

### Storing: one transaction per few trials

The slow part (embedding) happens first, outside the database. Then `store_trials` writes 10 trials at a time, **in
one transaction**.

A **transaction** is a group of database changes that are saved together (**commit**) or not at all (**rollback**).
Here is why that matters. The `trials` row carries the trial's fingerprint, and the fingerprint is how a re-run knows
a trial is finished. Because the trial row and all of its chunks are written in the same transaction, a crash can
never leave a trial that *looks* finished but has chunks missing. Either all of it is in the database or none of it is.
(The test `test_a_failed_write_rolls_back_the_whole_batch` proves this by making the write fail half way.)

For a trial that already exists, its old chunks are **deleted and re-inserted**, not updated one by one, because the new
version may have fewer chunks than the old one.

### Re-running safely: the fingerprint

Each trial gets a **fingerprint** (a **hash**): a short code computed from content, which changes if the content changes
at all. We use SHA-256. It is stored in `trials.content_hash`. On every run, a trial whose fingerprint is already in the
database is **skipped**.

The fingerprint covers three things: the raw record, `CHUNKING_VERSION` in `chunk.py`, and the embedding model's
name. So the trial is processed again when:

- ClinicalTrials.gov changed the trial (after a `make fetch --refresh`),
- we change the chunking rules and bump `CHUNKING_VERSION`, or
- we switch embedding model (vectors from two different models must never be mixed in one table).

Without the last two, improving the chunker would leave the database quietly full of chunks made by the old rules.

An operation that gives the same result however many times you run it is called **idempotent**. Ingestion is:
the first run does the work, the second finds nothing to do. And an interrupted run just continues, because every
finished batch is already saved.

Not handled: a trial that disappears from the downloaded data stays in the database. Ingestion adds and updates, it
doesn't delete. `make db-reset` followed by `make ingest` gives a clean rebuild.

### Tests

- `tests/test_chunk.py` (no database or model needed): the splitting rules, the header on every chunk, missing sections,
  very long titles, and a **safety net over all the real downloaded trials** that checks every chunk fits.
- `tests/test_ingest.py` (real Postgres, **fake embedding model**): rows and chunks are stored, trials are findable by ID,
  a second run does nothing, a changed trial is rewritten, a crash keeps finished trials and leaves no half-written one.

The fake embedder (`tests/fakes.py`) returns a fixed vector per text. It needs no Ollama, so these tests are instant, and
the database side is still the real thing.

## Try it yourself

You need Docker running with Postgres up (`make db-up`, then `make db-init`), Ollama running, and the downloaded trials
(`make fetch`, from [guide 02](02-getting-the-data.md)).

### 1. Look at the chunks of one trial (no database needed)

```bash
uv run python - <<'EOF'
from pathlib import Path

from ingestion.chunk import chunk_trial
from ingestion.fetch import load_cached_trials

trials = load_cached_trials(Path("data/raw"))
chunks = chunk_trial(trials["NCT04998136"])
print(f"{len(chunks)} chunks for NCT04998136\n")
print(f"{'section':<12} {'index':>5} {'chars':>6}  label")
for c in chunks:
    print(f"{c.section:<12} {c.chunk_index:>5} {len(c.text):>6}  {c.text.splitlines()[1]}")
print()
print(chunks[0].text)
EOF
```

Expected: the table shown above (8 chunks; the sizes may differ slightly if ClinicalTrials.gov edited the trial since), followed by the
first chunk's text, starting with `NCT04998136 | Research Study Investigating ...`.

### 2. Ingest the small sample

```bash
make fetch-sample
make ingest-sample
```

Expected (the timings vary with your Mac; this took 80 seconds on ours):

```
163 trials to ingest, 0 already up to date
  100/163 trials, 778 chunks, about 34s left
  163/163 trials, 1174 chunks, about 0s left
Done in 80s: 163 trials ingested (1174 chunks), 0 skipped as unchanged.
```

(If you already ran the full ingestion, it says `0 trials to ingest, 163 already up to date` instead. That is the
next step in action.)

### 3. Run it again: nothing to do

```bash
make ingest-sample
```

Expected:

```
0 trials to ingest, 163 already up to date
Done in 0s: 0 trials ingested (0 chunks), 163 skipped as unchanged.
```

It finished instantly and never called the embedding model.

### 4. Ingest everything

```bash
make ingest
```

Expected: the first lines say how many trials are left to do (if you ran the sample before, those 163 are skipped), then a progress
line every 100 trials with an estimate of the time left, and finally (the timings are from our Mac):

```
3245 trials to ingest, 163 already up to date
  100/3245 trials, ... chunks, about ...s left
  ...
  3000/3245 trials, 21723 chunks, about 117s left
  3100/3245 trials, 22377 chunks, about 69s left
  3200/3245 trials, 23026 chunks, about 21s left
  3245/3245 trials, 23310 chunks, about 0s left
Done in 1535s: 3245 trials ingested (23310 chunks), 163 skipped as unchanged.
```

**This takes a long time: about 25 minutes**, at roughly 15 chunks per second (24,484 chunks in total). Keep your Mac awake and plugged
in, and leave the terminal alone. If you stop it (Ctrl+C) or it fails, run `make ingest` again: it carries on with the trials
that are not finished. Run it a last time when it's done:

```bash
make ingest
```

Expected, instantly:

```
0 trials to ingest, 3408 already up to date
Done in 0s: 0 trials ingested (0 chunks), 3408 skipped as unchanged.
```

### 5. Look inside the database

```bash
make db-status
```

Expected (the versions may differ):

```
postgres  16.16 (Debian 16.16-1.pgdg12+1)
pgvector  0.8.7
trials    3408
chunks    24484
indexes   ['chunks_embedding_idx', 'chunks_nct_id_section_chunk_index_key', 'chunks_pkey', 'chunks_tsv_idx']
```

24,484 chunks for 3,408 trials is about 7 chunks per trial.

```bash
docker compose exec -T postgres psql -U rag trials -c "SELECT section, count(*) AS chunks, round(avg(length(text))) AS avg_chars, max(length(text)) AS max_chars FROM chunks GROUP BY section ORDER BY section;"
```

Expected:

```
   section   | chunks | avg_chars | max_chars
-------------+--------+-----------+-----------
 description |   3546 |       987 |      1400
 eligibility |   9555 |       851 |      1400
 outcomes    |   7085 |       538 |      1400
 summary     |   4298 |       911 |      1400
```

Only 3,546 `description` chunks come from the 1,732 trials that have a detailed description (long ones produce several chunks), while
every one of the 3,408 trials has `summary` chunks. Eligibility is the biggest section because those lists are long.

The `max_chars` column is never above 1,400.

### 6. Find a trial by its ID

This failed in the [database guide](04-the-database.md) because the ID wasn't in the chunk text. Now it is:

```bash
docker compose exec -T postgres psql -U rag trials -c "SELECT nct_id, section, chunk_index FROM chunks WHERE tsv @@ websearch_to_tsquery('english', 'NCT04998136') ORDER BY section, chunk_index;"
```

Expected: all 8 chunks of NCT04998136:

```
   nct_id    |   section   | chunk_index
-------------+-------------+-------------
 NCT04998136 | eligibility |           0
 NCT04998136 | eligibility |           1
 NCT04998136 | outcomes    |           0
 NCT04998136 | outcomes    |           1
 NCT04998136 | outcomes    |           2
 NCT04998136 | outcomes    |           3
 NCT04998136 | summary     |           0
 NCT04998136 | summary     |           1
```

### 7. Search by meaning, on real data

A preview of Step 06: embed a question, and ask the database for the closest chunks.

```bash
uv run python - <<'EOF'
from pgvector import Vector

from app.db import connect
from app.providers.factory import get_embedder

question = "Which phase 3 trials test GLP-1 drugs for obesity?"
vector = Vector(get_embedder().embed([question])[0])
with connect() as conn:
    rows = conn.execute(
        "SELECT nct_id, section, embedding <=> %s AS distance, split_part(text, E'\\n', 1) "
        "FROM chunks ORDER BY distance LIMIT 8",
        (vector,),
    )
    for nct_id, section, distance, first_line in rows:
        print(f"{distance:.2f}  {section:<11} {first_line[:100]}")
EOF
```

Expected (the exact trials and distances may differ a little):

```
0.22  eligibility NCT03038620 | Impact of Liraglutide 3.0 on Body Fat Distribution | PHASE4
0.23  eligibility NCT00781937 | Comparison of Liraglutide Versus Placebo in Weight Loss Maintenance in Obese Subjects:
0.23  eligibility NCT07177469 | UBT251 Injection Phase II Study (Overweight or Obesity) | PHASE2
0.23  summary     NCT02417103 | Pretreatment of Patients Expecting Bariatric Surgery With the GLP-1 Analogon Liragluti
0.23  eligibility NCT00978393 | A Trial to Assess the Effect of Liraglutide on Gastric Emptying in Healthy Obese Volun
0.23  summary     NCT02963922 | Effect and Safety of Liraglutide 3.0 mg in Subjects With Overweight or Obesity and Typ
0.24  eligibility NCT06937203 | A First-In-Human Study of ARO-ALK7 in Adults With Obesity With and Without Type 2 Diab
0.24  eligibility NCT04487743 | Efficacy and Safety of Liraglutide on Body Weight in Obese Subjects or Overweight Subj
```

Each line is a chunk: its distance to the question (smaller is closer), its section, and its header line (NCT ID, title, phase).

Read it critically. The search clearly understood the *topic*: every hit is about GLP-1 drugs (liraglutide, a GLP-1 analogue) and obesity, and
none is about asthma. But it is a good start, not an answer, for two reasons that Step 06 will address:

- **It ignores "phase 3".** Look at the phases in the headers: PHASE4, PHASE2, and several with no phase shown on that line.
  Meaning search measures *similarity of topic*, and "phase 3" is a precise condition, not a topic. Exact conditions need an
  exact filter.
- **It ranks chunks, but the question is about trials.** Several of these hits are *eligibility* chunks that rank high only because the header with the
  trial's title is part of the chunk. The answer to "which trials?" is a list of trials, so retrieval must combine the chunks of
  one trial, and it will mix this meaning search with keyword search to catch exact terms (**hybrid search**).

### 8. Run the tests

```bash
make test
```

Expected: all tests pass (`57 passed` at the end of this step; the number grows with each step).

## Common problems

- **`Cannot connect to Postgres. Start it with make db-up`**: Docker Desktop isn't running or Postgres is stopped. Run `make db-up`.
- **`The tables don't exist yet. Run make db-init first`**: you started Postgres but never created the tables.
- **`FAILED: Cannot reach Ollama ... Is the Ollama app running?`**: open the Ollama app. Trials saved so far are kept; run the same
  command again to continue.
- **`No trials found in data/raw/`**: download them first with `make fetch` (or `make fetch-sample` for the sample).
- **It is slow**: embedding is the slow part, at roughly 15 to 25 chunks per second, and it is the same whatever batch size you use.
  Keep your Mac awake and plugged in for the full run; it is safe to stop with Ctrl+C and restart.
- **`the input length exceeds the context length`**: a chunk is over the embedding model's limit. The chunker is designed
  to prevent this, and the test over the real data would have caught it. If you changed `MAX_CHARS`, lower it again.
- **I improved the chunker, but the database didn't change**: increase `CHUNKING_VERSION` in `ingestion/chunk.py`. The next
  `make ingest` then rebuilds every trial.
- **I switched the embedding model**: the next `make ingest` re-embeds every trial, and each trial's old chunks are replaced.
  For a completely clean start use `make db-reset`, then `make db-init`, then `make ingest`.
- **The output only appears at the end when I redirect it to a file**: it shouldn't, because the script flushes its progress lines. If you wrap
  it in another tool that buffers, that tool is the cause.
