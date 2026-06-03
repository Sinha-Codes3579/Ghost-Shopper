"""Store Intelligence API — Brigade Bangalore (ST1008) FastAPI entrypoint with structured JSON logging and graceful degradation."""

import json
import time
import uuid
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse

from database import init_db, load_pos_data
from ingestion import router as ingest_router
from metrics import router as metrics_router
from funnel import router as funnel_router
from heatmap import router as heatmap_router
from anomalies import router as anomalies_router
from health import router as health_router


# ── Structured JSON logging ──────────────────────────────────────────────────
class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return json.dumps({
            "time":    self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level":   record.levelname,
            "logger":  record.name,
            "msg":     record.getMessage(),
        })

handler = logging.StreamHandler()
handler.setFormatter(JsonFormatter())
logging.root.setLevel(os.getenv("LOG_LEVEL", "INFO"))
logging.root.handlers = [handler]
logger = logging.getLogger("api")


# ── Lifespan ─────────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting Store Intelligence API")
    try:
        init_db()
        load_pos_data()
        logger.info("DB initialised and POS data loaded")
    except Exception as e:
        logger.error(f"Startup error (non-fatal): {e}")
    yield
    logger.info("Shutting down")


app = FastAPI(
    title="Store Intelligence API",
    version="1.0.0",
    description="Retail store analytics for Brigade Bangalore (ST1008)",
    lifespan=lifespan,
)


# ── Request logging middleware ───────────────────────────────────────────────
@app.middleware("http")
async def logging_middleware(request: Request, call_next):
    trace_id = str(uuid.uuid4())[:8]
    store_id = request.path_params.get("store_id", "-")
    start    = time.time()

    try:
        response: Response = await call_next(request)
        latency_ms = round((time.time() - start) * 1000, 1)
        event_count = response.headers.get("X-Event-Count", "-")
        logger.info(json.dumps({
            "trace_id":    trace_id,
            "store_id":    store_id,
            "method":      request.method,
            "endpoint":    request.url.path,
            "status_code": response.status_code,
            "latency_ms":  latency_ms,
            "event_count": event_count,
        }))
        response.headers["X-Trace-Id"] = trace_id
        return response

    except Exception as e:
        latency_ms = round((time.time() - start) * 1000, 1)
        logger.error(json.dumps({
            "trace_id":   trace_id,
            "store_id":   store_id,
            "endpoint":   request.url.path,
            "error":      str(e),
            "latency_ms": latency_ms,
        }))
        # Graceful degradation — no raw stack traces in response
        return JSONResponse(
            status_code=500,
            content={
                "error":     "internal_server_error",
                "trace_id":  trace_id,
                "message":   "An unexpected error occurred. Check logs.",
            },
        )


# ── Routers ──────────────────────────────────────────────────────────────────
app.include_router(ingest_router)
app.include_router(metrics_router)
app.include_router(funnel_router)
app.include_router(heatmap_router)
app.include_router(anomalies_router)
app.include_router(health_router)
