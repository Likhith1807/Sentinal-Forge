"""Hold the machine awake while a long benchmark runs (Windows: SetThreadExecutionState; a no-op elsewhere).

    python scripts/bench/keep_awake.py &        # run in the background; ends when the process is killed

A laptop that enters Modern Standby mid-run stalls the JVM and the driver together, and the pause shows up as a latency outlier of
exactly the standby duration. That happened once during this project's first streaming-latency run (24 minutes; see
docs/benchmarks.md). This asks the OS not to idle-sleep; it changes no system setting and lasts only as long as this process.
"""
import sys
import time

if sys.platform == "win32":
    import ctypes
    ES_CONTINUOUS, ES_SYSTEM_REQUIRED = 0x80000000, 0x00000001
    ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)
    print("holding the system awake; kill this process to release", flush=True)
    while True:
        time.sleep(60)
else:
    print("nothing to do on this platform")
