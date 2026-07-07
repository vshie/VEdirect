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

# Logs rotate weekly. File names embed the ISO year + week (zero-padded), so a
# plain lexicographic sort is chronological, including across year boundaries
# (e.g. "vedirect_2026-W52.csv" < "vedirect_2027-W01.csv").
_FILE_PREFIX = "vedirect_"
_FILE_GLOB = f"{_FILE_PREFIX}*.csv"


def _weekly_path(data_dir: Path, dt: datetime) -> Path:
    iso = dt.isocalendar()  # (year, week, weekday)
    return data_dir / f"{_FILE_PREFIX}{iso[0]}-W{iso[1]:02d}.csv"


def current_csv_path(data_dir: Path) -> Path:
    return _weekly_path(data_dir, datetime.now(timezone.utc))


def list_csv_files(data_dir: Path) -> list[Path]:
    if not data_dir.is_dir():
        return []
    return sorted(data_dir.glob(_FILE_GLOB))


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
    path = current_csv_path(data_dir)
    write_header = not path.is_file() or path.stat().st_size == 0
    line = {k: row.get(k, "") for k in CSV_FIELDS}
    with _lock:
        with path.open("a", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=CSV_FIELDS)
            if write_header:
                w.writeheader()
            w.writerow(line)


def _read_file_rows(path: Path) -> list[dict[str, Any]]:
    with path.open("r", newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def read_history(data_dir: Path, minutes: float = 20.0) -> list[dict[str, Any]]:
    """Return rows from the last `minutes`, spanning weekly files as needed.

    Files are read newest-first; because both the file names and the rows within
    each file are chronological, we can stop as soon as we reach a non-empty file
    with no rows inside the window.
    """
    cutoff = datetime.now(timezone.utc).timestamp() - minutes * 60.0
    rows: list[dict[str, Any]] = []
    with _lock:
        for path in reversed(list_csv_files(data_dir)):
            file_rows = _read_file_rows(path)
            kept: list[dict[str, Any]] = []
            for row in file_rows:
                ts = row.get("timestamp_utc") or ""
                try:
                    t = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                except ValueError:
                    continue
                if t.timestamp() >= cutoff:
                    kept.append(row)
            rows = kept + rows
            if file_rows and not kept:
                break
    return rows


def csv_path_for_download(data_dir: Path) -> Path | None:
    """Newest weekly CSV (what the 'download current log' link points at)."""
    files = list_csv_files(data_dir)
    return files[-1] if files else None
