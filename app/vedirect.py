"""VE.Direct text-protocol parser and background serial reader.

The VE.Direct text protocol streams "frames" roughly once per second. A frame is
a set of newline-delimited ``<label>\\t<value>`` fields, terminated by a
``Checksum\\t<byte>`` field. The frame is valid when the arithmetic sum of every
byte in the frame (labels, tabs, values, delimiters and the checksum byte itself)
is a multiple of 256.

Reference protocol: https://www.victronenergy.com/support-and-downloads/technical-information
Parser structure mirrors the widely used byte state machine (Janne Kario's
``vedirect``), extended to skip interleaved HEX-protocol messages.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable

import serial

log = logging.getLogger(__name__)

_HEADER1 = ord("\r")  # 0x0D
_HEADER2 = ord("\n")  # 0x0A
_DELIMITER = ord("\t")  # 0x09
_HEXMARKER = ord(":")  # 0x3A


class VEDirectParser:
    """Feed raw bytes, get back complete validated frames (dict[str, str])."""

    (WAIT_HEADER, IN_KEY, IN_VALUE, IN_CHECKSUM, IN_HEX) = range(5)

    def __init__(self) -> None:
        self.state = self.WAIT_HEADER
        self.key = bytearray()
        self.value = bytearray()
        self.bytes_sum = 0
        self.frame: dict[str, str] = {}

    def parse(self, data: bytes) -> list[dict[str, str]]:
        frames: list[dict[str, str]] = []
        for b in data:
            frame = self._input(b)
            if frame is not None:
                frames.append(frame)
        return frames

    def _input(self, b: int) -> dict[str, str] | None:
        # A ':' outside the checksum byte marks the start of an interleaved HEX
        # message, which is not part of the text-frame checksum.
        if b == _HEXMARKER and self.state != self.IN_CHECKSUM:
            self.state = self.IN_HEX
            return None

        if self.state == self.WAIT_HEADER:
            self.bytes_sum += b
            if b == _HEADER2:
                self.state = self.IN_KEY
            return None

        if self.state == self.IN_KEY:
            self.bytes_sum += b
            if b == _DELIMITER:
                if bytes(self.key) == b"Checksum":
                    self.state = self.IN_CHECKSUM
                else:
                    self.state = self.IN_VALUE
            else:
                self.key += bytes((b,))
            return None

        if self.state == self.IN_VALUE:
            self.bytes_sum += b
            if b == _HEADER1:
                # End of this field; commit it and wait for the next header.
                self.state = self.WAIT_HEADER
                try:
                    self.frame[self.key.decode("latin-1")] = self.value.decode("latin-1")
                except Exception:
                    pass
                self.key = bytearray()
                self.value = bytearray()
            else:
                self.value += bytes((b,))
            return None

        if self.state == self.IN_CHECKSUM:
            self.bytes_sum += b
            self.key = bytearray()
            self.value = bytearray()
            self.state = self.WAIT_HEADER
            valid = (self.bytes_sum % 256) == 0
            self.bytes_sum = 0
            if valid:
                out = self.frame
                self.frame = {}
                return out
            log.debug("VE.Direct checksum mismatch; dropping frame")
            self.frame = {}
            return None

        if self.state == self.IN_HEX:
            # HEX messages end with '\n'; they do not contribute to the checksum.
            self.bytes_sum = 0
            if b == _HEADER2:
                self.state = self.WAIT_HEADER
            return None

        return None


# --- Field registry -------------------------------------------------------
# Ordered list of (ve_direct_label, csv_column, kind). "kind" drives scaling
# from the raw protocol units to engineering units and Python types.
#   mv_to_v : millivolts  -> volts (float)
#   ma_to_a : milliamps   -> amps  (float)
#   cx001   : value * 0.01 (e.g. 0.01 kWh yields)
#   int     : integer
#   str     : passthrough string
Kind = str
FIELD_REGISTRY: list[tuple[str, str, Kind]] = [
    ("V", "battery_voltage_V", "mv_to_v"),
    ("I", "battery_current_A", "ma_to_a"),
    ("VPV", "pv_voltage_V", "mv_to_v"),
    ("PPV", "pv_power_W", "int"),
    ("IL", "load_current_A", "ma_to_a"),
    ("LOAD", "load_state", "str"),
    ("Relay", "relay_state", "str"),
    ("CS", "charge_state_code", "int"),
    ("MPPT", "mppt_state_code", "int"),
    ("OR", "off_reason", "str"),
    ("ERR", "error_code", "int"),
    ("H19", "yield_total_kWh", "cx001"),
    ("H20", "yield_today_kWh", "cx001"),
    ("H21", "max_power_today_W", "int"),
    ("H22", "yield_yesterday_kWh", "cx001"),
    ("H23", "max_power_yesterday_W", "int"),
    ("HSDS", "day_sequence", "int"),
    ("T", "battery_temp_C", "int"),
    ("VS", "aux_voltage_V", "mv_to_v"),
    ("VM", "midpoint_voltage_V", "mv_to_v"),
    ("DM", "midpoint_deviation_permille", "int"),
    ("PID", "product_id", "str"),
    ("FW", "firmware", "str"),
    ("SER#", "serial_number", "str"),
]

# Numeric columns the GUI can plot on the dual-axis chart.
NUMERIC_COLUMNS: list[str] = [
    "battery_voltage_V",
    "battery_current_A",
    "pv_voltage_V",
    "pv_power_W",
    "load_current_A",
    "charge_state_code",
    "mppt_state_code",
    "error_code",
    "yield_total_kWh",
    "yield_today_kWh",
    "max_power_today_W",
    "battery_temp_C",
]

_LABEL_TO_ENTRY = {label: (col, kind) for label, col, kind in FIELD_REGISTRY}


def _convert(kind: Kind, raw: str) -> Any:
    raw = raw.strip()
    if raw == "":
        return None
    try:
        if kind == "mv_to_v":
            return round(int(raw) / 1000.0, 3)
        if kind == "ma_to_a":
            return round(int(raw) / 1000.0, 3)
        if kind == "cx001":
            return round(int(raw) * 0.01, 2)
        if kind == "int":
            return int(raw)
    except (ValueError, TypeError):
        return raw
    return raw


def decode_frame(frame: dict[str, str]) -> tuple[dict[str, Any], dict[str, str]]:
    """Split a raw VE.Direct frame into decoded (engineering) values and the
    set of labels we don't have a registry entry for.

    Returns ``(decoded, extras)`` where ``decoded`` is keyed by CSV column name
    and ``extras`` keeps any unrecognised labels verbatim so nothing is lost.
    """
    decoded: dict[str, Any] = {}
    extras: dict[str, str] = {}
    for label, value in frame.items():
        entry = _LABEL_TO_ENTRY.get(label)
        if entry is None:
            extras[label] = value
            continue
        col, kind = entry
        decoded[col] = _convert(kind, value)
    return decoded, extras


@dataclass
class ReaderState:
    connected: bool = False
    last_error: str | None = None
    last_frame_monotonic: float | None = None
    frames_received: int = 0
    raw: dict[str, str] = field(default_factory=dict)
    decoded: dict[str, Any] = field(default_factory=dict)
    extras: dict[str, str] = field(default_factory=dict)


class SerialReader:
    """Background thread that keeps the serial port open and parses frames.

    Decouples the ~1 Hz VE.Direct stream from the async poller: the reader owns
    the (blocking) serial I/O and publishes the latest decoded frame under a
    lock; the poller snapshots it at its own cadence for CSV + MAVLink.
    """

    def __init__(self, get_settings: Callable[[], Any]) -> None:
        self._get_settings = get_settings
        self._lock = threading.Lock()
        self._state = ReaderState()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="vedirect-reader", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=3.0)

    def snapshot(self) -> ReaderState:
        with self._lock:
            s = self._state
            fresh = ReaderState(
                connected=s.connected,
                last_error=s.last_error,
                last_frame_monotonic=s.last_frame_monotonic,
                frames_received=s.frames_received,
                raw=dict(s.raw),
                decoded=dict(s.decoded),
                extras=dict(s.extras),
            )
        return fresh

    def _set(self, **kwargs: Any) -> None:
        with self._lock:
            for k, v in kwargs.items():
                setattr(self._state, k, v)

    def _run(self) -> None:
        parser = VEDirectParser()
        ser: serial.Serial | None = None
        cur_port: str | None = None
        cur_baud: int | None = None
        while not self._stop.is_set():
            s = self._get_settings()
            port = s.serial_port
            baud = int(s.baud_rate)

            # (Re)open the port if it's closed or the config changed.
            if ser is None or not ser.is_open or port != cur_port or baud != cur_baud:
                if ser is not None:
                    try:
                        ser.close()
                    except Exception:
                        pass
                    ser = None
                parser = VEDirectParser()
                try:
                    ser = serial.Serial(port, baudrate=baud, timeout=1.0)
                    cur_port, cur_baud = port, baud
                    self._set(connected=True, last_error=None)
                    log.info("Opened VE.Direct serial port %s @ %d baud", port, baud)
                except Exception as e:
                    self._set(connected=False, last_error=f"Cannot open {port}: {e}")
                    log.warning("Serial open failed: %s", e)
                    self._stop.wait(2.0)
                    continue

            try:
                data = ser.read(256)
            except Exception as e:
                self._set(connected=False, last_error=f"Serial read error: {e}")
                log.warning("Serial read failed: %s", e)
                try:
                    ser.close()
                except Exception:
                    pass
                ser = None
                self._stop.wait(1.0)
                continue

            if not data:
                continue

            for frame in parser.parse(data):
                decoded, extras = decode_frame(frame)
                with self._lock:
                    self._state.raw = frame
                    self._state.decoded = decoded
                    self._state.extras = extras
                    self._state.last_frame_monotonic = time.monotonic()
                    self._state.frames_received += 1
                    self._state.connected = True
                    self._state.last_error = None

        if ser is not None:
            try:
                ser.close()
            except Exception:
                pass
