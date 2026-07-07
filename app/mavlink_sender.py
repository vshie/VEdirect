from __future__ import annotations

from typing import Any

import httpx

# mavlink-server's in-memory store is shaped:
#   vehicles[system_id] -> components[component_id] -> messages[message_type] -> latest
# Every NAMED_VALUE_FLOAT we POST from the same (system_id, component_id) pair lands
# in the same slot, so the last write wins and only one metric survives for the
# inspector / .BIN log. Each metric therefore gets its own component_id, computed
# as `component_id_base + NAMED_VALUE_OFFSETS[name]`.
NAMED_VALUE_OFFSETS: dict[str, int] = {
    "VE_PV_V": 0,    # PV (solar) array voltage, volts
    "VE_PV_W": 1,    # PV (solar) array power, watts
    "VE_LOAD_A": 2,  # load output current, amps
    "VE_BAT_V": 3,   # battery voltage, volts
    "VE_BAT_A": 4,   # battery current, amps (positive = charging)
    # Heartbeat: emitted every cycle so the .BIN log unambiguously records the
    # extension being alive even when the controller sends no useful values.
    "VE_OK": 5,
}


def planned_component_ids(component_id_base: int) -> dict[str, int]:
    return {name: component_id_base + offset for name, offset in NAMED_VALUE_OFFSETS.items()}


def _nvf_name_field(name: str) -> list[str]:
    """10 single-char strings, null-padded (same shape as mavlink2rest)."""
    out: list[str] = []
    for i in range(10):
        out.append(name[i] if i < len(name) else "\x00")
    return out


def _nvf_payload(
    name: str,
    value: float,
    header_system_id: int,
    header_component_id: int,
) -> dict[str, Any]:
    return {
        "header": {
            "system_id": header_system_id,
            "component_id": header_component_id,
            "sequence": 0,
        },
        "message": {
            "type": "NAMED_VALUE_FLOAT",
            "time_boot_ms": 0,
            "value": float(value),
            "name": _nvf_name_field(name),
        },
    }


async def send_named_value_floats(
    post_url: str,
    client: httpx.AsyncClient,
    values: dict[str, float],
    header_system_id: int,
    component_id_base: int,
) -> list[str]:
    errors: list[str] = []
    for name, val in values.items():
        if val is None:  # type: ignore[comparison-overlap]
            continue
        offset = NAMED_VALUE_OFFSETS.get(name, 0)
        component_id = component_id_base + offset
        body = _nvf_payload(name, val, header_system_id, component_id)
        try:
            r = await client.post(post_url, json=body, timeout=2.0)
            if r.status_code >= 400:
                errors.append(f"{name}: HTTP {r.status_code} {r.text[:200]}")
        except Exception as e:
            errors.append(f"{name}: {e}")
    return errors
