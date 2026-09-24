> **Historical document.** Written during an earlier phase of the project and kept as a record. Numbers here were measured on the original 44-report set and the pre-audit pipeline; the current, audited results and limits are in [`docs/evaluation.md`](../evaluation.md) and [`docs/limitations.md`](../limitations.md).

# Phase 4 — Scala/Spark Toolchain Setup

This environment started with only Java 8 installed (verified directly,
not assumed — see `docs/spec/stage3-observability-checker.md`'s original
note). Getting to a real, running Spark compiler took 3 separate fixes,
each a genuine environment problem, not a design choice — kept here so the
setup is reproducible and so the errors aren't lost.

## What's installed, and why

1. **Coursier** (`cs-x86_64-pc-win32.exe setup`) — installs `sbt`, `scala`,
   `scalac`, `scala-cli`, `scalafmt` in one step, reusing the existing
   Java 8 JRE where possible. Installed to
   `~/AppData/Local/Coursier/data/bin` (launcher `.bat` files — Git Bash's
   `which` doesn't resolve `.bat` automatically; call them directly, e.g.
   `sbt.bat`).
2. **JDK 17** (`cs java --jvm temurin:17`) — required beyond Java 8 for two
   independent reasons: the installed **sbt launcher is sbt 2.x**, which
   refuses to start below JDK 17 (`project/build.properties` still pins
   the actual build to **sbt 1.10.5**, which is what really runs); and
   Scala 3 tooling coursier installs by default needs Java 11+ class file
   versions anyway. `JAVA_HOME` must point at the JDK 17 install for `sbt`
   to launch at all.
3. **`--add-opens` JVM flags** (`build.sbt`, `Compile / run / javaOptions`,
   with `fork := true`) — Spark 3.5.3's storage layer reflectively touches
   `sun.nio.ch.DirectBuffer`, which Java 9+'s module system blocks by
   default. These are the same flags `spark-submit` sets automatically;
   running via `sbt run`/`runMain` needs them supplied explicitly.
4. **`winutils.exe` + `hadoop.dll`** (Hadoop 3.3.5 build, placed under
   `.tools/hadoop/bin`, `HADOOP_HOME` set to `.tools/hadoop`) — Spark's
   bundled Hadoop client calls native Windows file APIs even for plain
   local-filesystem Parquet reads; without them, `spark.read.parquet(...)`
   fails with `UnsatisfiedLinkError` before ever getting near the data.

None of `.tools/` is committed (see `.gitignore`) — it's a reinstallable
toolchain, not project content. To reproduce, run the 4 steps above in
order; each one's exact command is in this repo's history if needed
verbatim.

## What this unblocked, with real numbers

- **`sbt compile`**: all 9 Scala sources compile clean — the Phase 0
  golden reference, all 5 Phase 1 manual baselines, and the 3 new Stage 4
  compiler files (`CompiledSpec.scala`, `RuleCompiler.scala`,
  `ReplayCheck.scala`). These had been hand-written text since Phase 0 and
  never actually verified to compile until now.
- **`sbt "runMain sentinelforge.compiler.ReplayCheck"`**: runs a real local
  Spark 3.5.3 session against the real partitioned Parquet store built in
  Phase 1 (`data/processed/events/`), compiles all 5 behaviours' specs via
  `RuleCompiler`, and checks the output against the independently-authored
  labels from Phase 1. **17 / 17 scenarios passed** — every true positive
  detected, every negative correctly held back, both `insufficient_context`
  cases (accounts absent from the policy reference) correctly distinguished
  from `no_alert`. Full output in
  [`experiments/results/phase4_replay_check.json`](../../experiments/results/phase4_replay_check.json).

This is the first point in the project where "compile" and "execute" are
literally true, not descriptive language for a Python prototype.

## What's still a Python prototype

`compiler/src/observability_checker.py` (Stage 3) remains Python. Nothing
about the toolchain now blocks porting it to Scala — that's a real,
available next step, not one this environment prevents — but it wasn't
redone here since the Python version is already verified against 17 real
cases of its own (`compiler/test/evaluate_stage3.py`) and porting it
wouldn't change what it validates, only what language does the validating.
