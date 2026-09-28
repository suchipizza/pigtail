"""Every structured-output schema the product sends to the API, with the job that sends it (ADR-086
addendum 1). Shared by the schema-size guard (unit) and the live contract smoke test. Add a new
schema here when a new structured call is added."""

from __future__ import annotations

from pydantic import BaseModel


def product_schemas() -> list[tuple[str, str, type[BaseModel]]]:
    """(name, job, schema) of every structured call of the product."""
    from pigtail.briefs.confirm import JOB as TITLE_JOB
    from pigtail.briefs.confirm import PH_JOB, TitleMatchVerdict
    from pigtail.briefs.expansion import JOB as EXPANSION_JOB
    from pigtail.briefs.expansion import ExpansionOutput
    from pigtail.briefs.relevance import JOB as RELEVANCE_JOB
    from pigtail.briefs.relevance import RelevanceOutput
    from pigtail.briefs.surface import JOB as SURFACE_JOB
    from pigtail.briefs.surface import SurfaceOutput
    from pigtail.forensics.frame import Adjudication, CaseCoding
    from pigtail.forensics.prompts import JOB_ADJUDICATION, JOB_CODING

    return [
        ("coder", JOB_CODING, CaseCoding),
        ("adjudicator", JOB_ADJUDICATION, Adjudication),
        ("relevance", RELEVANCE_JOB, RelevanceOutput),
        ("surface", SURFACE_JOB, SurfaceOutput),
        ("title_check", TITLE_JOB, TitleMatchVerdict),
        ("ph_check", PH_JOB, TitleMatchVerdict),
        ("expansion", EXPANSION_JOB, ExpansionOutput),
    ]
