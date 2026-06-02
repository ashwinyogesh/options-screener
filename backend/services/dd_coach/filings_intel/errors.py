"""Domain errors for filings_intel. Routers map these to HTTP."""
from __future__ import annotations

from services.dd_coach.errors import DDCoachUnavailable, DDEntryInvalid, DDEntryNotFound


class FilingsIntelUnavailable(DDCoachUnavailable):
    """Underlying SEC or Azure OpenAI dependency is unreachable / unconfigured."""


class FilingNotFound(DDEntryNotFound):
    """The requested filing type doesn't exist for this ticker (e.g. no prior-year 10-K)."""


class InvalidInsightType(DDEntryInvalid):
    """Caller passed an unknown insight_type."""
