"""Pretend parts for tests: a trial record and an embedding model that needs no Ollama."""

import zlib

from app.providers.base import ProviderError


def make_study(
    nct_id: str = "NCT00000001",
    title: str = "Semaglutide for obesity",
    phases: tuple[str, ...] = ("PHASE3",),
    summary: str | None = "A study of semaglutide in adults with obesity.",
    description: str | None = "Participants inject once a week for a year.",
    eligibility: str | None = (
        "Inclusion Criteria:\n\n* Age 18 or older\n* BMI of 30 or more\n\n"
        "Exclusion Criteria:\n\n* Pregnancy\n* Type 1 diabetes"
    ),
    primary: tuple[str, ...] = ("Change in body weight",),
    secondary: tuple[str, ...] = ("Change in waist size",),
    interventions: tuple[str, ...] = ("Semaglutide", "Placebo"),
    status: str = "COMPLETED",
) -> dict:
    """A small trial record shaped like the ones ClinicalTrials.gov returns (see fetch.py)."""
    description_module = {}
    if summary:
        description_module["briefSummary"] = summary
    if description:
        description_module["detailedDescription"] = description

    protocol = {
        "identificationModule": {
            "nctId": nct_id,
            "briefTitle": title,
            "officialTitle": title + "!",
        },
        "statusModule": {"overallStatus": status, "startDateStruct": {"date": "2022-08"}},
        "sponsorCollaboratorsModule": {"leadSponsor": {"name": "Acme Pharma"}},
        "descriptionModule": description_module,
        "conditionsModule": {"conditions": ["Obesity"]},
        "designModule": {"studyType": "INTERVENTIONAL", "phases": list(phases)},
        "armsInterventionsModule": {
            "interventions": [{"type": "DRUG", "name": name} for name in interventions]
        },
        "outcomesModule": {
            "primaryOutcomes": [{"measure": m} for m in primary],
            "secondaryOutcomes": [
                {"measure": m, "description": "Measured weekly"} for m in secondary
            ],
        },
    }
    if eligibility:
        protocol["eligibilityModule"] = {
            "eligibilityCriteria": eligibility,
            "sex": "ALL",
            "minimumAge": "18 Years",
        }
    return {"protocolSection": protocol}


class FakeEmbedder:
    """Returns a fixed, text-dependent 1024-number vector and remembers every request.

    `fail_on_call=2` makes the second request raise, to test what happens when the model breaks
    half way through.
    """

    def __init__(self, fail_on_call: int | None = None):
        self.calls: list[list[str]] = []
        self.fail_on_call = fail_on_call

    def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(list(texts))
        if self.fail_on_call == len(self.calls):
            raise ProviderError("the embedding model fell over")
        vectors = []
        for text in texts:
            vector = [0.0] * 1024
            vector[zlib.crc32(text.encode()) % 1024] = 1.0
            vectors.append(vector)
        return vectors
