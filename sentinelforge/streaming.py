"""Launch and read the Structured Streaming executor (`RunStreaming`) - see StreamingEngine.scala for the semantics."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

from . import spark_engine as se


def command(spec: Path, source: Path, out: Path, checkpoint: Path, policy: Path | None = None, lateness_s: int = 30,
            expiry_s: int = 3600, trigger_s: int = 1, available_now: bool = True, max_files: int = 1000,
            idle_exit_s: int = 0, heap: str = "1g") -> list[str]:
    java = se.find_java()
    cp = se.ensure_built().read_text(encoding="utf-8").strip()
    args = ["--spec", str(spec), "--source", str(source), "--out", str(out), "--checkpoint", str(checkpoint),
            "--lateness-seconds", str(lateness_s), "--expiry-seconds", str(expiry_s), "--trigger-seconds", str(trigger_s),
            "--available-now", str(available_now).lower(), "--max-files-per-trigger", str(max_files), "--idle-exit-seconds", str(idle_exit_s)]
    if policy:
        args += ["--policy", str(policy)]
    return se.jvm_command(java, heap) + ["-cp", cp, "sentinelforge.compiler.RunStreaming"] + args


def start(*a, log: Path | None = None, **kw) -> subprocess.Popen:
    cmd = command(*a, **kw)
    out = open(log, "wb") if log else subprocess.DEVNULL
    return se.popen_tree(cmd, cwd=se.REPO_ROOT, stdout=out, stderr=subprocess.STDOUT, env=se.child_env())


def run_to_completion(*a, timeout: float = 600, **kw) -> int:
    p = start(*a, **kw)
    try:
        return p.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        se.kill_tree(p)
        raise


def kill(p: subprocess.Popen) -> None:
    """Hard-kill the whole process tree (a crash: no shutdown hooks, no final commit)."""
    se.kill_tree(p)


def read_kind(out: Path, kind: str) -> list[dict]:
    rows: list[dict] = []
    d = Path(out) / kind
    if d.exists():
        for f in sorted(d.glob("batch-*.jsonl")):
            rows += [json.loads(x) for x in f.read_text(encoding="utf-8").splitlines() if x.strip()]
    return rows


def batches_written(out: Path, kind: str = "alert") -> int:
    d = Path(out) / kind
    return len(list(d.glob("batch-*.jsonl"))) if d.exists() else 0
