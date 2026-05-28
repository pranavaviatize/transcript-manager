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


def _highlight(text, query):
    """Wrap case-insensitive matches of `query` in <mark> tags. Returns Markup."""
    from markupsafe import Markup, escape
    if not text:
        return Markup("")
    if not query:
        return Markup(escape(text))
    safe = str(escape(text))
    safe_q = str(escape(query))
    # Case-insensitive replace while preserving original casing
    lower = safe.lower()
    qlower = safe_q.lower()
    if qlower not in lower:
        return Markup(safe)
    out = []
    i = 0
    while True:
        j = lower.find(qlower, i)
        if j < 0:
            out.append(safe[i:])
            break
        out.append(safe[i:j])
        out.append("<mark>")
        out.append(safe[j : j + len(safe_q)])
        out.append("</mark>")
        i = j + len(safe_q)
    return Markup("".join(out))


templates.env.filters["age_days"] = _age_days
templates.env.filters["age_tier"] = _age_tier
templates.env.filters["highlight"] = _highlight
