from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import log
from app.api import meta
from app.config import settings

log.setup(settings.log_level, settings.log_format, settings.secrets())

app = FastAPI(title="Ops Agent Harness")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.cors_origins.split(",") if o.strip()],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(meta.router)
