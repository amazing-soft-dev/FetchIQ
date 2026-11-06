"""Response models for every JSON route.

Field names match what the presenters in the route modules already produce, so
the frontend's lib/types.ts keeps working unchanged; the point is that
GET /openapi.json now carries a schema for every route, which P2-2b turns into
generated TypeScript types.
"""

from app.schemas.campaigns import (
    ActiveCampaign,
    CampaignCounts,
    CampaignFailure,
    CampaignOut,
    MailCheckOut,
)
from app.schemas.contacts import (
    BulkStatusResult,
    ContactLoginsOut,
    ContactOut,
    ContactPageOut,
    CountryList,
    Deleted,
    OutreachStatusOut,
    StatusChange,
    StatusList,
)
from app.schemas.jobs import (
    AnalysisOut,
    JobCounts,
    JobList,
    JobOut,
    JobSummary,
    OverlapJob,
    OverlapPreview,
    PlaceReport,
    ReadyOut,
    RequestBudget,
    SliceCap,
)
from app.schemas.presets import GeoCatalog, PresetList, PresetOut

__all__ = [
    "ActiveCampaign",
    "AnalysisOut",
    "BulkStatusResult",
    "CampaignCounts",
    "CampaignFailure",
    "CampaignOut",
    "ContactLoginsOut",
    "ContactOut",
    "ContactPageOut",
    "CountryList",
    "Deleted",
    "GeoCatalog",
    "JobCounts",
    "JobList",
    "JobOut",
    "JobSummary",
    "MailCheckOut",
    "OutreachStatusOut",
    "OverlapJob",
    "OverlapPreview",
    "PlaceReport",
    "PresetList",
    "PresetOut",
    "ReadyOut",
    "RequestBudget",
    "SliceCap",
    "StatusChange",
    "StatusList",
]
