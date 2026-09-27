import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import log
from app.api import meta
from app.config import settings
from app.kb import qdrant
from app.kb.ingest import ingest

log.setup(settings.log_level, settings.log_format, settings.secrets())


@asynccontextmanager
async def lifespan(app: FastAPI):
    # The API starts even without a knowledge base; the search tool then returns `unavailable`.
    try:
        await ingest(qdrant.get_kb(), settings.data_dir / "kb")
    except Exception as exc:
        logging.getLogger("app.kb").warning("knowledge base not indexed: %s: %s", type(exc).__name__, exc)
    yield


app = FastAPI(title="Ops Agent Harness", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.cors_origins.split(",") if o.strip()],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(meta.router)
