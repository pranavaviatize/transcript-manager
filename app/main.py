from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware
from contextlib import asynccontextmanager
import os
import secrets
import logging

from app.database import engine, Base, init_fts, init_vec, init_chunks_fts
from app.routers import api, web, chat, auth as auth_router
from app.auth import AuthMiddleware
from app.config import get_settings

logger = logging.getLogger(__name__)

settings = get_settings()
os.makedirs(settings.upload_dir, exist_ok=True)
os.makedirs("data", exist_ok=True)

Base.metadata.create_all(bind=engine)
init_fts()
init_vec()
init_chunks_fts()


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield


app = FastAPI(title="Transcript Hub", lifespan=lifespan)

if not settings.auth_key:
    logger.warning("AUTH_KEY is not set — the app is running WITHOUT authentication.")

session_secret = settings.session_secret or secrets.token_urlsafe(32)
if settings.auth_key and not settings.session_secret:
    logger.warning("SESSION_SECRET not set — using a random secret. Sessions will not survive restart.")

app.add_middleware(AuthMiddleware, auth_key=settings.auth_key)
app.add_middleware(SessionMiddleware, secret_key=session_secret, same_site="lax", https_only=False)

app.mount("/static", StaticFiles(directory="app/static"), name="static")
app.include_router(auth_router.router)
app.include_router(api.router)
app.include_router(web.router)
app.include_router(chat.router)
