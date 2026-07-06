"""get_sensors: CPU/GPU temperatures, fan RPM and voltages from LibreHardwareMonitor.

psutil reads no CPU temperature on Windows — that API is effectively Linux-only —
so Baby reads LibreHardwareMonitor's **Remote Web Server**: Options -> Remote Web
Server -> Run (default port 8085) publishes the whole sensor tree as JSON at
``/data.json``. (LHM's old WMI provider was dropped in the 0.9.x line, so the web
server is the current supported integration.) Run LHM as administrator so its
sensor driver populates. When the server is unreachable the tool returns a
structured error naming the fix instead of nothing — the agent loop then tells
the user exactly what is missing rather than going silent.

Override the endpoint with the ``LHM_URL`` env var if you run the web server on a
different port/host.
"""

from __future__ import annotations

import os

from tools.registry import tool

_LHM_URL = os.environ.get("LHM_URL", "http://127.0.0.1:8085/data.json")
_SETUP_HINT = (
    "in LibreHardwareMonitor enable Options -> Remote Web Server (Run, port 8085) "
    "and run LHM as administrator; scripts\\setup.ps1 installs and autostarts it"
)


def _num(value: str) -> float | None:
    """Leading number of an LHM value string ('54.9 °C' -> 54.9)."""
    try:
        return float(str(value).split()[0].replace(",", "."))
    except (ValueError, IndexError):
        return None


def _walk(node: dict, temps: list, fans: list, volts: list) -> None:
    """Collect leaf sensor readings from LHM's nested node tree by unit suffix."""
    value = node.get("Value") or ""
    children = node.get("Children") or []
    if value and not children:
        number = _num(value)
        if number is not None:
            name = node.get("Text", "")
            text = str(value)
            if "°C" in text:
                temps.append({"name": name, "celsius": round(number, 1)})
            elif text.rstrip().endswith("RPM"):
                fans.append({"name": name, "rpm": round(number)})
            elif text.rstrip().endswith(" V"):
                volts.append({"name": name, "volts": round(number, 3)})
    for child in children:
        _walk(child, temps, fans, volts)


def parse_sensor_tree(root: dict, detail: bool = False) -> dict:
    """LHM /data.json tree -> temps (+ fans/voltages when detail). Pure, testable."""
    temps: list[dict] = []
    fans: list[dict] = []
    volts: list[dict] = []
    _walk(root, temps, fans, volts)
    if not temps:
        return {
            "error": "sensor source unavailable: no temperatures reported "
            "(run LibreHardwareMonitor as administrator)",
            "hint": _SETUP_HINT,
        }
    result: dict = {
        "unit": "celsius",
        "hottest": max(temps, key=lambda t: t["celsius"]),
        "temperatures_c": temps,
    }
    if detail:
        result["fans_rpm"] = fans
        result["voltages_v"] = volts
    return result


@tool
def get_sensors(detail: bool = False) -> dict:
    """CPU/GPU temperatures, fan RPM and voltages from LibreHardwareMonitor."""
    import httpx

    try:
        resp = httpx.get(_LHM_URL, timeout=3.0)
        resp.raise_for_status()
        root = resp.json()
    except Exception as exc:  # noqa: BLE001 — server down / not enabled / bad JSON
        return {
            "error": f"sensor source unavailable: LibreHardwareMonitor web server "
            f"not reachable at {_LHM_URL} ({exc})",
            "hint": _SETUP_HINT,
        }
    return parse_sensor_tree(root, detail=detail)
