from datetime import datetime
from fastapi.templating import Jinja2Templates

templates = Jinja2Templates(directory="app/templates")


def _age_days(value):
    """Return integer number of days between `value` (a datetime) and now (UTC)."""
    if value is None:
        return None
    now = datetime.utcnow()
    delta = now - value
    return max(0, delta.days)


def _age_tier(days):
    """Map an integer day count to a staleness tier: fresh / warm / stale / cold."""
    if days is None:
        return "fresh"
    if days < 7:
        return "fresh"
    if days < 14:
        return "warm"
    if days < 30:
        return "stale"
    return "cold"


templates.env.filters["age_days"] = _age_days
templates.env.filters["age_tier"] = _age_tier
