"""P0 repro (#6): a sensors tool must return real temps or a structured error.

There is no CPU-temperature path today — tools/system_stats.py reads none, and
psutil's sensors_temperatures() is effectively Linux-only. P1 adds
tools/sensors.py::get_sensors backed by LibreHardwareMonitor + WMI with
graceful degradation: real readings when LHM is up, a structured
{"error": ..., "hint": ...} when it is not. Never empty, never silent.

This test fails today (module tools.sensors does not exist) and goes green
once P1 lands. It pins the contract without dictating the exact schema.
"""

from __future__ import annotations


def _has_temperature(obj) -> bool:
    """True if any nested key mentions a temperature reading."""
    if isinstance(obj, dict):
        for key, value in obj.items():
            if "temp" in str(key).lower():
                return True
            if _has_temperature(value):
                return True
    elif isinstance(obj, list):
        return any(_has_temperature(item) for item in obj)
    return False


def test_get_sensors_never_empty_or_silent():
    from tools.sensors import get_sensors  # built in P1

    result = get_sensors()
    assert isinstance(result, dict)
    assert result, "sensors returned an empty dict — that is the silent-failure bug"

    if "error" in result:
        # Graceful-degradation path: say what is missing and how to fix it.
        assert result["error"], "error message must not be blank"
        assert "hint" in result, "degraded sensors must teach the fix (hint)"
    else:
        # Real-data path: at least one temperature reading present.
        assert _has_temperature(result), "sensor success must carry temperature data"
