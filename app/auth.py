import secrets
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse, Response


PUBLIC_PATHS = ("/login", "/logout", "/static", "/favicon.ico")


def _is_public(path: str) -> bool:
    return any(path == p or path.startswith(p + "/") or path.startswith(p + "?") for p in PUBLIC_PATHS)


class AuthMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, auth_key: str):
        super().__init__(app)
        self.auth_key = auth_key
        self.enabled = bool(auth_key)

    async def dispatch(self, request: Request, call_next):
        if not self.enabled:
            return await call_next(request)

        if _is_public(request.url.path):
            return await call_next(request)

        if request.session.get("authed") is True:
            return await call_next(request)

        if request.headers.get("HX-Request") == "true":
            resp = Response(status_code=401)
            resp.headers["HX-Redirect"] = "/login"
            return resp

        if request.url.path.startswith("/api/"):
            return JSONResponse({"detail": "Not authenticated"}, status_code=401)

        next_url = request.url.path
        if request.url.query:
            next_url = f"{next_url}?{request.url.query}"
        return RedirectResponse(url=f"/login?next={next_url}", status_code=303)


def check_key(provided: str, expected: str) -> bool:
    if not expected:
        return False
    return secrets.compare_digest(provided.encode("utf-8"), expected.encode("utf-8"))
