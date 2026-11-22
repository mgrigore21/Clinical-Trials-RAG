"""Tests for ingestion/parse.py and ingestion/chunk.py. No database, no model: pure functions."""

from pathlib import Path

import pytest

from app.config import Settings
from ingestion.chunk import MAX_CHARS, TITLE_MAX_CHARS, chunk_trial, split_text
from ingestion.fetch import load_cached_trials
from ingestion.parse import trial_fields
from tests.fakes import make_study

# --- split_text ---------------------------------------------------------------------------


def test_split_text_keeps_short_text_in_one_piece():
    assert split_text("one line\nsecond line", limit=100) == ["one line\nsecond line"]


def test_split_text_breaks_between_lines_first():
    text = "\n".join(f"* criterion number {i}" for i in range(20))

    pieces = split_text(text, limit=100)

    assert len(pieces) > 1
    assert all(len(p) <= 100 for p in pieces)
    # Nothing lost, nothing cut in the middle of a line, order kept.
    assert "\n".join(pieces) == text


def test_split_text_falls_back_to_sentences_then_words():
    sentences = "First sentence is here. Second sentence follows it. Third one ends the paragraph."
    no_spaces = "x" * 250

    assert all(len(p) <= 40 for p in split_text(sentences, limit=40))
    assert split_text(sentences, limit=40)[0].endswith(".")  # broke at a sentence end
    assert all(len(p) <= 100 for p in split_text(no_spaces, limit=100))  # even a giant "word"


def test_split_text_ignores_blank_lines_and_empty_input():
    assert split_text("a\n\n\nb", limit=100) == ["a\nb"]
    assert split_text("   \n  ", limit=100) == []


# --- trial_fields ------------------------------------------------------------------------


def test_trial_fields_flattens_the_record():
    fields = trial_fields(make_study(phases=("PHASE2", "PHASE3")))

    assert fields["nct_id"] == "NCT00000001"
    assert fields["phases"] == ["PHASE2", "PHASE3"]
    assert fields["interventions"] == ["Semaglutide", "Placebo"]
    assert fields["sponsor"] == "Acme Pharma"
    assert fields["start_date"] == "2022-08"


def test_trial_fields_copes_with_missing_modules():
    bare = {"protocolSection": {"identificationModule": {"nctId": "NCT1", "briefTitle": "T"}}}

    fields = trial_fields(bare)

    assert fields["title"] == "T"
    assert fields["phases"] == [] and fields["conditions"] == [] and fields["interventions"] == []
    assert fields["status"] is None and fields["sponsor"] is None


# --- chunk_trial -------------------------------------------------------------------------


def test_every_chunk_starts_with_the_trial_identity():
    chunks = chunk_trial(make_study(title="Semaglutide for obesity", phases=("PHASE2", "PHASE3")))

    assert chunks
    for chunk in chunks:
        assert chunk.text.startswith("NCT00000001 | Semaglutide for obesity | PHASE2/PHASE3\n")


def test_a_trial_without_phase_is_labelled_na():
    chunks = chunk_trial(make_study(phases=()))
    assert chunks[0].text.startswith("NCT00000001 | Semaglutide for obesity | NA\n")


def test_all_four_sections_are_produced_with_labels():
    chunks = chunk_trial(make_study())
    by_label = {c.text.splitlines()[1]: c for c in chunks}

    assert set(by_label) == {
        "Summary",
        "Detailed description",
        "Eligibility: inclusion criteria",
        "Eligibility: exclusion criteria",
        "Outcomes: primary",
        "Outcomes: secondary",
    }
    assert {c.section for c in chunks} == {"summary", "description", "eligibility", "outcomes"}


def test_summary_chunk_carries_the_facts():
    summary = next(c for c in chunk_trial(make_study()) if c.section == "summary").text

    assert "Status: COMPLETED" in summary
    assert "Sponsor: Acme Pharma" in summary
    assert "Conditions: Obesity" in summary
    assert "Interventions: Semaglutide (DRUG); Placebo (DRUG)" in summary
    assert "A study of semaglutide in adults with obesity." in summary


def test_inclusion_and_exclusion_are_kept_apart():
    chunks = [c for c in chunk_trial(make_study()) if c.section == "eligibility"]
    inclusion = next(c.text for c in chunks if "inclusion criteria" in c.text.splitlines()[1])
    exclusion = next(c.text for c in chunks if "exclusion criteria" in c.text.splitlines()[1])

    assert "BMI of 30 or more" in inclusion and "Pregnancy" not in inclusion
    assert "Sex: ALL" in inclusion and "Minimum age: 18 Years" in inclusion
    assert "Pregnancy" in exclusion and "BMI of 30 or more" not in exclusion
    assert not inclusion.endswith("Exclusion Criteria:")


def test_eligibility_without_an_exclusion_heading_is_one_part():
    chunks = chunk_trial(make_study(eligibility="Adults with obesity who can swallow tablets."))
    labels = [c.text.splitlines()[1] for c in chunks if c.section == "eligibility"]

    assert labels == ["Eligibility criteria"]


def test_missing_sections_are_skipped_not_empty():
    chunks = chunk_trial(make_study(description=None, eligibility=None, secondary=()))
    sections = {c.section for c in chunks}

    assert "description" not in sections and "eligibility" not in sections
    assert not any("Outcomes: secondary" in c.text for c in chunks)
    assert all(c.text.strip() for c in chunks)


def test_a_long_section_is_split_into_numbered_chunks_that_all_fit():
    criteria = "\n".join(f"* Participants must satisfy criterion number {i}." for i in range(200))
    study = make_study(eligibility=f"Inclusion Criteria:\n{criteria}\nExclusion Criteria:\n* None")

    chunks = [c for c in chunk_trial(study) if c.section == "eligibility"]

    assert len(chunks) > 3
    assert [c.chunk_index for c in chunks] == list(range(len(chunks)))
    assert all(len(c.text) <= MAX_CHARS for c in chunks)
    # The label is repeated on every piece, so no piece is an anonymous list of bullet points.
    assert all("Eligibility: inclusion criteria" in c.text for c in chunks[:-1])


def test_a_huge_title_cannot_push_a_chunk_over_the_limit():
    study = make_study(title="Very long title " * 100, description="word " * 2000)

    chunks = chunk_trial(study)

    assert all(len(c.text) <= MAX_CHARS for c in chunks)
    assert len(chunks[0].text.splitlines()[0]) < TITLE_MAX_CHARS + 40  # header stays short


def test_chunk_keys_are_unique_within_a_trial():
    study = make_study(
        description="A sentence. " * 500, secondary=tuple(f"Outcome {i}" for i in range(80))
    )

    keys = [(c.section, c.chunk_index) for c in chunk_trial(study)]

    assert len(keys) == len(set(keys))


def test_chunking_is_deterministic():
    assert chunk_trial(make_study()) == chunk_trial(make_study())


def test_every_chunk_of_the_real_data_fits_the_embedding_model():
    # The safety net for the real world: if the downloaded data holds a shape of text the rules
    # above did not anticipate, this is where it shows up. Skipped when nothing is downloaded.
    raw_dir = Path(Settings(_env_file=None).data_dir) / "raw"
    trials = load_cached_trials(raw_dir) if raw_dir.exists() else {}
    if not trials:
        pytest.skip("No downloaded trials (run `make fetch`)")

    for study in trials.values():
        chunks = chunk_trial(study)
        assert chunks, trial_fields(study)["nct_id"]
        assert all(0 < len(c.text) <= MAX_CHARS for c in chunks)
