from __future__ import annotations

import asyncio
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import Body, FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from app.csv_log import csv_path_for_download, read_history
from app.poller import READER, get_state, poller_loop
from app.settings_store import ensure_data_dir, load_settings, save_settings
from app.vedirect import NUMERIC_COLUMNS

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    stop = asyncio.Event()
    task = asyncio.create_task(poller_loop(stop), name="poller_loop")
    try:
        yield
    finally:
        stop.set()
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass


app = FastAPI(title="Victron VE.Direct Monitor", lifespan=lifespan)

if STATIC_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/")
async def root():
    index = STATIC_DIR / "index.html"
    if not index.is_file():
        return PlainTextResponse("UI missing; add static/index.html", status_code=500)
    return FileResponse(index)


@app.get("/register_service")
async def register_service():
    payload = {
        "name": "Victron VE.Direct Monitor",
        "description": (
            "Reads a Victron solar charge controller over VE.Direct, logs all "
            "data to CSV, and publishes PV voltage/power, load current and "
            "battery voltage to the autopilot as MAVLink NamedValueFloat."
        ),
        "icon": "mdi-solar-power",
        "company": "Community",
        "version": "1.0.0",
        "webpage": "https://github.com/vshie/VEdirect",
        "api": "https://github.com/vshie/VEdirect",
        "works_in_relative_paths": True,
    }
    return JSONResponse(payload)


@app.get("/api/status")
async def api_status():
    st = get_state()
    cfg = load_settings()
    snap = READER.snapshot()
    now = time.monotonic()
    age = (
        None
        if snap.last_frame_monotonic is None
        else round(now - snap.last_frame_monotonic, 1)
    )
    fresh = age is not None and age <= float(cfg.stale_after_s)
    return {
        "connected": snap.connected,
        "reader_error": snap.last_error,
        "frames_received": snap.frames_received,
        "seconds_since_last_frame": age,
        "fresh": fresh,
        "decoded": snap.decoded,
        "raw": snap.raw,
        "extras": snap.extras,
        "rows_logged": st.rows_logged,
        "last_csv_error": st.last_csv_error,
        "last_mavlink_errors": st.last_mavlink_errors,
        "mavlink_enabled": cfg.mavlink_enabled,
        "serial_port": cfg.serial_port,
    }


@app.get("/api/metrics")
async def api_metrics():
    """Numeric columns available for the configurable chart."""
    return {"numeric_columns": NUMERIC_COLUMNS}


@app.get("/api/settings")
async def api_get_settings():
    ensure_data_dir()
    return load_settings().model_dump()


@app.put("/api/settings")
async def api_put_settings(body: dict[str, Any] = Body(...)):
    cur = load_settings()
    try:
        merged = cur.merge(body)
        save_settings(merged)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return merged.model_dump()


@app.get("/api/history")
async def api_history(minutes: float = 20.0):
    rows = read_history(ensure_data_dir(), minutes=minutes)
    return {"minutes": minutes, "points": rows}


@app.get("/api/download/csv")
async def api_download_csv():
    path = csv_path_for_download(ensure_data_dir())
    if not path.is_file():
        raise HTTPException(status_code=404, detail="No CSV yet")
    return FileResponse(path, filename="vedirect_log.csv", media_type="text/csv")
