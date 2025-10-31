from __future__ import annotations
import re
from datetime import timedelta

_UNIT = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}
_RX = re.compile(r"(\d+)\s*([smhdw])", re.I)

def parse_duration_to_seconds(spec: str) -> int:
    spec = spec.strip().lower()
    total = 0
    for value, unit in _RX.findall(spec):
        total += int(value) * _UNIT[unit]
    if total <= 0:
        raise ValueError("Invalid or zero duration")
    return total

def parse_duration(spec: str) -> timedelta:
    return timedelta(seconds=parse_duration_to_seconds(spec))
