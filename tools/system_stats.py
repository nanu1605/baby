"""System stats tool: CPU, RAM, disk, GPU, top processes."""

from __future__ import annotations

import time

from tools.registry import tool

_nvml_ready: bool | None = None


def _gpu() -> dict | None:
    """GPU utilization + VRAM via NVML; None when unavailable (fail soft)."""
    global _nvml_ready
    try:
        import pynvml  # nvidia-ml-py; lazy — keeps CPU-only boots fast

        if _nvml_ready is None:
            pynvml.nvmlInit()
            _nvml_ready = True
        handle = pynvml.nvmlDeviceGetHandleByIndex(0)
        util = pynvml.nvmlDeviceGetUtilizationRates(handle)
        mem = pynvml.nvmlDeviceGetMemoryInfo(handle)
        name = pynvml.nvmlDeviceGetName(handle)
        return {
            "name": name if isinstance(name, str) else name.decode(),
            "util_percent": util.gpu,
            "vram_used_gb": round(mem.used / 1e9, 2),
            "vram_total_gb": round(mem.total / 1e9, 2),
        }
    except Exception:  # noqa: BLE001 — no GPU / driver issues are not errors
        _nvml_ready = None
        return None


def snapshot() -> dict:
    """Cheap stats snapshot, shared with the UI's /stats gauges."""
    import psutil

    vm = psutil.virtual_memory()
    disk = psutil.disk_usage("C:\\")
    return {
        "cpu_percent": psutil.cpu_percent(interval=0.3),
        "ram": {
            "used_gb": round(vm.used / 1e9, 1),
            "total_gb": round(vm.total / 1e9, 1),
            "percent": vm.percent,
        },
        "disk_c": {
            "free_gb": round(disk.free / 1e9, 1),
            "total_gb": round(disk.total / 1e9, 1),
        },
        "gpu": _gpu(),
    }


@tool
def get_system_stats(detail: bool = False) -> dict:
    """CPU, RAM, disk and GPU usage; detail adds top 5 processes."""
    import psutil

    stats = snapshot()
    if detail:
        procs = list(psutil.process_iter(["name", "pid"]))
        for p in procs:
            try:
                p.cpu_percent(None)  # prime
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        time.sleep(0.3)
        samples = []
        for p in procs:
            try:
                samples.append(
                    {
                        "name": p.info["name"],
                        "pid": p.info["pid"],
                        "cpu_percent": p.cpu_percent(None),
                        "rss_mb": round(p.memory_info().rss / 1e6, 1),
                    }
                )
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        stats["top_processes"] = sorted(samples, key=lambda s: s["cpu_percent"], reverse=True)[:5]
    return stats
