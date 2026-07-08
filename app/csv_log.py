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
# (e.g. "vedirect_2026-W52.csv" < "vedirect_2027-W01.csv"). The glob is
# restricted to the dated pattern so unrelated files (e.g. a legacy
# "vedirect_log.csv" from before rotation) are not swept into history reads,
# where they would sort out of order and be parsed needlessly.
_FILE_PREFIX = "vedirect_"
_FILE_GLOB = f"{_FILE_PREFIX}[0-9][0-9][0-9][0-9]-W[0-9][0-9].csv"


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


# Upper bound on points returned by read_history. A long window (e.g. 12 h at
# 1 Hz ≈ 43k rows) would otherwise return a huge payload that both pegs the CPU
# on every poll and overwhelms the browser chart. We decimate to at most this
# many evenly spaced points; the chart doesn't need finer resolution.
DEFAULT_MAX_POINTS = 1500


def _ts_of_line(line: str) -> float | None:
    """Parse the leading timestamp column of a raw CSV data line.

    The timestamp is the first field and ISO-8601 (no commas), so splitting on
    the first comma is safe and far cheaper than csv-parsing the whole file.
    """
    head = line.split(",", 1)[0]
    if not head:
        return None
    try:
        return datetime.fromisoformat(head.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def read_history(
    data_dir: Path,
    minutes: float = 20.0,
    max_points: int = DEFAULT_MAX_POINTS,
) -> list[dict[str, Any]]:
    """Return decimated rows from the last `minutes`, spanning weekly files.

    Files (and rows within them) are chronological, so we scan newest-first from
    the *end* of each file and stop as soon as we cross the cutoff. Only the rows
    inside the window are CSV-parsed, and the result is decimated to at most
    `max_points` so both the request cost and the payload stay bounded regardless
    of how large the window or the log files are.
    """
    cutoff = datetime.now(timezone.utc).timestamp() - minutes * 60.0
    header: str | None = None
    kept_lines: list[str] = []  # oldest -> newest across all files
    with _lock:
        for path in reversed(list_csv_files(data_dir)):
            try:
                text = path.read_text(encoding="utf-8")
            except OSError:
                continue
            lines = text.splitlines()
            if not lines:
                continue
            if header is None:
                header = lines[0]
            file_kept: list[str] = []
            crossed = False
            for line in reversed(lines[1:]):
                if not line:
                    continue
                t = _ts_of_line(line)
                if t is None:
                    continue
                if t >= cutoff:
                    file_kept.append(line)
                else:
                    crossed = True
                    break
            file_kept.reverse()
            kept_lines = file_kept + kept_lines
            if crossed:
                break
    if header is None or not kept_lines:
        return []
    if max_points and len(kept_lines) > max_points:
        stride = -(-len(kept_lines) // max_points)  # ceil division
        kept_lines = kept_lines[::stride]
    return list(csv.DictReader([header] + kept_lines))


def csv_path_for_download(data_dir: Path) -> Path | None:
    """Newest weekly CSV (what the 'download current log' link points at)."""
    files = list_csv_files(data_dir)
    return files[-1] if files else None
