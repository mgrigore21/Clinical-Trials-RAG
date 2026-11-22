"""Split one trial into small, self-describing pieces ("chunks") ready to be embedded.

Why chunks: search works best on short pieces about one thing. A whole trial mixes sponsor,
summary, eligibility rules and outcomes, so one vector for all of it is a blurry average. And the
embedding model can only read about 512 tokens at once (docs/07-providers.md).
See docs/03-chunking-and-embeddings.md.
"""

import re
import textwrap
from dataclasses import dataclass

from ingestion.parse import trial_fields

# Bump this when the chunking rules change. It is part of each trial's fingerprint
# (ingestion/run.py), so the next ingestion re-processes every trial with the new rules.
CHUNKING_VERSION = 1

# The embedding model reads at most 512 tokens. Real trial text measured about 0.23 tokens per
# character, 0.31 at worst, so 512 tokens is about 1,660 characters in the worst case. We stay
# clearly below that, header included. (docs/07-providers.md explains the measurement.)
MAX_CHARS = 1400

# A very long title would eat the room that belongs to the content.
TITLE_MAX_CHARS = 150


@dataclass(frozen=True)
class Chunk:
    nct_id: str
    section: str  # summary, description, eligibility or outcomes
    chunk_index: int  # position within the section, so (nct_id, section, chunk_index) is unique
    text: str  # what gets embedded, header included


def split_text(text: str, limit: int) -> list[str]:
    """Cut text into pieces of at most `limit` characters, breaking at the most natural place.

    Preference order: between lines (eligibility criteria are one per line), then between sentences,
    and only as a last resort between words. Pieces are packed greedily, so we get few, full chunks
    rather than many tiny ones.
    """
    pieces, current = [], ""
    for unit in _units(text, limit):
        if current and len(current) + 1 + len(unit) > limit:
            pieces.append(current)
            current = unit
        else:
            current = f"{current}\n{unit}" if current else unit
    if current:
        pieces.append(current)
    return pieces


def _units(text: str, limit: int):
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if len(line) <= limit:
            yield line
            continue
        for sentence in re.split(r"(?<=[.!?])\s+", line):
            if len(sentence) <= limit:
                yield sentence
            else:
                yield from textwrap.wrap(sentence, limit)  # last resort: break between words


def chunk_trial(study: dict) -> list[Chunk]:
    """All the chunks of one trial. Sections the trial doesn't have are simply skipped."""
    fields = trial_fields(study)
    protocol = study["protocolSection"]

    # Every chunk starts with the same header. It puts the trial's identity into the embedding and
    # into keyword search: without the NCT ID in the text, nobody could find a trial by its ID
    # (docs/04-the-database.md shows this). Then a label says which part of the trial this is.
    phase = "/".join(fields["phases"]) or "NA"
    header = f"{fields['nct_id']} | {_shorten(fields['title'], TITLE_MAX_CHARS)} | {phase}"

    sections = [
        ("summary", _summary_parts(fields, protocol)),
        ("description", _description_parts(protocol)),
        ("eligibility", _eligibility_parts(protocol)),
        ("outcomes", _outcomes_parts(protocol)),
    ]
    chunks = []
    for section, parts in sections:
        index = 0
        for label, body in parts:
            prefix = f"{header}\n{label}\n"
            for piece in split_text(body, MAX_CHARS - len(prefix)):
                chunks.append(Chunk(fields["nct_id"], section, index, prefix + piece))
                index += 1
    return chunks


def _shorten(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _summary_parts(fields: dict, protocol: dict) -> list[tuple[str, str]]:
    lines = []
    if fields["official_title"] and fields["official_title"] != fields["title"]:
        lines.append(f"Official title: {fields['official_title']}")

    facts = [
        f"{name}: {value}"
        for name, value in (
            ("Status", fields["status"]),
            ("Study type", fields["study_type"]),
            ("Start date", fields["start_date"]),
            ("Sponsor", fields["sponsor"]),
        )
        if value
    ]
    if facts:
        lines.append(". ".join(facts) + ".")
    if fields["conditions"]:
        lines.append("Conditions: " + "; ".join(fields["conditions"]))

    interventions = protocol.get("armsInterventionsModule", {}).get("interventions", [])
    named = [
        f"{i['name']} ({i.get('type', 'unknown type')})" for i in interventions if i.get("name")
    ]
    if named:
        lines.append("Interventions: " + "; ".join(named))

    brief = protocol.get("descriptionModule", {}).get("briefSummary")
    if brief:
        lines.append(brief)
    return [("Summary", "\n".join(lines))]


def _description_parts(protocol: dict) -> list[tuple[str, str]]:
    # Only about half of the trials have one, so a missing description is normal.
    detailed = protocol.get("descriptionModule", {}).get("detailedDescription")
    return [("Detailed description", detailed)] if detailed else []


def _eligibility_parts(protocol: dict) -> list[tuple[str, str]]:
    module = protocol.get("eligibilityModule", {})
    text = (module.get("eligibilityCriteria") or "").strip()

    who = [f"Sex: {module['sex']}"] if module.get("sex") else []
    if module.get("minimumAge"):
        who.append(f"Minimum age: {module['minimumAge']}")
    if module.get("maximumAge"):
        who.append(f"Maximum age: {module['maximumAge']}")
    demographics = ". ".join(who) + "." if who else ""

    # Split inclusion from exclusion: a chunk of bullet points without that heading is ambiguous
    # ("pregnancy" means the opposite in the two lists), and the label is also embedded.
    match = re.search(r"exclusion criteria\s*:?", text, re.IGNORECASE)
    if match:
        inclusion, exclusion = text[: match.start()], text[match.end() :]
        inclusion_label = "Eligibility: inclusion criteria"
    else:
        inclusion, exclusion = text, ""
        inclusion_label = "Eligibility criteria"
    inclusion = re.sub(r"^\W*inclusion criteria\s*:?", "", inclusion.strip(), flags=re.IGNORECASE)

    parts = [
        (inclusion_label, "\n".join(x for x in (demographics, inclusion.strip()) if x)),
        ("Eligibility: exclusion criteria", exclusion.strip()),
    ]
    return [(label, body) for label, body in parts if body]


def _outcomes_parts(protocol: dict) -> list[tuple[str, str]]:
    module = protocol.get("outcomesModule", {})
    parts = []
    for label, key in (
        ("Outcomes: primary", "primaryOutcomes"),
        ("Outcomes: secondary", "secondaryOutcomes"),
    ):
        lines = []
        for outcome in module.get(key, []):
            measure = (outcome.get("measure") or "").strip()
            description = (outcome.get("description") or "").strip()
            if measure:
                lines.append(f"- {measure}: {description}" if description else f"- {measure}")
        if lines:
            parts.append((label, "\n".join(lines)))
    return parts
