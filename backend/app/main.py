import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from starlette.exceptions import HTTPException as StarletteHTTPException

from app import log
from app.api import MaskedJSONResponse, eval, meta, runs
from app.config import cfg, settings
from app.harness.runner import Conflict, NotFound, Runner
from app.kb import qdrant
from app.kb.ingest import ingest

log.setup(settings.log_level, settings.log_format, settings.secrets())
api_log = logging.getLogger("app.api")


@asynccontextmanager
async def lifespan(app: FastAPI):
    runner = await Runner.open(settings.db_path)
    app.state.runner = runner
    sweep = None
    try:
        # Only the API recovers: runs left `running` by a crash become `interrupted` (ADR 0013). The CLI never does.
        await runner.recover()
        # The API starts even without a knowledge base; the search tool then returns `unavailable`.
        try:
            await ingest(qdrant.get_kb(), settings.data_dir / "kb")
        except Exception as exc:
            logging.getLogger("app.kb").warning("knowledge base not indexed: %s: %s", type(exc).__name__, exc)
        sweep = asyncio.create_task(runner.sweep_forever(cfg.approval.sweep_s))
        yield
    finally:
        if sweep is not None:
            sweep.cancel()
            await asyncio.gather(sweep, return_exceptions=True)
        await runner.close()  # cancels running segments; their runs stay `running` until the next start


app = FastAPI(title="Ops Agent Harness", lifespan=lifespan, default_response_class=MaskedJSONResponse)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.cors_origins.split(",") if o.strip()],
    allow_methods=["*"],
    allow_headers=["*"],
)


# FastAPI's and Starlette's own handlers answer with a plain JSONResponse, which would echo a secret sent
# in the request (a validation error repeats the input). These two keep their bodies and mask them.
@app.exception_handler(RequestValidationError)
async def request_invalid(request: Request, exc: RequestValidationError) -> MaskedJSONResponse:
    return MaskedJSONResponse({"detail": jsonable_encoder(exc.errors())}, status_code=422)


@app.exception_handler(StarletteHTTPException)
async def http_error(request: Request, exc: StarletteHTTPException) -> MaskedJSONResponse:
    return MaskedJSONResponse({"detail": exc.detail}, status_code=exc.status_code, headers=exc.headers)


@app.exception_handler(NotFound)
async def not_found(request: Request, exc: NotFound) -> MaskedJSONResponse:
    return MaskedJSONResponse({"detail": str(exc)}, status_code=404)


@app.exception_handler(Conflict)
async def conflict(request: Request, exc: Conflict) -> MaskedJSONResponse:
    return MaskedJSONResponse({"detail": str(exc)}, status_code=409)


@app.exception_handler(Exception)
async def internal_error(request: Request, exc: Exception) -> MaskedJSONResponse:
    api_log.error("unexpected error on %s %s", request.method, request.url.path, exc_info=exc)  # traceback: log only
    return MaskedJSONResponse({"detail": "internal error"}, status_code=500)


app.include_router(runs.router)
app.include_router(eval.router)
app.include_router(meta.router)
