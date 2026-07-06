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


_LHM_SAMPLE = {
    "Text": "Sensor",
    "Value": "",
    "Children": [
        {
            "Text": "DESKTOP",
            "Value": "",
            "Children": [
                {
                    "Text": "AMD Ryzen 7 5800X",
                    "Value": "",
                    "Children": [
                        {
                            "Text": "Temperatures",
                            "Value": "",
                            "Children": [
                                {"Text": "Core (Tctl/Tdie)", "Value": "54.9 °C", "Children": []},
                                {"Text": "CCD1", "Value": "48.2 °C", "Children": []},
                            ],
                        },
                        {
                            "Text": "Fans",
                            "Value": "",
                            "Children": [
                                {"Text": "CPU Fan", "Value": "1234 RPM", "Children": []}
                            ],
                        },
                        {
                            "Text": "Voltages",
                            "Value": "",
                            "Children": [
                                {"Text": "Core", "Value": "1.381 V", "Children": []}
                            ],
                        },
                        {
                            "Text": "Clocks",
                            "Value": "",
                            "Children": [
                                {"Text": "Core #1", "Value": "4200.0 MHz", "Children": []}
                            ],
                        },
                    ],
                }
            ],
        }
    ],
}


def test_parse_sensor_tree_extracts_by_unit():
    from tools.sensors import parse_sensor_tree

    out = parse_sensor_tree(_LHM_SAMPLE, detail=True)
    assert out["unit"] == "celsius"
    assert out["hottest"]["celsius"] == 54.9  # the hottest reading
    temps = {t["name"]: t["celsius"] for t in out["temperatures_c"]}
    assert temps == {"Core (Tctl/Tdie)": 54.9, "CCD1": 48.2}
    assert out["fans_rpm"] == [{"name": "CPU Fan", "rpm": 1234}]
    assert out["voltages_v"] == [{"name": "Core", "volts": 1.381}]
    # Clocks (MHz) must NOT be miscounted as temp/fan/voltage.
    assert all("MHz" not in t["name"] for t in out["temperatures_c"])


def test_parse_sensor_tree_excludes_thresholds():
    from tools.sensors import parse_sensor_tree

    # LHM lists limits/thresholds under Temperatures with °C units — a Critical
    # High Limit (85 °C) must never count as a reading or become "hottest".
    root = {
        "Text": "Sensor",
        "Value": "",
        "Children": [
            {
                "Text": "Temperatures",
                "Value": "",
                "Children": [
                    {"Text": "Core (Tctl/Tdie)", "Value": "48.6 °C", "Children": []},
                    {"Text": "Thermal Sensor Critical High Limit", "Value": "85.0 °C", "Children": []},
                    {"Text": "Warning Temperature", "Value": "74.0 °C", "Children": []},
                    {"Text": "Temperature Sensor Resolution", "Value": "0.3 °C", "Children": []},
                ],
            }
        ],
    }
    out = parse_sensor_tree(root)
    names = [t["name"] for t in out["temperatures_c"]]
    assert names == ["Core (Tctl/Tdie)"]  # only the real reading survives
    assert out["hottest"]["celsius"] == 48.6  # NOT the 85 °C limit


def test_parse_sensor_tree_no_temps_is_structured_error():
    from tools.sensors import parse_sensor_tree

    root = {
        "Text": "Sensor",
        "Value": "",
        "Children": [
            {"Text": "Clocks", "Value": "", "Children": [
                {"Text": "Core", "Value": "4200 MHz", "Children": []}
            ]}
        ],
    }
    out = parse_sensor_tree(root)
    assert "error" in out and "hint" in out
