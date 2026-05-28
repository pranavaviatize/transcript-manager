from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from contextlib import asynccontextmanager
import os

from app.database import engine, Base, init_fts
from app.routers import api, web
from app.config import get_settings

settings = get_settings()
os.makedirs(settings.upload_dir, exist_ok=True)
os.makedirs("data", exist_ok=True)

Base.metadata.create_all(bind=engine)
init_fts()


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield


app = FastAPI(title="Transcript Hub", lifespan=lifespan)
app.mount("/static", StaticFiles(directory="app/static"), name="static")
app.include_router(api.router)
app.include_router(web.router)
