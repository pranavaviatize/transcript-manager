from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from typing import Optional
from urllib.parse import urlparse

from app.templating import templates
from app.auth import check_key
from app.config import get_settings

router = APIRouter()


def _safe_next(next_url: Optional[str]) -> str:
    if not next_url:
        return "/"
    parsed = urlparse(next_url)
    if parsed.scheme or parsed.netloc:
        return "/"
    if not next_url.startswith("/"):
        return "/"
    return next_url


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, next: Optional[str] = None):
    if request.session.get("authed") is True:
        return RedirectResponse(url=_safe_next(next), status_code=303)
    return templates.TemplateResponse("login.html", {
        "request": request,
        "next": _safe_next(next),
        "error": None,
    })


@router.post("/login", response_class=HTMLResponse)
async def login_submit(
    request: Request,
    key: str = Form(...),
    next: Optional[str] = Form(None),
):
    settings = get_settings()
    if check_key(key, settings.auth_key):
        request.session["authed"] = True
        return RedirectResponse(url=_safe_next(next), status_code=303)

    return templates.TemplateResponse("login.html", {
        "request": request,
        "next": _safe_next(next),
        "error": "Invalid key.",
    }, status_code=401)


@router.post("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse(url="/login", status_code=303)
