"""Read the handful of facts we need out of one raw ClinicalTrials.gov record.

The raw record is deeply nested (see docs/02-getting-the-data.md). Every other step wants flat,
predictable values, and some modules are missing for some trials, so the digging lives here, once.
"""


def trial_fields(study: dict) -> dict:
    """The columns of the `trials` table (see ingestion/schema.sql), minus `raw` and the hash."""
    protocol = study["protocolSection"]
    ident = protocol["identificationModule"]
    status = protocol.get("statusModule", {})
    design = protocol.get("designModule", {})
    interventions = protocol.get("armsInterventionsModule", {}).get("interventions", [])
    return {
        "nct_id": ident["nctId"],
        "title": ident.get("briefTitle") or ident.get("officialTitle") or ident["nctId"],
        "official_title": ident.get("officialTitle"),
        "status": status.get("overallStatus"),
        "study_type": design.get("studyType"),
        "phases": design.get("phases", []),
        "start_date": status.get("startDateStruct", {}).get("date"),
        "conditions": protocol.get("conditionsModule", {}).get("conditions", []),
        "interventions": [i["name"] for i in interventions if i.get("name")],
        "sponsor": protocol.get("sponsorCollaboratorsModule", {})
        .get("leadSponsor", {})
        .get("name"),
    }
