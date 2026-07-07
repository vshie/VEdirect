from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from threading import Lock
from typing import Any

import httpx

from app.csv_log import append_row, build_row
from app.mavlink_sender import send_named_value_floats
from app.settings_store import ensure_data_dir, load_settings
from app.vedirect import SerialReader

log = logging.getLogger(__name__)


@dataclass
class PollerState:
    rows_logged: int = 0
    last_csv_error: str | None = None
    last_mavlink_errors: list[str] = field(default_factory=list)


_state_lock = Lock()
STATE = PollerState()

# The serial reader is created once and shared with the poller loop. It owns the
# blocking serial I/O in its own thread; the loop just snapshots the latest frame.
READER = SerialReader(load_settings)


def get_state() -> PollerState:
    with _state_lock:
        return PollerState(
            rows_logged=STATE.rows_logged,
            last_csv_error=STATE.last_csv_error,
            last_mavlink_errors=list(STATE.last_mavlink_errors),
        )


def _update_state(**kwargs: Any) -> None:
    with _state_lock:
        for k, v in kwargs.items():
            setattr(STATE, k, v)


def _build_nvf(decoded: dict[str, Any], emit_heartbeat: bool) -> dict[str, float]:
    """The four metrics the operator asked for, plus battery current and an
    optional liveness heartbeat."""
    nvf: dict[str, float] = {}
    if emit_heartbeat:
        nvf["VE_OK"] = 1.0
    mapping = {
        "VE_PV_V": "pv_voltage_V",
        "VE_PV_W": "pv_power_W",
        "VE_LOAD_A": "load_current_A",
        "VE_BAT_V": "battery_voltage_V",
        "VE_BAT_A": "battery_current_A",
    }
    for nvf_name, col in mapping.items():
        v = decoded.get(col)
        if v is not None and isinstance(v, (int, float)):
            nvf[nvf_name] = float(v)
    return nvf


async def poller_loop(stop: asyncio.Event) -> None:
    READER.start()
    async with httpx.AsyncClient() as client:
        while not stop.is_set():
            s = load_settings()
            interval = float(s.poll_interval_s)
            snap = READER.snapshot()

            now = time.monotonic()
            stale = (
                snap.last_frame_monotonic is None
                or (now - snap.last_frame_monotonic) > float(s.stale_after_s)
            )

            # Log a CSV row from the freshest frame we have. Skip logging when
            # we've never seen a frame, but keep the loop (and heartbeat) alive.
            if snap.decoded and not stale:
                row = build_row(snap.decoded, snap.extras)
                try:
                    append_row(ensure_data_dir(), row)
                    with _state_lock:
                        STATE.rows_logged += 1
                        STATE.last_csv_error = None
                except Exception as e:
                    _update_state(last_csv_error=str(e))

            if s.mavlink_enabled:
                decoded = {} if stale else snap.decoded
                nvf = _build_nvf(decoded, s.emit_heartbeat)
                if nvf:
                    try:
                        errs = await send_named_value_floats(
                            s.mavlink_rest_post_url,
                            client,
                            nvf,
                            s.mavlink_header_system_id,
                            s.mavlink_header_component_id,
                        )
                        _update_state(last_mavlink_errors=errs)
                    except Exception as e:
                        _update_state(last_mavlink_errors=[str(e)])
                else:
                    _update_state(last_mavlink_errors=[])
            else:
                _update_state(last_mavlink_errors=[])

            try:
                await asyncio.wait_for(stop.wait(), timeout=interval)
            except asyncio.TimeoutError:
                pass

    READER.stop()
