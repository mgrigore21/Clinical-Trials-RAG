# 05 - Retrieval: finding the right trials

(Built in Step 06. The number `05` is its place in the final reading order; see the
[roadmap](roadmap.md).)

## What we're doing

Writing the "R" of RAG: given a question, find the trials most likely to contain the answer. The code
is `app/rag/retrieve.py`, and you can try it from the terminal with `make search`.

In short, we run **two different searches**, **merge** their results, and **group** them into trials:

```
        question
        /      \
  by meaning   by keyword        two searches, because each is good where the other is weak
  (vectors)    (exact words)
        \      /
     merge the two rankings      Reciprocal Rank Fusion (RRF)
            |
   group chunks into trials      the question is about trials, not chunks
            |
   the best few trials, each with its best chunks
```

Nothing writes an answer yet. That is Step 07. Retrieval only decides *what the model gets to read*.

## Why

Both searches have a blind spot, and the blind spots are different:

| Question | Search by meaning | Search by keyword |
|---|---|---|
| `NCT04998136` | Useless. An ID has no meaning; the top hits were a depression trial and a diabetes-prevention trial | Perfect: finds exactly that trial |
| "drugs that help people lose weight" | Finds trials about obesity drugs, whatever words they use | Misses a trial that says "weight reduction" instead |
| "semaglutide in Thailand and South Korea" | Finds the right trial, then drifts to any semaglutide trial | Finds the right trial through its rare words "Thailand" and "Korea" |

Running both and keeping what each does well is called **hybrid search**.

**Why merge rankings rather than scores?** A vector distance (0.22) and a keyword score (2.5) are on different scales; adding them
is meaningless. A *position in a list* ("first", "fifth") means the same everywhere, so we merge positions.

**Why group by trial?** The user asks "which trials ...?", not "which paragraphs ...?". One trial may match through
three chunks; it should still be listed once, with its best chunks as evidence for the answer step.

## How it works

### Search 1: by meaning (vectors)

The question is embedded with the same model as the chunks (the provider from the [providers guide](07-providers.md)), and Postgres
returns the 50 chunks whose vectors are closest, using **cosine distance** (`<=>`, explained in the
[database guide](04-the-database.md)). The HNSW index makes this fast: about 8 ms on 24,484 chunks.

Two traps here, both found by measuring on the real data, both fixed in `vector_search`:

1. **The index silently returns too few rows.** The index only explores 40 candidates by default (`ef_search`), so asking for
   100 returned **40**, with no error. We set `hnsw.ef_search` and `hnsw.iterative_scan` so that asking for 50 returns 50. (Either
   setting alone fixes the count; `ef_search` also finds better neighbours, and iterative scanning keeps going when a
   filter rejects candidates.)
2. **A tie-breaker in `ORDER BY` turns the index off.** We first wrote `ORDER BY distance, id` to make ties deterministic.
   Results stayed correct, but Postgres could no longer use the index and **sorted every chunk (about 24,000) on every question**. Nothing
   looked wrong; it was just slower. The distance must be the *only* thing in the `ORDER BY`. The test
   `test_the_meaning_search_uses_the_vector_index` now asks Postgres for its plan, to make sure this can't come back unnoticed.

### Search 2: by keyword (full-text)

This uses the full-text search from the database guide, and it needed the most care. The obvious approach does not work for a
natural-language question. For "Which phase 3 trials test GLP-1 drugs for obesity?":

- **Every word must match** (the usual `websearch_to_tsquery`): **1 chunk** out of 24,484. Useless.
- **Any word may match**: **18,550 chunks (76%)**. Also useless, because common words match nearly everything.

So we search only for **distinctive words**: `distinctive_terms()` counts, for each word of the question, how many chunks contain it, and
drops the words that are in more than **15%** of all chunks, as well as words that match nothing at all
(stop words like "which" and "for", or words that aren't in the data):

```
which        0 chunks    0.0%     <- a stop word: dropped
phase     2493 chunks   10.2%
3         5747 chunks   23.5%     <- too common: dropped
trials    4665 chunks   19.1%     <- too common: dropped
test      3228 chunks   13.2%
glp       2921 chunks   11.9%
1         6866 chunks   28.0%     <- too common: dropped
drugs     6229 chunks   25.4%     <- too common: dropped
for          0 chunks    0.0%     <- a stop word: dropped
obesity   6245 chunks   25.5%     <- too common: dropped
```

(The percentages are specific to our data: it is *about* obesity and diabetes, so "obesity" is everywhere.) Words that survive: `phase`, `test`,
`glp`. For "semaglutide Thailand South Korea" they are `semaglutide`, `thailand`, `south`, `korea`: exactly the rare, telling words.

**The ranking trap.** Postgres ranks matches partly by how often a word *repeats*. An outcomes chunk that lists "Phase 1 ... Phase 2 ...
Phase 3 ..." ten times beat chunks containing the rare word we cared about. We saw a depression trial and a breast-cancer trial
rank first for a GLP-1 question just because of the word "phase". So `keyword_search` ranks chunks by **how many different question
words they contain** first (a soft "must contain all the words"), and uses Postgres's score only to order ties.

When the question has no distinctive word at all, the keyword search stays silent and the vector search carries the result.
That is on purpose: a weak keyword signal is worse than none.

### Merging: Reciprocal Rank Fusion (RRF)

Each search gives a ranked list of up to 50 chunks. RRF gives each chunk, for each list it appears in, `1 / (60 + position)`, and adds the
points up. A chunk absent from a list gets nothing from it.

A real example (from the question "Which trials test semaglutide in Thailand and South Korea?", see "Try it yourself"). These are the best chunks of four trials:

| Trial | Position in the meaning search | Position in the keyword search | Calculation | Score |
|---|---|---|---|---|
| NCT04998136 | 2 | 1 | 1/62 + 1/61 | **0.0325** |
| NCT05132088 | 9 | 13 | 1/69 + 1/73 | **0.0282** |
| NCT07271251 | 21 | 15 | 1/81 + 1/75 | **0.0257** |
| NCT04097600 | 42 | 12 | 1/102 + 1/72 | **0.0237** |

Reading it: a chunk that **both** searches like beats one that only a single search ranks first (a chunk first in just one list scores
1/61 = 0.0164, while one that is 10th in both scores 2/70 = 0.0286). The constant 60 is the value everyone uses: it
makes the gap between 1st and 2nd small, so no single list can dominate.

### Grouping into trials

Chunks are walked best-first. The first chunk of a trial decides the trial's position (it is its best one); up to 3 chunks per trial are kept
as evidence, and the walk stops adding new trials after `top_k` (8 by default, the `TOP_K` setting).

### A trial named by its ID always comes first

If the question contains an NCT ID that exists in the data, that trial gets a bonus that puts it first. Why a special rule? In our
ID search, the exact match scored 0.0164 and so did the best meaningless vector hit (both are first in a single list), and
the exact match only won the tie because of the order the chunks happened to be stored in. When the user names a trial, we know what they want.

### Filters

`retrieve()` accepts exact filters: `phases=["PHASE3"]` and `statuses=["RECRUITING"]`. They are plain database conditions applied to
**both** searches, so a filtered result contains only matching trials.

What `retrieve()` does **not** do is read "phase 3" out of the question text and turn it into a filter. In plain RAG "phase 3" is just two more
words, which the meaning search mostly ignores (you will see this below). That is a known weakness of the baseline, and a
deliberate one: the evaluation phase will **measure** it, and the agent extension will fix it by letting a model choose the filters. It
is also why the filters are already here: they will become the parameters of the agent's search tool.

### Trying each search alone

`--mode vector` and `--mode keyword` run just one search, and `--mode hybrid` (the default) runs both. This is for understanding, and later
for the evaluation phase: "does the hybrid really beat the vector search alone?" is a question we will answer with numbers.

### Tests

`tests/test_retrieve.py` has 15 tests:

- Merging and grouping are plain functions, tested without a database, including the worked RRF example above.
- The rest run on a **scripted collection**: three trials we know well plus 25 fillers, and a fake embedder whose vectors depend on
  topic words in a predictable way. So we can state exactly what each search must find: the meaning search finds the semaglutide
  trials, keyword search finds the ID the meaning search cannot, filters are exact, a named ID comes first.
- The three traps described above each have a test that **fails when the fix is removed**. We checked that by removing each fix on purpose
  and watching the test fail; the first version of the "too few rows" test passed even without the fix (on a tiny table Postgres
  doesn't use the index), so we had to make it force the index.

## Try it yourself

Postgres and Ollama must be running, with the trials ingested ([guide 03](03-chunking-and-embeddings.md)).

### 1. Search, with the details visible

```bash
make search Q="Which trials test semaglutide in Thailand and South Korea?" ARGS="--top-k 5"
```

Expected (the exact numbers can shift slightly if the data changes):

```
mode=hybrid  keyword terms: ['test', 'semaglutide', 'thailand', 'south', 'korea']

1. NCT04998136  [PHASE3, COMPLETED]  score 0.0325
   Research Study Investigating How Well Semaglutide Works in People From Thailand and South Korea Living With Ob
     - summary     vector #2, keyword #1
     - summary     vector #1, keyword #2
     - eligibility vector #4, keyword #3

2. NCT05132088  [PHASE3, COMPLETED]  score 0.0282
   Research Study to Investigate How Well Semaglutide Tablets Taken Once Daily Work in East Asian People Who Are
     - summary     vector #9, keyword #13
     - eligibility vector #15, keyword #-
     - summary     vector #46, keyword #-

3. NCT07271251  [PHASE3, COMPLETED]  score 0.0257
   ...
5 trials in 0.2s   (yours will be around 0.1 to 0.3s)
```

Each trial shows its best chunks, and where each search ranked that chunk (`#-` means "not found by that search"). Check the score of
trial 2 yourself with the formula above: `1/(60+9) + 1/(60+13)`.

```bash
python3 -c "print(round(1/62 + 1/61, 4), round(1/69 + 1/73, 4))"
```

Expected: `0.0325 0.0282`.

### 2. Compare the three modes

```bash
uv run python - <<'EOF'
from app.db import connect
from app.providers.factory import get_embedder
from app.rag.retrieve import retrieve

embedder = get_embedder()
with connect() as conn:
    for question in ["Which trials test semaglutide in Thailand and South Korea?", "NCT04998136"]:
        print("\n#####", question)
        for mode in ("vector", "keyword", "hybrid"):
            result = retrieve(conn, embedder, question, top_k=4, mode=mode)
            print(f"--- {mode}")
            for i, t in enumerate(result.trials, 1):
                print(f"  {i}. {t.nct_id} {'/'.join(t.phases) or 'NA':<7} {t.title[:62]}")
EOF
```

Expected:

```
##### Which trials test semaglutide in Thailand and South Korea?
--- vector
  1. NCT04998136 PHASE3  Research Study Investigating How Well Semaglutide Works in Peo
  2. NCT02161588 PHASE1  A Trial to Assess the Pharmacokinetics, Pharmacodynamics, Safe
  3. NCT02146079 PHASE1  A Trial to Assess the Pharmacokinetics, Pharmacodynamics, and
  4. NCT05132088 PHASE3  Research Study to Investigate How Well Semaglutide Tablets Tak
--- keyword
  1. NCT04998136 PHASE3  Research Study Investigating How Well Semaglutide Works in Peo
  2. NCT04524832 PHASE1  Research Study Comparing New Tablets of Semaglutide in New Dos
  3. NCT05236517 PHASE1  A Research Study Looking at How 50 mg Semaglutide Daily Affect
  4. NCT04109508 PHASE1  A Research Study Comparing Active Drug in the Blood in Healthy
--- hybrid
  1. NCT04998136 PHASE3  Research Study Investigating How Well Semaglutide Works in Peo
  2. NCT05132088 PHASE3  Research Study to Investigate How Well Semaglutide Tablets Tak
  3. NCT07271251 PHASE3  A Research Study to See if Two Different Formulations of Oral
  4. NCT04596631 PHASE3  A Research Study to Compare a New Medicine Oral Semaglutide to

##### NCT04998136
--- vector
  1. NCT04787042 PHASE1/PHASE2 Phase 1a and Phase 2 Study for Safety, Preliminary Efficacy, P
  2. NCT01423240 PHASE3  Major Depressive Disorder With Mixed Features
  3. NCT00220961 PHASE3  Actos Now for Prevention of Diabetes (ACT NOW)
  4. NCT01971541 PHASE2/PHASE3 Groups for Regaining Our Wellbeing (GROW)
--- keyword
  1. NCT04998136 PHASE3  Research Study Investigating How Well Semaglutide Works in Peo
--- hybrid
  1. NCT04998136 PHASE3  Research Study Investigating How Well Semaglutide Works in Peo
  2. NCT04787042 PHASE1/PHASE2 Phase 1a and Phase 2 Study for Safety, Preliminary Efficacy, P
  3. NCT01423240 PHASE3  Major Depressive Disorder With Mixed Features
  4. NCT00220961 PHASE3  Actos Now for Prevention of Diabetes (ACT NOW)
```

Read it:

- **First question.** The meaning search finds the right trial, then fills up with PHASE1 trials. The keyword search also finds it, then fills
  up with other semaglutide trials, again PHASE1. The hybrid keeps the right one first, and what follows are PHASE3 semaglutide trials: better than either search alone.
- **Second question** (an ID). The meaning search returns unrelated trials (an ID has no meaning), the keyword search returns
  exactly one trial, and the hybrid puts it first. Everything after it is filler from the meaning search.

### 3. See the filters work

Without filters, the question "Which phase 3 trials test GLP-1 drugs for obesity?" returns trials of every phase. With filters:

```bash
make search Q="Which phase 3 trials test GLP-1 drugs for obesity?" ARGS="--phase PHASE3 --status RECRUITING --top-k 3"
```

Expected: three trials, each shown as `[PHASE3, RECRUITING]`, for example NCT07150975 (a semaglutide comparison), NCT07575399 and
NCT07481747 (tirzepatide).

The values for `--phase` and `--status` are the ones stored in the data. Here is what exists:

```bash
docker compose exec -T postgres psql -U rag trials -At -c "SELECT status, count(*) FROM trials GROUP BY status ORDER BY count(*) DESC;"
```

Expected: `COMPLETED|2089`, `RECRUITING|392`, `UNKNOWN|295`, `TERMINATED|200`, `NOT_YET_RECRUITING|176`, `ACTIVE_NOT_RECRUITING|141`,
`WITHDRAWN|96`, `ENROLLING_BY_INVITATION|10`, `SUSPENDED|9`. Phases are `EARLY_PHASE1`, `PHASE1`, `PHASE2`, `PHASE3`, `PHASE4` and `NA`.

### 4. Why common words are dropped

```bash
uv run python - <<'EOF'
import re

from app.db import connect

question = "Which phase 3 trials test GLP-1 drugs for obesity?"
with connect() as conn:
    total = conn.execute("SELECT count(*) FROM chunks").fetchone()[0]
    for word in dict.fromkeys(w.lower() for w in re.findall(r"[A-Za-z0-9]+", question)):
        n = conn.execute(
            "SELECT count(*) FROM chunks WHERE tsv @@ to_tsquery('english', %s)", (word,)
        ).fetchone()[0]
        print(f"{word:<8} {n:>6} chunks  {n / total:6.1%}")
EOF
```

Expected: the table shown in "Search 2" above.

### 5. Search for a trial by its ID

```bash
make search Q="NCT04998136" ARGS="--top-k 3"
```

Expected: trial 1 is `NCT04998136` with `score 1.0164` (the extra 1.0 is the "named by its ID" bonus; the 0.0164 is the keyword search's
`1/61`), followed by two unrelated trials found only by the meaning search, with scores around 0.016.

### 6. Run the tests

```bash
make test
```

Expected: all tests pass (`72 passed` at the end of this step; the number grows with each step).

## Common problems

- **`Cannot connect to Postgres`**: run `make db-up`. **`Cannot reach Ollama`**: open the Ollama app.
- **No trials come back**: the database is empty. Run `make ingest` ([guide 03](03-chunking-and-embeddings.md)).
- **`keyword terms: -`**: the question had no distinctive word, so only the meaning search ran. Normal for questions made of common words.
- **A filter returns nothing**: the values are exact and upper-case: `PHASE3`, not `phase 3` or `Phase3`; `NOT_YET_RECRUITING`, not "not yet
  recruiting". Both filters must hold at once, and a trial must have *any* of the phases you list.
- **The results look off for a question about what trials *exclude* or *don't* do**: this is a real limit of the baseline. "Exclude
  pregnant women" shares its words with trials that *include* pregnant women, and meaning search is weak with negation. The evaluation phase will show
  how often this happens.
- **"Phase 3" in the question is ignored**: see "Filters". Pass `--phase PHASE3`; reading it from the question is the agent's job later.
- **The first search is slow (several seconds)**: Ollama is loading the embedding model into memory. Later searches take about 0.1 to 0.2 seconds.
- **The results differ slightly from this guide**: the meaning search is *approximate* (that is the price of the fast index), and the data changes over
  time. The structure should be the same.
