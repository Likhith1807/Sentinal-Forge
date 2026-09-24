"""Shared benchmark helpers: hardware capture and sample-size-aware statistics."""
from __future__ import annotations

import json
import math
import os
import platform
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))


def hardware() -> dict:
    """What the numbers were measured on. Best effort per OS; anything unavailable is reported as such, never guessed."""
    info = {"os": f"{platform.system()} {platform.release()} ({platform.version()})", "machine": platform.machine(),
            "python": platform.python_version(), "logicalCpus": os.cpu_count(), "cpuModel": None, "physicalCores": None,
            "ramGB": None, "storage": None, "machinesUsed": 1, "note": "single machine; no cluster was used"}
    try:
        if platform.system() == "Windows":
            def ps(cmd):
                return subprocess.run(["powershell", "-NoProfile", "-Command", cmd], capture_output=True, text=True, timeout=30).stdout.strip()
            cpu = json.loads(ps("Get-CimInstance Win32_Processor | Select-Object Name,NumberOfCores,NumberOfLogicalProcessors | ConvertTo-Json -Compress"))
            cpu = cpu[0] if isinstance(cpu, list) else cpu
            info.update(cpuModel=cpu["Name"].strip(), physicalCores=cpu["NumberOfCores"], logicalCpus=cpu["NumberOfLogicalProcessors"])
            info["ramGB"] = round(int(ps("(Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory")) / 2**30, 1)
            info["storage"] = ps("(Get-PhysicalDisk | Select-Object -First 1 | ForEach-Object { $_.MediaType + ' ' + $_.FriendlyName })") or None
        elif platform.system() == "Linux":
            cpuinfo = Path("/proc/cpuinfo").read_text()
            info["cpuModel"] = next((l.split(":", 1)[1].strip() for l in cpuinfo.splitlines() if l.startswith("model name")), None)
            info["ramGB"] = round(int(next(l.split()[1] for l in Path("/proc/meminfo").read_text().splitlines() if l.startswith("MemTotal"))) / 2**20, 1)
        elif platform.system() == "Darwin":
            info["cpuModel"] = subprocess.run(["sysctl", "-n", "machdep.cpu.brand_string"], capture_output=True, text=True).stdout.strip()
            info["ramGB"] = round(int(subprocess.run(["sysctl", "-n", "hw.memsize"], capture_output=True, text=True).stdout) / 2**30, 1)
    except Exception as exc:  # noqa: BLE001
        info["captureError"] = f"{type(exc).__name__}: {exc}"
    try:
        info["gitCommit"] = subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO, capture_output=True, text=True).stdout.strip()
        info["gitDirty"] = bool(subprocess.run(["git", "status", "--porcelain"], cwd=REPO, capture_output=True, text=True).stdout.strip())
    except Exception:  # noqa: BLE001
        pass
    return info


def _nearest_rank(sorted_vals: list[float], p: float) -> float:
    return sorted_vals[max(0, math.ceil(p * len(sorted_vals)) - 1)]


def describe(values: list[float]) -> dict:
    """Summary statistics, each reported only when the sample can support it.

    n < 2 : the single value, no spread.  n >= 5 : quartiles.  n >= 20 : p95 (nearest rank; still a noisy estimate, so a
    bootstrap interval is attached).  n >= 100 : p99.  A percentile the sample cannot support is `null` with a reason -
    "p95 of three runs" is the maximum of three runs, and is never reported here."""
    n = len(values)
    if n == 0:
        return {"n": 0}
    v = sorted(values)
    mean = sum(v) / n
    out = {"n": n, "min": v[0], "median": _nearest_rank(v, 0.5) if n % 2 else (v[n // 2 - 1] + v[n // 2]) / 2, "mean": mean, "max": v[-1]}
    if n >= 2:
        out["stdev"] = math.sqrt(sum((x - mean) ** 2 for x in v) / (n - 1))
    out["iqr"] = [_nearest_rank(v, 0.25), _nearest_rank(v, 0.75)] if n >= 5 else None
    for name, p, need in (("p95", 0.95, 20), ("p99", 0.99, 100)):
        if n >= need:
            out[name] = _nearest_rank(v, p)
            if name == "p95":
                import random
                rng = random.Random(7)
                boots = sorted(_nearest_rank(sorted(rng.choice(v) for _ in v), 0.95) for _ in range(1000))
                out["p95Bootstrap95CI"] = [boots[25], boots[975]]
        else:
            out[name] = None
            out.setdefault("notReported", []).append(f"{name}: needs n>={need}, have {n}")
    return out
