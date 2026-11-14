"""Download trials from the ClinicalTrials.gov API and keep a copy on disk.

Why a copy: the API is free but shared, and downloading thousands of trials takes minutes. We
download once, then every later step reads the local files. See docs/02-getting-the-data.md.

Run it:  uv run python -m ingestion.fetch            (everything, a few minutes)
         uv run python -m ingestion.fetch --limit 20  (tiny sample, a few seconds)
"""

import argparse
import json
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import httpx

from app.config import get_settings

API_URL = "https://clinicaltrials.gov/api/v2/studies"
MAX_PAGE_SIZE = 1000  # the largest page the API allows
MAX_ATTEMPTS = 4

# 429 = "slow down", 5xx = the server had a hiccup. Both are worth trying again; other errors
# (like 400, "your request is wrong") would fail the same way every time, so we don't retry them.
RETRYABLE_STATUS = {429, 500, 502, 503, 504}

# Ask only for what the assistant needs. Full records are huge (mostly lists of hospital
# locations), and every extra byte is slower to download, store and search.
FIELDS = [
    "NCTId",
    "BriefTitle",
    "OfficialTitle",
    "OverallStatus",
    "Phase",
    "StudyType",
    "StartDate",
    "Condition",
    "InterventionName",
    "InterventionType",
    "LeadSponsorName",
    "BriefSummary",
    "DetailedDescription",
    "EligibilityCriteria",
    "Sex",
    "MinimumAge",
    "MaximumAge",
    "PrimaryOutcomeMeasure",
    "PrimaryOutcomeDescription",
    "SecondaryOutcomeMeasure",
]


@dataclass(frozen=True)
class Topic:
    """One search to run against the API, and the most trials we keep from it."""

    name: str  # also the cache file name
    params: dict[str, str]
    max_studies: int


# Interventional = trials that test a treatment (not just observe people). Phase 2/3 are the
# mid-to-late stages where treatments are compared. Keeping to these makes questions like
# "which phase 3 trials test X" meaningful.
INTERVENTIONAL = "AREA[StudyType]INTERVENTIONAL"
LATE_PHASE = f"{INTERVENTIONAL} AND AREA[Phase](PHASE2 OR PHASE3)"
GLP1_DRUGS = "semaglutide OR liraglutide OR tirzepatide OR dulaglutide OR exenatide OR GLP-1"

# The first four are our "core" slice, so the demo question about GLP-1 drugs for obesity has
# plenty to find. The last five are distractors: unrelated trials, so retrieval has to pick the
# right ones instead of returning whatever is there. The caps keep the total at a few thousand.
TOPICS = [
    Topic(
        "glp1-obesity",
        {"query.cond": "obesity", "query.intr": GLP1_DRUGS, "filter.advanced": INTERVENTIONAL},
        1000,
    ),
    Topic(
        "glp1-type2-diabetes",
        {
            "query.cond": "type 2 diabetes",
            "query.intr": GLP1_DRUGS,
            "filter.advanced": INTERVENTIONAL,
        },
        800,
    ),
    Topic("obesity", {"query.cond": "obesity", "filter.advanced": LATE_PHASE}, 600),
    Topic("type2-diabetes", {"query.cond": "type 2 diabetes", "filter.advanced": LATE_PHASE}, 600),
    Topic("hypertension", {"query.cond": "hypertension", "filter.advanced": LATE_PHASE}, 200),
    Topic("heart-failure", {"query.cond": "heart failure", "filter.advanced": LATE_PHASE}, 200),
    Topic("asthma", {"query.cond": "asthma", "filter.advanced": LATE_PHASE}, 200),
    Topic("breast-cancer", {"query.cond": "breast cancer", "filter.advanced": LATE_PHASE}, 200),
    Topic("depression", {"query.cond": "depression", "filter.advanced": LATE_PHASE}, 200),
]


def _get_json(client: httpx.Client, params: dict) -> dict:
    """GET one page, trying again with growing pauses if the server is busy or the network blips."""
    attempt = 0
    while True:
        attempt += 1
        last_attempt = attempt == MAX_ATTEMPTS
        try:
            response = client.get(API_URL, params=params)
        except httpx.TransportError:
            if last_attempt:
                raise
        else:
            if response.status_code not in RETRYABLE_STATUS or last_attempt:
                response.raise_for_status()
                return response.json()
        time.sleep(2**attempt)  # 2s, 4s, 8s: give a busy server room to recover


def fetch_topic(client: httpx.Client, topic: Topic, limit: int | None = None) -> Iterator[dict]:
    """Yield trials for one topic, following the API's page-by-page "next page" tokens.

    The API never returns everything at once. Each response holds one page of trials plus a
    `nextPageToken`; we send that token back to get the following page, until there is none.
    """
    cap = min(topic.max_studies, limit) if limit else topic.max_studies
    page_size = min(MAX_PAGE_SIZE, cap)
    token = None
    yielded = 0
    while True:
        params = {**topic.params, "fields": ",".join(FIELDS), "pageSize": page_size}
        if token:
            params["pageToken"] = token
        page = _get_json(client, params)
        for study in page.get("studies", []):
            yield study
            yielded += 1
            if yielded >= cap:
                return
        token = page.get("nextPageToken")
        if not token:
            return


def download_topic(
    client: httpx.Client,
    topic: Topic,
    raw_dir: Path,
    limit: int | None = None,
    refresh: bool = False,
) -> tuple[int, bool]:
    """Save one topic as <raw_dir>/<name>.jsonl, one trial per line. Returns (count, from_cache)."""
    path = raw_dir / f"{topic.name}.jsonl"
    if path.exists() and not refresh:
        with path.open(encoding="utf-8") as f:
            return sum(1 for _ in f), True

    raw_dir.mkdir(parents=True, exist_ok=True)
    # Write to a temporary file and rename at the end: if the download is interrupted, there is
    # no half-finished file that a later run would mistake for a complete download.
    tmp = path.with_suffix(".jsonl.tmp")
    count = 0
    with tmp.open("w", encoding="utf-8") as f:
        for study in fetch_topic(client, topic, limit):
            f.write(json.dumps(study, ensure_ascii=False) + "\n")
            count += 1
    tmp.replace(path)
    return count, False


def load_cached_trials(raw_dir: Path) -> dict[str, dict]:
    """Read every cached topic file into {NCT ID: trial}.

    A trial can match several topics (a GLP-1 obesity trial is also an obesity trial), so the
    same NCT ID can appear in more than one file. Keying by NCT ID keeps exactly one copy.
    """
    trials: dict[str, dict] = {}
    for path in sorted(raw_dir.glob("*.jsonl")):
        with path.open(encoding="utf-8") as f:
            for line in f:
                study = json.loads(line)
                nct_id = study["protocolSection"]["identificationModule"]["nctId"]
                trials.setdefault(nct_id, study)
    return trials


def main() -> None:
    parser = argparse.ArgumentParser(description="Download trials from ClinicalTrials.gov.")
    parser.add_argument(
        "--limit", type=int, help="keep at most N trials per topic (for a quick try)"
    )
    parser.add_argument("--refresh", action="store_true", help="download again even if cached")
    args = parser.parse_args()

    # A limited download goes to its own folder, so a small sample is never mistaken for the
    # full dataset by the steps that come later.
    data_dir = Path(get_settings().data_dir)
    raw_dir = data_dir / ("raw-sample" if args.limit else "raw")

    headers = {"User-Agent": "clinical-trials-rag-demo"}
    with httpx.Client(timeout=get_settings().request_timeout_s, headers=headers) as client:
        for topic in TOPICS:
            count, cached = download_topic(client, topic, raw_dir, args.limit, args.refresh)
            print(f"{topic.name:<22} {count:>5} trials  ({'cached' if cached else 'downloaded'})")

    print(f"\n{len(load_cached_trials(raw_dir))} unique trials in {raw_dir}/")


if __name__ == "__main__":
    main()
