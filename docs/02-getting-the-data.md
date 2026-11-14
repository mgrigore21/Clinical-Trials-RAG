# 02 - Getting the data

## What we're doing

Downloading about 3,400 real clinical trials from [ClinicalTrials.gov](https://clinicaltrials.gov)
and saving them as files on your computer. The code is in `ingestion/fetch.py`.

Nothing is split, embedded or stored in a database yet. Those are later steps. This step only
answers: *where does the data come from, and how do we get it?*

## Why

- **The assistant is only as good as its data.** RAG answers from the records we give it, so we need
  real, detailed, up-to-date records.
- **Download once, then work offline.** The ClinicalTrials.gov service is free and shared by everyone,
  and downloading thousands of trials takes a moment. We download once and keep a copy; every later
  step reads the copy.
- **A focused slice, not everything.** The site holds hundreds of thousands of trials. We take a few
  thousand about one theme (obesity, type 2 diabetes and the GLP-1 drugs used for them) plus a few
  unrelated topics. The theme lets us ask meaningful questions like "which phase 3 trials test GLP-1
  drugs for obesity?". The unrelated trials are **distractors**: they make retrieval prove it
  can find the right trials among wrong ones, as it would in real life.

## How it works

### The source: an API

An **API** is a way for programs (not people) to ask a service for data. ClinicalTrials.gov offers one
at `https://clinicaltrials.gov/api/v2/studies`. You send a request with **query parameters**, which
are the filters written after the `?` in a web address, and you get **JSON** back. JSON is plain text
organised as nested lists and labelled values, which Python reads easily.

Example parameters we use:

| Parameter | Meaning |
|---|---|
| `query.cond=obesity` | trials about the condition "obesity" |
| `query.intr=semaglutide OR ...` | trials testing one of these interventions (treatments) |
| `filter.advanced=AREA[StudyType]INTERVENTIONAL` | only trials that *test a treatment*, not ones that just observe people |
| `fields=NCTId,BriefTitle,...` | only send these parts of each trial |
| `pageSize=1000` | up to 1,000 trials per response (the API's maximum) |

### What a trial looks like

Each trial is identified by an **NCT ID** (like `NCT04998136`) and is split into named modules.
The ones we keep:

| Module | What is inside |
|---|---|
| `identificationModule` | NCT ID, short title, official title |
| `statusModule` | status (completed, recruiting, ...), start date |
| `sponsorCollaboratorsModule` | the sponsor, e.g. a drug company or a university |
| `descriptionModule` | `briefSummary`, and sometimes a `detailedDescription` |
| `conditionsModule` | the conditions studied |
| `designModule` | study type and **phase** |
| `armsInterventionsModule` | the treatments being tested |
| `eligibilityModule` | who may join: inclusion/exclusion text, sex, age range |
| `outcomesModule` | what the trial measures |

A **phase** is a trial's stage: phase 1 checks safety in a few people, phase 2 checks whether it works,
phase 3 compares it with the standard treatment in many people, phase 4 watches it after approval.

A few facts about our data that matter later (see the chunking guide): every trial has a brief
summary, but only about half (1,732 of 3,408) have a detailed description, and a few have no outcomes
listed. **Not every section exists for every trial**, so the code that splits trials must cope
with missing parts.

### Pages and tokens

The API will not send 800 trials in one go. It sends one **page** at a time, plus a `nextPageToken`: a
bookmark meaning "continue from here". We send the token back to get the next page, and stop when
no token comes back (or when we have enough). This is called **pagination**. It is in
`fetch_topic()`.

### Topics

Instead of one huge search, `TOPICS` in `fetch.py` lists nine smaller searches, each with a cap:

| Topic | Cap | Role |
|---|---|---|
| `glp1-obesity` | 1000 | core: GLP-1 drugs (semaglutide, tirzepatide, ...) for obesity |
| `glp1-type2-diabetes` | 800 | core: the same drugs for type 2 diabetes |
| `obesity`, `type2-diabetes` | 600 each | core: phase 2/3 trials of any treatment |
| `hypertension`, `heart-failure`, `asthma`, `breast-cancer`, `depression` | 200 each | distractors |

A trial can match several topics (a GLP-1 obesity trial is also an obesity trial). We keep one copy
per NCT ID, which is why 3,834 downloaded rows give 3,408 unique trials.

### Saving a copy (the cache)

Each topic is saved as `data/raw/<topic>.jsonl`. **JSONL** ("JSON lines") means one JSON object
per line, so one trial per line. It is easy to read a line at a time and to count with `wc -l`.

A saved copy that we reuse is a **cache**. If the file exists, `fetch` does not download it again
unless you ask (`--refresh`). Two safety details:

- Downloads are written to a temporary file and renamed at the end, so an interrupted download never
  leaves a half-finished file that looks complete.
- A small sample (`--limit`) goes to its own folder, `data/raw-sample/`, so it can never be mistaken
  for the full dataset.

The `data/` folder is **git-ignored** (listed in `.gitignore`), so the 21 MB of trials stay out of the
repository: anyone can download them again.

### Being a good guest: retries

Shared services sometimes answer "slow down" (status 429) or fail for a moment (5xx). The code then
waits 2, 4 and 8 seconds and tries again, up to 4 attempts. A "your request is wrong" error (like 400)
is **not** retried, because it would fail the same way every time. This is **retry with backoff**:
waiting longer after each failure to give the server room to recover. See `_get_json()`.

### Tests without the internet

`tests/test_fetch.py` checks all of this without calling the real service. It uses a **mock**: a pretend
server that returns whatever the test wants (a second page, an error, ...). Tests that depend on the
real internet are slow and fail at random, so these tests are instant and repeatable.

## Try it yourself

Run from the repository folder. The numbers below come from a real run; because the live database
changes (trials are added and updated every day), yours may differ a little.

### 1. Look at the raw API

```bash
curl -s "https://clinicaltrials.gov/api/v2/studies?query.cond=obesity&pageSize=2&fields=NCTId,BriefTitle&countTotal=true" | python3 -m json.tool
```

Expected: JSON with `totalCount` (about 15,000), a `studies` list of 2 trials each with an `nctId` and a
`briefTitle`, and a `nextPageToken` (the bookmark for page 2).

### 2. Download a tiny sample first

```bash
make fetch-sample
```

Expected: nine lines, each `20 trials (downloaded)`, then `163 unique trials in data/raw-sample/`
(about; the overlap between topics varies). It takes a couple of seconds.

### 3. Download everything

```bash
make fetch
```

Expected (numbers may vary slightly):

```
glp1-obesity             834 trials  (downloaded)
glp1-type2-diabetes      800 trials  (downloaded)
obesity                  600 trials  (downloaded)
type2-diabetes           600 trials  (downloaded)
hypertension             200 trials  (downloaded)
heart-failure            200 trials  (downloaded)
asthma                   200 trials  (downloaded)
breast-cancer            200 trials  (downloaded)
depression               200 trials  (downloaded)

3408 unique trials in data/raw/
```

It takes about 10 seconds. Run it again: every line now says `(cached)` and nothing is downloaded.
(`glp1-obesity` is below its cap of 1000 because the API has only 834 such trials.)

### 4. Look at what you saved

```bash
wc -l data/raw/*.jsonl
du -sh data/raw
```

Expected: one line count per topic matching the above (3834 in total) and a size of about `21M`.

```bash
head -n 1 data/raw/glp1-obesity.jsonl | uv run python -m json.tool | head -22
```

Expected: the start of one trial, beginning with `"protocolSection": {` and an
`"identificationModule"` that contains an `"nctId"` and a `"briefTitle"`.

### 5. Load the data the way later steps will

```bash
uv run python -c "
from pathlib import Path
from ingestion.fetch import load_cached_trials
trials = load_cached_trials(Path('data/raw'))
print(len(trials), 'unique trials')"
```

Expected: `3408 unique trials` (about).

### 6. Run the tests

```bash
make test
```

Expected: `11 passed`.

## Common problems

- **`make fetch` seems to hang**: each topic is one or a few requests, so it should finish in well under a
  minute. A slow connection or a busy service can take longer; the code retries by itself. If a
  topic fails after 4 attempts you get an `HTTPStatusError`: wait a minute and run `make fetch` again.
  Topics that already finished are cached and are not downloaded again.
- **The numbers differ from this guide**: expected. The live database changes daily, so counts move a little.
- **I want fresh data**: `uv run python -m ingestion.fetch --refresh` downloads everything again.
- **`make fetch-sample` didn't change the full data**: right, samples are saved in `data/raw-sample/`
  and the full download in `data/raw/`.
- **A 400 error**: the API rejected a request. This only happens if a parameter in `TOPICS` or `FIELDS` is
  wrong, for example a mistyped field name. The error text says what it didn't like.
- **Which folder is `data/`?** Set `DATA_DIR` in `.env` to store it somewhere else.
