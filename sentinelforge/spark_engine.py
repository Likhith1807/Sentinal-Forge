"""Launch the Spark rule executor (`sentinelforge.compiler.RunRule`) as a child JVM.

Why a child process and not py4j / a long-lived session: a run must be isolated (its own heap, its own
failure), cancellable, and leave a self-describing directory behind. The JVM is found from the environment
(`SF_JAVA`, `JAVA_HOME`, then `PATH`) and the classpath from a file the build writes, so nothing here is
tied to one machine's install locations.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
CLASSPATH_FILE = REPO_ROOT / "target" / "sf-classpath.txt"
SCALA_SOURCES = [REPO_ROOT / "compiler" / "src" / "main" / "scala", REPO_ROOT / "build.sbt"]

_ADD_OPENS = [
    "java.lang", "java.lang.invoke", "java.lang.reflect", "java.io", "java.net", "java.nio", "java.util",
    "java.util.concurrent", "java.util.concurrent.atomic", "sun.nio.ch", "sun.nio.cs", "sun.security.action",
    "sun.util.calendar",
]


class EngineUnavailable(RuntimeError):
    """The JVM, sbt or the built classpath is missing; the message says how to fix it."""


@dataclass
class Java:
    path: str
    major: int


def find_java() -> Java:
    candidates = []
    if os.environ.get("SF_JAVA"):
        candidates.append(os.environ["SF_JAVA"])
    if os.environ.get("JAVA_HOME"):
        candidates.append(str(Path(os.environ["JAVA_HOME"]) / "bin" / ("java.exe" if os.name == "nt" else "java")))
    if shutil.which("java"):
        candidates.append(shutil.which("java"))
    for c in candidates:
        try:
            out = subprocess.run([c, "-version"], capture_output=True, text=True, timeout=20)
        except (OSError, subprocess.TimeoutExpired):
            continue
        m = re.search(r'version "(\d+)(?:\.(\d+))?', out.stderr + out.stdout)
        if m:
            major = int(m.group(2)) if m.group(1) == "1" and m.group(2) else int(m.group(1))
            return Java(c, major)
    raise EngineUnavailable("No Java runtime found. Install JDK 8, 11 or 17 and set JAVA_HOME (or SF_JAVA).")


def find_sbt() -> str:
    for name in (os.environ.get("SBT"), shutil.which("sbt"), shutil.which("sbt.bat")):
        if name:
            return name
    win = Path(os.environ.get("LOCALAPPDATA", "")) / "Coursier" / "data" / "bin" / "sbt.bat"
    if win.exists():
        return str(win)
    raise EngineUnavailable("sbt not found. Install sbt (https://www.scala-sbt.org) or set SBT to its path.")


def _newest_source_mtime() -> float:
    newest = 0.0
    for root in SCALA_SOURCES:
        files = [root] if root.is_file() else list(root.rglob("*.scala"))
        for f in files:
            newest = max(newest, f.stat().st_mtime)
    return newest


def ensure_built(force: bool = False) -> Path:
    """Compile the Scala sources and cache the runtime classpath. Idempotent and cheap when up to date."""
    if not force and CLASSPATH_FILE.exists() and CLASSPATH_FILE.stat().st_mtime >= _newest_source_mtime():
        return CLASSPATH_FILE
    sbt = find_sbt()
    proc = subprocess.run([sbt, "-batch", "-no-colors", "compile", "export Runtime/fullClasspath"],
                          cwd=REPO_ROOT, capture_output=True, text=True)
    if proc.returncode != 0:
        raise EngineUnavailable("sbt compile failed:\n" + (proc.stdout + proc.stderr)[-2000:])
    lines = [ln.strip() for ln in proc.stdout.splitlines() if ln.strip()]
    cp = next((ln for ln in reversed(lines) if os.pathsep in ln and ("scala-library" in ln or "spark" in ln)), None)
    if not cp:
        raise EngineUnavailable("could not read the classpath from sbt output")
    CLASSPATH_FILE.parent.mkdir(parents=True, exist_ok=True)
    CLASSPATH_FILE.write_text(cp, encoding="utf-8")
    return CLASSPATH_FILE


def jvm_command(java: Java, heap: str = "2g") -> list[str]:
    cmd = [java.path, f"-Xmx{heap}"]
    if java.major >= 9:
        cmd += [f"--add-opens=java.base/{p}=ALL-UNNAMED" for p in _ADD_OPENS] + ["-Djdk.reflect.useDirectMethodHandle=false"]
    hadoop = REPO_ROOT / ".tools" / "hadoop"
    if os.name == "nt" and hadoop.exists():
        cmd.append(f"-Dhadoop.home.dir={hadoop}")
    return cmd


def popen_tree(cmd: list[str], **kw) -> subprocess.Popen:
    """Start a child in its own process group so the WHOLE tree can be terminated. Needed because on Windows `java.exe`
    can be a launcher stub that spawns the real JVM: killing only the stub leaves an orphan that keeps running (and
    keeps writing to a streaming checkpoint)."""
    if os.name != "nt":
        kw.setdefault("start_new_session", True)
    return subprocess.Popen(cmd, **kw)


def kill_tree(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True)
    else:
        import signal
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except ProcessLookupError:
            pass
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:  # pragma: no cover
        pass


def child_env() -> dict:
    """Environment for the child JVM. On Windows Spark's Hadoop client needs winutils.exe / hadoop.dll (a repo-local
    toolchain under .tools/hadoop, see docs/spec/stage4-scala-toolchain.md); elsewhere nothing is added."""
    env = dict(os.environ)
    hadoop = REPO_ROOT / ".tools" / "hadoop"
    if os.name == "nt" and hadoop.exists():
        env["HADOOP_HOME"] = str(hadoop)
        env["PATH"] = str(hadoop / "bin") + os.pathsep + env.get("PATH", "")
    return env


def run_rule(spec_path: Path, events_path: Path, out_dir: Path, policy_path: Path | None = None, heap: str = "2g",
             extra: list[str] | None = None, timeout: float | None = 900) -> dict:
    """Run RunRule; returns the parsed run.json (status "completed" or "failed"). Raises only when the engine
    itself cannot be started; a failed run is a result, not an exception, and its directory is preserved."""
    java = find_java()
    cp = ensure_built().read_text(encoding="utf-8").strip()
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    args = ["--spec", str(spec_path), "--events", str(events_path), "--out", str(out_dir)]
    if policy_path:
        args += ["--policy", str(policy_path)]
    cmd = jvm_command(java, heap) + ["-cp", cp, "sentinelforge.compiler.RunRule"] + args + (extra or [])
    with open(out_dir / "engine.log", "wb") as log:
        proc = popen_tree(cmd, cwd=REPO_ROOT, stdout=log, stderr=subprocess.STDOUT, env=child_env())
        try:
            code = proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            kill_tree(proc)
            code = -9
    run_json = out_dir / "run.json"
    if run_json.exists():
        result = json.loads(run_json.read_text(encoding="utf-8"))
    else:
        tail = (out_dir / "engine.log").read_text(encoding="utf-8", errors="replace")[-1500:]
        result = {"status": "failed", "error": {"kind": "engine_crash" if code != -9 else "timeout", "message": tail}}
        run_json.write_text(json.dumps(result, indent=2), encoding="utf-8")
    result["exitCode"] = code
    return result


def read_alerts(out_dir: Path) -> list[dict]:
    path = Path(out_dir) / "alerts.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


if __name__ == "__main__":  # python -m sentinelforge.spark_engine  -> build + report what would be used
    j = find_java()
    print(f"java: {j.path} (major {j.major})")
    print(f"classpath: {ensure_built(force='--force' in sys.argv)}")
