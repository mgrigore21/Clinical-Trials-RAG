"""Tests for ingestion/fetch.py.

No real network calls: httpx.MockTransport lets each test play the part of the ClinicalTrials.gov
server, so the tests are fast, free and give the same result every time.
"""

import json

import httpx
import pytest

from ingestion import fetch
from ingestion.fetch import Topic

TOPIC = Topic("demo", {"query.cond": "obesity"}, max_studies=1000)


def study(nct_id: str) -> dict:
    return {"protocolSection": {"identificationModule": {"nctId": nct_id}}}


def client_for(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


@pytest.fixture(autouse=True)
def no_waiting(monkeypatch):
    # Retries sleep between attempts; tests shouldn't actually wait.
    monkeypatch.setattr(fetch.time, "sleep", lambda _seconds: None)


def test_follows_next_page_token_until_there_is_none():
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(dict(request.url.params))
        if "pageToken" not in request.url.params:
            return httpx.Response(200, json={"studies": [study("NCT1")], "nextPageToken": "t2"})
        return httpx.Response(200, json={"studies": [study("NCT2")]})

    ids = [
        s["protocolSection"]["identificationModule"]["nctId"]
        for s in fetch.fetch_topic(client_for(handler), TOPIC)
    ]

    assert ids == ["NCT1", "NCT2"]
    assert requests[1]["pageToken"] == "t2"
    assert "NCTId" in requests[0]["fields"]  # we ask only for the fields we need


def test_limit_caps_the_results_and_the_page_size():
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(dict(request.url.params))
        # A server that returns more than asked for, and offers a next page.
        studies = [study(f"NCT{i}") for i in range(5)]
        return httpx.Response(200, json={"studies": studies, "nextPageToken": "more"})

    results = list(fetch.fetch_topic(client_for(handler), TOPIC, limit=3))

    assert len(results) == 3
    assert len(requests) == 1  # stopped at the cap instead of fetching the next page
    assert requests[0]["pageSize"] == "3"


def test_transient_errors_are_retried():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        if len(calls) == 1:
            return httpx.Response(503)
        return httpx.Response(200, json={"studies": [study("NCT1")]})

    assert len(list(fetch.fetch_topic(client_for(handler), TOPIC))) == 1
    assert len(calls) == 2


def test_gives_up_after_max_attempts():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(503)

    with pytest.raises(httpx.HTTPStatusError):
        list(fetch.fetch_topic(client_for(handler), TOPIC))
    assert len(calls) == fetch.MAX_ATTEMPTS


def test_client_errors_are_not_retried():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(400)  # our request is wrong; trying again can't help

    with pytest.raises(httpx.HTTPStatusError):
        list(fetch.fetch_topic(client_for(handler), TOPIC))
    assert len(calls) == 1


def test_download_writes_one_trial_per_line_and_reuses_the_cache(tmp_path):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(200, json={"studies": [study("NCT1"), study("NCT2")]})

    client = client_for(handler)

    count, from_cache = fetch.download_topic(client, TOPIC, tmp_path)
    assert (count, from_cache) == (2, False)
    lines = (tmp_path / "demo.jsonl").read_text().splitlines()
    assert [
        json.loads(line)["protocolSection"]["identificationModule"]["nctId"] for line in lines
    ] == [
        "NCT1",
        "NCT2",
    ]

    count, from_cache = fetch.download_topic(client, TOPIC, tmp_path)
    assert (count, from_cache) == (2, True)
    assert len(calls) == 1  # the second run did not touch the network

    fetch.download_topic(client, TOPIC, tmp_path, refresh=True)
    assert len(calls) == 2


def test_failed_download_leaves_no_cache_file(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400)

    with pytest.raises(httpx.HTTPStatusError):
        fetch.download_topic(client_for(handler), TOPIC, tmp_path)

    # Otherwise the next run would treat a partial file as a finished download.
    assert not (tmp_path / "demo.jsonl").exists()


def test_load_cached_trials_keeps_one_copy_of_a_trial_in_several_topics(tmp_path):
    (tmp_path / "a.jsonl").write_text(
        json.dumps(study("NCT1")) + "\n" + json.dumps(study("NCT2")) + "\n"
    )
    (tmp_path / "b.jsonl").write_text(
        json.dumps(study("NCT2")) + "\n" + json.dumps(study("NCT3")) + "\n"
    )

    assert sorted(fetch.load_cached_trials(tmp_path)) == ["NCT1", "NCT2", "NCT3"]
