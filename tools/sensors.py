"""get_sensors: CPU/GPU temperatures, fan RPM and voltages via WMI.

Windows exposes no CPU temperature through psutil — that API is effectively
Linux-only — so Baby reads LibreHardwareMonitor's WMI publisher at
``root\\LibreHardwareMonitor``. LHM must run with its WMI option enabled
(scripts\\setup.ps1 installs and autostarts it; the kernel driver needs a
one-time admin approval). When LHM is absent the tool degrades to a structured
error that names the fix instead of returning nothing — the agent loop then
tells the user exactly what is missing rather than going silent.
"""

from __future__ import annotations

from tools.registry import tool

_NAMESPACE = "root\\LibreHardwareMonitor"
_SETUP_HINT = (
    "start LibreHardwareMonitor with its 'WMI' option and 'Run on Windows "
    "startup' enabled; scripts\\setup.ps1 installs and autostarts it"
)


def _connect():
    """A WMI client bound to the LHM namespace. Raises if unavailable."""
    import wmi  # lazy; pywin32-backed, Windows-only

    return wmi.WMI(namespace=_NAMESPACE)


@tool
def get_sensors(detail: bool = False) -> dict:
    """CPU/GPU temperatures, fan RPM and voltages from LibreHardwareMonitor."""
    try:
        client = _connect()
        sensors = client.Sensor()
    except ModuleNotFoundError:
        return {
            "error": "sensor source unavailable: the 'wmi' package is not installed",
            "hint": "pip install wmi (pywin32); " + _SETUP_HINT,
        }
    except Exception as exc:  # noqa: BLE001 — LHM down / namespace missing / read failed
        return {
            "error": f"sensor source unavailable: LibreHardwareMonitor not running ({exc})",
            "hint": _SETUP_HINT,
        }

    temps: list[dict] = []
    fans: list[dict] = []
    voltages: list[dict] = []
    for s in sensors:
        try:
            stype, value, name = s.SensorType, s.Value, s.Name
        except Exception:  # noqa: BLE001 — a torn sensor row must not end the scan
            continue
        if value is None:
            continue
        if stype == "Temperature":
            temps.append({"name": name, "celsius": round(float(value), 1)})
        elif detail and stype == "Fan":
            fans.append({"name": name, "rpm": round(float(value))})
        elif detail and stype == "Voltage":
            voltages.append({"name": name, "volts": round(float(value), 3)})

    if not temps:
        return {
            "error": "sensor source unavailable: no temperature sensors reported",
            "hint": _SETUP_HINT,
        }

    result: dict = {
        "unit": "celsius",
        "hottest": max(temps, key=lambda t: t["celsius"]),
        "temperatures_c": temps,
    }
    if detail:
        result["fans_rpm"] = fans
        result["voltages_v"] = voltages
    return result
