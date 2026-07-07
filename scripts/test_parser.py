"""Quick self-test for the VE.Direct parser using synthetic MPPT frames."""
from __future__ import annotations

from app.vedirect import VEDirectParser, decode_frame


def make_frame(fields: list[tuple[str, str]]) -> bytes:
    """Build a valid VE.Direct text frame (fields + trailing checksum byte)."""
    body = bytearray()
    for label, value in fields:
        body += b"\r\n" + label.encode("latin-1") + b"\t" + value.encode("latin-1")
    body += b"\r\n" + b"Checksum" + b"\t"
    running = sum(body) % 256
    checksum_byte = (256 - running) % 256
    body += bytes((checksum_byte,))
    return bytes(body)


def main() -> None:
    fields = [
        ("PID", "0xA057"),
        ("FW", "159"),
        ("SER#", "HQ2043ABCDE"),
        ("V", "13120"),      # 13.12 V battery
        ("I", "2450"),       # 2.45 A charging
        ("VPV", "18740"),    # 18.74 V panel
        ("PPV", "32"),       # 32 W
        ("IL", "800"),       # 0.8 A load
        ("LOAD", "ON"),
        ("CS", "3"),         # Bulk
        ("MPPT", "2"),       # MPP tracking
        ("ERR", "0"),
        ("H19", "1234"),     # 12.34 kWh total
        ("H20", "56"),       # 0.56 kWh today
        ("H21", "88"),       # 88 W max today
        ("HSDS", "42"),
        ("FOO", "bar"),      # unknown label -> extras
    ]
    frame_bytes = make_frame(fields)

    parser = VEDirectParser()
    # Split across chunk boundaries to exercise the streaming state machine.
    frames = []
    for i in range(0, len(frame_bytes), 7):
        frames += parser.parse(frame_bytes[i : i + 7])

    assert len(frames) == 1, f"expected 1 frame, got {len(frames)}"
    raw = frames[0]
    decoded, extras = decode_frame(raw)

    assert decoded["battery_voltage_V"] == 13.12, decoded
    assert decoded["battery_current_A"] == 2.45, decoded
    assert decoded["pv_voltage_V"] == 18.74, decoded
    assert decoded["pv_power_W"] == 32, decoded
    assert decoded["load_current_A"] == 0.8, decoded
    assert decoded["load_state"] == "ON", decoded
    assert decoded["charge_state_code"] == 3, decoded
    assert decoded["yield_total_kWh"] == 12.34, decoded
    assert decoded["yield_today_kWh"] == 0.56, decoded
    assert extras == {"FOO": "bar"}, extras

    # A corrupted checksum must be rejected.
    bad = bytearray(frame_bytes)
    bad[-1] ^= 0xFF
    p2 = VEDirectParser()
    assert p2.parse(bytes(bad)) == [], "corrupt frame should be dropped"

    print("PARSER_OK")
    print("decoded:", decoded)
    print("extras:", extras)


if __name__ == "__main__":
    main()
