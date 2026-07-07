from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any

from app.vedirect import FIELD_REGISTRY

# Fixed, stable header. Every decoded engineering column comes first (blank when
# the controller didn't send that label), and any labels we don't recognise are
# preserved verbatim in the trailing JSON column so no data is ever dropped.
CSV_FIELDS: list[str] = (
    ["timestamp_utc"] + [col for _label, col, _kind in FIELD_REGISTRY] + ["extra_json"]
)


def _csv_path(data_dir: Path) -> Path:
    return data_dir / "vedirect_log.csv"


_lock = Lock()


def build_row(decoded: dict[str, Any], extras: dict[str, str]) -> dict[str, Any]:
    ts = datetime.now(timezone.utc).isoformat()
    row: dict[str, Any] = {"timestamp_utc": ts}
    for _label, col, _kind in FIELD_REGISTRY:
        v = decoded.get(col)
        row[col] = "" if v is None else v
    row["extra_json"] = json.dumps(extras, separators=(",", ":")) if extras else ""
    return row


def append_row(data_dir: Path, row: dict[str, Any]) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    path = _csv_path(data_dir)
    write_header = not path.is_file() or path.stat().st_size == 0
    line = {k: row.get(k, "") for k in CSV_FIELDS}
    with _lock:
        with path.open("a", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=CSV_FIELDS)
            if write_header:
                w.writeheader()
            w.writerow(line)


def read_history(data_dir: Path, minutes: float = 20.0) -> list[dict[str, Any]]:
    path = _csv_path(data_dir)
    if not path.is_file():
        return []
    cutoff = datetime.now(timezone.utc).timestamp() - minutes * 60.0
    rows: list[dict[str, Any]] = []
    with _lock:
        with path.open("r", newline="", encoding="utf-8") as f:
            r = csv.DictReader(f)
            for row in r:
                ts = row.get("timestamp_utc") or ""
                try:
                    t = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                except ValueError:
                    continue
                if t.timestamp() >= cutoff:
                    rows.append(row)
    return rows


def csv_path_for_download(data_dir: Path) -> Path:
    return _csv_path(data_dir)
