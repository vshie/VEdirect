from __future__ import annotations

import json
import os
from pathlib import Path
from threading import Lock
from typing import Any

from pydantic import BaseModel, Field


def _data_dir() -> Path:
    return Path(os.environ.get("DATA_DIR", "/data"))


class AppSettings(BaseModel):
    """Configuration for the Victron VE.Direct monitor.

    Defaults target a Victron solar charge controller (MPPT) connected to
    /dev/ttyUSB0 on a BlueOS device, talking the VE.Direct text protocol at
    19200 baud, with metrics pushed to the autopilot via mavlink2rest.
    """

    # --- Serial / VE.Direct ---
    serial_port: str = "/dev/ttyUSB0"
    baud_rate: int = 19200
    # How often to snapshot the latest VE.Direct frame -> CSV row + MAVLink push.
    # The controller streams a frame ~once per second, so 1.0 s captures each one.
    poll_interval_s: float = Field(default=1.0, ge=0.2, le=60.0)
    # Treat the serial data as stale (device unplugged / powered off) after this
    # many seconds without a valid frame.
    stale_after_s: float = Field(default=10.0, ge=1.0, le=300.0)

    # --- mavlink2rest ---
    mavlink_rest_read_base: str = "http://host.docker.internal/mavlink2rest/mavlink"
    mavlink_rest_post_url: str = "http://host.docker.internal:6040/v1/mavlink"
    mavlink_enabled: bool = True
    # Emit VE_OK = 1.0 every cycle so the autopilot .BIN log explicitly records
    # the extension being alive even when the controller sends no useful data.
    emit_heartbeat: bool = True
    mavlink_header_system_id: int = 255
    # Base component_id for our NAMED_VALUE_FLOAT senders. Each metric occupies
    # base + offset (see app/mavlink_sender.py). Default 70 keeps clear of the
    # 25-28 range used by the BlueOS PH/TEMP/SALINITY/CONDUCT extension and the
    # 60-range used by the Mikrotik monitor extension.
    mavlink_header_component_id: int = 70
    target_system: int = 1
    target_component: int = 1

    # --- GUI chart (persisted so all clients share the same default view) ---
    # Column keys come from app/vedirect.py CSV_FIELDS numeric columns.
    chart_left_metric: str = "load_current_A"
    chart_right_metric: str = "pv_power_W"
    chart_window_minutes: float = Field(default=20.0, ge=1.0, le=1440.0)

    def merge(self, patch: dict[str, Any]) -> "AppSettings":
        data = self.model_dump()
        for k, v in patch.items():
            if k in data:
                data[k] = v
        return AppSettings.model_validate(data)


_lock = Lock()


def _settings_path() -> Path:
    return _data_dir() / "settings.json"


def ensure_data_dir() -> Path:
    d = _data_dir()
    d.mkdir(parents=True, exist_ok=True)
    return d


def load_settings() -> AppSettings:
    path = _settings_path()
    if not path.is_file():
        return AppSettings()
    with _lock:
        raw = path.read_text(encoding="utf-8")
    data = json.loads(raw) if raw.strip() else {}
    return AppSettings.model_validate(data)


def save_settings(settings: AppSettings) -> None:
    ensure_data_dir()
    path = _settings_path()
    tmp = path.with_suffix(".json.tmp")
    body = settings.model_dump_json(indent=2)
    with _lock:
        tmp.write_text(body, encoding="utf-8")
        tmp.replace(path)
